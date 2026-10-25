"""The operator, and which seats are privileged: read from config, never a name in code.

Which users are administrators must be configurable.

ONE block in `~/.sessionkit/tools/settings/session.json`, the home
sessiontools made for the operator (the `session` tool, package `tools`),
extended with `privileged`:

    {"operator": {"address": "you@host.example", "mailbox": "you",
                  "privileged": ["ops", "you"]}}

- `privileged` names seats (or full addresses). The operator is ALWAYS
  privileged, listed or not: its `mailbox` (the seat name) and `address`.
- No operator configured (neither `address` nor `mailbox`) means NOTHING is
  privileged, whatever the list says, and every refusal names the key.
- FAIL SAFE: a missing, unreadable or malformed file is nothing configured.
  A bad file can only REMOVE privilege; it never grants any, and it never
  changes WHETHER a gate refuses, only the refusal's words.
- No block at all: the operator DEFAULTS to the account (`default`),
  which names the human and privileges nothing. `mailbox`, `address` and
  `privileged` stay empty, so no gate, no `$operator` match and no
  `session operator` field changes: a default can NAME, only config can
  TRUST. Its only readers are the words (`who_may`) and the shipped
  humans fallback (`_sender.operator_human`). On a real host the name is read from
  the password database (`account_name`), so it is config-shaped, never a
  name in code (SC4).
- "Absent" is judged on the RAW template and host layers, never the merged
  values: ANY `operator` key in any of those files, well formed or not,
  opts out of the default and is read as today. `"operator": null` is the
  documented opt-out; it survives the merge, even as a drop-in over a good
  block.

No environment path into privilege:

- The file is found from the PASSWORD DATABASE's home (`rules.account_home`),
  never `$HOME`: an agent can point `$HOME` at a planted file (the locked
  rules file's rule, D3, applied to privilege).
- Only the TEMPLATE and HOST layers are read. The seat and session layers are
  written by agents (one uid), so reading them would let an agent privilege
  itself.
- `AGENT_OPERATOR_ADDR` (sessiontools' ASK override) is never read here.
- The default's NAME is the password database's too (`pw_name` of this
  process's uid), never `$USER`, `$LOGNAME` or `getpass.getuser()`, which
  reads those first.

`read()` is the ONE call site. When sessiontools ships its public reader,
the swap is one call inside `read()`; that bumps the sessiontools pin, which
is a separate, later change.
"""

from __future__ import annotations

import os
import pwd
import re
from dataclasses import dataclass, replace
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from . import _settings
from ._maildir import strict_mode

#: The settings tool and the block within it.
TOOL, BLOCK = "session", "operator"
#: The package that ships the `session` tool, as sessiontools' map names it
#: (`PACKAGES["session"] == "tools"`). sessionmail spells it here,
#: and does not ask sessiontools. The floor (sessiontools 0.3.0) has the
#: map. sessiontools 0.1.0, the floor when this was written, had
#: none, and its settings root was the old, deprecated tree.
#: tests/test_operator_home.py holds this to sessiontools' map whenever the
#: installed sessiontools has one.
PACKAGE = "tools"
#: The `session` tool's settings folder, relative to the settings home:
#: `~/.sessionkit/tools/settings/`, sessiontools' `package_dir(PACKAGE)`.
#: The kit root is the mail tool's own (`_settings.ROOT`), so the two
#: folders cannot drift apart.
DIR = _settings.ROOT / PACKAGE / "settings"
#: Where an operator sets it: named in every refusal (SC2).
KEY_FILE = f"~/{(DIR / f'{TOOL}.json').as_posix()}"
#: The key a refusal tells the operator to set.
PRIVILEGED_KEY = "operator.privileged"
#: The operator's own keys, for the "nothing configured" refusal.
OPERATOR_KEY = "operator.mailbox"


@dataclass(frozen=True)
class Operator:
    """Who the operator is, and who is privileged, as config says.

    `operator` is `operator.address`, `mailbox` is `operator.mailbox`, each
    stripped, else None. `privileged` is sorted, case-folded and de-duplicated,
    and always holds the operator's mailbox and address when either is set.
    `problem` says why nothing was read (a bad file), for refusal text only.
    `default` is the installing account's name when NO block exists at all:
    it names the human and grants nothing. It is never in
    `privileged`, never `mailbox`, and never in `as_json` (the frozen
    three); only a consumer that reads `.default` sees it."""

    operator: str | None
    mailbox: str | None
    privileged: tuple[str, ...]
    problem: str | None = None
    default: str | None = None

    @property
    def configured(self) -> bool:
        """An operator is set (by mailbox or address)."""
        return bool(self.operator or self.mailbox)

    def as_json(self) -> dict:
        """EXACTLY the frozen shape `session operator` prints:
        `{"operator", "mailbox", "privileged"}`, nothing more."""
        return {"operator": self.operator, "mailbox": self.mailbox,
                "privileged": list(self.privileged)}

    def is_privileged(self, who: str | None) -> bool:
        """WHO (a seat's address, or a full address) is privileged: an EXACT,
        case-folded match against `privileged`. No globs, no host forms (host
        forms read `SESSIONMAIL_HOST`, an environment variable)."""
        if not who or not who.strip():
            return False
        return who.strip().casefold() in self.privileged


#: Nothing configured: nobody is privileged.
NOTHING = Operator(None, None, ())


def settings_home() -> Path:
    """The home the settings are read under: the password database's, NOT
    `$HOME`. THE test seam (the suite's conftest patches it, as it does
    `rules.locked_path`)."""
    from . import rules  # deferred: rules will read the operator too
    return rules.account_home()


def account_name() -> str | None:
    """The installing account's name: `pw_name` of this process's uid in the
    PASSWORD DATABASE, case-folded. Never `$USER`, `$LOGNAME` or
    `getpass.getuser()` (they read the environment first). None, so no
    default, when there is no pwd entry, at uid 0 (root installed nothing
    as a person), or when the name is not a safe local part. THE test seam
    for the default (the suite's conftest patches it to a fake name: on a
    real host it can be a live seat's name)."""
    uid = os.getuid()
    if uid == 0:
        return None
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return None
    return _safe_name(name)


def _safe_name(name: object) -> str | None:
    """NAME case-folded, when it is a safe local part (`_seat.seat_name`
    leaves it whole), else None. Nothing is guessed or repaired."""
    if not isinstance(name, str) or not name:
        return None
    from . import _seat  # deferred: keep this module's imports light
    folded = name.casefold()
    return folded if _seat.seat_name(folded) == folded else None


def _tool_dir(tool: str) -> Path:
    """TOOL's host settings folder under `settings_home()`. The mail
    tool's is sessionmail's own (`_settings.DIR`, the one definition its
    writer uses too), so its readers and its writer share one folder; the
    `session` tool's is `DIR`, `~/.sessionkit/tools/settings/`. Both are
    sessionmail's constants, never sessiontools' old, deprecated settings
    root: nothing here reads the tree that came before the kit's home.
    Any other tool has no folder here, never a guessed one: ValueError."""
    home = settings_home()
    if tool == _settings.TOOL:
        return _settings.path({"HOME": str(home)}).parent
    if tool == TOOL:
        return home / DIR
    raise ValueError(f"{tool}: sessionmail reads no settings folder for it "
                     f"(only {_settings.TOOL} and {TOOL})")


def files(tool: str = TOOL) -> tuple[Path, ...]:
    """Every path `host_values(tool)` reads, for a watcher
    (`rules.LiveLayers`): TOOL's template, its host file, its drop-in folder
    (a file added to or removed from it changes the folder's stamp) and each
    drop-in in it (one edited in place does not). TOOL is `session` by
    default, `read()`'s; `rules` also watches `mail`'s, for the alert runner
   . A running listener then re-reads `$operator` and the runner
    when that config changes, as it does when a rules file does. Nothing here reads their contents."""
    try:
        from sessiontools import settings as st
        tool_dir = _tool_dir(tool)
        drop_ins = tuple(p for p in st.layer_files(tool_dir, tool)
                         if p != tool_dir / f"{tool}.json")
    except Exception:  # noqa: BLE001 - no sessiontools: nothing is read, nothing to watch
        return ()
    return (tool_dir / f"{tool}.template.json", tool_dir / f"{tool}.json",
            tool_dir / f"{tool}.d", *drop_ins)


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _from_block(block: object) -> Operator:
    """The block's values under design a.3's rules. Pure."""
    if not isinstance(block, dict):
        return NOTHING
    address, mailbox = _text(block.get("address")), _text(block.get("mailbox"))
    raw = block.get("privileged")
    listed = [s.strip().casefold() for s in raw if isinstance(s, str) and s.strip()] \
        if isinstance(raw, list) else []
    if not (address or mailbox):
        problem = (f"{PRIVILEGED_KEY} is set but no operator is: set {OPERATOR_KEY} "
                   f"in {KEY_FILE}") if listed else None
        return replace(NOTHING, problem=problem)
    every = set(listed) | {v.casefold() for v in (address, mailbox) if v}
    return Operator(address, mailbox, tuple(sorted(every)))


def host_sources(tool: str) -> list:
    """TOOL's template and host layers, read now, UNMERGED: the
    `sessiontools.settings.Source` list `host_values` merges, under
    `settings_home()` (pwd's home, never `$HOME`). No seat, agent or session
    layer: agents write those. Raises on any failure. `read()` judges an
    ABSENT block on these raw layers, because the merge drops an
    empty dict and cannot tell "no block" from a malformed one."""
    from sessiontools import settings as st
    tool_dir = _tool_dir(tool)
    srcs = []
    template = tool_dir / f"{tool}.template.json"
    if S_ISREG(strict_mode(template)):
        srcs.append(st.Source("template", template, st.read_json_object(template)))
    _stat_layer_files(tool_dir, tool)
    srcs += [st.Source("host", p, st.read_json_object(p))
             for p in st.layer_files(tool_dir, tool)]
    return srcs


def _stat_layer_files(directory: Path, tool: str) -> None:
    """Stat, strictly, every path `sessiontools.settings.layer_files` tests,
    before it is asked: `<tool>.json`, the `<tool>.d` folder, and each
    `.json` entry in that folder. RAISES what `strict_mode` raises.

    `layer_files` tests with pathlib, and from CPython 3.14 pathlib answers
    False for a path it cannot stat. In a settings folder that lists but
    cannot be searched, every layer would then be left out without a word,
    and each key's default taken: `_settings.carrier()` would pick the drop
    on a host whose file names an MTA. Here the read fails instead, as it
    did up to 3.13, and the carrier is refused as unreadable.

    This restates `layer_files`' rules, and only for this check. It can go
    once the sessiontools this package requires raises there itself."""
    strict_mode(directory / f"{tool}.json")
    folder = directory / f"{tool}.d"
    if S_ISDIR(strict_mode(folder)):
        for entry in folder.iterdir():
            if entry.suffix == ".json":
                strict_mode(entry)


def host_values(tool: str) -> dict:
    """TOOL's merged settings, read now: `host_sources`, merged through
    `sessiontools.settings` (never the JSON file directly), `_locked`
    honoured by its merge. Raises on any failure; each caller decides what
    a failure means. `read()` reads TOOL `session`; `message verify` reads
    `mail`'s `registry` (design e.2) through the same door and the same
    test seam."""
    from sessiontools import settings as st
    return st.merge(host_sources(tool))[0]


def _default_operator(name: str) -> Operator:
    """The operator a host with NO block gets: NAME as `default`,
    and every other field NOTHING's. Name-only: it privileges no seat and
    fills no `$operator`.

    An OPEN question:
    (A) the default only NAMES the human, which is what is built here, or
    (B) the default IS the privileged operator. (B) would change ONE thing,
    HERE and nowhere else: `privileged=(name,)`. That is left UNWIRED on
    purpose (a seat can spell the account's name: same uid), no flag or
    environment variable switches it, and a test pins it unwired, so (B)
    is its own change, one that changes that test on purpose."""
    return replace(NOTHING, default=name)


def _default() -> Operator:
    """The no-block operator: the account's name (`account_name`, the seam),
    re-checked here, or NOTHING when there is none. A seam that fails is no
    default; nothing is guessed."""
    try:
        name = _safe_name(account_name())
    except Exception:  # noqa: BLE001 - fail safe: no pwd answer is no default
        name = None
    return _default_operator(name) if name else NOTHING


def tilde(text: str) -> str:
    """TEXT with the settings home shown as `~`. A settings error
    quotes its file by the ABSOLUTE path under `settings_home()`
    (sessiontools builds it from that string, unresolved), and that path
    holds the account's name: `/Users/<account>/.sessionkit/...`. A refusal
    shows it as `~/.sessionkit/...`, so no account name reaches user text.
    Only a whole path component matches (`/home/al` never eats
    `/home/alice`); a home of `/`, or none, leaves TEXT as it is."""
    try:
        home = str(settings_home()).rstrip("/")
    except Exception:  # noqa: BLE001 - no home to find: nothing in TEXT is under it
        return text
    if not home:
        return text
    return re.sub(re.escape(home) + r"(?![\w.-])", "~", text)


def read() -> Operator:
    """The operator block, read now (`host_sources`, merged). Anything that
    goes wrong is nothing configured, and NEVER the default; the reason is
    kept for the refusal, with the settings home shown as `~` (`tilde`).
    Only a truly ABSENT block (no template or host file has an `operator`
    key at all) is the default (`_default`); every present spelling, `{}`
    and `null` included, is read by `_from_block` as before."""
    try:
        from sessiontools import settings as st
        srcs = host_sources(TOOL)
        values = st.merge(srcs)[0]
    except Exception as exc:  # noqa: BLE001 - fail safe: a bad file is nothing configured
        return replace(NOTHING, problem=f"{KEY_FILE} could not be read ({tilde(str(exc))})")
    if not any(BLOCK in src.data for src in srcs):
        return _default()
    return _from_block(values.get(BLOCK))


# ── the words every gate uses (design c.1; SC4: never a person's name in code) ──

def who_may(op: Operator) -> str:
    """Who a privileged verb is for, as a refusal says it: `a privileged
    seat (here: ops, you, from operator.privileged in ~/.sessionkit/...)`,
    or, with none configured, how to configure one. Every form names the
    key (SC2). The names, when any, come from config, or, on a host with no
    block, from the password database (the default), named as a
    default that privileges no seat."""
    if op.privileged:
        return (f"a privileged seat (here: {', '.join(op.privileged)}, from "
                f"{PRIVILEGED_KEY} in {KEY_FILE})")
    if op.problem:
        why = f" ({op.problem})"
    elif op.default:
        why = (f" (no operator is configured: it defaults to this account, "
               f"{op.default}, which names the human and privileges no seat)")
    else:
        why = ""
    return (f"a privileged seat, and this host has none: set {OPERATOR_KEY} and "
            f"{PRIVILEGED_KEY} in {KEY_FILE}{why}")


def route(op: Operator, topic: str) -> str:
    """The request-by-mail door for everyone else: to the first privileged
    SEAT (an entry with no `@`, sorted), else the first entry. With none
    configured, the operator's key to set."""
    seats = [p for p in op.privileged if "@" not in p] or list(op.privileged)
    if not seats:
        return (f"no seat here may grant it: the operator sets {PRIVILEGED_KEY} "
                f"in {KEY_FILE}")
    return (f"request it by mail, from a privileged seat: "
            f"message send {seats[0]} -s '{topic}: ...' -f FILE")


__all__ = [
    "KEY_FILE",
    "NOTHING",
    "OPERATOR_KEY",
    "PRIVILEGED_KEY",
    "Operator",
    "account_name",
    "files",
    "host_sources",
    "host_values",
    "read",
    "route",
    "settings_home",
    "tilde",
    "who_may",
]
