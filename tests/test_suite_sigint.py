"""SM-1035: the suite passes however it was launched, SIGINT included.

SK-251's 0.3.3 cut ran the full suite as `( nohup cut-mail.sh suite ... & )`.
An async job in a non-interactive shell starts with SIGINT IGNORED, and
every child inherits that. Six tests that send a child ONE SIGINT went red,
and green when re-run in the foreground, which read as host load.
MEASURED at 1e2d120: the 3 files red 6/106 at idle when launched so,
and green 106/106 in the foreground under a CPU burner at load 15.

The conftest's `sigint_at_its_default` is the fix. These tests launch the six
exactly as the cut did, through a real bash `&`, and they measure the
precondition themselves, so a green here cannot come from a launch that
never ignored SIGINT at all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from sessionmail import _fresh
from conftest import sigint_unignored
from test_fresh import fake  # noqa: F401 - `fake` is a fixture

REPO = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"

#: The six that went red under the cut's launch (SK-251 do-001 r1, 07-suite.txt).
VICTIMS = (
    "tests/test_fresh.py::test_the_bound_stops_notmuch_with_sigint_not_sigterm",
    "tests/test_fresh.py::test_a_notmuch_that_ignores_sigint_is_killed_after_the_grace",
    "tests/test_fresh.py::test_a_refresh_behind_a_live_writer_skips_at_the_bound",
    "tests/test_mark_index_lag.py::test_a_refresh_bounded_out_says_so_before_the_refusal",
    ("tests/test_pager_monitor.py::"
     "test_one_ctrl_c_ends_a_hand_listen_within_the_budget_with_a_log_lock_held[seq]"),
    ("tests/test_pager_monitor.py::"
     "test_one_ctrl_c_ends_a_hand_listen_within_the_budget_with_a_log_lock_held[quiet]"),
)

#: What a child says its SIGINT handler is.
PROBE = "import signal; print(signal.getsignal(signal.SIGINT))"


def _env() -> dict[str, str]:
    """This run's environment, less pytest's own markers."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}


def _as_async_job(*argv: str, timeout: float) -> subprocess.CompletedProcess:
    """ARGV run as the cut ran its suite: an `&` job of a non-interactive
    bash, which then waits for it."""
    return subprocess.run([BASH, "-c", '"$@" & wait $!', "bash", *argv],
                          cwd=REPO, env=_env(), capture_output=True, text=True,
                          timeout=timeout, check=False)


def test_an_async_job_starts_with_sigint_ignored():
    """The rig's precondition, measured: what the cut's launch handed pytest."""
    said = _as_async_job(sys.executable, "-c", PROBE, timeout=60)
    assert said.returncode == 0, said.stderr
    assert said.stdout.strip() == str(signal.SIG_IGN), said.stdout


def test_a_child_a_test_spawns_starts_with_sigint_at_its_default():
    """However this run was launched, the child's SIGINT is its default:
    Python installs KeyboardInterrupt only over a SIGINT not ignored."""
    said = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                          text=True, timeout=60, env=_env(), check=False)
    assert said.returncode == 0, said.stderr
    assert said.stdout.strip() == str(signal.default_int_handler), said.stdout


#: Every signal a test could see handled, for "no other signal is touched".
_OTHERS = [s for s in signal.valid_signals()
           if s not in (signal.SIGINT, signal.SIGKILL, signal.SIGSTOP)]


def _dispositions() -> dict:
    return {s: signal.getsignal(s) for s in _OTHERS}


def test_the_fixture_unignores_sigint_and_puts_sig_ign_back():
    """The ruled shape (a): acts ONLY on an ignored SIGINT, puts back exactly
    that, and touches no other signal."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        others = _dispositions()
        with sigint_unignored():
            assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
            assert _dispositions() == others
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
        assert _dispositions() == others
    finally:
        signal.signal(signal.SIGINT, previous)


def test_the_fixture_leaves_a_sigint_not_ignored_exactly_as_it_is():
    """A foreground run is unchanged: a handled or default SIGINT is never
    replaced, inside or after."""
    def mine(signum, frame):
        raise AssertionError("never sent")

    previous = signal.getsignal(signal.SIGINT)
    try:
        for given in (mine, signal.SIG_DFL, signal.default_int_handler):
            signal.signal(signal.SIGINT, given)
            others = _dispositions()
            with sigint_unignored():
                assert signal.getsignal(signal.SIGINT) is given
            assert signal.getsignal(signal.SIGINT) is given
            assert _dispositions() == others
    finally:
        signal.signal(signal.SIGINT, previous)


def test_an_inherited_sig_ign_is_what_decides_it(fake, monkeypatch):  # noqa: F811
    """Both directions, in one process, on the same fake and the same refresh.
    With SIGINT ignored here, the child inherits it: the fake never sees the
    SIGINT and is killed at the grace, which is the RED the cut saw. With it at
    its default, the same call stops at the bound with SIGINT. Each leg
    asserts only what holds at any load: an ignored SIGINT is ignored from
    the child's first instruction, and a default one stops it either way, by
    the fake's trap (exit 1) or, before the trap is set, by the signal (-2).
    So the short grace is the ignored leg's alone, where the kill comes at
    the grace whatever the load; the honoured leg runs at the real GRACE, so
    a loaded host's slow trap is never read as an ignored SIGINT."""
    real_grace = _fresh.GRACE
    monkeypatch.setattr(_fresh, "GRACE", 0.3)
    env = {**fake.env, "FRESH_MODE": "sleep", _fresh.VAR: "0.5"}

    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        ignored = _fresh.refresh(env)
    finally:
        signal.signal(signal.SIGINT, previous)
    assert "ignored SIGINT for 0.3s and was killed" in ignored, ignored
    assert "signal INT" not in fake.lines()

    assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN
    monkeypatch.setattr(_fresh, "GRACE", real_grace)
    honoured = _fresh.refresh(env)
    assert "did not finish within 0.5s" in honoured and "stopped with SIGINT" in honoured, \
        honoured
    assert "signal TERM" not in fake.lines()


def test_the_six_pass_when_the_suite_is_an_async_job():
    """The six, launched as the cut launched them. Without the fixture each
    child ignores the ONE SIGINT it is sent, and all six fail (RED at
    1e2d120: SM-1035 write-tests-001's evidence)."""
    run = _as_async_job(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "-rs", *VICTIMS, timeout=600)
    out = run.stdout[-4000:] + run.stderr[-2000:]
    assert run.returncode == 0, out
    assert "failed" not in run.stdout and "error" not in run.stdout, out
    # The notmuch-backed three skip on a host with no notmuch; none may vanish.
    tail = run.stdout.strip().splitlines()[-1]
    counts = {word: int(n) for n, word in
              (part.split()[:2] for part in tail.split(" in ")[0].split(", "))}
    assert counts.get("passed", 0) + counts.get("skipped", 0) == len(VICTIMS), tail
