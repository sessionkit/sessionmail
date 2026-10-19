"""SM-715 do-001: seat-init and seat-agent moved into the package, with shims behind.

design.md §3-§4, §6, §7 row do-001. The three SM-590 files moved VERBATIM:

    scripts/seat-init                            -> src/sessionmail/seat_init.py
    scripts/seat-agent/seat-agent                -> src/sessionmail/seat/seat-agent
    scripts/seat-agent/seat-agent.plist.template -> src/sessionmail/seat/seat-agent.plist.template

The SM-590 tests (test_seat_init, test_seat_agent, test_seat_acceptance) run and read
the packaged files. This file pins what the move itself added: the package data's
executable bits, both artifacts lines, nothing new on PATH, and a smoke run through
each shim at the old paths (SM-590's runbook still names them; design §4, Q-B).
Everything is scratch: a tmp registry, a /tmp $SESSIONMAIL_HOME, no launchctl.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
from test_seat_agent import ADDRESS, agents_on, answers, minted, short_home  # noqa: F401
from test_seat_init import git_env, registry, tools  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "sessionmail"
SEAT_INIT = PKG / "seat_init.py"
WRAPPER = PKG / "seat" / "seat-agent"
TEMPLATE = PKG / "seat" / "seat-agent.plist.template"
INIT_SHIM = REPO / "scripts" / "seat-init"
AGENT_SHIM = REPO / "scripts" / "seat-agent" / "seat-agent"


def pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text())


# ── the package data ─────────────────────────────────────────────────────────

def test_the_seat_files_ship_as_package_data_and_keep_their_exec_bits():
    """As test_postfix_vmailbox_gen pins vmailbox-gen's: the executable bit is part of the move."""
    for script, shebang in ((WRAPPER, "#!/bin/bash\n"), (SEAT_INIT, "#!/usr/bin/env python3\n")):
        assert script.is_file(), script
        assert os.access(script, os.X_OK), f"{script.name}: the executable bit is part of the move"
        assert script.read_text().startswith(shebang)
    assert TEMPLATE.is_file() and TEMPLATE.read_text().startswith("<?xml")


def test_both_artifacts_lines_name_the_seat_dir():
    build = pyproject()["tool"]["hatch"]["build"]["targets"]
    assert build["wheel"]["packages"] == ["src/sessionmail"]
    for target in ("wheel", "sdist"):
        assert "src/sessionmail/seat" in build[target]["artifacts"], target


def test_nothing_new_is_on_path():
    """The package spec's three commands, exactly (ruling (b))."""
    assert set(pyproject()["project"]["scripts"]) == {"message", "pager", "memory"}


# ── the shims at the old paths ───────────────────────────────────────────────

def run(cmd, env_, timeout=60):
    done = subprocess.run([str(c) for c in cmd], env=env_, capture_output=True, text=True,
                          timeout=timeout, check=False)
    return done.returncode, done.stdout, done.stderr


def test_the_seat_init_shim_answers_help_and_refuses_as_the_module_does(git_env, tmp_path):  # noqa: F811
    rc, out, _ = run([INIT_SHIM, "--help"], git_env)
    assert rc == 0 and out.startswith("usage: message seat init ") and "--registry" in out
    rc, _, err = run([INIT_SHIM, ADDRESS], git_env)
    assert rc == 2 and "--registry" in err, "--registry keeps no default"
    rc, _, err = run([INIT_SHIM, "--registry", tmp_path / "not-a-registry", ADDRESS], git_env)
    assert rc == 2 and "Nothing was written" in err
    assert not (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys").exists()


def test_a_mint_through_the_seat_init_shim(git_env, registry):  # noqa: F811
    rc, out, err = run([INIT_SHIM, "--registry", registry, ADDRESS], git_env)
    assert rc == 0, err
    assert f"{ADDRESS} minted, and NOT blessed" in out
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / ADDRESS
    assert (home / "id_ed25519").is_file() and (home / "seat.pub").is_file()
    assert not list(registry.glob("certs/*blessed-by*")), "seat-init never blesses"
    rc, _, err = run([INIT_SHIM, "--registry", registry, ADDRESS], git_env)
    assert rc == 2, "a second mint is refused, through the shim as directly"


def test_the_seat_agent_shim_passes_its_arguments_and_refusals_through(tmp_path):
    env_ = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "SESSIONMAIL_HOME": str(tmp_path / "h")}
    rc, _, err = run([AGENT_SHIM], env_)
    assert rc == 2 and "message seat agent: REFUSED - usage: message seat agent ADDRESS [SOCKET]" in err
    rc, _, err = run([AGENT_SHIM, "uma+desk"], env_)
    assert rc == 2 and "has a '+'" in err
    rc, _, err = run([AGENT_SHIM, ADDRESS], env_)
    assert rc == 2 and f"no key home {tmp_path / 'h'}/seat-keys/{ADDRESS}" in err


def test_the_seat_agent_shim_execs_so_term_stops_the_agent(minted, tools):  # noqa: F811
    """The shim EXECs: the pid started is the wrapper's own, so TERM gives the wrapper's
    rc 0, its socket removed and no agent left. A shim that ran the wrapper as a
    child would die of the TERM itself (rc -15) and leave the agent running."""
    with open(minted["home"].parent.parent / "shim.log", "w+") as log:
        proc = subprocess.Popen([str(AGENT_SHIM), ADDRESS], env=minted["env"], stdout=log, stderr=log)
        try:
            for _ in range(100):
                if answers(minted["sock"]) == 0 or proc.poll() is not None:
                    break
                time.sleep(0.1)
            log.seek(0)
            assert answers(minted["sock"]) == 0, log.read()
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(10) == 0
            assert not os.path.exists(minted["sock"])
            assert agents_on(minted["sock"]) == []
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)


@pytest.mark.parametrize("shim", [INIT_SHIM, AGENT_SHIM], ids=["seat-init", "seat-agent"])
def test_a_shim_whose_target_is_gone_refuses_with_2(tmp_path, shim):
    """A shim copied away from the package finds nothing to run, and says so."""
    lonely = tmp_path / "repo" / shim.relative_to(REPO)
    lonely.parent.mkdir(parents=True)
    shutil.copy2(shim, lonely)
    cmd = [sys.executable, lonely] if shim == INIT_SHIM else [lonely]
    rc, _, err = run([*cmd, ADDRESS], {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert rc == 2 and "REFUSED" in err and "is missing" in err, err
