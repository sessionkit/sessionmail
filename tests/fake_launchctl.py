"""A fake launchctl: launchd simulated, with no launchd.

`tests/conftest.py` points `SESSIONMAIL_LAUNCHCTL` at one of these for EVERY
test, so a test that forgets the stub still never reaches the real
/bin/launchctl. A test that builds its own environment from nothing passes
`launchctl_fake.env` (or the variable) itself; `tests/test_seat_install.py`
holds a meta-test that greps for it.

It keeps its state in a directory beside itself:

- `calls`             one line per run, the argv joined by a unit separator.
- `jobs/<label>.json` a loaded job: its plist path and the pid it started.
- knobs, as empty files: `fail-bootstrap` (rc 5, as launchd's own I/O
  error), `no-start` (load, but start nothing), `fail-bootout` (rc 5).

The verbs it knows, as `message seat agent` uses them:

- `print gui/<uid>/<label>`: rc 0 when loaded, else 113 (launchctl's own
  "Could not find service").
- `bootstrap gui/<uid> PLIST`: parses PLIST with plistlib and starts its
  ProgramArguments in the background, in a new session, with its
  EnvironmentVariables over HOME, stdout and stderr to its log paths. rc 5
  when the label is already loaded.
- `bootout gui/<uid>/<label>`: TERMs the job's pid, by pid, waits for it to
  go, and forgets the job. rc 113 when nothing is loaded.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import time
from pathlib import Path


FAKE_LAUNCHCTL = r'''#!{python}
import json, os, plistlib, signal, subprocess, sys, time
state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-launchctl.d")
jobs = os.path.join(state, "jobs")
os.makedirs(jobs, exist_ok=True)
with open(os.path.join(state, "calls"), "a") as log:
    log.write("\x1f".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
def knob(name):
    return os.path.exists(os.path.join(state, name))
def job_file(label):
    return os.path.join(jobs, label + ".json")
def label_of(target):
    return target.rsplit("/", 1)[-1]
if not args:
    sys.exit(64)
verb = args[0]
if verb == "print" and len(args) == 2:
    if os.path.exists(job_file(label_of(args[1]))):
        print(args[1] + " = { state = running }")
        sys.exit(0)
    sys.stderr.write("Could not find service \"%s\" in domain for user\n" % label_of(args[1]))
    sys.exit(113)
if verb == "bootstrap" and len(args) == 3:
    with open(args[2], "rb") as f:
        plist = plistlib.load(f)
    label = plist["Label"]
    if knob("fail-bootstrap") or os.path.exists(job_file(label)):
        sys.stderr.write("Bootstrap failed: 5: Input/output error\n")
        sys.exit(5)
    pid = None
    if not knob("no-start"):
        env = {"HOME": os.environ.get("HOME", "/")}
        env.update(plist.get("EnvironmentVariables", {}))
        out = open(plist.get("StandardOutPath", os.devnull), "a")
        err = open(plist.get("StandardErrorPath", os.devnull), "a")
        proc = subprocess.Popen(plist["ProgramArguments"], env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, start_new_session=True, cwd="/")
        pid = proc.pid
    with open(job_file(label), "w") as f:
        json.dump({"plist": args[2], "pid": pid}, f)
    sys.exit(0)
if verb == "bootout" and len(args) == 2:
    path = job_file(label_of(args[1]))
    if not os.path.exists(path):
        sys.stderr.write("Boot-out failed: 113: Could not find service\n")
        sys.exit(113)
    if knob("fail-bootout"):
        sys.stderr.write("Boot-out failed: 5: Input/output error\n")
        sys.exit(5)
    with open(path) as f:
        pid = json.load(f)["pid"]
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
    os.unlink(path)
    sys.exit(0)
sys.stderr.write("fake launchctl: unknown use: %s\n" % " ".join(args))
sys.exit(64)
'''


class FakeLaunchctl:
    """The suite's fake, as a test sees it."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        # Not fakes.write_fake: that one runs str.format, and this source has
        # braces of its own. The interpreter is absolute for the same reason.
        self.path = directory / "launchctl"
        self.path.write_text(FAKE_LAUNCHCTL.replace("{python}", sys.executable, 1))
        self.path.chmod(self.path.stat().st_mode | stat.S_IEXEC)
        self.state = directory / "fake-launchctl.d"
        self.state.mkdir(exist_ok=True)

    @property
    def env(self) -> dict[str, str]:
        """What a test that builds its environment from nothing adds to it."""
        return {"SESSIONMAIL_LAUNCHCTL": str(self.path)}

    def calls(self) -> list[list[str]]:
        log = self.state / "calls"
        if not log.exists():
            return []
        return [line.split("\x1f") for line in log.read_text().splitlines()]

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls()]

    def knob(self, name: str, on: bool = True) -> None:
        path = self.state / name
        if on:
            path.write_text("")
        elif path.exists():
            path.unlink()

    def jobs(self) -> dict[str, dict]:
        d = self.state / "jobs"
        if not d.is_dir():
            return {}
        return {p.stem: json.loads(p.read_text()) for p in d.glob("*.json")}

    def stop_all(self) -> None:
        """Teardown: TERM, then KILL, every pid a bootstrap started, by pid."""
        for job in self.jobs().values():
            pid = job.get("pid")
            if not pid:
                continue
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
                for _ in range(40):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    continue
                break
