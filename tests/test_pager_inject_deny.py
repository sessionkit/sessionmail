"""SM-518 do-005: the mail-guard's deny row, from stdlib, failing open.

openspec/changes/pager-inject-log, "The mail-guard's deny is logged from
stdlib, and fails open" (RULED: `event: inject, via: pretooluse-deny`).
hooks/mail-guard/hook.py runs on system
python3 and cannot import sessionmail, so it carries its own copy of the
`_injectlog` protocol. These tests hold the copy to the original: the same
file, the same seq, the same roll and modes, the same token estimate, and
both writers at once (design §18.3).

Hermetic: conftest's sandbox HOME; the hook runs as a subprocess on this
interpreter, as tests/test_mail_guard.py runs it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _injectlog, _tokens

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "mail-guard" / "hook.py"
SESSION = "11111111-2222-3333-4444-555555555555"
#: A subject the shell would damage: denied by rule 4 (`message send`).
MARK = "CMDTEXTMARKER"
DENIED = f'message send bob@example.invalid -s "about `uname` {MARK}" -f body.md'
ALLOWED = "message send bob@example.invalid -s 'plain' -f body.md"

#: do-002's schema: the full key set of an `inject` row (capture off), plus
#: the deny's own `blocked_tool` (a review's addition, taken).
INJECT_KEYS = {"ts", "tool", "event", "via", "seq", "sid", "seat", "pid",
               "hook_event_name", "tool_use_ids", "chars", "lines", "tokens_est", "tok",
               "items", "superseded", "held", "past_budget", "capped", "ms"}


def _load():
    spec = importlib.util.spec_from_file_location("mail_guard_deny", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()


@pytest.fixture(autouse=True)
def _no_session_env(monkeypatch):
    for var in guard.SESSION_ID_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv(_injectlog.CAPTURE_VAR, raising=False)


def payload(command: str = DENIED, **extra) -> dict:
    return {"session_id": SESSION, "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_use_id": "toolu_01DENY", "tool_input": {"command": command}, **extra}


def run(p: dict | None = None, env: dict | None = None):
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(p or payload()),
                          capture_output=True, text=True, env=env)


def reason(result) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def log_dir(session: str = SESSION) -> Path:
    d = _injectlog.log_dir(session)
    assert d is not None
    return d


def rows(session: str = SESSION) -> list[dict]:
    path = log_dir(session) / _injectlog.FILE
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


# ── the row: sized on the reason, the full key set, no command text ──────

def test_a_deny_is_one_row_sized_on_its_reason_byte_for_byte():
    r = run()
    assert r.returncode == 0
    s = reason(r)
    assert s.startswith("mail-guard: "), "the prefix is part of what the agent reads"
    [row] = rows()
    assert row["chars"] == len(s)
    assert row["lines"] == s.count("\n") + 1
    assert row["tokens_est"] == _tokens.estimate(s), "sessionmail's own estimate"
    assert row["tok"] == "chars/4"


def test_the_deny_row_carries_the_full_key_set_and_blocked_tool():
    run()
    [row] = rows()
    assert set(row) == INJECT_KEYS | {"blocked_tool"}, set(row) ^ (INJECT_KEYS | {"blocked_tool"})
    assert row["tool"] == "mail" and row["event"] == "inject"
    assert row["via"] == "pretooluse-deny"
    assert row["sid"] == SESSION and row["seat"] is None, "§18.2: null, not resolvable stdlib-only"
    assert isinstance(row["pid"], int) and row["pid"] > 0
    assert row["seq"] == 1
    assert row["hook_event_name"] == "PreToolUse"
    assert row["tool_use_ids"] == ["toolu_01DENY"]
    assert row["blocked_tool"] == "Bash"
    assert (row["items"], row["superseded"], row["held"], row["past_budget"], row["capped"]) \
        == ([], 0, 0, 0, None)
    assert isinstance(row["ms"], int) and row["ms"] >= 0


def test_the_row_holds_no_command_text_no_subject_no_sender():
    run()
    raw = (log_dir() / _injectlog.FILE).read_text()
    for text in (MARK, "uname", "bob@example.invalid", "body.md", "about"):
        assert text not in raw, text


def test_the_deny_writes_where_sessionmail_writes():
    run()
    assert (log_dir() / _injectlog.FILE).exists()
    assert guard.log_dir(SESSION) == str(log_dir())
    assert guard.log_dir(SESSION).endswith(f"/.session/sessions/{SESSION}/log/mail")


def test_the_modes_are_sessionmails():
    run()
    d = log_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for name in (_injectlog.FILE, _injectlog.SEQ_FILE):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o600, name


def test_an_allowed_command_writes_nothing():
    r = run(payload(ALLOWED))
    assert r.returncode == 0 and r.stdout == ""
    assert not log_dir().exists()


def test_no_tool_use_id_is_an_empty_list():
    p = payload()
    del p["tool_use_id"]
    run(p)
    assert rows()[0]["tool_use_ids"] == []


@pytest.mark.parametrize("text", ["", "a", "abc", "abcd", "x" * 401, "mail-guard: é ✓"])
def test_the_token_estimate_is_sessionmails(text):
    assert guard._tokens_estimate(text) == _tokens.estimate(text)


# ── the session: the payload's, else the env's; none means nothing ───────

def test_no_session_id_writes_nothing():
    p = payload()
    del p["session_id"]
    r = run(p)
    assert reason(r), "the deny still prints"
    home = Path(os.environ["HOME"])
    assert not list(home.rglob("pager.jsonl"))


@pytest.mark.parametrize("sid", ["-", "", "   "])
def test_a_blank_or_dash_session_writes_nothing(sid):
    run(payload(session_id=sid))
    assert not list(Path(os.environ["HOME"]).rglob("pager.jsonl"))


def test_the_env_session_when_the_payload_has_none():
    p = payload()
    del p["session_id"]
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SESSION)
    run(p, env=env)
    assert len(rows()) == 1


# ── fail OPEN ────────────────────────────────────────────────────────────

def test_a_log_that_cannot_be_written_never_costs_the_deny():
    d = log_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("a FILE where the log dir goes")
    r = run()
    assert r.returncode == 0 and r.stderr == ""
    assert reason(r).startswith("mail-guard: ")
    assert d.read_text() == "a FILE where the log dir goes"


@pytest.mark.parametrize("where", ["log_append", "deny_row", "log_dir", "_session_of"])
def test_any_writer_exception_leaves_no_row_and_the_deny_prints(monkeypatch, where):
    """In-process, the module's own `main`: a writer that raises ANYWHERE
    leaves no row; the decision JSON is already on stdout, and main exits 0."""
    mod = _load()

    def boom(*a, **k):
        raise RuntimeError("writer bug")
    monkeypatch.setattr(mod, where, boom)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload())))
    monkeypatch.setattr(sys, "stdout", out)
    assert mod.main([]) == 0
    decision = json.loads(out.getvalue())
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not list(Path(os.environ["HOME"]).rglob("pager.jsonl"))


def test_the_deny_is_on_stdout_before_the_row_is_written(monkeypatch):
    mod = _load()
    seen = []
    out = io.StringIO()

    def spy(p, text, started, env=None):
        seen.append(out.getvalue())
        return None
    monkeypatch.setattr(mod, "log_deny", spy)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload())))
    monkeypatch.setattr(sys, "stdout", out)
    mod.main([])
    assert seen and '"permissionDecision": "deny"' in seen[0]


# ── size-only: the capture switch is not readable stdlib-only ────────────

def test_the_deny_row_is_size_only_even_with_capture_on():
    env = dict(os.environ, SESSIONMAIL_CAPTURE="on")
    run(env=env)
    [row] = rows()
    assert "capture" not in row
    assert not (log_dir() / _injectlog.CAPTURE_DIR).exists()


# ── one file, one seq, one roll: both writers ────────────────────────────

def test_the_deny_takes_the_next_seq_in_sessionmails_sequence():
    assert _injectlog.write(SESSION, via="batch", s="first") == 1
    run()
    assert _injectlog.write(SESSION, via="batch", s="third") == 3
    assert [(r["seq"], r["via"]) for r in rows()] == [
        (1, "batch"), (2, "pretooluse-deny"), (3, "batch")]


def test_the_stdlib_roll_is_sessionmails_roll(tmp_path):
    _injectlog.write(SESSION, via="batch", s="first")
    d = log_dir()
    (d / _injectlog.FILE).write_text((d / _injectlog.FILE).read_text() + "z" * 300 + "\n")
    row = guard.deny_row(payload(), "mail-guard: x", 0, SESSION)
    assert guard.log_append(str(d), row, roll_at=200) == 2
    assert '"seq":1' in (d / _injectlog.ROLLED).read_text()
    assert [r["seq"] for r in rows()] == [2]
    assert stat.S_IMODE((d / _injectlog.FILE).stat().st_mode) == 0o600


def test_the_stdlib_roll_happens_inside_the_seq_lock(monkeypatch):
    import fcntl
    _injectlog.write(SESSION, via="batch", s="first")
    d = log_dir()
    mod = _load()
    seen = []
    real = os.replace

    def replace(src, dst):
        fd = os.open(d / _injectlog.SEQ_FILE, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(True)
        finally:
            os.close(fd)
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    row = mod.deny_row(payload(), "mail-guard: x", 0, SESSION)
    assert mod.log_append(str(d), row, roll_at=1) == 2
    assert seen == [True]


def test_the_stdlib_writer_reads_sessionmails_seq_file():
    for _ in range(3):
        _injectlog.write(SESSION, via="batch", s="x")
    raw = (log_dir() / _injectlog.SEQ_FILE).read_bytes()
    assert raw == f"{3:020d}\n".encode()
    run()
    assert (log_dir() / _injectlog.SEQ_FILE).read_bytes() == f"{4:020d}\n".encode()
    assert rows()[-1]["seq"] == 4


SYSTEM_PYTHON = Path("/usr/bin/python3")


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no /usr/bin/python3 on this host")
def test_the_deny_row_is_written_by_the_system_python3_too():
    """do-005 review N1: `mail-guard-hook` runs whichever `python3` is on
    PATH, often the system's (3.9 on macOS), not the venv's. A newer-syntax
    edit to hook.py would fail open there with no row and no error; this
    runs it there, isolated (-I), so that cannot pass silently."""
    r = subprocess.run([str(SYSTEM_PYTHON), "-I", str(HOOK)], input=json.dumps(payload()),
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert reason(r).startswith("mail-guard: ")
    [row] = rows()
    assert row["via"] == "pretooluse-deny" and row["chars"] == len(reason(r))
