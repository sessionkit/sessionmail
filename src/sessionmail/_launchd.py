"""The seat agent's LaunchAgent: render it, load it, unload it.

`message seat agent --install ADDRESS [SOCKET]` and `--uninstall ADDRESS`.
A seat with no Claude session (a bot) signs through its own ssh-agent, and
that agent has to come back after a reboot: launchd keeps it alive. This
module renders the packaged template (`seat/seat-agent.plist.template`),
writes it into `~/Library/LaunchAgents`, loads it with `launchctl
bootstrap`, and checks the effect: the agent answers on its socket and
holds seat.pub's key. `--uninstall` boots the job out and removes the plist.
It never touches the key, the registry or the cert.

Streams and exits follow `seat init`'s convention:

- stdout: the success report only (`--install`'s KEY=VALUE lines).
- stderr: narration, refusals (`REFUSED - ... Nothing was written.`) and
  failures (`FAILED - ...`, naming what was put back).
- 0 done · 1 launchctl failed, a plist write or removal failed, or the
  agent never answered, rolled back · 2 usage or refused, nothing written.

`launchctl` is `$SESSIONMAIL_LAUNCHCTL` when set and not empty, else
`/bin/launchctl`: the same seam as `SESSIONMAIL_SENDMAIL`. The test suite
points it at a fake for every test, so no test reaches the real launchd.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import IO, Mapping
from xml.sax.saxutils import escape

PROG = "message seat agent"

EX_OK = 0
EX_FAILED = 1
EX_REFUSED = 2

#: The seam: a launchctl of the caller's choosing (a test's fake).
LAUNCHCTL_VAR = "SESSIONMAIL_LAUNCHCTL"
DEFAULT_LAUNCHCTL = "/bin/launchctl"

#: The kit's launchd family for mail, as the template's Label has it.
LABEL_PREFIX = "dev.sessionkit.mail.seat-agent."
#: The wrapper's own rules (seat/seat-agent): the address, and the socket
#: cap. macOS's sun_path is 104 bytes with its NUL. The address is seat
#: init's grammar (seat_init.ADDRESS): a name, or ONE '+' between two names
#: (heard+airpods). A '+' is used verbatim, never mapped: in the Label
#: (launchd.plist(5): any string; launchctl reads `gui/<uid>/<label>` with
#: '/' as its only separator, and no address holds a '/'), the plist and log
#: file names, and the default socket path under seat-keys/<address>/.
#: `\Z`, not `$`: under re.match a `$` also matches before a trailing newline,
#: so 'heard+airpods\n' would pass (seat_init.ADDRESS ends in `\Z` too).
ADDRESS_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:\+[a-z0-9][a-z0-9._-]*)?\Z")
SOCK_MAX = 103
PLACEHOLDER = re.compile(r"@([A-Z_]+)@")

#: How long the agent has to answer after a load, and the job to go after
#: a bootout. Module values, so a test can shorten them in-process.
ANSWER_SECONDS = 10.0
GONE_SECONDS = 5.0
POLL_SECONDS = 0.1
#: How many lines of the job's log a failure quotes.
LOG_TAIL = 8

#: The packaged template, beside this module's real path.
TEMPLATE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "seat",
                        "seat-agent.plist.template")


class Refused(Exception):
    """Exit 2: usage or refused, nothing written."""


class Failed(Exception):
    """Exit 1: a tool failed; the message names what was put back."""


@dataclass(frozen=True)
class Job:
    address: str
    label: str
    plist: str          # ~/Library/LaunchAgents/<label>.plist
    socket: str
    pub: str
    key: str
    log: str            # <log_dir>/seat-agent.<address>.log
    log_dir: str
    sessionmail_home: str
    message: str        # `command -v message`, not resolved
    domain: str         # gui/<uid>

    @property
    def target(self) -> str:
        return f"{self.domain}/{self.label}"


def label_for(address: str) -> str:
    return LABEL_PREFIX + address


def launchctl_path(env: Mapping[str, str]) -> str:
    return env.get(LAUNCHCTL_VAR) or DEFAULT_LAUNCHCTL


def launchctl(env: Mapping[str, str], *args: str) -> subprocess.CompletedProcess:
    """Run launchctl ARGS (the seam's, else /bin/launchctl), output captured.
    A launchctl that cannot be started is a Failed, before anything moves."""
    path = launchctl_path(env)
    try:
        return subprocess.run([path, *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=dict(env), check=False)
    except OSError as exc:
        raise Failed(f"launchctl could not run ({path}: {exc.strerror or exc})") from exc


def _words(done: subprocess.CompletedProcess) -> str:
    said = " ".join((done.stderr or done.stdout or "").split())
    return f"rc {done.returncode}" + (f": {said}" if said else "")


def _sessionmail_home(env: Mapping[str, str], home: str) -> str:
    """As the wrapper reads it: $SESSIONMAIL_HOME, a leading ~ expanded, else
    $HOME/.sessionmail. Absolute, as the template wants it."""
    value = env.get("SESSIONMAIL_HOME") or os.path.join(home, ".sessionmail")
    if value == "~" or value.startswith("~/"):
        value = home + value[1:]
    return os.path.abspath(value)


def _check_platform() -> None:
    if sys.platform != "darwin":
        raise Refused(f"launchd only: this is {sys.platform}, not macOS; run "
                      f"`{PROG} ADDRESS [SOCKET]` under your own service manager")


def _check_address(address: str) -> None:
    if not ADDRESS_RE.match(address):
        raise Refused(f"'{address}' is not a seat address")


def _home(env: Mapping[str, str]) -> str:
    home = env.get("HOME", "")
    if not os.path.isabs(home):
        raise Refused("HOME is unset or not absolute: the LaunchAgent lives under "
                      "$HOME/Library/LaunchAgents")
    return home


def plan(address: str, socket: str | None, env: Mapping[str, str]) -> Job:
    """The job for ADDRESS, every install check made; nothing is written."""
    _check_platform()
    _check_address(address)
    home = _home(env)
    sm_home = _sessionmail_home(env, home)
    key_home = os.path.join(sm_home, "seat-keys", address)
    key = os.path.join(key_home, "id_ed25519")
    pub = os.path.join(key_home, "seat.pub")
    if not os.path.isdir(key_home):
        raise Refused(f"no key home {key_home}: mint the seat first "
                      f"(message seat init --registry DIR {address})")
    for path in (key, pub):
        if not os.path.isfile(path):
            raise Refused(f"no {os.path.basename(path)} at {path}: mint the seat first "
                          f"(message seat init --registry DIR {address})")
    if socket is None:
        socket = os.path.join(key_home, "agent.sock")
    elif not os.path.isabs(socket):
        raise Refused(f"SOCKET {socket!r} is not an absolute path: launchd starts the "
                      "job in /, so name it in full")
    size = len(socket.encode())
    if size > SOCK_MAX:
        raise Refused(f"socket path is {size} bytes; macOS binds {SOCK_MAX} at most "
                      "(104 with its NUL). Name a shorter SOCKET")
    if not os.path.isdir(os.path.dirname(socket)):
        raise Refused(f"no directory {os.path.dirname(socket)} for the socket")
    message = shutil.which("message", path=env["PATH"]) if env.get("PATH") else None
    if message is None:
        raise Refused("`message` is not on PATH: launchd runs `message seat agent`, "
                      "by the path `command -v message` prints")
    label = label_for(address)
    log_dir = os.path.join(home, "Library", "Logs")
    return Job(address=address, label=label,
               plist=os.path.join(home, "Library", "LaunchAgents", f"{label}.plist"),
               socket=socket, pub=pub, key=key,
               log=os.path.join(log_dir, f"seat-agent.{address}.log"), log_dir=log_dir,
               sessionmail_home=sm_home, message=os.path.abspath(message),
               domain=f"gui/{os.getuid()}")


def render(template: bytes, job: Job) -> bytes:
    """The template with its five placeholders filled, each value XML-escaped.
    One pass, so a value is never itself read for a placeholder. An unknown
    placeholder, a render plistlib cannot read, or a Label that is not the
    job's is refused."""
    values = {"ADDRESS": job.address, "MESSAGE": job.message,
              "SESSIONMAIL_HOME": job.sessionmail_home, "SOCKET": job.socket,
              "LOG_DIR": job.log_dir}
    text = template.decode("utf-8")
    unknown = sorted(set(PLACEHOLDER.findall(text)) - set(values))
    if unknown:
        raise Refused("the packaged template holds a placeholder this sessionmail does not "
                      f"fill ({', '.join('@' + u + '@' for u in unknown)}): a broken install")
    out = PLACEHOLDER.sub(lambda m: escape(values[m.group(1)]), text).encode("utf-8")
    try:
        parsed = plistlib.loads(out)
    except Exception as exc:  # plistlib raises several kinds on a bad document
        raise Refused(f"the rendered LaunchAgent is not a valid plist ({exc})") from exc
    if parsed.get("Label") != job.label:
        raise Refused(f"the rendered Label is {parsed.get('Label')!r}, not {job.label!r}")
    return out


def loaded(job_target: str, env: Mapping[str, str]) -> bool:
    """`launchctl print TARGET`: rc 0 means the job is loaded."""
    return launchctl(env, "print", job_target).returncode == 0


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path: str, data: bytes) -> None:
    """Write DATA at PATH, 0644, through a temp file and os.replace."""
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".seat-agent.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def answers(job: Job, env: Mapping[str, str]) -> bool:
    """The agent on the socket lists seat.pub's key. A clean env: only this
    socket, never the caller's SSH_AUTH_SOCK."""
    ssh_add = shutil.which("ssh-add", path=env.get("PATH", "")) or "/usr/bin/ssh-add"
    try:
        with open(job.pub) as f:
            blob = f.read().split()[1]
    except (OSError, IndexError):
        return False
    clean = {"PATH": "/usr/bin:/bin", "HOME": env.get("HOME", "/"), "SSH_AUTH_SOCK": job.socket}
    try:
        done = subprocess.run([ssh_add, "-L"], capture_output=True, text=True, env=clean,
                              stdin=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and blob in done.stdout


def _wait(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def _log_tail(job: Job) -> str:
    try:
        with open(job.log, errors="replace") as f:
            lines = f.read().splitlines()[-LOG_TAIL:]
    except OSError:
        return f"(no log at {job.log})"
    return f"the last lines of {job.log}:\n" + "\n".join("  " + line for line in lines)


def report(job: Job) -> str:
    """The stdout contract: KEY=VALUE, shell-quoted, for `eval`."""
    pairs = (("SEAT_LABEL", job.label), ("SEAT_PLIST", job.plist), ("SEAT_SOCKET", job.socket),
             ("SEAT_PUB", job.pub), ("SEAT_LOG", job.log))
    return "".join(f"{k}={shlex.quote(v)}\n" for k, v in pairs)


def install(address: str, socket: str | None = None, *, env: Mapping[str, str] | None = None,
            out: IO[str] | None = None, err: IO[str] | None = None) -> int:
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        return _install(address, socket, env, out, err)
    except Refused as exc:
        err.write(f"{PROG}: REFUSED - {exc}. Nothing was written.\n")
        return EX_REFUSED
    except Failed as exc:
        err.write(f"{PROG}: FAILED - {exc}\n")
        return EX_FAILED


def _install(address: str, socket: str | None, env: Mapping[str, str],
             out: IO[str], err: IO[str]) -> int:
    job = plan(address, socket, env)
    with open(TEMPLATE, "rb") as f:
        rendered = render(f.read(), job)
    existing = _read(job.plist)
    is_loaded = loaded(job.target, env)
    if is_loaded and existing == rendered:
        if not _wait(lambda: answers(job, env), ANSWER_SECONDS):
            raise Failed(f"{job.label} is loaded but its agent does not answer on {job.socket} "
                         f"with seat.pub's key; the job was left as found (this run did not "
                         f"load it). {_log_tail(job)}")
        err.write(f"{PROG}: {job.label} already installed; the agent answers on {job.socket}\n")
        out.write(report(job))
        return EX_OK
    if is_loaded:
        why = "with a different plist" if existing is not None else f"with no plist at {job.plist}"
        raise Refused(f"a job already holds {job.label} ({why}): run "
                      f"`{PROG} --uninstall {address}` first")
    if existing is not None and existing != rendered:
        raise Refused(f"a different plist is at {job.plist}: run "
                      f"`{PROG} --uninstall {address}` first; a job's plist is never overwritten")

    wrote = existing is None
    # An OSError here is a FAILED line, never a traceback. _write leaves no temp
    # file and no plist behind, and nothing has been loaded yet. A plist that was
    # there before this run is never touched, so the line says it is kept.
    path = job.log_dir
    try:
        os.makedirs(job.log_dir, exist_ok=True)
        path = job.plist
        if wrote:
            _write(job.plist, rendered)
    except OSError as exc:
        kept = ("no plist was left" if wrote
                else f"the plist at {job.plist} was there before this run and is kept")
        raise Failed(f"could not write {path} ({exc.strerror or exc}); nothing was "
                     f"loaded and {kept}") from exc
    done = launchctl(env, "bootstrap", job.domain, job.plist)
    if done.returncode != 0:
        put_back = _remove_ours(job, wrote)
        raise Failed(f"launchctl bootstrap {job.domain} {job.plist} failed ({_words(done)}); "
                     f"{put_back}")
    if not _wait(lambda: answers(job, env), ANSWER_SECONDS):
        off = launchctl(env, "bootout", job.target)
        booted = ("booted it out" if off.returncode == 0
                  else f"its bootout failed too ({_words(off)})")
        put_back = _remove_ours(job, wrote)
        raise Failed(f"loaded {job.label}, but its agent did not answer on {job.socket} with "
                     f"seat.pub's key within {ANSWER_SECONDS:g} s; {booted}; {put_back}. "
                     f"{_log_tail(job)}")
    err.write(f"{PROG}: installed and loaded {job.label}; the agent answers on {job.socket}\n")
    out.write(report(job))
    return EX_OK


def _remove_ours(job: Job, wrote: bool) -> str:
    if not wrote:
        return f"the plist at {job.plist} was there before this run and is kept"
    try:
        os.unlink(job.plist)
    except FileNotFoundError:
        pass
    except OSError as exc:
        return f"the plist this run wrote could NOT be removed ({job.plist}: {exc.strerror})"
    return f"the plist this run wrote is removed ({job.plist})"


def uninstall(address: str, *, env: Mapping[str, str] | None = None,
              err: IO[str] | None = None) -> int:
    env = os.environ if env is None else env
    err = sys.stderr if err is None else err
    try:
        return _uninstall(address, env, err)
    except Refused as exc:
        err.write(f"{PROG}: REFUSED - {exc}. Nothing was changed.\n")
        return EX_REFUSED
    except Failed as exc:
        err.write(f"{PROG}: FAILED - {exc}\n")
        return EX_FAILED


def _uninstall(address: str, env: Mapping[str, str], err: IO[str]) -> int:
    _check_platform()
    _check_address(address)
    home = _home(env)
    label = label_for(address)
    plist = os.path.join(home, "Library", "LaunchAgents", f"{label}.plist")
    target = f"gui/{os.getuid()}/{label}"
    existing = _read(plist)
    if existing is not None:
        try:
            theirs = plistlib.loads(existing).get("Label")
        except Exception:
            theirs = None
        if theirs != label:
            raise Refused(f"the plist at {plist} has Label {theirs!r}, not {label!r}: "
                          "not this tool's to remove")
    said = []
    if loaded(target, env):
        off = launchctl(env, "bootout", target)
        if off.returncode != 0:
            raise Failed(f"launchctl bootout {target} failed ({_words(off)}); "
                         f"the plist is kept" + (f" at {plist}" if existing is not None else ""))
        if not _wait(lambda: not loaded(target, env), GONE_SECONDS):
            raise Failed(f"{label} is still loaded {GONE_SECONDS:g} s after its bootout; "
                         "the plist is kept" + (f" at {plist}" if existing is not None else ""))
        said.append(f"booted out {label}")
    if existing is not None:
        try:
            os.unlink(plist)
        except FileNotFoundError:
            pass
        except OSError as exc:
            done = f"{'; '.join(said)}; but " if said else ""
            raise Failed(f"{done}could not remove {plist} ({exc.strerror or exc}); "
                         "the plist is kept") from exc
        said.append(f"removed {plist}")
    if not said:
        err.write(f"{PROG}: nothing installed for {address}\n")
    else:
        err.write(f"{PROG}: {'; '.join(said)}\n")
    return EX_OK
