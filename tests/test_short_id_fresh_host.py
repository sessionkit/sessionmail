"""A short session id resolves on a FRESH host (SM-969 do-009, on SM-885).

A fresh install has no `~/.sessionmail`: mail's state is at
`~/.sessionkit/mail/state/` (SM-969), and the session dirs are wherever
sessiontools keeps them (`settings.sessions_root`). SM-885's short-id
resolver (`_boxes.resolve_session`) must find its candidates there, through
the ONE session-dir resolver SM-969 uses (`_layer.sessions_root`), and never
through mail's own state leaf (`state_path("sessions")`, which holds only
the legacy compatibility links) or a spelled `~/.session` / `~/.sessionmail`.

Every case runs in a tmp HOME with every root variable unset: nothing here
reads or writes the real HOME. Synthetic ids only. The resolver is a reader,
so each case also pins that it wrote nothing (no `~/.sessionmail` is born).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _boxes, _layer, _seat, _settings
from sessionmail.message import cli

FULL = "f7e5a969-1111-4111-8111-111111111111"
TWIN = "f7e5a969-2222-4222-8222-222222222222"
CALLER = "c0de0969-3333-4333-8333-333333333333"
SHORT = FULL[:8]

#: Every variable that would move a root off the fresh HOME's defaults.
ROOT_VARS = ("SESSIONMAIL_HOME", "SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT", "SESSIONS_DIR")


@pytest.fixture
def fresh(tmp_path, monkeypatch) -> Path:
    """A fresh-NEW host: HOME with mail state at `~/.sessionkit/mail/state/`
    and no `~/.sessionmail`; no root variable set; no postfix map read."""
    home = tmp_path / "fresh-home"
    (home / ".sessionkit" / "mail" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ROOT_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_seat, "box_via_map", lambda name: None)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)
    assert not (home / ".sessionmail").exists()
    yield home
    assert not (home / ".sessionmail").exists(), "a resolver wrote the old home"


def env_of(home: Path) -> dict[str, str]:
    return {"HOME": str(home)}


def maildir(d: Path) -> Path:
    for leaf in ("new", "cur", "tmp"):
        (d / leaf).mkdir(parents=True, exist_ok=True)
    return d


def test_the_fresh_host_is_fresh_new(fresh):
    """The premise, measured: mail's state resolves into the new home, and
    the short-id resolver's session root IS SM-969's session-dir resolver."""
    env = env_of(fresh)
    assert _settings.state_path("sessions", env) == fresh / ".sessionkit/mail/state/sessions"
    assert _boxes._sessions_root(env) == _layer.sessions_root(env)
    assert _boxes._sessions_root(env) != _settings.state_path("sessions", env)


def test_session_a_short_id_resolves_to_the_session_dir_sessiontools_keeps(fresh):
    env = env_of(fresh)
    (_layer.sessions_root(env) / FULL).mkdir(parents=True)
    got = _boxes.resolve_session(SHORT, source="session", env=env)
    assert got.value == FULL
    assert got.full == FULL
    assert f"is session {FULL}" in got.note


def test_session_a_dir_only_in_mails_state_leaf_is_not_a_session(fresh):
    """Mail's `state/sessions/` holds compatibility links, not session dirs:
    a short id is never expanded from it."""
    env = env_of(fresh)
    (_settings.state_path("sessions", env) / FULL).mkdir(parents=True)
    with pytest.raises(_boxes.Unresolved) as exc:
        _boxes.resolve_session(SHORT, source="session", env=env)
    assert exc.value.why == "none"


def test_session_two_candidates_refuse_naming_both(fresh):
    env = env_of(fresh)
    for sid in (FULL, TWIN):
        (_layer.sessions_root(env) / sid).mkdir(parents=True)
    with pytest.raises(_boxes.Unresolved) as exc:
        _boxes.resolve_session(SHORT, source="session", env=env)
    assert exc.value.why == "ambiguous"
    assert exc.value.candidates == [FULL, TWIN]


def test_box_a_short_id_resolves_to_the_session_box_under_the_default_root(fresh):
    env = env_of(fresh)
    maildir(fresh / ".sessionkit" / "mail" / "stores" / FULL)
    got = _boxes.resolve_session(f"{SHORT}+notes", source="box", env=env)
    assert got.value == f"{FULL}+notes"
    assert got.full == FULL


def test_box_and_session_both_resolve_on_one_fresh_host(fresh):
    """The ask, whole: the same short id, both sources, one fresh host."""
    env = env_of(fresh)
    (_layer.sessions_root(env) / FULL).mkdir(parents=True)
    maildir(fresh / ".sessionkit" / "mail" / "stores" / FULL)
    assert _boxes.resolve_session(SHORT, source="session", env=env).full == FULL
    assert _boxes.resolve_session(SHORT, source="box", env=env).full == FULL


def test_subscription_list_for_a_short_id_resolves_on_a_fresh_host(fresh, monkeypatch):
    """The door the SM-969 x SM-885 merge joined (`message._list_whose`):
    `--for SHORT` on a fresh host roots the tree at the full session."""
    env = env_of(fresh)
    (_layer.sessions_root(env) / FULL).mkdir(parents=True)
    maildir(fresh / ".sessionkit" / "mail" / "stores" / FULL)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    done = CliRunner().invoke(cli, ["subscription", "list", "--for", SHORT])
    assert done.exit_code == 0, done.output
    assert f"'{SHORT}' is session {FULL}" in done.stderr


def test_subscription_list_for_keeps_a_folder_in_mails_state_leaf_literal(fresh, monkeypatch):
    """The merge's own hunk (`message._list_whose`, SM-969 x SM-885): the
    "is it already a session folder" checks read mail's sessions leaf through
    SM-969's resolver (`_seat.sessions_root`), never a spelled
    `~/.sessionmail/sessions`. On a fresh-NEW host that leaf is under
    `~/.sessionkit/mail/state/`, so a short-form name with a folder there is
    kept literal (no door, no refusal), and a plain name with one is a
    session. Reverting either check to master's `_seat.home()` form sends
    the short name to the door, which refuses: no session starts with it."""
    from sessionmail import message
    # The seat lookup is not under test: keep it off sessiontools' live list.
    monkeypatch.setattr(message, "_session_seat", lambda sid: "")
    leaf = _seat.sessions_root()
    assert leaf == fresh / ".sessionkit/mail/state/sessions"
    literal = "5e55a969"
    assert _boxes.short_form(literal)
    (leaf / literal).mkdir(parents=True)
    (leaf / "lab-run").mkdir(parents=True)
    assert message._list_whose(literal)[0] == literal
    assert message._list_whose("lab-run")[0] == "lab-run"
