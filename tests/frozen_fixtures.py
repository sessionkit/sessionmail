"""Fixture builders for the frozen-turn detector (SM-1004 design.md 2.9).

Shared by do-002's unit tests (`test_frozen_core.py`) and do-003's
monitor-level ones. A `Script` writes Claude Code transcript entries, shaped
like the measured Claude Code 2.1.314 lines, at moments DERIVED from one base:
the project's synthetic Sunday, 2030-06-09 (never another date written out).

    s = Script()                      # t = 0 is BASE
    s.prompt(0).api_request(1)        # a real prompt, then a request at +1 s
    path = s.write(config_dir, sid)   # <config>/projects/x/<sid>.jsonl
    now = s.at(1 + 721)               # wall-clock epoch seconds

Every builder takes OFFSET, seconds after BASE, and returns the script, so
calls chain. `entries` is the list, for `_frozen.fold`.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: t = 0 for every script: the project's earlier synthetic Sunday, UTC.
BASE = datetime(2030, 6, 9, tzinfo=timezone.utc)

#: A session id for scripts that do not care which.
SID = "0d14beee-0000-4000-8000-000000000001"


def stamp(offset: float) -> str:
    """BASE + OFFSET seconds as Claude Code writes it: ISO UTC, ms, `Z`."""
    moment = BASE + timedelta(seconds=offset)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch(offset: float) -> float:
    """BASE + OFFSET seconds as wall-clock epoch seconds (judge's NOW)."""
    return (BASE + timedelta(seconds=offset)).timestamp()


class Script:
    """A transcript under construction. Builders append one entry each."""

    def __init__(self, session: str = SID):
        self.session = session
        self.entries: list[dict] = []
        self._ids = itertools.count(1)

    # ── helpers ──
    def at(self, offset: float) -> float:
        return epoch(offset)

    def _uuid(self) -> str:
        return f"00000000-0000-4000-8000-{next(self._ids):012d}"

    def _base(self, kind: str, offset: float | None, **extra) -> dict:
        entry: dict = {"type": kind, "isSidechain": False, "uuid": self._uuid(),
                       "sessionId": self.session}
        if offset is not None:
            entry["timestamp"] = stamp(offset)
        entry.update(extra)
        return entry

    def add(self, entry: dict) -> Script:
        self.entries.append(entry)
        return self

    # ── the entries ──
    def prompt(self, offset: float, text: str = "do the thing", **extra) -> Script:
        """A real prompt: a user entry with string content (dzr's predicate)."""
        return self.add(self._base("user", offset,
                                   message={"role": "user", "content": text}, **extra))

    def meta(self, offset: float, text: str = "<local-command-stdout/>") -> Script:
        """An isMeta user entry with string content: never a real prompt (2j)."""
        return self.prompt(offset, text, isMeta=True)

    def compact_summary(self, offset: float) -> Script:
        """An isCompactSummary user entry: never a real prompt (2i)."""
        return self.prompt(offset, "This session is being continued from a previous "
                                   "conversation.", isCompactSummary=True)

    def api_request(self, offset: float, source: str = "repl_main_thread") -> Script:
        """A main-thread `api-request`: written at dispatch, one per attempt.
        Like the real line, it carries no isSidechain field."""
        return self.add({"type": "api-request", "sessionId": self.session,
                         "id": self._uuid(), "timestamp": stamp(offset),
                         "querySource": source})

    def assistant(self, offset: float, *tool_ids: str, kind: str = "text") -> Script:
        """An assistant entry: one `tool_use` block per TOOL_IDS, else one
        block of KIND (`text` or `thinking`)."""
        if tool_ids:
            content = [{"type": "tool_use", "id": t, "name": "Bash", "input": {}}
                       for t in tool_ids]
        else:
            content = [{"type": kind, kind: "..."}]
        return self.add(self._base("assistant", offset,
                                   message={"role": "assistant", "content": content}))

    def thinking(self, offset: float) -> Script:
        return self.assistant(offset, kind="thinking")

    def tool_result(self, offset: float, *tool_ids: str, output: str = "ok") -> Script:
        content = [{"type": "tool_result", "tool_use_id": t, "content": output}
                   for t in tool_ids]
        return self.add(self._base("user", offset,
                                   message={"role": "user", "content": content}))

    def turn_duration(self, offset: float) -> Script:
        return self.add(self._base("system", offset, subtype="turn_duration",
                                   durationMs=1000, isMeta=False))

    def interrupt(self, offset: float, for_tool: bool = False) -> Script:
        text = "[Request interrupted by user" + (" for tool use]" if for_tool else "]")
        return self.add(self._base("user", offset, message={
            "role": "user", "content": [{"type": "text", "text": text}]}))

    def api_error(self, offset: float) -> Script:
        return self.add(self._base("system", offset, subtype="api_error",
                                   retryInMs=500, retryAttempt=1, maxRetries=10))

    def enqueue(self, offset: float, content: str = "a page") -> Script:
        """An outside arrival: never the clock (D-1). No uuid, like the real one."""
        return self.add({"type": "queue-operation", "operation": "enqueue",
                         "timestamp": stamp(offset), "sessionId": self.session,
                         "content": content})

    def dequeue(self, offset: float) -> Script:
        return self.add({"type": "queue-operation", "operation": "dequeue",
                         "timestamp": stamp(offset), "sessionId": self.session})

    def queued_command(self, offset: float, prompt: str = "a page") -> Script:
        """A `queued_command` attachment: an arrival folded in; never the clock."""
        return self.add(self._base("attachment", offset, attachment={
            "type": "queued_command", "prompt": prompt}))

    def attachment(self, offset: float, kind: str = "hook_success") -> Script:
        return self.add(self._base("attachment", offset, attachment={"type": kind}))

    def sidechain(self, entry_builder: str, offset: float, *args) -> Script:
        """The entry ENTRY_BUILDER would add, marked `isSidechain: true`."""
        getattr(self, entry_builder)(offset, *args)
        self.entries[-1]["isSidechain"] = True
        return self

    def untimed(self, kind: str = "cost-state") -> Script:
        """An entry with no timestamp (cost-state, last-prompt, ...)."""
        return self.add({"type": kind, "sessionId": self.session})

    # ── the file ──
    def text(self) -> str:
        return "".join(json.dumps(e) + "\n" for e in self.entries)

    def write(self, config: Path, session: str | None = None,
              project: str = "x") -> Path:
        """Write the transcript as `<config>/projects/<project>/<sid>.jsonl`."""
        folder = Path(config) / "projects" / project
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session or self.session}.jsonl"
        path.write_text(self.text())
        return path

    def append(self, path: Path, since: int) -> int:
        """Append ENTRIES[SINCE:] to PATH; return the new length."""
        with open(path, "a") as fh:
            fh.writelines(json.dumps(e) + "\n" for e in self.entries[since:])
        return len(self.entries)


def frozen_turn(start: float = 0.0) -> Script:
    """Design 2.9's fixture 1a: a real prompt, then one `api-request` a
    second later, then nothing. The clock is `start + 1`."""
    return Script().prompt(start).api_request(start + 1)


def fallback_chain(start: float = 0.0, attempts: int = 4, every: float = 300.0) -> Script:
    """Design 2.9's fixture 1e: a real prompt, then ATTEMPTS `api-request`s
    EVERY seconds apart with no `assistant` entry. `first` is `start + 1`."""
    s = Script().prompt(start)
    for i in range(attempts):
        s.api_request(start + 1 + i * every)
    return s


def watch(home: Path, data: dict | None = None, **env_extra: str):
    """`_settings.frozen()` for a host file holding DATA (default: the watch
    on, every other key its default) under HOME, with ENV_EXTRA (the timers)
    and nothing from the running seat's environment."""
    from sessionmail import _settings

    env = {"HOME": str(home), **env_extra}
    p = _settings.path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"frozen_watch": True} if data is None else data))
    return _settings.frozen(env)
