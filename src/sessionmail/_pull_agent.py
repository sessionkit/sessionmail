"""The puller's LaunchAgent: resolve the job, render its plist, install it.

`message setup --pull` (the `pull-agent` step in `_setup`) is the only
caller. The job is `<interpreter> -u -m sessionmail._puller`, where the
interpreter is the one that ran setup.

The job reads its config from the settings file, never from setup's
environment: the plist carries only PATH and HOME, and no config value and
no secret. So `resolve` reads the pull config twice, once with setup's
environment and once with the job's exactly, and refuses when the two
disagree (a key set only by a variable, or a variable that moves a value).
The plist is rendered from the JOB's config.

A plist at the target that is not a sessionmail puller is never overwritten,
and a job loaded under the label with no plist at the target is never booted
out: the label is configurable, and a host may already run a puller of its
own under it. OUR puller's plist is replaced; nobody else's.

Seams, each the same shape as `_launchd`'s: `launchctl` is
`_launchd.launchctl` (`$SESSIONMAIL_LAUNCHCTL`), `plutil` is
`$SESSIONMAIL_PLUTIL` (else /usr/bin/plutil). `on_macos`, `uid`,
`interpreter` and `listening` are module functions a test patches.
"""

from __future__ import annotations

import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import _launchd, _settings

#: The job's PATH: generic install prefixes, so `postmap`, notmuch, the
#: tailscale CLI and the secret command's tools are found under launchd.
JOB_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
#: How the job runs the puller, after the interpreter.
MODULE_ARGS = ("-u", "-m", "sessionmail._puller")
THROTTLE_SECONDS = 30

#: The seam for plutil, as `_launchd.LAUNCHCTL_VAR` is for launchctl.
PLUTIL_VAR = "SESSIONMAIL_PLUTIL"
DEFAULT_PLUTIL = "/usr/bin/plutil"
PLUTIL_TIMEOUT = 30.0

#: How long a booted-out job has to go before the install is FAILED, and
#: how long the listener has to answer. Module values a test shortens.
GONE_SECONDS = 10.0
POLL_SECONDS = 0.1
LISTEN_SECONDS = 2.0


class Refused(Exception):
    """The pull config cannot be installed as a job: setup refuses the plan."""


class Failed(Exception):
    """A tool failed while installing; the message names what is left where."""


@dataclass(frozen=True)
class Job:
    """The LaunchAgent to install, built from the job's own config."""

    config: object          # _puller.Config, resolved with the job's environment
    label: str
    home: Path
    plist: Path             # <home>/Library/LaunchAgents/<label>.plist
    log: Path               # <state_dir>/log/puller.log
    domain: str             # gui/<uid>
    body: bytes             # the rendered plist

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.label}"


@dataclass(frozen=True)
class Seen:
    """What is at the target now (read only)."""

    foreign: str            # why the target is not ours to touch; "" when it is
    current: bytes | None   # the plist's bytes at the target, None when absent
    loaded: bool


# -- seams -----------------------------------------------------------------------

def on_macos() -> bool:
    return sys.platform == "darwin"


def uid() -> int:
    return os.getuid()


def interpreter() -> str:
    return sys.executable


def listening(host: str, port: int) -> bool:
    """Whether something accepts a TCP connection at HOST:PORT. Connects and
    closes; sends nothing."""
    try:
        with socket.create_connection((host, port), timeout=LISTEN_SECONDS):
            return True
    except OSError:
        return False


def plutil_path(env: Mapping[str, str]) -> str:
    return env.get(PLUTIL_VAR) or DEFAULT_PLUTIL


# -- resolve and render ----------------------------------------------------------

def job_env(home: str) -> dict[str, str]:
    """The job's environment, exactly: what the plist gives it."""
    return {"HOME": home, "PATH": JOB_PATH}


def _shown(value: object) -> str:
    if value is None or value == "" or value == ():
        return "(unset)"
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        return f"{value[0]}:{value[1]}"         # the listener, as it is written
    if isinstance(value, tuple):
        return ",".join(str(v) for v in value)
    return str(value)


def resolve(env: Mapping[str, str]) -> Job:
    """The job for ENV's host, or Refused naming every reason at once: a
    required key unset, a key set only in this environment, a value that a
    variable here moves, or nothing to pull."""
    from . import _puller
    mine = _puller.config(env)
    if isinstance(mine, _puller.Missing):
        raise Refused(mine.why().removeprefix("REFUSED - "))
    home = (env.get("HOME") or "").strip()
    if not home:
        raise Refused("HOME is not set: the LaunchAgent is written under "
                      "$HOME/Library/LaunchAgents")
    theirs = _puller.config(job_env(home))
    reasons: list[str] = []
    if isinstance(theirs, _puller.Missing):
        for key in theirs.keys:
            reasons.append(f"{key} ({_settings.KEYS[key].var}) is set only in the environment; "
                           f"the LaunchAgent reads {_settings.SHOWN}: write {key} there")
        for key, why in theirs.invalid:
            reasons.append(f"{key} in {_settings.SHOWN} is not usable ({why}); this shell's "
                           f"{_settings.KEYS[key].var} hides it from you, not from the "
                           "LaunchAgent")
        if theirs.unreadable:
            reasons.append(f"the LaunchAgent could not read {_settings.SHOWN}: "
                           f"{theirs.unreadable}")
        raise Refused("; ".join(reasons))
    for d in mine.differences(theirs):
        names = " or ".join(d.variables)
        reasons.append(f"{d.key} ({names}): the LaunchAgent would use {_shown(d.theirs)}, "
                       f"this shell {_shown(d.mine)}: unset {names} or write {d.key} into "
                       f"{_settings.SHOWN}")
    if reasons:
        raise Refused("; ".join(reasons))
    if not theirs.unsorted_box and not _puller.read_boxes(theirs.boxes_file):
        raise Refused(f"nothing to pull: {theirs.boxes_file} names no box and pull.unsorted_box "
                      f"is unset. Write one box per line there (pull.boxes_file), or set "
                      f"pull.unsorted_box")
    plist = Path(home) / "Library" / "LaunchAgents" / f"{theirs.label}.plist"
    log = theirs.state_dir / "log" / "puller.log"
    body = render(theirs.label, interpreter(), home, log)
    return Job(config=theirs, label=theirs.label, home=Path(home), plist=plist, log=log,
               domain=f"gui/{uid()}", body=body)


def render(label: str, python: str, home: str, log: Path) -> bytes:
    """The plist's bytes: the same input gives the same bytes (`plistlib`,
    keys sorted), so a second run can compare them."""
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [python, *MODULE_ARGS],
        "EnvironmentVariables": job_env(home),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }, sort_keys=True)


def ours(data: bytes) -> bool:
    """Whether DATA is a plist that runs the sessionmail puller (any
    interpreter)."""
    try:
        got = plistlib.loads(data)
    except Exception:  # noqa: BLE001 - anything plistlib cannot read is not ours
        return False
    if not isinstance(got, dict):
        return False
    args = got.get("ProgramArguments")
    return (isinstance(args, list) and len(args) == 1 + len(MODULE_ARGS)
            and tuple(args[1:]) == MODULE_ARGS)


# -- read ------------------------------------------------------------------------

def _launchctl(env: Mapping[str, str], *args: str) -> subprocess.CompletedProcess:
    """`_launchd.launchctl`, its could-not-run as this module's Failed."""
    try:
        return _launchd.launchctl(env, *args)
    except _launchd.Failed as exc:
        raise Failed(str(exc)) from exc


def loaded(job: Job, env: Mapping[str, str]) -> bool:
    """`launchctl print gui/<uid>/<label>`: rc 0 means loaded."""
    return _launchctl(env, "print", job.target).returncode == 0


def see(job: Job, env: Mapping[str, str]) -> Seen:
    """What is at the target, read only. Raises Failed when launchctl
    cannot run."""
    is_loaded = loaded(job, env)
    path = job.plist
    if os.path.islink(path):
        return Seen(f"{path} is a link, not a sessionmail puller's plist", None, is_loaded)
    if os.path.lexists(path):
        if not path.is_file():
            return Seen(f"{path} is not a file", None, is_loaded)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return Seen(f"{path} cannot be read ({exc.strerror or exc})", None, is_loaded)
        if not ours(data):
            return Seen(f"a plist that is not a sessionmail puller is at {path}", data,
                        is_loaded)
        return Seen("", data, is_loaded)
    if is_loaded:
        return Seen(f"a job is loaded as {job.target} with no plist at {path}", None, True)
    return Seen("", None, False)


# -- install ---------------------------------------------------------------------

def _words(done: subprocess.CompletedProcess) -> str:
    said = " ".join((done.stderr or done.stdout or "").split())
    return f"rc {done.returncode}" + (f": {said}" if said else "")


def lint(path: Path, env: Mapping[str, str]) -> None:
    """`plutil -lint PATH`, or Failed."""
    tool = plutil_path(env)
    try:
        done = subprocess.run([tool, "-lint", str(path)], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=dict(env), check=False,
                              timeout=PLUTIL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Failed(f"plutil could not run ({tool}: {exc})") from exc
    if done.returncode != 0:
        raise Failed(f"plutil -lint refused the rendered plist ({_words(done)})")


def _gone(job: Job, env: Mapping[str, str], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if not loaded(job, env):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def install(job: Job, env: Mapping[str, str]) -> str:
    """Write, lint, put in place, reload. Returns what it did; raises Failed
    naming what is left where."""
    job.log.parent.mkdir(parents=True, exist_ok=True)
    job.plist.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{job.label}.", suffix=".plist",
                               dir=job.plist.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(job.body)
        os.chmod(tmp, 0o644)
        lint(Path(tmp), env)
        seen = see(job, env)               # again: nothing foreign arrived meanwhile
        if seen.foreign:
            raise Failed(f"{seen.foreign}: it is never overwritten")
        os.replace(tmp, job.plist)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    did = [f"wrote {job.plist}"]
    if seen.loaded:
        off = _launchctl(env, "bootout", job.target)
        if not _gone(job, env, GONE_SECONDS):
            raise Failed(f"{job.target} is still loaded {GONE_SECONDS:g} s after its bootout "
                         f"({_words(off)}); no bootstrap was attempted. The new plist is at "
                         f"{job.plist}")
        did.append(f"booted out {job.target}")
    on = _launchctl(env, "bootstrap", job.domain, str(job.plist))
    if on.returncode != 0:
        raise Failed(f"launchctl bootstrap {job.domain} {job.plist} failed ({_words(on)}); "
                     f"the plist is left in place at {job.plist}")
    if not loaded(job, env):
        raise Failed(f"launchctl bootstrap {job.domain} {job.plist} returned 0, but "
                     f"{job.target} is not loaded; the plist is left in place")
    did.append(f"bootstrapped {job.target}")
    return ", ".join(did)
