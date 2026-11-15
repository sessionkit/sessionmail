"""Service files: sessionmail's word to `sessionkit service` about each of its jobs.

One JSON file per service under `~/.sessionkit/mail/services/`
(`_settings.services_dir`), beside `settings/` and never inside it, in the
schema the kit owns (version SCHEMA). A service that runs once per host is
`services/<name>.json`; one that runs once per agent is
`services/<group>/<agent>.json`. The owner's install writes the file with
`enabled: true`, its uninstall with `enabled: false`, and the file is kept,
so the service is still known on this host. This module is the ONE writer:
each verb builds its record beside its own label (`record`) and makes one
call, `declare`.

THE WRITE IS ATOMIC. `replace_bytes` writes a temp file in the target's own
folder (fchmod 0644, write, flush, fsync), then renames it over the target,
so a reader sees the old file or the new one and never part of either. Any
failure before the rename removes the temp file and leaves the old file
byte-identical. The folder is not fsynced: the file is rewritten at every
install and uninstall, and a rename lost to a power cut leaves the previous
file, which is still a valid declaration. A symlink at the target is
replaced, never followed. Two writers at once: the last rename wins, and
both files were whole.

THE FILE IS REWRITTEN WHOLE. Nothing is merged from the old file, so the
facts follow the release; a hand-edited file, or one that is not JSON, is
replaced.

A FAILED WRITE never undoes the launchd act it goes with: `declare` returns
the COULD_NOT_WRITE line and False, never raising OSError, and the verb
finishes its act, says so, and exits 1. A re-run is the fix.

Stdlib only. sessionmail never imports the kit: the schema is the kit's,
and this module checks the parts of it a writer here can get wrong.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from . import _settings

#: The value of the `service` key: "this file is a service", schema version 1.
SCHEMA = 1
#: Every sessionmail label starts with this; a service's `name` is the rest
#: of it (dots allowed: `seat-agent.<address>`).
LABEL_PREFIX = f"dev.sessionkit.{_settings.PACKAGE}."
#: The keys every file carries, besides exactly one of `label` and `run`.
REQUIRED = ("service", "name", "enabled", "install")
#: The optional keys, in the order a file is written: `uninstall` beside
#: `install`, then the schema's own order. An unknown key is refused here:
#: the schema ignores one, but a builder in this package that passes one has
#: a typo.
OPTIONAL = ("uninstall", "description", "kind", "port", "protocol", "bind", "health",
            "hazard", "agent", "logs", "order", "note")
#: The `hazard` words the schema knows.
HAZARDS = ("call",)
#: The file mode: the file holds no secret, and the plists it describes are 0644.
MODE = 0o644
#: The line a failed write prints. The docs quote it, so a user who sees it
#: knows the launchd act still ran.
COULD_NOT_WRITE = "service could not write {path}: {reason}; the previous file is unchanged"


def _one_name(word: str, what: str) -> str:
    """WORD, checked to be one file name: never a path, never `.`/`..`."""
    if not word or word in (".", "..") or "/" in word or "\\" in word or "\0" in word:
        raise ValueError(f"a service {what} is one file name, not {word!r}")
    return word


def path(stem: str, group: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """`~/.sessionkit/mail/services/<stem>.json`, or `<group>/<stem>.json`
    for a service that runs once per agent. HOME from ENV."""
    folder = _settings.services_dir(env)
    if group is not None:
        folder = folder / _one_name(group, "group")
    return folder / f"{_one_name(stem, 'file stem')}.json"


def name_of(label: str) -> str:
    """The service's name: LABEL with LABEL_PREFIX stripped (never the last
    dotted word, so `seat-agent.<address>` stays whole). Raises ValueError
    for a label that is not sessionmail's."""
    if not isinstance(label, str) or not label.startswith(LABEL_PREFIX) or label == LABEL_PREFIX:
        raise ValueError(f"label {label!r} does not start {LABEL_PREFIX!r}")
    return label[len(LABEL_PREFIX):]


def _is_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def check(rec: Mapping) -> list[str]:
    """The faults the kit's schema names, as one line each; [] when there are
    none: a missing required key, `label` and `run` both or neither, a label
    that does not start LABEL_PREFIX, a name that is not the label's tail,
    the types of the required keys and of the optional keys this package
    writes, and an unknown key."""
    out = [f"missing required key {k!r}" for k in REQUIRED if k not in rec]
    has_label, has_run = "label" in rec, "run" in rec
    if has_label and has_run:
        out.append("both 'label' and 'run': a service has exactly one")
    elif not has_label and not has_run:
        out.append("missing required key 'label' (or 'run')")
    if "service" in rec and (rec["service"] != SCHEMA or isinstance(rec["service"], bool)):
        out.append(f"'service' is {rec['service']!r}, not {SCHEMA}")
    if "name" in rec and not _is_str(rec["name"]):
        out.append("'name' is not a non-empty string")
    if "enabled" in rec and not isinstance(rec["enabled"], bool):
        out.append("'enabled' is not true or false")
    for key in ("install", "uninstall", "agent"):
        if key in rec and not _is_str(rec[key]):
            out.append(f"{key!r} is not a non-empty string")
    if "hazard" in rec and (not isinstance(rec["hazard"], list)
                            or not all(h in HAZARDS for h in rec["hazard"])):
        out.append(f"'hazard' is not a list of {', '.join(map(repr, HAZARDS))}")
    if has_run and (not isinstance(rec["run"], list) or not rec["run"]
                    or not all(isinstance(w, str) for w in rec["run"])):
        out.append("'run' is not a list of strings")
    if has_label:
        try:
            tail = name_of(rec["label"])
        except ValueError:
            out.append(f"'label' {rec['label']!r} does not start {LABEL_PREFIX!r}")
        else:
            if "name" in rec and rec["name"] != tail:
                out.append(f"'name' {rec['name']!r} is not the label's tail {tail!r}")
    known = {*REQUIRED, "label", "run", *OPTIONAL}
    out += [f"unknown key {k!r}" for k in rec if k not in known]
    return out


def record(*, enabled: bool, install: str, label: str | None = None,
           run: list[str] | None = None, name: str | None = None, **optional) -> dict:
    """The file's dict, in the written key order: service, name, label or
    run, enabled, install, then OPTIONAL's order. NAME defaults to the
    label's tail. A fault `check` finds raises ValueError: that is this
    package's own bug, never something to show a user."""
    if name is None and label is not None:
        try:
            name = name_of(label)
        except ValueError:
            name = None                 # check() names the bad label
    rec: dict = {"service": SCHEMA}
    if name is not None:
        rec["name"] = name
    if label is not None:
        rec["label"] = label
    if run is not None:
        rec["run"] = list(run)
    rec["enabled"] = enabled
    rec["install"] = install
    for k in OPTIONAL:
        if optional.get(k) is not None:
            rec[k] = optional[k]
    for k in optional:
        if k not in OPTIONAL:
            rec[k] = optional[k]
    problems = check(rec)
    if problems:
        raise ValueError("bad service record: " + "; ".join(problems))
    return rec


def render(rec: Mapping) -> str:
    """The file's text: two-space JSON in the record's own key order, one
    trailing newline."""
    return json.dumps(rec, indent=2) + "\n"


def replace_bytes(target: Path, data: bytes, mode: int = MODE) -> None:
    """Put DATA at TARGET atomically: a temp file in TARGET's folder, fchmod
    MODE, write, flush, fsync, then one rename. On any failure the temp file
    is removed, TARGET is left as it was, and the error re-raised. The
    folder must exist."""
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def write(rec: Mapping, target: Path) -> Path:
    """Write REC to TARGET, making its folder first. Raises OSError."""
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_bytes(target, render(rec).encode())
    return target


def declare(rec: Mapping, target: Path) -> tuple[str, bool]:
    """The one call each verb makes: (the line to print, whether the write
    held). A write that fails gives the COULD_NOT_WRITE line and False; it
    never raises OSError."""
    try:
        write(rec, target)
    except OSError as exc:
        reason = exc.strerror or str(exc) or type(exc).__name__
        return COULD_NOT_WRITE.format(path=target, reason=reason), False
    return f"service {target} (enabled: {json.dumps(rec['enabled'])})", True
