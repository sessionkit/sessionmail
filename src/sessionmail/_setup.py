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
  (`_maildir.root`) and the drop's (`_drop.mail_root(env) / "agents"`)
  differ, setup REFUSES before applying anything: the drop would deliver into
  a tree the readers never open.
- `box:<name>`: the first boxes the mail drop needs: the
  operator's (`_operator.read()`: `mailbox`, else `default`) and every
  `--box NAME`. Made with `_seat.birth_box`, the birth's own box maker. The
  drop itself still never mints a box. Never a session box (the pager and the
  SessionStart hook birth those).
- `maps`: by the carrier (`regen_step`). Under the drop, DONE: no generator
  runs. Under an MTA, rebuilt once if a box was made, on a host that has
  maps; on a host with none, MANUAL naming `--write-maps`, the opt-in.
- `notmuch-profile`: the notmuch profile host mode reads (`_index`,
  `sessionkit` by default). DONE when its file exists. Else it is written
  from `notmuch/notmuch-config.sessionkit.template`, its database the mail
  root, only when no notmuch database is in the mail root: setup adopts no
  database (MANUAL), and never writes over a file. A notmuch config that is
  not sessionmail's, `~/.notmuch-config` included, is never read or written.
- `notmuch-hook:post-new` and `notmuch-hook:post-insert`: links to the
  packaged post-new hook, in the `database.hook_dir` notmuch reports for
  that profile, asked with `--config=<profile>` and notmuch's own override
  variables dropped. With no profile the step is BLOCKED and nothing is
  linked anywhere. A different file there is never replaced.
- `carrier`: DONE whatever the carrier, naming its value and where it came
  from (the variable, the host file, or the template's default). MANUAL only
  when an MTA is named and is not an executable file.
- `suffix`: MANUAL on a postfix host that has no MagicDNS suffix. A drop host
  needs none (the drop runs the generator with `--no-fqdn`).

No privileged step is registered in this build: on a host with no postfix,
nothing setup does needs root. The engine's privileged path (print first;
`sudo -- ARGV` only at a tty, else left for the human) is tested with a fake
step, so the postfix steps can be added later as data.

The carrier and the suffix go through ONE seam, `settings(env)` and
`_record(...)`. The carrier is read by the one resolver,
`_settings.carrier` (the variable, else the settings file, else the drop);
a carrier that is not one refuses the plan before any step runs.

Setup writes NO key into the settings file. A MANUAL line names the key to
write, and the variable that beats it. Postfix is never edited: on a host
with no maps, setup writes none unless `--write-maps` asks.
"""
from __future__ import annotations

import argparse
import json
import os
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
NOTMUCH_TIMEOUT = 10.0

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
        return EX_OK if not todo and _all_done(steps) else EX_REMAINS
    if todo:
        print("applying:", file=out)
    for s in todo:
        if s.privileged:
            line = _apply_privileged(s, env, tty)
            print(f"  {s.id:<{width}}  {line}", file=out)
            continue
        try:
            line = s.apply() if s.apply else "nothing to do"
        except Left as exc:
            s.state = BLOCKED
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
        print(f"{COMMAND}: done what it can; {len(left)} step(s) left for you: "
              + ", ".join(f"{s.id} ({s.state})" for s in left), file=out)
        return EX_REMAINS
    print(f"{COMMAND}: every step DONE", file=out)
    return EX_OK


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
    """The agents root, asked of the readers and of the drop. Refused when
    they differ: the drop would deliver into a tree the readers never open."""
    from . import _drop, _maildir
    readers = _maildir.root(dict(env))
    dropped = _drop.mail_root(env) / "agents"
    if os.path.realpath(readers) != os.path.realpath(dropped):
        raise PlanRefused(
            f"the readers' agents root ({readers}, from ${_maildir.ROOT_VAR} else "
            f"{_maildir.DEFAULT_ROOT}) is not the mail drop's ({dropped}, from "
            f"$SESSIONMAIL_ROOT, else $SESSION_MAIL_ROOT, else $MAIL_ROOT/agents): the drop "
            f"would deliver into a tree the readers never open. Set them to the same place")
    return readers


def mail_root_step(agents: Path) -> Step:
    from . import _maildir

    def check() -> tuple[str, str]:
        if agents.is_dir():
            return DONE, str(agents)
        if os.path.lexists(agents):
            return BLOCKED, f"{agents} is there and is not a directory"
        return TODO, f"make {agents} (0700)"

    def apply() -> str:
        _maildir.make_private_dirs(agents)
        return f"made {agents} (0700)"
    return Step("mail-root", f"the agents root, {agents}", check=check, apply=apply)


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
    its file exists. Otherwise it is written from PROFILE_TEMPLATE, its
    database the mail root, only when no notmuch database is in the mail
    root already: setup adopts no database. Never written over."""
    from . import _index, _settings
    name, via = _index.host_profile(dict(env))
    why = _index.profile_name_problem(name)
    if why:
        line = f"{json.dumps(name)} ({via}) is not a notmuch profile: {why}"
        return Step("notmuch-profile", line, check=lambda: (BLOCKED, line))
    found, _tried = _index.profile_file(name, dict(env))
    if found is not None:
        return Step("notmuch-profile", "", check=lambda: (DONE, f"{found} (profile {name}, {via})"))
    database = mail_root / ".notmuch"
    if os.path.lexists(database):
        return Step("notmuch-profile", (
            f"a notmuch database already exists at {database} and no profile names it: setup "
            "adopts no database. Write the profile by hand (see the host-migration contract) "
            "or choose another mail root"), manual=True)
    target = _index.profile_config(name, dict(env))

    def check() -> tuple[str, str]:
        return TODO, f"write {target} (database.path={mail_root})"

    def apply() -> str:
        # A plain replace: the template's `new.ignore` holds a regex `$`.
        body = PROFILE_TEMPLATE.read_text().replace("${database_path}", str(mail_root))
        if not _settings._publish(target, body.encode()):
            raise Failed(f"{target} appeared while setup ran: it is never written over")
        return f"wrote {target} (database.path={mail_root})"
    return Step("notmuch-profile", f"the notmuch profile {name}", check=check, apply=apply)


def hook_dir(env: Mapping[str, str]) -> tuple[Path | None, str]:
    """The hook dir notmuch reports for the host's profile, or (None, why).
    Asked through the one resolution (`_notmuch._engine`): `--config=<the
    profile>`, with notmuch's own override variables out of the child's
    environment, so a notmuch config that is not sessionmail's is never
    read. No profile is a refusal, never a fallback. `notmuch config get`
    exits 0 with no database, so its exit proves nothing: only a path does."""
    from . import _notmuch
    name = _notmuch.binary(dict(env))
    exe = shutil.which(name, path=env.get("PATH"))
    if exe is None:
        return None, f"install notmuch: {name!r} is not found (${_notmuch.BINARY_VAR}, else PATH)"
    try:
        prefix, child = _notmuch._engine(exe, dict(env))
    except _notmuch.CouldNotLook as exc:
        return None, f"{exc}; no hook is linked anywhere until it exists"
    try:
        proc = subprocess.run([*prefix, "config", "get", HOOK_DIR_KEY], capture_output=True,
                              text=True, env=child, timeout=NOTMUCH_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"`{' '.join(prefix)} config get {HOOK_DIR_KEY}` could not run: {exc}"
    got = proc.stdout.strip()
    if not got:
        return None, (f"notmuch reports no {HOOK_DIR_KEY}: it has no database yet. Run "
                      f"`{name} {prefix[1]} new` once, then {COMMAND} again")
    return Path(got).expanduser(), ""


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


def notmuch_steps(env: Mapping[str, str]) -> list[Step]:
    directory, why = hook_dir(env)
    if directory is None:
        return [Step("notmuch-hook", why, check=lambda: (BLOCKED, why))]
    return [hook_step(directory, name) for name in HOOK_NAMES]


#: Nothing in sessionmail schedules `notmuch new` (SC3: the requirement is
#: named, not shipped). A note, not a step: it would never be DONE.
NOTMUCH_NEW_NOTE = ("nothing here runs `notmuch new`: `message mark` and `memory` read an "
                    "empty index until something does (launchd, cron, or by hand)")


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


def plan(env: Mapping[str, str], boxes: Sequence[str], *, write_maps: bool = False
         ) -> tuple[list[Step], list[str]]:
    """Every step, in order, and the notes. Raises PlanRefused before any step
    is read or applied."""
    carrier = settings(env).carrier
    if carrier.kind == "refused":
        raise PlanRefused(carrier.why)
    for name in boxes:
        problem = box_name_problem(name)
        if problem:
            raise PlanRefused(f"--box {name!r}: {problem}")
    agents = roots(env)
    steps = [mail_root_step(agents)]
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
    steps.append(profile_step(agents.parent, env))
    steps += notmuch_steps(env)
    steps += carrier_steps(env)
    return steps, [NOTMUCH_NEW_NOTE]


# ── the verb ─────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=COMMAND, add_help=False)
    ap.add_argument("--box", action="append", default=[], metavar="NAME")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(WRITE_MAPS, dest="write_maps", action="store_true")
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
        steps, notes = plan(env, args.box, write_maps=args.write_maps)
    except PlanRefused as exc:
        print(f"{COMMAND}: REFUSED - {exc}. Nothing was applied.", file=err)
        return EX_USAGE
    if tty is None:
        tty = sys.stdin.isatty()
    return run_plan(steps, env, out, dry_run=args.dry_run, tty=tty, notes=notes)
