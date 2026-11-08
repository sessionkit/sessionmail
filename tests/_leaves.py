"""Where sessiontools keeps a HOME's `sessions` and `seats` leaves, for the
tests that name a session's or a seat's folder.

A test names those folders by sessiontools' own resolver
(`settings.sessions_root` / `settings.seats_root`, the leaf rule), never by
spelling `$HOME/.session/...` or `~/.sessionkit/...`: the resolver is the
one rule the product follows, and its answer for a fresh HOME is not fixed
(a release may move it from the old tree to the new one).

Asking the resolver AFTER the code under test has run would follow wherever
that code wrote: a write into the wrong tree makes that tree a real folder,
and the rule then picks it, so the test would read back the wrong write and
pass. So each HOME's answer is PINNED the first time it is asked, and a
fixture asks before the act (`pin`; `hermetic` pins the sandbox HOME). Every
later answer must be the same, or the test fails with "the <leaf> leaf
moved". A test whose point is a tree it builds itself (an old-only HOME)
builds it first and then pins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LEAVES = ("sessions", "seats")

_PINS: dict[tuple[str, str], Path] = {}


def _home(home: Path | str | None) -> Path:
    return Path(home) if home is not None else Path(os.environ["HOME"])


def _resolve(leaf: str, home: Path) -> Path:
    """sessiontools' answer for LEAF under HOME, in the process's env."""
    from sessiontools import settings
    env = {**os.environ, "HOME": str(home)}
    return {"sessions": settings.sessions_root,
            "seats": settings.seats_root}[leaf](env)


def reset() -> None:
    """Forget every pin (each test starts with none)."""
    _PINS.clear()


def pin(home: Path | str | None = None) -> dict[str, Path]:
    """Pin both leaves for HOME as sessiontools resolves them NOW, and
    return them. Call it before the code under test runs."""
    h = _home(home)
    return {leaf: leaf_root(leaf, h) for leaf in LEAVES}


def leaf_root(leaf: str, home: Path | str | None = None) -> Path:
    """LEAF's folder under HOME: the pinned answer, checked against the
    resolver's answer now."""
    h = _home(home)
    found = _resolve(leaf, h)
    pinned = _PINS.setdefault((str(h), leaf), found)
    assert found == pinned, f"the {leaf} leaf moved: {pinned} -> {found}"
    return pinned


def sessions_root(home: Path | str | None = None) -> Path:
    return leaf_root("sessions", home)


def seats_root(home: Path | str | None = None) -> Path:
    return leaf_root("seats", home)


def session_dir(sid: str, home: Path | str | None = None) -> Path:
    """SID's session dir, `<sessions leaf>/<sid>`."""
    return sessions_root(home) / sid


def session_mail(sid: str, home: Path | str | None = None) -> Path:
    """SID's mail state, `<session dir>/mail/` (session-settings 3.3a)."""
    return session_dir(sid, home) / "mail"


def seat_dir(seat: str, home: Path | str | None = None) -> Path:
    """SEAT's folder, `<seats leaf>/<seat>`."""
    return seats_root(home) / seat


def seat_settings(seat: str, home: Path | str | None = None) -> Path:
    """SEAT's mail layer, `<seats leaf>/<seat>/settings/mail.json`."""
    return seat_dir(seat, home) / "settings" / "mail.json"


def session_settings(sid: str, home: Path | str | None = None) -> Path:
    """SID's mail layer, `<session dir>/settings/mail.json`."""
    return session_dir(sid, home) / "settings" / "mail.json"


# ── a SESSION's settings layer, by the pinned leaf ─────────────────────────
# The resolver's twins of `_seatmap`'s session readers. `_seatmap` still
# spells `$HOME/.session/sessions` for its own importers, whose readers (the
# map generator) do not follow the resolver yet; a test whose code under
# test DOES follow it names the layer here.

def session_live_path(sid: str, home: Path | str | None = None) -> Path:
    """The file the session's verbs write (task 3.4): `mail.d/70-live.json`."""
    return session_dir(sid, home) / "settings" / "mail.d" / "70-live.json"


def session_values(sid: str, home: Path | str | None = None) -> dict:
    """SID's `mail.json` as written (no `mail.d/`), {} when absent."""
    p = session_settings(sid, home)
    return json.loads(p.read_text()) if p.is_file() else {}


def session_log(sid: str, home: Path | str | None = None) -> list[dict]:
    """SID's settings provenance, `<session dir>/log/settings.jsonl`."""
    p = session_dir(sid, home) / "log" / "settings.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []
