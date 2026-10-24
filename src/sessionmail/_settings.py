"""The mail tool's HOST settings file - `$HOME/.sessionkit/mail/settings/mail.json`.

openspec/changes/pager-settings-file (PROPOSED): ONE file decides whether
the plugin's two pager hooks speak, and enabling them is ONE setting,
`"hooks": true`.

Host settings for every kit package live in the package's settings
folder, `~/.sessionkit/<package>/settings/` (package first), as
`<tool>.json` beside `<tool>.schema.json`; `message`, `pager` and `memory`
are the one tool `mail`, in package `mail`. `DIR` is the ONE definition,
and it agrees with sessiontools' map, `host_dir(tool) = ROOT /
PACKAGES[tool] / "settings"`. Nothing reads any other settings folder: no
override variable, no fallback. The file's first home was
`~/.session/mail/settings.json` (`OLD_RELATIVE`), and its move ships three
things together:
`ensure` CONVERTS the old file once (its bytes
become the new file), the writer writes only the new path, and the old path
becomes a SYMLINK to the new one for readers not yet updated. The file is
written lazily, by the first tool that needs it and finds it missing. Never under
SESSIONMAIL_HOME: config is not state.

Four functions, and no I/O at import:

- `path(env)`   where the file is
- `read(env)`   what it says, under D1's strict-boolean rule; never writes
- `ensure(env)` `read`, and if the file is missing, create it - from the
                old file when one is there (the conversion), else from the
                template - atomically, never replacing anything (D4); then
                the old path becomes a symlink, and the schema and template
                are installed beside the file

This module decides nothing about what a hook says. It reports a state and,
for a fault, why; the hooks choose their words (`hook_lines.toml`).
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, Mapping

from ._maildir import strict_mode

#: The template, package data beside this module: the ONE source of what a
#: fresh install writes, the way `arm.txt` is for the arm
#: instruction and `hook_lines.toml` for the hook lines.
TEMPLATE = "settings.template.json"

#: The tool's one-word name: it names the host dir, the file at every layer,
#: the schema and the log dir (session-settings, "Tool").
TOOL = "mail"

#: The kit's home, relative to `$HOME`: each package's folders live under it.
ROOT = Path(".sessionkit")

#: The package folder under ROOT, with no "session" prefix.
PACKAGE = "mail"

#: The package's settings folder, `$HOME/<DIR>`: the ONE definition of where
#: the mail tool's host settings live. It agrees with sessiontools' map,
#: `host_dir(tool) = ROOT / PACKAGES[tool] / "settings"`, mail -> mail.
DIR = ROOT / PACKAGE / "settings"

#: `$HOME/<RELATIVE>`. Never under SESSIONMAIL_HOME: config is not state.
RELATIVE = DIR / f"{TOOL}.json"

#: How user text shows the folder and the file: under `~`, never an
#: account's home. Every help line, note and refusal derives from these.
SHOWN_DIR = f"~/{DIR.as_posix()}/"
SHOWN = f"~/{RELATIVE.as_posix()}"

#: The mail tool's settings folder before the kit's home (0.1.0), relative
#: to `$HOME`: the ONE place the old path is named. While it is still a real
#: folder the host is STRANDED (`stranded`), and the writers write NOTHING
#: (`ensure`, `install`), so moving it finds no second copy in its way. Only
#: its kind is checked: it is never opened, read, written or a fallback.
LEGACY_DIR = Path(".settings") / TOOL

#: How user text shows LEGACY_DIR.
SHOWN_LEGACY_DIR = f"~/{LEGACY_DIR.as_posix()}/"

#: Where the file was until session-settings 3.1. Converted once by
#: `ensure`, then a symlink to RELATIVE; nothing writes it again.
OLD_RELATIVE = Path(".session") / "mail" / "settings.json"

#: The schema (JSON Schema 2020-12), package data beside this module, and
#: installed beside the host file with the template, so the settings reader
#: and the Settings app find both in `DIR` (specs/settings,
#: "Each tool ships a schema").
SCHEMA = f"{TOOL}.schema.json"

#: The template's INSTALLED name: one naming rule with `<tool>.json` and
#: `<tool>.schema.json`, where sessiontools' reader reads the tool layer
#: (`sessiontools.settings.sources`, 414bad3). The package file keeps its
#: name, `TEMPLATE`.
INSTALLED_TEMPLATE = f"{TOOL}.template.json"

ON: Literal["on"] = "on"                        # "hooks": true
OFF: Literal["off"] = "off"                     # "hooks": false - the only SILENT off
MISSING: Literal["missing"] = "missing"         # no file (off, to a reader)
MALFORMED: Literal["malformed"] = "malformed"   # every other off: a FAULT
STRANDED: Literal["stranded"] = "stranded"      # `ensure` on a stranded host: a FAULT, nothing written

State = Literal["on", "off", "missing", "malformed", "stranded"]


@dataclass(frozen=True)
class Settings:
    """What the file says. `hooks` is True only for state `on`.

    `why` is the fault's `{error}`: the parser's message,
    "no `hooks` key", what is wrong with `hooks`, a top level that is not an
    object, or the OS error (`unchecked_why` when the old settings folder
    could not be checked); for `stranded`, `STRANDED_WHY`. It is empty
    for `on`, `off` and `missing`."""

    hooks: bool
    state: State
    why: str = ""

    @property
    def fault(self) -> bool:
        return self.state in (MALFORMED, STRANDED)


def _home(env: Mapping[str, str] | None) -> Path:
    """HOME is the one the hook was given (the way `_sender.root_of` reads
    it), else `Path.home()`."""
    env = os.environ if env is None else env
    return Path(env.get("HOME") or str(Path.home()))


def path(env: Mapping[str, str] | None = None) -> Path:
    """`$HOME/.sessionkit/mail/settings/mail.json`."""
    return _home(env) / RELATIVE


def old_path(env: Mapping[str, str] | None = None) -> Path:
    """`$HOME/.session/mail/settings.json`: where the file was, and after
    the conversion a symlink to `path()`."""
    return _home(env) / OLD_RELATIVE


def _real_dir(p: Path) -> bool:
    """Is P a REAL folder: a directory, and not a symlink to one? Two
    stats, never a read: `os.stat` (follows a link), then `os.lstat`,
    through `_maildir.strict_mode`, so this is the 3.13 `p.is_dir() and not
    p.is_symlink()` on every version. Nothing there is False; a folder on
    the way that cannot be searched RAISES PermissionError (3.14 and later
    `Path.is_dir()` would answer False, and an unreadable folder would read
    as an absent one)."""
    return stat.S_ISDIR(strict_mode(p)) and not stat.S_ISLNK(strict_mode(p, follow_symlinks=False))


def stranded(env: Mapping[str, str] | None = None) -> bool:
    """Is this host STRANDED: is `$HOME/<LEGACY_DIR>` still a real folder?
    Once its files are moved it is gone, or a symlink to `DIR`, and neither
    is stranded. A stat, never a read: a refusal, not a fallback.

    Raises PermissionError (any OSError but "nothing there") when
    `$HOME/<LEGACY_DIR>` cannot be stat'd, the pre-0.2.0 settings folder
    unsearchable above all: that is "cannot tell", never "not stranded"."""
    return _real_dir(_home(env) / LEGACY_DIR)


def copied(env: Mapping[str, str] | None = None) -> bool:
    """Are BOTH folders real: is the host `stranded` while `$HOME/<DIR>` is
    a real folder too? The old files were copied, not moved. The new file
    is read and used; the writers still write nothing (`stranded`), and the
    old folder has to go. A stat of each folder, never a read of either.

    Raises PermissionError, as `stranded` does, when either folder cannot
    be stat'd: "cannot tell", never "not copied"."""
    return stranded(env) and _real_dir(_home(env) / DIR)


#: Why `ensure` wrote nothing on a stranded host: the fault's `why`.
STRANDED_WHY = f"the settings in {SHOWN_LEGACY_DIR} have not been moved to {SHOWN_DIR}"


def unchecked_why(err: OSError, shown: str = SHOWN_LEGACY_DIR) -> str:
    """Why nothing was written when `stranded` (or `copied`) could not tell:
    it raised ERR on the folder SHOWN. The fault's `why`, and `pager
    settings install`'s refusal. Never "not stranded": a folder that cannot
    be searched may hold the real settings."""
    return f"{shown} could not be checked ({_os_why(err)}), so nothing was written"


def template() -> str:
    """The template's text, exactly as a fresh install writes it."""
    return files(__package__).joinpath(TEMPLATE).read_text(encoding="utf-8")


def schema() -> str:
    """The schema's text, exactly as it is installed."""
    return files(__package__).joinpath(SCHEMA).read_text(encoding="utf-8")


# JSON's names for what a top level can be, in the words a person editing
# the file would use (D5: "the top level is a list, not an object").
_JSON_KIND = {list: "list", str: "string", int: "number", float: "number",
              bool: "boolean", type(None): "null"}


def _os_why(err: OSError) -> str:
    return err.strerror or str(err)


def _parse(text: str) -> Settings:
    """ON if and only if TEXT is a JSON object whose `hooks` is the
    boolean `true`. `"hooks": false` is the only silent OFF; every other OFF
    is a fault, with its reason. Unknown keys and `version` are ignored."""
    try:
        data = json.loads(text)
    except ValueError as err:
        return Settings(False, MALFORMED, f"not JSON: {err}")
    if not isinstance(data, dict):
        kind = _JSON_KIND.get(type(data), type(data).__name__)
        return Settings(False, MALFORMED, f"the top level is a {kind}, not an object")
    if "hooks" not in data:
        # What a hand-edit typo looks like: `{"hook": true}` (D1).
        return Settings(False, MALFORMED, "no `hooks` key")
    hooks = data["hooks"]
    if not isinstance(hooks, bool):   # "true" and 1 are OFF, never truthy-on
        return Settings(False, MALFORMED, f"`hooks` is {json.dumps(hooks)}, not true or false")
    return Settings(True, ON) if hooks else Settings(False, OFF)


def read(env: Mapping[str, str] | None = None) -> Settings:
    """What the file says. Never writes, never raises for the file's sake.

    A file that is not there is `missing`. A path that is there and cannot
    be read - a directory, mode 000, a dangling symlink, a parent that is a
    plain file - is a fault carrying the OS error, never a silent miss."""
    p = path(env)
    try:
        raw = p.read_bytes()
    except FileNotFoundError as err:
        if os.path.lexists(p):   # a dangling symlink: something IS there
            return Settings(False, MALFORMED, f"{_os_why(err)} (a symlink to nothing)")
        return Settings(False, MISSING)
    except OSError as err:
        return Settings(False, MALFORMED, _os_why(err))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        return Settings(False, MALFORMED, f"not UTF-8: {err}")
    return _parse(text)


#: The host's switch for the pager monitor. A missing key means
#: ON, so the template does not carry it; only the boolean `false` turns the
#: monitor off. Independent of `hooks`: a file that is a fault for the hooks
#: (no `hooks` key, say) can still say `"monitor": false`.
MONITOR_KEY = "monitor"


@dataclass(frozen=True)
class HostMonitor:
    """What the host file says about the monitor. `off` is True only for
    `"monitor": false`. `set_on` is True for `"monitor": true`, so a reader
    can name the file as the source of an ON. `ignored` says why a value
    that was there decided nothing (it is not a boolean, or the file could
    not be read): the monitor is then ON, and `pager status` says why."""

    off: bool = False
    set_on: bool = False
    ignored: str = ""


def monitor_host(env: Mapping[str, str] | None = None) -> HostMonitor:
    """The host file's `monitor` key. Never writes, never
    raises for the file's sake. A file that is not there says nothing (ON)."""
    p = path(env)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return HostMonitor()
    except OSError as err:
        return HostMonitor(ignored=f"{p} could not be read ({_os_why(err)}), so the monitor is on")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return HostMonitor(ignored=f"{p} is not JSON, so the monitor is on")
    if not isinstance(data, dict) or MONITOR_KEY not in data:
        return HostMonitor()
    value = data[MONITOR_KEY]
    if value is False:
        return HostMonitor(off=True)
    if value is True:
        return HostMonitor(set_on=True)
    return HostMonitor(ignored=f'"{MONITOR_KEY}": {json.dumps(value)} in {p} is ignored: '
                               "only the boolean false turns the monitor off")


def host_value(key: str, env: Mapping[str, str] | None = None) -> tuple[bool, object, str]:
    """KEY in the host file: `(present, value, why)`. WHY is non-empty only
    when the file is there and cannot be read or is not a JSON object; a
    missing file is simply not present. Never writes, never raises. The
    fallback `_seat.pager_required` uses when the layered reader is missing."""
    p = path(env)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return False, None, ""
    except OSError as err:
        return False, None, f"{p} could not be read ({_os_why(err)})"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False, None, f"{p} is not JSON"
    if not isinstance(data, dict):
        return False, None, f"{p} is not a JSON object"
    if key not in data:
        return False, None, ""
    return True, data[key], ""


# ── the keys, and the ONE reader every key is read by ──────────────────────
#
# openspec/changes/infra-settings D2-D4. Each key names its variable in the
# schema's `x-env`. `setting(key, env)` is the one reader: the variable when
# it is set and not empty, else the value merged from the TEMPLATE and HOST
# layers, else the key's default. The seat and session layers are never read
# for these keys: they describe the HOST (its MTA, its mail root, its
# notmuch), and a seat that could set its own carrier could deliver
# somewhere else. So a session's birth copy of a variable
# (`mail.d/50-env.json`) never outlives a change to the host file.

#: The carrier's key in mail.json, its variable, and the drop's one spelling.
#: The kit's per-host step imports these and copies none.
CARRIER_KEY = "sendmail"
CARRIER_VAR = "SESSIONMAIL_SENDMAIL"
CARRIER_DROP = "drop"

#: What a carrier is, said in every refusal of one.
CARRIER_RULE = ('the carrier is "drop" or an absolute path to an MTA '
                "(for example /usr/sbin/sendmail)")

#: The notmuch profile's key: `profile` under `notmuch`.
NOTMUCH_PROFILE_KEY = ("notmuch", "profile")


@dataclass(frozen=True)
class Key:
    """One key: its dotted NAME, the variable that wins over the file, and
    the DEFAULT `setting` gives when neither sets it. A default of None
    means the consumer works its default out at use (`hostname -s`, ask
    tailscale, the generator's lookup, `postconf`). `parent` is True for
    `root` alone: its variable names the AGENTS root, so the key's value is
    that path's parent."""

    name: str
    var: str
    default: object = None
    parent: bool = False

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self.name.split("."))


#: Every key, by its dotted name: the ONE table.
KEYS: dict[str, Key] = {k.name: k for k in (
    Key("root", "SESSIONMAIL_ROOT", "~/.mail", parent=True),
    Key("host", "SESSIONMAIL_HOST"),
    Key(CARRIER_KEY, CARRIER_VAR, CARRIER_DROP),
    Key("magicdns_suffix", "MAGICDNS_SUFFIX"),
    Key("notmuch.command", "SESSIONMAIL_NOTMUCH", "notmuch"),
    Key("notmuch.timeout", "SESSIONMAIL_NOTMUCH_TIMEOUT", 10),
    Key(".".join(NOTMUCH_PROFILE_KEY), "SESSIONMAIL_NOTMUCH_PROFILE", "sessionkit"),
    Key("postfix.generator", "SESSIONMAIL_VMAILBOX_GEN"),
    Key("postfix.relay_domain", "SESSIONMAIL_RELAY_DOMAIN", "relay.home"),
    Key("postfix.origin", "SESSIONMAIL_ORIGIN"),
    # The host-to-host pull (`python -m sessionmail._puller`). No row names a
    # private value: a required key has no default, and `_puller.config`
    # refuses, naming each one that is unset.
    Key("pull.imap_host", "SESSIONMAIL_PULL_IMAP_HOST"),
    Key("pull.imap_port", "SESSIONMAIL_PULL_IMAP_PORT", 993),
    Key("pull.login", "SESSIONMAIL_PULL_LOGIN"),
    Key("pull.secret_command", "SESSIONMAIL_PULL_SECRET_COMMAND"),
    Key("pull.relay_domain", "SESSIONMAIL_PULL_RELAY_DOMAIN"),
    Key("pull.listener", "SESSIONMAIL_PULL_LISTENER"),
    Key("pull.vmailbox", "SESSIONMAIL_PULL_VMAILBOX"),
    Key("pull.boxes_file", "SESSIONMAIL_PULL_BOXES_FILE", "~/.sessionkit/mail/settings/pull.boxes"),
    Key("pull.state_dir", "SESSIONMAIL_PULL_STATE_DIR", "~/.sessionmail/puller"),
    Key("pull.local_domain", "SESSIONMAIL_PULL_LOCAL_DOMAIN"),
    Key("pull.own_ips", "SESSIONMAIL_PULL_OWN_IPS"),
    Key("pull.unsorted_box", "SESSIONMAIL_PULL_UNSORTED_BOX"),
    Key("pull.audit_box", "SESSIONMAIL_PULL_AUDIT_BOX"),
    Key("pull.audit_to", "SESSIONMAIL_PULL_AUDIT_TO"),
    Key("pull.audit_sendmail", "SESSIONMAIL_PULL_AUDIT_SENDMAIL", "/usr/sbin/sendmail"),
    Key("pull.label", "SESSIONMAIL_PULL_LABEL", "dev.sessionkit.mail.puller"),
    Key("pull.backfill_seconds", "SESSIONMAIL_PULL_BACKFILL_SECONDS", 86400),
    Key("pull.reissue_seconds", "SESSIONMAIL_PULL_REISSUE_SECONDS", 300),
    Key("pull.login_gate", "SESSIONMAIL_PULL_LOGIN_GATE", 4),
)}

#: Where a value came from: `Setting.source`.
VARIABLE, TEMPLATE_LAYER, HOST_LAYER, DEFAULT = "variable", "template", "host", "default"
#: The host layers could not be read (and no variable was set): the value is
#: None and `why` says what went wrong. Each consumer decides what that
#: means; the carrier refuses, the others take their default.
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Setting:
    """What `setting` read. It unpacks as `(value, source)`:

        value, source = setting("sendmail")

    VALUE is the variable's text, stripped (`root`: its parent), or the
    file's value exactly as written (any JSON type), or the key's default.
    SOURCE is `variable`, `template`, `host`, `default` or `unreadable`.
    WHY is set only for `unreadable`: the error, with the settings home
    shown as `~`."""

    value: object
    source: str
    why: str = ""

    def __iter__(self):
        return iter((self.value, self.source))


def setting(key: str, env: Mapping[str, str] | None = None) -> Setting:
    """KEY (a dotted name in `KEYS`), read now: its variable from ENV
    (default `os.environ`) when set and not empty after stripping; else the
    template and host layers, merged (`_operator.host_values`'s sources,
    under the settings home: the password database's home, never `$HOME`);
    else the key's default. A set variable means the file is not read.
    The seat and session layers are never read. Never writes, never raises
    for the file's sake (an unknown KEY is a KeyError: a bug, not a host).

    The value is never judged here: `carrier_why` judges the carrier, and
    each consumer judges its own key. The older spellings of a variable
    (`SESSION_MAIL_ROOT`, `MAIL_ROOT`, `SESSION_MAIL_HOST`) are the
    consumer's to read, in today's order, before it falls back to this."""
    k = KEYS[key]
    env = os.environ if env is None else env
    raw = (env.get(k.var) or "").strip()
    if raw:
        return Setting(str(Path(raw).parent) if k.parent else raw, VARIABLE)
    from . import _operator          # here, not at import: _operator imports this module
    try:
        from sessiontools import settings as st
        srcs = _operator.host_sources(TOOL)
        values, origins, _locked, _leaves = st.merge(srcs)
    except Exception as exc:  # noqa: BLE001 - a bad layer is named, never raised
        return Setting(None, UNREADABLE, _operator.tilde(str(exc)))
    node: object = values
    for part in k.path:
        if not isinstance(node, dict) or part not in node:
            return Setting(k.default, DEFAULT)
        node = node[part]
    # A map where a value belongs is given as written too (the consumer
    # judges it); its origin is that of its leaves.
    origin = origins.get(k.name) or next(
        (f for leaf, f in origins.items() if leaf.startswith(k.name + ".")), "")
    layer = TEMPLATE_LAYER if Path(origin).name == INSTALLED_TEMPLATE else HOST_LAYER
    return Setting(node, layer)


def file_value(key: str, env: Mapping[str, str] | None = None) -> object:
    """KEY's value from the template and host layers, for a consumer at the
    point where it would take its HARD DEFAULT. None when no
    layer sets it, when the layers cannot be read, or when the key's own
    variable is set: that variable beats the file, even for a
    consumer that never read it, which then keeps its default."""
    got = setting(key, env)
    return got.value if got.source in (TEMPLATE_LAYER, HOST_LAYER) else None


def file_text(key: str, env: Mapping[str, str] | None = None) -> str | None:
    """`file_value` when it is a string with something in it, stripped; else
    None, so a consumer takes its default for a value of the wrong kind."""
    value = file_value(key, env)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def file_path(key: str, env: Mapping[str, str] | None = None) -> str | None:
    """`file_text`, with a leading `~` meaning the account's home, as
    `registry` reads it: the settings home, never `$HOME`."""
    text = file_text(key, env)
    if text is None:
        return None
    if text == "~" or text.startswith("~/"):
        from . import _operator          # here, not at import: _operator imports this module
        return str(_operator.settings_home() / text[2:])
    return text


def carrier_why(value: object) -> str:
    """"" when VALUE is a carrier (exactly `drop`, or a string that starts
    with `/`), else why not, naming VALUE as JSON. Judged exactly as given:
    the caller strips a variable first (`setting` does), never a file's
    value. Whether an absolute path is an executable MTA is the sender's
    check, not this one."""
    if value == CARRIER_DROP or (isinstance(value, str) and value.startswith("/")):
        return ""
    return f"{json.dumps(value)} is not a carrier: {CARRIER_RULE}"


@dataclass(frozen=True)
class Carrier:
    """How mail leaves this host. KIND is `drop`, `mta` or
    `refused`; PATH is the MTA's path, for `mta`; SOURCE is where the value
    came from (`setting`'s sources); WHY, for `refused` only, names the
    value and where it was set, or why the file could not be read. A
    sender says it as `no MTA: <why> - NOTHING WAS SENT`."""

    kind: Literal["drop", "mta", "refused"]
    path: str | None
    source: str
    why: str = ""


def carrier(env: Mapping[str, str] | None = None) -> Carrier:
    """The carrier, read now through `setting`: the variable when set and
    not empty (stripped), else the `sendmail` key in the template and host
    layers, else the drop. A value that is not a carrier (`carrier_why`) is
    REFUSED, never a fallback: a typo on a postfix host would otherwise
    stop mail across machines without a word. A host file that cannot be
    read, with no variable set, is refused too. Never raises."""
    try:
        got = setting(CARRIER_KEY, env)
    except Exception as exc:  # noqa: BLE001 - never raises: an unknown failure is a refusal
        return Carrier("refused", None, UNREADABLE,
                       f"{SHOWN} could not be read ({exc}), so the carrier is not known")
    if got.source == UNREADABLE:
        return Carrier("refused", None, UNREADABLE,
                       f"{SHOWN} could not be read ({got.why}), so the carrier is not known")
    why = carrier_why(got.value)
    if why:
        if got.source == VARIABLE:
            said = (f"${CARRIER_VAR} is {json.dumps(got.value)}, which is not a carrier: "
                    f"{CARRIER_RULE}")
        else:
            shown = SHOWN if got.source == HOST_LAYER else f"{SHOWN_DIR}{INSTALLED_TEMPLATE}"
            said = (f'"{CARRIER_KEY}": {json.dumps(got.value)} in {shown} is not a carrier: '
                    f"{CARRIER_RULE}")
        return Carrier("refused", None, got.source, said)
    if got.value == CARRIER_DROP:
        return Carrier("drop", None, got.source)
    return Carrier("mta", str(got.value), got.source)


def _old_body(env: Mapping[str, str] | None) -> bytes | None:
    """The old file's bytes when it is a REAL file to convert; None when it
    is missing, already a symlink (converted), or cannot be read - in each
    of those there is nothing to carry over, and the template is used."""
    old = old_path(env)
    if old.is_symlink():
        return None
    try:
        return old.read_bytes()
    except OSError:
        return None


def _publish(target: Path, body: bytes) -> bool:
    """Write BODY at TARGET in one step, never replacing anything (D4).
    True if this call published it; False if something was already there
    (another session won). Raises OSError for any other failure.

    1. make the parent directories, default mode
    2. write BODY to a temp file in the SAME directory, fsync it
    3. `os.link` it into place - that publishes the whole file in one step
       and raises FileExistsError if anything is at the path, even a
       dangling symlink
    4. unlink the temp file on every path"""
    tmp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        name = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        tmp = name   # ours now, so ours to unlink - never another racer's
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            return False
        return True
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _link_old(env: Mapping[str, str] | None) -> None:
    """The third thing a move ships: the old path becomes a
    symlink to the new file, for readers not yet updated. Only a REAL old
    file whose bytes the new file already holds is replaced - so a hand
    edit made to the old file after the copy is never thrown away - and the
    swap is one `os.replace` of a symlink made beside it. A missing old
    path gets no symlink: nothing read it, and no directory is made for
    nothing (slipbox `no-empty-dirs`). Best effort: a failure leaves the
    old file where it was, and the next call tries again."""
    old, new = old_path(env), path(env)
    if old.is_symlink() or not old.is_file():
        return
    tmp: Path | None = None
    try:
        if old.read_bytes() != new.read_bytes():
            return
        tmp = old.with_name(f".{old.name}.{os.getpid()}.{secrets.token_hex(4)}")
        os.symlink(os.path.relpath(new, old.parent), tmp)
        os.replace(tmp, old)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def drift(env: Mapping[str, str] | None = None) -> bool:
    """True when the old path is a REAL file whose bytes differ from the new
    file's, so `_link_old` keeps it. From then on the two can say different
    things: the hooks read the new file, and anything not yet moved reads
    the old one (a real bug: the Settings app wrote one
    file while the tool read the other). It happens after a hand edit, or a
    writer that replaces the symlink with a file (temp file and rename). The
    ride-along says so once, and a person merges them. Never raises: a file that cannot
    be read is not drift."""
    old, new = old_path(env), path(env)
    try:
        if old.is_symlink() or not old.is_file() or not new.is_file():
            return False
        return old.read_bytes() != new.read_bytes()
    except OSError:
        return False


def install(env: Mapping[str, str] | None = None, *, make: bool = False) -> list[Path]:
    """Install the schema and the template beside the host file, each only
    when it is missing or differs from this release's; the release owns
    both, so a hand edit to either is replaced. Returns what it wrote.
    Best effort: an OSError skips that file, and the next call tries again.

    MAKE False (`ensure`'s call) never makes `DIR` on its own:
    the directory is there only when the host file is. MAKE True makes it
   : a birth (`_birth.ensure_session_box`) and `pager settings
    install` run before any hook has, and the launcher's birth writer reads
    SESSIONMAIL_SUBSCRIBE only from an INSTALLED schema. The directory is
    never left empty: the schema goes into it at once (no-empty-dirs).
    Neither ever writes the host file `mail.json`: only `ensure`, from
    `hook_batch`, creates it and says the first-run note.

    A STRANDED host (`stranded`) gets nothing, MAKE or not: moving the old
    folder must not find a second copy of either file in its way. Nor does
    a host whose old folder cannot be checked (`stranded` raises): it may
    be stranded."""
    wrote: list[Path] = []
    d = path(env).parent
    try:
        if stranded(env):
            return wrote                  # a stranded host: nothing is written
    except OSError:
        return wrote                      # cannot tell: nothing is written either
    if not d.is_dir():
        if not make:
            return wrote
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return wrote
    for name, text in ((SCHEMA, schema()), (INSTALLED_TEMPLATE, template())):
        target, body = d / name, text.encode("utf-8")
        try:
            if target.is_file() and not target.is_symlink() and target.read_bytes() == body:
                continue
            tmp = d / f".{name}.{os.getpid()}.{secrets.token_hex(4)}"
            tmp.write_bytes(body)
            os.replace(tmp, target)
            wrote.append(target)
        except OSError:
            continue
    return wrote


def ensure(env: Mapping[str, str] | None = None) -> tuple[Settings, bool]:
    """`read`, and if the file is missing, create it. Returns `(settings,
    created)`; `created` is True for exactly one caller however many race
    (D4), and only when the file came from the TEMPLATE.

    The CONVERSION (session-settings 3.1): when the old file
    `~/.session/mail/settings.json` is a real file, its bytes - whatever
    they say, a fault included - become the new file, and `created` is
    False: the host's setting carried over, so nothing was created OFF and
    there is no first-run note to say. Either way the old path is then
    turned into a symlink (`_link_old`) and the schema and template are
    installed beside the file (`install`), on every call, so a move
    interrupted half way finishes on the next one.

    Never `os.replace`/`rename` over the file (they overwrite), and never
    O_EXCL on the path itself (a reader in the window would see an empty
    file, a false fault). Any other OSError leaves no file: the result is a
    fault with the OS error as `why` (D4 step 5), the
    hooks are off, and the next call tries again.

    A missing file on a host whose old folder cannot be checked (`stranded`
    raises, the pre-0.2.0 settings folder unsearchable) is a fault too, with
    `unchecked_why` as `why`, and NOTHING is written: a fresh OFF file there
    would hide the real settings in the folder nobody could look in."""
    current = read(env)
    if current.state != MISSING:
        _link_old(env)
        install(env)
        return current, False
    try:
        is_stranded = stranded(env)
    except OSError as err:
        return Settings(False, MALFORMED, unchecked_why(err)), False   # write NOTHING
    if is_stranded:
        return Settings(False, STRANDED, STRANDED_WHY), False   # write NOTHING
    body = _old_body(env)
    converted = body is not None
    if body is None:
        body = template().encode("utf-8")
    try:
        published = _publish(path(env), body)
    except OSError as err:
        return Settings(False, MALFORMED, _os_why(err)), False
    _link_old(env)
    install(env)
    if not published:
        return read(env), False   # someone else created it; say what it holds
    return read(env), not converted
