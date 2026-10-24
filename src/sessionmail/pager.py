"""`pager` - the alerting command. Scaffolded from its authored help pages.

Pages: `help/pager.txt` and `help/pager/*.txt`.
Every flag below is on one of those pages, and every page's
prose is the docstring or the epilog here - with ONE exception, marked
where it lives: `listen --query` is hidden and exists only to refuse, so
that the refusal can name the environment variable to use instead.

All three verbs are built; this module has no stubs left.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
import subprocess
import sys
import time
import tomllib

import click

from . import __version__, _arm, _birth, _injectlog, _layer, _seat, _settings, _why
from ._help import CONTEXT_SETTINGS, OrderedGroup, PageCommand, unwrapped
# SESSIONMAIL_SUBSCRIBE and its birth-time
# reader moved to `_birth`, the ONE birth path (R5); re-exported here, where
# tests and callers have always reached them as `pager.*`. At land-prep the
# reader is master's task-3.4 `birth_subscriptions` (it replaced D4's
# `subscribe_at_birth`, which wrote), moved the same way.
from ._birth import SUBSCRIBE_VAR, birth_subscriptions  # noqa: F401

CAPABILITY = "pager"

#: The exit codes the authored pages document. MEASURED CONFLICT, reported:
#: click answers a bad flag with 2, which every page here spends on "could
#: not look". Left alone deliberately - it is one decision for all fifteen
#: verbs, not something `pager status` should settle on its own.
EX_PAGE = 0        # something would page you
EX_COULD_NOT_LOOK = 2
EX_NOTHING = 3
#: `pager status
#: --new/--stopped` before claude has started for this session exits 0 with
#: the not-yet line - INFORMATIONAL, not a page. A `session start` context
#: generator (a wake item) is exactly this run, and sessiontools frames every
#: non-zero generator exit as CONTEXT GENERATOR FAILED; no code caller of
#: `pager status` needs 3 here. `--quiet` pre-claude: exit 0 too.
EX_NOT_YET = 0

EX_TIMEOUT = 124   # `listen`, nothing fired before --timeout

#: `listen`: this session already has a
#: live listener, so a second one refuses rather than split its pages.
EX_SECOND_ARM = 3

#: `listen`: a hand `pager listen` on a seat whose pager monitor already holds the
#: session is not a fault - the monitor hears for it - so it exits calm.
#: ONE constant, so a change to another code is a one-line change. A hand-armed holder keeps EX_SECOND_ARM.
EX_MONITOR_HEARS = 0

#: `listen` (specs/pager "No children, and it watches itself"): the caller
#: went, so the listener returns rather than waiting alone. 0, as the wait's
#: CallerGone always returned: nobody is left to read it. Said on stderr.
EX_CALLER_GONE = 0

#: `pager off`/`stop` on the pager monitor: REFUSED (a monitor has mute and
#: unmute, not an off), one line naming
#: `pager mute` and `pager stop --kill`, nothing written. Non-zero because
#: the act asked for was not done; distinct from 3 (nothing listening) and 1
#: (usage). Before the refusal, `off` on a monitor meant mute.
EX_MONITOR_REFUSED = 4

#: `listen --monitor` with the monitor switched off:
#: it says ONE stdout line naming what switched it off, and exits 0.
#: It cannot be silent: Claude Code posts a notice for ANY monitor exit
#: (measured), so the line is what the seat reads beside it.
EX_MONITOR_OFF = 0

#: The pid an orphan is handed to: init on Linux, launchd on macOS.
ORPHAN_PARENT = 1

#: `listen --monitor`: a flag it cannot take with it.
EX_USAGE = 1

#: The least gap between two stdout lines of a monitor.
#: Claude Code 2.1.295 meters a plugin monitor's output with a token bucket
#: of 10, refilled 1 per 2 s, and DROPS a batch over it - for a pager, a
#: wake that never happens. 2.5 s apart can never drain it.
MONITOR_SPACING_VAR = "SESSIONMAIL_MONITOR_SPACING_SECONDS"
DEFAULT_MONITOR_SPACING = 2.5
#: The longest monitor line. Claude Code cuts a monitor's line
#: at 500 characters and appends "...(truncated)"; this stays under it.
MONITOR_LINE_MAX = 480
#: The signals that end a monitor quietly: claude ending the
#: task, `pager off`/`stop`, a terminal going away.
MONITOR_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")

#: How many times the listener recreates a vanished queue and links
#: one arrival again before it gives up LOUDLY (exit 2, could not look). A
#: scoop's window is two syscalls long, so one retry is the real case. The
#: bound exists so that a queue that keeps vanishing is reported instead of
#: spun on.
QUEUE_LINK_TRIES = 3

#: A ride-along line is read at a glance, beside real work.
LINE_WIDTH = 96

#: specs/pager, "A query rides the environment, never argv". The ancestor
#: is the retired bash listener's query variable; the package prefix is ours.
QUERY_VAR = "SESSIONMAIL_QUERY"

#: How often the listener asks. Small enough to feel immediate, large
#: enough that a whole day of waiting is cheap.
POLL_VAR = "SESSIONMAIL_POLL"
DEFAULT_POLL = 1.0

#: THE PAGE TEXT - one place, three shapes: generic, succinct, short, and
#: it makes sense to the agent. Like a good error message, it includes a
#: suggestion of what to do next - even if there has been no mail, even
#: for an agent that has not loaded any skill. Every line here is read
#: by an agent that may have loaded nothing, so each says what to do.
#:
#: HOW TO ARM IS NOT WRITTEN HERE. specs/pager, "The arm instruction has
#: one source": the words
#: live in `arm.txt` and every teller READS them. This line is that read -
#: change the file, not this module.
ARM_HOW = _arm.instruction()
#: The same, its trailing rider dropped: for a
#: line where arm.txt's monitor rider cannot apply (a monitor switched off).
ARM_HOW_NO_RIDER = _arm.without_rider()
PAGE_WAKE = f"pager: mail for you - below. Your next tool call carries the rest; then re-arm: {ARM_HOW}."
PAGE_NOTHING = f"pager: nothing arrived. Re-arm: {ARM_HOW}."

#: THE TWO HOOK LINES live in `hook_lines.toml` beside this file - one
#: place, read by both hooks, so the wording lives in one place.
#: "Not listening" is the same fact wherever the pager says it,
#: so the ride-along's nudge is that line too. The line COMPOSES the state
#: phrase with the arm instruction read from `arm.txt` ({arm}): two single
#: sources, one for
#: the state, one for how to arm; neither restates the other.
def _hook_texts() -> dict[str, str]:
    text = resources.files("sessionmail").joinpath("hook_lines.toml").read_text(encoding="utf-8")
    return {k: str(v) for k, v in tomllib.loads(text).items()}


def _hook_lines(texts: dict[str, str]) -> dict[str, str]:
    return {k: texts[k].format(arm=ARM_HOW, ways_out=texts["ways_out"])
            for k in ("not_listening", "listening", "listening_monitor", "off", "not_yet",
                      "no_monitor", "ways_out", "monitor_ended")}

_HOOK_TEXTS = _hook_texts()
HOOK_LINES = _hook_lines(_HOOK_TEXTS)
LINE_NOT_LISTENING = HOOK_LINES["not_listening"]
LINE_LISTENING = HOOK_LINES["listening"]
#: LINE_LISTENING when the live listener is the plugin's
#: monitor - names it, and never advises `pager stop` (nothing restarts it).
LINE_LISTENING_MONITOR = HOOK_LINES["listening_monitor"]
LINE_OFF = HOOK_LINES["off"]
#: LINE_OFF's place on a seat that is not required when the stamp is `pager stop
#: --kill`'s - the monitor armed and
#: was ended, so nothing is left to unmute. Names it and the two ways out.
LINE_MONITOR_ENDED = HOOK_LINES["monitor_ended"]
#: No listener runs for this session - the monitor cannot start mid-session; the two
#: ways out. `pager unmute` and
#: `pager mute` say it; so does `pager status`.
LINE_NO_MONITOR = HOOK_LINES["no_monitor"]
#: `pager unmute` on a live listener that was not muted - a no-op, said in one line.
LINE_NOT_MUTED = "not muted: already listening"
#: The DEAF lines, TEMPLATES - formatted by
#: `deaf_check` with {gate} or {why}; {arm} and {ways_out} are filled here.
#: Every one ends with the two ways out (LINE_WAYS_OUT, kept once).
LINE_WAYS_OUT = HOOK_LINES["ways_out"]


def _deaf_template(key: str) -> str:
    return _HOOK_TEXTS[key].replace("{ways_out}", LINE_WAYS_OUT).replace("{arm}", ARM_HOW)


DEAF_MONITOR_MISSING = _deaf_template("deaf_monitor_missing")
DEAF_MONITOR_ENDED = _deaf_template("deaf_monitor_ended")
DEAF_NOT_LISTENING = _deaf_template("deaf_not_listening")
#: What `pager status` says instead of "not listening" when claude
#: is not running for this session yet (`_seat.claude_for` is None) - a
#: check made before a monitor CAN exist is not deafness. Composed with
#: {arm}; it never asserts a monitor, so it holds on both paths.
LINE_NOT_YET = HOOK_LINES["not_yet"]
#: The settings file's two lines, TEMPLATES - formatted where they
#: are said: `.format(path=...)`, and `error=` for the fault.
SETTINGS_CREATED = _HOOK_TEXTS["settings_created"]
SETTINGS_FAULT = _HOOK_TEXTS["settings_fault"]
SETTINGS_STRANDED = _HOOK_TEXTS["settings_stranded"]
SETTINGS_COPIED = _HOOK_TEXTS["settings_copied"]
#: The SessionStart hook's line about the box - a TEMPLATE, formatted
#: with `box=` and `session=` where it is said (`hook start`).
BORN_LINE = _HOOK_TEXTS["born"]
#: session-settings 3.1: the old host file differs from the new one and was
#: not linked; `.format(old=..., path=...)`.
SETTINGS_DRIFT = _HOOK_TEXTS["settings_drift"]

#: THE QUIET PAGE - `pager listen`'s page, built by `quiet_page()` below.
#: specs/pager, "The pager is a nudge, and the re-arm carries": a page carries NO per-message
#: detail by default - no sender, no subject, no count of who it is from -
#: and four things in this order: (1) the pager is off now and the command
#: that arms it again, (2) how many are waiting, a count and never a
#: manifest, (3) the ride-along's state, (4) the commands to reach the mail.
#: PAGE_WAKE above is the OLD page and stays for `pager status --new`'s
#: scoop, which DELIVERS (the ride-along is the thing that carries).
PAGE_OFF = f"pager: off now - re-arm when you are ready: {ARM_HOW}."
#: Item 4 is not optional: "A page SHALL NOT leave the reader without a way
#: to reach the mail." Many ids in one call (specs/message 1.6).
#: The WAY is the re-arm: the messages ride along on it. The manual recipe
#: is the fallback and says so: a seat that re-arms AND runs `message list`
#: in one batch does the work twice, since the messages ride along on the
#: re-arm. Before this the recipe was offered unconditionally
#: right after item 3 said "wait rather than act", and every fresh seat read
#: it as the next step (changes/page-says-ride-along).
#: Composed from ARM_HOW, never restated: ONE source, so the Stop hook and
#: every page move together.
PAGE_COMMANDS = (f"pager: {ARM_HOW}; the messages above ride along on that call "
                 "as [mail] lines. Only if they do not: `message list --unread`, "
                 "`message read ID...`, `message mark ID... done --reason WHY` - "
                 "many ids in one call.")
#: The ride-along's nudge is the SAME line the Stop hook says (one fact, one
#: wording).
PAGE_NOT_ARMED = LINE_NOT_LISTENING

#: THE SWITCH - what brings the listing form back (tasks.md 3.4).
#: specs/pager, "The pager is a nudge, and the re-arm carries": "A page MAY
#: carry one line per entry - box, sender, subject, never a body - when a
#: switch is set. The switch SHALL default off." The switch is BOTH an
#: environment variable in the `SESSIONMAIL_` family AND a flag on
#: `pager listen`, and WHERE BOTH ARE GIVEN THE FLAG WINS.
#:
#: The switch ADDS the lines; it does not take the page away. Items 1-4 are
#: SHALL ("A page SHALL NOT leave the reader without a way to reach the
#: mail. Item 4 is not optional"), so a listed page is the quiet page with
#: one line per entry under it - never the old PAGE_WAKE page back.
PAGE_LIST_VAR = "SESSIONMAIL_PAGE_LIST"
#: Set to one of these, the variable is ON. Anything else - unset, empty, a
#: typo - is OFF, because the switch defaults off and a misspelling must not
#: quietly put senders and subjects on a page. (The mirror of
#: `_seat.pager_enabled`, which defaults ON and so lists the OFF words.)
PAGE_LIST_ON = ("on", "1", "yes", "true")


def page_list_on(flag: bool | None = None, env: dict[str, str] | None = None) -> bool:
    """Does this page carry one line per entry? Default off; the FLAG WINS.

    `flag` is the tri-state `--list/--no-list`: None when neither was
    given (fall through to the variable), True or False when one was -
    and then the variable is not consulted at all, in either direction.
    """
    if flag is not None:
        return flag
    env = os.environ if env is None else env
    return (env.get(PAGE_LIST_VAR) or "").strip().lower() in PAGE_LIST_ON


#: THE PAGE AGE - T in "page only when the oldest queue entry is older
#: than T". If the agent is stopped, it pages immediately; if it is
#: running, it waits about a minute. So T is not one number: the IDLE verdict
#: selects it (changes/stopped-means-idle: `session get idle`, never the
#: Stop hook - a turn boundary was never "stopped"). Idle -> 0 (no tool
#: call is coming to carry the entry). Else -> this setting, PROVISIONAL at
#: 60 s (one setting, not a constant).
PAGE_AGE_VAR = "SESSIONMAIL_PAGE_AGE_SECONDS"
DEFAULT_PAGE_AGE = 60.0


def _running_page_age() -> float:
    raw = (os.environ.get(PAGE_AGE_VAR) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_PAGE_AGE
    except ValueError:
        return DEFAULT_PAGE_AGE


def page_age(seat: "_seat.Seat", session: str) -> float:
    """T for this wake: 0 when the session is IDLE - not busy for
    SESSIONMAIL_IDLE_SECONDS by Claude Code's own status, read through
    `session get idle` (changes/stopped-means-idle) - else the running
    threshold. Unknown is not idle. Re-read
    at every scan by the age watchdog (a callable handed to _detect), so
    going idle mid-wait is seen inside one poll (the verdict is cached
    IDLE_POLL_SECONDS). `idle with children` (turn over, a background agent
    still running, as sessiontools reports it) is idle here, since the turn
    end: the library's number already says so (changes/idle-with-children)."""
    if _seat.is_idle(session) is True:
        return 0.0
    return _running_page_age()


#: WHEN NOTHING CAN SAY BUSY. Idleness is Claude Code's own word, read
#: from its sidecar (`~/.claude/sessions/<pid>.json`) and, failing that,
#: from `session get idle`. A client that is not Claude Code has neither,
#: so the verdict is unknown for the life of the session and T stays at
#: the running page age: mail is not lost, it is up to a minute late. The
#: cure is a setting, not a code change - and it belongs in the banner,
#: where a stranger meets the problem, not in a README they have already
#: skipped. (Measured: unknown stays NOT idle - changes/stopped-means-idle
#: item 5 is untouched.)
BLIND_HINT = f"not Claude Code? set {PAGE_AGE_VAR}=0 to page at once"


#: The rule named wherever the idle verdict is, so a page at T=0 on a seat whose sidecar
#: still says busy explains itself.
CHILDREN_RULE = "idle with children: turn ended, children running"


def _children_rule(session: str) -> str:
    """` (idle with children: ...)` when SESSION reads `idle with children`,
    else "" - so every other state's text is today's, byte for byte."""
    return (f" ({CHILDREN_RULE})"
            if _seat.seat_state(session) == _seat.IDLE_WITH_CHILDREN else "")


def idle_state(session: str) -> str:
    """One phrase for the banner: what the verdict is and why."""
    secs = _seat.idle_seconds(session)
    t = _seat.idle_threshold()
    if secs is None:
        return f"unknown (session get idle {session[:8]} said nothing) -> {_running_page_age():g}s"
    if secs >= t:
        return f"idle {secs:g}s >= {t:g}s -> at once{_children_rule(session)}"
    if secs == 0:
        return f"busy -> {_running_page_age():g}s"
    return f"not busy {secs:g}s < {t:g}s -> {_running_page_age():g}s{_children_rule(session)}"




def _poll_interval() -> float:
    try:
        return max(0.01, float(os.environ.get(POLL_VAR) or DEFAULT_POLL))
    except ValueError:
        return DEFAULT_POLL


def _diverged() -> list[str]:
    """specs/pager, "A version mismatch is alarmed, never a refusal".

    Another reachable copy of `pager` at a different version means the
    fleet is running two different listeners and nothing else ever says
    which. It is said loudly and the listener arms anyway - a listener
    that refused would turn documentation drift into fleet-wide silence.
    """
    seen: dict[str, str] = {}
    for directory in (os.environ.get("PATH") or "").split(os.pathsep):
        candidate = os.path.join(directory, "pager")
        if not (directory and os.path.isfile(candidate)):
            continue
        real = os.path.realpath(candidate)
        if real in seen:
            continue
        try:
            done = subprocess.run([candidate, "--version"],
                                  capture_output=True, text=True, timeout=5)
            seen[real] = (done.stdout or "").strip() or "unreadable"
        except (OSError, subprocess.TimeoutExpired):
            seen[real] = "unreadable"
    if len(set(seen.values())) <= 1:
        return []
    lines = ["another reachable copy of `pager` reports a different "
             "version - the box is running two listeners and nothing else "
             "says which. Arming anyway."]
    lines += [f"    {version}   {path}" for path, version in sorted(seen.items())]
    return lines

class PagerGroup(OrderedGroup):
    """`pager on` is not a verb: it exists only to refuse and point at
    `listen` (a `pager on` that returns at once would read as broken).
    A refusal in the resolver, so
    the verb count the scaffold gate pins is untouched.

    The refusal is a teller too, so it READS the arm instruction (ARM_HOW,
    out of `arm.txt`) instead of writing its own copy - specs/pager, "The
    arm instruction has one source"."""

    def get_command(self, ctx: click.Context, name: str):
        if name == "on":
            raise click.UsageError(
                "there is no `pager on` - the pager is a call that blocks and "
                f"returns. To arm it: {ARM_HOW}; `pager off` stops it.")
        return super().get_command(ctx, name)


#: message-subscriptions 4.3: this page used to say listen "reads every file
#: in <seat>/subscriptions/ on every run" - false now, and the files are
#: retired (4.2). What reaches the
#: box is postfix's job, set by `message subscription add`; the pager only
#: watches the one box. "124 timeout" went with it: --timeout is hidden
#: and the page no longer names it.
GROUP_EPILOG = unwrapped(
    """It takes no argument about WHAT to hear: `pager listen` watches
this session's box. What else reaches that box is a subscription
(`message subscription add BOX`), delivered by postfix; the pager
reads no subscription file. Alerts are mail with X-Alert-* headers
from the host's runner, one thread per alert. The pager never
writes to the maildir and never claims a message.

Exit: 0 ok · 1 usage · 2 could not look"""
)


@click.group(
    cls=PagerGroup,
    context_settings=CONTEXT_SETTINGS,
    epilog=GROUP_EPILOG,
    options_metavar="",
    subcommand_metavar="<verb> [args]",
)
@click.version_option(__version__, "--version", "-V")
def cli() -> None:
    """pager - alerts an agent: a line at the desk, a wake when idle; it blocks"""


@cli.command(
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="[--all] [--quiet]",
    short_help="what would page you now, one line each; never blocks",
    epilog=unwrapped(
        """Reads this session's box (the one `pager listen` watches) and
prints the 3 oldest not-done messages, one line each, tagged with the
address that delivered it, then "+N more". No index, no subscription
file. Nothing is written, no message is claimed.

--new is the scoop: takes THIS session's queue (<session>/ride/new/)
by renaming the directory - exactly once, no query, no index - prints one
line per entry from its headers, marks each `seen` (ride-along), files the
link in done/. A leftover taking.* is given first; a message marked done is
filed unshown. Empty queue: what this session was given and nobody marked
done rides as ONE depth line per SESSIONMAIL_REMIND_MINUTES (60), never the
item again. --stopped (the Stop hook): stamps the session stopped, marks
nothing. Both lead with "NOT ARMED" when no `pager listen` is live for this
session (opt out: SESSIONMAIL_PAGER=off).

Exit: 0 something would page, or claude not up yet (--new/--stopped) ·
1 usage · 2 could not look (no box yet, or unreadable - never reported as
quiet) · 3 nothing: a quiet line"""
    ),
)
@click.option("--all", "show_all", is_flag=True,
              help="every not-done message in the box, not just the 3 oldest")
@click.option("--quiet", is_flag=True, help="exit code only")
@click.option("--new", "new_only", is_flag=True,
              help="the scoop: what this session has not been given yet; marks seen")
@click.option("--stopped", "stopped", is_flag=True,
              help="from the Stop hook: stamps this session stopped; marks nothing")
def status(show_all: bool, quiet: bool, new_only: bool, stopped: bool) -> None:
    """pager status - what would page you now, one line each; never blocks"""
    from . import _maildir
    ctx = click.get_current_context()

    def could_not_look(why: str) -> None:
        """Exit 2. Printed even under --quiet: the page says a could-not-look
        is "never reported as quiet", because silent deafness is the one
        failure this whole capability exists to prevent."""
        click.echo(f"pager status: could not look: {why}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)

    try:
        seat = _seat.resolve()
    except _seat.NoIdentity as exc:
        could_not_look(str(exc))
        return

    session = _seat.session_id()
    try:
        # The scoop reads the queue and the marks and needs nothing else
        # (the-queue-chain). The marks are the SESSION's;
        # the seat is still passed for the run that has no
        # session id at all. message-subscriptions 4.2: no subscription file
        # is read here any more, and none gates the run - the born files
        # retired, so their absence no longer means "a seat not born yet".
        marks = _seat.read_marks(seat, session)
        events = _seat.read_mark_events(seat, session) if new_only else []
    except (_seat.Malformed, OSError) as exc:
        could_not_look(str(exc))
        return

    now = datetime.now().astimezone()
    given: set[str] = set()
    # "Arm your pager" from the Stop hook AND as a
    # ride-along. Both are this one check: is this session's listener
    # alive? Silent when the pager is opted out (#5), or when armed.
    # ONE `ps` for both questions below, asked only if one needs it.
    table: list = []

    def ps():
        if not table:
            table.append(_seat._ps_table())
        return table[0]

    nudge = ((new_only or stopped) and _seat.pager_enabled()
             and _seat.is_listening(seat, session, ps=ps) is None)
    # "not listening" before claude has started for this session is
    # not deafness - a `session start` context generator (a wake item) runs
    # before claude, so nothing CAN be listening, and "arm now" there invited
    # a hand-armed listen that fought the plugin's monitor. Asked ONLY where
    # the nudge would fire, so a listening session never pays for it.
    not_yet = nudge and _seat.claude_for(session, ps=ps) is None
    if not_yet:
        nudge = False
    if stopped:
        # The Stop hook's form: the turn ended. It stamps
        # NOTHING (changes/stopped-means-idle: a turn boundary is not idle;
        # idle is `session get idle`) and marks NOTHING - a Stop hook's stdout
        # reaches nobody unless the hook BLOCKS with it, so the only line
        # worth printing here is the nudge: a stop hook that reliably and
        # succinctly tells the agent to arm the pager if it is not armed. It
        # never listens itself - one line and exit - so the stop hook and
        # the listener never fight for a seat.
        # The listener's deferred set is the wake.
        if nudge:
            click.echo(PAGE_NOT_ARMED)
            ctx.exit(EX_PAGE)
        if not_yet:
            if not quiet:
                click.echo(LINE_NOT_YET)
            ctx.exit(EX_NOT_YET)         # informational, never a FAILED generator
        ctx.exit(EX_NOTHING)
    if new_only:
        # A ride-along ran, so a tool call just ran - this
        # session is busy. The stamp is not what status measures.
        _seat.touch(seat, _seat.RIDE_ALONG_STAMP, session)
        # the-queue-chain (G2): the scoop takes this session's queue by
        # directory swap. No notmuch query, no index-revision baseline, no
        # remind-window arithmetic decides what to give: the queue holds
        # only what has not been given, by construction. F18's duplicate
        # (a claim elsewhere bumping lastmod) cannot recur here.
        _scoop(ctx, seat, session, marks, events, now, nudge=nudge, quiet=quiet,
               layers=_layers_or_none(seat, session), not_yet=not_yet)
        return

    # message-subscriptions 4.2: the resident view reads no subscription
    # file and asks no index. It reads THIS SESSION'S BOX - the one `pager
    # listen` watches - because that is where everything the retired files
    # named now lands (specs/pager, REMOVED "A subscription belongs to the
    # seat", Migration: the born `seat` file is redundant because the seat's
    # alias row delivers its address here; the born `session` file because
    # the listener watches this box; a hand-written one becomes `message
    # subscription add`, which routes into this box too).
    if session in ("", "-"):
        could_not_look(f"no session id ({' or '.join(_seat.SESSION_VARS)}) - "
                       "the session id is the box, so there is no box to read")
        return
    box_path = _maildir.root() / session
    if not ((box_path / "new").is_dir() and (box_path / "cur").is_dir()):
        # A ride-along never creates what it measures: the box is born by
        # this session's first `pager listen` (F19), never here.
        # Exit 2 stays (the bare-seat scenario at specs/pager: "pager status
        # on that bare seat refuses and names `pager listen`"): before claude has started for
        # this session the REASON is the not-yet line, which still names
        # `pager listen` and never reads as deaf.
        if _seat.pager_enabled() and _seat.claude_for(session) is None:
            could_not_look(LINE_NOT_YET)
            return
        if not quiet:
            # No box yet is where a switched-off monitor shows first - so the
            # `monitor:` line says why (and the `required:` line, and the
            # no-monitor sentence, beside it).
            for reading in status_readings(seat, session, ps=ps):
                click.echo(reading, err=True)
        could_not_look(f"no box at {box_path} yet - this session's first "
                       "`pager listen` births it")
        return
    try:
        entries = _maildir.enumerate_box(str(box_path))
    except (OSError, _maildir.NoSuchBox) as exc:
        could_not_look(f"box {box_path}: {exc}")
        return
    # `help/message/mark.txt`: "The pager wakes for not-seen and rides
    # along with not-done; a `done` is what quiets a line."
    live = [e for e in entries if "done" not in marks.get(e.message_id, set())]
    shown = live if show_all else live[:RESIDENT_LIMIT]
    lines: list[str] = [_box_line(e) for e in shown]
    if len(live) > len(shown):
        lines.append(f"[{RIDE_TAG}] +{len(live) - len(shown)} more - "
                     "`pager status --all` shows them")

    # D7 (tasks 4.3): what the next arrival would do, the last line either
    # way. It is a reading of the rules, not a message: it goes to STDERR,
    # beside the listener's banner, so stdout stays one line per message
    # (the resident view's contract), and it never changes the exit code.
    try:
        next_line = next_arrival_line(seat, session, now)
    except Exception as exc:  # noqa: BLE001 - the rules could not be read: say so
        next_line = f"pager: the next arrival cannot be judged now - {exc!r}"

    # The monitor's state and its switch.
    # A reading, like D7's line: STDERR, so stdout stays one line per
    # message, and it never changes the exit code.
    readings = status_readings(seat, session, ps=ps) if not quiet else []
    # A mail-drop host with no tailnet: said once a run, as INFO, in place
    # of the suffix warning `host_forms` keeps for every other host.
    from . import _magicdns
    if not quiet and _magicdns.drop_without_tailnet():
        readings.append(f"suffix: none - {_magicdns.NO_TAILNET}")

    if not lines:
        if not quiet:
            click.echo(f"nothing (box {session}, all quiet)")
            for reading in readings:
                click.echo(reading, err=True)
            click.echo(next_line, err=True)
        ctx.exit(EX_NOTHING)

    if not quiet:
        for line in lines:
            click.echo(line)
        for reading in readings:
            click.echo(reading, err=True)
        click.echo(next_line, err=True)
    ctx.exit(EX_PAGE)


def monitor_status_line(seat, session: str, ps=None,
                        switch: "_seat.MonitorSwitch | None" = None) -> str:
    """`pager status`'s `monitor:` line:
    armed (pid), not armed, or off - then the switch and the source that
    decided it, then every setting that was there and was ignored. The
    switch is read at launch, so a monitor that runs while the switch now
    says off is named as armed, and the line says when the change lands."""
    switch = _seat.monitor_switch() if switch is None else switch
    live = (_seat.is_listening(seat, session, ps=ps)
            if session not in ("", "-") else None)
    holder = None if live is None else _seat.is_monitor(live, ps=ps)
    if live is not None and holder:
        state = f"armed (pid {live})"
        if _seat.stopped_on_purpose(seat, session):
            state += ", muted"
    else:
        state = "off" if not switch.on else "not armed"
        if live is not None and holder is False:
            state += f" (a hand `pager listen`, pid {live}, hears for this session)"
        elif live is not None:
            state += f" (pid {live} listens; `ps` cannot say whether it is the monitor)"
    line = f"monitor: {state}; switch: {'on' if switch.on else 'off'} ({switch.source})"
    if live is not None and holder and not switch.on:
        line += " - read at launch, so it takes effect when the seat next starts"
    for why in switch.ignored:
        line += f"; {why}"
    return line


def required_status_line(seat, session: str,
                         required: "_seat.Required | None" = None) -> str:
    """`pager status`'s `required:` line:
    yes or no, the source that decided it, a contradiction with
    SESSIONMAIL_PAGER=off named (the pager off beats required), and every
    setting that was there and was ignored."""
    required = _seat.pager_required(seat, session) if required is None else required
    line = f"required: {'yes' if required.on else 'no'} ({required.source})"
    if required.on and not _seat.pager_enabled():
        line += (f" - but {_seat.PAGER_VAR}={(os.environ.get(_seat.PAGER_VAR) or '').strip()} "
                 "turns the pager off, which beats it: the deaf check says nothing")
    elif required.on:
        line += " - the Stop and PostToolBatch hooks say DEAF when nothing listens"
    for why in required.ignored:
        line += f"; {why}"
    return line


def today_status_line(seat, session: str, today: "str | None" = None) -> "str | None":
    """`pager status`'s `today:`
    line - this session's injections today, their estimated tokens, the
    ride-along's share and its split into mail and heard, whether capture
    is on, and the log's path. None with no session. Reads only; never
    raises (a reading never costs `pager status` its exit code)."""
    if session in ("", "-"):
        return None
    try:
        d = _injectlog.log_dir(session)
        t = _injectlog.today_totals(session, today=today)
        capture = "on" if _injectlog.capture_on(seat, session) else "off"
    except Exception as exc:  # noqa: BLE001 - see above
        return f"today: the inject log could not be read - {exc!r}"
    where = d / _injectlog.FILE if d is not None else "(no session dir)"
    if not t["injections"]:
        return f"today: nothing injected yet; capture {capture} - {where}"
    n = t["injections"]
    return (f"today: {n} injection{'' if n == 1 else 's'}, ≈{t['tokens']:,} tok "
            f"(rides ≈{t['rides']:,}: mail ≈{t['mail']:,}, heard ≈{t['heard']:,}); "
            f"capture {capture} - {where}")


def status_readings(seat, session: str, ps=None) -> list[str]:
    """What `pager status` says about the listener, on STDERR: the
    `monitor:` line, the `required:` line, the
    `today:` line (this session's inject log, summed), then - when
    nothing at all listens for this session and the pager is on - the (l5)
    sentence: the monitor can only start at launch, and the two ways out."""
    out = [monitor_status_line(seat, session, ps=ps), required_status_line(seat, session)]
    today = today_status_line(seat, session)
    if today:
        out.append(today)
    if (_seat.pager_enabled() and session not in ("", "-")
            and _seat.is_listening(seat, session, ps=ps) is None):
        out.append(f"pager: {LINE_NO_MONITOR}")
    return out


def _layers_or_none(seat, session: str):
    """The layers for a scoop, or None when the rules cannot be read (a bug
    in the reader never costs the ride-along)."""
    from . import rules
    try:
        return rules.load_layers(seat, session)
    except Exception:  # noqa: BLE001 - see above
        return None


#: The ride-along's tag for a queue entry. The queue is the session box's
#: arrivals - seat copies included - so the honest word is the plain one.
RIDE_TAG = "mail"

#: The ride-along's tag for a trusted SPOKEN turn, the first word of
#: its `[heard HH:MM:SS <device> <speaker>]` line (`_queue_line`).
HEARD_TAG = "heard"
#: The word an UNVERIFIED voice mail's heard line carries after the tag.
UNVERIFIED_TAG = "UNVERIFIED"


def _scoop(ctx: click.Context, seat: "_seat.Seat", session: str,
           marks: dict[str, set[str]], events: list, now: datetime,
           *, nudge: bool, quiet: bool, emit=None, layers=None,
           not_yet: bool = False, room: "int | None" = None,
           give_by: "float | None" = None,
           ride: "_injectlog.Ride | None" = None) -> list[str]:
    """`pager status --new` after the-queue-chain: take the queue, give
    each entry once, record the giving, file the link in done/. Returns
    the lines. `emit(lines)` is how they reach the world - called BEFORE
    the giving is recorded (see below); the default echoes them, the
    batch hook wraps them as hook JSON. Exits only for `status`: with
    `emit` given, the caller owns the exit (a hook's exit code is a
    verdict on the hook, so it exits 0 whatever it found).

    LAYERS (a `rules.Layers`, or None when the rules could not be read)
    judges each entry at its arrival, as the listener did, so each line
    carries the action that applied (D7, tasks 4.3). None: no action is
    shown, and every line is still given.

    NOT_YET: claude is not running for this session yet; the
    not-yet line leads, and the exit is EX_NOT_YET (0), mail or none.

    ROOM: the characters this scoop's lines may take in the
    hook's one `additionalContext` string, after the caller's own lines
    (None: unbounded, as `pager status` prints to a terminal). Claude Code
    hands the model only a 2,000-character preview of a string over its
    10,000 cap, so an entry whose lines would cross ROOM is NOT given: its
    link goes back to new/ with no mark, and every entry after it with it,
    in order (nothing is packed in behind it). One line says how many are
    held. A trusted voice turn too long for ANY hook (N13: never cut) is
    not given either: its header line and a `message read` pointer say it,
    unmarked, and its link is filed.

    GIVE_BY: a `time.monotonic()` deadline. Past it, before
    the queue is taken, the scoop takes nothing, says nothing and writes
    nothing: a hook cancelled at its timeout discards its output, so mail
    given that late can be lost. The next batch gives it.

    RIDE: when given, the scoop fills it BEFORE it
    emits - the items behind its lines in emit order, and its counts - so
    the hook's one `emit` can log the row on the exact string it printed.
    Filling it writes nothing and changes no line."""
    import copy
    from . import _maildir
    owned = emit is not None
    try:
        me = listener_me(seat, session) if layers is not None else None
    except Exception:  # noqa: BLE001 - no $me: no actions, every line still given
        layers = me = None

    # 5.3: a line carries the verdict
    # that APPLIED - the one the listener recorded when it judged the mail -
    # never a re-judge by the rules as they are now. Only a mail no one has
    # judged yet (no listener ran) is judged here, and that verdict is
    # recorded in its turn, so `pager why` can answer for it too.
    try:
        recorded = latest_verdicts(seat, session)
    except Exception:  # noqa: BLE001 - no record read: judge as before
        recorded = {}

    def action_of(entry, mid: str) -> str:
        if mid and mid in recorded:
            return action_text(verdict_of(recorded[mid]))
        if layers is None:
            return ""
        verdict = arrival_verdict(entry.path, layers, me, session=session)
        record_verdict(seat, session, mid, verdict, RIDE_BY, layers=layers)
        return action_text(verdict)
    if emit is None:
        def emit(lines: list[str]) -> None:
            if not quiet:
                for line in lines:
                    click.echo(line)
    if give_by is not None and time.monotonic() > give_by:
        if ride is not None:
            ride.cap("give_by")
        return []                        # F2: too near the hook's deadline to give
    try:
        taken = _seat.take_queue(seat, session, os.getpid())
    except OSError as exc:
        click.echo(f"pager status: could not look: queue: {exc}", err=True)
        if owned:
            return []
        ctx.exit(EX_COULD_NOT_LOOK)
        return []

    lines: list[str] = []
    #: The items behind LINES, in emit order, for RIDE; and whether
    #: a text view given was cut at its cap (`capped: text`).
    items: list[dict] = []
    text_capped = False
    # Each given entry, with the marks its view earned beyond `seen` (D5,
    # 3.4): `read` for a mail given whole (HELD until the mail guard is
    # deployed: `READ_ON_WHOLE`), `done` for a voice turn given whole, nothing for a
    # cut view, a header line or a coalesced count.
    given: list[tuple["_seat.QueueEntry", str, tuple[tuple[str, str], ...]]] = []
    for left in sorted({e.recovered for e in taken.entries if e.recovered}):
        count = sum(1 for e in taken.entries if e.recovered == left)
        lines.append(f"[{RIDE_TAG}] recovered {count} from {left} - a scoop that did not finish")
    # What this scoop's lines have used of ROOM, what goes back
    # to new/ un-given, the voice turns said by pointer only, and the
    # entries filed with no line at all (F4c, named on the next banner).
    used = _cost(lines)
    whole_room = None if room is None else room - SCOOP_TAIL_RESERVE - used
    held: list["_seat.QueueEntry"] = []
    unlined: list[tuple["_seat.QueueEntry", object, str]] = []
    # What is actually to be shown, parsed once. The GIVING is unchanged by
    # the grouping below: every one of these is marked seen and filed,
    # whether it got a line of its own or was counted into one. Coalescing
    # is a way of SAYING, never a way of skipping.
    shown: list = []
    for e in taken.entries:
        try:
            parsed = _maildir.parse(e.path, 0, True)
            mid = parsed.message_id
        except Exception:
            parsed, mid = None, ""
        if mid and "done" in marks.get(mid, set()):
            if not marks[mid] & {"seen", "read"}:
                unlined.append((e, parsed, mid))     # F4c: never shown to this session
            continue                     # terminal for the seat: filed, not shown
        shown.append((e, parsed, mid, action_of(e, mid)))

    # 3.3: supersede, mirroring `message list` (which hides
    # every message another one in the box supersedes). When one ride-along
    # would give both, it gives ONLY the superseding one; the superseded one
    # is not given, and is marked `seen`, reason `superseded`, below, after
    # the printing like every other mark. Taken out BEFORE the grouping, so
    # it is neither printed nor counted into a burst. Queue order does not
    # matter: a correction queued ahead of what it corrects still wins. A
    # superseding message is given on its own terms, as its own view: that
    # its target was (or was not) given earlier changes nothing about it.
    replaced = {p.supersedes for _e, p, _m, _a in shown if p is not None and p.supersedes}
    superseded = [(e, mid) for e, _p, mid, _a in shown if mid and mid in replaced]
    if superseded:
        shown = [s for s in shown if not (s[2] and s[2] in replaced)]

    # specs/pager: "Counts SHALL coalesce per address across a burst, not be
    # emitted per message." A bridge writes a seat every chunk of a call -
    # 14 in five minutes, measured on one seat's box - and one line each turns a
    # restored delivery into a storm. Above the threshold an address gets ONE
    # line saying how many are for it; below it, nothing changes and the
    # sender and subject still print.
    #
    # NO sender filter and NO subject filter is used to decide this - only
    # how many arrived for one address. A filter would fix the one bridge
    # that bit us and leave every other one armed.
    threshold = group_after()
    # The boxes a cut line's row is found in, resolved at most once and only
    # when a line needs a pointer: this runs on every tool batch, and
    # resolving a box that is not born yet reads the alias map (~15 ms,
    # measured), which an empty queue or a whole view never needs.
    boxes: list[tuple[str, tuple[str, ...]]] = []

    def read_boxes() -> tuple[str, tuple[str, ...]]:
        if not boxes:
            boxes.append(_read_boxes(seat, session))
        return boxes[0]

    # The batch budget (3.2c), made only when there is something to give:
    # `_views` pulls in the parsers (~5 ms, measured), and an empty
    # queue - most tool batches - never needs them.
    budget = None
    if shown:
        from . import _views
        budget = _views.Budget()
    burst: dict[str, int] = {}
    actions: dict[str, dict[str, int]] = {}
    for _e, parsed, _mid, action in shown:
        key = _maildir.original_to_key(parsed.original_to if parsed else "")
        burst[key] = burst.get(key, 0) + 1
        if action:
            tally = actions.setdefault(key, {})
            tally[action] = tally.get(action, 0) + 1
    coalesced = {k for k, n in burst.items() if n >= threshold}
    emitted: set[str] = set()
    for e, parsed, mid, action in shown:
        key = _maildir.original_to_key(parsed.original_to if parsed else "")
        if key in coalesced:
            if key in emitted:
                # Counted into a line already out, ahead of anything held:
                # said, so given, held or not.
                given.append((e, mid, ()))
                items.append(_injectlog.item(mid, kind="mail", view="count", given=0,
                                             in_mail=None, cut=True))
                continue
            if held:
                held.append(e)
                continue
            # One line, at the position of the burst's FIRST message, so
            # the order of everything else is untouched. It names its
            # door: a count with no way to reach the mail teaches a
            # reader to skim the slot.
            who = key or "(no X-Original-To)"
            tally = ", ".join(f"{a} {n}" for a, n in actions.get(key, {}).items())
            line = (f"[{RIDE_TAG}] {burst[key]:,} for {who}"
                    f" - `message list` to see them" + (f" ({tally})" if tally else ""))
            if room is not None and used + _cost([line]) > room - SCOOP_TAIL_RESERVE:
                held.append(e)           # F1: the burst's line does not fit
                continue
            emitted.add(key)
            used += _cost([line])
            lines.append(line)
            given.append((e, mid, ()))
            # The count line is the burst's FIRST mail's in the
            # emitted string; the rest are counted into it (given 0). No
            # view was made, so the body's size is not known: in_mail null.
            items.append(_injectlog.item(mid, kind="mail", view="count", given=len(line),
                                         in_mail=None, cut=True))
        else:
            # D4 (3.2a, 3.2b): the entry is given as a VIEW, by kind - the
            # header line, then the text view (mail) or the whole view
            # (trusted voice). The batch budget (3.2c) decides whether the
            # body follows: a mail past it gets the HEADER view (the header
            # line with its additions, no body) and `seen` only; a trusted
            # voice turn is always given whole (N13), and spends it.
            # UNVERIFIED voice is a mail here: capped, budgeted,
            # never `done`.
            if held:
                held.append(e)           # F1: in order - nothing packed in behind
                continue
            v = _entry_view(e, parsed, mid, read_boxes)
            head = _queue_line(e, parsed, v.extras if v else (), action=action,
                               heard=v.heard if v else None)
            before = copy.copy(budget)   # F1: an entry held must not spend it
            entry_lines = [head]
            earned: tuple[tuple[str, str], ...] = ()
            admitted = False
            if v is None:
                if budget.closed:
                    budget.skip()
            elif budget.admit(v):
                entry_lines.extend(v.body)
                earned = _earned(v)
                admitted = True
            cost = _cost(entry_lines)
            if room is not None and used + cost > room - SCOOP_TAIL_RESERVE:
                budget = before
                if v is not None and v.voice and cost > whole_room:
                    # A turn is never cut, and no hook can carry this
                    # one whole. Said by pointer, NOT given (no mark); its
                    # link is filed, so it is never retaken or re-said.
                    say = [head, _voice_pointer_line(
                        v.chars, _views.pointer(e.name, mid, *read_boxes()))]
                    if used + _cost(say) <= room - SCOOP_TAIL_RESERVE:
                        used += _cost(say)
                        lines.extend(say)
                        # Said by pointer, not given: its header is still
                        # text in the string, so it is an item, view header.
                        items.append(_injectlog.item(mid, kind="heard", view="header",
                                                     given=len("\n".join(say)),
                                                     in_mail=v.chars + v.cut, cut=True))
                        continue
                held.append(e)
                continue
            used += cost
            lines.extend(entry_lines)
            given.append((e, mid, earned))
            items.append(_ride_item(mid, v, entry_lines, admitted))
            if admitted and v is not None and not v.voice and v.cut:
                text_capped = True       # a mail's text view stops at TEXT_CAP

    over = budget.line(RIDE_TAG) if budget is not None else None
    if over:
        lines.append(over)
    if held:
        lines.append(HELD_LINE.format(n=len(held)))

    if not given:
        # "Read is not terminal", same session, after an interval: a depth
        # line once per interval, never the item again (Q05, an owner's switch).
        interval = _seat.remind_minutes() * 60
        count, oldest = _seat.overdue(events, marks, session,
                                      now - timedelta(seconds=interval))
        if count and oldest is not None and _seat.reminder_due(seat, session, interval):
            # The line names the box(es) it counts, so a seat
            # can tell its own backlog from a subscribed box's. Read from
            # the ride's done/ HERE, once the line is due, never on every
            # empty scoop.
            boxes = _seat.overdue_boxes(seat, session, _seat.overdue_ids(
                events, marks, session, now - timedelta(seconds=interval)))
            lines.append(not_done_line(count, _age(now - oldest), boxes, session))
            _seat.touch(seat, _seat.REMINDED_STAMP, session)

    if nudge:
        # STATE, not an event: rendered on every scoop
        # until the listener is armed.
        lines.insert(0, PAGE_NOT_ARMED)
    if not_yet:
        # Informational, not a page - and exit 0 even with an empty
        # queue (EX_NOT_YET: a wake item's non-zero reads as FAILED).
        lines.insert(0, LINE_NOT_YET)
    if ride is not None:
        # Handed UP before the emit, so the hook's
        # row is written on the string it printed. Fills, never writes.
        ride.items = items
        ride.superseded = len(superseded)
        ride.held = len(held)
        ride.past_budget = budget.past if budget is not None else 0
        if held:
            ride.cap("room")
        if ride.past_budget:
            ride.cap("batch")
        if text_capped:
            ride.cap("text")
    emit(lines)
    # The giving is recorded AFTER the printing, and the links filed after
    # the record: a scoop that dies between leaves a taking.* that the
    # next scoop recovers - shown twice, never dropped.
    stamp = now.isoformat(timespec="seconds")
    for e, mid, earned in given:
        if mid:
            reason = "ride-along" + (f" recovered from {e.recovered}" if e.recovered else "")
            _seat.write_mark(seat, mid, "seen", session, stamp, reason)
            for mark, why in earned:
                _seat.write_mark(seat, mid, mark, session, stamp, why)
    for e, mid in superseded:
        _seat.write_mark(seat, mid, "seen", session, stamp, SUPERSEDED_REASON)
    if unlined:
        _record_unlined(seat, session, stamp, unlined)
    if held:
        # Back to new/, un-given and unmarked; a rename keeps the
        # inode's mtime (delivery time), so the order of new/ is unchanged.
        back = _seat.queue_new(seat, session)
        for e in held:
            try:
                os.rename(e.path, back / e.name)
            except FileNotFoundError:
                pass
        keep = {e.name for e in held}
        taken = _seat.Taken(taken.dirs, tuple(e for e in taken.entries if e.name not in keep))
    _seat.finish_queue(seat, session, taken)
    if owned:
        return lines
    ctx.exit(EX_NOT_YET if not_yet else EX_PAGE if lines else EX_NOTHING)
    return lines


#: Claude Code's cap on one hook's `additionalContext` is
#: 10,000 characters; past it the model is handed a 2,000-character preview
#: and a file path it is not asked to read (code.claude.com/docs/en/hooks,
#: "Hook output"). The hook keeps its whole string under this, a margin
#: under the cap.
HOOK_CONTEXT_MAX = 9_500

#: What a bounded scoop keeps free under its ROOM for the lines
#: it says after the entries: the budget line, the held line, the not-done
#: reminder. Each is under 120 characters.
SCOOP_TAIL_RESERVE = 360

#: The cap on one tail line, SCOOP_TAIL_RESERVE's "each is
#: under 120 characters" - an invariant the not-done line now keeps by test.
TAIL_LINE_MAX = 120

#: The not-done line's door (an instruction with no door teaches an agent to
#: skim the slot). The verb always stays;
#: `--reason ...` goes first when the box names need the room.
NOT_DONE_DOORS = (" - `message mark ID done --reason ...` quiets one",
                  " - `message mark ID done` quiets one")


def _box_label(key: str, clip: int | None) -> str:
    """A box key, shortened to CLIP characters: a plus-address loses its
    base first (`sessionmail+web-12.foreman` -> `+web-12.foreman`: the
    extension is what tells two of a project's boxes apart), then its end."""
    if clip is None or len(key) <= clip:
        return key
    if "+" in key[1:]:
        key = "+" + key.split("+", 1)[1]
    return key if len(key) <= clip else key[:clip - 1] + "\u2026"


def not_done_line(count: int, age: str, boxes: dict[str, int], session: str) -> str:
    """`[mail] N not done, oldest Xh (box n, box n, +K more n) - <door>`.

    BOXES (`_seat.overdue_boxes`) splits the count by the
    `X-Original-To` key each mail came for. The session's own box is named
    `own` (a 36-character uuid would eat the line), and "" - no done/ copy,
    or no address on it - is `other`. The split goes AFTER `oldest Xh`, so
    the line still starts as it always has. The top two boxes are named and
    the rest rolled into `+K more n`; the line never passes TAIL_LINE_MAX:
    the door's `--reason ...` goes first, then the box names are shortened,
    then only one is named, then none (`(N boxes)`)."""
    named: dict[str, int] = {}
    own = (session or "").strip().lower()
    for key, n in boxes.items():
        label = "other" if not key else "own" if own and key == own else key
        named[label] = named.get(label, 0) + n
    ranked = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))
    head = f"[{RIDE_TAG}] {count} not done, oldest {age}"

    def split(top: int, clip: int | None) -> str:
        parts = [f"{_box_label(k, clip)} {n}" for k, n in ranked[:top]]
        rest = ranked[top:]
        if rest:
            parts.append(f"+{len(rest)} more {sum(n for _k, n in rest)}")
        return f" ({', '.join(parts)})"

    if ranked:
        for top in (2, 1):
            for clip in (None, 24, 16, 12):
                # `--reason ...` is the first thing to go: it is offered
                # only while the top two boxes are named in full, never
                # bought back with a shortened or dropped box name.
                doors = NOT_DONE_DOORS if (top, clip) == (2, None) else NOT_DONE_DOORS[-1:]
                for door in doors:
                    line = head + split(top, clip) + door
                    if len(line) <= TAIL_LINE_MAX:
                        return line
        line = head + f" ({len(ranked)} boxes)" + NOT_DONE_DOORS[-1]
        if len(line) <= TAIL_LINE_MAX:
            return line
    return head + (NOT_DONE_DOORS[0] if len(head + NOT_DONE_DOORS[0]) <= TAIL_LINE_MAX
                   else NOT_DONE_DOORS[-1])


#: The one line for the entries a bounded scoop did not give.
HELD_LINE = f"[{RIDE_TAG}] {{n:,}} more held for your next tool call"

#: This session's record of entries a scoop filed in
#: done/ with no line (a mail it had marked `done` before it was shown).
#: One JSON object a line, under the session's ride/ (a FILE beside the
#: queue's directories: every queue reader lists directories only). The
#: next `pager listen` banner names them.
UNLINED_FILE = "unlined.jsonl"


def _cost(lines: list[str]) -> int:
    """The characters LINES add to a hook's `"\n".join(...)`, one newline
    each (an over-count of at most one: the bound errs short)."""
    return sum(len(line) + 1 for line in lines)


def _ride_item(mid: str, v, entry_lines: list[str], admitted: bool) -> dict:
    """The `items[]` object for one entry the scoop gave a line of
    its own. GIVEN is the chars of its lines in the emitted string;
    IN_MAIL the chars of the body its view was cut from (`View.chars`
    given plus `View.cut` not given); CUT, that the body did not reach the
    string whole. VIEW is the view's kind when its body was admitted, else
    `header` (the header line only: refused by the batch budget, or no
    view could be made of the file)."""
    given = len("\n".join(entry_lines))
    if v is None:
        return _injectlog.item(mid, kind="mail", view="header", given=given,
                               in_mail=None, cut=True)
    kind = "heard" if v.voice else "mail"
    in_mail = v.chars + v.cut
    if admitted:
        return _injectlog.item(mid, kind=kind, view=v.kind, given=given,
                               in_mail=in_mail, cut=not v.given_whole)
    return _injectlog.item(mid, kind=kind, view="header", given=given,
                           in_mail=in_mail, cut=in_mail > 0 or v.textless)


def _page_items(entries: list) -> list[dict]:
    """The `items[]` of a monitor or hand-listen page - the
    arrivals it names, oldest first. A page is a count, never a body: no
    mail's text is in the string, so each is `view: count`, `given: 0`,
    `in_mail: null` (no view was made), `cut: true`."""
    return [_injectlog.item(getattr(a, "message_id", None), kind="mail", view="count",
                            given=0, in_mail=None, cut=True) for a in entries]


def _arrival_ms(entries: list, now: float | None = None) -> "int | None":
    """A page row's `ms`, from the OLDEST arrival's delivery (its
    file's mtime: the box's, or the queue link's, which keeps it) to now.
    None when no arrival can be stat'ed (scooped before the row)."""
    times = []
    for a in entries:
        try:
            times.append(a.path.stat().st_mtime)
        except (OSError, AttributeError):
            continue
    if not times:
        return None
    now = time.time() if now is None else now
    return max(0, int((now - min(times)) * 1000))


def _voice_pointer_line(chars: int, pointer: str) -> str:
    """A trusted voice turn too long for any hook. N13 rules a
    turn is given WHOLE or not at all, so it is not given: this line says
    so, and where to read it whole."""
    return (f"[{HEARD_TAG}] {chars:,} characters - too long to carry whole in a hook, "
            f"not given: `message read {pointer}` to read it whole")


def _record_unlined(seat: "_seat.Seat", session: str, stamp: str, unlined: list) -> None:
    """Append each (entry, parsed, mid) the scoop filed with no
    line to this session's `UNLINED_FILE`. Never raises: a record that
    cannot be written costs the banner a name, never the scoop."""
    try:
        path = _seat.ride_dir(seat, session) / UNLINED_FILE
        with path.open("a", encoding="utf-8") as fh:
            for e, parsed, mid in unlined:
                fh.write(json.dumps({
                    "stamp": stamp, "id": mid, "name": e.name,
                    "from": getattr(parsed, "sender_local", "") or "",
                    "subject": getattr(parsed, "subject", "") or ""}) + "\n")
    except (OSError, _seat.NoIdentity):
        pass


def read_unlined(seat: "_seat.Seat | None", session: str, since: datetime) -> list[dict]:
    """The entries this session's scoops filed with no line at
    or after SINCE, oldest first, one per id. Missing or torn: skipped."""
    try:
        path = _seat.ride_dir(seat, session) / UNLINED_FILE
        raw = path.read_text(encoding="utf-8").splitlines()
    except (OSError, _seat.NoIdentity):
        return []
    out: dict[str, dict] = {}
    for line in raw:
        try:
            rec = json.loads(line)
            when = datetime.fromisoformat(rec["stamp"])
        except (ValueError, KeyError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when >= since and rec.get("id") and rec["id"] not in out:
            out[rec["id"]] = rec
    return list(out.values())


#: How many given ids the listen banner names before it counts the rest:
#: every given id, capped at 10 named plus "N more".
BANNER_NAMED = 10

#: Seconds after a page's `seen` mark past which a link still
#: waiting in the queue is named UNSHOWN: the page said a count, and no
#: carry has shown it since.
UNCARRIED_GRACE = 120

#: A subject on a banner name line is cut to this many characters.
BANNER_SUBJECT = 60


def _named_mail(paths, wanted: set[str]) -> dict[str, tuple[str, str]]:
    """Message-ID -> (sender, subject) for the WANTED ids, read from the
    files PATHS yields, stopping once every one is found. A file that
    cannot be read is passed over: an id with no file is named bare."""
    from . import _detect, _maildir
    found: dict[str, tuple[str, str]] = {}
    for path in paths:
        if len(found) == len(wanted):
            break
        mid = _detect._message_id(path)
        if not mid or mid not in wanted or mid in found:
            continue
        try:
            parsed = _maildir.parse(path, 0, True)
        except Exception:  # noqa: BLE001 - see the docstring
            continue
        found[mid] = (parsed.sender_local or "?", parsed.subject or "(no subject)")
    return found


def _box_files(box: "Path | None", queue: "Path | None"):
    """Every file a banner name can be read from: the queue's new/ first
    (its links), then the box's new/ and cur/."""
    for d in ((queue,) if queue is not None else ()) + (
            (box / "new", box / "cur") if box is not None else ()):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            yield d / name


def given_lines(seat: "_seat.Seat | None", session: str, given: set[str],
                box: "Path | None", queue: "Path | None",
                now: "datetime | None" = None) -> list[str]:
    """The listen banner's names for what this session was
    given and may never have seen, under its `box now` count.

    - `given`: each id `given` holds (a `seen` or `read` of this session
      inside the reminder window), newest first, BANNER_NAMED of them, then
      a count. The banner cannot tell a giving that reached the model from
      one that was lost on the way (a cap, a timeout), so it names them all.
    - `UNSHOWN` (a): an id paged (`seen`, reason `pager wake`) and never
      carried, its link still waiting in the queue past UNCARRIED_GRACE.
    - `UNSHOWN` (c): an id a scoop filed with no line (`UNLINED_FILE`: it
      was marked `done` before this session was shown it).

    Each line: the id, the sender, the subject cut to one line. Never
    raises: a name that cannot be read costs the banner that line only."""
    if seat is None:
        return []
    from . import _detect, _views
    now = now or datetime.now().astimezone()
    since = now - timedelta(minutes=_seat.remind_minutes())
    try:
        events = _seat.read_mark_events(seat, session)
    except (_seat.Malformed, OSError):
        events = []
    latest: dict[str, datetime] = {}
    woke: dict[str, datetime] = {}
    carried: set[str] = set()
    for m in events:
        if m.session != session or m.mark not in ("seen", "read"):
            continue
        try:
            when = datetime.fromisoformat(m.stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when < since:
            continue
        latest[m.message_id] = max(when, latest.get(m.message_id, when))
        if m.reason.startswith("pager wake"):
            woke.setdefault(m.message_id, when)
        elif m.reason.startswith("ride-along"):
            carried.add(m.message_id)
    waiting: set[str] = set()
    if queue is not None:
        try:
            waiting = {_detect._message_id(queue / n) for n in os.listdir(queue)}
        except OSError:
            waiting = set()
    grace = timedelta(seconds=UNCARRIED_GRACE)
    uncarried = sorted((mid for mid, when in woke.items()
                        if mid in given and mid in waiting and mid not in carried
                        and now - when >= grace), key=lambda mid: woke[mid])
    unlined = [r for r in read_unlined(seat, session, since) if r["id"] not in given]
    named = sorted((mid for mid in given if mid not in uncarried),
                   key=lambda mid: latest.get(mid, since), reverse=True)
    shown, more = named[:BANNER_NAMED], len(named) - BANNER_NAMED
    names = _named_mail(_box_files(box, queue), set(shown) | set(uncarried))

    def flat(text: str) -> str:
        return " ".join(_views.without_controls(text, keep=_views.SPACING).split())

    def name(mid: str, who: str, subject: str) -> str:
        subject = flat(subject)
        if len(subject) > BANNER_SUBJECT:
            subject = subject[: BANNER_SUBJECT - 1] + "…"
        return f"{flat(mid)}  {flat(who)}  {subject}" if who or subject else flat(mid)

    out = [f"  given      : {name(mid, *names.get(mid, ('', '')))}" for mid in shown]
    if more > 0:
        out.append(f"  given      : and {more:,} more - `message list` to see them")
    for mid in uncarried:
        out.append(f"  UNSHOWN    : {name(mid, *names.get(mid, ('', '')))} - paged "
                   f"{woke[mid].strftime('%H:%M')}, never carried: still waiting in the queue")
    for r in unlined:
        out.append(f"  UNSHOWN    : {name(r['id'], r.get('from', ''), r.get('subject', ''))}"
                   " - marked done before it was shown: filed with no line")
    return out


def _fit(line: str, action: str = "") -> str:
    """LINE on one row of `LINE_WIDTH`, white space collapsed; ACTION (D7)
    appended as `(ride)` AFTER the cut, so a long subject never costs the
    line the action that applied."""
    line = " ".join(line.split())
    tail = f" ({action})" if action else ""
    room = LINE_WIDTH - len(tail)
    return (line if len(line) <= room else line[: room - 1] + "…") + tail


def _queue_line(entry: "_seat.QueueEntry", parsed, extras: tuple[str, ...] = (),
                action: str = "", heard=None) -> str:
    """One queue entry, one line: when, who, what - from the file itself.

    This is D4's HEADER line. EXTRAS are its additions (3.2b: the level
    when it is not `normal`, a voice turn's device), printed after the
    subject; a long subject is cut to keep them, so the one thing the
    receiver's rules and the reader key on is never the part that falls
    off the end.

    No control character reaches it: the subject and
    sender are the part of a mail its SENDER writes most freely, and this
    line goes into a seat's context and terminal. Each piece is stripped by
    `_views.without_controls`, THE strip the text part uses, with the
    whitespace controls kept only so `split()` makes them word breaks - so
    the line keeps no newline or tab either, and is measured after the strip.

    ACTION is the action that applied, when it is known. It is
    printed LAST, as `(ride)`, after the extras, so neither a long subject
    nor the additions ever cost the line its action (`_fit`).

    HEARD is a trusted spoken turn's `_views.Heard`, or UNVERIFIED
    voice's (`verified` False), whose line is `[heard UNVERIFIED
    HH:MM:SS <device>]`: no speaker, since an unsigned `Ears-Speaker` is
    never believed. A trusted line is
    `[heard HH:MM:SS <device> <speaker>]`; either carries NO subject: Ears
    writes a turn's Subject as the body's first words, so the old line
    printed them twice (once here, once in the whole view). Amends D4's
    header row: it keeps only what tells the reader who is speaking. A
    missing device is
    omitted; extras, the recovered note and the action follow as for mail.
    Every other line is unchanged, byte for byte.
    """
    from . import _views            # already loaded: the scoop made its Budget

    def flat(text: str) -> str:
        return " ".join(_views.without_controls(text, keep=_views.SPACING).split())

    if parsed is None:
        line = flat(f"[{RIDE_TAG}] {entry.name}")
    elif heard is not None:
        when = parsed.date.astimezone().strftime("%H:%M:%S") if parsed.date else "--:--:--"
        if getattr(heard, "verified", True):
            fields = [HEARD_TAG, when, flat(heard.device), flat(heard.speaker) or "?"]
        else:   # unsigned, so visibly so, and no speaker is shown
            fields = [HEARD_TAG, UNVERIFIED_TAG, when, flat(heard.device)]
        line = "[" + " ".join(f for f in fields if f) + "]"
    else:
        when = parsed.date.astimezone().strftime("%H:%M") if parsed.date else "--:--"
        line = flat(f"[{RIDE_TAG}] {when}  {parsed.sender_local or '?'}  "
                    f"{parsed.subject or '(no subject)'}"
                    # the unreadable mark; by getattr, as this line reads any
                    # entry-shaped PARSED (the tests' stand-ins carry none)
                    f"{getattr(parsed, 'unreadable_mark', '')}")
    tail = "".join(f"  ({flat(x)})" for x in extras)
    if entry.recovered:
        tail += f"  (recovered from {flat(entry.recovered)})"
    if not extras:
        return _fit(line + tail, flat(action))
    if action:
        tail += f" ({flat(action)})"
    room = LINE_WIDTH - len(tail)
    if len(line) > room:
        line = line[: max(room - 1, 0)] + "…"
    return line + tail


def _read_boxes(seat: "_seat.Seat", session: str) -> tuple[str, tuple[str, ...]]:
    """(the box `message read` reads with no BOX, the other box to try).

    The same choice as `message._default_box`: this session's box once it
    exists, else the seat's. A cut line's row must be a row in the box the
    reader's bare `message read N` opens, or it reads a different message."""
    from . import _maildir
    name = getattr(seat, "name", "") or ""
    if session not in ("", "-"):
        try:
            _maildir.resolve_box(session)
            return session, ((name,) if name else ())
        except _maildir.NoSuchBox:
            pass
    return name, ()


#: D3/O6 (3.3): the reason a superseded message's `seen` is written with,
#: when the ride-along gives its superseding one instead.
SUPERSEDED_REASON = "superseded"

#: D5 (3.4): the reasons the marks a whole view earns are written with.
WHOLE_REASON = "ride-along whole"
WHOLE_VOICE_REASON = "ride-along whole (voice)"

#: HELD until the subagent mail guard is DEPLOYED: a mail given whole
#: earns `seen` ONLY, not `read`. `message list --unread` hides `read`, and
#: until that guard is deployed a background subagent's tool batch can take the
#: seat's ride-along, so a `read` written there would hide the mail from
#: the documented mitigation (`message list --unread` after a subagent).
#: The path back to O13 (*Delivered whole is read*) is this one line: set
#: it True once the guard is deployed. The voice turn's `done` is not held.
READ_ON_WHOLE = False


def _earned(view) -> tuple[tuple[str, str], ...]:
    """The marks VIEW earned beyond `seen` (D5, 3.4), as (mark, reason).

    A view that carries every character (`View.given_whole`): a MAIL earns
    `read`, reason `ride-along whole`; a trusted VOICE turn earns `done`,
    reason `ride-along whole`, so no `not done` line counts it. A
    cut view earns nothing. Mail never earns `done` here: for mail, done is
    the seat's own act (*Read is not terminal*).

    HELD: while `READ_ON_WHOLE` is False (until the mail guard is deployed), a mail
    given whole earns nothing beyond `seen` either."""
    if not view.given_whole:
        return ()
    if view.voice:
        return (("done", WHOLE_VOICE_REASON),)
    return (("read", WHOLE_REASON),) if READ_ON_WHOLE else ()


def _entry_view(entry: "_seat.QueueEntry", parsed, mid: str,
                read_boxes: "Callable[[], tuple[str, tuple[str, ...]]]"):
    """ENTRY's view (`_views.View`), or None when the file cannot be read
    as a message. A view that fails leaves the header line as it was:
    the view is an addition to the line and never a reason to lose it.
    READ_BOXES is asked only when the view needs a pointer."""
    if parsed is None:
        return None
    from . import _views, _voice
    try:
        msg = _views.load(entry.path)
        return _views.view(
            msg, lambda: _views.pointer(entry.name, mid, *read_boxes()),
            signed=_voice.signed_file(entry.path))
    except Exception:
        return None


def _age(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"

def ride_along_line(seat: "_seat.Seat | None", session: str,
                    now: float | None = None) -> str:
    """Item 3 of the page: the ride-along's state, read from THIS session's
    ride-along stamp (`<session>/stamps/ride-along`) and nothing
    else. No new instrument: the stamp is the one the ride-along already
    touches on every tool call, and its mtime is the clock.

    specs/pager: "A page SHALL distinguish an absent stamp from a stale
    one", and where the evidence is ambiguous the page states its best
    reading AND the evidence, so the reader can judge it. Three states,
    three different sentences - none of them says the ride-along has
    stopped, because a stamp cannot tell you that:

    - no stamp: it has not run for THIS session. It may never have been
      wired here; a session that has made no tool call yet looks the same.
    - fresh (younger than the reminder window; it was the busy TTL until
      stopped-means-idle retired that on 2030-06-09): it is keeping up, so the entries
      arrive on the next tool call - wait rather than act.
    - stale (older): best reading is that nothing is scooping for this
      session now, so act. The age is printed either way.

    SEAT is a NAME here and may be None: the stamp is found under the
    session id, so a seatless session gets the same line - and the same
    page - a seated one gets ("A seatless session"). Only a run with no
    session id EITHER has no stamp to read.
    """
    try:
        # The stamp keys on the SESSION, so a seat is
        # not needed to find it: a seatless session reads the same stamp a
        # seated one wrote. `NoIdentity` is the case 1.3 left open - no
        # session id AND no seat - where there is nothing to key on at all.
        stamp = _seat.stamp_dir(seat, session) / _seat.RIDE_ALONG_STAMP
    except _seat.NoIdentity:
        return ("pager: ride-along - cannot be read: no session id and no seat "
                "identity in the environment, so there is no stamp to read. "
                "Take them yourself below.")
    try:
        ran_at = stamp.stat().st_mtime
    except OSError:
        return ("pager: ride-along - has not run for this session: no stamp at "
                f"{stamp}. It may not be wired here, or this session may not "
                "have made a tool call yet. Take them yourself below.")
    now = time.time() if now is None else now
    age = _age(timedelta(seconds=max(0.0, now - ran_at)))
    # The window is the reminder interval (SESSIONMAIL_REMIND_MINUTES): the
    # busy TTL went with the stopped stamp (stopped-means-idle, 2030-06-09).
    ttl = _seat.remind_minutes() * 60
    if (now - ran_at) < ttl:
        return (f"pager: ride-along - live, last ran {age} ago: they should arrive "
                "on your next tool call, which the re-arm is. Wait rather than act.")
    return (f"pager: ride-along - last ran {age} ago, past the {ttl / 60:g} min window: "
            "best reading is that nothing is scooping for this session now, so "
            "take them yourself below. The evidence is the stamp's mtime.")


#: How many messages for ONE address it takes before the ride-along says
#: "N for this address" instead of one line each. specs/pager: "Counts SHALL
#: coalesce per address across a burst, not be emitted per message."
#:
#: 4 is a provisional choice, and it is reversible - like
#: `SESSIONMAIL_PAGE_LIST`'s name before it. It is a threshold, deliberately
#: NOT a sender or subject filter: a filter fixes one instance and leaves the
#: class armed. Below it, ordinary
#: traffic keeps its sender and subject; above it, a bridge writing every
#: chunk of a call costs one line instead of fourteen.
GROUP_AFTER_VAR = "SESSIONMAIL_GROUP_AFTER"
GROUP_AFTER = 4


def group_after(env: dict[str, str] | None = None) -> int:
    """The burst threshold, or GROUP_AFTER. A value that is not a positive
    whole number is IGNORED rather than obeyed: a typo must not silently turn
    grouping off, the same reasoning `page_list_on` uses for its own typos."""
    env = os.environ if env is None else env
    raw = (env.get(GROUP_AFTER_VAR) or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return GROUP_AFTER


def group_line(group: "_maildir.Group") -> str:
    """One envelope recipient, one line: who it was addressed to, three
    counts, and where the rows are.

    NO sender and NO subject - that is the page's standing prohibition and
    this line does not touch it. `X-Original-To` is who it was addressed TO;
    "no count of who it is FROM" is about the sender, a different header.

    The counts carry a thousands separator and the ROW RANGE does not: a row
    number is pasted into `message read` and a comma would not survive the
    paste (specs/message, "A listing defaults to the newest 40" - settled
    there, not re-decided here).
    """
    who = group.address or "(no X-Original-To)"
    line = (f"pager: {who} - {group.unseen:,} unseen, {group.unread:,} unread, "
            f"{group.total:,} total")
    return line + (f"; rows {group.rows}." if group.rows else ".")


#: How many addresses get a line of their own before the rest become a
#: count. The page is a NUDGE and must stay readable at a glance: "a count,
#: never a manifest" is the ratified rule, and one line per address with no
#: ceiling is a manifest wearing a count's clothes on a box with fifty
#: correspondents. Above-the-fold is the whole discipline here.
#:
#: 4 is provisional and reversible.
GROUP_LINES_MAX = 4


def group_lines(groups: "list[_maildir.Group]") -> list[str]:
    """The grouped counts, oldest group first, BOUNDED.

    Empty when there is nothing to group, so a caller can always splice the
    result in. Past `GROUP_LINES_MAX` addresses the remainder becomes one
    line that says how many are left and names its door - a reader is never
    left with a number and no way to reach the mail behind it.
    """
    if len(groups) <= GROUP_LINES_MAX:
        return [group_line(g) for g in groups]
    head = groups[:GROUP_LINES_MAX]
    rest = groups[GROUP_LINES_MAX:]
    return [group_line(g) for g in head] + [
        f"pager: and {len(rest):,} more address{'' if len(rest) == 1 else 'es'}, "
        f"{sum(g.total for g in rest):,} messages - `message list` for the rest."
    ]


def quiet_page(count: int, seat: "_seat.Seat | None", session: str,
               now: float | None = None,
               groups: "list[_maildir.Group] | None" = None,
               why: "list[str] | None" = None) -> list[str]:
    """THE PAGE. specs/pager, "The pager is a nudge, and the re-arm
    carries": four things, in this order, and no per-message detail - no
    sender, no subject, no count of who it is from. A page delivers
    nothing; the re-arm IS the delivery.

    The order is the requirement's order and is load-bearing: the reader is
    deaf from the moment the listener returns, so how to arm it again comes
    before anything else on the page.
    """
    # GROUPS is optional so that every caller written before the grouping
    # keeps working unchanged, and so the four items stay exactly
    # four: the grouped counts are part of ITEM 2 - "how many messages are
    # waiting" - said per envelope recipient, not a fifth item. WHY is the
    # same (D7, tasks 4.3): what woke it, as the deciding rules, part of
    # item 2 and never a fifth thing.
    return [
        PAGE_OFF,                                                    # 1
        f"pager: {count} message{'' if count == 1 else 's'} waiting.",  # 2
        *group_lines(groups or []),                                  # 2, by address
        *(why or []),                                                # 2, what woke it
        ride_along_line(seat, session, now),                         # 3
        PAGE_COMMANDS,                                               # 4
    ]


#: How many not-done lines the resident view shows before "+N more". It
#: was each subscription's `policy:` (default 3); with no files, the default.
RESIDENT_LIMIT = 3


def _box_line(entry: "_maildir.Entry") -> str:
    """One message in the session's box, one line: which address delivered
    it (`X-Original-To`, the envelope recipient - a subscribed box says its
    own name), when, who, what. From the file itself, never an index.

    No control character reaches it: the
    same strip as the ride-along's header line (`_queue_line`), because
    `pager status` puts this line into a seat's context and terminal too."""
    from . import _maildir, _views
    tag = _maildir.original_to_key(entry.delivered_to) or RIDE_TAG
    when = entry.date.astimezone().strftime("%H:%M") if entry.date else "--:--"
    line = (f"[{tag}] {when}  {entry.sender_local or '?'}  {entry.subject or '(no subject)'}"
            f"{entry.unreadable_mark}")
    line = " ".join(_views.without_controls(line, keep=_views.SPACING).split())
    return line if len(line) <= LINE_WIDTH else line[: LINE_WIDTH - 1] + "…"


#: D8: with NO session id, the box is the
#: maildir this names. An mbox (`/var/mail/admin`) is refused, never read;
#: the pager never creates it. Measured: unset in a Claude Code session, so
#: an agent meets it only when inherited, and then the session wins.
MAIL_VAR = "MAIL"

def grace_line(box: str, session: str | None, box_path: Path | None = None) -> str:
    """The ONE line `pager listen BOX` prints in its release of grace (D9;
    spec "A box argument, in the release of grace"). The design's words, BOX's name
    put in. A BOX that IS the
    session's own box has nothing to subscribe to: the bare verb is its
    replacement, and the line says that instead.

    With NO session (a human at a shell, `pager listen alice`) the line never
    names `message subscription add`: it defaults to the SESSION, which a
    human lacks. Design D8 names the human's replacement - MAIL set to the
    maildir, the bare verb - so the line names that, with BOX's path."""
    if session is None:
        where = shlex.quote(str(box_path if box_path is not None else box))
        return (f"pager listen: BOX is going away; with no session, run "
                f"MAIL={where} pager listen")
    if box == session:
        return ("pager listen: BOX is going away; this is your own box - run the bare "
                "`pager listen`")
    return (f"pager listen: BOX is going away; to hear {box} in your own box, "
            f"run message subscription add {box}")


# ── `pager listen --monitor`: the pager as a Claude Code monitor ────
#
# design.md (task dir) D1-D8. Claude Code runs a plugin monitor's command for
# the whole session and turns each stdout line into a notification; it does
# NOT restart one that exits (read in 2.1.295). So the monitor never exits on
# a page: it prints ONE line and keeps listening. `pager off`/`stop` ended it
# for good; now they REFUSE on it (they once muted it), `pager mute` silences it, and only
# `pager stop --kill` ends it: the seat then listens with the bare verb.


def monitor_spacing(env: dict[str, str] | None = None) -> float:
    """The least gap between two monitor lines, in seconds (D6). A value
    that is not a number, or is negative, is IGNORED: a typo must not let
    the pager drain the host's bucket."""
    env = os.environ if env is None else env
    raw = (env.get(MONITOR_SPACING_VAR) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_MONITOR_SPACING
    except ValueError:
        return DEFAULT_MONITOR_SPACING
    return value if value >= 0 else DEFAULT_MONITOR_SPACING


class MonitorOut:
    """The monitor's stdout: one line per call, flushed, spaced (D2, D6).

    stdout to a pipe is block-buffered, and an unflushed page is a page that
    never arrives. Nothing else in monitor mode may write to stdout."""

    def __init__(self, spacing: float | None = None, *, log: bool = False) -> None:
        self.spacing = monitor_spacing() if spacing is None else spacing
        self.last: float | None = None
        #: `pager listen --monitor` makes it with LOG
        #: on, and every line `say` prints - the page, the deaf line, the off
        #: line - gets one `inject` row, `via: monitor`. SEAT is set once the
        #: listener has resolved it; a line said before that resolves it here.
        self.log = log
        self.seat: "_seat.Seat | None" = None
        self._seat_tried = False

    def say(self, line: str, *, items: "list[dict] | None" = None,
            ms: "int | None" = None) -> None:
        if self.last is not None:
            wait = self.last + self.spacing - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        said = " ".join(line.split())
        out = sys.stdout
        out.write(said + "\n")
        out.flush()
        self.last = time.monotonic()
        if self.log:
            # AFTER the print, on the string printed - the normalised line,
            # not the argument and not its newline (§16.2). The session is
            # read now: a monitor that followed claude to a new id
            # logs under the id it is running in. Never raises.
            try:
                if self.seat is None and not self._seat_tried:
                    self._seat_tried = True
                    try:
                        self.seat = _seat.resolve()
                    except _seat.NoIdentity:
                        self.seat = None
                _injectlog.write(_seat.session_id(), via=_injectlog.VIA_MONITOR, s=said,
                                 seat=self.seat, ride=_injectlog.Ride(items=list(items or [])),
                                 ms=ms)
            except Exception:  # noqa: BLE001 - a row is never why a page fails
                pass


def _ride_fresh(seat: "_seat.Seat | None", session: str, now: float | None = None) -> bool:
    """Is the ride-along keeping up for SESSION - its stamp younger than the
    reminder window (`ride_along_line`'s second state)? False when there is
    no stamp, or it cannot be read."""
    try:
        stamp = _seat.stamp_dir(seat, session) / _seat.RIDE_ALONG_STAMP
        ran_at = stamp.stat().st_mtime
    except (OSError, _seat.NoIdentity):
        return False
    now = time.time() if now is None else now
    return (now - ran_at) < _seat.remind_minutes() * 60


#: The monitor line's tail, by the ride-along's state.
MONITOR_TAIL_FRESH = ("They ride along on your next tool call; if not: "
                      "message list --unread")
MONITOR_TAIL_STALE = ("Nothing is scooping for this session: "
                      "message list --unread")
#: The tail of a page of REMINDERS only: what
#: lists a read mail, and what quiets it. Never `message list --unread`
#: alone - it hides a read mail, so the seat would find nothing to act on.
MONITOR_TAIL_REMINDER = ("`pager status` lists them; "
                         "`message mark <ID> done` quiets one")
MONITOR_TAIL_REMINDER_ONE = ("`pager status` lists it; "
                             "`message mark <ID> done` quiets it")


@dataclass(frozen=True)
class Quiet:
    """What THIS session's marks say about paging, from ONE read of them
   . GIVEN: a `seen` or `read` inside the reminder window - not
    paged again yet ("never twice"). DONE: a `done`, at any time - never
    paged again ("Done is terminal", specs/pager; the same test `pager
    status` makes). SEEN: a `seen` or `read` at any time - a page of one of
    these is a REMINDER, not new mail. READ: a `read` at any time."""
    given: frozenset[str] = frozenset()
    done: frozenset[str] = frozenset()
    seen: frozenset[str] = frozenset()
    read: frozenset[str] = frozenset()


def quiet_marks(events: list, session: str, since: datetime) -> Quiet:
    """`Quiet` for SESSION from its mark EVENTS; SINCE opens the window.
    Another session's marks never count (as `_seat.given_since`)."""
    mine = [m for m in events if m.session == session]
    return Quiet(
        given=frozenset(_seat.given_since(mine, session, since)),
        done=frozenset(m.message_id for m in mine if m.mark == "done"),
        seen=frozenset(m.message_id for m in mine if m.mark in ("seen", "read")),
        read=frozenset(m.message_id for m in mine if m.mark == "read"),
    )


def reminder_state(reminders: "dict[str, bool]") -> str:
    """How a reminder's mails were given before: `read` when every one was
    read, else `paged before` (some were only seen)."""
    return "read" if reminders and all(reminders.values()) else "paged before"


def reminder_line(count: int, state: str) -> str:
    """The bare page's reminder line: part of
    the quiet page's item 2, never a fifth item. It names the quieting act
    and a command that LISTS the mail, because `message list --unread`
    hides a read one."""
    return (f"pager: reminder - {count} of these {state}, not done: `pager status` lists "
            f"{'it' if count == 1 else 'them'}; `message mark <ID> done` quiets "
            f"{'it' if count == 1 else 'one'}.")


def monitor_line(entries: list, verdicts: "dict[str, rules.Verdict | None]",
                 promoted: "set[str] | frozenset[str]" = frozenset(),
                 fresh: bool = True,
                 reminders: "dict[str, bool] | None" = None) -> str:
    """THE MONITOR'S PAGE, one line: the quiet page's count, by
    the address each mail was sent TO, and what woke it - never a sender,
    never a subject, never a re-arm (there is nothing to re-arm). At most
    MONITOR_LINE_MAX characters; a cut takes the middle, never the tail
    that says where the mail is.

    REMINDERS: entry name -> was it `read`,
    for each entry this session was given before and has not marked done.
    A page of new mail only keeps today's text. A page of reminders only
    says so in its head, and its tail names `pager status` (which lists a
    read mail) and `message mark <ID> done` (which quiets it) - never only
    `message list --unread`, which hides a read mail. A mixed page keeps
    today's head and tail and adds the reminder clause."""
    count = len(entries)
    rem = {a.name: reminders[a.name] for a in entries if reminders and a.name in reminders}
    by: dict[str, int] = {}
    for a in entries:
        key = _address_key(a) or "(no X-Original-To)"
        by[key] = by.get(key, 0) + 1
    parts = [f"{who} {n}" for who, n in list(by.items())[:GROUP_LINES_MAX]]
    if len(by) > GROUP_LINES_MAX:
        parts.append(f"+{len(by) - GROUP_LINES_MAX} more")
    woke = [w.removeprefix("pager: ") for w in
            why_lines([verdicts.get(a.name) for a in entries],
                      [a.name in promoted for a in entries])]
    middle = ", ".join(parts) + (f"; woke: {'; '.join(woke)}" if woke else "")
    head = f"pager: {count} message{'' if count == 1 else 's'} waiting"
    tail = MONITOR_TAIL_FRESH if fresh else MONITOR_TAIL_STALE
    if rem:
        state = reminder_state(rem)
        if len(rem) == count:
            head = (f"pager: reminder - {count} message{'' if count == 1 else 's'} "
                    f"{state}, not done")
            tail = MONITOR_TAIL_REMINDER_ONE if count == 1 else MONITOR_TAIL_REMINDER
        else:
            head = f"{head}, {len(rem)} of them a reminder ({state}, not done)"
            tail = f"{tail}. Reminders: {MONITOR_TAIL_REMINDER}"
    middle = " ".join(middle.split())
    room = MONITOR_LINE_MAX - len(head) - len(tail) - len(" (). ")
    if len(middle) > room:
        middle = middle[: max(0, room - 1)] + "…"
    return f"{head} ({middle}). {tail}"


def deaf_line(why: str) -> str:
    """The monitor's last line when it cannot do its job: it says
    so, and names the way back - Claude Code does not restart a monitor."""
    line = (f"pager: DEAF - {' '.join(why.split())}. Run `pager listen` in the "
            "background to listen again.")
    return line if len(line) <= MONITOR_LINE_MAX else line[: MONITOR_LINE_MAX - 1] + "…"


def monitor_off_line(switch: "_seat.MonitorSwitch") -> str:
    """The monitor's one line when its switch is off:
    what switched it off, by name. With the whole pager off
    (SESSIONMAIL_PAGER) that is all it says - the seat opted out, so nothing
    tells it to arm. Otherwise the hand arm is still there, and the line
    reads the arm instruction from its one source (`arm.txt`), without the
    monitor rider, which cannot apply to a monitor switched off."""
    if switch.source.startswith(f"{_seat.PAGER_VAR}="):
        line = (f"pager: the pager monitor did not start: the pager is off for this session "
                f"({switch.source}).")
    else:
        line = (f"pager: the pager monitor did not start: it is switched off ({switch.source}). "
                f"Nothing pages this session unless you listen by hand: {ARM_HOW_NO_RIDER}.")
    line = " ".join(line.split())
    return line if len(line) <= MONITOR_LINE_MAX else line[: MONITOR_LINE_MAX - 1] + "…"


#: The variable the re-exec'd monitor reads its session from.
#: First in sessiontools' chain, so it wins over a stale CLAUDE_SESSION_ID.
FOLLOW_VAR = "CLAUDE_CODE_SESSION_ID"


def follow_session(session: str, caller: int, seat: "_seat.Seat | None",
                   tried: set[str], stamped: bool = True, life=None) -> None:
    """Follow the session across `/clear`.

    A `/clear` keeps claude (the monitor's parent, under `exec`) but gives
    it a NEW session id; a monitor left under the old one still pages, late
    and with no carry (measured). So each tick reads the caller's
    sidecar, and when it names a session other than SESSION this clears
    THIS session's `listening` stamp (when STAMPED: the D8 wait has none
    yet), says so on stderr, and `execv`s itself with FOLLOW_VAR=<new>: the
    pid, the stdout pipe and claude's task stay, and the whole listen start
    - birth, doorbell, declaration, `listening`, confirm - runs for the new
    session.

    stdout says NOTHING: a line there is a notification to the seat, and a
    change of session is not mail. A sidecar that cannot be read, or names
    no session, is nothing (never DEAF on it). Returns only when there is
    nothing to follow, or when the exec failed (said on stderr, the id
    put in TRIED so a failing exec is tried once per id, not every tick).

    LIFE: the listener's lifecycle writer, `life(event, new)`. The
    exec skips every `finally`, so the old session's `exit` row is written
    here, just before it; a failed exec writes a fresh `start`, since the
    listener goes on under SESSION. Never raises.
    """
    new = _seat.sidecar_session(caller)
    if not new or new == session or new in tried:
        return
    tried.add(new)
    pid = os.getpid()
    if seat is not None and stamped:
        _seat.clear_listening(seat, session, pid)
    click.echo(f"pager listen: session {session} -> {new} (claude's sidecar changed: "
               "/clear); the monitor starts again under the new session", err=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    before = os.environ.get(FOLLOW_VAR)
    os.environ[FOLLOW_VAR] = new
    if life is not None:
        life("exit", new)
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except OSError as exc:
        if life is not None:
            life("start", new)
        if before is None:
            os.environ.pop(FOLLOW_VAR, None)
        else:
            os.environ[FOLLOW_VAR] = before
        if seat is not None and stamped:
            _seat.write_listening(seat, session, pid)
        click.echo(f"pager listen: could not start again under {new}: {exc} - still "
                   f"listening under {session}", err=True)


def _box_argument(token: str, session: str) -> tuple[Path, str | None]:
    """Where BOX (in its release of grace) resolves: a maildir path, a name
    under SESSION_MAIL_ROOT, else the map postfix delivers by (a project box
    lives at <root>/<store>/<name>/ under nested-v1) - (path, via_map). The
    path may hold no new/; the caller refuses that."""
    from . import _detect
    box_path = _detect._resolve_box(token)
    if not (box_path / "new").is_dir() and token != session:
        found = _seat.box_via_map(token)
        if found is not None:
            return found
    return box_path, None


@cli.command(
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="[--quiet] [--list]",
    short_help="block on one box until mail sits in it; print the page; exit",
    # message-subscriptions 4.9: the page says when it pages,
    # what it prints and how to stop, with all of that in the first 20 lines
    # (agents read `--help | head -20`). How it works - the ride queue, the
    # hardlinks, the stamps, the reminder window - is in
    # docs/pager-listen-internals.md. Pinned by tests/test_pager_listen_help.py.
    help=unwrapped(
        """Wait until mail arrives in your box, print the page, and exit.
Run it again to keep listening.
`pager stop` stops it.

Your box is this session's. With no session (a human at a shell),
it's $MAIL. Your box is created when your session starts, and the
banner says whether mail reaches it. To hear a project or store
box too, subscribe to it: message subscription add BOX.

It pages at once if you're idle. If you're busy, only when a mail
has waited 60 s without riding along on a tool call."""
    ),
    sections="""Environment:
  SESSIONMAIL_IDLE_SECONDS (1) · SESSIONMAIL_PAGE_AGE_SECONDS (60)
  SESSIONMAIL_REMIND_MINUTES (60) · SESSIONMAIL_PAGE_LIST
  SESSIONMAIL_SUBSCRIBE: boxes the launcher subscribes it to at birth
  MAIL: your box when there is no session

Exit: 0 paged · 1 usage · 2 could not look · 3 already listening
  0 too when the pager monitor already hears for this session""",
)
# BOX and --timeout are still HONOURED, but the help no longer
# names them. --timeout is retired, but taskmaster still calls it, so it
# keeps working until tasks.md 4.8b and 5.2 retire it together; it still
# exits 124 with nothing paged. BOX (tasks.md 4.7) is in its ONE release of
# grace (an argument in the help is one agents use, so the help leaves it
# out): it still watches BOX - a maildir
# path, a name under SESSION_MAIL_ROOT, or a name postfix delivers to (the
# banner says `via map`) - it births the SESSION's box too, and prints
# grace_line() once. After the release it is a usage error (tasks.md 6.5).
@click.argument("box", required=False, metavar="[BOX]")
@click.option("--timeout", "-t", "timeout", type=float, metavar="S", hidden=True,
              help="give up after S seconds without a page (default: none)")
@click.option("--quiet", is_flag=True, help="exit code only")
@click.option("--list/--no-list", "listing", default=None,
              help="one line per message: box, sender, subject")
# HIDDEN, like --timeout. Its one caller is the plugin's
# monitors.json; an agent typing it into a background Bash call would get a
# process that never exits and whose lines nobody reads - deaf, looking armed.
@click.option("--monitor", "monitor", is_flag=True, hidden=True,
              help="for the plugin monitor only: never exit on a page; one line per page")
def listen(box: str | None, timeout: float | None, quiet: bool,
           listing: bool | None, monitor: bool = False) -> None:
    """pager listen - block on ONE box until mail sits in its new/; page; exit"""
    if not monitor:
        _listen(box, timeout, quiet, listing)
        return
    # Claude ending the task, `pager off`/`stop`, a hang-up - a quiet
    # exit 0 through the `finally`s, so the `listening` stamp goes with it
    # (a default SIGTERM would skip them). Restored after, for the caller.
    import signal
    held: dict = {}

    def ended(signum, frame) -> None:
        # Quiet only while the `finally` below puts the caller's
        # handlers back. `signal.signal` runs a pending handler BEFORE it
        # switches, so a raise there aborted that loop and left `ended`
        # installed for the caller; the loop is three `signal.signal` calls
        # and no I/O, so nothing is lost by not ending it twice. Everywhere
        # else a second signal ends it AGAIN, as the first did: the unwind
        # can still block (the `exit` row's flocks wait up to the lock
        # budget, `SESSIONMAIL_LOG_LOCK_SECONDS`), and a repeated Ctrl-C or
        # SIGTERM is how an operator escapes a shutdown that hangs.
        if held.get("restoring"):
            return
        clear = held.get("clear")
        if clear is not None:
            try:
                clear()
            except (OSError, _seat.NoIdentity):
                pass
        raise SystemExit(0)

    previous = {}
    for name in MONITOR_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is not None:
            previous[sig] = signal.signal(sig, ended)
    try:
        _listen(box, timeout, quiet, listing, monitor=MonitorOut(log=True), held=held)
    finally:
        held["restoring"] = True
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _listen(box: str | None, timeout: float | None, quiet: bool,
            listing: bool | None, monitor: "MonitorOut | None" = None,
            held: dict | None = None) -> None:
    """The body of `pager listen`; MONITOR set is `--monitor`."""
    # The caller, read FIRST: before the imports, the banner,
    # the `listening` stamp and the confirm mail. Read after them (it was, at
    # the wait), a caller that went during startup left an orphan that took
    # its new parent for its caller and, with no --timeout, waited for ever.
    parent = os.getppid()
    from . import _detect, _maildir
    ctx = click.get_current_context()

    def stop(why: str, code: int) -> None:
        click.echo(f"pager listen: {why}", err=True)
        if monitor is not None and code == EX_COULD_NOT_LOOK:
            # Stderr reaches nobody in a monitor until it ends, so the
            # one channel the seat hears says it went deaf, and the way back.
            monitor.say(deaf_line(why))
        ctx.exit(code)

    if monitor is not None:
        # What a monitor cannot take. --timeout is meaningless (it never
        # returns on its own), BOX is in its one release of grace and is not
        # carried into a new mode, and --quiet would silence the very stdout
        # that IS the monitor's event stream.
        clash = [flag for flag, given in (("--timeout", timeout is not None),
                                          ("BOX", bool(box)), ("--quiet", quiet)) if given]
        if clash:
            stop(f"refused: --monitor cannot take {', '.join(clash)} - the plugin's "
                 "monitor runs `pager listen --monitor` alone", EX_USAGE)
            return
        # The switch, read once at start -
        # a monitor runs for the whole session, so a later change takes
        # effect at the next launch. Off: one line naming the source, exit
        # 0, before anything is born, stamped or mailed.
        switch = _seat.monitor_switch()
        if not switch.on:
            click.echo(f"pager listen: {switch.source} - the pager monitor is off; "
                       "not starting", err=True)
            monitor.say(monitor_off_line(switch))
            ctx.exit(EX_MONITOR_OFF)
            return

    if parent == ORPHAN_PARENT:
        # Gone before even this line: an orphan already. Nothing is born,
        # stamped or mailed for a caller that is not there to hear it.
        stop(f"the caller went before the listener started (parent is pid "
             f"{ORPHAN_PARENT}); returning rather than waiting alone", EX_CALLER_GONE)
        return

    session = _seat.session_id()
    has_session = session not in ("", "-")
    box = box or None    # `pager listen ""` is the bare verb, as it always was
    # BOX, in its release of grace, is resolved once, here: the no-session
    # grace line names its path (MAIL=<path>), and the D8 block below uses it.
    box_arg = _box_argument(box, session) if box is not None else None
    if box is not None and not quiet:
        # The ONE release of grace: BOX
        # is still honoured, and says once what replaces it. Said before the
        # box is checked, so a BOX that then refuses still says it.
        # absolute(): a relative BOX would give a MAIL that works only from
        # this directory.
        click.echo(grace_line(box, session if has_session else None,
                              box_arg[0].absolute() if box_arg else None), err=True)
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity:
        seat = None      # a human at a shell: no stamps, no marks, and it says so
    if monitor is not None:
        monitor.seat, monitor._seat_tried = seat, True    # its rows' `seat`
    # This listener's rows - `via`, and its own life (start/exit/error).
    via = _injectlog.VIA_MONITOR if monitor is not None else _injectlog.VIA_LISTEN

    def log_error(what: str, exc: BaseException) -> None:
        # Spec "The listener logs its own life": a write it depends on failed.
        _injectlog.error(_seat.session_id(), via=via, seat=seat,
                         what=f"{what}: {type(exc).__name__}: {exc}")
    # The session ids this monitor tried to follow and could
    # not exec under - one attempt per id, never one per tick.
    followed: set[str] = set()
    # 3.8: the only arm, for mail AND ears, refuses a second: ONE listener
    # per session. Two on
    # one box is carded trouble here: they race for the same page.
    # Asked FIRST, before anything below births a box, writes a
    # subscription, declares an address or stamps `listening`, so a refusal
    # changes no stamp and no mark. Another session's listener is not a
    # second arm: the question is keyed on THIS session.
    # Session-settings 3.3a: the session's state moves to `<session dir>/mail/`
    # FIRST - the second-arm check below reads a stamp there, and every writer
    # after it writes there. One rename under the session dir's own flock;
    # the banner says what happened (`state`), and the box hears anything but
    # a plain move. A dead session is
    # never moved: only a listen moves, and only the living listen.
    moved = _seat.Moved([])
    if has_session:
        try:
            moved = _seat.move_session_state(session)
        except OSError as exc:
            log_error("session state move", exc)
            moved = _seat.Moved([f"NOT MOVED - {exc}; this run writes under "
                                 f"{_seat.session_root(session)}"], True)
        live = _seat.other_listener(seat, session)
        if live is not None and monitor is not None:
            # A refused monitor is gone for good (Claude Code does not
            # restart one), so it WAITS for the other listener to end and
            # takes over - a bare listen that fires and finds the monitor's
            # stamp on its re-arm is refused, and the seat converges on one.
            click.echo(f"pager listen: this session is already listening, pid {live} - "
                       "the monitor waits and takes over when it ends", err=True)
            while live is not None:
                if os.getppid() != parent:
                    stop("the caller went; returning rather than waiting alone",
                         EX_CALLER_GONE)
                    return
                # A /clear while it waits - the listener it
                # waits on is the OLD session's; follow claude to the new
                # one. Nothing is stamped yet, so nothing is cleared.
                follow_session(session, parent, seat, followed, stamped=False)
                time.sleep(_poll_interval())
                live = _seat.other_listener(seat, session)
        if live is not None:
            # The live listener hears this session's box and
            # its subscriptions, never a BOX beyond them. So a `pager listen
            # BOX` asks, READ-ONLY, whether BOX is one of those before it is
            # told anything hears for it: BOX is this session's own box, or a
            # subscription of this session or this seat names BOX exactly.
            # Exact matching errs toward the refusal, which names the repair.
            # SESSIONMAIL_SUBSCRIBE is never read here: where it was applied
            # it is already in the session's map (rca point 3).
            box_heard = True
            if box is not None:
                from . import _subscription
                try:
                    box_heard = (box == session or box_arg[0].resolve()
                                 == (_maildir.root() / session).resolve())
                    if not box_heard:
                        subscribers = {session} | ({seat.address} if seat is not None else set())
                        box_heard = any((who, box) in _subscription.pairs(live={session})
                                        for who in subscribers)
                except Exception:  # noqa: BLE001 - unmeasured is not heard
                    box_heard = False
            if not box_heard:
                # A refusal, as D10 says: exit 3, no stamp, no mark, and a
                # muted monitor stays muted. It names only what was measured:
                # BOX is neither this session's box nor subscribed. Never
                # "that one hears for you" (below): for BOX it would be false.
                why = (f"the pager monitor (pid {live}) hears this session's box, not {box}"
                       if _seat.is_monitor(live)
                       else f"this session is already listening, pid {live}, and {box} "
                            "is neither this session's box nor subscribed")
                stop(f"refused: {why} - to hear {box}, run message subscription add {box}",
                     EX_SECOND_ARM)
                return
            if _seat.is_monitor(live):
                # The live listener is the pager monitor.
                # Today's "`pager stop` ends it first" pointed a seat that only
                # wanted to arm at the one act that makes it deaf: Claude Code
                # does not restart a monitor. Any other listener (or no `ps`
                # answer) keeps today's text, where `pager stop` is right.
                # And it is CALM - exit EX_MONITOR_HEARS, on stdout.
                # The signpost is the FIRST line, nothing above it:
                # a backgrounded exit 0 reads as COMPLETED, and
                # a seat taught to re-arm on every fire must be told not to.
                # The seat meant to listen, so a deliberate stop (its `off`
                # stamp) ends here, as any `pager listen` ends it.
                click.echo(f"the pager monitor (pid {live}) hears for this session; "
                           "you don't need pager listen. Do not re-run it.")
                # Clearing the stamp is an
                # UNMUTE, so the line says so - and only when there was one.
                if _seat.clear_off(seat, session):
                    click.echo(f"it was muted: this `pager listen` unmuted it, so the pager "
                               f"monitor (pid {live}) pages again.")
                ctx.exit(EX_MONITOR_HEARS)
                return
            stop(f"refused: this session is already listening, pid {live} - that one "
                 f"hears for you; `pager stop` ends it first", EX_SECOND_ARM)
            return
    # Session-settings 3.3b: this session's OLD files (box files, `address`)
    # into its layer, right after the move - THE conversion `pager settings
    # migrate` runs for every live session; here it catches one resumed after
    # that ran. After the second-arm check, so a refused listen writes
    # nothing. Lines join the move's on the banner; anything left or failed
    # is noteworthy, so the box hears it. A write owes the map a rebuild.
    converted = False
    if has_session:
        from . import _migrate
        conv = _migrate.Report(dry_run=False)
        try:
            _migrate.convert_session(session, conv, by=seat.address if seat is not None
                                     else session[:8], argv="pager listen")
        except Exception as exc:  # noqa: BLE001 - a conversion never costs the arm
            conv.errors.append((_seat.session_root(session) or Path("."), repr(exc)))
        conv_lines = [ln.strip() for ln in _migrate.session_lines(conv, header=False)]
        conv_lines += [f"NOT converted {path}: {why}" for path, why in conv.errors]
        moved.lines.extend(f"3.3b {ln}" for ln in conv_lines)
        if conv.session_conflicts or conv.address_conflicts or conv.errors:
            moved.noteworthy = True
        converted = conv.changed
    # EVERY session listen births the session's box, a
    # BOX argument or not - the old `own_box` condition minted only on the
    # bare form, and 3 of 8 live sessions launched with `pager listen
    # <store or project>` had no box. The birth is the session's, so it
    # happens before BOX is resolved: a BOX that refuses still leaves the
    # session a box. A $MAIL box is never made (a human's box is theirs).
    # ONE birth path, shared with the SessionStart hook - birth,
    # the birth file's subscriptions (D4, task 3.4), the suffix,
    # declare (D1) and doorbell/regen, in that order, in
    # `_birth.ensure_session_box` (moved from here whole; its
    # master changes ported there at land-prep).
    birth = _birth.ensure_session_box(
        session, seat, converted=converted,
        box_label=(f"this session's box {_maildir.root() / session}"
                   if has_session and box is not None and box != session else None))
    if birth.error is not None:
        stop(birth.error, EX_COULD_NOT_LOOK)
        return
    session_box, born_box = birth.box, birth.born
    sub_lines, bell_lines = birth.sub_lines, birth.bell_lines
    # The maps' FQDN rows need the
    # tailnet suffix. With none (no MAGICDNS_SUFFIX, no tailscale answering)
    # this listen does NOT refuse: the birth skipped every regen, and it is
    # said here and in the banner, and the listen goes on with the maps as
    # they are. A deaf fleet cannot be told what is wrong. A success is
    # memoised for the process, so `$me` below costs nothing more. A
    # mail-drop host with no tailnet needs no suffix: there it is said once,
    # here, as INFO, with no advice to act on (`drop_without_tailnet`).
    from . import _magicdns
    suffix_lines: list[str] = []
    if birth.suffix_unknown and _magicdns.drop_without_tailnet():
        suffix_lines.append(f"  suffix     : none - {_magicdns.NO_TAILNET}")
        _magicdns.said()
    elif birth.suffix_unknown:
        click.echo(f"pager listen: {_magicdns.UNKNOWN}; maps NOT regenerated", err=True)
        suffix_lines.append(f"  suffix     : {_magicdns.UNKNOWN}; maps NOT regenerated - "
                            "listening on the existing maps; $me and the keeper test use "
                            "the short host only")
        _magicdns.said()

    # D8 (10.1, 10.2): which box is watched. The session's; else, with
    # no session id, the maildir $MAIL names; else refuse naming both. The
    # session wins because MAIL is inherited: an agent started from a
    # person's shell would otherwise listen on that person's mailbox. BOX, in its release of
    # grace, wins over both.
    via_map = None
    if box is not None and box_arg is not None:
        token = box
        box_path, via_map = box_arg
        # changes/task-address-doorbell D5: a `<base>+<ext>` seat listening on
        # `<base>` hears every sibling's mail (one measured seat: 349 ride
        # mails, 17 its own).
        # Grammar-free: sessionmail knows the plus, not what the extension means.
        # Its one way round, the HEARS variable, retired with message-
        # subscriptions 4.8: a seat that wants its base subscribes.
        refused = (_seat.refuses_base(seat, box)
                   if seat is not None and box != session else None)
        if refused:
            stop(f"refused: {box} is the base of this seat's address {seat.address} - run "
                 f"the bare `pager listen`, which hears {seat.address} in this session's box; "
                 f"to hear {refused} there too, run message subscription add {refused}",
                 EX_COULD_NOT_LOOK)
            return
        if not (box_path / "new").is_dir():
            hint = ("" if "/" in token or token == session
                    else f"; and no virtual_mailbox_maps row delivers {token}@<host> to a maildir")
            stop(f"no maildir at {box_path} (no new/) - nothing to listen on{hint}",
                 EX_COULD_NOT_LOOK)
            return
    elif session_box is not None:
        box_path = session_box
    else:
        mail = (os.environ.get(MAIL_VAR) or "").strip()
        if not mail:
            stop(f"no box: set {' or '.join(_seat.SESSION_VARS)} - the session id is a "
                 f"box; with no session, set ${MAIL_VAR} to your maildir", EX_COULD_NOT_LOOK)
            return
        box_path = Path(mail).expanduser()
        if not (box_path / "new").is_dir():
            what = "a file (an mbox?)" if box_path.is_file() else "no maildir (no new/)"
            stop(f"{MAIL_VAR} must name a maildir (a folder holding new/): {box_path} is "
                 f"{what} - never read, never created", EX_COULD_NOT_LOOK)
            return
    # Tasks 4.1: every arrival is JUDGED before it may
    # page, by the layers as their files are at that arrival - `live` stats
    # them each time and re-reads a file that changed, so an edit applies
    # from the next mail with no re-arm. The seat is the one `_seat` resolved
    # (its folder holds the seat's rules); $me is its address and the session.
    from . import rules
    live = rules.LiveLayers(seat, session if has_session else None)
    me = listener_me(seat, session)
    # Whose secret proves a Pager-Note here: this session's. No
    # session, no secret - and no confirmation was ever sent to this box.
    note_session = session if has_session else ""

    def muted() -> bool:
        # This session's `off` stamp,
        # written by `pager mute` (or `off`/`stop` on the monitor), re-read at
        # every scan. With no seat there are no stamps, so nothing mutes.
        return seat is not None and has_session and _seat.stopped_on_purpose(seat, session)

    def threshold() -> float:
        # The stopped stamp selects T, re-read at every scan.
        return page_age(seat, session) if seat is not None else _running_page_age()

    def quiet_now() -> Quiet:
        # specs/pager "Read is not terminal" / "never twice": what this
        # session was already given inside the reminder window is not
        # paged again; another session's marks do not silence this one.
        # And what this session marked `done` is never paged again
        # ("Done is terminal"; the test `pager status` makes) - read in the
        # SAME read of the marks, so a tick reads them once.
        if seat is None:
            return Quiet()
        since = datetime.now().astimezone() - timedelta(minutes=_seat.remind_minutes())
        try:
            return quiet_marks(_seat.read_mark_events(seat, session), session, since)
        except (_seat.Malformed, OSError):
            return Quiet()

    def given_now() -> "frozenset[str]":
        return quiet_now().given

    interval = _poll_interval()
    resident = sorted(os.listdir(box_path / "new"))
    marks_now = quiet_now()
    given = marks_now.given
    # the-queue-chain (G3): with a seat, every box arrival is linked into
    # THIS session's queue and the page age is read off the queue - an old
    # link is one no tool call has scooped. Without a seat there is no
    # queue: the box's own new/ is aged, as before, and the banner says.
    queue: "Path | None" = None
    linked: set[str] = set()
    if seat is not None:
        try:
            queue = _seat.queue_new(seat, session)
            linked = _seat.queue_names(seat, session)
        except OSError as exc:
            stop(f"could not open the queue under {_seat.ride_dir(seat, session)}: {exc}",
                 EX_COULD_NOT_LOOK)
            return

    # The queue's new/ can be ABSENT for a moment under a live
    # listener. An older installed scoop claims it with `mv new taking.* &&
    # mkdir new`, in another process, and a fresh seat on a backlogged box
    # meets that window at boot. Every look at the queue goes through these
    # two helpers. A missing queue is recreated and SAID (a seat must be able
    # to see that its queue vanished), and it never becomes a traceback after
    # a healthy-looking banner: five deaf re-arms in the wild.
    def queue_recreated(why: str) -> None:
        _seat.queue_new(seat, session)
        if not quiet:
            click.echo(f"pager listen: queue {queue} was missing ({why}) - recreated; "
                       "a scoop's rename window, still listening", err=True)

    def queue_listing() -> list[str]:
        try:
            return os.listdir(queue)
        except FileNotFoundError:
            queue_recreated("listing it")
            return []      # what was in it went with the rename: the scoop's now

    def queue_link(fresh: list) -> None:
        # One arrival at a time: a NoSuchBox names the ONE link that did not
        # land, so a retry can never link an arrival twice into two queues.
        for a in fresh:
            for attempt in range(QUEUE_LINK_TRIES):
                try:
                    _detect.link_into([a], [queue])
                    break
                except _detect.NoSuchBox as exc:
                    queue_recreated(f"linking {a.name}: {exc}")
            else:
                stop(f"could not link {a.name} into the queue {queue}: it vanished "
                     f"{QUEUE_LINK_TRIES} times running", EX_COULD_NOT_LOOK)
                return

    if not quiet:
        # specs/pager, "The banner tells the truth": what it watches, how
        # T is chosen and what T is NOW, what is already there, and
        # whether it can stamp and mark. Deafness is announced, never silent.
        click.echo(f"pager listen: box {box_path}   [v{__version__}]", err=True)
        if via_map:
            click.echo(f"  via map    : {via_map} - a bare name that is not <root>/<name>, "
                       f"resolved as postfix delivers it", err=True)
        if born_box:
            # The SESSION's box, which under a BOX argument is not box_path.
            click.echo(f"  born box   : {session_box} (cur/new/tmp) - this session's own, "
                       "made here since the arm retired (F19)", err=True)
        elif has_session and (hook_born := _birth.read_born(session)) is not None:
            # The SessionStart hook made the box, so this
            # listen did not; say so, and what the generator did about it.
            # No record means a listen-born box (said above) or nobody's.
            click.echo(f"  born box   : {session_box} (cur/new/tmp) - this session's own, born "
                       f"at SessionStart {hook_born.when} ({hook_born.source}); "
                       + (hook_born.line or f"generator {hook_born.regen}"), err=True)
        for line in suffix_lines:
            click.echo(line, err=True)
        for line in sub_lines:
            click.echo(line, err=True)
        for line in bell_lines:
            click.echo(line, err=True)
        for line in _diverged():
            click.echo(f"  ⚠ DIVERGED : {line}", err=True)
        state = (idle_state(session) if seat is not None else
                 f"running -> {threshold():g}s (no seat: idleness is never asked)")
        # BLIND: nothing in the environment can say busy, so T can never be 0
        # and the cure is a setting. Said at the end of the line the reader is
        # already on, not in a README they have skipped.
        blind = seat is None or _seat.idle_seconds(session) is None
        click.echo(f"  page age   : now {state}  (idle 0, ${_seat.IDLE_SECONDS_VAR} "
                   f"{_seat.idle_threshold():g}s via `session get idle`; running "
                   f"{_running_page_age():g}s, ${PAGE_AGE_VAR})"
                   + (f" - {BLIND_HINT}" if blind else ""), err=True)
        click.echo("  at once    : an honoured urgent given wake pages a RUNNING seat at "
                   "T=0, not the page age - the backstop for a broken ride-along; "
                   "it rides along too", err=True)
        click.echo(f"  box now    : {len(resident)} in new/"
                   + (f"; {len(given)} id(s) already given to this session, not paged "
                      "again inside the reminder window" if given else ""), err=True)
        # Name them, and what may never have been shown.
        try:
            for line in given_lines(seat, session, given, box_path, queue):
                click.echo(line, err=True)
        except Exception as exc:  # noqa: BLE001 - names cost a line, never the arm
            click.echo(f"  given      : could not be named - {exc!r}", err=True)
        if queue is not None:
            waiting = len(queue_listing())
            done_dir = queue.parent / _seat.QUEUE_DONE
            done_count = len(os.listdir(done_dir)) if done_dir.is_dir() else 0
            click.echo(f"  queue      : {queue.parent} - {waiting} waiting, {done_count} given; "
                       "arrivals are linked here for the ride-along, the page is its age",
                       err=True)
        else:
            click.echo("  queue      : NONE - no session id and no seat, so nothing to key "
                       "a queue on: paging on the box's own new/", err=True)
        click.echo(f"  poll       : every {interval:g}s behind the watch (kqueue on new/)",
                   err=True)
        if monitor is not None:
            click.echo(f"  monitor    : ONE stdout line per page, at least {monitor.spacing:g}s "
                       "apart; never exits on a page. `pager mute` silences it, `pager "
                       "unmute` resumes; `pager off`/`stop` refuse on it; only `pager stop "
                       "--kill` ends it, for good (Claude Code does not restart a "
                       "monitor) - then listen with the bare `pager listen`"
                       + ("; --list is ignored (one line per page)"
                          if page_list_on(listing) else ""), err=True)
        if seat is not None:
            click.echo(f"  seat       : {seat.name} (from {seat.source}), session "
                       f"{session or '-'} - the queue, the stamps and the marks are under "
                       f"{_seat.session_root(session) or seat.root}", err=True)
        else:
            click.echo("  seat       : NONE - nothing in the environment says who this is: "
                       "no listening stamp, no seen marks, page age = running", err=True)
        for line in moved.lines:
            click.echo(f"  state      : {line}", err=True)
        # Design A6 / the spec's *A line the parser does not know*: a bad
        # line present at arm is named here, file and line, and recorded as
        # named, so the ride-along does not name it a second time. D7: the
        # focus mode and the rule count. GUARDED: a bug
        # in the rules reader costs this banner a line, never the arm - each
        # mail is judged as it comes, and one that cannot be judged wakes.
        try:
            at_arm = live.current()
        except Exception as exc:  # noqa: BLE001 - see above
            click.echo(f"  rules      : could not be read - {exc!r}; each mail is judged as "
                       "it arrives, and one that cannot be judged wakes", err=True)
        else:
            click.echo(f"  filters    : {filters_line(at_arm)}", err=True)
            for error in at_arm.errors:
                click.echo(f"  rules      : {error} - that line is skipped, the rest stand",
                           err=True)
            if has_session:
                record_named(session, {str(e) for e in at_arm.errors})

    if seat is not None:
        # "Is the pager armed?": pid in, removed on every
        # exit - the finally runs through ctx.exit too.
        if held is not None:
            # A signal before the wait's `finally` still clears it.
            # Armed BEFORE the write: there is no instant with a
            # stamp and no clear, so a signal at startup leaves no stamp
            # naming a dead pid.
            pid = os.getpid()
            held["clear"] = lambda: _seat.clear_listening(seat, session, pid)
        _seat.write_listening(seat, session, os.getpid())
        if monitor is not None:
            # This session HAD a monitor. If it is gone later,
            # the deaf check says it was ended, not that it never armed.
            try:
                _seat.touch(seat, _seat.MONITOR_ARMED_STAMP, session)
            except (OSError, _seat.NoIdentity) as exc:
                log_error("monitor-armed stamp", exc)
    if has_session:
        # Tasks 4.2: one "listening" mail per arm, to this
        # session's own box, subject only. It carries `Pager-Note:`, so the
        # judgement below lets it ride and never pages on it, whatever the
        # rules say. A courtesy, never a condition: a send that fails is
        # named and the listener listens exactly as before.
        try:
            mid = confirm_listening(session, box if box is not None else session,
                                    from_=seat.address if seat is not None else session)
        except Exception as exc:  # noqa: BLE001 - see above
            log_error("listening mail", exc)
            said = f"listening mail not sent - {exc}"
        else:
            said = f"listening mail sent {mid} - it rides along, never pages"
        if not quiet:
            click.echo(f"  confirm    : {said}", err=True)
    if has_session and moved.noteworthy:
        # The move's line to the session's BOX, not only stderr (3.3a, call
        # 3): a subject-only note that rides along and never pages, as the
        # confirm does. A courtesy, never a condition.
        try:
            mid = send_note(NOTE_MOVED, session, "pager: mail state - " + "; ".join(moved.lines),
                            from_=seat.address if seat is not None else session)
        except Exception as exc:  # noqa: BLE001 - see the confirm above
            log_error("state mail", exc)
            said = f"state mail not sent - {exc}"
        else:
            said = f"state mail sent {mid} - it rides along, never pages"
        if not quiet:
            click.echo(f"  state mail : {said}", err=True)
    deadline = None if timeout is None else time.monotonic() + timeout
    ignore: set[str] = set()

    def timed_out() -> None:
        if not quiet:
            click.echo(f"pager listen: returned - nothing paged in {timeout:g}s", err=True)
            click.echo(PAGE_NOTHING)
        ctx.exit(EX_TIMEOUT)

    def wakeable(entries: list,
                 verdicts: "dict[str, rules.Verdict | None] | None" = None) -> list:
        """The entries that may ring the doorbell NOW: those whose address has
        not already been woken for inside the coalescing window. The rest are
        not dropped and not filtered - they stay in the queue and the
        ride-along shows them as one grouped desk line.

        The window applies only while the SESSION is running. An idle session is
        making no tool calls, so nothing rides along and a held entry is
        simply unheard: on a live call the session
        sat idle on the pager while fourteen of the caller's turns landed
        inside one window, and the caller hung up. Idle rings, every time:
        if the agent is stopped, it pages immediately
        (changes/idle-rings-through-burst). `idle with children`
        is idle here too (changes/idle-with-children): the turn is
        over, so no ride-along comes, and a live child may take it.

        An `at_once` mail is never held: it pages at T = 0
        and the burst window, which only spares a running seat a second
        doorbell for an address that already rang, is not its to hold back."""
        if seat is None:
            return entries
        if _seat.is_idle(session) is True:
            return entries
        window = _seat.burst_seconds()
        if window <= 0:
            return entries
        ringing = []
        for a in entries:
            key = _address_key(a)
            # AN UNATTRIBUTABLE MESSAGE ALWAYS RINGS. The window's claim is
            # "THIS ADDRESS already woke you for this burst"; with no
            # `X-Original-To` there is no address, so the claim cannot be
            # made and the wake is not ours to withhold. Grouping the
            # unattributable together is right for the DESK LINE - they show
            # as one "(no X-Original-To)" group - but coalescing on it would
            # let one unattributable message silence every unrelated one for
            # the whole window. Unknown identity fails toward waking.
            if (not key or at_once(verdicts.get(a.name) if verdicts else None)
                    or not _seat.in_burst(seat, session, key, window)):
                ringing.append(a)
        return ringing

    pages = [0]                          # pages said, for the exit row
    # The log-lock deadline of the exit under way, when a page set
    # one before `ctx.exit`; the `finally` takes a fresh one otherwise.
    exit_by: list = [None]

    def page(entries: list, verdicts: "dict[str, rules.Verdict | None]",
             promoted: "set[str] | frozenset[str]" = frozenset(),
             reminders: "dict[str, bool] | None" = None) -> None:
        # REMINDERS: entry name -> was it `read`, for each entry
        # this session was given before and has not marked done. None or
        # empty: new mail only, and the page text is today's.
        # Which stamp chose T (the first thing someone debugging a page will
        # want). Printed beside the page AND written into the mark's reason (so
        # the page is logged where it is read:
        # every page?" - it is, one row each, and the row now says why):
        # at 2 a.m. nobody is reading stderr, they read the marks file the
        # morning after. The page text itself stays one text in one place.
        t_now = threshold()
        # A4 (P-3): an honoured urgent pages a RUNNING seat at T = 0. The
        # reason is named, so the marks say the page did not wait its age.
        urgent = t_now > 0 and any(at_once(verdicts.get(a.name)) for a in entries)
        chose = "urgent" if urgent else ("idle" if t_now == 0 else "running")
        # An idle verdict on a seat whose children still run
        # says so, in the page line and in the mark the morning after reads.
        children = (chose == "idle" and seat is not None and has_session
                    and _seat.seat_state(session) == _seat.IDLE_WITH_CHILDREN)
        named = _seat.IDLE_WITH_CHILDREN if children else chose
        why = ("pager wake (urgent at once T=0s)" if urgent
               else f"pager wake ({named} T={t_now:g}s)")
        pages[0] += 1                    # the exit row's `pages`
        paged_by = ""
        if not quiet:
            if urgent:
                paged_by = (f"pager listen: paged at once by an honoured urgent (T=0s; "
                            f"a running seat waits {t_now:g}s for anything else)")
            else:
                paged_by = (f"pager listen: paged by the {chose} "
                            f"{'verdict' if chose == 'idle' else 'setting'} "
                            + (f"({CHILDREN_RULE}) " if children else "")
                            + f"(T={t_now:g}s)")
            click.echo(paged_by, err=True)
        if not quiet and monitor is None:
            # THE QUIET PAGE: a count, never a manifest. Nothing about who
            # the mail is from or what it says - the re-arm's ride-along
            # carries that.
            groups = waiting_groups(box_path, entries, seat, session, _maildir)
            # The page carries the action that applied, as the deciding
            # rule and its layer - never who or what (the quiet page).
            woke_by = why_lines([verdicts.get(a.name) for a in entries],
                                [a.name in promoted for a in entries])
            rem = {a.name: reminders[a.name] for a in entries
                   if reminders and a.name in reminders}
            if rem:
                # Part of item 2 (what is waiting), never a
                # fifth item - the re-arm command still comes first.
                woke_by.append(reminder_line(len(rem), reminder_state(rem)))
            # Collected, then echoed below, so the `listen` row is
            # sized on exactly these stdout lines (reminder line included).
            out = list(quiet_page(len(entries), seat, session, groups=groups, why=woke_by))
            # THE SWITCH (3.4). The lines come UNDER the four things, not
            # instead of them: items 1-4 are SHALL, and the reader who
            # asked for detail still needs the re-arm command first. The
            # order is OLDEST FIRST - `entries` arrives sorted that way
            # from both paths (the queue sorts on the link's mtime; the
            # detector returns oldest first, `_detect.py:34`).
            if page_list_on(listing):
                out.extend(_arrival_line(box_path.name, a, _maildir) for a in entries)
            for line in out:
                click.echo(line)
            # The hand page's row is its stdout
            # lines joined with `\n`; the stderr "paged by" line is sized
            # apart, in `stderr_chars`. After the print; never raises.
            # The page is on its way out (`ctx.exit` below), so its
            # row and the `finally`'s `exit` row share ONE lock deadline.
            exit_by[0] = _injectlog.exit_deadline()
            _injectlog.write(session, via=_injectlog.VIA_LISTEN, s="\n".join(out),
                             seat=seat, ride=_injectlog.Ride(items=_page_items(entries)),
                             ms=_arrival_ms(entries), extra={"stderr_chars": len(paged_by)},
                             deadline=exit_by[0])
        if seat is not None:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            for a in entries:
                if a.message_id:
                    _seat.write_mark(seat, a.message_id, "seen", session, stamp, why)
            # When a paged verdict
            # CARRIES, the stamp that lets the wake turn's UserPromptSubmit run
            # the ride-along, written AFTER the marks. The listener still
            # takes, moves and files nothing; the page text is unchanged.
            if any(getattr(verdicts.get(a.name), "carry", False) for a in entries):
                try:
                    _seat.write_carry(seat, session)
                except (OSError, _seat.NoIdentity) as exc:
                    log_error("carry stamp", exc)  # no carry: the re-arm's ride-along brings it
            # ONE WAKE PER BURST: remember which addresses this wake covered,
            # so the rest of a call's chunks ride the desk line instead of
            # ringing the doorbell again. Recorded AFTER the marks, so a
            # crash mid-page cannot silence an address it never paged for.
            for key in {_address_key(a) for a in entries} - {""}:
                try:
                    _seat.write_woke(seat, session, key)
                except OSError as exc:
                    log_error("woke record", exc)
        if monitor is not None:
            # ONE line, AFTER the marks, the carry stamp and the woke
            # records - the line starts the wake turn, whose UserPromptSubmit
            # claims the carry stamp, so the stamp must already be there.
            # Then back to the wait: no exit, nothing to re-arm.
            monitor.say(monitor_line(entries, verdicts, promoted,
                                     fresh=_ride_fresh(seat, session)
                                     if has_session else False,
                                     reminders=reminders),
                        items=_page_items(entries), ms=_arrival_ms(entries))
            return
        ctx.exit(EX_PAGE)

    # Only a monitor follows (a bare listen returns on a page
    # and its re-arm reads the new id), and only a session can be followed.
    follow = monitor is not None and has_session
    # Spec "The listener logs its own life": these rows, not `marks`, carry
    # it, and `marks` is unchanged. `start` once armed;
    # `exit` on the way out, through the same `finally` that clears the
    # `listening` stamp. A SIGKILL leaves no `exit`, and that is the signal.
    def life(event: str, new: str) -> None:
        # The follow's exec (`follow_session`) skips the `finally` below: the
        # session it LEAVES gets its `exit` here, naming where it went; a
        # failed exec stays, and starts again under SESSION.
        if event == "exit":
            # Bounded, a fresh deadline per attempt (a failed exec
            # stays and listens on, so it carries nothing to a later exit).
            _injectlog.exit_row(session, via=via, seat=seat, code=None,
                                pages=pages[0], followed=new)
        else:
            _injectlog.lifecycle(session, event="start", via=via, seat=seat,
                                 box=str(box_path), timeout=timeout)

    _injectlog.lifecycle(session, event="start", via=via, seat=seat,
                         box=str(box_path), timeout=timeout)
    code: list = [None]
    try:
        if queue is None:
            # A4 (P-3): `detect` blocks until the OLDEST arrival is T old, and
            # cannot tell an honoured urgent from the rest. So the age it is
            # handed is T - or 0 while an at_once mail is resident, judged
            # here, once, as it is first seen (the verdicts are kept and
            # reused below, so a mail is judged and recorded once).
            early: "dict[str, rules.Verdict]" = {}

            def urgent_resident() -> bool:
                try:
                    names = [n for n in os.listdir(box_path / "new") if n not in ignore]
                except OSError:
                    return False
                for stale in set(early) - set(names):
                    del early[stale]
                found = False
                for n in names:
                    if n not in early:
                        v = arrival_verdict(box_path / "new" / n, live, me,
                                            session=note_session)
                        if v is None:
                            continue
                        early[n] = v
                    if at_once(early[n]):
                        mid = _detect._message_id(box_path / "new" / n)
                        if not (mid and mid in given_now()):
                            found = True
                return found

            def box_threshold() -> float:
                return 0.0 if urgent_resident() else threshold()

            while True:
                if follow:
                    follow_session(session, parent, seat, followed, life=life)
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if follow:
                    # A monitor has no deadline, so `detect`
                    # would block until mail; it returns each interval
                    # instead, so the sidecar is read every tick.
                    remaining = interval
                try:
                    arrivals = _detect.detect(box_path, poll=interval, timeout=remaining,
                                              older_than=box_threshold, parent=parent,
                                              ignore=ignore)
                except _detect.Timeout:
                    if follow:
                        continue           # a tick: look at the sidecar again
                    timed_out()
                    return
                except _detect.CallerGone:
                    stop("the caller went; returning rather than waiting alone", EX_CALLER_GONE)
                    return
                except (_detect.NoSuchBox, OSError) as exc:
                    if monitor is None:
                        raise
                    stop(f"could not look at {box_path}: {exc}", EX_COULD_NOT_LOOK)
                    return
                given = given_now()
                fresh = [a for a in arrivals if not a.message_id or a.message_id not in given]
                # 4.1: judged first. THIS path has no queue, so nothing rides along from it - a
                # ride here would never be heard. So a ride PAGES: fail
                # toward waking. `$me` is unchanged (D2); the page says the
                # ride was promoted. A listener with a queue (below) keeps
                # ride meaning ride.
                verdicts = {a.name: early.get(a.name)
                            or arrival_verdict(a.path, live, me, session=note_session)
                            for a in fresh}
                promoted = {n for n, v in verdicts.items() if promotes(v)}
                # 5.3: each verdict is recorded where the marks live, keyed
                # by Message-ID, so `pager why` answers after the fact.
                for a in fresh:
                    record_verdict(seat, session, a.message_id, verdicts[a.name],
                                   LISTEN_BY, promoted=a.name in promoted, layers=live.last)
                waking = [a for a in fresh
                          if verdicts[a.name] is not None
                          and (verdicts[a.name].action in WAKE_ACTIONS
                               or a.name in promoted)]
                ringing = wakeable(waking, verdicts)
                if not ringing:
                    ignore.update(a.name for a in arrivals)
                    continue
                page(waking, verdicts, promoted)
                if monitor is not None:
                    # No queue here, so no marks to skip them by: what
                    # this page saw is not looked at again.
                    ignore.update(a.name for a in arrivals)
                    continue
                return

        mids: dict[str, str] = {}      # queue name -> Message-ID, read once
        # queue name -> its verdict (tasks 4.1), judged ONCE, at its first
        # sighting in the queue: its arrival, by the rules as they are then.
        judged: "dict[str, rules.Verdict]" = {}
        # The queue names a monitor has paged. A mail with a
        # Message-ID is kept from paging twice by its `seen` mark (`given`);
        # one without has no mark to find, so its name is remembered here.
        paged: set[str] = set()
        while True:
            now_m = time.monotonic()
            if deadline is not None and now_m >= deadline:
                timed_out()
                return
            if follow:
                # Every tick, before anything is judged or paged
                # under a session claude has left.
                follow_session(session, parent, seat, followed, life=life)
            listing_now = queue_listing()
            if monitor is not None:
                # The monitor never returns, so `given` is re-read every
                # tick: a mail paged and never scooped pages again once the
                # reminder window has passed, as a re-arm would have paged
                # it. And what the ride-along took is forgotten.
                marks_now = quiet_now()
                given = marks_now.given
                present = set(listing_now)
                for kept in (mids, judged):
                    for gone in set(kept) - present:
                        del kept[gone]
                paged &= present
            # What is waiting in the queue that WAKES, less what this session
            # was already paged with inside the window, and less what it
            # marked `done` ("Done is terminal", whether or not a
            # scoop ever drains the link); oldest first by the inode's
            # mtime - delivery time, whenever it was linked.
            waiting = []
            for name in listing_now:
                path = queue / name
                if name not in mids:
                    mids[name] = _detect._message_id(path)
                if mids[name] and (mids[name] in given or mids[name] in marks_now.done):
                    continue
                if not mids[name] and name in paged:
                    continue
                if name not in judged:
                    verdict = arrival_verdict(path, live, me, session=note_session)
                    if verdict is None:
                        continue           # scooped before it could be judged
                    judged[name] = verdict
                    # 5.3: recorded as it was decided (see `record_verdict`).
                    record_verdict(seat, session, mids[name], verdict, LISTEN_BY,
                                   layers=live.last)
                if judged[name].action not in WAKE_ACTIONS:
                    # RIDE ONLY: it never pages, and it is not part of the
                    # age that decides a page. It stays in the queue, and the
                    # next tool call's ride-along carries it (tasks 4.1).
                    continue
                try:
                    waiting.append((path.stat().st_mtime, name, path))
                except FileNotFoundError:
                    continue               # scooped between the listing and the stat
            waiting.sort()
            step = interval
            if waiting:
                age = time.time() - waiting[0][0]
                t_now = threshold()
                # A4 (P-3): an honoured urgent among them is due at T = 0.
                if muted():
                    # The `off` stamp mutes
                    # whatever listens. A muted listener HOLDS: nothing is
                    # paged, marked or said, the links stay in the queue for
                    # the ride-along, and it looks again each interval, so
                    # `pager unmute` resumes paging with no re-arm.
                    step = interval
                elif age >= t_now or any(at_once(judged[n]) for _, n, _p in waiting):
                    ready = [_detect.Arrival(n, mids[n], p) for _, n, p in waiting]
                    if wakeable(ready, judged):
                        # A mail this session
                        # was given before, not done, pages as a REMINDER.
                        page(ready, judged, reminders={
                            n: mids[n] in marks_now.read for _, n, _p in waiting
                            if mids[n] and mids[n] in marks_now.seen})
                        if monitor is not None:
                            paged.update(n for _, n, _p in waiting)
                            marks_now = quiet_now()
                            given = marks_now.given
                            continue
                        return
                    # every address here is mid-burst: no wake, and the links
                    # stay in the queue for the ride-along's grouped line.
                    # Tick once per `interval`. NOT `t_now - age`, which is
                    # <= 0 in this branch: a zero timeout returns from
                    # `detect` at once, and the loop re-lists the queue,
                    # re-parses every message and forks `session get idle`
                    # with no sleep, for as long as the burst window lasts.
                    step = interval
                else:
                    # not old enough to consider yet: sleep until it is, and
                    # at most one interval, so T can be re-read.
                    step = min(interval, max(0.0, t_now - age))
            if deadline is not None:
                step = min(step, max(0.0, deadline - now_m))
            try:
                arrivals = _detect.detect(box_path, poll=interval, timeout=step,
                                          parent=parent, ignore=linked)
            except _detect.Timeout:
                continue                   # a tick: re-read T and the queue's age
            except _detect.CallerGone:
                stop("the caller went; returning rather than waiting alone", EX_CALLER_GONE)
                return
            except (_detect.NoSuchBox, OSError) as exc:
                if monitor is None:
                    raise
                stop(f"could not look at {box_path}: {exc}", EX_COULD_NOT_LOOK)
                return
            fresh = [a for a in arrivals if a.name not in linked]
            if fresh:
                queue_link(fresh)
                linked.update(a.name for a in fresh)
                marks_now = quiet_now()
                given = marks_now.given
    except click.exceptions.Exit as exc:   # ctx.exit: a page, a timeout, a stop
        code[0] = exc.exit_code
        raise
    except SystemExit as exc:              # the monitor's signal handler (D5)
        code[0] = exc.code
        raise
    finally:
        try:
            if seat is not None:
                _seat.clear_listening(seat, session, os.getpid())
        finally:
            # The session it ends under: a monitor that followed claude to a
            # new id exits there. Never raises, and never waits on
            # the log past ONE deadline: a page's, or a fresh one.
            _injectlog.exit_row(_seat.session_id(), via=via, seat=seat,
                                deadline=exit_by[0], code=code[0], pages=pages[0])


# ── the judgement before the page (tasks 4.1) ────────────────────────────────

#: The actions that ring the doorbell. `start` implies `wake` (specs, "Each
#: mail gets one action"); the listener never STARTS anyone - an absent agent
#: has no listener - so to a live listener a start is a wake.
WAKE_ACTIONS = ("wake", "start")
#: The most of one mail read to judge it. The judgement reads headers only
#: (`rules.Facts`), so the read stops at the blank line, or here.
HEAD_LIMIT = 256 * 1024
#: Beside the stamps: the bad rules lines already NAMED to this session,
#: one `RuleError` per line as it prints (*"named ... in the
#: banner if present at arm, and otherwise once, in the next ride-along line
#: after it appears"*). The banner and the ride-along both read and write it.
RULES_NAMED = "rules-named"


def at_once(verdict: "rules.Verdict | None") -> bool:
    """Does
    VERDICT page a running seat AT ONCE? Only an honoured urgent given
    `wake` does - `Verdict.at_once`, which `rules.judge` computes from WHICH
    line decided and who sent it (N3), so a stranger's `Priority: urgent` is
    not one. WHY it pages a busy seat instead of waiting the page age:
    urgent is the BACKSTOP for a broken ride-along (hooks disabled, a
    ride-along bug) - the one mail that must not wait cannot depend on the
    thing that may be broken. It still rides along too; every other mail
    keeps the running page age."""
    return bool(verdict is not None and verdict.at_once and verdict.action in WAKE_ACTIONS)


def _head(handle) -> bytes:
    """The header block of the open mail HANDLE, or its first HEAD_LIMIT bytes."""
    out = b""
    while len(out) < HEAD_LIMIT:
        chunk = handle.read(16384)
        if not chunk:
            break
        out += chunk
        if b"\n\n" in out or b"\r\n\r\n" in out:
            break
    return out


def listener_me(seat: "_seat.Seat | None", session: str) -> "rules.Me":
    """Who `$me` is to this listener: the seat's full address (the `+`
    kept) and the session id, on this host (`host_forms`)."""
    from . import _voice, rules
    return rules.Me.of(session=session if session not in ("", "-") else "",
                       seat=seat.address if seat is not None else "",
                       host=_voice.host_forms()[-1])   # the FQDN; the short host
                                                       # alone when no suffix is known


#: The layers a verdict names for a confirmation mail and for a
#: mail that could not be judged: defined in `_why`, beside the words that
#: say them, and re-exported here.
NOTE_LAYER = _why.NOTE_LAYER
UNJUDGED = _why.UNJUDGED


def _note_verdict() -> "rules.Verdict":
    from . import rules
    return rules.Verdict("ride", None, NOTE_LAYER, None)


def _unjudged() -> "rules.Verdict":
    from . import rules
    return rules.Verdict("wake", None, UNJUDGED, None)


def _may_be_note(head: bytes) -> bool:
    """Could the header block HEAD carry a `Pager-Note` in either spelling?

    NOT a reader, and it decides nothing: a cheap SUPERSET test, so that
    `arrival_verdict` reads a body (up to NOTE_LIMIT) only for a mail that
    might be a note, and `genuine_note` - the one reader -
    decides. A field name is ASCII and never folded, so every header the
    reader could take contains these bytes, case aside; a false hit (the
    words in some other header's value) costs one read and changes no
    verdict. It replaces `pager_note`, the unproven claim reader (swap-001)."""
    # `Pager-Note:` and `X-Pager-Note:` both END in these bytes (D16).
    return PAGER_NOTE.lower().encode("ascii") + b":" in head.lower()


def judge_head(head: bytes, layers, me: "rules.Me", arrived: datetime,
               *, mail: bytes, session: str = "",
               signed: "Callable[..., bool] | None" = None) -> "rules.Verdict":
    """The verdict for one mail's header block HEAD (tasks 4.1, 4.2).

    The pager's OWN confirmation rides, and the rules never see it: A7,
    *"the listener checks Pager-Note BEFORE the rules and never pages on
    it"*, so not even a `wake!` or a session's `wake *` pages on it. But
    only a note PROVEN to be the pager's own, to SESSION (`genuine_note`,
    PAGER-NOTE HARDENED): a `Pager-Note:` anyone typed
    is ignored, and the rules decide that mail like any other. Every other
    mail is `judge`d at its ARRIVAL time by LAYERS (the `Layers` object,
    never a plain list), or by `live.current()` when
    LAYERS is a `rules.LiveLayers`. Raises what the rules raise;
    the callers decide what an error means. MAIL is the whole message the
    note is proven over (the proof covers its body too), and it is
    REQUIRED: with a default of HEAD alone
    the body hash was taken over an empty body and a body-swapped lift of a
    genuine note passed. A caller with only a header block must say so by
    passing it as MAIL, and then owns what that proves. SIGNED is the
    keeper-signature verdict seam over the whole file (`_voice.signed_file`):
    without it a voice mail is UNVERIFIED, so its calls are clamped."""
    from . import rules
    if genuine_note(mail, session, arrived) is not None:
        return _note_verdict()
    if isinstance(layers, rules.LiveLayers):
        layers = layers.current()
    return rules.judge(rules.Facts.from_bytes(head, signed=signed), layers, me, arrived)


def arrival_verdict(path: Path, layers, me: "rules.Me",
                    *, session: str = "") -> "rules.Verdict | None":
    """Tasks 4.1: the verdict for the mail at PATH, by `judge_head` over its
    headers, at its ARRIVAL time (the inode's mtime: the only clock a focus
    mode's end is checked by, 3.3).

    None when the mail is gone (a scoop took it first). A mail that cannot
    be read or judged gets `UNJUDGED`, a WAKE: a listener that raised here
    would be deaf after a healthy banner, and unknown fails toward waking (as
    `wakeable` treats a mail with no address). A Pager-Note mail still rides
    when the RULES cannot be read: the note is checked first.
    SESSION is whose secret proves a note; empty, none is genuine."""
    try:
        with path.open("rb") as handle:
            arrived = datetime.fromtimestamp(os.fstat(handle.fileno()).st_mtime).astimezone()
            head = _head(handle)
            # Only a mail that CLAIMS to be a note is read on: its proof
            # covers its body, and a note is small (NOTE_LIMIT).
            mail = head
            if len(head) <= NOTE_LIMIT and _may_be_note(head):
                mail = head + handle.read(NOTE_LIMIT + 1 - len(head))
    except FileNotFoundError:
        return None
    except OSError:
        return _unjudged()
    from . import _voice
    try:
        return judge_head(head, layers, me, arrived, session=session, mail=mail,
                          signed=_voice.signed_file(path))
    except Exception:  # noqa: BLE001 - see the docstring: fail toward waking
        return _unjudged()


def arrival_wakes(path: Path, live: "rules.LiveLayers", me: "rules.Me",
                  *, session: str = "") -> bool | None:
    """Does the mail at PATH ring the doorbell? `arrival_verdict`'s action
    is a wake or a start (None: the mail is gone)."""
    verdict = arrival_verdict(path, live, me, session=session)
    return None if verdict is None else verdict.action in WAKE_ACTIONS


def action_text(verdict: "rules.Verdict | None") -> str:
    """What a ride-along line says about VERDICT (D7): its action, or `ride:
    pager note` for a confirmation mail; empty when there is no verdict to
    report (not judged), so a line never claims an action it cannot know."""
    if verdict is None or verdict.layer == UNJUDGED:
        return ""
    if verdict.layer == NOTE_LAYER:
        return "ride: pager note"
    return shown_action(verdict)


def shown_action(verdict: "rules.Verdict") -> str:
    """The word a line shows for VERDICT's action: `carry` for
    a verdict that carries - its action is still `wake`, so everything that
    DECIDES reads `.action` - else the action itself. Every wake carries
    now, so a
    `wake` line's verdict shows `carry` here too, unless `set carry off`."""
    return "carry" if getattr(verdict, "carry", False) else verdict.action


def page_action(verdict: "rules.Verdict") -> str:
    """The word the PAGE shows for VERDICT's action (`why_lines`): `carry`
    only for a verdict that carries AND whose deciding line is WRITTEN as a
    carry (`carry`, `carry!`); else the action itself.

    The page text is untouched. Once a `wake` line's
    verdict never carried, so its page said `wake`; it carries now, and
    `shown_action` would say `carry`. Keyed on the line as written, the
    page says exactly what it said before for every line and every
    `set carry` (the quiet page, specs/pager), while `pager why` and the
    ride-along line say that the wake carried."""
    from . import rules
    if not getattr(verdict, "carry", False):
        return verdict.action
    verb = (verdict.rule or "").split(maxsplit=1)[:1]
    return "carry" if verb and verb[0] in rules.CARRY_VERBS else verdict.action


def promotes(verdict: "rules.Verdict | None") -> bool:
    """On a listen path with NO
    ride-along, is VERDICT a ride that must page instead? Every `ride`,
    an explicit user `ride` line included (bound 3) - except the pager's
    own confirmation, which never pages, whatever path it lands on.

    Drafts are excluded, and none can reach this path in
    `arrival_verdict` builds `Facts.from_bytes`, which never sets
    `draft`, and nothing in `src/` does (only a later draft watch will).
    Recorded rather than built for here."""
    return (verdict is not None and verdict.action == "ride"
            and verdict.layer != NOTE_LAYER)


def why_lines(verdicts: "list[rules.Verdict | None]",
              promoted: "list[bool] | None" = None) -> list[str]:
    """What the PAGE says woke it (D7): one line per deciding rule, in the
    order met, at most `WHY_MAX`. A rule and its layer, never a sender or a
    subject: the page stays a count (specs/pager, the quiet page). PROMOTED,
    parallel to VERDICTS, marks a ride that paged because this listener has
    no ride-along: its line says so. The action word is `page_action`'s, so
    every page stays byte-identical now that every wake carries (the page
    text is untouched)."""
    flags = list(promoted or ()) + [False] * len(verdicts)
    seen: list[tuple] = []
    for v, flag in zip(verdicts, flags):
        if v is None:
            continue
        key = (page_action(v), v.rule, v.layer, flag)
        if key not in seen:
            seen.append(key)
    out = []
    for action, rule, layer, flag in seen[:WHY_MAX]:
        if flag:
            by = f"by `{rule}`" if rule is not None else "- no rule matched"
            out.append(f"pager: wake, promoted from {action} {by} ({layer}) - this "
                       "listener has no ride-along to carry it")
        elif layer == UNJUDGED:
            out.append(f"pager: {action} - it could not be judged, so it woke: an unknown "
                       "wakes")
        elif rule is None:
            out.append(f"pager: {action} - no rule matched ({layer})")
        else:
            out.append(f"pager: {action} by `{rule}` ({layer})")
    if len(seen) > WHY_MAX:
        out.append(f"pager: and {len(seen) - WHY_MAX} more rules")
    return out


#: The most deciding rules the page names before "and N more".
WHY_MAX = 3


# ── the recorded verdicts (tasks 5.3) ────────────────────────────────────────

#: Who recorded a verdict: the listener, at the mail's first sighting, or
#: the ride-along, for a mail no listener had judged.
LISTEN_BY = "pager listen"
RIDE_BY = "ride-along"
#: The most bad rules lines one record keeps (they are named elsewhere too).
BAD_MAX = 5


#: The one rule for a Message-ID, now in `_why` so `message`
#: shares it without importing the pager.
_mid = _why.mid


def record_verdict(seat: "_seat.Seat | None", session: str, message_id: str,
                   verdict: "rules.Verdict | None", by: str, *,
                   promoted: bool = False, layers=None) -> bool:
    """Record VERDICT for MESSAGE_ID where the marks live (tasks 5.3: "so
    why works after the fact"), as it was DECIDED: its action, rule, layer
    and line, `at_once`, who judged it (BY), whether a no-ride-along path
    PROMOTED it to a page, and the bad lines LAYERS skipped.

    True when written. Never raises: a verdict that cannot be recorded costs
    `pager why` its answer, never the listener or the ride-along its work.
    A mail with no Message-ID has no key, so it is not recorded."""
    mid = _mid(message_id or "")
    if not mid or verdict is None:
        return False
    record = {"stamp": datetime.now().astimezone().isoformat(timespec="seconds"),
              "message_id": mid, "action": verdict.action, "rule": verdict.rule,
              "layer": verdict.layer, "line": verdict.line,
              "at_once": bool(verdict.at_once), "by": by}
    if getattr(verdict, "carry", False):
        record["carry"] = True          # written only when true
    if promoted:
        record["promoted"] = True
    try:
        bad = [str(e) for e in getattr(layers, "errors", ())][:BAD_MAX]
        if bad:
            record["bad"] = bad
        _seat.write_verdict(seat, session, record)
    except Exception:  # noqa: BLE001 - see the docstring
        return False
    return True


def verdicts_for(seat: "_seat.Seat | None", session: str,
                 message_id: str) -> list[dict]:
    """Every verdict recorded for MESSAGE_ID in SESSION, oldest first."""
    mid = _mid(message_id)
    return [r for r in _seat.read_verdicts(seat, session) if r.get("message_id") == mid]


def latest_verdicts(seat: "_seat.Seat | None", session: str) -> dict[str, dict]:
    """Message-ID -> the LAST verdict recorded for it in SESSION: the one in
    force when the ride-along carries the mail (a re-arm re-judges a mail
    still queued, and the later listener's verdict is the later decision)."""
    out: dict[str, dict] = {}
    for record in _seat.read_verdicts(seat, session):
        out[record["message_id"]] = record
    return out


def verdict_of(record: dict) -> "rules.Verdict":
    """A recorded verdict as a `rules.Verdict` again (what `action_text`
    reads)."""
    from . import rules
    return rules.Verdict(record.get("action") or "ride", record.get("rule"),
                         record.get("layer") or rules.FALLBACK, record.get("line"),
                         bool(record.get("at_once")), bool(record.get("carry")))


def page_lines(seat: "_seat.Seat | None", session: str, mid: str) -> list[str]:
    """`paged at <stamp>: <reason>` for each page of MID this session's marks
    record (the `seen` mark a page writes, reason `pager wake (...)`). Empty
    when there is none or the marks cannot be read."""
    try:
        events = _seat.read_mark_events(seat, session)
    except (_seat.NoIdentity, OSError):
        return []
    return [f"paged at {m.stamp}: {m.reason}" for m in events
            if m.message_id == mid and m.mark == "seen" and m.reason.startswith("pager wake")]


#: How one recorded verdict is said, now in `_why`: `message`
#: says a verdict in the same words without importing the pager.
why_text = _why.why_text


_WHY_KEY = ("action", "rule", "layer", "line", "at_once", "promoted", "carry")


# ── what the agent sees (tasks 4.3, D7) ─────────────────────────────────────

#: The layers the banner counts, in the order it names them (D7's example
#: names the seat first: `rules 4 (seat 3, session 1)`). The built-in layer
#: and the focus modes' own lines are not the agent's rules, so they do
#: not count; the focus mode is named on its own.
COUNTED_LAYERS = ("locked", "seat", "session", "env")


def until_text(until: datetime | None, now: datetime | None = None) -> str:
    """An end time as a person reads it: `07:00` within the next day, else
    the date too. Empty for no end time."""
    if until is None:
        return ""
    now = datetime.now().astimezone() if now is None else now
    ahead = until - now
    if timedelta(0) <= ahead < timedelta(days=1):
        return until.strftime("%H:%M")
    return until.strftime("%Y-%m-%d %H:%M")


def focus_text(mode: str, until: datetime | None, now: datetime | None = None) -> str:
    """`sleep until 07:00`, `sleep`, or `off`."""
    from . import rules
    if not mode or mode == rules.OFF:
        return rules.OFF
    return mode + (f" until {until_text(until, now)}" if until is not None else "")


def filters_line(layers: "rules.Layers", now: datetime | None = None) -> str:
    """D7's banner line: `focus: sleep until 07:00; rules 4 (seat 3,
    session 1)`. The focus mode in effect NOW, and the count of the agent's
    own rules, by layer."""
    now = datetime.now().astimezone() if now is None else now
    mode = layers.focus(now)
    focus = focus_text(mode.mode, mode.until, now) if mode is not None else "off"
    counts = [(name, sum(len(layer.rules) for layer in layers if layer.name == name))
              for name in COUNTED_LAYERS]
    total = sum(n for _, n in counts)
    parts = ", ".join(f"{name} {n}" for name, n in counts if n)
    return f"focus: {focus}; rules {total}" + (f" ({parts})" if total else "")


def next_arrival_line(seat: "_seat.Seat", session: str,
                      now: datetime | None = None) -> str:
    """What `pager status` says the next arrival would do, for a mail
    addressed to this seat directly: `a mail To alice@host now: WAKE (default:
    to:$me)`. The layers are read now, as the listener would read them."""
    from . import _voice, rules
    now = datetime.now().astimezone() if now is None else now
    short = _voice.host_forms()[0]
    addr = seat.address
    facts = rules.Facts({"x-original-to": addr, "to": f"{addr}@{short}"})
    verdict = rules.judge(facts, rules.load_layers(seat, session), listener_me(seat, session),
                          now)
    if verdict.rule is None:
        why = "no rule matched"
    elif verdict.layer == rules.BUILT_IN:
        why = "default: " + verdict.rule.split(None, 1)[-1]
    else:
        why = f"{verdict.layer}: {verdict.rule}"
    return f"pager: a mail To {addr}@{short} now: {verdict.action.upper()} ({why})"


# ── the confirmation mails (tasks 4.2) ──────────────────────────────────────

#: The confirmation mails' own header, with NO
#: `X-` prefix. The listener checks it before the rules and never pages on it.
PAGER_NOTE = "Pager-Note"
NOTE_LISTENING, NOTE_FOCUS = "listening", "focus"
#: Session-settings 3.3a: the state move's line, when it says anything but
#: "moved" or "nothing to move" - to the session's box, so an agent whose
#: state did not move learns it at the desk.
NOTE_MOVED = "moved"
#: Design A7: both mails ALSO carry the level, so any other reader sees it.
NOTE_PRIORITY = ("Priority", "non-urgent")

# PAGER-NOTE HARDENED: a `Pager-Note`
# is honoured ONLY when the mail provably came from this pager's own send
# path to this session's own box. `From:` and `X-Session-From:` are both
# forgeable, so the proof is a MAC under a per-SESSION secret that only the
# pager's send path reads: `Pager-Proof: v2 <nonce> <hmac-sha256>`. Every
# seat runs as one unix user, so the bar is "no send tool can pass it without
# deliberately reading another session's private files". Mechanism (i) of
# three, chosen as the cheapest that meets the bar: stdlib only, no
# subprocess per arrival, and it works for every session, where a signature
# (ii) exists only when the session is keyed - an unkeyed arm's own
# confirmation would then fall to `wake to:$me` and page its own seat.
#
# BOUND TO ITS MAIL: an earlier MAC covered the
# subject only, so a proof lifted out of a box (every agent here can read
# every maildir) silenced any mail it was copied onto, the operator's included. The
# MAC now covers the session, the note, the nonce, the Subject, the From, the
# Date, the Message-ID and the SHA-256 of the CANONICAL body, and a proof is
# FRESH only inside a window round its arrival. So what a lifted proof can
# still do is exactly this: make THE SAME message ride again, within 15
# minutes of its Date - the same sender, subject, date, id and body, which
# says only what the confirmation said. There is no once-per-Message-ID
# ledger: a replay of the same message is harmless.
PAGER_PROOF = "Pager-Proof"
PROOF_VERSION = "v2"
#: Beside the session's stamps: `<home>/sessions/<session>/pager-note.key`,
#: 0600, 32 random bytes as hex, made by the first send that needs it.
NOTE_KEY = "pager-note.key"
_KEY_BYTES = 32
#: The freshness window: a proof
#: whose Date is more than NOTE_FRESH before the mail ARRIVED (postfix can
#: defer a mail for minutes), or more than NOTE_SKEW after it (clocks), is no
#: proof. Arrival, not "now": a note is judged when it lands, and a verdict
#: recorded then is kept, so a late ride-along reads the same answer.
NOTE_FRESH = timedelta(minutes=15)
NOTE_SKEW = timedelta(minutes=2)
#: A note is a subject-only mail. One larger than this is not one.
NOTE_LIMIT = 64 * 1024


def note_key_path(session: str) -> Path | None:
    """Where SESSION's pager-note secret lives, or None with no session."""
    root = _seat.session_root(session)
    return None if root is None else root / NOTE_KEY


def _parse_key(data: bytes) -> bytes | None:
    try:
        key = bytes.fromhex(data.decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return key if len(key) == _KEY_BYTES else None


def _publish_key(path: Path) -> None:
    """Make a key at PATH, whole or not at all: the hex is
    written and synced to a temp file beside it, then LINKED into place, so
    no reader ever sees an empty or half-written key and a crash leaves at
    most a stray temp file. `os.link` refuses an existing PATH just as
    O_EXCL did: a racer that loses keeps the winner's key."""
    import secrets
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{NOTE_KEY}.{os.getpid()}.{secrets.token_hex(8)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(_KEY_BYTES) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass                            # another sender made it first: theirs stands
    finally:
        tmp.unlink(missing_ok=True)


def _set_aside(path: Path, inode: int) -> None:
    """Move the BROKEN key at PATH (the file whose inode was read and found
    not a key) out of the way, so the next make replaces it. If another
    sender replaced it in between, the key moved is theirs, and good: it
    is put back unless a newer one already stands."""
    import secrets
    aside = path.with_name(f".{NOTE_KEY}.bad.{os.getpid()}.{secrets.token_hex(8)}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return
    try:
        if os.stat(aside).st_ino != inode:
            try:
                os.link(aside, path)
            except FileExistsError:
                pass
    finally:
        aside.unlink(missing_ok=True)


def note_key(session: str, *, make: bool = False) -> bytes | None:
    """SESSION's secret, or None: no session, no key (and not MAKE), or a
    file that cannot be read. A check with no key proves nothing, so the
    rules decide.

    With MAKE (a SENDER, never the listener's check), a missing key is made,
    and a file that is there but is NOT a key (empty, as a crash under the old
    O_EXCL-then-write left one, or not 32 bytes of hex) is REPLACED: a
    session is never left without confirmations for good. A
    file that cannot be READ is not replaced (it may be a good key); the
    send is refused, and says so, every time."""
    path = note_key_path(session)
    if path is None:
        return None
    for _ in range(4):
        try:
            with path.open("rb") as handle:
                data = handle.read(4096)
                inode = os.fstat(handle.fileno()).st_ino
        except FileNotFoundError:
            if not make:
                return None
            try:
                _seat.make_dir(path.parent, session)
                _publish_key(path)
            except OSError:
                return None
            continue
        except OSError:
            return None
        key = _parse_key(data)
        if key is not None or not make:
            return key
        try:
            _set_aside(path, inode)
        except OSError:
            return None
    return None


def _subject_form(subject: str) -> str:
    """A subject as the proof covers it: RFC 2047 decoded (the sender
    encodes a non-ASCII one), whitespace collapsed (unfolding)."""
    from email.header import decode_header, make_header
    try:
        text = str(make_header(decode_header(subject)))
    except Exception:  # noqa: BLE001 - an undecodable subject is taken as it is
        text = subject
    return " ".join(text.split())


def _from_form(value: str) -> list[str]:
    """A From as the proof covers it: the display name as `_subject_form`
    reads it, and the address as the signature reduces it (`canon_addr`: the
    lowercased local part), because postfix qualifies a bare From with this
    host's domain in transport. Raises on a From that cannot be reduced."""
    from email.utils import parseaddr
    from . import _sender
    name, addr = parseaddr(value)
    if not addr:
        raise ValueError(f"no address in From {value!r}")
    return [_subject_form(name), _sender.canon_addr(addr)]


def _date_form(value: str) -> int:
    """A Date as the proof covers it: the moment, in epoch seconds, however
    it is written. Raises on a Date that is not one."""
    from datetime import timezone
    from email.utils import parsedate_to_datetime
    when = parsedate_to_datetime(value)
    if when is None:
        raise ValueError(f"not a Date: {value!r}")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


def _body_form(payload: bytes) -> bytes:
    """A body as the proof covers it (the CANONICAL form), after its
    transfer encoding is undone: line endings as LF, trailing spaces and tabs
    dropped from each line, trailing blank lines dropped. So a transport that
    re-encodes it (8bit to quoted-printable or base64, LF to CRLF, a padded
    last line) leaves the same form; any change of what it SAYS does not."""
    text = payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return b"\n".join(line.rstrip(b" \t") for line in text.split(b"\n")).rstrip(b"\n")


def _body_sha(payload: bytes) -> str:
    import hashlib
    return hashlib.sha256(_body_form(payload)).hexdigest()


def _mac(key: bytes, session: str, note: str, nonce: str, *, subject: str,
         from_: list[str], date: int, message_id: str, body_sha: str) -> str:
    """The HMAC-SHA256 over every field the proof binds, each in the form
    the reader can rebuild from the delivered mail. JSON, so no field's text
    can run into the next."""
    import hashlib
    import hmac
    text = json.dumps(["sessionmail pager-note", PROOF_VERSION, session, note.strip(),
                       nonce, _subject_form(subject), from_, date,
                       " ".join(message_id.split()), body_sha], ensure_ascii=False)
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def note_proof(session: str, note: str, subject: str, *, from_: str, date: str,
               message_id: str, body: bytes = b"") -> str:
    """The `Pager-Proof:` value for a NOTE mail to SESSION with SUBJECT, sent
    From FROM_ with this DATE and MESSAGE_ID and BODY (so all of them must be
    fixed before the proof is made: `note_mint`). Raises `RuntimeError` when
    SESSION has no secret and none can be made, naming the file: better no
    confirmation than one that would page its own seat."""
    import secrets
    key = note_key(session, make=True)
    if key is None:
        raise RuntimeError(f"no pager-note key for session {session!r}: "
                           f"{note_key_path(session)} is missing and could not be made, "
                           f"or cannot be read - the confirmation is not sent")
    nonce = secrets.token_hex(16)
    mac = _mac(key, session, note, nonce, subject=subject, from_=_from_form(from_),
               date=_date_form(date), message_id=message_id, body_sha=_body_sha(body))
    return f"{PROOF_VERSION} {nonce} {mac}"


#: The headers a note carries exactly once. A second copy of any of them
#: could be the one a reader shows while the proof checked the other.
_ONCE = ("From", "Date", "Message-ID", "Subject", PAGER_PROOF)


def genuine_note(mail: bytes, session: str, at: datetime | None = None) -> str | None:
    """The Pager-Note value of MAIL (the whole message: headers and body)
    when it is PROVEN the pager's own, to SESSION, and fresh at AT (when it
    arrived; now when None); else None, and the header is ignored. Keys on
    provenance, never on the spelling: `Pager-Note` and `X-Pager-Note` are
    read alike, through the header reader (`_headers`).

    THE one reader of `Pager-Note`: the unproven claim reader beside it (`pager_note`) was
    deleted, and `arrival_verdict` asks only `_may_be_note` before it reads
    a body for this to prove."""
    import hmac
    from datetime import timezone
    from email.parser import BytesParser
    from email.policy import compat32
    from . import _headers
    if not session or len(mail) > NOTE_LIMIT:
        return None
    msg = BytesParser(policy=compat32).parsebytes(mail)
    value = _headers.header(msg, PAGER_NOTE)
    if value is None or msg.is_multipart():
        return None
    if any(len(msg.get_all(name) or []) != 1 for name in _ONCE):
        return None
    note = str(value).strip()
    parts = str(msg[PAGER_PROOF]).split()
    if len(parts) != 3 or parts[0] != PROOF_VERSION:
        return None
    try:
        from_, date = _from_form(str(msg["From"])), _date_form(str(msg["Date"]))
    except Exception:  # noqa: BLE001 - a From or Date that cannot be read proves nothing
        return None
    at = datetime.now(timezone.utc) if at is None else at
    sent = datetime.fromtimestamp(date, timezone.utc)
    if not at - NOTE_FRESH <= sent <= at + NOTE_SKEW:
        return None
    defects = len(msg.defects)
    payload = msg.get_payload(decode=True)
    if len(msg.defects) != defects:
        return None                         # a body its own encoding cannot decode
    key = note_key(session)
    if key is None:
        return None
    want = _mac(key, session, note, parts[1], subject=str(msg["Subject"]), from_=from_,
                date=date, message_id=str(msg["Message-ID"]),
                body_sha=_body_sha(payload if isinstance(payload, bytes) else b""))
    return note if hmac.compare_digest(want, parts[2]) else None


@dataclass(frozen=True)
class NoteMint:
    """The Date and Message-ID a note is sent with, fixed BEFORE its proof
    is made (the proof covers both), and the parts `_sender.send` mints them
    from, so the wire carries exactly what was proven."""

    now: int
    pid: int
    rand: int
    message_id: str
    date: str


def _note_epoch() -> int:
    """The send time of a note, in epoch seconds (a seam for a test)."""
    return int(time.time())


def note_mint(env: "dict[str, str]") -> NoteMint:
    from . import _sender
    epoch = _note_epoch()
    pid, rand = os.getpid(), _sender.fresh_rand(epoch)   # one process, one id each
    message_id, date = _sender.mint(_sender.host_of(env), epoch, pid, rand)
    return NoteMint(epoch, pid, rand, message_id, date)


def _deliver_by_mta(req, env, mint: NoteMint) -> str:
    """Hand REQ to the MTA the way every sessionmail send goes (`_sender.send`:
    sendmail -t, signed when the session is keyed), minted from MINT's parts.
    The Message-ID back."""
    from . import _sender
    return _sender.send(req, b"", env, now=mint.now, pid=mint.pid, rand=mint.rand).message_id


#: THE SEAM: every confirmation mail goes through this one name, as
#: `_mta_send(req, env, mint)`. Tests point it at a recorder (conftest), so
#: no test can reach a real MTA.
_mta_send = _deliver_by_mta


def send_note(note: str, to: str, subject: str, *, from_: str,
              env: "dict[str, str] | None" = None) -> str:
    """One confirmation mail (D9): SUBJECT only, no body, To the session box
    TO, with `Pager-Note: NOTE` and `Priority: non-urgent`, and
    the proof, made over the Date and Message-ID minted for it here. The
    Message-ID back; what the sender refuses is raised (`_sender.Refused`).
    A rule never causes a send: only an arm and a focus change call this."""
    from . import _sender
    env = dict(os.environ) if env is None else dict(env)
    mint = note_mint(env)
    proof = note_proof(to, note, subject, from_=from_, date=mint.date,
                       message_id=mint.message_id)
    req = _sender.Request(to=to, from_=from_, subject=subject, no_body=True,
                          headers=[(PAGER_NOTE, note), NOTE_PRIORITY, (PAGER_PROOF, proof)])
    return _mta_send(req, env, mint)


def confirm_listening(session: str, box: str, *, from_: str,
                      env: "dict[str, str] | None" = None) -> str:
    """The arm's "listening" mail, to its own SESSION's box (D9). Says what
    it listens on and the session id, and nothing that reads as an
    instruction: it suggests nothing to do."""
    where = "" if box == session else f" on {box}"
    return send_note(NOTE_LISTENING, session, f"pager listening{where}, session {session}",
                     from_=from_, env=env)


def confirm_focus(session: str, mode: str, until: datetime | None, *, from_: str,
                  env: "dict[str, str] | None" = None) -> str:
    """The focus change's mail, to the SESSION whose mode changed (D9): the
    mode it is now in, and the session id. `pager focus` (tasks 5.2) is its
    caller; FROM_ is whoever made the change."""
    return send_note(NOTE_FOCUS, session,
                     f"pager focus {focus_text(mode, until)}, session {session}",
                     from_=from_, env=env)


def _named_path(session: str) -> Path | None:
    root = _seat.session_root(session)
    return root / _seat.STAMPS_DIR / RULES_NAMED if root is not None else None


def named_bad_lines(session: str) -> set[str]:
    """The bad lines already named to SESSION (`RULES_NAMED`)."""
    path = _named_path(session)
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))) if path else set()
    except (OSError, ValueError, TypeError):
        return set()


def record_named(session: str, named: set[str]) -> None:
    """Record NAMED as what SESSION has been told: exactly the bad lines
    there are now, so a line fixed and then broken again is named again."""
    path = _named_path(session)
    if path is None:
        return
    try:
        _seat.make_dir(path.parent, session)
        tmp = path.with_name(f".{RULES_NAMED}.{os.getpid()}")
        tmp.write_text(json.dumps(sorted(named)), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass       # the cost is one line named twice, never a lost page


def bad_line_text(error) -> str:
    """One bad rules line, as the banner and the ride-along name it."""
    return (f"pager: rules - {error} - that line is skipped, the rest stand; "
            "fix it and the next mail reads it")


def _address_key(arrival) -> str:
    """The coalescing key for one arrival: its `X-Original-To`, normalised.

    It calls `_maildir.original_to_key` - the single definition, already
    shared by the grouping and the ride-along's burst count - and does NOT
    re-implement the normalisation. That matters more than it looks: the
    header arrives in THREE qualification levels in the same box (bare,
    `@host`, and the FQDN), and for one measured reader the BARE form is the most common
    spelling. A second, host-qualified comparison written here would silently
    disagree with the grouping about who a reader is, on the majority of real
    traffic - so identity is compared through the shared key, and the verbatim
    spelling is kept for display only.

    A message whose header cannot be read groups under "", which coalesces
    rather than disappearing: unattributable mail is still mail waiting.
    """
    from . import _maildir
    try:
        entry = _maildir.parse(arrival.path, 0, True)
    except Exception:
        return ""
    return _maildir.original_to_key(getattr(entry, "original_to", "") or "")


def waiting_groups(box_path, arrivals, seat, session, _maildir) -> list:
    """The waiting arrivals, grouped by `X-Original-To`, with the box's own
    row numbers on each group.

    The row range has to come from the BOX, because that is the index
    `message read` takes - so the box is enumerated once and the arrivals are
    matched into it BY MESSAGE-ID, not by path. Path would be wrong on the
    queue branch, where an arrival is a HARDLINK in this session's ride queue
    and its path is not the box file's at all. (Measured cost of the
    enumeration, on the largest real box on this host: 1.67s for 8,669
    messages. A page is a rare event that ends a blocking wait, so this is
    paid once per wake, never in a loop.)

    Anything that cannot be matched is still grouped, from its own file, with
    no row number - a message is never dropped from the counts just because
    the index could not be found. If the box cannot be read at all, the
    arrivals are grouped on their own: fewer facts, never a wrong one.
    """
    marks: dict[str, set[str]] = {}
    if seat is not None:
        try:
            marks = _seat.read_marks(seat, session)
        except (_seat.Malformed, OSError):
            marks = {}
    by_id: dict[str, object] = {}
    try:
        for entry in _maildir.enumerate_box(str(box_path)):
            if entry.message_id:
                by_id.setdefault(entry.message_id, entry)
    except Exception:
        by_id = {}                      # unreadable box: group what we hold
    entries = []
    for a in arrivals:
        found = by_id.get(getattr(a, "message_id", "") or "")
        if found is not None:
            entries.append(found)
            continue
        try:
            entries.append(_maildir.parse(a.path, 0, True))
        except OSError:
            continue                    # a file that went while we looked
        except Exception:  # noqa: BLE001 - any other fault still counts
            # A header the parser cannot read no longer reaches here (parse
            # is total over it); anything else that does is still mail
            # waiting, so it is counted, marked, never silently dropped.
            entries.append(_maildir.unparsed(a.path, 0, True))
    return _maildir.group_by_original_to(entries, marks)


def _arrival_line(box: str, arrival, _maildir) -> str:
    """One message, one line: box, who, what - never the body.

    THE LISTING FORM, not the default page. specs/pager: "A page MAY carry
    one line per entry ... when a switch is set. The switch SHALL default
    off." The default page is `quiet_page()` and carries a count instead;
    the ONE caller is `listen`'s page(), and only when `page_list_on()`
    says the switch is set (tasks.md 3.4). These bytes are the bytes the
    listing form had before the quiet page - do not rewrite them here
    without measuring what reads them.
    """
    try:
        entry = _maildir.parse(arrival.path, 0, True)
        line = (f"[{box}] {entry.sender_local or '?'}  {entry.subject or '(no subject)'}"
                f"{entry.unreadable_mark}")
    except Exception:
        line = f"[{box}] {arrival.name}"
    line = " ".join(line.split())
    return line if len(line) <= LINE_WIDTH else line[: LINE_WIDTH - 1] + "…"


# ── the verbs: `pager rule list|add|remove`, `pager why ID` (5.1, 5.3) ───────
#: Design D6 / specs/pager "A session's own rules are its own; the rest are
#: a privileged seat's" (the names come from config): an agent writes its own
#: SESSION's rules; the SEAT layer, the LOCKED file and any `start` rule are
#: written only by a caller whose seat is privileged (`_operator`,
#: `operator.privileged`, the seats `message subscription --seat` names).
#: Anyone else: exit 2, the request-by-mail route, nothing written. All seats on a host run as one
#: uid, so the file system enforces none of it: the verb is the only gate,
#: and a gate on a seat NAME is policy, not security (design O-f).

#: The door for everyone else (D6): `who.route(RULE_TOPIC)`, to a privileged
#: seat named by config (`_operator.route`).
RULE_TOPIC = "pager rule"
#: `start` parses and is stored; nothing acts on it yet (P15).
START_UNHONOURED = ("note: nothing honours a start rule yet (D8): there is no host-level "
                    "starter, and a live listener treats start as wake")
#: The one verb whose rules are privileged wherever they are written (D6).
START_VERB = "start"


class RuleRefused(Exception):
    """`pager rule` refuses: exit 2, the line says why, nothing is written."""


def _rule_body(text: str) -> str:
    """A rules line as it is compared and stored: its comment dropped and
    its white space collapsed, so `ride   to:x` and `ride to:x` are one."""
    from . import rules
    return " ".join(rules._COMMENT.sub("", text.removeprefix("﻿")).split())


def _rule_layer(who, seat: bool, locked: bool) -> tuple[str, Path]:
    """The layer a `pager rule` verb names, and its ONE file (P3): the
    session's by default, `--seat`'s, or `--locked`'s. The paths are the
    loader's own (`rules.load_layers`), so what is written is what is read."""
    from . import rules
    if seat and locked:
        raise RuleRefused("--seat and --locked name two layers: pick one; nothing written")
    if locked:
        return rules.LOCKED, rules.locked_path()
    if seat:
        path = rules._seat_rules(who.seat, _seat.home()) if who.seat is not None else None
        if path is None:
            raise RuleRefused("no seat identity in the environment (looked for "
                              + ", ".join(_seat.SEAT_VARS) + "), so no seat layer; "
                              "nothing written")
        return rules.SEAT, path
    sid, err = rules._session_name(who.session)
    if sid is None:
        why = f"{err.text!r} is not a session id" if err else \
            "no session id (" + " or ".join(_seat.SESSION_VARS) + ")"
        raise RuleRefused(f"{why}, so no session layer: it is the session's own file; "
                          "nothing written")
    from . import _reach
    stuck = _reach.unmoved(sid)
    if stuck:
        # 3.3a, the mixed window: a listener armed on code from before the
        # move reads `<home>/sessions/<sid>/rules`; a rule written to the new
        # file would be law nowhere until the re-arm that moves the state.
        # Say so rather than write a rule that reads as live (the old path is
        # named, never read or written).
        raise RuleRefused(f"session {sid}'s state is {stuck}: its listener runs code "
                          "from before the move and would not read the new file; "
                          "re-arm (`pager listen`) and retry")
    return rules.SESSION, _seat.session_root(sid) / rules.RULES_FILE


def _rule_gate(who, layer: str, body: str) -> None:
    """D6's gate. Refuses (RuleRefused) a caller who is not a privileged
    seat and names the seat layer, the locked file, or a `start` rule."""
    from . import rules
    if who.privileged:
        return
    if layer == rules.SEAT:
        why = "a seat rule outlives your session, and the next seat inherits it"
    elif layer == rules.LOCKED:
        why = "the locked file is above every agent on this host"
    elif body.split()[:1] == [START_VERB]:
        why = "a start rule is a privilege: it would start an agent (mail-starts-me)"
    else:
        return
    hint = ("" if layer == rules.SESSION
            else " Without --seat or --locked, pager rule changes only your session.")
    raise RuleRefused(f"{why}, so it is for {who.may} to write (you are {who.who}); "
                      f"nothing written. To have it, {who.route(RULE_TOPIC)}.{hint}")


def _rule_check(layer: str, path: Path, body: str, text: str, op=None) -> None:
    """BODY parses as a line of LAYER, or RuleRefused names why. The locked
    file also takes `set` lines, checked against what
    it holds already (one `set` a name); a `focus` line is `pager focus`'s.

    OP is the caller's operator config (`Caller.op`): its alias for
    `$operator` is honoured as the loader honours it. (Design d.3's note
    for `set urgent $operator` with no operator cannot arise here: only a
    privileged seat writes the locked file, and nothing is privileged until
    an operator is configured. `load_layers` names it in a hand-edited
    file, `rules.OPERATOR_EMPTY`.)"""
    from . import _operator, rules
    op = _operator.read() if op is None else op
    aliases = rules.operator_aliases(op)
    word = body.split()[0]
    if layer == rules.LOCKED and word == rules.FOCUS_WORD:
        raise RuleRefused("a focus line is not a rule: the fleet-wide mode is `pager focus`'s "
                          "to set; nothing written")
    if layer == rules.LOCKED and word == rules.SET_WORD:
        lines = text.split("\n") if text else []
        if lines and lines[-1] == "":
            lines.pop()
        _, bad = rules.parse_settings("\n".join([*lines, body]), str(path), aliases=aliases)
        mine = [e for e in bad if e.line == len(lines) + 1]
        if mine:
            raise RuleRefused(f"{mine[0].reason}: {body}; nothing written")
        return
    _, bad = rules.parse(body, layer, aliases=aliases)
    if bad:
        raise RuleRefused(f"{bad[0].reason}: {body}; nothing written")


def _rule_read(path: Path) -> str:
    """PATH's text, or "" when there is no file: its RAW BYTES decoded, as
    the loader (`rules._read`) decodes them, and with NO newline translation
   . `Path.read_text` uses universal
    newlines, which turn a lone CR into a line break and fold CRLF to LF, so
    the verbs saw a different text from the loader and a rewrite changed
    bytes it had no business touching. The loader splits on LF only; so must
    this. Undecodable bytes survive a rewrite (surrogateescape), so a write
    never mangles what it keeps."""
    try:
        return path.read_bytes().decode("utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return ""


def _rule_write(path: Path, text: str, session: str | None = None) -> None:
    """TEXT into PATH whole, as the same bytes `_rule_read` read them (no
    newline translation): a temporary file beside it, then a rename, so
    a listener reading it mid-write sees the old file or the new, never half
    (and the rename changes the inode, which `LiveLayers` notices). SESSION
    names the session layer's owner, so its `mail/` root is made private
    first (3.3a, `_seat.make_dir`); the other layers are made plainly."""
    if session:
        _seat.make_dir(path.parent, session)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_bytes(text.encode("utf-8", errors="surrogateescape"))
    os.replace(tmp, path)


def _shown(text: str) -> str:
    """TEXT as it is PRINTED: a byte that is not UTF-8 (kept by `_rule_read` as
    a lone surrogate, so a rewrite puts it back) shows as U+FFFD, the way the
    loader reads it. The stream cannot encode a surrogate, and an uncaught
    UnicodeEncodeError is a verb that fails. Only the
    print boundary changes; the read and the rewrite keep the bytes."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _say(text: str, err: bool = False) -> None:
    """`click.echo` of TEXT through `_shown`: what `pager rule` prints."""
    click.echo(_shown(text), err=err)


def _rule_verb(verb: str, words: tuple[str, ...], seat: bool, locked: bool) -> None:
    """`pager rule add|remove`: the gate, the check, then one write."""
    from . import _subscribe, rules
    ctx = click.get_current_context()
    prog = f"pager rule {verb}"
    body = _rule_body(" ".join(words))
    try:
        if not body:
            raise RuleRefused("no rule given (write it as the file holds it: "
                              "`ride from:*@delta.chat`); nothing written")
        who = _subscribe.caller()
        layer, path = _rule_layer(who, seat, locked)
        _rule_gate(who, layer, body)
        text = _rule_read(path)
        if verb == "add":
            _rule_check(layer, path, body, text, who.op)
    except RuleRefused as exc:
        _say(f"{prog}: {exc}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    except OSError as exc:
        _say(f"{prog}: could not read {exc.filename or 'the rules file'}: "
             f"{exc.strerror or exc}; nothing written", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    lines = text.split("\n")
    held = [i for i, raw in enumerate(lines) if _rule_body(raw) == body]
    if verb == "add":
        if held:
            _say(f"{layer} already holds `{body}` ({path}:{held[0] + 1}); nothing changed")
            return
        new = (text if not text or text.endswith("\n") else text + "\n") + body + "\n"
    else:
        if not held:
            _say(f"{layer} holds no `{body}` ({path}); nothing changed")
            ctx.exit(EX_NOTHING)
            return
        new = "\n".join(raw for i, raw in enumerate(lines) if i not in held)
    try:
        _rule_write(path, new, who.session if layer == rules.SESSION else None)
    except OSError as exc:
        _say(f"{prog}: {path} NOT written - {exc.strerror or exc}; nothing changed",
             err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    if verb == "add":
        number = len(new.rstrip("\n").split("\n"))
        _say(f"added to {layer}: {path}:{number}  {body}")
        if body.split()[0] == START_VERB:
            _say(START_UNHONOURED)
    else:
        _say(f"removed from {layer}: {path}  {body}"
             + (f" ({len(held)} lines)" if len(held) > 1 else ""))
    _say("it applies from the next arrival; no re-arm")


_ADD_EXIT = "Exit: 0 ok (or already there) · 2 usage, or refused (nothing written)"
_REMOVE_EXIT = "Exit: 0 ok · 2 usage, or refused (nothing written) · 3 nothing to remove"


@cli.group(
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="list|add|remove [args]",
    short_help="your rules: what wakes you and what rides along",
    epilog=unwrapped(
        """A rule: a verb (ride, wake, wake!, carry, carry!, start), then conditions (to:
from: subject: priority: call: draft: or any header), globs, all must
match: `ride from:*@delta.chat`. First match wins, down the layers:
locked, focus, session, seat, env, built-in. Your SESSION's rules are
yours; seat, locked and start rules are a privileged seat's, and others
ask one by mail. Edits apply from the next arrival. Every wake carries.

Exit: 0 ok · 2 usage, or refused · 3 nothing to remove"""
    ),
)
def rule() -> None:
    """pager rule - your rules: what wakes you and what rides along"""


@rule.command(
    "list",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[--seat | --locked | --all]",
    short_help="[--seat|--locked|--all]    the rules in a layer, numbered",
    epilog=unwrapped(
        """Your session's rules by default, each with its line number, and
every bad line (named with its file and line, and skipped). --all is
every layer in the order that decides, the built-in defaults last.
Open to everyone; nothing is written.

Exit: 0 ok · 2 usage, or could not look (no session id)"""
    ),
)
@click.option("--seat", is_flag=True, help="your seat's layer")
@click.option("--locked", is_flag=True, help="the host's locked file")
@click.option("--all", "every", is_flag=True, help="every layer, in the order that decides")
def rule_list(seat: bool, locked: bool, every: bool) -> None:
    """pager rule list - the rules in a layer, numbered; --all, every layer"""
    from . import _maildir, _subscribe, rules
    ctx = click.get_current_context()
    who = _subscribe.caller()
    if every:
        layers = rules.load_layers(who.seat, who.session)
        _list_all(layers)
        return
    try:
        layer, path = _rule_layer(who, seat, locked)
        # A strict stat: a layer folder that denies it is "could not look",
        # never "no file" (`path.exists()` says False there from 3.14).
        text = _rule_read(path) if _maildir.strict_mode(path) else None
    except (RuleRefused, OSError) as exc:
        _say(f"pager rule list: could not look: {exc}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    if text is None:
        _say(f"{layer}  {path} - no file: no rules of its own; the layers below decide")
        return
    errors = [e for e in rules.load_layers(who.seat, who.session).errors
              if e.source == str(path)]
    bad_at = {e.line for e in errors}
    _say(f"{layer}  {path}")
    shown = 0
    for number, _raw, body in rules._lines(text):
        if number not in bad_at:
            _say(f"  {number:>3}  {body}")
            shown += 1
    if not shown:
        _say("  (no rules)")
    for e in errors:
        _say(f"bad (skipped)  {e}")


def _list_all(layers) -> None:
    """`pager rule list --all`: each layer as the judgement reads it."""
    from . import rules
    now = datetime.now().astimezone()
    modes = {m.name: m for m in layers.modes}
    for layer in layers:
        if layer.focus is not None:
            mode = modes.get(layer.name)
            where = f"{mode.source}:{mode.line}" if mode else layer.focus
            state = " (ended)" if layer.ended(now) else ""
            head = f"{layer.name}  set by {where}{state}"
        elif layer.name == rules.BUILT_IN:
            head = f"{layer.name}  the defaults (D4), decided last"
        else:
            head = f"{layer.name}  {layer.rules[0].source if layer.rules else '(empty)'}"
        _say(head)
        for r in layer.rules:
            _say(f"  {r.line:>3}  {r.text}")
    for e in layers.errors:
        _say(f"bad (skipped)  {e}")


@rule.command(
    "add",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="RULE [--seat | --locked]",
    short_help="RULE [--seat|--locked]     add RULE to your session",
    epilog=unwrapped(
        f"""RULE is one line as the file holds it, quoted or not:
`pager rule add ride from:*@delta.chat`. It is parsed first; a bad
line is refused and named. --seat, --locked and any start rule are
for a privileged seat only: anyone else is refused, exit 2, and asks one
by mail. --locked also takes `set operator|urgent GLOB...`. A start rule
is stored, and nothing honours it yet. A repeat changes nothing.

{_ADD_EXIT}"""
    ),
)
@click.argument("words", nargs=-1, metavar="RULE")
@click.option("--seat", is_flag=True, help="your SEAT's layer, not your session (a privileged seat)")
@click.option("--locked", is_flag=True, help="the host's locked file (a privileged seat)")
def rule_add(words: tuple[str, ...], seat: bool, locked: bool) -> None:
    """pager rule add - add RULE to your session's rules (or --seat, --locked)"""
    _rule_verb("add", words, seat, locked)


@rule.command(
    "remove",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="RULE [--seat | --locked]",
    short_help="RULE [--seat|--locked]     remove RULE from your session",
    epilog=unwrapped(
        f"""Removes every line that reads RULE (its comment and spacing
aside), and keeps every other line and comment. The same gate as add:
--seat, --locked and any start rule are a privileged seat's.

{_REMOVE_EXIT}"""
    ),
)
@click.argument("words", nargs=-1, metavar="RULE")
@click.option("--seat", is_flag=True, help="your SEAT's layer, not your session (a privileged seat)")
@click.option("--locked", is_flag=True, help="the host's locked file (a privileged seat)")
def rule_remove(words: tuple[str, ...], seat: bool, locked: bool) -> None:
    """pager rule remove - remove RULE from your session's rules (or --seat, --locked)"""
    _rule_verb("remove", words, seat, locked)


# ── the verb: `pager focus sleep|off [--until TIME]` (5.2) ──────────────────
#: Design D5, D6; specs/pager "A session's own rules are its own; the rest
#: are a privileged seat's" (from config). The agent switches its OWN session's mode
#: (the session's `focus` file, O-c). ANOTHER session's mode is a privileged
#: seat's or its line manager's, and the FLEET-WIDE mode (a `focus` line in
#: the locked file, P14) is a privileged seat's. How the pager would know a
#: line manager is OPEN (O-e) and is NOT built on a guess: a caller who is
#: not privileged is refused, with the request-by-mail route,
#: and told why. Each change that changes something mails the changed session
#: once (D9, `confirm_focus`); a request that changes nothing sends nothing.

#: The door for everyone else (D6), as `pager rule`'s names it.
FOCUS_TOPIC = "pager focus"


class FocusRefused(Exception):
    """`pager focus` refuses: exit 2, the line says why, nothing changes."""


def _focus_target(who, for_: str | None, fleet: bool) -> tuple[str, Path, str | None]:
    """(where, file, session) for a `pager focus` call: WHERE is `session` or
    `fleet`, FILE is the one file the change is written to (the loader's
    own paths), SESSION is the session whose mode changes, and the one the
    confirmation mail goes to (fleet-wide: the changer's own, if any). D6's
    gate is applied here, before anything is read."""
    from . import rules
    if fleet and for_ is not None:
        raise FocusRefused("--for and --fleet name two targets: pick one; nothing changed")
    if fleet:
        if not who.privileged:
            raise FocusRefused(
                "the fleet-wide mode is every session on this host, so it is for "
                f"{who.may} to switch (you are {who.who}); nothing changed. To have it, "
                f"{who.route(FOCUS_TOPIC)}. Without --fleet, pager focus changes only "
                "your session.")
        return rules.FLEET, rules.locked_path(), who.session or None
    target = who.session if for_ is None else for_.strip()
    if for_ is not None and target != who.session and not who.privileged:
        raise FocusRefused(
            "another session's mode is switched by a privileged seat or its line manager, "
            "and the pager cannot yet tell who a session's line manager is (design O-e), "
            f"so here it is for {who.may} (you are {who.who}); nothing changed. To have "
            f"it, {who.route(FOCUS_TOPIC)}.")
    sid, err = rules._session_name(target)
    if sid is None:
        why = f"{err.text!r} is not a session id" if err else \
            "no session id (" + " or ".join(_seat.SESSION_VARS) + ")"
        raise FocusRefused(f"{why}, so no session to switch; nothing changed")
    from . import _reach
    stuck = _reach.unmoved(sid)
    if stuck:
        # As `_rule_layer` says: a focus file the live listener never reads
        # would promise a sleep that does not happen (3.3a, the mixed window).
        raise FocusRefused(f"session {sid}'s state is {stuck}: its listener runs code "
                           "from before the move and would not read the new file; "
                           "re-arm it (`pager listen`) and retry; nothing changed")
    folder = _seat.session_root(sid)
    if for_ is not None and sid != who.session and not folder.is_dir():
        raise FocusRefused(f"this host has no session {sid!r} ({folder.parent}); "
                           "nothing changed")
    return rules.SESSION, folder / rules.FOCUS_FILE, sid


def _focus_until(mode: str, until: str | None, now: datetime) -> datetime | None:
    """--until TIME as the moment it names (O-d): `HH:MM` is the next such
    local time, else an ISO date and time. None with no TIME. Refused for
    `off` (it lasts until changed), for what is not a time, and for a time
    already past (it would set a mode that has ended)."""
    from . import rules
    if until is None:
        return None
    if mode == rules.OFF:
        raise FocusRefused("off takes no --until: off lasts until it is changed; nothing changed")
    text = " ".join(until.split())
    try:
        when = rules._until(text, now)
    except rules._Bad as bad:
        raise FocusRefused(f"--until {bad}; nothing changed") from None
    if when <= now:
        raise FocusRefused(f"--until {text!r} is already past; nothing changed")
    return when


def _until_word(when: datetime) -> str:
    """The end time as the focus line holds it: the absolute local time, to
    the minute (to the second only when it has one). Never `HH:MM`: in the
    file that is anchored at the file's mtime (O-c), so a later write to the
    same file (the locked file is shared) would move it."""
    local = when.astimezone()
    if local.second or local.microsecond:
        return local.replace(microsecond=0, tzinfo=None).isoformat()
    return local.strftime("%Y-%m-%dT%H:%M")


def _focus_now(where: str, path: Path, text: str, now: datetime):
    """The mode PATH sets that is ON at NOW, or None: the current state the
    change is compared with. The same parse the loader does (`parse_focus`,
    or the locked file's `focus` line), anchored at the file's mtime."""
    from . import rules
    if not text:
        return None
    try:
        set_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        set_at = now
    if where == rules.FLEET:
        _, entries = rules._split_fleet(text)
        mode, _ = rules._focus_from(entries, str(path), set_at, rules.FLEET)
    else:
        mode, _ = rules.parse_focus(text, str(path), set_at)
    return mode if mode is not None and not mode.ended(now) else None


def _focus_text(where: str, text: str, line: str | None) -> str:
    """The file's new text. A session's `focus` file is its one mode line
    (`off` when there is none). The locked file keeps every line but its
    `focus` lines, which go; the new one, if any, is added at the end. The
    `focus` lines are the ones the LOADER reads as such (`_split_fleet`, by
    line number), so none it counts can survive: a BOM or a CR on a line
    does not hide it from the rewrite while the loader still reads it."""
    from . import rules
    if where != rules.FLEET:
        return f"{line or rules.OFF}\n"
    _, entries = rules._split_fleet(text)
    drop = {number for number, _, _ in entries}
    kept = [raw for number, raw in enumerate(text.split("\n"), start=1) if number not in drop]
    while kept and kept[-1] == "":
        kept.pop()
    if line is not None:
        kept.append(f"{rules.FOCUS_WORD} {line}")
    return "\n".join(kept) + "\n" if kept else ""


@cli.command(
    "focus",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="sleep|off [--until TIME] [--for SESSION | --fleet]",
    short_help="sleep: only a wake! or carry! wakes you; off: normal",
    epilog=unwrapped(
        """sleep: nothing wakes you but a breakthrough, a wake! or carry!
rule (all the operator's mail is one); the rest rides along. off is normal.
Every wake carries. Your session by default.
A mail that breaks through does not end sleep: TIME does, or off.
--for and --fleet are a privileged seat's; anyone else asks one by mail.
Each change mails the changed session a note that never wakes it.

Exit: 0 ok (or already so) · 2 usage, or refused (nothing changed)"""
    ),
)
@click.argument("mode", type=click.Choice(["sleep", "off"]), metavar="sleep|off")
@click.option("--until", "until", metavar="TIME",
              help="sleep until TIME: HH:MM, or an ISO date and time")
@click.option("--for", "for_", metavar="SESSION", help="another session's mode (a privileged seat)")
@click.option("--fleet", is_flag=True, help="every session here: the locked file (a privileged seat)")
def focus(mode: str, until: str | None, for_: str | None, fleet: bool) -> None:
    """pager focus - put your session in a focus mode (sleep), or take it out"""
    from . import _subscribe, rules
    ctx = click.get_current_context()
    now = datetime.now().astimezone()
    who = _subscribe.caller()
    try:
        where, path, session = _focus_target(who, for_, fleet)
        when = _focus_until(mode, until, now)
        text = _rule_read(path)
    except FocusRefused as exc:
        click.echo(f"pager focus: {exc}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    except OSError as exc:
        click.echo(f"pager focus: could not read {exc.filename or 'the focus file'}: "
                   f"{exc.strerror or exc}; nothing changed", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    whom = "the whole fleet" if where == rules.FLEET else f"session {session}"
    want = focus_text(mode, when, now)
    held = _focus_now(where, path, text, now)
    have =(held.mode, held.until) if held is not None else None
    if have == (None if mode == rules.OFF else (mode, when)):
        click.echo(f"focus: {whom} is already {want} ({path}); nothing changed")
        return
    line = None if mode == rules.OFF else mode + (f" until {_until_word(when)}" if when else "")
    try:
        _rule_write(path, _focus_text(where, text, line),
                    session if where == rules.SESSION else None)
    except OSError as exc:
        click.echo(f"pager focus: {path} NOT written - {exc.strerror or exc}; nothing changed",
                   err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    click.echo(f"focus: {whom} is now {want} ({path}); from the next arrival, no re-arm")
    if not session:
        click.echo("focus mail not sent - no session of yours to send it to (D9)")
        return
    # Tasks 4.2: the changed session is told, subject only,
    # by a mail that carries `Pager-Note: focus` and so never pages it. A
    # courtesy, never a condition: the change above stands if it fails.
    try:
        mid = confirm_focus(session, mode, when,
                            from_=who.seat.address if who.seat is not None else who.session)
    except Exception as exc:  # noqa: BLE001 - see above
        click.echo(f"focus mail not sent - {exc}")
    else:
        click.echo(f"focus mail sent {mid} to session {session} - it rides along, never pages")


@cli.command(
    "why",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="ID",
    short_help="why the mail ID rode along or woke you",
    epilog=unwrapped(
        """ID is a Message-ID, with or without its <>. Prints the action the
mail was given, the rule that gave it and that rule's layer (or the
built-in default that did), who judged it and when. It reads the
verdict RECORDED when the mail was judged, beside this session's marks:
it never re-judges, so a rule changed since cannot rewrite history.

Exit: 0 found · 2 usage, or could not look · 3 no verdict recorded for ID"""
    ),
)
@click.argument("message_id", metavar="ID")
def why(message_id: str) -> None:
    """pager why - the rule that decided a mail, and its layer"""
    ctx = click.get_current_context()
    mid = _mid(message_id)
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity:
        seat = None
    session = _seat.session_id()
    try:
        path = _seat.verdicts_path(seat, session)
        records = verdicts_for(seat, session, mid)
    except (_seat.NoIdentity, OSError) as exc:
        click.echo(f"pager why: could not look: {exc}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    if not records:
        click.echo(f"pager why <{mid}>: no verdict recorded in session {session} ({path}). "
                   "Nothing is re-judged: a verdict is recorded when `pager listen` or the "
                   "ride-along judges a mail.")
        ctx.exit(EX_NOTHING)
        return
    seen: list[tuple] = []
    distinct: list[dict] = []
    for record in records:
        key = tuple(record.get(k) for k in _WHY_KEY)
        if key not in seen:
            seen.append(key)
            distinct.append(record)
    for record in distinct:
        click.echo(f"pager why <{mid}>: {why_text(record)}")
    # The verdict says which RULE woke the mail; the page's own line
    # says which idleness let it page (`pager wake (idle with children T=0s)`).
    for line in page_lines(seat, session, mid):
        click.echo(f"pager why <{mid}>: {line}")
    if len(distinct) > 1:
        click.echo(f"pager why: judged {len(records)} times, and the rules changed between: "
                   "the last line is the latest")
    for bad in records[-1].get("bad") or ():
        click.echo(f"pager why: a bad line was skipped then: {bad}")


#: What `pager stop --kill` says after it ends
#: the pager monitor. Said as a fact, because it is final.
MONITOR_ENDED = ("ended for good: Claude Code does not restart a monitor. Hand-arm with "
                 "`pager listen` in the background, or restart the seat.")


def _mute_target(verb: str) -> "tuple[_seat.Seat, str, int | None, bool | None]":
    """The seat, the session, this session's live listener (or None) and
    whether it is the pager monitor (`is_monitor`: None when `ps` cannot say),
    for `pager off`/`stop`, `mute` and `unmute`. Exits 2 with no seat identity."""
    ctx = click.get_current_context()
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity as exc:
        click.echo(f"pager {verb}: could not look: {exc}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        raise                                     # unreachable: ctx.exit raises
    session = _seat.session_id()
    pid = _seat.is_listening(seat, session)
    return seat, session, pid, (_seat.is_monitor(pid) if pid is not None else None)


def _who(pid: int, monitor: "bool | None") -> str:
    return f"the pager monitor (pid {pid})" if monitor else f"the listener (pid {pid})"


@cli.command(
    "mute",
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="",
    short_help="silence this session's listener; it keeps running",
    epilog=unwrapped(
        """Leaves an `off` stamp for this session. Whatever listens - the
pager monitor or a hand `pager listen` - keeps running and pages nothing:
mail waits in the queue and rides along on tool calls as usual. The Stop
hook stays silent and the ride-along says the pager is muted.
`pager unmute` resumes paging, and so does the next `pager listen`.
Like putting a phone on silent: nothing is stopped.

Exit: 0 muted · 2 could not look (no seat identity) · 3 nothing is
listening for this session (the stamp is written all the same)"""
    ),
)
def mute() -> None:
    """pager mute - silence this session's listener; it keeps running"""
    ctx = click.get_current_context()
    seat, session, pid, monitor = _mute_target("mute")
    _seat.write_off(seat, session)
    if pid is None:
        click.echo(f"pager mute: muted session {session or '-'}, but nothing is listening: "
                   f"{LINE_NO_MONITOR}")
        ctx.exit(EX_NOTHING)
        return
    click.echo(f"pager mute: {_who(pid, monitor)} is muted: it is still running and pages "
               "nothing; `pager unmute` resumes")


@cli.command(
    "unmute",
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="",
    short_help="resume paging after `pager mute`",
    epilog=unwrapped(
        """Clears this session's `off` stamp, left by `pager mute` (or by
`pager off`/`stop`), so the listener that is still running pages again.
A listener that was not muted is left alone: "not muted: already
listening", and nothing is written.
When nothing is listening it says so: the pager monitor can only start
when the seat launches, so the way back is a hand `pager listen` in the
background, or a restart of the seat.

Exit: 0 paging again, or not muted · 2 could not look (no seat
identity) · 3 nothing is listening for this session"""
    ),
)
def unmute() -> None:
    """pager unmute - resume paging after `pager mute`"""
    ctx = click.get_current_context()
    seat, session, pid, monitor = _mute_target("unmute")
    if pid is not None and not _seat.stopped_on_purpose(seat, session):
        # Unmute only makes sense on a muted listener: a
        # live listener with no `off` stamp is a no-op - one line, nothing
        # written, exit 0.
        click.echo(f"pager unmute: {LINE_NOT_MUTED}")
        return
    _seat.clear_off(seat, session)
    if pid is None:
        click.echo(f"pager unmute: {LINE_NO_MONITOR}")
        ctx.exit(EX_NOTHING)
        return
    click.echo(f"pager unmute: {_who(pid, monitor)} pages again")


@cli.command(
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="[--kill]",
    short_help="old name: stop a hand listener; on the pager monitor it REFUSES",
    epilog=unwrapped(
        """`pager stop` is the same command, an old name: prefer `pager mute`
and `pager unmute`.
On the pager monitor it REFUSES and exits 4: it writes nothing, signals
nothing, and names `pager mute` (silence it) and --kill (end it for
good: Claude Code does not restart a monitor).
On a hand `pager listen` it stops it: SIGTERM, then clears its stamp.
That listener is found by the pid in this session's stamps/listening;
with no stamp, by its parent chain, only when its nearest claude is
this session's and no other session's stamp names it.
Never find a listener with `ps | grep`: one another session stamped,
or one under another session's claude, is never stopped.
Unless it refused, it leaves an `off` stamp, ON PURPOSE, so, when the hooks are on,
the Stop hook stays silent and the ride-along says muted,
not "re-arm"; `pager unmute` or `pager listen` clears it. After
`pager listen --timeout N` returned 124 nothing is listening, so this
exits 3 - expected: the stamp is written all the same.

Exit: 0 stopped or ended · 2 could not look (no seat identity) · 3
nothing was listening · 4 refused: the pager monitor holds this session"""
    ),
)
@click.option("--kill", "kill", is_flag=True,
              help="end the pager monitor for good (without it, off refuses on one)")
def off(kill: bool = False) -> None:
    """pager off - stop a hand listener; refuse on the pager monitor"""
    import signal
    ctx = click.get_current_context()
    seat, session, pid, monitor = _mute_target("off")
    # Every line names the verb as typed,
    # `pager stop` or `pager off`, as the refusal below always did.
    name = ctx.info_name or "off"
    if pid is not None and monitor and not kill:
        # On the monitor a stop is for good (it once meant mute), and old text that says `pager stop`
        # must not deafen the seat - so it REFUSES, writing nothing (no
        # `off` stamp, no signal), and names the two acts it may have meant.
        # With no `ps` answer (None) it is today's stop, as the listen
        # refusal keeps today's text then.
        click.echo(f"pager {name}: refused: {_who(pid, monitor)} hears for "
                   "this session, and Claude Code does not restart a monitor that is "
                   "stopped. `pager mute` silences it (it keeps running); `pager stop "
                   "--kill` ends it for good")
        ctx.exit(EX_MONITOR_REFUSED)
        return
    # A deliberate stop, whatever was running - the seat means to be
    # off, and a later hook must not tell it to arm.
    _seat.write_off(seat, session)
    if pid is None:
        click.echo(f"pager {name}: nothing listening for session {session or '-'}")
        ctx.exit(EX_NOTHING)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(40):
        if not _seat._pid_alive(pid):
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
    _seat.clear_listening(seat, session, pid)
    if monitor:
        click.echo(f"pager {name}: the pager monitor (pid {pid}) for session {session or '-'} "
                   f"is {MONITOR_ENDED}")
        return
    click.echo(f"pager {name}: stopped listener {pid} for session {session or '-'}")


#: `pager stop` is `pager off` by another name - the one the standard line
#: used ("pager is listening - run `pager stop` to stop"). One command, two names; the
#: spec's `pager off` stands.
#: Both are the OLD names now; `mute`/`unmute` are taught.
cli.add_command(off, "stop")


# ── the hooks: `pager hook stop` / `pager hook batch` ──────────────────────
#: The hooks the pager relies on are installed with the plugin, nothing
#: more for a user to do. hooks/hooks.json in
#: this repo registers Stop -> `pager hook stop` and PostToolBatch ->
#: `pager hook batch` (measured PostToolBatch: once per
#: batch of tool calls, not once per call). Both are verbs of the pager
#: itself so seat identity has ONE resolver (_seat) - an earlier drift was a
#: shell guard narrower than the tool it wrapped.
#: A hook's exit code is a verdict on the HOOK: these exit 0 on every
#: "could not look" (a plain shell has no seat; that is not an error), and
#: `stop` exits 2 for exactly one thing - the not-listening line.

def _hook_payload() -> dict:
    """The hook's JSON from the harness on stdin; {} at a tty or on junk."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@cli.group(
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="<event>",
    short_help="the plugin's hooks: `start`, `stop` and `batch`",
    epilog=unwrapped(
        f"""Registered by hooks/hooks.json; their lines live in hook_lines.toml.
Each speaks only when {_settings.SHOWN} holds
"hooks": true, OFF on a fresh install; SESSIONMAIL_PAGER=off wins.
`stop` reads the file and never writes it. When no listener is live for
this session, it says so on stderr with exit 2 - the one way a Stop hook
is heard - unless the harness says stop_hook_active (never a loop), or
the seat stood down with `pager stop` (its `off` stamp; `pager listen`
clears it). It stamps nothing.
`batch` writes the file OFF when it is missing and says so, once; a
faulty file is off and says so once. Off: silent, mail included, but
for DEAF: a seat with `pager_required` that nothing hears says why,
hooks on or off. On: it stamps ride-along, gives this session's queued
mail once, then its state line (listening, not listening, or "pager is
muted" after a `pager mute` or `pager stop`) once, then only on change.
Inside a subagent (payload agent_id) it takes, marks, stamps, writes and
says nothing: the seat's mail waits for its own main thread or the page.
Exit: 0 always, except stop's 2."""
    ),
)
def hook() -> None:
    """pager hook - the verbs the plugin's hooks run"""


@hook.command(
    "stop",
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="",
    short_help="Stop hook: say not-listening (exit 2) when true; stamps nothing",
)
def hook_stop() -> None:
    """pager hook stop - the Stop hook's half of the pager"""
    started = time.monotonic()           # the row's `ms` is from here
    ctx = click.get_current_context()
    payload = _hook_payload()
    # changes/pager-settings-file (tasks.md 3.1): the opt-out first, then the settings
    # file, then all as before.
    if not _seat.pager_enabled():
        ctx.exit(0)                      # SESSIONMAIL_PAGER=off wins: read nothing, say nothing (D7)
        return
    # Off on purpose, missing, or a fault: silent - except for the deaf check
    # below, which runs either way and speaks
    # only when this seat's pager is required. The Stop hook READS the file
    # and never writes it (D3): its only channel to the agent is exit 2, the
    # nag, so the ride-along creates the file and says the note. A fault
    # never makes this hook nag (D1).
    hooks_on = _settings.read().hooks
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity as exc:
        if hooks_on:
            click.echo(f"pager hook stop: could not look: {exc} - nothing to do", err=True)
        ctx.exit(0)
        return
    session = _seat.session_id()
    if session == "-":
        # A stamp under `-` is one no listener reads.
        if hooks_on:
            click.echo(f"pager hook stop: no session id in {', '.join(_seat.SESSION_VARS)} - nothing to do", err=True)
        ctx.exit(0)
        return
    # changes/stopped-means-idle: no stopped
    # stamp - a turn boundary is not idle, and nothing reads the file now.
    if payload.get("stop_hook_active") is True:
        ctx.exit(0)                      # already continuing from a Stop hook: never a loop
        return
    required = _seat.pager_required(seat, session)
    if required.on:
        # A required seat gets the deaf check's answer, and
        # only that: DEAF with its reason, or silence (listening, muted, or
        # in the grace). It REPLACES the plain not-listening nag, hooks on or
        # off. Asked before the `off` stamp below, because the check reads
        # that stamp itself: after `pager stop --kill` it says "ended".
        line = deaf_check(seat, session, required=required)
        if line:
            click.echo(line, err=True)
            _log_hook(_injectlog.VIA_STOP, line, seat=seat, payload=payload,
                      event=str(payload.get("hook_event_name") or "Stop"), started=started)
            ctx.exit(2)
            return
        ctx.exit(0)
        return
    if _seat.stopped_on_purpose(seat, session):
        ctx.exit(0)                      # settled by `pager stop`, not deaf
        return
    if not hooks_on:
        ctx.exit(0)
        return
    if _seat.pager_enabled() and _seat.is_listening(seat, session) is None:
        click.echo(LINE_NOT_LISTENING, err=True)
        _log_hook(_injectlog.VIA_STOP, LINE_NOT_LISTENING, seat=seat, payload=payload,
                  event=str(payload.get("hook_event_name") or "Stop"), started=started)
        ctx.exit(2)
        return
    ctx.exit(0)


def deaf_check(seat, session: str, ps=None, env: dict[str, str] | None = None,
               required: "_seat.Required | None" = None) -> str | None:
    """This seat's DEAF line, or None (say
    nothing). ALWAYS run by the Stop and PostToolBatch hooks, `"hooks":
    false` included; it SPEAKS only when the pager is required. In order:

    0. the pager off (`SESSIONMAIL_PAGER=off`) beats required: None;
    1. required? (`_seat.pager_required`) - no: None;
    2. muted? (the `off` stamp) - a muted seat is not nagged: None. But a
       mute needs a listener to mute: `pager stop --kill` writes the same
       stamp as it ENDS the monitor, and (l4) names that case "the
       pager monitor was ended". So the stamp silences the check only when
       no monitor ever armed, while something still listens (3), or when
       no monitor is expected (5);
    3. listening? (`stamps/listening` with a live pid, monitor or hand, or a
       listener by ancestry): None;
    4. grace? claude not running yet for this session, or younger than
       `_seat.GRACE_SECONDS`: None. (Asked after 3 only because it costs a
       `ps`; every one of 2-4 answers None, so the order changes no answer.)
    5. otherwise DEAF, with the reason: the monitor was expected (switch on,
       the plugin carries monitors/, claude interactive) and never armed
       (`monitor_missing`, naming tengu_amber_sentinel) or armed and is gone
       (ended); or none was expected (headless `-p`, switch off): the plain
       not-listening reason. Every line ends with the two ways out.

    PS is a shared `ps` table callable; one is made when it is None."""
    if not _seat.pager_enabled(env) or session in ("", "-"):
        return None
    required = _seat.pager_required(seat, session, env) if required is None else required
    if not required.on:
        return None
    muted = _seat.stopped_on_purpose(seat, session)
    armed = _seat.monitor_was_armed(seat, session)
    if muted and not armed:
        return None
    if ps is None:
        table: list = []

        def ps():
            if not table:
                table.append(_seat._ps_table())
            return table[0]
    if _seat.is_listening(seat, session, ps=ps) is not None:
        return None
    claude = _seat.claude_for(session, ps=ps)
    if claude is None:
        return None
    age = _seat.claude_age(claude)
    if age is not None and age < _seat.GRACE_SECONDS:
        return None
    expected = _seat.monitor_expected(session, ps=ps, env=env, claude=claude)
    if expected.expected:
        if armed:
            line = DEAF_MONITOR_ENDED
        else:
            line = DEAF_MONITOR_MISSING.replace("{gate}", _seat.amber_sentinel(env))
    elif muted:
        return None
    else:
        line = DEAF_NOT_LISTENING.replace("{why}", expected.why)
    return " ".join(line.split())


def _state_lines(seat, session: str, required: "_seat.Required | None" = None) -> list[str]:
    """The ride-along's state line, as a list of one:
    listening, then off on purpose, then not listening. Every seat gets one;
    none is exempt by what it is called. A listener that is
    this session's pager monitor gets the line that names it and
    never says `pager stop`; no `ps` answer keeps the plain line.
    On a REQUIRED seat (REQUIRED.on) the not-listening line is
    the deaf check's answer instead - DEAF with its reason, or nothing while
    claude is in its grace."""
    table: list = []

    def ps():
        if not table:
            table.append(_seat._ps_table())
        return table[0]

    # The `off` stamp mutes whatever listens,
    # so it is asked FIRST - a muted listener that is still running is muted,
    # not "listening".
    if _seat.stopped_on_purpose(seat, session):
        if required is not None and required.on:
            # A required seat whose monitor was ENDED
            # (`pager stop --kill` writes this stamp too) is deaf, not muted.
            line = deaf_check(seat, session, ps=ps, required=required)
            if line:
                return [line]
        elif _seat.monitor_was_armed(seat, session) and _seat.is_listening(
                seat, session, ps=ps) is None:
            # Not required, and the stamp is
            # `pager stop --kill`'s - a monitor armed and nothing listens.
            # A mute needs a listener to mute; "`pager unmute` resumes"
            # would be wrong advice, so the line names the ended monitor.
            return [LINE_MONITOR_ENDED]
        return [LINE_OFF]
    live = _seat.is_listening(seat, session, ps=ps)
    if live is not None:
        # The table if ancestry already paid for it, else ONE pid's `ps`.
        if _seat.is_monitor(live, ps=ps if table else lambda: _seat._ps_one(live)):
            return [LINE_LISTENING_MONITOR]
        return [LINE_LISTENING]
    if required is not None and required.on:
        line = deaf_check(seat, session, ps=ps, required=required)
        return [line] if line else []
    return [LINE_NOT_LISTENING]


def _deaf_unsaid(seat, session: str) -> tuple[list[str], str | None]:
    """With the hooks OFF: the deaf check's line as a list
    of none or one, unless it is what this session was last given - said
    once, then only on change, like every state line - and the record to
    write AFTER the emit, or None when nothing changed ("" records silence,
    so a DEAF that comes back is said again). It keeps its own record
    (`said-deaf`), apart from the state line's. A seat that is not required
    (or has the pager off) reads and writes no record at all: off keeps no
    record (D11), and only a required seat can be told DEAF."""
    if not _seat.pager_enabled() or session in ("", "-"):
        return [], None
    required = _seat.pager_required(seat, session)
    if not required.on:
        return [], None
    line = deaf_check(seat, session, required=required) or ""
    if (_seat.read_said(seat, session, _seat.SAID_DEAF_STAMP) or "") == line:
        return [], None
    return ([line] if line else []), line


#: The not-listening edge of D11, PROVISIONAL: the not-listening line is said
#: ONCE, like the others, because the Stop hook already nags at every turn
#: end. To say it on every batch instead, set this False: that line then skips the
#: record comparison, and nothing
#: else changes (tests/test_pager_hook.py pins both answers).
NOT_LISTENING_ONCE = True


def _unsaid(seat, session: str, lines: list[str]) -> list[str]:
    """LINES - a state line or a settings fault's line, as a list of none or
    one - unless it is what the ride-along last gave this session (only
    notify on a change).

    A record that cannot be read, and a session of `-`, which keeps none,
    say the line: a repeat is the safe failure."""
    if not lines:
        return []
    if not NOT_LISTENING_ONCE and lines == [LINE_NOT_LISTENING]:
        return lines
    if _seat.read_said(seat, session) == "\n".join(lines):
        return []
    return lines


#: The turn-start event the plugin also runs `pager hook batch` on
#: (one script, one scoop). Its payload names it
#: in `hook_event_name`, as PostToolBatch's does.
UPS_EVENT = "UserPromptSubmit"
#: What Claude Code's notice turn's prompt starts with (measured): the ONLY
#: field that tells a
#: notice from a typed prompt - the two payloads carry the same keys.
TASK_NOTIFICATION = "<task-notification>"


def _ups_carries(payload: dict) -> bool:
    """Does this UserPromptSubmit run the ride-along?

    Never for the pager off, a session or seat it cannot name, or hooks
    not on in the settings file (READ here, never created: a turn start
    that does not carry writes nothing). Then:

    - a `<task-notification>` prompt carries only if THIS run unlinks the
      carry stamp the page wrote (`_seat.claim_carry`): of two runs, one;
    - any other prompt is typed, and carries only when the host allows it:
      `set carry-prompt on` AND carry not killed by `set carry off` (the
      kill switch wins). No stamp is needed or taken.

    The subagent case (`agent_id`, E3) is refused before this is asked.
    Never raises: a gate that cannot decide does not carry."""
    try:
        if not _seat.pager_enabled():
            return False
        seat = _seat.resolve()
        session = _seat.session_id()
        if session == "-":
            return False
        settings = _settings.read()
        if settings.fault or not settings.hooks:
            return False
        prompt = str(payload.get("prompt") or "")
        if prompt.lstrip().startswith(TASK_NOTIFICATION):
            return _seat.claim_carry(seat, session)
        from . import rules
        host = rules.read_settings()
        return rules.carry_on(host) and rules.carry_prompt_on(host)
    except Exception:  # noqa: BLE001 - see the docstring
        return False


def _log_hook(via: str, text: str, *, seat, payload: dict, event: str, started: float,
              ride: "_injectlog.Ride | None" = None, session: str | None = None) -> None:
    """The inject row for a hook that just emitted TEXT. Only an
    exit-2 Stop line and an `additionalContext` value reach the model, so
    only those are logged; a stderr line at exit 0 is the transcript's.
    `ms` is hook entry (STARTED) to the emit. Never raises."""
    try:
        _injectlog.write(_seat.session_id() if session is None else session,
                         via=via, s=text, seat=seat, hook_event_name=event,
                         tool_use_ids=_injectlog.tool_use_ids(payload), ride=ride,
                         ms=_injectlog.elapsed_ms(started, time.monotonic()))
    except Exception:  # noqa: BLE001 - a row is never why a hook fails
        pass


#: Seconds into `pager hook batch` past which it takes nothing.
#: Claude Code cancels the hook at its `"timeout": 5` (hooks/hooks.json) and
#: discards its output, so a giving that late can be lost. Half the budget
#: leaves the rest for the scoop itself (about 0.15 s measured).
#: Interpreter start is before the clock starts; the margin absorbs it.
HOOK_GIVE_BY = 2.5


@hook.command(
    "batch",
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="",
    short_help="PostToolBatch hook: the ride-along's mail, then the state line",
)
def hook_batch() -> None:
    """pager hook batch - the ride-along, once per batch of tool calls"""
    started = time.monotonic()           # the clock the give-by is on
    ctx = click.get_current_context()
    payload = _hook_payload()
    # A hook's plain stdout reaches the transcript, not the model (measured:
    # the state line ran - ride-along stamp touched - and the model answered
    # NONE). What the model reads is this JSON, the form the notifications
    # carrier uses.
    event = str(payload.get("hook_event_name") or "PostToolBatch")
    if payload.get("agent_id"):
        # Claude Code runs this hook for a SUBAGENT's batches too,
        # with the PARENT's session id in the env and in the payload
        # (measured on 2.1.280). A scoop here would hand the seat's mail to
        # the subagent's context and mark it given to the seat - so the
        # listener never pages it (five letters were lost this way). `agent_id` is the one field present only
        # inside a subagent; `agent_type` is NOT a discriminator (a main
        # thread run with --agent carries it too).
        #
        # So a subagent's batch gives nothing, stamps nothing (it is not
        # the seat's tool call; a busy stamp would defer a page to a
        # ride-along that cannot come) and SAYS nothing: both state lines
        # are instructions about the SEAT's listener - "run `pager listen`"
        # would have the subagent arm on the seat's box, "run `pager stop`"
        # would have it stop the seat's. The queue waits for the main
        # thread's next batch, or the listener's page.
        ctx.exit(0)
        return
    if event == UPS_EVENT and not _ups_carries(payload):
        # A turn start that does not carry is a
        # strict no-op - nothing read but the gate, nothing written, nothing
        # said - exactly what UserPromptSubmit did before it was registered.
        ctx.exit(0)
        return

    #: The settings drift and copied lines, when due (below), each with its
    #: own record: they lead whatever this run says, and are recorded as
    #: said after the first emit.
    notice: list[tuple[str, str]] = []
    #: What the scoop hands up for the row. A path
    #: that never runs the scoop logs it as it starts: no items, zero counts.
    ride = _injectlog.Ride()
    #: Did this run print anything? The empty-run counter
    #: counts only a run that got past every gate, ran the scoop and didn't.
    printed: list[bool] = []

    def emit(lines: list[str]) -> None:
        lines = [line for line, _ in notice] + lines
        if lines:
            printed.append(True)
            text = "\n".join(lines)
            click.echo(json.dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": text}}))
            # The ONE place that holds the exact additionalContext
            # value, on every path that prints (§16.1); after the echo, and
            # never raising. A run that prints nothing logs nothing.
            _log_hook(_injectlog.VIA_PROMPT if event == UPS_EVENT else _injectlog.VIA_BATCH,
                      text, seat=seat, payload=payload, event=event, started=started, ride=ride)
        for line, record in notice:
            _seat.write_said(seat, drift_session, line, name=record)
        notice.clear()

    if not _seat.pager_enabled():
        ctx.exit(0)                      # SESSIONMAIL_PAGER=off wins, and creates nothing (D7)
        return
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity:
        ctx.exit(0)                      # a plain shell: no seat, nothing to say, nothing created
        return
    # tasks.md 3.2: the settings file decides
    # whether the ride-along speaks. This hook, and only this one, creates a
    # missing file (D3) - OFF, from the template, never replacing (D4).
    settings, created = _settings.ensure()
    if created:
        # The run that CREATED the file, and only that run, says the note.
        # Nothing else: the file it just wrote says OFF.
        emit([SETTINGS_CREATED.format(path=_settings.path())])
        ctx.exit(0)
        return
    # session-settings 3.1: an
    # old host file that differs from the new one was kept, not linked, and
    # the two now drift. Said ONCE per session, on or off (the off may BE the
    # drift: an old file saying true, a new one saying false), with its own
    # record so the state line's record is untouched.
    drift_session = _seat.session_id()
    if _settings.drift():
        line = SETTINGS_DRIFT.format(old=_settings.old_path(), path=_settings.path())
        if _seat.read_said(seat, drift_session, _seat.SAID_DRIFT_STAMP) != line:
            notice.append((line, _seat.SAID_DRIFT_STAMP))
    # Both settings folders real: the old files were copied, not moved. ONE
    # line, once per session, on or off; nothing is written to either folder
    # and nothing is read from the old one (a stat of each). A folder that
    # cannot be searched makes `copied` RAISE: then there is no
    # copied line. A missing file was already refused by `ensure` (a fault,
    # nothing written, said below); a file that is there is read and used.
    try:
        copied = _settings.copied()
    except OSError:
        copied = False
    if copied:
        line = SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
        if _seat.read_said(seat, drift_session, _seat.SAID_COPIED_STAMP) != line:
            notice.append((line, _seat.SAID_COPIED_STAMP))
    if settings.fault:
        # PROVISIONAL: a fault is OFF but never silent - ONE
        # line, the path, "treated as off" and why. No mail, no state line,
        # no take, mark or ride-along stamp, never exit 2. Said once, then
        # again only when it changes (D11, tasks.md 3.5): its one write is
        # the record of having said it, after the emit.
        # Stranded with both folders real: the copied line (above) is the
        # one said about the old folder, never the stranded line too.
        if settings.state != _settings.STRANDED:
            lines = [SETTINGS_FAULT.format(path=_settings.path(), error=settings.why)]
        elif copied:
            lines = []
        else:
            lines = [SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)]
        session = _seat.session_id()
        said = _unsaid(seat, session, [" ".join(line.splitlines()) for line in lines])
        # A fault is OFF, and the deaf check runs when off.
        deaf, deaf_record = _deaf_unsaid(seat, session)
        emit(said + deaf)
        if said:
            _seat.write_said(seat, session, said[0])
        if deaf_record is not None:
            _seat.write_said(seat, session, deaf_record, name=_seat.SAID_DEAF_STAMP)
        ctx.exit(0)
        return
    if not settings.hooks:
        # Off on purpose: silent, mail included - no take, no mark, no stamp
        # (off means off). The drift line, if due, is one exception (above).
        # The other is the deaf check: when this seat's pager is
        # REQUIRED and nothing listens, its DEAF line is the one thing said,
        # once, then only on change. Not required, muted, listening or in
        # the grace: silent, as before.
        session = _seat.session_id()
        deaf, deaf_record = _deaf_unsaid(seat, session)
        emit(deaf)
        if deaf_record is not None:
            _seat.write_said(seat, session, deaf_record, name=_seat.SAID_DEAF_STAMP)
        ctx.exit(0)
        return
    session = _seat.session_id()
    # D11: the state line only when it differs from the last
    # one this session was given. Mail is never filtered: it always rides.
    # On a required seat the not-listening line is the deaf
    # check's (DEAF with its reason, or nothing in the grace).
    state = _unsaid(seat, session,
                    _state_lines(seat, session, required=_seat.pager_required(seat, session)))

    def emit_with_state(lines: list[str]) -> None:
        emit(lines + state)
        if state:
            # AFTER the emit, the order the scoop keeps for marks: a crash
            # between the two repeats the line, never loses it.
            _seat.write_said(seat, session, "\n".join(state))

    # c89420a dropped the `not seat.subscriptions.is_dir()`
    # gate: it meant "seat not born yet", and born files are retired, so the
    # scoop runs and makes the queue on demand.
    if session == "-":
        emit_with_state([])            # no session: the line (if any) is the whole message
        ctx.exit(0)
        return
    try:
        # Session-settings 3.3a:
        # the move FIRST, then the reads. The touch below would
        # move a not-yet-moved session too, but only after the marks were
        # read from `mail/` - empty then - so the first hook after the merge
        # rode a done mail once more and skipped the "N not done" line.
        _seat.ensure_root(session)
        marks = _seat.read_marks(seat, session)          # session-keyed since 38f1e65
        events = _seat.read_mark_events(seat, session)
    except (_seat.Malformed, OSError) as exc:
        click.echo(f"pager hook batch: could not look: {exc}", err=True)
        emit_with_state([])
        ctx.exit(0)
        return
    _seat.touch(seat, _seat.RIDE_ALONG_STAMP, session)   # a tool call just ran: busy
    if event != UPS_EVENT:
        # A batch drops a stale carry stamp BEFORE its scoop, so a
        # stamp cannot outlive one batch. Measured G1 (CC 2.1.291): a notice
        # that lands mid-turn fires its UserPromptSubmit AFTER this batch's
        # PostToolBatch, so this scoop gives the mail and that UPS, finding
        # no stamp, stays silent: once, at the same boundary.
        _seat.claim_carry(seat, session)
    # Design A6: a bad rules line that appeared since the banner (or since
    # the last ride-along) is named ONCE, here, file and line. Recorded as
    # named only once the lines have gone out, so a scoop that could not
    # look names it next time instead of never.
    #
    # GUARDED: a bug in the rules reader would otherwise
    # traceback this hook on EVERY tool call, before the scoop runs - the
    # ride-along dead everywhere (a vanished-queue shape). On any error: no bad
    # lines, no actions on the lines, and the scoop runs as it always did.
    from . import rules
    try:
        layers_now = rules.load_layers(seat, session)
    except Exception:  # noqa: BLE001 - see above
        layers_now = None
    named = named_bad_lines(session)
    bad = ([bad_line_text(e) for e in layers_now.errors if str(e) not in named]
           if layers_now is not None else [])
    said: list[bool] = []

    def emit_all(lines: list[str]) -> None:
        emit(bad + lines + state)
        said.append(True)
        if state:
            # AFTER the emit, as emit_with_state does for the
            # early exits - a crash between the two repeats the line.
            _seat.write_said(seat, session, "\n".join(state))

    # The scoop's room is what the cap leaves after every other
    # line this one string carries. F2: past HOOK_GIVE_BY seconds into the
    # hook, it takes nothing (a hook cancelled at its timeout discards its
    # output); the next batch gives.
    _scoop(ctx, seat, session, marks, events, datetime.now().astimezone(),
           nudge=False, quiet=False, emit=emit_all, layers=layers_now,
           room=HOOK_CONTEXT_MAX - _cost([line for line, _ in notice] + bad + state),
           give_by=started + HOOK_GIVE_BY, ride=ride)
    if layers_now is not None:
        now_named = {str(e) for e in layers_now.errors}
        if said and now_named != named:
            record_named(session, now_named)
    if notice:
        emit([])        # a quiet batch still says a due drift line, alone
    if not printed:
        # Spec "Empty runs are counted, not logged":
        # past every gate - a carrying turn start or a batch, a seat, hooks
        # on, a session - the scoop ran and nothing was said. No row; one
        # bump of the cumulative counter the listeners' `exit` rows read.
        # Every other empty run returned above and never reaches here.
        _injectlog.bump_quiet(session, _injectlog.elapsed_ms(started, time.monotonic()))
    ctx.exit(0)
    ctx.exit(EX_PAGE)


#: `pager hook start`'s lines: each exits 0, and
#: each is the ONE line the hook says when a box could not be had.
START_NO_SESSION = "sessionmail: no session id - no box"
START_NO_ROOT = ("sessionmail: no mail root at {root} - no box born; "
                 "mail to this session will bounce")
START_FAILED = "sessionmail: {why}"


def _start_birth(payload: dict, env: dict[str, str]) -> tuple[list[str], bool, "_seat.Seat | None", str]:
    """The SessionStart birth, UNGATED (a box is an address, not speech): (lines to say,
    failed, seat, session).

    The session id has ONE resolver, `_seat.session_id`, over ENV; the
    payload's `session_id` is overlaid only when ENV has none (design.md
    2.3), and when the two disagree ENV wins - it is what the listener and
    the generator read - and the hook says so."""
    lines: list[str] = []
    session = _seat.session_id(env)
    given = str(payload.get("session_id") or "").strip()
    if session == "-" and given:
        env[_seat.SESSION_VARS[0]] = given
        session = _seat.session_id(env)
    elif given and session != given:
        lines.append(f"sessionmail: the harness said session {given}, the environment "
                     f"{session} - the environment's box is the one born")
    if session == "-":
        return [START_NO_SESSION], True, None, session
    try:
        seat = _seat.resolve(env)
    except _seat.NoIdentity:
        seat = None
    birth = _birth.ensure_session_box(
        session, seat, env=env, make_root=False, regen="detach",
        record_source=str(payload.get("source") or "-"), via="pager hook start")
    if birth.error is not None:
        if birth.error.startswith("no mail root at "):
            line = START_NO_ROOT.format(root=birth.box.parent)
        else:
            line = START_FAILED.format(why=birth.error)
        return lines + [line], True, seat, session
    return lines + [BORN_LINE.format(box=birth.box, session=session)], False, seat, session


@hook.command(
    "start",
    context_settings=CONTEXT_SETTINGS,
    cls=PageCommand,
    usage="",
    short_help="SessionStart hook: birth this session's box, hooks on or off",
)
def hook_start() -> None:
    """pager hook start - the SessionStart hook: a box from the first moment"""
    started = time.monotonic()           # the row's `ms` is from here
    ctx = click.get_current_context()
    payload = _hook_payload()
    if payload.get("agent_id"):
        # SessionStart does not fire for a subagent (measured, design.md
        # 2.1); if a harness ever sends one, it births nothing under the
        # parent's id and says nothing - the guard `hook batch` keeps.
        ctx.exit(0)
        return
    event = str(payload.get("hook_event_name") or "SessionStart")
    env = dict(os.environ)
    try:
        lines, failed, seat, session = _start_birth(payload, env)
    except Exception as exc:  # noqa: BLE001 - R4: a hook is never why a session cannot start
        lines, failed, seat, session = [f"sessionmail: hook start could not finish - {exc}"], True, None, "-"
    # THE ONE IF: the birth above is ungated; what the hook SAYS is gated -
    # SESSIONMAIL_PAGER=off, and the settings file read, never written
    # (missing, off or faulty = silent). Saying the arm line regardless of
    # the switch, if that is ever wanted, is THIS line, and only this line.
    if not (_seat.pager_enabled() and _settings.read().hooks):
        if failed:
            click.echo(" ".join(lines), err=True)    # unsaid to the model; the hook log keeps why
        ctx.exit(0)
        return
    # The box line ONLY - never a state line.
    # Claude Code runs this hook and starts the pager monitor in parallel,
    # and nothing orders the two: a state line read here meets a monitor
    # that has not stamped `listening` yet, and says "not listening" to a
    # seat about to be heard, which then arms a SECOND listener. The state is
    # the ride-along's and the Stop hook's to say (ratified
    # specs/pager/spec.md "two places"): `hook batch` says it on the first
    # PostToolBatch (a UserPromptSubmit only when it carries; a typed prompt
    # does not by default), and Stop at the end of the first turn, by when
    # the monitor has had time to arm. So this hook records no said state
    # either: a deaf seat's first batch must still find "not listening"
    # unsaid, and say it.
    text = "\n".join(lines)
    click.echo(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}))
    # A row only when the string is not empty - this echo prints the
    # JSON either way - and under the session the box was
    # born for (`-`: none, so nothing).
    _log_hook(_injectlog.VIA_START, text, seat=seat, payload=payload, event=event,
              started=started, session=session)
    ctx.exit(0)


# ── `pager settings` (migrate: session-settings 3.2 part 2; and install) ────
#
# HIDDEN: neither verb is an agent's. `migrate` a host runs once; `install` a
# LAUNCHER runs before it spawns a seat. So neither is on the page, and so
# not an MCP tool (`_mcp.tools` skips a hidden verb, as `_params` skips a
# hidden option). Part 1's NOT_CONVERTED line names `migrate`; the launchers'
# asks (sessiontools `session start`, taskmaster `tm kick`) name `install`.


@cli.group(
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="install | migrate [--dry-run]",
    hidden=True,
    short_help="the mail settings: install the schema; the move to ~/.session",
)
def settings() -> None:
    """pager settings - install the mail schema; the move to ~/.session"""


#: `pager settings install` on a stranded host: refused, nothing written.
EX_STRANDED = 3
#: ... and with both settings folders real (copied, not moved): refused.
EX_COPIED = 4


@settings.command(
    "install",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="",
    short_help="the mail schema and template, before a launch",
    epilog=unwrapped(
        f"""Writes this release's mail.schema.json and mail.template.json into
{_settings.SHOWN_DIR}, each only when it is missing or differs,
and makes the directory if it has to. Run it BEFORE a launcher writes a
session's birth file: that writer reads SESSIONMAIL_SUBSCRIBE only when
the installed schema names it, so on a HOME no hook has run under yet,
the first seat is born deaf without it. Idempotent: a second run writes
nothing.
It never writes mail.json: the first PostToolBatch hook creates that and
says so. A pager birth (`pager listen`, the SessionStart hook) installs
the same two files; this is the verb for code that runs before either.
Prints one line per file written, or one line saying both are current.
A host whose settings in {_settings.SHOWN_LEGACY_DIR} have not been moved
gets nothing: move the files in it to {_settings.SHOWN_DIR} first.
With both folders present, the old one has to go before the schema can
be refreshed.

Exit: 0 installed, or already current · 2 a file could not be written,
or a settings folder could not be checked (named) · 3 refused: the old settings are not moved yet (both named) ·
4 refused: both folders are present; remove the old one (both named)"""
    ),
)
def settings_install() -> None:
    """pager settings install - the mail schema and template, before a launch"""
    from . import _settings
    ctx = click.get_current_context()
    d = _settings.path().parent
    # Copied implies stranded; asked in this order, a raise names its folder.
    # A folder that cannot be searched is "cannot tell": refused,
    # nothing written, never read as "not stranded".
    try:
        is_stranded = _settings.stranded()
    except OSError as err:
        _say(f"pager settings install: refused: {_settings.unchecked_why(err)}", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    try:
        is_copied = is_stranded and _settings.copied()
    except OSError as err:
        _say(f"pager settings install: refused: {_settings.unchecked_why(err, _settings.SHOWN_DIR)}",
             err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    if is_copied:
        line = SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
        _say(f"pager settings install: refused: {line.removeprefix('sessionmail: ')}", err=True)
        ctx.exit(EX_COPIED)
        return
    if is_stranded:
        line = SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)
        _say(f"pager settings install: refused: {line.removeprefix('sessionmail: ')}", err=True)
        ctx.exit(EX_STRANDED)
        return
    wrote = _settings.install(make=True)
    want = {_settings.SCHEMA: _settings.schema(), _settings.INSTALLED_TEMPLATE: _settings.template()}
    bad = []
    for name, text in want.items():
        target = d / name
        try:
            ok = (target.is_file() and not target.is_symlink()
                  and target.read_bytes() == text.encode("utf-8"))
        except OSError:
            ok = False
        if not ok:
            bad.append(target)
    for target in wrote:
        _say(f"pager settings install: installed {target}")
    if bad:
        for target in bad:
            _say(f"pager settings install: could not write {target}",
                 err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    if not wrote:
        _say(f"pager settings install: {_settings.SCHEMA} and {_settings.INSTALLED_TEMPLATE} "
             f"already current in {d}")


@settings.command(
    "migrate",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[--dry-run]",
    short_help="[--dry-run]    a seat's old box files into its mail.json map",
    epilog=unwrapped(
        """Each seat's box files in ~/.sessionmail/seats/<seat>/subscriptions/
become `true` keys of ~/.session/seats/<seat>/settings/mail.json; each
file's note goes to the seat's log/settings.jsonl, and the file is
removed, so a second run does nothing. The map's own value wins over a
file. An agent's bare name stays put and its block for the agent MD is
printed; a session id's folder is listed; saved queries and seat rules
files are left. Then each LIVE session (live, with a box): its box files
in ~/.sessionmail/sessions/<sid>/subscriptions/ become `true` keys of
~/.session/sessions/<sid>/settings/mail.json, and its `address` file
the `address` key; each converted file is removed. A session that is
not live is never read. --dry-run says the same and writes nothing, and is open
to everyone; the run itself is a privileged seat's, or a person's with no seat.

Exit: 0 ok · 2 refused, or a box could not be moved (named)"""
    ),
)
@click.option("--dry-run", "dry_run", is_flag=True, help="say what would move; write nothing")
def settings_migrate(dry_run: bool) -> None:
    """pager settings migrate - a seat's old box files into its mail.json map"""
    from . import _migrate, _subscribe
    ctx = click.get_current_context()
    who = _subscribe.caller()
    if not dry_run and who.seat is not None and not who.privileged:
        _say(f"pager settings migrate: refused: it writes every seat's and live session's map, so it is "
             f"for {who.may}, or a person's with no seat (you are {who.who}); "
             "--dry-run is open to everyone", err=True)
        ctx.exit(EX_COULD_NOT_LOOK)
        return
    argv = "pager settings migrate" + (" --dry-run" if dry_run else "")
    report = _migrate.migrate(dry_run=dry_run, by=who.who, argv=argv)
    for line in _migrate.lines(report):
        _say(line)
    if report.changed:
        _ok, said = _subscribe.rebuild()
        _say(said)
    if report.errors:
        ctx.exit(EX_COULD_NOT_LOOK)


from ._mcp import attach  # noqa: E402  - the `mcp` verb, declared once for the three

attach(cli, CAPABILITY)


# `python -m sessionmail.pager` IS `pager`. Without this the module imports,
# does nothing and exits 0 (measured), and the MCP send
# door starts its child exactly this way (openspec/changes/agents-send-door,
# "Running the module sends, or says why not - it never does nothing").
# `prog_name`, or the page says `Usage: pager.py ...`.
if __name__ == "__main__":
    cli(prog_name="pager")
