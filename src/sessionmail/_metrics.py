"""The subject shortener's counter, in the node_exporter textfile
collector.

sessionmail emits no other metric, so this is the fleet's mechanism, not a
library: `NAME.prom` in the collector's directory, rewritten under an
`flock` (so concurrent seats never clobber each other) through a temporary
file and a rename (so the collector never reads it half-written).

The directory is the first of `$SESSIONMAIL_TEXTFILE_DIR`,
`$NODE_EXPORTER_TEXTFILE_DIR` that is SET, else the homebrew collector. It
is written only if it already exists, and never created: a named directory
that is missing writes nothing, and never falls through to the next one.

Best effort: `count` is called after the MTA accepted, and anything that
goes wrong is a notice for the door, never an exception. A sent mail must
not report as failed because a metrics file is unwritable."""

from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path

DIR_VAR = "SESSIONMAIL_TEXTFILE_DIR"
NODE_EXPORTER_VAR = "NODE_EXPORTER_TEXTFILE_DIR"
HOMEBREW_DIR = Path("/opt/homebrew/var/lib/node_exporter/textfile_collector")
FILE = "sessionmail_send.prom"

SENT = "sessionmail_send_total"
REMOVED = "sessionmail_subject_chars_removed_total"
#: Over the limit and shortened; or folded only, kept apart so a length
#: metric is not inflated by em dashes.
REASONS = ("subject_shortened", "subject_folded")
HELP = {
    SENT: "Mails message send sent with a subject the shortener changed, by reason.",
    REMOVED: "Characters the subject shortener removed from sent subjects.",
}
_SAMPLE = re.compile(r'^(?P<name>[a-z_]+(?:\{[^}]*\})?) (?P<value>\d+)$')


def _sent(reason: str) -> str:
    return f'{SENT}{{result="sent",reason="{reason}"}}'


def textfile_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    for var in (DIR_VAR, NODE_EXPORTER_VAR):
        if env.get(var):
            return Path(env[var])
    return HOMEBREW_DIR


def _read(path: Path) -> dict[str, int]:
    """Our samples in the file; a line that is ours and garbled raises, so
    a bad file is reported and left as it is, never silently reset."""
    if not path.exists():
        return {}
    found = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE.match(line)
        if not m:
            raise ValueError(f"{path.name}: not a sample: {line!r}")
        found[m["name"]] = int(m["value"])
    return found


def _render(values: dict[str, int]) -> str:
    lines = []
    for name in (SENT, REMOVED):
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} counter"]
        keys = [_sent(r) for r in REASONS] if name == SENT else [REMOVED]
        lines += [f"{key} {values.get(key, 0)}" for key in keys]
    return "\n".join(lines) + "\n"


def count(reason: str, removed: int, env: dict[str, str] | None = None) -> str:
    """Add one send for REASON and REMOVED characters (never less than 0).
    Returns "" or the notice the door prints."""
    d = textfile_dir(env)
    if not d.is_dir():
        return ""
    try:
        with open(d / f".{FILE}.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = d / FILE
            values = _read(path)
            values[_sent(reason)] = values.get(_sent(reason), 0) + 1
            values[REMOVED] = values.get(REMOVED, 0) + max(0, removed)
            tmp = d / f".{FILE}.tmp"
            tmp.write_text(_render(values))
            os.replace(tmp, path)
    except Exception as e:   # best effort: ANY failure is a notice
        return f"message send: counter not written: {e}"
    return ""
