"""The tailnet's MagicDNS suffix, from the ONE definition.

The definition is the shell helper shipped beside the alias generator,
`postfix/magicdns-suffix`: the generator is bash and must not need python,
so python runs the helper rather than the other way round. Its order:

  1. `MAGICDNS_SUFFIX`, non-empty after trimming, wins (a malformed one is
     refused, never corrected); in Python only, the settings file's
     `magicdns_suffix` comes next, by the same test (a caller that runs the
     helper or the generator passes it in the variable: `_generator.spawn_env`);
  2. else `tailscale status --json`'s `MagicDNSSuffix`, from `tailscale` on
     PATH, then the macOS app bundle's CLI, each briefly bounded;
  3. else a LOUD failure naming the fix. There is no default suffix.

This module adds two things and no rule of its own:

  * Step 1 is answered in-process, by the same normalisation and the same
    test the helper applies, so a call with the variable set forks nothing.
    `tests/test_magicdns_suffix.py` holds the two to one answer.
  * A runtime answer (step 2) is memoised for the life of the process, keyed
    by what decides it (PATH and the helper's seams). A failure is never
    memoised: the next call asks again.

`suffix()` raises `SuffixUnknown`, whose message is the helper's own (where it
looked, and "set MAGICDNS_SUFFIX ... or install/start tailscale"). It is for a
caller that cannot go on without one.

The PAGER must go on without one (a host
where no source answers would otherwise have every seat deaf at once, and a
deaf fleet cannot be told what is wrong). Its callers use `lookup()`, which
never raises, and `warn_unknown()`, which says so on stderr once per process.
They fall back to the forms they CAN derive (the short host); none of them
ever invents a suffix.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
from pathlib import Path
from typing import Mapping

#: The one definition, executed by path (package data, like `mhs/`).
HELPER = Path(__file__).parent / "postfix" / "magicdns-suffix"

#: The operator's word, step 1.
SUFFIX_VAR = "MAGICDNS_SUFFIX"
#: The settings file's key for it, read after the variable and before tailscale.
FILE_KEY = "magicdns_suffix"

#: What decides a runtime answer: the memo's key. The helper reads the two
#: seams; PATH decides which `tailscale` it finds.
RUNTIME_VARS = ("PATH", "MAGICDNS_TAILSCALE_BUNDLE", "MAGICDNS_TAILSCALE_TIMEOUT")

#: How long the helper itself may take: two bounded CLIs and change.
HELPER_TIMEOUT = 15.0

FIX = "set MAGICDNS_SUFFIX, or install/start tailscale"

#: What every degraded path says first, word for word (the review bar).
UNKNOWN = "MagicDNS suffix unknown: set MAGICDNS_SUFFIX or start Tailscale"

#: What a mail-drop host with no tailnet is told instead, once, as INFO: there
#: the short host is the right answer, not a fault (`drop_without_tailnet`).
NO_TAILNET = "no tailnet on this host, so addresses use the short host - all the mail drop needs"

#: The helper's own default for the app bundle's CLI (its BUNDLE_DEFAULT);
#: `tests/test_magicdns_suffix.py` holds the two equal.
BUNDLE_DEFAULT = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

_LABELS = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")

_RUNTIME: dict[tuple[str | None, ...], str] = {}

#: True once this process has said the suffix is unknown (`warn_unknown`).
_said = False


class SuffixUnknown(RuntimeError):
    """No MagicDNS suffix: none set, none from tailscale. The message says
    where it looked and how to fix it."""


#: `tr 'A-Z' 'a-z'`, exactly: ASCII only. `str.lower()` would fold some
#: non-ASCII to ASCII (U+212A KELVIN SIGN -> `k`) where the helper does not.
_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def normalise(value: str) -> str:
    """The helper's `normalise`: white space and dots trimmed, ASCII lowercase."""
    return value.translate(_ASCII_FOLD).strip(" \t\n\r\f\v.")


def suffix(env: Mapping[str, str] | None = None) -> str:
    """This tailnet's MagicDNS suffix, lowercase, without dots at the ends.

    ENV is the environment to read (the process's when None); a mapping
    handed in IS the environment, so the helper runs with exactly it.
    Raises SuffixUnknown when there is none."""
    env = os.environ if env is None else env
    raw = env.get(SUFFIX_VAR) or ""
    given = normalise(raw)
    if given:
        if _LABELS.fullmatch(given):
            return given
        raise SuffixUnknown(
            f"{SUFFIX_VAR}={raw} is not a DNS suffix (labels of a-z, 0-9 and -, "
            f"joined by dots) - fix {SUFFIX_VAR}, or unset it to ask tailscale")
    from . import _settings              # here, not at import: _settings is the higher layer
    filed = _settings.file_text(FILE_KEY, env)
    if filed is not None:
        given = normalise(filed)
        if _LABELS.fullmatch(given):
            return given
        raise SuffixUnknown(
            f"{FILE_KEY} {json.dumps(filed)} in {_settings.SHOWN} is not a DNS suffix "
            f"(labels of a-z, 0-9 and -, joined by dots) - fix it, or remove it to ask "
            f"tailscale")
    key = tuple(env.get(v) for v in RUNTIME_VARS)
    if key in _RUNTIME:
        return _RUNTIME[key]
    found = _ask_helper(env)
    _RUNTIME[key] = found
    return found


def _ask_helper(env: Mapping[str, str]) -> str:
    """Run the helper for the runtime answer. Raises SuffixUnknown."""
    run_env = None if env is os.environ else {k: v for k, v in env.items()}
    if run_env is not None:
        run_env.pop(SUFFIX_VAR, None)      # blank here, so blank there
    try:
        proc = subprocess.run([str(HELPER)], capture_output=True, text=True,
                              env=run_env, timeout=HELPER_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SuffixUnknown(f"no MagicDNS suffix: {HELPER} could not answer ({exc}) - "
                            f"{FIX}") from exc
    got = normalise(proc.stdout)
    if proc.returncode != 0 or not _LABELS.fullmatch(got):
        said = proc.stderr.strip() or f"{HELPER} exited {proc.returncode} and said nothing"
        raise SuffixUnknown(said if FIX in said or "fix:" in said else f"{said} - {FIX}")
    return got


def lookup(env: Mapping[str, str] | None = None) -> tuple[str | None, str]:
    """`suffix(ENV)` that never raises: (the suffix, "") or (None, why).

    Nothing is memoised on the failure path, so a tailscale started later is
    found at the next call."""
    try:
        return suffix(env), ""
    except SuffixUnknown as exc:
        return None, str(exc)


def tailscale_present(env: Mapping[str, str] | None = None) -> bool:
    """Whether a tailscale CLI EXISTS where the helper looks (its step 2):
    `tailscale` on ENV's PATH, else the app bundle's CLI at
    `$MAGICDNS_TAILSCALE_BUNDLE` (default BUNDLE_DEFAULT; empty: not looked
    for). Presence only: whether it answers is the helper's question."""
    env = os.environ if env is None else env
    if shutil.which("tailscale", path=env.get("PATH", os.defpath)):
        return True
    bundle = env.get("MAGICDNS_TAILSCALE_BUNDLE", BUNDLE_DEFAULT)
    return bool(bundle) and os.path.isfile(bundle) and os.access(bundle, os.X_OK)


def drop_without_tailnet(env: Mapping[str, str] | None = None) -> bool:
    """True when no suffix is wanted here, so its absence is no fault: no
    operator word (the variable, even a malformed one, or the settings file's
    key), no tailscale CLI (`tailscale_present`), and the carrier is the mail
    drop, which builds no maps. The short host is then the right origin.

    False on every other host: a tailscale that is there but does not answer,
    and a host whose MTA needs the suffix for its maps, keep their warnings.
    True implies a lookup fails (no source is there to answer). It decides
    what is SAID, never what anything resolves to."""
    env = os.environ if env is None else env
    if normalise(env.get(SUFFIX_VAR) or ""):
        return False
    from . import _settings              # here, not at import: _settings is the higher layer
    if _settings.file_text(FILE_KEY, env) is not None:
        return False
    if tailscale_present(env):
        return False
    return _settings.carrier(env).kind == "drop"


def warn_unknown(consequence: str, env: Mapping[str, str] | None = None) -> None:
    """Say, once per process, on stderr, that the suffix is unknown and what
    that costs here (CONSEQUENCE). A caller that has already said it in its
    own words (the `pager listen` banner) calls `said()` instead. On a
    mail-drop host with no tailnet (`drop_without_tailnet(ENV)`) it says
    nothing: the short host is right there, and the banner and `pager status`
    say so once, as INFO."""
    global _said
    if _said:
        return
    _said = True
    if drop_without_tailnet(env):
        return
    print(f"sessionmail: {UNKNOWN} - {consequence} (`{HELPER} --trace` shows where it looked)",
          file=sys.stderr)


def said() -> None:
    """Record that this process has told its reader the suffix is unknown."""
    global _said
    _said = True


def forget() -> None:
    """Drop every memoised runtime answer and the once-said flag (tests; a
    tailnet that moved)."""
    global _said
    _RUNTIME.clear()
    _said = False


__all__ = ["BUNDLE_DEFAULT", "FIX", "HELPER", "NO_TAILNET", "SUFFIX_VAR", "UNKNOWN",
           "SuffixUnknown", "drop_without_tailnet", "forget", "lookup", "normalise", "said",
           "suffix", "tailscale_present", "warn_unknown"]
