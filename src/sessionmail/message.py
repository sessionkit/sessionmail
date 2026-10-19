"""`message` - moving messages. Scaffolded from its authored help pages.

Pages: `help/message.txt` and `help/message/*.txt`. Every flag below
is on one of those pages. Bodies are stubs: see `_stub`.
"""

from __future__ import annotations

from datetime import datetime

import click

import fcntl
import os
import re
import signal
import stat
import sys
import time

import json
from pathlib import Path

from . import (__version__, _archive, _boxes, _maildir, _mhs, _notmuch, _reach, _seat, _send, _subscribe,
               _settings, _setup, _subscription, _tokens, _why)
from ._help import CONTEXT_SETTINGS, OrderedGroup, PageCommand, unwrapped
from ._stub import stub

CAPABILITY = "message"

#: The group page: "0 ok · 1 usage · 2 refused · 3 the MTA did not accept it".
EX_REFUSED = 2


@click.group(
    cls=OrderedGroup,
    # The verbs' own width (send, path, box): `subscription` alone puts its
    # line under its name, instead of wrapping every row (tasks 3.4).
    name_column=4,
    # No blank line between Options and Commands: with `verify` from master
    # there are 12 rows, and that one line keeps `mcp` inside `head -20`.
    joined_commands=True,
    # With `setup` listed, two more of click's own lines: no blank line
    # before Options, and no `-h, --help` row on this page (every verb's
    # page keeps its own; `-h` still answers here).
    joined_options=True,
    help_row=False,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="<verb> [args]",
    epilog=unwrapped(
        """Identity is the environment: the seat, then the session. BOX defaults
to your seat. message moves messages and never blocks; waiting is
pager's job; the filesystem view of a box is memory's.

Exit: 0 ok · 1 usage · 2 refused · 3 the MTA did not accept it"""
    ),
)
@click.version_option(__version__, "--version", "-V")
def cli() -> None:
    """message - send, list, tree, read, mark and verify mail in an agent maildir"""


def _reply_from(parent_id: str, declared: str) -> tuple[str, str]:
    """(the `From:` for a reply to PARENT_ID, a note when it is not obvious).

    specs/message, "A reply answers from the address it was mailed at": a
    reply goes out from the address the PARENT was delivered to, when that
    is one this session answers to. Mail that arrived at the session id is
    answered from the session id; mail that arrived at the seat, from the
    seat.

    THE REASON IS PRIVACY, NOT IDENTITY: a session id is a DM
    and a seat address is on the record, so answering a DM from the seat
    silently promotes a private exchange to an official one - and the
    sender, who chose the private door, is the last to know - like a reply
    to a direct message that comes back from a work address.

    EVERY FALL-BACK IS ANNOUNCED, because the failure here is silent: a DM
    answered from the work address looks exactly like an ordinary reply. So
    when the parent cannot be read, or went somewhere this session does not
    answer to, this SAYS so rather than quietly using the seat. The note is
    the caller's only chance to notice.

    The source is `X-Original-To` and ONLY that. `Delivered-To:` is a real,
    different header and is always the box being read, so it cannot
    distinguish anything (`specs/pager`, same clause, same reason).
    """
    boxes: list[str] = []
    session = _seat.session_id()
    if session not in ("", "-"):
        boxes.append(session)
    try:
        boxes.append(_seat.resolve().name)
    except _seat.NoIdentity:
        pass
    from . import _sender   # the one parent lookup: notmuch, then the walk

    for box in boxes:
        # ONE file, found the way the sender finds a parent (notmuch in ~10 ms,
        # the walk for what it misses), scoped to THIS box: the copy that
        # matters is the one delivered here, not a Cc'd copy in another seat's
        # box. Parsing every message in both boxes cost 4.3 s a reply (11,773
        # files; profiled).
        try:
            d = _maildir.box_dir(box)
            path = _sender.find_parents(d, {parent_id}, os.environ).get(parent_id)
        except Exception:
            # A box that will not open is not a reason to refuse a send; it
            # is a reason to fall back and say so, which the tail does.
            continue
        if path is None:
            continue
        found = _sender.header_values(_sender._head(path), "X-Original-To")
        to = found[0].strip() if found else ""
        if not to:
            # specs/pager rules that a message with no X-Original-To groups
            # under the EMPTY address. Empty is not an address to send FROM.
            return declared, (f"the parent {parent_id} carries no X-Original-To; "
                              f"From is the declared address {declared!r}")
        ours = _seat.answers_to(to)
        if ours:
            # `ours`, not the delivered address: a mention arrives at
            # `<ours>+<suffix>` and is answered from `<ours>`. Nobody
            # replies from `widget+lead+boot`.
            return ours, ""
        return declared, (f"the parent {parent_id} was delivered to {to!r}, which this "
                          f"session does not answer to; From is the declared address "
                          f"{declared!r}")
    return declared, (f"the parent {parent_id} is not in this session's boxes, so the "
                      f"address it was delivered to is unknown; From is the declared "
                      f"address {declared!r}")


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="TO [TO...] -s SUBJECT (-f FILE | --no-body) [options]",
    short_help="TO... -s SUBJECT (-f FILE|--no-body)  send a message; prints its id",
    epilog=unwrapped(
        """From is your seat, from the environment; there is no --from. A body
is a file or nothing: an inline body is refused, so a heredoc cannot
send an empty message and exit 0. An unknown -r/--supersedes id still
sends, with a warning; a malformed one is refused. --supersedes takes
ONE id, so one mail retires one item; repeat the flag and only the last
counts, with no warning. Prints the id on stdout.
--message-id sends under an id you chose, a NEW one at this host's bare
short name (<x@HOST>): one another host's, ending .session-mail, or the
id it replies to is refused, and nothing is sent.
A host may shorten an over-69 -s by at most its cap, default 10 over;
SESSIONMAIL_SUBJECT_SHORTEN=off stops it.

Exit: 0 the MTA accepted it · 1 usage · 2 refused (subject, address,
a malformed or foreign id) · 3 the MTA did not accept it · 4 a human's
render failed"""
    ),
)
@click.argument("to", nargs=-1, required=True, metavar="TO [TO...]")
@click.option("-s", "--subject", metavar="TEXT", required=True,
              help="<= 69 chars, the point in the first 55; refused "
                   "if it could execute (` or $( )")
@click.option("-f", "--body-file", metavar="FILE",
              help="the body, byte-verbatim; `-f -` reads stdin")
@click.option("--no-body", is_flag=True,
              help='the subject IS the message ("speak EOM")')
@click.option("-r", "--in-reply-to", metavar="ID",
              help="thread onto ID (In-Reply-To and References)")
@click.option("--supersedes", metavar="ID",
              help="this REPLACES ID; the old stays as provenance. ONE id: "
                   "given twice, only the last counts")
@click.option("--message-id", metavar="ID",
              help="a NEW id for this mail, never the one it replies to; the "
                   "part after @ must be this host's bare short name: <x@HOST>")
@click.option("-c", "--cc", metavar="ADDR", multiple=True,
              help="repeatable, one address per flag")
@click.option("--bcc", metavar="ADDR", multiple=True,
              help="as --cc, and no other recipient sees it")
@click.option("--header", metavar="'NAME: V'", multiple=True,
              help="any header but send's own, repeatable")
@click.option("--raw", is_flag=True,
              help="never render: single-part, byte-exact")
@click.option("--renderer", metavar="PATH",
              help="render with PATH, not the built-in")
@click.option("--no-shorten", is_flag=True,
              help="never shorten the subject")
@click.option("--json", "as_json", is_flag=True,
              help="one JSON object on stdout: sent, id or KIND, notices")
def send(to: tuple[str, ...], subject: str, body_file: str | None, no_body: bool,
         in_reply_to: str | None, supersedes: str | None, message_id: str | None,
         cc: tuple[str, ...], bcc: tuple[str, ...], header: tuple[str, ...], raw: bool,
         renderer: str | None, no_shorten: bool, as_json: bool) -> None:
    """message send - send one message to one or more addresses; print its id

    \f
    fold-override: 2030-10-03 maintainer - the send form, its flags and exit codes are above the fold; the page grew with the subject shortener
    """
    # `--json` (openspec/changes/agents-send-door): ONE object on stdout and
    # nothing on stderr; a program reads one key, `sent`. A refusal carries
    # its KIND. And it means the PYTHON sender, in-process: the
    # bash oracle has no kinds. WITHOUT the flag every line below does exactly
    # what it did, byte for byte, exit for exit.
    notices: list[str] = []

    def notice(text: str) -> None:
        notices.append(text) if as_json else click.echo(text, err=True)

    def answer(obj: dict, code: int) -> None:
        # ASCII on the wire: the mail may already be with the MTA, and an
        # encoding error HERE would turn a sent message into a crash.
        click.echo(json.dumps({**obj, "notices": [*notices, *obj.get("notices", [])]}))
        raise SystemExit(code)

    def refuse(kind: str, message: str, *, usage: bool = False, either_door: str = "") -> None:
        # EITHER_DOOR: the same refusal in words that fit the tool too. The
        # object is what an AGENT reads, through parameters called `body` and
        # `no_body`; it has never seen `-f FILE`. The
        # command line's own words stay byte for byte what they were.
        if as_json:
            answer({"sent": "no", "kind": kind, "message": either_door or message}, EX_REFUSED)
        if usage:
            raise click.UsageError(message)
        click.echo(f"message send: {message}", err=True)
        raise SystemExit(EX_REFUSED)

    # The strangler rule: this is
    # the click front door; the bash session-mail-send is the ORACLE behind
    # it for signing, the multipart form, threading-id checks, the
    # deliverability preflight and the transport. The rules the spec adds
    # on top (69 ASCII chars, the guard switch, EOM-implies-no-body, bytes
    # in / bytes out) are checked HERE, before anything is written.
    try:
        # `.address`, NOT `.name`: the address KEEPS the `+` and
        # the name deliberately drops it, because a name is a directory and
        # an address is who you are. Sending as `.name` made a task seat
        # answer as its PROJECT: a task seat replied to mail addressed to
        # `widget+tm-123.lead` and the reply went out `From: widget@alpha`,
        # so the doorbell could reach a seat that could not answer as itself
        # (measured).
        # The bash oracle already does the right thing with a plus `--from`
        # (measured: `--from alice+notes` -> `From: alice+notes@`),
        # so the whole defect was this one attribute.
        seat = _seat.resolve()
        sender = seat.address
    except _seat.NoIdentity:
        refuse("identity", "no seat in the environment to send From - there is "
                           "no --from; set an identity", usage=True)
    # The subject shortener, at the ONE line every door passes. OFF - the tool's default - is `check_subject` exactly as
    # before and nothing ahead of it. ON (the host's locked file, and neither
    # the flag nor the seat turned it off): a line break or a control is
    # refused FIRST, so it is never laundered into a space; then the subject
    # is fitted, and a subject the shortener declines (gutted, or no room) is
    # refused in today's words with why it was not shortened.
    original, report, fit = subject, "", None
    on, why = _send.shorten_on(no_shorten=no_shorten)
    if why:
        notice(f"message send: {why}")
    if on:
        from . import _sender, _shorten   # only the ON road needs them

        if "\n" in subject or "\r" in subject or _sender.CONTROL.search(subject):
            refuse("subject", "refused - the subject holds a line break or a control "
                              "character; it is never shortened, as that would hide it. "
                              "Put it in the body")
        # The guard reads the subject AS TYPED: a cut that dropped a backtick
        # would send what today refuses, and keep it in X-Original-Subject.
        if _send.could_execute(subject):
            refuse("subject", _send.could_execute(subject))
        # The host's cap. A subject more than `set subject-shorten-max`
        # (default 10) OVER the limit is declined and refused below, in
        # today's words plus "<k> over; the cap is <N>". A bad cap line is
        # named here, on every send, and the default stands.
        cap, bad = _send.shorten_max()
        for line in bad:
            notice(line)
        fit = _shorten.shorten(subject, _send.limit(), cap=cap)
        if fit.declined:
            refusals = _send.check_subject(subject)
            if refusals:
                refuse("subject", f"{refusals[0]} (not shortened: {fit.declined})")
        else:
            subject, report = fit.subject, fit.report()
    for refusal in _send.check_subject(subject):
        refuse("subject", refusal)
    for addr in (*to, *cc, *bcc):
        if "\n" in addr or "\r" in addr or "," in addr:
            refuse("address", f"refused - a recipient may not carry a comma or a "
                              f"line break: {addr!r}; one address per flag")
    for label, value in (("--in-reply-to", in_reply_to), ("--supersedes", supersedes)):
        if value and ("&lt;" in value or "&gt;" in value):
            refuse("thread-id", f"refused - {label} {value!r} is the HTML-escaped "
                                f"form, which threads to nothing; pass <id@host> or id@host")
    if body_file and no_body:
        refuse("usage", "-f FILE and --no-body together: which is it?", usage=True,
               either_door="a body and no-body together: which is it?")
    if not body_file and not no_body:
        if _send.is_eom(subject):
            no_body = True   # specs/message: an EOM subject MAY carry no body
        else:
            # ONE fault, ONE word: `body`, as the sender's own read_body says.
            refuse("body", "no body: -f FILE, or --no-body when the subject IS "
                           "the message (a subject ending ' EOM' implies it)", usage=True,
                   either_door="no body given: give one, or say no-body when the subject "
                               "IS the message (a subject ending ' EOM' implies it)")
    tmp = None
    if body_file == "-":
        # the authored page: `-f -` reads stdin. Bytes, not a shell string:
        # copied to a private file the oracle reads verbatim.
        tmp = _send.spool(sys.stdin.buffer.read())
        body_file = str(tmp)
    try:
        extra, custom = _send.parse_headers(header)
    except ValueError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        refuse("header", str(exc), usage=True)
    carried = dict(zip(extra[::2], extra[1::2]))   # {"--task": T, "--project": P}, before --raw joins
    # The send switches. The bash oracle knows --raw but
    # neither the off switch nor --renderer, so the door says them in the
    # bash's words: off is --raw, a named renderer is its env variable.
    if raw or not _send.render_on():
        extra.append("--raw")
    if len(to) > 1:
        notice(f"message send: notice - one To is what the transport carries; "
               f"{', '.join(to[1:])} go as Cc of the same message")
    # LAST, after every refusal has had its say: a send that is about to be
    # refused should not scan a maildir, and must not print a From note it
    # will never use.
    if in_reply_to:
        sender, why = _reply_from(_maildir.bare_id(in_reply_to), sender)
        if why:
            notice(f"message send: {why}")
    # The package's own sender is THE sender: one core function sends mail,
    # in Python. The bash takes a send only when a seat names it in
    # $SESSIONMAIL_SEND_ORACLE, for one release - and never for --json or a
    # header it cannot carry, which say so.
    # A CHANGED subject goes here too: the bash cannot
    # carry X-Original-Subject, and nothing is dropped on the floor.
    changed = subject != original
    # A KEYED seat never takes the
    # bash, which signs v1 only; the package's sender signs v2. Unkeyed,
    # neither signs, so the bash stays the one-release wire diff it was named
    # for. A seat that MUST sign never takes the bash either,
    # which does not read $SESSIONMAIL_REQUIRE_SIGNATURE and would fail OPEN;
    # the package's sender signs or refuses. Both read only when the bash is
    # named.
    v2 = must_sign = False
    if _send.oracle() is not None:
        from . import _sender
        v2 = _sender.keyed(_send.os.environ)
        must_sign = _sender.must_sign(_send.os.environ)
    # The bash is taken only when the carrier is an MTA (`_settings.carrier`:
    # the variable, else the settings file, else the drop by default). The
    # mail drop goes here: the bash cannot drop, and a host with no postfix
    # may have no bash sender at all. So does a carrier that is not one: the
    # package's sender refuses it by name, where the bash would not.
    from . import _settings
    carrier = _settings.carrier(_send.os.environ)
    dropping = carrier.kind == "drop"
    unsent = carrier.kind == "refused"
    # A chosen Message-ID goes here too. The bash has no such flag, so
    # sent there the id would be lost and the bash would mint its own.
    # `is not None`, not truthiness: an EMPTY id must reach
    # `validate` and be refused, never fall through to the bash unsaid.
    chosen = message_id is not None
    if (as_json or custom or chosen or changed or must_sign or v2 or dropping or unsent
            or _send.oracle() is None):
        from . import _sender   # only this road needs it

        if _send.os.environ.get(_send.ORACLE_VAR):
            # Only --json, a custom header, --message-id, a shortened
            # subject, must-sign, the mail drop or a keyed seat
            # reach here with the oracle named; say which one did.
            # The drop is named before a
            # keyed seat: the bash cannot drop at all, keyed or not, so it is
            # the deciding reason.
            why = ("--json" if as_json else
                   "--header " + ", ".join(k for k, _ in custom) if custom else
                   "--message-id (the bash has no such flag)" if chosen else
                   "a shortened subject (its X-Original-Subject)" if changed else
                   f"${_sender.REQUIRE_SIGNATURE} (the bash cannot refuse an unsigned send)"
                   if must_sign else
                   "the mail drop (the bash cannot drop)" if dropping else
                   "a carrier that is not one (the package's sender names it)" if unsent else
                   "a keyed seat (the bash signs v1 only; this send signs v2)")
            notice(f"message send: {why} goes by the package's own sender; "
                   f"${_send.ORACLE_VAR} is not consulted")
        # A short name before the address, on THIS road only. The
        # bash oracle below hands its raw --from to `sendmail -f`, and what
        # postfix makes of a name-addr there is unmeasured; the package's
        # own sender gives -f the bare address (_sender._deliver).
        sender, why = _send.with_display_name(sender, seat.name, _seat.session_id())
        if why:
            notice(f"message send: {why}")
        req = _sender.Request(to=to[0], from_=sender, subject=subject, body_file=body_file,
                              no_body=no_body, in_reply_to=in_reply_to, supersedes=supersedes,
                              message_id=message_id, cc=[*to[1:], *cc], bcc=list(bcc),
                              raw="--raw" in extra,
                              renderer=renderer, task=carried.get("--task", ""),
                              project=carried.get("--project", ""), headers=custom,
                              original_subject=original if changed else "")
        try:
            receipt = _sender.send_request(req, _send.oracle_env(renderer))
        except _sender.Refused as r:
            code = r.code if r.code in (EX_REFUSED, 4) else 3
            if not as_json:
                for n in r.notices:
                    click.echo(n, err=True)
                click.echo(f"message send: {r}", err=True)
                raise SystemExit(code)
            answer({"sent": r.sent, "kind": r.kind, "message": str(r), "notices": r.notices},
                   code)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        # The report comes AFTER the MTA accepted: stderr
        # on the command line, one of the notices under --json and the MCP.
        done = [*receipt.notices, *([report] if report else [])]
        if changed:
            # The counter, at ONE site after the MTA accepted, so
            # the MCP child counts once. Best effort: a failure is a notice.
            from . import _metrics

            reason = "subject_shortened" if fit.shortened else "subject_folded"
            unwritten = _metrics.count(reason, len(original) - len(subject))
            done += [unwritten] if unwritten else []
        if not as_json:
            for n in done:
                click.echo(n, err=True)
            click.echo(receipt.message_id)
            return
        answer({"sent": "yes", "id": receipt.message_id, "notices": done}, 0)
    argv = _send.argv(to=to[0], sender=sender, subject=subject, body_file=body_file,
                      no_body=no_body, in_reply_to=in_reply_to, supersedes=supersedes,
                      cc=(*to[1:], *cc), bcc=bcc, extra=extra)
    try:
        code, out, err = _send.run(argv, _send.oracle_env(renderer))
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    if err.strip():
        click.echo(err.rstrip("\n"), err=True)
    if code == 0:
        click.echo(out.rstrip("\n"))
        return
    raise SystemExit(code if code in (EX_REFUSED, 4) else 3)


def _looks_like_an_id(token: str) -> bool:
    """Is this token an ID rather than a BOX?

    An id is an index (digits) or a Message-ID (it carries `@`), bracketed
    or not; a box is a name, so it is neither. This is the ONE place
    `message read [BOX] ID...` is split, and it is needed only because ids
    became many: `read 1 2` used to mean box `1`, message `2`, and now means
    two messages in your own box. A BOX may still only come first, and a
    lone argument is still an id (`read gamma` looks for a message `gamma`,
    as it did before).
    """
    bare = token.strip().strip("<>")
    return bare.isdigit() or "@" in bare


def _receipt(lines: list[str], ids: list[str], what: str, missing: list[str]) -> str:
    """The ONE receipt a call prints, however many ids it carried.

    specs/message, "Read and mark take many ids in one call": *"one receipt,
    not one per id"*. One id prints the mark line itself - it is already one
    line that names its id, and it is what a reader pastes - so the single-id
    form is unchanged. Many print one line naming each in the order given;
    every line is in the marks file either way. A miss is named here too, so
    a reader who sees only stdout still knows something was missed.
    """
    if len(lines) == 1 and not missing:
        return lines[0]
    # One Message-ID held by two files is two `list` rows,
    # and was receipted twice (`15 read - X, X, Y, Y`). An id is named once,
    # in the order first given; the count is of ids, not of files.
    ids = list(dict.fromkeys(ids))
    missing = list(dict.fromkeys(missing))
    text = f"{len(ids) if ids else len(lines)} {what}" + (f" - {', '.join(ids)}" if ids else "")
    if missing:
        text += f"; {len(missing)} not found: {', '.join(missing)}"
    return text


def _mark_summary(marked: list[str], refused: list[str], what: str) -> str:
    """`message mark`'s receipt when it carries more than one lone success:
    `N of M marked <mark> - ids; K refused: ids`.

    It is the FIRST line mark writes, and it carries no path, because seats
    read mark through `2>&1 | tail -N`, `head` and `cut`: once,
    `| tail -3` over 43 refusals read as "only 3 failed", since
    the count was printed last, and only when something was marked. Whatever
    a pipe keeps of the head, it keeps the count. The `; K refused` tail is
    left off when nothing was refused, and the ` - ids` part when nothing
    was marked (`0 of 43 marked done; 43 refused: ...`).
    """
    text = f"{len(marked)} of {len(marked) + len(refused)} marked {what}"
    if marked:
        text += f" - {', '.join(marked)}"
    if refused:
        text += f"; {len(refused)} refused: {', '.join(refused)}"
    return text


#: specs/message, "A listing defaults to the newest 40": how many rows a
#: listing shows when `-n` is not given (chosen on a box of 8,416 rows).
DEFAULT_ROWS = 40


def _id_shown(e: "_maildir.Entry", name: str, bare: str) -> str:
    """An id header as `read` shows it: `<id>`, or, when the parser could
    not read it, the mark and the id the mail is keyed on (the
    sender's raw text, quoted so whitespace and controls show)."""
    if name in e.unreadable:
        return f"<unreadable header> (keyed as {bare!r})"
    return f"<{bare}>"


def _rows(count: str | None) -> int | None:
    """How many rows to show. `None` means every row.

    specs/message, "A listing defaults to the newest 40": absent is the
    newest `DEFAULT_ROWS`; `0` or `all` lifts the cut. `-n` is parsed here
    rather than by click's `int` so that `all` is a value and not a usage
    error, and so the message for a bad one names what it takes.
    """
    if count is None:
        return DEFAULT_ROWS
    if count.strip().lower() == "all":
        return None
    try:
        n = int(count)
    except ValueError:
        raise click.UsageError(f"-n {count!r}: a row count, 0, or 'all'")
    if n < 0:
        raise click.UsageError(f"-n {n}: a row count is not negative - "
                               f"0 or 'all' shows every row")
    return None if n == 0 else n


def _default_box() -> str:
    """The box a bare `message list`/`read`/`path` reads: THE BOX THE DOORBELL
    DELIVERS TO.

    specs/message, "A bare listing is the doorbell's box". `pager listen` with no BOX blocks on this
    session's id - `pager.listen` does literally `token = box or session` - so
    a reader who has just been paged and types `message list` gets the box
    they were paged about, not the seat's.

    They really are different boxes: the seat's is shared by every session
    that ever held the seat, the session's is the one the page keyed on.

    ONE helper for all three verbs on purpose. `read` takes the index `list`
    prints, so if they disagreed about the box a pasted row number would read
    a different message - a worse bug than the one this fixes.

    THE FALLBACK IS ANNOUNCED. With no session id, or before `pager listen`
    has born the session box, there is nothing to read and the seat's box is
    read as before - but it says so on stderr, naming both. A silent fallback
    would leave a reader believing they had seen their session's mail when
    they had seen somebody else's.
    """
    session = _seat.session_id()
    why = ""
    if session not in ("", "-"):
        try:
            _maildir.resolve_box(session)
            return session
        except _maildir.NoSuchBox:
            # The session box is only born by `pager listen`; before that
            # there is nothing to list, so the seat's box is the honest
            # answer - said out loud, below.
            why = f"no maildir for this session ({session}) yet"
    try:
        seat = _seat.resolve().name
    except _seat.NoIdentity:
        raise click.UsageError("no BOX given and nothing in the environment says "
                               "which session or seat this is - name the box")
    if why:
        # ONLY the surprising case is announced: the reader HAS a session and
        # is being shown a different box. With no session id there is nothing
        # to confuse it with, and a line on every invocation would spend the
        # reader's attention to tell them what the footer already says.
        click.echo(f"message: {why}; reading the SEAT's box {seat!r} instead of "
                   "this session's - name a BOX to be sure", err=True)
    return seat


@cli.command(
    "list",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] [--unread] [--from M] [--subject RE] [--to ADDR] [-n N]",
    short_help="[BOX] [filters]                       a line per message, no bodies",
    epilog=unwrapped(
        """Never opens a body; costs a few tokens per row. The index it prints is
what `read` takes - the box's own, oldest first over new/ and cur/, so a cut
listing and a full one number a row the same. Without -n you get the newest
40; the last line says how many rows it hid. A draft (a `D` file: a turn
still being written) is never listed, numbered or counted; the last line
says how many drafts it left out.

Exit: 0 ok (even when empty) · 1 usage · 2 no such box"""
    ),
)
@click.argument("box", required=False, metavar="BOX")
@click.option("--unread", is_flag=True,
              help="only messages you have not marked read")
@click.option("--from", "from_", metavar="M",
              help="sender matches M (substring, case-insensitive)")
@click.option("--subject", metavar="RE", help="subject matches RE")
@click.option("--to", metavar="ADDR",
              help="only mail delivered to this exact address (a plus "
                   "address, or the bare box as '')")
@click.option("-n", "count", metavar="N",
              help="newest N (default: 40; 0 or all for every row)")
def list_(box: str | None, unread: bool, from_: str | None, subject: str | None,
          to: str | None, count: str | None) -> None:
    """message list - one line per message in a box: index, from, subject"""
    # specs/message, "A listing discloses what it hid, and a peek claims
    # nothing" + "An EOM subject may carry no body, and the listing says so".
    # Reads the physical box by bytes (_maildir); never opens a body beyond
    # the byte that says whether there is one; never renames or flags.
    if box is None:
        box = _default_box()
    try:
        pattern = re.compile(subject) if subject else None
    except re.error as exc:
        raise click.UsageError(f"--subject {subject!r} is not a regex: {exc}")
    rows = _rows(count)
    try:
        # pager-genie 3.28 (specs/message, "A listing does not count a draft
        # as mail, and says how many it hid"): a `D` file is neither listed
        # nor counted, not even in "index spans all", and the footer says
        # how many drafts it left out.
        entries, drafts = _maildir.enumerate_with_drafts(box)
    except _maildir.NoSuchBox as exc:
        click.echo(f"message list: {exc}", err=True)
        raise SystemExit(EX_REFUSED)

    # --unread is THIS SESSION's marks. They key on the session id, so the
    # reader is the session doing
    # the listing - not, as before, a seat invented from the box's name,
    # which read a file nobody had written unless the box and the seat
    # happened to share a name.
    marks: dict[str, set[str]] = {}
    if unread:
        try:
            marks = _seat.read_marks(_seat.resolve(), _seat.session_id())
        except _seat.NoIdentity:
            marks = {}
    superseded = {e.supersedes for e in entries if e.supersedes}
    hidden: dict[str, int] = {}

    def hide(why: str) -> None:
        hidden[why] = hidden.get(why, 0) + 1

    shown = []
    for e in entries:
        if e.message_id and e.message_id in superseded:
            hide("superseded"); continue
        if unread and "read" in marks.get(e.message_id, set()):
            hide("marked read (--unread)"); continue
        if from_ and from_.lower() not in e.sender.lower():
            hide("--from"); continue
        if pattern and not pattern.search(e.subject):
            hide("--subject"); continue
        if to is not None and e.delivered_to != (to if to != "" else box):
            hide("--to"); continue
        shown.append(e)
    if rows is not None and len(shown) > rows:
        # The cut is the LAST thing done, over the box's own index, so the
        # rows it keeps carry the numbers they had - a number pasted into
        # `read` means the same message from a cut listing and a full one.
        why = (f"older than -n {count}" if count is not None
               else f"older than the newest {DEFAULT_ROWS}")
        for _ in shown[: len(shown) - rows]:
            hide(why)
        shown = shown[len(shown) - rows:]
    if drafts:
        # Last among the reasons: a draft is not a message this listing
        # filtered, it is not mail at all - but what was left out is said.
        hide_n = len(drafts)
        hidden["draft" if hide_n == 1 else "drafts"] = hide_n

    for e in shown:
        flag = "N" if e.unread else "."
        tail = "" if e.has_body else "  (no body)"
        click.echo(f"{e.index:>4} {flag} {e.sender_local[:12]:<12} {e.subject}{tail}"
                   f"{e.unreadable_mark}")
    total_hidden = sum(hidden.values())
    why = ", ".join(f"{n:,} {reason}" for reason, n in hidden.items())
    # Counts are grouped (8,376) because a four-figure one is now ordinary;
    # the index column above is NOT, because that number is pasted into
    # `read` and a comma would not survive the paste.
    click.echo(f"-- {box}: {len(shown):,} shown, {total_hidden:,} hidden"
               + (f" ({why})" if why else "") + f"; index spans all {len(entries):,}")


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="ID [--depth N] [--all] [--dates]",
    short_help="ID [--depth N] [--all]                a thread's subjects as a tree",
    epilog=unwrapped(
        """Ends with its own token cost, so you know what you paid. For a
mailbox-as-memory path use `memory tree`.

Exit: 0 ok · 1 usage · 2 no such message"""
    ),
)
@click.argument("id", metavar="ID")
@click.option("--depth", type=int, metavar="N",
              help="cut N levels below the top (0 = the top only)")
@click.option("--all", "show_all", is_flag=True,
              help="keep superseded messages (default: hidden)")
@click.option("--dates", is_flag=True,
              help="show [sender date] instead of sender only")
def tree(id: str, depth: int | None, show_all: bool, dates: bool) -> None:
    """message tree - a thread's subjects as a tree, superseded hidden"""
    # specs/message, "Threading ids are checked, and a correction supersedes":
    # the tree is what threading buys - In-Reply-To/References read by
    # bytes from the seat's own box (_maildir.thread), a superseded message
    # hidden by default and counted, and the token cost of what was printed
    # on the last line so the reader knows what they paid.
    try:
        box = _seat.resolve().name
    except _seat.NoIdentity:
        raise click.UsageError("nothing in the environment says which seat this is - "
                               "the tree is read from your own box")
    try:
        entries = _maildir.enumerate_box(box)
    except _maildir.NoSuchBox as exc:
        click.echo(f"message tree: {exc}", err=True)
        raise SystemExit(EX_REFUSED)
    entry = _maildir.find(entries, id)
    if entry is None:
        click.echo(f"message tree: no message {id!r} in {box}", err=True)
        raise SystemExit(EX_REFUSED)
    # ID is the top. A thread's root is one hop up on a conversation and
    # [ROOT] on a mailbox-as-memory box, where "the whole thread" is the whole
    # box; rooting here keeps the verb bounded, and `--depth 0` = ID's own line.
    members = _maildir.descendants(entries, entry)
    above = _maildir.ancestors(entries, entry)
    superseded = {e.supersedes for e in members if e.supersedes}
    by_id = {e.message_id: e for e in members if e.message_id}
    children: dict[str, list] = {}
    root = entry
    if above:
        click.echo(f"(under: {above[-1].subject}; {len(above)} above)")
    for e in members:
        parent = e.in_reply_to if e.in_reply_to in by_id else (e.references[-1] if e.references and e.references[-1] in by_id else None)
        if e is root or parent is None:
            continue
        children.setdefault(parent, []).append(e)
    lines: list[str] = []
    hidden = 0

    def label(e) -> str:
        who = e.sender_local
        if dates and e.date:
            who = f"{who} {e.date.strftime('%a %d %b %H:%M')}"
        return f"{e.subject}  [{who}]{e.unreadable_mark}"

    def walk(e, level: int) -> None:
        nonlocal hidden
        if e.message_id in superseded and not show_all:
            hidden += 1
            return
        lines.append(("  " * level + ("└ " if level else "")) + label(e))
        if depth is not None and level >= depth:
            return
        for c in sorted(children.get(e.message_id, []), key=lambda x: x.index):
            walk(c, level + 1)

    walk(root, 0)
    text = "\n".join(lines)
    click.echo(text)
    click.echo(f"-- {len(lines)} shown, {hidden} hidden (superseded)"
               + (f", cut below depth {depth}" if depth is not None else "")
               + f" · ≈{_tokens.estimate(text)} tok")


#: pager-genie 3.28: what `read` says of a draft, in the one place.
DRAFT_NOTE = ("(a DRAFT: an open turn its keeper is still writing, not mail; "
              "nothing marked, nothing moved)")


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] N|ID [N|ID...] [--peek] [--thread] [--full] [--raw]",
    short_help="[BOX] N|ID... [--peek] [--thread]     open messages; marks each read",
    epilog=unwrapped(
        """Several ids in one call: each is printed in order and ONE receipt
lists them. A bad id is named and does not stop the others; the exit
says so. A leading BOX is a name - digits or an `@` make an id.

Reading appends `read` to this session's marks (see `message mark`);
the maildir flag is never touched, so nothing here wakes or silences a
listener. A draft is read by its Message-ID: it prints, says it is a
draft, and is neither marked nor moved.

Exit: 0 ok · 1 usage · 2 an id named nothing (the others were still read)"""
    ),
)
@click.argument("args", nargs=-1, required=True, metavar="[BOX] N|ID [N|ID...]")
@click.option("--peek", is_flag=True, help="do not mark it read")
@click.option("--thread", is_flag=True,
              help="also print the rest of its thread, oldest first")
@click.option("--full", is_flag=True,
              help="the whole body and every header (default: compact "
                   "headers, body cut at 40 lines / 400 tokens, and it says so)")
@click.option("--raw", is_flag=True, help="the RFC 5322 file verbatim")
def read(args: tuple[str, ...], peek: bool, thread: bool, full: bool, raw: bool) -> None:
    """message read - open one or more messages: headers and body; mark them read"""
    # specs/pager, "Read is not terminal": the read is an EVENT on this
    # session's marks (_seat.MARK_LINE: stamp, id, mark, seat, session), appended by
    # _seat.write_mark - the only writer. The maildir is never renamed or flagged; a --peek proves it.
    # specs/message, "Read and mark take many ids in one call": many ids, reported
    # in order, one receipt, and a bad one named rather than fatal.
    if len(args) >= 2 and not _looks_like_an_id(args[0]):
        box, wanted = args[0], list(args[1:])
    else:
        box, wanted = None, list(args)
    seat = None
    try:
        seat = _seat.resolve()
    except _seat.NoIdentity:
        if box is None:
            raise click.UsageError("no BOX given and nothing in the environment "
                                   "says which seat this is - name the box")
        if not peek:
            raise click.UsageError("no seat to record the read mark on - set an "
                                   "identity, or --peek")
    # The SAME box a bare `message list` reads - they must agree, because the
    # index `read` takes is the index `list` printed (specs/message, "A
    # listing defaults to the newest 40"). Moving `list` to the session box
    # and leaving `read` on the seat's would make a pasted row number read a
    # DIFFERENT message: worse than either box on its own.
    box = box or _default_box()
    try:
        entries, drafts = _maildir.enumerate_with_drafts(box)
    except _maildir.NoSuchBox as exc:
        click.echo(f"message read: {exc}", err=True)
        raise SystemExit(EX_REFUSED)
    found: list["_maildir.Entry"] = []
    missing: list[str] = []
    # pager-genie 3.28 (specs/message, "A listing does not count a draft as
    # mail"): a draft is found by its Message-ID only (it has no row), and
    # only when no MAIL carries that id - between a turn's finished mail and
    # the keeper removing its draft, the id names the mail. It is printed and
    # says it is a draft; it is never marked, and nothing here moves it.
    draft_found: set[int] = set()
    for which in wanted:
        entry = _maildir.find(entries, which)
        if entry is None:
            entry = _maildir.find_draft(drafts, which)
            if entry is not None:
                draft_found.add(id(entry))
        if entry is None:
            # Named, in the place it was asked for, and the others go on.
            click.echo(f"message read: no message {which!r} in {box} "
                       f"(index spans 1..{len(entries)})", err=True)
            missing.append(which)
            continue
        found.append(entry)

    def show(e: "_maildir.Entry") -> None:
        draft = id(e) in draft_found
        if raw:
            if draft:                   # the file stays verbatim; the notice goes aside
                click.echo(f"message read: {DRAFT_NOTE}", err=True)
            click.echo(e.path.read_bytes().decode("utf-8", "replace"), nl=False)
            return
        if draft:
            click.echo(f"#-  {e.subject}")
            click.echo(DRAFT_NOTE)
        else:
            click.echo(f"#{e.index}  {e.subject}")
        click.echo(f"From: {e.sender}")
        # To/Cc are the HEADERS, as written; the box the MTA delivered to is
        # its own field. This line once printed X-Original-To under the name
        # `To:` and no Cc at all, so a `To: alice, Cc: bob` read from bob's
        # box said `To: bob` - and its reader corrected the wrong party and
        # forwarded to nobody.
        # No To header (a Bcc-only mail) is `To: -`, never the box: the
        # fallback would be the same conflation in a narrower case. Box: below always carries the address.
        click.echo(f"To: {e.to or '-'}"
                   + (f"    Cc: {e.cc}" if e.cc else ""))
        click.echo(f"Date: {e.date.isoformat(timespec='minutes') if e.date else '?'}"
                   f"    Box: {e.delivered_to or box}")
        click.echo(f"Message-ID: {_id_shown(e, 'Message-ID', e.message_id)}"
                   + (f"    In-Reply-To: {_id_shown(e, 'In-Reply-To', e.in_reply_to)}"
                      if e.in_reply_to else ""))
        if e.unreadable:
            # Said once, in the header block, beside what was shown.
            click.echo(f"Unreadable: {', '.join(e.unreadable)} - the mail parser could "
                       f"not read {'it' if len(e.unreadable) == 1 else 'them'}; "
                       f"`--raw` shows the bytes as sent")
        body = _maildir.text_body(e.path)
        if not body.strip():
            click.echo("(no body)")
            return
        lines = body.rstrip("\n").split("\n")
        cut = None
        if not full:
            if len(lines) > 40:
                cut = f"{len(lines) - 40} more lines"
                lines = lines[:40]
            elif len(body) > 1600:
                cut = f"about {len(body) // 4 - 400} more tokens"
                lines = body[:1600].split("\n")
        click.echo("")
        click.echo("\n".join(lines))
        if cut:
            click.echo(f"... cut here: {cut}; --full for the rest")

    # --thread: the conversation THROUGH this message - its ancestors, then
    # it and everything under it. Not the whole thread: on a memory box that
    # is the whole box. With many ids, each one brings its own block, in the
    # order they were given, and the rule goes BETWEEN blocks - so one id
    # prints exactly what it always did.
    printed = 0
    for entry in found:
        members = ((_maildir.ancestors(entries, entry) + _maildir.descendants(entries, entry))
                   if thread and id(entry) not in draft_found else [entry])
        for e in members:
            if printed:
                click.echo("-" * 40)
            show(e)
            printed += 1

    if not found:
        raise SystemExit(EX_REFUSED)          # nothing read, nothing to receipt

    if peek:
        where = ", ".join(f"{e.path.parent.name}/{e.path.name}" for e in found)
        click.echo(f"peek: nothing marked, nothing moved - still {where}"
                   + (f"; {len(missing)} not found: {', '.join(missing)}" if missing else ""))
        if missing:
            raise SystemExit(EX_REFUSED)
        return
    # A draft is never marked (3.28): it is said, and left where it is.
    left = [e for e in found if id(e) in draft_found]
    if left:
        click.echo("draft: nothing marked, nothing moved - still "
                   + ", ".join(f"{e.path.parent.name}/{e.path.name}" for e in left))
    found = [e for e in found if id(e) not in draft_found]
    if not found:
        if missing:
            raise SystemExit(EX_REFUSED)
        return
    # One stamp for one call: the marks are one act, and the file keeps one
    # line per message read (Charter 1 - the stamp is generated, not written).
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    # Two files of one Message-ID are both SHOWN above (they
    # are two files), but the read is of the message: one row, one name.
    ids = list(dict.fromkeys(e.message_id or f"index-{e.index}" for e in found))
    lines = [_seat.write_mark(seat, mid, "read", _seat.session_id(), stamp)
             for mid in ids]
    click.echo(f"marked: {_receipt(lines, ids, 'read', missing)}")
    if missing:
        raise SystemExit(EX_REFUSED)


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] N|ID [N|ID...] seen|read|done [--reason TEXT]",
    short_help="[BOX] N|ID... seen|read|done          your private mark; never wakes",
    epilog=unwrapped(
        """N is the index `list` prints, resolved in BOX (default: your own
box), the same way `read` resolves it; ID is a Message-ID. Appends one
line - `<stamp> <Message-ID> <mark> <seat> <session-id>
[reason]` - to this SESSION's marks. Per session: a successor does not
inherit them. Never the maildir Seen flag. The pager wakes for not-seen
and rides along with not-done; a `done` is what quiets a line. A
reaction or a receipt to the SENDER is a reply, not a mark. A draft
(an id whose every file is a `D` file) is never marked. An id the
index has not reached yet is looked for in your own session and base
boxes on disk, and nowhere else.

Several ids in one call, MARK last: one line each, one receipt, and one
--reason for all of them. A bad id is named and does not stop the
others; the exit says so. An id given twice is marked once. The
receipt is stdout's one line: `N of M marked MARK - ids; K refused: ids`.

Exit: 0 ok · 1 usage · 2 an id named nothing (the others are marked)"""
    ),
)
@click.argument("ids", nargs=-1, required=True, metavar="[BOX] N|ID [N|ID...]")
@click.argument("mark_", metavar="seen|read|done",
                type=click.Choice(["seen", "read", "done"]))
@click.option("--reason", default="", metavar="TEXT",
              help="free text, written last on the line; on done, what "
                   "dealt with it (the task id, if one was made)")
def mark(ids: tuple[str, ...], mark_: str, reason: str) -> None:
    """message mark - record your private state on messages; never wakes anyone"""
    # specs/message, "Read and mark take many ids in one call": every id is
    # tried, in the order given; a bad one is named and does NOT stop the
    # others; one receipt, not one per id.
    #
    # OPEN, and NOT settled here: whether the many ids in one call need the
    # option of separate reasons. So ONE --reason applies to ALL the ids, and
    # there is deliberately no seam for one-per-id: building for both would
    # answer a question still open.
    ctx = click.get_current_context()
    # What the loop has to say about an id is HELD until the
    # summary is out, so the summary is the first byte mark writes; then
    # these follow on stderr, in the order they were said, each id named.
    said: list[str] = []

    def complain(why: str) -> None:
        said.append(f"message mark: {why}")

    try:
        seat = _seat.resolve()
    except _seat.NoIdentity as exc:
        # No seat is not about any one id: nothing can be marked at all,
        # so there is no count to lead with; said at once.
        click.echo(f"message mark: {exc}", err=True)
        ctx.exit(EX_REFUSED)
        return

    # Measured before this: 177 invocations, 0 marks. `list` prints an INDEX
    # and `mark` wanted a Message-ID, so the number in front of the reader
    # was the one thing the verb would not take. It takes it now, resolved in
    # BOX exactly the way `read` resolves it - same split, same helper, same
    # `_maildir.find` - so the two verbs cannot drift into two grammars.
    box = None
    if len(ids) >= 2 and not _looks_like_an_id(ids[0]):
        box, ids = ids[0], ids[1:]
    entries: list["_maildir.Entry"] | None = None
    listed_in = ""          # the box an index was resolved in, for a refusal

    def resolve(raw: str) -> tuple[str, Path | None] | None:
        """RAW as (a Message-ID, the file it was found in): an index looked
        up in BOX carries the file the box walk found (the walk has
        already read it, so the disk need not be asked again); anything else
        is the id as given, with no file. None means the index named nothing
        and has been complained about - the other ids go on."""
        nonlocal entries, listed_in
        if not raw.isdigit():
            return raw, None
        # task-address-doorbell D6: the index is looked up in the box a bare
        # `list` NUMBERED - the doorbell's, via the one helper `list` and
        # `read` use - never the seat's. A plus seat's seat box is its
        # project's, so a pasted row number would have marked a sibling's mail.
        where = listed_in = box or _default_box()
        if entries is None:
            try:
                entries = _maildir.enumerate_box(where)
            except _maildir.NoSuchBox as exc:
                complain(f"{exc} - an index is a position in a box; name the box, "
                         f"or pass the Message-ID")
                return None
        entry = _maildir.find(entries, raw)
        if entry is None or not entry.message_id:
            complain(f"no message {raw!r} in {where} (index spans 1..{len(entries)})")
            return None
        return entry.message_id, entry.path

    # When the index answers empty, refuses, or holds only drafts under the
    # id, the id is looked
    # for on disk in the seat's OWN two boxes - the session box and the base
    # box, the pair `_reply_from` reads - and nowhere else.
    #
    # Store mode: a store index holds no session box. Host mode: delivery and indexing are separate
    # steps, so a mail `list` just printed is not in the index until the
    # indexer's next `notmuch new` - seconds on a live loop, hours on a dead
    # one. Host mode has the fallback too, with ONE scope: the same two
    # boxes as store mode, which the seat can already
    # `message read`. Never another seat's box, never a project or store box
    # the seat does not own, never the mail root wholesale; an index resolved
    # in a box outside the two carries no file. The fallback only READS, to
    # find the file: the mark is written exactly as an indexed one is.
    # Both modes, so the mode is not read here: `_notmuch.search` picks the
    # index (store mode's profile) itself.
    from . import _sender   # here, not at the top: a lazy import
    own: list[tuple[str, Path]] | None = None

    def own_boxes() -> list[tuple[str, Path]]:
        """(name, maildir) of the seat's own boxes that exist: its session
        box, then its base box, one entry per directory."""
        nonlocal own
        if own is None:
            own = []
            seen: set[Path] = set()
            for name in (_seat.session_id(), seat.name):
                if name in ("", "-"):
                    continue
                try:
                    d = _maildir.box_dir(name)
                except _maildir.NoSuchBox:
                    continue
                if d.resolve() not in seen:
                    seen.add(d.resolve())
                    own.append((name, d))
        return own

    def in_own_box(path: Path) -> bool:
        """PATH is a file directly in an own box's new/ or cur/."""
        return (path.parent.name in ("new", "cur")
                and any(path.parent.parent.resolve() == d.resolve()
                        for _name, d in own_boxes()))

    def where_looked() -> str:
        """The own boxes, named, for a refusal (say what was looked up and
        where)."""
        names = [name for name, _d in own_boxes()]
        if not names:
            return "this seat has no own box on disk to look in"
        return f"not in this seat's own boxes ({', '.join(names)}: new/ or cur/)"

    def own_files(message_id: str, carried: Path | None) -> list[str]:
        """The files under MESSAGE_ID in the seat's own boxes, for the same
        draft check an indexed hit gets. CARRIED, the file an index resolved
        to, answers alone when it sits in an own box (the box walk already
        read it, and a listed entry is never a draft). Each box is searched
        in its OWN new/ and cur/ only: a store-level seat's box is the store
        directory, whose project boxes nest inside it (`_boxes.walk`), and a
        walk from the box would read every one of them. `find_messages`
        keeps ONE file per id, so when that one is a draft the box is
        enumerated for a finished copy beside it: the draft never decides
        alone (3.28: a draft only when EVERY file under the id is one)."""
        if carried is not None and in_own_box(carried):
            return [str(carried)]
        files: list[str] = []
        for _name, d in own_boxes():
            hits = [h for sub in ("new", "cur")
                    if (h := _sender.find_messages(d / sub, {message_id}).get(message_id))]
            if hits and all(_maildir.is_draft(Path(h)) for h in hits):
                try:
                    hits += [str(e.path) for e in _maildir.enumerate_box(str(d))
                             if e.message_id == message_id]
                except (_maildir.NoSuchBox, OSError):
                    pass                # gone since box_dir: the draft stands alone
            files += hits
        return files

    def only_drafts(filesets: list[tuple[str, ...]]) -> bool:
        """3.28's predicate: every file under the id is a draft."""
        return all(fs and all(_maildir.is_draft(Path(f)) for f in fs) for fs in filesets)

    # Charter 1: the stamp is generated, never written by hand. One call is
    # one act, so the ids it carries share its stamp.
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    marked: list[str] = []
    lines: list[str] = []
    missing: list[str] = []
    for raw in ids:
        # An id is tried ONCE per call, first one wins - two
        # files of one Message-ID are two `list` rows, and an id may be
        # typed twice. One row, one receipt entry, one refusal.
        if raw in missing:
            continue
        resolved = resolve(raw)
        if resolved is None:
            missing.append(raw)
            continue
        message_id, carried = resolved[0].strip().strip("<>"), resolved[1]
        if message_id and (message_id in marked or message_id in missing):
            continue
        if not message_id or any(c.isspace() for c in message_id):
            # An EMPTY id once reached the search
            # as `id:` - which notmuch answers with everything - and a row with
            # no id was written. A shell variable that came up empty is the
            # common cause; say so instead of marking nothing.
            complain(f"the message id is empty or has spaces ({raw!r}) - pass one "
                     "Message-ID, as printed at send time or read from the file's "
                     "Message-Id header")
            missing.append(raw)
            continue
        refused = False
        try:
            filesets = [m.filenames for m in _notmuch.search(f"id:{message_id}")]
        except _notmuch.CouldNotLook as exc:
            # The page documents 0, 1 and 2 only, and 2 is "no such message".
            # An index we could not read is NOT the same claim, so it says so
            # in words even though it must share the code, and
            # says it first, so the seat learns its index is missing; then
            # its own boxes answer (in host mode too).
            complain(f"could not check the index for {message_id}: {exc}; "
                     f"looking in this seat's own boxes")
            refused, filesets = True, []
        if not filesets or only_drafts(filesets):
            # Empty, refused, OR draft-only: the index has not reached every
            # file under the id (a store index holds no session box; a host
            # index lags delivery), so an indexed draft must not hide the
            # finished turn already delivered to this seat's own box (3.28
            # is "every file under the id").
            on_disk = own_files(message_id, carried)
            if on_disk:
                filesets = filesets + [tuple(on_disk)]
        if not filesets:
            # Say what it WOULD take, and say what was
            # looked up and where, so "no such message" is never said of a
            # mail `list` just printed without the reader seeing why.
            outside = ""
            if carried is not None and not in_own_box(carried):
                outside = (f"; {raw} was listed in {listed_in}, which is "
                           "not this seat's own box, so it is not looked for on disk")
            if refused:
                # Not "no such message": the index could not say (above).
                complain(f"not marked: {message_id} - the index could not be read "
                         f"(said above) and {where_looked()}{outside}")
            else:
                complain(f"no such message: {message_id} - not in the index "
                         f"(notmuch) and {where_looked()}{outside}; mark takes a "
                         f"Message-ID, or the index `list` prints (a number)")
            missing.append(message_id)
            continue
        if only_drafts(filesets):
            # pager-genie 3.28: "No verb of `message` SHALL mark, claim, move
            # or remove a draft." Only when EVERY file under the id is a
            # draft: once the finished turn has arrived under the same id,
            # the id names that mail, and marking it is marking the mail.
            complain(f"{message_id} is a draft - an open turn its keeper is still "
                     "writing, and no verb of message marks a draft; its finished "
                     "turn, under the same Message-ID, can be marked once it arrives")
            missing.append(message_id)
            continue
        lines.append(_seat.write_mark(
            seat, message_id, mark_, _seat.session_id(), stamp, reason
        ))
        marked.append(message_id)

    marks_file = _seat.marks_path(seat, _seat.session_id())
    if len(lines) == 1 and not missing:
        # The single-id form, a regression PIN: one success prints the mark
        # line itself - it names its id and has nothing to hide.
        click.echo(f"{marks_file}: {lines[0]}")
        for line in said:              # e.g. an index it could not read
            click.echo(line, err=True)
        return
    # The summary IS the one receipt (specs/message, "one
    # receipt, not one per id"), on stdout, first; a `2>/dev/null` caller
    # still sees the count, and stdout holds nothing else, so `| tail -1`
    # keeps it too. Then each refusal, named, in order, on stderr; then
    # where the rows went, after the count and never in front of it. That
    # last line carries the tally again, without the ids, so the pipe
    # seats use, `2>&1 | tail -3`, ends on the count, not on a refusal.
    click.echo(_mark_summary(marked, missing, mark_))
    for line in said:
        click.echo(line, err=True)
    tally = f"{len(marked)} of {len(marked) + len(missing)} marked"
    if missing:
        tally += f", {len(missing)} refused (named above)"
    where = f"rows written to {marks_file}" if lines else "no rows written"
    click.echo(f"message mark: {tally}; {where}", err=True)
    if missing:
        ctx.exit(EX_REFUSED)


@cli.command(
    "path",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] [--why]",
    short_help="[BOX] [--why]                         the maildir BOX resolves to",
    epilog=unwrapped(
        """Prints the folder and nothing else - for a human at a shell, or a tool
that needs a folder for neomutt (SessionView's Messages tab). The SAME
resolution `list` and `read` use: a maildir path, `<root>/<name>`, or the
box postfix delivers `<name>@<host>` to - which is where a project box
lives, nested under its store. BOX defaults to your seat.

Exit: 0 ok · 1 usage · 2 no such box (stderr names what was tried)"""
    ),
)
@click.argument("box", required=False, metavar="BOX")
@click.option("--why", is_flag=True, help="say on stderr how it resolved")
def path(box: str | None, why: bool) -> None:
    """message path - the maildir a box name resolves to; nothing else"""
    # changes/message-path: a viewer needs a FOLDER, and a project's
    # mail_root is a Message-ID. Public, so a human can check it too.
    _print_path(box, why, "message path")


def _print_path(box: str | None, why: bool, prog: str) -> None:
    """What `message path` has always printed, for both its names: `message
    box path` and the alias it keeps (specs/message, "The old verb still
    answers"). Only the prefix on stderr differs - each names itself."""
    if box is None:
        box = _default_box()
    try:
        found, how = _maildir.resolve_box(box)
    except _maildir.NoSuchBox as exc:
        click.echo(f"{prog}: {exc}", err=True)
        raise SystemExit(EX_REFUSED)
    if why:
        click.echo(f"{box}: {how}", err=True)
    click.echo(str(found))


# ── verify ────────────────────────────────────────────────────────────────

#: `message verify` keeps the VERIFIER's exit contract, not the group's:
#: plain 0 good · 1 bad · 2 unsigned · 3 usage or unreadable; --trust 0 FULL
#: · 1 FAIL · 4 UNBLESSED · 5 ERROR. Its callers already parse those codes
#: (every script that ran `session mail verify`), so the port keeps them
#: byte for byte.
#: Here 2 means UNSIGNED, so a usage error cannot exit click's 2: it exits
#: the verifier's own usage bucket, 3. A message that is not there is an
#: unreadable mail file by another name, so it is 3 too.
EX_VERIFY_USAGE = 3
#: --trust's "the test could not be run - fix the environment, not the mail".
EX_VERIFY_ERROR = 5


class VerifyCommand(PageCommand):
    """A page whose usage errors exit 3, the verifier's usage code.

    click raises UsageError both while parsing (a bad flag) and from inside
    the callback (no seat to default the box to), and exits 2 for both -
    which under this verb reads as "the mail is unsigned". Both doors are
    caught here and re-labelled; the message itself is click's, unchanged.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as exc:
            exc.exit_code = EX_VERIFY_USAGE
            raise

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except click.UsageError as exc:
            exc.exit_code = EX_VERIFY_USAGE
            raise


def _verify_target(args: tuple[str, ...]) -> Path:
    """The mail file `[BOX] N|ID|FILE` names.

    A FILE is a token with a slash in it (`./name` for one here): a mail
    file never needs a box, and a bare `12` stays a row number even when a
    file called `12` sits in the working directory. Anything else is an id,
    resolved exactly as `read` resolves it - same default box, same index -
    so the number `list` printed verifies the message `read` opens.
    """
    if len(args) > 2:
        raise click.UsageError(f"one message at a time - got {len(args)} arguments "
                               "(a verdict is per mail, and --trust prints exactly one)")
    target = args[-1]
    box = args[0] if len(args) == 2 else None
    if os.sep in target:
        if box is not None:
            raise click.UsageError(f"{target!r} is a file; a file takes no BOX")
        path = Path(target).expanduser()
        if not path.is_file():
            click.echo(f"message verify: no mail file at {target}", err=True)
            raise SystemExit(EX_VERIFY_USAGE)
        return path
    box = box or _default_box()
    try:
        entries = _maildir.enumerate_box(box)
    except _maildir.NoSuchBox as exc:
        click.echo(f"message verify: {exc}", err=True)
        raise SystemExit(EX_VERIFY_USAGE)
    entry = _maildir.find(entries, target)
    if entry is None:
        click.echo(f"message verify: no message {target!r} in {box} "
                   f"(index spans 1..{len(entries)})", err=True)
        raise SystemExit(EX_VERIFY_USAGE)
    return entry.path


#: The mail tool's key naming the registry `--trust` reads.
#: One definition, in `_mhs`, since `memory run` asks --trust too.
REGISTRY_KEY = _mhs.REGISTRY_KEY


def _configured_registry() -> str | None:
    """`registry` from the mail tool's config: `_mhs.configured_registry`."""
    return _mhs.configured_registry()


#: The TRUST: line for a missing verifier, written by this verb itself.
VERIFIER_ABSENT_LINE = "TRUST: ERROR reason=verifier-absent"


def _verify_absent_json(trust: bool, rc: int) -> str:
    """`verify --json` with no verifier to run.

    The verifier writes every other object, and runs under `python3 -I`, so it
    cannot share this code. This one is a second writer. Its key set is pinned
    to the verifier's error object by tests/test_verify_json_cli.py, and it is
    serialised the same way: sorted keys, ASCII, one line."""
    tr = None
    if trust:
        tr = {"verdict": "ERROR", "leg": None, "reason": "verifier-absent",
              "line": VERIFIER_ABSENT_LINE, "agent": None}
    obj = {"schema": 1, "rc": rc, "result": "unverified", "reason": "verifier-absent",
           "signature": None, "trust": tr}
    return json.dumps(obj, sort_keys=True, ensure_ascii=True)


def _verify_env(trust: bool) -> dict[str, str] | None:
    """The verifier's environment: `_mhs.trust_env` when --trust needs a
    registry; a plain verify reads none, so it inherits this one."""
    return _mhs.trust_env() if trust else None


@cli.command(
    "verify",
    cls=VerifyCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] N|ID|FILE [--trust [--require-blessing]] [--quiet] [--json]",
    short_help="[BOX] N|ID|FILE [--trust]             who signed it; trusted or not",
    epilog=unwrapped(
        f"""N, ID as in `read`; a FILE has a slash. Plain: the signer's
key and session; checks signature and From.

Signed (`covers:`): v1, From/To local parts, Subject, Date, Message-ID,
text body; v2, all h= headers (full addresses) and parts (p=).
Another v= is exit 3 (--trust: FAIL), never "bad". A header that cannot
be read one way (a non-ASCII byte, a tag twice, a second signature field,
a bare CR) is bad: exit 1.

--trust reads $SESSION_REGISTRY, else "registry" in
{_settings.SHOWN}; neither: ERROR (5).

--json: TRUST: is trust.line (docs/verify-json-verdict.md).

Exit, plain: 0 good · 1 bad signature · 2 unsigned · 3 usage or no such
message. --trust: 0 FULL · 1 FAIL · 4 UNBLESSED · 5 ERROR (the test could
not run) · 3 usage"""
    ),
)
@click.argument("args", nargs=-1, required=True, metavar="[BOX] N|ID|FILE")
@click.option("--trust", is_flag=True,
              help="registry cert and blessing too; one TRUST: line last")
@click.option("--require-blessing", is_flag=True,
              help="with --trust: UNBLESSED (4) becomes FAIL (1)")
@click.option("--quiet", is_flag=True, help="no per-leg lines; the verdict is kept")
@click.option("--json", "as_json", is_flag=True,
              help="the verdict, one JSON object on stdout (schema 1)")
def verify(args: tuple[str, ...], trust: bool, require_blessing: bool, quiet: bool,
           as_json: bool) -> None:
    """message verify - who signed a message, and whether to trust them"""
    # The retired `session mail verify`, now in `message`: public, takes
    # N|ID|FILE, keeps the old exit codes and --trust. The judgement is
    # the bundled verifier's (`mhs/session-mail-verify`, its three
    # legs), run through the same door the memory verbs use; this verb only
    # finds the file. A second verifier here would be a second gauge to
    # disagree with the first.
    path = _verify_target(args)
    flags = [f for f, on in (("--trust", trust), ("--require-blessing", require_blessing),
                             ("--quiet", quiet), ("--json", as_json)) if on]
    # `run` inside the try: a $SESSIONMAIL_MHS_ORACLE naming a missing file
    # fails at the exec, not at the lookup, and an escaped traceback exits 1,
    # which a caller reads as "bad signature".
    try:
        argv = _mhs.oracle("verify")
        code = _mhs.run([*argv, str(path), *flags], env=_verify_env(trust))
    except FileNotFoundError as exc:
        click.echo(f"message verify: {exc}", err=True)
        rc = EX_VERIFY_ERROR if trust else EX_VERIFY_USAGE
        if as_json:
            # The one object this verb writes itself. The file was
            # found, never read; a broken install still says so in the
            # object, and the TRUST: text rides as trust.line.
            click.echo(_verify_absent_json(trust, rc))
        elif trust:
            # The --trust invariant: every run that parsed its arguments
            # ends on exactly one TRUST: line.
            click.echo(VERIFIER_ABSENT_LINE)
        raise SystemExit(rc)
    if code:
        raise SystemExit(code)


# ── message box: the boxes on this host (message-subscriptions 3.1, D6) ───
#
# Declared AFTER `path`, so every existing verb keeps its place on the page
# and in the MCP tool list (whose order is the prompt cache's, `_mcp.tools`).

@cli.group(
    "box",
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="list|path|archive|unarchive [args]",
    short_help="list|path|archive|unarchive           boxes; a maildir; dead boxes",
    epilog=unwrapped(
        """A box is a maildir postfix delivers to: a seat, a project (nested
under its store), a store, or a session id. `list` names who else
receives each box's mail (its subscribers). `message path` is the old
name for `message box path` and still answers. `archive` moves dead
session boxes out of notmuch's scan; `unarchive` brings one back.

Exit: 0 ok · 2 usage, or no such box"""
    ),
)
def box() -> None:
    """message box - list the boxes on this host, or print one box's maildir"""


def _live_sessions() -> set[str]:
    """Sessions counted live, for the subscriber column. A seam, so a test
    names its own and never reads this host's harness."""
    return _boxes.live_sessions()


#: A `box list` page stops at 400 tokens as well as 40 rows (specs/message,
#: "A listing defaults to the newest 40"), counted CONSERVATIVELY - a
#: character is a third of a token, the scaffold's own fallback - so the
#: real token counter can only find it shorter.
BOX_LIST_CHARS = 400 * 3


@box.command(
    "list",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[PATTERN] [--all] [-n N]",
    short_help="[PATTERN] [--all] [-n N]   the boxes here, newest first",
    epilog=unwrapped(
        """One row per box: its kind, its name as `path` takes it, and `<- who`
when anyone else receives its mail - a seat by name, a live session by
its short id, `+tag:` when only that tag reaches them. PATTERN is a
substring, or a glob if it holds * ? or [; case is ignored.

Session-id boxes (most of the boxes here) are hidden unless --all; the
last line says how many. Without -n you get the newest 40 rows, fewer if
they would pass 400 tokens; the last line says how many it hid.

Exit: 0 ok (even when empty) · 2 usage"""
    ),
)
@click.argument("pattern", required=False, metavar="PATTERN")
@click.option("--all", "all_", is_flag=True, help="session-id boxes too")
@click.option("-n", "count", metavar="N",
              help="newest N (default: 40, within 400 tokens; 0 or all for every row)")
def box_list(pattern: str | None, all_: bool, count: str | None) -> None:
    """message box list - the boxes on this host, each with its subscribers"""
    # Reads directory names and subscription-file names; opens no message
    # and writes nothing (`_boxes`).
    rows = _rows(count)
    boxes = [b for b in _boxes.listing(_maildir.root(), live=_live_sessions())
             if _boxes.matches(b, pattern)]
    sessions = sum(1 for b in boxes if b.kind == "session")
    if not all_:
        boxes = [b for b in boxes if b.kind != "session"]
    boxes.sort(key=lambda b: (-b.mtime, b.name))

    lines: list[str] = []
    why = ""
    for b in boxes:
        if rows is not None and len(lines) >= rows:
            why = (f"older than -n {count}" if count is not None
                   else f"older than the newest {DEFAULT_ROWS}")
            break
        line = _boxes.row(b)
        # The token cap binds only the DEFAULT page: an explicit -n is the
        # reader asking for exactly that many. 80 characters are held back
        # for the footer, so the whole page stays inside the fold.
        if count is None and sum(len(l) + 1 for l in lines) + len(line) + 1 > BOX_LIST_CHARS - 80:
            why = "past 400 tokens"
            break
        lines.append(line)
    for line in lines:
        click.echo(line)
    hidden = len(boxes) - len(lines)
    tail = f"-- {len(lines):,} shown"
    if hidden:
        tail += f", {hidden:,} hidden ({why}; -n all shows every row)"
    if sessions and not all_:
        tail += f"; {sessions:,} session boxes hidden (--all shows them)"
    click.echo(tail)


@box.command(
    "path",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] [--why]",
    short_help="[BOX] [--why]              the maildir a box name resolves to",
    epilog=unwrapped(
        """Prints the folder and nothing else. The SAME resolution `list` and
`read` use: a maildir path, `<root>/<name>`, or the box postfix delivers
`<name>@<host>` to - where a project box lives, nested under its store.
BOX defaults to your seat. `message path` is this verb's old name and
answers exactly the same.

Exit: 0 ok · 2 usage, or no such box (stderr names what was tried)"""
    ),
)
@click.argument("box_", required=False, metavar="BOX")
@click.option("--why", is_flag=True, help="say on stderr how it resolved")
def box_path(box_: str | None, why: bool) -> None:
    """message box path - the maildir a box name resolves to; nothing else"""
    _print_path(box_, why, "message box path")


# ── message box archive|unarchive: dead session boxes ─────────────────────
#
# Declared AFTER `path`, so `list` and `path` keep their places on the page.
# Neither is an MCP tool (`_mcp.NOT_A_TOOL`): they move boxes and change the
# postfix maps, a host owner's act.

def _say(err: bool = False):
    """click.echo that never raises on a closed terminal (BN3): after a
    signal, or a hung-up tty, output is best effort and the ledger is the
    record, so EIO/EPIPE must never abort a verify."""
    def emit(line: str) -> None:
        try:
            click.echo(line, err=err)
        except OSError:
            pass
    return emit


def _archive_ctx(days: float, maps: str | None, archive: str | None) -> "_archive.Ctx":
    root = _maildir.root()
    return _archive.Ctx(root=root, archive=Path(archive) if archive else root.parent / "archive",
                        days=days, maps=Path(maps) if maps else None,
                        out=_say(), err=_say(err=True))


@box.command(
    "archive",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX...] [--days N] [--apply] [--all] [--maps DIR] [--archive DIR]",
    short_help="[BOX...] [--apply]         dead session boxes, archived",
    epilog=unwrapped(
        """Without --apply, a dry run that touches nothing: a CANDIDATE line
per dead box with its three reasons, then a total (--all adds the
KEEPs). DEAD is all of: (a) no live session owns it, (b) no alias row
names it, now or after the next regenerate, (c) no new file for
--days N. Only session-id boxes ever move.

--apply moves each to archive/<box>, regenerates once and verifies in
the WRITTEN map that its row is gone; if not, or if mail re-created it,
the box goes BACK (merged; a clash keeps both). An archived address
BOUNCES. Refuses unless notmuch's [new] ignore hides the archive;
never edits that config, never runs notmuch or git. A stopped run is
finished first by the next --apply.

Exit: 0 ok · 2 usage (--apply needs --days 1+) · 3 refused · 4 not
clean (a box went back, or the run was interrupted)"""
    ),
)
@click.argument("boxes", nargs=-1, metavar="BOX...")
@click.option("--days", type=float, default=_archive.DEFAULT_DAYS, show_default=True,
              help="quiet for N days (c); --apply needs 1 or more")
@click.option("--apply", "do", is_flag=True, help="move them (the default is a dry run)")
@click.option("--all", "all_", is_flag=True, help="list the KEEP rows too, with why")
@click.option("--maps", metavar="DIR", help="the postfix map dir (default: ask postconf)")
@click.option("--archive", "archive_", metavar="DIR", help="where archived boxes go")
def box_archive(boxes: tuple[str, ...], days: float, do: bool, all_: bool, maps: str | None,
                archive_: str | None) -> None:
    """message box archive - list dead session boxes; --apply moves them out of the scan"""
    if days < 0 or (do and days < 1):
        click.echo("message box archive: --apply needs --days 1 or more: (c) is the backstop "
                   "for what (a) cannot see", err=True)
        raise SystemExit(EX_REFUSED)
    ctx = _archive_ctx(days, maps, archive_)
    only = {b.lower() for b in boxes} or None
    raise SystemExit(_archive.apply(ctx, only) if do else _archive.listing(ctx, only, all_))


@box.command(
    "unarchive",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="BOX [--merge] [--apply] [--maps DIR] [--archive DIR]",
    short_help="BOX [--merge] [--apply]    bring an archived box back",
    epilog=unwrapped(
        """Without --apply, says what it would do. --apply renames the box back
under agents/ (an empty birth triad there is removed first), regenerates
the maps and verifies its row is back. If agents/<BOX> holds mail, it
refuses unless --merge: each archived file is linked in and unlinked
from the archive, never overwriting; a clash keeps both. tmp/ files are
left in the archive and named. The restored mail is new to notmuch, so
its next `notmuch new` tags it with new.tags; sessionmail's own read
marks are unchanged.

Exit: 0 ok · 2 usage, or no such archived box · 3 refused · 4 back, but
its row did not come back"""
    ),
)
@click.argument("box_", metavar="BOX")
@click.option("--merge", is_flag=True, help="join it into a box that holds mail")
@click.option("--apply", "do", is_flag=True, help="do it (the default says what it would do)")
@click.option("--maps", metavar="DIR", help="the postfix map dir (default: ask postconf)")
@click.option("--archive", "archive_", metavar="DIR", help="where archived boxes are")
def box_unarchive(box_: str, merge: bool, do: bool, maps: str | None, archive_: str | None) -> None:
    """message box unarchive - move an archived box back under agents/"""
    ctx = _archive_ctx(_archive.DEFAULT_DAYS, maps, archive_)
    raise SystemExit(_archive.unarchive(ctx, box_, merge, do))


# ── message subscription: add|remove (message-subscriptions 3.2, D3.1, D5) ─
#
# Declared AFTER `box`, so every existing verb keeps its place on the page
# and in the MCP tool list. `list` (tasks 3.3) comes last for the same reason.

#: A subscription file was written or removed, but the alias map was NOT
#: rebuilt: the file stands (it is the truth), and the next rebuild - any
#: `pager listen` birth, any add or remove - routes it.
EX_NOT_REBUILT = 3


@cli.group(
    "subscription",
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="add|remove|list [BOX] [--seat] [--for WHO]",
    short_help="add|remove|list [BOX]                 who else gets a box's mail",
    epilog=unwrapped(
        """A subscription copies a box's mail to your SESSION (the default;
it dies with the session) or, with --seat, to your seat. --seat and
--for are for a privileged seat (operator.privileged in
~/.sessionkit/tools/settings/session.json): anyone else REQUESTS a seat
subscription by mail, from one. A seat's own address is that seat's to grant. add and
remove rebuild the alias map and print its row; a repeat changes nothing.

Exit: 0 ok · 2 usage, or refused (no such box, not yours) · 3 written, map not rebuilt"""
    ),
)
def subscription() -> None:
    """message subscription - who else receives a box's mail: add, remove, list"""


def _subscription_verb(verb: str, box_: str, seat: bool, for_: str | None,
                       why: str | None) -> None:
    prog = f"message subscription {verb}"
    who = _subscribe.caller()
    try:
        if verb == "add":
            # THE add path (`_subscribe.add`): gates, no-op and write; the
            # rebuild is ours, below.
            done = _subscribe.add(box_, who, seat=seat, for_=for_, why=why)
        else:
            tgt = _subscribe.target(who, seat=seat, for_=for_)
            stored = _subscription_stored(box_)
            if tgt.map:
                # session-settings 3.2: a seat's subscriptions are its map.
                argv = " ".join(["message subscription remove", box_]
                                + (["--seat"] if seat else []) + ([f"--for {for_}"] if for_ else []))
                if not _subscribe.remove_from_map(tgt, box_, who, argv=argv):
                    click.echo(f"{tgt.label} is not subscribed to {box_} ({tgt.path_for}); "
                               "nothing changed, the map not rebuilt")
                    return
    except _subscribe.Denied as exc:
        click.echo(f"{prog}: {exc}", err=True)
        raise SystemExit(EX_REFUSED)
    except OSError as exc:
        # The home would not take the write (read-only, full, a file holding
        # the folder's name): nothing was written, so a refusal, one line
        # with the OS's own path and reason, as the pager's birth names it.
        click.echo(f"{prog}: {box_} NOT written - {exc}; nothing changed, "
                   "the map not rebuilt", err=True)
        raise SystemExit(EX_REFUSED)
    if verb == "add":
        if not done.written:
            click.echo(f"{done.target.label} is already subscribed to {box_} ({done.path}); "
                       "nothing changed, the map not rebuilt")
            return
        click.echo(f"subscribed {done.target.label} to {box_}: {done.path}")
    elif tgt.map:
        click.echo(f"unsubscribed {tgt.label} from {box_}: {tgt.path_for}")
    else:
        path = tgt.folder / stored
        if not (path.exists() or path.is_symlink()):
            click.echo(f"{tgt.label} is not subscribed to {box_} ({path}); "
                       "nothing changed, the map not rebuilt")
            return
        try:
            _subscribe.removable(path)
        except _subscribe.Denied as exc:
            click.echo(f"{prog}: {exc}", err=True)
            raise SystemExit(EX_REFUSED)
        try:
            path.unlink()
        except OSError as exc:
            click.echo(f"{prog}: {path} NOT removed - {exc}; nothing changed, "
                       "the map not rebuilt", err=True)
            raise SystemExit(EX_REFUSED)
        click.echo(f"unsubscribed {tgt.label} from {box_}: removed {path}")
    ok, line = _subscribe.rebuild()
    if not ok:
        click.echo(f"{prog}: the file stands, but the map was NOT rebuilt: {line}. "
                   "The next rebuild (any pager listen, add or remove) routes it.", err=True)
        raise SystemExit(EX_NOT_REBUILT)
    click.echo(f"map: {line} (--valias-only)")
    click.echo(_subscribe.row(box_))


def _subscription_stored(box_: str) -> str:
    """REMOVE's box gate: only the codec. Removing a copy never needs the box
    to resolve still, nor to be the caller's to grant."""
    try:
        return _subscription.file_name(box_)
    except _subscription.BadBox as exc:
        raise _subscribe.Denied(f"not a box: {exc}") from None


_SUB_EXIT = "Exit: 0 ok (or nothing to do) · 2 usage, or refused · 3 written, map not rebuilt"


@subscription.command(
    "add",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="BOX [--seat] [--for WHO] [--why TEXT]",
    short_help="BOX [--seat] [--for WHO]   copy BOX's mail to you",
    epilog=unwrapped(
        f"""BOX is a name `message path` resolves: a project (sessionmail), a
store/project, or a plus form (widget+tm-123 gets ONLY that tag,
and claims it from the bare box's subscribers). Refused, exit 2: a box
that does not resolve, a +memory address, another seat's own address,
and --seat or --for unless you are a privileged seat. Ask one by mail.
--seat on heard or heard+DEVICE is refused for everyone: a device
subscription is per session (N8).

{_SUB_EXIT}"""
    ),
)
@click.argument("box_", metavar="BOX")
@click.option("--seat", is_flag=True, help="your SEAT, not your session (a privileged seat)")
@click.option("--for", "for_", metavar="WHO", help="WHO's session, or seat with --seat (a privileged seat)")
@click.option("--why", metavar="TEXT", help="a note of why, kept in the file")
def subscription_add(box_: str, seat: bool, for_: str | None, why: str | None) -> None:
    """message subscription add - copy BOX's mail to your session; rebuilds the map"""
    _subscription_verb("add", box_, seat, for_, why)


@subscription.command(
    "remove",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="BOX [--seat] [--for WHO]",
    short_help="BOX [--seat] [--for WHO]   stop copying BOX's mail to you",
    epilog=unwrapped(
        f"""Removes the file `add` wrote, and rebuilds. Never an old saved
query sharing the folder, never a line of valias.static. --seat and
--for are a privileged seat's, as for add.

{_SUB_EXIT}"""
    ),
)
@click.argument("box_", metavar="BOX")
@click.option("--seat", is_flag=True, help="your SEAT, not your session (a privileged seat)")
@click.option("--for", "for_", metavar="WHO", help="WHO's session, or seat with --seat (a privileged seat)")
def subscription_remove(box_: str, seat: bool, for_: str | None) -> None:
    """message subscription remove - stop copying BOX's mail to you; rebuilds the map"""
    _subscription_verb("remove", box_, seat, for_, None)


@subscription.command(
    "list",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] [--for WHO] [--depth N]",
    short_help="[BOX] [--for WHO]          what reaches you; who hears BOX",
    epilog=unwrapped(
        f"""No BOX: a tree of what reaches you, rooted at your session. Your
seat is the first branch; under each address, the addresses that feed
it. A loop is marked {_reach.LOOP} and not walked again.
Depth stops at {_reach.DEFAULT_DEPTH} (--depth N for more). Only what the map routes
is shown: a saved query or a box that does not resolve is counted.

BOX: who hears it - each seat and live session subscribed, each tag
claimed from it, and any live `pager listen BOX`, marked LISTENER.
--for WHO lists a session's tree (or a seat's); open to everyone.

Exit: 0 ok (even when empty) · 2 usage, no such box, or no session"""
    ),
)
@click.argument("box_", required=False, metavar="BOX")
@click.option("--for", "for_", metavar="WHO", help="WHO's tree: a session id, or a seat")
@click.option("--depth", type=click.IntRange(min=1), default=_reach.DEFAULT_DEPTH,
              show_default=True, metavar="N", help="how deep the tree goes")
def subscription_list(box_: str | None, for_: str | None, depth: int) -> None:
    """message subscription list - what reaches you, as a tree; or who hears BOX"""
    # Reads directory names, subscription files and stamps; opens no message
    # and writes nothing (`_reach`). Listing is open to all (D5).
    prog = "message subscription list"
    root = _maildir.root()
    live = _live_sessions()
    g = _reach.graph(root, live=live)
    if box_ is not None:
        found = _reach.find_box(g, box_)
        if found is None:
            click.echo(f"{prog}: no such box {box_!r} under {root} "
                       "(`message box list` names them)", err=True)
            raise SystemExit(EX_REFUSED)
        lines, foot = _reach.hearers(g, found[0], found[1], live=live, root=root)
        for line in lines:
            click.echo(line)
        click.echo(foot)
        return
    session, seat = _list_whose(for_)
    if not session and not seat:
        click.echo(f"{prog}: no session id and no seat in the environment (looked for "
                   + ", ".join(_seat.SESSION_VARS) + "); name one with --for WHO", err=True)
        raise SystemExit(EX_REFUSED)
    if session and session not in live and for_ is None:
        live = set(live) | {session}          # the caller is running: count it live
        g = _reach.graph(root, live=live)
    t = _reach.tree(g, session=session, seat=seat, limit=depth)
    for line in _reach.render(t.root, limit=depth):
        click.echo(line)
    boxed = not session or (root / session / "new").is_dir()
    click.echo(_reach.footer(t, limit=depth, boxed=boxed))


def _list_whose(for_: str | None) -> tuple[str, str]:
    """(session, seat) the tree is rooted at: the caller's own, or WHO's.
    WHO is a session when this host knows it as one (a session folder, or a
    session-id box), else a seat, whose tree is its own subscriptions."""
    if for_ is None:
        who = _subscribe.caller()
        seat = who.seat.address if who.seat else ""
        # A seatless session's identity falls back to its own id (IDENTITY_VARS
        # ends with the session vars): that is the root, not a seat branch.
        return who.session, ("" if seat.lower() == who.session.lower() else seat)
    wanted = for_.strip()
    home = _seat.home()
    if wanted and "/" not in wanted and ((home / _subscription.SESSIONS_DIR / wanted).is_dir()
                                         or _boxes.is_session_id(wanted)):
        return wanted, _session_seat(wanted)
    return "", _seat.fold_address(wanted)


def _session_seat(sid: str) -> str:
    """The seat a session's row copies from: its declared address (`pager
    listen` writes it), else the harness's agent word. "" when neither."""
    declared = _seat.read_address(sid)          # its layer's `address` (3.3b)
    if declared:
        return _seat.fold_address(declared)
    try:
        for s in _reach.sessiontools.sessions():
            if s.id == sid and s.agent:
                return _seat.fold_address(s.agent)
    except Exception:
        pass
    return ""


# ── message seat: a seat with no Claude session ──────────────────────────
#
# sessionmail keeps exactly three commands, so seat-init and seat-agent
# ship as `message seat init` and `message seat agent`. The rest is the
# design in openspec/changes/message-seat:
#
# - HIDDEN: on no group page and no MCP tool, as
#   `pager settings` is. `seat agent` execs, and the MCP server runs a tool
#   in-process, so a tool would replace the server; `seat init` mints a
#   signing identity. Neither belongs in an agent's tool list. `message seat
#   --help` answers when typed, and the README says where to find it.
# - Click parses NEITHER verb's arguments: `init` hands them to seat_init's
#   argparse and `agent` to the packaged bash, as typed, so the args, the
#   exits and the refusals are the seat scripts' own. Only -h/--help (and
#   agent's --template) are click's.
# - Never run under CliRunner but for -h, --help and --template: `agent`
#   replaces the process, and `init` sets the process umask.

#: The packaged seat files, found beside this module's real
#: path as `_mhs.MHS_DIR` finds the oracle: a wheel installs unzipped.
SEAT_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "seat")
SEAT_AGENT = os.path.join(SEAT_DIR, "seat-agent")
SEAT_TEMPLATE = os.path.join(SEAT_DIR, "seat-agent.plist.template")

#: Every argument goes through untouched: an option click does not know is
#: an argument, and after the first argument everything is (so `init
#: --registry X --help` is argparse's help, not this page).
SEAT_PASS = dict(CONTEXT_SETTINGS, ignore_unknown_options=True, allow_interspersed_args=False)

#: CPython ignores both at startup, and an exec keeps an ignored signal
#: ignored: bash would then see EPIPE where a pipe should kill (`yes | head
#: -1` exits 1, not 141) and EFBIG past `ulimit -f`. These two are the set
#: `subprocess`'s restore_signals puts back (measured, design.md §1).
SEAT_RESTORE = ("SIGPIPE", "SIGXFSZ")


@cli.group(
    "seat",
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="init|agent [args]",
    hidden=True,
    short_help="init|agent          a seat with no Claude session",
    epilog=unwrapped(
        """A seat with no Claude session (a bot, a bridge) signs its mail
with a key of its own, held by its own ssh-agent. `init` mints the key
and its birth cert into a registry you name; `agent` runs that agent in
the foreground, for launchd. Neither runs launchctl, and neither blesses
a seat: a blessing is the registry owner's own act.

Exit: 0 ok · 2 usage (each verb's own exits are on its page)"""
    ),
)
def seat() -> None:
    """message seat - a seat with no Claude session: mint its key, run its agent"""


@seat.command(
    "init",
    cls=PageCommand,
    context_settings=SEAT_PASS,
    usage="--registry DIR ADDRESS [--machine NAME]",
    short_help="--registry DIR ADDRESS    mint a seat's key and birth cert",
    epilog=unwrapped(
        """  --registry DIR   the registry dir (allowed_signers, certs/) in its
                   git repo; there is NO default
  --machine NAME   the cert's machine: line (default: hostname -s)
  ADDRESS          the seat, e.g. bot.alice (a dot, never a +)

Generates an ed25519 key in $SESSIONMAIL_HOME/seat-keys/ADDRESS/ (dir
0700, key 0600), self-signs a birth cert, and registers both in DIR in
one commit signed by the new key. It prints the sid, the fingerprint and
the bless command, and never blesses. With no --registry it refuses, so
a live registry is never touched by accident.

Exit: 0 minted · 1 a tool failed, all rolled back · 2 usage or refused, nothing written"""
    ),
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED, metavar="")
def seat_init(args: tuple[str, ...]) -> None:
    """message seat init - mint a seat's key and birth cert into a registry you name"""
    from . import seat_init as minter
    click.get_current_context().exit(minter.main(list(args)))


@seat.command(
    "agent",
    cls=PageCommand,
    context_settings=dict(CONTEXT_SETTINGS, ignore_unknown_options=True),
    usage="ADDRESS [SOCKET] | --template",
    short_help="ADDRESS [SOCKET]          run the seat's ssh-agent, for launchd",
    epilog=unwrapped(
        """Runs the seat's own ssh-agent in the foreground, for launchd's
KeepAlive. This process BECOMES the packaged seat-agent script (an exec,
so launchd's TERM reaches it): it loads $SESSIONMAIL_HOME/seat-keys/
ADDRESS/id_ed25519 and checks it against seat.pub. SOCKET defaults to
agent.sock beside the key, 103 bytes at most.

--template prints the LaunchAgent template, byte for byte, for you to
render: ProgramArguments is `@MESSAGE@ seat agent @ADDRESS@ @SOCKET@`,
with @MESSAGE@ what `command -v message` prints. Nothing here renders
or loads it, or runs launchctl.

Exit: 0 stopped by a signal · 1 the agent failed or died · 2 usage or refused, nothing started"""
    ),
)
@click.option("--template", is_flag=True,
              help="print the LaunchAgent template, verbatim; takes no ADDRESS")
@click.argument("args", nargs=-1, type=click.UNPROCESSED, metavar="")
def seat_agent(template: bool, args: tuple[str, ...]) -> None:
    """message seat agent - the seat's own ssh-agent, in the foreground, for launchd"""
    ctx = click.get_current_context()
    if template:
        if args:
            click.echo("message seat agent: REFUSED - --template prints the template and takes "
                       f"no ADDRESS or SOCKET (got {' '.join(args)}); render it yourself. "
                       "Nothing was printed.", err=True)
            ctx.exit(EX_REFUSED)
        with open(SEAT_TEMPLATE, "rb") as f:
            _mhs._emit(sys.stdout, f.read())        # the bytes, as the oracle's are
        return
    _exec_seat_agent(list(args))


def _exec_seat_agent(args: list[str]) -> None:
    """Become the packaged seat-agent, with ARGS. An exec, never a child: the
    pid launchd started must be bash's own, so its TERM and KeepAlive act on
    the wrapper's trap, not on a Python parent (design.md §1)."""
    agent = SEAT_AGENT
    if not (os.path.isfile(agent) and os.access(agent, os.X_OK)):
        click.echo(f"message seat agent: REFUSED - {agent} is missing or not executable "
                   "(a broken sessionmail install). Nothing was started.", err=True)
        click.get_current_context().exit(EX_REFUSED)
    for name in SEAT_RESTORE:
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(agent, [agent, *args])


# ── receipt: what ANOTHER session recorded about one message ───────────────
#
# openspec/changes/message-receipt (PROPOSED; design.md D1-D12). Hidden, as
# the `seat` noun is (D3): on no `message --help` page and no MCP tool, so
# `mcp` keeps line 20 of `message --help`. Read-only: it reads ONE root,
# `<session dir>/mail/`, and only LOOKS at the old root (`lstat`, and
# for a symlink the mover's own `readlink` and `resolve()`).
# A seat's `marks` file and a retired sessions root are never read (H, G).
#
# Its exits are its own page's, not the group's (D5): 0 a row · 1 none ·
# 2 usage, could not look, or not moved · 3 no such session. EVERY failure
# that is not one of those named answers is 2, never 1: an uncaught exception, and click's ClickException, both exit 1 - which
# here would read as "no row".

#: Its exits (D5). 2 is click's own usage code, so "could not look" and "not
#: moved" share it: to a program, 2 means unmeasured.
EX_RECEIPT_NONE = 1
EX_RECEIPT_UNMEASURED = 2
EX_RECEIPT_NO_SESSION = 3

#: A FULL session id, and nothing else (D2): `_boxes._SESSION` with its tail
#: required. Matched with `fullmatch`, never `match`: a `$` also matches
#: before a trailing newline, so `match` would pass `<sid>\n`.
RECEIPT_SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                             re.IGNORECASE)

#: The bound on the mover's lock (D6). `message` never blocks;
#: `move_session_state` holds `LOCK_EX` on the session dir for one rename or
#: one fold, and `tm kick` polls every 0.5 s. A lock still held after this
#: is exit 2, could not look, naming the session dir.
RECEIPT_LOCK_SECONDS = 1.0
RECEIPT_LOCK_STEP = 0.05

#: The JSON contract's version (specs, "`--json` is one object, schema 1").
#: Keys are only ever ADDED within it.
RECEIPT_SCHEMA = 1


class _Unmeasured(Exception):
    """Exit 2 after the arguments were good: could not look, or not moved.
    Its text is the ONE stderr line (D8)."""


def _receipt_lock(session_dir: Path) -> int | None:
    """An fd holding `flock(LOCK_SH)` on SESSION_DIR, or None when there is
    no session dir (nothing to lock, and exit 3 is decided later).

    The lock the mover takes EXCLUSIVELY (`_seat.move_session_state`), taken
    SHARED and never blocking: tried for `RECEIPT_LOCK_SECONDS`, then exit 2.
    A shared flock changes nothing on disk."""
    try:
        fd = os.open(session_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unmeasured(f"could not look: {session_dir}: {exc.strerror}") from exc
    deadline = time.monotonic() + RECEIPT_LOCK_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise _Unmeasured(f"could not look: {session_dir} is locked (its state is "
                                  f"being moved); still held after {RECEIPT_LOCK_SECONDS:g} s")
            time.sleep(RECEIPT_LOCK_STEP)
        except OSError as exc:
            os.close(fd)
            raise _Unmeasured(f"could not look: {session_dir}: {exc.strerror}") from exc


def _receipt_old_root(old: Path, new: Path) -> None:
    """Raise `_Unmeasured` unless OLD, the old root, is absent or the mover's
    own symlink to NEW (D6). Looked at, never read: one
    `lstat`, and for a symlink only what the mover judges one by,
    `os.readlink` to name the target and `resolve()` to compare it
    (`_seat._move_locked`). Never a listing, an open, or a read through it."""
    try:
        st = os.lstat(old)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _Unmeasured(f"could not look: {old}: {exc.strerror}") from exc
    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(old)
        if old.resolve() != new.resolve():
            raise _Unmeasured(f"{old} is a symlink to {target}, not here: its state is not "
                              f"where this verb reads, and nothing was read through it")
        return
    if stat.S_ISDIR(st.st_mode):
        raise _Unmeasured(f"not moved; at {old}")
    raise _Unmeasured(f"not moved; at {old}, which is not a directory")


def _receipt_read(path: Path, reader):
    """READER (`_seat.read_marks_at` or `read_verdicts_at`) on PATH. Missing
    is no rows (taskmaster's pin 1: a seat still booting is exit 1); a file
    or directory that EXISTS and cannot be read is exit 2, never a partial 0."""
    try:
        return reader(path)
    except OSError as exc:
        raise _Unmeasured(f"could not look: {exc.filename or path}: "
                          f"{exc.strerror or exc}") from exc


def _receipt_flag(value) -> bool:
    return value is True


def _receipt_verdict(record: dict) -> dict:
    """One verdict record as schema 1 says it: the documented keys only, each
    with its type, and `carry`/`promoted`/`bad` (written only when set) at
    their defaults when absent (D9). A key the pager adds later reaches the
    JSON only when this verb adds it."""
    line = record.get("line")
    rule = record.get("rule")
    bad = record.get("bad")
    return {
        "stamp": str(record.get("stamp") or ""),
        "action": str(record.get("action") or ""),
        "carry": _receipt_flag(record.get("carry")),
        "promoted": _receipt_flag(record.get("promoted")),
        "at_once": _receipt_flag(record.get("at_once")),
        "rule": None if rule is None else str(rule),
        "layer": str(record.get("layer") or ""),
        "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
        "by": str(record.get("by") or ""),
        "bad": [str(b) for b in bad] if isinstance(bad, list) else [],
    }


def _receipt_when(stamp: str) -> float | None:
    """STAMP as a time, the way `_seat.given_since` reads one: ISO with its
    own offset, a stamp with none taken as local; None when it cannot be read
    (D8). Compared as times, never strings: string order breaks where the
    offset falls, at the end of DST."""
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.astimezone()
    return when.timestamp()


def _receipt_newest(stamps: list[str], keep: int) -> set[int]:
    """The positions of the newest KEEP of STAMPS (recorded order: verdicts,
    then marks). A stamp that cannot be read is older than every one that
    can; equal times keep recorded order, so the later row is the newer
    (D8)."""
    def key(i: int) -> tuple:
        when = _receipt_when(stamps[i])
        return (0, 0.0, i) if when is None else (1, when, i)
    return set(sorted(range(len(stamps)), key=key)[-keep:]) if keep else set()


def _receipt_answer(want: str, sid: str) -> dict:
    """The answer for Message-ID WANT in session SID, as schema 1's object
    (with `answer` set), or `_Unmeasured`. The order is D6's: the session
    dir's lock if it exists, the old root, the session dir (3 if absent),
    then the two files - all under the lock."""
    new = _seat.session_root(sid)          # RuntimeError: no sessiontools -> 2
    old = _seat.old_session_root(sid)
    if new is None or old is None:         # unreachable for a full sid; never a guess
        raise _Unmeasured(f"could not look: no session root for {sid}")
    session_dir = new.parent
    looked_at = {"new": str(new), "old": str(old)}
    out = {"schema": RECEIPT_SCHEMA, "answer": "no-such-session", "message_id": want,
           "session": sid, "looked_at": looked_at, "marks": [], "verdicts": [],
           "passed_over": {"marks": 0, "verdicts": 0}}
    fd = _receipt_lock(session_dir)
    try:
        _receipt_old_root(old, new)        # not moved is decided first (spec)
        if fd is None:
            return out                     # no session dir: 3
        marks, marks_over = _receipt_read(new / "marks", _seat.read_marks_at)
        verdicts, verdicts_over = _receipt_read(new / _seat.VERDICTS_FILE,
                                                _seat.read_verdicts_at)
    finally:
        if fd is not None:
            os.close(fd)                   # closing the fd drops the shared lock
    out["passed_over"] = {"marks": marks_over, "verdicts": verdicts_over}
    # D12, taskmaster's pin 3: a mark counts only when its session column is
    # SID. A foreign line is a record, just not SID's: never printed, never
    # counted as passed over. The verdicts file is SID's own.
    out["marks"] = [{"stamp": m.stamp, "mark": m.mark, "seat": m.seat, "reason": m.reason}
                    for m in marks
                    if m.session.lower() == sid and _why.mid(m.message_id) == want]
    out["verdicts"] = [_receipt_verdict(v) for v in verdicts
                       if isinstance(v.get("message_id"), str)
                       and _why.mid(v["message_id"]) == want]
    out["answer"] = "yes" if out["marks"] or out["verdicts"] else "no"
    return out


def _receipt_text(found: dict, new: str) -> list[str]:
    """The text answer (D8): the verdict rows said as `pager why` says them,
    then the mark rows, each in recorded order; the newest `DEFAULT_ROWS` of
    them when there are more; then the line naming what the answer is about;
    then, LAST, the cut (the text ends with it)."""
    want, sid = found["message_id"], found["session"]
    rows = ([(v["stamp"], f"verdict  {_why.why_text(v)}") for v in found["verdicts"]]
            + [(m["stamp"], "mark     " + "  ".join(
                [m["stamp"] or "-", m["mark"], m["seat"]] + ([m["reason"]] if m["reason"] else [])))
               for m in found["marks"]])
    kept = _receipt_newest([stamp for stamp, _ in rows], DEFAULT_ROWS)
    lines = [text for i, (_, text) in enumerate(rows) if i in kept]
    lines.append(f"-- {len(rows)} rows: what session {sid} RECORDED for <{want}>, read from "
                 f"{new} alone; never proof it read the content")
    if len(rows) > len(kept):
        lines.append(f"{len(rows) - len(kept)} older rows not shown; --json has all")
    return lines


@cli.command(
    "receipt",
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="ID --session SID [--json]",
    hidden=True,
    short_help="has session SID recorded a mark or verdict on ID?",
    epilog=unwrapped(
        """ID: one Message-ID, <> optional. Reads SID's mail/ alone; writes nothing.
A session that predates per-session marks may hold marks only in seats/*/marks.

A row is what SID RECORDED, never proof it read the content. read:
`message read` printed it, cut at 40 lines or 400 tokens unless --full.
seen: a page or a ride-along line, maybe cut, put it in context. done:
SID said it dealt with it, maybe in bulk. A verdict: the pager judged
and delivered it. Only SID's transcript shows what it read.

Exit: 0 a row · 1 none in SID's mail/ · 2 usage, could not look, or not
moved (old root named, never read) · 3 no such session: no session dir"""
    ),
)
@click.argument("message_id", metavar="ID")
@click.option("--session", "session", metavar="SID", required=True,
              help="the session asked about, by its FULL id")
@click.option("--json", "as_json", is_flag=True,
              help="every row as JSON (schema 1, docs/message-receipt.md); text: the newest 40")
def receipt(message_id: str, session: str, as_json: bool) -> None:
    """message receipt - has session SID recorded a mark or verdict on ID?"""
    ctx = click.get_current_context()
    # Usage first, before any path is built (D2, D4): click's UsageError is 2.
    want = _why.mid(message_id)
    if not want or any(c.isspace() for c in want) or "@" not in want:
        raise click.UsageError(
            f"ID {message_id!r}: one Message-ID, with its @ (an index is a position in one "
            "box, and a session id here is a swapped argument)")
    if not RECEIPT_SESSION.fullmatch(session):
        raise click.UsageError(
            f"--session {session!r}: a FULL session id (8-4-4-4-12 hex); a short id or a "
            "path is refused, and it is never your own by default")
    sid = session.lower()
    try:
        found = _receipt_answer(want, sid)
    except _Unmeasured as exc:
        click.echo(f"message receipt: {exc}", err=True)
        ctx.exit(EX_RECEIPT_UNMEASURED)
        return
    except Exception as exc:  # noqa: BLE001 - M1: an unnamed failure is 2, never 1
        click.echo(f"message receipt: could not look: {type(exc).__name__}: {exc}", err=True)
        ctx.exit(EX_RECEIPT_UNMEASURED)
        return
    code = {"yes": 0, "no": EX_RECEIPT_NONE}.get(found["answer"], EX_RECEIPT_NO_SESSION)
    new = found["looked_at"]["new"]
    if as_json:
        click.echo(json.dumps(found, ensure_ascii=False))
    elif code == 0:
        for line in _receipt_text(found, new):
            click.echo(line)
    elif code == EX_RECEIPT_NONE:
        click.echo(f"no row for <{want}> in {new}: session {sid}'s mail/ holds no mark or "
                   "verdict for it; the answer is about that root alone")
    else:
        click.echo(f"no such session: no session dir at {Path(new).parent} (where this "
                   "environment puts it); the answer is about that path alone")
    ctx.exit(code)


# The setup verb. Its name lives in ONE constant, `_setup.VERB`. It is on the
# group page, and it is not an MCP tool (`_mcp.NOT_A_TOOL`: it changes this
# host). The logic is `_setup.main(argv)`.
@cli.command(
    _setup.VERB,
    cls=PageCommand,
    context_settings=dict(CONTEXT_SETTINGS, ignore_unknown_options=True),
    usage="[--box NAME]... [--dry-run] [--write-maps]",
    short_help="[--box NAME]... [--dry-run]           make this host ready for mail",
    help=f"{_setup.COMMAND} - make this host ready: mail root, first boxes, notmuch hook",
    epilog=unwrapped(
        """Prints the WHOLE plan first (sudo steps, then every step's state),
then applies what is TODO; a sudo step runs only at a tty. Re-run safely.

  --box NAME   make box NAME too (repeatable); the operator's box
               (operator.mailbox, else the account name) is always made
  --dry-run    print the plan; apply nothing, write nothing
  --write-maps write postfix maps on a host with none (an MTA carrier)

Makes the agents root, the drop's boxes and its own notmuch profile
(never over a file or a database); links the hooks into its hook_dir.
Never edits postfix, a settings key, your notmuch config or a session box.

Exit: 0 every step DONE · 1 a step failed (what ran stays) · 2 usage
or refused, nothing applied · 4 done what it can, steps are left"""
    ),
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED, metavar="")
def setup(args: tuple[str, ...]) -> None:
    # No docstring: the page's first line is `help=` above, built from
    # `_setup.COMMAND`, so the name is written in one place.
    click.get_current_context().exit(_setup.main(list(args)))


from ._mcp import attach  # noqa: E402  - the `mcp` verb, declared once for the three

attach(cli, CAPABILITY)


# `python -m sessionmail.message` IS `message`. Without this the module imports,
# does nothing and exits 0, and the MCP send
# door starts its child exactly this way (openspec/changes/agents-send-door,
# "Running the module sends, or says why not - it never does nothing").
# `prog_name`, or the page says `Usage: message.py ...`.
if __name__ == "__main__":
    cli(prog_name="message")
