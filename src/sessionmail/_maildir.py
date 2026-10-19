"""The physical box: a maildir under $SESSION_MAIL_ROOT, read by bytes.

`specs/message`, "One physical box, on both sides": the box a listener
resolves is the box a sender writes, `~/.mail/agents/<box>/{new,cur,tmp}`
(the bash session-mail-* root; `SESSION_MAIL_ROOT` overrides it). This
module ENUMERATES a box the way session-mail-list does - every file in
new/ and cur/, sorted by the maildir unique name (epoch-prefixed, so
chronological), 1-based over ALL of them so an index means the same
thing to `list` and `read` - and parses HEADERS only. It never renames,
flags, or claims anything: reading here is harmless by construction.

A DRAFT is not among them (pager-genie 3.28): a file whose
maildir info carries the `D` flag (`<name>:2,D`) is an open turn the keeper
is still writing, not mail. It takes no index and is counted apart
(`enumerate_with_drafts`), so a row number never moves when a draft comes
or goes, and every verb that numbers a box numbers it the same way.

"No body" is seen by bytes (the EOM requirement): after the header block
there is nothing but whitespace.
"""

from __future__ import annotations

import email.utils
import errno
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from pathlib import Path

from ._headers import UNREADABLE, Unreadable, as_written, safe_header

DEFAULT_ROOT = "~/.mail/agents"
ROOT_VAR = "SESSION_MAIL_ROOT"

_INFO = re.compile(r":2,[A-Za-z]*$")


class NoSuchBox(Exception):
    """The box directory is not there (or has no new/ and cur/)."""


@dataclass(frozen=True)
class Entry:
    index: int          # 1-based over the whole box, new/ and cur/ together
    path: Path
    unread: bool        # sits in new/ (maildir's own notion; not a seat mark)
    message_id: str     # bare, no angle brackets; "" when the file has none
    sender: str
    subject: str
    date: datetime | None
    delivered_to: str   # X-Original-To, the exact address the MTA delivered to
    supersedes: str     # bare id this message replaces, or ""
    has_body: bool
    in_reply_to: str = ""            # bare parent id, or ""
    references: tuple[str, ...] = ()  # bare ids, oldest first
    to: str = ""                      # the To: header, as written; NOT the box
    cc: str = ""                      # the Cc: header, as written, or ""
    #: The headers the stdlib parser could not read, in the order
    #: `parse` reads them. A display field among them reads
    #: `<unreadable header>`; an id field keys on the sender's raw text.
    unreadable: tuple[str, ...] = ()

    @property
    def original_to(self) -> str:
        """`X-Original-To`, under the name the spec uses for it.

        The field above is called `delivered_to` for historical reasons and
        that name is a TRAP: `Delivered-To:` is a REAL, DIFFERENT header, and
        the spec forbids grouping on it ("always the box being read, so it
        cannot distinguish anything"). The bytes have always come from
        `X-Original-To` - see `parse()` - so this is a rename at the point of
        use, not a change of source. New readers SHOULD use this name; the old
        one is left working because `message list --to` reads it.
        """
        return self.delivered_to

    @property
    def unreadable_mark(self) -> str:
        """`  <unreadable header: Message-ID, ...>` for a row whose headers
        the parser could not all read, else "". Every surface
        that lists or reads the entry appends it, so the mail is marked,
        never hidden."""
        return f"  <unreadable header: {', '.join(self.unreadable)}>" if self.unreadable else ""

    @property
    def sender_local(self) -> str:
        addr = email.utils.parseaddr(self.sender)[1] or self.sender
        return addr.split("@", 1)[0]


def root(env: dict[str, str] | None = None) -> Path:
    """The agents root: `$SESSION_MAIL_ROOT`, else `<mail root>/agents` from
    the settings file's `root` key, else DEFAULT_ROOT."""
    env = os.environ if env is None else env
    given = env.get(ROOT_VAR)
    if given:
        return Path(given).expanduser()
    from . import _settings              # here, not at import: _settings is the higher layer
    mail_root = _settings.file_path("root", env)
    if mail_root is not None:
        return Path(mail_root).expanduser() / "agents"
    return Path(DEFAULT_ROOT).expanduser()


def _is_maildir(d: Path) -> bool:
    return (d / "new").is_dir() and (d / "cur").is_dir()


# -- absent, or unreadable: one stat that tells them apart ----------
#
# `Path.is_dir()`, `is_file()`, `exists()` and `is_symlink()` raised
# PermissionError up to CPython 3.13 when a folder on the way denied the
# stat (mode r--: it lists, but cannot be searched). From 3.14 they call
# `os.path.isdir` and the rest, which swallow EVERY OSError, so the same
# path answers False: an unreadable box reads as an absent one, silently.
# A caller that must say "unreadable" stats through `strict_mode`.

#: The errnos that mean nothing is there, the set pathlib ignored up to
#: 3.13 (`pathlib._abc._ignore_error`): no such entry, a non-directory on
#: the way, a bad descriptor, a symlink loop. Every other OSError raises.
ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


def strict_mode(path: Path, *, follow_symlinks: bool = True) -> int:
    """PATH's `st_mode`, or 0 when nothing is there (ABSENT_ERRNOS, or a
    path the OS cannot encode). 0 matches no `stat.S_IS*` test, so
    `S_ISDIR(strict_mode(p))` is the 3.13 `p.is_dir()` on every version, and
    `strict_mode(p) != 0` is its `p.exists()`. Any other OSError raises, a
    PermissionError above all: that is the caller's to report."""
    try:
        return os.stat(path, follow_symlinks=follow_symlinks).st_mode
    except OSError as exc:
        if exc.errno in ABSENT_ERRNOS:
            return 0
        raise
    except ValueError:                   # an embedded NUL: nothing can be there
        return 0


# -- making a box, and writing into one: owner-only ------------------
#
# A maildir holds other seats' mail, so what sessionmail CREATES is the
# owner's alone: 0700 directories, 0600 files, whatever the process umask
# says. `mkdir(mode=)` and `open(mode=)` are both masked by the umask, and
# `Path.mkdir(parents=True)` ignores `mode=` for the parents it makes, so
# every level is made one at a time and then chmodded. What already exists is
# never touched: an existing box, or a `~/.mail` its host owner set to some
# other mode, keeps its mode (narrowing those is a migration, not a create).

DIR_MODE = 0o700
FILE_MODE = 0o600


def make_private_dirs(path: Path) -> list[Path]:
    """PATH as a directory, every level this call MAKES (parents included)
    0700. Levels that already exist are left exactly as they are. Returns the
    directories made here, outermost first."""
    missing: list[Path] = []
    p = path
    while not os.path.lexists(p):
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    made: list[Path] = []
    for d in reversed(missing):
        try:
            os.mkdir(d, DIR_MODE)
        except FileExistsError:
            continue                     # a racing maker's: not ours to chmod
        os.chmod(d, DIR_MODE)            # mode= was umask-masked
        made.append(d)
    if not path.is_dir():                # an existing non-directory in the way
        raise NotADirectoryError(f"{path} is not a directory")
    return made


def make_maildir(box: Path) -> list[Path]:
    """BOX/{cur,new,tmp}, each missing level 0700 (make_private_dirs)."""
    made: list[Path] = []
    for sub in ("cur", "new", "tmp"):
        made += make_private_dirs(box / sub)
    return made


def write_private(path: Path, data: bytes, *, fsync: bool = True) -> None:
    """DATA into a NEW file at PATH, mode 0600 whatever the umask. O_EXCL:
    it never truncates or follows what is already there (a name collision or
    a planted symlink is an OSError, not an overwrite)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(fd, FILE_MODE)         # the umask may have masked 0600
        f.write(data)
        f.flush()
        if fsync:
            os.fsync(fd)


#: How many names one delivery tries before a collision is reported.
PUBLISH_ATTEMPTS = 3


def _unlink_ours(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def deliver_private(box: Path, data: bytes, name_for) -> Path:
    """DATA into BOX/new/ as one 0600 file, never replacing a file already
    there. Returns the path in new/.

    This publishes the way postfix's virtual(8) does (src/virtual/maildir.c):
    `write_private` into tmp/ (O_EXCL), then `os.link` into new/, then unlink
    the tmp file. `os.replace` is not used, because it overwrites: a repeat
    name silently replaced a delivered message. A link
    raises FileExistsError instead, and keeps the inode, so the copy keeps
    its 0600.

    NAME_FOR(fresh) gives the file name. FRESH is "" on the first attempt,
    so that name is unchanged. On a later attempt it is the maildir spec's
    random field, `R<8 hex>`. A FileExistsError from the tmp/ open or from
    the link is a name collision: our own tmp file (if the link was what
    failed) is unlinked, and the next name is tried, PUBLISH_ATTEMPTS in
    all. The last attempt's FileExistsError is raised. No other errno is
    retried; it is raised at once.

    Once the link has succeeded the copy IS delivered: a failed unlink of the
    tmp file after that is ignored, never raised, so a caller's record of
    what was written stays true."""
    for attempt in range(PUBLISH_ATTEMPTS):
        last = attempt == PUBLISH_ATTEMPTS - 1
        name = name_for("" if attempt == 0 else f"R{secrets.token_hex(4)}")
        tmp = box / "tmp" / name
        try:
            write_private(tmp, data)
        except FileExistsError:
            if last:
                raise
            continue                 # not ours: never touch the file in the way
        dest = box / "new" / name
        try:
            os.link(tmp, dest)
        except FileExistsError:
            _unlink_ours(tmp)
            if last:
                raise
            continue
        except OSError:
            _unlink_ours(tmp)
            raise
        _unlink_ours(tmp)            # best effort: the copy is in new/ already
        return dest
    raise AssertionError("unreachable: the last attempt returns or raises")


def resolve_box(box: str, env: dict[str, str] | None = None) -> tuple[Path, str]:
    """The maildir BOX resolves to, and how - ONE resolution for every reader
    (specs/message, "One physical box, on both sides"). In order: a path that
    is a maildir (absolute, or ~); `<root>/<name>`, where FLAT boxes live; and
    for a bare name, the box postfix DELIVERS `<name>@<host>` to
    (`_seat.box_via_map`). A project box nests under its store
    (`agents/example/chat/`), so `<root>/<name>` is not where it is: the
    pager learned the map first, and `list`/`read` refused every project
    box until they did too (changes/message-path).
    Raises NoSuchBox naming what was tried."""
    tried: list[str] = []
    p = Path(box).expanduser()
    if p.is_absolute():                      # a path first: the spec's order
        if _is_maildir(p):
            return p, f"path {p}"
        tried.append(f"no maildir at {p}")
    r = root(env)
    d = r / box
    if _is_maildir(d):                       # <root>/<name>, or <root>/<store>/<project>
        return d, f"under {r}"
    tried.append(f"no box {box!r} under {r}")
    if "/" not in box:                       # a bare name: the row postfix delivers by
        from . import _seat          # here, not at the top: _seat reads this module
        found = _seat.box_via_map(box)
        if found is not None:
            return found[0], f"via map {found[1]}"
        tried.append(f"no virtual_mailbox_maps row delivers {box}@<host> to a maildir")
    raise NoSuchBox("; and ".join(tried))


def box_dir(box: str, env: dict[str, str] | None = None) -> Path:
    d = resolve_box(box, env)[0]
    return d


def _key(path: Path) -> str:
    return _INFO.sub("", path.name)


def bare_id(value: str | None) -> str:
    """A Message-ID without its angle brackets. `<a@h>` and `a@h` are the
    same id, and every caller that compares ids must agree on which form."""
    return (value or "").strip().strip("<>")


#: The old private name, kept because this module calls it in a dozen places
#: and renaming those is churn with no reader.
_bare = bare_id


def parse(path: Path, index: int, unread: bool) -> Entry:
    """PATH as an Entry. Total over a header the parser cannot read
   : every header is read through `_headers.safe_header`, and one
    that faults is named in `Entry.unreadable`, never raised."""
    raw = path.read_bytes()
    headers = BytesHeaderParser(policy=default_policy).parsebytes(raw)
    split = re.search(rb"\r?\n\r?\n", raw)
    body = raw[split.end():] if split else b""
    bad: list[str] = []

    def get(name: str) -> object:
        value = safe_header(headers, name)
        if isinstance(value, Unreadable):
            bad.append(name)
        return value

    def text(name: str) -> str:
        """A display field: `<unreadable header>` when it cannot be read."""
        value = get(name)
        return "" if value is None else str(value).strip()

    def ident(name: str) -> str:
        """An id field: the sender's raw text when it cannot be read, so
        the mail stays addressable (`read ID`, `mark`) by what was written."""
        return _bare(as_written(get(name)))

    date_value = get("Date")
    try:
        date = (email.utils.parsedate_to_datetime(date_value)
                if date_value and not isinstance(date_value, Unreadable) else None)
    except (TypeError, ValueError):
        date = None
    return Entry(
        index=index, path=path, unread=unread,
        message_id=ident("Message-ID"),
        sender=text("From"),
        subject=text("Subject"),
        date=date,
        delivered_to=text("X-Original-To"),
        supersedes=ident("Supersedes"),
        has_body=bool(body.strip()),
        in_reply_to=ident("In-Reply-To"),
        references=tuple(_bare(r) for r in as_written(get("References")).split() if r),
        to=text("To"),
        cc=text("Cc"),
        unreadable=tuple(bad),
    )


def unparsed(path: Path, index: int, unread: bool) -> Entry:
    """An Entry for a file `parse` itself could not read (a fault outside
    the header accessor's named set): counted and marked, never dropped
   . Nothing in it is read from the file."""
    return Entry(index=index, path=path, unread=unread, message_id="", sender="",
                 subject=UNREADABLE, date=None, delivered_to="", supersedes="",
                 has_body=False, unreadable=("headers",))


def find(entries: list[Entry], which: str) -> Entry | None:
    """The entry N (1-based index) or ID (bare or <bracketed>) names."""
    if which.isdigit():
        n = int(which)
        return entries[n - 1] if 1 <= n <= len(entries) else None
    wanted = _bare(which)
    return next((e for e in entries if e.message_id == wanted), None)


def thread(entries: list[Entry], of: Entry) -> list[Entry]:
    """Every entry in the same thread as OF, oldest first, OF included:
    walk In-Reply-To up to the root within this box, then take everything
    that reaches a member through In-Reply-To or References, to a fixed
    point (a reply to a reply carries only its parent's id, so one hop
    from the root is not enough)."""
    by_id = {e.message_id: e for e in entries if e.message_id}
    root, seen = of, set()
    while root.in_reply_to in by_id and root.in_reply_to not in seen:
        seen.add(root.message_id)
        root = by_id[root.in_reply_to]
    members = {id(root): root, id(of): of}
    ids = {root.message_id, of.message_id} - {""}
    grew = True
    while grew:
        grew = False
        for e in entries:
            if id(e) in members:
                continue
            if e.in_reply_to in ids or any(r in ids for r in e.references):
                members[id(e)] = e
                if e.message_id:
                    ids.add(e.message_id)
                grew = True
    return sorted(members.values(), key=lambda e: e.index)


def ancestors(entries: list[Entry], of: Entry) -> list[Entry]:
    """OF's parent chain within this box, root first, OF excluded."""
    by_id = {e.message_id: e for e in entries if e.message_id}
    chain, seen, cur = [], set(), of
    while cur.in_reply_to in by_id and cur.in_reply_to not in seen:
        seen.add(cur.message_id)
        cur = by_id[cur.in_reply_to]
        chain.append(cur)
    return list(reversed(chain))


def descendants(entries: list[Entry], of: Entry) -> list[Entry]:
    """OF and everything under it, to a fixed point, oldest first. On a
    mailbox-as-memory box the whole box is ONE thread under [ROOT]; rooting
    at OF is what keeps `tree` and `read --thread` bounded there."""
    members = {id(of): of}
    ids = {of.message_id} - {""}
    grew = True
    while grew:
        grew = False
        for e in entries:
            if id(e) in members:
                continue
            if e.in_reply_to in ids or any(r in ids for r in e.references):
                members[id(e)] = e
                if e.message_id:
                    ids.add(e.message_id)
                grew = True
    return sorted(members.values(), key=lambda e: e.index)


def text_body(path: Path) -> str:
    """The text/plain part of the message at PATH (or the whole body if it
    is not multipart), decoded. Never the HTML part."""
    from email.parser import BytesParser
    msg = BytesParser(policy=default_policy).parsebytes(path.read_bytes())
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    if part is None:
        return ""
    try:
        return part.get_content()
    except Exception:  # an odd charset: show the bytes rather than nothing
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def is_draft(path: Path) -> bool:
    """pager-genie 3.28 (PROPOSED there, and the keeper conforms): PATH is a
    draft - its maildir info carries the `D` flag
    (`<name>:2,D`, alone or among others). The flag is how every reader
    tells a draft from mail; notmuch maps it to its `draft` tag. By NAME
    only: nothing is opened."""
    m = _INFO.search(path.name)
    return m is not None and "D" in m.group(0)[3:]


def enumerate_with_drafts(box: str, env: dict[str, str] | None = None
                          ) -> tuple[list[Entry], list[Path]]:
    """(every MESSAGE in BOX, oldest first, indexed 1..N over new/ and cur/;
    the paths of its DRAFTS, oldest first). A draft is never indexed and
    never parsed here: `list` only counts them, and `read` parses the one it
    is asked for (`find_draft`)."""
    d = box_dir(box, env)
    files = [(p, True) for p in (d / "new").iterdir() if p.is_file()]
    files += [(p, False) for p in (d / "cur").iterdir() if p.is_file()]
    files.sort(key=lambda t: _key(t[0]))
    mail = [(p, unread) for p, unread in files if not is_draft(p)]
    drafts = [p for p, _unread in files if is_draft(p)]
    return [parse(p, i, unread) for i, (p, unread) in enumerate(mail, start=1)], drafts


def enumerate_box(box: str, env: dict[str, str] | None = None) -> list[Entry]:
    """Every message in BOX, oldest first, indexed 1..N over new/ and cur/.
    Drafts are not messages (3.28): see `enumerate_with_drafts`."""
    return enumerate_with_drafts(box, env)[0]


def find_draft(drafts: list[Path], which: str) -> Entry | None:
    """The draft among DRAFTS whose Message-ID is WHICH (bare or
    <bracketed>), parsed, with index 0 (a draft has no row); None if none
    is. An index never names a draft, so a number is always None."""
    wanted = _bare(which)
    if not wanted or which.isdigit():
        return None
    for p in drafts:
        try:
            e = parse(p, 0, False)
        except OSError:
            continue                    # the keeper renamed it away: not here now
        if e.message_id == wanted:
            return e
    return None


# ── grouping by X-Original-To ────────────────────────────────────────────
#
# specs/pager, "The pager is a nudge, and the re-arm carries" (amended):
# what a page counts is grouped by the
# ENVELOPE RECIPIENT, `X-Original-To` - the address postfix actually
# delivered to, stamped on every copy it writes.
#
# NEVER `To:` and NEVER `Delivered-To:`, and the reasons are different:
# `To:` names the nominal recipient, so a Cc or Bcc copy names somebody
# else entirely; `Delivered-To:` is always the box being read, so it has
# the same value for every message in it and cannot distinguish anything.
# `Entry` reads `X-Original-To` and nothing else (see `parse`), and
# `Entry.original_to` is that field under the spec's own name.


@dataclass(frozen=True)
class Group:
    """One envelope recipient's mail inside one box.

    THREE counts, not two, and they are independent readings:

    - `unseen` - not marked `seen` for this session. `seen` is written by
      the pager's wake and by the ride-along's scoop, so unseen means
      nothing has put this in front of the reader yet.
    - `unread` - not marked `read`. `read` is written by `message read`.
      A message can be seen and unread (paged, never opened), which is
      exactly the state a page exists to report, so collapsing these two
      into one number would lose the reason for the page.
    - `total` - every message for this address in the box.

    `first`/`last` are the box's own row numbers (`Entry.index`), so they
    can be pasted straight into `message read`. They are 0 when the entries
    carry no index.
    """

    address: str                    # the grouping key: local part, "" if no header
    spellings: tuple[str, ...]      # verbatim X-Original-To values folded in here
    unseen: int
    unread: int
    total: int
    first: int
    last: int

    @property
    def rows(self) -> str:
        """The row range, formatted the way the listing formats row numbers:
        NO thousands separator, because these get pasted into `message read`
        and a comma would not survive the paste (specs/message, "A listing
        defaults to the newest 40"). Counts are separated; rows are not."""
        if not self.first:
            return ""
        return f"{self.first}" if self.first == self.last else f"{self.first}..{self.last}"


def original_to_key(value: str) -> str:
    """The grouping key for one `X-Original-To` value: the LOCAL PART, host
    discarded, `+extension` KEPT.

    The spec's examples are the specification of the shape - `alice`,
    `alice+inbox`, a session id - and all three are local parts. The host is
    dropped because it is not part of who the mail is for, and MEASURED on
    a live box it varies for the same reader: one reader's
    mail carried `alice@host.<suffix>`, `alice` and `alice@host`.
    Grouping verbatim would have reported one reader as three. The
    `+extension` is KEPT because `alice` and `alice+inbox` are two of the
    three examples, so they are meant to be two groups.

    An absent header gives "", which groups rather than disappears: a message
    nobody can attribute is still a message waiting.
    """
    value = (value or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    return value.split("@", 1)[0].strip().lower() if "@" in value else value.strip().lower()


def group_by_original_to(entries: "list[Entry]",
                         marks: "dict[str, set[str]] | None" = None) -> "list[Group]":
    """ENTRIES grouped by `X-Original-To`, oldest group first.

    MARKS is this session's marks (`_seat.read_marks`), message-id ->
    {"seen", "read", "done"}; without it every message counts unseen and
    unread, which is the truth for a reader who has no marks.

    The order is by the group's FIRST row - oldest first - which is the order
    everything else here prints in (`_detect` returns oldest first,
    the queue sorts on the link's mtime, the listed page prints oldest
    first). Not a new decision; the settled one.

    Pure: it reads no files and no clock, so a test can build entries by hand
    and a caller can group anything it has already enumerated.
    """
    marks = marks or {}
    buckets: dict[str, list[Entry]] = {}
    for e in entries:
        buckets.setdefault(original_to_key(e.original_to), []).append(e)
    groups: list[Group] = []
    for address, members in buckets.items():
        indexes = [m.index for m in members if m.index]
        groups.append(Group(
            address=address,
            spellings=tuple(sorted({m.original_to for m in members})),
            unseen=sum(1 for m in members if "seen" not in marks.get(m.message_id, ())),
            unread=sum(1 for m in members if "read" not in marks.get(m.message_id, ())),
            total=len(members),
            first=min(indexes) if indexes else 0,
            last=max(indexes) if indexes else 0,
        ))
    # Oldest group first; ties (and unindexed groups) fall back to the
    # address so the order is total and a test can rely on it.
    groups.sort(key=lambda g: (g.first if g.first else 1 << 30, g.address))
    return groups
