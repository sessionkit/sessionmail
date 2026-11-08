"""SM-998: a reader trusts an identity word only from a claude it runs under.

PROPOSED, not ratified: `openspec/changes/inherited-identity/`. Claude puts
`CLAUDE_PID=<its own pid>` into every child's environment. A tmux server
started from inside a seat copies that environment, seat words and all, into
every plain pane, whose chain is `shell -> tmux -> 1`: no claude in it. So a
`CLAUDE_PID` that is not an ancestor of this process marks the seat words as
inherited, and `message`, `pager` and `memory` drop them first
(`_seat.drop_inherited`).

Two layers. The predicate is tested over an injected process table (every
shape the super named: a direct child of claude, a grandchild through bash,
a backgrounded Bash, a hook; and the plain pane, an orphan, a dead pid, no
evidence, garbage, a table that cannot be read). Then the verbs, end to end,
as real child processes: `pager status` and `memory wake` with
`CLAUDE_CODE_AGENT=x` and a `CLAUDE_PID` that is a sibling's (not an
ancestor) resolve no seat and name the pid; with `CLAUDE_PID` = this test
process (an ancestor, standing in for claude) they resolve seat `x` as
before, from each process shape. Last, the real thing when tmux is here: a
pane on a private tmux server this test starts.

Every test here fails on b90d227 (no `drop_inherited`: the unit tests cannot
import it, and the end-to-end children resolve seat `x` from the inherited
word).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from sessionmail import _seat

BIN = Path(sys.executable).parent
PAGER = BIN / "pager"
MEMORY = BIN / "memory"

#: The pids of the injected table: claude, the shells under it, the verb.
CLAUDE, BASH, BG_BASH, HOOK_SH, VERB, TMUX = 4000, 4100, 4101, 4200, 4300, 4400


def table(chain: dict[int, int]):
    """A `parent_of` over CHAIN (pid -> ppid); a pid it lacks cannot be read."""
    def parent_of(pid: int) -> int:
        if pid not in chain:
            raise _seat.CannotLook(f"no row for {pid}")
        return chain[pid]
    return parent_of


def env_with(pid: object, **words: str) -> dict[str, str]:
    return {_seat.CLAUDE_PID_VAR: str(pid), **words}


# ── the predicate: trusted shapes ─────────────────────────────────────────

@pytest.mark.parametrize("shape, chain", [
    # the pager monitor: claude starts it directly
    ("direct child of claude", {VERB: CLAUDE, CLAUDE: 1}),
    # a Bash tool call: claude -> bash -> the verb
    ("grandchild through bash", {VERB: BASH, BASH: CLAUDE, CLAUDE: 1}),
    # a backgrounded Bash: claude -> bash -> bash (the & subshell) -> the verb
    ("backgrounded Bash", {VERB: BG_BASH, BG_BASH: BASH, BASH: CLAUDE, CLAUDE: 1}),
    # a hook: claude -> sh (the hook command) -> the verb
    ("hook process", {VERB: HOOK_SH, HOOK_SH: CLAUDE, CLAUDE: 1}),
])
def test_a_process_under_its_claude_keeps_its_words(shape, chain):
    env = env_with(CLAUDE, CLAUDE_CODE_AGENT="x")
    assert _seat.inherited_from(env, start=VERB, parent_of=table(chain)) is None, shape
    assert _seat.drop_inherited(env, start=VERB, parent_of=table(chain)) is None
    assert env == env_with(CLAUDE, CLAUDE_CODE_AGENT="x"), "trusted words must stay"
    assert _seat.resolve(env).name == "x"


def test_claude_itself_is_its_own_ancestor():
    """A verb claude runs with exec (no shell left between) has CLAUDE_PID
    as its own parent; the walk also accepts CLAUDE_PID == this pid."""
    assert _seat.inherited_from(env_with(VERB), start=VERB, parent_of=table({})) is None


# ── the predicate: inherited shapes ───────────────────────────────────────

@pytest.mark.parametrize("shape, chain", [
    # the bug: a plain pane on a tmux server a seat started (daemonised)
    ("plain pane on a seat-started tmux server", {VERB: BASH, BASH: TMUX, TMUX: 1}),
    # CHOSEN: an orphan whose claude has exited is no longer under any seat
    ("orphan whose claude exited", {VERB: BASH, BASH: 1}),
    ("a chain that ends at pid 0", {VERB: 0}),
])
def test_a_process_not_under_that_claude_has_inherited_words(shape, chain):
    assert _seat.inherited_from(env_with(CLAUDE), start=VERB,
                                parent_of=table(chain)) == CLAUDE, shape


def test_a_dead_claude_pid_is_inherited():
    """CLAUDE_PID names a process that no longer exists: nothing in this
    chain is it, and the chain is readable to pid 1."""
    chain = {VERB: BASH, BASH: TMUX, TMUX: 1}
    assert _seat.inherited_from(env_with(99999), start=VERB, parent_of=table(chain)) == 99999


# ── the predicate: no evidence, and fail-open ─────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "-5", "0", "1", "+4000",
                                 "4000.0", "0x10", "٤٠٠٠"])
def test_no_usable_claude_pid_changes_nothing(raw):
    """Unset, blank, not a plain ASCII number, or <= 1: no evidence either
    way, so behaviour is today's. The table is never consulted."""
    env = {"CLAUDE_CODE_AGENT": "x"}
    if raw is not None:
        env[_seat.CLAUDE_PID_VAR] = raw

    def never(pid: int) -> int:
        raise AssertionError(f"walked to {pid} with CLAUDE_PID={raw!r}")
    assert _seat.inherited_from(env, start=VERB, parent_of=never) is None
    assert _seat.drop_inherited(env, start=VERB, parent_of=never) is None
    assert env.get("CLAUDE_CODE_AGENT") == "x"


def test_a_padded_claude_pid_is_read():
    chain = {VERB: BASH, BASH: 1}
    assert _seat.inherited_from(env_with(f" {CLAUDE} "), start=VERB,
                                parent_of=table(chain)) == CLAUDE


@pytest.mark.parametrize("shape, chain", [
    ("ps cannot read a hop", {VERB: BASH}),          # BASH has no row
    ("the chain repeats", {VERB: BASH, BASH: VERB}),
    ("the chain never ends", {p: p + 1 for p in range(VERB, VERB + 200)}),
])
def test_ancestry_that_cannot_be_measured_trusts_the_words(shape, chain):
    """CHOSEN, fail-open: a reader that cannot look must not deafen a seat."""
    env = env_with(CLAUDE, CLAUDE_CODE_AGENT="x")
    assert _seat.inherited_from(env, start=VERB, parent_of=table(chain)) is None, shape
    assert _seat.drop_inherited(env, start=VERB, parent_of=table(chain)) is None
    assert env["CLAUDE_CODE_AGENT"] == "x"


# ── what drop_inherited does to the environment ───────────────────────────

PANE = {VERB: BASH, BASH: TMUX, TMUX: 1}
SEAT_WORDS = {
    "CLAUDE_CODE_AGENT": "foreman",
    "TM_AGENT_ADDRESS": "proj+t-1.foreman",
    "CLAUDE_CODE_SESSION_ID": "11111111-2222-4333-8444-555555555555",
    "CLAUDE_SESSION_ID": "11111111-2222-4333-8444-555555555555",
    "SESSION_SEAT": "foreman",
}


def test_inherited_words_are_dropped_and_the_pid_is_marked():
    env = env_with(CLAUDE, **SEAT_WORDS, EMAIL="proj+t-1.foreman@hq", PATH="/bin")
    assert _seat.drop_inherited(env, start=VERB, parent_of=table(PANE)) == CLAUDE
    assert not set(SEAT_WORDS) & set(env), env
    # EMAIL is a ruled declaration and is NOT touched; CLAUDE_PID stays too.
    assert env["EMAIL"] == "proj+t-1.foreman@hq"
    assert env[_seat.CLAUDE_PID_VAR] == str(CLAUDE)
    assert env["PATH"] == "/bin"
    assert env[_seat.INHERITED_MARK] == str(CLAUDE)


def test_with_no_seat_left_the_refusal_names_the_claude_pid():
    env = env_with(CLAUDE, **SEAT_WORDS)
    _seat.drop_inherited(env, start=VERB, parent_of=table(PANE))
    with pytest.raises(_seat.NoIdentity) as exc:
        _seat.resolve(env)
    text = str(exc.value)
    # the requirement that stands: the variables it looked for, in order
    assert "looked for TM_AGENT_ADDRESS, CLAUDE_CODE_AGENT, CLAUDE_CODE_SESSION_ID" in text
    assert (f"identity words inherited from claude pid {CLAUDE}, which this process "
            "does not run under") in text


def test_a_refusal_with_nothing_inherited_says_nothing_new():
    with pytest.raises(_seat.NoIdentity) as exc:
        _seat.resolve({})
    assert "inherited from claude pid" not in str(exc.value)


def test_nothing_to_drop_sets_no_mark():
    """A non-ancestor CLAUDE_PID with no seat words: nothing was inherited
    that a refusal should blame."""
    env = env_with(CLAUDE)
    assert _seat.drop_inherited(env, start=VERB, parent_of=table(PANE)) == CLAUDE
    assert _seat.INHERITED_MARK not in env


def test_a_child_of_a_dropping_verb_keeps_the_mark_for_the_same_pid():
    """`memory` drops, then starts its bundled script, which may start
    another verb: the words are gone, but the reason must still be said."""
    env = env_with(CLAUDE, **{_seat.INHERITED_MARK: str(CLAUDE)})
    _seat.drop_inherited(env, start=VERB, parent_of=table(PANE))
    assert env[_seat.INHERITED_MARK] == str(CLAUDE)


def test_a_stale_mark_is_removed():
    """A mark this tree did not set is not a reason: trusted, or another pid."""
    trusted = env_with(CLAUDE, CLAUDE_CODE_AGENT="x", **{_seat.INHERITED_MARK: "77"})
    _seat.drop_inherited(trusted, start=VERB, parent_of=table({VERB: CLAUDE}))
    assert _seat.INHERITED_MARK not in trusted
    other = env_with(CLAUDE, **{_seat.INHERITED_MARK: "77"})
    _seat.drop_inherited(other, start=VERB, parent_of=table(PANE))
    assert _seat.INHERITED_MARK not in other
    assert _seat.inherited_note({_seat.INHERITED_MARK: "not-a-pid"}) == ""


# ── the real process table ────────────────────────────────────────────────

def test_parent_pid_reads_the_real_table():
    assert _seat.parent_pid(os.getpid()) == os.getppid()
    with subprocess.Popen(["sleep", "30"]) as child:
        try:
            assert _seat.parent_pid(child.pid) == os.getpid()
            assert _seat.runs_under(os.getpid(), start=child.pid)
            assert not _seat.runs_under(child.pid)      # a child is no ancestor
        finally:
            child.kill()


def test_parent_pid_of_a_reaped_process_cannot_look():
    p = subprocess.Popen(["true"])
    p.wait()
    with pytest.raises(_seat.CannotLook):
        _seat.parent_pid(p.pid)


# ── end to end: the verbs as real children ────────────────────────────────

@pytest.fixture
def sibling():
    """A live process that is NOT an ancestor of anything this test starts."""
    p = subprocess.Popen(["sleep", "60"])
    yield p.pid
    p.kill()
    p.wait()


#: A synthetic session id: never a real box (the standing order on probes).
SID = "5e55a0de-0998-4998-8998-000000000998"


def run_env(tmp_path, claude_pid: int, **extra: str) -> dict[str, str]:
    """The hermetic environment plus a seat word, a session id and
    CLAUDE_PID, and a mail root holding the seat's box and the session's,
    so a resolved seat has something to read."""
    root = tmp_path / "mail" / "stores"
    for box in ("x", SID):
        for sub in ("new", "cur", "tmp"):
            (root / box / sub).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SESSIONMAIL_ROOT": str(root.parent),
           "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "PATH": f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}",
           "CLAUDE_CODE_AGENT": "x", "CLAUDE_CODE_SESSION_ID": SID,
           "CLAUDE_PID": str(claude_pid), **extra}
    # Literal names, not `_seat`'s: these children are the regression, and
    # must run (and fail for the right reason) on a tree without SM-998.
    env.pop("SESSIONMAIL_INHERITED_FROM", None)
    return env


def pager_status(env) -> subprocess.CompletedProcess:
    return subprocess.run([str(PAGER), "status"], capture_output=True, text=True,
                          env=env, timeout=60, stdin=subprocess.DEVNULL)


def test_pager_status_in_a_plain_pane_resolves_no_seat(tmp_path, sibling):
    p = pager_status(run_env(tmp_path, sibling))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "could not look: no identity in the environment" in p.stderr
    assert f"inherited from claude pid {sibling}" in p.stderr, p.stderr


def test_memory_wake_in_a_plain_pane_resolves_no_seat(tmp_path, sibling):
    p = subprocess.run([str(MEMORY), "wake"], capture_output=True, text=True,
                       env=run_env(tmp_path, sibling), timeout=60, stdin=subprocess.DEVNULL)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "memory wake: cannot resolve a box" in p.stderr, p.stderr
    assert f"inherited from claude pid {sibling}" in p.stderr, p.stderr
    assert "═══" not in p.stdout, "no readout of the seat's box"



MESSAGE = BIN / "message"


def test_message_send_in_a_plain_pane_refuses_and_sends_nothing(tmp_path, sibling):
    """The design's side effect, measured: a plain pane can no longer send
    as the seat. With no seat word left it refuses before any carrier runs,
    naming the pid; EMAIL (still set, a plus address) is not used on its own.
    The MTA is a recorder under tmp_path, asserted untouched."""
    sent = tmp_path / "sendmail.log"
    fake = tmp_path / "fake-sendmail"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >>{shlex.quote(str(sent))}\ncat >/dev/null\n")
    fake.chmod(0o755)
    body = tmp_path / "body.md"
    body.write_text("probe\n")
    env = run_env(tmp_path, sibling, EMAIL="proj+t-1.foreman@hq",
                  SESSIONMAIL_SENDMAIL=str(fake))
    assert env["SESSIONMAIL_SENDMAIL"] == str(fake)
    p = subprocess.run([str(MESSAGE), "send", "x", "-s", "probe", "-f", str(body)],
                       capture_output=True, text=True, env=env, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "no seat in the environment to send From" in p.stderr, p.stderr
    assert f"inherited from claude pid {sibling}" in p.stderr, p.stderr
    assert not sent.exists(), sent.read_text()


def test_message_list_in_a_plain_pane_lists_no_box_of_the_seat(tmp_path, sibling):
    env = run_env(tmp_path, sibling)
    p = subprocess.run([str(MESSAGE), "list"], capture_output=True, text=True,
                       env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert p.returncode != 0, p.stdout + p.stderr
    assert SID not in p.stdout and " x:" not in p.stdout, p.stdout
    assert f"inherited from claude pid {sibling}" in p.stderr, p.stderr

#: The process shapes the super named, each with this test process standing
#: in for claude (CLAUDE_PID = our pid, an ancestor of every shape).
SHAPES = {
    "direct child of claude (the pager monitor)": lambda verb: verb,
    "grandchild through bash (a Bash tool call)":
        lambda verb: ["bash", "-c", f"{_q(verb)}; exit $?"],
    "backgrounded Bash": lambda verb: ["bash", "-c", f"{_q(verb)} & wait $!"],
    "hook process (sh -c, the hook command)": lambda verb: ["/bin/sh", "-c", f"{_q(verb)}; :"],
}


def _q(argv: list[str]) -> str:
    return shlex.join(argv)


@pytest.mark.parametrize("shape", SHAPES)
def test_pager_status_under_claude_keeps_the_seat(tmp_path, shape):
    argv = SHAPES[shape]([str(PAGER), "status"])
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                       env=run_env(tmp_path, os.getpid()), stdin=subprocess.DEVNULL)
    assert "no identity" not in p.stderr, (shape, p.stderr)
    assert "inherited from claude pid" not in p.stdout + p.stderr, shape
    # Resolved: the seat's own rule line names seat x, and the session box
    # is read. (Its exit is 3, "not listening": no pager listens here.)
    assert "a mail To x@" in p.stderr, (shape, p.stderr)
    assert f"box {SID}" in p.stdout, (shape, p.stdout)


@pytest.mark.parametrize("shape", SHAPES)
def test_memory_wake_under_claude_keeps_the_seat(tmp_path, shape):
    argv = SHAPES[shape]([str(MEMORY), "wake"])
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                       env=run_env(tmp_path, os.getpid()), stdin=subprocess.DEVNULL)
    assert "cannot resolve a box" not in p.stderr, (shape, p.stderr)
    assert "inherited from claude pid" not in p.stdout + p.stderr, shape
    assert "CLAUDE_CODE_AGENT" in p.stdout + p.stderr, (shape, p.stdout, p.stderr)


def test_no_claude_pid_resolves_the_seat_as_today(tmp_path):
    env = run_env(tmp_path, 0)
    env.pop("CLAUDE_PID")
    p = subprocess.run([str(MEMORY), "wake"], capture_output=True, text=True,
                       env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert "cannot resolve a box" not in p.stderr, p.stderr
    assert "CLAUDE_CODE_AGENT" in p.stdout + p.stderr


# ── the real thing: a pane on a private tmux server ───────────────────────

@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_a_pane_on_a_tmux_server_started_under_claude_resolves_no_seat(tmp_path):
    """This test process stands in for the seat's claude: CLAUDE_PID is our
    pid. The server it starts daemonises, so the pane runs under tmux, not
    under us. Private socket under tmp_path; only that server is killed."""
    # A short socket dir of our own: macOS caps a socket path at 104 bytes,
    # and tmp_path alone runs past it.
    sockdir = tempfile.mkdtemp(prefix="sm998-")
    out = tmp_path / "pane.out"
    env = run_env(tmp_path, os.getpid())
    env.pop("TMUX", None)
    env["TMUX_TMPDIR"] = sockdir
    script = (f"{_q([str(PAGER), 'status'])} >{_q([str(out)])} 2>&1; "
              f"echo \"exit=$?\" >>{_q([str(out)])}")
    tmux = ["tmux", "-L", f"sm998-{uuid.uuid4().hex[:8]}", "-f", os.devnull]
    try:
        subprocess.run([*tmux, "new-session", "-d", "-s", "p", "/bin/sh", "-c", script],
                       env=env, check=True, timeout=30, capture_output=True)
        deadline = time.monotonic() + 30
        while "exit=" not in (out.read_text() if out.exists() else ""):
            assert time.monotonic() < deadline, "the pane never finished"
            time.sleep(0.05)
    finally:
        subprocess.run([*tmux, "kill-server"], env=env, timeout=30, capture_output=True)
        shutil.rmtree(sockdir, ignore_errors=True)
    text = out.read_text()
    assert "exit=2" in text, text
    assert f"inherited from claude pid {os.getpid()}" in text, text
