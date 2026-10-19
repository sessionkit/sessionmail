"""The built-in defaults and the layers: `openspec/changes/pager-filters`,
tasks 2.3 and 2.4.

D4 step 3 (RULED, good defaults): `wake! from:judy@*`, `wake
to:$me`, `ride *`, and START is never a default. D3 (RULED, P2, P3,
P11): the layers, read in this order, first match wins:

0. the LOCKED file `~/.sessionmail/rules.locked`, NEVER read from the
   environment, which agents could change;
1. the session file `~/.sessionmail/sessions/<sid>/rules`;
2. the seat file `~/.sessionmail/seats/<seat>/rules`;
3. the agent MD's lines, one variable `SESSIONMAIL_PAGE_RULES`, one rule
   per line;
4. the built-in defaults.

(0.5, the focus layer, is do-004's.) The roots are injected: every test
here writes under `tmp_path`, and the conftest points the default locked
path at the sandbox, so no test reads the real `~/.sessionmail`.

Every test here was run against a `sessionmail.rules` with no loader first
and failed (the fail-first rule of the slice).
"""

from __future__ import annotations

import os
import pwd
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sessionmail import rules
from sessionmail._seat import Seat
from sessionmail.rules import (
    BUILT_IN_TEXT,
    PAGE_RULES_VAR,
    Facts,
    Layer,
    Layers,
    Me,
    built_in,
    judge,
    load_layers,
    parse,
)

SID = "48c504e1-829c-95ea-295f-2ab6456e2bb2"
HOST = "hq.tailnet.example"
ME = Me.of(session=SID, seat="ivan", host=HOST)
NOW = datetime(2030, 7, 11, 12, 34, 56, tzinfo=timezone.utc)
JUDY = f"Judy <judy@{HOST}>"


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


def mail(*headers: tuple[str, str]) -> Facts:
    head = "".join(f"{name}: {value}\n" for name, value in headers)
    return Facts.from_bytes((head + "\n").encode())


class Roots:
    """A fake host: a sessionmail home and a locked file, both under tmp."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.locked = tmp / "etc" / "rules.locked"

    def write_locked(self, text: str) -> Path:
        self.locked.parent.mkdir(parents=True, exist_ok=True)
        self.locked.write_text(text, encoding="utf-8")
        return self.locked

    def session_file(self, sid: str = SID) -> Path:
        return _mail(sid) / "rules"

    def seat_file(self, seat: str = "ivan") -> Path:
        return self.home / "seats" / seat / "rules"

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, seat: str | Seat | None = "ivan", session: str | None = SID,
             env: dict[str, str] | None = None) -> Layers:
        return load_layers(seat, session, env or {}, home=self.home, locked=self.locked)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def names(layers: Layers) -> list[str]:
    return [layer.name for layer in layers]


# ── 2.3 the built-in defaults ────────────────────────────────────────────────

def test_the_built_ins_are_the_ruled_lines_in_order():
    """D4 step 3's three, as amended by design A1/A3/A5 (do-005): `$operator`
    for `judy@*`, the two Priority lines above `wake to:$me`; and by P-8
    (RULED, do-013): the two call lines above the Priority lines."""
    layer = built_in()
    assert layer.name == "built-in"
    assert [r.text for r in layer.rules] == ["carry! from:$operator", "carry call:$me",
        "carry call:everyone", "ride priority:non-urgent",
        "carry priority:urgent from:$urgent", "wake to:$me", "ride *"]
    assert [r.line for r in layer.rules] == [1, 2, 3, 4, 5, 6, 7]


def test_the_built_in_text_parses_clean():
    got, errors = parse(BUILT_IN_TEXT, "built-in")
    assert errors == ()
    assert len(got) == 7


def test_nothing_starts_by_default():
    """D4: *"START is never a default."* No built-in line is a `start`, so
    with no other layer no mail can be given `start`."""
    assert all(r.verb != "start" for r in built_in().rules)
    for facts in (mail(("From", JUDY)), mail(("X-Original-To", "ivan")),
                  mail(("X-Original-To", "atomnote")), mail()):
        assert judge(facts, [built_in()], ME, NOW).action != "start"


def test_through_a_subscription_alias_it_rides_and_direct_it_wakes(roots):
    """The slice's first criterion, and the spec's *Direct mail wakes,
    subscription mail rides along*: a seat `ivan` subscribed to `atomnote`,
    no rules of its own. The copy that came THROUGH the `atomnote` alias
    carries `X-Original-To: atomnote@...`, and rides; the direct one wakes."""
    layers = roots.load()
    assert names(layers) == ["built-in"]
    through = mail(("From", "eve@hq"), ("To", f"atomnote@{HOST}"),
                   ("X-Original-To", f"atomnote@{HOST}"))
    direct = mail(("From", "eve@hq"), ("To", f"ivan@{HOST}"),
                  ("X-Original-To", f"ivan@{HOST}"))
    got = judge(through, layers, ME, NOW)
    assert (got.action, got.rule, got.layer, got.line) == ("ride", "ride *", "built-in", 7)
    got = judge(direct, layers, ME, NOW)
    # line 6: P-8 (do-013) put the two call lines above the Priority lines
    assert (got.action, got.rule, got.layer, got.line) == ("wake", "wake to:$me", "built-in", 6)


def test_a_subscription_copy_that_also_names_me_in_to_still_rides(roots):
    """`X-Original-To` names the address a copy came through, and when it is
    there `to:` reads it alone (D2): a list mail that Cc's the seat but
    reached THIS box through the subscription alias rides."""
    through = mail(("To", f"atomnote@{HOST}"), ("Cc", f"ivan@{HOST}"),
                   ("X-Original-To", "atomnote"))
    assert judge(through, roots.load(), ME, NOW).action == "ride"


def test_direct_to_the_session_id_wakes_too(roots):
    direct = mail(("X-Original-To", f"{SID}@{HOST}"))
    assert judge(direct, roots.load(), ME, NOW).action == "wake"


def test_judys_mail_wakes_through_the_built_in_breakthrough(roots):
    got = judge(mail(("From", JUDY), ("X-Original-To", "atomnote")), roots.load(), ME, NOW)
    assert (got.action, got.rule, got.layer) == ("wake", "carry! from:$operator", "built-in")


# ── 2.4 the layers of D3 ─────────────────────────────────────────────────────

def test_a_session_ride_star_cannot_silence_judys_wake_bang(roots):
    """The slice's second criterion, and the spec's *A session cannot
    silence Judy*: the session's own FILE starts with `ride *`, loaded from
    disk, and Judy's mail still wakes by the built-in `wake!`."""
    roots.write(roots.session_file(), "ride *\n")
    layers = roots.load()
    assert names(layers) == ["session", "built-in"]
    got = judge(mail(("From", JUDY), ("X-Original-To", "ivan")), layers, ME, NOW)
    assert (got.action, got.rule, got.layer, got.line) == (
        "wake", "carry! from:$operator", "built-in", 1)
    # The control: the session file IS read, and does silence everyone else,
    # a direct mail included.
    got = judge(mail(("From", "eve@hq"), ("X-Original-To", "ivan")), layers, ME, NOW)
    assert (got.action, got.rule, got.layer) == ("ride", "ride *", "session")


def test_no_layer_below_can_silence_judy_either(roots):
    """Every layer an agent can reach (session, seat, env) writes `ride *`
    and a `ride from:judy@*`; Judy's mail still wakes."""
    silence = "ride from:judy@*\nride *\n"
    roots.write(roots.session_file(), silence)
    roots.write(roots.seat_file(), silence)
    layers = roots.load(env={PAGE_RULES_VAR: silence})
    assert names(layers) == ["session", "seat", "env", "built-in"]
    assert judge(mail(("From", JUDY)), layers, ME, NOW).action == "wake"


def test_the_layers_come_in_d3_order(roots):
    roots.write_locked("wake! from:alerts@*\n")
    roots.write(roots.session_file(), "ride from:kith@*\n")
    roots.write(roots.seat_file(), "ride from:*@delta.chat\n")
    layers = roots.load(env={PAGE_RULES_VAR: "wake x-project:atomnote\n"})
    assert names(layers) == ["locked", "session", "seat", "env", "built-in"]
    assert [[r.text for r in layer.rules] for layer in layers] == [
        ["wake! from:alerts@*"], ["ride from:kith@*"], ["ride from:*@delta.chat"],
        ["wake x-project:atomnote"], ["carry! from:$operator", "carry call:$me",
        "carry call:everyone", "ride priority:non-urgent",
        "carry priority:urgent from:$urgent", "wake to:$me", "ride *"]]


@pytest.mark.parametrize("upper, lower", [
    ("locked", "session"), ("session", "seat"), ("seat", "env"), ("env", "built-in")])
def test_an_upper_layer_beats_the_one_below_it(roots, upper, lower):
    """First match wins down D3 (RULED): each layer, holding `ride`
    for eve's mail, beats the layer below it holding `wake`."""
    text = {upper: "ride from:eve@*\n", lower: "wake from:eve@*\n"}
    env: dict[str, str] = {}
    for name, body in text.items():
        if name == "locked":
            roots.write_locked(body)
        elif name == "session":
            roots.write(roots.session_file(), body)
        elif name == "seat":
            roots.write(roots.seat_file(), body)
        elif name == "env":
            env[PAGE_RULES_VAR] = body
    got = judge(mail(("From", "eve@hq"), ("X-Original-To", "ivan")),
                roots.load(env=env), ME, NOW)
    assert (got.action, got.layer) == ("ride", upper)


def test_a_locked_breakthrough_is_named_as_the_locked_layer(roots):
    roots.write_locked("# Ivan and Judy only\nwake! from:judy@*\n")
    got = judge(mail(("From", JUDY)), roots.load(), ME, NOW)
    assert (got.rule, got.layer, got.line) == ("wake! from:judy@*", "locked", 2)


def test_the_seat_file_is_the_seat_address_folder_plus_kept(roots):
    """`seats/<address>/rules`, the `+` kept (the seat folder rule,
    `_seat.resolve`): a plus seat's folder is its whole address, and its
    base's folder is not read."""
    roots.write(roots.seat_file("sessionmail+sm-474.worker"), "ride from:eve@*\n")
    roots.write(roots.seat_file("sessionmail"), "wake from:kith@*\n")
    layers = roots.load(seat="sessionmail+sm-474.worker@hq.tailnet.example")
    assert names(layers) == ["seat", "built-in"]
    assert layers[0].rules[0].text == "ride from:eve@*"


def test_a_seat_object_reads_its_own_root(roots, tmp_path):
    root = tmp_path / "elsewhere" / "ivan"
    roots.write(root / "rules", "ride from:eve@*\n")
    seat = Seat(name="ivan", source="TM_AGENT_ADDRESS", root=root)
    layers = roots.load(seat=seat)
    assert names(layers) == ["seat", "built-in"]


def test_no_seat_and_no_session_leave_only_what_is_left(roots):
    roots.write_locked("wake! from:alerts@*\n")
    assert names(roots.load(seat=None, session=None)) == ["locked", "built-in"]
    assert names(roots.load(seat="", session="-")) == ["locked", "built-in"]


@pytest.mark.parametrize("sid", ["../../etc", "a/b", "..", ".", "/abs"])
def test_a_session_id_that_is_a_path_reads_nothing(roots, tmp_path, sid):
    """A session id is a folder NAME. One that would climb out of
    `sessions/` is not read (it is named as an error instead)."""
    roots.write(tmp_path / "etc" / "rules", "ride *\n")
    roots.write(roots.home / "rules", "ride *\n")
    layers = roots.load(seat=None, session=sid)
    assert names(layers) == ["built-in"]
    assert [e.source for e in layers.errors] == ["session"]


def test_an_empty_file_is_an_empty_layer(roots):
    roots.write(roots.session_file(), "# nothing yet\n")
    layers = roots.load()
    assert names(layers) == ["session", "built-in"]
    assert layers[0].rules == ()


def test_page_rules_is_one_rule_per_line(roots):
    env = {PAGE_RULES_VAR: "ride from:kith@*\r\n\n  # a role default\nwake x-project:atomnote"}
    layers = roots.load(env=env)
    env_layer = layers[0]
    assert env_layer.name == "env"
    assert [(r.text, r.line) for r in env_layer.rules] == [
        ("ride from:kith@*", 1), ("wake x-project:atomnote", 4)]
    assert env_layer.rules[0].source == PAGE_RULES_VAR


def test_an_empty_page_rules_adds_no_layer(roots):
    assert names(roots.load(env={PAGE_RULES_VAR: ""})) == ["built-in"]
    assert names(roots.load(env={PAGE_RULES_VAR: "  \n"})) == ["built-in"]


def test_the_env_defaults_to_the_process_environment(roots, monkeypatch):
    monkeypatch.setenv(PAGE_RULES_VAR, "ride from:kith@*")
    layers = load_layers("ivan", SID, home=roots.home, locked=roots.locked)
    assert names(layers) == ["env", "built-in"]


# ── bad lines: named with the file and line, and only that line skipped ─────

def test_a_bad_line_in_a_file_is_named_with_its_path_and_line(roots):
    path = roots.write(roots.seat_file(), "ride from:kith@*\n\nsnooze from:eve@*\nwake to:$me\n")
    layers = roots.load()
    assert [r.text for r in layers[0].rules] == ["ride from:kith@*", "wake to:$me"]
    (err,) = layers.errors
    assert (err.source, err.line, err.text) == (str(path), 3, "snooze from:eve@*")
    assert "unknown verb" in err.reason


def test_bad_lines_from_every_layer_are_gathered_in_layer_order(roots):
    locked = roots.write_locked("bogus\n")
    session = roots.write(roots.session_file(), "ride\n")
    seat = roots.write(roots.seat_file(), "wake to:$nobody\n")
    layers = roots.load(env={PAGE_RULES_VAR: "ride *\nride * to:x\n"})
    assert [(e.source, e.line) for e in layers.errors] == [
        (str(locked), 1), (str(session), 1), (str(seat), 1), (PAGE_RULES_VAR, 2)]


def test_the_built_ins_carry_no_errors(roots):
    assert roots.load().errors == ()


def test_a_file_that_cannot_be_read_is_named_not_raised(roots):
    """A rules path that is a directory (or unreadable) is named as an
    error on line 0 and its layer is left out; the others still load."""
    roots.session_file().mkdir(parents=True)
    roots.write(roots.seat_file(), "ride from:eve@*\n")
    layers = roots.load()
    assert names(layers) == ["seat", "built-in"]
    (err,) = layers.errors
    assert (err.source, err.line) == (str(roots.session_file()), 0)
    assert "cannot read" in err.reason


def test_bytes_that_are_not_utf8_do_not_lose_the_file(roots):
    path = roots.seat_file()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ride from:eve@*\nwake subject:caf\xe9*\n")
    layers = roots.load()
    assert [r.text for r in layers[0].rules][0] == "ride from:eve@*"
    assert len(layers[0].rules) == 2


# ── the locked file is NEVER taken from the environment ─────────────────────

LOCKISH = "ride from:judy@*\nride *\n"


def test_locked_content_in_an_env_var_is_ignored(roots, tmp_path):
    """The slice's third criterion (tasks 2.4, D3 layer 0). Every way an
    agent could try to hand the pager a locked file through its
    environment: the content in a variable named for it, a path to one in
    such a variable, or `SESSIONMAIL_HOME` pointed at a home holding a
    `rules.locked`. None of them makes a `locked` layer, and none changes a
    verdict."""
    fake_home = tmp_path / "fake-home"
    roots.write(fake_home / "rules.locked", LOCKISH)
    roots.write(fake_home / ".sessionmail" / "rules.locked", LOCKISH)
    lockfile = roots.write(tmp_path / "planted.locked", LOCKISH)
    env = {
        "SESSIONMAIL_RULES_LOCKED": LOCKISH,
        "SESSIONMAIL_LOCKED_RULES": LOCKISH,
        "SESSIONMAIL_RULES_LOCKED_FILE": str(lockfile),
        "SESSIONMAIL_LOCKED": str(lockfile),
        "RULES_LOCKED": LOCKISH,
        "SESSIONMAIL_HOME": str(fake_home),
        "HOME": str(fake_home),
    }
    # No locked= and no home=: the loader picks its own roots, the way the
    # listener will. The session and seat files follow SESSIONMAIL_HOME (as
    # every seat file does, `_seat.home`); the locked file does not.
    layers = load_layers("ivan", SID, env)
    assert "locked" not in names(layers)
    assert names(layers) == ["built-in"]
    direct = mail(("From", "eve@hq"), ("X-Original-To", "ivan"))
    assert judge(direct, layers, ME, NOW).action == "wake"


def test_locked_rules_in_page_rules_are_only_the_env_layer(roots):
    """A `wake!` or a `ride *` in `SESSIONMAIL_PAGE_RULES` is the ENV layer
    (3), never the locked one (0): it sits below the session and the seat,
    and it is named `env`."""
    roots.write(roots.session_file(), "wake from:eve@*\n")
    layers = roots.load(env={PAGE_RULES_VAR: LOCKISH})
    assert names(layers) == ["session", "env", "built-in"]
    got = judge(mail(("From", "eve@hq")), layers, ME, NOW)
    assert (got.action, got.layer) == ("wake", "session")
    assert judge(mail(("From", JUDY)), layers, ME, NOW).layer == "built-in"


def test_the_default_locked_path_ignores_home_and_sessionmail_home(monkeypatch, tmp_path):
    """`account_home()` reads the password database for this uid, never
    `$HOME` and never `$SESSIONMAIL_HOME`: an agent sets both at will."""
    real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "planted"))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "planted" / ".sessionmail"))
    assert rules.account_home() == real
    assert rules.default_locked_path() == real / ".sessionmail" / "rules.locked"


def test_the_suite_never_reads_the_real_locked_file(tmp_path):
    """The conftest points `locked_path()` into the sandbox, so a loader
    called with no `locked=` (as the listener will call it) reads a file
    the test owns, never the host's."""
    path = rules.locked_path()
    assert path != rules.default_locked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("wake! from:alerts@*\n", encoding="utf-8")
    layers = load_layers(None, None, {}, home=tmp_path / "home")
    assert names(layers) == ["locked", "built-in"]


# ── the loaded layers ────────────────────────────────────────────────────────

def test_layers_is_a_sequence_judge_takes(roots):
    layers = roots.load()
    assert len(layers) == 1
    assert isinstance(layers[0], Layer)
    assert list(layers) == [built_in()]
    assert layers.errors == ()


def test_loading_twice_reads_the_file_again(roots):
    """No cache in this slice (live re-read on a change is do-006, design
    A6): each call reads what is on disk now."""
    path = roots.write(roots.session_file(), "ride *\n")
    assert names(roots.load()) == ["session", "built-in"]
    path.unlink()
    assert names(roots.load()) == ["built-in"]
