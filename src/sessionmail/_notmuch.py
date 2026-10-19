"""The one search engine. `openspec/specs/pager/spec.md`: "Search SHALL be
notmuch ... The package SHALL contain no second index, filter language or
query planner."

So this module is a shim and nothing else: it hands a query to notmuch and
turns the answer into plain dicts. No caching, no re-ranking, no rewriting
of the query - a subscription's alerts are exactly its query's results, and
the moment we edit a query on the way past, that requirement is a lie.

One call per subscription: `notmuch show --format=json --body=false
--entire-thread=false QUERY`, which is the only notmuch output that carries
the Message-ID *and* the headline in one pass. Search's summary output
carries the headline but names threads, not messages, and marks are per
message (`help/message/mark.txt`), so summary output cannot be filtered by
what this seat has already finished with.

The query does travel on notmuch's argv, where `ps` can read it. That is
not the argv rule in specs/pager ("A query rides the environment, never
argv"), which governs how a CALLER hands a query to our listener; notmuch
takes its search terms on argv and there is no other door.

Which index a call reads is `_index`'s to say: each of
the three calls below takes its argv prefix and child environment from
`_engine`, and none carries a resolution of its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

# A module reference, never `from ._index import ...`: `_index` imports this
# module for `CouldNotLook`, so its names are looked up at call time.
from . import _index

#: Overridable so the tests can be hermetic - no maildir is touched by the
#: suite, and a test can make the engine fail on purpose (Charter 3: a gate
#: you have never watched fail is a decoration).
BINARY_VAR = "SESSIONMAIL_NOTMUCH"
DEFAULT_BINARY = "notmuch"

#: A ride-along runs after every tool call. It may not hang.
TIMEOUT_VAR = "SESSIONMAIL_NOTMUCH_TIMEOUT"
DEFAULT_TIMEOUT = 10.0


class CouldNotLook(Exception):
    """The index could not be read. Exit 2 on every page; never a quiet 3."""


@dataclass(frozen=True)
class Message:
    id: str
    timestamp: int
    sender: str
    subject: str
    date_relative: str
    #: The files notmuch holds under this id (its JSON `filename`, a list
    #: since notmuch 0.26; a bare string is taken too). Empty when the
    #: answer carries none. `message mark` reads it for 3.28: an id whose
    #: every file is a draft is not marked.
    filenames: tuple[str, ...] = ()


def binary(env: dict[str, str] | None = None) -> str:
    """The variable, else the settings file's `notmuch.command`, else notmuch."""
    env = os.environ if env is None else env
    from . import _settings              # here, not at import: _settings is the higher layer
    return env.get(BINARY_VAR) or _settings.file_path("notmuch.command", env) or DEFAULT_BINARY


def timeout(env: dict[str, str] | None = None) -> float:
    """The variable, else the settings file's `notmuch.timeout` (a number
    above 0), else DEFAULT_TIMEOUT. A value that is not one is the default."""
    env = os.environ if env is None else env
    given = env.get(TIMEOUT_VAR)
    if given:
        try:
            return float(given)
        except ValueError:
            return DEFAULT_TIMEOUT
    from . import _settings
    filed = _settings.file_value("notmuch.timeout", env)
    if isinstance(filed, (int, float)) and not isinstance(filed, bool) and filed > 0:
        return float(filed)
    return DEFAULT_TIMEOUT


def _engine(exe: str, env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """(argv prefix, child env) for one notmuch call, by `_index.resolve`:
    `[exe, --config=<profile>]` and ENV without NOTMUCH_CONFIG,
    NOTMUCH_DATABASE and NOTMUCH_PROFILE, each of which would override the
    profile (E3, E14). Host mode's profile is the host's own (`sessionkit`
    by default), store mode's the store's. Raises CouldNotLook, naming the
    missing profile, when there is none: never the default notmuch config,
    and never a host-wide answer in store mode (SC3)."""
    resolved = _index.require(env)
    return [exe, f"--config={resolved.config}"], _index.scrub(dict(env))


def _flatten(node) -> list[dict]:
    """notmuch's show JSON is a forest of [message, [replies]] pairs."""
    out: list[dict] = []
    if isinstance(node, list):
        for child in node:
            out.extend(_flatten(child))
    elif isinstance(node, dict) and "id" in node:
        out.append(node)
        out.extend(_flatten(node.get("replies") or []))
    return out


def _filenames(value) -> tuple[str, ...]:
    """notmuch's `filename`: a list of paths (0.26 on), or one path."""
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list):
        return tuple(str(v) for v in value if v)
    return ()


def search(query: str, env: dict[str, str] | None = None) -> list[Message]:
    """Every message matching QUERY, oldest first. Raises CouldNotLook."""
    env = os.environ if env is None else env
    exe = binary(env)
    if shutil.which(exe) is None and not os.path.exists(exe):
        raise CouldNotLook(f"no search engine: {exe!r} is not on the PATH")
    prefix, child = _engine(exe, env)
    argv = [*prefix, "show", "--format=json", "--body=false",
            "--entire-thread=false", query]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout(env), env=child
        )
    except subprocess.TimeoutExpired as exc:
        raise CouldNotLook(f"{exe} did not answer within {timeout(env):g}s") from exc
    except OSError as exc:
        raise CouldNotLook(f"{exe} could not be run: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise CouldNotLook(
            f"{exe} exited {done.returncode}"
            + (f": {detail[0]}" if detail else "")
        )
    try:
        parsed = json.loads(done.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise CouldNotLook(f"{exe} did not answer in JSON: {exc}") from exc

    messages = []
    for raw in _flatten(parsed):
        headers = raw.get("headers") or {}
        messages.append(
            Message(
                id=str(raw.get("id", "")).strip("<>"),
                timestamp=int(raw.get("timestamp") or 0),
                sender=str(headers.get("From", "")),
                subject=str(headers.get("Subject", "")),
                date_relative=str(raw.get("date_relative", "")),
                filenames=_filenames(raw.get("filename")),
            )
        )
    messages.sort(key=lambda m: (m.timestamp, m.id))
    return messages


#: `openspec/specs/pager/spec.md`, "A wake prints the message, never cats
#: the file": the headers and the text part, never the raw file, never a
#: multipart wrapper, never the HTML part. notmuch already decodes parts,
#: so this walks its answer rather than opening the maildir at all.
HEADERS_SHOWN = ("Date", "From", "To", "Cc", "Subject", "Message-ID")


def revision(env: dict[str, str] | None = None) -> int | None:
    """The index's modification counter (`notmuch count --lastmod '*'`),
    or None when the engine cannot say.

    Every `notmuch new` that indexes a message raises it by at least one,
    so `lastmod:N..` means "indexed since N": ARRIVAL order, immune to a
    message's own Date header (a delayed relay, a wrong clock, a mail
    written offline). The query is scoped to what is newer than a point,
    and the revision is the "time" that cannot be gamed. None is not an error:
    the caller polls unscoped, as before, and says so. A refusal (no
    profile) is None too: the engine cannot say.
    """
    env = os.environ if env is None else env
    exe = binary(env)
    try:
        prefix, child = _engine(exe, env)
    except CouldNotLook:
        return None
    try:
        done = subprocess.run([*prefix, "count", "--lastmod", "*"],
                              capture_output=True, text=True, timeout=timeout(env),
                              env=child)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    parts = (done.stdout or "").split()
    try:
        return int(parts[2])
    except (IndexError, ValueError):
        return None


def _text_parts(part) -> list[str]:
    out: list[str] = []
    if isinstance(part, list):
        for child in part:
            out.extend(_text_parts(child))
        return out
    if not isinstance(part, dict):
        return out
    kind = str(part.get("content-type", "")).lower()
    if kind.startswith("multipart/"):
        return _text_parts(part.get("content") or [])
    if kind == "text/plain" and isinstance(part.get("content"), str):
        out.append(part["content"])
    return out


def wake_text(message_id: str, env: dict[str, str] | None = None) -> str:
    """The header block and the text body of ONE message, and nothing else."""
    env = os.environ if env is None else env
    exe = binary(env)
    prefix, child = _engine(exe, env)
    argv = [*prefix, "show", "--format=json", "--include-html=false",
            "--entire-thread=false", f"id:{message_id.strip('<>')}"]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout(env), env=child
        )
        parsed = json.loads(done.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise CouldNotLook(f"could not read {message_id}: {exc}") from exc
    if done.returncode != 0:
        raise CouldNotLook(f"{exe} exited {done.returncode} reading {message_id}")

    wanted = message_id.strip("<>")
    messages = _flatten(parsed)
    if not messages:
        raise CouldNotLook(f"{wanted} is not in the index")
    # Pick BY ID, never "the first one back": notmuch can answer with
    # several files for one Message-ID (duplicates), and a wake that
    # printed the wrong message would be worse than no wake at all.
    raw = next((m for m in messages if str(m.get("id", "")).strip("<>") == wanted),
               messages[0])
    headers = raw.get("headers") or {}
    lines = [f"{name}: {headers[name]}" for name in HEADERS_SHOWN if headers.get(name)]
    if not headers.get("Message-ID"):
        lines.append(f"Message-ID: <{raw.get('id', message_id)}>")
    body = "\n".join(_text_parts(raw.get("body") or []))
    return "\n".join(lines) + "\n\n" + body.strip("\n") + "\n"
