"""memory wake must surface a brief addressed to the seat that rests in the PROJECT box.

A plus-grammar task seat (`<project>+<task>.<role>`) has no alias row of its own
until it listens, so mail to its exact address can rest in the project's BASE box.
Its boot readout (`memory wake`) is the one place it would see that mail.

THE SHAPE THAT MISSES, as measured on a live host: the project box is NESTED,
`<root>/<store>/<project>/`, under a store directory carrying the `.tm-store`
marker, and that is where the brief rests. Beside it sits a STALE FLAT box of the
same name, `<root>/<project>/`, holding a few old letters to other seats. The
box rail (mail-box.lib.sh) resolves a name FLAT FIRST, so wake reads the stale
flat box and reports "0 message(s) addressed exactly to <seat>", while the brief
sits in the nested box. Mail is delivered to the nested box when the store's name
sorts before the project's (`<store>/<project>/` sorts ahead of `<project>/` in
the generated map, and the first line wins): here `kit-store` sorts before `mail`,
as the live store does before its projects.

The controls are green today and must stay so: a seat whose brief rests in its own
session box, and a seat whose project box exists in one place only.

Hermetic: the mail root, HOME and PATH are under tmp_path; `notmuch` is a stub that
answers nothing, so RECENT reads empty and the readout rests on the file scan.
Nothing is sent; the live mail root is never read.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs

WAKE = Path(_mhs.MHS_DIR) / "session-mail-wake"

PROJECT = "mail"
STORE = "kit-store"            # sorts before PROJECT: the nested box is the delivered one
SEAT = f"{PROJECT}+ab-12.foreman"
SID = "7ed6b16b-31bd-fe66-35a0-284b56bbe9ec"
HOST = "wake.invalid"
DATE = "Sun, 09 Jun 2030 12:00:00 +0000"
BRIEF_MID = "brief.0001@wake.invalid"
BRIEF_SUBJECT = "ab-12 fix (high): read the README, then the brief"


def letter(mid: str, x_original_to: str, delivered_to: str, to: str, subject: str) -> str:
    """A delivered letter as postfix writes it: the stamps first, then the headers."""
    return (f"Return-Path: <kick@{HOST}>\n"
            f"X-Original-To: {x_original_to}\n"
            f"Delivered-To: {delivered_to}\n"
            f"From: Kick <kick@{HOST}>\n"
            f"To: {to}\n"
            f"Date: {DATE}\n"
            f"Subject: {subject}\n"
            f"Message-ID: <{mid}>\n"
            "\n"
            "The brief body.\n")


def maildir(d: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def drop(box: Path, name: str, text: str) -> None:
    (box / "new" / name).write_text(text)


def brief_to_seat() -> str:
    """The kick brief, To the seat's exact address, delivered to the base box."""
    return letter(BRIEF_MID, SEAT, f"{PROJECT}@{HOST}", f"{SEAT}@{HOST}", BRIEF_SUBJECT)


def nested_project_box(root: Path) -> Path:
    store = root / STORE
    store.mkdir(parents=True)
    (store / ".tm-store").write_text("")
    return maildir(store / PROJECT)


def stale_flat_box(root: Path) -> Path:
    """The flat box of the same name, holding old letters to OTHER seats only."""
    flat = maildir(root / PROJECT)
    for i in range(3):
        drop(flat, f"old.{i}", letter(f"old.{i}@wake.invalid", f"{PROJECT}+super",
                                      f"{PROJECT}@{HOST}", f"{PROJECT}+super@{HOST}",
                                      f"an old letter {i} to the super"))
    return flat


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """An environment built from nothing: the seat's address, a temp root, a stub notmuch."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    notmuch = bin_dir / "notmuch"
    notmuch.write_text("#!/bin/sh\nexit 0\n")
    notmuch.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "mail").mkdir()
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home),
            "SESSION_MAIL_ROOT": str(tmp_path / "mail"),
            "SESSIONMAIL_PYTHON": sys.executable}


def wake(env: dict[str, str], **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WAKE)], capture_output=True, text=True,
                          timeout=120, env={**env, **extra})


def seat_count(out: str, box: str) -> int:
    marker = f" message(s) addressed exactly to {box} "
    for line in out.splitlines():
        if marker in line:
            return int(line.split()[0])
    raise AssertionError(f"no seat count line for {box} in:\n{out}")


def surfaced(out: str, box: str) -> bool:
    """The brief is surfaced if the readout names it, or counts it as the seat's."""
    return BRIEF_MID in out or BRIEF_SUBJECT in out or seat_count(out, box) >= 1


# ── the miss ────────────────────────────────────────────────────────────────────────────

def test_a_stale_flat_box_does_not_hide_a_brief_resting_in_the_nested_project_box(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    stale_flat_box(root)
    drop(nested_project_box(root), "brief.1", brief_to_seat())
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    assert r.returncode == 0, r.stderr
    assert surfaced(r.stdout, SEAT), (
        "memory wake shows nothing of a brief To the seat's exact address that rests "
        f"in the nested project box {STORE}/{PROJECT}; it read the stale flat box:\n"
        + r.stdout)


# ── the controls: green today, and they stay green ────────────────────────────────────────

def test_a_brief_in_the_seats_own_session_box_is_counted(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    stale_flat_box(root)
    nested_project_box(root)
    drop(maildir(root / SID), "brief.1",
         letter(BRIEF_MID, SID, f"{SID}@{HOST}", f"{SID}@{HOST}", BRIEF_SUBJECT))
    r = wake(env, CLAUDE_CODE_SESSION_ID=SID)
    assert r.returncode == 0, r.stderr
    assert seat_count(r.stdout, SID) == 1, r.stdout


@pytest.mark.parametrize("layout", ["flat", "nested"])
def test_a_brief_in_a_project_box_that_exists_once_is_counted(env, layout):
    root = Path(env["SESSION_MAIL_ROOT"])
    box = maildir(root / PROJECT) if layout == "flat" else nested_project_box(root)
    drop(box, "brief.1", brief_to_seat())
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    assert r.returncode == 0, r.stderr
    assert seat_count(r.stdout, SEAT) == 1, r.stdout
