"""A seat's subscriptions where session-settings 3.2 put them, for fixtures:
the `subscriptions` map of `$HOME/.session/seats/<seat>/settings/mail.json`
(`_layer`). HOME is the sandbox conftest sets, unless one is given."""

from __future__ import annotations

import json
import os
from pathlib import Path


def seat_map_path(seat: str, home: Path | None = None) -> Path:
    home = Path(home) if home is not None else Path(os.environ["HOME"])
    return home / ".session" / "seats" / seat / "settings" / "mail.json"


def seat_subscribe(seat: str, *boxes: str, home: Path | None = None) -> Path:
    """Add BOXES to SEAT's map, `true` each, keeping what is there."""
    p = seat_map_path(seat, home)
    data = json.loads(p.read_text()) if p.is_file() else {}
    data.setdefault("subscriptions", {}).update({b: True for b in boxes})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


# ── a SESSION's layer (session-settings 3.3b) ───────────────────────────────
# `$HOME/.session/sessions/<sid>/settings/mail.json`: its `subscriptions` map
# and its declared plus `address`; provenance in `<session dir>/log/
# settings.jsonl`.

def session_dir(sid: str, home: Path | None = None) -> Path:
    home = Path(home) if home is not None else Path(os.environ["HOME"])
    return home / ".session" / "sessions" / sid


def session_map_path(sid: str, home: Path | None = None) -> Path:
    return session_dir(sid, home) / "settings" / "mail.json"


def session_values(sid: str, home: Path | None = None) -> dict:
    """The session's `mail.json` as written (no `mail.d/`), {} when absent."""
    p = session_map_path(sid, home)
    return json.loads(p.read_text()) if p.is_file() else {}


def session_live_path(sid: str, home: Path | None = None) -> Path:
    """The file the session's verbs write since task 3.4: `mail.d/70-live.json`."""
    return session_dir(sid, home) / "settings" / "mail.d" / "70-live.json"


def session_env_path(sid: str, home: Path | None = None) -> Path:
    """The launcher's birth file (sessiontools 4.4): `mail.d/50-env.json`."""
    return session_dir(sid, home) / "settings" / "mail.d" / "50-env.json"


def _fold(a: dict, b: dict) -> dict:
    """jq's `*`, as sessiontools folds a layer: maps merge key by key."""
    out = dict(a)
    for k, v in b.items():
        out[k] = _fold(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def session_folded(sid: str, home: Path | None = None) -> dict:
    """SID's layer as the readers see it: `mail.json`, then `mail.d/*.json`."""
    settings = session_dir(sid, home) / "settings"
    files = [settings / "mail.json"] + sorted((settings / "mail.d").glob("*.json"))
    out: dict = {}
    for f in files:
        if f.is_file():
            out = _fold(out, json.loads(f.read_text()))
    return out


def session_boxes(sid: str, home: Path | None = None) -> list[str]:
    """The boxes SID's layer subscribes to, folded (`true` keys), sorted."""
    subs = session_folded(sid, home).get("subscriptions") or {}
    return sorted(k for k, v in subs.items() if v is True)


def session_env(sid: str, boxes: str, home: Path | None = None) -> Path:
    """What the launcher's birth writes for SESSIONMAIL_SUBSCRIBE=BOXES."""
    p = session_env_path(sid, home)
    p.parent.mkdir(parents=True, exist_ok=True)
    names = [b for b in boxes.replace(",", " ").split() if b]
    p.write_text(json.dumps({"subscriptions": {b: True for b in names}}, indent=2) + "\n")
    return p


def session_subscribe(sid: str, *boxes: str, home: Path | None = None) -> Path:
    """Add BOXES to SID's map, `true` each, keeping what is there."""
    p = session_map_path(sid, home)
    data = session_values(sid, home)
    data.setdefault("subscriptions", {}).update({b: True for b in boxes})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def session_declare(sid: str, address: str | None, home: Path | None = None) -> Path:
    """Set SID's declared plus `address` (None removes it), as `pager listen` does."""
    p = session_map_path(sid, home)
    data = session_values(sid, home)
    if address is None:
        data.pop("address", None)
    else:
        data["address"] = address
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def session_log(sid: str, home: Path | None = None) -> list[dict]:
    p = session_dir(sid, home) / "log" / "settings.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []
