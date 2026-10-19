"""The mail tool's SEAT and SESSION layers in the new tree: one `mail.json`.

sessiontools `openspec/changes/session-settings`, task 3.2:

    ~/.session/seats/<seat>/settings/mail.json    the seat's layer
    ~/.session/seats/<seat>/log/settings.jsonl    who changed it, and why

A seat's subscriptions were files in `~/.sessionmail/seats/<seat>/
subscriptions/`. They are a MAP now: `"subscriptions": {"<box>": true}`.

- **Read per owner**: each layer's map makes its own rows, so
  routing is unchanged, and `false` means the same as absent. A layer's own
  files (`mail.json`, then `mail.d/*.json`) fold through sessiontools' one
  merge (`sessiontools.settings`; no second merge here). The
  layers are never merged with each other for routing.
- **One writer per seat file:** `message subscription add|remove --seat`.
  It writes `mail.json` only, under a lock, atomically, and logs the
  change (provenance goes to `log/settings.jsonl`).
- **An agent's bare name is not a seat**. A
  folder whose name has no `+` and no `.` (`alice`, `bob`, a session id)
  stays at the old path, read and written there, until 3.8 reads the
  agent layer.
- **No symlink at the old path**: the old
  folder also holds the retired saved queries, and nothing outside
  sessionmail reads it.

**The session layer** (task 3.3b): `<session dir>/settings/mail.json`,
read and written the same way. It holds the session's `subscriptions` map
(its own rows, per owner, as a seat's) and its declared plus `address`, a
top-level string (`set_value`). Its provenance goes to `<session dir>/log/
settings.jsonl`. A session's old files - `subscriptions/<box>` and
`address` under `~/.sessionmail/sessions/<sid>/` - route nothing once
converted (`_migrate`, and `pager listen` for its own session).

`sessiontools.settings` ships in sessiontools 0.0.4. It is imported only
when a seat file is read or written, so an older sessiontools fails that
one read or write with `LayerError`, never an import of this package.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

#: The tool's one-word name: the file at every layer (session-settings, "Tool").
TOOL = "mail"
FILE = f"{TOOL}.json"
SUBSCRIPTIONS = "subscriptions"
#: A session's declared plus address (task 3.3b): a top-level string.
ADDRESS = "address"
#: The drop-in a session's verbs write at run time (task 3.4, specs/settings
#: "A subscription removed stays removed"): the subscription verbs and the
#: listen's address. It sorts after the birth's `mail.d/50-env.json`, so what
#: a verb set - a `false` included - wins over what the environment said, and
#: before a hand's `90-*`. `mail.json` stays the first, lowest file: the
#: conversion's (`_migrate`). Drop-ins by writer.
LIVE = f"{TOOL}.d/70-live.json"
ENV = f"{TOOL}.d/50-env.json"     # the launcher's birth (sessiontools 4.4); never written here
#: Under the seat's dir, beside `settings/` (specs/tree: `log/settings.jsonl`).
LOG = Path("log") / "settings.jsonl"


class LayerError(Exception):
    """A seat file that cannot be read or written. The message names it."""


#: The words `_st` raises with when the reader itself is missing, so a
#: caller with a fallback (`_seat.pager_required`) can tell
#: "no reader" from "this layer's file is bad".
NOT_INSTALLED = "sessiontools.settings is not installed"


def _st():
    try:
        from sessiontools import settings
    except ImportError as exc:
        raise LayerError(f"{NOT_INSTALLED} (it ships in "
                         "sessiontools 0.0.4), so the seat and session files under "
                         "~/.session cannot be read") from exc
    return settings


def is_agent_name(name: str) -> bool:
    """A folder named for an AGENT, not a seat: no `+` and no `.` (`alice`,
    `bob`, a bare session's id). Seats are `<project>+<role>` or
    `<role>.<project>` (one measured host: 88 seats, 24 such names)."""
    return "+" not in name and "." not in name


def seats_root(env: Mapping[str, str] | None = None) -> Path:
    """`~/.session/seats`: sessiontools' root, from HOME."""
    return _st().session_root(env) / "seats"


def seat_dir(seat: str, env: Mapping[str, str] | None = None) -> Path:
    return _st().seat_dir(seat, env)


def settings_dir(seat: str, env: Mapping[str, str] | None = None) -> Path:
    return seat_dir(seat, env) / "settings"


def session_settings_dir(session: str, env: Mapping[str, str] | None = None) -> Path:
    """`<session dir>/settings`: SESSION's layer (task 3.3b), in the session dir
    sessiontools keeps (`settings.session_dir`, the one computation 3.3a uses)."""
    return _st().session_dir(session, env) / "settings"


def read(directory: Path, layer: str = "seat") -> tuple[dict, dict[str, str]]:
    """The one layer in DIRECTORY: `(values, origins)`, `mail.json` then
    `mail.d/*.json` folded by sessiontools' merge. Both are empty when the
    layer has no file. LayerError names a file that cannot be read. LAYER
    names it to the merge ("seat" or "session")."""
    st = _st()
    try:
        srcs = [st.Source(layer, p, st.read_json_object(p))
                for p in st.layer_files(directory, TOOL)]
    except (st.SettingsError, OSError, UnicodeDecodeError) as exc:
        raise LayerError(str(exc)) from None
    try:
        values, origins, _locked, _leaves = st.merge(srcs)
        if not isinstance(values, dict) or not isinstance(origins, dict):
            raise TypeError("values and origins are not maps")
    except st.SettingsError as exc:
        raise LayerError(str(exc)) from None
    except (ValueError, TypeError) as exc:
        # sessiontools' merge changed its answer (three values to four within
        # the hour, 414bad3 to dc4a6c8), or its call: name it, never crash the
        # verb.
        raise LayerError(f"{directory}: sessiontools.settings.merge answered in a "
                         f"shape this sessionmail does not read ({exc}); "
                         "sessionmail and sessiontools are out of step") from None
    return values, origins


def boxes(values: Mapping) -> list[str]:
    """The boxes VALUES subscribes to: keys of `subscriptions` whose value is
    exactly `true`. Anything else (`false`, a string, a map) routes nothing:
    fail safe, as a stray file never routes mail."""
    subs = values.get(SUBSCRIPTIONS)
    if not isinstance(subs, dict):
        return []
    return sorted(k for k, v in subs.items() if v is True)


def _load(path: Path) -> dict:
    """One layer file as written (`mail.json`, or a verb's drop-in), without
    the others: what the writer edits."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise LayerError(f"{path}: cannot read it: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LayerError(f"{path}: not JSON ({exc.msg}, line {exc.lineno}); "
                         "nothing written") from None
    if not isinstance(data, dict):
        raise LayerError(f"{path}: not a JSON object; nothing written")
    return data


def _write(path: Path, data: dict) -> None:
    """Whole or not at all: a hidden temp name beside it, then rename."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _lock(directory: Path) -> Iterator[None]:
    """One writer at a time per seat file: an flock on a dotfile beside it
    (sessiontools' `layer_files` reads only `mail.json` and `mail.d/`)."""
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / f".{FILE}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def set_entry(directory: Path, section: str, key: str, value: object | None,
              *, file: str = FILE) -> bool:
    """Set SECTION.KEY to VALUE in DIRECTORY's FILE (`mail.json`, or `LIVE`
    for a session's verbs), or remove it when VALUE is None. True if the
    file changed. A map left empty is dropped,
    and a file left empty is removed: an empty map adds nothing (sessiontools
    dc4a6c8), so it is never written. LayerError if the file is not ours to
    edit (not JSON, not an object, SECTION not a map), or the OS refuses."""
    path = directory / file
    if value is None and not path.is_file():
        return False                 # nothing to remove, and no dir is made for it
    try:
        with _lock(directory):
            path.parent.mkdir(parents=True, exist_ok=True)
            data = _load(path)
            sect = data.get(section, {})
            if not isinstance(sect, dict):
                raise LayerError(f"{path}: `{section}` is not a map; nothing written")
            if value is None:
                if key not in sect:
                    return False
                del sect[key]
            else:
                if key in sect and sect[key] == value and type(sect[key]) is type(value):
                    return False
                sect[key] = value
            if sect:
                data[section] = sect
            else:
                data.pop(section, None)
            if data:
                _write(path, data)
            else:
                path.unlink(missing_ok=True)
    except OSError as exc:
        raise LayerError(f"{path}: {exc.strerror or exc}; nothing written") from None
    return True


def address(values: Mapping) -> str:
    """The plus address VALUES declares, folded to lower case; "" when none.
    Anything but a non-empty string declares nothing (fail safe: a stray
    value never routes mail)."""
    value = values.get(ADDRESS)
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().lower()


def set_value(directory: Path, key: str, value: object | None,
              *, file: str = FILE) -> bool:
    """Set the TOP-LEVEL KEY of DIRECTORY's FILE to VALUE, or remove it when
    VALUE is None (a session's `address`). True if the file changed.
    The same lock, atomic write and empty-file rule as `set_entry`."""
    path = directory / file
    if value is None and not path.is_file():
        return False
    try:
        with _lock(directory):
            path.parent.mkdir(parents=True, exist_ok=True)
            data = _load(path)
            if value is None:
                if key not in data:
                    return False
                del data[key]
            else:
                if key in data and data[key] == value and type(data[key]) is type(value):
                    return False
                data[key] = value
            if data:
                _write(path, data)
            else:
                path.unlink(missing_ok=True)
    except OSError as exc:
        raise LayerError(f"{path}: {exc.strerror or exc}; nothing written") from None
    return True


def log(seat_root: Path, key: str, value: object | None, *, by: str,
        argv: str, why: str | None = None) -> None:
    """Append one provenance line to SEAT_ROOT/log/settings.jsonl: what a map
    value cannot say (who, when, the command, why). Best effort: a change
    that could not be logged still stands."""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": TOOL, "key": key,
           "value": value, "by": by, "argv": argv}
    if why:
        rec["why"] = " ".join(why.split())
    path = seat_root / LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


__all__ = ["LayerError", "TOOL", "FILE", "LIVE", "ENV", "SUBSCRIPTIONS", "ADDRESS", "LOG", "is_agent_name",
           "seats_root", "seat_dir", "settings_dir", "session_settings_dir", "read", "boxes",
           "address", "set_entry", "set_value", "log"]
