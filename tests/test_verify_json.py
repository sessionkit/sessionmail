"""SM-788: `session-mail-verify --json`, one JSON object on stdout, the verdict (schema 1).

Every case is REAL crypto: a signing key made for the test, registries whose cert names
it, a blessing key, and the shipped verifier run as it ships. Each case runs twice, under
`--json` and under `--json --quiet`: stdout must be byte-identical between the two and
byte-equal to its golden file, `tests/fixtures/verify_json/<case>.json`, once the
key-dependent signer fields are put back as `<sid>` and `<key>`. The case numbers are
design.md section 6's (SM-788 task store).

What the runner checks on EVERY case, beside the golden bytes:
- stdout is one line, `json.loads` reads it, and no stdout line starts `TRUST:`;
- `rc` in the object is the exit status, and is the exit status WITHOUT `--json` too;
- the success rule (design 3.1): `signature` is non-null if and only if the exit is 0, or 4
  under `--trust`;
- `covered` lists only names in the verified `h`, in `h` order;
- `trust.line` is the TRUST: line the same mail prints without `--json`, and a run that
  prints no TRUST: line has every `trust` member null;
- the per-leg lines a run prints on stdout without `--json` go to stderr under `--json`,
  and are dropped under `--json --quiet`.

The golden files are the contract's, not output to refresh casually: a change to one is a
change to what a consumer that gates on this object is told.

Run: uv run pytest -q tests/test_verify_json.py
"""
import ast
import base64
import email
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs, _sig

VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"
GOLDEN = Path(__file__).parent / "fixtures" / "verify_json"
VECTORS = Path(__file__).parent / "fixtures" / "sig_v2"
NS = "session-mail@example.com"          # MAIL_NS_DEFAULT, SESSION_MAIL_SIG_NS unset
BLESS_NS = "session-bless@example.com"   # the verifier's shipped defaults
BLESS_PRINCIPAL = "alice@bless"
V1_H = "from:to:subject:date:message-id"
WIDE = ("from:to:cc:subject:date:message-id:in-reply-to:references:supersedes:mime-version:"
        "content-type:x-session-from:x-mouth-device:x-mouth-priority:x-mouth-when:x-mouth-extra")
SCRUB = ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF", "SESSION_REGISTRY",
         "SESSION_BLESS_NS", "SESSION_BLESS_PRINCIPAL")

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")


def _lift(*names):
    """Top-level definitions lifted from the verifier's heredoc (it runs at import, so it
    cannot be imported): the code under test, never a copy."""
    text = VERIFIER.read_text()
    head = "<<'PYEOF'\n"
    src = text[text.index(head) + len(head):text.index("\nPYEOF\n")]
    keep = [n for n in ast.parse(src).body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))]
    ns = {"sys": sys, "email": email, "base64": base64}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(VERIFIER), "exec"), ns)
    return ns


CANON = _lift("HDRS", "local_part", "canonical")["canonical"]


def keygen(path: Path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    kt, kb = (path.parent / (path.name + ".pub")).read_text().split()[:2]
    return kt, kb


def sid_of(kb: str) -> str:
    h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class Rig:
    """A signing key whose session is `ann`, and the registries the --trust cases need."""

    def __init__(self, d: Path):
        self.d = d
        self.key = d / "key"
        kt, kb = keygen(self.key)
        self.sid, self.keyline = sid_of(kb), f"{kt} {kb}"
        self.cert = f"session-birth-v1\nsession-id: {self.sid}\npubkey: {kt} {kb}\nagent: ann\n"
        self.bless_key = d / "bless"
        bt, bb = keygen(self.bless_key)
        self.other_key = d / "other"
        keygen(self.other_key)
        self.allowed = f"{BLESS_PRINCIPAL} {bt} {bb}\n"
        self.reg = {name: self._registry(name) for name in
                    ("unblessed", "blessed", "bless-invalid", "no-cert", "agent-mismatch")}
        self.reg["absent"] = d / "no-such-registry"
        self.n = 0

    def _registry(self, kind: str) -> Path:
        r = self.d / f"reg-{kind}"
        (r / "certs").mkdir(parents=True)
        if kind == "no-cert":
            return r
        cert = r / "certs" / f"{self.sid}.cert"
        cert.write_text(self.cert.replace("agent: ann", "agent: kim")
                        if kind == "agent-mismatch" else self.cert)
        if kind in ("blessed", "bless-invalid"):
            key = self.bless_key if kind == "blessed" else self.other_key
            subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", BLESS_NS,
                            str(cert)], check=True, capture_output=True)
            (r / "certs" / f"{self.sid}.cert.sig").rename(
                r / "certs" / f"{self.sid}.cert.blessed-by.{BLESS_PRINCIPAL}.sig")
            (r / "allowed_signers").write_text(self.allowed)
        return r

    def sign(self, data: bytes) -> str:
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=data, capture_output=True, check=True)
        return "".join(x for x in done.stdout.decode().splitlines() if x and "SSH SIGNATURE" not in x)

    def v1(self, body: str = "hello there\n", extra: str = "") -> bytes:
        text = base("v1", body, extra)
        b = self.sign(CANON(email.message_from_string(text)))
        return insert(text.encode(), f"X-Session-Signature: v=1; a=sshsig; n={NS}; h={V1_H}; b={b}")

    def v2(self, wire: bytes, h=None) -> bytes:
        h = _sig.h_for(wire) if h is None else h
        p = _sig.digests(wire)
        b = self.sign(_sig.canonical(wire, NS, h, p, ""))
        return _sig.insert_header(wire, _sig.format_header(NS, h, p, b))

    def run(self, raw: bytes, *flags, registry="unblessed", verifier=VERIFIER):
        self.n += 1
        f = self.d / f"mail{self.n}.eml"
        f.write_bytes(raw)
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env["SESSION_REGISTRY"] = str(self.reg[registry])
        return subprocess.run([str(verifier), str(f), *flags], capture_output=True, text=True,
                              env=env)


def base(mid: str, body: str, extra: str = "") -> str:
    return (f"From: Ann <ann@host.example>\nTo: kim@host.example\nSubject: s {mid}\n"
            f"Date: Sun, 09 Jun 2030 12:00:00 +0000\nMessage-ID: <{mid}@host.example>\n"
            f"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n{extra}\n{body}")


def wire(mid: str, extra: bytes = b"", body: bytes = b"hello there\n") -> bytes:
    return base(mid, "").encode().replace(b"\n\n", b"\n" + extra + b"\n", 1) + body


def insert(raw: bytes, line: str) -> bytes:
    head, _, body = raw.partition(b"\n\n")
    return head + b"\n" + line.encode() + b"\n\n" + body


def sig_value(raw: bytes) -> str:
    m = re.search(rb"^X-Session-Signature: ([^\n]*(?:\n[ \t][^\n]*)*)\n", raw, re.M)
    return m.group(1).decode()


def resig(raw: bytes, value: str) -> bytes:
    old = "X-Session-Signature: " + sig_value(raw)
    return raw.replace(old.encode(), ("X-Session-Signature: " + value).encode(), 1)


def b_of(raw: bytes) -> str:
    return re.search(r"b=([A-Za-z0-9+/=\s]+)", sig_value(raw)).group(1).replace("\n", "").replace(" ", "")


# ── the cases (design 6) ────────────────────────────────────────────────────

def c_v1_wide_h(rig):
    """Case 2: a v1 header whose h= names x-mouth-device. v1 ignores h= (EARS-335 F1)."""
    raw = rig.v1(extra="X-Mouth-Device: call:1317\n")
    return resig(raw, f"v=1; a=sshsig; n={NS}; h={WIDE}; b={b_of(raw)}")


def c_v2_relay_adds(rig):
    """Case 5: signed without X-Mouth-Device; a relay adds one after."""
    return insert(rig.v2(wire("v2relay")), "X-Mouth-Device: call:1317")


def c_v2_forged_h(rig):
    """Case 6: case 5's mail with its h= widened to name x-mouth-device, without the key."""
    raw = c_v2_relay_adds(rig)
    tags = _sig.parse_header(_sig.signature_value(raw))
    h = tags["h"] + ":x-mouth-device"
    return resig(raw, f"v=2; a=sshsig; n={NS}; h={h}; p={tags['p']}; b={tags['b']}")


def c_version_confusion(rig):
    """Case 7: SM-787's EARS-335 shape: a v1 mail with a header a byte reader takes for v2."""
    raw = rig.v1(extra="X-Mouth-Device: call:1317\n")
    p = ",".join(_sig.digests(raw))
    return resig(raw, f"v=2; a=sshsig; n={NS}; h={WIDE}; p={p}; b={b_of(raw)};  v=1")


def c_v2_upper_h(rig):
    """Case 8 (design Q4, MEASURED here): an upper-case X-Mouth-Device in a signed h=, and a
    relay adds the field. fields() lowercases what it reads off the wire, so the upper-case
    name matches no field and signs `X-Mouth-Device: ` (EARS-335 G1); x-mouth-device is not
    a claim header, so below_floor does not refuse the relay's copy. MEASURED on
    feat/SM-788-verify-json: rc 0 (good), and covered carries the name as signed with
    `values: [""]`: the relay's value is never listed. The golden file pins it."""
    w = wire("v2upper")
    raw = rig.v2(w, h=_sig.h_for(w) + ["X-Mouth-Device"])
    return insert(raw, "X-Mouth-Device: call:1317")


def c_v1_multipart_mixed(rig):
    """Case 22: a v1 mail past canonical()'s limit (a multipart/mixed): rc 3 from inside
    canonical(), before any crypto, and no TRUST: line even under --trust."""
    raw = rig.v1()
    return raw.replace(b"Content-Type: text/plain; charset=utf-8",
                       b'Content-Type: multipart/mixed; boundary="b"', 1).replace(
        b"hello there\n", b"--b\nContent-Type: text/plain\n\nhello there\n--b--\n", 1)


def c_v2_below_floor(rig):
    """Case 12: an honest signature over an h= that lacks cc (a floor name)."""
    w = wire("v2floor")
    return rig.v2(w, h=[n for n in _sig.h_for(w) if n != "cc"])


CASES = {
    # name: (build, flags, registry)
    "01-v1-good": (lambda r: r.v1(), (), "unblessed"),
    "02-v1-wide-h": (c_v1_wide_h, (), "unblessed"),
    "03-v2-good": (lambda r: r.v2(wire("v2good", b"Cc: Lee <lee@host.example>, sam@host.example\n")),
                   (), "unblessed"),
    "04-v2-mouth-device-signed": (lambda r: r.v2(wire("v2mouth", b"X-Mouth-Device: call:1317\n")),
                                  (), "unblessed"),
    "05-v2-relay-adds-outside-h": (c_v2_relay_adds, (), "unblessed"),
    "06-v2-forged-h": (c_v2_forged_h, (), "unblessed"),
    "07-version-confusion": (c_version_confusion, (), "unblessed"),
    "08-v2-upper-case-h": (c_v2_upper_h, (), "unblessed"),
    "09-unsigned": (lambda r: base("unsigned", "hello there\n").encode(), (), "unblessed"),
    "10a-v1-body-tampered": (lambda r: r.v1().replace(b"hello there", b"hello THERE"), (), "unblessed"),
    "10b-v2-signed-header-changed": (lambda r: r.v2(wire("v2hdr")).replace(b"Subject: s v2hdr",
                                                                           b"Subject: s v2HDR"),
                                     (), "unblessed"),
    "11-v2-part-altered": (lambda r: r.v2(wire("v2part")).replace(b"hello there", b"hello THERE"),
                           (), "unblessed"),
    "12-v2-below-floor": (c_v2_below_floor, (), "unblessed"),
    "13a-v3-plain": (lambda r: r.v2(wire("v3")).replace(b"X-Session-Signature: v=2;",
                                                        b"X-Session-Signature: v=3;"), (), "unblessed"),
    "13b-v3-trust": (lambda r: r.v2(wire("v3")).replace(b"X-Session-Signature: v=2;",
                                                        b"X-Session-Signature: v=3;"),
                     ("--trust",), "unblessed"),
    "14-v2-non-utf8-covered-value": (lambda r: r.v2(wire("v2latin", b"X-Note: caf\xe9\n")),
                                     (), "unblessed"),
    "15-trust-full": (lambda r: r.v2(wire("v2full", b"X-Mouth-Device: call:1317\n")),
                      ("--trust",), "blessed"),
    "16-trust-unblessed": (lambda r: r.v2(wire("v2unbl", b"X-Mouth-Device: call:1317\n")),
                           ("--trust",), "unblessed"),
    "17-trust-require-blessing": (lambda r: r.v2(wire("v2req")),
                                  ("--trust", "--require-blessing"), "unblessed"),
    "18a-trust-no-cert": (lambda r: r.v2(wire("v2nocert")), ("--trust",), "no-cert"),
    "18b-trust-agent-mismatch": (lambda r: r.v2(wire("v2agent")), ("--trust",), "agent-mismatch"),
    "19-trust-bless-invalid": (lambda r: r.v2(wire("v2badbless")), ("--trust",), "bless-invalid"),
    "20-trust-registry-absent": (lambda r: r.v2(wire("v2noreg")), ("--trust",), "absent"),
    "21a-trust-unsigned": (lambda r: base("unsigned", "hello there\n").encode(), ("--trust",),
                           "unblessed"),
    "21b-trust-bad": (lambda r: r.v1().replace(b"hello there", b"hello THERE"), ("--trust",),
                      "unblessed"),
    "22-trust-canonicalization-limit": (c_v1_multipart_mixed, ("--trust",), "unblessed"),
    # beyond the table: the same limit in plain mode, and v1 FULL under --trust
    "22p-canonicalization-limit-plain": (c_v1_multipart_mixed, (), "unblessed"),
    "23-trust-v1-full": (lambda r: r.v1(), ("--trust",), "blessed"),
}


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm788"))


def placeholders(rig, text: str) -> str:
    return text.replace(rig.keyline, "<key>").replace(rig.sid, "<sid>")


def per_leg(plain_stdout: str) -> list[str]:
    """The per-leg lines: what a non-json run puts on stdout, less its TRUST: line."""
    return [ln for ln in plain_stdout.splitlines() if not ln.startswith("TRUST:")]


def check(rig, raw: bytes, flags, registry, verifier=VERIFIER, signer=None) -> dict:
    """Run RAW under --json and --json --quiet (and once without --json), assert every
    cross-cutting rule, and return the object with the stdout it came from."""
    plain = rig.run(raw, *flags, registry=registry, verifier=verifier)
    loud = rig.run(raw, "--json", *flags, registry=registry, verifier=verifier)
    quiet = rig.run(raw, "--json", "--quiet", *flags, registry=registry, verifier=verifier)
    assert "Traceback" not in loud.stderr + quiet.stderr + plain.stderr, loud.stderr
    # one line, one object, byte-identical under --quiet
    assert loud.stdout == quiet.stdout, (loud.stdout, quiet.stdout)
    assert loud.stdout.endswith("\n") and loud.stdout.count("\n") == 1, loud.stdout
    assert loud.stdout.isascii()
    obj = json.loads(loud.stdout)
    assert not any(ln.startswith("TRUST:") for ln in loud.stdout.splitlines())
    # rc: the object's, this run's, and the same mail's without --json
    assert obj["rc"] == loud.returncode == quiet.returncode == plain.returncode
    assert obj["schema"] == 1
    assert set(obj) == {"schema", "rc", "result", "reason", "signature", "trust"}
    # the success rule (design 3.1)
    trust = "--trust" in flags
    success = obj["rc"] == 0 or (trust and obj["rc"] == 4)
    assert (obj["signature"] is not None) == success, obj
    if obj["signature"] is not None:
        sig = obj["signature"]
        assert set(sig) == {"version", "h", "covered", "parts", "signer"}
        assert [e["name"] for e in sig["covered"]] == sig["h"]   # only h, in h order
        key = signer or rig.keyline
        assert sig["signer"] == {"session_id": sid_of(key.split()[1]), "key": key}
    # trust: null without --trust; the TRUST: line verbatim with it, or all-null when none
    lines = [ln for ln in plain.stdout.splitlines() if ln.startswith("TRUST:")]
    if not trust:
        assert obj["trust"] is None and lines == []
    else:
        assert set(obj["trust"]) == {"verdict", "leg", "reason", "line", "agent"}
        if lines:
            assert plain.stdout.splitlines()[-1] == lines[0] == obj["trust"]["line"]
            assert obj["reason"] == obj["trust"]["reason"]
        else:
            assert obj["trust"] == dict.fromkeys(obj["trust"])
    # --quiet acts on stderr: the per-leg lines move there, and --quiet drops them
    legs = per_leg(plain.stdout)
    loud_err = loud.stderr.splitlines()
    assert [ln for ln in loud_err if ln in legs] == legs
    assert not set(quiet.stderr.splitlines()) & set(legs), quiet.stderr
    return obj, loud.stdout


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden(rig, name):
    build, flags, registry = CASES[name]
    _, stdout = check(rig, build(rig), flags, registry)
    want = (GOLDEN / f"{name}.json").read_text()
    assert placeholders(rig, stdout) == want


def test_every_golden_file_has_a_case():
    """A golden left behind by a renamed case would pin nothing."""
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(CASES)


def test_the_golden_files_name_no_real_host_or_person():
    for p in GOLDEN.glob("*.json"):
        text = p.read_text()
        for word in re.findall(r"[a-z0-9.-]+@([a-z0-9.-]+)", text):
            assert word == "host.example", (p.name, word)


# ── v2's covered values are the vectors' signed text, line for line ─────────

@pytest.mark.parametrize("vector", ["single", "cc", "atom-headers"])
def test_v2_covered_is_the_vectors_signed_text(rig, vector):
    """Case 3 on the fixed-key vectors (tests/fixtures/sig_v2): every covered value, joined
    back as `name: value`, is the vector's .canon after its first line, byte for byte. They
    are not copied into golden files: their names are the vectors', not this file's."""
    raw = (VECTORS / f"{vector}.eml").read_bytes()
    vectors = json.loads((VECTORS / "vectors.json").read_text())
    key = " ".join(vectors["pubkey"].split()[:2])
    obj, _ = check(rig, raw, (), "unblessed", signer=key)
    canon = (VECTORS / f"{vector}.canon").read_bytes()
    lines = [f"{e['name']}: {v}".encode() for e in obj["signature"]["covered"] for v in e["values"]]
    assert b"\n".join(lines) + b"\n" == canon.split(b"\n", 1)[1]
    vec = vectors["vectors"][vector]
    assert obj["signature"]["h"] == vec["h"] and obj["signature"]["version"] == "2"
    assert [x["digest"] for x in obj["signature"]["parts"]] == vec["p"]
    assert [x["type"] for x in obj["signature"]["parts"]] == vec["leaves"]


# ── no object without a verdict (design 3.4) ────────────────────────────────

def _no_object(stdout: str):
    try:
        json.loads(stdout)
    except ValueError:
        return True
    return False


@pytest.mark.parametrize("argv", [
    ["--json"],                                         # no mail file
    ["--json", "a.eml", "b.eml"],                       # two
    ["--json", "--require-blessing", "a.eml"],          # --require-blessing without --trust
    ["--json", "--trust", "/no/such/mail.eml"],         # an unreadable mail file
    ["--json", "--no-such-flag"],
])
def test_a_usage_error_or_an_unreadable_file_prints_nothing_on_stdout(argv):
    done = subprocess.run([str(VERIFIER), *argv], capture_output=True, text=True)
    assert done.returncode == 3 and done.stdout == "", done.stdout


def test_help_under_json_is_never_an_object():
    done = subprocess.run([str(VERIFIER), "--json", "--help"], capture_output=True, text=True)
    assert done.returncode == 3 and done.stdout and _no_object(done.stdout)


def test_a_broken_install_is_one_unverified_object(rig, tmp_path):
    """No _sig.py beside the verifier: the mail WAS read, so one object, rc 3 plain and
    unverified/verifier-incomplete; under --trust rc 5, ERROR verifier-incomplete."""
    mhs = tmp_path / "mhs"
    mhs.mkdir()
    shutil.copy2(VERIFIER, mhs / "session-mail-verify")
    raw = rig.v1()
    obj, _ = check(rig, raw, (), "unblessed", verifier=mhs / "session-mail-verify")
    assert obj == {"schema": 1, "rc": 3, "result": "unverified", "reason": "verifier-incomplete",
                   "signature": None, "trust": None}
    obj, _ = check(rig, raw, ("--trust",), "unblessed", verifier=mhs / "session-mail-verify")
    assert obj == {"schema": 1, "rc": 5, "result": "unverified", "reason": "verifier-incomplete",
                   "signature": None,
                   "trust": {"verdict": "ERROR", "leg": None, "reason": "verifier-incomplete",
                             "line": "TRUST: ERROR reason=verifier-incomplete", "agent": None}}


def test_without_json_nothing_on_stdout_is_an_object(rig):
    """The flag path (design 4): no --json, no object; the TRUST: line stays last."""
    raw = rig.v2(wire("v2nojson"))
    for flags in [(), ("--quiet",), ("--trust",), ("--trust", "--quiet")]:
        done = rig.run(raw, *flags)
        assert not any(ln.lstrip().startswith("{") for ln in done.stdout.splitlines()), flags
    done = rig.run(raw, "--trust", "--quiet")
    assert done.stdout == "TRUST: UNBLESSED reason=bless-absent\n"


# ── the read-back refuses what it cannot reconstruct (design 2) ─────────────

READ_BACK = _lift("read_back")["read_back"]


def test_read_back_reads_each_value_as_signed():
    assert READ_BACK(b"from: a\ncc: \nx: 1\nx: 2\n", ["from", "cc", "x"]) == [
        {"name": "from", "values": ["a"]}, {"name": "cc", "values": [""]},
        {"name": "x", "values": ["1", "2"]}]
    assert READ_BACK(b"x: caf\xe9\n", ["x"]) == [{"name": "x", "values_b64": ["Y2Fm6Q=="]}]


@pytest.mark.parametrize("text, names", [
    (b"from: a", ["from"]),                       # no final newline
    (b"from: a\nto: b\n", ["from"]),              # a line no name in h accounts for
    (b"to: b\nfrom: a\n", ["from", "to"]),        # out of h order
    (b"from: a\n", ["from", "to"]),               # a name in h with no line
    (b"from:a\n", ["from"]),                      # not `name: `
    (b"From: a\n", ["from"]),                     # the name as signed, case included
    (b"", ["from"]),
])
def test_read_back_fails_closed(text, names, capsys):
    assert READ_BACK(text, names) is None
    assert "covered is null" in capsys.readouterr().err
