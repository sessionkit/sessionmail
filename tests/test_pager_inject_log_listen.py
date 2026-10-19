"""SM-518 do-003: the listener's rows, and the empty-run counter.

openspec/changes/pager-inject-log (PROPOSED; these requirements RULED, each
says by whom). Here:

- `via: monitor`, one `inject` row per line `MonitorOut.say` prints - the
  page, the deaf line, the off line - sized on the NORMALISED line (§16.2);
- `via: listen`, the hand page's row: its stdout lines joined with `\\n`,
  and the stderr "paged by" line in `stderr_chars` (§16.3, §17);
- the listener's own life: `start`, `exit` (with the cumulative empty-run
  counter), `error` - in `pager.jsonl`, never in `marks` (RULED Q6);
- the counter: bumped ONLY by a `pager hook batch` run past every gate that
  ran the scoop and printed nothing (§16.5, spec.md:763).

Hermetic: conftest's sandbox HOME, a maildir root and SESSIONMAIL_HOME under
tmp_path, as tests/test_pager_monitor.py rigs them. No live file is touched.
"""

from __future__ import annotations

import json
import os
import re
import signal
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _injectlog, _seat, _tokens, pager, rules
from sessionmail import _settings
from sessionmail.pager import cli

SID = "sess-a"
DEADLINE = 20.0
EX_PAGE, EX_TIMEOUT = pager.EX_PAGE, pager.EX_TIMEOUT

#: do-002's schema: the full key set of an `inject` row, capture off.
INJECT_KEYS = {"ts", "tool", "event", "via", "seq", "sid", "seat", "pid",
               "hook_event_name", "tool_use_ids", "chars", "lines", "tokens_est", "tok",
               "items", "superseded", "held", "past_budget", "capped", "ms"}
#: The envelope every row carries (spec "The row names its source").
ENVELOPE = {"ts", "tool", "event", "via", "seq", "sid", "seat", "pid"}
#: spec "The listener logs its own life": each lifecycle row is the envelope
#: plus exactly these (and none of the inject row's sizing/item/hook keys).
LIFE_KEYS = {
    "start": ENVELOPE | {"box", "timeout"},
    "exit": ENVELOPE | {"code", "pages", "quiet", "quiet_ms_total", "quiet_ms_max"},
    "error": ENVELOPE | {"what"},
}
ITEM_KEYS = {"id", "kind", "view", "given", "in_mail", "cut"}
_n = 0


# ── the rig ──────────────────────────────────────────────────────────────

@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "0.4")
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, "60")
    monkeypatch.setenv(pager.MONITOR_SPACING_VAR, "0.05")
    monkeypatch.delenv(pager.PAGE_LIST_VAR, raising=False)
    monkeypatch.setenv(rules.PAGE_RULES_VAR, "wake *")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    # Stopped (idle past the threshold): T = 0, a page is immediate.
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 75.0)
    hooks = Path(os.environ["HOME"]) / _settings.RELATIVE
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text('{"version": 1, "hooks": true}\n')

    class Rig:
        def __init__(self) -> None:
            self.root = root
            self.seat = _seat.resolve()

        def box(self, name: str = SID) -> Path:
            b = root / name
            for d in ("tmp", "new", "cur"):
                (b / d).mkdir(parents=True, exist_ok=True)
            return b

        def deliver(self, mid: str, name: str = SID, original_to: str = "") -> str:
            global _n
            _n += 1
            b = self.box(name)
            fname = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.inject"
            head = (f"From: SENDER-NAME <beta@example.invalid>\r\nTo: {name}@example.invalid\r\n"
                    + (f"X-Original-To: {original_to}\r\n" if original_to else "")
                    + f"Subject: SECRET-SUBJECT\r\nMessage-ID: <{mid}>\r\n")
            (b / "tmp" / fname).write_bytes((head + "\r\nTHE BODY\r\n").encode())
            os.rename(b / "tmp" / fname, b / "new" / fname)
            return fname

        def marks(self) -> bytes:
            m = _seat.marks_path(self.seat, SID)
            return m.read_bytes() if m.exists() else b""

        def seen(self) -> int:
            return sum(1 for ln in self.marks().decode().splitlines() if " seen " in f" {ln} ")

    return Rig()


def log_dir(session: str = SID) -> Path:
    d = _injectlog.log_dir(session)
    assert d is not None
    return d


def rows(session: str = SID) -> list[dict]:
    path = log_dir(session) / _injectlog.FILE
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def of(event: str, session: str = SID) -> list[dict]:
    return [r for r in rows(session) if r["event"] == event]


def assert_sized(row: dict, s: str) -> None:
    """Byte for byte: the row's sizes are of S, the string emitted."""
    assert s, "a row for an empty string"
    assert row["chars"] == len(s)
    assert row["lines"] == s.count("\n") + 1
    assert row["tokens_est"] == _tokens.estimate(s)
    assert row["tok"] == "chars/4"


def assert_inject(row: dict, via: str, extra: frozenset = frozenset()) -> None:
    """do-002's schema test, for the listener's paths: the full key set, and
    `hook_event_name` null and `tool_use_ids` [] (no hook payload here)."""
    assert set(row) == INJECT_KEYS | extra, set(row) ^ (INJECT_KEYS | extra)
    assert row["tool"] == "mail" and row["event"] == "inject" and row["via"] == via
    assert row["sid"] == SID and row["pid"] > 0 and isinstance(row["seq"], int)
    assert row["hook_event_name"] is None and row["tool_use_ids"] == []
    assert (row["superseded"], row["held"], row["past_budget"], row["capped"]) == (0, 0, 0, None)
    for it in row["items"]:
        assert set(it) == ITEM_KEYS


def assert_life(row: dict, event: str, via: str) -> None:
    assert set(row) == LIFE_KEYS[event], set(row) ^ LIFE_KEYS[event]
    assert row["tool"] == "mail" and row["event"] == event and row["via"] == via
    assert row["sid"] == SID and isinstance(row["seq"], int)


def listen(*argv: str):
    return CliRunner().invoke(cli, ["listen", *argv], prog_name="pager")


def monitor():
    return CliRunner().invoke(cli, ["listen", "--monitor"], prog_name="pager")


def until(pred, timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _ours() -> bool:
    handler = signal.getsignal(signal.SIGTERM)
    return callable(handler) and getattr(handler, "__name__", "") == "ended"


def driven(script) -> threading.Thread:
    """Run SCRIPT beside the monitor, then end it as claude would (SIGTERM,
    only to the monitor's own handler), as tests/test_pager_monitor.py does."""
    def body() -> None:
        try:
            script()
        finally:
            for _ in range(200):
                if _ours():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(0.01)
    t = threading.Thread(target=body, daemon=True)
    t.start()
    return t


def stdout_lines(result) -> list[str]:
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


# ── monitor: every line `say` prints is a row (§16.2) ────────────────────

def test_say_logs_the_normalised_line_not_its_argument(rig, capsys):
    out = pager.MonitorOut(spacing=0, log=True)
    out.say("pager:   two\n  lines\tand tabs ", items=[], ms=7)
    printed = capsys.readouterr().out
    assert printed == "pager: two lines and tabs\n"
    [row] = rows()
    assert_inject(row, "monitor")
    assert_sized(row, "pager: two lines and tabs")
    assert row["chars"] != len("pager:   two\n  lines\tand tabs "), "the argument is not what printed"
    assert row["seat"] == rig.seat.address and row["ms"] == 7


def test_say_without_log_writes_nothing(rig, capsys):
    pager.MonitorOut(spacing=0).say("a line")
    assert capsys.readouterr().out == "a line\n"
    assert not log_dir().exists()


def test_a_monitor_page_is_one_row_per_line_sized_on_it(rig):
    rig.box()

    def script() -> None:
        for i, mid in enumerate(("m1@example.invalid", "m2@example.invalid"), start=1):
            rig.deliver(mid)
            assert until(lambda: len(of("inject")) >= i), f"page {i} never logged"
        time.sleep(0.2)

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    out = stdout_lines(result)
    pages = of("inject")
    assert len(out) == len(pages) == 2, (out, pages)
    for line, row, mid in zip(out, pages, ("m1@example.invalid", "m2@example.invalid")):
        assert_inject(row, "monitor")
        assert_sized(row, line)
        assert row["items"] == [{"id": mid, "kind": "mail", "view": "count", "given": 0,
                                 "in_mail": None, "cut": True}]
        assert isinstance(row["ms"], int) and row["ms"] >= 0
    raw = (log_dir() / _injectlog.FILE).read_text()
    assert "SECRET-SUBJECT" not in raw and "SENDER-NAME" not in raw and "beta@" not in raw


def test_a_monitor_that_cannot_look_logs_its_deaf_line(rig, monkeypatch):
    rig.box()
    from sessionmail import _detect

    def boom(*a, **k):
        raise OSError("disk on fire")
    monkeypatch.setattr(_detect, "detect", boom)
    result = monitor()
    assert result.exit_code == pager.EX_COULD_NOT_LOOK, result.stderr
    [line] = stdout_lines(result)
    assert line.startswith("pager: DEAF - ")
    [row] = of("inject")
    assert_inject(row, "monitor")
    assert_sized(row, line)
    assert row["items"] == [] and row["ms"] is None, "no arrival: no items, no arrival ms"
    # The deaf line is said inside the wait, so the listener's life brackets it.
    assert [r["event"] for r in rows()] == ["start", "inject", "exit"]
    assert of("exit")[0]["code"] == pager.EX_COULD_NOT_LOOK


def test_a_monitor_switched_off_logs_its_off_line(rig, monkeypatch):
    monkeypatch.setattr(_seat, "monitor_switch",
                        lambda env=None: _seat.MonitorSwitch(False, "TEST_SWITCH=off"))
    result = monitor()
    assert result.exit_code == pager.EX_MONITOR_OFF, result.stderr
    [line] = stdout_lines(result)
    [row] = rows()
    assert_inject(row, "monitor")
    assert_sized(row, line)
    assert row["seat"] == rig.seat.address, "resolved by `say` itself: said before _listen's"


def test_a_monitor_logs_start_and_exit_around_its_pages(rig):
    rig.box()

    def script() -> None:
        assert until(lambda: bool(of("start")))
        rig.deliver("life@example.invalid")
        assert until(lambda: bool(of("inject")))
        time.sleep(0.2)

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    events = [r["event"] for r in rows()]
    assert events == ["start", "inject", "exit"], events
    start, _page, end = rows()
    assert_life(start, "start", "monitor")
    assert_life(end, "exit", "monitor")
    assert start["timeout"] is None and start["box"] == str(rig.root / SID)
    assert end["code"] == 0 and end["pages"] == 1, "SIGTERM is the quiet exit 0 (D5)"
    assert start["pid"] == end["pid"] == os.getpid()


# ── a hand listen: the page's stdout, and its stderr line apart (§16.3) ──

def paged_by(result) -> str:
    [line] = [ln for ln in result.stderr.splitlines() if ln.startswith("pager listen: paged ")]
    return line


def test_a_hand_page_is_one_row_on_its_stdout_lines(rig):
    rig.deliver("hand@example.invalid")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    s = result.stdout.rstrip("\n")
    assert s.count("\n") >= 1, "the quiet page is several lines"
    [row] = of("inject")
    assert_inject(row, "listen", frozenset({"stderr_chars"}))
    assert_sized(row, s)
    assert row["stderr_chars"] == len(paged_by(result))
    assert [it["id"] for it in row["items"]] == ["hand@example.invalid"]
    assert row["items"][0]["view"] == "count" and row["items"][0]["given"] == 0
    assert isinstance(row["ms"], int)


def test_a_hand_page_with_the_list_on_counts_the_list_lines_too(rig, monkeypatch):
    monkeypatch.setenv(pager.PAGE_LIST_VAR, "on")
    rig.deliver("l1@example.invalid")
    rig.deliver("l2@example.invalid")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    s = result.stdout.rstrip("\n")
    [row] = of("inject")
    assert_sized(row, s)
    assert [it["id"] for it in row["items"]] == ["l1@example.invalid", "l2@example.invalid"]
    raw = (log_dir() / _injectlog.FILE).read_text()
    assert "SECRET-SUBJECT" in s and "SECRET-SUBJECT" not in raw, "the list's subject is not logged"


def test_a_quiet_hand_page_prints_nothing_so_logs_no_inject(rig):
    rig.deliver("q@example.invalid")
    result = listen("--quiet", "--timeout", "10")
    assert result.exit_code == EX_PAGE and result.stdout == ""
    assert of("inject") == []
    assert [r["event"] for r in rows()] == ["start", "exit"]


def test_a_hand_listen_with_no_session_logs_nothing(rig, monkeypatch, tmp_path):
    """Spec scenario "A hand listen in a bare terminal logs nothing"."""
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    box = rig.box("human")
    monkeypatch.setenv("MAIL", str(box))
    rig.deliver("bare@example.invalid", name="human")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    assert "1 message" in result.stdout
    home = Path(os.environ["HOME"])
    assert not list(home.rglob("pager.jsonl")) and not list(tmp_path.rglob("pager.jsonl"))
    assert not [p for p in tmp_path.rglob("mail") if p.parent.name == "log"]


# ── the listener's life: start, exit, error; never in marks (Q6) ─────────

def test_start_and_exit_bracket_a_listener_that_times_out(rig):
    """Spec scenario "Start and exit bracket a listener"."""
    rig.box()
    before = rig.marks()
    result = listen("--timeout", "0.3")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    start, end = rows()
    assert_life(start, "start", "listen")
    assert_life(end, "exit", "listen")
    assert start["timeout"] == 0.3 and start["box"] == str(rig.root / SID)
    assert (end["code"], end["pages"]) == (EX_TIMEOUT, 0)
    assert start["pid"] == end["pid"] and end["seq"] == start["seq"] + 1
    assert rig.marks() == before, "marks is byte-identical: no lifecycle line"


def test_a_page_leaves_marks_in_its_own_format_and_no_life_line(rig):
    rig.deliver("fmt@example.invalid")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    assert [r["event"] for r in rows()] == ["start", "inject", "exit"]
    assert of("exit")[0]["pages"] == 1 and of("exit")[0]["code"] == EX_PAGE
    marks = rig.marks().decode()
    assert marks, "the page marked its mail seen"
    for line in marks.splitlines():
        # MARK_LINE, as before: `<ts> <Message-ID> <mark> <seat> <sid> <reason>`.
        assert re.match(r"^\S+ fmt@example\.invalid seen \S+ sess-a pager wake ", line), line
        assert not re.search(r"\b(start|exit|error)\b", line), line
    assert not (log_dir().parent.parent / "mail" / "marks").read_text().count('"event"')


def test_exit_carries_the_cumulative_quiet_counter(rig):
    rig.box()
    assert _injectlog.bump_quiet(SID, 12) and _injectlog.bump_quiet(SID, 30)
    listen("--timeout", "0.2")
    first = of("exit")[-1]
    assert (first["quiet"], first["quiet_ms_total"], first["quiet_ms_max"]) == (2, 42, 30)
    assert _injectlog.bump_quiet(SID, 5)
    listen("--timeout", "0.2")
    second = of("exit")[-1]
    assert (second["quiet"], second["quiet_ms_total"], second["quiet_ms_max"]) == (3, 47, 30), \
        "cumulative: a listener's exit reads the counter, never resets it"


def test_exit_with_no_counter_reads_zeros(rig):
    rig.box()
    listen("--timeout", "0.2")
    end = of("exit")[-1]
    assert (end["quiet"], end["quiet_ms_total"], end["quiet_ms_max"]) == (0, 0, 0)
    assert not (log_dir() / _injectlog.QUIET_FILE).exists(), "reading makes nothing"


def test_a_failed_write_the_listener_depends_on_is_an_error_row(rig, monkeypatch):
    def broken(*a, **k):
        raise OSError("woke dir gone")
    monkeypatch.setattr(_seat, "write_woke", broken)
    rig.deliver("err@example.invalid", original_to="alpha@example.invalid")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    # The page is printed (and logged) first; the marks and the woke record
    # are written after it, so the error follows the inject row.
    assert [r["event"] for r in rows()] == ["start", "inject", "error", "exit"]
    [err] = of("error")
    assert_life(err, "error", "listen")
    assert err["what"] == "woke record: OSError: woke dir gone"


def test_a_failed_confirm_mail_is_an_error_row_and_the_listener_listens(rig, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("no MTA")
    monkeypatch.setattr(pager, "confirm_listening", broken)
    rig.box()
    result = listen("--timeout", "0.2")
    assert result.exit_code == EX_TIMEOUT
    assert [r["event"] for r in rows()] == ["error", "start", "exit"]
    assert of("error")[0]["what"] == "listening mail: RuntimeError: no MTA"


def test_a_log_that_cannot_be_written_never_costs_the_page(rig):
    d = log_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("a FILE where the log dir goes")
    rig.deliver("safe@example.invalid")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE and result.exception is None, result.stderr
    assert "1 message" in result.stdout
    assert d.read_text() == "a FILE where the log dir goes"


# ── the empty-run counter: only the run §16.5 names ──────────────────────

def batch(payload: dict | None = None):
    body = {"session_id": SID, "hook_event_name": "PostToolBatch", "tool_calls": []}
    return CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(payload or body))


def quiet_file() -> Path:
    return log_dir() / _injectlog.QUIET_FILE


def test_an_empty_batch_is_counted_not_logged(rig):
    assert batch().stdout, "the first batch says the state line"
    n = len(rows())
    assert not quiet_file().exists(), "a batch that printed is not an empty run"
    for _ in range(2):
        r = batch()
        assert r.exit_code == 0 and r.stdout == ""
    assert len(rows()) == n, "no row for an empty run"
    q = _injectlog.read_quiet(SID)
    assert q["quiet"] == 2 and q["quiet_ms_max"] <= q["quiet_ms_total"]
    assert q["quiet_ms_total"] >= 0
    assert oct(quiet_file().stat().st_mode & 0o777) == "0o600"


def test_a_carrying_turn_start_that_prints_nothing_is_counted(rig):
    batch()                                          # the state line, said once
    _seat.write_carry(rig.seat, SID)
    r = batch({"session_id": SID, "hook_event_name": "UserPromptSubmit",
               "prompt": "<task-notification>x</task-notification>"})
    assert r.exit_code == 0 and r.stdout == ""
    assert _injectlog.read_quiet(SID)["quiet"] == 1


@pytest.mark.parametrize("case", ["ups-not-carrying", "subagent", "pager-off",
                                  "no-identity", "hooks-off"])
def test_the_runs_ruled_to_write_nothing_never_touch_the_counter(rig, monkeypatch, case):
    """§16.5 / spec.md:763: these empty runs write NOTHING - no row, no count."""
    payload = None
    if case == "ups-not-carrying":
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit",
                   "prompt": "a typed prompt"}
    elif case == "subagent":
        payload = {"session_id": SID, "hook_event_name": "PostToolBatch",
                   "tool_calls": [], "agent_id": "a81b552b29e268608"}
    elif case == "pager-off":
        monkeypatch.setenv(_seat.PAGER_VAR, "off")
    elif case == "no-identity":
        monkeypatch.delenv("TM_AGENT_ADDRESS")

        def none():
            raise _seat.NoIdentity()
        monkeypatch.setattr(_seat, "resolve", none)
    elif case == "hooks-off":
        (Path(os.environ["HOME"]) / _settings.RELATIVE).write_text(
            '{"version": 1, "hooks": false}\n')
    for _ in range(3):
        r = batch(payload)
        assert r.exit_code == 0 and r.stdout == "", (case, r.stdout)
    assert not quiet_file().exists(), case
    assert not (log_dir() / _injectlog.FILE).exists(), case


def test_the_counter_is_fixed_width_and_survives_a_torn_read(rig):
    assert _injectlog.bump_quiet(SID, 3)
    raw = quiet_file().read_text()
    assert raw == f"{1:020d} {3:020d} {3:020d}\n"
    quiet_file().write_text("garbage")
    assert _injectlog.read_quiet(SID) == {"quiet": 0, "quiet_ms_total": 0, "quiet_ms_max": 0}
    assert _injectlog.bump_quiet(SID, 4)
    assert _injectlog.read_quiet(SID)["quiet"] == 1


def test_no_session_counts_nothing_and_makes_nothing(rig):
    for sid in ("-", ""):
        assert _injectlog.bump_quiet(sid, 1) is False
        assert _injectlog.read_quiet(sid) == {"quiet": 0, "quiet_ms_total": 0, "quiet_ms_max": 0}
    assert not log_dir().exists()


WRITER = """
import sys
from sessionmail import _injectlog
for i in range(int(sys.argv[2])):
    assert _injectlog.bump_quiet(sys.argv[1], 1)
"""


def test_concurrent_bumps_lose_no_count(rig, tmp_path):
    import subprocess
    import sys
    script = tmp_path / "bump.py"
    script.write_text(WRITER)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(_injectlog.__file__).resolve().parents[1])
    procs = [subprocess.Popen([sys.executable, str(script), SID, "80"], env=env) for _ in range(3)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    assert _injectlog.read_quiet(SID) == {"quiet": 240, "quiet_ms_total": 240, "quiet_ms_max": 1}


# ── SM-682: a monitor that follows /clear execs, so it logs its exit first ─

def test_a_follow_logs_the_old_sessions_exit_before_the_exec(rig, monkeypatch):
    """`follow_session` execs, which skips every `finally`: the session it
    leaves gets its `exit` row (naming where it went) BEFORE the exec. Here
    the exec fails, so the monitor stays and starts again under its own id."""
    rig.box()
    calls: list = []
    monkeypatch.setattr(_seat, "sidecar_session", lambda caller: "sess-b")

    def no_exec(path, argv):
        calls.append([r["event"] for r in rows()])      # what was logged before it
        raise OSError("exec refused in a test")
    monkeypatch.setattr(pager.os, "execv", no_exec)

    def script() -> None:
        assert until(lambda: len(of("start")) >= 2)
        time.sleep(0.1)

    t = driven(script)
    result = monitor()
    t.join(DEADLINE)
    assert result.exit_code == 0, result.stderr
    assert calls and calls[0] == ["start", "exit"], "the exit row is written before the exec"
    events = [r["event"] for r in rows()]
    assert events == ["start", "exit", "start", "exit"], events
    first, followed, again, last = rows()
    assert set(followed) == LIFE_KEYS["exit"] | {"followed"}
    assert followed["followed"] == "sess-b" and followed["code"] is None
    assert_life(again, "start", "monitor")
    assert_life(last, "exit", "monitor")
    assert last["code"] == 0


def test_follow_session_without_life_is_unchanged(rig, monkeypatch):
    monkeypatch.setattr(_seat, "sidecar_session", lambda caller: "sess-c")
    monkeypatch.setattr(pager.os, "execv", lambda p, a: (_ for _ in ()).throw(OSError("no")))
    pager.follow_session(SID, os.getpid(), rig.seat, set(), stamped=False)
    assert not log_dir().exists()


# ── after the merge with SM-763: a reminder page is sized like any page ──

def test_a_reminder_hand_page_is_sized_with_its_reminder_line(rig, monkeypatch):
    """SM-763 adds a reminder line to a hand page of mail this session was
    given before and has not marked done. It is one of the page's stdout
    lines, so the `listen` row is sized on it too (do-007 merge)."""
    from datetime import datetime, timedelta
    monkeypatch.setenv(_seat.REMIND_VAR, "0.01")         # a 0.6 s reminder window
    rig.deliver("again@example.invalid")
    old = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
    _seat.write_mark(rig.seat, "again@example.invalid", "seen", SID, old, "pager wake")
    result = listen("--timeout", "10")
    assert result.exit_code == EX_PAGE, result.stderr
    s = result.stdout.rstrip("\n")
    assert "pager: reminder - 1 of these" in s, s
    [row] = of("inject")
    assert_inject(row, "listen", frozenset({"stderr_chars"}))
    assert_sized(row, s)
    assert [it["id"] for it in row["items"]] == ["again@example.invalid"]


# ── SM-817: an exit never waits on its log; every other row still does ──

def held_lock(name: str) -> int:
    """A lock on log file NAME through its own open file description: to
    `flock` that is another holder, as a stalled hook would be."""
    import fcntl

    _injectlog._make(log_dir())
    fd = os.open(log_dir() / name, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


QUIET_KEYS = {"quiet", "quiet_ms_total", "quiet_ms_max"}


@pytest.mark.parametrize("raw, seconds", [
    (None, 1.0), ("0.25", 0.25), ("30", 30.0), (" 2 ", 2.0),
    # Defined as the DEFAULT (the safe reading), never "no wait" or "forever":
    ("0", 1.0), ("-2", 1.0), ("abc", 1.0), ("", 1.0), ("nan", 1.0), ("inf", 1.0),
])
def test_the_budget_knob_reads_bad_values_as_the_default(raw, seconds):
    env = {} if raw is None else {_injectlog.LOCK_SECONDS_VAR: raw}
    assert _injectlog.EXIT_LOCK_SECONDS == 1.0
    assert _injectlog.LOCK_SECONDS_VAR == "SESSIONMAIL_LOG_LOCK_SECONDS"   # the documented name
    assert _injectlog.lock_seconds(env) == seconds
    d = _injectlog.exit_deadline(env)
    assert d.seconds == seconds and 0 < d.at - time.monotonic() <= seconds


def test_an_exit_row_with_seq_held_is_dropped_and_said_once(rig, capsys):
    _injectlog.bump_quiet(SID, 3)
    fd = held_lock(_injectlog.SEQ_FILE)
    try:
        t0 = time.monotonic()
        seq = _injectlog.exit_row(SID, via="listen", deadline=_injectlog.exit_deadline(
            {_injectlog.LOCK_SECONDS_VAR: "0.2"}), code=0, pages=0)
        took = time.monotonic() - t0
    finally:
        os.close(fd)
    assert seq is None and of("exit") == []
    assert 0.15 <= took < 0.8, took
    out, err = capsys.readouterr()
    assert out == ""
    [line] = err.splitlines()
    assert line.startswith("pager: exit row not logged: ")
    assert str(log_dir() / "seq") in line and str(log_dir() / "quiet") not in line
    assert "0.2 s exit budget (SESSIONMAIL_LOG_LOCK_SECONDS)" in line


def test_an_exit_row_with_quiet_held_is_written_without_its_counts(rig, capsys):
    _injectlog.bump_quiet(SID, 3)
    fd = held_lock(_injectlog.QUIET_FILE)
    try:
        seq = _injectlog.exit_row(SID, via="listen", deadline=_injectlog.exit_deadline(
            {_injectlog.LOCK_SECONDS_VAR: "0.2"}), code=0, pages=2)
    finally:
        os.close(fd)
    [row] = of("exit")
    assert row["seq"] == seq and (row["code"], row["pages"]) == (0, 2)
    assert set(row) == LIFE_KEYS["exit"] - QUIET_KEYS, "no counts: zeros would be a false count"
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith("pager: exit row logged without its quiet counts: ")
    assert str(log_dir() / "quiet") in line and "0.2 s exit budget" in line


def test_both_locks_held_share_one_budget_and_one_line(rig, capsys):
    _injectlog.bump_quiet(SID, 3)
    fds = [held_lock(_injectlog.QUIET_FILE), held_lock(_injectlog.SEQ_FILE)]
    try:
        t0 = time.monotonic()
        _injectlog.exit_row(SID, via="listen", deadline=_injectlog.exit_deadline(
            {_injectlog.LOCK_SECONDS_VAR: "0.4"}), code=0, pages=0)
        took = time.monotonic() - t0
    finally:
        for fd in fds:
            os.close(fd)
    assert took < 0.7, f"{took:.3f}s: two budgets, not one"
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith("pager: exit row not logged: ")
    assert str(log_dir() / "quiet") in line and str(log_dir() / "seq") in line
    assert of("exit") == []


def test_an_unheld_exit_row_is_written_as_before_and_says_nothing(rig, capsys):
    _injectlog.bump_quiet(SID, 12)
    _injectlog.bump_quiet(SID, 30)
    seq = _injectlog.exit_row(SID, via="monitor", code=0, pages=1)
    [row] = of("exit")
    assert_life(row, "exit", "monitor")
    assert row["seq"] == seq and (row["quiet"], row["quiet_ms_total"], row["quiet_ms_max"]) \
        == (2, 42, 30)
    assert capsys.readouterr() == ("", "")


def test_the_deadline_keywords_each_drop_and_say_once(rig, capsys):
    """append / lifecycle / write / read_quiet take an optional DEADLINE
    (SM-817); past it each drops what it holds and, where it never raises,
    says so once."""
    _injectlog.bump_quiet(SID, 3)
    gone = _injectlog.Deadline(time.monotonic(), 0.5)        # already spent
    fds = [held_lock(_injectlog.SEQ_FILE), held_lock(_injectlog.QUIET_FILE)]
    try:
        with pytest.raises(_injectlog.LockHeld) as exc:
            _injectlog.append(log_dir(), {"ts": "x", "sid": SID}, deadline=gone)
        assert exc.value.path == log_dir() / "seq" and exc.value.deadline is gone
        assert _injectlog.lifecycle(SID, event="error", via="listen", what="w",
                                    deadline=gone) is None
        assert _injectlog.write(SID, via="listen", s="a page", deadline=gone) is None
        assert _injectlog.read_quiet(SID, deadline=gone) == {}
    finally:
        for fd in fds:
            os.close(fd)
    assert rows() == []
    lines = capsys.readouterr().err.splitlines()
    assert [ln.split(":")[1].strip() for ln in lines] == [
        "error row not logged", "inject row not logged", "quiet counts not read"]
    assert all("0.5 s exit budget" in ln for ln in lines)


@pytest.mark.parametrize("call", ["lifecycle", "write", "read_quiet"])
def test_a_row_with_no_deadline_still_waits_for_the_lock(rig, call):
    """Normal operation is unchanged (SM-817 changes the exit path only):
    with no deadline - every hook, the mail-guard, the in-loop rows - a
    held lock is WAITED for, and the row lands once it is let go."""
    _injectlog.bump_quiet(SID, 9)
    name = _injectlog.QUIET_FILE if call == "read_quiet" else _injectlog.SEQ_FILE
    got: list = []
    run = {
        "lifecycle": lambda: _injectlog.lifecycle(SID, event="error", via="monitor", what="w"),
        "write": lambda: _injectlog.write(SID, via="monitor", s="a line"),
        "read_quiet": lambda: _injectlog.read_quiet(SID),
    }[call]
    fd = held_lock(name)
    try:
        t = threading.Thread(target=lambda: got.append(run()), daemon=True)
        t.start()
        time.sleep(1.5 * _injectlog.EXIT_LOCK_SECONDS)          # past any exit budget
        assert t.is_alive() and got == [] and rows() == [], "it dropped instead of waiting"
    finally:
        os.close(fd)
    t.join(DEADLINE)
    assert not t.is_alive()
    if call == "read_quiet":
        assert got == [{"quiet": 1, "quiet_ms_total": 9, "quiet_ms_max": 9}]
    else:
        assert isinstance(got[0], int) and len(rows()) == 1


def test_a_hand_page_and_its_exit_share_one_budget(rig, monkeypatch):
    """The hand page's `inject` row (just before `ctx.exit`) and the
    `finally`'s `exit` row are ONE exit: one deadline, not one each. With
    `seq` held both are dropped, each said once, inside ONE budget."""
    import fcntl

    monkeypatch.setenv(_injectlog.LOCK_SECONDS_VAR, "0.5")
    rig.box()
    taken: list = []

    def take_then_deliver() -> None:
        # After `start` (an in-loop row: it would wait), take `seq`, then
        # hand the listener the mail it pages on.
        assert until(lambda: of("start"))
        fd = os.open(log_dir() / _injectlog.SEQ_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        taken.extend([fd, time.monotonic()])
        rig.deliver("shared@example.invalid")

    t = threading.Thread(target=take_then_deliver, daemon=True)
    t.start()
    try:
        result = listen("--timeout", "10")
        took = time.monotonic() - taken[1]
    finally:
        t.join(DEADLINE)
        if taken:
            os.close(taken[0])
    assert result.exit_code == EX_PAGE, result.stderr
    assert took < 0.95, f"{took:.3f}s: the page and the exit each took a budget"
    said = [ln for ln in result.stderr.splitlines() if "exit budget" in ln]
    assert [ln.split(":")[1].strip() for ln in said] == ["inject row not logged",
                                                         "exit row not logged"], said
    assert [r["event"] for r in rows()] == ["start"]
    assert "exit budget" not in result.stdout
