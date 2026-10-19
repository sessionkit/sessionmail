"""SM-785, fix (a): `memory run` executes an item only if its SIGNER passes --trust.

What was wrong (repro-001, rca-001): `run` executed every MHS 170 member, and
membership checks a mail against the key the mail CARRIES. So a self-signed
item from anyone who could deliver to the box ran as the owner (SK-80 B1). The
memory spec requires a three-leg `--trust` ("A script-mail's subject never
executes"). Now every mail item is put to the verifier with --trust after
membership and forks, and before it runs. The verifier's exit code decides:
0 FULL and 4 UNBLESSED run (4 named in the header; `REQUIRE_BLESSING` is the
one constant that would refuse it), anything else is REFUSED, named with its
TRUST: line, not run, and the exit is 3. Every other item still runs.

NOTHING HERE RUNS `memory run` FOR REAL (SM-530). The executor runs in a temp
HOME against a `notmuch` stub serving a fixture thread. Each item's body only
touches a marker file, so the tests see exactly what EXECUTED.

Two verifiers:
  - REAL (`test_real_*`): `session-mail-verify` itself, on mails signed here
    with real SSHSIG keys and a temp registry: the repro-001 shapes.
  - STUB (`stub` fixture): answers plain mode 0 (a member) and --trust from
    the mail's X-Fixture-Trust header, so every exit the contract has (and
    some it does not) can be driven.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mhs, _operator, memory
from sessionmail import _settings

REPO = Path(__file__).resolve().parent.parent
EXECUTOR = Path(_mhs.MHS_DIR) / "session-mail-run"
SIGPY = REPO / "src" / "sessionmail" / "_sig.py"
AGENT = "reader"
HOST = "host1.session-mail"
NS = "session-mail@example.com"          # _sender.SIG_NS_DEFAULT

NOTMUCH = """#!/usr/bin/env bash
for a in "$@"; do case "$a" in
  --output=threads) echo "thread:00000000000000aa"; exit 0 ;;
  --entire-thread=false) cat "{T}/root.json"; exit 0 ;;
esac; done
for a in "$@"; do case "$a" in --body=false|--body=true) cat "{T}/thread.json"; exit 0 ;; esac; done
echo "notmuch stub: unexpected: $*" >&2; exit 9
"""

#: Plain mode: X-Fixture-Sig (good 0, else 2), as the fork suite's stub.
#: --trust: X-Fixture-Trust is "<rc>" or "<rc> <TRUST line>", or "sleep".
#: Every call is logged, so a test can see what was asked.
STUB_VERIFY = """#!/usr/bin/env bash
echo "$*" >> "{T}/verify.log"
hdr(){{ sed -n "/^\\$/q; s/^$1: //p" "$2"; }}
f="$1"; shift
case " $* " in
  *" --trust "*)
    t="$(hdr X-Fixture-Trust "$f")"
    [ "$t" = sleep ] && {{ sleep 30; exit 0; }}
    rc="${{t%% *}}"; line="${{t#* }}"
    [ "$line" != "$t" ] && echo "$line"
    exit "${{rc:-1}}" ;;
esac
case "$(hdr X-Fixture-Sig "$f")" in good) exit 0 ;; *) exit 2 ;; esac
"""


# ── the fixture ───────────────────────────────────────────────────────────

class Box:
    """A temp HOME with a [ROOT] > [BOOT] > [WAKE] thread of numbered items."""

    def __init__(self, root: Path):
        self.T = root
        for d in ("bin", "keys", "mail", "marks", "registry/certs"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "bin" / "notmuch").write_text(NOTMUCH.format(T=root))
        (root / "bin" / "notmuch").chmod(0o755)
        self.order: list[dict] = []
        self.kids: list[list] = []
        self.verifier: str | None = None        # None: the real, bundled one

    # mails ------------------------------------------------------------------
    def key(self, name: str) -> Path:
        k = self.T / "keys" / name
        if not k.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)],
                           check=True, stdin=subprocess.DEVNULL)
        return k

    def cert(self, key: str, agent: str) -> None:
        """A session-birth-v1 cert in the registry for KEY, naming AGENT."""
        t, b64 = (self.T / "keys" / f"{key}.pub").read_text().split()[:2]
        h = hashlib.sha256(base64.b64decode(b64)).digest()[:16].hex()
        sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        (self.T / "registry" / "certs" / f"{sid}.cert").write_text(
            f"session-birth-v1\nsession-id: {sid}\npubkey: {t} {b64}\nagent: {agent}\n")

    def msg(self, local: str, subject: str, key: str | None = None, v: int = 2,
            run: str | None = None, extra: dict | None = None, frm: str = AGENT,
            body: str | None = None):
        """One mail; KEY signs it for real (v2 or v1), EXTRA adds headers.
        BODY, when given, is the body verbatim (SM-771: a placeholder)."""
        fn = self.T / "mail" / local
        to = f"{AGENT}+memory@{HOST}"
        frm_h = f"{frm} <{frm}@{HOST}>"
        date = "Sun, 08 Dec 2030 18:00:%02d +1100" % len(self.order)
        mid = f"<{local}@{HOST}>"
        if body is None:
            body = f"echo {run}-ran; touch {self.T}/marks/{run}\n" if run else subject + "\n"
        head = [f"From: {frm_h}", f"To: {to}", f"Date: {date}", f"Subject: {subject}",
                f"Message-ID: {mid}", "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8", "Content-Transfer-Encoding: 8bit"]
        head += [f"{k}: {val}" for k, val in (extra or {}).items()]
        hdr = None
        if key:
            S = _sigmod()
            wire = ("\n".join(head) + "\n\n" + body).encode()
            if v == 2:
                h, p = S.h_for(wire), S.digests(wire)
                hdr = S.format_header(NS, h, p, self._sign(key, S.canonical(wire, NS, h, p, "")))
            else:
                canon = (f"from: {frm}\nto: {AGENT}+memory\nsubject: {subject}\ndate: {date}\n"
                         f"message-id: {mid}\n\n{body}").rstrip("\n") + "\n"
                hdr = S.format_v1(NS, self._sign(key, canon.encode()))
        fn.write_text("\n".join(head + ([hdr] if hdr else [])) + "\n\n" + body)
        m = {"id": mid.strip("<>"), "timestamp": 2000 + len(self.order), "filename": [str(fn)],
             "headers": {"Subject": subject, "From": frm_h, "To": to, "Date": date},
             "body": [{"id": 1, "content-type": "text/plain", "content": body}]}
        self.order.append(m)
        return [m, []]

    def _sign(self, key: str, data: bytes) -> str:
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.key(key)), "-n", NS, "-"],
                           input=data, capture_output=True, check=True)
        return "".join(l for l in r.stdout.decode().split("\n") if l and "SSH SIGNATURE" not in l)

    def item(self, *a, **kw) -> None:
        self.kids.append(self.msg(*a, **kw))

    def seal(self, key: str | None = None, extra: dict | None = None) -> None:
        """Write the thread the notmuch stub serves."""
        root = self.msg("root", "[ROOT]", key, extra=extra)
        boot = self.msg("boot", "[BOOT]", key, extra=extra)
        wake = self.msg("wake", "[WAKE]", key, extra=extra)
        # labels first in document order, items after: re-stamp timestamps
        for i, m in enumerate([root[0], boot[0], wake[0]] + [k[0] for k in self.kids]):
            m["timestamp"] = 2000 + i
        wake[1] = self.kids
        boot[1] = [wake]
        root[1] = [boot]
        (self.T / "thread.json").write_text(json.dumps([[root]]))
        (self.T / "root.json").write_text(json.dumps([[[root[0], []]]]))

    # running ----------------------------------------------------------------
    def run(self, *argv: str, registry: bool = True, **env: str) -> subprocess.CompletedProcess:
        e = {**os.environ, "PATH": f"{self.T / 'bin'}{os.pathsep}{os.environ['PATH']}",
             "HOME": str(self.T), "CLAUDE_CODE_AGENT": AGENT, "SESSION_AGENT": "",
             "READER_ROOT": f"root@{HOST}", "SESSIONMAIL_MEMORY_RUN": "1",
             "SESSION_MAIL_VERIFY_TIMEOUT": "5"}
        e.pop("SESSION_REGISTRY", None)
        e.pop("SESSION_MAIL_VERIFY", None)
        if registry:
            e["SESSION_REGISTRY"] = str(self.T / "registry")
        if self.verifier:
            e["SESSION_MAIL_VERIFY"] = self.verifier
        e.update(env)
        return subprocess.run([sys.executable, str(EXECUTOR), "/BOOT/WAKE", *argv],
                              capture_output=True, text=True, env=e, timeout=120)

    def ran(self) -> list[str]:
        return sorted(p.name for p in (self.T / "marks").iterdir())

    def verify_log(self) -> list[str]:
        p = self.T / "verify.log"
        return p.read_text().splitlines() if p.exists() else []


_SIG = []


def _sigmod():
    if not _SIG:
        spec = importlib.util.spec_from_file_location("_sig_sm785", SIGPY)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _SIG.append(m)
    return _SIG[0]


needs_ssh = pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen")


@pytest.fixture
def real(tmp_path):
    """The repro-001 matrix, signed for real, under the REAL verifier.

    10 owner-signed, owner's cert names `reader`      -> UNBLESSED: runs
    20 self-signed v2, a fresh key with no cert       -> FAIL no-cert: refused
    30 an intruder's registered key, forging From     -> FAIL agent-mismatch: refused
    40 self-signed v1, a fresh key with no cert       -> FAIL no-cert: refused
    50 unsigned                                       -> not a member: skipped
    """
    b = Box(tmp_path)
    for k in ("owner", "self", "intruder"):
        b.key(k)
    b.item("w10", "10 owner-signed control", "owner", run="owner")
    b.item("w20", "20 self-signed v2, fresh key", "self", run="self_v2")
    b.item("w30", "30 registered intruder forges From reader", "intruder", run="intruder")
    b.item("w40", "40 self-signed v1, fresh key", "self", v=1, run="self_v1")
    b.item("w50", "50 unsigned control", None, run="unsigned")
    b.seal("owner")
    b.cert("owner", AGENT)
    b.cert("intruder", "intruder")
    return b


@pytest.fixture
def stub(tmp_path):
    b = Box(tmp_path)
    v = tmp_path / "bin" / "verify"
    v.write_text(STUB_VERIFY.format(T=tmp_path))
    v.chmod(0o755)
    b.verifier = str(v)
    return b


def good(trust: str) -> dict:
    return {"X-Fixture-Sig": "good", "X-Fixture-Trust": trust}


# ── the real verifier: repro-001's three shapes are refused ───────────────

@needs_ssh
def test_real_untrusted_items_are_refused_named_and_not_run(real):
    p = real.run()
    out = p.stdout
    assert p.returncode == 3, out + p.stderr
    assert real.ran() == ["owner"], "only the trusted owner's item may execute:\n" + out
    for n, why in (("20", "reason=no-cert"), ("30", "reason=agent-mismatch"), ("40", "reason=no-cert")):
        assert f"REFUSED: item {n} <w{n}@{HOST}> is NOT TRUSTED: TRUST: FAIL leg=2-registry {why}" in out, out
    assert "═══ REFUSED: item 20, 30, 40 NOT TRUSTED — not run; every other item did (exit 3) ═══" in out
    assert "SKIPPED: item 50 is NOT A MEMBER" in out, "a non-member keeps its old line"


@needs_ssh
def test_real_mixed_root_the_trusted_item_still_runs_under_unblessed(real):
    out = real.run().stdout
    head = next(l for l in out.splitlines() if l.startswith("═══ 10 "))
    assert ", UNBLESSED)" in head, head
    assert "owner-ran" in out


@needs_ssh
def test_real_json_names_the_refused_and_the_verdicts(real):
    p = real.run("--json")
    assert p.returncode == 3, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["refused_trust"] == ["20", "30", "40"]
    assert d["refused_forks"] == []
    by = {s["num"]: s for s in d["sections"]}
    assert by["10"]["trust"] == "TRUST: UNBLESSED reason=bless-absent" and by["10"]["rc"] == 0
    for n in ("20", "30", "40"):
        assert by[n]["refused"] == "trust" and by[n]["rc"] == -1
        assert by[n]["trust"].startswith("TRUST: FAIL leg=2-registry")
    assert "trust" not in by["50"], "a non-member is never put to --trust"
    assert "trust_banner" not in d
    assert real.ran() == ["owner"]


@needs_ssh
def test_real_list_comments_out_the_refused_and_runs_nothing(real):
    p = real.run("--list")
    assert p.returncode == 3, p.stdout + p.stderr
    out = p.stdout
    assert real.ran() == [], "--list runs nothing"
    for n in ("20", "30", "40"):
        assert f"# {n}  budget=600   # REFUSED — NOT TRUSTED, would not run: TRUST: FAIL" in out, out
        live = [l for l in out.splitlines() if f"/marks/self_v2" in l or "/marks/intruder" in l
                or "/marks/self_v1" in l]
        assert live and all(l.startswith("#") for l in live), live
    assert "# 10  budget=600   # UNBLESSED, runs: TRUST: UNBLESSED reason=bless-absent" in out


@needs_ssh
def test_real_no_registry_refuses_every_item_under_one_banner(real):
    p = real.run(registry=False)
    out = p.stdout
    assert p.returncode == 3, out + p.stderr
    assert real.ran() == [], "with no registry nothing can be trusted, so nothing runs"
    assert out.count("NO TRUST REGISTRY") == 1, out
    assert out.splitlines()[0].startswith("═══ memory run: NO TRUST REGISTRY"), out
    for n in ("10", "20", "30", "40"):
        assert f"REFUSED: item {n} " in out
    assert "reason=registry-absent" in out
    d = json.loads(real.run("--json", registry=False).stdout)
    assert "NO TRUST REGISTRY" in d["trust_banner"] and d["refused_trust"] == ["10", "20", "30", "40"]


@needs_ssh
def test_real_trust_is_asked_once_per_member_by_subprocess(real, tmp_path):
    """A logging wrapper over the REAL verifier: one `--trust --quiet` per
    member item, none for the non-member, none for the labels. Naming a
    verifier turns the SM-668 port off, so every call is seen (rca-001)."""
    log = tmp_path / "argv.log"
    w = tmp_path / "bin" / "verify-log"
    w.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{log}"\n'
                 f'exec "{Path(_mhs.MHS_DIR) / "session-mail-verify"}" "$@"\n')
    w.chmod(0o755)
    p = real.run(SESSION_MAIL_VERIFY=str(w))
    assert p.returncode == 3
    trust = sorted(Path(l.split()[0]).name for l in log.read_text().splitlines() if "--trust" in l)
    assert trust == ["w10", "w20", "w30", "w40"], log.read_text()
    assert all(l.endswith("--trust --quiet") for l in log.read_text().splitlines() if "--trust" in l)
    assert real.ran() == ["owner"]


# ── SM-822: a v1 item a relay reshaped is not run (the real verifier) ────

@pytest.fixture
def reshaped(tmp_path):
    """Owner-signed v1 items, the owner's cert registered (so each would run UNBLESSED).
    A relay with no key then reshapes two of them. The notmuch stub serves what GMime would
    hand `body_text`: the relay's decode for the relabel, both text/plain parts for the
    append. At 0539fd2 both verified GOOD and the relay's command ran.

    10 honest v1, as the v1 sender wrote it                    -> runs
    20 v1, CTE 8bit relabelled base64 (GMime decodes the relay's) -> refused, not run
    30 v1, a text/plain part appended (body_text joins it)       -> refused, not run
    """
    b = Box(tmp_path)
    b.key("owner")
    b.item("w10", "10 honest v1", "owner", v=1, run="honest_v1")
    b.item("w20", "20 v1 relabelled base64", "owner", v=1, run="relabel_signed")
    b.item("w30", "30 v1 with a part appended", "owner", v=1, run="append_signed")
    relay = f"echo mallory-ran; touch {tmp_path}/marks/mallory\n"
    f20 = Path(b.kids[1][0]["filename"][0])
    f20.write_bytes(f20.read_bytes().replace(b"Content-Transfer-Encoding: 8bit",
                                             b"Content-Transfer-Encoding: base64", 1))
    b.kids[1][0]["body"] = [{"id": 1, "content-type": "text/plain", "content": relay}]
    f30 = Path(b.kids[2][0]["filename"][0])
    head, body = f30.read_bytes().split(b"\n\n", 1)
    head = head.replace(b"Content-Type: text/plain; charset=utf-8",
                        b'Content-Type: multipart/alternative; boundary="R"', 1)
    f30.write_bytes(head + b"\n\n--R\nContent-Type: text/plain; charset=utf-8\n\n" + body
                    + b"\n--R\nContent-Type: text/plain; charset=utf-8\n\n" + relay.encode()
                    + b"--R--\n")
    signed_text = b.kids[2][0]["body"][0]["content"]
    b.kids[2][0]["body"] = [{"id": 1, "content-type": "multipart/alternative", "content": [
        {"id": 2, "content-type": "text/plain", "content": signed_text},
        {"id": 3, "content-type": "text/plain", "content": relay}]}]
    b.seal("owner")
    b.cert("owner", AGENT)
    return b


@needs_ssh
def test_real_a_reshaped_v1_item_is_not_run(reshaped):
    p = reshaped.run()
    out = p.stdout
    assert reshaped.ran() == ["honest_v1"], "only the honest v1 item may execute:\n" + out + p.stderr
    assert "mallory-ran" not in out
    assert "honest_v1-ran" in out
    for n in ("20", "30"):
        # plain verify (membership) now says BAD, so the item is not a member: never run
        assert (f"SKIPPED: item {n} is NOT A MEMBER (MHS 170): signature does not verify"
                in out), out


# ── the stub verifier: every exit the contract has, and some it does not ──

@pytest.mark.parametrize("trust, runs, named", [
    ("0 TRUST: FULL", True, None),
    ("0", True, None),                                  # no TRUST: line, rc 0: a pass
    ("4 TRUST: UNBLESSED reason=bless-absent", True, "UNBLESSED"),
    ("1 TRUST: FAIL leg=2-registry reason=no-cert", False, "TRUST: FAIL leg=2-registry reason=no-cert"),
    ("1 TRUST: FAIL leg=3-bless reason=bless-invalid", False, "reason=bless-invalid"),
    ("5 TRUST: ERROR reason=allowed-signers-absent", False, "TRUST: ERROR reason=allowed-signers-absent"),
    ("2", False, "reason=rc-2"),                        # never a --trust verdict: fail closed
    ("3", False, "reason=rc-3"),
    ("6", False, "reason=rc-6"),
    ("1", False, "reason=rc-1"),                        # a FAIL with no line is still a FAIL
])
def test_stub_the_exit_code_decides(stub, trust, runs, named):
    stub.item("w05", "05 control", run="c05", extra=good("0 TRUST: FULL"))
    stub.item("w10", "10 under test", run="t10", extra=good(trust))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert stub.ran() == (["c05", "t10"] if runs else ["c05"]), out + p.stderr
    assert p.returncode == (0 if runs else 3), out
    head = next(l for l in out.splitlines() if l.startswith("═══ 10 "))
    if runs:
        assert ("UNBLESSED" in head) == (named == "UNBLESSED"), head
        assert "REFUSED" not in out
    else:
        assert "REFUSED: NOT TRUSTED" in head, head
        assert f"REFUSED: item 10 <w10@{HOST}> is NOT TRUSTED: " in out and named in out, out


def test_stub_a_verify_that_times_out_is_refused(stub):
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 slow verify", run="t10", extra=good("sleep"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run(SESSION_MAIL_VERIFY_TIMEOUT="1")
    assert p.returncode == 3, p.stdout + p.stderr
    assert stub.ran() == ["c05"]
    assert "TRUST: ERROR reason=timeout" in p.stdout


def test_stub_a_missing_verifier_is_refused(stub, tmp_path):
    stub.item("w10", "10 item", run="t10", extra=good("0"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    # membership needs the verifier too, so point --trust's call at a path
    # that membership never takes: only the --trust call can fail here.
    w = tmp_path / "bin" / "half-verify"
    w.write_text(f'#!/usr/bin/env bash\ncase " $* " in *" --trust "*) exec /nonexistent/verify ;; esac\n'
                 f'exec "{stub.verifier}" "$@"\n')
    w.chmod(0o755)
    p = stub.run(SESSION_MAIL_VERIFY=str(w))
    assert p.returncode == 3 and stub.ran() == [], p.stdout + p.stderr
    assert "REFUSED: item 10 " in p.stdout


def test_stub_a_fork_and_an_untrusted_item_exit_3_and_say_both(stub):
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10a", "10 copy a", run="a10", extra=good("0"))
    stub.item("w10b", "10 copy b", run="b10", extra=good("0"))
    stub.item("w20", "20 untrusted", run="u20", extra=good("1 TRUST: FAIL leg=2-registry reason=no-cert"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert p.returncode == 3, "trust refusal outranks a fork's 2:\n" + out
    assert stub.ran() == ["c05"], out
    # Each trailer must be true with the other present (review N2): neither
    # may say "every other item did", since the other refused item did not.
    assert "every other item did" not in out, out
    assert ("REFUSED: FORK at item 10 — neither copy ran; "
            "every item not refused or skipped above ran (exit 3)") in out, out
    assert ("REFUSED: item 20 NOT TRUSTED — not run; "
            "every item not refused or skipped above ran (exit 3)") in out, out
    d = json.loads(stub.run("--json").stdout)
    assert d["refused_forks"] == ["10"] and d["refused_trust"] == ["20"]
    lst = stub.run("--list")
    assert lst.returncode == 3 and stub.ran() == ["c05"]


def test_stub_a_fork_alone_still_exits_2_and_its_copies_are_not_put_to_trust(stub):
    stub.item("w10a", "10 copy a", run="a10", extra=good("0"))
    stub.item("w10b", "10 copy b", run="b10", extra=good("0"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    assert p.returncode == 2, p.stdout
    assert not [l for l in stub.verify_log() if "--trust" in l], stub.verify_log()


def test_stub_only_filters_before_trust(stub):
    """--only N: an item that will not run is not put to --trust, and an
    untrusted item outside the selection does not set exit 3."""
    stub.item("w10", "10 ok", run="t10", extra=good("0"))
    stub.item("w20", "20 untrusted", run="u20", extra=good("1 TRUST: FAIL leg=2-registry reason=no-cert"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run("--only", "10")
    assert p.returncode == 0 and stub.ran() == ["t10"], p.stdout
    assert [Path(l.split()[0]).name for l in stub.verify_log() if "--trust" in l] == ["w10"]


def test_stub_the_toml_shipwreck_is_not_mail_and_is_not_put_to_trust(stub):
    """No /BOOT/WAKE (no thread): the owner's local wake.toml runs as today."""
    (stub.T / f".{AGENT}").mkdir()
    (stub.T / f".{AGENT}" / "wake.toml").write_text(
        f'[section.a]\norder = 10\ncommand = "touch {stub.T}/marks/toml"\n')
    p = stub.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SHIPWRECK fallback" in p.stdout and stub.ran() == ["toml"]
    assert not [l for l in stub.verify_log() if "--trust" in l]


# ── the threshold is ONE constant ─────────────────────────────────────────

def _load(text: str | None = None) -> types.ModuleType:
    os.environ.setdefault("CLAUDE_CODE_AGENT", "nobody")
    if text is None:
        loader = SourceFileLoader("smr_sm785", str(EXECUTOR))
        spec = importlib.util.spec_from_loader("smr_sm785", loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        return m
    m = types.ModuleType("smr_sm785_flip")
    m.__file__ = str(EXECUTOR)
    exec(compile(text, str(EXECUTOR), "exec"), m.__dict__)
    return m


def test_the_threshold_is_one_constant_and_flipping_it_refuses_unblessed(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "nobody")
    text = EXECUTOR.read_text()
    assert text.count("\nREQUIRE_BLESSING = False\n") == 1
    m = _load()
    assert m.REQUIRE_BLESSING is False
    assert m.TRUST_ARGV == ["--trust", "--quiet"]
    assert m.TRUST_RUNS == {0: "FULL", 4: "UNBLESSED"}
    assert m.TRUST_REFUSED_EXIT == 3
    flip = _load(text.replace("\nREQUIRE_BLESSING = False\n", "\nREQUIRE_BLESSING = True\n"))
    assert flip.TRUST_ARGV == ["--trust", "--quiet", "--require-blessing"]
    assert flip.TRUST_RUNS == {0: "FULL"}, "(B): only a FULL (blessed) signer runs"


def test_strict_mode_refuses_an_unblessed_signer(stub, monkeypatch):
    """Under (B) the verifier's rc 4 would be rc 1 already; even if a
    verifier still answered 4, strict mode does not run it."""
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "nobody")
    flip = _load(EXECUTOR.read_text().replace("\nREQUIRE_BLESSING = False\n",
                                              "\nREQUIRE_BLESSING = True\n"))
    pt = types.SimpleNamespace(VERIFY=[stub.verifier], VERIFY_TIMEOUT=5)
    f = stub.T / "mail" / "x"
    f.write_text("X-Fixture-Sig: good\nX-Fixture-Trust: 4 TRUST: UNBLESSED reason=bless-absent\n\nx\n")
    ok, verdict = flip.trust_verdict(pt, {"file": str(f)})
    assert ok is False and "UNBLESSED" in verdict
    assert stub.verify_log()[-1].endswith("--trust --quiet --require-blessing")


# ── the click door hands the configured registry to the executor ──────────

ENV_ORACLE = """#!{python}
import os, sys
open(os.environ["FAKE_LOG"], "w").write(os.environ.get("SESSION_REGISTRY", "<unset>"))
"""


@pytest.fixture
def env_oracle(tmp_path, monkeypatch):
    fake = tmp_path / "fake-mhs"
    fake.write_text(ENV_ORACLE.format(python=sys.executable))
    fake.chmod(0o755)
    log = tmp_path / "env.log"
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(fake))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("SESSIONMAIL_MEMORY_RUN", "1")
    return log


def _mail_config(text: str | None) -> None:
    path = _operator.settings_home() / _settings.RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text)


@pytest.mark.parametrize("extra", [[], ["--list"]])
def test_the_click_door_fills_session_registry_from_config(env_oracle, tmp_path, monkeypatch, extra):
    monkeypatch.delenv("SESSION_REGISTRY", raising=False)
    _mail_config(json.dumps({"registry": str(tmp_path / "reg")}))
    r = CliRunner().invoke(memory.cli, ["run", "/BOOT/WAKE", *extra], prog_name="memory")
    assert r.exit_code == 0, r.output
    assert env_oracle.read_text() == str(tmp_path / "reg")


def test_the_variable_wins_over_config_at_the_click_door(env_oracle, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_REGISTRY", str(tmp_path / "from-env"))
    _mail_config(json.dumps({"registry": str(tmp_path / "from-config")}))
    CliRunner().invoke(memory.cli, ["run", "/BOOT/WAKE"], prog_name="memory")
    assert env_oracle.read_text() == str(tmp_path / "from-env")


def test_no_config_and_no_variable_leaves_it_unset_for_the_executor_to_refuse(env_oracle, monkeypatch):
    monkeypatch.delenv("SESSION_REGISTRY", raising=False)
    _mail_config(None)
    CliRunner().invoke(memory.cli, ["run", "/BOOT/WAKE"], prog_name="memory")
    assert env_oracle.read_text() == "<unset>"


def test_trust_env_is_one_definition_shared_with_message_verify():
    from sessionmail import message
    assert message.REGISTRY_KEY == _mhs.REGISTRY_KEY == "registry"
    assert _mhs.REGISTRY_VAR == "SESSION_REGISTRY"


# ── SM-853: the no-registry banner says what a registry is, and that we do not make one ──
# A stranger who follows the OFF text to `--list` lands on this banner. It must say what
# SESSION_REGISTRY points at, say plainly that sessionmail does not create a registry, and
# name the page that says how to build one by hand. Each phrase sits on ONE banner line, so
# it survives every renderer (the header, `--list` commenting each line out, `--json`).

BANNER_MUST_SAY = (
    "memory run: NO TRUST REGISTRY",
    'set SESSION_REGISTRY, or "registry" in ~/.sessionkit/mail/settings/mail.json',
    "a git work tree holding a committed allowed_signers file",
    '`<sid>@session namespaces="..." <public key>` line',
    "certs/ directory",
    "sessionmail does not create a registry today",
    "`message seat init --registry DIR`",
    "only adds a seat to a registry that is already seeded",
    'docs/message-seat.md, "The trust registry"',
)
SEAT_DOC = REPO / "docs" / "message-seat.md"


@pytest.fixture
def executor(monkeypatch):
    """The executor, imported (its gate is in main()); importing needs an agent."""
    monkeypatch.setenv("CLAUDE_CODE_AGENT", AGENT)
    loader = SourceFileLoader("smr_sm853", str(EXECUTOR))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("smr_sm853", loader))
    loader.exec_module(mod)
    return mod


def test_the_banner_says_what_a_registry_is_and_that_none_is_made(executor):
    banner = executor.NO_REGISTRY
    lines = banner.splitlines()
    for phrase in BANNER_MUST_SAY:
        assert sum(phrase in ln for ln in lines) == 1, (phrase, banner)
    # The headline did not move: it is what every existing reader keys on.
    assert lines[0].startswith("memory run: NO TRUST REGISTRY (set SESSION_REGISTRY")
    assert lines[0].endswith("Fix the environment, not the mail.")


def test_the_page_the_banner_names_exists_and_says_the_same():
    """The pointer is live: the page and its section exist, and the section says the
    same two things the banner does (the shape, and that nothing here creates one)."""
    text = SEAT_DOC.read_text()
    assert "\n## The trust registry\n" in text
    section = text.split("\n## The trust registry\n", 1)[1]
    flat = " ".join(section.split())
    assert "sessionmail does not create a registry today" in flat
    for word in ("allowed_signers", "certs/", "git work tree", "SESSION_REGISTRY",
                 '"registry"', "namespaces="):
        assert word in flat, word


def test_the_pages_seed_line_is_one_seat_init_accepts_and_is_made_up():
    """The example seed is the shape `seat init` copies namespaces= from, and every
    value in it is synthetic: a zero sid, the example namespace, a placeholder key."""
    from sessionmail import seat_init
    section = SEAT_DOC.read_text().split("\n## The trust registry\n", 1)[1]
    block = section.split("```text\n", 1)[1].split("\n```", 1)[0]
    (seed,) = block.splitlines()
    m = seat_init.SESSION_LINE.match(seed)
    assert m, seed
    assert m.group(1) == "00000000-0000-0000-0000-000000000001"
    assert m.group(2) == NS
    assert "EXAMPLE" in seed.split()[-1]


@needs_ssh
@pytest.mark.parametrize("argv", [(), ("--list",), ("--json",)], ids=["header", "list", "json"])
def test_real_no_registry_banner_carries_every_line_in_every_renderer(real, executor, argv):
    p = real.run(*argv, registry=False)
    assert p.returncode == 3, p.stdout + p.stderr
    text = json.loads(p.stdout)["trust_banner"] if argv == ("--json",) else p.stdout
    for phrase in BANNER_MUST_SAY:
        assert phrase in text, (phrase, text)
    assert text.count("NO TRUST REGISTRY") == 1, text
    if argv == ("--list",):
        # Every banner line is commented out: --list is a script, and the banner is not code.
        n = len(executor.NO_REGISTRY.splitlines())
        head = p.stdout.splitlines()[1:1 + n]
        assert len(head) == n and all(ln.startswith("# ") for ln in head), head
        assert head[0].startswith("# memory run: NO TRUST REGISTRY"), head
    if argv == ():
        assert p.stdout.splitlines()[0].startswith("═══ memory run: NO TRUST REGISTRY"), p.stdout
