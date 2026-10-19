"""Voice is recognised by `Ears-Kind`, and believed only when the keeper SIGNED it.

openspec/changes/pager-genie, tasks.md 3.1 ("The kind: a header"), D14 and
"Trust", as amended when O22 (S) was built: heard voice is no longer
believed on an unsigned `From:` alone, and its body is bounded. The
spec requirement it proves is *Voice arrives as mail in the ears box, and
the pager treats it as mail*.

The rule, whole. A mail is in exactly ONE of three states:

  1. MARKED as voice when it carries `Ears-Kind`, read through 3.22's
     reader (so `X-Ears-Kind` too, until 3.23). Presence marks it, whatever
     the value: the keeper writes `spoken`, or a cut notice. A mail with no
     mark, or a marked mail whose `From:` is not the keeper's address, is
     PLAIN mail, unchanged.
  2. The keeper's ADDRESS is ONE named value, `KEEPER = "heard"`, passing a
     `From:` whose address is `heard@<host>` or `heard+<tag>@<host>`, any
     non-empty tag, at this host in either host form (the short host or its
     FQDN, as the alias generator writes every row). A marked mail From that
     address is UNVERIFIED voice: an unsigned `From:` is something any local
     process can write (`sendmail -t`), so it is never believed on its own.
     Every heard mail is unverified today: the keeper does not sign yet
     (the producer's half, not this package's).
  3. TRUSTED voice is a marked mail From the keeper's address that the
     keeper SIGNED: a GOOD `session-mail-verify --trust` verdict (FULL:
     blessed; never UNBLESSED, never plain verify, which is self-attesting),
     whose registry cert agent is the From's own seat and a keeper address,
     and whose signature names in `h=` every `Ears-*` header the mail
     carries (so v1, which signs five fixed headers, is never trusted
     voice). `keeper_signed` is that test; it goes through the one
     verifier, never a second. The verdict reaches `recognise` through a
     SEAM, `signed`, so this module's tests stay pure; with no seam the
     mail is unverified (fail closed).

What each state is GIVEN is the views' (`_views`) and the rules' to decide:
trusted voice is given whole up to `CEILING` (16,000 characters, `whole`),
its speaker believed, its calls unclamped; unverified voice is fenced like
mail (the text cap, the batch budget, an UNVERIFIED header, no speaker, no
`done`) and its calls page behind ONE switch, `UNVERIFIED_CALLS`, clamped
to at most `carry` by the rules.

Pure but for `keeper_signed`, which runs the verifier on a file: no other
I/O but the env it is handed (and, when that env names no
`MAGICDNS_SUFFIX`, one memoised ask of tailscale for the FQDN's suffix,
`_magicdns`), and no judgement. What a turn DOES (ride or page) is
`pager-filters`' to decide: this module only says which state a mail is
in, for the calls feed (`_parsers.calls`) and the views (3.2b).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
from pathlib import Path
from typing import Callable, Mapping

from . import _magicdns
from ._headers import _unfold, read_header
from ._sender import host_of

#: The keeper's address: ONE named value, in ONE place ("From whom";
#: "Trust" (F)). A turn is From `heard+<device>@<host>` (the base
#: was renamed from `ears`: the mailbox is called heard), so a keeper turn no longer
#: lands in the `ears` ask box.
KEEPER = "heard"

#: O22 (F): a TRUSTED voice body is given whole up to this many characters.
CEILING = 16_000

#: The three states. `Voice.state` holds one of them.
TRUSTED = "trusted"
UNVERIFIED = "unverified"
PLAIN = "plain"

#: THE ONE SWITCH: do UNVERIFIED voice's calls page? True: they do,
#: so a spoken "hey <name>" keeps working while the keeper is unsigned, and the rules
#: CLAMP every such call to at most `carry` (no `start`, no breakthrough),
#: so an unverified call can do nothing plain mail cannot already do to the
#: seat. False (strict): unverified voice calls nobody until the keeper
#: signs. Flipping it is this one line.
UNVERIFIED_CALLS = True

#: The verdict seam: MSG -> True only when the keeper signed it
#: (`keeper_signed`, or a test's stand-in). Anything it raises is False.
Signed = Callable[[Message], bool]

#: How long one `verify --trust` may run before the mail is unverified.
VERIFY_TIMEOUT = 10.0

def host_forms(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """This host's forms, lowercase: `("host", "host.<suffix>")`, or `("host",)`.

    The short host is the sender's (`SESSIONMAIL_HOST`, else the hostname's
    first label); the suffix is the one the alias generator keys every row
    with, from the ONE definition (`_magicdns`): `MAGICDNS_SUFFIX`,
    else tailscale's MagicDNSSuffix. There is no default.

    With no suffix this returns the one form it CAN derive, the short host,
    and says so on stderr (once per process). It never raises: the pager's
    call sites (`$me`, the urgent senders, the keeper test, the views) must
    keep working on a host where no source answers. What that costs: a
    keeper `From:` at the FQDN is not recognised (fails closed), and neither
    is an FQDN call entry; `to:$me` still matches (`rules.Me.local` compares
    the first label)."""
    env = os.environ if env is None else env
    short = host_of(env).strip().lower()
    found, _why = _magicdns.lookup(env)
    if found is None:
        _magicdns.warn_unknown(f"this host's forms are the short host only ({short!r}), so a "
                               f"keeper or call address at the FQDN is not recognised")
        return (short,)
    return short, f"{short}.{found}"


def _sender(msg: Message) -> str | None:
    """The one address in `From:`, or None when there is not exactly one.

    Parsed from the RAW field, never the policy's rendering: the default
    policy repairs a malformed `From: heard@host <bob@host>` to `heard@host` (with
    only a defect to say so), which would pass the keeper test on a field
    that names two addresses. The strict `getaddresses` refuses it whole, so
    a `From:` it cannot parse is not the keeper (a trust test fails closed).

    Nor is one it cannot parse WITHOUT RAISING: RFC 5322 comments nest and
    `email.utils` reads them recursively, so a `From:` behind ~500 levels of
    `(` raises RecursionError. Every mail the listener hears is judged, and
    its calls are read through here (`_parsers.calls`), so that would be a
    mail any sender could write to deafen a seat. Taken from the rules'
    stand-in for this function when the stand-ins were deleted: the
    stricter of the two, kept."""
    fields = [_unfold(v) for k, v in msg.raw_items() if k.lower() == "from"]
    try:
        pairs = [(n, a) for n, a in getaddresses(fields) if a]
    except RecursionError:
        return None
    if len(pairs) != 1:
        return None
    return pairs[0][1].strip()


def _is_keeper(address: str, hosts: tuple[str, ...]) -> bool:
    local, at, domain = address.rpartition("@")
    if not at or domain.lower() not in hosts:
        return False
    if local == KEEPER:
        return True
    base, plus, tag = local.partition("+")
    return base == KEEPER and bool(plus) and bool(tag)


def keeper_sent(msg: Message, env: Mapping[str, str] | None = None) -> bool:
    """True when MSG's `From:` is the keeper's address at this host. The
    ADDRESS half of the test only: an unsigned `From:` is never trust on its
    own, so this alone makes a marked mail UNVERIFIED voice, not
    trusted (`recognise`).

    The local part is compared exactly (`heard`, `heard+<tag>`), the domain
    case-insensitively against this host's two forms and nothing wider
    (`host.<anything>` is not this host). A `From:` naming no address, or more
    than one, is not the keeper."""
    sender = _sender(msg)
    return sender is not None and _is_keeper(sender, host_forms(env))


@dataclass(frozen=True)
class Voice:
    """What recognition found for one mail.

    `voice`     True only for TRUSTED voice: marked, From the keeper, and
                the keeper signed it (every reader that believes a voice
                mail reads this, so it stays the trusted flag)
    `marked`    True when `Ears-Kind` is present in either spelling
    `kind`      its value (`spoken`, ...), None when absent
    `spelling`  the wire spelling read (`Ears-Kind`, `X-Ears-Kind`)
    `prefixed`  True if the `X-` form was read; None when absent
    `sender`    the one `From:` address, None when not exactly one
    `reason`    why: `trusted voice`, `unverified voice (...)`,
                `no Ears-Kind`, or `Ears-Kind, but not the keeper (<sender>)`
    `state`     `TRUSTED`, `UNVERIFIED` or `PLAIN`. Defaulted so a
                Voice built the old way is still plain
    """

    voice: bool
    marked: bool
    kind: str | None
    spelling: str | None
    prefixed: bool | None
    sender: str | None
    reason: str
    state: str = PLAIN

    @property
    def unverified(self) -> bool:
        """True for UNVERIFIED voice: marked, From the keeper's address, not
        signed by the keeper. Fenced, never believed."""
        return self.state == UNVERIFIED


#: Why a marked keeper-From mail is not trusted: said on every unverified mail.
UNVERIFIED_REASON = "unverified voice (From the keeper's address, no keeper signature)"


def recognise(msg: Message, env: Mapping[str, str] | None = None,
              signed: Signed | None = None) -> Voice:
    """Which of the three states MSG is in (see the module doc).

    SIGNED is the verdict seam: asked only for a marked mail From the
    keeper's address, and only its True makes TRUSTED voice. None (no
    verdict to hand) and anything it raises are UNVERIFIED: fail closed."""
    kind = read_header(msg, "Ears-Kind")
    sender = _sender(msg)
    if not kind.present:
        reason, state = "no Ears-Kind", PLAIN
    elif sender is None or not _is_keeper(sender, host_forms(env)):
        reason, state = f"Ears-Kind, but not the keeper ({sender or 'no single From'})", PLAIN
    elif _asked(signed, msg):
        reason, state = "trusted voice", TRUSTED
    else:
        reason, state = UNVERIFIED_REASON, UNVERIFIED
    return Voice(
        voice=state == TRUSTED,
        marked=kind.present,
        kind=kind.value,
        spelling=kind.spelling,
        prefixed=kind.prefixed,
        sender=sender,
        reason=reason,
        state=state,
    )


def _asked(signed: Signed | None, msg: Message) -> bool:
    """SIGNED's verdict on MSG, True only when it says exactly True."""
    if signed is None:
        return False
    try:
        return signed(msg) is True
    except Exception:  # noqa: BLE001 - a verdict that fails is no verdict
        return False


# == O22 (S): the keeper's signature, through the ONE verifier ================


#: The header a signed mail carries (`session-mail-verify`'s SIGH).
SIGNATURE_HEADER = "X-Session-Signature"


def ears_fields(msg: Message) -> tuple[str, ...]:
    """The `Ears-*` and `X-Ears-*` header names MSG carries, lowercased,
    each once: what a trusted signature must name in its `h=`."""
    out: list[str] = []
    for name, _ in msg.raw_items():
        low = name.strip().lower()
        if (low.startswith("ears-") or low.startswith("x-ears-")) and low not in out:
            out.append(low)
    return tuple(out)


def verdict_trusts(obj: object, ears: tuple[str, ...]) -> bool:
    """True when OBJ, `session-mail-verify --trust --json`'s verdict
    (docs/verify-json-verdict.md, schema 1), makes the mail TRUSTED voice:

      - the documented gate, tightened: `trust.verdict` is `FULL` (the
        keeper's cert is blessed; UNBLESSED is a cert nobody vouched for),
        `rc` 0, and `signature` not null;
      - `trust.agent`, the cert agent leg 2 matched to the From's seat, is
        a keeper address (`heard`, `heard+<tag>`). Leg 2 keeps the
        `+extension`, so a cert `heard` passes only `From: heard@<host>`
        and a device keeper needs a cert per device seat;
      - every name in EARS is in `signature.h` exactly (a name signed in
        upper case covers no field), so no `Ears-*` header the mail carries
        went unsigned: v1 (five fixed names) never passes.

    Anything else, a malformed object included, is False: fail closed."""
    try:
        trust, sig = obj["trust"], obj["signature"]   # type: ignore[index]
        if obj["rc"] != 0 or trust["verdict"] != "FULL" or not sig:  # type: ignore[index]
            return False
        agent = trust["agent"]
        h = sig["h"]
    except (KeyError, TypeError):
        return False
    if not isinstance(agent, str) or not isinstance(h, list):
        return False
    base, plus, tag = agent.partition("+")
    if agent != KEEPER and not (base == KEEPER and plus and tag):
        return False
    return all(name in h for name in ears)


def keeper_signed(path: str | os.PathLike, msg: Message,
                  env: Mapping[str, str] | None = None) -> bool:
    """O22 (S): True when the mail at PATH (parsed as MSG) passes
    `verdict_trusts` under `session-mail-verify --trust --json`, the bundled
    verifier (`_mhs.oracle`), with the registry `_mhs.trust_env` names.

    A mail with no `X-Session-Signature` is not signed, so it is False with
    no verifier run (every heard mail today). A verifier that is missing,
    times out, exits outside its contract or prints no object is False:
    the test could not be run, and voice it cannot vouch for is unverified."""
    if not any(k.strip().lower() == SIGNATURE_HEADER.lower() for k, _ in msg.raw_items()):
        return False
    from . import _mhs     # here: `_voice` stays light for the pure readers
    try:
        argv = [*_mhs.oracle("verify", dict(env) if env is not None else None),
                os.fspath(path), "--trust", "--json", "--quiet"]
        # Its own subprocess, not `_mhs.run`: so it names the package's
        # interpreter itself, or the verifier's wrapper runs PATH's python3.
        child_env = _mhs.child_env(_mhs.trust_env(dict(env) if env is not None else None))
        done = subprocess.run(argv, capture_output=True, env=child_env,
                              stdin=subprocess.DEVNULL, timeout=VERIFY_TIMEOUT)
        obj = json.loads(done.stdout.decode("utf-8", "replace").strip() or "null")
    except Exception:  # noqa: BLE001 - see the docstring: fail closed
        return False
    return isinstance(obj, dict) and obj.get("rc") == done.returncode \
        and verdict_trusts(obj, ears_fields(msg))


def signed_file(path: str | os.PathLike,
                env: Mapping[str, str] | None = None) -> Signed:
    """The verdict seam for the mail at PATH: `keeper_signed`, asked at
    most once (the views and the rules both ask of one file)."""
    memo: dict[str, bool] = {}

    def signed(msg: Message) -> bool:
        if "v" not in memo:
            memo["v"] = keeper_signed(Path(path), msg, env)
        return memo["v"]
    return signed


@dataclass(frozen=True)
class Whole:
    """A body as given: whole, or cut at the ceiling.

    `text`       what is printed: the body, or its first CEILING characters
                 then one line with the count and the command
    `cut`        characters not given (0 when whole)
    `more_line`  the one line, None when whole
    """

    text: str
    cut: int
    more_line: str | None


def whole(body: str, row: int | str, ceiling: int = CEILING) -> Whole:
    """BODY given whole up to CEILING characters; past it, the first CEILING
    and one line: `… N more - message read ROW` (the text view's pointer, so a reader
    meets one shape of "the rest is here")."""
    if len(body) <= ceiling:
        return Whole(text=body, cut=0, more_line=None)
    cut = len(body) - ceiling
    line = f"… {cut:,} more - message read {row}"
    return Whole(text=f"{body[:ceiling]}\n{line}", cut=cut, more_line=line)
