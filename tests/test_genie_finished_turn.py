"""pager-genie, the FINISHED-TURN path end to end: tasks.md 3.5, 3.6, 3.7, 3.17.

openspec/changes/pager-genie @ d156f13. The behaviour proven, against a
SYNTHETIC keeper mail (the spec's headers, both spellings where it matters):

- 3.5 *"Hey everyone" and "not subscribed", over `heard+<device>`* (D3, D14):
  three sessions on `heard+airpods`, one `everyone=everyone` turn reaches
  each box once and one session's `done` does not hide it from another; a
  turn To `heard+airpods` never reaches a session on `heard+delta` only; a
  session on the bare `heard` does not get a claimed device's turn. Spec:
  *Hey everyone*; *Not subscribed, not heard*; *A bare ears subscription
  does not hear a claimed device*; *Two sessions, one device*.
- 3.6 *Succession, the sessionmail half* (D3, N8): a device subscription is
  a SESSION subscription written at birth from `SESSIONMAIL_SUBSCRIBE`; a
  successor holds only turns after its own subscription and is never paged
  for one given to its predecessor; the predecessor's OWN `message
  subscription remove heard+<device>` ends its turns. Spec: *Stale voice
  after a succession*; *A settled predecessor stops hearing the room*. As
  tasks.md 3.6 orders, nothing here tests O10's old "boot status, unseen"
  answer.
- 3.7 *The page age for voice*: a voice mail to a BUSY session is given by
  the ride-along with no page inside the page age, and pages past it; an
  idle one is paged at once. No delay (N2): today's page age, unchanged.
  Spec: *The pager wakes only a stopped agent*; *Hey Eve, while Eve is busy*.
- 3.17 *Wire the `Ears-Rule` parser into SM-474's `call:` condition* (D14):
  `Facts.calls` is 3.21's calls feed (empty unless trusted voice), and the
  built-in voice default and the narrowed locked voice breakthrough (O11)
  read it per entry, never a glob. Spec: *Asleep, a call still breaks
  through and a bare name does not*.
- And the slice's own lines: a turn whose `Ears-Rule` CALLS the seat WAKES
  it (a stopped seat is paged); any other finished turn RIDES along and
  never pages; a draft (a partial) never wakes.
- SM-782: the synthetic keeper mail is UNSIGNED, so it is UNVERIFIED voice.
  Its calls still page through the built-in `carry` lines (so "hey eve"
  works while the keeper is unsigned), but a breakthrough on `call:` is
  clamped to `carry` and never passes a focus mode. A test that means a
  TRUSTED turn (the keeper signed it) says so: `KEEPER_SIGNED` for a pure
  judgement, `keeper_signs` for a listener or a ride-along.

What pages is SM-474's judgement (`rules.judge`, its built-in layer); this
file adds no wake rule and proves the two halves meet. The 3.17 breakthrough
is a LOCKED-FILE line (*"the locked voice breakthrough in place"* is the
scenario's GIVEN): the tests write it, nothing in `src/` ships it.

HERMETIC. Delivery runs over test_message_subscription.py's synthetic fleet:
a mail root, a sessionmail home and the postfix maps under tmp_path, fake
postconf/postmap/ps, live sessions as sidecars, and the REAL generator (as
SM-374's `fleet` fixture runs it) writing its maps under tmp, never postmap.
Each mail is then delivered by test_alias_expansion.py's transcription of
postfix's own alias expansion over THOSE maps, into the tmp maildirs. The
pager runs as `pager listen` / `pager status --new` in-process (CliRunner),
on those tmp boxes, with a fake idle state: no live listener, no real
`~/.mail`, `~/.sessionmail`, notmuch index or MTA (conftest).
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from _seatmap import session_env
from conftest import KEEPER_SIGNED
from sessionmail import _seat, _voice, message, pager, rules
from test_alias_expansion import deliver as resolve  # postfix's expansion, transcribed
from test_message_subscription import (  # noqa: F401  (fleet is a fixture)
    IVAN_SID, HOST, OTHER, SID, _maildir, as_seat, fleet, make_live, rows,
)
from test_subscription_ears_seat import ears  # noqa: F401  (a fixture: the one ears box)

EX_PAGE, EX_TIMEOUT = 0, 124
SUFFIX = "example.ts.net"          # set explicitly, both host forms (SM-674: no default)
FQDN = f"{HOST}.{SUFFIX}"
DEVICE = "airpods"                       # the synthetic mail's device
IDLE_T = 60.0
QUIET = 1.2                              # how long a mail that must NOT page is watched

#: The sessions, by seat. eve and ivan are the fleet's own (OTHER, IVAN_SID).
EVE, IVAN = OTHER, IVAN_SID
RUPERT = "d0c70000-1111-2222-3333-444455556666"        # the third "hey everyone" ear
DELTA_ONLY = SID                                        # the fleet's worker, on heard+delta
BARE = "ba2e0000-1111-2222-3333-444455556666"           # a session on the bare `heard`
DOC_A = "d0ca0000-1111-2222-3333-444455556666"          # succession: the predecessor
DOC_B = "d0cb0000-1111-2222-3333-444455556666"          # succession: the successor
ENGINEER = "e9e90000-1111-2222-3333-444455556666"       # a PLUS-GRAMMAR seat
SEATS = {EVE: "eve", IVAN: "ivan", RUPERT: "rupert", DELTA_ONLY: "worker",
         BARE: "kith", DOC_A: "rupert", DOC_B: "rupert", ENGINEER: "sessionmail+engineer"}

_n = 0


def _mail(sid: str) -> Path:
    """A session's mail state since session-settings 3.3a: under the session
    dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/` (spelt out,
    not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


# ── the synthetic keeper mail (spec headers; D16: both spellings) ──────────

def turn(mid: str, rule: str | None, *, device: str = DEVICE, prefixed: bool = False,
         sender: str | None = None, words: str | None = None) -> bytes:
    """One FINISHED turn as the keeper mails it (X1, owed): To AND From
    `heard+<device>@<host>` (R10, P-2), `Ears-Kind`, `Ears-Device`,
    `Ears-Speaker`, `Ears-Seq/Start/End`, and `Ears-Rule` keyed by address
    (N10). PREFIXED writes every `Ears-` name in the `X-` form (the keeper
    as built today, D16)."""
    x = "X-" if prefixed else ""
    addr = f"heard+{device}@{HOST}"
    head = [
        f"From: Ears <{sender or addr}>",
        f"To: {addr}",
        f"Subject: heard {mid}",
        f"Message-ID: <{mid}>",
        "Date: Tue, 01 Jan 2030 12:00:00 +0000",
        f"{x}Ears-Kind: spoken",
        f"{x}Ears-Device: {device}",
        f"{x}Ears-Speaker: judy@{HOST}",
        f"{x}Ears-Seq: 1",
        f"{x}Ears-Start: 2030-01-01T11:59:52+00:00",
        f"{x}Ears-End: 2030-01-01T12:00:00+00:00",
    ]
    if rule is not None:
        head.append(f"{x}Ears-Rule: {rule}")
    body = words if words is not None else f"the words of {mid}, spoken whole"
    return ("\n".join(head) + "\n\n" + body + "\n").encode()


# ── the MTA: the real generator's maps, expanded as postfix expands them ───

def _table(path: Path) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in path.read_text().splitlines() if "\t" in line)


def maps(f) -> tuple[dict, dict]:
    aliases = {k: [d.strip() for d in v.split(",") if d.strip()]
               for k, v in _table(f.maps / "valias").items()}
    return aliases, _table(f.maps / "vmailbox")


def post(f, raw: bytes, to: str | None = None) -> Counter:
    """Deliver RAW To TO (default: its own `To:`, the device's address)
    through the fleet's CURRENT maps: one file per copy
    in each maildir's new/, headed as postfix's virtual(8) heads it
    (`X-Original-To` the address it was sent to, `Delivered-To` the
    recipient this copy is for). Returns copies per box, relative to the
    mail root (`st/heard`, `<sid>`)."""
    global _n
    if to is None:
        to = re.search(rb"^To: (\S+)$", raw, re.M).group(1).decode()
    copies = resolve(maps(f), to)
    for rcpt, box in copies:
        _n += 1
        d = f.agents.parent / box
        name = f"{int(time.time())}.M{_n:06d}P{os.getpid()}.genie"
        (d / "tmp" / name).write_bytes(
            f"X-Original-To: {to}\nDelivered-To: {rcpt}\n".encode() + raw)
        os.rename(d / "tmp" / name, d / "new" / name)
    return Counter(str(Path(box).relative_to("agents")) for _, box in copies)


def held(f, sid: str) -> list[str]:
    """The Message-IDs in SID's box (new/ and cur/)."""
    out = []
    for leaf in ("new", "cur"):
        for p in sorted((f.agents / sid / leaf).glob("*")):
            m = re.search(rb"^Message-ID: <([^>]+)>", p.read_bytes(), re.M)
            if m:
                out.append(m.group(1).decode())
    return out


# ── the fleet, with the genie's sessions ───────────────────────────────────

def live(f, tmp_path, sid: str, *, box: bool = True) -> None:
    """SID live in the fleet as its seat; its box made unless BOX is False
    (a session whose first `pager listen` will birth it)."""
    make_live(tmp_path, sid, SEATS[sid])
    # The old root's dir: the subscriptions walk (`_subscription._owners`)
    # still enumerates `<home>/sessions/*` until 3.3b moves subscriptions
    # into the session's mail.json; a live session is one with a dir there.
    (f.home / "sessions" / sid).mkdir(parents=True, exist_ok=True)
    (f.home / "seats" / SEATS[sid]).mkdir(parents=True, exist_ok=True)
    if box:
        _maildir(f.agents / sid)
        # A subscription's rebuild is the alias half only; a NEW box's
        # mailbox row comes from a whole run, as a birth's doorbell does.
        assert _seat.regen_maps(f.agents).startswith("generator ran"), f.log


@pytest.fixture
def genie(ears, tmp_path, monkeypatch):  # noqa: F811 - the imported fixture
    """The ears fleet, the pager pointed at it: this host's two names for the
    keeper test, a fast poll, today's page age, no listener found by `ps`,
    and every session STOPPED (idle past the threshold) unless a test says
    `running(...)`."""
    monkeypatch.setenv("SESSIONMAIL_HOST", HOST)
    monkeypatch.setenv("MAGICDNS_SUFFIX", SUFFIX)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setenv(_seat.IDLE_SECONDS_VAR, str(IDLE_T))
    monkeypatch.delenv(pager.PAGE_AGE_VAR, raising=False)
    monkeypatch.delenv(rules.PAGE_RULES_VAR, raising=False)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    idle: dict[str, float] = {}
    monkeypatch.setattr(_seat, "idle_seconds",
                        lambda session, now=None: idle.get(session, IDLE_T + 15))
    ears.idle = idle
    return ears


def running(f, sid: str) -> None:
    f.idle[sid] = 0.0                    # a tool call is running


def act_as(monkeypatch, sid: str) -> None:
    as_seat(monkeypatch, SEATS[sid], sid)


def subscribe(monkeypatch, sid: str, box: str) -> None:
    act_as(monkeypatch, sid)
    done = CliRunner().invoke(message.cli, ["subscription", "add", box], prog_name="message")
    assert done.exit_code == 0, done.output


def unsubscribe(monkeypatch, sid: str, box: str) -> None:
    act_as(monkeypatch, sid)
    done = CliRunner().invoke(message.cli, ["subscription", "remove", box], prog_name="message")
    assert done.exit_code == 0, done.output


def listen(monkeypatch, sid: str, timeout: float = QUIET):
    act_as(monkeypatch, sid)
    return CliRunner().invoke(pager.cli, ["listen", "--timeout", str(timeout)],
                              prog_name="pager")


def ride_along(monkeypatch, sid: str) -> str:
    """SID's next tool call: `pager status --new`, its ride-along."""
    act_as(monkeypatch, sid)
    done = CliRunner().invoke(pager.cli, ["status", "--new"], prog_name="pager")
    assert done.exit_code in (0, 1), done.output
    return done.output


def marks(sid: str) -> str:
    path = _seat.marks_path(_seat.resolve({"CLAUDE_CODE_AGENT": SEATS[sid]}), sid)
    return path.read_text() if path.exists() else ""


def paged(result) -> int | None:
    m = re.search(r"pager: (\d+) messages? waiting\.", result.stdout)
    return int(m.group(1)) if m else None


def paged_for(sid: str) -> set[str]:
    """The Message-IDs SID's page marked (`pager wake`)."""
    return set(re.findall(r"([\w.-]+@[\w.-]+)", "\n".join(
        ln for ln in marks(sid).splitlines() if "pager wake" in ln)))


# ═══ 3.5: "hey everyone" and "not subscribed", over heard+<device> ═══════════

@pytest.fixture
def room(genie, tmp_path, monkeypatch):
    """eve, ivan and rupert on `heard+airpods`; the worker on `heard+delta`
    only; kith on the bare `heard`. Each by its OWN session subscription,
    through the verb, so each row is the real generator's."""
    for sid in (RUPERT, BARE):
        live(genie, tmp_path, sid)
    for sid in (EVE, IVAN, RUPERT):
        subscribe(monkeypatch, sid, f"heard+{DEVICE}")
    subscribe(monkeypatch, DELTA_ONLY, "heard+delta")
    subscribe(monkeypatch, BARE, "heard")
    return genie


def test_the_rows_are_the_subscriptions_and_no_box_is_made_for_a_device(room):
    """*A device's turn lands in the one ears box*: the generator wrote no
    row for the device but the subscription's, and no box named for it."""
    aliases, mailboxes = maps(room)
    assert rows(room.valias)[f"heard+{DEVICE}@{HOST}"] == ", ".join(
        [f"heard+{DEVICE}@{HOST}", *(f"{s}@{HOST}" for s in sorted((EVE, IVAN, RUPERT)))])
    assert not (room.agents / DEVICE).exists() and not (room.agents / "st" / DEVICE).exists()
    assert not any(DEVICE in k for k in mailboxes)


@pytest.mark.parametrize("prefixed", [False, True], ids=["Ears-", "X-Ears-"])
def test_hey_everyone_reaches_each_subscriber_once_and_the_ears_box(room, prefixed):
    got = post(room, turn("all1@genie.invalid", "everyone=everyone", prefixed=prefixed))
    assert got == Counter({"st/heard": 1, EVE: 1, IVAN: 1, RUPERT: 1})
    for sid in (EVE, IVAN, RUPERT):
        assert held(room, sid) == ["all1@genie.invalid"]
    for sid in (DELTA_ONLY, BARE):
        assert held(room, sid) == []


@pytest.mark.parametrize("prefixed", [False, True], ids=["Ears-", "X-Ears-"])
def test_hey_everyone_pages_each_idle_listener_once(room, monkeypatch, prefixed):
    """*Hey everyone*: each listener pages once (SM-474's `wake
    call:everyone`, fed by 3.21's calls)."""
    post(room, turn("all2@genie.invalid", "everyone=everyone", prefixed=prefixed))
    for sid in (EVE, IVAN, RUPERT):
        result = listen(monkeypatch, sid, timeout=3)
        assert result.exit_code == EX_PAGE, (sid, result.stdout, result.stderr)
        assert paged(result) == 1, result.stdout
        assert paged_for(sid) == {"all2@genie.invalid"}


def test_one_sessions_done_does_not_hide_the_turn_from_another(room, monkeypatch, keeper_signs):
    """*Hey everyone*: Eve's marks do not hide it from Ivan's ride-along.
    Eve's ride-along gives the turn whole and marks it `done` (3.4); Ivan's
    and Rupert's still give it, whole, after that."""
    words = "hey everyone, the build is green and it is time to stop " * 20
    post(room, turn("all3@genie.invalid", "everyone=everyone", words=words.strip()))
    for sid in (EVE, IVAN, RUPERT):           # every busy session's listener queues it
        running(room, sid)
        assert listen(monkeypatch, sid, timeout=0.3).exit_code == EX_TIMEOUT
    eve = ride_along(monkeypatch, EVE)
    assert words.strip() in eve.splitlines(), eve
    assert re.search(r"done .*all3@genie\.invalid|all3@genie\.invalid.*done", marks(EVE)), marks(EVE)
    for sid in (IVAN, RUPERT):
        assert "all3@genie.invalid" not in marks(sid)
        given = ride_along(monkeypatch, sid)
        assert words.strip() in given.splitlines(), (sid, given)


def test_a_turn_to_one_device_never_reaches_a_session_on_another_only(room, monkeypatch):
    """*Not subscribed, not heard*: the worker holds `heard+delta` only. A
    turn To `heard+airpods` calling the worker reaches nothing of its, and
    its listener is not paged."""
    got = post(room, turn("np1@genie.invalid", f"worker@{HOST}=call"))
    assert DELTA_ONLY not in got and held(room, DELTA_ONLY) == []
    result = listen(monkeypatch, DELTA_ONLY)
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert "np1@genie.invalid" not in marks(DELTA_ONLY)


def test_its_own_device_does_reach_it(room):
    """The control for the above: the worker's subscription is live."""
    got = post(room, turn("np2@genie.invalid", None, device="delta"))
    assert got == Counter({"st/heard": 1, DELTA_ONLY: 1})


def test_a_bare_ears_subscriber_does_not_get_a_claimed_devices_turn(room, monkeypatch):
    """*A bare ears subscription does not hear a claimed device*: a plus row
    is an EXACT key, and postfix tries `user+ext` first."""
    got = post(room, turn("bare1@genie.invalid", f"kith@{HOST}=call"))
    assert BARE not in got and held(room, BARE) == []
    assert listen(monkeypatch, BARE).exit_code == EX_TIMEOUT


def test_a_bare_ears_subscriber_gets_an_unclaimed_devices_turn(room):
    """...and receives only the turns of devices no live subscription has
    claimed (the spec's requirement text), by the bare row, tag carried."""
    got = post(room, turn("bare2@genie.invalid", None, device="phone"))
    assert got == Counter({"st/heard": 1, BARE: 1})


# ═══ the slice's lines: a call WAKES, any other turn RIDES, a draft never ═══

@pytest.mark.parametrize("sender", [None, f"heard+{DEVICE}@{FQDN}"], ids=["short-host", "fqdn"])
@pytest.mark.parametrize("prefixed", [False, True], ids=["Ears-", "X-Ears-"])
def test_a_turn_that_calls_the_seat_pages_it_and_rides_along_on_the_other(
        room, monkeypatch, prefixed, sender):
    """*Hey Eve, heard by Ivan too*: Eve's listener pages once, Ivan's does
    not, and the turn rides along on Ivan's next tool call. From the keeper
    at either of this host's names: the FQDN is the form the keeper will
    send (_voice.py:97-104 @ 754f5b8)."""
    post(room, turn("call1@genie.invalid", f"eve@{HOST}=call", prefixed=prefixed, sender=sender))
    eve = listen(monkeypatch, EVE, timeout=3)
    assert eve.exit_code == EX_PAGE, (eve.stdout, eve.stderr)
    assert paged_for(EVE) == {"call1@genie.invalid"}
    ivan = listen(monkeypatch, IVAN)
    assert ivan.exit_code == EX_TIMEOUT, (ivan.stdout, ivan.stderr)
    assert "call1@genie.invalid" not in marks(IVAN)
    given = ride_along(monkeypatch, IVAN)
    assert "the words of call1@genie.invalid, spoken whole" in given.splitlines(), given
    assert "(ride)" in given, given


@pytest.mark.parametrize("rule", [
    pytest.param(None, id="no-rule"),
    pytest.param(f"eve@{HOST}=mention", id="a-bare-name"),
    pytest.param(f"evette@{HOST}=call", id="a-near-name"),
    pytest.param(f"eve@{HOST}=first, eve@{HOST}=reply, eve@{HOST}=light", id="keeper-rules"),
    pytest.param(f"ivan@{HOST}=call", id="another-seat-called"),
])
def test_any_other_finished_turn_rides_and_never_pages(room, monkeypatch, rule):
    """*A bare name only rides along*; *A near name is not a call*; and the
    keeper's other entries never wake (N10)."""
    post(room, turn("ride1@genie.invalid", rule))
    result = listen(monkeypatch, EVE)
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert paged_for(EVE) == set()
    assert "the words of ride1@genie.invalid, spoken whole" in ride_along(monkeypatch, EVE)


@pytest.mark.parametrize("sender", [
    pytest.param(f"eve@{HOST}", id="not-the-keeper"),
    pytest.param(f"heard+{DEVICE}", id="no-domain"),
    pytest.param(f"heard+{DEVICE}@elsewhere.example", id="another-host"),
])
def test_a_call_on_mail_the_keeper_did_not_send_does_not_page(room, monkeypatch, sender):
    """*A call on mail that is not trusted voice is no call*: the same turn,
    through the same device row, From someone not the keeper. NO-DOMAIN is
    engineer consult (ii): a bare `heard+airpods` names no host, so it is not
    the keeper (`_is_keeper`'s `not at` branch, _voice.py:99 @ 754f5b8): it
    is no call, and the turn RIDES."""
    post(room, turn("spoof1@genie.invalid", f"ivan@{HOST}=call", sender=sender))
    act_as(monkeypatch, IVAN)
    raw = next((room.agents / IVAN / "new").iterdir()).read_bytes()
    assert rules.Facts.from_bytes(raw).calls == ()             # untrusted: no calls
    result = listen(monkeypatch, IVAN)
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert paged_for(IVAN) == set()
    assert "the words of spoof1@genie.invalid, spoken whole" in ride_along(monkeypatch, IVAN)


def test_a_draft_never_wakes(room, monkeypatch):
    """A partial is a `:2,D` file in the ears box's cur/ (N11/N12, P-1). It
    is never copied, so no session's listener meets it; and were the draft
    watch (3.18c, held) to judge one that CALLS the seat, the built-in for a
    draft is `ride *` (P-5 RULED, SM-474's)."""
    raw = turn("draft1@genie.invalid", f"eve@{HOST}=call")
    (room.agents / "st" / "heard" / "cur" / f"{int(time.time())}.M1P1.genie:2,D").write_bytes(raw)
    result = listen(monkeypatch, EVE)
    assert result.exit_code == EX_TIMEOUT, (result.stdout, result.stderr)
    assert held(room, EVE) == []
    act_as(monkeypatch, EVE)
    facts = rules.Facts.from_bytes(raw)
    assert facts.calls == (f"eve@{HOST}",)                   # a call, on the finished turn
    draft = rules.Facts(facts.headers, facts.priority, facts.calls, draft=True)
    layers = rules.load_layers("eve", EVE)
    me = pager.listener_me(_seat.resolve(), EVE)
    assert rules.judge(facts, layers, me).action == "wake"
    assert rules.judge(draft, layers, me).action == "ride"


# ═══ a plus-grammar seat is called by its FULL address, never its base ═════

@pytest.fixture
def engineer_on_airpods(genie, tmp_path, monkeypatch):
    """`sessionmail+engineer`'s session on `heard+airpods`: a plus-grammar
    seat (engineer consult (i))."""
    live(genie, tmp_path, ENGINEER)
    subscribe(monkeypatch, ENGINEER, f"heard+{DEVICE}")
    return genie


def test_a_plus_grammar_seats_me_is_its_full_address(engineer_on_airpods, monkeypatch):
    """`$me` keeps the `+`: the seat's whole local part, then the session."""
    act_as(monkeypatch, ENGINEER)
    me = pager.listener_me(_seat.resolve(), ENGINEER)
    assert me.addresses == ("sessionmail+engineer", ENGINEER)


@pytest.mark.parametrize("key", [
    pytest.param(f"sessionmail+engineer@{HOST}", id="short-host"),
    pytest.param(f"sessionmail+engineer@{FQDN}", id="fqdn"),
    pytest.param("sessionmail+engineer", id="bare"),
])
def test_a_plus_grammar_seat_is_woken_by_a_call_keyed_with_its_full_address(
        engineer_on_airpods, monkeypatch, key):
    f = engineer_on_airpods
    assert ENGINEER in post(f, turn("full1@genie.invalid", f"{key}=call",
                                    sender=f"heard+{DEVICE}@{FQDN}"))
    woke = listen(monkeypatch, ENGINEER, timeout=3)
    assert woke.exit_code == EX_PAGE, (woke.stdout, woke.stderr)
    assert paged(woke) == 1 and paged_for(ENGINEER) == {"full1@genie.invalid"}


@pytest.mark.parametrize("key", [
    pytest.param(f"sessionmail@{HOST}", id="base-short-host"),
    pytest.param(f"sessionmail@{FQDN}", id="base-fqdn"),
    pytest.param("sessionmail", id="base-bare"),
    pytest.param(f"engineer@{HOST}", id="the-role-alone"),
    pytest.param(f"sessionmail+engineer.x@{HOST}", id="a-longer-local"),
])
def test_a_plus_grammar_seat_is_not_woken_by_a_call_keyed_with_its_base(
        engineer_on_airpods, monkeypatch, key):
    """`Me.called` is EXACT on the local part (rules.py:505-517 @ 754f5b8):
    a call keyed with the project base is the project's, not this seat's.
    The turn still reaches the seat, and RIDES."""
    f = engineer_on_airpods
    assert ENGINEER in post(f, turn("base1@genie.invalid", f"{key}=call",
                                    sender=f"heard+{DEVICE}@{FQDN}"))
    quiet = listen(monkeypatch, ENGINEER)
    assert quiet.exit_code == EX_TIMEOUT, (quiet.stdout, quiet.stderr)
    assert paged_for(ENGINEER) == set()
    assert "the words of base1@genie.invalid, spoken whole" in ride_along(monkeypatch, ENGINEER)


# ═══ 3.6: succession, the sessionmail half ══════════════════════════════════

@pytest.fixture
def succession(genie, tmp_path, monkeypatch):
    """Rupert's session A, born with `SESSIONMAIL_SUBSCRIBE=heard+airpods`
    (since task 3.4 the launcher's birth writes it into the session's layer,
    `mail.d/50-env.json`; its first `pager listen` births the box and routes
    it), and nothing else on the device."""
    live(genie, tmp_path, DOC_A, box=False)
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, f"heard+{DEVICE}")
    session_env(DOC_A, f"heard+{DEVICE}")
    born = listen(monkeypatch, DOC_A, timeout=0)
    assert born.exit_code == EX_TIMEOUT, born.output
    assert (genie.agents / DOC_A / "new").is_dir()
    assert f"heard+{DEVICE}" in genie.subscribed(DOC_A)
    assert genie.seat_file("rupert", f"heard+{DEVICE}").exists() is False   # never a seat's
    return genie


def birth_b(f, tmp_path, monkeypatch) -> None:
    live(f, tmp_path, DOC_B, box=False)
    session_env(DOC_B, f"heard+{DEVICE}")          # the launcher's birth (3.4), as A's
    born = listen(monkeypatch, DOC_B, timeout=0)
    assert born.exit_code == EX_TIMEOUT, born.output
    assert f"heard+{DEVICE}" in f.subscribed(DOC_B)


def test_stale_voice_after_a_succession(succession, tmp_path, monkeypatch):
    """*Stale voice after a succession*: A was given turn V whole, and
    settled (its own remove). B boots, subscribes at birth, arms: B is not
    paged for V, and no ride-along of B's gives V. A turn after B's
    subscription is B's."""
    f = succession
    v = turn("v@genie.invalid", f"rupert@{HOST}=call")
    assert post(f, v) == Counter({"st/heard": 1, DOC_A: 1})
    running(f, DOC_A)
    assert listen(monkeypatch, DOC_A, timeout=0.3).exit_code == EX_TIMEOUT
    assert "the words of v@genie.invalid, spoken whole" in ride_along(monkeypatch, DOC_A)
    assert "v@genie.invalid" in marks(DOC_A)                 # given whole: A's mark
    unsubscribe(monkeypatch, DOC_A, f"heard+{DEVICE}")        # A settles

    birth_b(f, tmp_path, monkeypatch)
    assert held(f, DOC_B) == []                              # V stayed where it was
    assert marks(DOC_B) == ""                                # marks are per session
    quiet = listen(monkeypatch, DOC_B)
    assert quiet.exit_code == EX_TIMEOUT, (quiet.stdout, quiet.stderr)
    assert "v@genie.invalid" not in ride_along(monkeypatch, DOC_B)

    assert post(f, turn("w@genie.invalid", f"rupert@{HOST}=call")) == \
        Counter({"st/heard": 1, DOC_B: 1})
    woke = listen(monkeypatch, DOC_B, timeout=3)
    assert woke.exit_code == EX_PAGE, (woke.stdout, woke.stderr)
    assert paged(woke) == 1 and paged_for(DOC_B) == {"w@genie.invalid"}


def test_a_settled_predecessor_stops_hearing_the_room(succession, tmp_path, monkeypatch):
    """*A settled predecessor stops hearing the room*, driven by A's OWN
    `message subscription remove` (the settle trigger itself is
    message-subscriptions 6.6, not this card's). A's window is still open:
    A is still live, its box still there, its listener still armable."""
    f = succession
    birth_b(f, tmp_path, monkeypatch)
    before = post(f, turn("both@genie.invalid", None))
    assert before == Counter({"st/heard": 1, DOC_A: 1, DOC_B: 1})   # both, until A settles

    unsubscribe(monkeypatch, DOC_A, f"heard+{DEVICE}")
    assert f"heard+{DEVICE}" not in f.subscribed(DOC_A)
    assert rows(f.valias)[f"heard+{DEVICE}@{HOST}"] == f"heard+{DEVICE}@{HOST}, {DOC_B}@{HOST}"

    after = post(f, turn("room@genie.invalid", f"rupert@{HOST}=call"))
    assert after == Counter({"st/heard": 1, DOC_B: 1})
    assert "room@genie.invalid" not in held(f, DOC_A)
    assert "room@genie.invalid" in held(f, DOC_B)
    a = listen(monkeypatch, DOC_A)
    assert "room@genie.invalid" not in marks(DOC_A), (a.stdout, a.stderr)


def test_two_boxes_in_subscribe_give_two_session_subscriptions_at_birth(
        genie, tmp_path, monkeypatch):
    """Engineer consult (iv): `SESSIONMAIL_SUBSCRIBE='sessionmail
    heard+airpods'` is split on whitespace (since task 3.4, by the launcher's
    birth: sessiontools 518bd2e splits on commas AND spaces), so a kicked
    seat that APPENDS its device to the variable is born with BOTH as
    session subscriptions, and each box's mail reaches it."""
    _maildir(genie.agents / "st" / "sessionmail")
    assert _seat.regen_maps(genie.agents).startswith("generator ran"), genie.log
    live(genie, tmp_path, DOC_A, box=False)
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, f"sessionmail heard+{DEVICE}")
    session_env(DOC_A, f"sessionmail heard+{DEVICE}")
    born = listen(monkeypatch, DOC_A, timeout=0)
    assert born.exit_code == EX_TIMEOUT, born.output
    for box in ("sessionmail", f"heard+{DEVICE}"):
        assert box in genie.subscribed(DOC_A), (box, born.output)
        assert genie.seat_file("rupert", box).exists() is False      # never a seat's
        assert re.search(rf"subscribe\s+: {re.escape(box)} \(from \${pager.SUBSCRIBE_VAR}, "
                         r"at the launcher's birth\)",
                         born.output), born.output
    assert post(genie, turn("two1@genie.invalid", None)) == Counter({"st/heard": 1, DOC_A: 1})
    got = post(genie, b"From: kith@hq\nTo: sessionmail@hq\nMessage-ID: <two2@genie.invalid>\n"
                      b"Subject: to the project\n\nx\n", to=f"sessionmail@{HOST}")
    assert got[DOC_A] == 1 and got["st/sessionmail"] == 1, got


def test_subscribe_is_birth_only_a_born_session_subscribes_by_hand(
        succession, tmp_path, monkeypatch):
    """The live caveat (engineer consult): the variable is read at BIRTH
    only (since task 3.4 by the launcher, once). A session already born that later
    carries a new box in it is not subscribed by its next `pager listen`;
    `message subscription add` is the way."""
    f = succession
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, f"heard+{DEVICE} heard+delta")
    again = listen(monkeypatch, DOC_A, timeout=0)
    assert again.exit_code == EX_TIMEOUT, again.output
    assert "heard+delta" not in f.subscribed(DOC_A)
    assert DOC_A not in post(f, turn("late1@genie.invalid", None, device="delta"))
    subscribe(monkeypatch, DOC_A, "heard+delta")
    assert DOC_A in post(f, turn("late2@genie.invalid", None, device="delta"))


def test_the_successor_is_not_given_a_mark_of_the_predecessors(succession, tmp_path, monkeypatch):
    """Marks are per session (*"a successor SHALL NOT inherit them"*): A's
    `done` on V is A's alone, and B never meets V to mark it."""
    f = succession
    post(f, turn("vm@genie.invalid", None))
    running(f, DOC_A)
    listen(monkeypatch, DOC_A, timeout=0.3)
    ride_along(monkeypatch, DOC_A)
    unsubscribe(monkeypatch, DOC_A, f"heard+{DEVICE}")
    birth_b(f, tmp_path, monkeypatch)
    assert "vm@genie.invalid" in marks(DOC_A) and "vm@genie.invalid" not in marks(DOC_B)


# ═══ 3.7: the page age for voice ════════════════════════════════════════════

@pytest.fixture
def eve_on_airpods(genie, monkeypatch):
    subscribe(monkeypatch, EVE, f"heard+{DEVICE}")
    return genie


def test_a_busy_session_is_given_the_call_by_the_ride_along_inside_the_page_age(
        eve_on_airpods, monkeypatch):
    """*Hey Eve, while Eve is busy*: her next tool call returns within the
    page age, the pager's own ride-along gives the turn whole, and no page
    is sent (not then, and not past the page age: it was given)."""
    f = eve_on_airpods
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "6")
    running(f, EVE)
    post(f, turn("busy1@genie.invalid", f"eve@{HOST}=call"))
    inside = listen(monkeypatch, EVE, timeout=QUIET)
    assert inside.exit_code == EX_TIMEOUT, (inside.stdout, inside.stderr)
    given = ride_along(monkeypatch, EVE)
    assert "the words of busy1@genie.invalid, spoken whole" in given.splitlines(), given
    assert paged_for(EVE) == set()
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "0.3")             # now well past the page age
    past = listen(monkeypatch, EVE, timeout=QUIET)
    assert past.exit_code == EX_TIMEOUT, (past.stdout, past.stderr)
    assert paged_for(EVE) == set()


def test_a_busy_session_not_given_the_call_is_paged_past_the_page_age(eve_on_airpods, monkeypatch):
    f = eve_on_airpods
    age = 0.6
    monkeypatch.setenv(pager.PAGE_AGE_VAR, str(age))
    running(f, EVE)
    post(f, turn("busy2@genie.invalid", f"eve@{HOST}=call"))
    t0 = time.monotonic()
    result = listen(monkeypatch, EVE, timeout=4)
    took = time.monotonic() - t0
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert took >= age * 0.8, f"paged inside the page age ({took:.2f}s < {age}s)"
    assert paged_for(EVE) == {"busy2@genie.invalid"}


def test_an_idle_session_is_paged_at_once_with_no_delay_for_voice(eve_on_airpods, monkeypatch):
    """N2: no delay. Stopped is T = 0, voice or not, whatever the page age."""
    monkeypatch.setenv(pager.PAGE_AGE_VAR, "6")
    post(eve_on_airpods, turn("idle1@genie.invalid", f"eve@{HOST}=call"))
    t0 = time.monotonic()
    result = listen(monkeypatch, EVE, timeout=4)
    assert result.exit_code == EX_PAGE, (result.stdout, result.stderr)
    assert time.monotonic() - t0 < 2.0


def test_the_page_age_for_voice_is_todays_page_age():
    """3.7 adds no per-voice timing (N2; 3.15 `after:` RETIRED): the one
    knob is SESSIONMAIL_PAGE_AGE_SECONDS, and nothing in the pager names a
    voice page age."""
    import inspect
    src = inspect.getsource(pager)
    assert not re.search(r"(?i)voice[_ ]page[_ ]age|PAGE_AGE_VOICE|EARS_PAGE_AGE", src)


# ═══ 3.17: asleep, a call still breaks through and a bare name does not ═════

#: The NARROWED locked voice breakthrough (O11 as narrowed by N10, design
#: D14: *"wakes a seat in sleep only for a turn that wakes this session by
#: that test"*), as the scenario's GIVEN puts it in place: a LOCKED-file
#: line, read through `call:`, i.e. per entry of 3.21's calls feed.
VOICE_BREAKTHROUGH = "wake! call:$me\nwake! call:everyone\n"


def _layers(home: Path, locked: Path, sid: str = EVE, *, breakthrough: bool = True,
            asleep: bool = True):
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text(VOICE_BREAKTHROUGH if breakthrough else "")
    focus = _mail(sid) / rules.FOCUS_FILE
    focus.parent.mkdir(parents=True, exist_ok=True)
    focus.write_text("sleep\n" if asleep else "off\n")
    return rules.load_layers("eve", sid, {}, home=home, locked=locked)


ENV = {"SESSIONMAIL_HOST": HOST, "MAGICDNS_SUFFIX": SUFFIX}
ME = rules.Me.of(session=EVE, seat="eve", host=FQDN)
NIGHT = datetime(2030, 6, 13, 2, 0).astimezone()


def verdict(raw: bytes, layers, signed=KEEPER_SIGNED) -> tuple[str, str | None, str]:
    """The judgement of RAW; a keeper turn is TRUSTED unless SIGNED says not."""
    v = rules.judge(rules.Facts.from_bytes(raw, ENV, signed=signed), layers, ME, NIGHT)
    return v.action, v.rule, v.layer


CLAMPED = f" {rules.CLAMP_NOTE}"


@pytest.mark.parametrize("prefixed", [False, True], ids=["Ears-", "X-Ears-"])
def test_asleep_a_call_still_breaks_through_and_a_bare_name_does_not(tmp_path, prefixed):
    """THE SPEC'S SCENARIO (3.17). GIVEN Eve in `sleep` and the locked voice
    breakthrough in place; WHEN a turn calling eve arrives, then one naming
    eve without "hey": the first wakes her and she is still in `sleep`; the
    second does not wake her."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE)
    assert [m.mode for m in layers.modes] == ["sleep"]
    called = verdict(turn("s1@genie.invalid", f"eve@{HOST}=call", prefixed=prefixed), layers)
    assert called == ("wake", "wake! call:$me", "locked")
    named = verdict(turn("s2@genie.invalid", f"eve@{HOST}=mention", prefixed=prefixed), layers)
    assert named == ("ride", "ride *", "focus: sleep")
    # still in sleep: the mode is unchanged, and a plain mail To her rides by it
    assert [m.mode for m in layers.modes] == ["sleep"]
    direct = b"From: beta@hq\nTo: eve@hq\nX-Original-To: eve@hq\nSubject: hi\n\nx\n"
    assert verdict(direct, layers) == ("ride", "ride *", "focus: sleep")


def test_asleep_hey_everyone_breaks_through(tmp_path):
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE)
    assert verdict(turn("s3@genie.invalid", "everyone=everyone"), layers) == \
        ("wake", "wake! call:everyone", "locked")


@pytest.mark.parametrize("rule", [f"eve@{HOST}=call", "everyone=everyone"])
def test_asleep_an_unverified_call_does_not_break_through(tmp_path, rule):
    """SM-782: the same turns UNSIGNED. The locked `wake!` on `call:` is
    clamped to `carry` and judged below the focus layer, so sleep holds, as
    it does for plain mail To her."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE)
    got = verdict(turn("u1@genie.invalid", rule), layers, signed=None)
    assert got == ("ride", "ride *", "focus: sleep")
    assert [m.mode for m in layers.modes] == ["sleep"]


def test_awake_an_unverified_call_on_the_locked_breakthrough_carries_from_its_place(tmp_path):
    """SM-782: awake, the clamped locked line carries (a wake, no
    breakthrough), and `pager why` says it was clamped."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE, asleep=False)
    assert verdict(turn("u2@genie.invalid", f"eve@{HOST}=call"), layers, signed=None) == \
        ("wake", "wake! call:$me" + CLAMPED, "locked")


@pytest.mark.parametrize("rule,sender", [
    pytest.param(f"evette@{HOST}=call", None, id="a-near-name"),
    pytest.param(f"ivan@{HOST}=call", None, id="another-seat"),
    pytest.param(f"eve@{HOST}=call", f"eve@{HOST}", id="not-the-keeper"),
    pytest.param(f"eve@{HOST}=call", f"heard+{DEVICE}@elsewhere.example", id="another-host"),
    pytest.param(f"eve@{HOST}=call", f"heard+{DEVICE}", id="no-domain"),
    pytest.param(f"*eve*=call", None, id="a-glob-key"),
])
def test_asleep_nothing_but_a_trusted_call_on_me_breaks_through(tmp_path, rule, sender):
    """Never a glob over the value, and no call on mail that is not trusted
    voice: the breakthrough reads `Facts.calls`, which is empty then."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE)
    got = verdict(turn("s4@genie.invalid", rule, sender=sender), layers)
    assert got == ("ride", "ride *", "focus: sleep")


def test_asleep_without_the_breakthrough_line_a_call_rides(tmp_path):
    """The control: SM-474's built-in `carry call:$me` sits BELOW the focus
    layer, so it is the locked line, and only it, that lets a call through
    sleep."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE,
                     breakthrough=False)
    assert verdict(turn("s5@genie.invalid", f"eve@{HOST}=call"), layers) == \
        ("ride", "ride *", "focus: sleep")


@pytest.mark.parametrize("signed", [KEEPER_SIGNED, None], ids=["trusted", "unverified"])
def test_awake_the_built_in_voice_default_decides_from_the_calls_feed(tmp_path, signed):
    """3.17's other half: with no locked line and no mode, SM-474's built-in
    voice default reads 3.21's calls (P-8: above the Priority lines).
    SM-782: the same for UNVERIFIED voice while `UNVERIFIED_CALLS` is on:
    the built-in lines are `carry`, within what plain mail can do."""
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE,
                     breakthrough=False, asleep=False)
    assert verdict(turn("a1@genie.invalid", f"eve@{HOST}=call"), layers, signed) == \
        ("wake", "carry call:$me", "built-in")
    assert verdict(turn("a2@genie.invalid", "everyone=everyone"), layers, signed) == \
        ("wake", "carry call:everyone", "built-in")
    assert verdict(turn("a3@genie.invalid", f"eve@{HOST}=mention"), layers, signed) == \
        ("ride", "ride *", "built-in")
    non_urgent = turn("a4@genie.invalid", f"eve@{HOST}=call").replace(
        b"Ears-Kind", b"Priority: non-urgent\nEars-Kind", 1)
    assert verdict(non_urgent, layers, signed) == ("wake", "carry call:$me", "built-in")


def test_strict_unverified_voice_calls_nobody(tmp_path, monkeypatch):
    """SM-782's ONE switch flipped to strict: an unverified call is no call,
    so it rides; a trusted call still wakes."""
    monkeypatch.setattr(_voice, "UNVERIFIED_CALLS", False)
    layers = _layers(tmp_path / "home", tmp_path / "etc" / rules.LOCKED_FILE,
                     breakthrough=False, asleep=False)
    called = turn("x1@genie.invalid", f"eve@{HOST}=call")
    assert verdict(called, layers, signed=None) == ("ride", "ride *", "built-in")
    assert verdict(called, layers) == ("wake", "carry call:$me", "built-in")


def test_asleep_end_to_end_the_listener_pages_the_call_and_not_the_name(
        eve_on_airpods, monkeypatch, keeper_signs):
    """The scenario through `pager listen` itself, on Eve's tmp box: the
    locked file is conftest's sandboxed one, the mode her session's own."""
    f = eve_on_airpods
    locked = rules.locked_path()
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text(VOICE_BREAKTHROUGH)
    focus = _mail(EVE) / rules.FOCUS_FILE
    focus.parent.mkdir(parents=True, exist_ok=True)
    focus.write_text("sleep\n")
    post(f, turn("e1@genie.invalid", f"eve@{HOST}=call"))
    woke = listen(monkeypatch, EVE, timeout=3)
    assert woke.exit_code == EX_PAGE, (woke.stdout, woke.stderr)
    assert paged_for(EVE) == {"e1@genie.invalid"}
    assert focus.read_text() == "sleep\n"                     # still in sleep
    post(f, turn("e2@genie.invalid", f"eve@{HOST}=mention"))
    slept = listen(monkeypatch, EVE)
    assert slept.exit_code == EX_TIMEOUT, (slept.stdout, slept.stderr)
    assert paged_for(EVE) == {"e1@genie.invalid"}


def test_asleep_end_to_end_an_unverified_call_does_not_page(eve_on_airpods, monkeypatch):
    """SM-782 through `pager listen`: the same call UNSIGNED, the locked voice
    breakthrough in place and Eve asleep. The clamped line never passes the
    focus mode, so the listener is not paged and she stays in sleep."""
    f = eve_on_airpods
    locked = rules.locked_path()
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text(VOICE_BREAKTHROUGH)
    focus = _mail(EVE) / rules.FOCUS_FILE
    focus.parent.mkdir(parents=True, exist_ok=True)
    focus.write_text("sleep\n")
    post(f, turn("u3@genie.invalid", f"eve@{HOST}=call"))
    slept = listen(monkeypatch, EVE)
    assert slept.exit_code == EX_TIMEOUT, (slept.stdout, slept.stderr)
    assert paged_for(EVE) == set()
    assert focus.read_text() == "sleep\n"
