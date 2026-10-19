"""`memory init`'s READ half: find BOX's canonical root before anything is sent.

The normative text is the change delta,
`openspec/changes/memory-init/specs/memory/spec.md`:

- "The root hint comes from the environment" (MODIFIED) scopes the scan. It
  reads BOX's OWN maildir, a `[ROOT]` To BOX at any plus-tag, never a
  mailstore-wide search. A hint present (`$<BOX>_ROOT`, or `mail_root:` in
  the `--record` FILE) must agree with the scan, or the call refuses (exit 2).
- "Canon is the box key, and init consumes the key without owning it"
  (ADDED) decides what counts. A `[ROOT]` is canon only when its
  signature verifies against the BOX KEY, whose `allowed_signers` principal
  is the box's BARE address.
- "init refuses loudly and never forks" is the closed exit table.
- "One live-node rule resolves every Supersedes: graph, the root's and the
  labels' alike" (ADDED) decides which root is live and which
  labels are present. `read_root` and `_labels` both call ONE resolver,
  `_resolve_graph`; they differ only in which messages are nodes.

The design is D2 in the task's `design.md`. EXISTENCE comes from the maildir,
IDENTITY from the hint, and they must agree. Where D2 predates the rulings
(an unsigned root meant "any signature"), the spec wins: the signer that
counts is the box key.

Nothing here sends, writes, renames or marks anything. Reading is harmless by
construction, as it is in `_maildir`.

THE BOX KEY'S HOME IS NOT SETTLED. The delta says `init` SHALL obtain the key
only from its home, and that the home (the cupboard, PROPOSED) is the
caller's to state. So the key arrives through `key_source`, which is injectable. Its
default, `no_key_home`, finds nothing, and every real call therefore refuses
with exit 3. That is seam.md §4 row 1 ("today, every project box"), stated
by the code rather than papered over. There is deliberately no environment
variable or file argument that names a key: the delta forbids a key "on the
command line or from a file argument", and inventing a home here would be
exactly the guess the brief forbids.
"""

from __future__ import annotations

import email
import email.utils
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from email.policy import compat32
from pathlib import Path

from . import _maildir, _sig
from ._init import EX_NOT_FOUND, EX_SIGNATURE, EX_USAGE, InitError

ROOT_SUBJECT = "[ROOT]"
SIG_HEADER = "X-Session-Signature"
#: The signed fields, in the verifier's order (session-mail-verify `HDRS`).
SIGNED = ("from", "to", "subject", "date", "message-id")
SIG_NS_DEFAULT = "session-mail@example.com"
_BEGIN, _END = "-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----"
_BARE_BOX = re.compile(r"[a-z0-9][a-z0-9._-]*", re.IGNORECASE)


# ── the key: consumed, never owned ───────────────────────────────────────


@dataclass(frozen=True)
class BoxKey:
    """The public half of a box key, as `ssh-keygen -Y verify` consumes it.

    `principal` is the box's BARE address (`sessionmail`, never
    `sessionmail+<role>`, never a session id, never a path).
    `allowed_signers` is a file whose line names that principal. `init`
    verifies with this and signs with nothing it holds: signing is the mint
    half's, through the send oracle.

    The SIGNING half is two optional fields, in the shape the send oracle
    already signs with (`_sender.keyed`/`_sender.sign_wire`, and the bash
    oracle's own condition): `public_key`, the box key's `.pub` file, and
    `agent_sock`, an ssh-agent socket that holds its private half. The mint
    half hands the oracle THESE in place of the session's
    `CLAUDE_SESSION_PUB` / `CLAUDE_SESSION_AGENT_SOCK`, so the oracle signs
    as the box and never as the running session. Where they come from is
    the caller's (the key's home is not settled); `init` never reads a private
    key itself. A key that can verify but not sign is enough to READ a box,
    and unusable for a MINT (exit 3, nothing sent).
    """

    principal: str
    allowed_signers: Path
    public_key: Path | None = None
    agent_sock: str | None = None

    @property
    def can_sign(self) -> bool:
        """The oracle's own test of a keyed environment (`_sender.keyed`):
        a socket named, and a public key that is a file."""
        return bool(self.agent_sock) and self.public_key is not None \
            and Path(self.public_key).is_file()

    def signing_env(self) -> dict[str, str]:
        """The two variables the oracle signs with, set to the BOX key."""
        return {"CLAUDE_SESSION_PUB": str(self.public_key or ""),
                "CLAUDE_SESSION_AGENT_SOCK": self.agent_sock or ""}


#: (box, env) -> the box's key, or None when there is none to be had.
KeySource = Callable[[str, Mapping[str, str]], "BoxKey | None"]
#: (message file, key) -> True when its signature verifies against KEY.
Verifier = Callable[[Path, BoxKey], bool]


def no_key_home(box: str, env: Mapping[str, str]) -> BoxKey | None:
    """The default key source: the key's home is not ruled yet.

    It finds nothing, so a real `init` refuses with exit 3 until a home is
    settled and a source that reads it replaces this one.
    """
    return None


# ── verifying: the reader's message-signature leg, principal replaced ─────


def _local_part(value: str) -> str:
    """The verifier's transit-invariant reduction (session-mail-verify
    `local_part`): innermost <...> if any, then the lowercased local-part.
    Best-effort, as the verifier is: it must attempt whatever arrived."""
    v = " ".join(str(value or "").split())
    i = v.rfind("<")
    j = v.find(">", i + 1) if i != -1 else -1
    if i != -1 and j != -1:
        v = " ".join(v[i + 1:j].split())
    return v.split("@", 1)[0].lower()


def canonical(raw: str) -> bytes | None:
    """Canonicalization v1, exactly as the verifier rebuilds it from a file
    (session-mail-verify `canonical`): the five signed headers,
    whitespace-collapsed, From and To reduced to the local-part, then the
    body with CRLF folded. The whole text is rstrip("\\n") plus one "\\n",
    which is what the signer signs. Multipart is unwrapped only for
    multipart/alternative with a text/plain first part. Any other multipart
    gives None, because v1 cannot canonicalise it."""
    msg = email.message_from_string(raw)
    parts = []
    for h in SIGNED:
        v = " ".join(str(msg.get(h, "") or "").split())
        if h in ("from", "to"):
            v = _local_part(v)
        parts.append(f"{h}: {v}\n")
    body = msg.get_payload()
    if isinstance(body, list):
        sub = body[0] if body else None
        if (msg.get_content_type() == "multipart/alternative" and sub is not None
                and sub.get_content_type() == "text/plain"):
            body = sub.get_payload()
        if isinstance(body, list):
            return None
    text = "".join(parts) + "\n" + body.replace("\r\n", "\n")
    return (text.rstrip("\n") + "\n").encode()


def ssh_verify(path: Path, key: BoxKey) -> bool:
    """True when the message at PATH carries an `X-Session-Signature` that
    `ssh-keygen -Y verify` accepts for KEY's principal and allowed_signers.
    A v2 header (what the sender writes now) is checked over the
    whole wire through `_sig`; a v1 header over the five-field canonical.

    This is the reader's message-signature leg (session-mail-verify, the
    `-Y verify -f <allowed> -I "<sid>@session"` call), with the principal
    replaced by the box's bare address, as the delta specifies. Unlike that
    leg, the allowed_signers is the BOX KEY's, never one built from the key
    embedded in the signature. Trusting the embedded key would let any
    signer be canon.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    msg = email.message_from_string(raw)
    header = msg.get(SIG_HEADER)
    if not header:
        return False
    tool = shutil.which("ssh-keygen")
    if tool is None:
        return False
    try:
        tags = _sig.parse_header(" ".join(str(header).split()))
    except _sig.Malformed:
        return False
    if tags.get("v") == _sig.VERSION:
        # v2: the signature covers the exact wire, so
        # the canonical text is rebuilt by `_sig` from the header's own h= and
        # p=, as `session-mail-verify` rebuilds it. A floor name missing from
        # h=, or a body digest that does not match, is refused before any
        # crypto, as the verifier refuses it. ORIGIN is asked only for a bare
        # address in a signed header (a captured wire; delivered mail is
        # qualified), from the same source the signer used.
        raw_b = _sig.lf(path.read_bytes())
        try:
            ns, h, p, blob = _sig.parse_v2(tags)
        except _sig.Malformed:
            return False
        if _sig.below_floor(raw_b, h) or _sig.digests(raw_b) != p:
            return False
        origin = _sig.origin_of() if _sig.has_bare_address(raw_b, h) else ""
        data: bytes | None = _sig.canonical(raw_b, ns, h, p, origin)
    else:
        blob = tags.get("b", "").strip()
        ns = tags.get("n") or SIG_NS_DEFAULT
        data = canonical(raw)
    if not blob or data is None:
        return False
    armor = f"{_BEGIN}\n" + "\n".join(textwrap.wrap(blob, 70)) + f"\n{_END}\n"
    with tempfile.TemporaryDirectory(prefix="sessionmail-verify-") as tmp:
        sig = Path(tmp) / "msg.sig"
        sig.write_text(armor)
        try:
            done = subprocess.run(
                [tool, "-Y", "verify", "-f", str(key.allowed_signers), "-I", key.principal,
                 "-n", ns, "-s", str(sig)],
                input=data, capture_output=True, check=False)
        except OSError:
            return False
    return done.returncode == 0


# ── names ─────────────────────────────────────────────────────────────────


def canon_label(subject: str) -> str:
    """A label's canonical name: brackets stripped, whitespace collapsed,
    case folded (the MHS resolver rule, mhs-thread `canon`), spelled back as
    `[NAME]`. `[asks]`, ` [ ASKS ] ` and `ASKS` are all `[ASKS]`."""
    s = " ".join(subject.split())
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return "[" + " ".join(s.split()).upper() + "]"


def is_label(subject: str) -> bool:
    s = subject.strip()
    return len(s) > 2 and s.startswith("[") and s.endswith("]")


def root_var(box: str) -> str:
    """`$<BOX>_ROOT`, named exactly as the reader names it
    (mhs-thread :471 @ c0f3cf8: upper, `-` and `.` to `_`)."""
    return box.upper().replace("-", "_").replace(".", "_") + "_ROOT"


def _to_box(to: str, box: str) -> bool:
    """To BOX at any plus-tag: some address on To reduces to BOX once the
    tag is dropped (spec delta: "To BOX at any plus-tag")."""
    for _, addr in email.utils.getaddresses([to]):
        local = _local_part(addr)
        if local.split("+", 1)[0] == box.lower():
            return True
    return False


# ── the hint ──────────────────────────────────────────────────────────────

_MAIL_ROOT = re.compile(r"""^mail_root:[ \t]*(?:"([^"]*)"|'([^']*)'|([^\s#]*))""")


def record_hint(path: str | os.PathLike[str]) -> str | None:
    """The `mail_root:` id in FILE's YAML front matter, bare, or None when
    the front matter has no such line (or an empty one).

    Refuses as usage (exit 1) when FILE does not exist or has no front
    matter. The delta ("The record is a front-matter line") refuses both
    "before anything is minted". Reading only: writing the line is `_record`'s.
    """
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InitError(f"--record {p}: cannot read it ({exc})", EX_USAGE, kind="record")
    if not lines or lines[0].strip() != "---" or "---" not in (x.strip() for x in lines[1:]):
        raise InitError(f"--record {p}: no YAML front matter (a '---' block at the top)",
                        EX_USAGE, kind="record")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = _MAIL_ROOT.match(line)
        if m:
            return _maildir.bare_id(next(g for g in m.groups() if g is not None)) or None
    return None


# ── the state the read half hands to the mint half ────────────────────────


@dataclass(frozen=True)
class RootState:
    """What BOX holds, as canon, before `init` sends anything.

    `root_id` is None on a fresh box (no `[ROOT]` To BOX at all). `labels`
    maps each PRESENT canonical label (one live end of the label graph
    carries it) to that end's bare id. `vacated` maps each VACATED name (a
    node carries it, no live end does: renamed or retired) to the id of the
    end that vacated it. `superseded` names the roots the live root's
    component retired. `not_canon` names the `[ROOT]`s To BOX that do not
    verify against the box key: readable, never canon, never adopted.
    """

    box: str
    maildir: Path
    how: str
    key: BoxKey
    root_id: str | None
    labels: Mapping[str, str] = field(default_factory=dict)
    superseded: tuple[str, ...] = ()
    not_canon: tuple[str, ...] = ()
    vacated: Mapping[str, str] = field(default_factory=dict)
    #: `--supersede` only: the root the act supersedes. The live root,
    #: when a NEW root is to be minted; a hint-named DIRECT predecessor of the
    #: live root, when a supersede was left at step 1 (the live root IS the
    #: new one, and the pointer still names the old); the one `[ROOT]` the
    #: box key never signed, on a box whose root predates its custodian (the
    #: transition). None when there is nothing to supersede, or when the read
    #: was not asked for a supersede.
    predecessor: str | None = None


# ── the read ─────────────────────────────────────────────────────────────


def resolve(box: str, env: Mapping[str, str] | None = None) -> tuple[Path, str]:
    """BOX's maildir, resolved ONLY by `_maildir.resolve_box`: the one
    resolution every reader and the pager share.

    `resolve_box` tries the FLAT `<root>/<name>` before the postfix map
    (seam.md A4, `_maildir.py:100-102 @ 43dc058`). A flat box of the same name would
    therefore win the READ while postfix DELIVERS `<name>@<host>` somewhere
    else, and `init` would read one box and mint into another: a fork made
    by the tool that exists to prevent one. So when the flat form won, the
    map is consulted as a CHECK and never as the answer. If it names a
    different maildir, the call refuses (exit 2) and names both.
    """
    if not _BARE_BOX.fullmatch(box or "") or "+" in box:
        raise InitError(f"BOX must be a bare box name (like 'sessionmail'), not {box!r}: "
                        f"not a path, not a plus-address, not a session id",
                        EX_USAGE, kind="usage")
    try:
        path, how = _maildir.resolve_box(box, dict(env) if env is not None else None)
    except _maildir.NoSuchBox as exc:
        raise InitError(f"no such BOX {box!r}: {exc}. init births no box; that is the "
                        f"step before it", EX_NOT_FOUND, kind="box") from None
    if not how.startswith("via map"):
        from . import _seat  # as `_maildir` does: _seat reads _maildir
        mapped = _seat.box_via_map(box)
        if mapped is not None and mapped[0].resolve() != path.resolve():
            raise InitError(
                f"BOX {box!r} is two boxes: it reads {path} ({how}), but postfix delivers "
                f"{box}@<host> to {mapped[0]} ({mapped[1]}). Refusing to read one and "
                f"mint into the other", EX_NOT_FOUND, kind="ambiguous")
    return path, how


def read_root(box: str, *,
              record: str | os.PathLike[str] | None = None,
              env: Mapping[str, str] | None = None,
              key_source: KeySource | None = None,
              verify: Verifier | None = None,
              supersede: bool = False) -> RootState:
    """Read BOX's canonical root and labels, or refuse on the closed table.

    Order, which is the spec's: the call (1), the box (2), the key (3), then
    the scan and the hints. Returning is exit 0: a fresh box (root_id None),
    or a root the hints agree with (the D9 case, which is not a refusal).

    SUPERSEDE is the custodian's `--supersede`, and it changes two
    readings and nothing else: (1) a box whose only `[ROOT]`s do not verify
    is RETURNED (root_id None, the roots in `not_canon`) instead of refused
    exit 3, because that box is exactly what the transition acts on; (2) a
    hint naming the live root's DIRECT predecessor (an id the live root's own
    `Supersedes:` names, canon or not) is read as step 2 left undone and
    accepted, where the plain read refuses it (exit 2). What the act is to
    supersede comes back as `predecessor`. A keyword, not a caught
    `InitError`: catching would lose the `RootState` (the maildir, the key,
    the labels) that the mint half needs, and the hint reading has to know
    the mode too.
    """
    env = os.environ if env is None else env
    key_source = key_source or no_key_home
    verify = verify or ssh_verify

    hints: list[tuple[str, str]] = []
    if record is not None:                           # usage first: exit 1 before any read
        from_file = record_hint(record)
        if from_file:
            hints.append((f"mail_root: in {record}", from_file))

    path, how = resolve(box, env)

    key = key_source(box, env)
    if key is None:
        raise InitError(
            f"the box key for {box!r} is absent: no allowed_signers names the principal "
            f"{box!r}, so no [ROOT] can be judged canon and none may be minted. The key's "
            f"home is the caller's to provide. Nothing minted", EX_SIGNATURE, kind="key")
    if key.principal != box.lower() or not Path(key.allowed_signers).is_file():
        raise InitError(
            f"the box key for {box!r} is unusable: principal {key.principal!r}, "
            f"allowed_signers {key.allowed_signers}. Nothing minted",
            EX_SIGNATURE, kind="key")

    from_env = _maildir.bare_id(env.get(root_var(box)))
    if from_env:
        hints.insert(0, (f"${root_var(box)}", from_env))

    judge = _Judge(_maildir.enumerate_box(str(path), dict(env)), box, key, verify)
    nodes, not_exact, not_canon = _root_nodes(judge)
    if not_exact:
        raise InitError(
            f"BOX {box!r} has a box-signed [ROOT] whose subject is not EXACTLY [ROOT]: "
            f"{', '.join(f'{c.subject!r} {c.id}' for c in not_exact)}. It is neither a root "
            f"nor a label: the reader refuses to prime from it, and init will not anchor on "
            f"it or mint beside it", EX_NOT_FOUND, kind="fork")
    if not nodes and not_canon and not supersede:
        raise InitError(
            f"BOX {box!r} has a [ROOT] that does not verify against the box key "
            f"(principal {box!r}): {', '.join(not_canon)}. It is not canon, it is not "
            f"adopted, and init will not mint beside it; the transition is --supersede, "
            f"the custodian's act", EX_SIGNATURE, kind="signature")

    graph = _resolve_graph(judge, nodes, box, "[ROOT]s")
    root = graph.live.get(ROOT_SUBJECT)
    if nodes and root is None:
        # PROPOSED, NOT YET SETTLED (delta, "One live-node rule"): a root graph
        # whose only components are RETIRED is refused, never read as fresh.
        raise InitError(
            f"BOX {box!r} has no live root: every signed [ROOT] is retired "
            f"({', '.join(graph.ids)}). init does not read a retired root as a fresh box, "
            f"so it mints nothing (PROPOSED, not ruled)", EX_NOT_FOUND, kind="fork")
    superseded = tuple(n for n in graph.component(root) if n != root
                       and graph.nodes[n].exact_root) if root else ()
    # The live root's own Supersedes: ids, canon or not: its DIRECT predecessors.
    direct = tuple(graph.nodes[root].supersedes) if root else ()
    predecessor = root if supersede else None

    for source, hinted in hints:
        if hinted == root:
            continue
        if supersede and (hinted in direct if root else hinted in not_canon):
            # The supersede's own states: step 2 left undone (the live root
            # supersedes what the pointer still names), or the transition's
            # old root (no canon root; this one the box key never signed).
            if predecessor not in (root, hinted):
                raise InitError(
                    f"{source} names {hinted}, but another hint names {predecessor}, and the "
                    f"live root of BOX {box!r} supersedes both. A human decides which "
                    f"--supersede this is", EX_NOT_FOUND, kind="hint")
            predecessor = hinted
            continue
        if root is None:
            raise InitError(
                f"{source} names {hinted}, which is not a signed [ROOT] in BOX {box!r} "
                f"(the box holds no canonical root). init never mints beside a hint "
                f"that points somewhere", EX_NOT_FOUND, kind="hint")
        if hinted in superseded:
            undone = (f". The live root supersedes it directly: this is the pointer a "
                      f"--supersede left behind, so `memory init {box} --supersede` with the "
                      f"same hint completes the move and mints nothing new"
                      if hinted in direct else "")
            raise InitError(
                f"{source} names {hinted}, a SUPERSEDED root; the live root of BOX {box!r} "
                f"is {root}{undone}", EX_NOT_FOUND, kind="hint")
        raise InitError(
            f"{source} names {hinted}, but the canonical [ROOT] in BOX {box!r} is {root}. "
            f"A human decides which is live", EX_NOT_FOUND, kind="hint")

    if supersede and root is None and predecessor is None and not_canon:
        # The transition with no hint: the one root there is, or a refusal.
        if len(not_canon) > 1:
            raise InitError(
                f"BOX {box!r} has {len(not_canon)} [ROOT]s that do not verify against the box "
                f"key: {', '.join(not_canon)}. --supersede needs a hint (${root_var(box)}, or "
                f"mail_root: in --record FILE) naming the one it supersedes; init does not "
                f"pick", EX_NOT_FOUND, kind="hint")
        predecessor = not_canon[0]

    labels, vacated = _labels(judge, root, box) if root else ({}, {})
    return RootState(box=box, maildir=path, how=how, key=key, root_id=root,
                     labels=labels, superseded=superseded, not_canon=not_canon,
                     vacated=vacated, predecessor=predecessor)


# ── the one live-node rule ──────────────────────────────────
#
# The delta's "One live-node rule resolves every Supersedes: graph, the
# root's and the labels' alike" (openspec/changes/memory-init/specs/memory/
# spec.md:446 @ 85513d2). The two graphs differ ONLY in which messages are
# their nodes (`_root_nodes`, `_label_nodes`): both are then resolved by
# `_resolve_graph`, and neither caller keeps graph logic of its own.

RETIRED = "X-Retired"          # changes/retire-an-item: the proposed marker
#: Exactly one Message-ID, bracketed or bare, and nothing else on the line.
_ONE_ID = re.compile(r"<([^<>\s@]+@[^<>\s@]+)>|([^<>\s@]+@[^<>\s@]+)")


@dataclass(frozen=True)
class _Copy:
    """One delivered FILE To BOX, with the fields the resolver reads."""

    path: Path
    id: str
    subject: str
    in_reply_to: str
    supersedes: tuple[str, ...]    # each well-formed Supersedes:/X-Supersedes: id
    malformed: tuple[str, ...]     # each such header that is not one Message-ID
    retired: bool

    def read(self) -> tuple:
        """What copies of one id must agree on (the PROPOSED copies clause)."""
        return (" ".join(self.subject.split()), self.in_reply_to,
                tuple(sorted(self.supersedes)), tuple(sorted(self.malformed)), self.retired)

    @property
    def rootish(self) -> bool:
        """Canonically `[ROOT]`, however it is spelled."""
        return is_label(self.subject) and canon_label(self.subject) == ROOT_SUBJECT

    @property
    def exact_root(self) -> bool:
        """EXACTLY `[ROOT]`, the reader's own test (mhs-thread's)."""
        return self.subject.strip() == ROOT_SUBJECT

    @property
    def label(self) -> bool:
        return is_label(self.subject) and not self.rootish


def _copy(e: _maildir.Entry) -> _Copy:
    """Read E's graph headers: EVERY Supersedes: line (and the legacy
    X-Supersedes:, as the reader reads it, mhs-thread:84 @ c0f3cf8), and the
    retirement marker. `Entry.supersedes` keeps only the first line, and a
    merge is several."""
    good: list[str] = []
    bad: list[str] = []
    retired = False
    try:
        head = BytesHeaderParser(policy=compat32).parsebytes(e.path.read_bytes())
    except OSError:
        head = None
    if head is not None:
        for name in ("Supersedes", "X-Supersedes"):
            for value in head.get_all(name) or []:
                v = " ".join(str(value).split())
                m = _ONE_ID.fullmatch(v)
                if m:
                    good.append(m.group(1) or m.group(2))
                else:
                    bad.append(v)
        retired = any(" ".join(str(v).split()).lower() == "yes"
                      for v in head.get_all(RETIRED) or [])
    return _Copy(path=e.path, id=e.message_id, subject=e.subject, in_reply_to=e.in_reply_to,
                 supersedes=tuple(good), malformed=tuple(bad), retired=retired)


class _Judge:
    """BOX's copies To BOX, and the box key's verdict on each, asked at most
    once per file. Only a VERIFIED copy counts; an unverified one
    contributes nothing, to either graph."""

    def __init__(self, entries: list[_maildir.Entry], box: str, key: BoxKey,
                 verify: Verifier) -> None:
        self.copies = [_copy(e) for e in entries if e.message_id and _to_box(e.to, box)]
        self._key, self._verify = key, verify
        self._seen: dict[Path, bool] = {}

    def ok(self, c: _Copy) -> bool:
        if c.path not in self._seen:
            self._seen[c.path] = bool(self._verify(c.path, self._key))
        return self._seen[c.path]

    def verified(self, mid: str) -> list[_Copy]:
        return [c for c in self.copies if c.id == mid and self.ok(c)]


def _root_nodes(j: _Judge) -> tuple[set[str], list[_Copy], tuple[str, ...]]:
    """The ROOT graph's nodes: verified messages To BOX whose subject is
    EXACTLY `[ROOT]`, wherever they are threaded, and the retirements that
    supersede one. Also returns the verified copies that are canonically
    `[ROOT]` but not exactly (refused), and the ids of `[ROOT]`s with no
    verified copy at all (not canon)."""
    rootish = [c for c in j.copies if c.rootish]
    nodes = {c.id for c in rootish if c.exact_root and j.ok(c)}
    not_exact = [c for c in rootish if not c.exact_root and c.id not in nodes and j.ok(c)]
    not_canon = tuple(dict.fromkeys(c.id for c in rootish if not any(
        j.ok(o) for o in rootish if o.id == c.id)))
    retirements = [c for c in j.copies if c.retired and not c.rootish]
    _grow(nodes, retirements, j, lambda c, ns: bool(set(c.supersedes) & ns))
    return nodes, not_exact, not_canon


def _label_nodes(j: _Judge, root: str) -> set[str]:
    """The LABEL graph's nodes under ROOT, the smallest set that holds:
    (1) a verified label To BOX whose In-Reply-To: is ROOT; (2) a verified
    label or retirement To BOX that supersedes a node already in the set,
    whose In-Reply-To: is ROOT or a node in the set. Clause 2 is the sender's
    own shape, a successor threaded under its predecessor
    (`_sender.py:321-322` @ 5769f1f)."""
    nodes: set[str] = set()
    candidates = [c for c in j.copies if c.label or (c.retired and not c.rootish)]

    def admits(c: _Copy, ns: set[str]) -> bool:
        one = c.label and c.in_reply_to == root
        two = (c.in_reply_to == root or c.in_reply_to in ns) and bool(set(c.supersedes) & ns)
        return one or two

    _grow(nodes, candidates, j, admits)
    return nodes


def _grow(nodes: set[str], candidates: list[_Copy], j: _Judge,
          admits: Callable[[_Copy, set[str]], bool]) -> None:
    """Add every verified candidate ADMITS, to a fixed point."""
    changed = True
    while changed:
        changed = False
        for c in candidates:
            if c.id not in nodes and admits(c, nodes) and j.ok(c):
                nodes.add(c.id)
                changed = True


@dataclass(frozen=True)
class _Graph:
    """A resolved graph with no fork in it."""

    nodes: Mapping[str, _Copy]               # id -> its first verified copy
    parts: tuple[tuple[tuple[str, ...], str, bool], ...]   # (ids, end, retired)
    live: Mapping[str, str]                  # canonical name -> the live end's id

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self.nodes)

    def component(self, mid: str) -> tuple[str, ...]:
        return next((ids for ids, _, _ in self.parts if mid in ids), ())


def _resolve_graph(j: _Judge, ids: set[str], box: str, what: str) -> _Graph:
    """The ONE resolver, for the root graph and the label graph alike.

    A node is a Message-ID; its verified copies must agree on every field
    read here (PROPOSED), and each `Supersedes:` line must be exactly one id.
    Every line is one edge to another node of THIS graph; any other target is
    inert. Per connected component: a directed cycle is a FORK; more than one
    end is a FORK; a single retired end is RETIRED; otherwise the single end
    is LIVE. The live ends are then grouped by canonical name, and two of
    one name are a fork. Every fork refuses, exit 2, naming every id in it.
    """
    order = [c.id for c in j.copies if c.id in ids]
    order = list(dict.fromkeys(order))
    copies = {mid: j.verified(mid) for mid in order}
    nodes = {mid: cs[0] for mid, cs in copies.items()}

    split = {mid: cs for mid, cs in copies.items() if len({c.read() for c in cs}) > 1}
    if split:
        raise InitError(
            f"BOX {box!r}: verified copies of one Message-ID disagree on a field init reads "
            f"(subject, In-Reply-To:, Supersedes:, {RETIRED}:): "
            + "; ".join(f"{mid} in {', '.join(str(c.path) for c in cs)}"
                        for mid, cs in split.items())
            + ". A fork is reported, never resolved by picking a copy", EX_NOT_FOUND, kind="fork")
    bad = {mid: c.malformed for mid, c in nodes.items() if c.malformed}
    if bad:
        raise InitError(
            f"BOX {box!r}: a Supersedes: that is not exactly one Message-ID, on "
            + "; ".join(f"{mid} ({', '.join(repr(v) for v in vs)})" for mid, vs in bad.items())
            + ". Refusing to guess the edge", EX_NOT_FOUND, kind="fork")

    succ = {mid: sorted(set(nodes[mid].supersedes) & set(nodes)) for mid in order}
    near: dict[str, set[str]] = {mid: set(succ[mid]) for mid in order}
    for n, ms in succ.items():
        for m in ms:
            near[m].add(n)
    superseded = {m for ms in succ.values() for m in ms}

    parts: list[tuple[tuple[str, ...], str, bool]] = []
    forks: list[str] = []
    seen: set[str] = set()
    for start in order:
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            x = stack.pop()
            if x not in comp:
                comp.add(x)
                stack.extend(near[x] - comp)
        seen |= comp
        ids_ = tuple(m for m in order if m in comp)
        loop = _directed_cycle(ids_, succ)
        ends = [m for m in ids_ if m not in superseded]
        if loop:
            forks.append(f"a Supersedes: cycle ({' -> '.join(loop)}) in {', '.join(ids_)}")
        elif len(ends) > 1:
            forks.append(f"{len(ends)} ends, neither superseding the other "
                         f"({', '.join(ends)}) of {', '.join(ids_)}")
        else:
            parts.append((ids_, ends[0], nodes[ends[0]].retired))
    if forks:
        raise InitError(
            f"BOX {box!r}: its signed {what} fork: {'; '.join(forks)}. A fork is reported, "
            f"never resolved by init", EX_NOT_FOUND, kind="fork")

    by_name: dict[str, list[str]] = {}
    for _, end, retired in parts:
        if not retired:
            by_name.setdefault(canon_label(nodes[end].subject), []).append(end)
    for name, ends in by_name.items():
        if len(ends) > 1:
            if name == ROOT_SUBJECT:
                raise InitError(
                    f"BOX {box!r} has {len(ends)} live roots, neither superseding the other: "
                    f"{', '.join(ends)}. A fork is reported, never resolved by init",
                    EX_NOT_FOUND, kind="fork")
            raise InitError(
                f"the root of BOX {box!r} carries {name} {len(ends)} times: "
                f"{', '.join(f'{nodes[e].subject!r} {e}' for e in ends)}. Refusing to add to "
                f"a forked tree; supersede one", EX_NOT_FOUND, kind="fork")
    return _Graph(nodes=nodes, parts=tuple(parts),
                  live={name: ends[0] for name, ends in by_name.items()})


def _directed_cycle(ids: tuple[str, ...], succ: Mapping[str, list[str]]) -> list[str]:
    """A directed cycle of `Supersedes:` pointers among IDS, in pointer
    order with the first repeated at the end (`a -> b -> a`), or []. A node
    superseding itself is a cycle; n -> m1, n -> m2, m1 -> m2 is not."""
    state: dict[str, int] = {}
    path: list[str] = []

    def walk(x: str) -> list[str]:
        state[x] = 1
        path.append(x)
        for y in succ[x]:
            if state.get(y) == 1:
                loop = path[path.index(y):]
                return loop + [y]
            if y not in state:
                found = walk(y)
                if found:
                    return found
        state[x] = 2
        path.pop()
        return []

    for x in ids:
        if x not in state:
            found = walk(x)
            if found:
                return found
    return []


def _labels(j: _Judge, root: str, box: str) -> tuple[dict[str, str], dict[str, str]]:
    """The labels under ROOT, by the one rule: PRESENT names (canonical name
    -> the live end's id) and VACATED names (canonical name -> the id of the
    end that vacated it: a rename's live end, or a retirement). A name no
    node carries is ABSENT, and in neither map."""
    graph = _resolve_graph(j, _label_nodes(j, root), box, "labels")
    vacated: dict[str, str] = {}
    for ids, end, _ in graph.parts:
        for mid in ids:
            node = graph.nodes[mid]
            if node.label:
                name = canon_label(node.subject)
                if name not in graph.live:
                    vacated.setdefault(name, end)
    return dict(graph.live), vacated
