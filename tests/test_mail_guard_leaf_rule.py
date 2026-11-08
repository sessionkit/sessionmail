"""SM-1005 fix-002: the mail-guard's step 4 follows sessiontools' leaf rule.

hooks/mail-guard/hook.py runs on system python3, stdlib only. When no
resolver answers (no importable sessiontools, no `session` verb on PATH),
`session_dir` used to spell `$HOME/.session/sessions/<sid>` outright, so a
denied `message send` on a fresh host birthed `~/.session`. Under
sessiontools' whole-root step d, one stray `~/.session` flips every
sessiontools leaf to OLD for good. Step 4 now copies the rule (sessiontools
`hooks/busy-sound` `leaf()`, with step d's whole-root test):

  a. SESSIONS_DIR set and non-empty -> it (a leading `~/` is $HOME's)
  b. ~/.session/sessions a directory, not -ef the new one -> OLD
  c. ~/.sessionkit/tools/state/sessions a directory -> NEW
  d. neither -> NEW, unless ~/.session is a real directory of its own -> OLD

The five parity cases are sessiontools tests/test_leaf_paths.py's, plus
partial-old (the correction's fifth). Every case runs on a tmp HOME and
reaches step 4: `_sessiontools` answers None and PATH holds no `session`.
The end-to-end cases run the hook on /usr/bin/python3 -I under a scrubbed
env (design-001 §0's guards set, though the hook never reaches the
generator).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "mail-guard" / "hook.py"
SID = "11111111-2222-4333-8444-555555555555"
OLD = Path(".session", "sessions")
NEW = Path(".sessionkit", "tools", "state", "sessions")
#: A subject the shell would damage: denied (rule 4, `message send`).
DENIED = 'message send bob@example.invalid -s "about `uname`" -f body.md'
SYSTEM_PYTHON = Path("/usr/bin/python3")


def _load():
    spec = importlib.util.spec_from_file_location("mail_guard_leaf_rule", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hook(monkeypatch):
    """The hook as system python3 sees it: no sessiontools to import."""
    mod = _load()
    monkeypatch.setattr(mod, "_sessiontools", lambda: None)
    return mod


def env_for(home: Path, **extra) -> dict[str, str]:
    """HOME, and a PATH with no `session` on it: step 4."""
    empty = home.parent / "empty-bin"
    empty.mkdir(exist_ok=True)
    return {"HOME": str(home), "PATH": str(empty), **extra}


def make(layout: str, home: Path) -> None:
    """sessiontools test_leaf_paths.make's layouts, plus partial-old and
    the whole-root links of step d."""
    old, new = home / OLD, home / NEW
    home.mkdir(exist_ok=True)
    if layout == "old-only":
        old.mkdir(parents=True)
    if layout in ("new-only", "migrated"):
        new.mkdir(parents=True)
    if layout == "migrated":
        old.parent.mkdir(parents=True)
        old.symlink_to(new)
    if layout == "partial-old":
        # A live host's shape: ~/.session holds other leaves, not this one.
        (home / ".session" / "state").mkdir(parents=True)
    if layout == "root-link-into-new":
        # ~/.session itself a link onto the new root, no sessions leaf yet.
        (home / NEW.parent).mkdir(parents=True)
        (home / ".session").symlink_to(home / NEW.parent)
    if layout == "root-link-elsewhere":
        (home.parent / "elsewhere").mkdir()
        (home / ".session").symlink_to(home.parent / "elsewhere")
    if layout == "root-link-inside-new":
        # A link to a folder INSIDE the new root is not -ef it: its own.
        (home / NEW.parent / "inside").mkdir(parents=True)
        (home / ".session").symlink_to(home / NEW.parent / "inside")
    if layout == "root-link-to-new-sessions":
        # Not -ef the new root, so its own; but the new sessions leaf is
        # there (the link's target), so step c answers before step d.
        (home / NEW).mkdir(parents=True)
        (home / ".session").symlink_to(home / NEW)
    if layout == "root-file":
        # A plain file is no directory, so no live host.
        (home / ".session").write_text("")


#: Each layout's answer under the corrected rule.
WANT = {
    "old-only": OLD,
    "fresh": NEW,
    "migrated": NEW,
    "new-only": NEW,
    "partial-old": OLD,
    "root-link-into-new": NEW,
    "root-link-elsewhere": OLD,
    "root-link-inside-new": OLD,
    "root-link-to-new-sessions": NEW,
    "root-file": NEW,
}

#: Step d's own test (sessiontools `settings.old_root_real`): is ~/.session
#: a directory of its own? Exact sameness with the new root, no prefix test.
OWN = {
    "fresh": False,
    "root-file": False,
    "root-link-into-new": False,
    "partial-old": True,
    "root-link-elsewhere": True,
    "root-link-inside-new": True,
    "root-link-to-new-sessions": True,
}


@pytest.mark.parametrize("layout", list(OWN))
def test_step_d_asks_exact_sameness_with_the_new_root(hook, tmp_path, layout):
    home = tmp_path / "home"
    make(layout, home)
    assert hook._old_root_is_its_own(str(home / ".session"), str(home / NEW.parent)) is OWN[layout]


@pytest.mark.parametrize("layout", list(WANT))
def test_step_4_is_the_leaf_rule(hook, tmp_path, layout):
    home = tmp_path / "home"
    make(layout, home)
    env = env_for(home)
    assert hook.sessions_root(env) == str(home / WANT[layout])
    assert hook.session_dir(SID, env) == str(home / WANT[layout] / SID)
    assert hook.log_dir(SID, env) == str(home / WANT[layout] / SID / "log" / "mail")


@pytest.mark.parametrize("layout", list(WANT))
def test_finding_the_dir_creates_nothing(hook, tmp_path, layout):
    home = tmp_path / "home"
    make(layout, home)
    before = sorted(p for p in home.rglob("*"))
    hook.log_dir(SID, env_for(home))
    assert sorted(p for p in home.rglob("*")) == before


@pytest.mark.parametrize("layout", list(WANT))
def test_sessions_dir_wins_over_every_layout(hook, tmp_path, layout):
    home = tmp_path / "home"
    make(layout, home)
    sd = tmp_path / "sd"
    assert hook.session_dir(SID, env_for(home, SESSIONS_DIR=str(sd))) == str(sd / SID)


def test_sessions_dir_leading_tilde_is_homes(hook, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = env_for(home, SESSIONS_DIR="~/elsewhere/s")
    assert hook.session_dir(SID, env) == str(home / "elsewhere" / "s" / SID)


def test_an_empty_sessions_dir_is_unset(hook, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert hook.session_dir(SID, env_for(home, SESSIONS_DIR="")) == str(home / NEW / SID)


def test_the_deny_row_on_a_fresh_home_lands_new_and_births_no_session_root(hook, tmp_path):
    """log_deny on a fresh HOME: the row is under the new leaf, and the
    write path (log_dir, log_append's makedirs) leaves no ~/.session."""
    import time
    home = tmp_path / "home"
    home.mkdir()
    payload = {"tool_name": "Bash", "session_id": SID, "tool_input": {"command": "x"}}
    assert hook.log_deny(payload, "mail-guard: no", time.monotonic(), env_for(home)) == 1
    assert (home / NEW / SID / "log" / "mail" / "pager.jsonl").is_file()
    assert not (home / ".session").exists() and not (home / ".session").is_symlink()


# ── end to end: the hook on system python3, nothing importable ───────────

def _scrubbed(home: Path) -> dict[str, str]:
    """env -i with HOME, TMPDIR and PATH set, plus design-001 §0's guards."""
    tmp = home.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    return {"HOME": str(home), "TMPDIR": str(tmp), "PATH": "/usr/bin:/bin",
            "SESSIONMAIL_SENDMAIL": "drop", "SESSIONMAIL_VMAILBOX_GEN": "/usr/bin/true"}


def _deny(home: Path) -> subprocess.CompletedProcess:
    payload = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_use_id": "toolu_01LEAF", "tool_input": {"command": DENIED}}
    return subprocess.run([str(SYSTEM_PYTHON), "-I", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=_scrubbed(home), timeout=60)


needs_system_python = pytest.mark.skipif(not SYSTEM_PYTHON.exists(),
                                         reason="no /usr/bin/python3 on this host")


@needs_system_python
def test_the_scrubbed_env_reaches_step_4(tmp_path):
    """The e2e premise: system python3 imports no sessiontools, and PATH
    holds no `session`, so neither step 1-3 can answer."""
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([str(SYSTEM_PYTHON), "-I", "-c", "import sessiontools"],
                       capture_output=True, text=True, env=_scrubbed(home), timeout=60)
    assert r.returncode != 0, "system python3 imports sessiontools: step 1 answers"
    assert not any(Path(d, "session").exists() for d in ("/usr/bin", "/bin"))


@needs_system_python
def test_a_fresh_host_deny_births_no_session_root(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _deny(home)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    rows = (home / NEW / SID / "log" / "mail" / "pager.jsonl").read_text().splitlines()
    assert [json.loads(x)["via"] for x in rows] == ["pretooluse-deny"]
    assert not (home / ".session").exists() and not (home / ".session").is_symlink()
    born = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert all(p.startswith(".sessionkit") for p in born), born


@needs_system_python
@pytest.mark.parametrize("layout", ["old-only", "partial-old"])
def test_a_host_with_the_old_root_keeps_writing_old(tmp_path, layout):
    home = tmp_path / "home"
    make(layout, home)
    r = _deny(home)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert (home / OLD / SID / "log" / "mail" / "pager.jsonl").is_file()
    assert not (home / ".sessionkit").exists(), "a live host is not moved"
