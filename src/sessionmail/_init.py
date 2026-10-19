"""`memory init`'s library door: the seam a task runner calls in-process.

The contract is the memory-init change's `seam.md` §2. The public `init()`
fills `InitResult`. This module holds the two types every half of `init`
raises and returns, so that the read half (`_root`) and the mint half share
one refusal shape:

- `InitError` carries `.code`, `.kind` and `.result` (amendments A1 and A2).
- `InitResult` is exactly §2's frozen record. `init()` fills it.
- `init()` is the seam, exactly §2's signature; `run()` is the same body with
  a `report` line sink, which the CLI passes (`memory init` is a thin wrapper).

The exit table is CLOSED (spec delta, "init refuses loudly and never forks":
"these exit codes and no others"): 0 ok · 1 usage · 2 not found or ambiguous ·
3 signature refused. So `InitError` refuses to be built with any other code,
the way `_sender.Refused` refuses a refusal without a kind.

⚠️ `_root` imports this module at its top. Keep this module free of
an import of `_root` at ITS top, or import `_root` below the class definitions
or inside `init()`, so the two never form an import cycle.
"""

from __future__ import annotations

import os
import re
import time
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

EX_OK, EX_USAGE, EX_NOT_FOUND, EX_SIGNATURE = 0, 1, 2, 3

#: The closed table's refusal codes. 0 is not a refusal, so it is not here.
CODES = (EX_USAGE, EX_NOT_FOUND, EX_SIGNATURE)

#: What KIND a refusal is: one word a caller can switch on, never prose
#: (seam.md A1, the house shape of `_sender.Refused` and the caller's BoxError).
#: A kind says WHICH thing was wrong; the code says which row of the table.
KINDS = (
    "usage",       # 1: the call itself is wrong (BOX not a bare name, a bad flag)
    "record",      # 1: --record FILE unfit, refused before anything is sent (_record.precheck)
                   # 2: the record write or commit failed AFTER a mint (_record.record)
    "box",         # 2: BOX does not exist
    "ambiguous",   # 2: the box BOX reads is not the box BOX is delivered to
    "hint",        # 2: a root hint names something the box does not hold as canon
    "fork",        # 2: a Supersedes: graph that does not resolve to one item (the one
                   #    live-node rule): two live roots or two labels of one
                   #    name, a cycle, more than one end, disagreeing copies of one id,
                   #    a Supersedes: that is not one Message-ID, a box-signed [ROOT]
                   #    that is not exactly [ROOT], a root graph that is only retired
    "key",         # 3: the box key is absent or unusable (for a mint: it cannot SIGN,
                   #    by its shape or by a probe signature that does not verify)
    "signature",   # 3: a [ROOT] exists, or a message this call minted, and it does
                   #    not verify against the box key
    "delivery",    # 3: a message this call sent never arrived in BOX, so it could not
                   #    be verified (the minted root "does not verify" row)
    "send",        # 2: the sender refused or the MTA failed a send (2, as no row of
                   #    the closed table names it; the route to BOX did not take it)
)


@dataclass(frozen=True)
class InitResult:
    """What one `init` call left behind (seam.md §2, fields exactly as settled)."""

    root_id: str                      # the live root after the call, BARE id
    minted: tuple[str, ...]           # canonical subjects THIS call sent, in send order
    existing: tuple[str, ...]         # canonical labels already under the root before the call
    recorded: str | None              # sha of the record commit THIS call made, or None
    labels: Mapping[str, str] = field(default_factory=dict)  # canonical label -> bare id
    superseded: str | None = None     # the old root's bare id when a supersede minted a new one


class InitError(Exception):
    """A refusal from `init`, on the closed table.

    `kind` is REQUIRED and keyword-only, with no default, as in
    `_sender.Refused`: a refusal without its word cannot be written. `result`
    is what this call had ALREADY sent before it stopped (seam.md A2), or None
    when nothing was sent. The read half never sends, so it always gives None.
    """

    def __init__(self, message: str, code: int, *, kind: str,
                 result: InitResult | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"not an init exit code: {code!r} (the table is closed: {CODES})")
        if kind not in KINDS:
            raise ValueError(f"not an init refusal kind: {kind!r}")
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.result = result


# ── the mint half ───────────────────────────────────────────────
#
# Normative text: the change delta, `openspec/changes/memory-init/specs/
# memory/spec.md` - "init prints the root a box has, or mints one with the
# standard labels", "init is additive and never duplicates", "The label set
# is selectable within the ruled ten", "Canon is the box key, and init
# consumes the key without owning it", "init refuses loudly and never forks"
# and, for VACATED names, "One live-node rule ..." (PROPOSED there).

#: The ruled ten, in the order init mints them.
DEFAULT_LABELS = ("[CHARTER]", "[RULES]", "[HOW-TO]", "[BOOT]", "[CONVO]", "[ASKS]")
SELECTABLE_LABELS = ("[IDEAS]", "[RULINGS]", "[LESSONS]", "[TOOLS]")
TEN = DEFAULT_LABELS + SELECTABLE_LABELS

#: How long a sent message has to arrive in BOX before it counts as not
#: verified. Delivery is postfix's and is not instant; a test shortens it.
DELIVERY_WAIT_SECONDS = 10.0
DELIVERY_POLL_SECONDS = 0.1

#: Where the box key comes from, and what judges a signature: None is the
#: read half's defaults (`_root.no_key_home`, `_root.ssh_verify`). The seam's
#: signature takes neither (seam.md §2), so a test replaces these, never an
#: argument. `no_key_home` finds nothing, so a real call refuses exit 3 until
#: the key has a settled home.
KEY_SOURCE = None
VERIFY = None
#: What signs the pre-send probe with the box key's signing half: None is the
#: sender's own v2 signer (`_sender.sign_wire`, over the exact wire, with
#: `ssh-keygen -Y sign` under the agent the env names), so the probe signs
#: exactly as the mint will. A test replaces it, or replaces
#: `_sender.sign_bytes` underneath both.
SIGNER = None

ROOT_BODY = ("The root of the {box} box. Minted {date} by {seat} with `memory init`. "
             "Every label in this box hangs under this mail; its Message-ID is the "
             "box's root hint.\n")
#: A superseding root's body names the old root as provenance (spec delta,
#: "A new root is a new tree": the body SHALL name that id).
SUPERSEDE_BODY = ROOT_BODY + ("It supersedes <{old}>: a new tree, not a reply. That root "
                              "and everything under it are unchanged and stay readable by "
                              "anchoring on its id; they are not canon.\n")


def _names(value: Sequence[str] | str | None) -> list[str]:
    """A selection as given: a sequence of names, or one comma-separated
    string (the CLI's `L,..` form). Empty pieces are dropped."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    return [str(v) for v in items if str(v).strip()]


def select(labels: Sequence[str] | str | None = None,
           plus: Sequence[str] | str = ()) -> tuple[tuple[str, ...], frozenset[str]]:
    """The requested set, in the ruled ten's order, and the names the caller
    NAMED (`--labels` or `--plus`), which may re-mint a vacated name.

    Refuses as usage (exit 1), before anything is read or sent: `--labels`
    with `--plus`; `labels=()` (seam.md A5: an empty selection is a caller
    bug, never "root only"); any name outside the ruled ten, with the ten
    printed. Names are bare or bracketed, in any case and spacing."""
    from ._root import canon_label

    if labels is not None and _names(plus):
        raise InitError("--labels and --plus together: --labels is the exact set, --plus "
                        "adds to the default; give one", EX_USAGE, kind="usage")
    if labels is not None and not _names(labels):
        raise InitError("--labels names no label: an empty selection mints nothing and is "
                        "refused (give --labels L,.. or leave it out for the default "
                        f"{' '.join(DEFAULT_LABELS)})", EX_USAGE, kind="usage")
    named_raw = _names(labels) if labels is not None else _names(plus)
    named = [canon_label(n) for n in named_raw]
    unknown = [raw for raw, n in zip(named_raw, named) if n not in TEN]
    if unknown:
        raise InitError(f"not a mintable label: {', '.join(repr(u) for u in unknown)}. "
                        f"The ruled ten: {' '.join(TEN)}", EX_USAGE, kind="usage")
    wanted = set(named) if labels is not None else set(DEFAULT_LABELS) | set(named)
    return tuple(n for n in TEN if n in wanted), frozenset(named)


def init(box: str, *,
         labels: Sequence[str] | None = None,
         plus: Sequence[str] = (),
         supersede: bool = False,
         record: str | os.PathLike[str] | None = None,
         env: Mapping[str, str] | None = None,
         ) -> InitResult:
    """Find BOX's canonical root, or mint `[ROOT]`; mint the missing labels; record.

    The seam a task runner calls in-process (seam.md §2, exactly). Returns what the
    call left behind, or raises `InitError` on the closed table, carrying in
    `.result` whatever it had ALREADY sent (seam.md A2)."""
    return run(box, labels=labels, plus=plus, supersede=supersede, record=record, env=env)


def run(box: str, *,
        labels: Sequence[str] | None = None,
        plus: Sequence[str] = (),
        supersede: bool = False,
        record: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        report: Callable[[str], None] | None = None) -> InitResult:
    """`init`, plus REPORT: one line per thing done or found, as it happens.

    The CLI passes `click.echo`; `init` passes nothing. Same body, so the two
    doors cannot drift. The order is the spec's: usage (1) before the box (2)
    before the key (3), and every refusal before the first send."""
    from . import _record, _root, _seat

    say = report or (lambda line: None)
    env = os.environ if env is None else env
    requested, named = select(labels, plus)
    try:
        seat = _seat.resolve(dict(env))
    except _seat.NoIdentity as exc:
        raise InitError(f"no seat in the environment to mint From: {exc}", EX_USAGE,
                        kind="usage") from None
    if record is not None:
        _record.precheck(record)

    state = _root.read_root(box, record=record, env=env,
                            key_source=KEY_SOURCE or _root.no_key_home,
                            verify=VERIFY or _root.ssh_verify, supersede=supersede)
    old: str | None = None            # the root this call supersedes
    if supersede:
        # Q8: a new root is a NEW TREE, in two steps - mint and verify
        # the new [ROOT] (and its labels), THEN move the pointer. The read half
        # has already said what is superseded (`predecessor`): the live root,
        # or a root the box key never signed (the transition), or the live
        # root's direct predecessor when a run stopped between the steps, in
        # which case the live root IS the new one and nothing new is minted.
        if state.predecessor is None:
            raise InitError(f"--supersede on BOX {box!r}, which has no root: there is nothing "
                            f"to supersede. Run without --supersede to mint the first root",
                            EX_USAGE, kind="usage")
        old = state.predecessor
        if state.root_id is not None and old != state.root_id:
            say(f"resumed [ROOT] {state.root_id} supersedes {old}; the pointer still names "
                f"{old}, so this run completes the move")
        else:
            # A new tree: nothing under the old root is "existing" here, and a
            # name vacated there is not vacated here.
            state = dataclasses.replace(state, root_id=None, labels={}, vacated={})

    existing = _order(state.labels)
    for name in existing:
        say(f"exists  {name} {state.labels[name]}")
    todo: list[str] = []
    for name in requested:
        if name in state.labels:
            continue
        if name in state.vacated and name not in named:
            say(f"vacated {name} {state.vacated[name]}: not re-minted by the default set; "
                f"name it in --labels or --plus to mint it again")
            continue
        todo.append(name)

    minter = _Minter(box, state, seat.address, env, say)
    minter.supersedes = old           # a resumed supersede's refusals name it too
    if state.root_id is None or todo:
        minter.check_key()
    root_id = state.root_id or minter.mint_root(supersedes=old)
    for name in todo:
        minter.mint_label(name, root_id)

    have = {**state.labels, **minter.labels}
    result = InitResult(root_id=root_id, minted=tuple(minter.minted), existing=existing,
                        recorded=None, labels={n: have[n] for n in _order(have)},
                        superseded=old)
    if record is not None:
        # Step 2, the POINTER: the blessing. Only after every send verified.
        try:
            sha = _record.record(record, root_id, box=box, result=result)
        except InitError as exc:
            if old is None:
                raise
            # The spec's "pointer has not moved yet" state: safe, named, and
            # completed by the same command (the read half resumes it).
            raise InitError(
                f"{exc}. The new [ROOT] {root_id} is minted and verified and supersedes {old}; "
                f"readers stay anchored on {old} until the pointer moves. Run `memory init "
                f"{box} --supersede --record {record}` again to complete the move: it mints "
                f"nothing new", exc.code, kind=exc.kind, result=result) from None
        if sha:
            say(f"recorded {record} {sha}")
            result = dataclasses.replace(result, recorded=sha)
    if old is not None:
        say(f"superseded {old}")
    say(f"root {root_id} ({len(result.labels)} labels)")
    say(f"export {_root.root_var(box)}='{root_id}'")
    return result


def _order(labels: Mapping[str, str]) -> tuple[str, ...]:
    """Label names in the ruled ten's order, then any others alphabetically."""
    rank = {n: i for i, n in enumerate(TEN)}
    return tuple(sorted(labels, key=lambda n: (rank.get(n, len(TEN)), n)))


class _Minter:
    """The sends of ONE call, in order, each verified before the next.

    Every send goes through the package's own sender (`_sender.send`, the
    core every door of `message send` ends in, now the bash oracle's PATH
    fallback is retired) with the box key's signing variables in
    place of the session's, so the sender signs v2 as the BOX, and with
    `$SESSIONMAIL_REQUIRE_SIGNATURE` set, so a send that cannot sign is
    refused rather than sent unsigned. Every Message-ID is the
    sender's receipt, never one built here. After each send the message is
    found in BOX's own maildir and its signature checked against the box key;
    until it verifies, nothing else is sent."""

    def __init__(self, box: str, state, sender: str, env: Mapping[str, str],
                 say: Callable[[str], None]) -> None:
        self.box, self.state, self.sender, self.say = box, state, sender, say
        self.env = dict(env)
        self.minted: list[str] = []
        self.labels: dict[str, str] = {}
        self.root_id: str | None = state.root_id
        self.supersedes: str | None = None

    # -- before the first send ----------------------------------------------

    def check_key(self) -> None:
        """A mint is due: the box key must be able to SIGN, or nothing goes.
        The oracle would otherwise sign as the session, or send unsigned
        (7(b): right for a letter, wrong for a root)."""
        key = self.state.key
        if not key.can_sign:
            raise InitError(
                f"the box key for {self.box!r} cannot sign: principal {key.principal!r}, "
                f"public key {key.public_key or '(none)'}, agent socket "
                f"{key.agent_sock or '(none)'}. A [ROOT] or label not signed by it would not "
                f"be canon, so nothing was minted", EX_SIGNATURE, kind="key")
        if not probe(key, self.env):
            # The shape is right and the key still cannot sign as the box: a dead
            # agent, an agent without the key, or a .pub that allowed_signers does
            # not name. The oracle would send anyway (UNSIGNED, 7(b)), and an
            # unverified [ROOT] in BOX refuses every later run (exit 3) until a
            # custodian's --supersede: the orphan the spec forbids (exit table).
            raise InitError(
                f"the box key for {self.box!r} is unusable: a probe signed with its "
                f"signing half (public key {key.public_key}, agent socket {key.agent_sock}) "
                f"does not verify against {key.allowed_signers} as {key.principal!r}. A "
                f"[ROOT] sent now would not be canon and would block this box, so nothing "
                f"was minted", EX_SIGNATURE, kind="key")

    # -- the sends ----------------------------------------------------------

    def sofar(self) -> InitResult | None:
        """What this call had already sent (seam.md A2), or None."""
        if not self.minted:
            return None
        have = {**self.state.labels, **self.labels}
        return InitResult(root_id=self.root_id or "", minted=tuple(self.minted),
                          existing=_order(self.state.labels), recorded=None,
                          labels={n: have[n] for n in _order(have)},
                          superseded=self.supersedes)

    def mint_root(self, *, supersedes: str | None = None) -> str:
        """The new `[ROOT]`. SUPERSEDES names the old root for a NEW TREE
       : the wire carries `Supersedes: <old>` as a courtesy to
        readers and NO `In-Reply-To:`, and the body names the old id as
        provenance. The delivered copy is checked for exactly that shape
        before anything else is sent."""
        from . import _root

        self.supersedes = supersedes
        date = time.strftime("%a %d %b %Y %H:%M %Z")
        body = (SUPERSEDE_BODY.format(box=self.box, date=date, seat=self.sender, old=supersedes)
                if supersedes else ROOT_BODY.format(box=self.box, date=date, seat=self.sender))
        mid = self._send(_root.ROOT_SUBJECT, body=body, in_reply_to=None, supersedes=supersedes)
        self.root_id = mid
        self.minted.append(_root.ROOT_SUBJECT)
        self._verify(mid, _root.ROOT_SUBJECT, in_reply_to="", supersedes=supersedes)
        self.say(f"minted  {_root.ROOT_SUBJECT} {mid}"
                 + (f" (supersedes {supersedes})" if supersedes else ""))
        return mid

    def mint_label(self, name: str, root_id: str) -> str:
        mid = self._send(name, body=None, in_reply_to=root_id)
        self.minted.append(name)
        self._verify(mid, name, in_reply_to=root_id)
        self.labels[name] = mid
        self.say(f"minted  {name} {mid}")
        return mid

    def _send(self, subject: str, *, body: str | None, in_reply_to: str | None,
              supersedes: str | None = None) -> str:
        """One send through the package's sender; the receipt's Message-ID, bare.
        SUPERSEDES is sent UNTHREADED: the sender would otherwise thread a
        supersede under its target (`_sender.py`, send:1093), which is the
        reply the spec forbids for a root."""
        from . import _maildir, _sender

        child = {**self.env, **self.state.key.signing_env(),
                 _sender.REQUIRE_SIGNATURE: "1"}
        req = _sender.Request(to=self.box, from_=self.sender, subject=subject,
                              no_body=body is None,
                              in_reply_to=f"<{in_reply_to}>" if in_reply_to else None,
                              supersedes=f"<{supersedes}>" if supersedes else None,
                              unthreaded=supersedes is not None)
        try:
            receipt = _sender.send(req, (body or "").encode("utf-8"), child)
        except _sender.Refused as refusal:
            # `sent` is "unknown" only after the wire reached the MTA (its
            # exit was not 0): say so, and stop, as a receipt without an id
            # did. Everything else was refused before a byte went anywhere.
            maybe = refusal.sent != "no"
            raise self._failed(f"the sender refused {subject} ({refusal.kind}, exit "
                               f"{refusal.code}): {refusal}"
                               + ("; it MAY have been sent" if maybe else ""),
                               subject) from None
        mid = _maildir.bare_id(receipt.message_id)
        if not _RECEIPT_ID.fullmatch(mid):
            raise self._failed(f"the sender's receipt for {subject} names no Message-ID "
                               f"({receipt.message_id!r}); it MAY have been sent", subject)
        return mid

    def _failed(self, why: str, subject: str) -> InitError:
        """A send that did not go (or is not known to have gone). No row of
        the closed table names it, so it is 2, kind "send": the route to BOX
        did not take the message. `.result` is what had gone BEFORE it."""
        done = f"; already sent: {', '.join(self.minted)} under root {self.root_id}" \
            if self.minted else "; nothing was sent before it"
        return InitError(f"BOX {self.box!r}: {why}{done}", EX_NOT_FOUND, kind="send",
                         result=self.sofar())

    def _verify(self, mid: str, subject: str, *, in_reply_to: str,
                supersedes: str | None = None) -> None:
        """Find MID in BOX's maildir, To BOX, and check it against the box
        key. The minted root not verifying, or a label not verifying, stops
        the call: exit 3, nothing further sent (spec delta, the exit table).
        The copy must also have the SHAPE that was sent: the subject, the
        In-Reply-To: (none, for a root), and for a superseding root a
        `Supersedes:` naming SUPERSEDES, so a sender that threaded it or
        dropped the courtesy header cannot leave a root the read half would
        misread."""
        from . import _maildir, _root

        state = self.state
        verify = VERIFY or _root.ssh_verify
        deadline = time.monotonic() + DELIVERY_WAIT_SECONDS
        while True:
            copies = [e for e in _maildir.enumerate_box(str(state.maildir), dict(self.env))
                      if e.message_id == mid and _root._to_box(e.to, self.box)]
            if copies:
                break
            if time.monotonic() >= deadline:
                raise InitError(
                    f"BOX {self.box!r}: {subject} {mid} was sent but did not arrive in "
                    f"{state.maildir} within {DELIVERY_WAIT_SECONDS:g}s, so it could not be "
                    f"verified against the box key. Nothing further was minted",
                    EX_SIGNATURE, kind="delivery", result=self.sofar())
            time.sleep(DELIVERY_POLL_SECONDS)
        good = [e for e in copies if verify(e.path, state.key)]
        shaped = [e for e in good
                  if _root.canon_label(e.subject) == subject and e.in_reply_to == in_reply_to
                  and (supersedes is None or supersedes in _root._copy(e).supersedes)]
        if not shaped:
            why = ("does not verify against the box key (principal "
                   f"{state.key.principal!r})" if not good else
                   "arrived with a subject, In-Reply-To: or Supersedes: other than the one sent")
            raise InitError(
                f"BOX {self.box!r}: the minted {subject} {mid} {why}. It is not canon; "
                f"nothing further was minted", EX_SIGNATURE, kind="signature",
                result=self.sofar())


def probe(key, env: Mapping[str, str]) -> bool:
    """True when KEY's signing half signs a probe that verifies as the box.

    Sent nowhere. A small wire is signed by `SIGNER` (the sender's own v2
    signer, `_sender.sign_wire`) with the variables the mint hands the sender
    (`BoxKey.signing_env`), then judged by `VERIFY` (the read half's verifier)
    exactly as a minted message is. A `can_sign` key is only the right SHAPE;
    this is the measurement that the sender will produce a signature the box
    key accepts. The probe's addresses are qualified, so no mail origin is
    looked up for it."""
    import tempfile
    from pathlib import Path

    from . import _root, _sender, _sig

    signer = SIGNER or _sender.sign_wire
    verify = VERIFY or _root.ssh_verify
    wire = (f"From: {key.principal}@probe.invalid\nTo: {key.principal}@probe.invalid\n"
            f"Subject: [ROOT] signing probe\nDate: Tue, 01 Jan 2030 00:00:00 +0000\n"
            f"Message-ID: <probe@probe.invalid>\nMIME-Version: 1.0\n"
            f"Content-Type: text/plain; charset=utf-8\n\n"
            f"memory init: a signing probe, never sent\n").encode("utf-8")
    header = signer(wire, {**env, **key.signing_env()}, [])
    if not header:
        return False
    with tempfile.TemporaryDirectory(prefix="sessionmail-probe-") as tmp:
        path = Path(tmp) / "probe.eml"
        path.write_bytes(_sig.insert_header(wire, header))
        return bool(verify(path, key))


#: A bare Message-ID as a receipt carries it: one `local@domain`, no spaces.
_RECEIPT_ID = re.compile(r"[^<>\s@]+@[^<>\s@]+")
