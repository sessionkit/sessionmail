"""SM-875: the operator block is read from the kit's home, version-agnostic.

The `session` tool's settings (the operator block) live in
`~/.sessionkit/tools/settings/session.json`, sessiontools' package folder
for the `session` tool (ST-202's map: session -> tools). sessionmail names
that folder itself (`_operator.DIR`). It was written when the floor was
sessiontools 0.1.0, which has no `host_dir` and no `PACKAGES`, and whose
settings root is the old `~/.settings`, which sessiontools writes no longer.
The floor is now 0.3.0, which has the map.

Red before the fix (sessionmail a7818e8): `_operator._tool_dir` built the
session folder as `st.settings_root(env) / "session"`, `~/.settings/session/`,
so a fresh host read an absent file and ran on the default operator, while a
host that kept an old file there was read from it.

The AGREEMENT tests hold sessionmail's two folders to sessiontools' map.
They RUN when the installed sessiontools has the map (`PACKAGES` and
`host_dir`, sessiontools master, 0.2.0 on) and SKIP LOUDLY otherwise: the
skip reason and a warning name the installed version and the missing name.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import warnings
from pathlib import Path

import pytest

from sessionmail import _operator, _settings

#: Spelled out, not taken from `_operator`, so the test does not follow the
#: code it tests.
NEW_DIR = Path(".sessionkit") / "tools" / "settings"
NEW_SHOWN = "~/.sessionkit/tools/settings/session.json"
OLD_DIR = Path(".settings") / "session"

BLOCK = {"operator": {"mailbox": "opr", "address": "opr@hq.example", "privileged": ["ops"]}}
OLD_BLOCK = {"operator": {"mailbox": "stale", "privileged": ["old"]}}

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A settings home with nothing in it: no new file, no old one."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(_operator, "settings_home", lambda: h)
    return h


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")
    return path


# ── where the folder is ─────────────────────────────────────────────────────

def test_the_session_folder_is_the_kit_home(home):
    assert _operator.DIR == NEW_DIR
    assert _operator._tool_dir("session") == home / NEW_DIR
    assert _operator.KEY_FILE == NEW_SHOWN


def test_the_mail_folder_is_unchanged(home):
    assert _operator._tool_dir("mail") == _settings.path({"HOME": str(home)}).parent


def test_both_folders_share_the_kit_root(home):
    """One kit root, `_settings.ROOT`: the two folders cannot drift apart."""
    assert _operator.DIR.parts[0] == _settings.ROOT.as_posix()
    assert _operator._tool_dir("session").parent.parent == _operator._tool_dir("mail").parent.parent


def test_another_tool_has_no_folder_here(home):
    """No guessed folder for a tool sessionmail does not read."""
    with pytest.raises(ValueError, match="ears"):
        _operator._tool_dir("ears")
    assert _operator.files("ears") == ()


# ── the read ─────────────────────────────────────────────────────────────────

def test_the_operator_is_read_from_the_new_path(home):
    _write(home / NEW_DIR / "session.json", BLOCK)
    op = _operator.read()
    assert op.configured and op.problem is None, op
    assert (op.mailbox, op.operator) == ("opr", "opr@hq.example")
    assert op.privileged == ("opr", "opr@hq.example", "ops")


def test_a_file_at_the_old_path_is_never_read(home):
    """No fallback: an operator block left in `~/.settings/session/` decides
    nothing. With no new file the host has NO block, so the operator is the
    default (the account), which privileges nobody."""
    _write(home / OLD_DIR / "session.json", OLD_BLOCK)
    op = _operator.read()
    assert not op.configured and op.privileged == (), op
    assert op.default == _operator.account_name()


def test_the_new_path_wins_over_an_old_file(home):
    _write(home / OLD_DIR / "session.json", OLD_BLOCK)
    _write(home / NEW_DIR / "session.json", BLOCK)
    assert _operator.read().mailbox == "opr"


def test_drop_ins_and_template_are_read_from_the_new_folder(home):
    _write(home / NEW_DIR / "session.template.json", {"operator": {"mailbox": "tmpl"}})
    _write(home / NEW_DIR / "session.d" / "50-x.json", {"operator": {"privileged": ["drop"]}})
    srcs = _operator.host_sources("session")
    assert [s.path for s in srcs] == [home / NEW_DIR / "session.template.json",
                                      home / NEW_DIR / "session.d" / "50-x.json"]
    op = _operator.read()
    assert op.mailbox == "tmpl" and "drop" in op.privileged


def test_the_watcher_watches_the_new_folder_only(home):
    _write(home / NEW_DIR / "session.d" / "50-x.json", {})
    _write(home / OLD_DIR / "session.d" / "50-x.json", {})
    watched = _operator.files("session")
    assert watched and all(p.is_relative_to(home / NEW_DIR) for p in watched), watched


def test_a_bad_file_is_named_by_the_new_path(home):
    """The refusal names the file the operator must fix, as a `~` path."""
    _write(home / NEW_DIR / "session.json", {}).write_text("{not json")
    op = _operator.read()
    assert not op.configured and op.problem and NEW_SHOWN in op.problem, op.problem
    assert str(home) not in op.problem
    assert NEW_SHOWN in _operator.who_may(op) and ".settings/" not in _operator.who_may(op)


def test_nothing_under_the_old_tree_is_made(home):
    _operator.read()
    _operator.files("session")
    _operator.files("mail")
    assert not (home / ".settings").exists()


def test_no_source_calls_the_old_settings_root():
    """Zero `settings_root` in src/: the old tree is not read through
    sessiontools either."""
    bad = [f"{p.relative_to(ROOT)}:{n}"
           for p in sorted((ROOT / "src" / "sessionmail").rglob("*.py"))
           for n, line in enumerate(p.read_text().splitlines(), 1)
           if "settings_root" in line]
    assert bad == [], bad


def test_no_source_names_the_old_session_home():
    old = re.compile(r"\.settings/session\b")
    bad = []
    for p in sorted((ROOT / "src" / "sessionmail").rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        bad += [f"{p.relative_to(ROOT)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                if old.search(line)]
    assert bad == [], bad


# ── agreement with sessiontools' map (runs only where the map exists) ───────

def _map_or_skip():
    """sessiontools.settings, when it has `PACKAGES` and `host_dir`; else a
    LOUD skip: a warning and a skip reason naming the installed version and
    the missing name."""
    from sessiontools import settings as st
    missing = [n for n in ("PACKAGES", "host_dir") if not hasattr(st, n)]
    if missing:
        try:
            version = importlib.metadata.version("sessiontools")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        why = (f"SM-875 agreement NOT CHECKED: installed sessiontools {version} "
               f"has no {' or '.join(missing)} (sessiontools.settings); runs against "
               f"a sessiontools with the map (0.2.0 on)")
        warnings.warn(why, stacklevel=2)
        pytest.skip(why)
    return st


@pytest.mark.parametrize("tool", ["mail", "session"])
def test_the_folder_agrees_with_sessiontools_map(tool, home):
    st = _map_or_skip()
    env = {"HOME": str(home)}
    assert _operator._tool_dir(tool) == st.host_dir(tool, env)[0]


def test_the_mail_path_agrees_with_sessiontools_map(home):
    st = _map_or_skip()
    env = {"HOME": str(home)}
    assert _settings.path(env).parent == st.host_dir("mail", env)[0]


def test_the_session_package_agrees_with_sessiontools_map():
    st = _map_or_skip()
    assert st.PACKAGES["session"] == _operator.PACKAGE
    assert st.PACKAGES["mail"] == _settings.PACKAGE
