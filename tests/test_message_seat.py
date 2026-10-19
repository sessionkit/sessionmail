"""SM-715 do-002: `message seat init` and `message seat agent`, the hidden seat noun.

Ruling (b): sessionmail keeps exactly three commands,
and SM-590's seat-init and seat-agent ship as `message seat init` and `message seat
agent`. The rest is the design (SM-715 design.md §1-§6, openspec/changes/message-seat):
the noun is HIDDEN (Q-F), `agent` EXECs the packaged bash with SIGPIPE
and SIGXFSZ restored, a broken install exits 2, and `--template` prints the template.

The behaviour of the two verbs is SM-590's, and test_seat_init / test_seat_agent /
test_seat_acceptance run it through these verbs. This file pins only what the door adds.

NEVER IN-PROCESS but for -h, --help and --template (design §6): `seat agent` replaces
the process it runs in, and `seat init` sets the process umask. Every other run is a
child: the test venv's `message`, or `python -c` over the same click group when a test
must point the door at a file of its own.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mcp
from sessionmail import message as message_mod

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "src" / "sessionmail" / "seat" / "seat-agent"
TEMPLATE = REPO / "src" / "sessionmail" / "seat" / "seat-agent.plist.template"
MESSAGE = Path(sys.executable).parent / "message"


@pytest.fixture
def bare(tmp_path):
    """An env built from nothing, with a scratch HOME and SESSIONMAIL_HOME (launchd's shape)."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "SESSIONMAIL_HOME": str(tmp_path / "sm")}


def message(*args: str, env: dict[str, str], timeout: float = 30) -> subprocess.CompletedProcess:
    assert MESSAGE.is_file(), f"no {MESSAGE}: the verbs cannot be run as installed"
    return subprocess.run([str(MESSAGE), *args], env=env, capture_output=True, timeout=timeout,
                          check=False)


def door(agent: Path, *args: str, env: dict[str, str]) -> subprocess.Popen:
    """`message seat agent ARGS` in a CHILD whose door is pointed at AGENT instead of
    the packaged wrapper. The child is plain `python -c`: CPython ignores SIGPIPE and
    SIGXFSZ at startup there, exactly as the `message` launcher does."""
    code = textwrap.dedent(f"""
        from sessionmail import message
        message.SEAT_AGENT = {str(agent)!r}
        message.cli(["seat", "agent", *{list(args)!r}], prog_name="message")
    """)
    return subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


# ── hidden, and still answers (Q-F, both conditions) ─────────────────────────

def test_seat_is_on_no_page_and_no_mcp_tool():
    seat = message_mod.cli.commands["seat"]
    assert seat.hidden
    page = CliRunner().invoke(message_mod.cli, ["-h"], prog_name="message").output
    assert not re.search(r"^\s+seat\b", page, re.MULTILINE), page
    names = [name for name, _, _ in _mcp.tools(message_mod.cli, "message")]
    assert names and not any("seat" in name for name in names), names


@pytest.mark.parametrize("argv, usage", [
    (["seat", "--help"], "Usage: message seat init|agent"),
    (["seat", "-h"], "Usage: message seat init|agent"),
    (["seat", "agent", "--help"], "Usage: message seat agent ADDRESS [SOCKET] | --template"),
    (["seat", "agent", "-h"], "Usage: message seat agent ADDRESS [SOCKET] | --template"),
    (["seat", "init", "--help"], "Usage: message seat init --registry DIR ADDRESS"),
])
def test_the_hidden_pages_answer_when_typed(bare, argv, usage):
    done = message(*argv, env=bare)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode().startswith(usage), done.stdout
    if "agent" in argv:
        assert b"--template" in done.stdout, "the page is how a user finds the template"


# ── the template ─────────────────────────────────────────────────────────────

def test_template_prints_the_packaged_file_byte_for_byte(bare):
    done = message("seat", "agent", "--template", env=bare)
    assert done.returncode == 0, done.stderr
    assert done.stdout == TEMPLATE.read_bytes()
    assert done.stderr == b""


def test_template_under_clirunner_is_the_same_bytes():
    """--template is one of the three runs allowed in-process: it never execs."""
    done = CliRunner().invoke(message_mod.cli, ["seat", "agent", "--template"], prog_name="message")
    assert done.exit_code == 0 and done.stdout_bytes == TEMPLATE.read_bytes()


@pytest.mark.parametrize("args", [["--template", "desk.uma"], ["desk.uma", "--template"]])
def test_template_takes_no_address_and_renders_nothing(bare, args):
    done = message("seat", "agent", *args, env=bare)
    assert done.returncode == 2 and done.stdout == b""
    assert b"takes no ADDRESS" in done.stderr and b"Nothing was printed" in done.stderr


#: The one launchd label the kit ships for mail: dev.sessionkit.<package>.<job>,
#: and the package is `mail`. The organisation word it replaced is built from
#: pieces, so this file never carries it and an archive grep for it stays clean.
LABEL = "dev.sessionkit.mail.seat-agent.@ADDRESS@"
ORG_WORD = re.compile("fail" "mode", re.IGNORECASE)


def test_the_label_is_the_kits_and_nothing_the_verb_prints_names_the_org(bare):
    """The template's Label is dev.sessionkit.mail.*, and no output of `message seat
    agent` (the template, both help pages, the refusal) carries the old org word, in
    any case. Nor does the packaged wrapper, whose words land in the launchd log."""
    text = TEMPLATE.read_text()
    labels = re.findall(r"<key>Label</key>\s*<string>(.*?)</string>", text)
    assert labels == [LABEL] and labels[0].startswith("dev.sessionkit.mail.")
    printed = [TEMPLATE.read_bytes(), WRAPPER.read_bytes()]
    for argv in (["--template"], ["-h"], ["--help"], ["--template", "desk.uma"]):
        done = message("seat", "agent", *argv, env=bare)
        assert done.stdout or done.stderr, argv
        printed += [done.stdout, done.stderr]
    for out in printed:
        assert not ORG_WORD.search(out.decode()), out.decode()


def test_the_org_word_pattern_catches_any_case():
    """The pin above would pass vacuously on a pattern that matched nothing."""
    for word in ("fail" "mode", "Fail" "Mode", "com." "fail" "mode.seat-agent.x"):
        assert ORG_WORD.search(word), word


def test_the_template_is_valid_xml_its_comment_holds_no_double_hyphen():
    """`--` ends nothing in an XML comment and is illegal inside one: a header that
    names the flag literally would make every rendered plist fail to load."""
    comment = TEMPLATE.read_text().split("<!--", 1)[1].split("-->", 1)[0]
    assert "--" not in comment


# ── the door: exec, signals restored, a broken install refused ──────────────

@pytest.mark.parametrize("broken", ["missing", "not-executable"])
def test_a_broken_install_exits_2_and_starts_nothing(tmp_path, bare, broken):
    agent = tmp_path / "seat-agent"
    if broken == "not-executable":
        agent.write_text("#!/bin/bash\ntouch \"$HOME/ran\"\n")
        agent.chmod(0o644)
    proc = door(agent, "desk.uma", env=bare)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 2, err
    assert "a broken sessionmail install" in err and "Nothing was started" in err
    assert str(agent) in err and out == ""
    assert not (tmp_path / "ran").exists()


PROBE = """#!/bin/bash
echo "pid=$$"
yes | head -1 >/dev/null
echo "pipe=${PIPESTATUS[0]}"
(ulimit -f 1; dd if=/dev/zero of="$HOME/big" bs=1024 count=8 2>/dev/null)
echo "xfsz=$?"
echo "args=$*"
"""


def test_the_door_execs_with_sigpipe_and_sigxfsz_restored(tmp_path, bare):
    """design.md §1, measured there: through an exec that did not restore them, bash
    inherits CPython's ignores. `yes | head -1` then exits 1 ("Broken pipe"), not 141
    (killed by SIGPIPE), and `dd` past `ulimit -f` gets EFBIG (1), not 153 (SIGXFSZ).
    The same pid is the exec: the script reports the pid the door was started as."""
    agent = tmp_path / "seat-agent"
    agent.write_text(PROBE)
    agent.chmod(0o755)
    proc = door(agent, "desk.uma", "/some/sock", env=bare)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    said = dict(line.split("=", 1) for line in out.splitlines())
    assert said["pid"] == str(proc.pid), "an exec, not a child: launchd's pid is bash's"
    assert said["pipe"] == "141", said
    assert said["xfsz"] == str(128 + 25), said
    assert said["args"] == "desk.uma /some/sock", "the arguments go through as typed"


def test_the_signal_probe_can_say_no(tmp_path, bare):
    """The instrument above, shown failing: the same script started from Python
    WITHOUT the restore inherits both ignores."""
    agent = tmp_path / "seat-agent"
    agent.write_text(PROBE)
    agent.chmod(0o755)
    done = subprocess.run([str(agent)], env=bare, capture_output=True, text=True,
                          restore_signals=False, timeout=30, check=False)
    said = dict(line.split("=", 1) for line in done.stdout.splitlines())
    assert said["pipe"] != "141" and said["xfsz"] != "153", said


def test_init_hands_every_argument_to_the_minter(bare, tmp_path):
    """Click parses nothing for `init`: no --registry is argparse's usage error (2),
    a registry that is not one is the minter's refusal (2, nothing written), and an
    option after one click does not know reaches argparse, whose help answers."""
    done = message("seat", "init", "desk.uma", env=bare)
    assert done.returncode == 2 and b"the following arguments are required: --registry" in done.stderr
    done = message("seat", "init", "--registry", str(tmp_path / "none"), "desk.uma", env=bare)
    assert done.returncode == 2 and b"Nothing was written" in done.stderr
    assert not (tmp_path / "sm").exists()
    done = message("seat", "init", "--registry", "x", "--help", env=bare)
    assert done.returncode == 0 and done.stdout.startswith(b"usage: message seat init ")


def test_agent_with_no_arguments_is_the_wrappers_usage_refusal(bare):
    """Success criterion: `message seat agent` with no args is rc 2, usage."""
    done = message("seat", "agent", env=bare)
    assert done.returncode == 2
    assert b"message seat agent: REFUSED - usage: message seat agent ADDRESS [SOCKET]" in done.stderr
    assert os.access(WRAPPER, os.X_OK)
