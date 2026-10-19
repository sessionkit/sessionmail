"""The session id is read through sessiontools, not a copy of its chain.

SM-362: sessionmail kept two
copies of "first non-blank of CLAUDE_CODE_SESSION_ID, CLAUDE_SESSION_ID" -
`_seat.session_id()` and the inline chain that stamps X-Session-From in
`_sender._deliver`. Both now call `sessiontools.session_id(env)`, and
`_seat.SESSION_VARS` IS sessiontools' tuple. Behaviour is unchanged, and
the rest of the suite says so without being edited; this file proves the
one thing it cannot see - that the call really goes through sessiontools.

One edge DID move, inside the ruling, and it is pinned here rather than
left to be rediscovered: the sender's inline chain did no strip, so a
whitespace-only CLAUDE_CODE_SESSION_ID won as "   ". sessiontools strips
and skips blanks, so that case now falls through to CLAUDE_SESSION_ID -
the rule `_seat.session_id()` already kept.
"""

from __future__ import annotations

import pytest
import sessiontools
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _seat, _sender
from sessionmail.pager import cli
from test_sender import FAKE_SENDMAIL, HOST, body_file, headers_of


@pytest.fixture
def send_env(tmp_path):
    """A bare seat with a fake sendmail that keeps the wire bytes."""
    bin_dir, root = tmp_path / "bin", tmp_path / "mail"
    bin_dir.mkdir()
    root.mkdir()
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL)
    wire = tmp_path / "sendmail.wire"
    env = {
        "HOME": str(tmp_path), "PATH": str(bin_dir), "TMPDIR": str(tmp_path),
        "FAKE_SENDMAIL_ARGV": str(tmp_path / "sendmail.argv"),
        "FAKE_SENDMAIL_WIRE": str(wire),
        "SESSION_MAIL_ROOT": str(root), "SESSIONMAIL_ROOT": str(root),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail"),
    }
    return env, wire


def stamp(env: dict[str, str], wire, tmp_path) -> str | None:
    """Send one letter under ENV and return its X-Session-From, or None."""
    rc, _, err = _sender.run(["eve", "--from", "eve", "--subject", "s",
                              "--body-file", body_file(tmp_path, b"hi\n")], env)
    assert rc == 0, err
    return dict(headers_of(wire.read_bytes())).get("X-Session-From")


def test_the_session_id_is_read_through_sessiontools(send_env, tmp_path, monkeypatch):
    env, wire = send_env
    env["CLAUDE_CODE_SESSION_ID"] = "35bf6365-6686-d084-55b4-1507b25b65a2"
    seen: list[object] = []

    def fake_session_id(e=None):
        seen.append(e)
        return answer

    monkeypatch.setattr(sessiontools, "session_id", fake_session_id)

    # Whatever sessiontools answers, both readers follow - not the env.
    answer = "abcdef0123456789"
    assert _seat.session_id(env) == "abcdef0123456789"
    assert stamp(env, wire, tmp_path) == "abcdef01"
    # The SAME env each caller holds is what sessiontools is handed.
    assert seen and all(e is env for e in seen)

    # No id from sessiontools: marks keep their `-`, the stamp stays absent.
    answer = None
    assert _seat.session_id(env) == "-"
    assert stamp(env, wire, tmp_path) is None

    # One tuple, not a copy of it - and the refusal names it, in order.
    assert _seat.SESSION_VARS is sessiontools.SESSION_ID_VARS
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sessiontools, "session_id", lambda e=None: None)
    result = CliRunner().invoke(cli, ["listen", "--timeout", "1"], prog_name="pager")
    assert "set CLAUDE_CODE_SESSION_ID or CLAUDE_SESSION_ID - the session id is a box" \
        in result.stderr


def test_a_blank_session_id_falls_through_in_the_sender_too(send_env, tmp_path):
    """The named delta: `"   "` no longer wins the X-Session-From stamp."""
    env, wire = send_env
    env["CLAUDE_CODE_SESSION_ID"] = "   "
    env["CLAUDE_SESSION_ID"] = "  9f8e7d6c-5b4a-3210  "
    assert stamp(env, wire, tmp_path) == "9f8e7d6c"
    assert _seat.session_id(env) == "9f8e7d6c-5b4a-3210"
    del env["CLAUDE_SESSION_ID"]
    assert stamp(env, wire, tmp_path) is None
    assert _seat.session_id(env) == "-"
