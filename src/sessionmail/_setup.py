"""The setup verb: make a fresh host ready for sessionmail, printing first.

The name lives in ONE constant, `VERB`, and every line this module prints is
built from `COMMAND`, so a rename inside `message` is one edit. A move to
another command is that edit plus the ~10-line registration in
`message.py`; the logic is `main(argv)` and is reachable from anywhere.

The verb is listed on `message --help`. It is not an MCP tool
(`_mcp.NOT_A_TOOL`): it changes this host, a host owner's act. The kit's
installer calls it by argv.

What it does, in this order:

1. It builds the whole PLAN. Every step's `check` READS ONLY.
2. It prints the plan: the privileged (sudo) steps FIRST, each with its exact
   command, or `none`; then every step with its state.
3. Only then, unless `--dry-run`, does it apply the TODO steps, in order,
   stopping at the first failure.

The steps, in this order:

- `mail-root`: the agents root, made 0700. When the readers' root
  (`_maildir.root`) and the drop's (`_drop.mail_root(env)`, one resolver now)
  differ, setup REFUSES before applying anything: the drop would deliver into
  a tree the readers never open.
- `host`: this host's NAME, pinned once in the host settings file
  (`host_step`). A `host` already set is never touched; a file not
  there yet is DONE, because the hook that creates it writes the pin
  (`_settings.fresh_body`); a file with no `host` gains the live short
  hostname, lowercased, and no other change.
- `box:<name>`: the first boxes the mail drop needs: the
  operator's (`_operator.read()`: `mailbox`, else `default`) and every
  `--box NAME`. Made with `_seat.birth_box`, the birth's own box maker. The
  drop itself still never mints a box. Never a session box (the pager and the
  SessionStart hook birth those).
- `maps`: by the carrier (`regen_step`). Under the drop, DONE: no generator
  runs. Under an MTA, rebuilt once if a box was made, on a host that has
  maps; on a host with none, MANUAL naming `--write-maps`, the opt-in.
- `notmuch-profile`: the notmuch profile host mode reads (`_index`,
  `sessionkit` by default). DONE when its file exists: an old one while it
  is real (`$XDG_CONFIG_HOME/notmuch/<name>/config`, `~/.notmuch-config.<name>`),
  else the kit's. Else the kit's, `~/.sessionkit/mail/settings/notmuch/<name>/config`,
  is written from `notmuch/notmuch-config.sessionkit.template`, its database the mail
  root, only when no notmuch database is in the mail root: setup adopts no
  database (MANUAL), and never writes over a file. A notmuch config that is
  not sessionmail's, `~/.notmuch-config` included, is never read or written.
- `notmuch-index`: sessionmail's own index, built ONCE. DONE when notmuch
  reports a `database.hook_dir` for the profile (it opened a database).
  Otherwise it runs `notmuch --config=<profile> new --no-hooks`, and only
  on an EMPTY index: the profile is written in this same run (over a mail
  root with no database, by `notmuch-profile`'s own rule), or notmuch
  reports no hook dir AND no `.notmuch` is at the `database.path` it
  reports. A database already there is BLOCKED: setup runs no `new` over
  it. With no binary the step is BLOCKED and still names the `new` setup
  will run once notmuch is found, so one run names every link left.
- `notmuch-hook:post-new` and `notmuch-hook:post-insert`: links to the
  packaged post-new hook, in the `database.hook_dir` notmuch reports for
  that profile, asked with `--config=<profile>` and notmuch's own override
  variables dropped. When the hook dir cannot be known while the plan is
  built, ONE `notmuch-hook` step waits on `notmuch-index`: it asks the hook
  dir after the index is built, in the same run. With no profile nothing is
  linked anywhere. A different file there is never replaced.
- `delivery`: the pipe(8) wrapper `nm-deliver`, installed or refreshed at
  `/usr/local/etc/postfix-agents/nm-deliver` ($SESSIONMAIL_NM_DELIVER_DIR
  moves it) with the kit's notmuch profile path (`_index.profile_config`)
  baked in, so delivery indexes through the same config `message` reads
  (`delivery_step`). It also REPORTS whether postfix's `nminsert`
  service runs that path, and never edits postfix. A host with no
  `nminsert` service is not an insert host: DONE, nothing installed. The
  install is privileged (printed first, `sudo` only at a tty) unless the
  folder is writable. It waits on `notmuch-profile`: while the profile's
  file is not there and that step will not write it, the step is BLOCKED
  and installs nothing, since a wrapper baking in a missing file defers
  every delivery.
- `carrier`: DONE whatever the carrier, naming its value and where it came
  from (the variable, the host file, or the template's default). MANUAL only
  when an MTA is named and is not an executable file.
- `suffix`: MANUAL on a postfix host that has no MagicDNS suffix. A drop host
  needs none (the drop runs the generator with `--no-fqdn`).
- `pull-agent`, only with `--pull` (the opt-in; without it no pull config
  is read): the puller's LaunchAgent (`_pull_agent`). The pull config is
  resolved with setup's environment AND the job's exact one (HOME and the
  plist's PATH); a required key unset, a key set only by a variable, or a
  value a variable moves REFUSES the plan, naming each. DONE when the plist
  at the target holds exactly the rendered bytes and the job is loaded. A
  plist there that is not a sessionmail puller, or a job loaded under the
  label with no plist, is BLOCKED: never overwritten, never booted out.
  Otherwise the listener must answer (BLOCKED if not), then apply writes,
  lints (`plutil -lint`), boots out our loaded job and bootstraps it.

When steps are left, the run ends with ONE next-steps block: every step not
DONE, numbered, in plan order, with its detail, then the line to re-run.

One privileged step is registered: `delivery`, on an insert host whose
install folder this user cannot write. On a host with no postfix nothing
setup does needs root. The engine's privileged path: print first; run the
step's apply, which only PREPARES what the line reads (no root); then
`sudo -- ARGV` only at a tty, else left for the human. Only a TODO
privileged step prints its line; any other state prints its detail, so
a BLOCKED install offers no command to copy.

The carrier and the suffix go through ONE seam, `settings(env)` and
`_record(...)`. The carrier is read by the one resolver,
`_settings.carrier` (the variable, else the settings file, else the drop);
a carrier that is not one refuses the plan before any step runs.

Setup writes ONE key into the settings file, `host`, and only when it is
absent. Every other key it
leaves to a MANUAL line naming the key to write, and the variable that
beats it. Postfix is never edited: on a host
with no maps, setup writes none unless `--write-maps` asks.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

#: The verb's name. A rename inside `message` is this one edit.
VERB = "setup"
#: How every printed line names the verb.
COMMAND = f"message {VERB}"

#: Exit codes (this verb's own page; 3 is avoided because on the `message`
#: page it means "the MTA did not accept it").
EX_OK = 0          # every step is DONE
EX_FAILED = 1      # a step failed while applying; what ran before it stays
EX_USAGE = 2       # usage, or refused before anything was applied
EX_REMAINS = 4     # what setup can do is done; MANUAL/BLOCKED/privileged steps remain

#: A step's state, as `check` reads it.
DONE, TODO, BLOCKED, MANUAL = "DONE", "TODO", "BLOCKED", "MANUAL"

#: The packaged post-new hook (it ships 0755 in the wheel; `hooks/notmuch-post-new`
#: in the repo is a link to it). Linked as both of these hook names.
PACKAGED_HOOK = Path(__file__).parent / "notmuch" / "post-new"
#: The host profile `message setup` writes when there is none (D7).
PROFILE_TEMPLATE = Path(__file__).parent / "notmuch" / "notmuch-config.sessionkit.template"
HOOK_NAMES = ("post-new", "post-insert")
#: A link whose target ends in one of these is a sessionmail hook, from a wheel
#: or from a checkout (`hooks/notmuch-post-new`): DONE, left as is.
OUR_HOOK_TAILS = ("sessionmail/notmuch/post-new", "hooks/notmuch-post-new")
#: A DANGLING link ending here is ours to repair (a uv venv rebuilt on a new
#: Python minor moves site-packages). Anything else dangling is not ours.
REPAIRABLE_TAIL = "sessionmail/notmuch/post-new"

#: What notmuch is asked for the hook dir. It reads empty until the first
#: `notmuch new`.
HOOK_DIR_KEY = "database.hook_dir"
#: Where the profile's database is. Asked only when the hook dir reads empty:
#: a notmuch older than 0.32 has no `database.hook_dir` key at all.
DATABASE_PATH_KEY = "database.path"
NOTMUCH_TIMEOUT = 10.0
#: The one `notmuch new` setup runs indexes the mail root, so it gets minutes,
#: not NOTMUCH_TIMEOUT's seconds.
NOTMUCH_NEW_TIMEOUT = 600.0
#: The step that builds the empty index, and the argv tail it runs. No hook
#: runs in the run that links the hooks (as `store-index` builds its own).
INDEX_STEP = "notmuch-index"
NEW_ARGS = ("new", "--no-hooks")

#: The generator's own fold (`vmailbox-gen`, `san()`): a box whose name it
#: would rewrite gets no map row, so the drop would still refuse mail to it.
_BOX_NAME = re.compile(r"[a-z0-9_.-]+")
#: A session id's shape: a session box is the pager's to birth, never setup's.
_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class PlanRefused(Exception):
    """Setup cannot plan on this input: nothing is applied (exit 2). Its own
    class, not the sender's `Refused`: it is outside the send door's kinds."""


# ── the seam for the open settings ───────────────────────────────────────────

@dataclass(frozen=True)
class Wanted:
    """What setup knows about the carrier and the suffix."""
    carrier: "_settings.Carrier"
    suffix: str | None


def settings(env: Mapping[str, str]) -> Wanted:
    """What setup needs to know: the carrier through the one resolver
    (`_settings.carrier`: a set variable wins, else the settings file, else
    the drop), and the suffix's variable (`_magicdns` reads the settings
    file after it). Reads only; writes nothing."""
    from . import _settings
    return Wanted(carrier=_settings.carrier(env),
                  suffix=env.get("MAGICDNS_SUFFIX") or None)


def _record(step_id: str, key: str, value: str, var: str, why: str, *, also: str = ""
            ) -> Step:
    """A MANUAL step naming the settings KEY first: "write `"KEY": "VALUE"`
    in the host file (or set VAR)ALSO: WHY". Setup never writes the key
    itself (a key it wrote would outlive the reason it wrote it)."""
    from . import _settings
    return Step(step_id, f'write "{key}": "{value}" in {_settings.SHOWN} (or set {var}){also}: '
                f"{why}", manual=True)


def carrier_source(carrier: "_settings.Carrier") -> str:
    """Where CARRIER's value came from, as the carrier line says it."""
    from . import _settings
    if carrier.source == _settings.VARIABLE:
        return f"from ${_settings.CARRIER_VAR}"
    if carrier.source == _settings.HOST_LAYER:
        return f"from {_settings.SHOWN}"
    if carrier.source == _settings.TEMPLATE_LAYER:
        return f"the default, from {_settings.SHOWN_DIR}{_settings.INSTALLED_TEMPLATE}"
    return "the default"


# ── the plan engine ──────────────────────────────────────────────────────────

@dataclass
class Step:
    """One thing setup checks and, maybe, does.

    `check` reads only and returns (state, detail). `apply` does the TODO and
    returns one line saying what it did; it raises OSError (or Failed) on
    failure. A privileged step carries `argv`, run as `sudo -- ARGV`."""
    id: str
    what: str
    privileged: bool = False
    manual: bool = False
    check: Callable[[], tuple[str, str]] | None = None
    apply: Callable[[], str] | None = None
    argv: Sequence[str] = ()
    state: str = field(default="", init=False)
    detail: str = field(default="", init=False)

    def read(self) -> None:
        if self.manual:
            self.state, self.detail = MANUAL, self.what
        elif self.check is None:
            self.state, self.detail = TODO, self.what
        else:
            self.state, self.detail = self.check()


class Failed(Exception):
    """A step's apply failed; its message is the line to print."""


class Left(Exception):
    """A step's apply could not finish it, and nothing after it depends on
    it: the step is BLOCKED with this line, and the run goes on (exit 4)."""


def sudo_line(argv: Sequence[str]) -> str:
    return " ".join(["sudo", "--", *(shlex.quote(a) for a in argv)])


def run_plan(steps: list[Step], env: Mapping[str, str], out: TextIO, *, dry_run: bool,
             tty: bool, notes: Sequence[str] = ()) -> int:
    """Read every step, print the whole plan (privileged block first), then
    apply unless DRY_RUN. Returns the exit code."""
    for step in steps:
        step.read()
    width = max([len(s.id) for s in steps] + [8])
    print(f"{COMMAND}: the plan, printed whole before anything is applied", file=out)
    print("privileged steps (sudo):", file=out)
    privileged = [s for s in steps if s.privileged]
    if not privileged:
        print("  none", file=out)
    for s in privileged:
        # Only a step that will run offers its line: a BLOCKED one says why
        # instead, so nobody copies a command setup itself will not run.
        if s.state != TODO:
            print(f"  {s.id:<{width}}  {s.state:<7}  {s.detail}", file=out)
            continue
        print(f"  {s.id:<{width}}  {s.state:<7}  {sudo_line(s.argv)}", file=out)
        print(f"  {'':<{width}}           {s.what}", file=out)
    print("steps:", file=out)
    for s in steps:
        if not s.privileged:
            print(f"  {s.id:<{width}}  {s.state:<7}  {s.detail}", file=out)
    for note in notes:
        print(f"note: {note}", file=out)
    todo = [s for s in steps if s.state == TODO]
    if dry_run:
        print(f"dry run: nothing applied ({len(todo)} step(s) TODO)", file=out)
        human = [s for s in steps if s.state not in (DONE, TODO)]
        last = (f"run `{COMMAND}` to apply the {len(todo)} TODO step(s)." if todo
                else f"then run `{COMMAND}` again.")
        if human:
            next_steps(human, out, f"{COMMAND}: {len(human)} step(s) left for you:", last)
        elif todo:
            print(last, file=out)
        return EX_OK if not todo and _all_done(steps) else EX_REMAINS
    if todo:
        print("applying:", file=out)
    for s in todo:
        if s.privileged:
            # Its apply, when it has one, PREPARES the line (writes what the
            # sudo command reads); it never needs root.
            try:
                if s.apply:
                    s.apply()
            except Left as exc:
                s.state, s.detail = BLOCKED, str(exc)
                print(f"  {s.id:<{width}}  BLOCKED: {exc}", file=out)
                continue
            except (OSError, Failed) as exc:
                print(f"  {s.id:<{width}}  FAILED: {exc}", file=out)
                print(f"{COMMAND}: stopped at {s.id}; what ran before it stays. Re-run "
                      f"{COMMAND} once the cause is fixed.", file=out)
                return EX_FAILED
            line = _apply_privileged(s, env, tty)
            if s.state != DONE:
                s.detail = line
            print(f"  {s.id:<{width}}  {line}", file=out)
            continue
        try:
            line = s.apply() if s.apply else "nothing to do"
        except Left as exc:
            s.state, s.detail = BLOCKED, str(exc)
            print(f"  {s.id:<{width}}  BLOCKED: {exc}", file=out)
            continue
        except (OSError, Failed) as exc:
            print(f"  {s.id:<{width}}  FAILED: {exc}", file=out)
            print(f"{COMMAND}: stopped at {s.id}; what ran before it stays. Re-run "
                  f"{COMMAND} once the cause is fixed.", file=out)
            return EX_FAILED
        s.state = DONE
        print(f"  {s.id:<{width}}  {line}", file=out)
    left = [s for s in steps if s.state != DONE]
    if left:
        next_steps(left, out, f"{COMMAND}: done what it can; {len(left)} step(s) left for you:",
                   f"then run `{COMMAND}` again.")
        return EX_REMAINS
    print(f"{COMMAND}: every step DONE", file=out)
    return EX_OK


def next_steps(left: Sequence[Step], out: TextIO, head: str, last: str) -> None:
    """The ONE next-steps block, printed once at the end of a run that leaves
    steps: every step not DONE, in plan order, one numbered line each, with
    the same detail as its row, then LAST. A step that waits on another says
    so in its detail ("waits on <id>"), so one cause reads as one cause."""
    print(head, file=out)
    for n, s in enumerate(left, 1):
        print(f"  {n}. {s.id} ({s.state}): {s.detail}", file=out)
    print(last, file=out)


def _all_done(steps: Sequence[Step]) -> bool:
    return all(s.state == DONE for s in steps)


def _apply_privileged(step: Step, env: Mapping[str, str], tty: bool) -> str:
    """`sudo -- ARGV` at a tty; without one, left TODO for the human."""
    line = sudo_line(step.argv)
    if not tty:
        return f"NOT RUN (no tty to ask for a password): run it yourself: {line}"
    sudo = shutil.which("sudo", path=env.get("PATH"))
    if sudo is None:
        return f"NOT RUN (no sudo on PATH): run it yourself: {line}"
    proc = subprocess.run([sudo, "--", *step.argv], env=dict(env), check=False)
    if proc.returncode != 0:
        return f"FAILED (sudo exit {proc.returncode}): {line}"
    step.state = DONE
    return f"ran: {line}"


# ── the steps ────────────────────────────────────────────────────────────────

def roots(env: Mapping[str, str]) -> Path:
    """The boxes root, from the one resolver (`_maildir.root`). Setup once
    refused here when the readers' root and the drop's differed; they are
    one function now, so there is nothing to refuse. A retired root
    variable that is still set is NAMED by the mail-root step, not acted on."""
    from . import _maildir
    return _maildir.root(dict(env))


def mail_root_step(agents: Path, env: Mapping[str, str] | None = None) -> Step:
    from . import _maildir
    ignored = _maildir.retired_root_vars(dict(env) if env is not None else None)
    note = (f"; ignored, retired: {', '.join('$' + v for v in ignored)}" if ignored else "")

    def check() -> tuple[str, str]:
        if agents.is_dir():
            return DONE, str(agents) + note
        if os.path.lexists(agents):
            return BLOCKED, f"{agents} is there and is not a directory{note}"
        return TODO, f"make {agents} (0700){note}"

    def apply() -> str:
        _maildir.make_private_dirs(agents)
        return f"made {agents} (0700)"
    return Step("mail-root", f"the boxes root, {agents}", check=check, apply=apply)


#: The `host` step's line on a host whose settings file is not there yet.
HOST_AT_BIRTH = ("pinned when the first session creates the settings file "
                 "(the pager's hook writes it with \"host\": the live hostname, lowercased)")


def host_step(env: Mapping[str, str]) -> Step:
    """`host`: pin this host's NAME once (the host-rule spec's "setup
    pins the name once"), so a later hostname change cannot move it.

    - DONE, nothing written: a non-empty string `host` is set in the template
      or host layers, whatever it is ("never over a user's host"); or the host
      file is not there yet (`HOST_AT_BIRTH`: the hook that alone creates it,
      `_settings.ensure`, writes the pin into it).
    - TODO: the host file has no `host`. Apply adds `"host": <the live short
      hostname, lowercased>` and nothing else (`_settings.add_key`). A set
      $SESSIONMAIL_HOST still beats the pin, and the line says so.
    - MANUAL: the host file's `host` is there but is not a non-empty string.
    - BLOCKED: the live hostname is empty, or the host file cannot be read or
      is not a JSON object. Never written."""
    from . import _host, _operator, _settings
    target = _operator.settings_home() / _settings.RELATIVE
    shown = _settings.SHOWN
    pinned = _host.setting(env)
    if pinned:
        return Step("host", "", check=lambda: (DONE, f"{pinned!r}, from {shown}"))
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        if os.path.lexists(target):
            why = f"{shown} is a symlink to nothing: setup never writes it"
            return Step("host", why, check=lambda: (BLOCKED, why))
        return Step("host", "", check=lambda: (DONE, HOST_AT_BIRTH))
    except OSError as exc:
        why = f"{shown} could not be read ({exc.strerror or exc}): setup never writes it"
        return Step("host", why, check=lambda: (BLOCKED, why))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        why = f"{shown} is not a JSON object: setup never writes it"
        return Step("host", why, check=lambda: (BLOCKED, why))
    if _settings.HOST_KEY in data:
        return Step("host", f'"{_settings.HOST_KEY}": {json.dumps(data[_settings.HOST_KEY])} in '
                    f"{shown} is not a host name (a non-empty string): set it to this host's "
                    "name, or remove it and re-run setup to pin the hostname", manual=True)
    live = _host.live()
    if not live:
        why = "the hostname is empty, so there is no name to pin: set SESSIONMAIL_HOST"
        return Step("host", why, check=lambda: (BLOCKED, why))
    var = _host._variable(env)
    beaten = f" (${_host.VAR}={var} still wins while it is set)" if var else ""

    def check() -> tuple[str, str]:
        return TODO, f'write "{_settings.HOST_KEY}": "{live}" in {shown}{beaten}'

    def apply() -> str:
        try:
            wrote = _settings.add_key(target, _settings.HOST_KEY, live)
        except ValueError as exc:
            raise Failed(f"{shown}: {exc}; nothing was written") from None
        if not wrote:
            return f"{shown} gained a host while setup ran; left as it is"
        return f'wrote "{_settings.HOST_KEY}": "{live}" in {shown}{beaten}'
    return Step("host", f"this host's name, pinned in {shown}", check=check, apply=apply)


def box_name_problem(name: str) -> str | None:
    """Why NAME cannot be a box setup makes, or None."""
    if not name or name.startswith("."):
        return "a box name is not empty and does not start with a dot"
    if not _BOX_NAME.fullmatch(name):
        return ("the map generator folds a box name to lowercase [a-z0-9_.-]; this one "
                "would get no map row, so mail to it would still be refused")
    if _SESSION_ID.fullmatch(name):
        return ("that is a session id: a session's box is born by `pager listen` and the "
                "SessionStart hook, never by setup")
    return None


def box_step(agents: Path, name: str, made: list[str]) -> Step:
    from . import _seat
    box = agents / name

    def check() -> tuple[str, str]:
        if (box / "new").is_dir():
            return DONE, str(box)
        if os.path.lexists(box) and not box.is_dir():
            return BLOCKED, f"{box} is there and is not a directory"
        return TODO, f"make {box}/{{cur,new,tmp}} (0700)"

    def apply() -> str:
        _seat.birth_box(agents, name)
        made.append(name)
        return f"made {box}"
    return Step(f"box:{name}", f"the box {name}", check=check, apply=apply)


def operator_box() -> tuple[str | None, str]:
    """The operator's box name (`mailbox`, else `default`) and where it came
    from; (None, why) when there is none."""
    from . import _operator
    op = _operator.read()
    if op.mailbox:
        return op.mailbox, f"{_operator.OPERATOR_KEY} in {_operator.KEY_FILE}"
    if op.default:
        return op.default, "the installing account (no operator block is set)"
    why = op.problem or "no operator is configured and the account has no usable name"
    return None, (f"no operator box: {why}. Set {_operator.OPERATOR_KEY} in "
                  f"{_operator.KEY_FILE}, or pass --box NAME")


#: The opt-in that lets setup write postfix maps on a host that has none.
WRITE_MAPS = "--write-maps"


def live_maps() -> bool:
    """Whether this host HAS postfix maps: postconf names virtual_mailbox_maps
    and its file (or its compiled `.db`) exists. The seam the tests patch."""
    from . import _seat
    maps = _seat._postfix_maps("virtual_mailbox_maps")
    for m in maps or ():
        path = Path(m.split(":", 1)[1])
        if path.exists() or path.with_name(path.name + ".db").exists():
            return True
    return False


def regen_step(agents: Path, env: Mapping[str, str], boxes: Sequence[Step],
               made: list[str], *, write_maps: bool = False) -> Step:
    """Once, after the boxes: the postfix maps, by the carrier.

    - The mail drop: DONE, and the generator is never run; the drop reads
      the maps per send.
    - An MTA, on a host that has maps: rebuild them once if a box was made
      (what a birth does). A failed rebuild leaves the step BLOCKED: the
      boxes stay made.
    - An MTA, on a host with NO maps: setup writes none. A MANUAL step names
      the opt-in, WRITE_MAPS, which runs the one writer as a birth does.
      Setup never edits postfix's own configuration."""
    from . import _seat, _settings
    carrier = _settings.carrier(env)
    if carrier.kind == "drop":
        return Step("maps", "", check=lambda: (
            DONE, "the carrier is the mail drop: it reads the maps per send; none are written"))
    if carrier.kind == "refused":            # `plan` refuses first; never a step
        return Step("maps", carrier.why, check=lambda: (BLOCKED, carrier.why))
    if not write_maps and not live_maps():
        return Step("maps", f"no postfix maps exist on this host, and setup writes none: "
                    f"`{COMMAND} {WRITE_MAPS}` writes them (the postfix-configuration note "
                    f"says what main.cf must name); setup never edits postfix",
                    manual=True)

    def check() -> tuple[str, str]:
        if write_maps:
            return TODO, f"write the postfix maps ({WRITE_MAPS})"
        if any(s.state == TODO for s in boxes):
            return TODO, "rebuild the maps once the boxes are made"
        return DONE, "no box to make: the maps are not rebuilt"

    def apply() -> str:
        if not made and not write_maps:
            return "no box was made: the maps are not rebuilt"
        ok, line = _seat._run_generator(agents, dict(env), ())
        if not ok:
            raise Left(f"{line}. The boxes are made; mail to them is refused until the "
                       "maps are rebuilt")
        return line
    return Step("maps", "the postfix maps, rebuilt once if a box was made", check=check,
                apply=apply)


def profile_step(mail_root: Path, env: Mapping[str, str]) -> Step:
    """`notmuch-profile`: the profile host mode reads (`_index`). DONE when
    its file exists (`_index.host_profile_file`: an old file while it is
    real, else the kit's). Otherwise the kit's path
    (`_index.kit_profile_config`) is written from PROFILE_TEMPLATE, its
    database the mail root, only when no notmuch database is in the mail
    root already: setup adopts no database. Never written over."""
    from . import _index, _settings
    name, via = _index.host_profile(dict(env))
    why = _index.profile_name_problem(name)
    if why:
        line = f"{json.dumps(name)} ({via}) is not a notmuch profile: {why}"
        return Step("notmuch-profile", line, check=lambda: (BLOCKED, line))
    found, _tried = _index.host_profile_file(name, dict(env))
    if found is not None:
        return Step("notmuch-profile", "", check=lambda: (DONE, f"{found} (profile {name}, {via})"))
    database = mail_root / ".notmuch"
    if os.path.lexists(database):
        return Step("notmuch-profile", (
            f"a notmuch database already exists at {database} and no profile names it: setup "
            "adopts no database. Write the profile by hand (see the host-migration contract) "
            "or choose another mail root"), manual=True)
    target = _index.kit_profile_config(name, dict(env))     # nothing old is real here

    def check() -> tuple[str, str]:
        return TODO, f"write {target} (database.path={mail_root})"

    def apply() -> str:
        # A plain replace: the template's `new.ignore` holds a regex `$`.
        body = PROFILE_TEMPLATE.read_text().replace("${database_path}", str(mail_root))
        if not _settings._publish(target, body.encode()):
            raise Failed(f"{target} appeared while setup ran: it is never written over")
        return f"wrote {target} (database.path={mail_root})"
    return Step("notmuch-profile", f"the notmuch profile {name}", check=check, apply=apply)


class NotAsked(Exception):
    """A `notmuch config get` could not run; its message is the line to print."""


def notmuch_exe(env: Mapping[str, str]) -> tuple[str, str | None]:
    """(the name setup asks for, its path on PATH or None). The name is the
    variable, else the settings file's `notmuch.command`, else notmuch."""
    from . import _notmuch
    name = _notmuch.binary(dict(env))
    return name, shutil.which(name, path=env.get("PATH"))


def not_found(name: str, new_line: str) -> str:
    """What was measured when the binary is not there, and what setup does
    once it is. It states the measurement only (the HOLD on notmuch wording)."""
    from . import _notmuch
    return (f"notmuch: {name!r} not found (${_notmuch.BINARY_VAR}, else PATH), so the index "
            f"and hook steps are left. Once it is found, setup runs `{new_line}` once and "
            "links the hooks")


def config_get(prefix: Sequence[str], child: Mapping[str, str], key: str) -> str:
    """`<prefix> config get KEY`, stripped. Raises NotAsked when it cannot
    run. `notmuch config get` exits 0 with no database, so its exit proves
    nothing: only a value does."""
    shown = f"{' '.join(prefix)} config get {key}"
    try:
        proc = subprocess.run([*prefix, "config", "get", key], capture_output=True,
                              text=True, env=dict(child), timeout=NOTMUCH_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotAsked(f"`{shown}` could not run: {exc}") from None
    return proc.stdout.strip()


def hook_dir(env: Mapping[str, str]) -> tuple[Path | None, str]:
    """The hook dir notmuch reports for the host's profile, or (None, why).
    Asked through the one resolution (`_notmuch._engine`): `--config=<the
    profile>`, with notmuch's own override variables out of the child's
    environment, so a notmuch config that is not sessionmail's is never
    read. No profile is a refusal, never a fallback."""
    from . import _notmuch
    name, exe = notmuch_exe(env)
    if exe is None:
        return None, f"notmuch: {name!r} not found (${_notmuch.BINARY_VAR}, else PATH)"
    try:
        prefix, child = _notmuch._engine(exe, dict(env))
    except _notmuch.CouldNotLook as exc:
        return None, f"{exc}; no hook is linked anywhere until it exists"
    try:
        got = config_get(prefix, child, HOOK_DIR_KEY)
    except NotAsked as exc:
        return None, str(exc)
    if not got:
        return None, f"notmuch reports no {HOOK_DIR_KEY} for {prefix[1]}: it opened no database"
    return Path(got).expanduser(), ""


def existing_database(path: str) -> str:
    """The line for guard (ii): a database is there, so setup runs no `new`."""
    database = Path(path).expanduser() / ".notmuch"
    return (f"a notmuch database is at {database}, and notmuch reports no {HOOK_DIR_KEY} for "
            f"it (a notmuch older than 0.32 has no such key): setup runs no `notmuch new` over "
            "an existing database. Link post-new and post-insert into its hook dir by hand")


def build_index(env: Mapping[str, str]) -> str:
    """The `notmuch-index` apply: `<exe> --config=<profile> new --no-hooks`,
    ONCE, and only on an EMPTY index of sessionmail's own profile.

    Everything is asked again here, after the profile step's apply: the
    binary, the one resolution (`_notmuch._engine`: `--config=<profile>`,
    notmuch's override variables dropped, so `~/.notmuch-config` is never
    read), then the database path. A `.notmuch` already at that path is
    `Left` (BLOCKED, the run goes on): setup never runs `new` over a
    database. A `new` that fails, times out or cannot start is `Failed`."""
    from . import _notmuch
    name, exe = notmuch_exe(env)
    if exe is None:
        raise Left(f"notmuch: {name!r} not found (${_notmuch.BINARY_VAR}, else PATH)")
    try:
        prefix, child = _notmuch._engine(exe, dict(env))
    except _notmuch.CouldNotLook as exc:
        raise Left(f"{exc}; setup runs no `notmuch new` without its own profile") from None
    try:
        path = config_get(prefix, child, DATABASE_PATH_KEY)
    except NotAsked as exc:
        raise Left(str(exc)) from None
    if not path:
        raise Left(f"notmuch reports no {DATABASE_PATH_KEY} for {prefix[1]}: setup does not "
                   "guess where the index is")
    database = Path(path).expanduser() / ".notmuch"
    if os.path.lexists(database):
        raise Left(existing_database(path))
    argv = [*prefix, *NEW_ARGS]
    shown = " ".join(shlex.quote(a) for a in argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=child,
                              timeout=NOTMUCH_NEW_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise Failed(f"`{shown}` did not finish within {NOTMUCH_NEW_TIMEOUT:g}s") from None
    except OSError as exc:
        raise Failed(f"`{shown}` could not start: {exc}") from None
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise Failed(f"`{shown}` exited {proc.returncode}" + (f": {said[-1]}" if said else ""))
    return f"ran `{shown}`: the index is at {database}"


def hook_step(directory: Path, name: str) -> Step:
    from . import _maildir
    link = directory / name
    target = str(PACKAGED_HOOK)

    def check() -> tuple[str, str]:
        if os.path.islink(link):
            there = os.readlink(link)
            if there == target and os.path.exists(link):
                return DONE, f"{link} -> {there}"
            if os.path.exists(link):
                if there.endswith(OUR_HOOK_TAILS):
                    return DONE, f"{link} -> {there} (a sessionmail hook; left as is)"
                return BLOCKED, (f"{link} links to {there}, not a sessionmail hook: setup "
                                 "never replaces a different file")
            if there.endswith(REPAIRABLE_TAIL):
                return TODO, f"repair the dangling {link} -> {there}: link it to {target}"
            return BLOCKED, (f"{link} is a dangling link to {there}, not ours: setup never "
                             "replaces a different file")
        if os.path.lexists(link):
            return BLOCKED, f"{link} is a different file: setup never replaces it"
        return TODO, f"link {link} -> {target}"

    def apply() -> str:
        if not directory.is_dir():
            _maildir.make_private_dirs(directory)
        if os.path.islink(link) and not os.path.exists(link):
            os.unlink(link)                      # our own dangling link (check said so)
            os.symlink(target, link)
            return f"repaired {link} -> {target}"
        os.symlink(target, link)
        return f"linked {link} -> {target}"
    return Step(f"notmuch-hook:{name}", f"notmuch's {name} hook", check=check, apply=apply)


def deferred_hook_step(env: Mapping[str, str], index: Step) -> Step:
    """ONE `notmuch-hook` step, for when the hook dir cannot be known while
    the plan is built (no binary, no profile yet, or no index yet). It waits
    on INDEX: TODO when the index step is, else BLOCKED naming it. Its apply
    runs after the index step's: it asks the hook dir then, and links each
    of HOOK_NAMES that is TODO. Nothing after it depends on it, so what it
    cannot finish is `Left`, not `Failed`."""
    waits = f"waits on {index.id}"

    def check() -> tuple[str, str]:
        if index.state == TODO:
            return TODO, ("link post-new and post-insert in the hook dir notmuch reports once "
                          "the index exists")
        return BLOCKED, waits

    def apply() -> str:
        if index.state != DONE:
            raise Left(waits)
        directory, why = hook_dir(env)
        if directory is None:
            raise Left(f"{why}; no hook is linked")
        done, blocked = [], []
        for name in HOOK_NAMES:
            link = hook_step(directory, name)
            link.read()
            if link.state == TODO:
                done.append(link.apply())
            elif link.state == DONE:
                done.append(link.detail)
            else:
                blocked.append(link.detail)
        if blocked:
            raise Left("; ".join(done + blocked))
        return "; ".join(done)
    return Step("notmuch-hook", "notmuch's post-new and post-insert hooks", check=check,
                apply=apply)


def profile_shown(env: Mapping[str, str]) -> str:
    """The host profile's path, for a line that forecasts a call on it."""
    from . import _index
    name, _via = _index.host_profile(dict(env))
    if _index.profile_name_problem(name):
        return "<the profile>"
    return str(_index.profile_config(name, dict(env)))


def notmuch_steps(env: Mapping[str, str], profile: Step) -> list[Step]:
    """`notmuch-index`, then the hook step(s). The chain (binary, profile,
    index, hook dir, links) is one step per link, so ONE run names every
    link that is left, and a link this run fills in (the profile, the index)
    is not reported as missing by the steps after it.

    The index step runs `notmuch --config=<profile> new --no-hooks` once
    (`build_index`), and only when the index is EMPTY by both guards: (i)
    notmuch reports no `database.hook_dir` (or the profile is being written
    this run, over a mail root with no `.notmuch`: `profile_step` refuses
    otherwise), and (ii) no `.notmuch` is at the `database.path` notmuch
    reports. Every check here reads only."""
    from . import _notmuch
    name, exe = notmuch_exe(env)
    if exe is None:
        why = not_found(name, f"{name} --config={profile_shown(env)} {' '.join(NEW_ARGS)}")
        index = Step(INDEX_STEP, why, check=lambda: (BLOCKED, why))
        return [index, deferred_hook_step(env, index)]
    try:
        prefix, child = _notmuch._engine(exe, dict(env))
    except _notmuch.CouldNotLook as exc:
        refusal = str(exc)
        new_line = f"{name} --config={profile_shown(env)} {' '.join(NEW_ARGS)}"

        def check() -> tuple[str, str]:
            if not profile.state:
                profile.read()
            if profile.state == TODO:          # no database by construction (profile_step)
                return TODO, (f"run `{new_line}` once, after the profile is written: the "
                              "index is empty")
            if profile.state == DONE:
                return BLOCKED, f"{refusal}; setup runs no `notmuch new` without its profile"
            return BLOCKED, f"waits on {profile.id}"
        index = Step(INDEX_STEP, "the notmuch index", check=check, apply=lambda: build_index(env))
        return [index, deferred_hook_step(env, index)]

    def blocked(line: str) -> list[Step]:
        index = Step(INDEX_STEP, line, check=lambda: (BLOCKED, line))
        return [index, deferred_hook_step(env, index)]
    try:
        got = config_get(prefix, child, HOOK_DIR_KEY)
    except NotAsked as exc:
        return blocked(str(exc))
    if got:
        directory = Path(got).expanduser()
        index = Step(INDEX_STEP, "", check=lambda: (
            DONE, f"the database notmuch opened for {prefix[1]} (hook dir {directory})"))
        return [index, *(hook_step(directory, n) for n in HOOK_NAMES)]
    try:
        path = config_get(prefix, child, DATABASE_PATH_KEY)
    except NotAsked as exc:
        return blocked(str(exc))
    if not path:
        return blocked(f"notmuch reports no {HOOK_DIR_KEY} and no {DATABASE_PATH_KEY} for "
                       f"{prefix[1]}: setup does not guess where the index is")
    if os.path.lexists(Path(path).expanduser() / ".notmuch"):
        return blocked(existing_database(path))
    new_line = f"{name} {prefix[1]} {' '.join(NEW_ARGS)}"
    database = Path(path).expanduser() / ".notmuch"
    index = Step(INDEX_STEP, "the notmuch index", check=lambda: (
        TODO, f"run `{new_line}` once: no index is at {database} yet"),
        apply=lambda: build_index(env))
    return [index, deferred_hook_step(env, index)]


# ── the delivery wrapper, nm-deliver ─────────────────────────────────────────

#: The packaged pipe(8) wrapper, and the ONE line an install sets in it. The
#: package copy keeps the line empty and runs notmuch as it always did.
PACKAGED_NM_DELIVER = Path(__file__).parent / "postfix" / "nm-deliver"
NM_DELIVER_MARK = "KIT_NOTMUCH_CONFIG="
#: Where setup installs the wrapper; the variable moves it (tests install
#: into a throwaway prefix).
NM_DELIVER_DIR = "/usr/local/etc/postfix-agents"
NM_DELIVER_DIR_VAR = "SESSIONMAIL_NM_DELIVER_DIR"
#: The pipe service docs/postfix-configuration.md names for index at delivery.
INSERT_SERVICE = "nminsert"
DELIVERY_STEP = "delivery"
#: What a privileged install runs, after setup stages the rendered copy.
INSTALL = "/usr/bin/install"
#: The characters a baked config path may hold: it is single-quoted in sh,
#: so a quote, a space or a `$` is refused at install, never met at delivery.
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._@+=/-]+")


def config_path_problem(value: str) -> str:
    """"" when VALUE can be baked into nm-deliver: an absolute, normal path
    of safe characters. Otherwise why not."""
    if not value.startswith("/"):
        return "it is not an absolute path"
    if not _SAFE_PATH.fullmatch(value):
        return "it holds a character outside A-Za-z0-9._@+=/-"
    if os.path.normpath(value) != value:
        return "it is not a normal path (a //, /./, /../ or a trailing /)"
    return ""


def render_nm_deliver(config: Path | str) -> bytes:
    """The installed wrapper: the package copy, byte for byte, with its ONE
    `KIT_NOTMUCH_CONFIG=` line set to CONFIG, single-quoted. Raises
    ValueError when CONFIG cannot be baked (`config_path_problem`) or the
    package copy does not hold exactly one such line."""
    value = str(config)
    why = config_path_problem(value)
    if why:
        raise ValueError(f"the notmuch config {value!r} cannot be baked into nm-deliver: {why}")
    lines = PACKAGED_NM_DELIVER.read_text().split("\n")
    at = [i for i, line in enumerate(lines) if line == NM_DELIVER_MARK]
    if len(at) != 1:
        raise ValueError(f"{PACKAGED_NM_DELIVER} holds {len(at)} `{NM_DELIVER_MARK}` lines, "
                         "not one")
    lines[at[0]] = f"{NM_DELIVER_MARK}'{value}'"
    return "\n".join(lines).encode()


def insert_service(env: Mapping[str, str]) -> str | None:
    """postfix's `nminsert/unix` line (`postconf -M`), "" when master.cf has
    none, None when there is no postconf to ask ($NM_DELIVER_POSTCONF, the
    wrapper's own seam, else PATH). Raises NotAsked when postconf fails.
    Reads only."""
    exe = env.get("NM_DELIVER_POSTCONF") or shutil.which("postconf", path=env.get("PATH"))
    if not exe:
        return None
    shown = f"{exe} -M {INSERT_SERVICE}/unix"
    try:
        proc = subprocess.run([exe, "-M", f"{INSERT_SERVICE}/unix"], capture_output=True,
                              text=True, env=dict(env), timeout=NOTMUCH_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotAsked(f"`{shown}` could not run: {exc}") from None
    if proc.returncode != 0:
        raise NotAsked(f"`{shown}` exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return " ".join(proc.stdout.split())


def service_argv(line: str) -> tuple[str, str]:
    """(the program a pipe service runs, its `user=`), each "" when absent."""
    args = line.split()[8:]
    prog = next((a[len("argv="):] for a in args if a.startswith("argv=")), "")
    user = next((a[len("user="):].split(":", 1)[0] for a in args if a.startswith("user=")), "")
    return prog, user


def staged_nm_deliver(env: Mapping[str, str]) -> Path:
    """Where a privileged install stages the rendered wrapper: the kit's own
    settings folder (`$HOME/.sessionkit/mail/settings/postfix/nm-deliver`),
    which only this user writes, so the root `install` reads what setup wrote."""
    from . import _index
    return _index.kit_profiles(dict(env)).parent / "postfix" / "nm-deliver"


def _write_file(target: Path, body: bytes, mode: int) -> None:
    """BODY at TARGET in one step (a temp file in the same folder, then
    os.replace), with MODE."""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def delivery_step(env: Mapping[str, str], profile: Step) -> Step:
    """`delivery`: the installed nm-deliver, with the kit's notmuch profile
    baked in (`render_nm_deliver`, the path `_index.profile_config` names),
    and a READ-ONLY report on postfix's `nminsert` transport.

    - DONE, nothing installed: no postconf to ask, or master.cf has no
      `nminsert/unix` service. Not an insert host: setup never switches a
      host to index at delivery, so it installs no wrapper nothing runs.
    - BLOCKED: postconf fails; the service runs as another `user=` (the
      profile baked in would be the wrong user's); the profile cannot be
      named, or its path cannot be baked (refused here, at install); or the
      profile's file is not there and PROFILE (the `notmuch-profile` step,
      read before this one) will not write it. A wrapper that bakes in a
      file that is not there defers every delivery, so setup never installs
      one, and its apply checks again after PROFILE's has run.
    - DONE: the installed copy is byte for byte what setup would write, and
      the service's `argv=` runs it.
    - MANUAL: it is current, but the service runs another path (repoint
      `argv=` by hand; setup never edits postfix); or the install folder is
      not there.
    - TODO: install or refresh it. Written directly when its folder is
      writable; else a privileged step, `sudo -- install -m 0755 <staged>
      <target>`, after setup stages the rendered copy (`staged_nm_deliver`).
    The folder is `$SESSIONMAIL_NM_DELIVER_DIR`, else NM_DELIVER_DIR."""
    from . import _index
    target_dir = Path(env.get(NM_DELIVER_DIR_VAR) or NM_DELIVER_DIR)
    target = target_dir / "nm-deliver"

    def fixed(state: str, line: str) -> Step:
        return Step(DELIVERY_STEP, line, check=lambda: (state, line))
    try:
        line = insert_service(env)
    except NotAsked as exc:
        return fixed(BLOCKED, f"{exc}: cannot tell whether postfix delivers by {INSERT_SERVICE}")
    if not line:
        why = ("no postconf to ask" if line is None
               else f"master.cf has no {INSERT_SERVICE}/unix service")
        return fixed(DONE, f"not an insert host ({why}): no wrapper is installed; setup never "
                     "switches a host to index at delivery")
    prog, user = service_argv(line)
    runs = f"{INSERT_SERVICE}/unix runs {prog or '(no argv=)'}"
    try:
        me = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        me = str(os.getuid())
    if user and user != me:
        return fixed(BLOCKED, f"{runs} as user={user}, but setup runs as {me}: the profile it "
                     f"bakes in would be {me}'s. Run {COMMAND} as {user}")
    name, via = _index.host_profile(dict(env))
    problem = _index.profile_name_problem(name)
    if problem:
        return fixed(BLOCKED, f"{json.dumps(name)} ({via}) is not a notmuch profile: {problem}")
    config = _index.profile_config(name, dict(env))
    try:
        body = render_nm_deliver(config)
    except ValueError as exc:
        return fixed(BLOCKED, f"{exc}; no wrapper is installed")
    points = bool(prog) and os.path.realpath(prog) == os.path.realpath(target)
    report = runs if points else (f"{runs}, not {target}: repoint its argv= by hand "
                                  "(setup never edits postfix)")
    try:
        current: bytes | None = target.read_bytes()
    except FileNotFoundError:
        current = None
    except OSError as exc:
        return fixed(BLOCKED, f"{target} could not be read ({exc.strerror or exc})")
    baked = f"NOTMUCH_CONFIG={config} (profile {name}, {via})"

    def waits(installed: bool = False) -> str:
        head = (f"{config} is not there and {profile.id} is {profile.state or 'not run'}: "
                f"waits on {profile.id}. ")
        if installed:       # the copy is already in place: say what it does now
            return head + (f"{target} bakes it in, so every delivery through it defers "
                           "(4.3.0) until the file is there")
        return head + ("Setup installs no wrapper: one that bakes in a missing file "
                       "defers every delivery")

    def gated(state: str, line: str, *, installed: bool = False
              ) -> Callable[[], tuple[str, str]]:
        def check() -> tuple[str, str]:
            if profile.state != TODO and not config.is_file():
                return BLOCKED, waits(installed)
            return state, line
        return check

    def present() -> None:
        if not config.is_file():
            raise Left(waits())
    if current == body:
        if points:
            done = f"{target}, {baked}; {runs}"
            return Step(DELIVERY_STEP, done, check=gated(DONE, done, installed=True))
        return Step(DELIVERY_STEP, f"{target} is current, {baked}; but {report}", manual=True)
    if not target_dir.is_dir():
        return Step(DELIVERY_STEP, f"{target_dir} is not a folder: make it "
                    f"(`{sudo_line(['mkdir', '-p', str(target_dir)])}`), then run {COMMAND} "
                    f"again; {report}", manual=True)
    verb = "install" if current is None else "refresh (it is not what setup would write)"
    todo = f"{verb} {target}, {baked}; {report}"
    if os.access(target_dir, os.W_OK):
        def apply() -> str:
            present()
            _write_file(target, body, 0o755)
            return f"installed {target}, {baked}"
        return Step(DELIVERY_STEP, todo, check=gated(TODO, todo), apply=apply)
    staged = staged_nm_deliver(env)
    what = f"{todo}. Setup writes {staged} first, then this line installs it"

    def stage() -> str:
        from . import _maildir
        present()
        _maildir.make_private_dirs(staged.parent)
        _write_file(staged, body, 0o644)
        return f"wrote {staged}"
    return Step(DELIVERY_STEP, what, privileged=True, check=gated(TODO, what), apply=stage,
                argv=(INSTALL, "-m", "0755", str(staged), str(target)))


#: Setup builds the empty index once (`notmuch-index`) and schedules
#: nothing: the readers keep it fresh themselves (`_fresh`). Still a
#: note, because it says what keeps the index current and how to turn that
#: off. A note, not a step: it would never be DONE.
NOTMUCH_NEW_NOTE = ("setup runs `notmuch new` once, to build an empty index, and schedules "
                    "nothing: `memory` refreshes the index before it reads, and `message mark` "
                    "when an id is missing, each with a bounded `notmuch new --no-hooks` "
                    "(`notmuch.refresh`, seconds, default 2; 0 turns it off)")


def carrier_steps(env: Mapping[str, str]) -> list[Step]:
    """`carrier`, and on a postfix host `suffix`. Both through the seam."""
    from . import _magicdns, _sender, _settings
    wanted = settings(env)
    if wanted.carrier.kind == "refused":         # `plan` refuses first; never a step
        why = wanted.carrier.why
        return [Step("carrier", why, check=lambda: (BLOCKED, why))]
    source = carrier_source(wanted.carrier)
    if wanted.carrier.kind == "drop":
        return [Step("carrier", "", check=lambda: (
            DONE, f"the mail drop ({source}); no suffix is needed"))]
    mta = wanted.carrier.path or _sender.DEFAULT_SENDMAIL
    if not (os.path.isfile(mta) and os.access(mta, os.X_OK)):
        return [_record("carrier", _settings.CARRIER_KEY, _settings.CARRIER_DROP,
                        f"{_settings.CARRIER_VAR}={_settings.CARRIER_DROP}",
                        f"the MTA at {mta} ({source}) is not an executable file, so "
                        "`message send` refuses with no MTA")]
    steps = [Step("carrier", "", check=lambda: (DONE, f"the MTA at {mta} ({source})"))]
    # The seam's value, checked by the one resolver (it validates a set value,
    # else asks tailscale): today the same as reading the environment.
    found, why = _magicdns.lookup({**env, _magicdns.SUFFIX_VAR: wanted.suffix or ""})
    if found is None:
        steps.append(_record("suffix", _magicdns.FILE_KEY, "<suffix>", _magicdns.SUFFIX_VAR,
                             f"the postfix maps are not regenerated without it ({why})",
                             also=", or start tailscale"))
    else:
        steps.append(Step("suffix", "", check=lambda: (DONE, f"the MagicDNS suffix {found}")))
    return steps


def plan(env: Mapping[str, str], boxes: Sequence[str], *, write_maps: bool = False,
         pull: bool = False) -> tuple[list[Step], list[str]]:
    """Every step, in order, and the notes. Raises PlanRefused before any step
    is read or applied. Only PULL reads the pull config."""
    carrier = settings(env).carrier
    if carrier.kind == "refused":
        raise PlanRefused(carrier.why)
    for name in boxes:
        problem = box_name_problem(name)
        if problem:
            raise PlanRefused(f"--box {name!r}: {problem}")
    job = pull_job(env) if pull else None
    agents = roots(env)
    steps = [mail_root_step(agents, env), host_step(env)]
    names: list[str] = []
    op, op_why = operator_box()
    if op is None:
        steps.append(Step("box:operator", op_why, manual=True))
    elif box_name_problem(op):
        why = f"the operator's box {op!r} ({op_why}): {box_name_problem(op)}"
        steps.append(Step(f"box:{op}", why, check=lambda: (BLOCKED, why)))
    else:
        names.append(op)
    names += [n for n in dict.fromkeys(boxes) if n not in names]
    made: list[str] = []
    made_steps = [box_step(agents, name, made) for name in names]
    steps += made_steps
    steps.append(regen_step(agents, env, made_steps, made, write_maps=write_maps))
    profile = profile_step(agents.parent, env)
    steps.append(profile)
    steps += notmuch_steps(env, profile)
    steps.append(delivery_step(env, profile))
    steps += carrier_steps(env)
    if job is not None:
        steps.append(pull_step(job, env))
    return steps, [NOTMUCH_NEW_NOTE]


#: The opt-in that adds the pull step.
PULL = "--pull"


def pull_job(env: Mapping[str, str]) -> "_pull_agent.Job":
    """The puller's job, or PlanRefused naming every reason (`_pull_agent.resolve`)."""
    from . import _pull_agent
    try:
        return _pull_agent.resolve(env)
    except _pull_agent.Refused as exc:
        raise PlanRefused(str(exc)) from None


def pull_step(job: "_pull_agent.Job", env: Mapping[str, str]) -> Step:
    """`pull-agent`: the puller's LaunchAgent. `check` reads only; the
    listener is asked only when the step would be TODO."""
    from . import _pull_agent
    cfg = job.config
    host, port = cfg.listener

    def check() -> tuple[str, str]:
        if not _pull_agent.on_macos():
            return BLOCKED, (f"a LaunchAgent is macOS only (this is {sys.platform}): run "
                             "`python -m sessionmail._puller` under your own service manager")
        try:
            seen = _pull_agent.see(job, env)
        except _pull_agent.Failed as exc:
            return BLOCKED, str(exc)
        if seen.foreign:
            return BLOCKED, (f"{seen.foreign}: pick another pull.label, or remove it by hand. "
                             "Setup never overwrites it or boots it out")
        if seen.current == job.body and seen.loaded:
            return DONE, f"{job.target} loaded from {job.plist}"
        if not _pull_agent.listening(host, port):
            return BLOCKED, (f"nothing listens on {host}:{port}: the puller hands mail to a "
                             "local-only smtpd (pull.listener); see docs/pull.md")
        if seen.current is None:
            todo = f"write {job.plist}, lint it, bootstrap {job.target}"
        elif seen.current != job.body:
            todo = (f"replace {job.plist} (it differs from the rendered plist), lint it, "
                    + ("boot out and " if seen.loaded else "") + f"bootstrap {job.target}")
        else:
            todo = f"bootstrap {job.target} ({job.plist} is current; the job is not loaded)"
        return TODO, todo

    def apply() -> str:
        try:
            return _pull_agent.install(job, env)
        except _pull_agent.Failed as exc:
            raise Failed(f"{exc}. Re-run {COMMAND} {PULL} once the cause is fixed") from None
    return Step("pull-agent", f"the puller's LaunchAgent, {job.label}", check=check,
                apply=apply)


# ── the verb ─────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=COMMAND, add_help=False)
    ap.add_argument("--box", action="append", default=[], metavar="NAME")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(WRITE_MAPS, dest="write_maps", action="store_true")
    ap.add_argument(PULL, dest="pull", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None, *,
         out: TextIO | None = None, err: TextIO | None = None, tty: bool | None = None) -> int:
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    ap = parser()
    try:
        args, extra = ap.parse_known_args(list(argv or ()))
    except SystemExit:
        return EX_USAGE
    if extra:
        print(f"{COMMAND}: unknown argument(s): {' '.join(extra)} (see {COMMAND} --help)",
              file=err)
        return EX_USAGE
    try:
        steps, notes = plan(env, args.box, write_maps=args.write_maps, pull=args.pull)
    except PlanRefused as exc:
        print(f"{COMMAND}: REFUSED - {exc}. Nothing was applied.", file=err)
        return EX_USAGE
    if tty is None:
        tty = sys.stdin.isatty()
    return run_plan(steps, env, out, dry_run=args.dry_run, tty=tty, notes=notes)
