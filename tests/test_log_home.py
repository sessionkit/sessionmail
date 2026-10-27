"""sessionmail's log home: ONE resolver, `_settings.log_dir`, and the
seat agent's read fallback to its pre-0.3.2 log.

The kit keeps its own logs: they live in
`~/.sessionkit/mail/log/`, beside `~/.sessionkit/mail/settings/`, and never
in `~/Library/Logs`. A seat agent installed before 0.3.2 keeps writing
`~/Library/Logs/seat-agent.<ADDRESS>.log` until it is uninstalled and
installed again, so the one reader, `_launchd._log_tail`, falls back to it.
That fallback is dropped in 0.3.3.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from sessionmail import _launchd, _settings

SRC = Path(__file__).resolve().parents[1] / "src" / "sessionmail"


# ── the resolver ────────────────────────────────────────────────────────────

def test_the_log_dir_is_the_packages_beside_its_settings(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert _settings.LOG_DIR == Path(".sessionkit") / "mail" / "log"
    assert _settings.LOG_DIR.parent == _settings.DIR.parent, "package first, log/ beside settings/"
    assert _settings.log_dir(env) == tmp_path / ".sessionkit" / "mail" / "log"
    assert _settings.SHOWN_LOG_DIR == "~/.sessionkit/mail/log/"
    assert _settings.old_log_dir(env) == tmp_path / "Library" / "Logs"


def test_the_log_dir_is_never_under_sessionmail_home(tmp_path):
    """Logs are not state: SESSIONMAIL_HOME (state, 0.3.3) does not move them."""
    env = {"HOME": str(tmp_path / "u"), "SESSIONMAIL_HOME": str(tmp_path / "h")}
    assert _settings.log_dir(env) == tmp_path / "u" / ".sessionkit" / "mail" / "log"


def test_make_log_dir_makes_it_0700_whatever_the_umask(tmp_path):
    env = {"HOME": str(tmp_path)}
    old = os.umask(0o000)
    try:
        d = _settings.make_log_dir(env)
    finally:
        os.umask(old)
    assert d == _settings.log_dir(env) and d.is_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    other = {"HOME": str(tmp_path / "v")}
    (tmp_path / "v" / ".sessionkit" / "mail").mkdir(parents=True)
    old = os.umask(0o277)               # strips the owner's own bits: mkdir alone gives 0400
    try:
        d2 = _settings.make_log_dir(other)
    finally:
        os.umask(old)
    assert stat.S_IMODE(d2.stat().st_mode) == 0o700


def test_make_log_dir_leaves_an_existing_dir_and_its_mode_alone(tmp_path):
    env = {"HOME": str(tmp_path)}
    d = _settings.log_dir(env)
    d.mkdir(parents=True)
    d.chmod(0o750)
    (d / "kept.log").write_text("kept\n")
    assert _settings.make_log_dir(env) == d
    assert stat.S_IMODE(d.stat().st_mode) == 0o750
    assert (d / "kept.log").read_text() == "kept\n"


def test_make_log_dir_on_a_file_in_its_place_raises(tmp_path):
    env = {"HOME": str(tmp_path)}
    d = _settings.log_dir(env)
    d.parent.mkdir(parents=True)
    d.write_text("not a dir")
    with pytest.raises(OSError):
        _settings.make_log_dir(env)
    assert d.read_text() == "not a dir"


# ── no other file spells the path ───────────────────────────────────────────

#: Hand-written spellings of either log home: the new one as a path or as
#: path parts, and the old one the same two ways.
SPELLINGS = (
    re.compile(r"\.sessionkit/mail/log"),
    re.compile(r"Library/Logs"),
    re.compile(r"""["']Logs["']"""),
    re.compile(r"""["']mail["']\s*,\s*["']log["']"""),
)


def test_no_file_in_src_but_settings_spells_a_log_home():
    """ONE resolver. A second spelling is how a move misses one."""
    hits = []
    for p in sorted(SRC.rglob("*")):
        if not p.is_file() or p.name == "_settings.py" or p.suffix in (".pyc",):
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(rx.search(line) for rx in SPELLINGS):
                hits.append(f"{p.relative_to(SRC)}:{n}: {line.strip()}")
    assert hits == []


# ── the seat agent's job and its log reader ─────────────────────────────────

def job(tmp_path: Path) -> _launchd.Job:
    new = tmp_path / ".sessionkit" / "mail" / "log"
    old = tmp_path / "Library" / "Logs"
    return _launchd.Job(address="bot.alice", label=_launchd.label_for("bot.alice"),
                        plist="/x/p.plist", socket="/x/a.sock", pub="/x/seat.pub",
                        key="/x/id_ed25519", log=str(new / "seat-agent.bot.alice.log"),
                        log_dir=str(new), old_log=str(old / "seat-agent.bot.alice.log"),
                        sessionmail_home="/x/h", message="/x/bin/message", domain="gui/501")


def test_log_tail_reads_the_new_log_and_names_it(tmp_path):
    j = job(tmp_path)
    Path(j.log).parent.mkdir(parents=True)
    Path(j.log).write_text("".join(f"new {i}\n" for i in range(20)))
    Path(j.old_log).parent.mkdir(parents=True)
    Path(j.old_log).write_text("old\n")
    said = _launchd._log_tail(j)
    assert said.splitlines()[0] == f"the last lines of {j.log}:"
    assert said.splitlines()[1:] == [f"  new {i}" for i in range(12, 20)]
    assert j.old_log not in said and "pre-0.3.2" not in said


def test_log_tail_falls_back_to_the_pre_032_log_and_says_so(tmp_path):
    """An agent installed before 0.3.2 still writes ~/Library/Logs. Dropped in 0.3.3."""
    j = job(tmp_path)
    Path(j.old_log).parent.mkdir(parents=True)
    Path(j.old_log).write_text("message seat agent: FAILED - the old agent said\n")
    said = _launchd._log_tail(j)
    assert said == (f"the last lines of {j.old_log} (the pre-0.3.2 log: no log yet at the "
                    f"new path):\n  message seat agent: FAILED - the old agent said")
    assert Path(j.old_log).read_text().endswith("said\n"), "a read, never a move"
    assert not Path(j.log).exists()


def test_log_tail_with_no_log_anywhere_names_both_paths(tmp_path):
    j = job(tmp_path)
    assert _launchd._log_tail(j) == f"(no log at {j.log}, nor at the pre-0.3.2 {j.old_log})"


def test_log_tail_with_an_unreadable_new_log_says_so_and_does_not_fall_back(tmp_path):
    j = job(tmp_path)
    Path(j.log).mkdir(parents=True)                 # a dir where the file goes: IsADirectory
    Path(j.old_log).parent.mkdir(parents=True)
    Path(j.old_log).write_text("old\n")
    said = _launchd._log_tail(j)
    assert said.startswith(f"(could not read {j.log}: ")
