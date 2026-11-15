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


def test_a_root_variable_that_differs_from_the_file_is_refused(host, tmp_path):
    """This env is built from nothing, so the one variable is set here on
    purpose, naming another root than the file: refused, never silently
    ignored. (The retired SESSION_MAIL_ROOT is read by nothing, so it can
    no longer differ from anything.)"""
    env = {**host.env, "SESSIONMAIL_ROOT": str(tmp_path / "elsewhere")}
    rc, _out, err = setup(env, "--pull", "--dry-run")
    assert rc == 2, err
    assert "root (SESSIONMAIL_ROOT)" in err
    assert str(tmp_path / "elsewhere") in err and str(host.root) in err


def test_a_root_variable_naming_the_same_place_is_not_refused(host):
    """Values compare as resolved: the variable and the file both name the
    mail root, and they are the same place."""
    env = {**host.env, "SESSIONMAIL_ROOT": str(host.root)}
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


def test_the_log_is_in_the_kits_log_dir_whatever_this_shells_state_home(host, tmp_path):
    """The log is `~/.sessionkit/mail/log/puller.log` under the job's HOME,
    from the one resolver, whatever SESSIONMAIL_HOME this shell sets. Apply
    makes the folder 0700."""
    log = host.home / ".sessionkit" / "mail" / "log" / "puller.log"
    assert log == _settings.log_dir(host.env) / _pull_agent.LOG_NAME
    env = {**host.env, "SESSIONMAIL_HOME": str(tmp_path / "another-state-home")}
    rc, out, err = setup(env, "--pull")
    assert err == "", err
    got = plistlib.loads(host.plist.read_bytes())
    assert got["StandardOutPath"] == got["StandardErrorPath"] == str(log)
    assert log.parent.is_dir(), "apply makes the log folder"
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700
    # The puller's state dir as the resolver chooses it (a fresh account: the
    # state folder's puller/), never a hand-spelled path that may not be it.
    state = _puller._state_dir("")
    assert state == _operator.settings_home() / _settings.STATE_DIR / _puller.STATE_LEAF
    assert not (state / "log").exists(), "nothing is made under the state dir for the log"


def test_a_set_state_dir_does_not_move_the_log(host, tmp_path):
    """pull.state_dir is state: setting it leaves the plist byte-identical,
    so the step stays DONE and no launchctl write is made."""
    setup(host.env, "--pull")
    before = host.plist.read_bytes()
    host.pull["state_dir"] = str(tmp_path / "state")
    host.write()
    host.forget_calls()
    _rc, out, err = setup(host.env, "--pull")
    assert err == "", err
    assert step(out)[0] == _setup.DONE, out
    assert host.plist.read_bytes() == before
    got = plistlib.loads(before)
    assert got["StandardOutPath"] == str(_settings.log_dir(host.env) / "puller.log")
    assert not (tmp_path / "state" / "log").exists()
    assert [c[0] for c in host.launchctl.calls() if c[0] != "print"] == []


def test_the_log_folder_keeps_the_mode_it_already_has(host):
    """The shared helper makes the folder 0700 only when it is missing."""
    d = _settings.log_dir(host.env)
    d.mkdir(parents=True)
    d.chmod(0o755)
    _rc, _out, err = setup(host.env, "--pull")
    assert err == "", err
    assert stat.S_IMODE(d.stat().st_mode) == 0o755


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (stat.S_IMODE(p.lstat().st_mode),
                                       p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def test_a_puller_installed_before_the_move_is_re_rendered_and_its_old_log_kept(host, tmp_path):
    """A plist rendered with the old log path is our puller's, so setup
    replaces it and restarts the job; until then it keeps writing the old
    log. The old log is never deleted, moved or written."""
    state = tmp_path / "state"
    host.pull["state_dir"] = str(state)
    host.write()
    old_log = state / "log" / "puller.log"
    old_log.parent.mkdir(parents=True)
    old_log.write_bytes(b"puller: 2 boxes from the old days\n")
    old_body = _pull_agent.render(LABEL, sys.executable, str(host.home), old_log)
    host.plist.parent.mkdir(parents=True)
    host.plist.write_bytes(old_body)
    host.load(host.plist)
    before = _tree(state)
    _rc, out, err = setup(host.env, "--pull")
    assert err == "", err
    assert "booted out" in applied(out)
    got = plistlib.loads(host.plist.read_bytes())
    assert got["StandardOutPath"] == str(_settings.log_dir(host.env) / "puller.log")
    assert _tree(state) == before, "the old log and its folder are left exactly as they were"


def test_a_changed_plist_re_renders_boots_out_then_bootstraps(host, monkeypatch):
    setup(host.env, "--pull")
    before = host.plist.read_bytes()
    monkeypatch.setattr(_pull_agent, "interpreter", lambda: "/opt/another/bin/python3")
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


def test_a_bootout_that_never_clears_is_failed_with_no_bootstrap(host, monkeypatch):
    setup(host.env, "--pull")
    monkeypatch.setattr(_pull_agent, "interpreter", lambda: "/opt/another/bin/python3")
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


def test_the_help_page_names_exit_3():
    page = CliRunner().invoke(message_cli, [_setup.VERB, "--help"], prog_name="message").stdout
    flat = " ".join(page.split())
    assert "3 the puller is loaded but its service file was not written (re-run to write it)" \
        in flat, page
    assert "4 done what it can, steps are left" in flat


def test_the_help_page_names_pull():
    page = CliRunner().invoke(message_cli, [_setup.VERB, "--help"], prog_name="message").stdout
    assert any(line.lstrip().startswith("--pull") for line in page.splitlines()), page
    assert _setup.parser().parse_args(["--pull"]).pull is True
    assert _setup.parser().parse_args([]).pull is False


# -- the service file: services/puller.json, beside settings/ ------------------------
#
# `sessionkit service` finds the puller by reading one file the install writes:
# `~/.sessionkit/mail/services/puller.json`, under the job's HOME. Written only
# once the job is bootstrapped and loaded, and only for a label under the kit's
# `dev.sessionkit.mail.` prefix: a host that picked another label runs a job the
# kit does not manage, so no file is written for it. Validated against a
# vendored copy of the kit's schema, `tests/fixtures/service/service.schema.json`.

SERVICE_SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "service" /
                             "service.schema.json").read_text())


def service_file(host: Host) -> Path:
    return host.home / ".sessionkit" / "mail" / "services" / "puller.json"


def expected_service(label: str = LABEL, name: str = "puller") -> dict:
    """The ask's record for the puller, key for key."""
    return {"service": 1, "name": name, "label": label, "enabled": True,
            "install": "message setup --pull"}


def use_label(host: Host, label: str) -> Path:
    """Set pull.label in the settings file; the plist path it gives."""
    host.pull["label"] = label
    host.write()
    return host.home / "Library" / "LaunchAgents" / f"{label}.plist"


def test_the_install_writes_a_schema_valid_puller_service_file(host):
    rc, out, err = setup(host.env, "--pull")
    assert "FAILED" not in out, out
    assert err == "", err
    target = service_file(host)
    rec = json.loads(target.read_text())
    assert rec == expected_service()
    assert list(rec) == list(expected_service()), "the written key order"
    import jsonschema
    jsonschema.validate(rec, SERVICE_SCHEMA)
    assert _service_file_faults(rec) == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert target.read_text().endswith("}\n")
    # Beside settings/, never inside it.
    assert target.parent.parent == host.home / ".sessionkit" / "mail"
    assert f"service {target} (enabled: true)" in applied(out), out
    assert applied(out).index("bootstrapped") < applied(out).index(f"service {target}")


def _service_file_faults(rec: dict) -> list[str]:
    from sessionmail import _service_file
    return _service_file.check(rec)


def test_the_install_command_in_the_file_is_the_verb_that_wrote_it(host):
    setup(host.env, "--pull")
    rec = json.loads(service_file(host).read_text())
    assert rec["install"] == f"{_setup.COMMAND} {_setup.PULL}" == _pull_agent.SERVICE_INSTALL


def test_another_label_under_the_kits_prefix_names_the_service_by_its_tail(host):
    label = "dev.sessionkit.mail.puller.alt"
    plist = use_label(host, label)
    rc, out, _err = setup(host.env, "--pull")
    assert "FAILED" not in out, out
    assert plist.is_file()
    rec = json.loads(service_file(host).read_text())
    assert rec == expected_service(label, "puller.alt")
    import jsonschema
    jsonschema.validate(rec, SERVICE_SCHEMA)


@pytest.mark.parametrize("label", ["org.example.puller", "dev.sessionkit.mailer.puller",
                                   "dev.sessionkit.voice.puller", "dev.sessionkit.mail"])
def test_a_label_outside_the_kits_prefix_writes_no_service_file(host, label):
    plist = use_label(host, label)
    rc, out, _err = setup(host.env, "--pull")
    assert "FAILED" not in out, out
    assert plist.is_file(), "the job is installed all the same"
    assert label in host.launchctl.jobs()
    assert not (host.home / ".sessionkit" / "mail" / "services").exists()
    assert "no service file" in applied(out) and "dev.sessionkit.mail." in applied(out), out
    # A second run is DONE: the missing file is not something to do.
    rc2, out2, _ = setup(host.env, "--pull")
    assert step(out2)[0] == _setup.DONE, out2
    assert not (host.home / ".sessionkit" / "mail" / "services").exists()


def test_a_label_outside_the_prefix_leaves_an_earlier_file_untouched(host):
    """The file belongs to a label the kit manages; a host that moves the
    puller off it gets no write at all, the old file included."""
    old = service_file(host)
    old.parent.mkdir(parents=True)
    old.write_text('{"keep": "me"}\n')
    use_label(host, "org.example.puller")
    setup(host.env, "--pull")
    assert old.read_text() == '{"keep": "me"}\n'


def test_a_re_run_is_idempotent_and_writes_nothing(host):
    setup(host.env, "--pull")
    target = service_file(host)
    before = target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns
    host.forget_calls()
    rc, out, err = setup(host.env, "--pull")
    assert "FAILED" not in out and err == ""
    assert step(out)[0] == _setup.DONE, out
    assert applied(out) == ""
    assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns) == before
    assert sorted(p.name for p in target.parent.iterdir()) == ["puller.json"]
    assert set(host.launchctl.verbs()) == {"print"}


def test_a_running_puller_with_no_file_gets_one_without_a_restart(host, monkeypatch):
    """A puller installed before the file existed: the plist is current and
    the job loaded, so the step writes the file alone. No bootout, no
    bootstrap, no lint, and the listener is not asked: nothing launchd runs
    changes."""
    setup(host.env, "--pull")
    service_file(host).unlink()
    host.forget_calls()
    asked = []
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: asked.append((h, p)) or False)
    rc, out, err = setup(host.env, "--pull", "--dry-run")
    state, detail = step(out)
    assert state == _setup.TODO and f"write {service_file(host)}" in detail, out
    assert not service_file(host).exists(), "a dry run writes nothing"
    rc, out, err = setup(host.env, "--pull")
    assert "FAILED" not in out, out
    assert json.loads(service_file(host).read_text()) == expected_service()
    assert applied(out) == f"service {service_file(host)} (enabled: true)", out
    assert set(host.launchctl.verbs()) == {"print"}, host.launchctl.calls()
    assert host.plutil_calls() == []
    assert asked == []
    assert step(setup(host.env, "--pull")[1])[0] == _setup.DONE


@pytest.mark.parametrize("old", [b'{"service": 1, "name": "x", "enabled": false}\n',
                                 b"not json at all", b""])
def test_a_stale_or_hand_edited_file_is_rewritten_whole(host, old):
    setup(host.env, "--pull")
    service_file(host).write_bytes(old)
    host.forget_calls()
    rc, out, _err = setup(host.env, "--pull")
    assert "FAILED" not in out, out
    assert json.loads(service_file(host).read_text()) == expected_service()
    assert "bootstrap" not in host.launchctl.verbs()


def test_the_write_is_a_temp_file_renamed_into_place(host, monkeypatch):
    """Atomic: the bytes go to a temp file in the services folder, and one
    rename puts it over the target."""
    setup(host.env, "--pull")
    service_file(host).unlink()
    renames = []
    real = os.replace

    def spy(src, dst):
        renames.append((Path(src), Path(dst)))
        return real(src, dst)
    monkeypatch.setattr(os, "replace", spy)
    setup(host.env, "--pull")
    assert len(renames) == 1, renames
    src, dst = renames[0]
    assert dst == service_file(host)
    assert src.parent == dst.parent and src != dst and not src.exists()


def test_a_failed_rename_leaves_the_old_file_byte_identical(host, monkeypatch):
    setup(host.env, "--pull")
    target = service_file(host)
    target.write_bytes(b"old bytes")
    real = os.replace

    def refuse(src, dst):
        if Path(dst) == target:
            raise PermissionError(13, "Permission denied")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", refuse)
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_UNDECLARED == 3, out
    assert target.read_bytes() == b"old bytes"
    assert sorted(p.name for p in target.parent.iterdir()) == ["puller.json"], "no temp left"


def block_services(host: Host) -> Path:
    """Put a FILE where the services folder goes, so no service file can be
    written under it; the blocker, to check it is left as it was."""
    blocker = host.home / ".sessionkit" / "mail" / "services"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file, not a folder")
    return blocker


def test_a_failed_write_names_the_job_as_installed_and_exits_3(host):
    """The services path is a file, so the folder cannot be made: the job is
    still installed and loaded (a failed write never undoes the launchd
    act), the line says what to re-run, and the exit is 3, not a failed
    install's 1."""
    blocker = block_services(host)
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_UNDECLARED == 3, out
    assert "FAILED: service could not write" in out
    assert "the previous file is unchanged" in out
    assert f"{LABEL} is installed and loaded" in out
    assert f"Re-run {_setup.COMMAND} {_setup.PULL}" in out
    assert host.plist.is_file() and LABEL in host.launchctl.jobs()
    assert blocker.read_text() == "a file, not a folder"
    assert out.rstrip("\n").splitlines()[-1] == (
        f"{_setup.COMMAND}: pull-agent installed and loaded, but its service file was not "
        f"written; re-run {_setup.COMMAND} with the same options to write it")
    assert "stopped at" not in out, "the run is not stopped: the job is up"


def test_exit_3_wins_over_4_and_still_lists_the_steps_left(host):
    """This host has no notmuch, so a clean run here is 4 (steps left). With
    the file unwritten it is 3, and the block of steps left still prints,
    before the line that says to re-run."""
    clean_rc = setup(host.env, "--pull", "--dry-run")[0]
    assert clean_rc == _setup.EX_REMAINS, "the premise: steps are left on this host"
    block_services(host)
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_UNDECLARED, out
    assert "step(s) left for you:" in out and "notmuch-index (BLOCKED)" in out
    block = out.split("step(s) left for you:\n", 1)[1].split("then run", 1)[0]
    assert "pull-agent" not in block, "the puller is not a step left for the human"
    assert out.index("step(s) left for you:") < out.index("pull-agent installed and loaded, but")


def test_a_running_puller_whose_file_cannot_be_written_is_3_with_no_launchctl_write(host):
    """The file-alone path: the job runs from a current plist, the file is
    missing and cannot be written. 3, and launchd is only read."""
    setup(host.env, "--pull")
    service_file(host).unlink()
    service_file(host).parent.rmdir()
    block_services(host)
    host.forget_calls()
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_UNDECLARED, out
    assert "FAILED: service could not write" in applied(out), out
    assert set(host.launchctl.verbs()) == {"print"}, host.launchctl.calls()
    assert LABEL in host.launchctl.jobs()


def test_a_bootstrap_failure_is_1_even_with_the_services_folder_blocked(host):
    """1 keeps its meaning: a failed install is 1, and its file is never tried."""
    host.launchctl.knob("fail-bootstrap")
    block_services(host)
    rc, out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_FAILED == 1, out
    assert "service could not write" not in out
    assert "stopped at pull-agent" in out


def _plan_step(sid: str, kind: str) -> "_setup.Step":
    """A step whose apply does KIND: "ok", or raises "undeclared", "failed"
    or "left". Built at run time, so the file collects on any sha."""
    raises = {"undeclared": "Undeclared", "failed": "Failed", "left": "Left"}

    def apply() -> str:
        if kind == "ok":
            return f"did {sid}"
        raise getattr(_setup, raises[kind])(f"{sid} {kind}")
    return _setup.Step(sid, sid, check=lambda: (_setup.TODO, sid), apply=apply)


@pytest.mark.parametrize("kinds, want", [
    (("ok", "undeclared"), 3),
    (("undeclared", "ok"), 3),
    (("undeclared", "failed"), 1),
    (("undeclared", "left"), 3),
    (("ok", "left"), 4),
    (("ok", "ok"), 0),
])
def test_run_plan_exit_precedence_is_1_then_3_then_4_then_0(kinds, want):
    """An Undeclared step does not stop the run (nothing later reads its
    file); a later Failed still stops it with 1."""
    plan = [_plan_step(sid, kind) for sid, kind in zip("ab", kinds)]
    out = io.StringIO()
    rc = _setup.run_plan(plan, {}, out, dry_run=False, tty=False)
    assert rc == want, out.getvalue()
    if want == 3:
        assert "FAILED: " in out.getvalue()
    if kinds == ("undeclared", "ok"):
        assert "did b" in out.getvalue(), "the run goes on past an undeclared step"


def test_the_exit_codes_are_distinct_and_3_is_undeclared():
    assert (_setup.EX_OK, _setup.EX_FAILED, _setup.EX_USAGE, _setup.EX_UNDECLARED,
            _setup.EX_REMAINS) == (0, 1, 2, 3, 4)
    assert issubclass(_setup.Undeclared, _setup.Failed)
    assert issubclass(_pull_agent.Undeclared, _pull_agent.Failed)


def test_a_failed_install_writes_no_service_file(host):
    host.launchctl.knob("fail-bootstrap")
    rc, _out, _err = setup(host.env, "--pull")
    assert rc == _setup.EX_FAILED
    assert not service_file(host).exists()


def test_a_lint_failure_writes_no_service_file(host):
    (host.plutil.parent / "plutil.fail").write_text("")
    setup(host.env, "--pull")
    assert not service_file(host).exists()


def test_a_blocked_step_writes_no_service_file(host, monkeypatch):
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: False)
    setup(host.env, "--pull")
    assert not service_file(host).exists()
    monkeypatch.setattr(_pull_agent, "listening", lambda h, p: True)
    _foreign_plist(host.plist)
    setup(host.env, "--pull")
    assert not service_file(host).exists()


def test_a_dry_run_writes_no_service_file(host):
    setup(host.env, "--pull", "--dry-run")
    assert not (host.home / ".sessionkit").exists()

