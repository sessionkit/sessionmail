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
- `set_hooks(on, env)` the one writer of `"hooks"`: `pager
                settings hooks on|off`, through `ensure` and `with_value`

This module decides nothing about what a hook says. It reports a state and,
for a fault, why; the hooks choose their words (`hook_lines.toml`).
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime
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

#: The package's LOG folder, `$HOME/<LOG_DIR>`, beside DIR (package first).
#: The kit keeps its own logs, never in ~/Library/Logs. The ONE definition
#: of where sessionmail's launch agents log; every log path is derived from
#: `log_dir`, and nothing else spells it by hand. Never under
#: SESSIONMAIL_HOME: logs are not state.
LOG_DIR = ROOT / PACKAGE / "log"

#: How user text shows LOG_DIR.
SHOWN_LOG_DIR = f"~/{LOG_DIR.as_posix()}/"

#: The log dir's mode when this tool creates it: a seat log can name
#: addresses and mail subjects.
LOG_MODE = 0o700

#: Where the seat agent logged before 0.3.2, relative to `$HOME`: the ONE
#: place the old path is named. A seat agent installed before 0.3.2 keeps
#: writing it until it is uninstalled and installed again, so readers fall
#: back to it. Nothing writes here, and nothing ever deletes or moves a file
#: in it. The fallback is DROPPED in the next release, 0.3.3.
OLD_LOG_DIR = Path("Library") / "Logs"

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


def log_dir(env: Mapping[str, str] | None = None) -> Path:
    """`$HOME/.sessionkit/mail/log`: where sessionmail's logs go."""
    return _home(env) / LOG_DIR


def old_log_dir(env: Mapping[str, str] | None = None) -> Path:
    """`$HOME/Library/Logs`: the pre-0.3.2 seat-agent log folder, read as a
    fallback only, until 0.3.3."""
    return _home(env) / OLD_LOG_DIR


def make_log_dir(env: Mapping[str, str] | None = None) -> Path:
    """Create `log_dir()` if it is missing, LOG_MODE (0700) whatever the
    umask, and return it. The parents (~/.sessionkit, ~/.sessionkit/mail)
    get the default mode, as `install` gives them. A folder that is already
    there keeps its mode: an operator who widened it chose to, and this is
    not the place to undo that silently. Raises OSError."""
    d = log_dir(env)
    if d.is_dir():
        return d
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        d.mkdir(mode=LOG_MODE)
    except FileExistsError:
        if not d.is_dir():
            raise
        return d                    # a racing writer made it: its mode stands
    os.chmod(d, LOG_MODE)           # mkdir's mode is masked by the umask
    return d


#: The hooks verb's audit file in LOG_DIR: ONE JSON line per flip of
#: `"hooks"` (`pager settings hooks on|off`), from every run, seated or
#: not, since the file it audits is host-wide. Append-only; sessionmail
#: never reads, rotates or deletes it.
AUDIT_LOG = "settings.jsonl"

#: The audit file's mode when made: a row names a seat and a session.
AUDIT_MODE = 0o600


def audit_path(env: Mapping[str, str] | None = None) -> Path:
    """`$HOME/.sessionkit/mail/log/settings.jsonl`: the hooks verb's audit."""
    return log_dir(env) / AUDIT_LOG


def audit(fields: Mapping[str, object], env: Mapping[str, str] | None = None) -> bool:
    """Append one row to `audit_path()`: `ts` (local time, with its offset)
    then FIELDS, as one JSON line. The log dir is made 0700 when missing
    (`make_log_dir`), the file 0600. One write(2) under O_APPEND, so two
    flips never interleave. True when the row was written; never raises:
    a row is never why the verb fails."""
    try:
        row = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **fields}
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        make_log_dir(env)
        fd = os.open(audit_path(env), os.O_WRONLY | os.O_APPEND | os.O_CREAT, AUDIT_MODE)
        try:
            return os.write(fd, line) == len(line)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - see above
        return False


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


#: The key `ensure` pins in a file it makes from the template, and `message
#: setup` adds to one that lacks it (KEY and FILE unchanged: sessiontools
#: reads them).
HOST_KEY = "host"


def with_key(raw: bytes, key: str, value: object) -> bytes | None:
    """RAW (a JSON object's bytes) with `"KEY": VALUE` added as its LAST
    member, every other byte as it was; None when KEY is already there.
    Raises ValueError when RAW is not UTF-8 JSON whose top level is an
    object. One line stays one line; a file with a member per line gets the
    new member on its own line, at the last member's indent."""
    text = raw.decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        kind = _JSON_KIND.get(type(data), type(data).__name__)
        raise ValueError(f"the top level is a {kind}, not an object")
    if key in data:
        return None
    close = len(text.rstrip()) - 1                 # the object's own `}`
    head = text[:close].rstrip()
    gap, tail = text[len(head):close], text[close:]
    pair = f"{json.dumps(key)}: {json.dumps(value)}"
    if head.endswith("{"):
        out = head + pair + gap + tail
    elif "\n" in gap:
        last = head.rsplit("\n", 1)[-1]
        out = head + ",\n" + last[:len(last) - len(last.lstrip())] + pair + gap + tail
    else:
        out = head + ", " + pair + gap + tail
    if json.loads(out) != {**data, key: value}:     # never write what was not meant
        raise ValueError("the key could not be added without changing the file's meaning")
    return out.encode("utf-8")


# ── one value, in place: `with_value`, and the verb's `set_hooks` ──────────

_WS = " \t\n\r"


def _members(text: str) -> list[tuple[str, int, int]]:
    """The TOP-LEVEL members of the JSON object TEXT, in order: `(key, start,
    end)`, where `text[start:end]` is the member's value exactly as written.
    TEXT has already parsed as an object (`json.loads`), so this only finds
    spans: `scanstring` for each key (escapes and all), `raw_decode` for
    each value. A nested object's keys are never members."""
    dec = json.JSONDecoder()
    out: list[tuple[str, int, int]] = []

    def skip(i: int) -> int:
        while i < len(text) and text[i] in _WS:
            i += 1
        return i

    i = skip(0)
    if text[i:i + 1] != "{":
        raise ValueError("the top level is not an object")
    i = skip(i + 1)
    if text[i:i + 1] == "}":
        return out
    while True:
        if text[i:i + 1] != '"':
            raise ValueError(f"expected a key at {i}")
        key, i = json.decoder.scanstring(text, i + 1)
        i = skip(i)
        if text[i:i + 1] != ":":
            raise ValueError(f"expected ':' at {i}")
        start = skip(i + 1)
        _value, end = dec.raw_decode(text, start)
        out.append((key, start, end))
        i = skip(end)
        if text[i:i + 1] == ",":
            i = skip(i + 1)
            continue
        if text[i:i + 1] == "}":
            return out
        raise ValueError(f"expected ',' or '}}' at {i}")


def _is(have: object, want: object) -> bool:
    """HAVE is WANT, type and all: `1 == True` in Python, and 1 is not on."""
    return type(have) is type(want) and have == want


def with_value(raw: bytes, key: str, value: object) -> bytes | None:
    """RAW (a JSON object's bytes) with the TOP-LEVEL member KEY set to
    VALUE, every other byte as it was; None when KEY already holds VALUE, of
    VALUE's own type (so `{"hooks": 1}` is not already `true`). A member is
    changed in place: only its value's span is replaced. A missing KEY is
    added last, by `with_key`. Raises ValueError when RAW is not UTF-8 JSON
    whose top level is an object, when KEY is a member more than once and a
    write is needed, or when the result would not mean `{**data, KEY:
    VALUE}`. With duplicates the LAST member is the one read, as `json`
    reads it, so "already" asks the last."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(f"not UTF-8: {err}") from err
    try:
        data = json.loads(text)
    except ValueError as err:
        raise ValueError(f"not JSON: {err}") from err
    if not isinstance(data, dict):
        kind = _JSON_KIND.get(type(data), type(data).__name__)
        raise ValueError(f"the top level is a {kind}, not an object")
    if key in data and _is(data[key], value):
        return None
    spans = [(s, e) for k, s, e in _members(text) if k == key]
    if not spans:
        return with_key(raw, key, value)
    if len(spans) > 1:
        raise ValueError(f"`{key}` is a member {len(spans)} times")
    (start, end), = spans
    out = text[:start] + json.dumps(value) + text[end:]
    got = json.loads(out)
    if got != {**data, key: value} or not _is(got[key], value):   # never write what was not meant
        raise ValueError("the value could not be set without changing the file's meaning")
    return out.encode("utf-8")


def _replace(real: Path, body: bytes) -> None:
    """BODY at REAL (a real file, never a symlink: the caller resolves) in
    ONE step: a temp file in REAL's own directory, O_CREAT|O_EXCL at 0600,
    written and fsync'd, given REAL's mode, then `os.replace`. A reader sees
    the old file or the new one, never part of one. The temp file is
    unlinked on every path. Raises OSError; then REAL is as it was."""
    st = os.stat(real)
    tmp = real.with_name(f".{real.name}.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.replace(tmp, real)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def fresh_body() -> bytes:
    """What `ensure` makes a missing file from: the template, with
    `"host"` pinned to the live short hostname, lowercased (`_host.live`).
    The template itself when the hostname is empty: no pin beats a wrong one."""
    from . import _host
    body = template().encode("utf-8")
    pinned = _host.live()
    return (with_key(body, HOST_KEY, pinned) or body) if pinned else body


def add_key(target: Path, key: str, value: object) -> bool:
    """Add `"KEY": VALUE` to the JSON object at TARGET, only if KEY is
    absent (`with_key`). True if written, False if KEY was already there.
    Raises OSError (missing, unreadable) or ValueError (not a JSON object):
    either way nothing is written. A symlink is written through, to its
    target. One step: a temp file in the same directory, fsync'd, given the
    file's mode, then `os.replace` (`_replace`, shared with `set_hooks`).
    The window: a hand edit saved between the read and the replace is lost."""
    real = target.resolve()
    os.stat(real)                     # missing: OSError before anything is read
    body = with_key(real.read_bytes(), key, value)
    if body is None:
        return False
    _replace(real, body)
    return True


def _link_old(env: Mapping[str, str] | None) -> None:
    """The third thing a move ships: the old path becomes a
    symlink to the new file, for readers not yet updated. Only a REAL old
    file whose bytes the new file already holds is replaced - so a hand
    edit made to the old file after the copy is never thrown away - and the
    swap is one `os.replace` of a symlink made beside it. A missing old
    path gets no symlink: nothing read it, and no directory is made for
    nothing (slipbox `no-empty-dirs`). Best effort: a failure leaves the
    old file where it was, and the next call tries again.

    The new file must be a real file DISTINCT from the old one.
    When mail.json is a symlink to the old file, the two hold the same
    bytes because they are one file read twice; replacing the old file
    then makes a two-node loop and loses the only copy. So the old file
    is never replaced by a link to itself."""
    old, new = old_path(env), path(env)
    if old.is_symlink() or not old.is_file():
        return
    tmp: Path | None = None
    try:
        if not new.is_file() or os.path.samefile(old, new):
            return
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
    Neither ever writes the host file `mail.json`: only `ensure` creates
    it, from `hook_batch` (which says the first-run note) or from
    `set_hooks` (`pager settings hooks on|off`, whose own line is the
    person's notice).

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

    A file made from the template carries the host PIN
    (`fresh_body`): `"host"`, the live short hostname, lowercased, so the
    name can never drift after the first session. The conversion adds
    nothing.

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
        body = fresh_body()
    try:
        published = _publish(path(env), body)
    except OSError as err:
        return Settings(False, MALFORMED, _os_why(err)), False
    _link_old(env)
    install(env)
    if not published:
        return read(env), False   # someone else created it; say what it holds
    return read(env), not converted


#: Why `set_hooks` refused (`HooksResult.refused`); "" when it did not.
REFUSED_ROOT = "root"            # euid 0, or SUDO_USER set (Q8)
REFUSED_STRANDED = "stranded"    # the old settings folder is still a real folder
REFUSED_COPIED = "copied"        # ... and the new one is real too
REFUSED_UNCHECKED = "unchecked"  # either folder could not be stat'd
REFUSED_FAULT = "fault"          # the file (or the old file) could not be read, judged or written
REFUSED_READBACK = "readback"    # written, and the read-back disagrees: a race or a bug

#: How user text shows OLD_RELATIVE.
SHOWN_OLD = f"~/{OLD_RELATIVE.as_posix()}"

#: `HooksResult.was` when there was no `hooks` member to report.
class _NoKey:
    def __repr__(self) -> str:
        return "NO_KEY"


NO_KEY = _NoKey()


@dataclass(frozen=True)
class HooksResult:
    """What `set_hooks` did. The verb only prints it.

    `want` is what was asked. `path` is the file judged: `path(env)`, or
    the old file when it is the old file a conversion would carry that was
    refused. `state` is what the file says when the call returns (`read`).
    `before` is the state it said before (`missing` for no file, the
    conversion included); `was` the JSON value of its `hooks` before
    (`NO_KEY` for no member, None for no file or a file that did not
    parse). `made` says this call found no file and left one (`converted`:
    from the old file); `wrote` that this call replaced the file's bytes;
    `already` that it said what was asked, so nothing was touched.
    `refused` is a `REFUSED_*` or ""; `why` names it."""

    want: bool
    path: Path
    state: State = MISSING
    before: State = MISSING
    was: object = None
    made: bool = False
    converted: bool = False
    wrote: bool = False
    already: bool = False
    refused: str = ""
    why: str = ""

    @property
    def flipped(self) -> bool:
        """The file changed: made, or its value written (Q1: the audit
        line is written on a flip only)."""
        return self.made or self.wrote


def _as_root(env: Mapping[str, str]) -> str:
    """Why this run is root's, or "": euid 0, or SUDO_USER set (Q8). A
    `sudo` that keeps HOME would leave a root-owned mail.json, and the
    folders `ensure` makes, in a person's home."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        return "this is root (euid 0)"
    sudo = (env.get("SUDO_USER") or "").strip()
    if sudo:
        return f"this is sudo (SUDO_USER={sudo})"
    return ""


def _judge(p: Path) -> tuple[bytes | None, State, object, str]:
    """The REAL file at P (a symlink followed): `(raw, state, was, why)`.
    RAW is None and WHY the reason when it cannot be read: the OS error, or
    a symlink to nothing. STATE and WAS are what it says, as `_parse` reads
    it, and WAS the `hooks` member's value (`NO_KEY` without one)."""
    try:
        raw = p.resolve().read_bytes()
    except FileNotFoundError as err:
        if os.path.lexists(p):
            return None, MALFORMED, None, f"{_os_why(err)} (a symlink to nothing)"
        return None, MISSING, None, ""
    except OSError as err:
        return None, MALFORMED, None, _os_why(err)
    try:
        said = _parse(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return raw, MALFORMED, None, ""
    was: object = None
    if said.state in (ON, OFF):
        was = said.hooks
    else:
        try:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                was = data.get("hooks", NO_KEY)
        except ValueError:
            pass
    return raw, said.state, was, ""


def set_hooks(on: bool, env: Mapping[str, str] | None = None) -> HooksResult:
    """`"hooks": ON` in the host file, through `ensure`, `with_value` and
    one atomic replace. Never raises for the file's
    sake; every refusal is a `HooksResult` with `refused` set, and the
    verb's own write never happened unless `wrote` is True.

    0. Root, or sudo: refused (Q8), nothing written.
    1. Stranded, or copied, or either folder could not be checked:
       refused, nothing written (`install`'s checks, in its order).
    2. Judged BEFORE anything is written: a file that is there and cannot
       be read, or is not a JSON object, or needs a write it cannot take (a
       duplicate member), is refused. One that already says ON is
       "already": nothing is run, `ensure` included, so a second run
       touches no file. No file, and an old file the conversion would
       carry: those bytes are judged the same way.
    3. `ensure`: the one creator (the template and the host pin, or the
       old file), the old-path link and the schema, BEFORE the write, so
       `_link_old` finds the bytes equal.
    4-6. The real file read again, `with_value`, `_replace`.
    7. The read-back, through `read`: a disagreement is the one refusal
       after the write.

    `SESSIONMAIL_PAGER` is never read here: the file is the host's switch."""
    env = dict(os.environ if env is None else env)
    p = path(env)
    root = _as_root(env)
    if root:
        return HooksResult(on, p, refused=REFUSED_ROOT, why=root)
    try:
        is_stranded = stranded(env)
    except OSError as err:
        return HooksResult(on, p, refused=REFUSED_UNCHECKED, why=unchecked_why(err))
    if is_stranded:
        try:
            is_copied = copied(env)
        except OSError as err:
            return HooksResult(on, p, refused=REFUSED_UNCHECKED, why=unchecked_why(err, SHOWN_DIR))
        kind = REFUSED_COPIED if is_copied else REFUSED_STRANDED
        return HooksResult(on, p, refused=kind, why=STRANDED_WHY)

    there = os.path.lexists(p)
    converting = False
    if there:
        raw, before, was, why = _judge(p)
        if raw is None:
            return HooksResult(on, p, before=before, refused=REFUSED_FAULT, why=why)
        try:
            body = with_value(raw, "hooks", on)
        except ValueError as err:
            return HooksResult(on, p, before=before, was=was, refused=REFUSED_FAULT, why=str(err))
        if body is None:
            return HooksResult(on, p, state=before, before=before, was=was, already=True)
    else:
        before, was = MISSING, None
        old = _old_body(env)
        if old is not None:
            converting = True
            try:
                with_value(old, "hooks", on)
            except ValueError as err:
                return HooksResult(on, old_path(env), refused=REFUSED_FAULT, why=str(err))

    made_now = False
    if not there:
        settings, _created = ensure(env)
        if not os.path.lexists(p):
            return HooksResult(on, p, refused=REFUSED_FAULT,
                               why=settings.why or "the file could not be made")
        made_now = True
    else:
        ensure(env)          # the old-path link and the schema; never touches the file

    # From here a refusal comes after `ensure` (only a race, an I/O error or
    # a bug reaches one): it says what the file reads as now.
    done = {"before": before, "was": was, "made": made_now, "converted": converting and made_now}

    def refuse(why: str) -> HooksResult:
        return HooksResult(on, p, state=read(env).state, refused=REFUSED_FAULT, why=why, **done)

    raw, _state, _was, why = _judge(p)
    if raw is None:
        return refuse(why)
    try:
        body = with_value(raw, "hooks", on)
    except ValueError as err:
        return refuse(str(err))
    wrote = False
    if body is not None:
        try:
            _replace(p.resolve(), body)
        except OSError as err:
            return refuse(f"could not be written ({_os_why(err)})")
        wrote = True
    after = read(env)
    result = HooksResult(on, p, state=after.state, wrote=wrote, **done)
    if after.state != (ON if on else OFF):
        return dataclasses.replace(result, refused=REFUSED_READBACK, why=after.why)
    return result
