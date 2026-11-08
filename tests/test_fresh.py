"""SM-996: `_fresh.refresh`, the readers' bounded `notmuch new --no-hooks`.

The unit tests drive fake engines: bash scripts (they start in milliseconds,
so a short bound is not spent on interpreter start-up) that record their
argv, the three notmuch overrides in their environment, and every signal they
receive. The last test drives the REAL notmuch on a tmp root, with a
concurrent writer that provably holds the write lock before the refresh
starts: the design point that a reader is never blocked past the bound.

Hermetic: every path is under tmp_path, the profile is a tmp file, and each
call hands `refresh` its whole environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sessionmail import _fresh, _index

NOTMUCH = shutil.which("notmuch")

#: Records, then behaves by $FRESH_MODE. Each line of $FRESH_LOG is one fact.
#: A long wait is a backgrounded `sleep` with its fds closed, so the fake's
#: stderr pipe is held by the fake alone and closes when it dies.
FAKE = r"""#!/bin/bash
log="$FRESH_LOG"
mode="${FRESH_MODE:-ok}"
on_int() { echo "signal INT" >> "$log"; [ "$mode" = ignore-int ] && return; kill "$kid" 2>/dev/null; exit 1; }
on_term() { echo "signal TERM" >> "$log"; kill "$kid" 2>/dev/null; exit 1; }
trap on_int INT
trap on_term TERM
{ printf 'argv'; printf ' %s' "$@"; printf '\n'; } >> "$log"
echo "env NOTMUCH_CONFIG=${NOTMUCH_CONFIG-unset} NOTMUCH_DATABASE=${NOTMUCH_DATABASE-unset} NOTMUCH_PROFILE=${NOTMUCH_PROFILE-unset}" >> "$log"
[ -t 0 ] && echo "stdin tty" >> "$log"
case "$mode" in
  ok) exit 0 ;;
  fail) echo "first line" >&2; echo "Error: Cannot open database" >&2; exit 3 ;;
  flood) head -c 1048576 /dev/zero | tr '\0' 'x' >&2; exit 0 ;;
  sleep|ignore-int)
    while :; do sleep 30 </dev/null >/dev/null 2>&1 & kid=$!; wait "$kid"; done ;;
esac
"""


@pytest.fixture
def fake(tmp_path):
    """A host-mode environment with a profile file and the recording fake as
    the engine. `fake.env` is the whole environment `refresh` is handed."""
    engine = tmp_path / "bin" / "notmuch"
    engine.parent.mkdir()
    engine.write_text(FAKE)
    engine.chmod(0o755)
    xdg = tmp_path / "xdg"
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(xdg),
           "PATH": "/usr/bin:/bin", "SESSIONMAIL_NOTMUCH": str(engine),
           "FRESH_LOG": str(tmp_path / "fresh.log")}
    profile = _index.profile_config(env=env)
    profile.parent.mkdir(parents=True)
    profile.write_text("[database]\npath=/nonexistent\n")

    class Fake:
        pass

    fx = Fake()
    fx.env, fx.profile, fx.engine, fx.log = env, profile, engine, tmp_path / "fresh.log"

    def lines() -> list[str]:
        return fx.log.read_text().splitlines() if fx.log.exists() else []

    fx.lines = lines
    _index.clear_cache()
    yield fx
    _index.clear_cache()


def test_a_clean_refresh_runs_the_exact_argv_and_says_nothing(fake):
    assert _fresh.refresh(fake.env) == ""
    assert fake.lines()[0] == f"argv --config={fake.profile} new --no-hooks --quiet"
    assert "stdin tty" not in fake.lines()


def test_the_overrides_never_reach_the_child(fake):
    """A planted NOTMUCH_CONFIG, NOTMUCH_DATABASE or NOTMUCH_PROFILE would
    beat `--config` (E3, E14): `new` would then write another index."""
    env = {**fake.env, "NOTMUCH_CONFIG": "/planted/config",
           "NOTMUCH_DATABASE": "/planted/db", "NOTMUCH_PROFILE": "planted"}
    assert _fresh.refresh(env) == ""
    assert ("env NOTMUCH_CONFIG=unset NOTMUCH_DATABASE=unset NOTMUCH_PROFILE=unset"
            in fake.lines())


@pytest.mark.parametrize("off", ["0", "0.0"])
def test_zero_is_off_and_runs_nothing(fake, off):
    assert _fresh.refresh({**fake.env, _fresh.VAR: off}) == ""
    assert fake.lines() == []
    assert not _fresh.enabled({**fake.env, _fresh.VAR: off})


def test_the_bound_stops_notmuch_with_sigint_not_sigterm(fake):
    """SIGTERM throws away everything before notmuch's first autocommit, so
    a bound that used it would never drain a backlog (design-001, MEASURED).
    The fake records which signal it got."""
    env = {**fake.env, "FRESH_MODE": "sleep", _fresh.VAR: "0.5"}
    start = time.monotonic()
    note = _fresh.refresh(env)
    took = time.monotonic() - start
    assert took < 3, took
    assert "signal INT" in fake.lines()
    assert "signal TERM" not in fake.lines()
    assert "did not finish within 0.5s (notmuch.refresh)" in note
    assert "SIGINT" in note and note.endswith("reading the index as it was")


def test_a_notmuch_that_ignores_sigint_is_killed_after_the_grace(fake, monkeypatch):
    monkeypatch.setattr(_fresh, "GRACE", 0.3)
    env = {**fake.env, "FRESH_MODE": "ignore-int", _fresh.VAR: "0.5"}
    start = time.monotonic()
    note = _fresh.refresh(env)
    assert time.monotonic() - start < 3
    assert "signal INT" in fake.lines()
    assert "signal TERM" not in fake.lines()
    assert "ignored SIGINT for 0.3s and was killed" in note


def test_a_failed_refresh_quotes_its_last_stderr_line(fake):
    note = _fresh.refresh({**fake.env, "FRESH_MODE": "fail"})
    assert "exited 3: Error: Cannot open database" in note
    assert "first line" not in note
    assert note.endswith("reading the index as it was")


def test_a_stderr_flood_does_not_stall_the_refresh(fake):
    """notmuch writes a line per non-mail file even under --quiet (754,893
    bytes for 3,000 files, MEASURED). Waited on with `Popen.wait` and a PIPE,
    the child blocks on a full pipe and the refresh runs to its bound; under
    `communicate` it finishes at once."""
    env = {**fake.env, "FRESH_MODE": "flood", _fresh.VAR: "5"}
    start = time.monotonic()
    assert _fresh.refresh(env) == ""
    assert time.monotonic() - start < 3
    assert "signal INT" not in fake.lines()


def test_store_mode_never_refreshes(fake, tmp_path):
    """A store's index is kept by whoever keeps the store."""
    store_profile = tmp_path / "home" / ".notmuch-config.somestore"
    store_profile.parent.mkdir(parents=True, exist_ok=True)
    store_profile.write_text("[database]\npath=/nonexistent\n")
    env = {**fake.env, _index.VAR: _index.STORE, "NOTMUCH_PROFILE": "somestore"}
    assert _index.resolve(env).config == store_profile      # a real store profile
    assert _fresh.refresh(env) == ""
    assert fake.lines() == []
    assert not _fresh.enabled(env)


def test_no_profile_never_refreshes(fake):
    fake.profile.unlink()
    assert _index.resolve(fake.env).refused
    assert _fresh.refresh(fake.env) == ""
    assert fake.lines() == []


def test_no_binary_never_refreshes(fake, tmp_path):
    env = {**fake.env, "SESSIONMAIL_NOTMUCH": str(tmp_path / "no-notmuch-here")}
    assert _fresh.refresh(env) == ""
    assert fake.lines() == []


@pytest.mark.parametrize("given, bound", [
    ("x", 2.0), ("-1", 2.0), ("nan", 2.0), ("inf", 2.0), ("", 2.0), ("  ", 2.0),
    ("0", 0.0), ("0.25", 0.25), (" 7 ", 7.0),
])
def test_the_bound_reads_the_variable_and_a_bad_value_is_the_default(given, bound):
    assert _fresh.seconds({_fresh.VAR: given}) == bound


def test_the_default_is_two_seconds():
    assert _fresh.DEFAULT_SECONDS == 2.0
    assert _fresh.seconds({}) == 2.0


# ── a real notmuch, a concurrent writer ─────────────────────────────────────

MESSAGES = 6000


def _letters(new: Path, count: int) -> None:
    for n in range(count):
        (new / f"{n:06d}.M{n}P1.fresh").write_text(
            f"From: beta@example.invalid\nTo: alpha@example.invalid\n"
            f"Date: Sun, 09 Jun 2030 12:00:00 +1000\nSubject: letter {n}\n"
            f"Message-ID: <fresh-{n}@example.invalid>\n\nbody {n}\n")


@pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")
def test_a_refresh_behind_a_live_writer_skips_at_the_bound(tmp_path):
    """The super's design point: assume a concurrent live `notmuch new` (a
    host's own job); never block a reader past the bound.

    The holder is a `notmuch new` over MESSAGES letters with a small
    autocommit, so it commits as it goes. A count above 0 while the holder
    is still running PROVES it holds the write lock before the refresh
    starts (design-001 final review, advisory 2), so the roles cannot swap
    and the timing below does not depend on the host's load. The refresh
    then waits on the lock, gets SIGINT at the bound, and exits at once;
    the holder carries on and indexes everything."""
    root = tmp_path / "mail"
    new = root / "box" / "new"
    new.mkdir(parents=True)
    for sub in ("cur", "tmp"):
        (root / "box" / sub).mkdir()
    xdg = tmp_path / "xdg"
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(xdg),
           "PATH": f"{os.path.dirname(NOTMUCH)}:/usr/bin:/bin",
           "SESSIONMAIL_NOTMUCH": NOTMUCH, _fresh.VAR: "0.3"}
    (tmp_path / "home").mkdir()
    profile = _index.profile_config(env=env)
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={root}\nautocommit=20\n"
                       "[new]\ntags=unread;\n[maildir]\nsynchronize_flags=false\n")
    assert profile.is_relative_to(tmp_path) and root.is_relative_to(tmp_path)
    child = {"HOME": env["HOME"], "PATH": env["PATH"]}

    def count() -> int:
        done = subprocess.run([NOTMUCH, f"--config={profile}", "count", "*"],
                              capture_output=True, text=True, timeout=30, env=child)
        return int(done.stdout.strip() or 0) if done.returncode == 0 else 0

    # An empty database first, then the letters: the holder's `new` is the
    # one that indexes them.
    subprocess.run([NOTMUCH, f"--config={profile}", "new", "--quiet", "--no-hooks"],
                   check=True, capture_output=True, timeout=60, env=child)
    assert count() == 0
    _letters(new, MESSAGES)

    _index.clear_cache()
    holder = subprocess.Popen([NOTMUCH, f"--config={profile}", "new", "--quiet", "--no-hooks"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=child)
    try:
        deadline = time.monotonic() + 60
        while count() == 0:
            assert holder.poll() is None, "the holder finished before it was seen holding"
            assert time.monotonic() < deadline, "the holder never committed"
            time.sleep(0.02)
        assert holder.poll() is None, "the holder finished before the refresh could start"

        start = time.monotonic()
        note = _fresh.refresh(env)
        took = time.monotonic() - start
        assert took < 1.5, took
        assert "did not finish within 0.3s" in note, note
        assert holder.wait(timeout=120) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
    _index.clear_cache()
    assert count() == MESSAGES
