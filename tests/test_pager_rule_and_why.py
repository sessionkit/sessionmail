"""`pager rule list|add|remove` and `pager why ID`: SM-474 do-008 (tasks.md
5.1 and 5.3; design D6, D7, D8; specs/pager "A session's own rules are its
own; the rest are Ivan's and Judy's" and "Why a mail did what it did").

- 5.1: the SESSION layer by default. `--seat`, `--locked` and any `start`
  rule are for a caller whose seat is `ivan` or `judy`; anyone else exits 2
  with a line naming the request-by-mail-to-Ivan route, and nothing is
  written. A `start` rule added prints that nothing honours it yet (D8).
- 5.3: `pager why ID` prints the action, the rule and its layer (or the
  built-in default that did) from the verdict RECORDED for that Message-ID
  where the marks live. It never re-judges (the do-007 review N1): with no
  record, it says so.

Hermetic: the rig of `test_pager_listen_judges` (a maildir root and a
sessionmail home under tmp_path, the locked file in conftest's sandbox, no
live listener, no real `~/.mail`, no real MTA).

Written to fail first against `15cb8f2`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _seat, pager, rules
from sessionmail.pager import cli
from test_pager_listen_judges import (  # noqa: F401  (the rig is a fixture)
    EX_PAGE,
    EX_TIMEOUT,
    SESSION,
    batch,
    later,
    rig,
    run,
)

EX_OK, EX_REFUSED, EX_NOTHING = 0, 2, 3


def _mail(sid: str) -> Path:
    """A session's mail state since session-settings 3.3a: under the session
    dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/` (spelt out,
    not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def pager_cmd(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="pager")


def session_file(rig) -> Path:
    return _mail(SESSION) / rules.RULES_FILE


def seat_file(rig) -> Path:
    return Path(rig.seat.root) / rules.RULES_FILE


def as_seat(monkeypatch, address: str):
    """The caller's seat is ADDRESS (what `_seat.resolve` reads)."""
    monkeypatch.setenv("TM_AGENT_ADDRESS", address)
    return _seat.resolve()


def enqueue(rig, fname: str) -> None:
    q = _seat.queue_new(rig.seat, SESSION)
    os.link(rig.box() / "new" / fname, q / fname)


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ROOT and its bytes: 'writes nothing' made checkable."""
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── 5.1: the session layer by default ──────────────────────────────────────

def test_add_writes_the_session_layer_by_default(rig):
    result = pager_cmd("rule", "add", "ride", "from:*@delta.chat")
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    assert session_file(rig).read_text() == "ride from:*@delta.chat\n"
    assert str(session_file(rig)) in result.stdout
    assert not seat_file(rig).exists()


def test_a_quoted_rule_is_one_argument_as_the_spec_writes_it(rig):
    result = pager_cmd("rule", "add", "wake x-project:atomnote")
    assert result.exit_code == EX_OK, result.stderr
    assert session_file(rig).read_text() == "wake x-project:atomnote\n"


def test_the_added_rule_decides_the_next_verdict(rig):
    """What `add` writes is what `load_layers` reads: the session layer."""
    assert pager_cmd("rule", "add", "ride from:beta@*").exit_code == EX_OK
    layers = rules.load_layers(rig.seat, SESSION)
    facts = rules.Facts({"x-original-to": SESSION, "from": "beta@hq"})
    verdict = rules.judge(facts, layers, pager.listener_me(rig.seat, SESSION))
    assert (verdict.action, verdict.rule, verdict.layer) == ("ride", "ride from:beta@*", "session")


def test_add_keeps_what_the_file_already_holds_comments_too(rig):
    path = session_file(rig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# mine\nride to:kith@*", encoding="utf-8")       # no final newline
    assert pager_cmd("rule", "add", "wake", "from:eve@*").exit_code == EX_OK
    assert path.read_text() == "# mine\nride to:kith@*\nwake from:eve@*\n"


def test_adding_a_rule_already_there_changes_nothing(rig):
    assert pager_cmd("rule", "add", "ride", "to:kith@*").exit_code == EX_OK
    before = session_file(rig).read_bytes()
    again = pager_cmd("rule", "add", "ride   to:kith@*")                 # spacing differs
    assert again.exit_code == EX_OK, again.stderr
    assert "already" in again.stdout
    assert session_file(rig).read_bytes() == before


def test_a_bad_rule_is_refused_named_and_nothing_is_written(rig):
    result = pager_cmd("rule", "add", "snooze", "from:x")
    assert result.exit_code == EX_REFUSED
    assert "unknown verb 'snooze'" in result.stderr
    assert "nothing written" in result.stderr
    assert not session_file(rig).exists()


def test_a_set_line_is_refused_outside_the_locked_file(rig):
    result = pager_cmd("rule", "add", "set", "urgent", "eve@*")
    assert result.exit_code == EX_REFUSED
    assert "locked file" in result.stderr
    assert not session_file(rig).exists()


def test_no_session_id_means_no_session_layer_and_nothing_written(rig, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    before = snapshot(rig.home)
    result = pager_cmd("rule", "add", "ride", "*")
    assert result.exit_code == EX_REFUSED
    assert "session" in result.stderr
    assert snapshot(rig.home) == before


def test_remove_takes_the_rule_out_and_keeps_the_rest(rig):
    path = session_file(rig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# mine\nride to:kith@*\nwake from:eve@*  # eve\n", encoding="utf-8")
    result = pager_cmd("rule", "remove", "wake", "from:eve@*")
    assert result.exit_code == EX_OK, result.stderr
    assert path.read_text() == "# mine\nride to:kith@*\n"


def test_removing_a_rule_that_is_not_there_changes_nothing(rig):
    path = session_file(rig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ride to:kith@*\n", encoding="utf-8")
    result = pager_cmd("rule", "remove", "ride", "*")
    assert result.exit_code == EX_NOTHING
    assert "nothing changed" in result.stdout
    assert path.read_text() == "ride to:kith@*\n"


def test_list_shows_the_session_layer_with_line_numbers(rig):
    path = session_file(rig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# mine\nride to:kith@*\nbogus x:y\n", encoding="utf-8")
    result = pager_cmd("rule", "list")
    assert result.exit_code == EX_OK, result.stderr
    assert str(path) in result.stdout
    assert "2  ride to:kith@*" in result.stdout
    assert f"{path}:3:" in result.stdout, "a bad line is named, file and line"


def test_list_all_shows_every_layer_in_the_order_that_decides(rig, monkeypatch):
    rig.session_rules("ride to:kith@*\n")
    rig.session_rules("sleep\n", rules.FOCUS_FILE)
    rig.locked("wake! from:eve@*\n")
    result = pager_cmd("rule", "list", "--all")
    assert result.exit_code == EX_OK, result.stderr
    out = result.stdout
    order = [out.index(name) for name in ("locked", "focus: sleep", "session", "built-in")]
    assert order == sorted(order), out
    assert "wake to:$me" in out, "the built-in defaults are listed too"


def test_list_needs_no_privilege_for_the_seat_layer(rig):
    seat_file(rig).parent.mkdir(parents=True, exist_ok=True)
    seat_file(rig).write_text("ride from:*@delta.chat\n", encoding="utf-8")
    result = pager_cmd("rule", "list", "--seat")
    assert result.exit_code == EX_OK, result.stderr
    assert "ride from:*@delta.chat" in result.stdout


def test_list_of_a_layer_whose_folder_cannot_be_searched_could_not_look(rig):
    """SM-877: a layer's folder that lists but cannot be searched (r--)
    denies the stat of its rules file. `rule list` says it could not look
    (exit 2); it never says "no file: no rules of its own", which is what
    `Path.exists()` made of it from CPython 3.14."""
    path = session_file(rig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ride to:kith@*\n", encoding="utf-8")
    before = path.parent.stat().st_mode & 0o7777
    os.chmod(path.parent, 0o444)
    try:
        if os.access(path.parent, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        result = pager_cmd("rule", "list")
    finally:
        os.chmod(path.parent, before)
    assert result.exit_code == pager.EX_COULD_NOT_LOOK, (result.stdout, result.stderr)
    assert "could not look" in result.stderr
    assert "no file" not in result.stdout


# ── 5.1: seat, locked and start are Ivan's and Judy's ──────────────────────

def test_a_worker_seat_rule_add_exits_2_names_the_route_and_writes_nothing(rig, monkeypatch):
    """The slice's criterion, and the spec's *A worker tries to change its
    seat's rules*: `pager rule add --seat 'ride from:*'`."""
    as_seat(monkeypatch, "worker")
    before = snapshot(rig.home), snapshot(rules.locked_path().parent)
    result = pager_cmd("rule", "add", "--seat", "ride from:*")
    assert result.exit_code == EX_REFUSED, (result.stdout, result.stderr)
    assert "message send ivan" in result.stderr, "names the request-by-mail route"
    assert "by mail, from a privileged seat: message send ivan" in result.stderr
    assert "nothing written" in result.stderr
    assert (snapshot(rig.home), snapshot(rules.locked_path().parent)) == before


@pytest.mark.parametrize("argv", [
    ["add", "--locked", "wake! from:eve@*"],
    ["add", "start from:eve@*"],
    ["add", "--seat", "start from:eve@*"],
    ["remove", "--seat", "ride from:*"],
    ["remove", "--locked", "ride from:*"],
    ["remove", "start from:eve@*"],
])
def test_every_privileged_write_is_refused_to_anyone_else(rig, monkeypatch, argv):
    as_seat(monkeypatch, "alpha")
    # something for the removes to find, so a refusal is not a no-op in disguise
    seat_file(rig).parent.mkdir(parents=True, exist_ok=True)
    seat_file(rig).write_text("ride from:*\n", encoding="utf-8")
    rig.locked("ride from:*\n")
    rig.session_rules("start from:eve@*\n")
    before = snapshot(rig.home), snapshot(rules.locked_path().parent)
    result = pager_cmd("rule", *argv)
    assert result.exit_code == EX_REFUSED, (argv, result.stdout, result.stderr)
    assert "message send ivan" in result.stderr
    assert (snapshot(rig.home), snapshot(rules.locked_path().parent)) == before


def test_a_plus_seat_named_like_ivan_is_not_ivan(rig, monkeypatch):
    """The gate is the seat's WHOLE address: `ivan+x` is not `ivan`."""
    as_seat(monkeypatch, "ivan+x")
    result = pager_cmd("rule", "add", "--seat", "ride from:*")
    assert result.exit_code == EX_REFUSED


@pytest.mark.parametrize("who", ["ivan", "judy"])
def test_ivan_and_judy_may_write_their_seat_layer(rig, monkeypatch, who):
    seat = as_seat(monkeypatch, who)
    result = pager_cmd("rule", "add", "--seat", "ride", "from:*@delta.chat")
    assert result.exit_code == EX_OK, result.stderr
    assert (Path(seat.root) / rules.RULES_FILE).read_text() == "ride from:*@delta.chat\n"
    removed = pager_cmd("rule", "remove", "--seat", "ride from:*@delta.chat")
    assert removed.exit_code == EX_OK, removed.stderr
    assert (Path(seat.root) / rules.RULES_FILE).read_text() == ""


def test_ivan_may_write_the_locked_file_and_its_set_lines(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    assert pager_cmd("rule", "add", "--locked", "wake! from:eve@*").exit_code == EX_OK
    assert pager_cmd("rule", "add", "--locked", "set urgent $operator eve@*").exit_code == EX_OK
    path = rules.locked_path()
    assert path.read_text() == "wake! from:eve@*\nset urgent $operator eve@*\n"
    settings = rules.load_layers(None, SESSION).settings
    assert settings.urgent == ("$operator", "eve@*")


def test_a_bad_set_line_is_refused_even_for_ivan(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("rule", "add", "--locked", "set nobody x@*")
    assert result.exit_code == EX_REFUSED
    assert not rules.locked_path().exists()


@pytest.mark.parametrize("word", ["0", "-5", "ten", "1.5"])
def test_a_bad_shorten_cap_is_refused_at_write_and_nothing_written(rig, monkeypatch, word):
    """SM-792 N3: the write-time half of "refused loudly". The writer runs
    the same `parse_settings`, so `set subject-shorten-max 0` never lands,
    and the refusal names the OFF switch as the way to never shorten."""
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("rule", "add", "--locked", f"set subject-shorten-max {word}")
    assert result.exit_code == EX_REFUSED
    assert "whole number above 0" in result.stderr and "nothing written" in result.stderr
    assert "'set subject-shorten off'" in result.stderr
    assert not rules.locked_path().exists()


def test_a_good_shorten_cap_is_written_to_the_locked_file(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("rule", "add", "--locked", "set subject-shorten-max 24")
    assert result.exit_code == EX_OK, result.stderr
    assert rules.locked_path().read_text() == "set subject-shorten-max 24\n"
    assert rules.shorten_max(rules.read_settings()) == 24


def test_a_start_rule_added_says_nothing_honours_it_yet(rig, monkeypatch):
    """D8: `start` parses and is stored; the listener never acts on it."""
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("rule", "add", "start", "from:eve@*")
    assert result.exit_code == EX_OK, result.stderr
    assert "nothing honours" in result.stdout
    assert session_file(rig).read_text() == "start from:eve@*\n"


def test_seat_and_locked_together_is_a_usage_error(rig, monkeypatch):
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("rule", "add", "--seat", "--locked", "ride *")
    assert result.exit_code == EX_REFUSED
    assert not seat_file(rig).exists() and not rules.locked_path().exists()


# ── 5.3: the verdict is recorded where the marks live ──────────────────────

def verdicts(rig) -> list[dict]:
    path = _seat.verdicts_path(rig.seat, SESSION)
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_the_verdicts_file_lives_beside_the_marks(rig):
    assert _seat.verdicts_path(rig.seat, SESSION).parent == \
        _seat.marks_path(rig.seat, SESSION).parent


def test_the_listener_records_each_verdict_keyed_by_message_id(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("ride1@x.invalid", to="atomnote"))
    later(0.4, lambda: rig.deliver("wake1@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr
    got = {v["message_id"]: v for v in verdicts(rig)}
    assert got["ride1@x.invalid"]["action"] == "ride"
    assert got["ride1@x.invalid"]["rule"] == "ride *"
    assert got["ride1@x.invalid"]["layer"] == "built-in"
    assert got["ride1@x.invalid"]["by"] == "pager listen"
    assert got["wake1@x.invalid"]["action"] == "wake"
    assert got["wake1@x.invalid"]["rule"] == "wake to:$me"


# ── 5.3: pager why ─────────────────────────────────────────────────────────

def sleep_and_kith_rode(rig):
    """The spec's *Why didn't that wake me*: a Kith mail rides along while
    the session is in `sleep`, judged by a real listener."""
    rig.box()
    rig.session_rules("sleep\n", rules.FOCUS_FILE)
    rig.stopped()
    later(0.2, lambda: rig.deliver("kith1@x.invalid", sender="kith@hq", to="kith"))
    result = run("--timeout", "1")
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)


def test_why_on_a_kith_mail_ridden_in_sleep_prints_ride_the_rule_and_focus_sleep(rig):
    """The slice's criterion: `ride`, `ride *`, `focus: sleep`."""
    sleep_and_kith_rode(rig)
    result = pager_cmd("why", "kith1@x.invalid")
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    out = result.stdout
    assert "ride" in out and "`ride *`" in out and "focus: sleep" in out, out


def test_why_takes_the_message_id_with_its_angle_brackets_too(rig):
    sleep_and_kith_rode(rig)
    result = pager_cmd("why", "<kith1@x.invalid>")
    assert result.exit_code == EX_OK
    assert "focus: sleep" in result.stdout


def test_why_reads_the_recorded_verdict_it_never_re_judges(rig):
    """The N1 carry: the rules change after the arrival; `why`
    still reports what APPLIED, not what the rules would say now."""
    sleep_and_kith_rode(rig)
    (_mail(SESSION) / rules.FOCUS_FILE).unlink()
    rig.session_rules("wake from:kith@*\n")
    result = pager_cmd("why", "kith1@x.invalid")
    assert result.exit_code == EX_OK
    assert "focus: sleep" in result.stdout
    assert "wake from:kith@*" not in result.stdout


def test_why_names_the_built_in_default_that_decided(rig):
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("d1@x.invalid"))
    assert run("--timeout", "3").exit_code == EX_PAGE
    result = pager_cmd("why", "d1@x.invalid")
    assert result.exit_code == EX_OK
    assert "wake" in result.stdout and "`wake to:$me`" in result.stdout
    assert "built-in default" in result.stdout


def test_why_names_a_session_rule_and_its_line(rig):
    rig.box()
    rig.session_rules("# mine\nride from:beta@*\n")
    rig.stopped()
    later(0.2, lambda: rig.deliver("s1@x.invalid"))
    assert run("--timeout", "1").exit_code == EX_TIMEOUT
    out = pager_cmd("why", "s1@x.invalid").stdout
    assert "`ride from:beta@*`" in out and "session" in out and "line 2" in out, out


def test_why_on_a_pager_note_says_the_rules_never_saw_it(rig):
    pager.record_verdict(rig.seat, SESSION, "n1@x.invalid", pager._note_verdict(),
                         "pager listen")
    out = pager_cmd("why", "n1@x.invalid").stdout
    assert "Pager-Note" in out and "ride" in out


def test_why_with_no_recorded_verdict_says_so_and_judges_nothing(rig):
    result = pager_cmd("why", "never@x.invalid")
    assert result.exit_code == EX_NOTHING
    assert "no verdict recorded" in result.stdout
    assert "never@x.invalid" in result.stdout


def test_why_shows_each_distinct_verdict_when_a_mail_was_judged_twice(rig):
    """A mail still queued across a re-arm is judged by each listener; if
    the rules changed between, both verdicts are history and both show."""
    ride = rules.Verdict("ride", "ride *", "built-in", 7)
    wake = rules.Verdict("wake", "wake from:beta@*", "session", 1)
    for v in (ride, ride, wake):
        pager.record_verdict(rig.seat, SESSION, "t1@x.invalid", v, "pager listen")
    out = pager_cmd("why", "t1@x.invalid").stdout
    assert out.count("`ride *`") == 1 and out.count("`wake from:beta@*`") == 1, out
    assert out.index("`ride *`") < out.index("`wake from:beta@*`")


def test_a_record_that_cannot_be_written_never_costs_the_listener(rig, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(_seat, "write_verdict", boom)
    rig.box()
    rig.stopped()
    later(0.2, lambda: rig.deliver("w9@x.invalid"))
    result = run("--timeout", "3")
    assert result.exit_code == EX_PAGE, result.stderr


# ── the ride-along reads the stored verdict too (N1) ───────────────────────

def test_the_ride_along_line_reports_the_stored_verdict_not_a_re_judge(rig):
    """The listener judged the mail `ride` (focus: sleep); the focus is then
    lifted. The ride-along line reports the action that APPLIED."""
    sleep_and_kith_rode(rig)
    (_mail(SESSION) / rules.FOCUS_FILE).unlink()
    rig.session_rules("wake from:kith@*\n")
    lines = [ln for ln in batch() if ln.startswith("[mail]")]
    assert len(lines) == 1 and lines[0].endswith("(ride)"), lines


def test_a_mail_the_ride_along_judges_first_is_recorded_by_it(rig):
    """No listener ran: the ride-along's own judgement is the verdict, and
    it is recorded, so `why` works for it too."""
    rig.box()
    enqueue(rig, rig.deliver("r7@x.invalid", to="atomnote"))
    lines = [ln for ln in batch() if ln.startswith("[mail]")]
    assert lines and lines[0].endswith("(ride)"), lines
    got = [v for v in verdicts(rig) if v["message_id"] == "r7@x.invalid"]
    assert len(got) == 1 and got[0]["by"] == "ride-along", got
    out = pager_cmd("why", "r7@x.invalid").stdout
    assert "`ride *`" in out and "ride-along" in out, out


# ── the pages ──────────────────────────────────────────────────────────────

def test_the_group_page_lists_rule_and_why(rig):
    out = pager_cmd("-h").stdout
    assert "rule" in out and "why" in out


def test_the_rule_page_names_the_route_and_the_exit_codes():
    out = pager_cmd("rule", "add", "-h").stdout
    # SM-727: the route is "a privileged seat", from config, never a name
    assert "a privileged seat" in out and "asks one" in out and "Exit:" in out and "--seat" in out



def test_why_says_a_ride_was_promoted_when_the_record_says_so(rig):
    """do-014 F1 (b): a ride on a path with no ride-along pages. That path
    has no session to record under today (a session id is a seat, so it has
    a queue), so the record is made by hand: `why` must still say it."""
    ride = rules.Verdict("ride", "ride *", "built-in", 7)
    pager.record_verdict(rig.seat, SESSION, "p1@x.invalid", ride, "pager listen",
                         promoted=True)
    out = pager_cmd("why", "p1@x.invalid").stdout
    assert "promoted from ride by `ride *`" in out and "no ride-along" in out, out
