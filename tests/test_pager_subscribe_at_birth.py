"""SESSIONMAIL_SUBSCRIBE at a session's birth, since task 3.4 (mail settings;
sessiontools 4.4 `write_env_at_birth`; specs/settings "A subscription removed
stays removed", lines 125-132).

- The LAUNCHER applies the variable: `mail.schema.json` names it with
  `x-env`, and the birth writes `<session dir>/settings/mail.d/50-env.json`
  once, before the session runs. The listen writes nothing from the variable
  (D4's write retired), so it never puts back a box the agent removed.
- The listen that births the box REPORTS the birth file's boxes, one banner
  line each, and its regen routes them. Each box meets the add path's gates
  (`_subscribe.check_box`); the generator does not hold D5, so a refused box
  is turned OFF in the verbs' `mail.d/70-live.json`, which wins.
- The session's verbs write `70-live.json`: a `remove` of a box the birth
  file subscribes writes `false` there; a hand's later drop-in (`90-*`) wins
  over the verbs, and they leave it alone.
- The variable set with NO birth file is named NOT applied, never silence.

Every test runs over the do-006 synthetic fleet (`test_message_subscription`):
a mail root and a sessionmail home under tmp_path, fake postconf/postmap and
live sessions as sidecars + `ps`, and the REAL generator writing under tmp - never the live
map. Each listen returns at once through `--timeout 0`."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

import json

from _seatmap import (session_env, session_env_path, session_live_path, session_map_path,
                      session_subscribe)
from sessionmail import _layer, _seat, _subscribe, pager
from sessionmail.pager import cli
from test_postfix_vmailbox_gen import _valias, generator, needs_jq  # noqa: F401
from test_message_subscription import (HOST, OTHER, SID, IVAN_SID, JUDY_SID, as_seat,  # noqa: F401
                                       fleet, make_live, rows, sub)

#: A session that has never listened: no box yet, live by its sidecar.
NEW = "b0b0b0b0-1111-2222-3333-444455556666"
EX_TIMEOUT = 124


@pytest.fixture
def fresh(fleet, tmp_path, monkeypatch):
    """The fleet, plus a live worker session NEW whose box is not born yet."""
    make_live(tmp_path, NEW, "worker")
    as_seat(monkeypatch, "worker", NEW)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    assert not (fleet.agents / NEW).exists()
    return fleet


def listen(*argv: str):
    return CliRunner().invoke(cli, ["listen", "--timeout", "0", *argv], prog_name="pager")


def banner(result, key: str = "subscribe") -> list[str]:
    return [line for line in result.stderr.splitlines() if line.startswith(f"  {key}")]


# ── the first listen ─────────────────────────────────────────────────────

def born_with(fresh, monkeypatch, boxes: str) -> None:
    """What `session start` does for SESSIONMAIL_SUBSCRIBE=BOXES (3.4)."""
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, boxes)
    session_env(NEW, boxes, fresh.userhome)


def test_the_first_listen_reports_and_routes_the_birth_files_boxes(fresh, monkeypatch):
    """The birth wrote two boxes; the listen births the box, names each box
    and the birth file once, and the birth's regen routes both. It writes no
    subscription itself: neither `mail.json` nor the verbs' file."""
    born_with(fresh, monkeypatch, "atomnote  voicemode")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert (fresh.agents / NEW / "new").is_dir()                      # born here
    assert fresh.subscribed(NEW) == ["atomnote", "voicemode"]
    for box in ("atomnote", "voicemode"):
        assert rows(fresh.valias)[f"{box}@{HOST}"].endswith(f", {NEW}@{HOST}")
    lines = banner(result)
    assert len(lines) == 2, result.stderr
    assert "atomnote" in lines[0] and "voicemode" in lines[1], lines
    env_file = str(session_env_path(NEW, fresh.userhome))
    assert all("at the launcher's birth" in line and env_file in line for line in lines), lines
    assert not session_map_path(NEW, fresh.userhome).exists()
    assert not session_live_path(NEW, fresh.userhome).exists()
    assert fresh.session_log(NEW) == []                               # the listen wrote nothing


def test_the_listen_never_calls_the_add_path(fresh, monkeypatch):
    """D4's write is retired: the listen reads the birth file, it does not
    subscribe through `_subscribe.add`."""
    monkeypatch.setattr(_subscribe, "add", lambda *a, **k: pytest.fail("add called"))
    born_with(fresh, monkeypatch, "atomnote")
    assert listen().exit_code == EX_TIMEOUT


def test_the_second_listen_does_nothing(fresh, monkeypatch):
    """Reported once, at the box's birth: a second listen prints no line and
    does not rebuild."""
    born_with(fresh, monkeypatch, "atomnote voicemode")
    assert listen().exit_code == EX_TIMEOUT
    runs = fresh.runs()
    again = listen()
    assert again.exit_code == EX_TIMEOUT, again.output
    assert banner(again) == [], again.stderr
    assert "born box" not in again.stderr
    assert fresh.runs() == runs, fresh.log.read_text()


def test_one_rebuild_at_birth_routes_every_box(fresh, monkeypatch):
    """The birth's own whole-generator run (D9, the doorbell) routes the
    birth file's boxes: N boxes cost no N extra rebuilds."""
    born_with(fresh, monkeypatch, "atomnote voicemode")
    assert listen().exit_code == EX_TIMEOUT
    assert fresh.runs() == 1, fresh.log.read_text()
    assert "--valias-only" not in fresh.log.read_text()


def test_a_session_with_no_resolvable_seat_still_gets_its_rows(fresh, monkeypatch):
    """When nothing resolves a seat no doorbell runs: one `--valias-only`
    rebuild routes the birth file's boxes instead, and the banner says so."""
    def no_seat(env=None):
        raise _seat.NoIdentity()

    monkeypatch.setattr(_seat, "resolve", no_seat)
    born_with(fresh, monkeypatch, "atomnote voicemode")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    for box in ("atomnote", "voicemode"):
        assert rows(fresh.valias)[f"{box}@{HOST}"].endswith(f", {NEW}@{HOST}")
    assert fresh.runs() == 1 and "--valias-only" in fresh.log.read_text()
    assert len(banner(result)) == 2, result.stderr
    (line,) = banner(result, "map")
    assert "--valias-only" in line and "generator ran" in line, line


# ── the add path's gates, held at the birth ──────────────────────────────

def test_a_box_that_does_not_resolve_is_refused_named_and_turned_off(fresh, monkeypatch):
    born_with(fresh, monkeypatch, "nosuchbox atomnote")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output               # still listened
    lines = banner(result)
    assert len(lines) == 2, result.stderr
    assert "nosuchbox" in lines[1] or "nosuchbox" in lines[0], lines
    refused = [line for line in lines if "REFUSED" in line]
    assert len(refused) == 1 and "nosuchbox" in refused[0], lines
    live = session_live_path(NEW, fresh.userhome)
    assert str(live) in refused[0], refused
    assert json.loads(live.read_text()) == {"subscriptions": {"nosuchbox": False}}
    assert fresh.subscribed(NEW) == ["atomnote"]


def test_another_seats_own_address_is_refused_and_never_routed(fresh, monkeypatch):
    """D5 (RULED S6): a seat's own mail is that seat's to grant. The
    generator does not hold D5, so the refusal turns the box OFF in the
    verbs' file: ivan's row never names this session."""
    born_with(fresh, monkeypatch, "ivan")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    (line,) = banner(result)
    assert "REFUSED" in line and "ivan's own address" in line and "turned off" in line, line
    assert fresh.subscribed(NEW) == []
    assert NEW not in rows(fresh.valias).get(f"ivan@{HOST}", ""), fresh.valias
    assert json.loads(session_env_path(NEW, fresh.userhome).read_text()) == {
        "subscriptions": {"ivan": True}}                              # the birth file untouched


# ── what the listen does not do ──────────────────────────────────────────

def test_quiet_routes_but_prints_nothing(fresh, monkeypatch):
    born_with(fresh, monkeypatch, "atomnote")
    result = listen("--quiet")
    assert result.exit_code == EX_TIMEOUT, result.output
    assert result.stderr == ""
    assert fresh.subscribed(NEW) == ["atomnote"]


def test_a_box_already_born_is_not_a_birth_and_nothing_is_reported(fresh, monkeypatch):
    as_seat(monkeypatch, "worker", SID)
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert banner(result) == [], result.stderr
    assert fresh.files() == []


def test_no_session_reports_nothing(fresh, monkeypatch, tmp_path):
    """A human at a shell ($MAIL, no session): nothing is born."""
    as_seat(monkeypatch, "judy", None)
    monkeypatch.setenv("MAIL", str(fresh.agents / "judy"))
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert banner(result) == [], result.stderr
    assert fresh.files() == []


def test_the_variable_with_no_birth_file_is_named_not_applied(fresh, monkeypatch):
    """A session not launched by `session start` (or a schema without the
    `x-env`): the listen does not apply the variable, and says so."""
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote voicemode")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    (line,) = banner(result)
    assert "NOT applied" in line and str(session_env_path(NEW, fresh.userhome)) in line, line
    assert fresh.files() == [] and fresh.session_log(NEW) == []


def test_an_empty_variable_and_no_birth_file_say_nothing(fresh, monkeypatch):
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "   ")
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert banner(result) == [] and fresh.files() == []


# ── a subscription removed stays removed (specs/settings 125-132) ────────

def test_a_box_the_birth_subscribed_can_be_removed_and_stays_removed(fresh, monkeypatch):
    """The spec's scenario. The verbs' `false` in `70-live.json` folds after
    the birth file's `true`: the generator stops routing it, a later listen
    does not put it back, and the birth file still says what the env said.
    `add` turns it back on in the same file."""
    born_with(fresh, monkeypatch, "atomnote voicemode")
    assert listen().exit_code == EX_TIMEOUT
    done = sub("remove", "voicemode")
    assert done.exit_code == 0, done.output
    live = session_live_path(NEW, fresh.userhome)
    assert json.loads(live.read_text()) == {"subscriptions": {"voicemode": False}}
    assert json.loads(session_env_path(NEW, fresh.userhome).read_text()) == {
        "subscriptions": {"atomnote": True, "voicemode": True}}
    assert fresh.subscribed(NEW) == ["atomnote"]
    assert NEW not in rows(fresh.valias).get(f"voicemode@{HOST}", "")
    assert fresh.session_log(NEW)[-1]["value"] is False
    again = listen()
    assert again.exit_code == EX_TIMEOUT and fresh.subscribed(NEW) == ["atomnote"]
    assert sub("remove", "voicemode").exit_code == 0                  # not subscribed: a no-op
    assert sub("add", "voicemode").exit_code == 0
    assert fresh.subscribed(NEW) == ["atomnote", "voicemode"]
    assert rows(fresh.valias)[f"voicemode@{HOST}"].endswith(f", {NEW}@{HOST}")


def test_a_hands_later_drop_in_wins_and_the_verbs_leave_it_alone(fresh, monkeypatch):
    """A `mail.d/90-*` file folds after the verbs' 70-live: remove refuses,
    naming it, and changes nothing."""
    born_with(fresh, monkeypatch, "atomnote")
    assert listen().exit_code == EX_TIMEOUT
    hand = session_env_path(NEW, fresh.userhome).with_name("90-judy.json")
    hand.write_text(json.dumps({"subscriptions": {"atomnote": True}}))
    done = sub("remove", "atomnote")
    assert done.exit_code != 0 and str(hand) in done.output, done.output
    assert not session_live_path(NEW, fresh.userhome).exists()
    assert fresh.subscribed(NEW) == ["atomnote"]


@needs_jq
def test_the_generator_names_the_file_that_set_a_box(generator, tmp_path):
    """Each record names the file that set its key, as `_layer.read`'s
    origins do (the Rupert's read of a8528f2): a skipped box from the birth
    file is named in `50-env.json`, one from the session's own map in
    `mail.json`. Run as test_session_map.py runs it: `_regen_maps` keeps the
    skip list to itself, so the generator's stderr is where it shows."""
    session_env("s1", "atomnote+memory", tmp_path / "home")
    session_subscribe("s1", "ghost", home=tmp_path / "home")
    done = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "worker"}], {}, ["s1"])
    assert done.returncode == 0, done.stderr
    assert "mail.d/50-env.json#atomnote+memory" in done.stderr, done.stderr
    assert "mail.json#atomnote+memory" not in done.stderr, done.stderr
    assert "settings/mail.json#ghost" in done.stderr, done.stderr
