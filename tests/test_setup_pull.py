"""`message setup --pull`: the `pull-agent` step installs the puller's LaunchAgent.

design.md D3 and D5 (the setup half). Everything here runs on a scratch
HOME, with the suite's fake launchctl (`launchctl_fake`, through
`$SESSIONMAIL_LAUNCHCTL`) and a fake plutil (`$SESSIONMAIL_PLUTIL`). The
fake launchctl starts a bootstrapped job's ProgramArguments unless its
`no-start` knob is set: every test here sets it, so no puller ever runs.
Nothing reaches the real launchd, a real `~/Library/LaunchAgents`, IMAP or
SMTP. The platform, the uid and the listener check are module seams in
`_pull_agent`, patched, so the file runs off macOS too.

The pull config lives in the sandboxed settings home's `mail.json`, never
in variables: setup refuses a key that only its own shell can see, by
design. Fixtures are `alpha` and `beta` at `example.org`.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import socket
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from conftest import write_mail_settings
from sessionmail import _launchd, _operator, _pull_agent, _puller, _settings, _setup
from sessionmail.message import cli as message_cli

LABEL = "dev.sessionkit.mail.puller"
UID = 4242

FAKE_PLUTIL = """#!{python}
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "plutil.calls"), "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
if os.path.exists(os.path.join(here, "plutil.fail")):
    sys.stderr.write(sys.argv[-1] + ": invalid\\n")
    sys.exit(1)
print(sys.argv[-1] + ": OK")
"""


class Host:
    """A scratch host: its HOME, settings file, fakes and env."""

    def __init__(self, tmp: Path, fake):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.root = tmp / "mailroot"
        self.launchctl = fake
        fake.knob("no-start")                    # never start a real puller
        tools = tmp / "tools"
        tools.mkdir()
        self.plutil = tools / "plutil"
        self.plutil.write_text(FAKE_PLUTIL.replace("{python}", sys.executable, 1))
        self.plutil.chmod(self.plutil.stat().st_mode | stat.S_IEXEC)
        self.boxes = tmp / "pull.boxes"
        self.boxes.write_text("alpha\nbeta\n")
        self.pull = {
            "imap_host": "imap.example.org",
            "login": "{box}*puller",
            "secret_command": "cat /nonexistent/secret",
            "relay_domain": "relay.example.org",
            "listener": "127.0.0.1:2526",
            "vmailbox": str(tmp / "vmailbox"),
            "boxes_file": str(self.boxes),
        }
        self.extra: dict = {}
        self.env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(tmp),
            **fake.env,
            _pull_agent.PLUTIL_VAR: str(self.plutil),
        }
        self.write()

    def write(self) -> None:
        body = {"root": str(self.root), "host": "testhost", "pull": self.pull, **self.extra}
        write_mail_settings(_operator.settings_home(), json.dumps(body) + "\n")

    @property
    def plist(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def plutil_calls(self) -> list[list[str]]:
        log = self.plutil.parent / "plutil.calls"
        if not log.exists():
            return []
        return [line.split("\x1f") for line in log.read_text().splitlines()]

    def load(self, plist: Path | None = None) -> None:
        """Mark LABEL loaded in the fake launchd, as if a job were running."""
        jobs = self.launchctl.state / "jobs"
        jobs.mkdir(exist_ok=True)
        (jobs / f"{LABEL}.json").write_text(json.dumps({"plist": str(plist or ""),
                                                        "pid": None}))

    def forget_calls(self) -> None:
        for log in (self.launchctl.state / "calls", self.plutil.parent / "plutil.calls"):
            if log.exists():
                log.unlink()


@pytest.fixture
def host(tmp_path, launchctl_fake, monkeypatch) -> Host:
    monkeypatch.setattr(_pull_agent, "on_macos", lambda: True)
    monkeypatch.setattr(_pull_agent, "uid", lambda: UID)
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: True)
    monkeypatch.setattr(_pull_agent, "GONE_SECONDS", 0.3)
    monkeypatch.setattr(_pull_agent, "POLL_SECONDS", 0.02)
    h = Host(tmp_path, launchctl_fake)
    yield h
    assert all(job.get("pid") is None for job in launchctl_fake.jobs().values()), \
        "the fake started a puller: the no-start knob was lost"


def setup(env, *argv):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(list(argv), env, out=out, err=err, tty=False)
    return rc, out.getvalue(), err.getvalue()


def step(out: str, name: str = "pull-agent") -> tuple[str, str]:
    """NAME's (state, detail) on the plan's `steps:` lines."""
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split(None, 2)
        if line.startswith("  ") and parts and parts[0] == name:
            return parts[1], (parts[2] if len(parts) > 2 else "")
    return "", ""


def applied(out: str, name: str = "pull-agent") -> str:
    """NAME's line under `applying:` ("" when it was not applied)."""
    if "\napplying:\n" not in out:
        return ""
    for line in out.split("\napplying:\n", 1)[1].splitlines():
        parts = line.split(None, 1)
        if line.startswith("  ") and parts and parts[0] == name:
            return parts[1] if len(parts) > 1 else ""
    return ""


# -- the guard: never the real tools -------------------------------------------

def test_the_real_launchctl_and_plutil_are_never_the_resolved_paths(host):
    assert _launchd.launchctl_path(host.env) == str(host.launchctl.path)
    assert _launchd.launchctl_path(host.env) != _launchd.DEFAULT_LAUNCHCTL
    assert _launchd.launchctl_path(host.env) != "/bin/launchctl"
    assert _pull_agent.plutil_path(host.env) == str(host.plutil)
    assert _pull_agent.plutil_path(host.env) != _pull_agent.DEFAULT_PLUTIL
    assert str(host.plist).startswith(str(host.tmp))
    # And the in-process fallback fails closed (the conftest's pin).
    assert _launchd.launchctl_path({}) != "/bin/launchctl"


# -- SC1: refusal, naming every missing key ------------------------------------

def test_dry_run_with_no_pull_config_refuses_naming_every_missing_key(host):
    host.pull = {}
    host.write()
    rc, out, err = setup(host.env, "--pull", "--dry-run")
    assert rc == _setup.EX_USAGE == 2
    assert out == ""
    for key in _puller.REQUIRED:
        assert f"{key} ({_settings.KEYS[key].var})" in err, err
    assert "REFUSED - pull is not configured" in err
    assert "Nothing was applied" in err
    assert host.launchctl.calls() == []
    assert not (host.home / "Library").exists()


def test_a_key_set_only_in_this_shell_is_refused(host):
    del host.pull["imap_host"]
    host.write()
    env = {**host.env, "SESSIONMAIL_PULL_IMAP_HOST": "imap.example.org"}
    rc, out, err = setup(env, "--pull", "--dry-run")
    assert rc == 2, err
    assert "pull.imap_host (SESSIONMAIL_PULL_IMAP_HOST) is set only in the environment" in err
    assert _settings.SHOWN in err
    assert host.launchctl.calls() == []


def test_a_variable_that_moves_a_value_is_refused_naming_both_values(host):
    env = {**host.env, "SESSIONMAIL_PULL_LISTENER": "127.0.0.1:2599"}
    rc, _out, err = setup(env, "--pull", "--dry-run")
    assert rc == 2
    assert "pull.listener (SESSIONMAIL_PULL_LISTENER)" in err
    assert ("the LaunchAgent would use 127.0.0.1:2526, this shell 127.0.0.1:2599: unset "
            "SESSIONMAIL_PULL_LISTENER or write pull.listener into") in err, err


@pytest.mark.parametrize("var", ["SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT"])
def test_a_root_variable_that_differs_from_the_file_is_refused(host, tmp_path, var):
    """The suite's conftest sets SESSION_MAIL_ROOT for every test; this env is
    built from nothing, so it is set here on purpose (the legacy spelling
    too: refused, never silently ignored)."""
    env = {**host.env, var: str(tmp_path / "elsewhere" / "agents")}
    rc, _out, err = setup(env, "--pull", "--dry-run")
    assert rc == 2, err
    assert "root (SESSIONMAIL_ROOT or SESSION_MAIL_ROOT or MAIL_ROOT)" in err
    assert str(tmp_path / "elsewhere") in err and str(host.root) in err


def test_a_root_variable_naming_the_same_place_is_not_refused(host):
    """Values compare as resolved: the variable names the agents root, the
    file the mail root, and they are the same place."""
    agents = str(host.root / "agents")
    env = {**host.env, "SESSIONMAIL_ROOT": agents, "SESSION_MAIL_ROOT": agents}
    rc, out, err = setup(env, "--pull", "--dry-run")
    assert "REFUSED" not in err, err
    assert step(out)[0] == _setup.TODO


def test_no_boxes_and_no_unsorted_box_is_refused(host):
    host.boxes.write_text("# nothing yet\n")
    rc, _out, err = setup(host.env, "--pull", "--dry-run")
    assert rc == 2
    assert "nothing to pull" in err and "pull.unsorted_box" in err


def test_general_delivery_alone_is_enough(host):
    host.boxes.unlink()
    host.pull["unsorted_box"] = "unsorted"
    host.write()
    rc, out, err = setup(host.env, "--pull", "--dry-run")
    assert "REFUSED" not in err, err
    assert step(out)[0] == _setup.TODO


# -- SC2: install, then idempotent ---------------------------------------------

def test_dry_run_with_a_full_config_writes_nothing(host):
    rc, out, err = setup(host.env, "--pull", "--dry-run")
    assert err == ""
    state, detail = step(out)
    assert state == _setup.TODO
    assert f"write {host.plist}" in detail
    assert not host.plist.exists()
    assert "bootstrap" not in host.launchctl.verbs()
    assert host.plutil_calls() == []


def test_first_run_writes_lints_and_bootstraps_then_the_second_is_done(host):
    rc, out, err = setup(host.env, "--pull")
    assert err == "", err
    assert step(out)[0] == _setup.TODO
    assert applied(out).startswith(f"wrote {host.plist}"), out
    assert "bootstrapped" in applied(out)
    # The plist: the rendered bytes, under the scratch HOME.
    job = _pull_agent.resolve(host.env)
    assert host.plist.read_bytes() == job.body
    assert stat.S_IMODE(host.plist.stat().st_mode) == 0o644
    # Linted before it was put in place: the lint ran on a temp file beside it.
    lints = host.plutil_calls()
    assert len(lints) == 1 and lints[0][0] == "-lint"
    linted = Path(lints[0][1])
    assert linted.parent == host.plist.parent and linted != host.plist
    assert not linted.exists(), "the temp file was renamed into place"
    # Not loaded before, so no bootout; then bootstrap into gui/<uid>.
    calls = host.launchctl.calls()
    assert ["bootout", f"gui/{UID}/{LABEL}"] not in calls
    assert ["bootstrap", f"gui/{UID}", str(host.plist)] in calls
    assert LABEL in host.launchctl.jobs()

    host.forget_calls()
    rc2, out2, err2 = setup(host.env, "--pull")
    assert err2 == ""
    assert step(out2)[0] == _setup.DONE, out2
    assert applied(out2) == ""
    assert set(host.launchctl.verbs()) == {"print"}, host.launchctl.calls()
    assert host.plutil_calls() == []


def test_the_plist_runs_the_module_and_carries_only_path_and_home(host):
    setup(host.env, "--pull")
    got = plistlib.loads(host.plist.read_bytes())
    assert got["Label"] == LABEL
    assert got["ProgramArguments"] == [sys.executable, "-u", "-m", "sessionmail._puller"]
    assert got["EnvironmentVariables"] == {"HOME": str(host.home),
                                           "PATH": _pull_agent.JOB_PATH}
    assert got["RunAtLoad"] is True and got["KeepAlive"] is True
    assert got["ThrottleInterval"] == 30
    text = host.plist.read_text()
    for value in ("imap.example.org", "relay.example.org", "cat /nonexistent/secret"):
        assert value not in text, "no config value and no secret goes into the plist"


def test_the_log_path_is_the_job_environments_state_dir(host, tmp_path):
    """The default state dir is under the settings home, whatever
    SESSIONMAIL_HOME this shell sets; a set pull.state_dir moves it."""
    default = _operator.settings_home() / ".sessionmail" / "puller" / "log" / "puller.log"
    env = {**host.env, "SESSIONMAIL_HOME": str(tmp_path / "another-state-home")}
    rc, out, err = setup(env, "--pull")
    assert err == "", err
    got = plistlib.loads(host.plist.read_bytes())
    assert got["StandardOutPath"] == got["StandardErrorPath"] == str(default)
    assert default.parent.is_dir(), "apply makes <state_dir>/log"

    host.pull["state_dir"] = str(tmp_path / "state")
    host.write()
    setup(host.env, "--pull")
    got = plistlib.loads(host.plist.read_bytes())
    assert got["StandardOutPath"] == str(tmp_path / "state" / "log" / "puller.log")


def test_a_changed_config_re_renders_boots_out_then_bootstraps(host, tmp_path):
    setup(host.env, "--pull")
    before = host.plist.read_bytes()
    host.pull["state_dir"] = str(tmp_path / "moved")
    host.write()
    host.forget_calls()
    rc, out, err = setup(host.env, "--pull")
    assert err == "", err
    state, detail = step(out)
    assert state == _setup.TODO and "replace" in detail and "boot out" in detail
    assert host.plist.read_bytes() != before
    verbs = [c[0] for c in host.launchctl.calls() if c[0] != "print"]
    assert verbs == ["bootout", "bootstrap"], host.launchctl.calls()
    assert "booted out" in applied(out)


def test_a_current_plist_that_is_not_loaded_is_bootstrapped(host):
    setup(host.env, "--pull")
    (host.launchctl.state / "jobs" / f"{LABEL}.json").unlink()
    host.forget_calls()
    rc, out, _err = setup(host.env, "--pull")
    state, detail = step(out)
    assert state == _setup.TODO and "is current" in detail
    assert "bootstrap" in host.launchctl.verbs()
    assert "bootout" not in host.launchctl.verbs()


def test_the_launch_agents_dir_is_made_when_absent(host):
    assert not (host.home / "Library").exists()
    setup(host.env, "--pull")
    assert host.plist.is_file()


# -- failures: nothing half-installed -------------------------------------------

def test_a_lint_failure_leaves_nothing_installed(host):
    (host.plutil.parent / "plutil.fail").write_text("")
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_FAILED
    assert "FAILED: plutil -lint refused the rendered plist" in out
    assert f"{_setup.COMMAND} {_setup.PULL}" in out
    assert not host.plist.exists()
    assert list(host.plist.parent.iterdir()) == [], "the temp file is removed"
    assert "bootstrap" not in host.launchctl.verbs()


def test_a_plutil_that_cannot_run_is_failed(host, tmp_path):
    env = {**host.env, _pull_agent.PLUTIL_VAR: str(tmp_path / "no-plutil-here")}
    rc, out, _err = setup(env, "--pull")
    assert rc == 1
    assert "plutil could not run" in out
    assert not host.plist.exists()


def test_a_bootout_that_never_clears_is_failed_with_no_bootstrap(host):
    setup(host.env, "--pull")
    host.pull["state_dir"] = str(host.tmp / "moved")
    host.write()
    host.launchctl.knob("fail-bootout")
    host.forget_calls()
    rc, out, _err = setup(host.env, "--pull")
    assert rc == 1
    assert "still loaded" in out and "no bootstrap was attempted" in out
    assert "bootout" in host.launchctl.verbs()
    assert "bootstrap" not in host.launchctl.verbs()


def test_a_bootstrap_failure_is_failed_and_names_the_plist_left(host):
    host.launchctl.knob("fail-bootstrap")
    rc, out, _err = setup(host.env, "--pull")
    assert rc == 1
    assert f"the plist is left in place at {host.plist}" in out
    assert f"Re-run {_setup.COMMAND} {_setup.PULL}" in out
    assert host.plist.is_file()


# -- never another's job ---------------------------------------------------------

def _foreign_plist(path: Path) -> bytes:
    data = plistlib.dumps({"Label": LABEL, "ProgramArguments": [
        "/usr/bin/python3", "-u", "/opt/elsewhere/puller.py"]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def test_a_foreign_plist_is_blocked_byte_identical_and_never_booted_out(host):
    data = _foreign_plist(host.plist)
    host.load(host.plist)
    rc, out, _err = setup(host.env, "--pull")
    state, detail = step(out)
    assert state == _setup.BLOCKED, out
    assert "not a sessionmail puller" in detail and "pick another pull.label" in detail
    assert rc == _setup.EX_REMAINS
    assert host.plist.read_bytes() == data
    assert "bootout" not in host.launchctl.verbs()
    assert "bootstrap" not in host.launchctl.verbs()
    assert host.plutil_calls() == []


def test_an_unparseable_plist_is_not_ours(host):
    host.plist.parent.mkdir(parents=True)
    host.plist.write_bytes(b"not a plist at all")
    rc, out, _err = setup(host.env, "--pull")
    assert step(out)[0] == _setup.BLOCKED
    assert host.plist.read_bytes() == b"not a plist at all"


def test_a_job_loaded_with_no_plist_at_the_target_is_blocked(host):
    host.load()
    rc, out, _err = setup(host.env, "--pull")
    state, detail = step(out)
    assert state == _setup.BLOCKED
    assert f"a job is loaded as gui/{UID}/{LABEL} with no plist" in detail
    assert not host.plist.exists()
    assert "bootout" not in host.launchctl.verbs()


def test_our_plist_from_another_interpreter_is_ours():
    other = plistlib.dumps({"ProgramArguments": ["/elsewhere/python", "-u", "-m",
                                                 "sessionmail._puller"]})
    assert _pull_agent.ours(other)
    assert not _pull_agent.ours(plistlib.dumps({"ProgramArguments": ["x", "-m",
                                                                     "sessionmail._puller"]}))
    assert not _pull_agent.ours(plistlib.dumps(["not", "a", "dict"]))
    assert not _pull_agent.ours(b"\x00garbage")


# -- the listener, the platform -----------------------------------------------------

def test_nothing_listening_is_blocked_and_nothing_is_written(host, monkeypatch):
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: False)
    rc, out, _err = setup(host.env, "--pull")
    state, detail = step(out)
    assert state == _setup.BLOCKED
    assert "nothing listens on 127.0.0.1:2526" in detail
    assert not host.plist.exists()
    assert "bootstrap" not in host.launchctl.verbs()


def test_the_listener_is_asked_only_when_the_step_would_be_todo(host, monkeypatch):
    setup(host.env, "--pull")
    asked = []
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: asked.append((h, p)) or False)
    rc, out, _err = setup(host.env, "--pull")
    assert step(out)[0] == _setup.DONE
    assert asked == []


def test_listening_connects_to_a_real_scratch_socket():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert _pull_agent.listening("127.0.0.1", port)
    assert not _pull_agent.listening("127.0.0.1", port)


def test_off_macos_the_step_is_blocked(host, monkeypatch):
    monkeypatch.setattr(_pull_agent, "on_macos", lambda: False)
    rc, out, _err = setup(host.env, "--pull")
    state, detail = step(out)
    assert state == _setup.BLOCKED and "macOS only" in detail
    assert host.launchctl.calls() == []
    assert not host.plist.exists()


# -- SC7: without --pull, nothing changes ----------------------------------------------

def test_without_pull_no_pull_config_is_read(host, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the pull config was read without --pull")
    monkeypatch.setattr(_puller, "config", boom)
    monkeypatch.setattr(_pull_agent, "resolve", boom)
    host.pull = {}                       # no pull config at all: no refusal either
    host.write()
    rc, out, err = setup(host.env, "--dry-run")
    assert "REFUSED" not in err, err
    assert step(out) == ("", "")
    assert host.launchctl.calls() == []


def test_the_plan_with_pull_is_the_plan_without_it_plus_one_step(host):
    without, _ = _setup.plan(host.env, [])
    with_pull, _ = _setup.plan(host.env, [], pull=True)
    assert [s.id for s in with_pull] == [s.id for s in without] + ["pull-agent"]


def test_the_help_page_names_pull():
    page = CliRunner().invoke(message_cli, [_setup.VERB, "--help"], prog_name="message").stdout
    assert any(line.lstrip().startswith("--pull") for line in page.splitlines()), page
    assert _setup.parser().parse_args(["--pull"]).pull is True
    assert _setup.parser().parse_args([]).pull is False
