"""The MAIL DROP: delivery into this host's maildirs, for a host with no postfix.

A host that does not run postfix can still take mail by a plain file drop.
The pager already watches a maildir's new/ itself (kqueue plus a 1 s
poll) and needs no Dovecot, so the one thing a postfix-less host lacks is
the thing that puts a message INTO the box. This is that thing, and nothing
else.

SELECTED BY ONE SETTING, the one that already names the MTA:

    SESSIONMAIL_SENDMAIL=drop

The sender then delivers in-process through this module instead of piping
to a sendmail, and runs its deliverability pre-flight against THIS module's
resolution instead of postfix's compiled maps (openspec/specs/message, "The
send self-gates on deliverability" - the gate moves with the transport, it
does not go away). It is a mode, not a program: the package spec allows
exactly three commands on the PATH ("One package, three commands"), so there
is no `sessionmail-drop` executable. `python -m sessionmail._drop` speaks
sendmail's interface (`-t -i -f FROM`, the message on stdin) for testing and
for callers that want one.

WHERE A RECIPIENT LANDS is not decided here. It is asked of the SAME
generator that writes postfix's maps (`postfix/vmailbox-gen`, run in its
print modes, which touch no file): `--print` is the mailbox map
(`<name>@<host>  agents/<name>/`) and `--print-valias` is the alias map
(`<seat>@<host>  <seat>@<host>, <session>@<host>` - the doorbell copy the
pager listens for). The drop then does with those two maps what postfix's
virtual(8) does: expand aliases (an address that lists itself is delivered,
not expanded again), fall back from `+ext` to the base when the exact key
has no row, and deliver one copy per distinct box into `<mail root>/<value>`.
So a host that later installs postfix changes a setting, never where mail
lands. A generator that lists `no-fqdn-v1` is asked with `--no-fqdn`:
its short-host rows only, since the drop reads no other, so a host with no
MagicDNS suffix and no tailscale still delivers to its own boxes.

Two rules carried over from the retired bash filedrop (session-mail-send,
lines 45-56), because they were learned the hard way:

- IT RESOLVES A BOX, IT NEVER MINTS ONE. A recipient whose final box is
  not an existing maildir refuses; a typo must not create a phantom box at
  exit 0.
- ALL OR NOTHING. Every recipient is resolved before any byte is written;
  one miss refuses the whole message and delivers to nobody.

ONE QUALIFICATION is reproduced, and not here: postfix appends
myorigin to a bare `From:` in transit, and readers that judge the sender
(`_voice.keeper_sent`) need that domain. The drop has no transit, so the
SENDER qualifies a bare From before it builds and signs the wire
(`_sender.send`, drop branch only), with the domain `origin` below; the
Return-Path follows, because it is that From's address. It is never done to
bytes already built (`drop`, the `-m` CLI): a rewrite after signing breaks
v2 wherever its domain differs from the signer's origin.

Not done, on purpose: cross-host delivery (a remote domain refuses by
name), any other header rewriting (postfix's canonical rewrite of a bare
`To: alice` is not reproduced; To and Cc stay bare, and the trace fields
keep the short host), queueing or retry across runs (a failure is reported
now).
Each FILE is whole or absent: tmp/, then a link into new/ that never
replaces a file already there. A MESSAGE to several boxes is
written box by box and stops at the first failure, so a failure can leave
copies in the boxes before it; the report names them (`drop`).
"""

from __future__ import annotations

import email.utils
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from email.policy import compat32
from pathlib import Path

from . import _generator
from . import _host   # the ONE host rule
from . import _magicdns
from . import _settings
from ._maildir import deliver_private

#: The value of $SESSIONMAIL_SENDMAIL that selects this mode: the carrier
#: vocabulary's one spelling of the drop, defined in `_settings`.
SETTING = _settings.CARRIER_DROP
PREFIX = "mail drop: "

# sysexits(3), what a sendmail says.
EX_OK = 0
EX_USAGE = 64
EX_NOUSER = 67
EX_UNAVAILABLE = 69
EX_CANTCREAT = 73
#: A name collision that outlived every fresh-name retry, with nothing
#: written. Under postfix pipe(8) 75 defers and retries, where 73
#: bounces. Never used once a copy is written: pipe(8) gives one status to
#: every recipient, so a retry would write those boxes twice.
EX_TEMPFAIL = 75

#: The generator, found by the one lookup (`_generator`), with the copy
#: shipped inside this package as the last resort (a fresh install has no
#: vmailbox-gen on PATH and no ~/.mail/.postfix). The drop may use the
#: packaged copy because it only runs the print modes: it writes no postfix
#: map. Both names are kept as aliases for callers and tests.
GEN_VAR = _generator.VAR
PACKAGED_GEN = _generator.PACKAGED
GEN_TIMEOUT = 30.0
#: Ten hops is already a loop in any map this generator writes.
MAX_ALIAS_DEPTH = 10


class NotDelivered(Exception):
    """The drop would not, or could not, deliver. `written` lists any copy
    already in a box (only a failure while WRITING can leave one)."""

    def __init__(self, message: str, code: int, written: Sequence[Path] = ()):
        super().__init__(message)
        self.code = code
        self.written = list(written)


@dataclass
class Maps:
    root: Path                                  # the tree map values are relative to
    host: str
    mailbox: dict[str, str]
    alias: dict[str, list[str]]
    notes: list[str] = field(default_factory=list)
    #: The domain a bare From is qualified with (`origin`); set by the
    #: sender's drop pre-flight, empty when nothing asked.
    origin: str = ""
    #: Why the generator refused the alias print (`generator exit N: its
    #: last stderr line`), set by `prepare`; empty when it printed. The
    #: alias map is then empty, and a reader that must tell "no row" from
    #: "no map" (the doorbell) reads this.
    alias_refused: str = ""
    #: The environment `prepare` read; `names` asks the IS-ME set with it.
    env: Mapping[str, str] | None = None
    _names: frozenset[str] | None = field(default=None, repr=False)

    def names(self) -> frozenset[str]:
        """Every name this host has (`_host.names`), asked once per
        send and only when a domain is not `host` itself: `scutil` runs at
        most once a send, and not at all for mail to the NAME."""
        if self._names is None:
            self._names = _host.names(self.env) | {self.host}
        return self._names


def selected(env: Mapping[str, str]) -> bool:
    """The carrier is the drop: `_settings.carrier`, the one resolver (the
    variable, else the settings file, else the drop by default)."""
    return _settings.carrier(env).kind == "drop"


# ── where things are ─────────────────────────────────────────────────────────

def mail_root(env: Mapping[str, str]) -> Path:
    """The tree the map values are relative to (postfix's
    virtual_mailbox_base): the parent of the agents root the sender and
    readers use (`$SESSIONMAIL_ROOT`, then `$SESSION_MAIL_ROOT`, as
    `_seat.regen_maps` takes `root.parent`), else `$MAIL_ROOT`, else the
    settings file's `root`, else ~/.mail - the generator's own default."""
    agents = env.get("SESSIONMAIL_ROOT") or env.get("SESSION_MAIL_ROOT")
    if agents:
        return Path(agents).expanduser().parent
    if env.get("MAIL_ROOT"):
        return Path(env["MAIL_ROOT"]).expanduser()
    given = _settings.file_path("root", env)
    if given is not None:
        return Path(given).expanduser()
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".mail"


def short_host(env: Mapping[str, str]) -> str:
    """This host's NAME, the sender's (`_host.name`), so both halves
    of one send agree on what "this host" is. The map rows are keyed by it."""
    return _host.name(env)


#: The signer's override for the mail origin (`_sig.origin_of`).
ORIGIN_VAR = "SESSIONMAIL_ORIGIN"


def origin(env: Mapping[str, str], host: str, notes: list[str] | None = None) -> str:
    """The domain the drop qualifies a bare From with, as postfix's myorigin
    would: `$SESSIONMAIL_ORIGIN` when set, else `<host>.<MagicDNS suffix>`,
    else HOST.

    - SESSIONMAIL_ORIGIN wins: it is the origin the v2 signer canonicalises
      a bare address with, so the signer and the drop never disagree on the
      domain. Read as the signer reads it (trimmed, case kept).
    - Else the FQDN from the ONE suffix definition (`_magicdns`), the form
      postfix writes when `myorigin = $myhostname`, and a form
      `_voice.host_forms` accepts.
    - Else the short host. A host with no tailnet gets here on every send
      (the generator's no-fqdn rows), and there the short host is right, so
      nothing is said (`_magicdns.drop_without_tailnet`). Any other host
      (a malformed operator suffix, or a tailscale that is there but does
      not answer) is said in NOTES."""
    given = (env.get(ORIGIN_VAR) or "").strip() or _settings.file_text("postfix.origin", env)
    if given:
        return given
    found, _why = _magicdns.lookup(env)
    if found:
        return f"{host}.{found}"
    if notes is not None and not _magicdns.drop_without_tailnet(env):
        notes.append(f"⚠  {_magicdns.UNKNOWN} - a bare From is qualified with the short "
                     f"host only (@{host})")
    return host


def generator(env: Mapping[str, str], root: Path) -> str:
    """The generator the drop runs: env, PATH, `<root>/.postfix`, then the
    packaged copy LAST. A set `$SESSIONMAIL_VMAILBOX_GEN` is the only
    candidate, so a pin whose file is missing refuses here."""
    found = _generator.find(env, root, packaged=True)
    if found is not None:
        return found.path
    if env.get(GEN_VAR):
        raise NotDelivered(f"no map generator: {GEN_VAR} is set to {env[GEN_VAR]}, "
                           "which is not a file", EX_UNAVAILABLE)
    pinned = _settings.file_path("postfix.generator", env)
    if pinned is not None:
        raise NotDelivered(f"no map generator: postfix.generator in {_settings.SHOWN} is "
                           f"{pinned}, which is not a file", EX_UNAVAILABLE)
    raise NotDelivered("no map generator: vmailbox-gen is not on PATH, not in "
                       f"{root}/.postfix/, and not in this package", EX_UNAVAILABLE)


def _run_gen(gen: str, args: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess:
    # By its own shebang; a copy that lost its execute bit in an install
    # still runs, through bash (`_generator.argv`).
    cmd = _generator.argv(gen, args, env)
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              env=_generator.spawn_env(env) or dict(env), timeout=GEN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotDelivered(f"the map generator could not run ({gen}): {exc}", EX_UNAVAILABLE)


def _rows(text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, tab, value = line.partition("\t")
        if not tab or not key or key.startswith("#"):
            continue
        rows[key.strip().lower()] = [v.strip().lower() for v in value.split(",") if v.strip()]
    return rows


def _last(stderr: str | None) -> str:
    return ((stderr or "").strip().splitlines()[-1:] or ["no message"])[0]


#: The generator's capability token for its print-only `--no-fqdn` mode
#: short-host rows only, and no MagicDNS suffix asked.
NO_FQDN_CAP = "no-fqdn-v1"


def _no_fqdn(gen: str, env: Mapping[str, str]) -> bool:
    """Whether GEN lists `no-fqdn-v1` (one fork). An older generator answers
    `--capabilities` with exit 2, or lacks the token: False, today's argv."""
    done = _run_gen(gen, ["--capabilities"], env)
    return done.returncode == 0 and NO_FQDN_CAP in done.stdout.split()


def _print_argv(env: Mapping[str, str]) -> tuple[Path, str, str, list[str], bool]:
    """(root, host, generator, argv base, no_fqdn) for the print modes: the
    one place the drop's print argv is built."""
    root, host = mail_root(env), short_host(env)
    if not host:
        raise NotDelivered("this host has no name ($SESSIONMAIL_HOST, the settings file's "
                           "`host` and the hostname are all empty), so the drop cannot key "
                           "its maps: set SESSIONMAIL_HOST", EX_UNAVAILABLE)
    gen = generator(env, root)
    base = ["--root", str(root), "--host", host]
    no_fqdn = _no_fqdn(gen, env)
    if no_fqdn:
        base.append("--no-fqdn")
    return root, host, gen, base, no_fqdn


#: The two print modes, by the map each prints.
PRINT_MODES = {"mailbox": "--print", "alias": "--print-valias"}


def print_map(env: Mapping[str, str], which: str) -> str:
    """One map's text exactly as the drop resolves it (WHICH is "mailbox" or
    "alias"), from the argv `prepare` runs. Strict where `prepare` is
    lenient: any non-zero exit raises NotDelivered, a refused alias map
    included, so a caller that judges by the rows never reads a refusal as
    "no rows"."""
    _root, _host, gen, base, _no = _print_argv(env)
    done = _run_gen(gen, [*base, PRINT_MODES[which]], env)
    if done.returncode != 0:
        raise NotDelivered(f"the map generator's {PRINT_MODES[which]} failed "
                           f"(exit {done.returncode}): {_last(done.stderr)}", EX_UNAVAILABLE)
    return done.stdout


def prepare(env: Mapping[str, str]) -> Maps:
    """The two maps as the generator would write them for postfix, less the
    `@<fqdn>` rows. The mailbox map is required. An alias map the generator
    REFUSES (exit 3: e.g. no `session` on PATH to list live sessions) is
    announced and taken as empty: seat mail then lands in the seat's own box
    only, and no session box gets its doorbell copy.

    A generator that lists `no-fqdn-v1` is ALWAYS asked with `--no-fqdn`
    suffix or not: `qualify` turns every address on
    this host into `local@<short host>`, so no lookup here ever reads an FQDN
    row, and the print then needs no MagicDNS suffix and no tailscale. That
    is what lets a host with no tailnet deliver. An older generator is run
    exactly as before, and its refusal says it predates the token."""
    root, host, gen, base, no_fqdn = _print_argv(env)
    done = _run_gen(gen, [*base, "--print"], env)
    if done.returncode != 0:
        old = "" if no_fqdn else f" (this generator predates {NO_FQDN_CAP}: {gen})"
        raise NotDelivered(f"the map generator failed (exit {done.returncode}): "
                           f"{_last(done.stderr)}{old}", EX_UNAVAILABLE)
    mailbox = {k: v[0] for k, v in _rows(done.stdout).items() if v}
    maps = Maps(root, host, mailbox, {}, env=env)
    done = _run_gen(gen, [*base, "--print-valias"], env)
    if done.returncode == 0:
        maps.alias = _rows(done.stdout)
    else:
        maps.alias_refused = f"generator exit {done.returncode}: {_last(done.stderr)}"
        maps.notes.append(f"⚠  no alias map ({maps.alias_refused}) - seat mail lands in the "
                          "seat's own box only; no session box gets its doorbell copy")
    return maps


# ── resolution: what virtual(8) does with the two maps ───────────────────────

def qualify(address: str, host: str,
            me: Callable[[], frozenset[str]] | None = None) -> str | None:
    """`local@host`, lowercased, +ext kept; None when the domain is not this
    host. HOST is the NAME the map rows are keyed by; a domain is this host
    when it is HOST or under it (the tailnet FQDN), or, given ME (the IS-ME
    set, `Maps.names`, asked only for a domain that is not HOST), any name
    this machine has or had: the pin, the live hostname, LocalHostName,
    ComputerName slugged, localhost. A local domain is rewritten
    to HOST, so `alpha@MacBook-Air` resolves as `alpha@<the NAME>`."""
    local, at, domain = address.strip().lower().partition("@")
    if not local:
        return None
    if at and not _host.matches(domain, {host}):
        if me is None or not _host.matches(domain, me()):
            return None
    return f"{local}@{host}"


def _base(key: str) -> str | None:
    local, _, domain = key.partition("@")
    return f"{local.split('+', 1)[0]}@{domain}" if "+" in local else None


def expand(key: str, maps: Maps, depth: int = 0) -> list[str]:
    """The final addresses KEY delivers to: the exact key first, then its base
    without the +ext (recipient_delimiter `+`). An address that lists ITSELF
    is delivered, not expanded again - the generator's seat lines and its
    `+memory` identity lines rely on exactly that."""
    if depth > MAX_ALIAS_DEPTH:
        raise NotDelivered(f"alias loop at {key} (more than {MAX_ALIAS_DEPTH} hops)", EX_UNAVAILABLE)
    found = maps.alias.get(key)
    if found is None and (b := _base(key)) is not None:
        found = maps.alias.get(b)
    if found is None:
        return [key]
    out: list[str] = []
    for dest in found:
        d = qualify(dest, maps.host, maps.names)
        if d is None:
            raise NotDelivered(f"{key} is aliased to {dest}, which is not on this host - the "
                               "drop delivers on this host only", EX_NOUSER)
        out.extend([d] if d == key else expand(d, maps, depth + 1))
    return out


def box_for(final: str, maps: Maps) -> Path | None:
    value = maps.mailbox.get(final)
    if value is None and (b := _base(final)) is not None:
        value = maps.mailbox.get(b)
    if value is None:
        return None
    root = maps.root.resolve()
    box = (root / value).resolve()
    if root not in box.parents:
        return None                  # a map value never leads out of the mail root
    return box if (box / "new").is_dir() and (box / "tmp").is_dir() else None


def plan(recipients: Sequence[str], maps: Maps) -> list[tuple[str, str, Path]]:
    """[(original, final, box)], one per distinct box. EVERY recipient is
    resolved before any is written; raises NotDelivered naming every miss."""
    misses: list[str] = []
    out: list[tuple[str, str, Path]] = []
    seen: set[Path] = set()
    for original in recipients:
        key = qualify(original, maps.host, maps.names)
        if key is None:
            misses.append(f"'{original}' is not on this host (the drop delivers locally only)")
            continue
        for final in expand(key, maps):
            box = box_for(final, maps)
            if box is None:
                misses.append(f"'{original}'" + ("" if final == key else f" (as {final})")
                              + f": no mailbox row delivers {final} to an existing maildir "
                              f"under {maps.root} - the drop never makes a box")
            elif box not in seen:
                seen.add(box)
                out.append((original, final, box))
    if misses:
        raise NotDelivered("; ".join(misses) + ". NOTHING WAS DELIVERED, to this recipient "
                           "or any other.", EX_NOUSER)
    return out


# ── the message ──────────────────────────────────────────────────────────────

_SPLIT = re.compile(rb"\r?\n\r?\n")


def split(raw: bytes) -> tuple[bytes, bytes]:
    m = _SPLIT.search(raw)
    return (raw[:m.start()], raw[m.start():]) if m else (raw, b"")


def header_recipients(head: bytes) -> list[str]:
    msg = BytesHeaderParser(policy=compat32).parsebytes(head + b"\n\n")
    values = [v for name in ("To", "Cc", "Bcc") for v in msg.get_all(name, [])]
    return [a for _, a in email.utils.getaddresses(values) if a]


def strip_bcc(head: bytes) -> bytes:
    """Drop every `Bcc:` field, continuation lines included - what `-t`
    promises (sendmail(8)) and what postfix does."""
    out, skipping = [], False
    for line in head.split(b"\n"):
        if skipping and line[:1] in (b" ", b"\t"):
            continue
        skipping = line.lower().startswith(b"bcc:")
        if not skipping:
            out.append(line)
    return b"\n".join(out)


def trace(sender: str, original: str, final: str, host: str, now: float) -> bytes:
    """The fields postfix's virtual(8) prepends, so a dropped copy reads like
    a delivered one: X-Original-To is what `message list --to` groups by,
    Delivered-To names the box's own address."""
    stamp = email.utils.formatdate(now, localtime=True)
    return (f"Return-Path: <{sender}>\nX-Original-To: {original}\nDelivered-To: {final}\n"
            f"Received: by {host} (sessionmail mail drop); {stamp}\n").encode()


def write(box: Path, data: bytes, host: str, seq: int) -> Path:
    """tmp/ then a link into new/: a reader never sees half a message, and
    a file already in new/ is never replaced. The file is 0600
    under any umask, as virtual(8) writes it. A name collision
    retries under a fresh name (`_maildir.deliver_private`); the first name
    is `<sec>.M<usec>P<pid>Q<seq>.<host>`, a retry adds `R<hex>` after Q."""
    now = time.time()
    stem = f"{int(now)}.M{int((now % 1) * 1e6)}P{os.getpid()}Q{seq}"
    return deliver_private(box, data, lambda fresh: f"{stem}{fresh}.{host}")


def _report(failed: Path, why: str, written: Sequence[Path], unattempted: Sequence[Path]) -> str:
    """What a failed write leaves: the box that failed, the copies delivered
    and the boxes never attempted, so no reader takes part of it for the
    whole."""
    text = f"could not write into {failed}: {why}"
    if written:
        text += " - ALREADY DELIVERED to " + ", ".join(str(p) for p in written)
    else:
        text += " - NOTHING WAS DELIVERED"
    if unattempted:
        text += " - NOT ATTEMPTED: " + ", ".join(str(b) for b in unattempted)
    return text


def drop(raw: bytes, sender: str, recipients: Sequence[str], maps: Maps) -> list[Path]:
    """Deliver RAW to RECIPIENTS: plan every box, then write each copy, in
    plan order, stopping at the first failure.

    A failure raises NotDelivered naming the failed box, the copies already
    delivered (also in `.written`) and the boxes not attempted. Its code:
    EX_TEMPFAIL (75) for a name collision that outlived every retry with
    NOTHING written; EX_CANTCREAT (73) for any other write failure, and for
    ANY failure once a copy is written (75 there would make a
    pipe(8) caller deliver those copies again)."""
    targets = plan(recipients, maps)
    head, body = split(raw)
    message = strip_bcc(head) + body
    now, written = time.time(), []
    for seq, (original, final, box) in enumerate(targets):
        try:
            written.append(write(box, trace(sender or "MAILER-DAEMON", original, final,
                                            maps.host, now) + message, maps.host, seq))
        except OSError as exc:
            transient = isinstance(exc, FileExistsError) and not written
            raise NotDelivered(_report(box, exc.strerror or str(exc), written,
                                       [b for _, _, b in targets[seq + 1:]]),
                               EX_TEMPFAIL if transient else EX_CANTCREAT, written)
    return written


# ── sendmail's interface, for `python -m sessionmail._drop` ──────────────────

USAGE = ("usage: python -m sessionmail._drop [-t] [-i|-oi] [-f FROM] [RECIPIENT...] < message\n"
         "  Delivers into this host's maildirs, where the sessionmail map generator says.\n"
         "  The sender uses it in-process: SESSIONMAIL_SENDMAIL=drop.")


def parse(argv: Sequence[str]) -> tuple[bool, str, list[str]]:
    from_headers, sender, rcpts = False, "", []
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "-t":
            from_headers = True
        elif a in ("-i", "-oi"):
            pass                             # a lone "." never ends input here anyway
        elif a == "-f":
            if not args:
                raise NotDelivered("-f needs an address", EX_USAGE)
            sender = args.pop(0)
        elif a.startswith("-f"):
            sender = a[2:]
        elif a.startswith("-"):
            raise NotDelivered(f"unknown option {a}\n{USAGE}", EX_USAGE)
        else:
            rcpts.append(a)
    return from_headers, sender, rcpts


def main(argv: Sequence[str] | None = None) -> int:
    """sendmail's interface and sysexits(3) codes, as a pipe(8) caller would
    read them: 75 (EX_TEMPFAIL) defers, the others bounce. No pipe(8) caller
    exists today: no master.cf line pipes postfix into this module;
    the live path is the sender's, in process."""
    argv = sys.argv[1:] if argv is None else argv
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return EX_OK
    try:
        from_headers, sender, rcpts = parse(argv)
        raw = sys.stdin.buffer.read()
        if from_headers:
            rcpts += header_recipients(split(raw)[0])
        if not rcpts:
            raise NotDelivered("no recipients (none given, and no To/Cc/Bcc with -t)", EX_USAGE)
        maps = prepare(os.environ)
        written = drop(raw, sender, rcpts, maps)
    except NotDelivered as n:
        print(PREFIX + str(n), file=sys.stderr)
        return n.code
    for note in maps.notes:
        print(PREFIX + note, file=sys.stderr)
    print(PREFIX + "delivered " + ", ".join(str(p) for p in written), file=sys.stderr)
    return EX_OK


if __name__ == "__main__":
    sys.exit(main())
