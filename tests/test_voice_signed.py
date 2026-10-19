"""SM-782: TRUSTED voice is a keeper mail the keeper SIGNED, judged by the one verifier.

O22 (S), built against a FIXTURE registry and inert in production until the
keeper signs (EARS-230). Every case is REAL crypto, as tests/test_verify_json.py
does it: a keeper key made for the test, a registry whose cert names it with agent
`heard`, a blessing key, and the shipped `session-mail-verify --trust --json` run
as it ships, through `_voice.keeper_signed`. No second verifier.

The trusted gate (`_voice.verdict_trusts`): `trust.verdict` FULL (blessed) with
rc 0 and a signature; the cert agent a keeper address; and every `Ears-*` header the
mail carries named in the signature's `h=`. Anything else is UNVERIFIED voice.

Then the trusted path end to end, against the same fixture: the whole view, the
speaker believed, `done` earned, calls unclamped; and the pager's two seams
(`_entry_view`, `arrival_verdict`) wired to the file.

Run: uv run pytest -q tests/test_voice_signed.py
"""
from __future__ import annotations

import base64
import email
import hashlib
import os
import shutil
import subprocess
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from types import SimpleNamespace

import pytest

from sessionmail import _mhs, _sig, _views, _voice, pager, rules
from sessionmail._voice import TRUSTED, UNVERIFIED

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="ssh-keygen not on this machine")

NS = "session-mail@example.com"          # the verifier's shipped MAIL_NS default
BLESS_NS = "session-bless@example.com"
BLESS_PRINCIPAL = "alice@bless"
SCRUB = ("SESSIONMAIL_ORIGIN", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_VERIFY_SELF", "SESSION_REGISTRY",
         "SESSION_BLESS_NS", "SESSION_BLESS_PRINCIPAL", _mhs.ORACLE_VAR)
#: this host, for the keeper test (never the box's own hostname)
HOST = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "example.ts.net"}
BODY = "hey eve, the build is green. " * 1000          # 29,000 characters: past TEXT_CAP


def keygen(path: Path) -> tuple[str, str]:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    kt, kb = (path.parent / (path.name + ".pub")).read_text().split()[:2]
    return kt, kb


def sid_of(kb: str) -> str:
    h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class Key:
    """One signing key and its session id."""

    def __init__(self, path: Path):
        self.path = path
        kt, kb = keygen(path)
        self.sid, self.keyline = sid_of(kb), f"{kt} {kb}"

    def sign(self, data: bytes) -> str:
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.path), "-n", NS],
                              input=data, capture_output=True, check=True)
        return "".join(x for x in done.stdout.decode().splitlines() if x and "SSH SIGNATURE" not in x)


class Rig:
    """The keeper's key, a session's key, a blessing key, and registries naming them."""

    def __init__(self, d: Path):
        self.d = d
        self.keeper = Key(d / "keeper")
        self.session = Key(d / "session")
        self.bless = d / "bless"
        bt, bb = keygen(self.bless)
        self.allowed = f"{BLESS_PRINCIPAL} {bt} {bb}\n"
        self.n = 0

    def registry(self, name: str, certs: dict[Key, str], blessed: bool = True) -> Path:
        """A registry holding a cert per KEY naming its agent; each blessed when BLESSED."""
        r = self.d / f"reg-{name}"
        (r / "certs").mkdir(parents=True)
        (r / "allowed_signers").write_text(self.allowed)
        for key, agent in certs.items():
            cert = r / "certs" / f"{key.sid}.cert"
            cert.write_text(f"session-birth-v1\nsession-id: {key.sid}\npubkey: {key.keyline}\n"
                            f"agent: {agent}\n")
            if blessed:
                subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.bless), "-n",
                                BLESS_NS, str(cert)], check=True, capture_output=True)
                (r / "certs" / f"{key.sid}.cert.sig").rename(
                    r / "certs" / f"{key.sid}.cert.blessed-by.{BLESS_PRINCIPAL}.sig")
        return r

    def env(self, registry: Path) -> dict[str, str]:
        """The verifier's environment: this process's, scrubbed, naming REGISTRY."""
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env["SESSION_REGISTRY"] = str(registry)
        return env

    def file(self, raw: bytes) -> Path:
        self.n += 1
        f = self.d / f"mail{self.n}.eml"
        f.write_bytes(raw)
        return f


def turn(sender: str = "Ears <heard@hq>", *, body: str = BODY, extra: str = "") -> bytes:
    """A keeper turn as the wire carries it, unsigned."""
    return (f"From: {sender}\nTo: heard@hq\nSubject: hey eve\n"
            f"Date: Thu, 03 Jan 2030 00:00:00 +0000\nMessage-ID: <turn{len(extra)}@hq>\n"
            f"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
            f"Ears-Kind: spoken\nEars-Device: airpods\nEars-Speaker: judy@hq\n"
            f"Ears-Rule: eve@hq=call\n{extra}\n{body}\n").encode()


def v2(key: Key, wire: bytes, h: list[str] | None = None) -> bytes:
    """WIRE signed v2 by KEY, `h=` every header it carries unless H is given."""
    h = _sig.h_for(wire) if h is None else h
    p = _sig.digests(wire)
    b = key.sign(_sig.canonical(wire, NS, h, p, ""))
    return _sig.insert_header(wire, _sig.format_header(NS, h, p, b))


def v1(key: Key, wire: bytes) -> bytes:
    """WIRE signed v1 by KEY: five fixed headers and the body, no `Ears-*`."""
    verifier = Path(_mhs.MHS_DIR) / "session-mail-verify"
    text = verifier.read_text()
    src = text[text.index("<<'PYEOF'\n") + len("<<'PYEOF'\n"):text.index("\nPYEOF\n")]
    import ast
    import sys
    keep = [n for n in ast.parse(src).body
            if (isinstance(n, ast.FunctionDef) and n.name in ("local_part", "canonical"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "HDRS" for t in n.targets))]
    ns = {"sys": sys, "email": email, "base64": base64}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(verifier), "exec"), ns)
    b = key.sign(ns["canonical"](email.message_from_bytes(wire)))
    head, _, rest = wire.partition(b"\n\n")
    line = f"X-Session-Signature: v=1; a=sshsig; n={NS}; h={_sig.V1_FIELDS}; b={b}"
    return head + b"\n" + line.encode() + b"\n\n" + rest


def parse(raw: bytes):
    return BytesParser(policy=default_policy).parsebytes(raw)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    return Rig(tmp_path_factory.mktemp("sm782"))


@pytest.fixture(scope="module")
def keeper_reg(rig):
    """The fixture registry the trusted path is built against: the keeper's
    key, cert agent `heard`, blessed; a session's key, cert agent its seat."""
    return rig.registry("keeper", {rig.keeper: "heard", rig.session: "sessionmail+t-1.worker"})


def signed(rig, raw: bytes, registry: Path) -> bool:
    path = rig.file(raw)
    return _voice.keeper_signed(path, parse(raw), rig.env(registry))


def state(rig, raw: bytes, registry: Path) -> str:
    path = rig.file(raw)
    return _voice.recognise(parse(raw), HOST, _voice.signed_file(path, rig.env(registry))).state


# == the trusted gate, through the verifier ===================================


def test_a_keeper_turn_signed_v2_by_the_blessed_keeper_cert_is_trusted(rig, keeper_reg):
    raw = v2(rig.keeper, turn())
    assert signed(rig, raw, keeper_reg) is True
    assert state(rig, raw, keeper_reg) == TRUSTED


def test_the_same_turn_unsigned_is_unverified_and_runs_no_verifier(rig, keeper_reg, monkeypatch):
    def no_child(*a, **k):
        raise AssertionError("an unsigned mail must not run the verifier")
    monkeypatch.setattr(_voice.subprocess, "run", no_child)
    raw = turn()
    assert signed(rig, raw, keeper_reg) is False
    assert state(rig, raw, keeper_reg) == UNVERIFIED


def test_an_unblessed_keeper_cert_is_unverified(rig):
    # FULL only (SM-782's tightening). UNBLESSED is a cert nobody vouched for.
    reg = rig.registry("unblessed", {rig.keeper: "heard"}, blessed=False)
    raw = v2(rig.keeper, turn())
    assert signed(rig, raw, reg) is False
    assert state(rig, raw, reg) == UNVERIFIED


def test_a_session_key_is_never_the_keeper(rig, keeper_reg):
    # A seat's own key, blessed, signing a mail From the keeper's address:
    # leg 2 refuses (agent-mismatch), so it is unverified.
    raw = v2(rig.session, turn())
    assert signed(rig, raw, keeper_reg) is False
    assert state(rig, raw, keeper_reg) == UNVERIFIED


def test_a_foreign_key_with_no_cert_is_unverified(rig, keeper_reg, tmp_path):
    stranger = Key(tmp_path / "stranger")
    raw = v2(stranger, turn())
    assert signed(rig, raw, keeper_reg) is False


def test_a_bad_signature_is_unverified(rig, keeper_reg):
    raw = v2(rig.keeper, turn()).replace(b"the build is green", b"the build is RED!!", 1)
    assert signed(rig, raw, keeper_reg) is False


def test_a_cert_agent_heard_does_not_vouch_for_a_tagged_from(rig, keeper_reg):
    # MEASURED, session-mail-verify:253-281 and :432-437 @ 57f9fa6: leg 2 keeps
    # the +extension, so cert agent `heard` proves only `From: heard@<host>`.
    raw = v2(rig.keeper, turn("Ears <heard+airpods@hq>"))
    assert signed(rig, raw, keeper_reg) is False
    assert state(rig, raw, keeper_reg) == UNVERIFIED


def test_a_tagged_keeper_with_its_own_device_cert_is_trusted(rig, tmp_path):
    device = Key(tmp_path / "device")
    reg = rig.registry("device", {device: "heard+airpods"})
    raw = v2(device, turn("Ears <heard+airpods@hq>"))
    assert signed(rig, raw, reg) is True
    assert state(rig, raw, reg) == TRUSTED


def test_a_v1_signature_never_covers_the_ears_headers(rig, keeper_reg):
    # v1 signs from/to/subject/date/message-id and the body: a forged
    # Ears-Speaker or Ears-Rule under it would ride free, so it is unverified.
    raw = v1(rig.keeper, turn())
    path = rig.file(raw)
    done = subprocess.run([*_mhs.oracle("verify"), str(path), "--trust", "--json"],
                          capture_output=True, env=rig.env(keeper_reg))
    assert done.returncode == 0, done.stdout       # the verifier itself says FULL
    assert signed(rig, raw, keeper_reg) is False


def test_a_v2_signature_that_leaves_an_ears_header_out_of_h_is_unverified(rig, keeper_reg):
    wire = turn()
    h = [n for n in _sig.h_for(wire) if n != "ears-speaker"]
    raw = v2(rig.keeper, wire, h)
    assert signed(rig, raw, keeper_reg) is False


def test_an_ears_header_added_after_signing_is_unverified(rig, keeper_reg):
    raw = v2(rig.keeper, turn())
    head, _, rest = raw.partition(b"\n\n")
    forged = head + b"\nX-Ears-Speaker: eve@hq\n\n" + rest
    assert signed(rig, forged, keeper_reg) is False


def test_no_registry_is_unverified(rig, tmp_path):
    raw = v2(rig.keeper, turn())
    assert signed(rig, raw, tmp_path / "no-such-registry") is False


# == the gate itself, on objects (pure) =======================================


def _obj(**over):
    obj = {"rc": 0, "signature": {"h": ["from", "ears-kind"]},
           "trust": {"verdict": "FULL", "agent": "heard"}}
    obj.update(over)
    return obj


@pytest.mark.parametrize("obj", [
    _obj(rc=4, trust={"verdict": "UNBLESSED", "agent": "heard"}),
    _obj(trust={"verdict": "FAIL", "agent": "heard"}),
    _obj(signature=None),
    _obj(trust={"verdict": "FULL", "agent": "eve"}),
    _obj(trust={"verdict": "FULL", "agent": "heard+"}),
    _obj(trust={"verdict": "FULL", "agent": None}),
    _obj(signature={"h": ["from"]}),
    _obj(signature={"h": ["from", "Ears-Kind"]}),        # upper case covers no field
    {}, None, [], "FULL",
])
def test_the_gate_fails_closed(obj):
    assert _voice.verdict_trusts(obj, ("ears-kind",)) is False


def test_the_gate_passes_full_with_every_ears_header_covered():
    assert _voice.verdict_trusts(_obj(), ("ears-kind",)) is True
    assert _voice.verdict_trusts(_obj(trust={"verdict": "FULL", "agent": "heard+a"}),
                                 ("ears-kind",)) is True


def test_ears_fields_reads_both_spellings_once_each():
    msg = parse(b"From: a@hq\nEars-Kind: spoken\nX-Ears-Speaker: x\nears-kind: again\n"
                b"Earsx: no\n\nbody\n")
    assert _voice.ears_fields(msg) == ("ears-kind", "x-ears-speaker")


def test_the_verifier_is_asked_once_per_file(rig, keeper_reg, monkeypatch):
    calls = []
    real = _voice.keeper_signed
    monkeypatch.setattr(_voice, "keeper_signed",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    raw = v2(rig.keeper, turn())
    seam = _voice.signed_file(rig.file(raw), rig.env(keeper_reg))
    msg = parse(raw)
    assert seam(msg) and seam(msg)
    assert len(calls) == 1


# == the trusted path end to end, against the fixture =========================


def test_trusted_voice_gets_the_whole_view_and_its_speaker(rig, keeper_reg):
    raw = v2(rig.keeper, turn())
    seam = _voice.signed_file(rig.file(raw), rig.env(keeper_reg))
    v = _views.view(parse(raw), "7", HOST, signed=seam)
    assert v.kind == _views.WHOLE and v.voice is True and v.unverified is False
    # whole up to the CEILING (16,000), not the mail cap: BODY is 29,000
    assert v.chars == _voice.CEILING and v.cut == len(BODY.rstrip()) - _voice.CEILING
    assert v.heard == _views.Heard(device="airpods", speaker="judy")
    assert _views.UNVERIFIED_FENCE not in v.body
    assert pager._earned(v) == ()                 # cut at the ceiling: seen only
    short = v2(rig.keeper, turn(body="hey eve, short and whole"))
    whole = _views.view(parse(short), "8", HOST,
                        signed=_voice.signed_file(rig.file(short), rig.env(keeper_reg)))
    assert whole.voice and whole.given_whole
    assert pager._earned(whole) == (("done", pager.WHOLE_VOICE_REASON),)


def test_unverified_voice_from_the_same_bytes_unsigned_is_fenced(rig, keeper_reg):
    raw = turn()
    seam = _voice.signed_file(rig.file(raw), rig.env(keeper_reg))
    v = _views.view(parse(raw), "7", HOST, signed=seam)
    assert v.kind == _views.TEXT and v.voice is False and v.unverified is True
    assert v.chars == _views.TEXT_CAP
    assert v.heard == _views.Heard(device="airpods", speaker="", verified=False)
    assert pager._earned(v) == ()


def _judge(raw: bytes, registry: Path, rig, text: str) -> rules.Verdict:
    seam = _voice.signed_file(rig.file(raw), rig.env(registry))
    facts = rules.Facts.from_bytes(raw, HOST, signed=seam)
    seat, errors = rules.parse(text, "seat")
    assert not errors
    me = rules.Me.of(seat="eve", host="hq")
    return rules.judge(facts, [rules.Layer("seat", seat), rules.built_in()], me)


def test_trusted_calls_are_not_clamped(rig, keeper_reg):
    raw = v2(rig.keeper, turn())
    v = _judge(raw, keeper_reg, rig, "start call:$me\n")
    assert v.action == "start" and v.rule == "start call:$me"


def test_unverified_calls_are_clamped_to_carry(rig, keeper_reg):
    v = _judge(turn(), keeper_reg, rig, "start call:$me\n")
    assert v.action == "wake" and v.carry is True
    assert v.rule == f"start call:$me {rules.CLAMP_NOTE}"


# == the pager's seams are wired to the file ==================================


@pytest.fixture
def host_env(monkeypatch, rig, keeper_reg):
    for k in SCRUB:
        monkeypatch.delenv(k, raising=False)
    for k, v in HOST.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SESSION_REGISTRY", str(keeper_reg))


def test_the_ride_along_view_asks_the_verifier_of_the_file(rig, host_env):
    for raw, expect in ((v2(rig.keeper, turn()), True), (turn(), False)):
        path = rig.file(raw)
        entry = SimpleNamespace(path=path, name=path.name)
        v = pager._entry_view(entry, object(), "<turn@hq>", lambda: ("sess-a", ()))
        assert v is not None and v.voice is expect and v.unverified is (not expect)


def test_the_arrival_verdict_asks_the_verifier_of_the_file(rig, host_env):
    seat, _ = rules.parse("wake! call:$me\n", "seat")
    layers = [rules.Layer("seat", seat), rules.built_in()]
    me = rules.Me.of(seat="eve", host="hq")
    good = pager.arrival_verdict(rig.file(v2(rig.keeper, turn())), layers, me)
    assert good is not None and good.rule == "wake! call:$me"
    bare = pager.arrival_verdict(rig.file(turn()), layers, me)
    assert bare is not None and bare.rule == f"wake! call:$me {rules.CLAMP_NOTE}"
