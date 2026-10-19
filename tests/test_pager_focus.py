"""`pager focus sleep|off [--until TIME]`: SM-474 do-009 (tasks.md 5.2;
design D5, D6, D9, O-c, O-d, O-e; specs/pager "Focus modes: a named rules
layer, left only on purpose or at its time" and "A session's own rules are
its own; the rest are Ivan's and Judy's").

- The agent switches its OWN session's mode (P6), in the session's `focus`
  file (O-c), which `rules.load_layers` reads at D3 layer 0.5.
- ANOTHER session's mode: Judy, Ivan, or its line manager (P6). How the
  pager knows a line manager is OPEN (O-e) and NOT built on a guess: anyone
  but Ivan and Judy is refused, exit 2, with the request-by-mail route.
- The FLEET-WIDE mode is a `focus` line in the locked file (P14), switched
  only by Judy or Ivan. The spec's scenario: a worker's fleet-wide `pager
  focus sleep` exits 2, names the request-by-mail route, and no mode changes.
- Each change sends the focus confirmation mail (D9, do-007's
  `confirm_focus`) exactly once, to the session whose mode changed, from
  the changer. A request that
  changes nothing sends nothing.

Hermetic: the rig of `test_pager_listen_judges` (a maildir root and a
sessionmail home under tmp_path, the locked file in conftest's sandbox),
and `confirm_focus` replaced by a recorder, so no mail goes anywhere.

Written to fail first against `a9dfc39`.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _seat, pager, rules
from sessionmail.pager import cli
from test_pager_listen_judges import SESSION, rig  # noqa: F401  (the rig is a fixture)

EX_OK, EX_REFUSED = 0, 2
OTHER = "sess-b"


def _mail(sid: str) -> Path:
    """A session's mail state since session-settings 3.3a: under the session
    dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/` (spelt out,
    not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def pager_cmd(*argv: str):
    return CliRunner().invoke(cli, list(argv), prog_name="pager")


def as_seat(monkeypatch, address: str):
    monkeypatch.setenv("TM_AGENT_ADDRESS", address)
    return _seat.resolve()


def focus_file(rig, session: str = SESSION) -> Path:
    return _mail(session) / rules.FOCUS_FILE


def other_session(rig) -> Path:
    """A second session on this host: its folder exists, as a live one's does."""
    d = _mail(OTHER)
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot(*roots: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for root in roots:
        if root.exists():
            out.update({str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()})
    return out


def now() -> datetime:
    return datetime.now().astimezone()


def mode_of(session: str = SESSION, seat=None):
    """The mode `load_layers` puts in effect for SESSION now, or None."""
    return rules.load_layers(seat, session).focus(now())


@pytest.fixture
def sent(monkeypatch):
    """Every `confirm_focus` call, as (args, kwargs). Nothing is sent."""
    calls: list[tuple[tuple, dict]] = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return f"<focus-{len(calls)}@test.invalid>"

    monkeypatch.setattr(pager, "confirm_focus", record)
    return calls


def call_of(entry) -> dict:
    """A recorded `confirm_focus(session, mode, until, *, from_)` by name."""
    args, kwargs = entry
    named = dict(zip(("session", "mode", "until"), args))
    named.update(kwargs)
    return named


# ── the agent, for itself ──────────────────────────────────────────────────

def test_sleep_puts_this_session_in_sleep(rig, sent):
    result = pager_cmd("focus", "sleep")
    assert result.exit_code == EX_OK, (result.stdout, result.stderr)
    assert focus_file(rig).read_text() == "sleep\n"
    mode = mode_of()
    assert mode is not None and (mode.mode, mode.until, mode.where) == ("sleep", None, "session")


def test_sleep_mails_the_changed_session_once_from_the_changer(rig, sent):
    """do-007 N2: exactly once, to the changed session, the changer as from_."""
    assert pager_cmd("focus", "sleep").exit_code == EX_OK
    assert len(sent) == 1, sent
    got = call_of(sent[0])
    assert (got["session"], got["mode"], got["until"], got["from_"]) == \
        (SESSION, "sleep", None, "alpha")


def test_the_mail_is_the_focus_note_through_the_one_seam(rig, monkeypatch):
    """Not the recorder: the real `confirm_focus`, down to `_mta_send`."""
    reqs = []
    monkeypatch.setattr(pager, "_mta_send", lambda req, env, mint=None: reqs.append(req) or "<m@t.invalid>")
    result = pager_cmd("focus", "sleep")
    assert result.exit_code == EX_OK, result.stderr
    assert len(reqs) == 1
    req = reqs[0]
    assert req.to == SESSION and req.from_ == "alpha"
    assert (pager.PAGER_NOTE, pager.NOTE_FOCUS) in req.headers
    assert SESSION in req.subject and "sleep" in req.subject
    assert "<m@t.invalid>" in result.stdout + result.stderr, "the Message-ID is named"


def test_off_takes_this_session_out_and_mails_it(rig, sent):
    rig.session_rules("sleep\n", rules.FOCUS_FILE)
    result = pager_cmd("focus", "off")
    assert result.exit_code == EX_OK, result.stderr
    assert mode_of() is None
    assert [call_of(c)["mode"] for c in sent] == ["off"]
    assert call_of(sent[0])["until"] is None


def test_until_hh_mm_ends_at_the_next_such_time(rig, sent):
    result = pager_cmd("focus", "sleep", "--until", "07:00")
    assert result.exit_code == EX_OK, result.stderr
    mode = mode_of()
    assert mode is not None and mode.mode == "sleep"
    t = now()
    assert (mode.until.hour, mode.until.minute) == (7, 0)
    assert t < mode.until <= t + timedelta(days=1)
    assert call_of(sent[0])["until"] == mode.until


def test_until_is_written_as_the_moment_it_names(rig, sent):
    """An `HH:MM` in the file is anchored at the file's mtime (O-c), so a
    touch would move it; the verb writes the absolute time instead."""
    assert pager_cmd("focus", "sleep", "--until", "07:00").exit_code == EX_OK
    text = focus_file(rig).read_text()
    until = mode_of().until
    assert text == f"sleep until {until.strftime('%Y-%m-%dT%H:%M')}\n", text


def wall_time(naive: datetime) -> datetime:
    """NAIVE local wall time as the moment it names: the UTC offset is the one
    in force AT that time, not now's (SM-570). `now() + timedelta(...)` keeps
    now's fixed offset, so across a DST change it names a different instant."""
    assert naive.tzinfo is None, naive
    return naive.astimezone()


def test_until_an_iso_time(rig, sent):
    when = wall_time((datetime.now() + timedelta(days=2)).replace(hour=9, minute=30, second=0, microsecond=0))
    stamp = when.strftime("%Y-%m-%dT%H:%M")
    assert pager_cmd("focus", "sleep", "--until", stamp).exit_code == EX_OK
    assert mode_of().until == when


#: A DST-observing zone as a POSIX TZ rule (Australia/Sydney's: AEST +10, AEDT
#: +11 from the first Sunday in October 02:00 to the first Sunday in April
#: 03:00). A rule, not a zone name, so it needs no tz database on the host.
DST_ZONE = "AEST-10AEDT,M10.1.0,M4.1.0/3"


@pytest.fixture
def dst_zone():
    """This process on DST_ZONE for the test, whatever the host's TZ (a UTC
    host has no DST at all), and back to the host's after."""
    had = os.environ.get("TZ")
    os.environ["TZ"] = DST_ZONE
    time.tzset()
    try:
        yield DST_ZONE
    finally:
        if had is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = had
        time.tzset()


def next_offset_change(start: datetime) -> datetime:
    """The first local day after START whose noon has a different UTC offset
    from START's: the far side of the next DST change, from any date."""
    offset = start.astimezone().utcoffset()
    day = start.replace(hour=12, minute=0, second=0, microsecond=0)
    for _ in range(370):
        day += timedelta(days=1)
        if wall_time(day).utcoffset() != offset:
            return day
    raise AssertionError(f"no UTC offset change within a year of {start} in TZ={os.environ.get('TZ')}")


def test_until_an_iso_time_across_a_dst_change(rig, sent, dst_zone):
    """SM-570: the target is ON PURPOSE on the far side of the next DST change,
    so the test cannot pass by luck of today's date or the host's zone."""
    start = datetime.now()
    when = wall_time(next_offset_change(start).replace(hour=9, minute=30))
    assert when.utcoffset() != now().utcoffset(), "the target must cross a DST change"
    stamp = when.strftime("%Y-%m-%dT%H:%M")
    assert pager_cmd("focus", "sleep", "--until", stamp).exit_code == EX_OK
    assert mode_of().until == when
    assert mode_of().until.utcoffset() == when.utcoffset()


def test_until_in_the_past_is_refused_and_nothing_changes(rig, sent):
    stamp = (now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    result = pager_cmd("focus", "sleep", "--until", stamp)
    assert result.exit_code == EX_REFUSED
    assert "past" in result.stderr
    assert not focus_file(rig).exists() and not sent


def test_a_bad_until_is_refused_named_and_nothing_changes(rig, sent):
    result = pager_cmd("focus", "sleep", "--until", "tea-time")
    assert result.exit_code == EX_REFUSED
    assert "tea-time" in result.stderr and "not a time" in result.stderr
    assert not focus_file(rig).exists() and not sent


def test_off_takes_no_until(rig, sent):
    result = pager_cmd("focus", "off", "--until", "07:00")
    assert result.exit_code == EX_REFUSED
    assert "off" in result.stderr
    assert not focus_file(rig).exists() and not sent


def test_an_unknown_mode_is_a_usage_error(rig, sent):
    result = pager_cmd("focus", "airplane")
    assert result.exit_code == EX_REFUSED
    assert not focus_file(rig).exists() and not sent


def test_sleep_again_changes_nothing_and_mails_nothing(rig, sent):
    assert pager_cmd("focus", "sleep").exit_code == EX_OK
    before = focus_file(rig).read_bytes()
    again = pager_cmd("focus", "sleep")
    assert again.exit_code == EX_OK, again.stderr
    assert "already" in again.stdout
    assert focus_file(rig).read_bytes() == before
    assert len(sent) == 1, "a request that changes nothing is not a focus change"


def test_off_when_already_off_writes_nothing_and_mails_nothing(rig, sent):
    result = pager_cmd("focus", "off")
    assert result.exit_code == EX_OK, result.stderr
    assert "already" in result.stdout
    assert not focus_file(rig).exists() and not sent


def test_a_new_end_time_is_a_change(rig, sent):
    assert pager_cmd("focus", "sleep").exit_code == EX_OK
    assert pager_cmd("focus", "sleep", "--until", "07:00").exit_code == EX_OK
    assert len(sent) == 2
    assert mode_of().until is not None


def test_an_ended_sleep_is_off_so_sleep_is_a_change(rig, sent):
    stale = (now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    rig.session_rules(f"sleep until {stale}\n", rules.FOCUS_FILE)
    assert mode_of() is None
    assert pager_cmd("focus", "sleep").exit_code == EX_OK
    assert mode_of() is not None and len(sent) == 1


def test_no_session_id_is_refused_and_nothing_written(rig, sent, monkeypatch):
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    before = snapshot(rig.home)
    result = pager_cmd("focus", "sleep")
    assert result.exit_code == EX_REFUSED
    assert "session" in result.stderr
    assert snapshot(rig.home) == before and not sent


def test_asleep_kith_rides_and_judy_wakes_without_ending_sleep(rig, sent):
    """The spec's *Asleep, Kith rides along and Judy's mail wakes without
    ending sleep*, with the mode set by the verb, not by hand."""
    assert pager_cmd("focus", "sleep").exit_code == EX_OK
    layers = rules.load_layers(rig.seat, SESSION)
    me = pager.listener_me(rig.seat, SESSION)
    kith = rules.judge(rules.Facts({"x-original-to": SESSION, "from": "kith@hq"}), layers, me)
    assert (kith.action, kith.layer) == ("ride", "focus: sleep")
    judy = rules.judge(rules.Facts({"x-original-to": SESSION, "from": "judy@hq"}), layers, me)
    assert judy.action == "wake"
    assert mode_of() is not None and mode_of().mode == "sleep"


def test_a_failed_confirmation_is_named_and_the_change_stands(rig, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no MTA")

    monkeypatch.setattr(pager, "confirm_focus", boom)
    result = pager_cmd("focus", "sleep")
    assert result.exit_code == EX_OK, result.stderr
    assert "not sent" in result.stdout + result.stderr
    assert "no MTA" in result.stdout + result.stderr
    assert mode_of().mode == "sleep"


# ── fleet-wide: Judy or Ivan, a line in the locked file (P14) ──────────────

def test_a_worker_fleet_wide_sleep_exits_2_names_the_route_no_mode_changes(rig, sent, monkeypatch):
    """The slice's criterion, and the spec's *A worker tries to switch the
    fleet's focus mode*."""
    as_seat(monkeypatch, "worker")
    rig.locked("wake! from:eve@*\n")
    before = snapshot(rig.home, rules.locked_path().parent)
    result = pager_cmd("focus", "sleep", "--fleet")
    assert result.exit_code == EX_REFUSED, (result.stdout, result.stderr)
    assert "message send ivan" in result.stderr, "names the request-by-mail route"
    assert "by mail, from a privileged seat: message send ivan" in result.stderr
    assert "nothing changed" in result.stderr
    assert snapshot(rig.home, rules.locked_path().parent) == before
    assert rules.load_layers(None, SESSION).modes == ()
    assert not sent


@pytest.mark.parametrize("argv", [["off", "--fleet"], ["sleep", "--fleet", "--until", "07:00"]])
def test_every_fleet_wide_change_is_refused_to_anyone_else(rig, sent, monkeypatch, argv):
    as_seat(monkeypatch, "alpha")
    rig.locked("focus sleep\n")
    before = snapshot(rig.home, rules.locked_path().parent)
    result = pager_cmd("focus", *argv)
    assert result.exit_code == EX_REFUSED, (argv, result.stdout, result.stderr)
    assert "message send ivan" in result.stderr
    assert snapshot(rig.home, rules.locked_path().parent) == before and not sent


def test_a_plus_seat_named_like_ivan_is_not_ivan(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan+x")
    assert pager_cmd("focus", "sleep", "--fleet").exit_code == EX_REFUSED
    assert not rules.locked_path().exists() and not sent


@pytest.mark.parametrize("who", ["ivan", "judy"])
def test_ivan_and_judy_switch_the_fleet_wide_mode_in_the_locked_file(rig, sent, monkeypatch, who):
    as_seat(monkeypatch, who)
    rig.locked("# host\nwake! from:eve@*\nset urgent $operator eve@*\n")
    result = pager_cmd("focus", "sleep", "--fleet")
    assert result.exit_code == EX_OK, result.stderr
    text = rules.locked_path().read_text()
    assert text == "# host\nwake! from:eve@*\nset urgent $operator eve@*\nfocus sleep\n", text
    layers = rules.load_layers(None, "any-session")
    assert [(m.mode, m.where) for m in layers.modes] == [("sleep", rules.FLEET)]
    assert layers.settings.urgent == ("$operator", "eve@*"), "the rest of the file is kept"


def test_fleet_wide_off_takes_the_focus_line_out_and_keeps_the_rest(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan")
    rig.locked("wake! from:eve@*\nfocus sleep  # night\nride from:*@delta.chat\n")
    result = pager_cmd("focus", "off", "--fleet")
    assert result.exit_code == EX_OK, result.stderr
    assert rules.locked_path().read_text() == "wake! from:eve@*\nride from:*@delta.chat\n"
    assert rules.load_layers(None, SESSION).modes == ()


def test_fleet_wide_sleep_replaces_the_old_line_not_adds_one(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan")
    rig.locked("focus sleep\n")
    assert pager_cmd("focus", "sleep", "--fleet", "--until", "07:00").exit_code == EX_OK
    lines = [ln for ln in rules.locked_path().read_text().splitlines() if ln.startswith("focus")]
    assert len(lines) == 1 and lines[0].startswith("focus sleep until "), lines
    mode = rules.load_layers(None, SESSION).focus(now())
    assert mode is not None and (mode.until.hour, mode.until.minute) == (7, 0)


def test_fleet_wide_change_mails_once_to_the_changers_session(rig, sent, monkeypatch):
    """D9 names "the session whose mode changed"; fleet-wide, that is every
    session on the host. One mail, to the changer's own (also changed),
    never a broadcast."""
    as_seat(monkeypatch, "ivan")
    assert pager_cmd("focus", "sleep", "--fleet").exit_code == EX_OK
    assert len(sent) == 1
    got = call_of(sent[0])
    assert (got["session"], got["mode"], got["from_"]) == (SESSION, "sleep", "ivan")


def test_fleet_wide_already_so_changes_nothing(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan")
    rig.locked("focus sleep\n")
    before = rules.locked_path().read_bytes()
    result = pager_cmd("focus", "sleep", "--fleet")
    assert result.exit_code == EX_OK, result.stderr
    assert "already" in result.stdout
    assert rules.locked_path().read_bytes() == before and not sent


@pytest.mark.parametrize("argv", [["off", "--fleet"], ["sleep", "--fleet", "--until", "07:00"]])
def test_fleet_wide_change_finds_the_focus_line_the_loader_reads(rig, sent, monkeypatch, argv):
    """do-009 review: the verb finds the old `focus` line by the LOADER's own
    reading (`rules._split_fleet`), so a line the loader counts cannot
    survive the rewrite. A locked file that starts with a BOM kept its
    `\ufefffocus sleep` line, which the loader reads as the fleet's mode:
    the verb said `now off` and mailed it, and the fleet stayed asleep."""
    as_seat(monkeypatch, "ivan")
    path = rules.locked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufefffocus sleep\r\nwake! from:eve@*\r\n", encoding="utf-8")
    assert rules.load_layers(None, SESSION).focus(now()) is not None, "the loader reads sleep"
    result = pager_cmd("focus", *argv)
    assert result.exit_code == EX_OK, result.stderr
    layers = rules.load_layers(None, SESSION)
    assert not layers.errors, layers.errors
    mode = layers.focus(now())
    if argv[0] == "off":
        assert mode is None, f"the verb said off; the loader still reads {mode}"
    else:
        assert mode is not None and mode.until is not None, mode
    # `_rule_read` reads the raw bytes (do-016), so the rewrite keeps the
    # other line byte for byte, its CRLF included.
    assert b"wake! from:eve@*\r\n" in path.read_bytes(), "every other line is kept, as it was"


# ── another session: Judy or Ivan (a line manager: O-e, not built) ─────────

def test_a_worker_cannot_switch_another_sessions_mode(rig, sent, monkeypatch):
    as_seat(monkeypatch, "worker")
    other_session(rig)
    before = snapshot(rig.home)
    result = pager_cmd("focus", "sleep", "--for", OTHER)
    assert result.exit_code == EX_REFUSED, (result.stdout, result.stderr)
    assert "message send ivan" in result.stderr
    assert "line manager" in result.stderr, "O-e is named, not hidden"
    assert snapshot(rig.home) == before and not sent


def test_for_ones_own_session_is_ones_own(rig, sent):
    result = pager_cmd("focus", "sleep", "--for", SESSION)
    assert result.exit_code == EX_OK, result.stderr
    assert focus_file(rig).read_text() == "sleep\n"


@pytest.mark.parametrize("who", ["ivan", "judy"])
def test_ivan_and_judy_switch_another_sessions_mode(rig, sent, monkeypatch, who):
    as_seat(monkeypatch, who)
    other_session(rig)
    result = pager_cmd("focus", "sleep", "--for", OTHER)
    assert result.exit_code == EX_OK, result.stderr
    assert focus_file(rig, OTHER).read_text() == "sleep\n"
    assert not focus_file(rig).exists(), "the caller's own session is untouched"
    assert mode_of(OTHER).mode == "sleep"
    assert len(sent) == 1
    got = call_of(sent[0])
    assert (got["session"], got["mode"], got["from_"]) == (OTHER, "sleep", who)


def test_for_a_session_this_host_does_not_have_is_refused(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan")
    before = snapshot(rig.home)
    result = pager_cmd("focus", "sleep", "--for", "no-such-session")
    assert result.exit_code == EX_REFUSED
    assert "no session" in result.stderr
    assert snapshot(rig.home) == before and not sent


@pytest.mark.parametrize("bad", ["..", "a/b", "/etc"])
def test_for_a_path_is_not_a_session(rig, sent, monkeypatch, bad):
    as_seat(monkeypatch, "ivan")
    result = pager_cmd("focus", "sleep", "--for", bad)
    assert result.exit_code == EX_REFUSED
    assert not sent


def test_for_and_fleet_name_two_targets(rig, sent, monkeypatch):
    as_seat(monkeypatch, "ivan")
    other_session(rig)
    result = pager_cmd("focus", "sleep", "--for", OTHER, "--fleet")
    assert result.exit_code == EX_REFUSED
    assert not focus_file(rig, OTHER).exists() and not rules.locked_path().exists()


# ── the MCP door: the verb is a tool, and an identity write ────────────────

def test_the_tool_writes_as_the_caller_over_stdio(rig, sent):
    tools = {n: fn for n, fn, _ in _mcp.tools(cli, "pager")}
    assert "pager_focus" in tools
    tools["pager_focus"](mode="sleep")
    assert focus_file(rig).read_text() == "sleep\n"


def test_the_tool_refuses_over_http_where_it_cannot_know_the_caller(rig, sent):
    from mcp.server.mcpserver.exceptions import ToolError
    tools = {n: fn for n, fn, _ in _mcp.tools(cli, "pager", transport="streamable-http")}
    with pytest.raises(ToolError, match="identity"):
        tools["pager_focus"](mode="sleep")
    assert not focus_file(rig).exists() and not sent
