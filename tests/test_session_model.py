"""SM-918: every mail names the model that wrote it, in `Session-Model:`, signed.

What is pinned here (the task README's success criteria, design.md's tests):

  - WHERE the id comes from: a Claude Code seat's transcript,
    `<config>/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl`, its LAST main-chain
    assistant turn (never a `<synthetic>` one, never a subagent's sidechain
    turn); else `$SESSIONMAIL_MODEL`, which a non-Claude harness sets; else
    nothing, and then NO header;
  - the header is signed: altered after signing is BAD, and one ADDED unsigned
    is refused (a claim header, `_sig.CLAIM_NAMES`);
  - a mail with no model verifies, and the golden vectors signed before this
    change verify exactly as before;
  - no caller can type it: `--header` and a structured `headers=` both refuse;
  - the MCP `message_send` door stamps it too, because it runs `message send`.

Fixtures are `eve` / `ivan` at `testhost`, never a seat. No dates are made up here.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _sig
from test_sender import (EX_OK, EX_REFUSED, HOST, FAKE_SENDMAIL, body_file,  # noqa: F401
                         bin_dir, env, fake_sendmail, headers_of, keyed, root, send, sender,
                         verifier)
from test_sender_v2 import send_req, tags_of, verify

MODEL = "claude-opus-5-5"
SID = "35bf6365-6686-d084-55b4-1507b25b65a2"        # the `keyed` fixture's
VECTORS = Path(__file__).parent / "fixtures" / "sig_v2"


# ── a fake Claude Code config dir ────────────────────────────────────────────

def turn(model: str, *, sidechain: bool = False) -> dict:
    return {"type": "assistant", "isSidechain": sidechain,
            "message": {"role": "assistant", "model": model, "content": []}}


def user(text: str = "go") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def transcript(config: Path, sid: str, records: list[dict],
               slug: str = "-Users-eve-Code-elsewhere") -> Path:
    """`<config>/projects/<slug>/<sid>.jsonl`, one JSON record a line, as Claude Code
    writes it. The slug is a LAUNCH cwd, deliberately not the test's."""
    d = config / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


#: The design's source pin: an earlier turn names another model, the last real turn names
#: MODEL, and Claude Code's own synthetic turn and a subagent's turn come after it.
PINNED = [user(), turn("claude-sonnet-5-0"), user(), turn(MODEL),
          turn("claude-haiku-5-0", sidechain=True), turn("<synthetic>"), user("thanks")]


@pytest.fixture
def claude_seat(env, tmp_path):
    """A Claude Code seat: its session id set, its transcript under `$HOME/.claude`."""
    env["CLAUDE_CODE_SESSION_ID"] = SID
    transcript(Path(env["HOME"]) / ".claude", SID, PINNED)
    return env


def model_of(wire: bytes) -> list[str]:
    return [v for k, v in headers_of(wire) if k.lower() == "session-model"]


# ── where the id comes from ──────────────────────────────────────────────────

def test_the_id_is_the_last_main_chain_assistant_turn_of_the_seats_transcript(sender, tmp_path):
    home = tmp_path / "home"
    transcript(home / ".claude", SID, PINNED)
    assert sender.writing_model({"HOME": str(home), "CLAUDE_CODE_SESSION_ID": SID}) == MODEL


def test_claude_config_dir_moves_the_transcript_root(sender, tmp_path):
    config = tmp_path / "elsewhere"
    transcript(config, SID, [turn(MODEL)])
    transcript(tmp_path / "home" / ".claude", SID, [turn("claude-sonnet-5-0")])
    env = {"HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(config),
           "CLAUDE_CODE_SESSION_ID": SID}
    assert sender.writing_model(env) == MODEL


def test_a_turn_far_past_the_first_window_is_still_found(sender, tmp_path):
    """The last assistant turn sits behind a tool result larger than the first window:
    the window grows until it holds a whole record."""
    big = {"type": "user", "message": {"role": "user", "content": "x" * (3 * sender.TRANSCRIPT_TAIL)}}
    home = tmp_path / "home"
    transcript(home / ".claude", SID, [turn("claude-sonnet-5-0"), turn(MODEL), big])
    assert sender.writing_model({"HOME": str(home), "CLAUDE_CODE_SESSION_ID": SID}) == MODEL


def test_sessionmail_model_is_the_other_harness_source(sender, tmp_path):
    env = {"HOME": str(tmp_path), "SESSIONMAIL_MODEL": "  gemma-3-27b-it\n"}
    assert sender.writing_model(env) == "gemma-3-27b-it"


def test_the_transcript_wins_over_sessionmail_model(sender, tmp_path):
    transcript(tmp_path / ".claude", SID, [turn(MODEL)])
    env = {"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": SID,
           "SESSIONMAIL_MODEL": "gemma-3-27b-it"}
    assert sender.writing_model(env) == MODEL


def test_a_transcript_with_no_usable_turn_falls_to_sessionmail_model(sender, tmp_path):
    transcript(tmp_path / ".claude", SID, [user(), turn("<synthetic>"), turn("two words")])
    env = {"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": SID}
    assert sender.writing_model(env) is None
    assert sender.writing_model({**env, "SESSIONMAIL_MODEL": "gemma-3-27b-it"}) == "gemma-3-27b-it"


@pytest.mark.parametrize("value", ["", "   ", "opus 4", "a\nX-Machine: forged", "<synthetic>",
                                   "-leading-dash", "x" * 200])
def test_a_value_that_is_not_one_id_shaped_token_is_no_model(sender, tmp_path, value):
    assert sender.writing_model({"HOME": str(tmp_path), "SESSIONMAIL_MODEL": value}) is None


@pytest.mark.parametrize("sid", ["../../etc/passwd", "a/b", "*", ""])
def test_a_session_id_that_is_not_a_file_name_reads_nothing(sender, tmp_path, sid):
    transcript(tmp_path / ".claude", "abc", [turn(MODEL)])
    assert sender.writing_model({"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": sid}) is None


def test_a_transcript_gone_between_the_glob_and_the_stat_is_no_model(sender, tmp_path,
                                                                     monkeypatch):
    """A transcript removed after the glob found it: `is_file` saw it, then the stat
    for its mtime raises. That is no model, never a failed send."""
    transcript(tmp_path / ".claude", SID, [turn(MODEL)])
    real_stat, real_is_file, seen = Path.stat, Path.is_file, set()

    def is_file(self):
        found = real_is_file(self)
        seen.add(self.name)                       # the glob saw it ...
        return found

    def stat(self, *a, **kw):
        if self.name in seen:                     # ... and now it is gone
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "stat", stat)
    env = {"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": SID}
    assert sender.writing_model(env) is None
    assert sender.writing_model({**env, "SESSIONMAIL_MODEL": "gemma-3-27b-it"}) == "gemma-3-27b-it"


def test_an_unreadable_transcript_is_no_model_and_never_raises(sender, tmp_path):
    p = transcript(tmp_path / ".claude", SID, [turn(MODEL)])
    p.write_bytes(b"\xff\xfe not json\n{\"type\": \"assistant\", \"message\": 7}\n")
    assert sender.writing_model({"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": SID}) is None


# ── the header on the wire ───────────────────────────────────────────────────

def test_a_claude_code_seats_mail_carries_the_exact_id(sender, claude_seat, tmp_path,
                                                       fake_sendmail):
    rc, _, err = send(sender, claude_seat, tmp_path)
    assert rc == EX_OK, err
    h = headers_of(fake_sendmail.wire)
    assert model_of(fake_sendmail.wire) == [MODEL]
    assert [k for k, _ in h][-3:] == ["X-Session-From", "Session-Model", "X-Machine"]


def test_no_model_known_means_no_header(sender, env, tmp_path, fake_sendmail):
    env["CLAUDE_CODE_SESSION_ID"] = SID                 # a session, but no transcript
    rc, _, err = send(sender, env, tmp_path)
    assert rc == EX_OK, err
    assert model_of(fake_sendmail.wire) == []
    assert b"unknown" not in fake_sendmail.wire.lower()


# ── signed ───────────────────────────────────────────────────────────────────

@pytest.fixture
def keyed_seat(keyed):
    transcript(Path(keyed["HOME"]) / ".claude", SID, PINNED)
    return keyed


def test_the_model_is_signed_and_the_mail_verifies(sender, keyed_seat, tmp_path, fake_sendmail,
                                                   verifier):
    send_req(sender, keyed_seat)
    wire = fake_sendmail.wire
    assert model_of(wire) == [MODEL]
    assert "session-model" in tags_of(wire)["h"].split(":")
    done = verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_model_altered_after_signing_is_bad(sender, keyed_seat, tmp_path, fake_sendmail,
                                                verifier):
    send_req(sender, keyed_seat)
    wire = fake_sendmail.wire
    forged = wire.replace(f"Session-Model: {MODEL}\n".encode(),
                          b"Session-Model: gemma-3-27b-it\n")
    assert forged != wire
    done = verify(verifier, tmp_path, forged)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "signature-crypto  : ❌ BAD" in done.stdout


def test_a_model_removed_after_signing_is_bad(sender, keyed_seat, tmp_path, fake_sendmail,
                                              verifier):
    send_req(sender, keyed_seat)
    stripped = fake_sendmail.wire.replace(f"Session-Model: {MODEL}\n".encode(), b"")
    assert verify(verifier, tmp_path, stripped).returncode == 1


def test_a_model_signed_with_none_verifies_and_one_added_unsigned_is_refused(
        sender, keyed, tmp_path, fake_sendmail, verifier):
    send_req(sender, keyed)                              # a session, no transcript: no model
    wire = fake_sendmail.wire
    assert model_of(wire) == []
    assert "session-model" not in tags_of(wire)["h"].split(":")
    done = verify(verifier, tmp_path, wire)
    assert done.returncode == 0, done.stdout + done.stderr
    added = _sig.insert_header(wire, "Session-Model: claude-opus-5-5")
    done = verify(verifier, tmp_path, added)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "BELOW FLOOR: h= lacks session-model" in done.stdout


def test_session_model_is_a_claim_header():
    assert _sig.is_claim("session-model")


# ── back-compatible: mail signed before this change ──────────────────────────

def _vectors() -> list[str]:
    return sorted(json.loads((VECTORS / "vectors.json").read_text())["vectors"])


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on this machine")
@pytest.mark.parametrize("name", _vectors())
def test_a_vector_signed_before_this_change_still_verifies(tmp_path, verifier, name):
    raw = (VECTORS / f"{name}.eml").read_bytes()
    assert b"session-model" not in raw.lower()
    h = _sig.parse_v2(_sig.parse_header(_sig.signature_value(raw)))[1]
    assert _sig.below_floor(raw, h) == []
    origin = json.loads((VECTORS / "vectors.json").read_text())["vectors"][name]["origin"]
    done = verify(verifier, tmp_path, raw, origin=origin)
    assert done.returncode == 0, done.stdout + done.stderr
    added = _sig.insert_header(raw, "Session-Model: claude-opus-5-5")
    assert verify(verifier, tmp_path, added, origin=origin).returncode == 1


# ── never typed ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spelling", ["Session-Model: x", "session-model=x", "SESSION-MODEL: x"])
def test_header_flag_cannot_set_it(fake_sendmail, monkeypatch, spelling):
    """`message send --header` (the CLI door, `_send.parse_headers`): refused, kind
    `header`, the refusal naming where the id comes from, and nothing sent."""
    from click.testing import CliRunner
    from sessionmail import _send
    from sessionmail.message import cli
    with pytest.raises(ValueError, match="'Session-Model' is written by send itself"):
        _send.parse_headers((spelling,))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ivan")     # a seat, so the header is what refuses
    r = CliRunner().invoke(cli, ["send", "beta", "-s", "hi", "--no-body", "--header", spelling,
                                 "--json"])
    said = json.loads(r.stdout)
    assert r.exit_code == EX_REFUSED and said["sent"] == "no" and said["kind"] == "header", said
    assert "harness" in said["message"]
    assert fake_sendmail.wire is None


def test_structured_headers_cannot_set_it(sender, env, fake_sendmail):
    with pytest.raises(sender.Refused) as e:
        send_req(sender, env, headers=[("Session-Model", "claude-opus-5-5")])
    assert "written by send itself" in str(e.value)
    assert fake_sendmail.wire is None


# ── the MCP door stamps it too ───────────────────────────────────────────────

def test_mcp_message_send_stamps_the_model_and_takes_no_argument_for_it(tmp_path, monkeypatch):
    """The real child (`python -m sessionmail.message send`), as test_mcp_send's decoy test
    runs it, from a seat whose transcript names MODEL."""
    from sessionmail import _mcp, message
    from test_mcp_send import call
    props = _mcp.server(message.cli, "message")._tool_manager.get_tool(
        "message_send").parameters["properties"]
    assert not any("model" in p for p in props), props
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    wire = tmp_path / "wire"
    for var in ("SESSIONMAIL_SEND_ORACLE", "CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK",
                "CLAUDE_SESSION_PUB", "FAKE_SENDMAIL_RC", "PYTHONPATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(tmp_path / "argv"))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "sendmail"))
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(tmp_path / "mail"))
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    transcript(tmp_path / ".claude", SID, PINNED)
    result, _ = call({"to": ["beta"], "subject": "the point", "body": "words\n"})
    assert result.is_error is False, [getattr(c, "text", "") for c in result.content]
    assert model_of(wire.read_bytes()) == [MODEL]


# ── a plus seat (SM-920): the stamp signs and verifies under its key ─────────
#
# SM-918 was built before 0.3.0 let `message seat init` mint a plus seat
# (`heard+airpods`). These run the real sender signing with that seat's key,
# From that seat's own plus address, and the real verifier with `--trust`
# against the scratch registry: rc 4 is leg 2 passed (only the blessing is
# absent, as test_seat_plus.py pins); rc 1 with BAD is the crypto failing.

from test_seat_init import git_env, registry, tools  # noqa: E402,F401
from test_seat_plus import PLUS, signed_as, trust, two_seats  # noqa: E402,F401


@pytest.fixture
def plus_seat_mail(sender, env, tmp_path, fake_sendmail, two_seats):  # noqa: F811
    """A mail from the plus seat, its Session-Model read from the seat's transcript."""
    env["CLAUDE_CODE_SESSION_ID"] = SID
    transcript(Path(env["HOME"]) / ".claude", SID, PINNED)
    eml = signed_as(sender, env, tmp_path, fake_sendmail, two_seats, PLUS, f"{PLUS}@{HOST}")
    return eml, two_seats


def test_a_plus_seats_mail_carries_the_model_signed_and_passes_leg_2(plus_seat_mail, verifier):  # noqa: F811
    eml, seats = plus_seat_mail
    wire = eml.read_bytes()
    assert model_of(wire) == [MODEL]
    assert "session-model" in tags_of(wire)["h"].split(":")
    rc, said = trust(verifier, eml, seats["reg"])
    assert rc == 4, said                       # crypto good, leg 2 passed, unblessed
    assert f"sender-session-id : {seats['seats'][PLUS]['sid']}" in said
    assert "TRUST: UNBLESSED reason=bless-absent" in said and "BAD" not in said


@pytest.mark.parametrize("tamper", ["altered", "removed"])
def test_a_plus_seats_model_tampered_after_signing_fails_verify(plus_seat_mail, verifier,  # noqa: F811
                                                                tmp_path, tamper):
    eml, seats = plus_seat_mail
    wire = eml.read_bytes()
    line = f"Session-Model: {MODEL}\n".encode()
    assert line in wire
    forged = wire.replace(line, b"Session-Model: gemma-3-27b-it\n" if tamper == "altered" else b"")
    bad = tmp_path / f"plus-{tamper}.eml"
    bad.write_bytes(forged)
    rc, said = trust(verifier, bad, seats["reg"])
    assert rc == 1, said
    assert "signature-crypto  : ❌ BAD" in said
