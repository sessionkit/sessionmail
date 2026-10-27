"""memory wake's IDENTITY section (SM-980), against a FAKE `session` on PATH.

The contract is sessiontools ST-272's design/identity-block.md (store 593b4e844b):
`session identity <sid>` prints the block, exit 0; exit 3 = no block; any other exit
is a fault. Wake passes the SID explicitly, waits 2 s at most (killing the child's
process group), prints stdout VERBATIM under its banner (40 lines at most, then one
note), and otherwise prints one note line. The wake never fails because of it.

The fake records its argv and acts per a mode file beside it. Temp roots only, and
an environment built from nothing, as in test_wake_two_boxes.py.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sessionmail import _mhs

WAKE = Path(_mhs.MHS_DIR) / "session-mail-wake"
SID = "7ed6b16b-31bd-fe66-35a0-284b56bbe9ec"
BANNER = "═══ IDENTITY — from `session identity`, at launch ═══"
BLOCK = ("Session id   : 7ed6b16b-31bd-fe66-35a0-284b56bbe9ec\n"
         "Seat         : mail+ab-12.worker\n"
         "Nickname     : none (no name given)\n"
         "  indented   :  kept as  it came  \n"
         "Key          : `not yet` $(not run)\n")

FAKE = """#!/bin/sh
here=$(dirname "$0")
printf '%s\\n' "$@" > "$here/argv"
mode=$(cat "$here/mode")
case "$mode" in
  ok)      cat "$here/block"; exit 0 ;;
  none)    echo "session identity: no identity block for $2" >&2; exit 3 ;;
  other)   echo "session: unknown verb 'identity'" >&2; exit 2 ;;
  hang)    sleep 10 & echo $! > "$here/grandchild"; wait; exit 0 ;;
esac
exit 99
"""


@pytest.fixture
def seat(tmp_path: Path):
    """(env, bin dir, box dir): a stub notmuch, a fake session, a box named SID."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    notmuch = bin_dir / "notmuch"
    notmuch.write_text("#!/bin/sh\nexit 0\n")
    notmuch.chmod(0o755)
    session = bin_dir / "session"
    session.write_text(FAKE)
    session.chmod(0o755)
    (bin_dir / "mode").write_text("ok")
    (bin_dir / "block").write_text(BLOCK)
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "mail"
    box = root / SID
    for sub in ("new", "cur", "tmp"):
        (box / sub).mkdir(parents=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home),
           "SESSION_MAIL_ROOT": str(root), "SESSIONMAIL_PYTHON": sys.executable,
           "CLAUDE_CODE_SESSION_ID": SID}
    return env, bin_dir, box


def wake(env, *argv, **extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WAKE), *argv], capture_output=True,
                          text=True, timeout=60, env={**env, **extra})


def head(out: str) -> list[str]:
    """The stdout lines before BOOT's banner: the IDENTITY section, blanks dropped."""
    lines = out.splitlines()
    end = next(i for i, ln in enumerate(lines) if ln.startswith("═══ BOOT"))
    return [ln for ln in lines[:end] if ln.strip()]


def test_exit_0_prints_the_block_verbatim_under_the_banner(seat):
    env, bin_dir, _ = seat
    r = wake(env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert out.startswith(BANNER + "\n" + BLOCK), out
    assert "— identity shown · ≈" in out
    # SID explicit: argv is exactly `identity <sid>`
    assert (bin_dir / "argv").read_text() == f"identity\n{SID}\n"


def test_sid_is_the_environments_never_the_box(seat, tmp_path):
    env, bin_dir, _ = seat
    other = tmp_path / "mail" / "someone-else"
    for sub in ("new", "cur", "tmp"):
        (other / sub).mkdir(parents=True)
    r = wake(env, "someone-else")
    assert r.returncode == 0, r.stderr
    assert (bin_dir / "argv").read_text() == f"identity\n{SID}\n"


def test_claude_session_id_is_the_fallback(seat):
    env, bin_dir, _ = seat
    env = {k: v for k, v in env.items() if k != "CLAUDE_CODE_SESSION_ID"}
    r = wake(env, SID, CLAUDE_SESSION_ID=SID)
    assert r.returncode == 0, r.stderr
    assert (bin_dir / "argv").read_text() == f"identity\n{SID}\n"


def test_no_sid_is_one_note_and_runs_nothing(seat):
    env, bin_dir, _ = seat
    env = {k: v for k, v in env.items() if k != "CLAUDE_CODE_SESSION_ID"}
    r = wake(env, SID)
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == ["identity: no session id in the environment — "
                              "no block to look up"]
    assert not (bin_dir / "argv").exists()


def test_exit_3_omits_the_section_with_one_note(seat):
    env, bin_dir, _ = seat
    (bin_dir / "mode").write_text("none")
    r = wake(env)
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == ["identity: no identity block for this session "
                              "(session identity exit 3)"]
    assert BANNER not in r.stdout


def test_absent_from_path_is_one_note(seat):
    env, bin_dir, _ = seat
    (bin_dir / "session").unlink()
    r = wake(env)
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == ["identity: `session` is not on PATH — no identity block"]


def test_other_exit_is_could_not_look(seat):
    env, bin_dir, _ = seat
    (bin_dir / "mode").write_text("other")
    r = wake(env)
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == ["identity: could not look (session identity exit 2)"]
    assert BANNER not in r.stdout
    assert "unknown verb 'identity'" in r.stderr  # the ⚠ echo names the fault


def test_timeout_kills_the_group_and_is_could_not_look(seat):
    env, bin_dir, _ = seat
    (bin_dir / "mode").write_text("hang")
    t0 = time.monotonic()
    r = wake(env)
    took = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    # 2 s budget plus the rest of the wake, well short of the fake's 10 s.
    assert took < 7, took
    assert head(r.stdout) == ["identity: could not look "
                              "(session identity timed out after 2 s)"]
    # `sleep 10` is the fake's CHILD and holds the pipe. A plain kill of the fake
    # would leave it running (the wake closes the pipe unread, so timing alone
    # cannot tell): only the GROUP kill takes the grandchild down too.
    gc = int((bin_dir / "grandchild").read_text())
    assert not _alive(gc), f"grandchild {gc} outlived the timeout: no group kill"


def _alive(pid: int) -> bool:
    """True while `pid` runs. A killed orphan is reaped by init; allow it 1 s."""
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        if time.monotonic() > deadline:
            return True
        time.sleep(0.05)


def test_exit_0_with_empty_stdout_says_so_under_the_banner(seat):
    env, bin_dir, _ = seat
    (bin_dir / "block").write_text("")
    r = wake(env)
    assert r.returncode == 0, r.stderr
    got = head(r.stdout)
    assert got[:2] == [BANNER, "(session identity exited 0 and printed nothing)"]
    assert got[2].startswith("— identity shown · ≈")


def test_over_40_lines_shows_40_then_one_note(seat):
    env, bin_dir, _ = seat
    (bin_dir / "block").write_text("".join(f"line {i:02d}\n" for i in range(1, 46)))
    r = wake(env)
    assert r.returncode == 0, r.stderr
    got = head(r.stdout)
    assert got[0] == BANNER
    assert got[1:41] == [f"line {i:02d}" for i in range(1, 41)]
    assert got[41] == (f"— identity block truncated: 5 more line(s); "
                       f"`session identity {SID}` prints it whole")
    assert got[42].startswith("— identity truncated · ≈")
    assert len(got) == 43


def test_exactly_40_lines_is_not_truncated(seat):
    env, bin_dir, _ = seat
    (bin_dir / "block").write_text("".join(f"line {i:02d}\n" for i in range(1, 41)))
    got = head(wake(env).stdout)
    assert len(got) == 42 and "truncated" not in "\n".join(got)


def test_identity_is_first_ahead_of_boot_and_recent(seat):
    env, _, _ = seat
    out = wake(env).stdout
    i, b, r = (out.index(BANNER), out.index("═══ BOOT"), out.index("═══ RECENT"))
    assert i == 0 and i < b < r


@pytest.mark.parametrize("mode", ["ok", "none", "other", "hang", "absent", "nosid"])
def test_wake_exit_0_and_new_unchanged_in_every_case(seat, mode):
    env, bin_dir, box = seat
    letter = box / "new" / "1.letter"
    letter.write_text("X-Original-To: someone\nSubject: hi\n\nbody\n")
    before = {p.name: p.read_bytes() for p in (box / "new").iterdir()}
    if mode == "absent":
        (bin_dir / "session").unlink()
    elif mode == "nosid":
        env = {k: v for k, v in env.items() if k != "CLAUDE_CODE_SESSION_ID"}
    else:
        (bin_dir / "mode").write_text(mode)
    r = wake(env, SID)
    assert r.returncode == 0, r.stderr
    assert {p.name: p.read_bytes() for p in (box / "new").iterdir()} == before
    assert sorted(os.listdir(box / "cur")) == []


def test_dry_run_prints_would_run_and_runs_nothing(seat):
    env, bin_dir, _ = seat
    r = wake(env, "-n")
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == [BANNER, f"  would run    : session identity {SID}"]
    assert not (bin_dir / "argv").exists()


def test_spawn_failure_is_one_could_not_look_line(seat):
    """An executable `session` that cannot be exec'd (its interpreter is missing)
    fails in Popen itself: one line naming the errno class, never a traceback."""
    env, bin_dir, _ = seat
    (bin_dir / "session").write_text("#!/nonexistent/sm980-interpreter\n")
    r = wake(env)
    assert r.returncode == 0, r.stderr
    assert head(r.stdout) == ["identity: could not look "
                              "(session identity could not start: FileNotFoundError)"]
    assert "Traceback" not in r.stderr
    assert "IDENTITY degrades to COULD-NOT-LOOK" in r.stderr


def _wake_module():
    loader = importlib.machinery.SourceFileLoader("sm980_wake", str(WAKE))
    spec = importlib.util.spec_from_loader("sm980_wake", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_an_unexpected_error_never_raises_out_of_the_section(monkeypatch, capsys):
    """The wrapper: whatever goes wrong inside, the section is one line and 0 tok."""
    mod = _wake_module()

    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mod, "identity_sid", boom)
    assert mod.identity_section() == 0
    assert capsys.readouterr().out == "identity: could not look (RuntimeError)\n\n"
