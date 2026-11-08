"""`pager settings hooks KEY [on|off]`: the verb's per-hook forms.

KEY is one of the four switches (`_settings.SWITCH_KEYS`):

- `pager settings hooks KEY on|off` writes that ONE member and nothing
  else, through `_settings.set_switch` (`set_hooks(on)` is
  `set_switch("hooks", on)`), with one audit row whose `key` is KEY;
- `pager settings hooks KEY` reads that hook's state and why, writing
  nothing: `off - "KEY": false`, `on - not set, so on`, or off because
  "hooks" is not on (the switch_overridden line), for the hooks that
  "hooks" gates;
- the bare form is BYTE-IDENTICAL to the base for a file that sets no
  switch, with and without the SESSIONMAIL_PAGER note; a file that sets
  some gets one line for each after line 1 and before that note;
- any other word is a usage error (exit 2), and `on`/`off` may follow only
  a KEY;
- inside a subagent the mail guard allows `pager settings hooks KEY` (a
  read) and denies `pager settings hooks KEY on|off` (a write).

Hermetic: HOME and the mail root are the suite's tmp (conftest.py); a fake
SESSIONMAIL_SENDMAIL and a fake generator are asserted never run; the
session id and seat are made up; no date is written. The do-001 table's
sweep of the REAL home runs at the end of this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import test_mail_guard_subagent as bash_suite
from sessionmail import _seat, _settings, pager
from sessionmail.pager import cli
from test_hook_switches_table import real_home_untouched  # noqa: F401 - autouse, module scope
from test_mail_guard import guard
from test_mail_guard_subagent import decision

SESSION = "sess-switch-verb"
SEAT = "example+demo.worker"
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
KEYS = _settings.SWITCH_KEYS
#: The switches "hooks" gates, and what of each stays off.
GATED = {"ride_along": "the ride-along", "stop_nag": "the Stop nag", "birth": "the birth's box line"}
NOTE = ("pager settings hooks: note - SESSIONMAIL_PAGER=off is set here: a session started with it "
        "stays silent, whatever the file says")
FAKE = "#!/bin/sh\necho \"$*\" >> \"$FAKE_RUNS\"\n"


def home() -> Path:
    return Path(os.environ["HOME"])


def mail_json() -> Path:
    return home() / _settings.RELATIVE


def P() -> str:
    return str(mail_json())


def write(data: str) -> Path:
    p = mail_json()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode())
    return p


def hooks(*words: str):
    return CliRunner().invoke(cli, ["settings", "hooks", *words], prog_name="pager")


def audit_rows() -> list[dict]:
    p = home() / _settings.LOG_DIR / _settings.AUDIT_LOG
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture(autouse=True)
def person(monkeypatch, tmp_path):
    """A person's shell (no seat, no sudo, not root, the pager variable
    unset), with a fake MTA and a fake generator whose runs are asserted
    none at the end of every test."""
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "EMAIL", "SUDO_USER", _seat.PAGER_VAR):
        monkeypatch.delenv(var, raising=False)
    if AS_ROOT:
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    runs = tmp_path / "fake-runs"
    for name, var in (("sendmail", "SESSIONMAIL_SENDMAIL"), ("vmailbox-gen", "SESSIONMAIL_VMAILBOX_GEN")):
        fake = tmp_path / name
        fake.write_text(FAKE)
        fake.chmod(0o755)
        monkeypatch.setenv(var, str(fake))
    monkeypatch.setenv("FAKE_RUNS", str(runs))
    yield
    assert not runs.exists(), runs.read_text()


@pytest.fixture
def seated(monkeypatch):
    monkeypatch.setenv("TM_AGENT_ADDRESS", SEAT)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)


# ── the bare form, byte-identical for a file that sets no switch ──────────

def _not_json_why(body: str) -> str:
    try:
        json.loads(body)
    except ValueError as err:
        return f"not JSON: {err}"
    raise AssertionError(body)


#: (id, file body or a marker, stdout line 1 with {p}, exit): the base
#: commit's bare output for every file shape with no per-hook key, measured
#: on it, cell for cell.
BASE = [
    ("hooks-true", '{"version": 1, "hooks": true}', 'pager settings hooks: on - {p} says "hooks": true', 0),
    ("hooks-false", '{"version": 1, "hooks": false}',
     'pager settings hooks: off - {p} says "hooks": false', 3),
    ("no-key", '{"version": 1}', "pager settings hooks: off by mistake - {p}: no `hooks` key; "
     "`pager settings hooks on` or `off` sets it", 2),
    ("not-json", '{"hooks": false', "pager settings hooks: off by mistake - {p}: "
     + _not_json_why('{"hooks": false') + "; fix it by hand", 2),
    ("string", '{"hooks": "true"}', 'pager settings hooks: off by mistake - {p}: `hooks` is "true", '
     "not true or false; `pager settings hooks on` or `off` sets it", 2),
    ("list", "[1]", "pager settings hooks: off by mistake - {p}: the top level is a list, not an "
     "object; fix it by hand", 2),
    ("other-keys", '{"hooks": true, "monitor": false, "ride": false, "hook": false}',
     'pager settings hooks: on - {p} says "hooks": true', 0),
    ("no-file", None, "pager settings hooks: off - no {p} yet; the first PostToolBatch makes it, off", 3),
    ("no-file-old", "OLD", "pager settings hooks: off - no {p} yet; the first PostToolBatch makes it "
     "from ~/.session/mail/settings.json", 3),
    ("stranded", "STRANDED", "pager settings hooks: off by mistake - no {p} and the settings in "
     "~/.settings/mail/ have not been moved, so no hook makes it", 2),
]


def _shape(body) -> None:
    if body == "OLD":
        old = home() / _settings.OLD_RELATIVE
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text('{"hooks": true}')
    elif body == "STRANDED":
        (home() / _settings.LEGACY_DIR).mkdir(parents=True)
    elif body is not None:
        write(body)


@pytest.mark.parametrize("note", [False, True], ids=["plain", "note"])
@pytest.mark.parametrize("body,line,code", [b[1:] for b in BASE], ids=[b[0] for b in BASE])
def test_the_bare_form_is_the_base_byte_for_byte_with_no_switch_set(body, line, code, note, monkeypatch):
    _shape(body)
    if note:
        monkeypatch.setenv(_seat.PAGER_VAR, "off")
    before = sorted(str(p) for p in home().rglob("*"))
    r = hooks()
    want = line.format(p=P()) + "\n" + (NOTE + "\n" if note else "")
    assert (r.exit_code, r.stdout, r.stderr) == (code, want, "")
    assert sorted(str(p) for p in home().rglob("*")) == before    # a read writes nothing


@pytest.mark.parametrize("note", [False, True], ids=["plain", "note"])
def test_set_switches_list_after_line_1_and_before_the_note_in_key_order(note, monkeypatch):
    # the file's order is not the listing's: SWITCH_KEYS order is
    write('{"hooks": true, "birth": false, "mail_guard": true, "ride_along": false}')
    if note:
        monkeypatch.setenv(_seat.PAGER_VAR, "off")
    r = hooks()
    assert r.exit_code == 0     # line 1's code: the switches never move it
    assert r.stdout.splitlines() == [
        f'pager settings hooks: on - {P()} says "hooks": true',
        'pager settings hooks ride_along: off - "ride_along": false',
        'pager settings hooks mail_guard: on - "mail_guard": true',
        'pager settings hooks birth: off - "birth": false',
        *([NOTE] if note else []),
    ]


@pytest.mark.parametrize("hooks_value,code", [("false", 3), ('"yes"', 2)])
def test_the_switch_lines_never_move_the_exit_code(hooks_value, code):
    write(f'{{"hooks": {hooks_value}, "stop_nag": true, "mail_guard": false}}')
    r = hooks()
    assert r.exit_code == code
    lines = r.stdout.splitlines()
    assert len(lines) == 3
    assert lines[1] == ('pager settings hooks stop_nag: off - "stop_nag": true; "hooks" is '
                        + ("false" if hooks_value == "false" else "off by mistake")
                        + ", so the Stop nag stays off")
    assert lines[2] == 'pager settings hooks mail_guard: off - "mail_guard": false'


def test_the_listing_bounds_an_ignored_value_by_the_readers_own_reason():
    long = "x" * 500
    write(json.dumps({"hooks": True, "ride_along": long}))
    r = hooks()
    line = r.stdout.splitlines()[1]
    reason = _settings.switches().ride_along.ignored
    assert line == f"pager settings hooks ride_along: on - {reason}"
    assert long not in line and "..." in line
    assert len(line) < 200


# ── `pager settings hooks KEY`: the read ──────────────────────────────────

def test_the_three_shapes_of_the_read():
    write('{"hooks": true, "ride_along": false}')
    r = hooks("ride_along")
    assert (r.exit_code, r.stdout) == (3, 'pager settings hooks ride_along: off - "ride_along": false\n')
    r = hooks("stop_nag")
    assert (r.exit_code, r.stdout) == (0, "pager settings hooks stop_nag: on - not set, so on\n")
    write('{"hooks": false}')
    r = hooks("ride_along")
    assert (r.exit_code, r.stdout) == (
        3, 'pager settings hooks ride_along: off - "hooks" is false, so the ride-along stays off\n')


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("data,on,own", [
    ({"hooks": True}, True, "not set, so on"),
    ({"hooks": True, "K": True}, True, '"K": true'),
    ({"hooks": True, "K": False}, False, '"K": false'),
])
def test_every_key_reads_its_own_value_with_hooks_on(key, data, on, own):
    write(json.dumps({k.replace("K", key): v for k, v in data.items()}))
    r = hooks(key)
    word = "on" if on else "off"
    assert r.stdout == f"pager settings hooks {key}: {word} - {own.replace('K', key)}\n"
    assert r.exit_code == (0 if on else 3) and r.stderr == ""


@pytest.mark.parametrize("key", KEYS)
def test_hooks_false_holds_the_gated_hooks_off_but_not_the_guard(key):
    write(json.dumps({"hooks": False, key: True}))
    r = hooks(key)
    head = f'pager settings hooks {key}: '
    if key == "mail_guard":
        assert (r.exit_code, r.stdout) == (0, head + 'on - "mail_guard": true\n')
    elif key == "birth":   # the birth acts; only its box line is held
        assert (r.exit_code, r.stdout) == (
            0, head + 'on - "birth": true; "hooks" is false, so the birth\'s box line stays off\n')
    else:
        assert (r.exit_code, r.stdout) == (
            3, head + f'off - "{key}": true; "hooks" is false, so {GATED[key]} stays off\n')


def test_a_hooks_fault_reads_as_the_bare_form_does_off_by_mistake_exit_2():
    write('{"version": 1, "ride_along": true}')       # no `hooks` key
    r = hooks("ride_along")
    assert r.exit_code == 2
    assert r.stdout == ('pager settings hooks ride_along: off - "ride_along": true; '
                        '"hooks" is off by mistake, so the ride-along stays off\n')


def test_no_file_reads_and_makes_nothing():
    r = hooks("stop_nag")
    assert (r.exit_code, r.stdout) == (
        3, 'pager settings hooks stop_nag: off - "hooks" is off (no file yet), so the Stop nag stays off\n')
    r = hooks("mail_guard")
    assert (r.exit_code, r.stdout) == (0, "pager settings hooks mail_guard: on - not set, so on\n")
    assert not mail_json().exists() and not (home() / _settings.ROOT).exists()


def test_a_file_that_is_not_json_leaves_the_guard_on_and_says_why():
    write('{"mail_guard": false')
    r = hooks("mail_guard")
    assert r.exit_code == 0
    assert r.stdout == f"pager settings hooks mail_guard: on - {_settings.switches().why}\n"
    assert "every switch is on" in r.stdout


def test_an_ignored_value_reads_on_bounded():
    write(json.dumps({"hooks": True, "stop_nag": "no " * 100}))
    r = hooks("stop_nag")
    assert r.exit_code == 0
    assert r.stdout == f"pager settings hooks stop_nag: on - {_settings.switches().stop_nag.ignored}\n"
    assert "..." in r.stdout and "no " * 20 not in r.stdout


def test_the_read_writes_nothing_and_logs_nothing(seated):
    p = write('{"hooks": true, "birth": false}')
    st = os.lstat(p)
    for key in KEYS:
        hooks(key)
    assert (os.lstat(p).st_ino, os.lstat(p).st_mtime_ns) == (st.st_ino, st.st_mtime_ns)
    assert audit_rows() == []


def test_the_read_carries_the_note(monkeypatch):
    write('{"hooks": true}')
    monkeypatch.setenv(_seat.PAGER_VAR, "off")
    r = hooks("birth")
    assert r.stdout == f"pager settings hooks birth: on - not set, so on\n{NOTE}\n"


# ── `pager settings hooks KEY on|off`: the write ──────────────────────────

BODY = '{\n  "version": 1,\n  "hooks": true,\n  "sendmail": "drop",\n  "monitor": false\n}\n'


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("word", ["on", "off"])
def test_each_write_form_writes_one_member_and_nothing_else(key, word, seated):
    p = write(BODY)
    r = hooks(key, word)
    assert r.exit_code == 0 and r.stderr == ""
    value = word == "on"
    after = p.read_bytes()
    assert after == _settings.with_value(BODY.encode(), key, value)   # one member, added last
    assert json.loads(after) == {**json.loads(BODY), key: value}
    assert after.startswith(BODY.rstrip("\n}").encode())              # every byte before it kept
    assert r.stdout == (f'pager settings hooks {key}: {word} - wrote "{key}": {json.dumps(value)} '
                        f'in {P()} (it had no "{key}"); each session\'s next hook reads it\n')
    rows = audit_rows()
    assert len(rows) == 1
    assert (rows[0]["key"], rows[0]["from"], rows[0]["to"]) == (key, "on", word)
    assert rows[0]["sid"] == SESSION and rows[0]["seat"] == SEAT
    # read through the reader the hooks use
    assert _settings.switches()[key].on is value
    assert _settings.read().state == _settings.ON           # "hooks" untouched


@pytest.mark.parametrize("key", KEYS)
def test_a_value_in_place_changes_only_its_span(key):
    body = f'{{"hooks": true,  "{key}"  :  false , "z": 1}}'
    p = write(body)
    r = hooks(key, "on")
    assert r.exit_code == 0
    assert p.read_text() == body.replace("false", "true")
    assert f'(it said false)' in r.stdout


@pytest.mark.parametrize("key", KEYS)
def test_already_writes_nothing_and_logs_nothing(key, seated):
    p = write(json.dumps({"hooks": True, key: False}))
    st = os.lstat(p)
    r = hooks(key, "off")
    assert r.exit_code == 0
    assert r.stdout == f'pager settings hooks {key}: off - {P()} already says "{key}": false; nothing written\n'
    assert (os.lstat(p).st_ino, os.lstat(p).st_mtime_ns) == (st.st_ino, st.st_mtime_ns)
    assert audit_rows() == []


@pytest.mark.parametrize("key", KEYS)
def test_on_while_hooks_is_false_says_what_stays_off(key):
    write('{"hooks": false}')
    r = hooks(key, "on")
    assert r.exit_code == 0
    base = (f'pager settings hooks {key}: on - wrote "{key}": true in {P()} (it had no "{key}"); '
            "each session's next hook reads it")
    if key in GATED:
        assert r.stdout == f'{base}; "hooks" is false, so {GATED[key]} stays off\n'
    else:
        assert r.stdout == base + "\n"
    r = hooks(key, "on")    # already: the tail still says it
    assert r.stdout.endswith(f', so {GATED[key]} stays off\n' if key in GATED else "nothing written\n")


def test_off_never_carries_the_tail():
    write('{"hooks": false}')
    r = hooks("ride_along", "off")
    assert "stays off" not in r.stdout


def test_the_tail_is_the_hook_lines_entry():
    assert pager.SWITCH_OVERRIDDEN == "{hooks}, so {what} stays off"
    assert pager._SWITCH_GATED == GATED
    assert set(pager._SWITCH_GATED) | {"mail_guard"} == set(KEYS)


def test_a_switch_write_on_no_file_makes_it_from_the_template(seated):
    r = hooks("stop_nag", "off")
    assert r.exit_code == 0
    assert r.stdout == (f'pager settings hooks stop_nag: off - made {P()} from the template, with '
                        "\"stop_nag\": false; each session's next hook reads it\n")
    data = json.loads(mail_json().read_text())
    assert data["stop_nag"] is False and data["hooks"] is False      # the template's off, untouched
    rows = audit_rows()
    assert [(r["key"], r["from"], r["to"]) for r in rows] == [("stop_nag", "missing", "off")]


def test_a_switch_write_never_adds_hooks_to_a_file_without_it():
    p = write('{"version": 1}')
    r = hooks("birth", "off")
    assert r.exit_code == 0
    assert p.read_text() == '{"version": 1, "birth": false}'


@pytest.mark.parametrize("value,shown", [('"no"', '"no"'), ("0", "0"), ("null", "null")])
def test_over_an_ignored_value_the_parenthesis_says_it_read_as_on(value, shown, seated):
    write(f'{{"hooks": true, "ride_along": {value}}}')
    r = hooks("ride_along", "off")
    assert f"(it said {shown}, which reads as on)" in r.stdout
    assert [(r["from"], r["to"]) for r in audit_rows()] == [("on", "off")]


def test_over_a_long_ignored_value_the_parenthesis_is_bounded():
    long = "y" * 300
    write(json.dumps({"hooks": True, "mail_guard": long}))
    r = hooks("mail_guard", "on")
    assert r.exit_code == 0 and long not in r.stdout
    assert f"(it said {_settings._shown_value(long)}, which reads as on)" in r.stdout


def test_not_json_is_refused_and_never_written():
    p = write('{"hooks": true')
    r = hooks("mail_guard", "off")
    assert r.exit_code == 2 and r.stdout == ""
    assert r.stderr.startswith(f"pager settings hooks: refused: {P()}: not JSON: ")
    assert p.read_text() == '{"hooks": true'
    assert audit_rows() == []


@pytest.mark.parametrize("how", ["euid", "sudo"])
def test_root_and_sudo_are_refused_for_a_switch_too(how, monkeypatch):
    p = write('{"hooks": true}')
    if how == "euid":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    else:
        monkeypatch.setenv("SUDO_USER", "someone")
    r = hooks("mail_guard", "off")
    assert r.exit_code == 2 and "refused" in r.stderr
    assert p.read_text() == '{"hooks": true}'
    result = _settings.set_switch("mail_guard", False)
    assert (result.refused, result.key) == (_settings.REFUSED_ROOT, "mail_guard")


def test_a_failed_read_back_names_the_key(monkeypatch):
    write('{"hooks": true}')
    monkeypatch.setattr(_settings, "_key_state", lambda key, env: (_settings.ON, ""))
    r = hooks("birth", "off")
    assert r.exit_code == 2
    assert r.stderr.startswith(f'pager settings hooks: refused: wrote "birth": false in {P()}, but it reads as on')


def test_set_switch_takes_hooks_and_the_four_and_nothing_else():
    write('{"hooks": false}')
    assert _settings.set_switch("hooks", True).state == _settings.ON
    with pytest.raises(ValueError):
        _settings.set_switch("monitor", False)
    with pytest.raises(ValueError):
        _settings.set_switch("hook", True)


def test_set_hooks_is_set_switch_of_hooks():
    write('{"hooks": false}')
    result = _settings.set_hooks(True)
    assert result.key == "hooks" and result.wrote and result.state == _settings.ON


# ── usage: any other word is exit 2 ───────────────────────────────────────

@pytest.mark.parametrize("words", [
    ("on", "off"), ("off", "on"), ("hooks",), ("ride-along",), ("monitor",), ("ride_along", "maybe"),
    ("ride_along", "stop_nag"), ("ride_along", "on", "off"), ("RIDE_ALONG",), ("on", "ride_along"),
], ids=lambda w: "-".join(w))
def test_any_other_word_is_a_usage_error_and_nothing_is_made(words):
    r = hooks(*words)
    assert r.exit_code == 2
    assert not mail_json().exists() and audit_rows() == []


def test_the_usage_line_names_both_shapes():
    r = hooks("--help")
    assert r.output.startswith("Usage: pager settings hooks [on|off] | KEY [on|off]")


# ── the mail guard: a lone KEY is a read, KEY on|off a write ──────────────

WHAT = "pager settings hooks"

GUARD_ALLOW = [(f"read-{k}", f"pager settings hooks {k}") for k in KEYS] + [
    ("read-dd-before", "pager settings hooks -- ride_along"),
    ("read-dd-after", "pager settings hooks stop_nag --"),
    ("read-group-dd", "pager -- settings hooks birth"),
    ("read-help", "pager settings hooks mail_guard --help"),
]
GUARD_DENY = [(f"write-{k}-{w}", f"pager settings hooks {k} {w}") for k in KEYS for w in ("on", "off")] + [
    ("write-dd", "pager settings hooks mail_guard -- off"),
    ("write-bash-c", "bash -c 'pager settings hooks ride_along off'"),
    ("a-key-then-a-typo", "pager settings hooks birth maybe"),
    ("two-keys", "pager settings hooks birth stop_nag"),
    ("not-a-key", "pager settings hooks ride-along"),
]


@pytest.mark.parametrize("cell,command", GUARD_ALLOW, ids=[c for c, _ in GUARD_ALLOW])
def test_a_subagent_may_read_one_switch(cell, command):
    proc = bash_suite.run(command, subagent=True)
    assert decision(proc) == "allow", f"{cell}: {command!r} was denied: {proc.stdout}"


@pytest.mark.parametrize("cell,command", GUARD_DENY, ids=[c for c, _ in GUARD_DENY])
def test_a_subagent_may_not_write_one(cell, command):
    proc = bash_suite.run(command, subagent=True)
    assert decision(proc) == "deny", f"{cell}: {command!r} was allowed"
    assert guard.decide_subagent(command) == guard.subagent_reason(WHAT), cell


@pytest.mark.parametrize("cell,command", GUARD_DENY, ids=[c for c, _ in GUARD_DENY])
def test_a_main_thread_is_never_denied_a_switch_write(cell, command):
    proc = bash_suite.run(command, subagent=False)
    assert decision(proc) == "allow", f"{cell}: main thread {command!r} was denied"


def test_the_guards_keys_are_the_readers():
    assert guard.SETTINGS_SWITCH_KEYS == _settings.SWITCH_KEYS


def test_the_deny_line_points_at_the_one_switch_read():
    reason = guard.subagent_reason(WHAT)
    assert "`pager settings hooks` (bare)" in reason
    assert "`pager settings hooks KEY`" in reason
    assert "\n" not in reason
