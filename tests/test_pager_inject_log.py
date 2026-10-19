"""SM-518 do-002: the pager inject log's writer, and the hooks' rows.

openspec/changes/pager-inject-log (PROPOSED, most of it RULED): every string
the hooks put into an agent's context gets ONE row in
`<session-dir>/log/mail/pager.jsonl`, sized on the exact string
emitted. Here: the writer (`_injectlog`: seq under flock, the 16 MB roll
inside that lock, 0700/0600, never raises, no sid -> nothing) and the rows
of `pager hook batch` (prompt and batch, every path design §16.1 lists),
`pager hook start` and `pager hook stop`.

Hermetic: conftest's sandbox HOME, so the session dir (`$HOME/.session/
sessions/<sid>/`) is under tmp; no live file is read or written.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _injectlog, _seat, _settings, _tokens, pager
from sessionmail.pager import cli

SESSION = "11111111-2222-3333-4444-555555555555"
HOOKS_ON = '{"version": 1, "hooks": true}\n'
HOOKS_OFF = '{"version": 1, "hooks": false}\n'

#: README Requirement 2 / spec "The row names its source": the full key set
#: of an `inject` row, capture off (capture is do-004's).
INJECT_KEYS = {"ts", "tool", "event", "via", "seq", "sid", "seat", "pid",
               "hook_event_name", "tool_use_ids", "chars", "lines", "tokens_est", "tok",
               "items", "superseded", "held", "past_budget", "capped", "ms"}
ITEM_KEYS = {"id", "kind", "view", "given", "in_mail", "cut"}


# ── the rig ──────────────────────────────────────────────────────────────

def write_settings(text: str) -> Path:
    p = _settings.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def seat(tmp_path, monkeypatch):
    """test_pager_hook's seat: alpha, one session, hooks ON, not listening."""
    home = tmp_path / "sessionmail"
    (home / "seats" / "alpha").mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    write_settings(HOOKS_ON)
    return _seat.resolve()


def log_dir(session: str = SESSION) -> Path:
    d = _injectlog.log_dir(session)
    assert d is not None
    return d


def rows(session: str = SESSION) -> list[dict]:
    path = log_dir(session) / _injectlog.FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def ptb(**extra) -> str:
    return json.dumps({"session_id": SESSION, "hook_event_name": "PostToolBatch",
                       "tool_calls": [], **extra})


def batch(payload: str | None = None):
    return CliRunner().invoke(cli, ["hook", "batch"], input=payload if payload is not None else ptb())


def context(result) -> str:
    """The exact additionalContext string the hook printed ('' for none)."""
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


_QUEUED = [0]


def queue(tmp_path: Path, n: int, *, subject: str, sender: str = "beta@example.invalid",
          body: str = "body\n", to: str = "") -> list[str]:
    """Queue N mails for SESSION; return their Message-IDs in queue order."""
    s = _seat.resolve()
    q = _seat.queue_new(s, SESSION)
    mids = []
    for i in range(n):
        _QUEUED[0] += 1
        tag = f"q{_QUEUED[0]}"
        mid = f"1790000{i:03d}.{i}.{tag}@example.invalid"
        mail = tmp_path / f"17900000{i:02d}.M{i}P1.{tag}:2,"
        head = f"From: {sender}\nSubject: {subject} {i}\nMessage-ID: <{mid}>\n"
        if to:
            head += f"X-Original-To: {to}\n"
        mail.write_text(head + "\n" + body)
        os.link(mail, q / mail.name)
        # Distinct mtimes keep queue order = i order.
        os.utime(q / mail.name, (1908540800 + i, 1908540800 + i))
        mids.append(mid)
    return mids


def assert_sized(row: dict, s: str) -> None:
    """Byte for byte: the row's sizes are of S, the string emitted."""
    assert s, "a row for an empty string"
    assert row["chars"] == len(s)
    assert row["lines"] == s.count("\n") + 1
    assert row["tokens_est"] == _tokens.estimate(s)
    assert row["tok"] == "chars/4"


def assert_schema(row: dict) -> None:
    assert set(row) == INJECT_KEYS, set(row) ^ INJECT_KEYS
    assert row["tool"] == "mail" and row["event"] == "inject"
    assert row["sid"] == SESSION and row["pid"] > 0
    assert isinstance(row["seq"], int) and row["seq"] >= 1
    assert isinstance(row["ms"], int) and row["ms"] >= 0
    assert isinstance(row["tool_use_ids"], list)
    for k in ("superseded", "held", "past_budget"):
        assert isinstance(row[k], int)
    assert row["capped"] in (None, "text", "batch", "room", "give_by")
    for it in row["items"]:
        assert set(it) == ITEM_KEYS


# ── the writer ───────────────────────────────────────────────────────────

def test_no_session_writes_nothing_and_makes_nothing(seat):
    home = Path(os.environ["HOME"])
    before = sorted(home.rglob("*"))
    for sid in ("-", ""):
        assert _injectlog.write(sid, via="batch", s="hello") is None
    assert _injectlog.log_dir("-") is None
    assert sorted(home.rglob("*")) == before


def test_an_empty_string_writes_no_row(seat):
    assert _injectlog.write(SESSION, via="batch", s="") is None
    assert not log_dir().exists()


def test_the_dir_is_0700_and_its_files_0600(seat):
    assert _injectlog.write(SESSION, via="batch", s="x", seat=seat) == 1
    d = log_dir()
    assert d == _seat.session_root(SESSION).parent / "log" / "mail"
    # the rework ruling: the rows live at
    # <session-dir>/log/mail/pager.jsonl, spelled as the docs spell it
    assert (d / _injectlog.FILE).as_posix().endswith(f"/{SESSION}/log/mail/pager.jsonl")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for name in (_injectlog.FILE, _injectlog.SEQ_FILE):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o600, name


def test_a_row_is_one_compact_json_line_with_the_envelope(seat):
    _injectlog.write(SESSION, via="batch", s="héllo\nwörld", seat=seat, ms=3)
    raw = (log_dir() / _injectlog.FILE).read_text(encoding="utf-8")
    assert raw.count("\n") == 1 and ", " not in raw, "one compact line"
    assert "héllo" not in raw, "the string itself is never in the row"
    [row] = rows()
    assert_schema(row)
    assert row["seat"] == seat.address and row["via"] == "batch"
    assert row["chars"] == len("héllo\nwörld") and row["lines"] == 2


def test_seq_counts_up_and_is_not_the_files_length(seat):
    for _ in range(3):
        _injectlog.write(SESSION, via="batch", s="x")
    (log_dir() / _injectlog.FILE).write_text("")            # a reader truncated it
    _injectlog.write(SESSION, via="batch", s="x")
    assert [r["seq"] for r in rows()] == [4]


def test_tokens_est_is_the_estimate_not_chars_over_four(seat):
    for s in ("a", "abc", "abcd", "x" * 401):
        _injectlog.write(SESSION, via="batch", s=s)
    got = [r["tokens_est"] for r in rows()]
    assert got == [_tokens.estimate(s) for s in ("a", "abc", "abcd", "x" * 401)] == [1, 1, 1, 100]


def test_the_roll_renames_to_one_generation(seat, monkeypatch):
    monkeypatch.setattr(_injectlog, "ROLL_AT", 200)
    _injectlog.write(SESSION, via="batch", s="first")
    d = log_dir()
    (d / _injectlog.FILE).write_text((d / _injectlog.FILE).read_text() + "z" * 300 + "\n")
    _injectlog.write(SESSION, via="batch", s="second")
    assert (d / _injectlog.ROLLED).exists()
    assert (d / _injectlog.ROLLED).as_posix().endswith(f"/{SESSION}/log/mail/pager.1.jsonl")
    assert [r["seq"] for r in rows()] == [2], "the new file holds only the row after the roll"
    old = (d / _injectlog.ROLLED).read_text()
    assert '"seq":1' in old
    # a second roll replaces the one generation
    (d / _injectlog.FILE).write_text((d / _injectlog.FILE).read_text() + "y" * 300 + "\n")
    _injectlog.write(SESSION, via="batch", s="third")
    assert '"seq":2' in (d / _injectlog.ROLLED).read_text()
    assert '"seq":1' not in (d / _injectlog.ROLLED).read_text()
    assert stat.S_IMODE((d / _injectlog.FILE).stat().st_mode) == 0o600


def test_the_roll_happens_inside_the_seq_lock(seat, monkeypatch):
    """§18.1: two unlocked rollers could move a fresh file onto pager.1.jsonl.
    While the rename runs, a second open of `seq` cannot take the lock."""
    import fcntl
    monkeypatch.setattr(_injectlog, "ROLL_AT", 1)
    _injectlog.write(SESSION, via="batch", s="first")
    seen = []
    real = os.replace

    def replace(src, dst):
        fd = os.open(log_dir() / _injectlog.SEQ_FILE, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(True)
        finally:
            os.close(fd)
        return real(src, dst)

    monkeypatch.setattr(_injectlog.os, "replace", replace)
    assert _injectlog.write(SESSION, via="batch", s="second") == 2
    assert seen == [True]


def test_a_write_that_fails_costs_the_row_and_never_raises(seat):
    d = log_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("a FILE where the log dir goes")
    assert _injectlog.write(SESSION, via="batch", s="hello") is None


def test_a_torn_seq_file_restarts_rather_than_raises(seat):
    _injectlog.write(SESSION, via="batch", s="x")
    (log_dir() / _injectlog.SEQ_FILE).write_text("garbage")
    assert _injectlog.write(SESSION, via="batch", s="x") == 1


WRITER = """
import sys
from sessionmail import _injectlog
for i in range(int(sys.argv[2])):
    assert _injectlog.write(sys.argv[1], via=sys.argv[3], s="%s row %d" % (sys.argv[3], i)) is not None
"""


#: SM-518 do-005: the mail-guard's stdlib copy of the protocol, as a fourth
#: writer (design §18.3). hook.py is loaded by path, never imported from
#: sessionmail: it runs on system python3 with the stdlib only.
DENY_WRITER = """
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("mail_guard", sys.argv[3])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
p = {"session_id": sys.argv[1], "hook_event_name": "PreToolUse", "tool_name": "Bash",
     "tool_use_id": "toolu_x"}
for i in range(int(sys.argv[2])):
    assert mod.log_deny(p, "mail-guard: deny %d" % i, time.monotonic()) is not None
"""


@pytest.mark.parametrize("capture", ["off", "on"])
def test_three_writers_get_distinct_seq(seat, tmp_path, monkeypatch, capture):
    """SC3 (design §18.3): a monitor-like writer, two hook writers and the
    mail-guard's stdlib writer, four processes appending to one session's
    log at once. The seq half (do-002; do-005's stdlib writer among them),
    and with capture on the capture half (do-004): each capture file is
    named by exactly one row's seq, and holds that row's string. The deny
    rows are size-only (do-005), so they name none."""
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, capture)
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    deny_script = tmp_path / "deny_writer.py"
    deny_script.write_text(DENY_WRITER)
    hook = Path(__file__).resolve().parents[1] / "hooks" / "mail-guard" / "hook.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(_injectlog.__file__).resolve().parents[1])
    procs = [subprocess.Popen([sys.executable, str(script), SESSION, str(n), via], env=env)
             for n, via in ((120, "monitor"), (60, "batch"), (60, "prompt"))]
    procs.append(subprocess.Popen([sys.executable, "-I", str(deny_script), SESSION, "60",
                                   str(hook)], env=env))
    for p in procs:
        assert p.wait(timeout=60) == 0
    got = rows()
    seqs = [r["seq"] for r in got]
    assert len(got) == 300
    assert sorted(seqs) == list(range(1, 301)), "every seq distinct, none skipped"
    assert seqs == sorted(seqs), "appended under the lock: file order is seq order"
    assert {r["via"] for r in got} == {"monitor", "batch", "prompt", "pretooluse-deny"}
    assert sum(1 for r in got if r["via"] == "pretooluse-deny") == 60
    cd = log_dir() / _injectlog.CAPTURE_DIR
    if capture == "off":
        assert all("capture" not in r for r in got) and not cd.exists()
        return
    assert all("capture" not in r for r in got if r["via"] == "pretooluse-deny")
    got = [r for r in got if r["via"] != "pretooluse-deny"]
    named = [r["capture"] for r in got]
    assert len(set(named)) == 240, "no two rows name one file"
    assert sorted(p.name for p in cd.glob("*.txt")) == sorted(n.split("/")[1] for n in named), \
        "every file is named by a row, and every row's file exists"
    for r in got:
        assert r["capture"] == f"capture/{r['seq']:06d}.txt"
        text = (log_dir() / r["capture"]).read_text(encoding="utf-8")
        assert text.startswith(r["via"] + " row ") and len(text) == r["chars"]


@pytest.mark.parametrize("payload,want", [
    ({"tool_calls": [{"tool_name": "Read", "tool_input": {}, "tool_use_id": "toolu_01",
                      "tool_response": "secret"},
                     {"tool_name": "Bash", "tool_use_id": "toolu_02"}]}, ["toolu_01", "toolu_02"]),
    ({"tool_calls": []}, []),
    ({}, []),
    ({"tool_calls": None}, []),
    ({"tool_calls": ["junk", {"tool_use_id": None}, {"no": "id"}]}, []),
    ({"tool_use_id": "toolu_09"}, ["toolu_09"]),
    ({"tool_calls": [{"tool_use_id": "toolu_01"}], "tool_use_id": "toolu_01"}, ["toolu_01"]),
])
def test_tool_use_ids_populated_empty_missing(payload, want):
    """§16.4. The inner key is Claude Code's hooks reference, "PostToolBatch
    input": each `tool_calls` element carries `tool_use_id`."""
    assert _injectlog.tool_use_ids(payload) == want


# ── the batch hook: the ride ─────────────────────────────────────────────

def test_a_carrying_batch_writes_one_row_sized_on_the_string_it_printed(seat, tmp_path):
    mids = queue(tmp_path, 2, subject="UNIQUESUBJ", sender="UNIQUESENDER@example.invalid",
                 body="hello there\nsecond line\n")
    r = batch(ptb(tool_calls=[{"tool_name": "Bash", "tool_use_id": "toolu_A"}]))
    assert r.exit_code == 0
    s = context(r)
    assert "UNIQUESUBJ" in s
    [row] = rows()
    assert_schema(row)
    assert_sized(row, s)
    assert row["via"] == "batch" and row["hook_event_name"] == "PostToolBatch"
    assert row["tool_use_ids"] == ["toolu_A"]
    assert [it["id"] for it in row["items"]] == mids, "emit order, no angle brackets"
    for it in row["items"]:
        assert it["kind"] == "mail" and it["view"] == "text" and it["cut"] is False
        assert it["in_mail"] == len("hello there\nsecond line")
        assert 0 < it["given"] < len(s)
    assert (row["superseded"], row["held"], row["past_budget"], row["capped"]) == (0, 0, 0, None)
    raw = (log_dir() / _injectlog.FILE).read_text()
    assert "UNIQUESUBJ" not in raw and "UNIQUESENDER" not in raw, "no subject, no sender"
    assert "hello there" not in raw, "no body text"


def test_given_adds_up_to_the_items_share_of_the_string(seat, tmp_path):
    queue(tmp_path, 3, subject="parts", body="abc\n")
    r = batch()
    s = context(r)
    [row] = rows()
    # Each item's lines are a contiguous part of S, in order: their given
    # plus one newline each fit inside S, with the state line after them.
    total = sum(it["given"] + 1 for it in row["items"])
    assert total <= len(s) + 1
    first = s.splitlines()[0]
    assert row["items"][0]["given"] >= len(first)


def test_an_empty_batch_writes_no_row(seat):
    assert batch().exit_code == 0          # says the state line once
    n = len(rows())
    r = batch()
    assert r.stdout == ""
    assert len(rows()) == n, "printed nothing, so no row"


def test_the_state_line_alone_is_a_row_with_no_items(seat):
    r = batch()
    s = context(r)
    assert s
    [row] = rows()
    assert_sized(row, s)
    assert row["items"] == [] and row["capped"] is None


def test_a_text_view_cut_at_the_cap_says_text_and_cut(seat, tmp_path):
    from sessionmail import _views
    body = "w" * (_views.TEXT_CAP + 500) + "\n"
    [mid] = queue(tmp_path, 1, subject="long", body=body)
    r = batch()
    [row] = rows()
    assert_sized(row, context(r))
    [it] = row["items"]
    assert it["id"] == mid and it["view"] == "text" and it["cut"] is True
    assert it["in_mail"] == _views.TEXT_CAP + 500
    assert row["capped"] == "text"


def test_a_mail_with_no_text_part_is_cut_but_not_capped(seat, tmp_path):
    q = _seat.queue_new(seat, SESSION)
    mail = tmp_path / "1908540801.M1P1.html:2,"
    mail.write_text("From: b@example.invalid\nSubject: html only\nMessage-ID: <html@example.invalid>\n"
                    "Content-Type: text/html\n\n<p>hi</p>\n")
    os.link(mail, q / mail.name)
    r = batch()
    [row] = rows()
    assert_sized(row, context(r))
    [it] = row["items"]
    assert it["cut"] is True and it["in_mail"] == 0
    assert row["capped"] is None, "no cap applied: the mail has no words to give"


def test_past_the_batch_budget_is_counted_and_header_only(seat, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_GROUP_AFTER", "100")    # no burst: one line each
    body = "b" * 1900 + "\n"
    mids = queue(tmp_path, 5, subject="budget", body=body)
    r = batch()
    [row] = rows()
    assert_sized(row, context(r))
    assert row["past_budget"] == 1 and row["capped"] == "batch"
    views = [it["view"] for it in row["items"]]
    assert views == ["text"] * 4 + ["header"]
    assert row["items"][-1]["id"] == mids[-1] and row["items"][-1]["cut"] is True


def test_held_for_room_is_counted_not_listed(seat, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_GROUP_AFTER", "100")    # no burst: one line each
    monkeypatch.setattr(pager, "HOOK_CONTEXT_MAX", 900)
    mids = queue(tmp_path, 4, subject="room", body="r" * 300 + "\n")
    r = batch()
    [row] = rows()
    assert_sized(row, context(r))
    assert row["held"] >= 1 and row["capped"] == "room"
    ids = [it["id"] for it in row["items"]]
    assert ids == mids[:len(ids)] and len(ids) + row["held"] == 4, "held are counted, never listed"


def test_a_burst_is_one_count_line_and_each_mail_an_item(seat, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_GROUP_AFTER", "2")
    mids = queue(tmp_path, 3, subject="burst", to="alpha+burst@example.invalid")
    r = batch()
    s = context(r)
    [row] = rows()
    assert_sized(row, s)
    assert [it["id"] for it in row["items"]] == mids
    assert all(it["view"] == "count" for it in row["items"])
    line = s.splitlines()[0]
    assert "3 for" in line
    assert [it["given"] for it in row["items"]] == [len(line), 0, 0]


def test_a_superseded_mail_is_counted(seat, tmp_path):
    q = _seat.queue_new(seat, SESSION)
    old = tmp_path / "1908540801.M1P1.a:2,"
    old.write_text("From: b@example.invalid\nSubject: v1\nMessage-ID: <old@example.invalid>\n\nx\n")
    new = tmp_path / "1908540802.M2P1.a:2,"
    new.write_text("From: b@example.invalid\nSubject: v2\nMessage-ID: <new@example.invalid>\n"
                   "Supersedes: <old@example.invalid>\n\ny\n")
    os.link(old, q / old.name)
    os.link(new, q / new.name)
    r = batch()
    [row] = rows()
    assert_sized(row, context(r))
    assert row["superseded"] == 1 and [it["id"] for it in row["items"]] == ["new@example.invalid"]


# ── the batch hook: every other path that prints (§16.1) ─────────────────

def test_settings_created_is_a_row(seat):
    _settings.path().unlink()
    r = batch()
    s = context(r)
    assert "settings" in s.lower()
    [row] = rows()
    assert_schema(row)
    assert_sized(row, s)
    assert row["items"] == []


def test_a_settings_fault_is_a_row(seat):
    write_settings("not json")
    r = batch()
    s = context(r)
    assert "treated as off" in s
    [row] = rows()
    assert_sized(row, s)


def test_hooks_off_silent_writes_nothing(seat):
    write_settings(HOOKS_OFF)
    r = batch()
    assert r.stdout == ""
    assert not log_dir().exists(), "off means off"


def test_hooks_off_the_deaf_line_is_the_one_row(seat, monkeypatch):
    calls = []

    def deaf(s, session):
        calls.append(session)
        return ["pager: DEAF - a test's deaf line"], "deaf-record"
    monkeypatch.setattr(pager, "_deaf_unsaid", deaf)
    write_settings(HOOKS_OFF)
    r = batch()
    s = context(r)
    assert s == "pager: DEAF - a test's deaf line" and calls
    [row] = rows()
    assert_sized(row, s)


def test_could_not_look_is_a_row(seat, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk on fire")
    monkeypatch.setattr(_seat, "read_marks", boom)
    r = batch()
    s = context(r)
    assert s and "could not look" in r.stderr
    [row] = rows()
    assert_sized(row, s)
    assert "disk on fire" not in json.dumps(row), "the stderr line is not the string injected"


def test_a_drift_line_alone_past_give_by_is_a_row_that_says_give_by(seat, monkeypatch, tmp_path):
    old = _settings.old_path()
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text('{"version": 1, "hooks": false}\n')            # a real file, differing
    monkeypatch.setattr(pager, "HOOK_GIVE_BY", -1.0)
    queue(tmp_path, 1, subject="late")
    r = batch()
    s = context(r)
    assert s and "late" not in s
    [row] = rows()
    assert_sized(row, s)
    assert row["capped"] == "give_by" and row["items"] == []


def test_a_subagents_batch_writes_nothing(seat, tmp_path):
    queue(tmp_path, 1, subject="sub")
    r = batch(ptb(agent_id="a81b552b29e268608"))
    assert r.stdout == "" and not log_dir().exists()


def test_no_session_id_writes_nothing(seat, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    home = Path(os.environ["HOME"])
    r = batch()
    assert context(r), "the state line is still said"
    assert not list(home.rglob("pager.jsonl")) and not list(home.rglob("log/mail/seq"))


def test_a_failed_write_never_touches_the_injection(seat, tmp_path):
    """Requirement 4: a writer OSError costs the row, never the injection.
    The same mail, the same hook, a log dir that cannot be made: the output
    and the exit code are what they would have been."""
    d = log_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("a FILE where the log dir goes")
    queue(tmp_path, 1, subject="safe")
    r = batch()
    assert r.exit_code == 0 and r.exception is None
    lines = context(r).splitlines()
    assert any("safe 0" in ln for ln in lines) and lines[-1] == pager.LINE_NOT_LISTENING
    assert d.read_text() == "a FILE where the log dir goes"


# ── UserPromptSubmit: `prompt` ───────────────────────────────────────────

def ups(prompt: str) -> str:
    return json.dumps({"session_id": SESSION, "hook_event_name": "UserPromptSubmit",
                       "prompt": prompt})


def test_a_carrying_turn_start_is_a_prompt_row(seat, tmp_path):
    _seat.write_carry(seat, SESSION)
    queue(tmp_path, 1, subject="carried")
    r = batch(ups("<task-notification>\n<status>completed</status>\n</task-notification>"))
    s = context(r)
    assert "carried" in s
    [row] = rows()
    assert_sized(row, s)
    assert row["via"] == "prompt" and row["hook_event_name"] == "UserPromptSubmit"
    assert row["tool_use_ids"] == []


def test_a_turn_start_that_does_not_carry_writes_nothing(seat, tmp_path):
    queue(tmp_path, 1, subject="stays")
    r = batch(ups("a typed prompt"))
    assert r.stdout == ""
    assert not log_dir().exists(), "spec.md:763: gives nothing, writes nothing, says nothing"


# ── Stop: `stop` ─────────────────────────────────────────────────────────

def stop(payload: dict | None = None):
    return CliRunner().invoke(cli, ["hook", "stop"], input=json.dumps(payload or {}))


def test_stop_not_listening_is_a_stop_row_on_its_stderr_line(seat):
    r = stop({"hook_event_name": "Stop", "session_id": SESSION})
    assert r.exit_code == 2
    line = r.stderr.rstrip("\n")
    assert line == pager.LINE_NOT_LISTENING
    [row] = rows()
    assert_schema(row)
    assert_sized(row, line)
    assert row["via"] == "stop" and row["hook_event_name"] == "Stop" and row["items"] == []


def test_stop_deaf_on_a_required_seat_is_a_stop_row(seat, monkeypatch):
    monkeypatch.setattr(_seat, "pager_required",
                        lambda s, session, env=None: type("R", (), {"on": True})())
    monkeypatch.setattr(pager, "deaf_check", lambda s, session, required: "pager: DEAF - test")
    write_settings(HOOKS_OFF)
    r = stop()
    assert r.exit_code == 2
    [row] = rows()
    assert_sized(row, "pager: DEAF - test")


def test_a_quiet_stop_writes_nothing(seat):
    d = _seat.stamp_dir(None, SESSION)
    d.mkdir(parents=True, exist_ok=True)
    (d / _seat.LISTENING_STAMP).write_text(str(os.getpid()))
    r = stop()
    assert r.exit_code == 0
    assert not log_dir().exists()


def test_stop_without_a_session_writes_nothing(seat, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    r = stop()
    assert r.exit_code == 0
    assert not list(Path(os.environ["HOME"]).rglob("pager.jsonl"))


# ── SessionStart: `start` ────────────────────────────────────────────────

def start(payload: dict | None = None):
    return CliRunner().invoke(cli, ["hook", "start"], input=json.dumps(
        payload or {"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"}))


def _fake_birth(lines):
    return lambda payload, env: (list(lines), False, _seat.resolve(), SESSION)


def test_start_is_a_start_row_on_its_additional_context(seat, monkeypatch):
    monkeypatch.setattr(pager, "_start_birth", _fake_birth(["sessionmail: box born (test)"]))
    r = start()
    assert r.exit_code == 0
    s = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert s.startswith("sessionmail: box born (test)")
    [row] = rows()
    assert_schema(row)
    assert_sized(row, s)
    assert row["via"] == "start" and row["hook_event_name"] == "SessionStart"


def test_start_with_nothing_to_say_echoes_json_but_writes_no_row(seat, monkeypatch):
    """§18.5: hook_start echoes JSON even when lines and state are empty."""
    monkeypatch.setattr(pager, "_start_birth", _fake_birth([]))
    monkeypatch.setattr(pager, "_state_lines", lambda *a, **k: [])
    r = start()
    assert r.exit_code == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] == ""
    assert not log_dir().exists()


def test_start_with_the_hooks_off_writes_nothing(seat, monkeypatch):
    monkeypatch.setattr(pager, "_start_birth", _fake_birth(["sessionmail: box born (test)"]))
    write_settings(HOOKS_OFF)
    r = start()
    assert r.stdout == ""
    assert not log_dir().exists()


def test_the_real_start_birth_is_logged_on_its_string(seat, monkeypatch, tmp_path):
    """The real `_start_birth`, not a stub: whatever it says, the row is
    sized on it. Its doorbell and generator are faked (never the live map)."""
    gen = tmp_path / "fake-gen"
    gen.write_text("#!/bin/sh\nexit 0\n")
    gen.chmod(0o755)
    monkeypatch.setenv(_seat.GEN_VAR, str(gen))
    monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: (False, "NOT COPIED: test"))
    Path(os.environ["SESSION_MAIL_ROOT"]).mkdir(parents=True, exist_ok=True)
    r = start()
    assert r.exit_code == 0, r.output
    s = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    got = rows()
    if s:
        [row] = got
        assert_sized(row, s)
    else:
        assert got == []
