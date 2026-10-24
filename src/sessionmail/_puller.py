"""The host-to-host pull: a mail host's IMAP boxes, delivered into this host's postfix.

Each host delivers its own mail locally and sends one copy up to a shared
mail host. Mail that reached the mail host by another road (a seat on the
mail host itself, another host's copy) is pulled back down here. One thread
per box waits in IMAP IDLE, read-only: every box is opened with EXAMINE and
every body is fetched with BODY.PEEK, so the mail host's read state never
changes. Each arrival is handed to a local-only smtpd (`pull.listener`) as
RCPT BOX@<local domain>, so this host's own routing decides where it lands
(aliases, subscriptions, nested boxes). That listener must send no copy back
up to the mail host, or every pulled mail goes up and comes down again.

Two skips, both decided before the mail is handed on:

  - ALREADY HERE: the Message-ID is in the local box. notmuch is asked
    first, through the kit's own profile; then the box's last 15 minutes
    are scanned for what notmuch has not indexed yet.
  - OUR OWN LEG: the mail host's top Received header names one of this
    host's IPs and a BOX@<relay domain> recipient, so this is the copy this
    host sent up. It is skipped only when the local copy is there (after a
    short grace), so a mail that bounced here is still pulled. An own leg
    for a box with no local maildir (an alias) cannot be checked, and is
    skipped.

General delivery (`pull.unsorted_box`, off when unset): the mail host files
mail for boxes it does not have in one catch-all box. Every puller watches
it, and takes only mail for a SESSION box (a UUID or 8 hex digits, read from
X-Original-To) that this host's postfix map holds. Session ids are unique,
so two hosts never take the same mail. Any other name there is left alone.

The audit (`pull.audit_box`, off when unset): the mail host writes digests
of mail no puller took. Each host checks the entries IT sent for a copy
here, and mails `pull.audit_to` about any without one.

A cursor per box, "UIDVALIDITY UID", kept by `sessionmail._pull_imap`. A
box's first run starts at the oldest mail of the last
`pull.backfill_seconds` (0: only what arrives from now), so a box added soon
after its first mail still gets it; the dedup makes that safe. The audit and
general-delivery boxes start at UIDNEXT: an old digest would send its alert
again. `--from-start BOX` takes a box's whole history.

One puller per state dir: a second exits 3 before it connects. Every value
that names a host, a login, a secret or a box comes from config
(`pull.*` in ~/.sessionkit/mail/settings/mail.json, or its variable); with a
required one unset, the puller exits 2 naming each, before it makes the
state dir or takes the lock.

    python -m sessionmail._puller [--boxes FILE] [--once] [--dry-run]
        [--from-start BOX]... [--advance-cursors]
"""
from __future__ import annotations

import argparse
import dataclasses
import email.utils
import fcntl
import imaplib
import json
import os
import random
import re
import smtplib
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import _detect_imap as di
from . import _drop, _index, _notmuch, _operator, _settings
from . import _pull_imap as pi

# -- exits ---------------------------------------------------------------------

#: A refusal before anything is delivered: config, or no own IPs known.
EX_REFUSED = 2
#: Another puller holds this state dir's lock.
EX_LOCKED = 3
#: No box to pull (no boxes file and no general delivery).
EX_NO_BOXES = 1

# -- fixed values (not host-specific) --------------------------------------------

RECENT = 15 * 60            # the dedup's scan window, seconds
BACKOFF_MAX = 60.0
OWN_GRACE = 2.0             # seconds an own leg waits for its local copy
SECRET_RETRY = 60.0         # seconds between looks for a secret
SECRET_TIMEOUT = 60.0       # seconds the secret command may take
POSTMAP_TIMEOUT = 30.0
SMTP_TIMEOUT = 30.0
TAILSCALE = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")

_HEADER_END = re.compile(rb"\r?\n\r?\n")
_RECEIVED = re.compile(rb"^Received:(.*?)(?=^\S|\Z)", re.M | re.S | re.I)
_RETURN_PATH = re.compile(rb"^Return-Path:\s*<([^>]*)>", re.M | re.I)
_ORIG_TO = re.compile(rb"^X-Original-To:\s*<?([^@>\s]+)@", re.M | re.I)
_SESSION = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8}", re.I)
_LOCAL_PART = re.compile(r"[A-Za-z0-9._+-]+")
_BOX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
#: The mail host's own boxes (general delivery, the audit) may start with `_`.
_HOST_BOX = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]{0,127}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_FETCH_UID = re.compile(rb"UID (\d+)")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# -- config ----------------------------------------------------------------------

#: The keys with no default: unset, the puller and `message setup --pull`
#: both refuse, naming each.
REQUIRED = ("pull.imap_host", "pull.login", "pull.secret_command", "pull.relay_domain",
            "pull.listener", "pull.vmailbox")


@dataclass(frozen=True)
class Config:
    """Everything the puller reads from config, resolved once by `config`.
    Paths are expanded (`~` is the settings home, the password database's,
    never `$HOME`), so two Configs compare value for value. ENV is the
    environment it was resolved from, kept for the notmuch call; it is
    neither compared nor shown."""

    imap_host: str
    login: str
    secret_command: str
    relay_domain: str
    listener: tuple[str, int]
    vmailbox: str
    imap_port: int = 993
    boxes_file: Path = Path("pull.boxes")
    state_dir: Path = Path("puller")
    local_domain: str = "localhost"
    own_ips: tuple[str, ...] | None = None
    unsorted_box: str = ""
    audit_box: str = ""
    audit_to: tuple[str, ...] = ()
    audit_sendmail: str = "/usr/sbin/sendmail"
    label: str = "dev.sessionkit.mail.puller"
    backfill_seconds: int = 86400
    reissue_seconds: float = 300.0
    login_gate: int = 4
    mail_root: Path = Path(".mail")
    notmuch_command: str = "notmuch"
    notmuch_timeout: float = 10.0
    notmuch_config: str | None = None
    dry_run: bool = False
    env: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)

    def differences(self, other: Config) -> list[Difference]:
        """Each field whose value differs between SELF and OTHER (the
        environment-only `env` and the run flag `dry_run` aside), with the
        key and variables it is read from. Values are compared as resolved,
        so `~/.mail` and its expanded path are the same value."""
        out = []
        for f in dataclasses.fields(self):
            if not f.compare or f.name == "dry_run":
                continue
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if mine != theirs:
                key, variables = FIELD_SOURCES[f.name]
                out.append(Difference(f.name, key, variables, mine, theirs))
        return out


@dataclass(frozen=True)
class Difference:
    """One Config field that two resolutions disagree on."""

    field: str
    key: str
    variables: tuple[str, ...]
    mine: object
    theirs: object


def _pull_source(name: str) -> tuple[str, tuple[str, ...]]:
    key = f"pull.{name}"
    return key, (_settings.KEYS[key].var,)


#: Each Config field: the settings key it is read from and the variables that
#: can set it (the older spellings included, where the kit's reader takes
#: them), for a refusal that names where a value came from.
FIELD_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    **{n: _pull_source(n) for n in (
        "imap_host", "imap_port", "login", "secret_command", "relay_domain", "listener",
        "vmailbox", "boxes_file", "state_dir", "own_ips", "unsorted_box", "audit_box",
        "audit_to", "audit_sendmail", "label", "backfill_seconds", "reissue_seconds",
        "login_gate")},
    "local_domain": ("pull.local_domain", ("SESSIONMAIL_PULL_LOCAL_DOMAIN", "SESSIONMAIL_HOST",
                                           "SESSION_MAIL_HOST")),
    "mail_root": ("root", ("SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT", "MAIL_ROOT")),
    "notmuch_command": ("notmuch.command", (_notmuch.BINARY_VAR,)),
    "notmuch_timeout": ("notmuch.timeout", (_notmuch.TIMEOUT_VAR,)),
    "notmuch_config": ("notmuch.profile", (_settings.KEYS["notmuch.profile"].var,
                                           _index.VAR)),
}


@dataclass(frozen=True)
class Missing:
    """`config`'s refusal: every required key that is unset, every value
    that cannot be used (key, why), and why the settings file could not be
    read, if it could not."""

    keys: tuple[str, ...] = ()
    invalid: tuple[tuple[str, str], ...] = ()
    unreadable: str = ""

    def why(self) -> str:
        parts = [f"{k} ({_settings.KEYS[k].var})" for k in self.keys]
        parts += [f"{k} ({_settings.KEYS[k].var}): {why}" for k, why in self.invalid]
        head = "pull is not configured" if self.keys else "pull config is not usable"
        text = f"REFUSED - {head}: {', '.join(parts)}"
        if self.unreadable:
            text += f" (the settings file could not be read: {self.unreadable})"
        return text


def _under_settings_home(text: str) -> Path:
    """TEXT as a path, a leading `~` meaning the settings home."""
    if text == "~" or text.startswith("~/"):
        return _operator.settings_home() / text[2:]
    return Path(text)


def _as_list(value: object) -> tuple[str, ...] | None:
    """A list of strings, or comma-separated text, as a tuple of its
    non-empty items; None when VALUE is neither."""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        items = value
    else:
        return None
    return tuple(i.strip() for i in items if i.strip())


def _ask_notmuch_config(env: Mapping[str, str]) -> str | None:
    try:
        r = _index.resolve(dict(env))
    except Exception:  # noqa: BLE001 - an unknown failure is "no profile", never a crash
        return None
    return None if r.refused else str(r.config)


def config(env: Mapping[str, str] | None = None) -> Config | Missing:
    """The pull config, read now through `_settings.setting` (each key's
    variable, else the template and host layers, else its default). Returns
    a Config, or a Missing naming EVERY unset required key and every
    unusable value at once. Never raises, never writes, runs nothing:
    the own IPs are asked of tailscale by `main`, not here."""
    env = os.environ if env is None else env
    missing: list[str] = []
    invalid: list[tuple[str, str]] = []
    unreadable = ""

    def get(key: str) -> object:
        nonlocal unreadable
        got = _settings.setting(key, env)
        if got.source == _settings.UNREADABLE:
            unreadable = unreadable or got.why
            return None
        return got.value

    def text(key: str, *, required: bool = False) -> str:
        value = get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None or value == "":
            if required:
                missing.append(key)
            return ""
        invalid.append((key, f"{json.dumps(value)} is not a string"))
        return ""

    def number(key: str, kind: type, lowest: float, *, above: bool = False) -> float:
        value = get(key)
        default = _settings.KEYS[key].default
        if value is None:
            return kind(default)
        try:
            if isinstance(value, bool):
                raise ValueError
            got = kind(value)
        except (TypeError, ValueError):
            invalid.append((key, f"{json.dumps(value)} is not a number"))
            return kind(default)
        if got < lowest or (above and got == lowest):
            invalid.append((key, f"{json.dumps(value)} is out of range"))
            return kind(default)
        return got

    def items(key: str) -> tuple[str, ...] | None:
        value = get(key)
        if value is None:
            return None
        got = _as_list(value)
        if got is None:
            invalid.append((key, f"{json.dumps(value)} is not a list or comma-separated text"))
        return got or None

    imap_host = text("pull.imap_host", required=True)
    login = text("pull.login", required=True)
    if login:
        try:
            ok = "{box}" in login and login.format(box="x") != login
        except (IndexError, KeyError, ValueError):
            ok = False
        if not ok:
            invalid.append(("pull.login", f"{json.dumps(login)} does not name {{box}} once "
                            "and nothing else in braces"))
    secret_command = text("pull.secret_command", required=True)
    relay_domain = text("pull.relay_domain", required=True)
    listener_text = text("pull.listener", required=True)
    listener: tuple[str, int] = ("", 0)
    if listener_text:
        host, _, port = listener_text.rpartition(":")
        host = host.strip("[]")
        if host and port.isdigit() and 0 < int(port) < 65536:
            listener = (host, int(port))
        else:
            invalid.append(("pull.listener", f"{json.dumps(listener_text)} is not HOST:PORT"))
    vmailbox = text("pull.vmailbox", required=True)
    imap_port = int(number("pull.imap_port", int, 1))
    if imap_port > 65535:
        invalid.append(("pull.imap_port", f"{imap_port} is out of range"))
    boxes_file = _under_settings_home(text("pull.boxes_file")
                                      or str(_settings.KEYS["pull.boxes_file"].default))
    state_dir = _under_settings_home(text("pull.state_dir")
                                     or str(_settings.KEYS["pull.state_dir"].default))
    local_domain = text("pull.local_domain") or _drop.short_host(env)
    own_ips = items("pull.own_ips")
    boxes = {}
    for key in ("pull.unsorted_box", "pull.audit_box"):
        boxes[key] = text(key)
        if boxes[key] and not _HOST_BOX.fullmatch(boxes[key]):
            invalid.append((key, f"{json.dumps(boxes[key])} is not a box name"))
    audit_to = items("pull.audit_to") or ()
    if boxes["pull.audit_box"] and not audit_to:
        missing.append("pull.audit_to")
    audit_sendmail = text("pull.audit_sendmail") or str(
        _settings.KEYS["pull.audit_sendmail"].default)
    label = text("pull.label") or str(_settings.KEYS["pull.label"].default)
    if not _LABEL.fullmatch(label):
        invalid.append(("pull.label", f"{json.dumps(label)} is not a launchd label"))
    backfill = int(number("pull.backfill_seconds", int, 0))
    reissue = float(number("pull.reissue_seconds", float, 0, above=True))
    gate = int(number("pull.login_gate", int, 1))

    if missing or invalid:
        return Missing(tuple(missing), tuple(invalid), unreadable)
    return Config(
        imap_host=imap_host, login=login, secret_command=secret_command,
        relay_domain=relay_domain, listener=listener, vmailbox=vmailbox,
        imap_port=imap_port, boxes_file=boxes_file, state_dir=state_dir,
        local_domain=local_domain,
        own_ips=tuple(sorted(set(own_ips))) if own_ips else None,
        unsorted_box=boxes["pull.unsorted_box"], audit_box=boxes["pull.audit_box"],
        audit_to=audit_to, audit_sendmail=audit_sendmail, label=label,
        backfill_seconds=backfill, reissue_seconds=reissue, login_gate=gate,
        mail_root=_drop.mail_root(env),
        notmuch_command=_notmuch.binary(dict(env)), notmuch_timeout=_notmuch.timeout(dict(env)),
        notmuch_config=_ask_notmuch_config(env), env=dict(env))


# -- the log ---------------------------------------------------------------------

_lock = threading.Lock()
_said: set[str] = set()


def log(msg: str) -> None:
    with _lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def log_once(key: str, msg: str) -> None:
    """MSG, the first time KEY is said in this process only."""
    with _lock:
        if key in _said:
            return
        _said.add(key)
    log(msg)


# -- the secret ------------------------------------------------------------------

def read_secret(cfg: Config) -> str | None:
    """The secret command's stdout, stripped; None when it fails, times out
    or prints nothing."""
    try:
        r = subprocess.run(cfg.secret_command, shell=True, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=SECRET_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    secret = r.stdout.strip()
    return secret if r.returncode == 0 and secret else None


def wait_for_secret(cfg: Config, sleep: Callable[[float], None] = time.sleep) -> str:
    """The secret, looking again every SECRET_RETRY seconds until there is
    one, and saying so once. A secret that needs a live connection (an ssh
    control master, an unlocked store) may not be there yet."""
    said = False
    while True:
        s = read_secret(cfg)
        if s:
            return s
        if not said:
            log(f"secret     : the secret command gave nothing; looking again every "
                f"{SECRET_RETRY:g} s")
            said = True
        sleep(SECRET_RETRY)


# -- what is ours ----------------------------------------------------------------

def ask_tailscale() -> set[str]:
    """This host's tailnet IPs, from the tailscale CLI; empty when it
    cannot say."""
    for ts in TAILSCALE:
        try:
            r = subprocess.run([ts, "ip"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ips = {line.strip() for line in r.stdout.splitlines() if line.strip()}
        if ips:
            return ips
    return set()


def own_ips(cfg: Config) -> set[str]:
    """`pull.own_ips` when set, else what tailscale says."""
    return set(cfg.own_ips) if cfg.own_ips else ask_tailscale()


def head_of(raw: bytes) -> bytes:
    m = _HEADER_END.search(raw)
    return raw[: m.start()] + b"\n" if m else raw


def top_received(head: bytes) -> bytes:
    m = _RECEIVED.search(head)
    return m.group(1) if m else b""


def is_own_leg(head: bytes, ips: set[str], relay_domain: str) -> bool:
    """The copy this host sent up: the mail host took it FROM one of IPS and
    FOR BOX@<relay domain>. Mail this host sent straight to an address on the
    mail host also comes from one of IPS, but was never delivered here, so
    it is pulled."""
    top = top_received(head)
    return (f"@{relay_domain}>".encode() in top
            and any(f"[{ip}]".encode() in top for ip in ips))


def base_box(box: str) -> str:
    return box.split("+", 1)[0]


def local_dir(cfg: Config, box: str) -> Path | None:
    """The local maildir BOX@<local domain> lands in, from postfix's own map
    (`postmap -q`); None when the map has no row, or postmap cannot run.
    Never raises: it runs where no handler would catch it."""
    for name in (box, base_box(box)):
        try:
            r = subprocess.run(["postmap", "-q", f"{name}@{cfg.local_domain}", cfg.vmailbox],
                               capture_output=True, text=True, timeout=POSTMAP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_once("postmap", f"postmap    : could not ask the map ({exc}); "
                     "boxes read as having no local maildir")
            return None
        if r.returncode == 0 and r.stdout.strip():
            return cfg.mail_root / r.stdout.strip().rstrip("/")
    return None


def notmuch_files(cfg: Config, mid: str) -> list[str] | None:
    """The files notmuch holds under MID, asked through the kit's profile
    (`_index`) and bounded by `notmuch.timeout`; None when notmuch could not
    be asked (no profile, no binary, a timeout). Never raises."""
    try:
        argv = _index.argv(cfg.notmuch_command, dict(cfg.env))
        child = _index.child_env(dict(cfg.env))
        r = subprocess.run([*argv, "search", "--output=files", f"id:{mid}"],
                           capture_output=True, text=True, env=child,
                           timeout=cfg.notmuch_timeout)
    except (OSError, subprocess.TimeoutExpired, _notmuch.CouldNotLook) as exc:
        log_once("notmuch", f"notmuch    : could not ask ({exc}); the dedup scans "
                 f"the last {RECENT // 60} minutes only")
        return None
    return r.stdout.splitlines()


def held(cfg: Config, box_dir: Path | None, mid: str) -> bool:
    """MID is already in BOX_DIR: notmuch says so, or a scan of the box's
    files from the last RECENT seconds finds it. Never raises."""
    if not mid or box_dir is None:
        return False
    prefix = str(box_dir) + "/"
    files = notmuch_files(cfg, mid)
    if files and any(line.startswith(prefix) for line in files):
        return True
    needle = mid.encode()
    cutoff = time.time() - RECENT
    for sub in ("new", "cur"):
        try:
            entries = list(os.scandir(box_dir / sub))
        except OSError:
            continue
        for e in entries:
            try:
                if e.stat().st_mtime < cutoff:
                    continue
                with open(e.path, "rb") as f:
                    h = head_of(f.read(65536))
            except OSError:
                continue
            if needle in h:
                m = di._MSGID.search(h)
                if m and m.group(1).strip() == needle:
                    return True
    return False


# -- the audit -------------------------------------------------------------------

def audit_missing(cfg: Config, entries: list[dict], ips: set[str]) -> list[dict]:
    """The digest's entries THIS host sent (the mail host took them from one
    of IPS) with no copy here: in no local maildir, or not in the box they
    were for."""
    out = []
    for e in entries:
        if e.get("ip") not in ips:
            continue
        local = (e.get("to") or "").split("@", 1)[0] or e.get("box", "")
        if not held(cfg, local_dir(cfg, local), e.get("mid", "")):
            out.append(e)
    return out


def audit_mail(cfg: Config, missing: list[dict], digest_mid: str) -> bytes:
    by: dict[str, list[dict]] = {}
    for e in missing:
        by.setdefault(e["box"], []).append(e)
    top = sorted(by.items(), key=lambda kv: -len(kv[1]))
    lead = ", ".join(f"{b} x{len(es)}" for b, es in top[:3])
    subject = f"No home copy on {cfg.local_domain}: {len(missing)} mail, {lead}"
    if len(subject) > 69:
        subject = subject[:66].rsplit(" ", 1)[0] + "..."
    rows = "\n".join(
        f"| {b} | {len(es)} | {es[0].get('to') or '-'} | "
        f"{time.strftime('%H:%M', time.localtime(min(x['t'] for x in es)))}-"
        f"{time.strftime('%H:%M', time.localtime(max(x['t'] for x in es)))} |" for b, es in top)
    body = (f"Mail that {cfg.local_domain} sent sits on {cfg.imap_host} with **no copy here**, "
            f"and no puller takes it.\n\n"
            f"| box on the mail host | mails | addressed (X-Original-To) | landed |\n"
            f"|---|---:|---|---|\n{rows}\n\n"
            f"The usual cause: an address naming another host for a box that lives here, "
            f"or a box no host pulls. Fix the address, or add the box to its home host's "
            f"boxes file (`--from-start` takes a box's backlog).\n\n"
            f"First ids: {', '.join('<' + e['mid'] + '>' for e in missing[:5])}\n"
            f"Digest: <{digest_mid}> in {cfg.audit_box} on {cfg.imap_host}.\n")
    return (f"From: mailhost audit <mailhost-audit@{cfg.local_domain}>\n"
            f"To: {', '.join(cfg.audit_to)}\n"
            f"Subject: {subject}\n"
            f"Date: {email.utils.formatdate(localtime=True)}\n"
            f"Message-ID: {email.utils.make_msgid('audit', cfg.local_domain + '.mailhost')}\n"
            f"In-Reply-To: <{digest_mid}>\n"
            f"X-Mailhost-Audit: alert\n"
            f"Content-Type: text/markdown; charset=utf-8\n\n{body}").encode()


def audit_alert(cfg: Config, missing: list[dict], digest_mid: str) -> str:
    try:
        r = subprocess.run([cfg.audit_sendmail, "-i", "-f", f"mailhost-audit@{cfg.local_domain}",
                            *cfg.audit_to],
                           input=audit_mail(cfg, missing, digest_mid), capture_output=True,
                           timeout=SMTP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"sendmail FAILED: {exc}"
    if r.returncode == 0:
        return "mailed " + ", ".join(cfg.audit_to)
    return f"sendmail FAILED ({r.returncode}): {r.stderr.decode(errors='replace').strip()[:200]}"


# -- one box ---------------------------------------------------------------------

def nodelay(imap) -> None:
    """DONE and the FETCH behind it are two small writes. With Nagle on, the
    second waits for the first's ACK: one more round trip."""
    try:
        imap.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass


def first_base(cfg: Config, imap, uidnext: int) -> tuple[int, str]:
    """Where a box's FIRST run starts: the oldest UID that arrived in the last
    `backfill_seconds` (UID SEARCH YOUNGER, RFC 5032's WITHIN; a server
    without it gets SINCE the day the window began, which can take a little
    more), else UIDNEXT."""
    window_s = cfg.backfill_seconds
    if window_s <= 0:
        return uidnext, "backfill off: from UIDNEXT"
    # YOUNGER is tried, never looked up: imaplib keeps the capability list
    # from before login, and a server may name WITHIN only after it.
    try:
        typ, data = imap.uid("SEARCH", "YOUNGER", str(window_s))
    except imaplib.IMAP4.error:
        typ, data = "BAD", [b""]
    how = "YOUNGER"
    if typ != "OK":
        t = time.localtime(time.time() - window_s)
        typ, data = imap.uid("SEARCH", "SINCE", f"{t.tm_mday}-{_MONTHS[t.tm_mon - 1]}-{t.tm_year}")
        how = "SINCE (no WITHIN: from the day it began)"
    if typ != "OK":
        return uidnext, f"backfill search said {typ}: from UIDNEXT"
    uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) < uidnext)
    window = f"{window_s // 3600}h" if window_s >= 3600 else f"{window_s}s"
    window += "" if how == "YOUNGER" else f" by {how}"
    if not uids:
        return uidnext, f"backfill: nothing in the last {window}; from UIDNEXT"
    return uids[0], (f"backfill: {len(uids)} in the last {window}, from uid {uids[0]} "
                     "(dedup skips any already here)")


def received_age(head: bytes) -> str:
    top = top_received(head)
    if b";" not in top:
        return "?"
    try:
        when = email.utils.parsedate_to_datetime(top.rsplit(b";", 1)[1].decode().strip())
        return f"{time.time() - when.timestamp():.1f}s"
    except (TypeError, ValueError):
        return "?"


def orig_to(head: bytes) -> str:
    """The local part the mail host's delivery was addressed to (its topmost
    X-Original-To), or ''."""
    m = _ORIG_TO.search(head)
    local = m.group(1).decode(errors="replace") if m else ""
    return local if _LOCAL_PART.fullmatch(local) else ""


def local_rcpt(box: str, head: bytes) -> str:
    """The recipient here for mail pulled from BOX. The mail host may fold
    BOX+tag into BOX; its delivery keeps the address it was sent to in
    X-Original-To, so BOX+tag stays BOX+tag here."""
    m = _ORIG_TO.search(head)
    if m:
        local = m.group(1).decode(errors="replace")
        if base_box(local) == base_box(box) and _LOCAL_PART.fullmatch(local):
            return local
    return box


def inject(cfg: Config, rcpt: str, raw: bytes, head: bytes, tag: str) -> str:
    """Hand RAW to the local listener for RCPT@<local domain>, stamped with
    where it came from."""
    m = _RETURN_PATH.search(head)
    sender = m.group(1).decode(errors="replace") if m else ""
    stamp = (f"X-Mailhost-Pulled: {tag} by {cfg.local_domain}; "
             f"{email.utils.formatdate(localtime=True)}\r\n").encode()
    with smtplib.SMTP(*cfg.listener, timeout=SMTP_TIMEOUT) as s:
        s.sendmail(sender, [f"{rcpt}@{cfg.local_domain}"], stamp + raw)
        return "250"


def cursor_path(cfg: Config, box: str) -> Path:
    return pi.cursor_path(pi.Target(box, cfg.imap_host, cfg.imap_port, True, "INBOX"),
                          cfg.state_dir)


class BoxPuller(threading.Thread):
    """One box. After a push it costs ONE round trip: DONE and
    `UID FETCH base:* (UID BODY.PEEK[])` go out together, and imaplib reads
    the IDLE's tagged OK on its way to the FETCH's."""

    def __init__(self, cfg: Config, box: str, secret: str, ips: set[str], *, once: bool,
                 from_start: bool, gate: threading.BoundedSemaphore | None = None):
        super().__init__(name=f"pull-{box}", daemon=True)
        self.cfg = cfg
        self.box, self.secret, self.ips = box, secret, ips
        self.once, self.from_start = once, from_start
        self.gate = gate or threading.BoundedSemaphore(cfg.login_gate)
        self.user = cfg.login.format(box=box)
        self.cpath = cursor_path(cfg, box)
        self.stop = threading.Event()
        self.general = bool(cfg.unsorted_box) and box == cfg.unsorted_box
        self.audit = bool(cfg.audit_box) and box == cfg.audit_box
        self.counts = {"pulled": 0, "own": 0, "held": 0, "refused": 0, "left": 0, "audits": 0}
        self.validity: int | None = None
        self.box_dir: Path | None = None

    def say(self, s: str) -> None:
        log(f"{self.box:<18} {s}")

    def run(self) -> None:
        cfg = self.cfg
        backoff = 1.0
        if self.audit:
            self.say(f"start      : audit: digests of unpulled mail; this host's own without a "
                     f"copy here -> {', '.join(cfg.audit_to)}")
        elif self.general:
            self.say(f"start      : general delivery: session mail for boxes in {cfg.vmailbox} "
                     f"-> SESSION@{cfg.local_domain}")
        else:
            self.box_dir = local_dir(cfg, self.box)
            self.say(f"start      : -> {self.box}@{cfg.local_domain} "
                     f"(local {self.box_dir or 'NOT FOUND'})")
        while not self.stop.is_set():
            try:
                with self.gate:
                    imap = di.connect(cfg.imap_host, cfg.imap_port, True, self.user, self.secret)
            except di.CouldNotWatch as exc:
                wait = backoff * random.uniform(1.0, 1.5)     # retries fall out of step
                self.say(f"connect    : {exc}; again in {wait:.1f}s")
                if self.once:
                    return
                self.stop.wait(wait)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            backoff = 1.0
            nodelay(imap)
            try:
                self.serve(imap)
                if self.once:
                    return
            except (di.CouldNotWatch, imaplib.IMAP4.error, OSError, smtplib.SMTPException) as exc:
                self.say(f"lost       : {exc}; reconnecting")
                if self.once:
                    return
                self.stop.wait(backoff)
            finally:
                try:
                    imap.logout()
                except Exception:  # noqa: BLE001 - a logout on a dead link is not news
                    pass

    def serve(self, imap) -> None:
        _, uidnext = di.examine(imap, "INBOX")
        self.validity = di.uidvalidity(imap)
        cur = pi.read_cursor(self.cpath)
        if cur and cur[0] == self.validity:
            base = min(cur[1], uidnext)
        elif cur is None and self.from_start:
            base = 1
        elif cur is None and not (self.audit or self.general):
            base, why = first_base(self.cfg, imap, uidnext)
            self.say(f"cursor     : first run; {why}")
        else:
            if cur:
                self.say(f"cursor     : UIDVALIDITY {cur[0]} is not {self.validity}; from UIDNEXT")
            base = uidnext
        if not self.cfg.dry_run:
            pi.write_cursor(self.cpath, self.validity, base)
        if base < uidnext:
            base = self.take(imap, base, None)
        if self.once:
            return
        while not self.stop.is_set():
            base = self.idle(imap, base)

    def idle(self, imap, base: int) -> int:
        """One IDLE, up to `reissue_seconds`; then DONE with the FETCH behind
        it. A quiet reissue still fetches: the sweep for a lost push."""
        tag = imap._new_tag()
        imap.tagged_commands[tag] = None
        imap.send(tag + b" IDLE\r\n")
        while True:
            line = imap.readline()
            if not line:
                raise di.CouldNotWatch("connection closed starting IDLE")
            if line.startswith(b"+"):
                break
            if not line.startswith(b"* "):
                raise di.CouldNotWatch(f"IDLE refused: {line.decode(errors='replace').strip()}")
        pushed_at = None
        deadline = time.monotonic() + self.cfg.reissue_seconds
        while not self.stop.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if not di._readable(imap, min(left, 5.0)):
                continue
            line = imap.readline()
            if not line:
                raise di.CouldNotWatch("connection closed inside IDLE")
            if di._EXISTS.match(line):
                pushed_at = time.monotonic()
                break
        imap.send(b"DONE\r\n")
        return self.take(imap, base, pushed_at)

    def take(self, imap, base: int, pushed_at: float | None) -> int:
        typ, data = imap.uid("FETCH", f"{base}:*", "(UID BODY.PEEK[])")
        if typ != "OK":
            raise di.CouldNotWatch(f"UID FETCH {base}:*: {typ} {data}")
        imap.untagged_responses.pop("EXISTS", None)
        msgs = []
        for item in data:
            if isinstance(item, tuple):
                m = _FETCH_UID.search(item[0])
                # base:* names the last message even when it is below base
                if m and int(m.group(1)) >= base:
                    msgs.append((int(m.group(1)), item[1]))
        for uid, raw in sorted(msgs):
            self.handle(uid, raw, pushed_at)
            base = uid + 1
            if not self.cfg.dry_run:
                pi.write_cursor(self.cpath, self.validity, base)
        return base

    def handle(self, uid: int, raw: bytes, pushed_at: float | None) -> str:
        """Decide one mail and act on it; returns the verdict it logged."""
        cfg = self.cfg
        head = head_of(raw)
        m = di._MSGID.search(head)
        mid = m.group(1).decode(errors="replace").strip() if m else ""
        tag = f"{cfg.imap_host} box {self.box} uid {self.validity}/{uid}"
        if self.audit:
            self.counts["audits"] += 1
            try:
                entries = json.loads(raw[len(head):].strip() or b"{}").get("entries", [])
            except (ValueError, AttributeError) as exc:
                verdict = f"audit digest unreadable: {exc}"
                self.say(f"uid {uid:<6} <{mid}> {verdict}")
                return verdict
            missing = audit_missing(cfg, entries, self.ips)
            mine = sum(1 for e in entries if e.get("ip") in self.ips)
            done = (("dry run, not mailed" if cfg.dry_run else audit_alert(cfg, missing, mid))
                    if missing else "all at home")
            verdict = (f"audit: {len(entries)} entries, {mine} sent here, "
                       f"{len(missing)} with no copy here: {done}")
            self.say(f"uid {uid:<6} <{mid}> {verdict}")
            return verdict
        if self.general:
            rcpt = orig_to(head)
            session = bool(_SESSION.fullmatch(base_box(rcpt)))
            box_dir = local_dir(cfg, rcpt) if session else None
            if box_dir is None:
                self.counts["left"] += 1
                why = "not on this host" if session else "not a session"
                verdict = f"for {rcpt or '?'}: {why}, left"
                self.say(f"uid {uid:<6} <{mid}> {verdict}")
                return verdict
        else:
            rcpt, box_dir = local_rcpt(self.box, head), self.box_dir
        own = is_own_leg(head, self.ips, cfg.relay_domain)
        if held(cfg, box_dir, mid) or (own and box_dir is None):
            self.counts["own" if own else "held"] += 1
            verdict = "own leg, here, skipped" if own and box_dir else (
                "own leg, no local maildir to check, skipped" if own else "already here, skipped")
        elif own and (self.stop.wait(OWN_GRACE) or held(cfg, box_dir, mid)):
            self.counts["own"] += 1
            verdict = f"own leg, here after {OWN_GRACE:g}s, skipped"
        elif cfg.dry_run:
            verdict = (f"WOULD PULL{' as ' + rcpt if self.general else ''}"
                       f"{' (own leg NOT here)' if own else ''} ({len(raw)} bytes) - dry run")
        else:
            try:
                inject(cfg, rcpt, raw, head, tag)
                self.counts["pulled"] += 1
                since = (f"{1000 * (time.monotonic() - pushed_at):.0f} ms after the push"
                         if pushed_at else "from the backlog")
                verdict = (f"PULLED{' as ' + rcpt if self.general else ''}"
                           f"{' (own leg NOT here)' if own else ''} {since}, "
                           f"{received_age(head)} after the mail host took it")
            except smtplib.SMTPRecipientsRefused as exc:
                self.counts["refused"] += 1
                verdict = f"REFUSED here: {exc.recipients}"
        self.say(f"uid {uid:<6} <{mid}> {verdict}")
        return verdict


# -- the boxes -------------------------------------------------------------------

def read_boxes(path: Path) -> list[str]:
    """The box names in PATH, one per line (`#` starts a comment), sorted and
    unique; a line that is not a box name is skipped. [] when PATH cannot be
    read."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        b = line.split("#", 1)[0].strip()
        if b and _BOX.fullmatch(b):
            out.append(b)
    return sorted(set(out))


def _take_lock(state_dir: Path):
    """The state dir's lock, held: the open file, or None when another
    puller holds it (said in the log, with its pid)."""
    lock = open(state_dir / "puller.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.seek(0)
        log(f"puller: another puller holds {state_dir / 'puller.lock'} "
            f"(pid {lock.read().strip() or '?'}); refusing to deliver twice")
        lock.close()
        return None
    lock.seek(0)
    lock.truncate()
    lock.write(f"{os.getpid()}\n")
    lock.flush()
    return lock


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m sessionmail._puller", description=__doc__.split("\n\n")[0],
        epilog="Config: the pull.* keys in ~/.sessionkit/mail/settings/mail.json, each "
               "beaten by its SESSIONMAIL_PULL_* variable. Exit 2: not configured (each "
               "missing key named) or no own IP known; 3: another puller holds the state "
               "dir; 1: no boxes.")
    ap.add_argument("--boxes", type=Path, default=None, metavar="FILE",
                    help="the boxes file, one box per line (default: pull.boxes_file)")
    ap.add_argument("--once", action="store_true",
                    help="each box: deliver what waits past its cursor, then exit")
    ap.add_argument("--from-start", action="append", default=[], metavar="BOX",
                    help="a box with no cursor starts at UID 1, not at the backfill window")
    ap.add_argument("--advance-cursors", action="store_true",
                    help="set every box's cursor to its UIDNEXT now, then exit: run it "
                         "with the puller stopped, after a bulk import into the mail host's boxes")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and log; never inject, never move a cursor, take no lock")
    return ap


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Config first: a refusal makes no state dir and takes no lock.
    got = config(env)
    if isinstance(got, Missing):
        print(f"puller: {got.why()}", file=sys.stderr, flush=True)
        return EX_REFUSED
    cfg = dataclasses.replace(got, dry_run=args.dry_run)
    boxes_file = args.boxes if args.boxes is not None else cfg.boxes_file
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    # ONE puller per state dir: two would share the cursors and both deliver
    # every arrival. A dry run writes nothing, so it takes no lock.
    lock = None
    if not cfg.dry_run:
        lock = _take_lock(cfg.state_dir)
        if lock is None:
            return EX_LOCKED
    try:
        return _run(cfg, args, boxes_file)
    finally:
        if lock is not None:
            lock.close()


def _wanted(cfg: Config, boxes_file: Path) -> set[str]:
    return (set(read_boxes(boxes_file)) | ({cfg.unsorted_box} if cfg.unsorted_box else set())
            | ({cfg.audit_box} if cfg.audit_box else set()))


def _run(cfg: Config, args: argparse.Namespace, boxes_file: Path) -> int:
    boxes = read_boxes(boxes_file) + ([cfg.unsorted_box] if cfg.unsorted_box else [])
    if not boxes:
        log(f"puller: no boxes in {boxes_file} (one per line)")
        return EX_NO_BOXES
    ips = own_ips(cfg)
    log(f"puller: {len(boxes)} boxes from {boxes_file}; imaps://{cfg.imap_host}:{cfg.imap_port} "
        f"-> smtp {cfg.listener[0]}:{cfg.listener[1]} as BOX@{cfg.local_domain}; "
        f"own legs: {sorted(ips) or 'NONE'}{'; DRY RUN' if cfg.dry_run else ''}")
    if not ips:
        log("puller: no own IP known (pull.own_ips, or tailscale), so this host's own legs "
            "would come back; refusing")
        return EX_REFUSED
    secret = wait_for_secret(cfg)
    gate = threading.BoundedSemaphore(cfg.login_gate)
    if args.advance_cursors:
        for b in boxes:
            t = BoxPuller(cfg, b, secret, ips, once=True, from_start=False, gate=gate)
            imap = di.connect(cfg.imap_host, cfg.imap_port, True, t.user, secret)
            try:
                _, uidnext = di.examine(imap, "INBOX")
                validity = di.uidvalidity(imap)
                old = pi.read_cursor(t.cpath)
                if not cfg.dry_run:
                    pi.write_cursor(t.cpath, validity, uidnext)
                log(f"{b:<18} cursor     : {old} -> ({validity}, {uidnext})"
                    f"{' - dry run, not written' if cfg.dry_run else ''}")
            finally:
                imap.logout()
        return 0
    threads: dict[str, BoxPuller] = {}
    mtime = None
    while True:
        try:
            m = boxes_file.stat().st_mtime
        except OSError:
            m = None
        if m != mtime:
            mtime = m
            want = _wanted(cfg, boxes_file)
            for b in sorted(want - threads.keys()):
                threads[b] = BoxPuller(cfg, b, secret, ips, once=args.once,
                                       from_start=b in args.from_start, gate=gate)
                threads[b].start()
            for b in sorted(threads.keys() - want):
                threads.pop(b).stop.set()
                log(f"{b:<18} stopped     : no longer in {boxes_file}")
        if args.once:
            for t in threads.values():
                t.join()
            for b, t in sorted(threads.items()):
                log(f"{b:<18} done       : {t.counts}")
            return 0
        time.sleep(30)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
