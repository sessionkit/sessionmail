"""SM-780, fix (c), the interim: `memory run` is OFF by default and not an MCP tool.

Why: membership checks a mail against the key that mail carries, so anyone
who can deliver to a box could sign an item with a fresh key, forge From:
<owner>, and have it run as the owner (SK-80 B1). SM-785 (fix (a)) makes
`run` ask whose key it is (`--trust`; tests/test_memory_run_trust.py); the
opt-in and the MCP exclusion stay until they are ruled away.

NOTHING HERE RUNS `memory run` FOR REAL (SM-530). The click door is driven
against a fake oracle that only logs its argv. The executor is driven by
path with PATH holding only fakes (`sh`, `bash`, `python3`, `notmuch`,
`tokens`) that log and exit 0, and in a sandbox HOME: the refusal must come
before any of them is reached, and `--list` (which runs nothing) is the
instrument check that the fakes would have seen a call.

Pinned:
  - `memory_run` is not an MCP tool, by an explicit exclusion: `run` stays on
    the CLI page (not hidden), and the page's other verbs are still tools.
  - Both doors refuse without the opt-in (exit 4, nothing reached), and the
    opt-in is exactly "1". `--list` needs no opt-in at either door.
  - The two doors spell the gate the same way.
  - The help pages, the MCP instructions, the skill and the unsigned-send
    warning say the signer must pass --trust (SM-785), and none still says
    "the signer is NOT checked" (true under SM-780, false since).
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, _mhs, memory

REPO = Path(__file__).resolve().parent.parent
EXECUTOR = Path(_mhs.MHS_DIR) / "session-mail-run"
GATE = "SESSIONMAIL_MEMORY_RUN"          # written here, not read from _mhs
TRUSTED = "passes --trust"                   # SM-785: what every page now says
STALE = "signer is NOT checked"              # SM-780's words: false since SM-785
OLD_PROMISE = "signed by the box's owner"

FAKE_ORACLE = """#!{python}
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
sys.stdout.write("oracle says: " + " ".join(sys.argv[1:]) + "\\n")
"""


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    fake = tmp_path / "fake-mhs"
    fake.write_text(FAKE_ORACLE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(fake))
    monkeypatch.setenv("FAKE_LOG", str(log))

    class O:
        def calls(self):
            return [l.split("\x1f") for l in log.read_text().splitlines()] if log.exists() else []
    return O()


def invoke(*args):
    return CliRunner().invoke(memory.cli, list(args), prog_name="memory")


# ── the MCP surface ──────────────────────────────────────────────────────

def test_memory_run_is_not_an_mcp_tool():
    names = [n for n, _fn, _d in _mcp.tools(memory.cli, "memory")]
    assert "memory_run" not in names, names
    assert names == ["memory_ls", "memory_tree", "memory_cat", "memory_wake", "memory_init"], names


def test_the_served_server_has_no_memory_run_tool():
    server = _mcp.server(memory.cli, "memory")
    assert server._tool_manager.get_tool("memory_run") is None
    assert server._tool_manager.get_tool("memory_ls") is not None


def test_run_is_excluded_by_name_not_hidden_so_the_cli_page_still_shows_it():
    assert ("memory", "run") in _mcp.NOT_A_TOOL
    assert memory.cli.commands["run"].hidden is False
    page = invoke("-h").output
    assert re.search(r"^  run\s+PATH", page, re.M), page


def test_the_exclusion_is_scoped_to_memory():
    """A `run` verb on another command would still be a tool: the list
    names (command, verb), never a bare verb. memory's ONE withheld verb is
    `run`; any other command's entry is a noun's verb (`box archive`,
    SM-790), never a bare `run`. One named exception: `message setup`."""
    assert [v for g, v in _mcp.NOT_A_TOOL if g == "memory"] == ["run"]
    # a bare verb kept off the MCP tools by design: setup is a host owner's act
    bare_by_design = {("message", "setup")}
    assert bare_by_design <= _mcp.NOT_A_TOOL.keys()
    assert all(" " in v for g, v in _mcp.NOT_A_TOOL
               if g != "memory" and (g, v) not in bare_by_design)


# ── the click door ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1", "1 ", "2"])
def test_the_click_door_refuses_without_the_opt_in(oracle, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(GATE, raising=False)
    else:
        monkeypatch.setenv(GATE, value)
    r = invoke("run", "/BOOT/WAKE")
    assert r.exit_code == 4, r.output
    assert oracle.calls() == [], "the oracle must not be reached"
    assert TRUSTED in r.stderr and f"{GATE}=1" in r.stderr, r.stderr
    assert STALE not in r.stderr
    assert "Nothing was run" in r.stderr


@pytest.mark.parametrize("extra", [[], ["--json"], ["--only", "10"]])
def test_the_click_door_refuses_every_running_form(oracle, monkeypatch, extra):
    monkeypatch.delenv(GATE, raising=False)
    r = invoke("run", "/BOOT/WAKE", *extra)
    assert r.exit_code == 4 and oracle.calls() == []


def test_the_click_door_passes_with_the_opt_in(oracle, monkeypatch):
    monkeypatch.setenv(GATE, "1")
    r = invoke("run", "/BOOT/WAKE", "--only", "10")
    assert r.exit_code == 0, r.output
    assert oracle.calls() == [["run", "/BOOT/WAKE", "--only", "10"]]


def test_list_needs_no_opt_in_at_the_click_door(oracle, monkeypatch):
    monkeypatch.delenv(GATE, raising=False)
    r = invoke("run", "/BOOT/WAKE", "--list")
    assert r.exit_code == 0, r.output
    assert oracle.calls() == [["run", "/BOOT/WAKE", "--list"]]


def test_run_allowed_is_exactly_one():
    assert _mhs.run_allowed({GATE: "1"}) is True
    for v in ("", "0", "true", "yes", "on", " 1", "1\n", "01"):
        assert _mhs.run_allowed({GATE: v}) is False, repr(v)
    assert _mhs.run_allowed({}) is False


# ── the executor, by path ────────────────────────────────────────────────

FAKES = ("sh", "bash", "python3", "notmuch", "tokens", "gpg", "ssh-keygen")


@pytest.fixture
def fakebin(tmp_path):
    """A PATH of fakes that log their name and argv and exit 0, so anything
    the executor reaches is SEEN and nothing real is run."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "reached.log"
    for name in FAKES:
        f = bin_ / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 0\n')
        f.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()

    class F:
        def env(self, **extra):
            env = {"PATH": str(bin_), "HOME": str(home), "CLAUDE_CODE_AGENT": "nobody",
                   "NOTMUCH_CONFIG": str(tmp_path / "no-notmuch-config")}
            env.update(extra)
            return env

        def reached(self):
            return log.read_text().splitlines() if log.exists() else []
    return F()


def executor(fakebin, *argv, **env):
    return subprocess.run([sys.executable, str(EXECUTOR), *argv], capture_output=True,
                          text=True, env=fakebin.env(**env), timeout=60)


@pytest.mark.parametrize("extra", [[], ["--json"], ["--only", "10"], ["--toml", "/dev/null"]])
def test_the_executor_refuses_by_path_without_the_opt_in(fakebin, extra):
    p = executor(fakebin, "/BOOT/WAKE", *extra)
    assert p.returncode == 4, p.stdout + p.stderr
    assert TRUSTED in p.stderr and f"{GATE}=1" in p.stderr, p.stderr
    assert STALE not in p.stderr
    assert p.stdout == ""
    assert fakebin.reached() == [], "the executor reached a program before refusing"


@pytest.mark.parametrize("value", ["0", "true", " 1"])
def test_the_executor_opt_in_is_exactly_one(fakebin, value):
    p = executor(fakebin, "/BOOT/WAKE", **{GATE: value})
    assert p.returncode == 4 and fakebin.reached() == []


def test_the_executor_refuses_before_it_even_needs_an_agent(fakebin):
    """The gate is the first thing the script does: with no agent in the
    environment it is still the gate that answers, not the agent check."""
    env = fakebin.env()
    del env["CLAUDE_CODE_AGENT"]
    p = subprocess.run([sys.executable, str(EXECUTOR), "/BOOT/WAKE"], capture_output=True,
                       text=True, env=env, timeout=60)
    assert p.returncode == 4 and TRUSTED in p.stderr, p.stderr


def test_list_needs_no_opt_in_at_the_executor_and_the_fakes_can_see(fakebin):
    """The instrument check: `--list` passes the gate, reaches the fakes
    (so a refusal's empty log means something), and runs no item."""
    p = executor(fakebin, "/BOOT/WAKE", "--list")
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("# materialized from"), p.stdout
    assert fakebin.reached(), "the fakes saw nothing: the refusal tests prove nothing"
    assert not any(r.startswith("sh ") for r in fakebin.reached()), fakebin.reached()


def test_main_holds_the_gate_for_an_importer(fakebin, monkeypatch):
    """Imported (as the fork suite imports it), the module-level gate does
    not fire; main() still holds it."""
    for k, v in fakebin.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(GATE, raising=False)
    loader = SourceFileLoader("smr_sm780", str(EXECUTOR))
    spec = importlib.util.spec_from_loader("smr_sm780", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["session-mail-run", "/BOOT/WAKE"])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 4
    assert fakebin.reached() == []


def test_both_doors_spell_the_gate_the_same():
    text = EXECUTOR.read_text()
    m = re.search(r'^RUN_GATE_VAR = "([^"]+)"', text, re.M)
    assert m and m.group(1) == _mhs.RUN_GATE_VAR == GATE
    m = re.search(r"^RUN_OFF_EXIT = (\d+)", text, re.M)
    assert m and int(m.group(1)) == _mhs.RUN_OFF_EXIT == 4


# ── the words ────────────────────────────────────────────────────────────

def test_the_run_page_says_trusted_signers_only_and_opt_in():
    page = invoke("run", "-h").output
    flat = " ".join(page.replace("`", "").split())
    assert "Trusted signers only:" in flat, page
    assert TRUSTED in flat and "UNBLESSED" in flat
    assert "3 an item's signer refused by --trust" in flat
    assert STALE not in flat
    assert f"nothing runs unless {GATE}=1" in flat
    assert "--list runs nothing and needs no opt-in" in flat
    assert "Not an MCP tool" in flat
    assert "4 OFF" in flat
    assert OLD_PROMISE not in flat
    assert "verified" not in page.splitlines()[2], "the summary line promised a check"


def test_the_command_page_and_the_mcp_instructions_say_it_too():
    """`memory --help` IS the MCP server's instructions (`_mcp.server`)."""
    page = invoke("-h").output
    run_line = next(l for l in page.splitlines() if re.match(r"^  run\s", l))
    assert "trusted signers only" in run_line and "verified" not in run_line, run_line
    assert "NOT checked" not in page
    mcp_line = next(l for l in page.splitlines() if re.match(r"^  mcp\s", l))
    assert "but run" in mcp_line, mcp_line
    assert OLD_PROMISE not in page
    assert _mcp.server(memory.cli, "memory").instructions == page


@pytest.mark.parametrize("verb", ["ls", "tree", "cat", "wake"])
def test_no_other_verb_page_claims_the_run_gate(verb):
    """The gate is run's alone: no other verb exits 4 for it."""
    assert GATE not in invoke(verb, "-h").output


def test_the_mcp_page_names_what_is_not_a_tool():
    flat = " ".join(invoke("mcp", "-h").output.split())
    assert "Not a tool: `memory run`" in flat
    assert "never a tool call" in flat and STALE not in flat


def test_the_message_mcp_page_never_gives_memory_runs_reason():
    """`message` withholds its own verbs since SM-790 (`box archive`,
    `box unarchive`), each with ITS reason: run's script-mail sentence must
    never be printed for them (SM-790, review F6)."""
    from sessionmail import message
    out = " ".join(CliRunner().invoke(message.cli, ["mcp", "-h"], prog_name="message").output.split())
    assert "script-mail" not in out and "SM-780" not in out
    assert "Not a tool: `message box archive`, `message box unarchive`." in out


def test_the_skill_and_its_reference_say_trusted_signers_only():
    skill = " ".join((REPO / "skills/memory/SKILL.md").read_text().replace("`", "").split())
    assert "signed items only" not in skill
    assert TRUSTED in skill and f"{GATE}=1" in skill and STALE not in skill
    ref = " ".join((REPO / "skills/memory/references/root-labels-items.md").read_text().replace("`", "").split())
    assert TRUSTED in ref and f"{GATE}=1" in ref and STALE not in ref


def test_the_unsigned_send_warning_no_longer_promises_a_refusal():
    src = (REPO / "src/sessionmail/_sender.py").read_text()
    assert "`memory run` will refuse it" not in src
    assert "a signed one runs only" in src and "if its signer passes --trust" in src
    assert STALE not in src


def test_the_executor_docstring_says_off_and_trusted_signers_only():
    p = subprocess.run([sys.executable, str(EXECUTOR), "--help"], capture_output=True,
                       text=True, env={**os.environ, "CLAUDE_CODE_AGENT": "nobody"}, timeout=60)
    flat = " ".join(p.stderr.split())
    assert "OFF BY DEFAULT" in flat and f"{GATE}=1" in flat
    assert "TRUSTED SIGNERS ONLY: an item runs" in flat and STALE not in flat
