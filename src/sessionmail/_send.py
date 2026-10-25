"""The send door's own checks, and the road to the oracle.

`specs/message`: a subject is 69 ASCII characters or fewer; a subject that
could execute is refused at every door unless the seat switched the guard
off; an EOM subject may carry no body; a body is bytes. What is checked
here is checked BEFORE anything is written. Everything after - signing,
multipart rendering, threading-id lookup, the deliverability preflight, the
transport - is the package's own sender (`_sender`). The bash
`session-mail-send` sends only when a seat names it in
$SESSIONMAIL_SEND_ORACLE, for one release (openspec/changes/send-in-python
step 5).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

LIMIT_VAR = "SESSIONMAIL_SUBJECT_LIMIT"
GUARD_VAR = "SESSIONMAIL_SUBJECT_GUARD"
ORACLE_VAR = "SESSIONMAIL_SEND_ORACLE"
DEFAULT_LIMIT = 69

#: The oracle's own header flags. A `--header K=V` the oracle cannot carry
#: is refused rather than dropped on the floor.
HEADER_FLAGS = {"x-task": "--task", "x-project": "--project"}


def limit(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        return int(env.get(LIMIT_VAR) or DEFAULT_LIMIT)
    except ValueError:
        return DEFAULT_LIMIT


def guard_on(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(GUARD_VAR) or "on").strip().lower() != "off"


#: The seat's switch for the subject shortener. It can only turn it
#: OFF; ON is the host's.
SHORTEN_VAR = "SESSIONMAIL_SUBJECT_SHORTEN"


def shorten_on(env: dict[str, str] | None = None, *, no_shorten: bool = False,
               settings=None) -> tuple[bool, str]:
    """(is the shortener on for this mail?, a notice when the seat variable
    was set and ignored). The layers, highest first: the flag
    `--no-shorten` / `no_shorten` turns it OFF for one mail; the seat
    variable turns it OFF (`off`, `0`, `no`) and can do nothing else; then
    the host's locked file (`set subject-shorten on`); then the tool's
    default, OFF. SETTINGS is a `rules.Settings`, read from the locked file
    when not given - and read only when the two layers above it said nothing."""
    if no_shorten:
        return False, ""
    env = os.environ if env is None else env
    raw = (env.get(SHORTEN_VAR) or "").strip().lower()
    if raw in _OFF:
        return False, ""
    note = (f"ignoring ${SHORTEN_VAR}={raw!r} - a seat can only turn the subject "
            f"shortener off; the host switch turns it on" if raw else "")
    if settings is None:
        from . import rules   # deferred: this door module imports nothing at load
        settings = rules.read_settings()
    return bool(settings.subject_shorten), note


def shorten_max(settings=None, errors=None) -> tuple[int, list[str]]:
    """(the shortener's cap, a notice for each bad `set subject-shorten-max`
    line). The cap is the host's alone, `set subject-shorten-max N`
    in the locked file (`rules.shorten_max`, default 10). There is NO seat
    variable and no flag, so nothing below the host can raise it. A bad line
    is named LOUDLY, every send, and the default stands (it is skipped, as
    every bad locked line is). SETTINGS and ERRORS as
    `rules.read_settings_named` gives them; read from the locked file when
    not given."""
    from . import rules   # deferred: this door module imports nothing at load
    if settings is None:
        settings, errors = rules.read_settings_named()
    cap = rules.shorten_max(settings)
    bad = [e for e in errors or () if e.text.split()[:2] == [rules.SET_WORD, rules.SHORTEN_MAX]]
    return cap, [f"message send: ignoring a bad line of the locked file, {e}; "
                 f"the subject shortener's cap is {cap}" for e in bad]


def check_subject(subject: str, env: dict[str, str] | None = None) -> list[str]:
    """Every reason this subject is refused; empty means it may go."""
    out = []
    n, cap = counted(subject), limit(env)
    if n > cap:
        not_counted = ", not counting ' EOM'" if is_eom(subject) else ""
        out.append(f"refused - the subject is {n} characters{not_counted}; the limit is {cap} "
                   f"(RFC 5322's 78 minus 'Subject: '). Front-load the point, cut the rest")
    if not subject.isascii():
        bad = next(c for c in subject if not c.isascii())
        out.append(f"refused - the subject is not ASCII ({bad!r}); RFC 2047 would encode "
                   f"it and a raw grep would miss it. Put that character in the body")
    if could_execute(subject, env):
        out.append(could_execute(subject, env))
    return out


def could_execute(subject: str, env: dict[str, str] | None = None) -> str:
    """The guard's refusal (`check_subject`'s third reason), or "" when the
    guard is off or the subject is clean. Its own function so the shortener's
    road can run it on the subject AS TYPED: a cut must never carry
    a backtick past the guard into X-Original-Subject."""
    if guard_on(env) and ("`" in subject or "$(" in subject or "\n" in subject or "\r" in subject):
        return ("refused - the subject could execute (a backtick, $( or a line break); "
                "a stored subject is read back by every later reader with a shell. "
                f"Put the code span in the body; {GUARD_VAR}=off switches this off")
    return ""


def is_eom(subject: str) -> bool:
    return subject.endswith(" EOM")


def counted(subject: str) -> int:
    """The length the limit is held to: a trailing ' EOM' is not counted
    (eom-not-counted: the senders do not count those 4 characters). The shortener
    holds the marker aside the same way, so the two never disagree."""
    return len(subject) - len(" EOM") if is_eom(subject) else len(subject)


#: The name shown before the address in `From:` - `Alice <alice@host>`.
FROM_NAME_VAR = "SESSIONMAIL_FROM_NAME"
_OFF = ("off", "0", "no")
#: A phrase that needs no quoting and survives every reader: letters (any
#: script - the sender RFC 2047-encodes a non-ASCII one), digits, spaces,
#: and `. ' -`. Anything else would need quoting, and a quoted phrase is
#: where naive readers split on the wrong `<` or `@`.
_SAFE_NAME = re.compile(r"[^\W_](?:[\w .'-]*[^\W_])?")


def with_display_name(address: str, seat: str, session: str = "",
                      env: dict[str, str] | None = None) -> tuple[str, str]:
    """(the `From:` value, a note when a name was asked for and not used).

    A short name in front of the address, so a reader's mail shows a name
    instead of the full address.

    The name is the seat's, before any `+`, title-cased (alice -> Alice, a
    seat `bob` sending as `bob+repro-oracle` -> Bob), or $SESSIONMAIL_FROM_NAME;
    `off` sends the bare address, as before display names. The ADDRESS is never
    touched: the signature covers only its lowercased local-part, so the
    name is outside the signed set on both sides (sign: canon_addr; verify:
    local_part, which takes the innermost <...> first).

    NEVER on a reply answered from the session id: that is a DM, and the
    session id is used there precisely so the seat is not on the record
    (specs/message, "A reply answers from the address it was mailed at").
    Putting the seat's name in front of it would undo that.
    """
    env = os.environ if env is None else env
    raw = (env.get(FROM_NAME_VAR) or "").strip()
    if raw.lower() in _OFF:
        return address, ""
    if "<" in address or not address:
        return address, ""                     # already a name-addr, or nothing to name
    local = address.split("@", 1)[0]
    if session and session != "-" and local.lower() == session.lower():
        return address, ""                     # a DM: the session id, unnamed
    name = raw or seat.split("+", 1)[0].split("@", 1)[0].title()
    if not _SAFE_NAME.fullmatch(name):
        return address, (f"${FROM_NAME_VAR}={raw!r} is not a plain name (letters, digits, "
                         f"spaces, . ' -); From is the bare address {address!r}")
    return f"{name} <{address}>", ""


#: What send writes itself, so `--header` may not: the header, and the flag
#: or argument that sets it (only the ones the transport sets itself are
#: refused, and the refusal names the header and the flag that sets it).
#: The X-Session-* and X-Machine stamps are
#: the transport's too - X-Session-From is the sender's attribution, and a
#: caller who could write it could forge it. So is Session-Model:
#: the writing model's id is captured from the harness, never typed.
OWNED = {
    "date": "the transport; no flag", "from": "--from (your seat)",
    "sender": "the transport; no flag", "to": "the TO argument", "cc": "--cc",
    "bcc": "--bcc", "subject": "-s/--subject", "message-id": "the transport; no flag",
    "in-reply-to": "-r/--in-reply-to", "references": "-r/--in-reply-to",
    "supersedes": "--supersedes", "mime-version": "the transport; no flag",
    "received": "the transport; no flag", "return-path": "the transport; no flag",
    "x-machine": "the transport; no flag",
    "session-model": "the harness (Claude Code's transcript, or $SESSIONMAIL_MODEL); no flag",
    "x-original-subject": "-s/--subject (send writes it when it changes the subject)",
}
OWNED_PREFIXES = {"content-": "the transport (and --raw/--renderer); no flag",
                  "x-session-": "the transport; no flag"}
#: RFC 5322 3.6.8: a field name is printable US-ASCII, 33..126, but not ':'.
_FIELD_NAME = re.compile(r"[!-9;-~]+")
#: Spellings Title-Case gets wrong (the conventional spellings keep theirs).
_SPELLED = {"id": "ID", "mime": "MIME", "spf": "SPF", "dkim": "DKIM", "arc": "ARC"}


def title_case(name: str) -> str:
    """`status` -> `Status`, `x-alert-name` -> `X-Alert-Name`, `x-foo-id` -> `X-Foo-ID`."""
    return "-".join(_SPELLED.get(w.lower(), w[:1].upper() + w[1:].lower())
                    for w in name.split("-"))


def split_header(h: str) -> tuple[str, str]:
    """`Name: value` (the wire's own form) or `Name=value` (the old one).
    Whichever separator comes FIRST splits, so `Type: a=b` keeps its `=`."""
    colon, eq = h.find(":"), h.find("=")
    cut = min(i for i in (colon, eq) if i >= 0) if max(colon, eq) >= 0 else -1
    if cut < 0:
        raise ValueError(f"--header wants 'Name: value' (or Name=value), got {h!r}")
    return h[:cut].strip(), h[cut + 1:].strip()


def parse_headers(headers: tuple[str, ...]) -> tuple[list[str], list[tuple[str, str]]]:
    """(the oracle flags for X-Task/X-Project, every other header Title-Cased).

    Refused, and nothing is sent: a name that is not an RFC 5322 field name;
    a value carrying CR, LF or NUL (a line break in a value starts a NEW
    header - the injection this exists to close); a header the transport
    writes itself (OWNED)."""
    flags: list[str] = []
    extra: list[tuple[str, str]] = []
    for h in headers:
        k, v = split_header(h)
        low = k.lower()
        if not _FIELD_NAME.fullmatch(k):
            raise ValueError(f"--header name {k!r} is not a header name (printable ASCII, "
                             f"no spaces, no ':')")
        if any(c in v for c in "\r\n\0"):
            raise ValueError(f"--header {k!r}: the value carries a line break or NUL, which "
                             f"would start a header of its own. One line per header")
        owner = OWNED.get(low) or next((w for p, w in OWNED_PREFIXES.items()
                                        if low.startswith(p)), None)
        if owner:
            raise ValueError(f"--header {title_case(k)!r} is written by send itself; "
                             f"it is set by {owner}")
        if low in HEADER_FLAGS:
            flags += [HEADER_FLAGS[low], v]
        else:
            extra.append((title_case(k), v))
    return flags, extra


def headers_to_flags(headers: tuple[str, ...]) -> list[str]:
    """The oracle's flags only (kept for callers that carry nothing else)."""
    flags, extra = parse_headers(headers)
    if extra:
        raise ValueError(f"--header {extra[0][0]!r} is not carried by the bash transport")
    return flags


def spool(data: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix="message-send-", suffix=".body")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return Path(name)


def oracle(env: dict[str, str] | None = None) -> list[str] | None:
    """The bash that sends, when a seat NAMES it: $SESSIONMAIL_SEND_ORACLE, for
    one release, so a mail can be diffed against the old wire format. None
    otherwise - the package's own sender sends (one core function sends
    mail, and in a Python package it is Python). The two PATH
    fallbacks (`session-mail-send`, `session mail send`) are retired with it
    (openspec/changes/send-in-python)."""
    env = os.environ if env is None else env
    named = env.get(ORACLE_VAR)
    return [named] if named else None


def argv(*, to: str, sender: str, subject: str, body_file: str | None, no_body: bool,
         in_reply_to: str | None, supersedes: str | None, cc: tuple[str, ...],
         bcc: tuple[str, ...], extra: list[str],
         env: dict[str, str] | None = None) -> list[str]:
    """The oracle's argv. ENV picks the oracle (`oracle(env)`); None is this
    process's environment, as it always was for `message send`."""
    named = oracle(env)
    if named is None:
        raise FileNotFoundError(f"no send oracle: ${ORACLE_VAR} is not set")
    out = [*named, to, "--from", sender, "--subject", subject]
    out += ["--no-body"] if no_body else ["--body-file", str(body_file)]
    if in_reply_to:
        out += ["--in-reply-to", in_reply_to]
    if supersedes:
        out += ["--supersedes", supersedes]
    for a in cc:
        out += ["--cc", a]
    for a in bcc:
        out += ["--bcc", a]
    return out + extra


def render_on(env: dict[str, str] | None = None) -> bool:
    """`SESSIONMAIL_RENDER=off` (or 0, no): this seat never renders."""
    env = os.environ if env is None else env
    return env.get("SESSIONMAIL_RENDER", "").strip().lower() not in ("off", "0", "no")


#: Decision 9's new spellings the bash oracle cannot read, and its old ones.
RENDER_ALIASES = {"SESSIONMAIL_RENDERER": "SESSION_MAIL_RENDERER",
                  "SESSIONMAIL_HUMANS": "SESSION_MAIL_HUMANS"}


def oracle_env(renderer: str | None, env: dict[str, str] | None = None) -> dict[str, str]:
    """The oracle's environment: this one, with `--renderer PATH` and the
    new render spellings said in the bash's names."""
    out = dict(os.environ if env is None else env)
    for new, old in RENDER_ALIASES.items():
        if out.get(new) and not out.get(old):
            out[old] = out[new]
    if renderer:
        out["SESSIONMAIL_RENDERER"] = out["SESSION_MAIL_RENDERER"] = renderer
    return out


def run(command: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    done = subprocess.run(command, capture_output=True, text=True, env=env)
    return done.returncode, done.stdout, done.stderr
