"""The pager monitor in the ROOT plugin, on the PATH pager (SM-726 R1, R6).

The repo-root plugin ships `monitors/monitors.json`, which Claude Code loads
when `plugin.json` has no `monitors` key. Its one command, `hooks/pager-monitor`,
execs `pager listen --monitor` from PATH - the same pager the hooks run - and
never a checkout's virtualenv. The opt-in `plugins/pager-monitor` (SM-678)
is retired: it stays one release so live `--plugin-dir` launches still load,
and arms nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MONITORS = REPO / "monitors" / "monitors.json"
SHIM = REPO / "hooks" / "pager-monitor"
OPT_IN = REPO / "plugins" / "pager-monitor"
#: A PATH with the shell's tools and no `pager` (checked in the test that uses it).
BARE_PATH = "/usr/bin:/bin"


def _fake_pager(directory: Path, said: str = "pid=$$ argv=$*") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "pager"
    fake.write_text(f'#!/bin/sh\necho "{said}"\n')
    fake.chmod(0o755)
    return fake


def _checkout(tmp_path: Path) -> Path:
    """A checkout-shaped tree with the shim at hooks/pager-monitor AND a
    decoy pager in the checkout's virtualenv, where the old opt-in shim
    looked: a shim that reads it says DECOY."""
    root = tmp_path / "checkout"
    shim = root / "hooks" / "pager-monitor"
    shim.parent.mkdir(parents=True)
    shutil.copy2(SHIM, shim)
    _fake_pager(root / ".venv" / "bin", said="DECOY")
    return shim


# ── the root monitors.json ──────────────────────────────────────────────

def test_root_monitors_json_is_one_always_entry_named_pager():
    entries = json.loads(MONITORS.read_text())
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert set(entry) == {"name", "command", "description", "when"}
    assert all(isinstance(v, str) and v for v in entry.values())
    assert entry["name"] == "pager"
    assert entry["when"] == "always"


def test_the_command_execs_the_hooks_shim():
    command = json.loads(MONITORS.read_text())[0]["command"]
    assert command == 'exec "${CLAUDE_PLUGIN_ROOT}/hooks/pager-monitor"'
    # Claude Code substitutes ${CLAUDE_PLUGIN_ROOT}; a ${user_config.*} is
    # refused in a monitor command. Nothing else may be substituted.
    assert command.count("${") == 1
    assert os.access(SHIM, os.X_OK)


def test_plugin_json_has_no_monitors_key_so_the_default_path_loads():
    root = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert "monitors" not in root
    assert "monitors" not in root.get("experimental", {})


def test_the_shim_is_not_in_a_root_bin():
    """Claude Code puts a plugin's bin/ on the seat's Bash PATH."""
    assert not (REPO / "bin" / "pager-monitor").exists()


def test_the_marketplace_entry_is_still_the_root_plugin():
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert [p["source"] for p in market["plugins"]] == ["./"]


# ── the shim: PATH's pager, by exec; DEAF without one ───────────────────

def test_the_shim_execs_paths_pager_with_listen_monitor_never_the_venv(tmp_path):
    shim = _checkout(tmp_path)
    path_dir = tmp_path / "path"
    _fake_pager(path_dir)
    env = {**os.environ, "PATH": f"{path_dir}{os.pathsep}{BARE_PATH}"}
    proc = subprocess.Popen(["bash", "-c", f'exec "{shim}"'], env=env,
                            stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert "DECOY" not in out
    # exec, not a child: the fake pager IS the process bash was started as.
    assert out.strip() == f"pid={proc.pid} argv=listen --monitor"


def test_the_shim_text_never_names_a_venv():
    assert ".venv" not in SHIM.read_text()


def test_no_pager_on_path_says_deaf_on_stdout_and_exits_2(tmp_path):
    for d in BARE_PATH.split(os.pathsep):
        assert not (Path(d) / "pager").exists(), d
    shim = _checkout(tmp_path)       # a checkout venv pager exists: still DEAF
    result = subprocess.run([str(shim)], capture_output=True, text=True, timeout=10,
                            env={**os.environ, "PATH": BARE_PATH})
    assert result.returncode == 2
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith("pager: DEAF - "), result.stdout
    assert "DECOY" not in result.stdout
    assert "`pager listen`" in lines[0]
    assert len(lines[0]) <= 480


# ── the retired opt-in plugin arms nothing ──────────────────────────────

def test_the_opt_in_plugin_arms_no_monitor():
    assert json.loads((OPT_IN / "monitors" / "monitors.json").read_text()) == []
    manifest = json.loads((OPT_IN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "sessionmail-pager-monitor"   # still loads, one release
    assert "monitors" not in manifest
    assert not (OPT_IN / "bin" / "pager-monitor").exists()


def test_the_opt_in_readme_says_retired():
    assert "retired: the root plugin arms the monitor" in (OPT_IN / "README.md").read_text().lower()
