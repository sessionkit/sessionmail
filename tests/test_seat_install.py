"""`message seat agent --install ADDRESS [SOCKET]` and `--uninstall ADDRESS`.

A bot's signer has to come back after a reboot, so the seat's agent is loaded
as a LaunchAgent: the packaged template rendered into ~/Library/LaunchAgents,
`launchctl bootstrap`, and the effect checked (the agent answers on its socket
holding seat.pub's key). `--uninstall` boots it out and removes the plist.

NO TEST HERE RUNS THE REAL LAUNCHCTL. Every test gets the conftest's
`launchctl_fake` through `SESSIONMAIL_LAUNCHCTL`; one that builds its env from
nothing adds `launchctl_fake.env` to it, and the meta-test at the bottom
greps the suite for that. The fake simulates launchd: its `bootstrap` starts
the plist's ProgramArguments (the real `message seat agent`, on a scratch
socket in a scratch HOME) and its `bootout` TERMs that pid.

Every seat here is minted the way a stranger mints one today: `message seat
init` with an EXPLICIT `--registry`, a scratch registry hand-seeded with an
`@session` line (test_seat_init's `registry`).
"""

from __future__ import annotations

import io
import os
import plistlib
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from sessionmail import _launchd
from test_seat_init import MESSAGE, git_env, registry, seat_init, tools  # noqa: F401

ADDRESS = "bot.alice"
LABEL = f"dev.sessionkit.mail.seat-agent.{ADDRESS}"
REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "src" / "sessionmail" / "seat" / "seat-agent.plist.template"
VENV_BIN = str(MESSAGE.parent)

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS's")


def answers(sock: str) -> int:
    """`ssh-add -l` on SOCK: 0 holds keys, 1 empty, 2 nothing answers."""
    return subprocess.run(["ssh-add", "-l"], capture_output=True,
                          env={"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": sock}).returncode


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def scratch(launchctl_fake):
    """A short scratch dir under /tmp (the default socket must fit macOS's 103
    bytes), a HOME and a SESSIONMAIL_HOME in it, and an env built from nothing
    that carries the fake launchctl."""
    d = Path(tempfile.mkdtemp(prefix="sm-in-", dir="/tmp"))
    (d / "u").mkdir()
    env = {"PATH": f"{VENV_BIN}:/usr/bin:/bin", "HOME": str(d / "u"),
           "SESSIONMAIL_HOME": str(d / "h"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1", **launchctl_fake.env}
    yield dict(dir=d, env=env)
    launchctl_fake.stop_all()
    for sock in d.rglob("*.sock"):
        found = subprocess.run(["pgrep", "-f", f"ssh-agent -D -a {sock}"], capture_output=True,
                               text=True).stdout.split()
        for pid in found:
            os.kill(int(pid), signal.SIGKILL)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def seat(scratch, registry, tools):  # noqa: F811
    """bot.alice, minted with an explicit --registry into the scratch SESSIONMAIL_HOME."""
    rc, _, err = seat_init(scratch["env"], "--registry", registry, ADDRESS)
    assert rc == 0, err
    env = scratch["env"]
    home = Path(env["SESSIONMAIL_HOME"]) / "seat-keys" / ADDRESS
    return dict(scratch, home=home, pub=home / "seat.pub", sock=str(home / "agent.sock"),
                plist=Path(env["HOME"]) / "Library" / "LaunchAgents" / f"{LABEL}.plist",
                log=Path(env["HOME"]) / "Library" / "Logs" / f"seat-agent.{ADDRESS}.log")


def message(env, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([str(MESSAGE), *args], env=env, capture_output=True, text=True,
                          timeout=timeout, check=False)


def pairs(stdout: str) -> dict[str, str]:
    """The stdout contract, read the way `eval` would."""
    out = {}
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        (out[key],) = shlex.split(value)
    return out


def in_process(verb, *args, env):
    out, err = io.StringIO(), io.StringIO()
    if verb == "install":
        rc = _launchd.install(*args, env=env, out=out, err=err)
    else:
        rc = _launchd.uninstall(*args, env=env, err=err)
    return rc, out.getvalue(), err.getvalue()


# ── the whole path: install, answer, re-install, uninstall ──────────────────

@darwin
def test_install_loads_the_agent_reinstall_is_idempotent_uninstall_takes_it_away(seat, launchctl_fake):
    env = seat["env"]
    done = message(env, "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 0, done.stderr
    report = pairs(done.stdout)
    assert list(report) == ["SEAT_LABEL", "SEAT_PLIST", "SEAT_SOCKET", "SEAT_PUB", "SEAT_LOG"]
    assert report == {"SEAT_LABEL": LABEL, "SEAT_PLIST": str(seat["plist"]),
                      "SEAT_SOCKET": seat["sock"], "SEAT_PUB": str(seat["pub"]),
                      "SEAT_LOG": str(seat["log"])}
    assert done.stderr == (f"message seat agent: installed and loaded {LABEL}; "
                           f"the agent answers on {seat['sock']}\n")
    # the effect: the agent answers, holding seat.pub's key
    held = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "SSH_AUTH_SOCK": seat["sock"]})
    assert seat["pub"].read_text().split()[1] in held.stdout
    # the plist: 0644, our Label, launchd's arguments
    assert stat.S_IMODE(seat["plist"].stat().st_mode) == 0o644
    job = plistlib.loads(seat["plist"].read_bytes())
    assert job["Label"] == LABEL
    assert job["ProgramArguments"] == [str(MESSAGE), "seat", "agent", ADDRESS, seat["sock"]]
    assert job["EnvironmentVariables"]["SESSIONMAIL_HOME"] == env["SESSIONMAIL_HOME"]
    assert job["StandardErrorPath"] == str(seat["log"])
    uid = os.getuid()
    assert launchctl_fake.calls() == [["print", f"gui/{uid}/{LABEL}"],
                                      ["bootstrap", f"gui/{uid}", str(seat["plist"])]]
    pid = launchctl_fake.jobs()[LABEL]["pid"]
    assert alive(pid)

    # the stdout is eval-able as the contract says
    shell = subprocess.run(["/bin/bash", "-c", 'eval "$1"; printf %s "$SEAT_SOCKET"', "-",
                            done.stdout], capture_output=True, text=True)
    assert shell.stdout == seat["sock"]

    again = message(env, "seat", "agent", "--install", ADDRESS)
    assert again.returncode == 0, again.stderr
    assert again.stdout == done.stdout
    assert "already installed" in again.stderr
    assert launchctl_fake.verbs().count("bootstrap") == 1, "a re-run never loads twice"

    gone = message(env, "seat", "agent", "--uninstall", ADDRESS)
    assert gone.returncode == 0, gone.stderr
    assert gone.stdout == ""
    assert gone.stderr == f"message seat agent: booted out {LABEL}; removed {seat['plist']}\n"
    assert not seat["plist"].exists()
    assert not os.path.exists(seat["sock"]), "the wrapper's TERM trap removes the socket"
    assert not alive(pid)
    assert answers(seat["sock"]) == 2
    # the key and its pub are never touched
    assert (seat["home"] / "id_ed25519").is_file() and seat["pub"].is_file()

    twice = message(env, "seat", "agent", "--uninstall", ADDRESS)
    assert twice.returncode == 0 and twice.stdout == ""
    assert twice.stderr == f"message seat agent: nothing installed for {ADDRESS}\n"


@darwin
def test_install_with_a_named_socket(seat, launchctl_fake):
    sock = str(seat["dir"] / "s.sock")
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS, sock)
    assert done.returncode == 0, done.stderr
    assert pairs(done.stdout)["SEAT_SOCKET"] == sock
    assert answers(sock) == 0
    assert plistlib.loads(seat["plist"].read_bytes())["ProgramArguments"][-1] == sock


# ── render ──────────────────────────────────────────────────────────────────

def job(**over) -> _launchd.Job:
    values = dict(address=ADDRESS, label=LABEL, plist="/x/p.plist", socket="/x/a.sock",
                  pub="/x/seat.pub", key="/x/id_ed25519", log="/x/l/seat-agent.bot.alice.log",
                  log_dir="/x/l", sessionmail_home="/x/h", message="/x/bin/message",
                  domain="gui/501")
    values.update(over)
    return _launchd.Job(**values)


def test_render_fills_every_placeholder_and_escapes_each_value():
    odd = "/x/R&D <one>"
    out = _launchd.render(TEMPLATE.read_bytes(), job(sessionmail_home=odd, socket=odd + "/s"))
    assert not re.search(rb"@[A-Z_]+@", out)
    parsed = plistlib.loads(out)
    assert parsed["Label"] == LABEL
    assert parsed["EnvironmentVariables"]["SESSIONMAIL_HOME"] == odd
    assert parsed["ProgramArguments"] == ["/x/bin/message", "seat", "agent", ADDRESS, odd + "/s"]


def test_render_is_one_pass_a_value_is_never_read_for_a_placeholder():
    out = _launchd.render(TEMPLATE.read_bytes(), job(sessionmail_home="/x/@SOCKET@"))
    assert plistlib.loads(out)["EnvironmentVariables"]["SESSIONMAIL_HOME"] == "/x/@SOCKET@"


def test_render_refuses_a_placeholder_it_does_not_fill():
    with pytest.raises(_launchd.Refused, match="@EXTRA@"):
        _launchd.render(TEMPLATE.read_bytes().replace(b"<true/>", b"<string>@EXTRA@</string>", 1),
                        job())


def test_render_refuses_a_label_that_is_not_the_jobs():
    with pytest.raises(_launchd.Refused, match="rendered Label"):
        _launchd.render(TEMPLATE.read_bytes(), job(label="dev.sessionkit.mail.seat-agent.other"))


def test_render_refuses_a_document_plistlib_cannot_read():
    with pytest.raises(_launchd.Refused, match="not a valid plist"):
        _launchd.render(TEMPLATE.read_bytes().replace(b"</dict>\n</plist>", b"</plist>"), job())


# ── refusals: rc 2, nothing written, launchctl never asked ──────────────────

def nothing_written(seat, launchctl_fake):
    assert not seat["plist"].exists()
    assert not (Path(seat["env"]["HOME"]) / "Library").exists()
    assert launchctl_fake.calls() == []


@darwin
@pytest.mark.parametrize("argv, words", [
    (["--install", "--template", ADDRESS], "--install and --template are one each"),
    (["--install", "--uninstall", ADDRESS], "--install and --uninstall are one each"),
    (["--uninstall", "--template", ADDRESS], "--uninstall and --template are one each"),
    (["--install"], "usage: message seat agent --install ADDRESS [SOCKET]"),
    (["--install", ADDRESS, "/tmp/a.sock", "extra"], "usage: message seat agent --install"),
    (["--uninstall"], "usage: message seat agent --uninstall ADDRESS"),
    (["--uninstall", ADDRESS, "/tmp/a.sock"], "usage: message seat agent --uninstall ADDRESS"),
])
def test_flag_misuse_is_refused(seat, launchctl_fake, argv, words):
    done = message(seat["env"], "seat", "agent", *argv)
    assert done.returncode == 2, done.stderr
    assert words in done.stderr and done.stdout == ""
    nothing_written(seat, launchctl_fake)


@darwin
@pytest.mark.parametrize("address, words", [
    ("bot+alice", "has a '+'"),
    ("Bot.Alice", "is not a seat address"),
    ("bot.bob", "mint the seat first"),
])
def test_an_address_that_is_not_a_minted_seat_is_refused(seat, launchctl_fake, address, words):
    done = message(seat["env"], "seat", "agent", "--install", address)
    assert done.returncode == 2
    assert words in done.stderr and done.stderr.rstrip().endswith("Nothing was written.")
    nothing_written(seat, launchctl_fake)


@darwin
def test_a_missing_seat_pub_is_refused(seat, launchctl_fake):
    seat["pub"].unlink()
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 2 and "no seat.pub" in done.stderr
    nothing_written(seat, launchctl_fake)


@darwin
@pytest.mark.parametrize("which, words", [
    ("long", "bytes; macOS binds 103 at most"),
    ("no-dir", "no directory"),
    ("relative", "is not an absolute path"),
])
def test_a_socket_launchd_cannot_use_is_refused(seat, launchctl_fake, which, words):
    sock = {"long": "/tmp/" + "s" * 100 + ".sock", "no-dir": str(seat["dir"] / "none" / "a.sock"),
            "relative": "a.sock"}[which]
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS, sock)
    assert done.returncode == 2 and words in done.stderr
    nothing_written(seat, launchctl_fake)


@darwin
def test_no_message_on_path_is_refused(seat, launchctl_fake):
    env = dict(seat["env"], PATH="/usr/bin:/bin")
    rc, out, err = in_process("install", ADDRESS, env=env)
    assert rc == 2 and out == "" and "`message` is not on PATH" in err
    nothing_written(seat, launchctl_fake)


def test_not_macos_is_refused_for_both(monkeypatch, tmp_path, launchctl_fake):
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"HOME": str(tmp_path), "PATH": VENV_BIN, **launchctl_fake.env}
    for verb in ("install", "uninstall"):
        rc, out, err = in_process(verb, ADDRESS, env=env)
        assert rc == 2 and out == "" and "launchd only" in err
    assert launchctl_fake.calls() == []


@darwin
def test_a_different_plist_at_the_label_is_never_overwritten(seat, launchctl_fake):
    seat["plist"].parent.mkdir(parents=True)
    seat["plist"].write_bytes(b"someone else's\n")
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 2 and "a different plist" in done.stderr
    assert "--uninstall bot.alice` first" in done.stderr
    assert seat["plist"].read_bytes() == b"someone else's\n"
    assert "bootstrap" not in launchctl_fake.verbs()


@darwin
def test_a_job_already_holding_the_label_is_refused(seat, launchctl_fake):
    """Loaded, but by another plist (here: none at our path): never replaced."""
    jobs = launchctl_fake.state / "jobs"
    jobs.mkdir(exist_ok=True)
    (jobs / f"{LABEL}.json").write_text('{"plist": "/elsewhere.plist", "pid": null}')
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 2 and "a job already holds" in done.stderr
    assert not seat["plist"].exists()
    assert launchctl_fake.verbs() == ["print"]


# ── failures: rc 1, rolled back ─────────────────────────────────────────────

@darwin
def test_a_failed_bootstrap_removes_the_plist_this_run_wrote(seat, launchctl_fake):
    launchctl_fake.knob("fail-bootstrap")
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 1 and done.stdout == ""
    assert "launchctl bootstrap" in done.stderr and "Input/output error" in done.stderr
    assert "the plist this run wrote is removed" in done.stderr
    assert not seat["plist"].exists()
    assert "bootout" not in launchctl_fake.verbs()


@darwin
def test_a_failed_bootstrap_keeps_a_plist_that_was_there_before(seat, launchctl_fake, monkeypatch):
    """The same render, left from an earlier run and not loaded: it is used, and kept."""
    env = seat["env"]
    plan = _launchd.plan(ADDRESS, None, env)
    seat["plist"].parent.mkdir(parents=True)
    seat["plist"].write_bytes(_launchd.render(TEMPLATE.read_bytes(), plan))
    launchctl_fake.knob("fail-bootstrap")
    rc, out, err = in_process("install", ADDRESS, env=env)
    assert rc == 1 and out == ""
    assert "was there before this run and is kept" in err
    assert seat["plist"].exists()


@darwin
def test_an_agent_that_never_answers_is_booted_out_and_rolled_back(seat, launchctl_fake, monkeypatch):
    monkeypatch.setattr(_launchd, "ANSWER_SECONDS", 0.5)
    launchctl_fake.knob("no-start")
    seat["log"].parent.mkdir(parents=True)
    seat["log"].write_text("message seat agent: FAILED - something the agent said\n")
    rc, out, err = in_process("install", ADDRESS, env=seat["env"])
    assert rc == 1 and out == ""
    assert "did not answer" in err and "booted it out" in err
    assert "the plist this run wrote is removed" in err
    assert "something the agent said" in err, "the log's last lines are quoted"
    assert not seat["plist"].exists()
    assert launchctl_fake.verbs() == ["print", "bootstrap", "bootout"]
    assert launchctl_fake.jobs() == {}


@darwin
def test_a_plist_that_cannot_be_written_is_a_failed_line_not_a_traceback(seat, launchctl_fake):
    agents = seat["plist"].parent
    agents.mkdir(parents=True)
    agents.chmod(0o555)
    try:
        done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    finally:
        agents.chmod(0o755)
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.startswith("message seat agent: FAILED - could not write "), done.stderr
    assert "Traceback" not in done.stderr
    assert "nothing was loaded and no plist was left" in done.stderr
    assert list(agents.iterdir()) == [], "no plist and no temp file left"
    assert launchctl_fake.verbs() == ["print"]


@darwin
def test_a_log_dir_that_cannot_be_made_keeps_a_plist_that_was_there_before(seat, launchctl_fake):
    """The same render, left from an earlier run and not loaded; the log dir cannot be
    made. The FAILED line says the plist is kept, never that none was left."""
    env = seat["env"]
    plan = _launchd.plan(ADDRESS, None, env)
    seat["plist"].parent.mkdir(parents=True)
    rendered = _launchd.render(TEMPLATE.read_bytes(), plan)
    seat["plist"].write_bytes(rendered)
    library = seat["plist"].parent.parent
    library.chmod(0o555)
    try:
        done = message(env, "seat", "agent", "--install", ADDRESS)
    finally:
        library.chmod(0o755)
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr == (f"message seat agent: FAILED - could not write {seat['log'].parent} "
                           f"(Permission denied); nothing was loaded and the plist at "
                           f"{seat['plist']} was there before this run and is kept\n")
    assert seat["plist"].read_bytes() == rendered
    assert launchctl_fake.verbs() == ["print"]


@darwin
def test_a_loaded_job_that_stops_answering_is_reported_and_left_as_found(seat, launchctl_fake,
                                                                        monkeypatch):
    done = message(seat["env"], "seat", "agent", "--install", ADDRESS)
    assert done.returncode == 0, done.stderr
    pid = launchctl_fake.jobs()[LABEL]["pid"]
    os.kill(pid, signal.SIGTERM)       # the agent goes; launchd would restart it
    for _ in range(100):
        if not alive(pid):
            break
        subprocess.run(["sleep", "0.05"])
    monkeypatch.setattr(_launchd, "ANSWER_SECONDS", 0.3)
    rc, out, err = in_process("install", ADDRESS, env=seat["env"])
    assert rc == 1 and out == ""
    assert "left as found" in err
    assert seat["plist"].exists() and LABEL in launchctl_fake.jobs()
    assert "bootout" not in launchctl_fake.verbs()


# ── uninstall's edges ───────────────────────────────────────────────────────

@darwin
def test_uninstall_refuses_a_plist_whose_label_is_not_ours(seat, launchctl_fake):
    seat["plist"].parent.mkdir(parents=True)
    seat["plist"].write_bytes(plistlib.dumps({"Label": "dev.example.other"}))
    done = message(seat["env"], "seat", "agent", "--uninstall", ADDRESS)
    assert done.returncode == 2 and "not this tool's to remove" in done.stderr
    assert seat["plist"].exists() and launchctl_fake.calls() == []


@darwin
def test_uninstall_keeps_the_plist_when_bootout_fails(seat, launchctl_fake):
    assert message(seat["env"], "seat", "agent", "--install", ADDRESS).returncode == 0
    launchctl_fake.knob("fail-bootout")
    done = message(seat["env"], "seat", "agent", "--uninstall", ADDRESS)
    assert done.returncode == 1 and "bootout" in done.stderr and "plist is kept" in done.stderr
    assert seat["plist"].exists()


@darwin
def test_uninstall_that_cannot_remove_the_plist_says_what_it_did(seat, launchctl_fake):
    """The bootout is done; the unlink fails. A FAILED line names both, never a traceback."""
    assert message(seat["env"], "seat", "agent", "--install", ADDRESS).returncode == 0
    agents = seat["plist"].parent
    agents.chmod(0o555)
    try:
        done = message(seat["env"], "seat", "agent", "--uninstall", ADDRESS)
    finally:
        agents.chmod(0o755)
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr == (f"message seat agent: FAILED - booted out {LABEL}; but could not "
                           f"remove {seat['plist']} (Permission denied); the plist is kept\n")
    assert seat["plist"].exists() and launchctl_fake.jobs() == {}


@darwin
def test_uninstall_removes_a_plist_that_was_never_loaded(seat, launchctl_fake):
    seat["plist"].parent.mkdir(parents=True)
    seat["plist"].write_bytes(plistlib.dumps({"Label": LABEL}))
    done = message(seat["env"], "seat", "agent", "--uninstall", ADDRESS)
    assert done.returncode == 0
    assert done.stderr == f"message seat agent: removed {seat['plist']}\n"
    assert "bootout" not in launchctl_fake.verbs()


# ── the page and its marker ─────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_the_page_carries_the_capability_marker_as_a_whole_line(flag):
    done = subprocess.run([str(MESSAGE), "seat", "agent", flag], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
    assert done.returncode == 0, done.stderr
    assert "Capability: install" in done.stdout.splitlines(), "grep -qx must find it"
    for flag_name in ("--install", "--uninstall", "--template"):
        assert flag_name in done.stdout


# ── no test reaches the real launchd ────────────────────────────────────────

def test_every_test_runs_the_fake_never_the_real_launchctl(launchctl_fake):
    assert os.environ[_launchd.LAUNCHCTL_VAR] == str(launchctl_fake.path)
    assert _launchd.launchctl_path(os.environ) == str(launchctl_fake.path)
    assert _launchd.launchctl_path({}) != "/bin/launchctl", "the fallback fails closed"
    with pytest.raises(_launchd.Failed, match="could not run"):
        _launchd.launchctl({}, "print", "gui/0/x")


def test_every_test_file_that_installs_carries_the_fake():
    """A test that builds an env from nothing drops the conftest's variable, and
    would fall back to /bin/launchctl in a child. So every file that names the
    flags must name the fake too."""
    tests = Path(__file__).resolve().parent
    users = [p for p in tests.rglob("*.py")
             if re.search(r"--(un)?install\b", p.read_text(errors="replace"))
             and p.name != "conftest.py"]
    assert Path(__file__).resolve() in users, "the grep would find nothing: it is broken"
    for path in users:
        text = path.read_text(errors="replace")
        assert "launchctl_fake" in text or _launchd.LAUNCHCTL_VAR in text, path
