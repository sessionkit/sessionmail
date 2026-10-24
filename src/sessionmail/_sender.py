"""The sender: the six jobs `session-mail-send` did, in the package.

openspec/changes/send-in-python, step 4. Same argv as the bash oracle, so
step 5's switch is a dispatch and step 6's diff compares like with like.
Every `send:NNNN` below is a line of that script (2,575 lines, as it was
when ported); every refusal is exit 2 or 3 and fires BEFORE the
Message-ID is minted or a byte reaches sendmail - by ordering, not cleanup.

The contract the tests pin:

    run(argv, env, *, now=None, pid=None, rand=None) -> (rc, out, err)
    canonical(from_, to, subject, date, message_id, body) -> bytes
    check_msgid(value) -> (normalized | None, defect | None)

`env` is the WHOLE environment: every child (ssh-keygen, postconf, postmap,
sendmail) gets exactly it, so a test's environment is the sender's world.

A registered human on To, Cc or Bcc owes a rendered part (task 4b,
decision 2): multipart/alternative, the markdown first and signed,
the page second. The built-in renderer is `_render`, imported only when a
render is owed; `--renderer PATH` or $SESSIONMAIL_RENDERER names another,
`SESSIONMAIL_RENDER=off` never renders, `--raw` skips it for one mail.
"""

from __future__ import annotations

import base64
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from email._header_value_parser import parse_message_id
from email.utils import format_datetime
from pathlib import Path
from collections.abc import Mapping

import sessiontools

from . import _drop   # SESSIONMAIL_SENDMAIL=drop: the mail drop, no MTA
from . import _settings
from . import _sig

EX_OK, EX_REFUSED, EX_MTA, EX_RENDER = 0, 2, 3, 4

#: Decision 8: the prefix stays `session-mail-send:` for the one internal
#: release the bash is reachable by name; the first public one says `message send:`.
PREFIX = "session-mail-send: "
ARROW = "→"
DEFAULT_SENDMAIL = "/usr/sbin/sendmail"         # send:2450, no PATH hop
SIG_NS_DEFAULT = "session-mail@example.com"     # send:2149
SIG_FIELDS = "from:to:subject:date:message-id"
NO_SUBJECT = "(no subject)"                      # send:82
#: A C-locale [:space:], which is what `tr -d '[:space:]'` counts as no ink.
C_SPACE = b" \t\n\r\x0b\x0c"
#: C0 controls and DEL, less TAB (white space in a header) and CR/LF (their
#: own refusal, with their own words).
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: What `canon_addr` refuses after the reduction (send:2148).
UNCANONICAL = set('<> ():,"')


#: What KIND a refusal is: one word an agent can switch on, never prose
#: (agents-send-door, and AS A STRUCTURE: three groups, and a test that
#: forces every refusal to carry one). The WORDS are proposed and may change before release. Seven name the
#: INPUT to fix, four the WORLD. The tool's own three (timeout, no-receipt,
#: crashed) live with the MCP door: the sender can never be the one to say them.
#: `message-id` is the seventh input word (OQ2, an amendment to the
#: agents-send-door proposal): a caller's own `--message-id` refused.
KINDS_INPUT = ("usage", "subject", "address", "thread-id", "body", "header", "message-id")
KINDS_WORLD = ("identity", "undeliverable", "mta", "render")
KINDS = KINDS_INPUT + KINDS_WORLD
SENT = ("no", "unknown")


class Refused(Exception):
    """A send that will not go. Carries its exit code, ONE diagnostic, its
    KIND, and whether anything was sent.

    `kind` is REQUIRED and keyword-only, with no default: a refusal without
    a word cannot be written (a list in a spec holds nothing on its own).
    `tests/test_refusal_kinds.py` walks every site. `sent` is
    "no" unless the message had already been handed to the MTA."""

    def __init__(self, message: str, code: int = EX_REFUSED, *, kind: str, sent: str = "no"):
        if kind not in KINDS or sent not in SENT:
            raise ValueError(f"not a refusal kind: kind={kind!r} sent={sent!r}")
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.sent = sent
        #: What `send()` had already said when it refused (warnings come first).
        self.notices: list[str] = []


# ── settings: decision 9 ─────────────────────────────────────────────────────

def setting(env: Mapping[str, str], name: str, default: str = "") -> str:
    """`SESSIONMAIL_<NAME>`, else the old `SESSION_MAIL_<NAME>` (aliased for
    one release). Empty counts as unset, as bash's `:-` does."""
    return env.get("SESSIONMAIL_" + name) or env.get("SESSION_MAIL_" + name) or default


def host_of(env: Mapping[str, str]) -> str:
    """The variable, else the settings file's `host`, else the short hostname."""
    return (setting(env, "HOST") or _settings.file_text("host", env)
            or socket.gethostname().split(".")[0])   # send:1858


def root_of(env: Mapping[str, str]) -> Path:
    """The agents root: the variable, else `<root>/agents` from the settings
    file, else ~/.mail/agents."""
    given = setting(env, "ROOT")
    if given:
        return Path(given).expanduser()
    mail_root = _settings.file_path("root", env)
    if mail_root is not None:
        return Path(mail_root).expanduser() / "agents"
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(os.path.join(home, ".mail", "agents")).expanduser()


# ── addresses: pure ──────────────────────────────────────────────────────────

def collapse(value: str) -> str:
    """Whitespace runs to one space, ends trimmed. `str.split()` is the
    VERIFIER's rule (session-mail-verify:209,225); the bash's IFS split
    agrees on every value a header can carry after RFC 2047 encoding."""
    return " ".join(value.split())


def addr_spec(value: str) -> str:
    """The innermost `<...>` when there is one, else the whole field
    (send:79). Reduces exactly as the verifier's local_part() does."""
    v = collapse(value)
    i = v.rfind("<")
    j = v.find(">", i + 1) if i != -1 else -1
    if i != -1 and j != -1:
        v = collapse(v[i + 1:j])
    return v


def qualify_bare(value: str, domain: str) -> str:
    """VALUE with `@DOMAIN` appended to its address when that address holds no
    `@`, as postfix's cleanup appends myorigin (the mail drop's From).
    The address is the one `addr_spec` reduces to: the innermost `<...>` of a
    name-addr, else the whole field. The display phrase is kept byte for byte;
    an address already qualified, an empty one, or an empty DOMAIN is left
    exactly as given."""
    if not domain or not addr_spec(value) or "@" in addr_spec(value):
        return value
    i = value.rfind("<")
    j = value.find(">", i + 1) if i != -1 else -1
    if i != -1 and j != -1:
        return f"{value[:i + 1]}{collapse(value[i + 1:j])}@{domain}{value[j:]}"
    return f"{value.strip()}@{domain}"


def canon_addr(value: str) -> str:
    """The signed reduction: lowercased local-part, +extension kept, domain
    dropped. A residue that cannot be one is refused rather than half-signed
    (send:2148). SPECIFIED EQUAL to session-mail-verify's local_part(), which
    leg 1 and leg 2 of --trust both use; tests pin the two equal."""
    v = addr_spec(value)
    if UNCANONICAL & set(v) or v.count("@") > 1:
        raise Refused(f"invalid address: {value!r} (unparseable address form) - refusing "
                      f"to sign what the verifier could not reduce the same way", kind="address")
    return v.split("@", 1)[0].lower()


def lookup_addr(value: str, host: str) -> str:
    """The address postfix will look up: +ext stripped, host-qualified (send:2340)."""
    a = addr_spec(value)
    local, at, domain = a.partition("@")
    local = local.split("+", 1)[0].lower()
    return f"{local}@{(domain if at else host).lower()}"


def is_local(value: str, host: str) -> bool:
    """Unqualified, or qualified with this host's short or full name (send:2312)."""
    a = addr_spec(value)
    if "@" not in a:
        return True
    h, me = a.split("@", 1)[1].lower(), host.lower()
    return h == me or h.startswith(me + ".")


#: RFC 5322 3.6.8: a field name is printable US-ASCII, 33..126, but not ':'.
#: The same rule as the CLI door's `_send._FIELD_NAME`; the core keeps its
#: own copy because `_send` is the light door module and never imports this.
FIELD_NAME = re.compile(r"[!-9;-~]+")
#: The header that carries the subject as typed, when the door changed it.
ORIGINAL_SUBJECT = "X-Original-Subject"
#: RFC 5322 2.1.1: a line SHOULD be 78 characters at most and MUST be 998.
LINE_SHOULD, LINE_MUST = 78, 998


def fold(name: str, value: str, width: int = LINE_SHOULD) -> str:
    """`Name: value` folded ONLY at a space the text already holds (the one
    after the colon included), as `\n ` - so RFC 5322 unfolding, which removes
    the line break and keeps the space, gives the value back EXACTLY. Lines
    are packed greedily to WIDTH; a word longer than that stays whole on its
    own line (openspec/changes/subject-shorten: "the exact round-trip wins
    over the 78"). A break only ever comes before a non-empty word, so no line
    is white space alone (RFC 5322 3.2.2). Pure."""
    first, *words = f"{name}: {value}".split(" ")
    lines = [first]
    for word in words:
        if word and len(lines[-1]) + 1 + len(word) > width:
            lines.append(" " + word)
        else:
            lines[-1] += " " + word
    return "\n".join(lines)


def rfc2047(text: str) -> str:
    """ASCII passes byte-identical; otherwise base64 words of at most 45 raw
    bytes, split on character boundaries, joined by one space (send:777)."""
    if text.isascii():
        return text
    words, chunk = [], b""
    for ch in text:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > 45:
            words.append(chunk)
            chunk = b""
        chunk += b
    if chunk:
        words.append(chunk)
    return " ".join("=?UTF-8?B?" + base64.b64encode(w).decode("ascii") + "?=" for w in words)


def encode_addr_display(value: str) -> str:
    """A non-ASCII display name becomes an encoded phrase; the addr-spec and
    every ASCII form stay byte-identical (send:807)."""
    first, last = value.find("<"), value.rfind("<")
    if first == -1 or ">" not in value[first + 1:]:
        return value
    addr = value[last + 1:].split(">", 1)[0]
    phrase = value[:first].rstrip(" ")
    if phrase.isascii():
        return value
    if len(phrase) >= 2 and phrase[0] == phrase[-1] == '"':
        phrase = phrase[1:-1]
    return f"{rfc2047(phrase)} <{addr}>"


# ── threading ids: pure ──────────────────────────────────────────────────────

def check_msgid(value: str) -> tuple[str | None, str | None]:
    """Normalise (one `<` and one `>` stripped, re-wrapped) and parse as ONE
    RFC 5322 msg-id with no defects; the parser's text is the defect
    (send:397-419, :732). `&` is atext; the escaped form fails on its own."""
    bare = value.removeprefix("<").removesuffix(">")
    try:
        defects = [str(d) for d in parse_message_id(f"<{bare}>").all_defects]
    except Exception as exc:                      # a parser crash is a refusal too
        defects = [f"{type(exc).__name__}: {exc}"]
    if defects:
        return None, collapse("; ".join(defects))
    return f"<{bare}>", None


def refuse_msgid(flag: str, value: str, defect: str) -> Refused:
    """The malformed-id refusal, for the two threading flags and for
    `--message-id`. A threading id names ANOTHER mail, so its hint says where
    to copy it from; a `--message-id` is a NEW id, and a copied one is exactly
    what `_deliver`'s host rule refuses, so its hint only says to retype it
   . The threading words are byte for byte as they were."""
    escaped = any(e in value for e in ("&lt;", "&gt;", "&amp;"))
    said = (f"invalid {flag} value {value!r} - refusing to send: RFC 5322 does not parse it "
            f"as a Message-ID ({defect}).")
    if flag == "--message-id":
        hint = (" THIS IS THE HTML-ESCAPED FORM: retype the id with real angle brackets, "
                f"{flag} '<id@host>'.") if escaped else ""
        return Refused(f"{said} It would become the Message-ID header.{hint}", kind="message-id")
    what = ("Supersedes:" if flag == "--supersedes" else "In-Reply-To:")
    hint = ""
    if escaped:
        hint = (" THIS IS THE HTML-ESCAPED FORM: retype the id with real angle brackets, "
                f"{flag} '<id@host>' - copy it from the Message-ID printed at send time, "
                "not from a mail body.")
    return Refused(f"{said} It would become the {what} header and match nothing.{hint}",
                   kind="thread-id")


#: OQ6: the NORMALISED id,
#: angle brackets included. `Message-ID: ` is 12 characters and a reply's
#: `In-Reply-To: ` is 13, so 985 keeps both lines within RFC 5322's 998.
MESSAGE_ID_MAX = LINE_MUST - len("In-Reply-To: ")


def check_own_msgid(req: Request) -> None:
    """`--message-id`'s refusals that need no environment:
    malformed (the threading flags' own checker), non-ASCII, over-long, and the
    same id as this send's `--supersedes` or `--in-reply-to`. Normalises
    `req.message_id` in place. Idempotent: `validate` runs twice on the argv
    road. Called BEFORE `validate` copies `supersedes` into `in_reply_to`, so
    the equality refusal names the flag the caller typed (review note N2)."""
    value = req.message_id
    if value is None:
        return
    normalized, defect = check_msgid(value)
    if normalized is None:
        raise refuse_msgid("--message-id", value, defect or "no msg-id")
    if not normalized.isascii():
        raise Refused(f"invalid --message-id value {value!r} - refusing to send: a Message-ID is "
                      f"ASCII (RFC 5322 atext); it carries a non-ASCII character.",
                      kind="message-id")
    if len(normalized) > MESSAGE_ID_MAX:
        raise Refused(f"invalid --message-id value - refusing to send: {len(normalized)} "
                      f"characters, angle brackets included; a header line may not exceed "
                      f"{LINE_MUST} (RFC 5322 2.1.1), so the id may not exceed {MESSAGE_ID_MAX}, "
                      f"which keeps both its Message-ID: line and a reply's In-Reply-To: line "
                      f"legal.", kind="message-id")
    for flag, other in (("--supersedes", req.supersedes), ("--in-reply-to", req.in_reply_to)):
        if other is not None and other == normalized:
            raise Refused(f"--message-id {normalized} is the same id as this send's {flag} - "
                          f"refusing to send: a mail never carries the id it replies to or "
                          f"supersedes; give a NEW id, or drop the flag.", kind="message-id")
    req.message_id = normalized


def check_own_host(message_id: str, host: str) -> None:
    """OQ5 (BARE ONLY): a chosen id's right-hand side
    is this host's bare short name, `host_of(env)`, case-insensitively. Every
    MINTED id ends `@<host>.session-mail` (`mint`), so that form is refused: a
    chosen id can never equal a minted one. `message_id` is already normalised."""
    rhs = message_id[1:-1].rsplit("@", 1)[-1]
    if rhs.lower() != host.lower():
        raise Refused(f"--message-id {message_id} is not at this host - refusing to send: its "
                      f"right-hand side is {rhs!r}; use the bare host {host!r} "
                      f"(SESSIONMAIL_HOST, else the short hostname). An id ending .session-mail "
                      f"looks copied from a minted message. NOTHING WAS SENT.", kind="message-id")


# ── the request: argv, parsed and refused ────────────────────────────────────

@dataclass
class Request:
    to: str = ""
    from_: str = ""
    subject: str = NO_SUBJECT
    project: str = ""
    task: str = ""
    body_file: str | None = None
    no_body: bool = False
    supersedes: str | None = None
    in_reply_to: str | None = None
    unthreaded: bool = False
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    raw: bool = False
    renderer: str | None = None
    #: `--header` beyond X-Task/X-Project, already Title-Cased and
    #: refused by _send.parse_headers. Written after X-Task, before the stamps.
    #: The CORE guards them too (`validate`, door 2): a structured
    #: caller never passed through parse_headers.
    headers: list[tuple[str, str]] = field(default_factory=list)
    #: The subject as TYPED, when the door changed it (the shortener).
    #: Send OWNS the header it becomes (`X-Original-Subject`, _send.OWNED), so
    #: it is a named field the core guards, never one of `headers`. Not signed:
    #: SIG_FIELDS covers the subject AS SENT.
    original_subject: str = ""
    #: A caller-chosen Message-ID (`--message-id`), sent instead of the
    #: minted one; the Date is still minted. None mints, exactly as before.
    #: Checked in `validate` (malformed, non-ASCII, over-long, equal to this
    #: send's threading ids) and at the top of `_deliver` (this host, bare).
    message_id: str | None = None


VALUED = {"--from": "from_", "--subject": "subject", "--project": "project",
          "--task": "task", "--body-file": "body_file", "--supersedes": "supersedes",
          "--in-reply-to": "in_reply_to", "--renderer": "renderer",
          "--message-id": "message_id"}


def parse_argv(argv: list[str]) -> Request:
    """The bash's argv (send:909-1007) minus what decisions 1 and 3 dropped:
    --filedrop, --root, --reply-all, --quiet, --no-parent-check, inline words."""
    req, args = Request(), list(argv)
    if not args:
        raise Refused("missing <to>", kind="usage")
    if not args[0].startswith("-"):
        req.to = args.pop(0)
    while args:
        flag = args.pop(0)
        if flag == "--no-body":
            req.no_body = True
            continue
        if flag == "--raw":
            req.raw = True
            continue
        if flag in VALUED or flag in ("--cc", "--bcc"):
            if not args:
                raise Refused(f"{flag} needs a value", kind="usage")
            value = args.pop(0)
            if flag in ("--cc", "--bcc"):
                label = flag[2:].capitalize()
                if "\n" in value or "\r" in value:
                    raise Refused(f"line break in {flag} value - refusing to send: the "
                                  f"recipient headers ARE the envelope (sendmail -t), so it "
                                  f"injects a recipient. One address per {flag}.", kind="address")
                if "," in value:
                    raise Refused(f"comma in {flag} value - refusing to send: a comma adds "
                                  f"a recipient to the {label}: header. Repeat the flag instead.", kind="address")
                (req.cc if flag == "--cc" else req.bcc).append(value)
            else:
                setattr(req, VALUED[flag], value)
            continue
        if flag.startswith("-"):
            raise Refused(f"unknown flag: {flag}", kind="usage")
        raise Refused(f"inline body words are not carried ({flag!r}): the body is a file, "
                      f"--body-file PATH, or --no-body when the subject is the message", kind="usage")
    return req


def validate(req: Request) -> None:
    """Every argument refusal, in the bash's order (send:1013-1095)."""
    if not req.from_:
        raise Refused("missing --from", kind="usage")
    # DOOR 2: a structured request's own headers. The
    # CLI door's `_send.parse_headers` refuses these too, but a structured
    # caller (the pager, the MCP or a web door) comes straight here, so the
    # core is the guard for every road. A NAME first: a line break or a ':'
    # in it would write a header of the caller's choosing.
    # Then a header SEND WRITES ITSELF (`_send.OWNED`): with no line break at
    # all, `headers=[("X-Session-From", "forged")]` is written before the true
    # stamp and parses FIRST - the header forgery, with nothing to inject. The
    # CLI door refuses these in parse_headers; the core must too.
    # One list, imported, so the two
    # doors cannot drift (`_send` imports nothing from the package).
    from . import _send
    for name, _ in req.headers:
        if not FIELD_NAME.fullmatch(name):
            raise Refused(f"header name {name!r} is not a header name (printable ASCII, no "
                          f"spaces, no ':') - refusing to send", kind="header")
        low = name.lower()
        owner = _send.OWNED.get(low) or next(
            (w for p, w in _send.OWNED_PREFIXES.items() if low.startswith(p)), None)
        if owner:
            raise Refused(f"header {name!r} is written by send itself (set by {owner}) - "
                          f"refusing to send: a caller who could write it could forge it",
                          kind="header")
    # A line break in any single header value ends that header and starts
    # another. The door refuses it in Subject and recipients; the sender
    # refuses it everywhere, whatever the door's guard switch says.
    for name, value, kind in (("--from", req.from_, "address"), ("<to>", req.to, "address"),
                              ("--subject", req.subject, "subject"),
                              ("X-Original-Subject", req.original_subject, "subject"),
                              ("--project", req.project, "header"), ("--task", req.task, "header"),
                              *((f"--header {k}", v, "header") for k, v in req.headers)):
        if "\n" in value or "\r" in value:
            raise Refused(f"line break in the {name} value - refusing to send: it would end "
                          f"the header and inject the next one", kind=kind)
    # NUL, every other C0 control and DEL, in EVERY header value. Until the
    # core took a request, argv was the only guard: a process argument cannot
    # carry NUL, and a structured request can. TAB survives (decision 6): it is ordinary white space
    # in a mail header. The line break keeps its own words, above.
    for name, value, kind in (("--from", req.from_, "address"), ("<to>", req.to, "address"),
                              ("--subject", req.subject, "subject"),
                              ("X-Original-Subject", req.original_subject, "subject"),
                              ("--project", req.project, "header"), ("--task", req.task, "header"),
                              *((f"--header {k}", v, "header") for k, v in req.headers),
                              *(("--cc", a, "address") for a in req.cc),
                              *(("--bcc", a, "address") for a in req.bcc)):
        bad = CONTROL.search(value)
        if bad:
            raise Refused(f"control character {bad.group()!r} in the {name} value - refusing to "
                          f"send: a header value is text, and TAB is the only control it may "
                          f"carry", kind=kind)
    for flag in ("--supersedes", "--in-reply-to"):
        attr = "supersedes" if flag == "--supersedes" else "in_reply_to"
        value = getattr(req, attr)
        if value is None:
            continue
        normalized, defect = check_msgid(value)
        if defect:
            raise refuse_msgid(flag, value, defect)
        setattr(req, attr, normalized)
    check_own_msgid(req)                           # before the copy below
    if not req.to:
        raise Refused("missing <to>", kind="usage")
    if req.supersedes and not req.in_reply_to and not req.unthreaded:   # send:1093, 2030-06-09
        req.in_reply_to = req.supersedes


def read_body(req: Request) -> bytes:
    """The body file's refusals (send:1314-1498): refuse, never truncate."""
    if req.no_body:
        if req.body_file is not None:
            raise Refused("--no-body and --body-file were BOTH given - refusing to send. "
                          "Pass one of them.", kind="usage")
        if not req.subject.encode().translate(None, C_SPACE) or req.subject == NO_SUBJECT:
            raise Refused("--no-body with no --subject - refusing to send: a message with "
                          "neither says nothing. --no-body means the subject is the message.", kind="usage")
        return b""
    if req.body_file is None:
        raise Refused("no body given - refusing to send an empty message. Pass "
                      "--body-file PATH, or --no-body when the subject is the message.", kind="body")
    path = req.body_file
    if not path:
        raise Refused("--body-file was given an EMPTY path - refusing to send (an unset "
                      "shell variable looks like this)", kind="body")
    if path == "-":
        raise Refused("--body-file -: stdin is never read as a body - pass a real path", kind="body")
    p = Path(path)
    if not p.exists():
        raise Refused(f"--body-file: no such file: {path!r} - refusing to send", kind="body")
    if not p.is_file():
        raise Refused(f"--body-file: not a regular file: {path!r} - refusing to send", kind="body")
    if not os.access(p, os.R_OK):
        raise Refused(f"--body-file: not readable: {path!r} - refusing to send", kind="body")
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise Refused(f"--body-file: cannot read {path!r} ({exc.strerror}) - refusing to send", kind="body")
    check_body(data, f"--body-file: {path!r}")
    return data


def check_body(body: bytes, what: str = "the body") -> None:
    """The BODY's refusals, whichever door it came through: a text body handed
    to `send()` meets them exactly as a file does (agents-send-door, "A body
    is checked as a body"). WHAT names the source, so the file's own words
    are byte for byte what they were."""
    if b"\x00" in body:
        raise Refused(f"{what} contains a NUL byte - refusing to send: "
                      f"mail bodies are text; refuse, never truncate", kind="body")
    if not body.translate(None, C_SPACE):
        raise Refused(f"{what} has no body in it ({len(body)} bytes, none "
                      f"of them non-whitespace) - refusing to send", kind="body")


# ── render: a human on the line owes a page (task 4b, decision 2) ────────────

SHIPPED_HUMANS = Path(__file__).with_name("humans")


def render_on(env: Mapping[str, str]) -> bool:
    """`SESSIONMAIL_RENDER=off`: never render, never look up a registry."""
    return env.get("SESSIONMAIL_RENDER", "").strip().lower() not in ("off", "0", "no")


def humans_file(env: Mapping[str, str], root: Path) -> Path | None:
    """$SESSIONMAIL_HUMANS, else <root>/.humans, else the shipped list
    (send:1554-1603). A SET variable or a present .humans that cannot be
    read refuses: falling back would change who gets rendered mail."""
    named = setting(env, "HUMANS")
    if named:
        if not os.access(named, os.R_OK):
            raise Refused(f"$SESSIONMAIL_HUMANS is set but not readable: {named!r} - refusing "
                          f"to guess; when it is SET it is authoritative. Fix it or unset it.", kind="render")
        return Path(named)
    local = root / ".humans"
    if local.is_file():
        if not os.access(local, os.R_OK):
            raise Refused(f"humans registry exists but is not readable: '{local}' - refusing "
                          f"to guess. Fix the permissions.", kind="render")
        return local
    return SHIPPED_HUMANS if os.access(SHIPPED_HUMANS, os.R_OK) else None


def operator_human() -> set[str]:
    """The shipped list's one config-derived name: the
    operator's MAILBOX (`_operator.read`, pwd's home), case-folded. With NO
    operator block at all, the default operator: the installing
    account's name from the password database. That is formatting, not
    trust: it renders mail to the account for a human and privileges
    nothing. A bad file, or `"operator": null`, is nobody: code names no
    human. Only the shipped FINAL fallback adds it; a deployment's own
    registry ($SESSIONMAIL_HUMANS, <root>/.humans) is the whole list."""
    from . import _operator
    op = _operator.read()
    name = op.mailbox or op.default
    return {name.lower()} if name else set()


def humans_in(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        name = line.split("#", 1)[0].strip().lower()
        if name:
            out.add(name)
    return out


def human_key(value: str) -> str:
    """addr-spec, local part, +tag stripped, lowercased; no dot/dash guessing."""
    return addr_spec(value).split("@", 1)[0].split("+", 1)[0].lower()


def render_owed(req: Request, env: Mapping[str, str], root: Path, err: list[str]) -> bool:
    if req.raw or req.no_body or not render_on(env):
        return False
    path = humans_file(env, root)
    if path is None:
        err.append(f"{PREFIX}\u26a0  NO HUMANS REGISTRY FOUND ($SESSIONMAIL_HUMANS unset, no "
                   f"'{root}/.humans', no shipped list) - broken install: treating the "
                   f"recipients as agent-only, so mail to a HUMAN arrives UNRENDERED.")
        return False
    humans = humans_in(path)
    if path == SHIPPED_HUMANS:
        humans |= operator_human()
    return any(human_key(a) in humans for a in [req.to, *req.cc, *req.bcc] if a and human_key(a))


def _recipients(req: Request) -> str:
    out = f"To {req.to}"
    if any(req.cc):
        out += ", Cc " + ", ".join(a for a in req.cc if a)
    if any(req.bcc):
        out += ", Bcc " + ", ".join(a for a in req.bcc if a)
    return out


def render_refused(req: Request, what: str) -> Refused:
    return Refused(f"REFUSING TO SEND \u2014 this send OWES A RENDER (registered human on "
                   f"To/Cc/Bcc) but {what}. NOT SENT: '{req.subject}' ({_recipients(req)}). "
                   f"The render path NEVER silently degrades to single-part; to send honest "
                   f"single-part markdown deliberately, re-run with --raw.", EX_RENDER, kind="render")


def render(req: Request, body: bytes, env: Mapping[str, str]) -> bytes:
    """The page for the html part (send:1750-1855). Refusals are exit 4,
    except a body that is already an HTML document, which is exit 2."""
    lead = body.decode("utf-8", "replace").lstrip()[:256].lower()
    if lead.startswith("<!doctype") or lead.startswith("<html"):
        raise Refused("bodies are markdown and this one looks like a full HTML "
                      "document - refusing to render it. Send the markdown source, or pass "
                      "--raw to send the bytes as-is, single-part.", kind="body")
    try:                                  # the predicate lives beside the renderer
        from . import _render
    except Exception as exc:              # a broken install fails the render, loudly
        raise render_refused(req, f"the built-in renderer cannot load "
                                  f"({type(exc).__name__}: {exc})") from None
    named, how = req.renderer, "--renderer"
    if not named and setting(env, "RENDERER"):
        named, how = setting(env, "RENDERER"), "$SESSIONMAIL_RENDERER"
    if named:
        if not (os.path.isfile(named) and os.access(named, os.X_OK)):
            raise render_refused(req, f"{how} is set and AUTHORITATIVE but not an executable "
                                      f"file: '{named}' - fix it or drop it (a named renderer "
                                      f"never falls back silently)")
        page = _render_external(req, body, env, named)
    else:
        try:
            page = _render.render_page(body.decode("utf-8"), req.subject).encode("utf-8")
        except Exception as exc:          # fail closed: any failure is a refusal
            raise render_refused(req, f"the built-in renderer FAILED "
                                      f"({type(exc).__name__}: {exc})") from None
    if not _render.output_sane(page):
        raise render_refused(req, "the render output FAILED the sanity predicate (byte 1 is "
                                  "not '<!DOCTYPE' - a mislabeled page does not ship)")
    return page


def _render_external(req: Request, body: bytes, env: Mapping[str, str], program: str) -> bytes:
    """The file contract: `PROG BODY.md -o PAGE.html --title=SUBJECT`, both
    files 0600 in $TMPDIR, both gone afterwards whatever happened."""
    tmpdir = env.get("TMPDIR") or tempfile.gettempdir()
    made: list[str] = []
    try:
        fd, md = tempfile.mkstemp(prefix="session-mail-body.", dir=tmpdir)
        made.append(md)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        fd, page = tempfile.mkstemp(prefix="session-mail-page.", dir=tmpdir)
        made.append(page)
        os.close(fd)
        try:
            done = subprocess.run([program, md, "-o", page, f"--title={req.subject}"],
                                  capture_output=True, env=dict(env))
        except OSError as exc:
            raise render_refused(req, f"the renderer could not START ({exc.strerror})") from None
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        if done.returncode == 127:
            raise render_refused(req, "the renderer could not START (exit 127: "
                                      f"{said[0] if said else 'no message'})")
        if done.returncode != 0:
            raise render_refused(req, f"the renderer FAILED ({program} exit {done.returncode}: "
                                      f"{said[-1] if said else 'no message'})")
        return Path(page).read_bytes()
    except OSError as exc:
        raise render_refused(req, f"a render temp file could not be written in {tmpdir} "
                                  f"({exc.strerror})") from None
    finally:
        for path in made:
            Path(path).unlink(missing_ok=True)


def boundary_for(body: bytes, page: bytes, given: str | None = None) -> str:
    """`=_sesh890_` and 32 random hex, proven absent from both parts (send:1846)."""
    while True:
        b = given or "=_sesh890_" + os.urandom(16).hex()
        given = None
        if b.encode() not in body and b.encode() not in page:
            return b


# ── the maildir reader: Message-ID to file, no notmuch (decision 5) ──────────

def _head(path: str) -> bytes:
    """The header block's bytes: up to the first empty line, or the file."""
    out = b""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                out += chunk
                end = out.find(b"\n\n")
                if end != -1:
                    return out[:end + 1]
                if not chunk:
                    return out
    except OSError:
        return b""


def header_values(head: bytes, name: str) -> list[str]:
    """Every value of header NAME, continuation lines joined by one space
    (unfold_header, send:859)."""
    values, cur = [], None
    want = name.lower().encode() + b":"
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t") and cur is not None:
            cur.append(line.decode("utf-8", "replace"))
            continue
        if cur is not None:
            values.append(" ".join(cur).strip())
            cur = None
        if line.lower().startswith(want):
            cur = [line[len(want):].decode("utf-8", "replace")]
    if cur is not None:
        values.append(" ".join(cur).strip())
    return values


def find_messages(root: Path, ids: set[str]) -> dict[str, str]:
    """Each wanted `<id>` to ONE file under ROOT whose Message-ID is it.
    A plain walk of every box's new/ and cur/; stops when all are found."""
    wanted = {i: i.strip("<>").encode() for i in ids}
    found: dict[str, str] = {}
    if not wanted or not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "tmp" and not d.startswith(".")]
        if os.path.basename(dirpath) not in ("new", "cur"):
            continue
        for name in filenames:
            path = os.path.join(dirpath, name)
            head = _head(path)
            for msgid, needle in wanted.items():
                if msgid in found or needle not in head:
                    continue
                if any(v.strip().strip("<>") == needle.decode() for v in header_values(head, "Message-ID")):
                    found[msgid] = path
            if len(found) == len(wanted):
                return found
    return found


#: How long one `notmuch search` may take before the walk answers instead: a
#: `notmuch new` holding the database lock must not hold a send.
NOTMUCH_TIMEOUT = 2.0


def id_term(bare: str) -> str:
    """notmuch's query for ONE Message-ID, quoted: `id:"<bare>"`, any `"`
    inside doubled. Unquoted, an id with a space or a paren is a query parse
    error - harmless here, since an error
    falls to the walk, but the index should answer for every id it holds."""
    return 'id:"' + bare.replace('"', '""') + '"'


def notmuch_files(root: Path, ids: set[str], env: Mapping[str, str]) -> dict[str, str]:
    """Each wanted `<id>` to ONE file under ROOT, by notmuch's index - the
    accelerator (mail, so notmuch, with a fallback). Empty for every id when notmuch is
    not on env's PATH, fails,
    or times out; an id it does not know, or knows only outside ROOT, is
    simply absent - the caller walks for what is left. A file is taken only
    if its own Message-ID says so, as the walk would.

    Which index it asks is `_index`'s: `--config=<profile>`, with the three
    NOTMUCH_* overrides scrubbed; a refusal (no profile) is empty too, so a
    send never fails for want of an index."""
    # Here, not at the top: this function is the store index's whole fence in
    # this file. A module reference, never `from ._index import ...`.
    from . import _index
    found: dict[str, str] = {}
    exe = shutil.which("notmuch", path=env.get("PATH"))
    if not exe or not ids or not root.is_dir():
        return found
    resolved = _index.resolve(dict(env))
    if resolved.refused:
        return found                           # no profile: the walk answers
    prefix, child = [exe, f"--config={resolved.config}"], _index.scrub(dict(env))
    top = root.resolve()
    for msgid in ids:
        bare = msgid.strip().strip("<>")
        try:
            res = subprocess.run([*prefix, "search", "--output=files", id_term(bare)],
                                 capture_output=True, text=True, timeout=NOTMUCH_TIMEOUT,
                                 env=child)
        except (OSError, subprocess.SubprocessError):
            return found                       # no index to ask: the walk answers
        if res.returncode != 0:
            return found
        for path in res.stdout.splitlines():
            p = Path(path)
            try:
                inside = p.resolve().is_relative_to(top)
            except OSError:
                continue
            if inside and any(v.strip().strip("<>") == bare
                              for v in header_values(_head(path), "Message-ID")):
                found[msgid] = path
                break
    return found


def find_parents(root: Path, ids: set[str], env: Mapping[str, str]) -> dict[str, str]:
    """notmuch first, then the walk for whatever it did not find (a bare box
    without notmuch, an index that has not seen a just-arrived parent yet)."""
    found = notmuch_files(root, ids, env)
    rest = ids - found.keys()
    if rest:
        found.update(find_messages(root, rest))
    return found


def references_chain(parent: str, parent_file: str | None) -> list[str]:
    """The parent's References, then the parent (send:886-906, RFC 5322
    §3.6.4). One hop when the parent is not findable."""
    if parent_file is None:
        return [parent]
    tokens = " ".join(header_values(_head(parent_file), "References")).split()
    return [t for t in tokens if t != parent] + [parent]


# ── sign: world ──────────────────────────────────────────────────────────────

def canonical(from_: str, to: str, subject: str, date: str, message_id: str,
              body: bytes) -> bytes:
    """Canonicalization v1 (send:2152-2155): bash's `$(...)` strips every
    trailing newline of the whole text, `printf '%s\\n'` adds one - so an
    empty body signs as `...message-id: X\\n`, no blank line at all."""
    text = (f"from: {canon_addr(from_)}\nto: {canon_addr(to)}\n"
            f"subject: {collapse(subject)}\ndate: {collapse(date)}\n"
            f"message-id: {collapse(message_id)}\n\n").encode("utf-8") + body
    return text.rstrip(b"\n") + b"\n"


def keyed(env: Mapping[str, str]) -> bool:
    pub = env.get("CLAUDE_SESSION_PUB") or ""
    return bool(env.get("CLAUDE_SESSION_AGENT_SOCK")) and bool(pub) and Path(pub).is_file()


def sig_ns(env: Mapping[str, str]) -> str:
    """The signing namespace: $SESSIONMAIL_SIG_NS (either spelling), else the default."""
    return setting(env, "SIG_NS") or SIG_NS_DEFAULT


def sign_bytes(data: bytes, env: Mapping[str, str], ns: str) -> str | None:
    """`ssh-keygen -Y sign` under the session agent (decision 4: a subprocess).
    The BARE base64 blob, or None: unkeyed, no ssh-keygen, or it failed. Never
    raises. The key-handling half of signing, and the only one a seat key
    changes (which key signs); its must-sign refusal keys on this None. The
    header is `_sig.format_header`'s."""
    if not keyed(env):
        return None
    tool = shutil.which("ssh-keygen", path=env.get("PATH", ""))
    if tool is None:
        return None
    child = {**env, "SSH_AUTH_SOCK": env["CLAUDE_SESSION_AGENT_SOCK"]}
    try:
        done = subprocess.run([tool, "-Y", "sign", "-f", env["CLAUDE_SESSION_PUB"], "-n", ns, "-"],
                              input=data, capture_output=True, env=child)
    except OSError:
        return None
    if done.returncode != 0:
        return None
    armor = done.stdout.decode("ascii", "replace").split("\n")
    return "".join(line for line in armor
                   if "-----BEGIN SSH SIGNATURE-----" not in line
                   and "-----END SSH SIGNATURE-----" not in line)


def key_principal(env: Mapping[str, str]) -> tuple[str | None, str, bool]:
    """(the principal the session key names, the key's comment as written,
    whether the comment is the seat-init shape).

    Two shapes are live: `message seat init` writes `<address> seat`
    (seat_init.py `generate`), and the Claude-session keyholder writes its
    `--agent-name` bare (sessiontools start/identity.py `mint_identity`: the
    seat's mail address when a spawner passes one, else the bare agent name).
    So: a trailing ` seat` is dropped, else the comment as-is. The principal
    is None when the key names nothing comparable: no comment, or one that is
    not a single token. Never raises: an unreadable pub names nothing."""
    try:
        fields = Path(env.get("CLAUDE_SESSION_PUB") or "").read_text("utf-8", "replace").split()
    except OSError:
        return None, "", False
    comment = " ".join(fields[2:])
    seat_shape = comment.endswith(" seat")
    principal = comment[:-len(" seat")] if seat_shape else comment
    if not principal or len(principal.split()) != 1:
        return None, comment, False
    return principal, comment, seat_shape


def check_key_seat(from_: str, env: Mapping[str, str], required: bool, err: list[str]) -> None:
    """A keyed send whose From is not the SEAT its key names.

    The contract: the seat identity is compared. Both sides go through `canon_addr`, the signed
    reduction: the local part, lowercased, its `+extension` KEPT; the domain
    is not compared (the verifier owns that). Only an ADDRESS-SHAPED principal is
    compared: one holding `+` or `@`, or any `<x> seat` key. A bare keyholder
    agent name (`alice`) names no seat. A `proj seat` key is the seat `proj`
    and matches From `proj` only, never `proj+*`.

    Under $SESSIONMAIL_REQUIRE_SIGNATURE a mismatch is REFUSED (kind
    identity); without it the send signs as-is and says so on stderr. Not
    compared: a key naming no seat (no comment, more than one word, a bare
    agent name, a principal `canon_addr` cannot reduce), with one note under
    must-sign only; and a From that is this session's own id (a DM reply,
    `message._reply_from`).

    This is NOT the receiver's `--trust` leg 2, but leg 2 now
    reduces both of its sides with the verifier's local_part(), which is
    SPECIFIED EQUAL to canon_addr; the two now differ on two rows, each with
    the receiver the stricter side: a bare agent name, which this check does
    not compare, and a principal carrying `@` (`bob@testhost` as From `bob`),
    which this check passes by dropping the domain and leg 2 refuses outright
    (the verifier owns the domain). A key comment is free text: this
    catches accidents and a mis-set environment, not a hostile
    seat."""
    principal, comment, seat_shape = key_principal(env)
    asked = addr_spec(from_)
    seat = None
    if principal is not None and (seat_shape or "+" in principal or "@" in principal):
        try:
            seat = canon_addr(principal)
        except Refused:
            seat = None
    if not seat:
        if required:
            err.append(f"{PREFIX}note: the session key names no seat (comment "
                       f"{comment or '(none)'!r}), so From {asked!r} is not checked against it")
        return
    sid = (sessiontools.session_id(env) or "").lower()
    if sid and canon_addr(asked) == sid:
        return
    if canon_addr(asked) == seat:
        return
    said = (f"the session key names {principal!r} ($CLAUDE_SESSION_PUB comment {comment!r}) "
            f"but this send's From is {asked!r}")
    fix = (f"send from the key's seat (EMAIL / TM_AGENT_ADDRESS={principal}), or give "
           f"{asked!r} its own key with `message seat init`")
    if required:
        raise Refused(f"${REQUIRE_SIGNATURE} is set and {said} - NOTHING WAS SENT. Fix: {fix}",
                      kind="identity")
    err.append(f"{PREFIX}⚠  {said}; signing as-is (under ${REQUIRE_SIGNATURE} this send "
               f"is refused). Fix: {fix}")


def sign_wire(wire: bytes, env: Mapping[str, str], err: list[str]) -> str | None:
    """Signature v2: ONE signature over the exact wire
    `build` made - every header it wrote (`_sig.h_for`: the floor, then the rest,
    less NEVER) and every MIME part (`p=`). The X-Session-Signature header, or
    None when `sign_bytes` gives None. ORIGIN is asked only when a bare address
    is in a signed header (one `postconf` run); with none found the addresses
    sign bare, the send still goes (7(b)) and the receipt says so."""
    ns = sig_ns(env)
    h = _sig.h_for(wire)
    bare = _sig.has_bare_address(wire, h)
    origin = _sig.origin_of(env) if bare else ""
    p = _sig.digests(wire)
    b64 = sign_bytes(_sig.canonical(wire, ns, h, p, origin), env, ns)
    if b64 is None:
        return None
    if bare and not origin:
        err.append(f"{PREFIX}⚠  signed a BARE address unqualified: no mail origin "
                   f"($SESSIONMAIL_ORIGIN unset, no `postconf -xh myorigin`); if postfix "
                   f"qualifies it in transit, this mail reads BAD")
    return _sig.format_header(ns, h, p, b64)



#: A send that cannot sign is refused, never sent unsigned: a per-send
#: switch. Unset, 7(b) stands and the default is unchanged. Read with `setting`,
#: so the old SESSION_MAIL_ spelling works too.
REQUIRE_SIGNATURE = "SESSIONMAIL_REQUIRE_SIGNATURE"
OFF_WORDS = ("", "0", "off", "no", "false")


def must_sign(env: Mapping[str, str]) -> bool:
    """True when the send must carry a signature or not go at all. Fails
    CLOSED: any value but an off-word (empty, 0, off, no, false) turns it on,
    so a typo in a guard never quietly sends unsigned."""
    return setting(env, "REQUIRE_SIGNATURE").strip().lower() not in OFF_WORDS

# ── pre-flight: world ────────────────────────────────────────────────────────

#: General delivery (mailhost (c)): an
#: unknown same-host address shaped like a SESSION (a UUID, or its 8-hex
#: short form) goes to the mail host as ID@RELAY_DOMAIN when postfix has a
#: transport for that. The mail host files it in `_unsorted`, and the puller
#: on the host where the session lives takes it from there. Unknown SEAT
#: names are still refused: those are usually typos.
SESSION_SHAPED = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8}", re.I)
RELAY_DOMAIN_VAR = "SESSIONMAIL_RELAY_DOMAIN"
RELAY_DOMAIN = "relay.home"


def relay_domain(env: Mapping[str, str]) -> str:
    """Where a session-shaped miss relays: the variable, else the settings
    file's `postfix.relay_domain`, else RELAY_DOMAIN; trimmed, lowercase."""
    return (env.get(RELAY_DOMAIN_VAR) or _settings.file_text("postfix.relay_domain", env)
            or RELAY_DOMAIN).strip().lower()


def _relayed(value: str, domain: str) -> str:
    """VALUE with its address's domain set to DOMAIN; the display name, the
    local part and its +ext kept."""
    a = addr_spec(value)
    new = f"{a.partition('@')[0]}@{domain}"
    i = value.rfind("<")
    return f"{value[:i]}<{new}>" if i != -1 and value.find(">", i) != -1 else new


def preflight(req: Request, host: str, env: Mapping[str, str],
              err: list[str] | None = None) -> None:
    """Every same-host recipient must be in postfix's live maps; one miss
    fails the whole send by name (send:2295-2443). No tooling: no block.
    A miss shaped like a session is relayed instead, when it can be (see
    SESSION_SHAPED); REQ's field is rewritten and ERR gets a notice."""
    path = env.get("PATH", "")
    postmap, postconf = shutil.which("postmap", path=path), shutil.which("postconf", path=path)
    if postmap is None:
        return
    maps: list[str] = []
    if postconf is not None:
        done = subprocess.run([postconf, "-h", "virtual_mailbox_maps", "virtual_alias_maps"],
                              capture_output=True, text=True, env=dict(env))
        maps = done.stdout.replace(",", " ").split()
    if not maps:
        return
    for label, value in [("To", req.to)] + [("Cc", a) for a in req.cc] + [("Bcc", a) for a in req.bcc]:
        if not value or not is_local(value, host):
            continue
        addr = lookup_addr(value, host)
        if any(subprocess.run([postmap, "-q", addr, m], capture_output=True, env=dict(env)).returncode == 0
               for m in maps):
            continue
        relayed = _relay(value, addr, postmap, postconf, env)
        if relayed is not None:
            if label == "To":
                req.to = relayed
            else:
                field_ = req.cc if label == "Cc" else req.bcc
                field_[field_.index(value)] = relayed
            if err is not None:
                err.append(f"{PREFIX}general delivery: {label} '{addr}' is no box on this host but "
                           f"looks like a session, so it goes as '{addr_spec(relayed)}' to the mail "
                           f"host; the host where that session lives takes it from there.")
            continue
        shown = f"{label} '{addr}'" + ("" if value == addr else f"  (from {label} value '{value}')")
        raise Refused(
            f"REFUSING TO SEND - {shown} is not deliverable on this host: postfix has no "
            f"route for it, so submitting would 'succeed' and then BOUNCE into the sender's "
            f"box. NOTHING WAS SUBMITTED, to this recipient or any other. Check the map: "
            f"postmap -q '{addr}' {' '.join(maps)}", EX_MTA, kind="undeliverable")


def _relay(value: str, addr: str, postmap: str, postconf: str | None,
           env: Mapping[str, str]) -> str | None:
    """VALUE rewritten to the relay domain, when ADDR's box is session-shaped
    and postfix has a transport for the relayed address; else None."""
    if postconf is None or not SESSION_SHAPED.fullmatch(addr.partition("@")[0]):
        return None
    domain = relay_domain(env)
    if not domain:
        return None
    done = subprocess.run([postconf, "-h", "transport_maps"], capture_output=True, text=True,
                          env=dict(env))
    probe = f"{addr.partition('@')[0]}@{domain}"      # a regexp map keys the address,
    if not any(subprocess.run([postmap, "-q", key, m], capture_output=True,   # a hash map the domain
                              env=dict(env)).returncode == 0
               for m in done.stdout.replace(",", " ").split() for key in (probe, domain)):
        return None
    return _relayed(value, domain)


# ── the mail drop: no MTA (SESSIONMAIL_SENDMAIL=drop) ────────────────────────
#
# A host with no postfix
# delivers by MAIL DROP. `_drop` resolves each recipient with the SAME map
# generator postfix is fed from, and writes into the maildir. The pre-flight
# above asks postfix's compiled maps, which a postfix-less host does not
# have; in drop mode the gate asks the drop's own resolution instead - same
# promise (refuse naming the address, before the mint, nothing written).

def drop_preflight(req: Request, env: Mapping[str, str]) -> "_drop.Maps":
    """Every recipient resolves to an existing box, or the send refuses by
    name before anything is minted. Returns the maps, reused for delivery."""
    try:
        maps = _drop.prepare(env)
        _drop.plan([a for a in [req.to, *req.cc, *req.bcc] if a], maps)
    except _drop.NotDelivered as n:
        raise Refused(f"REFUSING TO SEND (mail drop) - {n}", EX_MTA, kind="undeliverable")
    maps.origin = _drop.origin(env, maps.host, maps.notes)
    return maps


def drop_deliver(req: Request, wire: bytes, maps: "_drop.Maps", message_id: str,
                 err: list[str]) -> str:
    rcpts = [addr_spec(a) for a in [req.to, *req.cc, *req.bcc] if a]
    try:
        written = _drop.drop(wire, addr_spec(req.from_), rcpts, maps)
    except _drop.NotDelivered as n:
        # The drop's text already names the copies delivered, the box that
        # failed and the boxes not attempted; it is not said twice.
        raise Refused(f"the mail drop did not deliver: {n}"
                      + ("" if n.written else " - NOTHING WAS SENT"),
                      EX_MTA, kind="mta", sent="unknown" if n.written else "no")
    err.extend(f"{PREFIX}{note}" for note in maps.notes)
    boxes = ", ".join(p.parent.parent.name for p in written)
    err.append(f"{PREFIX}sent via mail drop (no MTA) {ARROW} {boxes}")
    return message_id


# ── the six jobs, in order ───────────────────────────────────────────────────

def parent_check_on(env: Mapping[str, str], err: list[str]) -> bool:
    raw = setting(env, "PARENT_CHECK")
    value = raw.lower()
    if value in ("", "on", "1", "yes"):
        return True
    if value in ("off", "0", "no"):
        return False
    err.append(f"{PREFIX}⚠  ignoring $SESSIONMAIL_PARENT_CHECK={raw!r} - expected on|off "
               f"(or 1|0, yes|no). The check stays ON; it only ever warns.")
    return True


#: RFC 5322 section 2.1.1: a line is at most 998 octets, its CRLF not counted.
#: A longer line sent 8bit is folded by the MTA (postfix's
#: smtp_line_length_limit: a CRLF plus one space after octet 998), so the body
#: arrives altered while the send exits 0.
LINE_LIMIT = 998


def _leaf_cte(data: bytes) -> tuple[str, bytes]:
    """One MIME leaf's Content-Transfer-Encoding and its bytes as they go on
    the wire. Pure.

    `8bit` with DATA untouched while every line (split on LF, the LF not
    counted; a CR before it IS counted, so a CRLF line errs toward encoding)
    is within LINE_LIMIT: byte for byte what every send wrote before long-line folding.
    Past it, `quoted-printable`: every encoded line is 76 octets or fewer, so
    no MTA can fold it, and the decoded bytes are DATA exactly. QP and not
    base64 because a text body stays readable and greppable in the maildir
    file and is smaller; base64 is for binary.

    The signature is unchanged by the choice: v2's p= hashes each leaf AFTER
    its transfer encoding is undone (`_sig.leaf_canon`), and the
    Content-Transfer-Encoding header is never signed (`_sig.NEVER`). The
    caller encodes BEFORE `sign_wire`, as `build` runs first.

    THE SEAM for `send --attach`: every leaf `build` writes takes
    its encoding from here; an attachment leaf forces base64 beside it."""
    if all(len(line) <= LINE_LIMIT for line in data.split(b"\n")):
        return "8bit", data
    return "quoted-printable", qp_encode(data)


#: QP's literal octets (RFC 2045 section 6.7 rule 2): printable ASCII but `=`.
#: SPACE and TAB are literal too, except where they would end a line (rule 3).
QP_LITERAL = frozenset(range(33, 127)) - {ord("=")}
#: An encoded line is at most 76 characters (rule 5), its soft-break `=` included.
QP_WIDTH = 76


def qp_encode(data: bytes) -> bytes:
    """DATA quoted-printable, strictly (RFC 2045 section 6.7), so that any
    decoder gives DATA back byte for byte. Pure.

    Each LF in DATA is a hard line break and stays an LF; every other octet,
    a CR included, is literal or `=XX`, so a CRLF or a lone CR survives. A
    SPACE or TAB at the end of a line is always escaped (a decoder strips one
    left literal), every encoded line is 76 characters or fewer, and a soft
    break never splits an `=XX`. Not `quopri.encodestring`: binascii's
    encoder writes 77- and 78-character lines when it escapes a trailing
    space (measured), and `email.quoprimime` splits on a lone
    CR, so the CR comes back as a line break."""
    out = []
    for line in data.split(b"\n"):
        last = len(line) - 1
        rows, row, width = [], [], 0
        for i, octet in enumerate(line):
            if octet in QP_LITERAL or (octet in (9, 32) and i < last):
                token = bytes((octet,))
            else:
                token = b"=%02X" % octet
            if width + len(token) > QP_WIDTH - 1:      # room for the soft-break `=`
                rows.append(b"".join(row) + b"=")
                row, width = [], 0
            row.append(token)
            width += len(token)
        rows.append(b"".join(row))
        out.append(b"\n".join(rows))
    return b"\n".join(out)


def build(req: Request, body: bytes, *, host: str, date: str, message_id: str,
          references: list[str], session: str, signature: str | None,
          page: bytes | None = None, boundary: str = "") -> bytes:
    """The header block in build_message's order, then the body's own bytes
    (send:1887-2111), each leaf quoted-printable instead when a line of it is
    past LINE_LIMIT (`_leaf_cte`). Pure."""
    h = [f"From: {req.from_}", f"To: {req.to}"]
    cc, bcc = ", ".join(a for a in req.cc if a), ", ".join(a for a in req.bcc if a)
    if cc:
        h.append(f"Cc: {cc}")
    if bcc:
        h.append(f"Bcc: {bcc}")
    h += [f"Date: {date}", f"Subject: {req.subject}", f"Message-ID: {message_id}"]
    if req.in_reply_to:
        h.append(f"In-Reply-To: {req.in_reply_to}")
        h.append("References: " + "\n ".join(references))
    content_type = (f'multipart/alternative; boundary="{boundary}"' if page is not None
                    else "text/plain; charset=utf-8")
    # A single part carries its own encoding. A multipart's top level
    # stays 8bit: RFC 2045 section 6.4 forbids QP or base64 on a multipart
    # entity, so its parts are encoded each on their own, below.
    cte, data = _leaf_cte(body) if page is None else ("8bit", body)
    h += ["MIME-Version: 1.0", f"Content-Type: {content_type}",
          f"Content-Transfer-Encoding: {cte}"]
    if req.project:
        h.append(f"X-Project: {req.project}")
    if req.task:
        h.append(f"X-Task: {req.task}")
    if req.original_subject:
        h.append(fold(ORIGINAL_SUBJECT, req.original_subject))
    for name, value in req.headers:
        h.append(f"{name}: {value}")
    if req.supersedes:
        h.append(f"Supersedes: {req.supersedes}")
    if session:
        h.append(f"X-Session-From: {session}")
    h.append(f"X-Machine: {host}")
    if signature:
        h.append(signature)
    head = ("\n".join(h) + "\n\n").encode("utf-8")
    if page is None:
        return head + data
    part = "Content-Type: text/{}; charset=utf-8\nContent-Transfer-Encoding: {}\n\n"
    (text_cte, text), (html_cte, html) = _leaf_cte(body), _leaf_cte(page)
    return (head + f"--{boundary}\n{part.format('plain', text_cte)}".encode() + text
            + f"\n--{boundary}\n{part.format('html', html_cte)}".encode() + html
            + f"\n--{boundary}--\n".encode())


@dataclass
class Receipt:
    """What a send hands back: the id, and everything it said on the way."""
    message_id: str
    notices: list[str]


def send(req: Request, body: bytes, env: Mapping[str, str], *, now: int | None = None,
         pid: int | None = None, rand: int | None = None,
         boundary: str | None = None) -> Receipt:
    """THE CORE: a request already parsed, a body already in hand; a Receipt
    back, or `Refused` carrying the notices gathered before it. Every door
    ends here - `run` below for an argv, a web API directly (the CLI and the
    MCP use the same underlying functions, so a web API can too).

    It validates and checks the body ITSELF, because a structured caller comes
    straight to it; `validate` is idempotent, so the argv door's earlier call
    costs nothing."""
    notices: list[str] = []
    try:
        validate(req)
        if req.no_body:
            body = b""
        else:
            check_body(body)
        return Receipt(_deliver(req, body, env, notices, now=now, pid=pid, rand=rand,
                                boundary=boundary), notices)
    except Refused as refusal:
        refusal.notices = notices
        raise


#: The RANDOM in `<EPOCH.PID.RANDOM>`: bash's `$RANDOM`, 0..32767 (send:1857-1862).
RAND_MAX = 32767
#: The rands THIS process has minted, by epoch second. The bash sender
#: was a fresh process per send, so its pid alone kept two sends apart. In
#: process (`memory init`'s seven sends, the MCP door, the pager's notes)
#: every send shares one pid, and an id is then only 15 random bits a second:
#: two of init's labels drew ONE id about one run in 2,000 (measured), and the box it
#: minted was a fork every later run
#: refused, exit 2. So a rand is never drawn twice in one second of one
#: process; the pid still keeps processes apart.
_DRAWN: dict[int, set[int]] = {}
_DRAWN_LOCK = threading.Lock()
#: Seconds of history kept: a send whose clock was read just before another
#: thread's still finds its second here.
_DRAWN_KEEP = 60


def fresh_rand(epoch: int) -> int:
    """A rand in 0..RAND_MAX that this process has not minted in EPOCH, drawn
    at random as bash's `$RANDOM` is. The default for every mint whose caller
    fixes no rand; a caller that fixes one (a test, the pager's proof) keeps
    it. Raises RuntimeError rather than spin when one second has used all
    32,768, which no sender that forks an MTA per send can reach."""
    with _DRAWN_LOCK:
        for old in [e for e in _DRAWN if e < epoch - _DRAWN_KEEP]:
            del _DRAWN[old]
        used = _DRAWN.setdefault(epoch, set())
        if len(used) > RAND_MAX:
            raise RuntimeError(f"every Message-ID rand of second {epoch} is minted in this "
                               f"process ({RAND_MAX + 1}); refusing to mint a duplicate")
        while True:
            rand = random.randint(0, RAND_MAX)
            if rand not in used:
                used.add(rand)
                return rand


def mint(host: str, epoch: int, pid: int, rand: int) -> tuple[str, str]:
    """The Message-ID and Date a send carries, from its parts (send:1857-1862).
    Pure, and the ONE definition: the pager fixes both before it sends a
    confirmation, because its proof covers them, and hands the
    same parts to `send` so the wire carries exactly what was proven."""
    message_id = f"<{epoch}.{pid}.{rand}@{host}.session-mail>"
    return message_id, format_datetime(datetime.fromtimestamp(epoch).astimezone())


def _deliver(req: Request, body: bytes, env: Mapping[str, str], err: list[str], *,
             now: int | None, pid: int | None, rand: int | None, boundary: str | None) -> str:
    host = host_of(env)
    if req.message_id is not None:               # before render, preflight, mint, drop
        check_own_host(req.message_id, host)

    # Render before anything else touches the world (send:1642-1855); the
    # title is the subject as typed, before RFC 2047.
    page = render(req, body, env) if render_owed(req, env, root_of(env), err) else None
    mime_boundary = boundary_for(body, page, boundary) if page is not None else ""

    # Wire forms are fixed BEFORE signing: encode-then-sign (send:1875-1877).
    req.subject = rfc2047(req.subject)
    if req.original_subject:
        # Encoded like the Subject, then folded in `build`. A word no
        # line can hold (over 998, RFC 5322's MUST) is never split - that would
        # break the exact round-trip - so the header is left off and the mail
        # still goes (the mail just goes); the receipt says why.
        req.original_subject = rfc2047(req.original_subject)
        if any(len(line) > LINE_MUST
               for line in fold(ORIGINAL_SUBJECT, req.original_subject).split("\n")):
            err.append(f"{PREFIX}⚠  {ORIGINAL_SUBJECT} not written: the original subject holds "
                       f"a word longer than one header line may be ({LINE_MUST}, RFC 5322 "
                       f"2.1.1); the mail goes with the subject as sent")
            req.original_subject = ""
    req.from_ = encode_addr_display(req.from_)
    req.to = encode_addr_display(req.to)
    signing = keyed(env)
    required = must_sign(env)
    if required and not signing:                 # before the MTA, preflight and mint
        raise Refused(f"${REQUIRE_SIGNATURE} is set and this send cannot be signed: no session "
                      f"key in this environment ($CLAUDE_SESSION_AGENT_SOCK unset, or "
                      f"$CLAUDE_SESSION_PUB not a file) - NOTHING WAS SENT", kind="identity")
    if signing:                                  # the address refusal, before the mint
        canon_addr(req.from_)
        canon_addr(req.to)
        check_key_seat(req.from_, env, required, err)   # before the MTA and the mint
    carrier = _settings.carrier(env)              # before the pre-flight and the mint
    if carrier.kind == "refused":
        raise Refused(f"no MTA: {carrier.why} - NOTHING WAS SENT", EX_MTA, kind="mta")
    sendmail = carrier.path or DEFAULT_SENDMAIL
    dropping = drop_preflight(req, env) if carrier.kind == "drop" else None
    if dropping is not None:
        # Postfix appends myorigin to a bare From in transit; the drop
        # has no transit, so it is done HERE, before the mint, the build and
        # the signature, never to built bytes. The Return-Path follows
        # (`drop_deliver`). The postfix path never comes here: its From stays
        # bare, byte for byte as before.
        req.from_ = qualify_bare(req.from_, dropping.origin)
    else:
        if not (os.path.isfile(sendmail) and os.access(sendmail, os.X_OK)):
            raise Refused(f"no MTA: {sendmail} is not an executable file - NOTHING WAS SENT. "
                          f"Install postfix, name the MTA in $SESSIONMAIL_SENDMAIL, or set "
                          f"SESSIONMAIL_SENDMAIL=drop to write straight into this host's "
                          f"maildirs.", EX_MTA, kind="mta")
        preflight(req, host, env, err)

    # Threading: notmuch when it is here, else one walk of the maildir, for
    # every target (send:1111-1129, :886; the accelerator amends decision 5).
    check = parent_check_on(env, err)
    targets = {i for i in (req.in_reply_to, req.supersedes) if i}
    files = find_parents(root_of(env), targets, env) if targets else {}
    if check and req.supersedes and req.supersedes not in files:
        err.append(f"{PREFIX}⚠  supersede target {req.supersedes} is NOT in the local index - "
                   f"the Supersedes: header is still written and the send proceeds, but "
                   f"threading falls back to ONE HOP. Expected for a cross-host or another "
                   f"seat's message; if the target IS local, check the id.")
    if (check and req.in_reply_to and req.in_reply_to != req.supersedes
            and req.in_reply_to not in files):
        err.append(f"{PREFIX}⚠  reply target {req.in_reply_to} is NOT in the local index - "
                   f"the In-Reply-To: header is still written and the send PROCEEDS (the parent "
                   f"may be cross-host or in a box this host cannot see), but a mistyped id "
                   f"threads onto nothing. Silence: SESSIONMAIL_PARENT_CHECK=off.")
    references = references_chain(req.in_reply_to, files.get(req.in_reply_to)) if req.in_reply_to else []

    # Mint (send:1857-1862).
    epoch = int(time.time() if now is None else now)
    message_id, date = mint(host, epoch, os.getpid() if pid is None else pid,
                            fresh_rand(epoch) if rand is None else rand)
    if req.message_id is not None:               # the caller's id; the Date stays minted
        message_id = req.message_id
    session = (sessiontools.session_id(env) or "")[:8]

    # Build first, exactly as before: bare addresses, Bcc included (`sendmail -t`
    # strips it), no signature yet.
    wire = build(req, body, host=host, date=date, message_id=message_id,
                 references=references, session=session, signature=None,
                 page=page, boundary=mime_boundary)

    # Sign (send:2126-2173; 7(b): a send never fails for want of a key,
    # unless the seat asked for $SESSIONMAIL_REQUIRE_SIGNATURE).
    # v2: one signature over that exact wire, inserted where `build`
    # puts it; no other byte moves.
    signature = None
    if signing:
        signature = sign_wire(wire, env, err)
        if signature is None and required:       # refused, never sent unsigned
            raise Refused(f"${REQUIRE_SIGNATURE} is set and signing FAILED (keyholder dead? "
                          f"$CLAUDE_SESSION_AGENT_SOCK answered no signature for "
                          f"$CLAUDE_SESSION_PUB) - NOTHING WAS SENT", kind="identity")
        if signature is None:
            err.append(f"{PREFIX}⚠  keyed session but signing FAILED — sending UNSIGNED "
                       f"(keyholder dead?)")
    else:
        err.append(f"{PREFIX}⚠  sending UNSIGNED - no session key in this environment "
                   f"($CLAUDE_SESSION_AGENT_SOCK unset, or $CLAUDE_SESSION_PUB not a file); "
                   f"it delivers, and `memory run` skips an unsigned item (a signed one runs only "
                   f"if its signer passes --trust)")
    if signature:
        wire = _sig.insert_header(wire, signature)

    if dropping is not None:
        return drop_deliver(req, wire, dropping, message_id, err)

    # Hand to the MTA (send:2445-2457). The ENVELOPE sender is the address
    # alone: `-f` is an address, never a name-addr (the From
    # header may now carry `Alice <alice>`). Identical for a bare From.
    try:
        done = subprocess.run([sendmail, "-t", "-i", "-f", addr_spec(req.from_)], input=wire,
                              capture_output=True, env=dict(env))
    except OSError as exc:
        raise Refused(f"the MTA could not start: {sendmail} ({exc.strerror}) - NOTHING WAS SENT",
                      EX_MTA, kind="mta")
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip()
        raise Refused(f"the MTA did not accept the message: {sendmail} exit {done.returncode}"
                      + (f": {said}" if said else ""), EX_MTA, kind="mta", sent="unknown")
    err.append(f"{PREFIX}sent via smtp (postfix) {ARROW} {lookup_addr(req.to, host)}")
    return message_id


def send_request(req: Request, env: Mapping[str, str], **mint: int | str | None) -> Receipt:
    """A request whose body is still a FILE (`req.body_file`, or `--no-body`):
    what both command-line roads hold - `run` below, and the door's `--json`.
    Validated HERE as well as in send(), so a bad id is still reported before
    a bad body file, as it always was (the order tests pin it)."""
    validate(req)
    return send(req, read_body(req), env, **mint)  # type: ignore[arg-type]


def run(argv: list[str], env: Mapping[str, str], *, now: int | None = None,
        pid: int | None = None, rand: int | None = None,
        boundary: str | None = None) -> tuple[int, str, str]:
    """Send. stdout is the Message-ID and nothing else; every report is on
    stderr; exit 0, 2, 3 or 4 and never 1 (the spec's exit-code requirement)."""
    try:
        receipt = send_request(parse_argv(list(argv)), env, now=now, pid=pid, rand=rand,
                               boundary=boundary)
    except Refused as r:
        return r.code, "", "\n".join([*r.notices, PREFIX + str(r)]) + "\n"
    return EX_OK, receipt.message_id + "\n", "\n".join(receipt.notices) + "\n"
