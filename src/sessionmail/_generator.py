"""Where the map generator (`vmailbox-gen`) is, asked one way by every caller.

Before this module there were three lookups with
two rules: `_seat._run_generator`, `_archive.generator` and
`_drop.generator` each built their own list of candidates. This is the one
lookup they share. It is a module of its own because `_seat.py` and
`_drop.py` are edited by other cards (`_birth.py` was split for the same
reason).

The order:

1. ``$SESSIONMAIL_VMAILBOX_GEN``. A SET variable is the ONLY candidate:
   when its file is missing, the answer is None and nothing later is asked.
   This fails closed on purpose. The test suite's conftest sets it to a path
   that does not exist, so that a test that forgets its fixture can never
   reach the generator that writes the LIVE map. An
   operator's pin means "this copy", never "this copy or any other".
1b. With the variable unset, the settings file's ``postfix.generator``: a
   pin of the same kind, the only candidate when set.
2. ``vmailbox-gen`` on the PATH in the env the caller was handed (the
   process PATH when that env names none).
3. ``<mail root>/.postfix/vmailbox-gen``.
4. The copy shipped inside this package, LAST, and only when the caller
   asks for it (``packaged=True``).

Step 4 is for READ-ONLY callers only. Today that is the mail drop, which
runs the generator's print modes on every send and writes no postfix map.
The callers that WRITE a live map (`_seat._run_generator`, and `_archive`,
which regenerates through it) pass ``packaged=False`` and stop at step 3.
Making the packaged copy the one that writes a live map is reserved to
the project owner (`openspec/changes/vmailbox-gen-comes-home`), so the packaged copy is
never a writer. The tests pin both orders.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: The pin. Set, it is the only candidate (see the module docstring).
VAR = "SESSIONMAIL_VMAILBOX_GEN"
#: The generator's file name, on PATH and in `<mail root>/.postfix/`.
NAME = "vmailbox-gen"
#: The copy shipped inside this package. Read-only callers only.
PACKAGED = Path(__file__).parent / "postfix" / NAME

#: The `source` of a `Found`, one per step, in order.
SOURCE_ENV = "env"
SOURCE_FILE = "settings file"
SOURCE_PATH = "PATH"
SOURCE_MAIL_ROOT = "mail root"
SOURCE_PACKAGED = "packaged"


@dataclass(frozen=True)
class Found:
    """A generator file that exists, and the step that found it."""
    path: str
    source: str


def candidates(env: Mapping[str, str], mail_root: Path, *, packaged: bool
               ) -> list[tuple[str | None, str]]:
    """The (path, source) pairs `find` tries, in order. A path is None when
    its step has nothing to offer (an unset pin, nothing on PATH)."""
    pin = env.get(VAR)
    if pin:
        return [(pin, SOURCE_ENV)]
    filed = _file_pin(env)
    if filed is not None:
        return [(filed, SOURCE_FILE)]
    steps: list[tuple[str | None, str]] = [
        (shutil.which(NAME, path=env.get("PATH")), SOURCE_PATH),
        (str(Path(mail_root) / ".postfix" / NAME), SOURCE_MAIL_ROOT),
    ]
    if packaged:
        steps.append((str(PACKAGED), SOURCE_PACKAGED))
    return steps


def find(env: Mapping[str, str], mail_root: Path, *, packaged: bool) -> Found | None:
    """The first candidate that is a file, else None.

    PACKAGED is required and has no default, so every caller says which
    order it is on: True for a read-only caller (the drop), False for a
    caller that writes a live map."""
    for path, source in candidates(env, mail_root, packaged=packaged):
        if path and os.path.isfile(path):
            return Found(path, source)
    return None


def expected(env: Mapping[str, str], mail_root: Path) -> str:
    """The path to name when `find` found nothing: the pin when one is set,
    else the mail root's copy (the last step a writing caller asks)."""
    return env.get(VAR) or _file_pin(env) or str(Path(mail_root) / ".postfix" / NAME)


def _file_pin(env: Mapping[str, str]) -> str | None:
    """The settings file's `postfix.generator`, read only where the variable
    is unset: like the variable, a set value is the ONLY candidate."""
    from . import _settings              # here, not at import: _settings is the higher layer
    return _settings.file_path("postfix.generator", env)


def spawn_env(env: Mapping[str, str]) -> dict[str, str] | None:
    """The environment for a generator child when the settings file has a
    value the script reads from a variable and ENV does not set it: a copy of
    ENV with that variable added. None when there is nothing to add, so the
    caller spawns exactly as before. Only the suffix is passed: the script's
    root and host come from its own flags (`--root`, `--host`)."""
    from . import _magicdns, _settings
    if (env.get(_magicdns.SUFFIX_VAR) or "").strip():
        return None
    filed = _settings.file_text(_magicdns.FILE_KEY, env)
    if filed is None:
        return None
    return {**env, _magicdns.SUFFIX_VAR: filed}


def argv(path: str, args: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """The command that runs the generator at PATH: by its own shebang when
    it is executable, else through bash, so a copy that lost its execute bit
    in an install still runs."""
    if os.access(path, os.X_OK):
        return [path, *args]
    return [shutil.which("bash", path=env.get("PATH")) or "/bin/bash", path, *args]
