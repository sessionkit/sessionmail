"""Keep the host index fresh by refreshing it where it is read.

`message setup` builds the index once and schedules nothing, so before this
module mail delivered after setup stayed invisible to every reader that asks
the index: `memory wake` RECENT, `memory ls|tree|cat|run`, `message mark` on
a box that is not the seat's own. Nothing is installed to fix that (no
LaunchAgent, no systemd unit). The readers refresh the index themselves:

- `_mhs.run`, the one door for the `memory` verbs, refreshes BEFORE the
  oracle starts: those readers cannot tell a stale answer from a true empty
  one;
- `message mark` refreshes on a MISS only, once per invocation, after the
  seat's own boxes have not answered either.

PROPOSED, not ratified: `openspec/changes/index-refresh/`.

One refresh is `notmuch --config=<profile> new --no-hooks --quiet`, host mode
only, bounded by `notmuch.refresh` (`SESSIONMAIL_NOTMUCH_REFRESH`, seconds,
default 2; 0 turns it off). It writes only the Xapian database, a projection
of the maildir that notmuch can rebuild: no file moves out of `new/`, no flag
is added, no mark is written, and `--no-hooks` writes no tag. So a peek-only
reader stays peek-only.

At the bound it sends SIGINT, never SIGTERM. MEASURED (notmuch 0.40, on
scratch roots):

    6,000 new messages, SIGTERM at 0.8 s   rc -15, 0 indexed (all lost)
    the same, SIGINT at 0.8 s              rc 1, 4,095 indexed (committed)
    a `new` waiting on the write lock,     exited in 0.008 s; the holder
      SIGINT at 1.0 s                      went on, rc 0, all indexed

SIGTERM throws away everything before notmuch's first autocommit, so a
backlog bigger than one bound would never drain (a livelock). SIGINT commits
what notmuch has, so every refresh moves the index forward, and a refresh
that lands behind a live writer (a host's own `notmuch new` job) skips at
the bound instead of blocking the reader. GRACE is for a notmuch that
ignores SIGINT; then it is killed.

The wait is `communicate`, never `Popen.wait` with a PIPE open: notmuch
writes one `Note: Ignoring non-mail file` line per non-mail file even under
`--quiet` (3,000 such files gave 754,893 bytes, MEASURED), which fills a
pipe, stalls notmuch on its write, and under `wait` timed out with nothing
indexed, every time.

A refresh never raises, and the reader always goes on to read. A refresh
that did not finish clean returns ONE line for the caller to put on stderr;
a clean one, or one that did not apply, returns "".
"""

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess

# Module references, never `from ._x import ...`: `_index` and `_notmuch`
# import each other, so their names are looked up at call time.
from . import _index, _notmuch

VAR = "SESSIONMAIL_NOTMUCH_REFRESH"
KEY = "notmuch.refresh"

#: Seconds a refresh may take before it is stopped. Covers the slowest
#: MEASURED fleet-sized refresh (1.11 s at load 21-30) with margin.
DEFAULT_SECONDS = 2.0

#: Seconds notmuch gets to commit and exit after the SIGINT, before it is
#: killed. A test shortens it.
GRACE = 5.0

#: What a SIGINT'd notmuch exits: 1 when it caught the signal and stopped
#: cleanly ("Stopping..."), -2 when it died of it (a lock-waiter).
_STOPPED = (1, -signal.SIGINT)

#: Every note ends on this, so a reader knows what it is reading.
_AS_IT_WAS = "reading the index as it was"


def seconds(env: dict[str, str] | None = None) -> float:
    """The bound: the variable, else the settings file's `notmuch.refresh`,
    else DEFAULT_SECONDS. 0 is off. A value that is not a finite number of
    0 or more is the default, wherever it was set."""
    env = os.environ if env is None else env
    given = (env.get(VAR) or "").strip()
    if given:
        try:
            value = float(given)
        except ValueError:
            return DEFAULT_SECONDS
        return value if math.isfinite(value) and value >= 0 else DEFAULT_SECONDS
    from . import _settings              # here, not at import: _settings is the higher layer
    filed = _settings.file_value(KEY, env)
    if (isinstance(filed, (int, float)) and not isinstance(filed, bool)
            and math.isfinite(filed) and filed >= 0):
        return float(filed)
    return DEFAULT_SECONDS


def enabled(env: dict[str, str] | None = None) -> bool:
    """A refresh would run: the bound is not 0 and the index is the host's
    own (`_index.resolve`, never refused). Store mode never refreshes: a
    store's index is kept by whoever keeps the store."""
    env = os.environ if env is None else env
    return seconds(env) > 0 and _index.resolve(env).host


def _last_line(data: bytes | None) -> str:
    lines = [ln.strip() for ln in (data or b"").decode("utf-8", "replace").splitlines()]
    lines = [ln for ln in lines if ln]
    return lines[-1] if lines else ""


def refresh(env: dict[str, str] | None = None) -> str:
    """Run one bounded `notmuch new --no-hooks` on the host profile. Returns
    "" when it finished clean or did not apply (off, store mode, a refused
    profile, no notmuch: each is silent, because the reader reports it
    itself), else one line saying why the index is read as it was."""
    env = os.environ if env is None else env
    bound = seconds(env)
    if bound <= 0 or not _index.resolve(env).host:
        return ""
    exe = _notmuch.binary(env)
    if shutil.which(exe) is None and not os.path.exists(exe):
        return ""
    try:
        prefix, child = _notmuch._engine(exe, env)
    except _notmuch.CouldNotLook:
        return ""
    argv = [*prefix, "new", "--no-hooks", "--quiet"]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, env=child)
    except OSError as exc:
        return f"index refresh: {exe} could not be run: {exc}; {_AS_IT_WAS}"
    interrupted = False
    try:
        _out, err = proc.communicate(timeout=bound)
    except subprocess.TimeoutExpired:
        interrupted = True
        proc.send_signal(signal.SIGINT)
        try:
            _out, err = proc.communicate(timeout=GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return (f"index refresh: `{exe} new` passed the {bound:g}s bound "
                    f"({KEY}), ignored SIGINT for {GRACE:g}s and was killed; {_AS_IT_WAS}")
    rc = proc.returncode
    if rc == 0:
        return ""
    if interrupted and rc in _STOPPED:
        return (f"index refresh: `{exe} new` did not finish within {bound:g}s ({KEY}); "
                f"stopped with SIGINT, keeping what it indexed; {_AS_IT_WAS}")
    detail = _last_line(err)
    return (f"index refresh: `{exe} new` exited {rc}" + (f": {detail}" if detail else "")
            + f"; {_AS_IT_WAS}")
