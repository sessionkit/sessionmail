"""The project's OpenSpec root, read through the pointer.

`openspec/` in this tree holds ONE file, `config.yaml`, and it reads
`store: sessionmail`. The specs, the open changes and RATIFICATION.md are kept
in the project's OpenSpec store, and the openspec CLI resolves that store
from the pointer. A test that reads a spec file goes through `spec_file()`,
never through a path under `openspec/` in this tree.

The suite runs in shapes where the store cannot be reached, and each one
SKIPS, naming why:

- the release export: `.gitattributes` export-ignores `openspec/`, so the
  pointer is absent and untracked (`_export.export_ignored` skips);
- the openspec CLI is not on PATH: nothing can resolve the store;
- the store is not registered on this machine (`openspec store list`).

It FAILS, loudly, where skipping would let a test pass vacuously: a tracked
pointer that is missing or says anything but `store: <id>`, a CLI that is
there but errors, and a file that is not in a store that did resolve.

The store registry is the MACHINE's, kept under the user's own HOME, and the
suite's sandbox moves HOME (tests/conftest.py). So the CLI runs under the
environment this module saw at import, before any fixture ran, and with
`OPENSPEC_TELEMETRY=0`: the suite is network-free, and with telemetry off the
CLI writes nothing. `openspec store list` only reads.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _export import export_ignored

#: The pointer, relative to the repo root.
POINTER = "openspec/config.yaml"
#: What the pointer may say: one `store: <id>` line, nothing else.
POINTER_LINE = re.compile(r"store: (?P<id>[a-z0-9][a-z0-9-]*)\n?")
#: The environment at import (collection time): the real HOME, before the
#: sandbox moves it. The CLI reads the store registry under it.
_CLI_ENV = {**os.environ, "OPENSPEC_TELEMETRY": "0"}


def pointer_store() -> str:
    """The store id `openspec/config.yaml` names. SKIPS in the release
    export (absent and untracked); fails on a pointer that says more."""
    text = export_ignored(POINTER).read_text()
    match = POINTER_LINE.fullmatch(text)
    assert match, f"{POINTER} must read exactly 'store: <id>', not {text!r}"
    return match.group("id")


@functools.lru_cache(maxsize=None)
def _store_root(store: str) -> Path | str:
    """The registered root of STORE, or the reason it cannot be had. One
    CLI run per session: node starts slowly, and the answer cannot change."""
    cli = shutil.which("openspec")
    if cli is None:
        return f"the openspec CLI is not on PATH: cannot resolve OpenSpec store '{store}'"
    run = subprocess.run([cli, "store", "list", "--json"], capture_output=True, text=True,
                         env=_CLI_ENV, timeout=120)
    assert run.returncode == 0, f"`openspec store list --json` exit {run.returncode}: {run.stderr}"
    for entry in json.loads(run.stdout).get("stores", []):
        if entry.get("id") == store:
            return Path(entry["root"])
    return (f"OpenSpec store '{store}' is not registered on this machine "
            f"(`openspec store register <path> --id {store}`)")


def openspec_root() -> Path:
    """The `openspec/` directory the pointer resolves to, in the store."""
    found = _store_root(pointer_store())
    if isinstance(found, str):
        pytest.skip(found)
    root = found / "openspec"
    assert root.is_dir(), f"the store resolved to {found}, which has no openspec/"
    return root


def spec_file(rel: str) -> Path:
    """REL (e.g. `specs/help/spec.md`, `changes/<name>/specs/<cap>/spec.md`)
    inside the resolved OpenSpec root. The file must be there: a store that
    resolved but lacks it fails on the read, never skips."""
    assert not rel.startswith("openspec/"), f"{rel}: name the path inside the OpenSpec root"
    return openspec_root() / rel
