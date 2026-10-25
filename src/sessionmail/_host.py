"""This host's NAME, and the set of names that ARE this host.

The rule is `openspec/changes/host-rule/proposal.md` (its table of cases,
rows N1-N12 and I1-I14, is what `tests/test_host_rule.py` checks). Two
questions, two answers, never mixed:

- `name(env)`: what this host calls itself. Everything that WRITES an
  address, a Message-ID, a map key or a repair line uses it. The first
  source that is non-empty after `strip()` wins, LOWERCASED:
  `$SESSIONMAIL_HOST`, `$SESSION_MAIL_HOST` (the old spelling), the `host`
  setting (template and host layers), the live hostname's first label. A
  variable or a setting is kept whole, dots and all; only the live hostname
  is cut. Empty everywhere is `""`, and a writer refuses rather than write
  `name@`.
- `names(env)` / `is_me(domain, env)`: is a domain this host? A SET,
  compared case-insensitively: the name, the `host` setting itself, the live
  short hostname, `scutil`'s LocalHostName and ComputerName (slugged) where
  `scutil` exists (macOS), and `localhost`. ONLY the mail drop's local test
  asks it (`_drop.qualify`). On an MTA host postfix routes, untouched.

No network: `scutil --get` reads the local SystemConfiguration
preferences, each call bounded by `SCUTIL_TIMEOUT`. The binary is found by
`scutil_binary(env)`, THE test seam: the suite's conftest replaces it so a
test is answered only by a fake `scutil` on its PATH, never by the host's.

Not a sessiontools API: sessionmail's own, one function each, so
it can delegate later.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import unicodedata
from collections.abc import Mapping

#: The variable that names this host, and its old spelling (read second).
VAR = "SESSIONMAIL_HOST"
OLD_VAR = "SESSION_MAIL_HOST"
#: The settings key (`_settings.KEYS`); its KEY and FILE are read by
#: sessiontools' `operator.short_host` too, so they never move.
KEY = "host"
#: Always a name for this host.
LOCALHOST = "localhost"
#: Where macOS keeps `scutil`, when the env's PATH does not name one.
SCUTIL = "/usr/sbin/scutil"
#: The two names `scutil` is asked: LocalHostName as given, ComputerName slugged.
SCUTIL_NAMES = ("LocalHostName", "ComputerName")
#: Seconds each `scutil --get` may take (measured: about 10 ms).
SCUTIL_TIMEOUT = 1.0

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def live() -> str:
    """The live hostname's first label, stripped and lowercased ("" when the
    hostname is empty). It can change under a running seat (D6)."""
    return socket.gethostname().split(".")[0].strip().lower()


def _variable(env: Mapping[str, str]) -> str:
    for var in (VAR, OLD_VAR):
        value = (env.get(var) or "").strip()
        if value:
            return value.lower()
    return ""


def setting(env: Mapping[str, str] | None = None) -> str:
    """The `host` setting itself (template and host layers), stripped and
    lowercased, whatever the variables say; "" when it is not a non-empty
    string. `_settings.file_text` gives the variable precedence over the
    file, so the variable is taken out of the mapping it is asked with."""
    from . import _settings          # here, not at import: as `_sender` does
    env = _env(env)
    asked = {k: v for k, v in env.items() if k != VAR}
    return (_settings.file_text(KEY, asked) or "").lower()


def name(env: Mapping[str, str] | None = None) -> str:
    """This host's NAME: the variable, else the old variable, else the `host`
    setting, else the live short hostname; stripped, lowercased. "" when
    every source is empty: the caller refuses."""
    env = _env(env)
    return _variable(env) or setting(env) or live()


def slug(computer_name: str) -> str:
    """COMPUTER_NAME as macOS derives a LocalHostName from it: NFKD with the
    non-ASCII dropped, lowercased, apostrophes deleted, every other run of
    characters outside [a-z0-9] one `-`, no `-` at either end
    (`MacBook Air` -> `macbook-air`, `Alex's MacBook Air` -> `alexs-macbook-air`)."""
    text = unicodedata.normalize("NFKD", computer_name)
    text = text.encode("ascii", "ignore").decode("ascii").lower().replace("'", "")
    return _NOT_SLUG.sub("-", text).strip("-")


def scutil_binary(env: Mapping[str, str] | None = None) -> str | None:
    """The `scutil` to ask: the env's PATH first, then `SCUTIL`; None where
    there is none (Linux). THE test seam (conftest `hermetic` replaces it)."""
    env = _env(env)
    found = shutil.which("scutil", path=env.get("PATH"))
    if found:
        return found
    return SCUTIL if os.path.isfile(SCUTIL) and os.access(SCUTIL, os.X_OK) else None


def scutil_get(which: str, env: Mapping[str, str] | None = None) -> str:
    """`scutil --get WHICH`, stripped; "" on any failure, a non-zero exit, a
    timeout, or `<WHICH>: not set`. Never raises."""
    binary = scutil_binary(env)
    if binary is None:
        return ""
    try:
        done = subprocess.run([binary, "--get", which], capture_output=True, text=True,
                              timeout=SCUTIL_TIMEOUT, check=False, env=dict(_env(env)))
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    got = done.stdout.strip()
    if done.returncode != 0 or not got or got.endswith(": not set"):
        return ""
    return got


def names(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Every name this host has: the IS-ME set, lowercase, stripped, never
    empty strings. The drop's local test only (`_drop.qualify`)."""
    env = _env(env)
    local_host_name, computer_name = (scutil_get(w, env) for w in SCUTIL_NAMES)
    found = {name(env), setting(env), live(), local_host_name.strip().lower(),
             slug(computer_name), LOCALHOST}
    return frozenset(n for n in found if n)


def matches(domain: str, members: frozenset[str] | set[str]) -> bool:
    """DOMAIN (lowercased, stripped) equals a member or lies under one
    (`member.<anything>`, the tailnet FQDN form)."""
    d = domain.strip().lower()
    return bool(d) and any(d == m or d.startswith(m + ".") for m in members)


def is_me(domain: str, env: Mapping[str, str] | None = None) -> bool:
    """Is DOMAIN this host? The NAME is asked first, so `scutil` runs only
    for a domain that is not the name."""
    env = _env(env)
    here = name(env)
    if here and matches(domain, {here}):
        return True
    return matches(domain, names(env))
