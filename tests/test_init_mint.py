"""`memory init`'s MINT half and the public seam (`_init.init`), SM-186 do-005,
re-plumbed onto master's own sender in do-012.

The MTA is a FAKE `sendmail` named by $SESSIONMAIL_SENDMAIL, as in
`test_sender.py`: it takes the wire on stdin (`sendmail -t`), as postfix would,
and writes it into BOX's maildir. SIGNING is the package's own v2 path
(`_sender.sign_wire` over the exact wire) with ONE seam underneath it,
`_sender.sign_bytes`, faked to sign with the private half beside whatever
$CLAUDE_SESSION_PUB names, under whichever agent socket the environment it
was handed names. That is the variable pair the real sender signs with, so
"init signs as the box key, never as the session" is measured by a signature
on the delivered file, judged by the REAL verifier (`_root.ssh_verify`,
which reads v2 since do-012) against the box key's `allowed_signers`. The
pre-send probe signs through the same `sign_wire` and the same seam, so it
cannot drift from the mint.

The test environment is a KEYED SESSION whose key is the `stranger`'s. A mint
that leaked the session's signing variables through would be signed by the
stranger and refused by the verifier. The box key's signing half is handed to
init only through `_init.KEY_SOURCE` (the key's home is TM-5054's and is not
ruled, so no environment variable or argument names it).

The box is `alpha`, the seat is `beta`, the hosts are `example.invalid`
(`slipbox/project-conventions.md` convention 9). Nothing here touches a real
box, a real MTA or a real agent: PATH carries no `postmap`, so the sender's
postfix pre-flight is skipped, and $SESSIONMAIL_ORIGIN names the origin so no
`postconf` runs.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from click.testing import CliRunner

from sessionmail import _init, _mcp, _root, _seat, _sender, _sig, memory
from sessionmail._init import InitError, InitResult
import tool_call
from fakes import fake_sign_bytes
from test_root import BOX, Box

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="needs ssh-keygen to sign and check with the fake box key")

DEFAULT = ("[CHARTER]", "[RULES]", "[HOW-TO]", "[BOOT]", "[CONVO]", "[ASKS]")
NS = "session-mail@example.com"
#: The real signer, kept so the one test that wants a live agent can restore it.
REAL_SIGN_BYTES = _sender.sign_bytes

#: The fake MTA: the wire on stdin (`sendmail -t -i -f SENDER`), delivered
#: into BOX's maildir. Knobs, each keyed to the Nth send of the run (1-based):
#: FAKE_FAIL_AT=N:CODE exits CODE and delivers nothing; FAKE_LOST_AT=N exits 0
#: and delivers nothing; FAKE_SIGN_AT=N:PRIVKEY re-signs the wire (v2, over
#: the same bytes) with another key before delivering it. FAKE_QUALIFY=1 does
#: to the delivered copy what postfix does on local submission and a reader
#: can see: cleanup(8) qualifies a BARE From:/To:/Cc: address with
#: @$SESSIONMAIL_ORIGIN (append_at_myorigin; the origin v2 canonicalises a
#: bare address with, so the signature still verifies), and virtual(8)
#: prepends X-Original-To:/Delivered-To:. Without it the delivered To: is the
#: bare box name, which `_root` accepts and the reader's path walk does not
#: (mhs-thread `Members.header_faults` wants `<recip>[+tag]@`; found by the
#: do-008 proof, SM-186). Every send is logged:
#: the signing pub and socket the sender was handed, then the wire's To, From,
#: Subject, In-Reply-To and whether it had a body.
FAKE_SENDMAIL = """#!{python}
import email, os, subprocess, sys
from pathlib import Path
from sessionmail import _sig

def at(var):
    v = os.environ.get(var, "")
    if not v:
        return None
    n, _, rest = v.partition(":")
    return (int(n), rest)

wire = sys.stdin.buffer.read()
count = Path(os.environ["FAKE_COUNT"])
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
msg = email.message_from_bytes(wire)
head, _, body = wire.partition(b"\\n\\n")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join([os.environ.get("CLAUDE_SESSION_PUB", ""),
                             os.environ.get("CLAUDE_SESSION_AGENT_SOCK", ""),
                             msg.get("To", ""), msg.get("From", ""), msg.get("Subject", ""),
                             msg.get("In-Reply-To", ""), "body" if body.strip() else ""]) + "\\n")
fail = at("FAKE_FAIL_AT")
if fail and fail[0] == n:
    sys.stderr.write("fake sendmail: refused\\n")
    sys.exit(int(fail[1]))
other = at("FAKE_SIGN_AT")
if other and other[0] == n:
    # Re-sign v2 over the same wire with PRIVKEY: the delivered copy then
    # verifies against that key, never the box key.
    lines, keep, skipping = head.split(b"\\n"), [], False
    for line in lines:
        if line.lower().startswith(b"x-session-signature:"):
            skipping = True
            continue
        if skipping and line[:1] in (b" ", b"\\t"):
            continue
        skipping = False
        keep.append(line)
    raw = _sig.lf(b"\\n".join(keep) + b"\\n\\n" + body)
    h, p = _sig.h_for(raw), _sig.digests(raw)
    origin = os.environ.get("SESSIONMAIL_ORIGIN", "") if _sig.has_bare_address(raw, h) else ""
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", other[1], "-n", {ns!r}, "-"],
                          input=_sig.canonical(raw, {ns!r}, h, p, origin), capture_output=True,
                          check=True)
    b64 = "".join(l for l in done.stdout.decode().splitlines() if "SSH SIGNATURE" not in l)
    wire = _sig.insert_header(raw, _sig.format_header({ns!r}, h, p, b64))
lost = at("FAKE_LOST_AT")
if not (lost and lost[0] == n):
    to = msg.get("To", "").split("<")[-1].strip("> ").split("@")[0].split("+")[0]
    if os.environ.get("FAKE_QUALIFY"):
        origin = os.environ["SESSIONMAIL_ORIGIN"]
        head, sep, body = wire.partition(b"\\n\\n")
        out = []
        for line in head.split(b"\\n"):
            name, colon, value = line.partition(b":")
            if (colon and not line[:1].isspace()
                    and name.strip().lower() in (b"from", b"to", b"cc") and b"@" not in value):
                v = value.decode("utf-8").strip()
                if "<" in v:
                    v = v.replace(">", "@%s>" % origin)
                else:
                    v = ", ".join("%s@%s" % (a.strip(), origin) for a in v.split(","))
                line = name + b": " + v.encode("utf-8")
            out.append(line)
        rcpt = ("%s@%s" % (to, origin)).encode("utf-8")
        wire = (b"X-Original-To: " + rcpt + b"\\nDelivered-To: " + rcpt + b"\\n"
                + b"\\n".join(out) + sep + body)
    box = Path(os.environ["SESSION_MAIL_ROOT"]) / to / "new"
    (box / ("%d.M%d.example" % (1908540800 + n, n))).write_bytes(wire)
sys.exit(0)
"""


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def keydir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("keys")
    for name in ("box", "stranger"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name,
                        "-f", str(d / name)], check=True)
    pub = (d / "box.pub").read_text().split()
    (d / "allowed_signers").write_text(f"{BOX} {pub[0]} {pub[1]}\n")
    return d


@pytest.fixture
def boxkey(keydir) -> _root.BoxKey:
    """The box key, both halves: what TM-5054's home will hand init."""
    return _root.BoxKey(principal=BOX, allowed_signers=keydir / "allowed_signers",
                        public_key=keydir / "box.pub", agent_sock="/fake/box-agent.sock")


@pytest.fixture
def keyed(boxkey, monkeypatch):
    """The key's home finds the box key for `alpha`, and nothing else."""
    monkeypatch.setattr(_init, "KEY_SOURCE", lambda box, env: boxkey if box == BOX else None)
    return boxkey


@pytest.fixture
def mailroot(tmp_path) -> Path:
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / BOX / sub).mkdir(parents=True)
    return root


@pytest.fixture
def box(mailroot, keydir) -> Box:
    return Box(mailroot, keydir)


class Mta:
    """What the fake sendmail saw, one row per send: `[pub, sock, to, from,
    subject, in_reply_to, body]`."""

    def __init__(self, tmp: Path) -> None:
        self.log, self.count = tmp / "mta.log", tmp / "mta.count"

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [line.split("\x1f") for line in self.log.read_text().splitlines()]


@pytest.fixture
def mta(tmp_path) -> Mta:
    fake = tmp_path / "fake-sendmail"
    fake.write_text(FAKE_SENDMAIL.format(python=sys.executable, ns=NS))
    fake.chmod(0o755)
    m = Mta(tmp_path)
    m.path = fake
    return m


@pytest.fixture
def env(mailroot, mta, keydir, monkeypatch) -> dict[str, str]:
    """A keyed SESSION whose key is the stranger's, a seat, and the fake MTA.
    Set in os.environ too, for the CLI and the MCP door. PATH keeps every
    real directory but the `sbin` ones, so `ssh-keygen` and `git` are found
    and `postmap` is not (no postfix pre-flight against live maps)."""
    path = ":".join(p for p in os.environ.get("PATH", "").split(":") if "sbin" not in p)
    e = {"SESSION_MAIL_ROOT": str(mailroot), "PATH": path,
         "TM_AGENT_ADDRESS": "beta",
         "SESSIONMAIL_SENDMAIL": str(mta.path), "SESSIONMAIL_HOST": "example.invalid",
         "SESSIONMAIL_ORIGIN": "example.invalid", "SESSIONMAIL_RENDER": "off",
         "FAKE_LOG": str(mta.log), "FAKE_COUNT": str(mta.count),
         "CLAUDE_SESSION_PUB": str(keydir / "stranger.pub"),
         "CLAUDE_SESSION_AGENT_SOCK": "/fake/session-agent.sock"}
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    for k in ("FAKE_FAIL_AT", "FAKE_LOST_AT", "FAKE_SIGN_AT", "FAKE_QUALIFY",
              "SESSIONMAIL_SEND_ORACLE",
              "SESSIONMAIL_REQUIRE_SIGNATURE", _root.root_var(BOX)):
        monkeypatch.delenv(k, raising=False)
    return e


def _no_map(name):
    """`_seat.box_via_map`, here: no postfix map knows any box."""
    return None


#: A tool's verb runs in a CHILD (SM-926): both seams reach it by these names
#: (tests/tool_call.py). The fake signer by REFERENCE, from `fakes`, which
#: imports no command module, so the child imports it as it is.
tool_call.CARRIED_FNS[_no_map] = ("returns", None, None)
tool_call.CARRIED_FNS[fake_sign_bytes] = ("ref", "fakes:fake_sign_bytes", None)


@pytest.fixture(autouse=True)
def no_postfix(monkeypatch):
    monkeypatch.setattr(_seat, "box_via_map", _no_map)


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    """The box key's agent, faked ONE layer down: the probe and the mint both
    sign through `_sender.sign_wire`, and only its key-handling half is replaced."""
    monkeypatch.setattr(_sender, "sign_bytes", fake_sign_bytes)
    monkeypatch.setattr(_init, "SIGNER", None)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(_init, "DELIVERY_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(_init, "DELIVERY_POLL_SECONDS", 0.02)


def refused(fn, *args, **kwargs) -> InitError:
    with pytest.raises(InitError) as caught:
        fn(*args, **kwargs)
    return caught.value


def delivered(mailroot: Path) -> dict[str, Path]:
    """Every file in BOX's new/, by bare Message-ID."""
    out = {}
    for p in (mailroot / BOX / "new").iterdir():
        for line in p.read_text().splitlines():
            if line.startswith("Message-ID: "):
                out[line.split(" ", 1)[1].strip("<>")] = p
    return out


def snapshot(mailroot: Path) -> list[tuple[str, int]]:
    d = mailroot / BOX
    return sorted((str(p.relative_to(d)), p.stat().st_mtime_ns) for p in d.rglob("*"))


def receipts(mta: Mta) -> list[str]:
    """The ids the sender MINTED, in send order (epoch.pid.rand-stamped, so a
    typed or rebuilt id could never match), read from the delivered files."""
    return [p.read_text().split("Message-ID: <", 1)[1].split(">", 1)[0]
            for p in sorted(Path(os.environ["SESSION_MAIL_ROOT"], BOX, "new").iterdir())]


# ── the seam, exactly as seam.md §2 ──────────────────────────────────────


def test_the_seam_signature_is_exactly_seam_md_section_2():
    sig = inspect.signature(_init.init)
    assert list(sig.parameters) == ["box", "labels", "plus", "supersede", "record", "env"]
    p = sig.parameters
    assert p["box"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p[n].kind is inspect.Parameter.KEYWORD_ONLY
               for n in ("labels", "plus", "supersede", "record", "env"))
    assert (p["labels"].default, p["plus"].default, p["supersede"].default,
            p["record"].default, p["env"].default) == (None, (), False, None, None)
    assert [f.name for f in dataclasses.fields(InitResult)] == [
        "root_id", "minted", "existing", "recorded", "labels", "superseded"]
    assert dataclasses.fields(InitResult)[0].type == "str"


# ── 4.1: a fresh box, signed as the box key, verified, ids from the receipt ──


def test_a_fresh_box_gets_a_root_and_the_default_six(env, keyed, mta, mailroot):
    r = _init.init(BOX, env=env)
    ids = receipts(mta)
    assert len(ids) == 7 and len(mta.calls()) == 7
    # every field of InitResult, asserted
    assert r.root_id == ids[0] and "<" not in r.root_id          # BARE
    assert r.minted == ("[ROOT]", *DEFAULT)                       # send order, [ROOT] first
    assert r.existing == ()
    assert r.recorded is None
    assert dict(r.labels) == dict(zip(DEFAULT, ids[1:]))          # every id from a receipt
    assert r.superseded is None
    # each delivered message verifies against the box key, the REAL verifier
    files = delivered(mailroot)
    assert set(files) == set(ids)
    assert all(_root.ssh_verify(files[i], keyed) for i in ids)


def test_the_signer_is_the_box_key_never_the_running_session(env, keyed, mta, keydir,
                                                             mailroot):
    _init.init(BOX, env=env, labels=["ASKS"])
    calls = mta.calls()
    assert {c[0] for c in calls} == {str(keydir / "box.pub")}
    assert {c[1] for c in calls} == {"/fake/box-agent.sock"}
    stranger = _root.BoxKey(principal=BOX, allowed_signers=keydir / "stranger-signers")
    pub = (keydir / "stranger.pub").read_text().split()
    stranger.allowed_signers.write_text(f"{BOX} {pub[0]} {pub[1]}\n")
    for path in delivered(mailroot).values():
        assert _root.ssh_verify(path, keyed)
        assert not _root.ssh_verify(path, stranger)


def test_the_root_has_a_body_and_each_label_is_a_bodiless_reply_to_it(env, keyed, mta):
    r = _init.init(BOX, env=env, labels=["asks", "[Boot]"])
    root_call, *label_calls = [c[2:] for c in mta.calls()]
    to, from_, subject, in_reply_to, body = root_call
    assert to.split("@")[0] == BOX and body == "body"
    assert from_.split("<")[-1].strip("> ").split("@")[0] == "beta"
    assert subject == "[ROOT]" and in_reply_to == ""
    for call, name in zip(label_calls, ("[BOOT]", "[ASKS]")):
        to, _, subject, in_reply_to, body = call
        assert to.split("@")[0] == BOX
        assert subject == name and body == ""
        assert in_reply_to == f"<{r.root_id}>"


# ── additive and idempotent (D6, R2a) ────────────────────────────────────


def test_a_second_run_sends_nothing_and_changes_nothing(env, keyed, mta, mailroot):
    first = _init.init(BOX, env=env)
    before, sent = snapshot(mailroot), len(mta.calls())
    again = _init.init(BOX, env=env)
    assert len(mta.calls()) == sent
    assert snapshot(mailroot) == before
    assert again.root_id == first.root_id
    assert again.minted == ()
    assert again.existing == DEFAULT
    assert dict(again.labels) == dict(first.labels)
    assert again.recorded is None and again.superseded is None


def test_a_box_with_asks_gains_only_the_missing_five(env, keyed, mta, box, mailroot):
    box.put("r@example.invalid", "[ROOT]")
    box.put("a@example.invalid", "[ASKS]", in_reply_to="r@example.invalid")
    item = box.put("i@example.invalid", "please", in_reply_to="a@example.invalid",
                   body="an ask\n")
    item_bytes = item.read_bytes()
    r = _init.init(BOX, env=env)
    assert r.root_id == "r@example.invalid"
    assert r.minted == ("[CHARTER]", "[RULES]", "[HOW-TO]", "[BOOT]", "[CONVO]")
    assert r.existing == ("[ASKS]",)
    assert r.labels["[ASKS]"] == "a@example.invalid"
    assert len(r.labels) == 6
    assert item.read_bytes() == item_bytes
    for call in mta.calls():
        assert call[5] == "<r@example.invalid>"


@pytest.mark.parametrize("spelling", ["[asks]", "[Asks]", "[ASKS ]", "[ ASKS ]", "[ASKS] "])
def test_a_variant_spelling_is_the_same_label_and_gains_no_second_copy(
        spelling, env, keyed, mta, box):
    """R2a: the canonical name decides, never the bytes. Case variants and
    trailing-space variants each count as the label they spell."""
    box.put("r@example.invalid", "[ROOT]")
    box.put("a@example.invalid", spelling, in_reply_to="r@example.invalid")
    r = _init.init(BOX, env=env)
    subjects = [c[4] for c in mta.calls()]
    assert "[ASKS]" not in subjects
    assert r.existing == ("[ASKS]",)
    assert r.labels["[ASKS]"] == "a@example.invalid"


def test_a_trailing_space_on_the_subject_line_is_the_same_label(env, keyed, mta, box):
    """The trailing-space fixture, at the END of the Subject: line itself."""
    box.put("r@example.invalid", "[ROOT]")
    box.put("a@example.invalid", "[ASKS]   ", in_reply_to="r@example.invalid")
    r = _init.init(BOX, env=env, labels=["ASKS"])
    assert mta.calls() == []
    assert r.minted == () and r.existing == ("[ASKS]",)


def test_two_spellings_of_one_label_refuse_and_send_nothing(env, keyed, mta, box):
    """The ivan box's shape: its root carries [ASKS] twice."""
    box.put("r@example.invalid", "[ROOT]")
    box.put("a1@example.invalid", "[ASKS]", in_reply_to="r@example.invalid")
    box.put("a2@example.invalid", "[asks]", in_reply_to="r@example.invalid")
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (2, "fork", None)
    assert "a1@example.invalid" in str(err) and "a2@example.invalid" in str(err)
    assert mta.calls() == []


# ── VACATED names (design-002, PROPOSED: the default set does not re-mint) ──


def _renamed(box):
    """[RULES] renamed: [RULINGS] supersedes it, threaded under the root."""
    box.put("r@example.invalid", "[ROOT]")
    box.put("rules@example.invalid", "[RULES]", in_reply_to="r@example.invalid")
    box.put("rulings@example.invalid", "[RULINGS]", in_reply_to="r@example.invalid",
            supersedes="rules@example.invalid")


def test_a_vacated_name_is_not_re_minted_by_the_default_set(env, keyed, mta, box):
    _renamed(box)
    lines: list[str] = []
    r = _init.run(BOX, env=env, report=lines.append)
    assert "[RULES]" not in r.minted and "[RULES]" not in r.labels
    assert r.minted == ("[CHARTER]", "[HOW-TO]", "[BOOT]", "[CONVO]", "[ASKS]")
    assert r.existing == ("[RULINGS]",)
    # the vacating end is named: the rename's live end
    assert any(l.startswith("vacated [RULES] rulings@example.invalid") for l in lines)


@pytest.mark.parametrize("how", [{"labels": ["rules"]}, {"plus": ["[Rules]"]}])
def test_a_vacated_name_is_minted_when_named(how, env, keyed, mta, box):
    _renamed(box)
    r = _init.init(BOX, env=env, **how)
    assert "[RULES]" in r.minted
    assert r.labels["[RULES]"] != "rules@example.invalid"


# ── 4.3: selection over the ruled ten ────────────────────────────────────


def test_labels_replaces_the_default_set(env, keyed, mta):
    r = _init.init(BOX, env=env, labels=["ASKS"])
    assert r.minted == ("[ROOT]", "[ASKS]")
    assert list(r.labels) == ["[ASKS]"]


def test_plus_adds_to_the_default_set_any_spelling(env, keyed, mta):
    r = _init.init(BOX, env=env, plus=["tools", "[ideas]", " Lessons ", "[RULINGS]"])
    assert r.minted == ("[ROOT]", *DEFAULT, "[IDEAS]", "[RULINGS]", "[LESSONS]", "[TOOLS]")


def test_a_comma_string_is_a_list_of_names(env, keyed, mta):
    r = _init.init(BOX, env=env, labels="asks,[BOOT]")
    assert r.minted == ("[ROOT]", "[BOOT]", "[ASKS]")


@pytest.mark.parametrize("kw", [
    {"labels": ["ASKS"], "plus": ["IDEAS"]},       # --labels with --plus
    {"labels": ["GOSSIP"]},                        # outside the ten
    {"plus": ["ideas", "gossip"]},
    {"labels": ()},                                # seam A5: () is usage
    {"labels": ""},
])
def test_selection_refusals_are_usage_before_anything_is_read_or_sent(kw, env, mta,
                                                                      monkeypatch):
    # No key at all: a usage refusal must come before the key's exit 3.
    monkeypatch.setattr(_init, "KEY_SOURCE", None)
    err = refused(_init.init, BOX, env=env, **kw)
    assert (err.code, err.kind, err.result) == (1, "usage", None)
    assert mta.calls() == []


def test_an_unknown_label_prints_the_ten(env, keyed):
    err = refused(_init.init, BOX, env=env, plus=["GOSSIP"])
    assert "'GOSSIP'" in str(err)
    for name in _init.TEN:
        assert name in str(err)
    assert len(_init.TEN) == 10


# ── 4.2: the box key absent or unusable: exit 3, NOTHING sent ────────────


def test_no_key_home_is_exit_3_and_nothing_is_sent(env, mta, mailroot):
    before = snapshot(mailroot)
    err = refused(_init.init, BOX, env=env)      # KEY_SOURCE is the real default
    assert (err.code, err.kind, err.result) == (3, "key", None)
    assert repr(BOX) in str(err)
    assert mta.calls() == [] and snapshot(mailroot) == before


@pytest.mark.parametrize("change", [{"agent_sock": None}, {"public_key": None},
                                    {"public_key": Path("/nonexistent/box.pub")}])
def test_a_key_that_cannot_sign_mints_nothing(change, env, boxkey, mta, monkeypatch):
    weak = dataclasses.replace(boxkey, **change)
    monkeypatch.setattr(_init, "KEY_SOURCE", lambda box, env: weak)
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (3, "key", None)
    assert "cannot sign" in str(err) and repr(BOX) in str(err)
    assert mta.calls() == []


def test_a_key_whose_signing_half_is_not_the_box_key_mints_nothing(env, boxkey, keydir,
                                                                  mta, mailroot,
                                                                  monkeypatch):
    """The right SHAPE (`can_sign`) and the wrong key: a .pub allowed_signers
    does not name. Sent, its [ROOT] would not verify and would refuse every
    later run until a --supersede (the orphan the exit table forbids). The
    probe refuses first, and the box is left as it was: the right key then
    mints into it normally."""
    before = snapshot(mailroot)
    wrong = dataclasses.replace(boxkey, public_key=keydir / "stranger.pub")
    assert wrong.can_sign
    monkeypatch.setattr(_init, "KEY_SOURCE", lambda box, env: wrong)
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (3, "key", None)
    assert "unusable" in str(err) and repr(BOX) in str(err) and "stranger.pub" in str(err)
    assert mta.calls() == [] and snapshot(mailroot) == before
    monkeypatch.setattr(_init, "KEY_SOURCE", lambda box, env: boxkey)
    assert _init.init(BOX, env=env).minted == ("[ROOT]", *DEFAULT)


def test_an_agent_that_will_not_sign_mints_nothing(env, keyed, mta, mailroot,
                                                   monkeypatch):
    """A dead keyholder: the sender alone would send UNSIGNED (RULED 7(b)),
    and with $SESSIONMAIL_REQUIRE_SIGNATURE would refuse. The probe's signer
    fails first, so nothing is sent at all."""
    monkeypatch.setattr(_init, "SIGNER", lambda wire, env, err: None)
    before = snapshot(mailroot)
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (3, "key", None)
    assert mta.calls() == [] and snapshot(mailroot) == before


def test_an_agent_that_dies_after_the_probe_sends_nothing_unsigned(env, keyed, mta,
                                                                   mailroot, monkeypatch):
    """The probe passed, then the keyholder stopped answering. Without
    $SESSIONMAIL_REQUIRE_SIGNATURE the sender would send the [ROOT] UNSIGNED
    (RULED 7(b)) and init would stop at exit 3 with an orphan in BOX. With it
    (SM-590), the sender refuses before the MTA: nothing reaches BOX at all."""
    def probe_only(wire, env, err):
        with monkeypatch.context() as m:
            m.setattr(_sender, "sign_bytes", fake_sign_bytes)
            return _sender.sign_wire(wire, env, err)

    monkeypatch.setattr(_init, "SIGNER", probe_only)
    monkeypatch.setattr(_sender, "sign_bytes", lambda data, env, ns: None)
    before = snapshot(mailroot)
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (2, "send", None)
    assert "identity" in str(err) and "NOTHING WAS SENT" in str(err)
    assert "MAY have been sent" not in str(err)
    assert mta.calls() == [] and snapshot(mailroot) == before


@pytest.mark.skipif(shutil.which("ssh-agent") is None or shutil.which("ssh-add") is None,
                    reason="needs a real ssh-agent for the default signer")
def test_the_default_probe_signs_through_a_real_agent(boxkey, keydir, monkeypatch):
    """`SIGNER` None is `_sender.sign_wire`, the sender's own v2 signer, with
    the REAL `sign_bytes` under it: a real agent holding the box key passes;
    the same agent with the stranger's .pub, and an agent socket that holds
    nothing, do not."""
    import tempfile
    import time

    monkeypatch.setattr(_init, "SIGNER", None)
    monkeypatch.setattr(_sender, "sign_bytes", REAL_SIGN_BYTES)
    run = tempfile.mkdtemp(prefix="sm-agent-", dir="/tmp")
    sock = os.path.join(run, "a.sock")
    agent = subprocess.Popen(["ssh-agent", "-D", "-a", sock], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.02)
        e = {"PATH": os.environ.get("PATH", ""), "SSH_AUTH_SOCK": sock}
        # The .pub files ALONE: beside its private half, ssh-keygen signs from
        # the file and never asks the agent.
        for name in ("box", "stranger"):
            shutil.copy(keydir / f"{name}.pub", os.path.join(run, f"{name}.pub"))
        live = dataclasses.replace(boxkey, agent_sock=sock,
                                   public_key=Path(run, "box.pub"))
        assert _init.probe(live, e) is False                    # the agent holds nothing
        subprocess.run(["ssh-add", "-q", str(keydir / "box")], env={**os.environ, **e},
                       check=True, capture_output=True)
        assert _init.probe(live, e) is True
        assert _init.probe(dataclasses.replace(live, public_key=Path(run, "stranger.pub")),
                           e) is False
    finally:
        agent.terminate()
        agent.wait(timeout=5)
        shutil.rmtree(run, ignore_errors=True)


def test_a_key_that_cannot_sign_still_reads_a_complete_box(env, keyed, boxkey, mta,
                                                           monkeypatch):
    first = _init.init(BOX, env=env)
    weak = dataclasses.replace(boxkey, agent_sock=None)
    monkeypatch.setattr(_init, "KEY_SOURCE", lambda box, env: weak)
    again = _init.init(BOX, env=env)
    assert again.root_id == first.root_id and again.minted == ()


# ── verify after each send: stop at the first that does not verify ───────


def test_a_root_that_does_not_verify_stops_before_any_label(env, keyed, mta, keydir):
    err = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"1:{keydir / 'stranger'}"})
    assert (err.code, err.kind) == (3, "signature")
    assert len(mta.calls()) == 1
    root = receipts(mta)[0]
    assert root in str(err)
    assert err.result == InitResult(root_id=root, minted=("[ROOT]",), existing=(),
                                    recorded=None, labels={})


def test_a_label_that_does_not_verify_stops_there_and_names_the_root(env, keyed, mta,
                                                                     keydir):
    err = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"3:{keydir / 'stranger'}"})
    ids = receipts(mta)
    assert (err.code, err.kind) == (3, "signature") and len(ids) == 3
    assert err.result.root_id == ids[0]
    assert err.result.minted == ("[ROOT]", "[CHARTER]", "[RULES]")
    assert dict(err.result.labels) == {"[CHARTER]": ids[1]}     # only what verified


def test_a_rerun_completes_a_tree_a_failed_run_left(env, keyed, mta, keydir):
    """Not atomic, and that is fine (D5): the idempotent contract is the
    recovery. The unverified [RULES] is not canon, so it is minted again."""
    first = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"3:{keydir / 'stranger'}"})
    r = _init.init(BOX, env=env)
    assert r.root_id == first.result.root_id
    assert r.existing == ("[CHARTER]",)
    assert r.minted == ("[RULES]", "[HOW-TO]", "[BOOT]", "[CONVO]", "[ASKS]")


def test_a_send_that_never_arrives_is_not_verified(env, keyed, mta):
    err = refused(_init.init, BOX, env=env | {"FAKE_LOST_AT": "1"})
    assert (err.code, err.kind) == (3, "delivery")
    assert err.result.minted == ("[ROOT]",) and len(mta.calls()) == 1


def test_an_mta_refusal_after_the_root_carries_the_root(env, keyed, mta):
    err = refused(_init.init, BOX, env=env | {"FAKE_FAIL_AT": "2:3"})
    ids = receipts(mta)
    assert (err.code, err.kind) == (2, "send")
    assert err.result.root_id == ids[0] and err.result.minted == ("[ROOT]",)
    assert "exit 3" in str(err)


def test_an_mta_refusal_of_the_root_has_sent_nothing(env, keyed, mta):
    err = refused(_init.init, BOX, env=env | {"FAKE_FAIL_AT": "1:2"})
    assert (err.code, err.kind, err.result) == (2, "send", None)


def test_an_mta_exit_after_the_wire_went_says_it_may_have_been_sent(env, keyed, mta):
    """The MTA took the wire and exited non-zero: the sender says `sent:
    unknown`, and init says so rather than guessing either way."""
    err = refused(_init.init, BOX, env=env | {"FAKE_FAIL_AT": "1:75"})
    assert (err.code, err.kind, err.result) == (2, "send", None)
    assert "MAY have been sent" in str(err) and "exit 3" in str(err)


def test_a_receipt_that_names_no_message_id_stops_and_says_it_may_have_been_sent(
        env, keyed, mta, monkeypatch):
    """The sender returned, but its receipt carries no Message-ID: init cannot
    find or verify what it sent, so it stops, exit 2 kind send (verify-001
    round 2: the exit table did not name this cause)."""
    monkeypatch.setattr(_sender, "send", lambda req, body, env: _sender.Receipt("", []))
    err = refused(_init.init, BOX, env=env)
    assert (err.code, err.kind, err.result) == (2, "send", None)
    assert "names no Message-ID" in str(err) and "MAY have been sent" in str(err)


# ── the reader's v2 leg (`_root.ssh_verify`, do-012): what it must refuse ──


def _unsigned(raw: bytes) -> bytes:
    """RAW with its X-Session-Signature field (and its continuations) removed."""
    head, _, body = _sig.lf(raw).partition(b"\n\n")
    keep, skipping = [], False
    for line in head.split(b"\n"):
        if line.lower().startswith(b"x-session-signature:"):
            skipping = True
            continue
        if skipping and line[:1] in (b" ", b"\t"):
            continue
        skipping = False
        keep.append(line)
    return b"\n".join(keep) + b"\n\n" + body


def _signed_v2(raw: bytes, private: Path, h: list[str] | None = None) -> bytes:
    """RAW re-signed v2 by PRIVATE over h= (default: the sender's own h_for).
    The fake MTA leaves addresses bare, so ORIGIN is the environment's, as the
    sender and the reader both take it."""
    wire = _unsigned(raw)
    h, p = h or _sig.h_for(wire), _sig.digests(wire)
    origin = _sig.origin_of() if _sig.has_bare_address(wire, h) else ""
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(private), "-n", NS, "-"],
                          input=_sig.canonical(wire, NS, h, p, origin), capture_output=True,
                          check=True)
    b64 = "".join(l for l in done.stdout.decode().splitlines() if "SSH SIGNATURE" not in l)
    return _sig.insert_header(wire, _sig.format_header(NS, h, p, b64))


@pytest.fixture
def minted_root(env, keyed, mta, mailroot) -> Path:
    """One [ROOT] in BOX, minted v2 by init and verifying against the box key."""
    r = _init.init(BOX, env=env, labels=["ASKS"])
    path = delivered(mailroot)[r.root_id]
    assert _root.ssh_verify(path, keyed)
    return path


def test_a_v2_root_whose_body_is_altered_after_signing_does_not_verify(minted_root, keyed):
    """p= is signed, the body is not: only the digest check sees the edit."""
    raw = minted_root.read_bytes()
    assert b"Minted" in raw
    minted_root.write_bytes(raw.replace(b"Minted", b"Forged"))
    assert not _root.ssh_verify(minted_root, keyed)


def test_a_v2_signature_below_the_floor_does_not_verify(minted_root, keyed, keydir):
    """Signed by the BOX key, crypto good, but h= leaves out In-Reply-To (a
    floor name): the reader refuses it before any crypto, as the verifier does.
    The control re-signs over the full h= and verifies, so the floor is what
    refused it."""
    raw = minted_root.read_bytes()
    minted_root.write_bytes(_signed_v2(raw, keydir / "box"))
    assert _root.ssh_verify(minted_root, keyed)
    short = [n for n in _sig.h_for(_unsigned(raw)) if n != "in-reply-to"]
    minted_root.write_bytes(_signed_v2(raw, keydir / "box", h=short))
    assert not _root.ssh_verify(minted_root, keyed)


@pytest.mark.parametrize("old, new", [(b"v=2;", b"v=1;"),           # downgraded to v1
                                      (b"v=2;", b"v=2; v=2;"),      # a tag twice
                                      (b" p=", b" q=")])            # a required tag gone
def test_a_mangled_v2_header_does_not_verify(minted_root, keyed, old, new):
    raw = minted_root.read_bytes()
    assert raw.count(old) == 1
    minted_root.write_bytes(raw.replace(old, new))
    assert not _root.ssh_verify(minted_root, keyed)


# ── the other usage rows, and supersede ──────────────────────────────────


def test_no_seat_is_usage(env, keyed, mta):
    bare = {k: v for k, v in env.items() if k != "TM_AGENT_ADDRESS"}
    err = refused(_init.init, BOX, env=bare)
    assert (err.code, err.kind) == (1, "usage")
    assert mta.calls() == []


def test_supersede_on_a_box_with_no_root_is_usage(env, keyed, mta):
    err = refused(_init.init, BOX, env=env, supersede=True)
    assert (err.code, err.kind, err.result) == (1, "usage", None)
    assert mta.calls() == []


# ── the record ───────────────────────────────────────────────────────────


#: SM-866: a rig commit pins what it needs, so no signer or hook of the
#: runner's can refuse it (conftest also scrubs the runner's git config).
PINNED = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")


def _repo(tmp_path) -> Path:
    r = tmp_path / "store"
    r.mkdir()
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         *PINNED]
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    f = r / "PROJECT.md"
    f.write_text("---\nname: alpha\nmail_grammar: scope  # a comment\n---\n\n# alpha\n")
    subprocess.run(["git", "-C", str(r), "add", "PROJECT.md"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "seed"], check=True)
    return f


def test_record_writes_the_minted_root_and_a_rerun_records_nothing(env, keyed, mta,
                                                                   tmp_path, monkeypatch):
    f = _repo(tmp_path)
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(k, v)
    r = _init.init(BOX, env=env, labels=["ASKS"], record=f)
    assert r.recorded and len(r.recorded) >= 7
    assert f'mail_root: "{r.root_id}"' in f.read_text()
    assert "mail_grammar: scope  # a comment" in f.read_text()
    again = _init.init(BOX, env=env, labels=["ASKS"], record=f)
    assert again.recorded is None and again.root_id == r.root_id


def test_an_unfit_record_refuses_before_anything_is_sent(env, keyed, mta, tmp_path):
    f = tmp_path / "no-front-matter.md"
    f.write_text("# just a heading\n")
    err = refused(_init.init, BOX, env=env, record=f)
    assert (err.code, err.kind, err.result) == (1, "record", None)
    assert mta.calls() == []


# ── the CLI: a thin wrapper, InitError.code is the exit ──────────────────


def cli(*args):
    return CliRunner().invoke(memory.cli, ["init", *args], prog_name="memory")


def test_the_cli_prints_every_id_and_ends_with_the_export_line(env, keyed, mta):
    result = cli(BOX)
    assert result.exit_code == 0, result.output
    ids = receipts(mta)
    out = result.stdout.splitlines()
    assert out[0] == f"minted  [ROOT] {ids[0]}"
    for name, mid in zip(DEFAULT, ids[1:]):
        assert f"minted  {name} {mid}" in out
    assert out[-2] == f"root {ids[0]} (6 labels)"
    assert out[-1] == f"export ALPHA_ROOT='{ids[0]}'"
    again = cli(BOX)
    # SM-839: a second run that fails says WHICH path it took, its own words.
    said = f"stdout:\n{again.stdout}\nstderr:\n{again.stderr}"
    assert again.exit_code == 0, said
    assert f"exists  [ASKS] {ids[6]}" in again.stdout, said
    assert len(mta.calls()) == 7


def test_seven_sends_in_one_second_that_draw_one_rand_still_mint_seven_ids(env, keyed, mta,
                                                                          monkeypatch):
    """SM-839, the flake above: every send of one run shares the pid, and
    with the clock in one second two labels that drew one `$RANDOM` were one
    Message-ID. The first run passed; the second refused the box as a fork
    (exit 2, "verified copies of one Message-ID disagree"). Pinned here: a
    repeated draw is drawn again, so seven sends are seven ids, and the
    second run finds the six labels."""
    draws = iter([101, 4242, 202, 303, 404, 4242, 505, 606])
    monkeypatch.setattr(_sender.random, "randint", lambda a, b: next(draws))
    monkeypatch.setattr(_sender, "_DRAWN", {})
    monkeypatch.setattr(_sender.time, "time", lambda: 1908540800.0)
    first = cli(BOX)
    assert first.exit_code == 0, first.output
    ids = receipts(mta)
    assert len(set(ids)) == 7 and {i.split(".")[0] for i in ids} == {"1908540800"}
    assert [i.split(".")[2].split("@")[0] for i in ids] == [
        "101", "4242", "202", "303", "404", "505", "606"]
    again = cli(BOX)
    said = f"stdout:\n{again.stdout}\nstderr:\n{again.stderr}"
    assert again.exit_code == 0, said
    assert f"exists  [ASKS] {ids[6]}" in again.stdout, said
    assert len(mta.calls()) == 7


@pytest.mark.parametrize("args, code", [
    (["--labels", "ASKS", "--plus", "IDEAS"], 1),
    (["--plus", "GOSSIP"], 1),
    (["--labels", ""], 1),
    (["--supersede"], 1),            # no root yet
])
def test_cli_usage_rows_exit_1_from_the_body(args, code, env, keyed, mta):
    result = cli(BOX, *args)
    assert result.exit_code == code, result.output
    assert result.stderr.startswith("memory init: ")
    assert mta.calls() == []


def test_cli_maps_the_code_of_every_refusal(env, mta, box, keyed, monkeypatch):
    assert cli("nobody-zz-not-a-box").exit_code == 2
    box.put("r@example.invalid", "[ROOT]")
    box.put("a1@example.invalid", "[ASKS]", in_reply_to="r@example.invalid")
    box.put("a2@example.invalid", "[asks]", in_reply_to="r@example.invalid")
    assert cli(BOX).exit_code == 2
    monkeypatch.setattr(_init, "KEY_SOURCE", None)
    result = cli(BOX)
    assert result.exit_code == 3 and "box key" in result.stderr
    assert mta.calls() == []


def test_cli_names_what_was_already_sent(env, keyed, mta, keydir, monkeypatch):
    monkeypatch.setenv("FAKE_SIGN_AT", f"2:{keydir / 'stranger'}")
    result = cli(BOX)
    ids = receipts(mta)
    assert result.exit_code == 3
    assert f"already sent: [ROOT] [CHARTER]; root {ids[0]}" in result.stderr


def test_the_cli_and_the_library_leave_the_same_result(env, keyed, mta, box):
    box.put("r@example.invalid", "[ROOT]")
    box.put("a@example.invalid", "[ASKS]", in_reply_to="r@example.invalid")
    lib = _init.init(BOX, env=env, labels=["ASKS", "BOOT"])
    assert cli(BOX, "--labels", "ASKS,BOOT").exit_code == 0
    assert _init.init(BOX, env=env, labels=["ASKS", "BOOT"]) == InitResult(
        root_id="r@example.invalid", minted=(), existing=("[BOOT]", "[ASKS]"),
        recorded=None, labels=lib.labels)


# ── init -> the reader: what init mints, `memory ls --box` lists (do-008 N3) ──


@pytest.mark.skipif(shutil.which("notmuch") is None,
                    reason="no notmuch: the reader reads a real index")
def test_the_reader_lists_what_init_minted(env, keyed, mta, mailroot, tmp_path):
    """End to end, inside the suite: `init` mints through the fake MTA, which
    qualifies the delivered addresses as postfix does (FAKE_QUALIFY); a scratch
    notmuch indexes the box; the branch's own `memory ls --box` (mhs-thread
    underneath) lists the root and the six labels, and the PATH WALK into one
    of them resolves, which is the read a bare `To:` breaks. The reader runs in
    an environment built from nothing: a scratch HOME and notmuch database, the
    hint only from the `export` line's variable."""
    r = _init.init(BOX, env=env | {"FAKE_QUALIFY": "1"})
    assert r.minted == ("[ROOT]", *DEFAULT)
    db = tmp_path / "notmuch"
    db.mkdir()
    home = tmp_path / "reader-home"
    cfg = home / ".config" / "notmuch" / "sessionkit" / "config"     # the host's profile
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"[database]\npath={db}\nmail_root={mailroot}\n[new]\ntags=inbox;\n"
                   f"[search]\nexclude_tags=\n[maildir]\nsynchronize_flags=false\n")
    reader = {"HOME": str(home), "PATH": env["PATH"], "LANG": "en_US.UTF-8", "TERM": "dumb",
              "SESSION_MAIL_ROOT": str(mailroot),
              "SESSIONMAIL_HOST": "example.invalid", "SESSIONMAIL_ORIGIN": "example.invalid",
              "SESSIONMAIL_RENDER": "off", _root.root_var(BOX): r.root_id}
    subprocess.run(["notmuch", f"--config={cfg}", "new", "--quiet"], env=reader, check=True,
                   capture_output=True)
    memory_bin = str(Path(sys.executable).parent / "memory")

    def ls(*args):
        done = subprocess.run([memory_bin, "ls", *args, "--box", BOX], env=reader,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr

    code, out, err = ls("/")
    assert code == 0, (out, err)
    for name in DEFAULT:
        assert name in out, (name, out)
    assert "6 labels" in out, out
    code, out, err = ls("/ASKS")
    assert code == 0, (out, err)


# ── the MCP door: no box key, nothing that counts ────────────────────────


def test_the_mcp_tool_without_the_box_key_mints_nothing(env, mta, mailroot, monkeypatch):
    """Built on the tool door by default, so PROVED there. The session IS keyed
    (the stranger's key), and the box key's home finds nothing."""
    monkeypatch.setattr(_init, "KEY_SOURCE", None)
    server = _mcp.server(memory.cli, "memory")
    before = snapshot(mailroot)

    async def main():
        from mcp.client._memory import InMemoryTransport
        from mcp.client.session import ClientSession

        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("memory_init", {"box": BOX})

    result = anyio.run(main)
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert result.is_error is True
    assert "exit 3" in text and "box key" in text and repr(BOX) in text
    assert mta.calls() == []
    assert snapshot(mailroot) == before
    assert cli(BOX).stderr.strip() in text   # exactly as the CLI says it


# ── --supersede: Q8 as ruled, a NEW TREE in two steps (SM-186 do-007) ─────
#
# Step 1 mints the new [ROOT] with the box key, unthreaded, carrying
# `Supersedes:` and no `In-Reply-To:`, then its labels, each verified. Step 2,
# and only then, moves the pointer (`--record`). The pointer is the blessing.
# The old tree is never touched.


@pytest.fixture
def store(tmp_path, monkeypatch) -> Path:
    """A git repo holding PROJECT.md, the `--record` FILE, and a git identity."""
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(k, v)
    return _repo(tmp_path)


def names(f: Path) -> str:
    """The bare id FILE's `mail_root:` names."""
    return _root.record_hint(f)


def tree(mailroot: Path) -> dict[str, bytes]:
    """Every file in BOX, by path, with its bytes: the old tree must not move."""
    d = mailroot / BOX
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture
def spy_record(monkeypatch, mta, mailroot, keyed):
    """`_record.record`, observed at the moment it is called: how many sends the
    MTA had seen, and whether the root it is handed is in BOX and verifies."""
    from sessionmail import _record

    real, seen = _record.record, []

    def spy(path, root_id, **kw):
        files = delivered(mailroot)
        seen.append({"sends": len(mta.calls()), "root": root_id,
                     "verified": root_id in files and _root.ssh_verify(files[root_id], keyed)})
        return real(path, root_id, **kw)

    monkeypatch.setattr(_record, "record", spy)
    return seen


def first_root(env, store) -> InitResult:
    r = _init.init(BOX, env=env, record=store)
    assert names(store) == r.root_id and len(r.minted) == 7
    return r


def test_supersede_mints_a_new_tree_then_moves_the_pointer(env, keyed, mta, mailroot, store):
    r1 = first_root(env, store)
    old = tree(mailroot)
    r = _init.init(BOX, env=env, supersede=True, record=store)
    ids = receipts(mta)[7:]
    # step 1: a whole new tree, every id from a receipt
    assert len(ids) == 7 and len(mta.calls()) == 14
    assert r.root_id == ids[0] != r1.root_id
    assert r.superseded == r1.root_id                       # BARE, the seam's field
    assert r.minted == ("[ROOT]", *DEFAULT) and r.existing == ()
    assert dict(r.labels) == dict(zip(DEFAULT, ids[1:]))
    # the new root: Supersedes: the old, NOT a reply to it, body names it
    wire = delivered(mailroot)[r.root_id].read_text()
    head, _, body = wire.partition("\n\n")
    assert f"\nSupersedes: <{r1.root_id}>" in head
    assert "\nIn-Reply-To:" not in head and "\nReferences:" not in head
    assert r1.root_id in body
    assert mta.calls()[7][5] == ""                           # the root's In-Reply-To, as sent
    assert all(c[5] == f"<{r.root_id}>" for c in mta.calls()[8:])   # labels: under the NEW root
    assert all(_root.ssh_verify(delivered(mailroot)[i], keyed) for i in ids)
    # step 2: the pointer, committed
    assert r.recorded and names(store) == r.root_id
    # the old tree: every byte where it was
    assert {k: v for k, v in tree(mailroot).items() if k in old} == old


def test_after_a_supersede_the_old_root_is_hidden_by_default(env, keyed, mta, store):
    """S5's HIDDEN half (spec delta, the S5 scenario): the read half reports
    the superseded root as superseded, never as live or canon, and a plain run
    afterwards anchors on the new root and mints nothing. S5's --all half
    (the reveal) is reader work, deferred to SM-764 item 8: NOT proved here."""
    r1 = first_root(env, store)
    r = _init.init(BOX, env=env, supersede=True, record=store)
    state = _root.read_root(BOX, env=env, record=store, key_source=_init.KEY_SOURCE)
    assert (state.root_id, state.superseded) == (r.root_id, (r1.root_id,))
    assert set(state.labels.values()) == set(r.labels.values())     # none of the old tree's
    again = _init.init(BOX, env=env, record=store)
    assert (again.root_id, again.minted, again.recorded) == (r.root_id, (), None)
    assert len(mta.calls()) == 14


def test_the_pointer_moves_only_after_every_send_verified(env, keyed, mta, store,
                                                           spy_record):
    """The ORDER, measured where it happens: when the pointer step is called,
    all seven sends of the new tree are already in BOX and the root verifies.
    Swapping the two steps fails this test (the do-007 mutation check)."""
    first_root(env, store)
    seen_before = len(spy_record)
    r = _init.init(BOX, env=env, supersede=True, record=store)
    call = spy_record[seen_before]
    assert call == {"sends": 14, "root": r.root_id, "verified": True}


def test_a_new_root_that_does_not_verify_leaves_the_pointer_where_it_was(
        env, keyed, mta, keydir, store, spy_record):
    r1 = first_root(env, store)
    seen_before = len(spy_record)
    err = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"8:{keydir / 'stranger'}"},
                  supersede=True, record=store)
    assert (err.code, err.kind) == (3, "signature")
    assert len(mta.calls()) == 8                             # nothing after the root
    assert err.result.superseded == r1.root_id and err.result.minted == ("[ROOT]",)
    assert names(store) == r1.root_id and len(spy_record) == seen_before


def _failing_commit(monkeypatch):
    """The record's commit fails once, with words that are not a race."""
    from sessionmail import _record

    real, left = _record._git, ["fatal: disk full"]

    def run(cwd, args):
        if args[0] == "commit" and left:
            return subprocess.CompletedProcess(["git", *args], 128, "", left.pop(0))
        return real(cwd, args)

    monkeypatch.setattr(_record, "_git", run)


def test_a_failed_pointer_step_names_both_ids_and_a_rerun_completes_it(
        env, keyed, mta, store, monkeypatch):
    """design-002 review N-2, and the slice's seam criterion: the pointer step
    fails after R2 is minted and verified -> exit 2, `.result` names BOTH ids,
    the pointer still names R1. The same command again completes the move and
    mints nothing."""
    r1 = first_root(env, store)
    _failing_commit(monkeypatch)
    err = refused(_init.init, BOX, env=env, supersede=True, record=store)
    r2 = receipts(mta)[7]
    assert (err.code, err.kind) == (2, "record")
    assert (err.result.root_id, err.result.superseded) == (r2, r1.root_id)
    assert err.result.minted == ("[ROOT]", *DEFAULT)
    assert r1.root_id in str(err) and r2 in str(err)
    assert "--supersede --record" in str(err) and "mints nothing new" in str(err)
    assert names(store) == r1.root_id                        # the edit was undone
    # a plain run refuses: moving the pointer is the custodian's act
    plain = refused(_init.init, BOX, env=env, record=store)
    assert (plain.code, plain.kind) == (2, "hint") and "--supersede" in str(plain)
    # the same command again: no new tree, the pointer moves
    done = _init.init(BOX, env=env, supersede=True, record=store)
    assert (done.root_id, done.superseded, done.minted) == (r2, r1.root_id, ())
    assert done.existing == DEFAULT and done.recorded
    assert names(store) == r2 and len(mta.calls()) == 14


def test_a_supersede_stopped_at_a_label_is_finished_then_the_pointer_moves(
        env, keyed, mta, keydir, store):
    r1 = first_root(env, store)
    err = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"10:{keydir / 'stranger'}"},
                  supersede=True, record=store)
    assert (err.code, err.kind) == (3, "signature")
    r2 = err.result.root_id
    assert err.result.superseded == r1.root_id and names(store) == r1.root_id
    # stopped again, in the RESUMED run: its refusal still names both ids
    again = refused(_init.init, BOX, env=env | {"FAKE_SIGN_AT": f"11:{keydir / 'stranger'}"},
                    supersede=True, record=store)
    assert (again.code, again.result.root_id, again.result.superseded) == (3, r2, r1.root_id)
    assert again.result.minted == ("[RULES]",) and names(store) == r1.root_id
    done = _init.init(BOX, env=env, supersede=True, record=store)
    assert (done.root_id, done.superseded) == (r2, r1.root_id)
    assert done.existing == ("[CHARTER]",)
    assert done.minted == ("[RULES]", "[HOW-TO]", "[BOOT]", "[CONVO]", "[ASKS]")
    assert names(store) == r2


def test_the_transition_supersedes_a_root_the_box_key_never_signed(
        env, keyed, mta, box, mailroot, store):
    """A box whose root predates its custodian (sessionmail's): `--supersede`
    is the ONLY mechanism, with no inherit mode. Plain init refuses exit 3;
    the supersede mints a box-signed tree superseding that root; afterwards
    the old root is named not canon and a plain run mints nothing."""
    old = "ivan-root@example.invalid"
    box.put(old, "[ROOT]", signer="stranger")
    box.put("eve-asks@example.invalid", "[ASKS]", in_reply_to=old, signer="stranger")
    store.write_text(store.read_text().replace("name: alpha", f'name: alpha\nmail_root: "{old}"'))
    subprocess.run(["git", "-C", str(store.parent), *PINNED, "commit", "-qam", "hint"], check=True)
    before = tree(mailroot)
    assert refused(_init.init, BOX, env=env, record=store).code == 3
    assert mta.calls() == []
    r = _init.init(BOX, env=env, supersede=True, record=store)
    assert r.superseded == old and r.minted == ("[ROOT]", *DEFAULT) and r.existing == ()
    assert f"Supersedes: <{old}>" in delivered(mailroot)[r.root_id].read_text()
    assert names(store) == r.root_id
    assert {k: v for k, v in tree(mailroot).items() if k in before} == before
    state = _root.read_root(BOX, env=env, record=store, key_source=_init.KEY_SOURCE)
    assert (state.root_id, state.not_canon) == (r.root_id, (old,))
    assert _init.init(BOX, env=env, record=store).minted == ()


def test_the_transition_with_no_hint_supersedes_the_one_root_there_is(env, keyed, mta, box):
    box.put("ivan-root@example.invalid", "[ROOT]", signer="stranger")
    r = _init.init(BOX, env=env, supersede=True, labels=["ASKS"])
    assert r.superseded == "ivan-root@example.invalid" and r.minted == ("[ROOT]", "[ASKS]")
    assert r.recorded is None


def test_a_stale_export_naming_the_old_root_never_mints_a_third(env, keyed, mta, store):
    """After a completed supersede, a `$ALPHA_ROOT` still naming R1 under
    --supersede is a no-op completion (decision (b)), never a third root."""
    r1 = first_root(env, store)
    r2 = _init.init(BOX, env=env, supersede=True, record=store)
    stale = _init.init(BOX, env=env | {"ALPHA_ROOT": r1.root_id}, supersede=True,
                       record=store)
    assert (stale.root_id, stale.superseded, stale.minted, stale.recorded) == (
        r2.root_id, r1.root_id, (), None)
    assert len(mta.calls()) == 14


def test_the_cli_reports_the_supersede_and_ends_with_the_export_line(env, keyed, mta,
                                                                      store):
    r1 = first_root(env, store)
    result = cli(BOX, "--supersede", "--record", str(store))
    assert result.exit_code == 0, result.output
    r2 = receipts(mta)[7]
    out = result.stdout.splitlines()
    assert out[0] == f"minted  [ROOT] {r2} (supersedes {r1.root_id})"
    assert any(line.startswith(f"recorded {store} ") for line in out)
    assert out[-3:] == [f"superseded {r1.root_id}", f"root {r2} (6 labels)",
                        f"export ALPHA_ROOT='{r2}'"]


def test_the_cli_names_both_ids_when_the_pointer_step_fails(env, keyed, mta, store,
                                                             monkeypatch):
    r1 = first_root(env, store)
    _failing_commit(monkeypatch)
    result = cli(BOX, "--supersede", "--record", str(store))
    r2 = receipts(mta)[7]
    assert result.exit_code == 2
    assert result.stderr.splitlines()[-1] == (
        f"memory init: already sent: [ROOT] {' '.join(DEFAULT)}; root {r2}; "
        f"supersedes {r1.root_id}")
