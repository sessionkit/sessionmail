"""The memory verbs' road to their oracle: the bundled MHS scripts in `mhs/`.

The strangle plan: the click
package is the front door and shells to the oracle where it must, one verb
at a time, the oracle staying the truth until the port agrees byte for
byte. `specs/memory` - the filesystem view, signed membership, the root
hint, the signature-checked wake - is all in that oracle today, so these
five verbs translate their authored flags onto its flags and hand back
exactly what it printed and how it exited. Nothing is interpreted here; a
wake that the oracle refuses is refused.

The oracle was `session mail VERB` off the PATH, which the session repo's
dispatcher turned into `exec session-mail-VERB "$@"`. The session repo is
being retired, so the scripts behind it now ship HERE, verbatim from
session 2fcd1d1 (`src/sessionmail/mhs/`, package data), and VERB runs
`mhs/session-mail-VERB` directly: the same script with the same argv, one
hop fewer. Resolution, first hit wins:

  1. $SESSIONMAIL_MHS_ORACLE VERB   a named oracle (tests; a pinned build)
  2. mhs/session-mail-VERB          the bundled copy

There is no third step. `session` on the PATH is NOT a fallback: the
bundled copy is the truth, and a stale `session` found first (or found
when the bundle is broken) would quietly answer with an older reader. A
missing bundle is a loud exit 2 from the door, never a guess.

WHICH PYTHON runs a bundled Python script is the package's, never PATH's.
A `#!/usr/bin/env python3` shebang picks the first `python3` on PATH, and on
a stock macOS that is the system 3.9, below `requires-python`. So `oracle`
hands a Python script to `sys.executable` by name, and `run` sets
$SESSIONMAIL_PYTHON to `sys.executable` in the child's environment, for the
bash scripts (the three shims and the verifier's wrapper), which have no
`sys.executable` of their own. The variable is SET, never read here: an
inherited value is overwritten, every time. The shebangs stay, for a person
running a script by hand.

Which index the oracle reads is `_index`'s to say. The
oracles call bare `notmuch`, so `run` is their one injection point: the
child gets `NOTMUCH_CONFIG=<the resolved profile>`, with NOTMUCH_DATABASE
and NOTMUCH_PROFILE dropped (E14), in host mode (the host's own profile) and
store mode alike. `verify` reads no index, so it is never injected and
never refused, in either mode.
"""

from __future__ import annotations

import os
import subprocess
import sys

# A module reference, never `from ._index import ...`.
from . import _index

ORACLE_VAR = "SESSIONMAIL_MHS_ORACLE"

#: The interpreter a bundled script runs on, set by whoever starts it to its
#: own `sys.executable`. Only the bash scripts read it. It does not begin
#: with PYTHON, so the verifier's `python3 -I` leaves it alone.
PYTHON_VAR = "SESSIONMAIL_PYTHON"

#: The verbs whose bundled `session-mail-VERB` is a Python script: these run
#: as `[sys.executable, script]`. The rest are bash. A test checks this set
#: against the scripts' own shebangs.
PYTHON_VERBS = frozenset({"run", "wake"})

#: The NOTMUCH_CONFIG a refused `wake` runs under. It can never exist,
#: because `/dev/null` is not a directory, so every notmuch call inside the
#: oracle exits 1, "cannot load config file" (E15; re-measured with a planted
#: NOTMUCH_DATABASE too). The wake's peek sections
#: then say COULD NOT LOOK, and BOOT and COUNTS, which read files, render.
REFUSED_CONFIG = "/dev/null/sessionmail-store-index-refused"

#: Reads no index: `session-mail-verify` runs only ssh-keygen, and `message
#: verify` finds its file by walking the box. Never injected, never refused:
#: its exit codes are verdicts (2 is "unsigned"), so a refusal would lie.
NO_INDEX = frozenset({"verify"})

#: Has a could-not-look path of its own, so on a refusal it runs under
#: REFUSED_CONFIG. Every other verb's oracle reports a failed notmuch call
#: as a miss, so on a refusal it does not run at all.
DEGRADES = frozenset({"wake"})

#: A memory verb refused for want of a profile. It shares 2 with memory's
#: "path not found"; the refusal line on stderr tells the two apart, the way
#: `message mark` says in words which 2 it meant.
EX_REFUSED = 2

#: The ONE opt-in that lets `memory run` execute anything (the interim fix).
#: Off unless it is exactly "1". `run` executes script-mail as the owner.
#: Since the signer fix, an item runs only if its SIGNER passes
#: the three-leg `--trust`: its key has a registry cert whose agent is the
#: item's From. Membership alone checked the mail against the key the mail
#: carries, so anyone who could deliver to a box could sign an item.
#: The opt-in stays until it is ruled away: lifting it is not part of
#: (a). `--list` runs nothing and needs no opt-in. The executor,
#: `mhs/session-mail-run`, holds the same gate under the same name, because
#: it is reachable by path as well as through this door: keep the two
#: spellings identical.
RUN_GATE_VAR = "SESSIONMAIL_MEMORY_RUN"

#: What `memory run` exits when the gate is shut: the executor's own code.
RUN_OFF_EXIT = 4

#: The refusal, one text for both doors (the executor prints its own copy).
RUN_OFF = (f"memory run: OFF. It executes script-mail as you, so it runs "
           f"only on purpose: an item runs only if its signer passes --trust, "
           f"and running at all is opt-in. Nothing was run. Set {RUN_GATE_VAR}=1 "
           f"to run; `memory run PATH --list` shows what would run, and runs nothing.")


def run_allowed(env: dict[str, str] | None = None) -> bool:
    """True only when $SESSIONMAIL_MEMORY_RUN is exactly "1"."""
    env = os.environ if env is None else env
    return env.get(RUN_GATE_VAR, "") == "1"

#: The variable `--trust` reads its registry from, and nothing else (the
#: verifier runs standalone and reads no config).
REGISTRY_VAR = "SESSION_REGISTRY"

#: The mail tool's key naming that registry.
REGISTRY_KEY = "registry"


def configured_registry() -> str | None:
    """`registry` from the mail tool's TEMPLATE and HOST layers (pwd's home,
    `_operator.host_values`, design e.2), `~` expanded against that home.
    Unset, not a non-empty string, or an unreadable file: None, so verify
    says how to set it (`registry-absent`), never a traceback. Shared by
    `message verify --trust` and `memory run`, both of which ask
    the verifier --trust."""
    from . import _operator, _settings   # here, not at import: `_mhs` stays light
    try:
        value = _operator.host_values(_settings.TOOL).get(REGISTRY_KEY)
    except Exception:  # noqa: BLE001 - a bad mail.json is no registry, named by the verifier
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value == "~" or value.startswith("~/"):
        return str(_operator.settings_home() / value[2:])
    return os.path.expanduser(value)


def trust_env(env: dict[str, str] | None = None) -> dict[str, str] | None:
    """A child's environment for a --trust verify: ENV (else this process's)
    plus `SESSION_REGISTRY` from config when the variable is unset; the
    variable wins when set. None when there is nothing to add: the child
    inherits as it is."""
    base = os.environ if env is None else env
    if base.get(REGISTRY_VAR):
        return None if env is None else dict(env)
    registry = configured_registry()
    if registry is None:
        return None if env is None else dict(env)
    return {**base, REGISTRY_VAR: registry}

#: The bundled oracle's directory: every script it runs finds its siblings
#: here (the verbs' shims, mhs-thread, the verifier, the wake's libs).
MHS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mhs")


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """ENV (else this process's environment) plus $SESSIONMAIL_PYTHON set to
    this interpreter: the environment every bundled script is started with."""
    base = os.environ if env is None else env
    return {**base, PYTHON_VAR: sys.executable}


def oracle(verb: str, env: dict[str, str] | None = None) -> list[str]:
    """$SESSIONMAIL_MHS_ORACLE VERB, else the bundled mhs/session-mail-VERB,
    behind `sys.executable` when that script is Python (PYTHON_VERBS)."""
    env = os.environ if env is None else env
    named = env.get(ORACLE_VAR)
    if named:
        return [named, verb]
    script = os.path.join(MHS_DIR, f"session-mail-{verb}")
    if not os.access(script, os.X_OK):
        raise FileNotFoundError(f"no memory oracle: {script} is missing or not executable "
                                f"(a broken sessionmail install)")
    return [sys.executable, script] if verb in PYTHON_VERBS else [script]


def run(argv: list[str], env: dict[str, str] | None = None) -> int:
    """Run the oracle and hand back exactly what it printed, byte for byte,
    on the same streams, and return its exit code. ENV, when given, is the
    child's whole environment (`message verify` adds a configured
    registry); else the child inherits this process's.

    The bytes go through THIS process's sys.stdout / sys.stderr rather than
    an inherited fd: under `memory mcp` the tool result is whatever the
    click command wrote to sys.stdout (CliRunner captures that, not fd 1),
    and with an inherited fd the listing left on the server's stderr while
    the tool answered with empty content (measured).
    On a terminal the two are indistinguishable.

    The child always gets $SESSIONMAIL_PYTHON (`child_env`), so ENV=None
    is this process's environment plus that one variable.

    The child's environment comes from `_index` (see the module doc), in
    either mode. With no profile, the refusal line
    goes to stderr first, once; then `wake` runs under REFUSED_CONFIG and
    every other index-reading verb returns EX_REFUSED without running."""
    verb = verb_of(argv, env)
    if verb not in NO_INDEX:
        resolved = _index.resolve(os.environ if env is None else env)
        if resolved.refused:
            _emit(sys.stderr, f"memory {verb or argv[0]}: {resolved.refusal}\n".encode())
            if verb not in DEGRADES:
                return EX_REFUSED
            env = _store_env(env, REFUSED_CONFIG)
        else:
            env = _store_env(env, str(resolved.config))
    try:
        done = subprocess.run(argv, capture_output=True, env=child_env(env))
    except OSError as exc:
        raise FileNotFoundError(f"{argv[0]}: {exc}") from exc
    _emit(sys.stdout, done.stdout)
    _emit(sys.stderr, done.stderr)
    return done.returncode


def verb_of(argv: list[str], env: dict[str, str] | None = None) -> str:
    """The verb ARGV runs, read the way `oracle` built it: `argv[1]` after a
    named $SESSIONMAIL_MHS_ORACLE, else the `session-mail-VERB` script's
    name (`argv[1]` when `argv[0]` is this interpreter). "" when no shape
    matches, which `run` treats as a verb that reads the index (fail closed)."""
    env = os.environ if env is None else env
    named = env.get(ORACLE_VAR)
    if named and argv and argv[0] == named:
        return argv[1] if len(argv) > 1 else ""
    script = argv[1] if len(argv) > 1 and argv[0] == sys.executable else \
        argv[0] if argv else ""
    name = os.path.basename(script)
    return name[len("session-mail-"):] if name.startswith("session-mail-") else ""


def _store_env(env: dict[str, str] | None, config: str) -> dict[str, str]:
    """The oracle child's environment: ENV (else this process's)
    without the three overrides, and NOTMUCH_CONFIG=CONFIG. The oracles call
    bare `notmuch`, so the variable is the only door (E3, E14)."""
    base = os.environ if env is None else env
    return {**_index.scrub(dict(base)), "NOTMUCH_CONFIG": config}


def _emit(stream, data: bytes) -> None:
    if not data:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data)
    else:
        stream.write(data.decode("utf-8", "replace"))
    stream.flush()
