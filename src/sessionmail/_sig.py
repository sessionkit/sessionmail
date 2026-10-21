"""Signature v2: one signature over the whole mail.

The ONE canonicaliser. The sender imports it; the verifier
(`mhs/session-mail-verify`) loads this file BY PATH from its own install,
because the verifier runs PATH's `python3`, which cannot import the package
So this module is stdlib only and Python 3.9-safe:
`from __future__ import annotations`, no `match`, no 3.10+ calls, and it
must not import anything from `sessionmail`.

The canonical text a v2 signature covers:

    x-session-signature: v=2; a=sshsig; n=NS; h=H; p=P      (L0)
    <name>: <canonical value>                                 (one per h= name,
    ...                                                        per field, wire order)

- every header named in `h=` (an absent one signs as `name: `);
- address headers by their tokens, with qualify-if-bare;
- `p=`: one SHA-256 per MIME leaf, over its type, charset and CTE-decoded
  bytes.

Normative text: the task's design.md. Golden vectors:
`tests/fixtures/sig_v2/`. This module was built from the design's reference
implementation (`design/sig_ref.py`) with its canonical output unchanged.
"""
from __future__ import annotations

import base64
import binascii
import email
import email.errors
import email.policy
import hashlib
import os
import re
import shutil
import subprocess

VERSION = "2"
ALG = "sshsig"
#: The headers every v2 `h=` must name. A verifier refuses an h= without them.
FLOOR = ("from", "to", "cc", "subject", "date", "message-id", "in-reply-to",
         "references", "supersedes", "mime-version", "content-type", "x-session-from")
#: Never in h=: Q5 (x-original-subject), circular
#: (x-session-signature), never on the wire (bcc: `sendmail -t` strips it), and
#: re-encoding-fragile and covered by p= (content-transfer-encoding).
NEVER = ("x-original-subject", "x-session-signature", "bcc", "content-transfer-encoding")
#: Claim headers are required in h= WHEN PRESENT, or a relay could add one
#: unsigned. `atom-*` is the claim headers' prefix; type, id, project and parent are openspec change
#: atoms-by-headers' unprefixed names. A new atom header name joins CLAIM_NAMES in the same
#: change that adds it.
CLAIM_PREFIXES = ("atom-",)
CLAIM_NAMES = ("type", "id", "project", "parent")
#: RFC 5322 3.6.8 field name (the same rule as `_sender.FIELD_NAME`).
FIELD_NAME = re.compile(rb"[!-9;-~]+")
#: The address headers postfix's cleanup rewrites (cleanup_message.c's sender and recipient
#: header classes). In h= they get the STRUCTURAL canon, not collapse.
ADDRESS_FIELDS = ("from", "to", "cc", "sender", "reply-to", "resent-from", "resent-to",
                  "resent-cc", "resent-sender")
#: RFC 5322 3.2.3 specials, less the two that open a token ('"' and '(').
SPECIALS = b"<>[]:;@\\,."
WS = b" \t\n\r\x0b\x0c"
#: The specials that make a wire gap signed (`join`).
BACKSLASH, DOT = ("s", b"\\"), ("s", b".")
#: Where postconf may be when PATH lacks it (the macOS system postfix; Homebrew).
POSTCONF_FALLBACK = ("/usr/sbin/postconf", "/opt/homebrew/sbin/postconf")
#: The tags a v2 header must carry.
REQUIRED_TAGS = ("v", "a", "n", "h", "p", "b")
#: One p= entry: standard base64 of a SHA-256, padded - 44 characters.
DIGEST = re.compile(r"[A-Za-z0-9+/]{43}=")
WIDTH = 78
B_CHUNK = 72


class Malformed(ValueError):
    """A signature header that cannot be read."""


# ── input ──────────────────────────────────────────────────

def lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def header_block(raw: bytes) -> bytes:
    raw = lf(raw)
    end = raw.find(b"\n\n")
    return raw if end == -1 else raw[:end + 1]


def fields(raw: bytes) -> list[tuple[str, bytes]]:
    """(lowercased name, raw value bytes, continuation lines joined) in wire order. A line
    that is not `NAME:` (an mbox `From ` line, say) is skipped with its continuations."""
    out: list[list] = []
    cur: list | None = None
    for line in header_block(raw).split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if cur is not None:
                cur[1] += b"\n" + line
            continue
        cur = None
        name, colon, value = line.partition(b":")
        name = name.rstrip(b" \t")
        if colon and FIELD_NAME.fullmatch(name):
            cur = [name.decode("ascii").lower(), value]
            out.append(cur)
    return [(n, v) for n, v in out]


def collapse(value: bytes) -> bytes:
    return b" ".join(value.split())


def is_claim(name: str) -> bool:
    return name in CLAIM_NAMES or name.startswith(CLAIM_PREFIXES)


# ── parts and p= ────────────────────────────────────────────────

def leaves(raw: bytes) -> list:
    msg = email.message_from_bytes(lf(raw))
    return [p for p in msg.walk() if not p.is_multipart()]


def leaf_canon(part) -> bytes:
    ctype = part.get_content_type()
    cs = part.get_content_charset()
    head = f"content-type: {ctype}" + (f"; charset={cs}" if cs else "") + "\n\n"
    data = lf(part.get_payload(decode=True) or b"")
    return head.encode("ascii") + data.rstrip(b"\n") + b"\n"


def leaf_data(part) -> bytes:
    """A leaf's bytes as p= hashes them: CTE-decoded, LF line ends. The SAME
    expression as leaf_canon's `data`, said twice on purpose: leaf_canon's source is pinned
    byte for byte, and tests/test_signed_text.py checks the two agree."""
    return lf(part.get_payload(decode=True) or b"")


# ── the body a GOOD vouches for ─────────────────────────────────────
# What memory shows and runs for a trusted item is the text its signature covers: the SAME
# bytes and the SAME parse the verifier hashed (leaves(), leaf_data), never a second parser
# (notmuch's GMime JSON was one: a parse differential let a keyless edit change what a
# GOOD item showed or ran). notmuch stays the INDEX; this is the BODY.

#: Read as UTF-8, whatever the label says: the v1 shape allows only these, and v2's
#: sender writes utf-8 only (the wake shelf's rule, session-mail-wake @ 9723d42). Bytes
#: that are not UTF-8 read as U+FFFD (errors="replace"), as the shelf and `message read`
#: already show them, never silently dropped.
UTF8_LABELS = (None, "utf-8", "utf8", "us-ascii")
#: The `why` of a signed mail with no text/plain leaf at all: a legitimate state, shown
#: as a notice and never run. Every other `why` is a refusal.
NO_TEXT = "no-text-part"

# ── what a reader may SAY about a letter that verifies ───────────────────────────────
# Plain-mode verify checks a mail against the key the mail CARRIES
# (mhs/session-mail-verify: "SELF-ATTRIBUTING"), so a letter From the owner signed by
# any key at all passes it. Nothing yet pins which key is the owner's (key pinning is
# an open design). Until it is, no reader calls such a letter "sig OK". Here, once, for both
# readers that show a letter to self (mhs/session-mail-wake's shelf and mhs/mhs-thread's
# --boot and cat), which load this file by path.

#: The badge on a letter whose signature verifies, From the owner, signer unknown.
UNPINNED = "signature verifies · signer NOT pinned"

#: THE SWITCH between the two interim readings. False (F, FENCE, the ruled default):
#: the letter is shown under UNPINNED, exit 0. True (R, REFUSE): it is refused like an
#: unsigned one, body withheld, exit 3; since no signer is pinned, that refuses EVERY
#: letter to self, the owner's own included, until key pinning lands.
REFUSE_UNPINNED = False

#: The refusal's reason under R, said by both readers.
UNPINNED_REFUSED = "the signer is NOT pinned (no key is known to be the owner's yet)"


def _first_text(parts: list):
    """The index of the FIRST text/plain leaf, or None. The first only: v1 signs the
    first part of an alternative, the v2 sender writes the body first, and joining every
    text/plain leaf is what let a relay append a part that `memory run` then ran."""
    return next((i for i, p in enumerate(parts) if p.get_content_type() == "text/plain"),
                None)


def _read_leaf(part) -> tuple:
    """(text, "") from a leaf's hashed bytes, in its signed charset; (None, why) when
    the charset is unknown here."""
    cs = part.get_content_charset()
    try:
        return leaf_data(part).decode("utf-8" if cs in UTF8_LABELS else cs, "replace"), ""
    except LookupError:
        return None, "charset %r is unknown here" % cs


def signed_text(raw: bytes, check: bool = True) -> tuple:
    """(text, "") where text is the FIRST text/plain leaf of leaves(raw), its bytes exactly
    as p= hashes them (leaf_data), read in its signed charset. Else (None, why).

    check=True (every trusted reader): for v2 the chosen leaf must ALSO hash, here, to its
    p= entry, so an interpreter or a file that differs from the verifier's refuses, never
    shows. For v1 the mail must be in the v1 shape here too (v1_defects empty), where the
    v1 signed body IS this leaf. why == NO_TEXT: signed, but no text/plain leaf at all.

    check=False skips every signature check (the leaf reading alone): ONLY for a caller
    whose trust came from a NAMED verifier (SESSION_MAIL_VERIFY), which mhs-thread never
    second-guesses. It never raises: a mail that cannot be read is (None, why)."""
    try:
        return _signed_text(raw, check)
    except Exception as e:  # noqa: BLE001 - fail closed: a reading that cannot run shows nothing
        return None, "the mail cannot be read here (%s)" % type(e).__name__


def _signed_text(raw: bytes, check: bool) -> tuple:
    if not check:
        parts = leaves(raw)
        idx = _first_text(parts)
        return (None, NO_TEXT) if idx is None else _read_leaf(parts[idx])
    sv = signature_value(raw)
    if sv is None:
        return None, "unsigned"
    try:
        tags = parse_header(sv)
    except Malformed as e:
        return None, "signature header: %s" % e
    parts = leaves(raw)
    idx = _first_text(parts)
    v = tags.get("v", "1")
    if v == "2":
        try:
            p = parse_v2(tags)[2]
        except Malformed as e:
            return None, "signature header: %s" % e
        if len(p) != len(parts):
            return None, "%d parts where %d were signed" % (len(parts), len(p))
        if idx is None:
            return None, NO_TEXT
        got = base64.b64encode(hashlib.sha256(leaf_canon(parts[idx])).digest()).decode("ascii")
        if got != p[idx]:
            return None, "part %d does not hash to its p= entry here" % (idx + 1)
    elif v == "1":
        if v1_defects(raw):
            return None, "not in the v1 shape"
        if idx is None:
            return None, NO_TEXT
    else:
        return None, "unsupported v=%s" % v
    return _read_leaf(parts[idx])


def digests(raw: bytes) -> list[str]:
    return [base64.b64encode(hashlib.sha256(leaf_canon(p)).digest()).decode("ascii")
            for p in leaves(raw)]


def leaf_types(raw: bytes) -> list[str]:
    """Each leaf's content type, in leaf order: what the verifier names per part."""
    return [p.get_content_type() for p in leaves(raw)]


# ── no unsigned disposition ─────────────────────────────────────────
# No signature covers a part's Content-Disposition: it is not in the sender's h= (h_for names
# only what the sender wrote, and it writes none), and leaf_canon signs only a leaf's type,
# charset and bytes. A reader that obeys it (Python's get_body skips an `attachment`; a MUA
# shows one as an attachment) moves signed text out of view with no key. Neither sender
# (v2 at 72f2805, v1 at 91f6d82) has ever written one, so the VERIFIER refuses any.
# Nothing here changes what p= covers; digests() never calls it.

def dispo_defects(raw: bytes) -> list[tuple[str, str]]:
    """Every part (the top message included) that carries a Content-Disposition field, as
    (where, why), with `cte_defects`' labels. Empty for every mail our senders write. The
    VERIFIER's check: pure, and never called by `digests` or `leaf_canon`. By
    field NAME, whatever its value: `inline`, an unknown type and a bare `filename=` go
    too, because no signature covers any of them and readers differ on each."""
    out: list[tuple[str, str]] = []
    n = 0
    for part in email.message_from_bytes(lf(raw), policy=email.policy.compat32).walk():
        leaf = not part.is_multipart()
        if leaf:
            n += 1
        k = sum(1 for name, _ in part.raw_items() if name.lower() == "content-disposition")
        if k:
            where = f"part {n}" if leaf else f"container {part.get_content_type()}"
            out.append((where, f"{k} Content-Disposition field{'s' if k > 1 else ''}, "
                               "which no signature covers"))
    return out


# ── strict transfer encoding ────────────────────────────────────────
# p= covers each leaf's CTE-DECODED bytes, and Content-Transfer-Encoding is in NEVER, so a
# relay may re-encode a leaf and it still verifies (an 8BITMIME downgrade). That is only
# safe when every decoder reads the encoding ONE way. A loose encoding (a stray '=', a
# short base64 quantum, two CTE fields, a folded or commented value) decodes one way in
# Python, which p= hashed, and another in a GMime reader such as notmuch. The VERIFIER
# refuses such a leaf; nothing here changes what p= covers, and digests() never calls it.

#: The Content-Transfer-Encoding tokens a leaf may name (RFC 2045 6.1), compared lowercased.
CTE_TOKENS = ("7bit", "8bit", "binary", "quoted-printable", "base64")
#: The only ones a multipart or message/* container may name (RFC 2046 5.1, 5.2).
CTE_CONTAINER = ("7bit", "8bit", "binary")
#: One whole CTE field as the wire wrote it: one line, no fold, no comment, no quotes, no
#: whitespace after the token. Leading whitespace is allowed: every parser strips it.
CTE_FIELD = re.compile(r"content-transfer-encoding:[ \t]*([A-Za-z0-9-]+)\n?", re.I)
#: A CTE field among the lines the email package moved out of a header block into the body:
#: whitespace before its colon, or any spelling after a header line it could not read.
OBS_CTE = re.compile(rb"content-transfer-encoding[ \t]*:", re.I)
#: A CTE field that starts after a bare CR. The email package ends a line at a bare CR and
#: may read the field; GMime reads the CR inside the line before and never reads it as the
#: CTE (measured). In a BODY it is body text to both, so only a match the email package
#: reads as a header field counts (`_cr_cte_is_a_field`).
CR_CTE = re.compile(rb"\rcontent-transfer-encoding[ \t]*:", re.I)
#: What `_cr_cte_is_a_field` renames a CR_CTE match to, with 8 digits after it: a legal
#: field name as long as the one it replaces, so only the NAME changes.
CR_PROBE = b"x-sm819-cr-probe-"
#: RFC 2045 6.7, over lf() bytes: every '=' is '=' HEX HEX or a soft break ('=', optional
#: SP/TAB, then LF or the end), and no CR is left.
QP_BAD = re.compile(rb"=(?![0-9A-Fa-f]{2}|[ \t]*(?:\n|\Z))|\r")
#: RFC 2045 6.8, over lf() bytes: the alphabet and SP/TAB/LF, '=' only at the very end.
B64_OK = re.compile(rb"[A-Za-z0-9+/ \t\n]*=?=?[ \t\n]*")


class _Source(str):
    """A header value that also keeps the field's source lines, exactly as the wire wrote
    them (the email package hands them to `header_source_parse` unchanged)."""

    source = ""


class _KeepSource(email.policy.Compat32):
    """compat32, except each stored header value also carries its source lines, so a CTE
    field is read from the BYTES. 3.13 strips a fold that 3.9 keeps; this sees both."""

    def header_source_parse(self, sourcelines):
        name, value = super().header_source_parse(sourcelines)
        kept = _Source(value)
        kept.source = "".join(sourcelines)
        return name, kept


def _encoded(part) -> bytes:
    """A leaf's body still ENCODED, as bytes, the way Message.get_payload turns it back."""
    body = part.get_payload()
    if not isinstance(body, str):
        return b""
    try:
        return body.encode("ascii", "surrogateescape")
    except UnicodeError:
        return body.encode("raw-unicode-escape")


def _obs_cte(part) -> bool:
    """True when the part's header block holds a CTE field that the email package did not
    read as one. The parser ends a header block at the first line it cannot read as a
    field (`Content-Transfer-Encoding : x`, whitespace before the colon, RFC 5322
    obs-optional; a line with no colon; a space or a non-ASCII byte in a name) and keeps
    it, and every line to the first blank one, as BODY. A GMime reader skips or accepts
    that line and reads a CTE field after it as the field (measured). Only an EMPTY line
    ends a header block: GMime reads a line of spaces or tabs as a fold and reads on."""
    if not any(isinstance(d, email.errors.MissingHeaderBodySeparatorDefect)
               for d in part.defects):
        return False
    for line in _encoded(part).split(b"\n"):
        if not line:
            return False
        if OBS_CTE.match(line):
            return True
    return False


def _cr_probed(raw: bytes) -> bytes:
    """`raw` with each CR_CTE match's field name renamed to a numbered CR_PROBE of the same
    length. Only those names change: the parse's shape depends on Content-Type and the
    boundaries, never on a CTE field's name."""
    out = bytearray(raw)
    size = len(b"content-transfer-encoding")
    for i, m in enumerate(CR_CTE.finditer(raw)):
        out[m.start() + 1:m.start() + 1 + size] = CR_PROBE + b"%08d" % i
    return bytes(out)


def _cr_cte_is_a_field(raw: bytes) -> bool:
    """True when a CTE field that starts after a bare CR is read by the email package as a
    header FIELD, in any header block it parses: the top one, a part's, an inner message's.
    The blocks are the ones the parser reads, as R2 reads them, so a bare-CR boundary or a
    dropped line before the field is followed as Python follows it. A match in a body is
    body text to both readers (measured) and does not count. A probe among any part's
    fields, after `_cr_probed`, is a CTE field Python reads and GMime does not. Only the
    probes the UN-probed parse lacks count: an honest mail may carry a field that already
    bears a probe name, and that field is not a CTE field."""
    if not CR_CTE.search(raw):
        return False
    return _probe_fields(_cr_probed(raw)) > _probe_fields(raw)


def _probe_fields(raw: bytes) -> int:
    """How many fields the email package reads with a CR_PROBE name, in every header block."""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    stem = CR_PROBE.decode()
    return sum(name.lower().startswith(stem)
               for part in msg.walk() for name, _ in part.raw_items())


def _cte_field(part) -> tuple[str, str]:
    """(token, why): the part's one CTE token lowercased ("7bit" when there is no field),
    or ("", why) when the field cannot be read one way."""
    if _obs_cte(part):
        return "", "a Content-Transfer-Encoding field this parser reads as body"
    found = [v for n, v in part.raw_items() if n.lower() == "content-transfer-encoding"]
    if len(found) > 1:
        return "", f"{len(found)} Content-Transfer-Encoding fields"
    if not found:
        return "7bit", ""
    source = getattr(found[0], "source", "")
    m = CTE_FIELD.fullmatch(source)
    if not source.isascii() or not m:
        return "", f"Content-Transfer-Encoding is not one plain token: {source.rstrip()!r}"
    token = m.group(1).lower()
    if token not in CTE_TOKENS:
        return "", f"unknown Content-Transfer-Encoding {m.group(1)!r}"
    return token, ""


def _body_why(token: str, body: bytes) -> str:
    if token == "quoted-printable" and QP_BAD.search(body):
        return "the quoted-printable body is not strict (RFC 2045 6.7)"
    if token == "base64":
        chars = re.sub(rb"[ \t\n]", b"", body)
        if not B64_OK.fullmatch(body) or len(chars) % 4:
            return "the base64 body is not strict (RFC 2045 6.8)"
    return ""


def cte_defects(raw: bytes) -> list[tuple[str, str]]:
    """The parts whose transfer encoding may decode differently in another reader, as
    (where, why). `where` is "part N", N counting leaves as `leaves()` does, or
    "container <type>". Empty when every part is strict. The VERIFIER's check:
    pure, and never called by `digests` or `leaf_canon`.

    - a leaf: at most one CTE field, one plain token (R2); its body obeys that encoding's
      strict grammar on the encoded bytes (R3); decoding it adds no defect (R1);
    - a multipart or message/* container: the same R2, and only 7bit, 8bit or binary;
    - the mail: no CTE field that the parser reads as a header field starts after a bare
      CR ("the mail"); a body's bare CR is not looked at."""
    raw = lf(raw)
    msg = email.message_from_bytes(raw, policy=_KeepSource())
    out: list[tuple[str, str]] = []
    if _cr_cte_is_a_field(raw):
        out.append(("the mail", "a Content-Transfer-Encoding field after a bare CR"))
    n = 0
    for part in msg.walk():
        container = part.get_content_maintype() in ("multipart", "message")
        leaf = not part.is_multipart()
        if leaf:
            n += 1
        where = f"part {n}" if leaf else f"container {part.get_content_type()}"
        token, why = _cte_field(part)
        if not why and container and token not in CTE_CONTAINER:
            why = f"a {part.get_content_type()} part declares {token}"
        if not why and leaf:
            why = _body_why(token, lf(_encoded(part)))
        if not why and leaf:
            before = len(part.defects)
            part.get_payload(decode=True)
            if len(part.defects) != before:
                why = "decoding it reports " + ", ".join(
                    type(d).__name__ for d in part.defects[before:])
        if why:
            out.append((where, why))
    return out


# ── one MIME reading ────────────────────────────────────────────────
# p= signs each leaf's type and charset as the email package reads them, and no h= covers a
# multipart part's own header block. Two readings differ there with no key: the email
# package takes a part's FIRST Content-Type field and GMime (notmuch) its LAST; the email
# package ends a line at a bare CR and GMime does not. The VERIFIER refuses a mail that the
# two may split differently. Nothing here changes what p= covers; digests() never calls it.

def _mime_parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.compat32)


def _mime_where(msg):
    """(where, part) for every part, labelled as `cte_defects` labels them."""
    n = 0
    for part in msg.walk():
        if part.is_multipart():
            yield f"container {part.get_content_type()}", part
        else:
            n += 1
            yield f"part {n}", part


def _mime_shape(raw: bytes) -> list:
    """What the email package reads of `raw`: for each part, whether it is a container, its
    type, its field names, and a leaf's still-encoded payload with each bare CR as a space."""
    out = []
    for part in _mime_parse(raw).walk():
        body = None
        if not part.is_multipart():
            got = part.get_payload()
            body = got.replace("\r", " ") if isinstance(got, str) else repr(got)
        out.append((part.is_multipart(), part.get_content_type(),
                    [name.lower() for name, _ in part.raw_items()], body))
    return out


def mime_defects(raw: bytes) -> list[tuple[str, str]]:
    """The places two MIME readers may split this mail differently, as (where, why), with
    `cte_defects`' labels. Empty when the mail reads one way. The VERIFIER's check:
    pure, and never called by `digests` or `leaf_canon`.

    - R1: a part, a container or the top message has more than one Content-Type field;
    - R2p: a bare CR changes what the email package reads. The parse of lf(raw) and the
      parse with every bare CR read as a space must agree on the part tree, every part's
      field names and every leaf's payload ("the mail"). A CR inside a body line changes
      neither, so an honest body may hold one;
    - R3: a part, a container or the top message whose header block the email package
      ends early, at a line it cannot read as a field (MissingHeaderBodySeparatorDefect),
      and hashes from there as body. GMime skips that line and reads on to the blank line,
      so it hides what p= signed. Deleting the blank line after a part's headers does this
      with no key."""
    raw = lf(raw)
    out: list[tuple[str, str]] = []
    for where, part in _mime_where(_mime_parse(raw)):
        k = sum(1 for name, _ in part.raw_items() if name.lower() == "content-type")
        if k > 1:
            out.append((where, f"{k} Content-Type fields"))
        if any(isinstance(d, email.errors.MissingHeaderBodySeparatorDefect)
               for d in part.defects):
            out.append((where, "a header block this parser ends at a line it cannot read"))
    if b"\r" in raw and _mime_shape(raw) != _mime_shape(raw.replace(b"\r", b" ")):
        out.append(("the mail", "a bare CR changes the MIME structure this parser reads"))
    return out


# ── h= ────────────────────────────────────────────────────────────

def h_for(raw: bytes) -> list[str]:
    """The SENDER's h=: the floor, then every other header it wrote, in wire order, less
    NEVER. A new claim header is signed the day it is written, with no v3."""
    h = list(FLOOR)
    for name, _ in fields(raw):
        if name not in h and name not in NEVER:
            h.append(name)
    return h


def below_floor(raw: bytes, h: list[str]) -> list[str]:
    """What the VERIFIER refuses before any crypto: the floor names h= lacks, in
    FLOOR order, then the claim headers the mail carries that h= does not name, sorted.
    Empty when h= is acceptable."""
    missing = [n for n in FLOOR if n not in h]
    claims = sorted({n for n, _ in fields(raw) if is_claim(n)} - set(h))
    return missing + claims


# ── addresses ───────────────────────────────────────────────────

def origin_of(env=None) -> str:
    """The domain postfix appends to a bare address: $SESSIONMAIL_ORIGIN, else
    the settings file's `postfix.origin`, else `postconf -xh myorigin` (PATH,
    then POSTCONF_FALLBACK), else "" (leave bare). Never raises.

    The settings file is read only when this module is the package's. The
    verifier loads it BY PATH, as a module with no package and on purpose
    (`session-mail-verify`'s `load_sig`), where the file layer cannot be
    imported: there it reads the variable, then postconf. A bundled script
    still sees the file's value, as the variable: `_mhs.child_env` pins
    $SESSIONMAIL_ORIGIN from the file for every child it starts."""
    env = os.environ if env is None else env
    if env.get("SESSIONMAIL_ORIGIN"):
        return env["SESSIONMAIL_ORIGIN"].strip()
    if __package__:                      # loaded by path (the verifier): no file layer
        from . import _settings          # here, not at import: `_sig` stays light
        given = _settings.file_text("postfix.origin", env)
        if given:
            return given
    tool = shutil.which("postconf", path=env.get("PATH", ""))
    for cand in ([tool] if tool else []) + [c for c in POSTCONF_FALLBACK if os.access(c, os.X_OK)]:
        try:
            done = subprocess.run([cand, "-xh", "myorigin"], capture_output=True, text=True,
                                  env=dict(env))   # the caller's whole world, as every child
        except OSError:
            continue
        words = done.stdout.split()
        if done.returncode == 0 and len(words) == 1:
            return words[0]
    return ""


def unfold(value: bytes) -> bytes:
    """RFC 5322 2.2.3 unfolding: drop each line break that a SP or HT follows. A quoted
    string folded across lines then reads as one line, as a relay that unfolds it would
    leave it."""
    return re.sub(rb"\r?\n(?=[ \t])", b"", value)


def tokens(value: bytes) -> list[tuple[str, bytes]]:
    """RFC 5322 lexical tokens over the UNFOLDED value: ('w', atom) ('q', quoted-string,
    verbatim with its quotes) ('c', comment, as "(" + collapsed content + ")") ('s', one
    special) and ('ws', b" ") for each run of whitespace. `join` decides which runs are
    signed."""
    value = unfold(value)
    out: list[tuple[str, bytes]] = []
    i, n = 0, len(value)
    while i < n:
        c = value[i:i + 1]
        if c in WS:
            while i < n and value[i:i + 1] in WS:
                i += 1
            out.append(("ws", b" "))
        elif c == b'"':
            j = i + 1
            while j < n and value[j:j + 1] != b'"':
                j += 2 if value[j:j + 1] == b"\\" else 1
            out.append(("q", value[i:j + 1]))
            i = j + 1
        elif c == b"(":
            depth, j = 0, i
            while j < n:
                d = value[j:j + 1]
                if d == b"\\":
                    j += 2
                    continue
                depth += (d == b"(") - (d == b")")
                j += 1
                if depth == 0:
                    break
            # An unterminated comment keeps every byte to the end and is NOT given a ')'
            # (found in review): `[i+1:j-1]` dropped its last byte unsigned.
            closed = depth == 0
            body = value[i + 1:j - 1] if closed else value[i + 1:j]
            text = collapse(body)
            # A closed comment whose text ends in a backslash keeps its trailing gap:
            # `(a \\ )` would otherwise sign as `(a \\)`, the unterminated comment.
            if closed and text.endswith(b"\\") and body[-1:] in WS:
                text += b" "
            out.append(("c", b"(" + text + (b")" if closed else b"")))
            i = j
        elif c in SPECIALS:
            out.append(("s", c))
            i += 1
        else:
            j = i
            while j < n and value[j:j + 1] not in WS and value[j:j + 1] not in SPECIALS + b'"(':
                j += 1
            out.append(("w", value[i:j]))
            i = j
    return out


def join(toks: list[tuple[str, bytes]], dots: bool = True, strict: bool = False,
         glue: bool = False) -> bytes:
    """One space between two words (atom, quoted-string, comment); none beside a special.
    That is how postfix re-externalises a phrase (measured: `J. Smith` arrives `J.Smith`).

    A wire gap is kept, as one space, in two cases:
    - beside a '.' special, when `dots` is set. getaddresses reads `. x` as `.` where `.x`
      reads `.x`, and `..b` and `.. b` differently. The callers clear `dots` for a display
      phrase (the ruled `J. Smith` == `J.Smith`) and inside a closed <...>, where a gap beside
      a '.' changes no reading;
    - everywhere, trailing gap included, when `strict` is set: an item holding a backslash
      special (fail closed: getaddresses reads `m\\ x@y` and `m\\x@y` differently, the
      sender never writes one, and postfix re-spacing one reads BAD).

    And one word gap is signed by its ABSENCE, when `glue` is set (an addr-spec): a
    word glued to a quoted string takes no space. getaddresses reads `x"y"@z`
    and `x "y"@z` as two addresses; in a display phrase they read the same, so a display
    is never glued. The sender never writes one; postfix re-spacing one reads BAD."""
    out, prev, gap = b"", None, False
    for kind, t in toks:
        if kind == "ws":
            gap = prev is not None
            continue
        if prev is not None:
            words = prev[0] != "s" and kind != "s"
            glued = glue and not gap and "q" in (prev[0], kind)
            if (words and not glued) or (gap and (strict or (dots and DOT in (prev, (kind, t))))):
                out += b" "
        out += t
        prev, gap = (kind, t), False
    return out + b" " if strict and gap else out


def _is_group(toks: list[tuple[str, bytes]]) -> bool:
    return ("s", b":") in toks and ("s", b"<") not in toks


def _items(toks: list[tuple[str, bytes]]) -> list[list[tuple[str, bytes]]]:
    """Split at each ',' outside <...>. An empty item (a stray ',') is kept."""
    items: list[list[tuple[str, bytes]]] = []
    cur: list[tuple[str, bytes]] = []
    angle = 0
    for kind, t in toks:
        if kind == "s" and t == b"<":
            angle += 1
        elif kind == "s" and t == b">":
            angle = max(angle - 1, 0)
        if kind == "s" and t == b"," and angle == 0:
            items.append(cur)
            cur = []
        else:
            cur.append((kind, t))
    items.append(cur)
    return items


def _split_item(item: list[tuple[str, bytes]]):
    """(display, spec, after, angled, closed) for one address item.
    With an angle-addr: the display is the tokens before the LAST '<', the addr-spec runs to
    the next '>' (to the item's end when there is none), and every token after the '>' stays.
    Without one: the addr-spec is the non-comment tokens, and the comments follow."""
    opens = [i for i, (k, t) in enumerate(item) if k == "s" and t == b"<"]
    if opens:
        o = opens[-1]
        close = next((i for i in range(o + 1, len(item)) if item[i] == ("s", b">")), None)
        closed = close is not None
        close = len(item) if close is None else close
        return item[:o], item[o + 1:close], item[close + 1:], True, closed
    if BACKSLASH in item:          # strict (see `join`): its comments stay in place
        return [], item, [], False, True
    return ([], [(k, t) for k, t in item if k != "c"], [(k, t) for k, t in item if k == "c"],
            False, True)


def _is_bare(spec: list[tuple[str, bytes]]) -> bool:
    """An addr-spec postfix would qualify: not empty, and no '@' special in it."""
    return bool(join(spec)) and ("s", b"@") not in spec


def canon_address_list(value: bytes, origin: str) -> bytes:
    """The structural canon of an address header: per address, the display
    phrase by `join`, the addr-spec by `join` (its comments in place) and qualified if bare,
    every other token kept; addresses (empty ones too) joined by ", ". Group syntax (a
    top-level ':' with no '<') is not split: the whole value is `join`ed, unqualified."""
    toks = tokens(value)
    if _is_group(toks):
        return join(toks, strict=BACKSLASH in toks)
    out = []
    for item in _items(toks):
        # Every wire byte that changes how the field reads is signed: a comment inside
        # <...>, a space inside the addr-spec, an address after the
        # '>', a missing '>' and an empty item each canonicalise differently. A bare
        # (no-angle) address moves its comments to the end, exactly as postfix does
        # (measured: `x(c2)` arrives `x@origin (c2)`).
        display, spec, after, angled, closed = _split_item(item)
        strict = BACKSLASH in item
        addr = join(spec, dots=not (angled and closed), strict=strict, glue=True)
        if origin and _is_bare(spec):
            addr += b"@" + origin.encode("ascii")
        text = join(display, dots=False, strict=strict)
        text += (b" " if text else b"") + (b"<" + addr + (b">" if closed else b"") if angled else addr)
        rest = join(after, strict=strict)
        out.append(text + (b" " + rest if rest else b""))
    return b", ".join(out)


def has_bare_address(raw: bytes, h: list[str]) -> bool:
    """True when an address header named in h= still holds a bare address: a captured wire,
    never through postfix. Only then does a verifier need ORIGIN; delivered
    mail never does, because postfix qualified it."""
    for name, value in fields(raw):
        if name not in ADDRESS_FIELDS or name not in h:
            continue
        toks = tokens(value)
        if _is_group(toks):
            continue
        if any(_is_bare(_split_item(item)[1]) for item in _items(toks)):
            return True
    return False


# ── the signed text ─────────────────────────────────────────────

def canon_value(name: str, value: bytes, origin: str) -> bytes:
    return canon_address_list(value, origin) if name in ADDRESS_FIELDS else collapse(value)


def canonical(raw: bytes, ns: str, h: list[str], p: list[str], origin: str = "") -> bytes:
    fs = fields(raw)
    lines = [f"x-session-signature: v={VERSION}; a={ALG}; n={ns}; h={':'.join(h)}; "
             f"p={','.join(p)}".encode("ascii")]
    for name in h:
        values = [v for n, v in fs if n == name]
        for v in values or [b""]:
            lines.append(name.encode("ascii") + b": " + canon_value(name, v, origin))
    return b"\n".join(lines) + b"\n"


# ── the header ────────────────────────────────────────────────────

def format_header(ns: str, h: list[str], p: list[str], b64: str) -> str:
    lines = [f"X-Session-Signature: v={VERSION}; a={ALG}; n={ns};"]
    cur = " h=" + h[0]
    for name in h[1:]:
        if len(cur) + 1 + len(name) + 1 > WIDTH:
            lines.append(cur + ":")
            cur = " " + name
        else:
            cur += ":" + name
    lines.append(cur + ";")
    for i, d in enumerate(p):
        lines.append((" p=" if i == 0 else " ") + d + ("," if i < len(p) - 1 else ";"))
    chunks = [b64[i:i + B_CHUNK] for i in range(0, len(b64), B_CHUNK)] or [""]
    lines.append(" b=" + chunks[0])
    lines += [" " + c for c in chunks[1:]]
    return "\n".join(lines)


#: v1's signed fields, as its header names them (`_sender.SIG_FIELDS`, which tests pin equal).
V1_FIELDS = "from:to:subject:date:message-id"


def format_v1(ns: str, b64: str) -> str:
    """Today's v1 header line, kept only so tests and history can mint one: the
    sender emits v2 only."""
    return f"X-Session-Signature: v=1; a={ALG}; n={ns}; h={V1_FIELDS}; b={b64}"


def signature_value(raw: bytes) -> bytes | None:
    """The FIRST X-Session-Signature field's value, collapsed, or None."""
    for name, value in fields(raw):
        if name == "x-session-signature":
            return collapse(value)
    return None


def parse_header(value: bytes | str) -> dict[str, str]:
    """`tag=value; ...` into a dict, every value with ALL its whitespace removed.
    Unknown tags are kept here and ignored by the caller."""
    if isinstance(value, bytes):
        value = value.decode("ascii", "surrogateescape")
    tags: dict[str, str] = {}
    for seg in value.split(";"):
        if not seg.strip():
            continue
        tag, eq, val = seg.partition("=")
        tag = tag.strip()
        if not eq or not tag:
            raise Malformed(f"not a tag=value: {seg.strip()!r}")
        if tag in tags:
            raise Malformed(f"tag {tag}= appears twice")
        tags[tag] = "".join(val.split())
    return tags


def parse_v2(tags: dict[str, str]) -> tuple[str, list[str], list[str], str]:
    """(ns, h, p, b) from a v2 header's tags, or Malformed. The
    caller checks `a` first: an algorithm other than sshsig is unsupported, not malformed."""
    absent = [t for t in REQUIRED_TAGS if t not in tags]
    if absent:
        raise Malformed("v2 header lacks " + ", ".join(f"{t}=" for t in absent))
    ns, b = tags["n"], tags["b"]
    if not ns:
        raise Malformed("n= is empty")
    h = tags["h"].split(":")
    for name in h:
        if not re.fullmatch(r"[!-9;-~]+", name):
            raise Malformed(f"h= name {name!r} is not a field name")
    if len(set(h)) != len(h):
        raise Malformed("h= names a header twice")
    if "x-session-signature" in h:
        raise Malformed("h= names x-session-signature")
    p = tags["p"].split(",")
    for d in p:
        if not DIGEST.fullmatch(d):
            raise Malformed(f"p= entry {d!r} is not a 44-character base64 digest")
    if not b:
        raise Malformed("b= is empty")
    try:
        base64.b64decode(b, validate=True)
    except (binascii.Error, ValueError):
        raise Malformed("b= is not base64") from None
    return ns, h, p, b


def insert_header(wire: bytes, header: str) -> bytes:
    """The header just before the blank line, where `build` puts it; no other byte moves."""
    end = wire.find(b"\n\n")
    return wire[:end + 1] + header.encode("ascii") + b"\n" + wire[end + 1:]


# ── v1's exact shape ────────────────────────────────────────────────
# A v1 signature covers five headers and ONE body string: the raw payload of a single part,
# or of the text/plain first part of a multipart/alternative. No MIME header, no MIME tree,
# and a CR read as LF (the verifier reads v1 through universal newlines). So a relay with no
# key could add a CTE (a reader then decodes other text), re-wrap or append a part (memory
# run joins every text/plain part), re-label the charset, or turn LF into CR, and v1 still
# said GOOD. The VERIFIER now refuses every v1 mail that is not in the exact shape the v1
# senders wrote (`_sender.build` @ 91f6d82, and the bash session-mail-send before it):
#
#   single part:  Content-Type text/plain (or none), charset utf-8 / us-ascii (or none)
#   alternative:  multipart/alternative of exactly [text/plain, text/html], each as above
#   every part:   at most one CTE field, one plain token: 7bit, 8bit or binary (or none)
#   anywhere:     no bare CR, and no defect in Python's MIME parse
#
# Nothing here changes what a signature covers, and v2 never calls it.

#: The only Content-Transfer-Encoding tokens a v1 part may carry: the identity encodings.
V1_CTE = ("7bit", "8bit", "binary")
#: The only charsets a v1 leaf may name.
V1_CHARSETS = ("utf-8", "us-ascii")
#: The five headers v1 signs, in the order it signs them. The verifier's `HDRS`
#: (session-mail-verify, a lifted and pinned name) and `_root.SIGNED` are the other two
#: copies; tests pin all three equal. v1 signs the FIRST instance of each, so a mail with
#: any of them twice is refused: a reader may show the other instance.
V1_HDRS = tuple(V1_FIELDS.split(":"))
#: One whole CTE field as the wire wrote it: v2's CTE_FIELD, the same object. v1 reads
#: a part's CTE field by v2's rule (`_cte_field`, OBS_CTE included), then allows only
#: V1_CTE: one CTE rule for v1 and v2.
V1_CTE_FIELD = CTE_FIELD
_V1_TOKEN = r"[!#$%&'*+.^_`{|}~0-9A-Za-z-]+"
#: One whole Content-Type field: type/subtype, then `; name=value` params (a token or a
#: quoted string, folds allowed). No comment, no backslash, nothing after the last param.
V1_CT_FIELD = re.compile(
    r"content-type:[ \t]*(" + _V1_TOKEN + "/" + _V1_TOKEN + r")[ \t]*"
    r"((?:;[ \t\n]*" + _V1_TOKEN + r"=(?:" + _V1_TOKEN + r'|"[^"\\\n]*")[ \t\n]*)*)\n?', re.I)
_V1_PARAM = re.compile(r"(" + _V1_TOKEN + r')=(?:(' + _V1_TOKEN + r')|"([^"\\\n]*)")')


class _V1Source(str):
    """A header value that also keeps the field's source lines, as the wire wrote them."""

    source = ""


class _V1KeepSource(email.policy.Compat32):
    """compat32, but each header value also carries its source lines, so a field is read
    from the BYTES: 3.13 strips a fold that 3.9 keeps, and this sees both the same."""

    def header_source_parse(self, sourcelines):
        name, value = super().header_source_parse(sourcelines)
        kept = _V1Source(value)
        kept.source = "".join(sourcelines)
        return name, kept


def _v1_cte(part) -> str:
    """Why the part's CTE is not one identity token, or "". The field is read by v2's
    `_cte_field`: a CTE field the email package moved into the body (whitespace before its
    colon, or after a header line it cannot read), more than one field, or one that is not
    a single plain known token, is refused before the token is looked at."""
    token, why = _cte_field(part)
    if why:
        return why
    if token not in V1_CTE:
        return f"Content-Transfer-Encoding {token!r} is not 7bit, 8bit or binary"
    return ""


def _v1_type(part) -> tuple[str, dict[str, list[str]], str]:
    """(type/subtype lowercased, {param name: [values]}, why). No field is text/plain."""
    found = [v for n, v in part.raw_items() if n.lower() == "content-type"]
    if len(found) > 1:
        return "", {}, f"{len(found)} Content-Type fields"
    if not found:
        return "text/plain", {}, ""
    source = getattr(found[0], "source", "")
    m = V1_CT_FIELD.fullmatch(source)
    if not source.isascii() or not m:
        return "", {}, f"Content-Type is not a plain type and params: {source.rstrip()!r}"
    params: dict[str, list[str]] = {}
    for name, token, quoted in _V1_PARAM.findall(m.group(2)):
        params.setdefault(name.lower(), []).append(token or quoted)
    ctype = m.group(1).lower()
    if part.get_content_type() != ctype:
        return "", {}, f"Content-Type {ctype} is read as {part.get_content_type()}"
    return ctype, params, ""


def _v1_leaf(part, want: str) -> str:
    """Why a leaf is not `want` with at most one allowed charset, or ""."""
    ctype, params, why = _v1_type(part)
    if why:
        return why
    if ctype != want:
        return f"a {ctype} part where v1 has {want}"
    if set(params) - {"charset"}:
        return f"a {ctype} part with params {sorted(params)}"
    charsets = params.get("charset", [])
    if len(charsets) > 1:
        return f"a {ctype} part names {len(charsets)} charsets"
    if charsets and charsets[0].lower() not in V1_CHARSETS:
        return f"a {ctype} part in charset {charsets[0]!r}"
    return ""


def v1_defects(raw: bytes) -> list[tuple[str, str, str]]:
    """Why a v1 mail is not in the exact shape the v1 sender wrote, as (reason, where, why);
    empty when it is. `reason` is "cte-defect" (a part's transfer encoding) or "v1-shape"
    (structure, charset, bare CR, a parse defect, a Content-Disposition on any part, as
    `dispo_defects` labels it, or one of v1's five headers more than once). `where` is
    "the mail", "the container" or
    "part N", N counting leaves as `leaves()` does, or "container <type>" for a CD on a
    container. The VERIFIER's check: pure, and never called by v2."""
    out: list[tuple[str, str, str]] = []
    # V1 signs the FIRST instance of each of its five headers and never how many
    # there are, while notmuch shows the last Subject and Date and joins every To. Counted
    # by the byte parse, as X-Session-Signature is counted: it never sees fewer fields
    # than Python's email does, and it is the same on 3.9 and 3.13.
    names = [name for name, _ in fields(raw)]
    out += [("v1-shape", "the mail", f"{names.count(h)} {h} fields: v1 signs the first only")
            for h in V1_HDRS if names.count(h) > 1]
    data = lf(raw)
    bare = data.count(b"\r")
    if bare:
        out.append(("v1-shape", "the mail", f"{bare} bare CR (a line end to some readers)"))
    msg = email.message_from_bytes(data, policy=_V1KeepSource())
    n = 0
    for part in msg.walk():
        leaf = not part.is_multipart()
        if leaf:
            n += 1
        where = f"part {n}" if leaf else "the container"
        why = _v1_cte(part)
        if why:
            out.append(("cte-defect", where, why))
        if part.defects:
            out.append(("v1-shape", where, "the MIME parse reports "
                        + ", ".join(type(d).__name__ for d in part.defects)))
    ctype, params, why = _v1_type(msg)
    if why:
        out.append(("v1-shape", "the mail", why))
    elif ctype == "multipart/alternative":
        sub = msg.get_payload()
        if not msg.is_multipart() or len(sub) != 2:
            out.append(("v1-shape", "the mail", "a multipart/alternative of "
                        f"{len(sub) if isinstance(sub, list) else 0} parts, not 2"))
        else:
            if sorted(params) != ["boundary"] or len(params["boundary"]) != 1:
                out.append(("v1-shape", "the container",
                            f"multipart/alternative with params {sorted(params)}"))
            for i, (p, want) in enumerate(zip(sub, ("text/plain", "text/html"))):
                why = (f"a nested {p.get_content_type()}" if p.is_multipart()
                       else _v1_leaf(p, want))
                if why:
                    out.append(("v1-shape", f"part {i + 1}", why))
    else:
        why = _v1_leaf(msg, "text/plain")
        if why:
            out.append(("v1-shape", "part 1", why))
    # The v1 sender wrote no Content-Disposition, and v1 signs none.
    out += [("v1-shape", where, why) for where, why in dispo_defects(raw)]
    return out
