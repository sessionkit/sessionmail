"""SM-727 fix-003: `message verify --trust` finds its registry from config,
never a home path in code (design (e), acceptance test g.3; SC5).

The verifier (`mhs/session-mail-verify`) has no default registry: it reads
`$SESSION_REGISTRY` and nothing else. `message verify` reads `"registry"`
from the mail tool's template and host layers (pwd's home, the
`_operator.settings_home` seam the suite already points into its sandbox)
and passes it on as `$SESSION_REGISTRY` when that is unset.

The mail is REALLY signed: `test_seat_init`'s scratch registry, minted seat
key and seat agent, so leg 1 passes and the run reaches leg 2, the registry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_seat_init import (  # noqa: F401 - fixtures, by name
    bin_dir,
    env,
    fake_sendmail,
    git_env,
    mail_from,
    minted,
    registry,
    root,
    seat_agent,
    sender,
    tools,
)

from sessionmail import _operator, _settings
from sessionmail.message import _configured_registry, _verify_env, cli

VERIFIER = Path(__file__).resolve().parents[2] / "src" / "sessionmail" / "mhs" / "session-mail-verify"
HOW = '"registry" in ~/.sessionkit/mail/settings/mail.json'


def write_mail_config(text: str | None) -> Path:
    """TEXT as the host's `~/.sessionkit/mail/settings/mail.json`, under the seam's home."""
    path = _operator.settings_home() / _settings.RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text)
    return path


def verify_trust(eml: Path):
    result = CliRunner().invoke(cli, ["verify", str(eml), "--trust"])
    return result.exit_code, result.stdout, result.stderr


def test_registry_from_config_never_a_home_path(sender, seat_agent, tmp_path,  # noqa: F811
                                               fake_sendmail, minted, monkeypatch):  # noqa: F811
    eml = mail_from(sender, seat_agent, tmp_path, fake_sendmail, "desk.uma")
    monkeypatch.delenv("SESSION_REGISTRY", raising=False)

    # 1. Config names the registry, SESSION_REGISTRY unset: the run reaches
    #    leg 2 and passes it (the cert is there), ending UNBLESSED, not absent.
    write_mail_config(json.dumps({"registry": str(minted["reg"])}))
    rc, out, err = verify_trust(eml)
    assert rc == 4, out + err
    assert "registry-cert     : ✅ ok" in out and "agent: desk.uma" in out
    assert out.splitlines()[-1] == "TRUST: UNBLESSED reason=bless-absent"

    # 2. SESSION_REGISTRY, when set, wins over config: a registry without
    #    this seat's cert fails leg 2 on the cert, never on absence.
    other = tmp_path / "other-registry"
    (other / "certs").mkdir(parents=True)
    monkeypatch.setenv("SESSION_REGISTRY", str(other))
    rc, out, err = verify_trust(eml)
    assert rc == 1, out + err
    assert "no cert for session" in out
    assert out.splitlines()[-1] == "TRUST: FAIL leg=2-registry reason=no-cert"

    #    Set but missing is an ERROR naming the variable, with config set:
    #    config is never a fallback for a set variable.
    monkeypatch.setenv("SESSION_REGISTRY", str(tmp_path / "no-such-registry"))
    rc, out, err = verify_trust(eml)
    assert rc == 5, out + err
    assert out.splitlines()[-1] == "TRUST: ERROR reason=registry-absent"
    assert "$SESSION_REGISTRY" in err and "no-such-registry" in err

    # 3. Neither: ERROR registry-absent, rc 5, and stderr says how to set it.
    monkeypatch.delenv("SESSION_REGISTRY")
    write_mail_config(None)
    rc, out, err = verify_trust(eml)
    assert rc == 5, out + err
    assert out.splitlines()[-1] == "TRUST: ERROR reason=registry-absent"
    assert "SESSION_REGISTRY" in err and HOW in err

    # 4. The script carries no home path: nothing in code names a registry.
    text = VERIFIER.read_text()
    assert "~/Code" not in text and "github.com/mbailey" not in text
    # 5. The key is in the mail tool's schema (additionalProperties: false).
    assert json.loads(_settings.schema())["properties"]["registry"]["type"] == "string"


@pytest.mark.parametrize("text", [
    None,                                   # no file
    "{not json",                            # a bad file is no registry, never a traceback
    '{"registry": ""}',
    '{"registry": 7}',
    '{"hooks": true}',                      # a file without the key
])
def test_an_unset_or_bad_registry_is_none(text):
    write_mail_config(text)
    assert _configured_registry() is None


def test_a_tilde_registry_is_the_password_homes(monkeypatch):
    """`~` is pwd's home (the seam), not `$HOME`: the same home the file is
    read under, so an agent's `$HOME` cannot move it."""
    monkeypatch.setenv("HOME", "/nonexistent-home")
    write_mail_config('{"registry": "~/agents/registry"}')
    assert _configured_registry() == str(_operator.settings_home() / "agents" / "registry")


def test_config_is_passed_only_to_trust_and_only_when_the_variable_is_unset(tmp_path, monkeypatch):
    """The variable stays authoritative: when it is set, the child inherits
    it untouched. A plain verify reads no registry, so no config either."""
    monkeypatch.setenv("SESSION_REGISTRY", str(tmp_path / "no-such-registry"))
    write_mail_config(json.dumps({"registry": str(tmp_path)}))
    assert _verify_env(trust=True) is None          # the child inherits the variable as set
    monkeypatch.delenv("SESSION_REGISTRY")
    assert _verify_env(trust=False) is None
    assert _verify_env(trust=True)["SESSION_REGISTRY"] == str(tmp_path)


# ── SM-803: leg 2 compares the SEAT on both sides ────────────────────────────
#
# Leg 2's anti-spoof used to reduce the two sides differently: the From went
# through local_part() with its +extension STRIPPED, the cert agent through
# nothing but .lower(). A keyed plus-grammar seat (cert agent proj+t-1.worker,
# the keyholder's label, ruling 1b) then failed as itself, and a project key
# (cert agent proj) passed as ANY proj+* seat. Now both sides reduce through
# the verifier's one local_part(): lowercased local part, +extension KEPT,
# domain dropped (the domain is SM-606's question). Beside that reduction, a
# cert agent that carries '@' is refused outright (fix-002, NO LOOSENING): it
# FAILed before SM-803 and still does. Every row below runs the REAL verifier
# over REALLY signed mail.

SID_FROM = "35bf6365-6686-d084-55b4-1507b25b65a2"   # a DM reply's From: a session id


def sid_of(pubkey: str) -> str:
    """The signature-derived sid: sha256(wire blob)[:16], uuid-shaped."""
    h = hashlib.sha256(base64.b64decode(pubkey.split()[1])).digest()[:16].hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def cert_registry(where: Path, pubkey: str, agent: str) -> Path:
    """A scratch registry holding ONE cert for this key, naming AGENT. Leg 2
    reads only session-id, pubkey and agent; no blessing, so a pass is rc 4."""
    kt, kb = pubkey.split()[:2]
    sid = sid_of(pubkey)
    reg = where / "registry"
    (reg / "certs").mkdir(parents=True, exist_ok=True)
    (reg / "certs" / f"{sid}.cert").write_text(
        f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: {agent}\n")
    return reg


def trust_run(eml: Path, reg: Path, **extra):
    done = subprocess.run([str(VERIFIER), str(eml), "--trust"], capture_output=True, text=True,
                          env={**os.environ, "SESSION_REGISTRY": str(reg), **extra})
    said = done.stdout + done.stderr
    assert "signature-crypto  : ✅ good" in said, said      # leg 1 passed: leg 2 is judged
    return done.returncode, said


PASS = (4, "TRUST: UNBLESSED reason=bless-absent")
FAIL = (1, "TRUST: FAIL leg=2-registry reason=agent-mismatch")

#: (cert agent, wire From, verdict). Sent by the REAL sender (v2) under a real
#: seat key, judged by the real verifier. Fixture names only.
SENT_ROWS = [
    # The same seat, the domain on the From: it PASSES now (it FAILed: SM-803).
    ("proj+t-1.worker", "proj+t-1.worker@testhost", PASS),
    ("proj+t-1.worker", "proj+t-1.worker", PASS),
    # A project key is not every proj+* seat: it FAILS now (it PASSED).
    ("proj", "proj+t-1.worker", FAIL),
    # Another seat of the same project (SM-724 inheritance) FAILS, compared
    # with the seat, not with 'proj'.
    ("proj+t-2.worker", "proj+t-1.worker", FAIL),
    # A bare agent name against a plus From stays a FAIL.
    ("worker", "proj+t-1.worker", FAIL),
    # The tagged From, DECIDED: a +tag on the From is part of the seat, so a
    # base key 'eve' does not pass as 'eve+memory' (it PASSED). rca-001 §3: no
    # live wake reaches leg 2 this way (membership keeps the tag).
    ("eve", "eve+memory", FAIL),
    # The domain-qualified agent, RULED (NO
    # LOOSENING): a cert agent carrying '@' FAILS, as it did before SM-803.
    # Its domain names ONE host; dropping it would let the cert pass for that
    # local part From ANY host. Whether a domain is ever compared is SM-606's.
    # The sender's canon_addr drops the domain and does not flag this row:
    # the receiver is the stricter side (tests/test_send_key_seat.py pins it).
    ("eve@testhost", "eve", FAIL),
    # ... even when the From names the same host: the refusal is the '@' in
    # the cert agent, not a comparison of domains (SM-606).
    ("eve@testhost", "eve@testhost", FAIL),
    # A name-addr cert agent carries '@' too.
    ("Eve <eve@testhost>", "eve", FAIL),
    # A session-id From is unchanged: no cert agent is a uuid.
    ("keyseat.alpha", SID_FROM, FAIL),
    # The bare seat that always passed still passes.
    ("eve", "eve", PASS),
]
SENT_IDS = ["plus-seat-qualified-from", "plus-seat", "project-key-vs-plus-seat",
            "other-plus-seat", "bare-agent-vs-plus-seat", "base-key-vs-tagged-from",
            "domain-qualified-agent", "domain-qualified-agent-same-host",
            "domain-qualified-name-addr-agent", "session-id-from", "bare-seat"]


@pytest.mark.parametrize("agent, frm, want", SENT_ROWS, ids=SENT_IDS)
def test_leg_2_compares_the_seat_on_both_sides(sender, seat_agent, tmp_path, fake_sendmail,  # noqa: F811
                                               minted, agent, frm, want):  # noqa: F811
    eml = mail_from(sender, seat_agent, tmp_path, fake_sendmail, frm)
    reg = cert_registry(tmp_path, minted["pub"].read_text(), agent)
    rc, said = trust_run(eml, reg)
    assert (rc, said.strip().splitlines()[-1]) == want, said
    if want == FAIL:
        # The line names the SEAT on both sides: never a stripped 'proj'.
        assert f"From local-part {frm.split('@')[0].lower()!r}" in said, said
        # A domain-qualified cert agent is refused by name (SM-606).
        assert ("is domain-qualified" in said) is ("@" in agent), said


# A v1 mail, built by hand the way the old sender wrote it: OLD mail, still
# verifying, and the only way to sign a From the current sender refuses.

def v1_mail(tmp_path: Path, frm: str) -> tuple[Path, str]:
    from sessionmail import _sender
    key = tmp_path / "v1key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    head = {"From": frm, "To": "uma", "Subject": "s",
            "Date": "Sun, 09 Jun 2030 12:00:00 +0000", "Message-ID": "<1.2.3@hq.session-mail>"}
    body = b"hello\n"
    data = _sender.canonical(head["From"], head["To"], head["Subject"], head["Date"],
                             head["Message-ID"], body)
    signed = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "session-mail@example.com"],
                            input=data, capture_output=True, check=True).stdout.decode()
    b64 = "".join(line for line in signed.splitlines() if not line.startswith("-----"))
    lines = [f"{k}: {v}" for k, v in head.items()] + [
        "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8",
        f"X-Session-Signature: v=1; a=sshsig; n=session-mail@example.com; "
        f"h=from:to:subject:date:message-id; b={b64}"]
    eml = tmp_path / "v1.eml"
    eml.write_bytes(("\n".join(lines) + "\n\n").encode() + body)
    return eml, " ".join(Path(f"{key}.pub").read_text().split()[:2])


#: (cert agent, signed From, verdict) on a v1 mail.
V1_ROWS = [
    # Case and the name-addr form reduce away on both sides: the same seat.
    ("Proj+T-1.Worker", "Worker <proj+t-1.worker@testhost>", PASS),
    ("proj", "Worker <proj+t-1.worker@testhost>", FAIL),
    # An EMPTY reduction never matches: two nothings are not one seat.
    ("<>", "<>", FAIL),
    ("@testhost", "@otherhost", FAIL),
]


@pytest.mark.parametrize("agent, frm, want", V1_ROWS,
                         ids=["case-and-name-addr", "project-key-name-addr", "empty-angle", "empty-at"])
def test_leg_2_on_old_v1_mail(tmp_path, agent, frm, want):
    eml, pub = v1_mail(tmp_path, frm)
    rc, said = trust_run(eml, cert_registry(tmp_path, pub, agent))
    assert (rc, said.strip().splitlines()[-1]) == want, said


# The SM-594 golden vectors: mail signed before this change, From plus seats.
# Leg 1 is untouched (they still verify), and leg 2 now passes their own seat.

SIG_V2 = Path(__file__).resolve().parents[1] / "fixtures" / "sig_v2"
SIG_V2_PUB = json.loads((SIG_V2 / "vectors.json").read_text())["pubkey"]


SIG_V2_NAMES = sorted(json.loads((SIG_V2 / "vectors.json").read_text())["vectors"])


@pytest.mark.parametrize("name", SIG_V2_NAMES)
def test_old_signed_mail_still_verifies_and_passes_as_its_own_seat(tmp_path, name):
    from email.utils import parseaddr
    eml = SIG_V2 / f"{name}.eml"
    frm = next(ln for ln in eml.read_text().splitlines() if ln.startswith("From: "))
    seat = parseaddr(frm[len("From: "):])[1].split("@")[0].lower()   # NOT the code under test
    assert seat
    rc, said = trust_run(eml, cert_registry(tmp_path / "own", SIG_V2_PUB, seat),
                         SESSIONMAIL_ORIGIN="hq.example.net")
    assert (rc, said.strip().splitlines()[-1]) == PASS, said
    base = seat.split("+")[0]
    if base != seat:
        rc, said = trust_run(eml, cert_registry(tmp_path / "base", SIG_V2_PUB, base),
                             SESSIONMAIL_ORIGIN="hq.example.net")
        assert (rc, said.strip().splitlines()[-1]) == FAIL, said


# ONE reduction: the sender's canon_addr() and the verifier's local_part() are
# the two halves of it (the verifier is a standalone script and cannot import
# the sender). Pinned equal here, so neither can drift; no third copy.

PARITY_FORMS = [
    "ivan", "ivan@hq.example.net", "Ivan <ivan@hq.example.net>", "Ivan 7 <ivan@hq>",
    '"Ivan, 7" <ivan@hq>', "Eve <eve+repro-oracle@hq>", "<ivan@hq>",
    "proj+t-1.worker", "proj+t-1.worker@testhost", "Proj+T-1.Worker",
    "Worker <PROJ+t-1.worker@testhost>", "eve@testhost", "eve+memory", SID_FROM,
    "sessionmail+sm-803.worker.4b393e16@testhost",
]


def verifier_local_part():
    src = VERIFIER.read_text()
    start = src.index("def local_part(")
    ns: dict = {}
    exec(src[start:src.index("\ndef ", start + 1)], ns)
    return ns["local_part"]


@pytest.mark.parametrize("value", PARITY_FORMS)
def test_the_sender_and_leg_2_share_one_reduction(value):
    from sessionmail import _sender
    assert _sender.canon_addr(value) == verifier_local_part()(value)


def test_leg_2_reduces_both_sides_with_local_part_and_strips_no_tag():
    """The fix's shape, pinned in the source: no strip_tag anywhere, and the
    cert agent goes through local_part() like the From."""
    src = VERIFIER.read_text()
    assert "def local_part(v):" in src and "strip_tag=" not in src and "if strip_tag" not in src
    assert 'local_part(fields["agent"])' in src
    assert 'local_part(msg.get("from", ""))' in src


def test_leg_2_refuses_a_domain_qualified_cert_agent_beside_the_reduction():
    """NO LOOSENING (SM-803 fix-002, SM-606): the '@' refusal reads the RAW
    cert agent, and local_part() itself is unchanged (it still drops the
    domain, so canon_addr == local_part holds on every form)."""
    src = VERIFIER.read_text()
    assert 'if "@" in fields["agent"]:' in src
    assert verifier_local_part()("eve@testhost") == "eve"
