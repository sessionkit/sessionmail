"""`memory init --record FILE`: the RECORD half. One front-matter line, one commit.

The normative text is the change delta,
`openspec/changes/memory-init/specs/memory/spec.md`, requirement "The record
is a front-matter line in a file the caller names":

- write the root's Message-ID as `mail_root: "<id>"` into FILE's YAML front
  matter, replacing the existing line or inserting one, by a LINE edit that
  keeps every other byte (a YAML round-trip would drop the
  trailing comments live PROJECT.md files carry on `mail_grammar:`);
- commit that one file BY PATH in FILE's repository and report the commit;
- refuse, as usage (exit 1) and BEFORE anything is minted, a FILE that does
  not exist or has no front matter.

It writes `mail_root:` and nothing else. `mail_grammar:` is the taskmaster
wrapper's (design OQ2) and is FROZEN. This module mints nothing and sends
nothing. Its caller is `_init.init()`; `--supersede`'s pointer move is
the same edit with a different id.

Two doors, because the spec asks for a refusal BEFORE the mint and the commit
can only happen AFTER it:

- `precheck(FILE)`: everything that can be known before a send. It is exit 1,
  kind "record". The spec names a missing FILE and a FILE with no front
  matter. Three more are refused here, because each would make the commit
  fail after the mint, which is a worse place to learn it:
  - FILE is not in a git work tree ("commit that one file ... in FILE's
    repository" has no repository to commit in);
  - FILE is untracked (`git commit -- FILE` refuses a path git does not know);
  - FILE differs from HEAD, because the commit would carry someone else's
    edits under init's message.
  A FILE whose front matter names `mail_root:` twice is refused too: the
  reader takes the first, and editing either one is a guess.
- `record(FILE, root_id, result=...)`: the edit and the commit. It returns the
  sha of the commit THIS call made, or None when FILE already names root_id
  (seam.md §2, `InitResult.recorded`).

THE EXIT CODE FOR A RECORD THAT FAILS AFTER A MINT IS ASSIGNED HERE, and it is
2 (`EX_RECORD_FAILED`), kind "record". The delta says only that `init` "SHALL
name both ids and exit non-zero" when the pointer step fails, and its table is
closed ("these exit codes and no others"). Among 1, 2 and 3, 2 is the row that
describes the state left behind: the root is minted and the record does not
name it, so a re-run reads a hint that disagrees with the maildir scan, which
the delta already lists under exit 2. 1 would claim the call was malformed,
and it was not. 3 would claim a signature failed, and none did. seam.md A6
recommends the same code. The delta's exit-2 list names this case ("a
pointer step that failed after the mint").

Every failure after `precheck` (a FILE that changed since it was checked, a
write that fails, a commit that fails) raises `InitError(2, kind="record")`
carrying `result`, which is what the caller had already sent (seam.md A2). The
message names both ids: the one FILE still names, and the new root. When the
commit fails, the edit is undone (only if FILE still holds exactly the bytes
this call wrote), so the pointer does not move. That is the delta's "if step 2
fails no pointer moves" state. A later run completes a SUPERSEDE whose
pointer step failed by the delta's re-run rule ("A new root is a new tree"):
the same `--supersede --record FILE` reads FILE's id as the live root's direct
predecessor, mints nothing new, and calls this again (`_init.run`).

A SYMLINKED FILE IS FOLLOWED: the record is written into, and committed as,
the file the link points at. The link itself is never touched, so it stays a
link. (Replacing the path by a rename would turn a tracked link into a
regular file and leave its target without the record: a commit that exits 0
and records nothing where readers look; such links exist, e.g.
`projects/example/PROJECT.md -> README.md`.) The path is resolved
ONCE, at the top, and every git call runs in the TARGET's directory. A link
whose target does not exist, is not in a git work tree, or is in a DIFFERENT
work tree from the link is refused in `precheck` (exit 1, both paths named):
committing in a repository the caller did not name is a guess.

A concurrent commit is an ordinary failure in the store this writes into. A
ref race (`cannot lock ref 'HEAD'`) or another seat's `index.lock` is retried
a bounded number of times. Anything else raises at once.

The sha is read from the commit's own summary line, never from HEAD
afterwards: HEAD moves under you in a busy store. It is then checked to be a
commit whose only path is FILE.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from . import _maildir
from ._init import EX_NOT_FOUND, EX_USAGE, InitError, InitResult
from .seat_init import own_hooks

#: A record that fails after a mint. See the module docstring for why it is 2.
EX_RECORD_FAILED = EX_NOT_FOUND

#: The `mail_root:` line, exactly as the read half matches it
#: (`_root._MAIL_ROOT`), so the two halves agree on which line is the record.
_MAIL_ROOT = re.compile(r"""^mail_root:[ \t]*(?:"([^"]*)"|'([^']*)'|([^\s#]*))""")
#: A bare Message-ID that can be written between double quotes and read back.
_WRITABLE_ID = re.compile(r"[^\s\"'\\<>]+")
#: `git commit`'s summary line: `[<branch> <sha>] <subject>`, also
#: `[<branch> (root-commit) <sha>]` and `[detached HEAD <sha>]`.
_SUMMARY = re.compile(r"^\[[^\]]*?\s([0-9a-f]{7,64})\]\s", re.MULTILINE)
#: The failures another seat's concurrent commit causes: transient, retried.
_TRANSIENT = ("cannot lock ref", "index.lock': File exists")

ATTEMPTS = 3
BACKOFF_SECONDS = 0.25

#: How git is run: argv after `git -C DIR`. Tests replace it to simulate a
#: race. They never race real processes.
Git = Callable[[Path, list[str]], subprocess.CompletedProcess]


def _pins(cwd: Path, args: list[str]) -> list[str]:
    """What a `commit` here is pinned to. The record is sessionmail's own
    bookkeeping commit: it is never signed, so a user's global
    `commit.gpgsign` (or one exported through GIT_CONFIG_COUNT, which `-c`
    still beats) cannot refuse it, and the store repo's OWN hooks run while a
    global or system hooksPath does not. The user's identity is NOT touched:
    the record is authored by whoever the repo's config or env names."""
    if not args or args[0] != "commit":
        return []
    return ["-c", "commit.gpgsign=false", "-c", f"core.hooksPath={own_hooks(cwd)}"]


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
    try:
        pins = _pins(cwd, args)
        return subprocess.run(["git", "-C", str(cwd), *pins, *args], capture_output=True,
                              text=True, check=False, env=env)
    except OSError as exc:  # no git at all: the same answer as "not a repository"
        return subprocess.CompletedProcess(["git", *args], 127, "", str(exc))
    except RuntimeError as exc:  # the repo's own hooks dir could not be read
        return subprocess.CompletedProcess(["git", *args], 128, "", str(exc))


_git: Git = _run_git
_sleep: Callable[[float], None] = time.sleep


# ── the front matter ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Record:
    """FILE as `precheck` found it: fit to be edited and committed."""

    path: Path
    raw: bytes
    lines: tuple[str, ...]      # FILE's lines, line endings kept
    close: int                  # index of the closing `---`
    at: int | None              # index of the `mail_root:` line, or None
    current: str | None         # the bare id it names, or None
    link: Path | None = None    # the symlink the caller named, when PATH is its target

    @property
    def repo_dir(self) -> Path:
        return self.path.parent


def _usage(path: Path | str, why: str) -> InitError:
    return InitError(f"--record {path}: {why}. Nothing minted", EX_USAGE, kind="record")


def _parse(path: Path) -> Record:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except FileNotFoundError:
        raise _usage(path, "no such file") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise _usage(path, f"cannot read it ({exc})") from None
    lines = tuple(text.splitlines(keepends=True))
    if not lines or lines[0].rstrip("\r\n").strip() != "---":
        raise _usage(path, "no YAML front matter (a '---' block at the top)")
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n").strip() == "---"),
                 None)
    if close is None:
        raise _usage(path, "its YAML front matter is never closed (no second '---')")
    found = [i for i in range(1, close) if _MAIL_ROOT.match(lines[i])]
    if len(found) > 1:
        raise _usage(path, f"its front matter carries mail_root: {len(found)} times "
                           f"(lines {', '.join(str(i + 1) for i in found)}); a record names one root")
    at = found[0] if found else None
    current = None
    if at is not None:
        m = _MAIL_ROOT.match(lines[at])
        assert m is not None
        current = _maildir.bare_id(next(g for g in m.groups() if g is not None)) or None
    return Record(path=path, raw=raw, lines=lines, close=close, at=at, current=current)


def _resolve(path: str | os.PathLike[str]) -> tuple[Path, Path | None]:
    """(the file to edit and commit, the symlink the caller named or None).
    Resolved once: directory links in the path are followed too, so every
    later git call addresses the real file in its real directory."""
    given = Path(path).expanduser().absolute()
    return given.resolve(), (given if given.is_symlink() else None)


def _toplevel(d: Path) -> Path | None:
    top = _git(d, ["rev-parse", "--show-toplevel"])
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 and top.stdout.strip() else None


def precheck(path: str | os.PathLike[str]) -> Record:
    """Refuse, exit 1 kind "record", anything that would stop the record
    AFTER a mint: FILE missing, no front matter, `mail_root:` twice, not in a
    git work tree, untracked, or differing from HEAD; and, for a symlink, a
    target that is missing or lies in another work tree. Reads only.

    `init()` calls this before its first send."""
    p, link = _resolve(path)
    named = f"{link} (a symlink to {p})" if link else str(p)
    if link and not p.exists():
        raise _usage(named, "the link's target does not exist")
    rec = dataclasses.replace(_parse(p), link=link)
    inside = _git(rec.repo_dir, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise _usage(named, "not in a git work tree, so there is no repository to commit it in")
    if link:
        here, there = _toplevel(link.parent), _toplevel(p.parent)
        if here is None or here != there:
            raise _usage(named, f"the link is in {here or 'no work tree'} but its target is "
                                      f"in {there}; init commits only in the repository it was named in")
    tracked = _git(rec.repo_dir, ["ls-files", "--error-unmatch", "--", p.name])
    if tracked.returncode != 0:
        raise _usage(named, "untracked, and `git commit -- FILE` commits only a path git "
                        "already knows. Commit it once by hand first")
    clean = _git(rec.repo_dir, ["diff", "--quiet", "HEAD", "--", p.name])
    if clean.returncode == 1:
        raise _usage(named, "it has uncommitted changes, which init's commit would carry "
                        "under its own message. Commit or discard them first")
    if clean.returncode != 0:
        raise _usage(named, f"cannot compare it with HEAD ({clean.stderr.strip()})")
    return rec


# ── the edit and the commit ───────────────────────────────────────────────


def edited(rec: Record, root_id: str) -> bytes:
    """FILE's bytes with `mail_root: "<root_id>"`: the line replaced (keeping
    whatever followed the value, such as a comment) or inserted just before
    the closing `---`. Every other line is byte-identical."""
    lines = list(rec.lines)
    value = f'mail_root: "{root_id}"'
    if rec.at is not None:
        m = _MAIL_ROOT.match(lines[rec.at])
        assert m is not None
        lines[rec.at] = value + lines[rec.at][m.end():]
    else:
        # The line above the fence always ends in a newline (a line follows
        # it), so the new line takes that line's ending: CRLF files stay CRLF.
        eol = "\r\n" if lines[rec.close - 1].endswith("\r\n") else "\n"
        lines.insert(rec.close, value + eol)
    return "".join(lines).encode("utf-8")


def _replace(path: Path, data: bytes) -> None:
    """Write DATA to PATH whole: a temp file beside it, then a rename, so a
    reader never sees half a record. The file's mode is kept."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _commit(rec: Record, message: str) -> str:
    """`git commit -m MESSAGE -- FILE`, retried on a concurrent-commit race.
    Returns the full sha read from the commit's own summary line. Raises
    RuntimeError with git's words on anything else."""
    name = rec.path.name
    for attempt in range(1, ATTEMPTS + 1):
        done = _git(rec.repo_dir, ["commit", "-m", message, "--", name])
        if done.returncode == 0:
            break
        said = (done.stderr or done.stdout).strip()
        if attempt < ATTEMPTS and any(t in said for t in _TRANSIENT):
            _sleep(BACKOFF_SECONDS * attempt)
            continue
        tries = f" (after {attempt} tries)" if attempt > 1 else ""
        raise RuntimeError(f"git commit failed{tries}: {said}")
    m = _SUMMARY.search(done.stdout)
    if not m:
        raise RuntimeError(f"committed, but git's summary line names no commit: "
                           f"{done.stdout.strip()!r}")
    full = _git(rec.repo_dir, ["rev-parse", "--verify", "--quiet", f"{m.group(1)}^{{commit}}"])
    prefix = _git(rec.repo_dir, ["rev-parse", "--show-prefix"])
    sha = full.stdout.strip()
    if full.returncode != 0 or not sha:
        raise RuntimeError(f"committed {m.group(1)}, but it does not resolve to a commit")
    paths = _git(rec.repo_dir, ["show", "--no-renames", "--name-only", "--format=", sha])
    want = prefix.stdout.strip() + name
    if paths.returncode != 0 or paths.stdout.split() != [want]:
        raise RuntimeError(f"commit {sha} does not hold exactly {want}: {paths.stdout.split()}")
    return sha


def record(path: str | os.PathLike[str], root_id: str, *,
           box: str | None = None,
           result: InitResult | None = None) -> str | None:
    """Point FILE's `mail_root:` at ROOT_ID and commit FILE alone.

    Returns the sha of the commit this call made, or None when FILE already
    names ROOT_ID (no write, no commit). RESULT is what the caller has already
    sent. It rides on any failure, which is exit 2 kind "record" and names
    both ids (see the module docstring)."""
    rid = _maildir.bare_id(root_id)
    p, _ = _resolve(path)

    def failed(why: str, still: str | None) -> InitError:
        return InitError(
            f"--record {path}: {why}. It still names {still or 'no root'}; the root is {rid}. "
            f"The pointer did not move, and what was already sent rides on this error",
            EX_RECORD_FAILED, kind="record", result=result)

    def names_now() -> str | None:
        """What FILE names right now, for a message: best effort."""
        try:
            return _parse(p).current
        except InitError:
            return "an unreadable record"

    if not _WRITABLE_ID.fullmatch(rid):
        raise failed(f"{root_id!r} is not a Message-ID it can write", names_now())
    try:
        rec = precheck(path)
    except InitError as exc:  # FILE changed since the caller's own precheck
        raise failed(str(exc).removesuffix(". Nothing minted"), names_now()) from None
    if rec.current == rid:
        return None
    data = edited(rec, rid)
    try:
        _replace(p, data)
    except OSError as exc:
        raise failed(f"cannot write it ({exc})", rec.current) from None
    what = f"memory init {box}" if box else "memory init"
    try:
        return _commit(rec, f"{what}: mail_root -> {rid}")
    except RuntimeError as exc:
        try:
            if p.read_bytes() == data:
                _replace(p, rec.raw)
        except OSError:
            pass
        raise failed(str(exc), rec.current) from None
