"""Which notmuch index a call reads: one switch, one resolver.

PROPOSED, not ratified: `openspec/changes/store-index/specs/package/spec.md`.

`SESSIONMAIL_INDEX` picks the index:

- unset, empty or `host`: the host's own profile, `sessionkit` by default
  (below). A notmuch config that is not sessionmail's, `~/.notmuch-config`
  included, is never read: it may be a stranger's own mail.
- `store`: the store's profile (below).
- anything else: refused, naming the value.

Either way the profile is one FILE, resolved here, passed as
`--config=<file>` on every call, with `NOTMUCH_CONFIG`, `NOTMUCH_DATABASE`
and `NOTMUCH_PROFILE` dropped from the child's environment. An inherited
`NOTMUCH_CONFIG` beats a profile (E3), and an inherited `NOTMUCH_DATABASE`
beats `--config`, for reads and for `notmuch new`, which then rewrites the
database it names (E14). Dropping all three makes the resolved file the only
one possible.

Host mode's profile NAME is `$SESSIONMAIL_NOTMUCH_PROFILE` when set and not
empty, else the `notmuch.profile` key in mail.json, else `sessionkit`
(`_settings.setting`, the one reader). notmuch's own variables never choose
it: a stranger may export `NOTMUCH_PROFILE` or `NOTMUCH_CONFIG` for their
own mail. The file is found as in store mode's step 1, below. No file is a
refusal naming the profile and the paths tried; `message setup` writes it.

Store mode resolves the profile in this order:

1. `NOTMUCH_PROFILE=<p>`. The file is `$XDG_CONFIG_HOME/notmuch/<p>/config`
   if it exists, else `~/.notmuch-config.<p>`: notmuch's own order (E19a),
   so this module and the seat's raw `notmuch` read the same file. An EMPTY
   `NOTMUCH_PROFILE` is refused by name: notmuch fails every call under it,
   even with `~/.notmuch-config` present (E19b).
2. Otherwise the seat's store: the seat's address (`_seat.resolve`), its
   `+extension` stripped, its box (`_maildir.resolve_box`), then that box or
   its nearest ancestor holding `_boxes.STORE_MARKER`, below the agents root.
   That directory's name is `<p>`, found as in step 1. The box itself counts:
   a store-level seat's box IS the store directory.

The file must EXIST, checked with a file test. It is never probed with
`notmuch config get`, which exits 0 on a missing profile (E5). No profile
means a refusal that names what was tried; nothing ever falls back to the
host index (nothing is shared by default; it fails closed).

Callers take `argv` and `child_env` for a notmuch
child, and `oracle_env` for a script that calls bare `notmuch`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# A module reference, not a name: `_notmuch` imports this module, so the
# exception is looked up when a refusal is raised, never at import.
from . import _notmuch

VAR = "SESSIONMAIL_INDEX"
HOST = "host"
STORE = "store"
MODES = (HOST, STORE)

PROFILE_VAR = "NOTMUCH_PROFILE"

#: Host mode's profile when nothing names another: the kit's own, never a
#: stranger's default config.
SESSIONKIT_PROFILE = "sessionkit"
#: The variable that names host mode's profile, beating the file's
#: `notmuch.profile`. notmuch's own `NOTMUCH_PROFILE` never does.
PROFILE_KEY_VAR = "SESSIONMAIL_NOTMUCH_PROFILE"
#: Never inherited by a store-mode child (E3, E14). The profile var is
#: dropped too: it loses to `--config` anyway, and no child may fall back
#: to it.
SCRUBBED = ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", PROFILE_VAR)

#: The words every store-mode refusal ends on, so a reader can tell the
#: refusal from a missing message (SC3).
REFUSES_HOST = f"{VAR}={STORE} refuses the host index"


@dataclass(frozen=True)
class Resolved:
    """What a notmuch call reads. `config` is the profile file, None only
    when `refusal` is non-empty: then nothing may be read."""

    mode: str
    config: Path | None = None
    #: The profile name and how it was found, for a reader's diagnostics.
    profile: str = ""
    via: str = ""
    refusal: str = ""
    #: Every path and step looked at, in order (the refusal quotes them).
    tried: tuple[str, ...] = field(default=())

    @property
    def refused(self) -> bool:
        return bool(self.refusal)

    @property
    def host(self) -> bool:
        return self.mode == HOST and not self.refused


def mode(env: dict[str, str] | None = None) -> str:
    """The switch's raw value, `host` when unset or empty."""
    env = os.environ if env is None else env
    return env.get(VAR) or HOST


def _home(env: dict[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path.home()


def candidates(profile: str, env: dict[str, str] | None = None) -> tuple[Path, Path]:
    """(XDG file, dot file) for PROFILE, in the order notmuch reads them
    (E19a). `$XDG_CONFIG_HOME` unset or empty is `~/.config`."""
    env = os.environ if env is None else env
    home = _home(env)
    xdg = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return xdg / "notmuch" / profile / "config", home / f".notmuch-config.{profile}"


def profile_config(profile: str = SESSIONKIT_PROFILE, env: dict[str, str] | None = None
                   ) -> Path:
    """`$XDG_CONFIG_HOME/notmuch/<profile>/config`: the path to WRITE a
    profile to. HOME and XDG_CONFIG_HOME are read from ENV (default
    `os.environ`) at call time."""
    return candidates(profile, env)[0]


def profile_file(profile: str, env: dict[str, str] | None = None
                 ) -> tuple[Path | None, tuple[str, ...]]:
    """(the file PROFILE names, the paths tried). The file is None when
    neither exists. A file test, never `notmuch config get` (E5)."""
    tried: list[str] = []
    for path in candidates(profile, env):
        if path.is_file():
            return path, tuple(tried)
        tried.append(str(path))
    return None, tuple(tried)


#: The seat's store, once per process: `resolve_box` may read the postfix
#: map, 14-16 ms (MEASURED). Keyed on everything the
#: answer depends on. Only a found store is kept, so a box born later is
#: still found.
_STORES: dict[tuple[str, str], tuple[str, str]] = {}


def clear_cache() -> None:
    _STORES.clear()


def seat_store(env: dict[str, str] | None = None) -> tuple[str, str, tuple[str, ...]]:
    """(store name, how it was found, what was tried) for this seat. The name
    is "" when the seat has no store: no identity, no box, or no marker at or
    above its box below the agents root."""
    env = os.environ if env is None else env
    # Read, never edited here. Imported here, not at the top:
    # _maildir and _seat import each other.
    from . import _boxes, _maildir, _seat
    try:
        seat = _seat.resolve(env)
    except _seat.NoIdentity as exc:
        return "", "", (f"no seat: {exc}",)
    split = _seat.split_plus(seat.address)
    base = split[0] if split else seat.address
    root = _maildir.root(env)
    key = (base, str(root))
    if key in _STORES:
        name, how = _STORES[key]
        return name, how, ()
    try:
        box, how = _maildir.resolve_box(base, env)
    except _maildir.NoSuchBox as exc:
        return "", "", (f"seat {base!r} ({seat.source}): no box: {exc}",)
    try:
        resolved_root = root.resolve()
        d = box.resolve()
    except OSError as exc:
        return "", "", (f"seat {base!r}: box {box} could not be read: {exc}",)
    walked: list[str] = []
    while d != resolved_root and d.is_relative_to(resolved_root):
        if (d / _boxes.STORE_MARKER).exists():
            found = f"seat {base!r} ({seat.source}), box {box} ({how}), marker {d / _boxes.STORE_MARKER}"
            _STORES[key] = (d.name, found)
            return d.name, found, ()
        walked.append(str(d))
        d = d.parent
    where = ", ".join(walked) if walked else f"{box}, which is not under {resolved_root}"
    missing = (f"seat {base!r} ({seat.source}), box {box} ({how}): "
               f"no {_boxes.STORE_MARKER} at {where}")
    return "", "", (missing,)


def _refuse(why: str, tried: tuple[str, ...], profile: str = "") -> Resolved:
    detail = f"; tried {'; '.join(tried)}" if tried else ""
    return Resolved(mode=STORE, profile=profile, tried=tried,
                    refusal=f"no notmuch profile for this seat: {why}{detail}; {REFUSES_HOST}")


def host_profile(env: dict[str, str] | None = None) -> tuple[str, str]:
    """(host mode's profile name, where it came from): the variable, else
    `notmuch.profile` in mail.json, else SESSIONKIT_PROFILE. A settings file
    that cannot be read leaves the default, and says so."""
    env = os.environ if env is None else env
    from . import _settings          # here, not at import: _settings is the higher layer
    got = _settings.setting(".".join(_settings.NOTMUCH_PROFILE_KEY), env)
    if got.source == _settings.VARIABLE:
        return str(got.value), f"from ${PROFILE_KEY_VAR}"
    if got.source in (_settings.TEMPLATE_LAYER, _settings.HOST_LAYER):
        return got.value, f"from notmuch.profile in {_settings.SHOWN}"   # judged by the caller
    if got.source == _settings.UNREADABLE:
        return SESSIONKIT_PROFILE, f"the default; {_settings.SHOWN} could not be read: {got.why}"
    return SESSIONKIT_PROFILE, "the default"


def profile_name_problem(name: object) -> str:
    """"" when NAME can be a profile: a non-empty string that names one
    directory under notmuch's, never a path out of it."""
    if not isinstance(name, str) or not name.strip():
        return "it is not a name"
    if "/" in name or name in (".", ".."):
        return "a profile is one directory name, never a path"
    return ""


def _host(env: dict[str, str]) -> Resolved:
    name, via = host_profile(env)
    why = profile_name_problem(name)
    if why:
        return Resolved(mode=HOST, via=via, refusal=(
            f"no notmuch profile for this host: {json.dumps(name)} ({via}) is not a "
            f"profile: {why}. The default notmuch config is never read"))
    path, tried = profile_file(name, env)
    if path is None:
        return Resolved(mode=HOST, profile=name, via=via, tried=tried, refusal=(
            f'no notmuch profile for this host: profile "{name}" ({via}) has no file; '
            f"tried {'; '.join(tried)}; `message setup` writes it. "
            "The default notmuch config is never read"))
    return Resolved(mode=HOST, config=path, profile=name, via=via, tried=tried)


def resolve(env: dict[str, str] | None = None) -> Resolved:
    """What this process's notmuch calls read. Never raises: a refusal is a
    value, and `require` turns it into `CouldNotLook`."""
    env = os.environ if env is None else env
    value = mode(env)
    if value == HOST:
        return _host(env)
    if value != STORE:
        return Resolved(mode=value, refusal=(
            f"{VAR}={value!r} is not a mode: it is {HOST!r} (the default) or "
            f"{STORE!r}; refusing rather than guess which index to read"))
    if PROFILE_VAR in env:
        profile = env[PROFILE_VAR]
        if profile == "":
            return _refuse(f"{PROFILE_VAR} is set but EMPTY, which notmuch reads as a "
                           f"profile with no name and fails every call (never "
                           f"~/.notmuch-config.)", ())
        path, tried = profile_file(profile, env)
        if path is None:
            return _refuse(f"profile {profile!r} (from {PROFILE_VAR}) has no file", tried,
                           profile)
        return Resolved(mode=STORE, config=path, profile=profile, via=f"{PROFILE_VAR}={profile}",
                        tried=tried)
    store, how, tried = seat_store(env)
    if not store:
        return _refuse(f"{PROFILE_VAR} is unset and the seat has no store", tried)
    path, files = profile_file(store, env)
    if path is None:
        return _refuse(f"profile {store!r} (the seat's store: {how}) has no file", files,
                       store)
    return Resolved(mode=STORE, config=path, profile=store, via=how, tried=files)


def require(env: dict[str, str] | None = None) -> Resolved:
    """`resolve`, raising `_notmuch.CouldNotLook` with the refusal's text.
    Every caller already reports that as "could not look", never as
    "nothing found"."""
    r = resolve(env)
    if r.refused:
        raise _notmuch.CouldNotLook(r.refusal)
    return r


def argv(exe: str, env: dict[str, str] | None = None) -> list[str]:
    """The argv PREFIX for a notmuch call: `[exe, --config=<file>]`, in
    either mode. Raises CouldNotLook on a refusal."""
    return [exe, f"--config={require(env).config}"]


def scrub(env: dict[str, str]) -> dict[str, str]:
    """A copy of ENV without the three variables that override a config."""
    return {k: v for k, v in env.items() if k not in SCRUBBED}


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for a notmuch child: ENV without `SCRUBBED`, in
    either mode. Raises CouldNotLook on a refusal."""
    env = os.environ if env is None else env
    require(env)
    return scrub(env)


def oracle_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for a script that runs bare `notmuch` (the mhs
    oracles): `NOTMUCH_CONFIG=<file>` with the other two dropped, in either
    mode. Raises CouldNotLook on a refusal."""
    env = os.environ if env is None else env
    r = require(env)
    return {**scrub(env), "NOTMUCH_CONFIG": str(r.config)}


__all__ = [
    "HOST",
    "MODES",
    "PROFILE_KEY_VAR",
    "PROFILE_VAR",
    "REFUSES_HOST",
    "SCRUBBED",
    "SESSIONKIT_PROFILE",
    "STORE",
    "VAR",
    "Resolved",
    "argv",
    "candidates",
    "child_env",
    "clear_cache",
    "host_profile",
    "mode",
    "oracle_env",
    "profile_config",
    "profile_file",
    "profile_name_problem",
    "require",
    "resolve",
    "scrub",
    "seat_store",
]
