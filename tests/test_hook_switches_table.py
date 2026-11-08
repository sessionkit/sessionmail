"""The hook table: what every mail hook does for every settings-file shape.

A characterization, written BEFORE the per-hook switches, and green on the
base commit they start from. It is the proof that a host with no per-hook key
keeps its behaviour: every cell here must stay exactly as it is.

Rows are the shapes of `~/.sessionkit/mail/settings/mail.json` with NO
per-hook key in it:

- `"hooks": true`, and `"hooks": false`;
- key missing (`{"version": 1}`);
- file missing, in its three sub-cases (the new file absent, and):
  - nothing to carry: the ride-along writes the template and says so;
  - a real old file at `~/.session/mail/settings.json`: its bytes become the
    new file, nothing is said, and that run is the converted bytes' own row;
  - the old folder `~/.settings/mail/` still real (stranded), or not to be
    checked: nothing is written, the ride-along says ONE line, the other hooks
    read "missing". The line is the stranded line; or, with the new folder
    real too (copied, not moved), the copied line instead; or, when the old
    folder cannot be searched, the fault line naming why;
- malformed: not JSON, not an object, `hooks` not a boolean, unreadable.

Columns are the four hooks, run as Claude Code runs them:

- the ride-along, `pager hook batch`, as PostToolBatch and as UserPromptSubmit
  (a task notice, with the carry stamp the page wrote);
- the Stop nag, `pager hook stop`;
- the birth, `pager hook start` (SessionStart), with a mail root and without;
- the mail guard, by subprocess on `hooks/mail-guard-hook`, the plugin's own
  PreToolUse entry.

Each cell runs twice over: on a plain seat, and on a seat whose pager is
REQUIRED, where the deaf check speaks in every row it reaches. Each cell
asserts exit code, stdout (what the model is handed, and the person's line),
stderr, and the exact set of files written, under the sandbox HOME, the state
home and the mail root. The ride-along also runs a second time, to pin
"once".

Hermetic: HOME, the state home and the mail root are tmp; the generator is a
fake whose runs are counted; SESSIONMAIL_SENDMAIL is a fake asserted never
run; the session id and the seat are made up; no date is written. A sweep of
the REAL home (read from the password database) at the end of the module
asserts that nothing this run could write there was written.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pwd
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from sessionmail import _birth, _seat, _settings, pager
from sessionmail.pager import cli

ROOT = Path(__file__).resolve().parents[1]
GUARD_SHIM = ROOT / "hooks" / "mail-guard-hook"

#: Made up, and never uuid-shaped: no live session carries it.
SID = "sess-switch-table"
SEAT = "example+demo.worker"
#: The doorbell's answer for a fresh box: the rows are not there yet, so the
#: birth runs the (fake) generator, detached.
NOT_COPIED = (False, "NOT COPIED: no alias map postfix reads copies this seat into this box")
#: Why no monitor is expected: the deaf check's plain reason.
HEADLESS = "headless (-p): no monitor runs"
MAIL_NAME = "table.M1P1.host:2,"
MAIL_SUBJECT = "table mail"

FAKE_GEN = """#!/bin/sh
echo "$*" >> "$GEN_RUNS"
"""
FAKE_SENDMAIL = """#!/bin/sh
echo "$*" >> "$SENDMAIL_RUNS"
cat > /dev/null
"""

HOOKS_TRUE = b'{"version": 1, "hooks": true}\n'
HOOKS_FALSE = b'{"version": 1, "hooks": false}\n'

#: A damaged send the guard must deny, and a clean one it must allow.
GUARD_DENIES = 'message send bob -s "run `date` now" -f body.md'
GUARD_ALLOWS = "message send bob -s 'ok' -f body.md"


def _not_json_why() -> str:
    try:
        json.loads('{"hooks": true')
    except ValueError as err:
        return f"not JSON: {err}"
    raise AssertionError("the not-JSON row parsed")


#: Row name -> (what is on disk, the fault's reason or None). The kinds of
#: "what is on disk" other than bytes are built by `Rig.shape`.
SHAPES = {
    "hooks-true": (HOOKS_TRUE, None),
    "hooks-false": (HOOKS_FALSE, None),
    "key-missing": (b'{"version": 1}\n', "no `hooks` key"),
    "file-missing": ("missing", None),
    "file-missing-old-file": ("old-file", None),
    "file-missing-stranded": ("stranded", None),
    "file-missing-copied": ("copied", None),
    "file-missing-unchecked": (
        "unchecked",
        _settings.unchecked_why(PermissionError(errno.EACCES, os.strerror(errno.EACCES)))),
    "malformed-not-json": (b'{"hooks": true', _not_json_why()),
    "malformed-list": (b'[{"hooks": true}]', "the top level is a list, not an object"),
    "malformed-hooks-string": (b'{"hooks": "true"}', '`hooks` is "true", not true or false'),
    "malformed-unreadable": ("unreadable", os.strerror(errno.EACCES)),
}
FAULTS = {name for name, (_, why) in SHAPES.items() if why is not None}
#: The file-missing sub-cases where `ensure` writes NOTHING: no install.
NOTHING_WRITTEN = {"file-missing-stranded", "file-missing-copied", "file-missing-unchecked"}
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
#: Rows built from a mode-000 file or folder, which root reads anyway.
MODE_000 = {"malformed-unreadable", "file-missing-unchecked"}


def shape_params() -> list:
    unreadable = pytest.mark.skipif(AS_ROOT, reason="root reads a mode-000 file or folder")
    return [pytest.param(name, id=name, marks=unreadable if name in MODE_000 else ())
            for name in SHAPES]


SEATS = [pytest.param(False, id="plain"), pytest.param(True, id="required")]


# ── the paths a cell may write, as the snapshot names them ──────────────────

#: The session's dir, under the snapshot's `sessions` root: the sessions leaf
#: as sessiontools resolves it for the sandbox HOME (`sessions_root`, below),
#: never a spelled `~/.session/sessions/`. Which tree that is (old, or the
#: fresh-new `~/.sessionkit/tools/state/sessions/`) is the resolver's call.
S = f"sessions:{SID}"
K = f"home:{_settings.DIR.as_posix()}"        # the host settings folder
#: An inject row: what was handed to the model is logged.
LOG = {f"+{S}/log/mail/pager.jsonl", f"+{S}/log/mail/seq"}
#: `ensure` (and the birth) install the schema and the template beside the file.
INSTALL = {f"+{K}/mail.schema.json", f"+{K}/mail.template.json"}
#: The ride-along gave the queued mail: taken, marked, stamped.
GIVEN = {f"+{S}/mail/marks", f"+{S}/mail/verdicts", f"+{S}/mail/ride/done/{MAIL_NAME}",
         f"-{S}/mail/ride/new/{MAIL_NAME}", f"+{S}/mail/ride/new",
         f"+{S}/mail/stamps/ride-along"}
SAID = {f"+{S}/mail/stamps/said"}
SAID_DEAF = {f"+{S}/mail/stamps/said-deaf"}
SAID_COPIED = {f"+{S}/mail/stamps/said-copied"}
CREATED = {f"+{K}/mail.json"} | INSTALL
CONVERTED = {f"+{K}/mail.json", f"~home:{_settings.OLD_RELATIVE.as_posix()}"} | INSTALL
#: A box born: the maildir, the born record, the seat's declared address.
BORN = {f"+mail:{SID}/cur", f"+mail:{SID}/new", f"+mail:{SID}/tmp", f"+{S}/mail/born",
        f"+{S}/log/settings.jsonl", f"+{S}/settings/.mail.json.lock",
        f"+{S}/settings/mail.d/70-live.json"}


def sessions_root() -> Path:
    """The sessions leaf for the process env (the sandbox HOME), asked of
    sessiontools: the one rule the product's `session_dir` reads by
    (`_seat._settings()`). A session's dir is `sessions_root() / SID`."""
    return _seat._settings().sessions_root()


def snapshot(roots: dict[str, Path]) -> dict[str, tuple]:
    """Every file, symlink and EMPTY directory under ROOTS (a directory with
    something in it is said by what is in it), keyed `label:relative`. A
    root nested in another is said under its own label: the walk of the
    outer one does not go into it. It still names the nested root itself,
    so an EMPTY one (made, or left, with nothing in it) is said under the
    outer label, as any empty folder is."""
    out: dict[str, tuple] = {}
    tops = set(roots.values())
    for label, top in roots.items():
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            nested = [d for d in dirnames if Path(dirpath) / d in tops]
            dirnames[:] = [d for d in dirnames if d not in nested]
            for name in dirnames + nested + filenames:
                p = Path(dirpath) / name
                st = p.lstat()
                key = f"{label}:{p.relative_to(top).as_posix()}"
                if stat.S_ISLNK(st.st_mode):
                    out[key] = ("link", os.readlink(p))
                elif stat.S_ISDIR(st.st_mode):
                    try:
                        empty = not any(p.iterdir())
                    except OSError:            # the unchecked row's mode-000 folder
                        out[key] = ("dir", stat.S_IMODE(st.st_mode), "unsearchable")
                        continue
                    if empty:
                        out[key] = ("dir", stat.S_IMODE(st.st_mode))
                else:
                    try:
                        content = hashlib.sha256(p.read_bytes()).hexdigest()
                    except OSError:
                        content = (st.st_size, st.st_mtime_ns)
                    out[key] = ("file", stat.S_IMODE(st.st_mode), content)
    return out


def written(before: dict, after: dict) -> set[str]:
    """`+` made, `-` removed, `~` changed."""
    return {("+" if k not in before else "-" if k not in after else "~") + k
            for k in set(before) | set(after) if before.get(k) != after.get(k)}


# ── the rig ─────────────────────────────────────────────────────────────────

class Rig:
    """One seat, one session, one sandbox, set to a ROW, plain or REQUIRED."""

    def __init__(self, tmp_path: Path, monkeypatch, required: bool) -> None:
        self.tmp = tmp_path
        self.home = Path(os.environ["HOME"])
        self.state = tmp_path / "state"
        self.root = Path(os.environ["SESSIONMAIL_ROOT"], "stores")
        self.root.mkdir(parents=True, exist_ok=True)     # a machine WITH mail
        monkeypatch.setenv("SESSIONMAIL_HOME", str(self.state))
        monkeypatch.setenv("TM_AGENT_ADDRESS", SEAT)
        monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
        monkeypatch.delenv(pager.SUBSCRIBE_VAR, raising=False)
        self.gen_runs = tmp_path / "gen-runs"
        gen = tmp_path / "fake-gen"
        gen.write_text(FAKE_GEN)
        gen.chmod(0o755)
        monkeypatch.setenv(_seat.GEN_VAR, str(gen))
        monkeypatch.setenv("GEN_RUNS", str(self.gen_runs))
        self.sendmail_runs = tmp_path / "sendmail-runs"
        sendmail = tmp_path / "fake-sendmail"
        sendmail.write_text(FAKE_SENDMAIL)
        sendmail.chmod(0o755)
        monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(sendmail))
        monkeypatch.setenv("SENDMAIL_RUNS", str(self.sendmail_runs))
        # Nothing listens: no stamp, and no listener found by ancestry.
        monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
        monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
        monkeypatch.setattr(_seat, "doorbell", lambda address, session, env=None: NOT_COPIED)
        # Claude is up (the conftest pins its pid), past its grace, headless:
        # on a required seat the deaf check gives its plain reason.
        monkeypatch.setattr(_seat, "claude_age", lambda pid: 600.0)
        monkeypatch.setattr(_seat, "monitor_expected",
                            lambda session, ps=None, env=None, claude=None:
                            SimpleNamespace(expected=False, why=HEADLESS))
        if required:
            monkeypatch.setenv(_seat.REQUIRED_VAR, "1")
        else:
            monkeypatch.delenv(_seat.REQUIRED_VAR, raising=False)
        self.legacy = self.home / _settings.LEGACY_DIR / "mail.json"
        self.old = self.home / _settings.OLD_RELATIVE
        self._sessions: Path | None = None

    def sessions(self) -> Path:
        """The sessions leaf, as the resolver answers it BEFORE the first
        hook runs (after the row's shape, which can decide it: an old file
        makes `~/.session/` real), and pinned there: every later answer must
        be the same path. So a hook that writes into a tree the resolver did
        not name is caught under `home:`, and is never blessed by the
        resolver re-reading the folder the hook just made. Asserted inside
        the sandbox HOME, so the label never names a path outside it."""
        found = sessions_root()
        if self._sessions is None:
            assert found.is_relative_to(self.home), (found, self.home)
            self._sessions = found
        assert found == self._sessions, f"the sessions leaf moved: {self._sessions} -> {found}"
        return found

    def session_dir(self) -> Path:
        return self.sessions() / SID

    def roots(self) -> dict[str, Path]:
        return {"home": self.home, "state": self.state, "mail": self.root,
                "sessions": self.sessions()}

    def shape(self, name: str) -> None:
        kind, _ = SHAPES[name]
        p = _settings.path()
        if isinstance(kind, bytes):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(kind)
        elif kind == "old-file":
            self.old.parent.mkdir(parents=True, exist_ok=True)
            self.old.write_bytes(HOOKS_TRUE)
        elif kind in ("stranded", "copied"):
            self.legacy.parent.mkdir(parents=True)
            self.legacy.write_bytes(HOOKS_TRUE)
            if kind == "copied":
                p.parent.mkdir(parents=True)      # the new folder real too, no file in it
        elif kind == "unchecked":
            self.legacy.parent.parent.mkdir(parents=True)
            self.legacy.parent.parent.chmod(0o000)   # `~/.settings/` cannot be searched
        elif kind == "unreadable":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(HOOKS_TRUE)
            p.chmod(0o000)
        else:
            assert kind == "missing"

    def queue_mail(self) -> Path:
        mail = self.tmp / MAIL_NAME
        mail.write_text(f"From: judy@example.invalid\nTo: {SEAT}@example.invalid\n"
                        f"Subject: {MAIL_SUBJECT}\nMessage-ID: <table.one@example.invalid>\n"
                        "\nEOM\n")
        link = _seat.queue_new(_seat.resolve(), SID) / MAIL_NAME
        os.link(mail, link)
        return link

    def carry_stamp(self) -> Path:
        d = _seat.stamp_dir(None, SID)
        d.mkdir(parents=True, exist_ok=True)
        (d / _seat.CARRY_STAMP).write_text("")
        return d / _seat.CARRY_STAMP

    def generator_runs(self) -> int:
        return len(self.gen_runs.read_text().splitlines()) if self.gen_runs.exists() else 0

    def wait_for_born_record(self, within: float = 15.0) -> None:
        """The birth's generator is a detached REAL process: wait for the
        line it adds to the born record, bounded, before the snapshot."""
        record = _birth.born_record(SID)
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if record.exists() and len(record.read_text().splitlines()) >= 2:
                return
            time.sleep(0.05)
        raise AssertionError(f"the born record never got the generator's line: {record}")


@pytest.fixture
def make_rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(shape: str, required: bool) -> Rig:
        rig = Rig(tmp_path, monkeypatch, required)
        rig.shape(shape)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        assert not rig.sendmail_runs.exists(), "a hook ran sendmail"
        p = _settings.path()
        if p.exists() and not p.is_symlink():
            p.chmod(0o600)             # the unreadable row: let tmp be cleaned
        old_top = rig.legacy.parent.parent
        if old_top.is_dir() and not old_top.is_symlink():
            old_top.chmod(0o700)       # the unchecked row, likewise


# ── running a hook, and what came out ───────────────────────────────────────

@dataclass
class Out:
    exit: int
    said: list[str]                    # the model's lines (additionalContext)
    person: str | None                 # the person's line (systemMessage)
    stdout: str
    stderr: str
    files: set[str] = field(default_factory=set)


def _parse(stdout: str, event: str) -> tuple[list[str], str | None]:
    if not stdout.strip():
        return [], None
    data = json.loads(stdout)
    out = data["hookSpecificOutput"]
    assert out["hookEventName"] == event, data
    return out["additionalContext"].splitlines(), data.get("systemMessage")


def run_cli(rig: Rig, verb: str, payload: dict, event: str) -> Out:
    before = snapshot(rig.roots())
    r = CliRunner().invoke(cli, ["hook", verb], input=json.dumps(payload))
    if verb == "start" and rig.root.is_dir():
        rig.wait_for_born_record()
    said, person = ([], None) if verb == "stop" else _parse(r.stdout, event)
    return Out(r.exit_code, said, person, r.stdout, r.stderr,
               written(before, snapshot(rig.roots())))


def batch(rig: Rig) -> Out:
    return run_cli(rig, "batch", {"hook_event_name": "PostToolBatch", "session_id": SID},
                   "PostToolBatch")


def ups(rig: Rig) -> Out:
    return run_cli(rig, "batch", {"hook_event_name": "UserPromptSubmit", "session_id": SID,
                                  "prompt": f"{pager.TASK_NOTIFICATION}done</task-notification>"},
                   "UserPromptSubmit")


def stop(rig: Rig) -> Out:
    return run_cli(rig, "stop", {"hook_event_name": "Stop", "session_id": SID}, "Stop")


def start(rig: Rig) -> Out:
    return run_cli(rig, "start", {"hook_event_name": "SessionStart", "source": "startup",
                                  "session_id": SID}, "SessionStart")


def guard(rig: Rig, command: str) -> Out:
    before = snapshot(rig.roots())
    proc = subprocess.run([str(GUARD_SHIM)], input=json.dumps({
        "session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": command}}), capture_output=True, text=True, timeout=30)
    return Out(proc.returncode, [], None, proc.stdout, proc.stderr,
               written(before, snapshot(rig.roots())))


# ── the lines, composed from the pager's own constants ──────────────────────

def deaf() -> str:
    return " ".join(pager.DEAF_NOT_LISTENING.replace("{why}", HEADLESS).split())


def state_line(required: bool) -> str:
    """The ride-along's state line when nothing listens."""
    return deaf() if required else pager.LINE_NOT_LISTENING


def fault_line(shape: str) -> str:
    line = pager.SETTINGS_FAULT.format(path=_settings.path(), error=SHAPES[shape][1])
    return " ".join(line.splitlines())


def stranded_line() -> str:
    return pager.SETTINGS_STRANDED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)


def copied_line() -> str:
    return pager.SETTINGS_COPIED.format(old=_settings.SHOWN_LEGACY_DIR, new=_settings.SHOWN_DIR)


def born_line(rig: Rig) -> str:
    return pager.BORN_LINE.format(box=rig.root / SID, session=SID)


def assert_mail_then(out: Out, last: str) -> None:
    """The queued mail was given, then the one state line."""
    assert out.said[-1] == last, out.said
    assert any(MAIL_SUBJECT in ln for ln in out.said[:-1]), out.said
    assert "EOM" in out.said[:-1], out.said


# ── the ride-along, PostToolBatch ───────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_the_ride_along_on_post_tool_batch(make_rig, shape, required):
    rig = make_rig(shape, required)
    rig.queue_mail()
    first = batch(rig)
    second = batch(rig)
    for out in (first, second):
        assert out.exit == 0 and out.stderr == "", out
    dr = SAID_DEAF if required else set()
    if shape in ("hooks-true", "file-missing-old-file"):
        # Mail, then the state line (DEAF on a required seat); said once.
        assert_mail_then(first, state_line(required))
        assert first.person is None
        settings = INSTALL if shape == "hooks-true" else CONVERTED
        assert first.files == LOG | GIVEN | SAID | settings, sorted(first.files)
        assert second.said == [] and second.files == {f"+{S}/log/mail/quiet"}, second
        if shape == "file-missing-old-file":
            # the old file's bytes ARE the new file; the old path now links to it
            assert _settings.path().read_bytes() == HOOKS_TRUE
            assert rig.old.is_symlink() and rig.old.resolve() == _settings.path().resolve()
    elif shape == "hooks-false":
        # Silent but for DEAF; the mail is not taken.
        assert first.said == ([deaf()] if required else []), first.said
        assert first.files == INSTALL | ((LOG | dr) if required else set()), sorted(first.files)
        assert second.said == [] and second.files == set(), second
    elif shape == "file-missing":
        # The template is written and said, to the model and the person; that
        # run says nothing else, DEAF included. The next run is the false row.
        where = _settings.path()
        assert first.said == [pager.SETTINGS_CREATED.format(path=where)], first.said
        assert first.person == pager.SETTINGS_CREATED_PERSON.format(path=where)
        assert first.files == LOG | CREATED, sorted(first.files)
        assert where.read_bytes() == _settings.fresh_body()
        assert second.said == ([deaf()] if required else []), second.said
        assert second.files == ({f"~{S}/log/mail/pager.jsonl", f"~{S}/log/mail/seq"} | dr
                                if required else set()), sorted(second.files)
    elif shape == "file-missing-stranded":
        # One line, once, plus DEAF; nothing written to either settings folder.
        assert first.said == [stranded_line()] + ([deaf()] if required else []), first.said
        assert first.files == LOG | SAID | dr, sorted(first.files)
        assert second.said == [] and second.files == set(), second
        assert rig.legacy.read_bytes() == HOOKS_TRUE
    elif shape == "file-missing-copied":
        # Both folders real: the copied line INSTEAD of the stranded one, with
        # its own record, plus DEAF; nothing written to either folder.
        assert first.said == [copied_line()] + ([deaf()] if required else []), first.said
        assert first.files == LOG | SAID_COPIED | dr, sorted(first.files)
        assert second.said == [] and second.files == set(), second
        assert rig.legacy.read_bytes() == HOOKS_TRUE
        assert not any(_settings.path().parent.iterdir())
    elif shape == "file-missing-unchecked":
        # The old folder cannot be searched: the fault line naming why, plus
        # DEAF; nothing written, no install.
        assert first.said == [fault_line(shape)] + ([deaf()] if required else []), first.said
        assert first.files == LOG | SAID | dr, sorted(first.files)
        assert second.said == [] and second.files == set(), second
        assert not _settings.path().parent.exists()
    else:
        assert shape in FAULTS
        # The fault line naming the error, once, plus DEAF; never the mail.
        assert first.said == [fault_line(shape)] + ([deaf()] if required else []), first.said
        assert first.files == LOG | SAID | dr | INSTALL, sorted(first.files)
        assert second.said == [] and second.files == set(), second
    assert first.person is None or shape == "file-missing"
    assert rig.generator_runs() == 0


# ── the ride-along, UserPromptSubmit ────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_the_ride_along_on_user_prompt_submit(make_rig, shape, required):
    """A task notice with the page's carry stamp: it carries only with the
    hooks on; in every other row it is a strict no-op, the stamp and the mail
    left where they were."""
    rig = make_rig(shape, required)
    link = rig.queue_mail()
    carry = rig.carry_stamp()
    out = ups(rig)
    assert out.exit == 0 and out.stderr == "", out
    if shape == "hooks-true":
        assert_mail_then(out, state_line(required))
        assert out.files == LOG | GIVEN | SAID | INSTALL | {f"-{S}/mail/stamps/carry"}, \
            sorted(out.files)
    else:
        assert out.stdout == "" and out.files == set(), (out.stdout, sorted(out.files))
        assert link.exists() and carry.exists()
    assert rig.generator_runs() == 0


def test_a_typed_prompt_does_not_carry_even_with_the_hooks_on(make_rig):
    """The hooks-on row's other half: a typed prompt does not carry by default."""
    rig = make_rig("hooks-true", False)
    rig.queue_mail()
    out = run_cli(rig, "batch", {"hook_event_name": "UserPromptSubmit", "session_id": SID,
                                 "prompt": "hello"}, "UserPromptSubmit")
    assert out.exit == 0 and out.stdout == "" and out.stderr == "" and out.files == set(), out


# ── the Stop nag ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_the_stop_nag(make_rig, shape, required):
    """Hooks on: exit 2, not listening. Every other row: exit 0, silent. A
    required seat gets DEAF instead, in EVERY row: the deaf check is above
    the settings gate."""
    rig = make_rig(shape, required)
    out = stop(rig)
    assert out.stdout == ""
    if required:
        assert out.exit == 2 and out.stderr.strip() == deaf(), out
        assert out.files == LOG, sorted(out.files)
    elif shape == "hooks-true":
        assert out.exit == 2 and out.stderr.strip() == pager.LINE_NOT_LISTENING, out
        assert out.files == LOG, sorted(out.files)
    else:
        assert out.exit == 0 and out.stderr == "" and out.files == set(), out
    if shape.startswith("file-missing"):
        assert not _settings.path().exists(), "the Stop hook never writes the file"
    assert rig.generator_runs() == 0


# ── the birth ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_the_birth(make_rig, shape, required):
    """The box is born in every row, and the generator runs once (detached).
    Only the hooks-on row says the box line; no row says a state line, a
    required seat included. The file is never written; the schema and the
    template are installed beside it, except where the old folder is real or
    cannot be checked."""
    rig = make_rig(shape, required)
    out = start(rig)
    assert out.exit == 0 and out.stderr == "", out
    assert all((rig.root / SID / d).is_dir() for d in ("cur", "new", "tmp"))
    settings = set() if shape in NOTHING_WRITTEN else INSTALL
    if shape == "hooks-true":
        assert out.said == [born_line(rig)], out.said
        assert out.files == BORN | settings | LOG, sorted(out.files)
    else:
        assert out.stdout == "", out.stdout
        assert out.files == BORN | settings, sorted(out.files)
    assert out.person is None
    assert rig.generator_runs() == 1


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_a_failed_birth(make_rig, shape, required):
    """No mail root: no box, exit 0, ONE line. With the hooks on it is said
    to the model; in every other row it goes to stderr, unsaid. A required
    seat is no different: the birth says no state line."""
    rig = make_rig(shape, required)
    rig.root.rmdir()
    out = start(rig)
    line = pager.START_NO_ROOT.format(root=rig.root)
    assert out.exit == 0, out
    if shape == "hooks-true":
        assert out.said == [line] and out.stderr == "", out
        assert out.files == LOG, sorted(out.files)
    else:
        assert out.stdout == "" and out.stderr.strip() == line, out
        assert out.files == set(), sorted(out.files)
    assert not rig.root.exists()
    time.sleep(0.2)                    # a detached generator, were one started
    assert rig.generator_runs() == 0


# ── the mail guard ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("shape", shape_params())
def test_the_mail_guard_judges(make_rig, shape, required):
    """Every row: the guard reads no settings, so it denies the damaged send
    (decision JSON at exit 0, and its row in the inject log) and lets the
    clean one through, silent."""
    rig = make_rig(shape, required)
    denied = guard(rig, GUARD_DENIES)
    assert denied.exit == 0 and denied.stderr == "", denied
    hso = json.loads(denied.stdout)["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny", hso
    assert hso["permissionDecisionReason"].startswith("mail-guard: ")
    assert denied.files == LOG, sorted(denied.files)
    rows = rig.session_dir() / "log" / "mail" / "pager.jsonl"
    (row,) = [json.loads(ln) for ln in rows.read_text().splitlines()]
    assert row["via"] == "pretooluse-deny", row
    allowed = guard(rig, GUARD_ALLOWS)
    assert (allowed.exit, allowed.stdout, allowed.stderr, allowed.files) == (0, "", "", set()), \
        allowed
    assert rig.generator_runs() == 0


# ── the session dir the snapshot names is the product's ────────────────────

def test_a_nested_root_is_said_once_and_an_empty_one_still_shows(tmp_path):
    """The `sessions` root sits inside HOME. What is in it is said under its
    own label only, never twice; and an EMPTY one is still said, under the
    outer label, so a hook that makes a bare sessions leaf is caught."""
    home = tmp_path / "home"
    leaf = home / ".sessionkit" / "tools" / "state" / "sessions"
    home.mkdir()
    roots = {"home": home, "sessions": leaf}
    before = snapshot(roots)
    leaf.mkdir(parents=True)
    assert written(before, snapshot(roots)) == {"+home:.sessionkit/tools/state/sessions"}
    (leaf / SID).mkdir()
    (leaf / SID / "seq").write_text("1")
    assert written(before, snapshot(roots)) == {f"+sessions:{SID}/seq"}


def test_the_rigs_session_dir_is_where_the_product_writes(make_rig):
    """The `sessions` root is the leaf the product's own session dir hangs
    from, so `S` names exactly the folder the hooks write into."""
    rig = make_rig("hooks-true", False)
    assert _seat.session_root(SID) == rig.session_dir() / _seat.MAIL_DIR
    assert _birth.born_record(SID).is_relative_to(rig.session_dir())


def test_an_old_only_home_keeps_the_old_session_dir(make_rig):
    """An old-only HOME, built here (`~/.session/sessions/` real, holding an
    earlier session's folder, nothing new): the resolver answers OLD, and a
    cell's rows land in `~/.session/sessions/<sid>/`, by both the resolved
    and the spelled path. The earlier folder is a used host's, and it keeps
    the leaf from being an empty folder the hook then fills (said as `-`)."""
    rig = make_rig("hooks-true", False)
    old = rig.home / ".session" / "sessions"
    (old / "sess-earlier").mkdir(parents=True)
    assert rig.sessions() == old
    out = stop(rig)
    assert out.exit == 2 and out.files == LOG, sorted(out.files)
    assert (old / SID / "log" / "mail" / "pager.jsonl").is_file()


# ── nothing reached the real home ───────────────────────────────────────────

def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.fixture(scope="module", autouse=True)
def real_home_untouched(tmp_path_factory):
    """A marker before the module; after it, `find -newer` over the real
    home. The kit's mail folder (the settings tree) is small and is swept
    whole; the kit's other folders belong to other tools, which write there
    while this runs, and nothing here can reach them. The mail root and the
    session dirs are live and large, so they are swept to the depth a box or
    a session dir sits at, for this module's made-up session id."""
    marker = tmp_path_factory.mktemp("sweep") / "marker"
    marker.touch()
    time.sleep(0.01)
    yield
    real = _real_home()
    kit = real / _settings.DIR.parent
    if kit.is_dir():
        found = subprocess.run(["find", str(kit), "-newer", str(marker)],
                               capture_output=True, text=True).stdout.split()
        assert found == [], f"the run wrote under the real {kit}: {found}"
    real_sessions = _seat._settings().sessions_root({"HOME": str(real)})
    live = [str(p) for p in (real / ".mail", real / ".session", real_sessions) if p.is_dir()]
    if live:
        found = subprocess.run(["find", *live, "-maxdepth", "3", "-newer", str(marker),
                                "-name", f"*{SID}*"], capture_output=True, text=True).stdout.split()
        assert found == [], f"the run wrote under the real home: {found}"
