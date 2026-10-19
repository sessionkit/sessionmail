"""`_index`: the switch, the profile order, the marker walk and the refusal
(SM-731 do-002; design §2, §13; PROPOSED `changes/store-index`, package spec).

Every env here is built from nothing (`base_env`), so no developer's
NOTMUCH_CONFIG, NOTMUCH_DATABASE or NOTMUCH_PROFILE can reach a resolver call
or a notmuch child (design §13, test hygiene). `_seat.box_via_map` is
replaced for every test: a fixture box is found under the root or through the
fake map a test installs, never through the host's postfix.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sessionmail import _boxes, _index, _notmuch, _seat

NOTMUCH = shutil.which("notmuch")


@pytest.fixture(autouse=True)
def no_host_map(monkeypatch):
    """No test reads the host's postfix map; a test that wants a map row
    installs one with `map_rows`."""
    _index.clear_cache()
    monkeypatch.setattr(_seat, "box_via_map",
                        lambda name: (_ for _ in ()).throw(AssertionError(f"map read for {name}")))
    yield
    _index.clear_cache()


def map_rows(monkeypatch, rows: dict[str, Path]) -> list[str]:
    """A fake postfix map: NAME -> maildir. Returns the names asked."""
    asked: list[str] = []

    def fake(name: str):
        asked.append(name)
        return (rows[name], f"{name}@test -> fake row") if name in rows else None

    monkeypatch.setattr(_seat, "box_via_map", fake)
    return asked


def base_env(tmp: Path, **extra: str) -> dict[str, str]:
    """An environment with nothing inherited: a scratch HOME, mail root and
    XDG dir, plus EXTRA."""
    (tmp / "home").mkdir(exist_ok=True)
    return {"HOME": str(tmp / "home"), "SESSION_MAIL_ROOT": str(tmp / "mail"),
            "XDG_CONFIG_HOME": str(tmp / "xdg"), "PATH": "/usr/bin:/bin", **extra}


def maildir(d: Path) -> Path:
    for sub in ("cur", "new", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def store(tmp: Path, name: str) -> Path:
    """A store box under the agents root, holding the marker."""
    d = maildir(tmp / "mail" / name)
    (d / _boxes.STORE_MARKER).write_text("")
    return d


def dot_profile(tmp: Path, name: str) -> Path:
    path = tmp / "home" / f".notmuch-config.{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[database]\n")
    return path


def xdg_profile(tmp: Path, name: str) -> Path:
    path = tmp / "xdg" / "notmuch" / name / "config"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[database]\n")
    return path


# ── host mode: the host's sessionkit profile ────────────────────────────


@pytest.mark.parametrize("value", [None, "", "host"])
def test_host_reads_the_sessionkit_profile(tmp_path, value):
    path = xdg_profile(tmp_path, _index.SESSIONKIT_PROFILE)
    env = base_env(tmp_path)
    if value is not None:
        env[_index.VAR] = value
    r = _index.resolve(env)
    assert r.host and not r.refused and r.config == path and r.profile == "sessionkit"
    assert _index.argv("nm", env) == ["nm", f"--config={path}"]
    assert _index.child_env(env) == env, "nothing to scrub here, and nothing added"
    assert _index.oracle_env(env) == {**env, "NOTMUCH_CONFIG": str(path)}


def test_host_refuses_without_its_profile(tmp_path):
    """No sessionkit profile anywhere: host mode refuses by name, and never
    falls back to the default notmuch config."""
    env = base_env(tmp_path)
    r = _index.resolve(env)
    assert r.refused and not r.host and 'profile "sessionkit"' in r.refusal
    with pytest.raises(_notmuch.CouldNotLook, match="never read"):
        _index.argv("nm", env)


def test_host_drops_the_notmuch_variables(tmp_path):
    """A stranger may export notmuch's own variables: host mode drops all
    three from a child, and none of them chooses the profile."""
    path = xdg_profile(tmp_path, _index.SESSIONKIT_PROFILE)
    xdg_profile(tmp_path, "alpha")
    env = base_env(tmp_path, NOTMUCH_PROFILE="alpha", NOTMUCH_CONFIG="/c",
                   NOTMUCH_DATABASE="/d")
    assert _index.resolve(env).config == path
    out = _index.child_env(env)
    assert not any(k in out for k in ("NOTMUCH_PROFILE", "NOTMUCH_CONFIG", "NOTMUCH_DATABASE"))
    assert _index.oracle_env(env)["NOTMUCH_CONFIG"] == str(path)
    assert "NOTMUCH_PROFILE" not in _index.oracle_env(env)


def test_host_reads_os_environ_by_default(monkeypatch):
    monkeypatch.delenv(_index.VAR, raising=False)
    assert _index.argv("nm") == ["nm", f"--config={_index.profile_config()}"]
    assert "NOTMUCH_CONFIG" not in _index.child_env()


# ── store mode: NOTMUCH_PROFILE ─────────────────────────────────────────


def test_store_with_profile(tmp_path):
    path = dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    r = _index.resolve(env)
    assert (r.mode, r.config, r.profile, r.refused) == ("store", path, "alpha", False)
    assert _index.argv("nm", env) == ["nm", f"--config={path}"]


def test_store_drops_every_overriding_variable(tmp_path):
    """E3, E14: NOTMUCH_CONFIG beats a profile and NOTMUCH_DATABASE beats
    --config, so none of the three reaches a store-mode child."""
    path = dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha",
                   NOTMUCH_CONFIG="/host/config", NOTMUCH_DATABASE="/host/db", KEEP="me")
    child = _index.child_env(env)
    for var in ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE"):
        assert var not in child
    assert child["KEEP"] == "me" and child["HOME"] == env["HOME"]
    assert env["NOTMUCH_DATABASE"] == "/host/db", "the caller's env is not edited"
    oracle = _index.oracle_env(env)
    assert oracle["NOTMUCH_CONFIG"] == str(path)
    assert "NOTMUCH_DATABASE" not in oracle and "NOTMUCH_PROFILE" not in oracle


def test_xdg_profile_is_read_first(tmp_path):
    """E19a: with both files present notmuch reads the XDG one, so we do."""
    dot_profile(tmp_path, "alpha")
    xdg = xdg_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    assert _index.resolve(env).config == xdg


def test_xdg_defaults_to_dot_config(tmp_path):
    path = tmp_path / "home" / ".config" / "notmuch" / "alpha" / "config"
    path.parent.mkdir(parents=True)
    path.write_text("")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    del env["XDG_CONFIG_HOME"]
    assert _index.resolve(env).config == path
    env["XDG_CONFIG_HOME"] = ""
    assert _index.resolve(env).config == path


def test_missing_profile_refuses_and_names_it(tmp_path):
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    r = _index.resolve(env)
    assert r.refused and r.config is None
    assert "no notmuch profile for this seat" in r.refusal
    assert "'alpha'" in r.refusal
    assert str(tmp_path / "xdg" / "notmuch" / "alpha" / "config") in r.refusal
    assert str(tmp_path / "home" / ".notmuch-config.alpha") in r.refusal
    assert r.refusal.endswith(_index.REFUSES_HOST)
    for call in (_index.argv, _index.child_env, _index.oracle_env, _index.require):
        args = ("nm", env) if call is _index.argv else (env,)
        with pytest.raises(_notmuch.CouldNotLook, match="no notmuch profile for this seat"):
            call(*args)


def test_a_profile_directory_is_not_a_profile_file(tmp_path):
    """The check is a FILE test: a directory at the path does not pass."""
    (tmp_path / "home" / ".notmuch-config.alpha").mkdir(parents=True)
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    assert _index.resolve(env).refused


def test_empty_profile_is_refused_by_name(tmp_path):
    """E19b: an empty NOTMUCH_PROFILE fails every notmuch call, so it is
    refused, never read as `~/.notmuch-config.` or `~/.notmuch-config`."""
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".notmuch-config").write_text("")
    (tmp_path / "home" / ".notmuch-config.").write_text("")
    store(tmp_path, "alpha")
    dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="",
                   TM_AGENT_ADDRESS="alpha")
    r = _index.resolve(env)
    assert r.refused and r.config is None
    assert "NOTMUCH_PROFILE is set but EMPTY" in r.refusal
    with pytest.raises(_notmuch.CouldNotLook, match="EMPTY"):
        _index.argv("nm", env)


def test_profile_wins_over_the_marker(tmp_path):
    store(tmp_path, "alpha")
    dot_profile(tmp_path, "alpha")
    beta = dot_profile(tmp_path, "beta")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="beta",
                   TM_AGENT_ADDRESS="alpha")
    assert _index.resolve(env).config == beta


# ── store mode: the marker walk ─────────────────────────────────────────


def test_marker_names_a_project_seats_store(tmp_path, monkeypatch):
    """A project box nests under its store (`<root>/<store>/<project>`), so
    the box comes from the map, and the walk finds the marker one up."""
    alpha = store(tmp_path, "alpha")
    project = maildir(alpha / "proj")
    asked = map_rows(monkeypatch, {"proj": project})
    path = dot_profile(tmp_path, "alpha")
    # A kicked worker: a session id is its identity, EMAIL its declared
    # plus address (the address, not the session id, names the box).
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", EMAIL="proj+sm-1.worker.abc",
                   **{_seat.SESSION_VARS[0]: "e8f670e0-7a93-44ca-8482-8363587a78c2"})
    r = _index.resolve(env)
    assert (r.config, r.profile, r.refused) == (path, "alpha", False)
    assert asked == ["proj"], "the +extension is stripped to the base before the box"
    assert "marker" in r.via and "proj" in r.via
    assert _index.argv("nm", env) == ["nm", f"--config={path}"]


def test_a_store_level_seats_own_box_resolves(tmp_path):
    """A store-level seat's box IS the store directory: the box counts."""
    store(tmp_path, "alpha")
    path = dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="alpha+gc")
    r = _index.resolve(env)
    assert (r.config, r.profile) == (path, "alpha")


def test_marker_store_without_a_profile_refuses_naming_the_store(tmp_path):
    store(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="alpha")
    r = _index.resolve(env)
    assert r.refused and "'alpha'" in r.refusal and "the seat's store" in r.refusal
    assert str(tmp_path / "home" / ".notmuch-config.alpha") in r.refusal
    assert r.refusal.endswith(_index.REFUSES_HOST)


def test_a_box_in_no_store_refuses(tmp_path):
    """A flat box with no marker above it (a session box, `kaizen`) is in no
    store: refused, naming the box and the missing marker."""
    flat = maildir(tmp_path / "mail" / "kaizen")
    dot_profile(tmp_path, "kaizen")      # a profile by the box's name is NOT used
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="kaizen")
    r = _index.resolve(env)
    assert r.refused
    assert "the seat has no store" in r.refusal
    assert f"no {_boxes.STORE_MARKER} at {flat}" in r.refusal


def test_the_walk_stops_at_the_agents_root(tmp_path):
    """A marker in the agents root itself, or above it, names no store."""
    maildir(tmp_path / "mail" / "kaizen")
    (tmp_path / "mail" / _boxes.STORE_MARKER).write_text("")
    (tmp_path / _boxes.STORE_MARKER).write_text("")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="kaizen")
    assert _index.resolve(env).refused


def test_a_box_outside_the_root_names_no_store(tmp_path, monkeypatch):
    elsewhere = maildir(tmp_path / "elsewhere" / "proj")
    (elsewhere.parent / _boxes.STORE_MARKER).write_text("")
    map_rows(monkeypatch, {"proj": elsewhere})
    (tmp_path / "mail").mkdir()
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="proj")
    r = _index.resolve(env)
    assert r.refused and "not under" in r.refusal


def test_no_box_refuses(tmp_path, monkeypatch):
    map_rows(monkeypatch, {})
    (tmp_path / "mail").mkdir()
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="ghost+x")
    r = _index.resolve(env)
    assert r.refused and "seat 'ghost'" in r.refusal and "no box" in r.refusal


def test_no_identity_refuses(tmp_path):
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store")
    r = _index.resolve(env)
    assert r.refused and "no seat" in r.refusal


def test_the_seats_store_is_resolved_once(tmp_path, monkeypatch):
    """The map costs 14-16 ms on a test host (design §2): once per process. The
    design's bound is 50 ms for the uncached resolution."""
    alpha = store(tmp_path, "alpha")
    project = maildir(alpha / "proj")
    asked = map_rows(monkeypatch, {"proj": project})
    dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="proj")
    start = time.perf_counter()
    first = _index.resolve(env)
    assert time.perf_counter() - start < 0.05
    assert _index.resolve(env) == first
    assert asked == ["proj"]


def test_a_miss_is_not_cached(tmp_path):
    """A box that gains its marker later is found: only a hit is kept."""
    box = maildir(tmp_path / "mail" / "alpha")
    dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", TM_AGENT_ADDRESS="alpha")
    assert _index.resolve(env).refused
    (box / _boxes.STORE_MARKER).write_text("")
    assert not _index.resolve(env).refused


# ── a bad mode ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["stores", "STORE", "Host", " store"])
def test_a_bad_mode_is_refused_by_value(tmp_path, value):
    dot_profile(tmp_path, "alpha")
    env = base_env(tmp_path, SESSIONMAIL_INDEX=value, NOTMUCH_PROFILE="alpha")
    r = _index.resolve(env)
    assert r.refused and repr(value) in r.refusal and not r.host
    with pytest.raises(_notmuch.CouldNotLook, match="is not a mode"):
        _index.argv("nm", env)
    with pytest.raises(_notmuch.CouldNotLook):
        _index.child_env(env)


# ── against the real engine: what the argv and env actually read ────────


def _scratch_db(tmp: Path, name: str, msgid: str) -> Path:
    """A scratch maildir with one message, indexed, and its config."""
    root = maildir(tmp / name / "mail" / "box").parent
    (root / "box" / "new" / "1.M1P1.test").write_text(
        f"From: a@example.invalid\nTo: b@example.invalid\nSubject: {name}\n"
        f"Message-ID: <{msgid}>\n\nbody\n")
    config = tmp / name / "config"
    config.write_text(f"[database]\npath={root}\n[user]\nname=t\n"
                      "primary_email=t@example.invalid\n[new]\ntags=unread\n")
    run = subprocess.run([NOTMUCH, f"--config={config}", "new", "--no-hooks", "--quiet"],
                         env={"HOME": str(tmp / name), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return config


@pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")
def test_a_planted_database_does_not_reach_notmuch(tmp_path):
    """The package spec's scenario, with the real engine (E14): a planted
    NOTMUCH_DATABASE and NOTMUCH_CONFIG name the 'host' database, and the
    store-mode call still answers from the store's."""
    host_config = _scratch_db(tmp_path, "host", "host-only@x")
    store_config = _scratch_db(tmp_path, "alpha", "store-only@x")
    profile = dot_profile(tmp_path, "alpha")
    profile.write_text(store_config.read_text())
    env = base_env(tmp_path, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha",
                   NOTMUCH_CONFIG=str(host_config),
                   NOTMUCH_DATABASE=str(tmp_path / "host" / "mail"))

    def ids(argv, child):
        run = subprocess.run([*argv, "search", "--output=messages", "*"], env=child,
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        return run.stdout.split()

    # The control: the planted variables, unscrubbed, really do win (E14).
    assert ids([NOTMUCH, f"--config={profile}"], env) == ["id:host-only@x"]
    assert ids(_index.argv(NOTMUCH, env), _index.child_env(env)) == ["id:store-only@x"]
    assert ids([NOTMUCH], _index.oracle_env(env)) == ["id:store-only@x"]
