"""The mail guard honours its own switch, `mail_guard`.

The guard (`hooks/mail-guard/hook.py`, PreToolUse on Bash and the message and
pager MCP tools) runs on system python3 and cannot import sessionmail, so it
reads the host settings file with its own stdlib copy of the switches' rule,
`guard_switched_off`:

- `"mail_guard": false` turns off the subject and body checks ONLY: a main
  thread's backtick subject is allowed, exit 0, no stdout, no stderr, no
  inject row. Whatever `hooks` says, a fault included. The subagent rules
  stay enforced whatever the switch says: a subagent that would take or
  mark the seat's mail, or switch its hooks, is denied at both doors (the
  security-first course change, 15:41, Desk card f6). The file is read only
  when the subject and body checks would run;
- `"mail_guard": true`, or absent, is ON: the guard judges, and a backtick
  subject is denied;
- a value that is not a boolean is ignored: the guard judges;
- a file that is missing, cannot be read, is not text, is not JSON or is not
  a JSON object decides nothing: the guard judges. An odd or broken settings
  file NEVER turns the guard off.

The PARITY test runs both readers - the guard's and `_settings.switches` -
over the do-001 table's file shapes, every value kind of the reader's test,
and every file state, and asserts they agree. It also pins the guard's
settings path to `_settings.RELATIVE` and its key to `_settings.MAIL_GUARD`,
as tests/test_pager_inject_deny.py keeps the guard's log protocol in step.

The file-level `Switches.why` (decided on purpose): on a file that decides
nothing, the guard judges and says NOTHING about the file. Its only voice is
a deny; an allow is silent. Every such file is also a `hooks` fault, so the
ride-along's fault line names it (and a non-boolean value is named by the
ride-along's switch_ignored line). The tests below pin both halves: the deny
is the guard's plain deny and nothing more, and each such file is a `hooks`
fault.

Fail-open behaviour is NOT changed: that is an open question (Q-A), not
this switch's.

Hermetic, by the do-001 table's rig: HOME, the state home and the mail root
are tmp; the fake SESSIONMAIL_SENDMAIL and the fake generator are asserted
never run; the session id and the seat are made up; no date is written. The
table's sweep of the REAL home runs at the end of this module too.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from sessionmail import _settings

from test_hook_switches_reader import ABSENT, VALUES
from test_hook_switches_table import (  # noqa: F401 - real_home_untouched is autouse
    AS_ROOT, GUARD_ALLOWS, GUARD_DENIES, GUARD_SHIM, LOG, SEATS, SHAPES, SID, Out, guard,
    make_rig, real_home_untouched, shape_params, snapshot, written)

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "mail-guard" / "hook.py"
NOT_AS_ROOT = pytest.mark.skipif(AS_ROOT, reason="root reads a mode-000 file")

#: A made-up subagent id; the payloads below are a subagent's.
AGENT_ID = "agent-switch-test"
#: A Bash command a subagent is denied (it would read the SEAT's mail).
SUBAGENT_DENIED = "message read 1"
#: An MCP tool a subagent is denied, through the other door.
SUBAGENT_MCP = "mcp__plugin_sessionmail_message__message_read"
#: The MCP mark tool, denied to a subagent the same way.
SUBAGENT_MCP_MARK = "mcp__plugin_sessionmail_message__message_mark"


def _load():
    spec = importlib.util.spec_from_file_location("mail_guard_switch", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load()


def _file(data: dict) -> bytes:
    return (json.dumps({"version": 1, **data}) + "\n").encode()


def _put(data: dict | bytes) -> Path:
    """Write DATA as the host file under the (tmp) process HOME."""
    p = _settings.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not p.is_symlink():
        p.chmod(0o600)
    p.write_bytes(data if isinstance(data, bytes) else _file(data))
    return p


def _rig(make_rig, data: dict | bytes, required: bool = False):
    """A rig on the hooks-true row, its file then replaced by DATA."""
    rig = make_rig("hooks-true", required)
    _put(data)
    return rig


def _agree(env=None) -> bool:
    """Both readers on the file as it is now: assert they agree, and give
    whether the guard is OFF."""
    off = hook.guard_switched_off(env)
    sw = _settings.switches(env)
    assert off is (not sw.mail_guard.on), (off, sw)
    return off


def _raw_guard(rig, payload: dict) -> Out:
    """The plugin's entry, `hooks/mail-guard-hook`, on PAYLOAD."""
    before = snapshot(rig.roots())
    proc = subprocess.run([str(GUARD_SHIM)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    return Out(proc.returncode, [], None, proc.stdout, proc.stderr,
               written(before, snapshot(rig.roots())))


def _subagent_bash(command: str) -> dict:
    return {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "agent_id": AGENT_ID, "agent_type": "general-purpose",
            "tool_input": {"command": command}}


def _subagent_mcp() -> dict:
    return {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": SUBAGENT_MCP,
            "agent_id": AGENT_ID, "agent_type": "general-purpose",
            "tool_input": {"id": "1"}}


def _assert_silent_allow(out: Out) -> None:
    assert (out.exit, out.stdout, out.stderr, out.files) == (0, "", "", set()), out


def _assert_plain_deny(out: Out) -> None:
    """The guard's deny, and nothing about the settings file: one decision
    JSON line, no stderr, its row in the inject log."""
    assert out.exit == 0 and out.stderr == "", out
    (line,) = out.stdout.splitlines()
    hso = json.loads(line)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny", hso
    reason = hso["permissionDecisionReason"]
    assert reason.startswith("mail-guard: "), reason
    for word in ("mail_guard", "settings", "mail.json", "ignored", "not JSON"):
        assert word not in reason, reason
    assert out.files == LOG, sorted(out.files)


# ── the pins: path, key, HOME ──────────────────────────────────────────────

def test_the_guards_settings_path_is_settings_relative():
    """The constant the guard reads by, part for part, is `_settings.RELATIVE`."""
    assert hook.SETTINGS_RELATIVE == _settings.RELATIVE.parts
    assert "/".join(hook.SETTINGS_RELATIVE) == _settings.RELATIVE.as_posix()


def test_the_guards_key_is_settings_mail_guard():
    assert hook.MAIL_GUARD_KEY == _settings.MAIL_GUARD == "mail_guard"
    assert hook.MAIL_GUARD_KEY in _settings.SWITCH_KEYS


def test_the_guard_finds_the_file_where_settings_does(tmp_path, monkeypatch):
    """HOME given, HOME empty and HOME not in the env: the same path both ways."""
    env = {"HOME": str(tmp_path / "given")}
    assert hook.settings_path(env) == str(_settings.path(env))
    monkeypatch.setenv("HOME", str(tmp_path / "process"))
    for env in ({}, {"HOME": ""}):
        assert hook.settings_path(env) == str(_settings.path(env))
        assert hook.settings_path(env) == str(tmp_path / "process" / _settings.RELATIVE)
    assert hook.settings_path() == str(_settings.path())


# ── parity: the guard's reader agrees with _settings.switches ──────────────

@pytest.mark.parametrize("shape", shape_params())
def test_parity_on_every_table_shape(make_rig, shape):
    """The do-001 table's file shapes, none with a `mail_guard` key: both
    readers say ON, so the guard judges on every row as it did."""
    make_rig(shape, False)
    assert _agree() is False


def _is_object(kind) -> bool:
    try:
        return isinstance(json.loads(kind), dict)
    except (TypeError, ValueError):
        return False


BYTE_SHAPES = [name for name, (kind, _) in SHAPES.items() if _is_object(kind)]


@pytest.mark.parametrize("shape", BYTE_SHAPES)
def test_parity_on_every_table_object_shape_with_mail_guard_false(make_rig, shape):
    """Each table shape that is a JSON object, `"mail_guard": false` added:
    OFF both ways, a `hooks` fault (no key, a string) included."""
    make_rig(shape, False)
    data = json.loads(SHAPES[shape][0])
    _put(_file({**data, "mail_guard": False}))
    assert _agree() is True


def test_parity_on_every_table_shape_that_is_not_an_object_with_mail_guard_false(make_rig):
    """The table's not-JSON and list rows, `"mail_guard": false` inside
    each: the file decides nothing, so ON both ways."""
    make_rig("hooks-true", False)
    for data in (b'{"hooks": true, "mail_guard": false', b'[{"mail_guard": false}]'):
        _put(data)
        assert _agree() is False, data


@pytest.mark.parametrize("kind", list(VALUES))
@pytest.mark.parametrize("hooks", [True, False, "absent"],
                         ids=["hooks-true", "hooks-false", "hooks-absent"])
def test_parity_on_every_value_kind(tmp_path, kind, hooks):
    """`mail_guard` holds every value kind of the reader's test, beside a
    `hooks` that is true, false or absent: the guard is OFF exactly when
    `_settings.switches` says the switch is off - the boolean false alone."""
    value, on, _, _ = VALUES[kind]
    home = tmp_path / "home"
    env = {"HOME": str(home)}
    data = {} if hooks == "absent" else {"hooks": hooks}
    if value is not ABSENT:
        data["mail_guard"] = value
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.write_bytes(json.dumps(data).encode())
    assert _agree(env) is (not on)


@pytest.mark.parametrize("key", [k for k in _settings.SWITCH_KEYS if k != "mail_guard"])
def test_parity_another_switch_false_never_turns_the_guard_off(tmp_path, key):
    home = tmp_path / "home"
    env = {"HOME": str(home)}
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.write_bytes(_file({"hooks": False, key: False}))
    assert _agree(env) is False


@pytest.mark.parametrize("text, off", [
    pytest.param(b'{"mail_guard": false, "mail_guard": true}', False, id="dup-last-true"),
    pytest.param(b'{"mail_guard": true, "mail_guard": false}', True, id="dup-last-false"),
    pytest.param(b'{"Mail_Guard": false}', False, id="typo-case"),
    pytest.param(b'{"mail-guard": false}', False, id="typo-dash"),
    pytest.param(b'{"mail_guard ": false}', False, id="typo-space"),
    pytest.param(b'{"mail_guard": false}', True, id="bare-false"),
    pytest.param(b' \n{"mail_guard": false}\n\n', True, id="whitespace-around"),
    pytest.param(b'\xef\xbb\xbf{"mail_guard": false}', False, id="utf8-bom"),
    pytest.param(b'{"mail_guard": false}\xff', False, id="not-utf8"),
    pytest.param(b'{"mail_guard": false', False, id="not-json-truncated"),
    pytest.param(b"", False, id="not-json-empty"),
    pytest.param(b"mail_guard: false\n", False, id="not-json-yaml"),
    pytest.param(b"[" * 100_000 + b"]" * 100_000, False, id="not-json-too-deep"),
    pytest.param(b'{"mail_guard": ' + b"1" * 5000 + b"}", False, id="not-json-huge-number"),
    pytest.param(b'{"mail_guard": NaN}', False, id="nan-is-ignored"),
    pytest.param(b'[{"mail_guard": false}]', False, id="not-an-object-list"),
    pytest.param(b'"mail_guard"', False, id="not-an-object-string"),
    pytest.param(b"false", False, id="not-an-object-false"),
    pytest.param(b"null", False, id="not-an-object-null"),
])
def test_parity_on_every_file_text(tmp_path, text, off):
    home = tmp_path / "home"
    env = {"HOME": str(home)}
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.write_bytes(text)
    assert _agree(env) is off


def _env(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    return home, {"HOME": str(home)}


def test_parity_with_no_file_and_no_folder(tmp_path):
    home, env = _env(tmp_path)
    assert _agree(env) is False
    (home / _settings.ROOT).mkdir()
    assert _agree(env) is False
    assert sorted(p.name for p in home.rglob("*")) == [_settings.ROOT.name], \
        "neither reader creates anything"


@NOT_AS_ROOT
def test_parity_on_a_mode_000_file_and_an_unsearchable_folder(tmp_path):
    home, env = _env(tmp_path)
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.write_bytes(_file({"hooks": True, "mail_guard": False}))
    assert _agree(env) is True
    try:
        p.chmod(0o000)
        assert _agree(env) is False
        p.chmod(0o600)
        p.parent.chmod(0o000)
        assert _agree(env) is False
    finally:
        p.parent.chmod(0o700)
        p.chmod(0o600)


def test_parity_on_a_directory_or_a_file_where_one_does_not_go(tmp_path):
    home, env = _env(tmp_path)
    (home / _settings.RELATIVE).mkdir(parents=True)
    assert _agree(env) is False
    home2, env2 = _env(tmp_path / "two")
    (home2 / _settings.DIR).parent.mkdir(parents=True)
    (home2 / _settings.DIR).write_bytes(_file({"mail_guard": False}))
    assert _agree(env2) is False


def test_parity_on_symlinks(tmp_path):
    """A symlink to nothing decides nothing; a symlink to a real file is
    followed, both ways."""
    home, env = _env(tmp_path)
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.symlink_to(tmp_path / "nowhere.json")
    assert _agree(env) is False
    real = tmp_path / "real.json"
    real.write_bytes(_file({"mail_guard": False}))
    p.unlink()
    p.symlink_to(real)
    assert _agree(env) is True


def test_the_guards_reader_never_raises(tmp_path, monkeypatch):
    """Whatever goes wrong inside it, the reader says the guard is ON."""
    home, env = _env(tmp_path)
    p = home / _settings.RELATIVE
    p.parent.mkdir(parents=True)
    p.write_bytes(_file({"mail_guard": False}))

    def boom(*a, **k):
        raise RuntimeError("an unexpected failure")

    monkeypatch.setattr(hook.json, "loads", boom)
    assert hook.guard_switched_off(env) is False


def test_the_guard_never_reads_the_old_settings_homes(tmp_path):
    """Only the host file counts: `false` in the old folder or the old file
    leaves the guard on, both ways."""
    home, env = _env(tmp_path)
    for old in (home / _settings.LEGACY_DIR / "mail.json", home / _settings.OLD_RELATIVE):
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(_file({"hooks": True, "mail_guard": False}))
    assert _agree(env) is False


# ── the guard, by the plugin's entry: off allows, on judges ────────────────

OFF_FILES = [
    pytest.param({"hooks": True, "mail_guard": False}, id="hooks-true"),
    pytest.param({"hooks": False, "mail_guard": False}, id="hooks-false"),
    pytest.param({"mail_guard": False}, id="hooks-fault-no-key"),
    pytest.param({"hooks": "true", "mail_guard": False}, id="hooks-fault-string"),
    pytest.param({"hooks": False, "mail_guard": False, "birth": False, "ride_along": False,
                  "stop_nag": False}, id="all-off"),
]


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", OFF_FILES)
def test_mail_guard_false_allows_a_backtick_subject(make_rig, data, required):
    """OFF: the send the guard exists to deny goes through, exit 0, nothing
    said, no inject row, nothing written. A clean send too."""
    rig = _rig(make_rig, data, required)
    _assert_silent_allow(guard(rig, GUARD_DENIES))
    _assert_silent_allow(guard(rig, GUARD_ALLOWS))
    assert rig.generator_runs() == 0


#: What a subagent is denied, through each door, whatever the switch says:
#: reading or marking the seat's mail, and switching the seat's hooks.
SUBAGENT_PAYLOADS = [
    pytest.param(lambda: _subagent_bash(SUBAGENT_DENIED), id="bash-read"),
    pytest.param(lambda: _subagent_bash("message mark 1 read"), id="bash-mark"),
    pytest.param(lambda: _subagent_bash("pager settings hooks mail_guard on"),
                 id="bash-settings-hooks-write"),
    pytest.param(_subagent_mcp, id="mcp-read"),
    pytest.param(lambda: {**_subagent_mcp(), "tool_name": SUBAGENT_MCP_MARK,
                          "tool_input": {"ids": ["1"], "mark_": "done"}}, id="mcp-mark"),
]


def _assert_subagent_deny(out: Out) -> None:
    """The subagent rule's deny: one decision JSON line, no stderr, its row
    in the inject log."""
    assert out.exit == 0 and out.stderr == "", out
    (line,) = out.stdout.splitlines()
    hso = json.loads(line)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny", hso
    assert hso["permissionDecisionReason"].startswith("mail-guard: a subagent "), hso
    assert out.files == LOG, sorted(out.files)


@pytest.mark.parametrize("payload", SUBAGENT_PAYLOADS)
@pytest.mark.parametrize("data", OFF_FILES)
def test_mail_guard_false_still_denies_a_subagent_at_both_doors(make_rig, data, payload):
    """OFF turns off the subject and body checks ONLY: a subagent that would
    take or mark the seat's mail, or switch its hooks, is still denied, at
    the Bash door and the MCP door, whatever `hooks` says."""
    rig = _rig(make_rig, data)
    _assert_subagent_deny(_raw_guard(rig, payload()))


@pytest.mark.parametrize("payload", SUBAGENT_PAYLOADS)
def test_the_subagent_calls_are_denied_when_on(make_rig, payload):
    """The control for the test above: the same calls, the switch ON."""
    rig = _rig(make_rig, {"hooks": True, "mail_guard": True})
    _assert_subagent_deny(_raw_guard(rig, payload()))


@pytest.mark.parametrize("data", OFF_FILES)
def test_mail_guard_false_lets_a_subagents_backtick_subject_through(make_rig, data):
    """A subagent's send that only the subject check would deny is allowed
    when OFF (the switch is about that check), and denied when ON."""
    rig = _rig(make_rig, data)
    _assert_silent_allow(_raw_guard(rig, _subagent_bash(GUARD_DENIES)))
    _put({"hooks": True, "mail_guard": True})
    _assert_plain_deny(_raw_guard(rig, _subagent_bash(GUARD_DENIES)))


ON_FILES = [
    pytest.param({"hooks": True, "mail_guard": True}, id="true"),
    pytest.param({"hooks": True}, id="absent"),
    pytest.param({"hooks": False}, id="absent-hooks-false"),
    pytest.param({"hooks": False, "mail_guard": True}, id="true-hooks-false"),
    pytest.param({"mail_guard": True}, id="true-hooks-fault"),
    pytest.param({"hooks": True, "mail_guard": "false"}, id="ignored-string-false"),
    pytest.param({"hooks": True, "mail_guard": 0}, id="ignored-zero"),
    pytest.param({"hooks": True, "mail_guard": None}, id="ignored-null"),
    pytest.param({"hooks": True, "mail_guard": [False]}, id="ignored-list"),
    pytest.param({"hooks": False, "birth": False, "ride_along": False, "stop_nag": False},
                 id="the-other-three-off"),
]


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data", ON_FILES)
def test_mail_guard_on_denies_a_backtick_subject(make_rig, data, required):
    """ON (true, absent or a value that is not a boolean): the guard judges.
    It denies the damaged send with its plain deny - nothing about the file,
    an ignored value included (the ride-along's switch_ignored names that) -
    and lets the clean send through, silent."""
    rig = _rig(make_rig, data, required)
    _assert_plain_deny(guard(rig, GUARD_DENIES))
    _assert_silent_allow(guard(rig, GUARD_ALLOWS))


def test_mail_guard_on_with_the_file_missing_denies(make_rig):
    rig = make_rig("file-missing", False)
    assert not _settings.path().exists()
    _assert_plain_deny(guard(rig, GUARD_DENIES))


# ── a file that decides nothing: the guard judges, and says nothing ─────────

def _dangling(rig) -> None:
    p = _settings.path()
    p.unlink()
    p.symlink_to(rig.tmp / "nothing-here.json")


def _unreadable(rig) -> None:
    _settings.path().chmod(0o000)


def _a_directory(rig) -> None:
    p = _settings.path()
    p.unlink()
    p.mkdir()


WHY_FILES = [
    pytest.param(b'{"hooks": true, "mail_guard": false', None, id="not-json"),
    pytest.param(b'\xff{"hooks": true, "mail_guard": false}', None, id="not-utf8"),
    pytest.param(b'[{"hooks": true, "mail_guard": false}]', None, id="not-an-object"),
    pytest.param(b"false", None, id="not-an-object-false"),
    pytest.param(_file({"hooks": True, "mail_guard": False}), _unreadable, id="unreadable"),
    pytest.param(b"", _dangling, id="dangling-symlink"),
    pytest.param(b"", _a_directory, id="a-directory"),
]


@pytest.mark.parametrize("required", SEATS)
@pytest.mark.parametrize("data,after", WHY_FILES)
def test_a_file_that_decides_nothing_leaves_the_guard_on_and_silent(
        make_rig, data, after, required):
    """The decision this slice pins: `Switches.why` is set, so the guard is
    ON - even a `"mail_guard": false` the file cannot be read for never turns
    it off - and the guard says NOTHING about the file: its deny is the plain
    deny, its allow is silent. Each such file is a `hooks` fault, so the
    ride-along's fault line names it at the first batch."""
    if AS_ROOT and after is _unreadable:
        pytest.skip("root reads a mode-000 file")
    rig = _rig(make_rig, data, required)
    if after is not None:
        after(rig)
    sw = _settings.switches()
    assert sw.why and sw.mail_guard.on and not sw.mail_guard.set, sw
    settings = _settings.read()
    assert settings.fault and not settings.hooks, settings
    assert hook.guard_switched_off() is False
    _assert_plain_deny(guard(rig, GUARD_DENIES))
    _assert_silent_allow(guard(rig, GUARD_ALLOWS))


# ── main(), in process ─────────────────────────────────────────────────────

def _main(monkeypatch, capsys, payload: str) -> tuple[int, str, str, io.StringIO]:
    stdin = io.StringIO(payload)
    monkeypatch.setattr(hook.sys, "stdin", stdin)
    rc = hook.main([])
    out = capsys.readouterr()
    return rc, out.out, out.err, stdin


def test_main_off_takes_the_payload_and_says_nothing(make_rig, monkeypatch, capsys):
    """OFF: main reads the whole payload (so the harness's write never meets
    a closed pipe), judges nothing, and prints nothing - a payload too big
    for the guard to judge included."""
    _rig(make_rig, {"hooks": True, "mail_guard": False})
    for command in (GUARD_DENIES, GUARD_DENIES + " " + "x" * 300_000):
        payload = json.dumps({"session_id": SID, "tool_name": "Bash",
                              "tool_input": {"command": command}})
        rc, out, err, stdin = _main(monkeypatch, capsys, payload)
        assert (rc, out, err) == (0, "", "")
        assert stdin.tell() == len(payload) and stdin.read() == ""


def _count_reads(monkeypatch) -> list:
    calls: list = []
    real = hook.guard_switched_off

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(hook, "guard_switched_off", counted)
    return calls


def test_main_reads_the_switch_only_when_the_checks_would_run(make_rig, monkeypatch, capsys):
    """The guard reads no more than it needs: a main thread's MCP call, and a
    call the subagent rules deny, never read the settings file. A Bash call
    that reaches the subject and body checks reads it once."""
    _rig(make_rig, {"hooks": True, "mail_guard": False})
    calls = _count_reads(monkeypatch)
    main_mcp = json.dumps({"session_id": SID, "tool_name": SUBAGENT_MCP,
                           "tool_input": {"id": "1"}})
    assert _main(monkeypatch, capsys, main_mcp)[:3] == (0, "", "")
    for payload in (_subagent_bash(SUBAGENT_DENIED), _subagent_mcp()):
        rc, out, err, _ = _main(monkeypatch, capsys, json.dumps(payload))
        assert rc == 0 and err == ""
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert calls == []
    main_bash = json.dumps({"session_id": SID, "tool_name": "Bash",
                            "tool_input": {"command": GUARD_DENIES}})
    assert _main(monkeypatch, capsys, main_bash)[:3] == (0, "", "")
    assert calls == [1]


def test_main_off_still_answers_help(make_rig, monkeypatch, capsys):
    _rig(make_rig, {"hooks": True, "mail_guard": False})
    assert hook.main(["--help"]) == 0
    assert "THE OFF SWITCH" in capsys.readouterr().out


def test_main_on_denies_in_process(make_rig, monkeypatch, capsys):
    _rig(make_rig, {"hooks": True, "mail_guard": True})
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": GUARD_DENIES}})
    rc, out, err, _ = _main(monkeypatch, capsys, payload)
    assert rc == 0 and err == ""
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_guard_still_reads_no_hooks_key(make_rig):
    """`hooks` never governed the guard, and still does not: `"hooks": false`
    alone leaves it judging (the do-001 table pins every row)."""
    rig = _rig(make_rig, {"hooks": False})
    assert os.path.exists(hook.settings_path())
    _assert_plain_deny(guard(rig, GUARD_DENIES))
