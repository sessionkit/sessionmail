"""SM-727 fix-001: the operator and the privileged seats come from config.

Design `designs/operator-config.md` (the SM-727 task dir) sections (a)-(c)
and (g.1). One block in `~/.sessionkit/tools/settings/session.json`, read through
`sessiontools.settings`, template and host layers, under the password
database's home (the conftest seam `_operator.settings_home`):

    {"operator": {"mailbox": "judy", "privileged": ["ivan", "judy"]}}

- SC2: the privileged set comes from that key. With no config, nothing is
  privileged, and the refusal names `operator.privileged` and the file.
- Fail safe: a bad file is nothing configured. An environment variable
  (`AGENT_OPERATOR_ADDR`, a planted `$HOME`) never grants privilege.
- SC4: the gate verbs' `--help` and refusals say "a privileged seat" and
  never name Ivan or Judy from code. On a host whose config names them, the
  refusal quotes the config; that is the config talking, not the code.
"""

from __future__ import annotations

import json
import os
import pwd
import re
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import ACCOUNT_DEFAULT, SC6_OPERATOR, write_operator
from test_message_subscription import IVAN_SID, JUDY_SID, SID, as_seat, fleet, sub  # noqa: F401 - `fleet` is a fixture

from sessionmail import _operator
from sessionmail import message as message_mod
from sessionmail import pager as pager_mod

#: The REAL home seam, taken at import, before the autouse conftest patches
#: `_operator.settings_home` into the sandbox (B2 of the fix-001 backup
#: review: a test that only sets `$HOME` cannot see the seam it replaced).
REAL_SETTINGS_HOME = _operator.settings_home

#: A person's name, as a word. SC4: never in a gate's help or (on a host
#: whose config does not name them) its refusal: the names this suite's
#: config gives, and the names a default in code once held (from pieces).
NAMES = re.compile(r"(?i)\b(ivan|judy|%s|%s)\b" % ("co" "ra", "mi" "ke"))

#: The host files the acceptance test runs over (design g.1's table), and
#: whether seat ivan, judy, sessionmail+super, alice may write a seat
#: subscription under each.
HOSTS = {
    "sc6": (SC6_OPERATOR, {"ivan": True, "judy": True, "sessionmail+super": False,
                           "alice": False}),
    # SM-806 (d): the seat named like the installing account (the conftest's
    # fake `acct-default`) inherits nothing from the default operator.
    "stranger": (None, {"ivan": False, "judy": False, "sessionmail+super": False,
                        "alice": False, "acct-default": False}),
    "bad-json": ("{not json", {"ivan": False, "judy": False, "sessionmail+super": False,
                               "alice": False}),
    "no-operator": ('{"operator": {"privileged": ["ivan", "judy"]}}',
                    {"ivan": False, "judy": False, "sessionmail+super": False,
                     "alice": False}),
    "alice": ('{"operator": {"mailbox": "alice"}}',
              {"ivan": False, "judy": False, "sessionmail+super": False, "alice": True}),
}

#: The gate verbs whose `--help` SC4 covers: (cli, argv).
HELP_PAGES = [
    (message_mod.cli, ["subscription", "--help"]),
    (message_mod.cli, ["subscription", "add", "--help"]),
    (message_mod.cli, ["subscription", "remove", "--help"]),
    (pager_mod.cli, ["rule", "--help"]),
    (pager_mod.cli, ["rule", "add", "--help"]),
    (pager_mod.cli, ["rule", "remove", "--help"]),
    (pager_mod.cli, ["focus", "--help"]),
    (pager_mod.cli, ["settings", "migrate", "--help"]),
]


def _sessions_for(seat: str) -> str | None:
    return {"ivan": IVAN_SID, "judy": JUDY_SID}.get(seat, SID)


def _refusal_words(result) -> str:
    """The refusal, less `(you are SEAT)` and the repair line's `--for WHO`
    (SM-690: the caller's own seat or session, filled in; the gate's argv
    here passes no `--for`): the caller's own name is not one the code
    chose."""
    said = result.stderr if result.stderr_bytes is not None else result.output
    said = re.sub(r"\(you are [^)]*\)", "(you are ...)", said)
    return re.sub(r"--for \S+", "--for ...", said)


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_privileged_comes_from_config_and_fails_safe(fleet, monkeypatch, stranger, host):
    """THE fix-001 acceptance test (design g.1)."""
    text, may = HOSTS[host]
    stranger(text)

    # 1. The gate: `message subscription add BOX --seat`, through the CLI.
    for seat, allowed in may.items():
        as_seat(monkeypatch, seat, _sessions_for(seat))
        done = sub("add", "atomnote", "--seat")
        if allowed:
            assert done.exit_code == 0, (host, seat, done.output)
            continue
        assert done.exit_code == 2, (host, seat, done.output)
        said = _refusal_words(done)
        # SC2: every refusal names the key to set, and the file it lives in.
        assert _operator.PRIVILEGED_KEY in said, (host, seat, said)
        assert _operator.KEY_FILE in said, (host, seat, said)
        if host != "sc6":
            # SC4: no person's name from code. (On the SC6 host the config
            # names ivan and judy, and the refusal quotes it.)
            assert not NAMES.search(said), (host, seat, said)

    # 2. The seam's shape is the frozen contract, key for key.
    got = _operator.read().as_json()
    assert set(got) == {"operator", "mailbox", "privileged"}
    want = {
        "sc6": {"operator": None, "mailbox": "judy", "privileged": ["ivan", "judy"]},
        "alice": {"operator": None, "mailbox": "alice", "privileged": ["alice"]},
    }.get(host, {"operator": None, "mailbox": None, "privileged": []})
    assert got == want, (host, got)


def test_an_environment_variable_never_grants_privilege(fleet, monkeypatch, stranger, tmp_path):
    """No env path into privilege: not ST-116's ASK override, and not a
    `$HOME` pointed at a planted file that privileges the caller."""
    stranger(None)
    planted = {"operator": {"mailbox": "sessionmail+super",
                            "privileged": ["sessionmail+super", "worker"]}}
    fake_home = tmp_path / "planted"
    write_operator(fake_home, json.dumps(planted))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("AGENT_OPERATOR_ADDR", "sessionmail+super")
    for seat in ("sessionmail+super", "worker"):
        as_seat(monkeypatch, seat, SID)
        done = sub("add", "atomnote", "--seat")
        assert done.exit_code == 2, (seat, done.output)
    # SM-806: STRICTER than the `== NOTHING` it replaces. Every NOTHING
    # field still holds (no operator, no mailbox, nothing privileged, no
    # problem), and the one new field is the PASSWORD DATABASE's name (the
    # conftest seam), never `$HOME`'s planted file or `AGENT_OPERATOR_ADDR`.
    assert _operator.read() == replace(_operator.NOTHING, default=ACCOUNT_DEFAULT)


def test_the_real_home_is_the_password_databases_not_home(monkeypatch, tmp_path):
    """No env path into privilege, at the seam itself: the real
    `settings_home` ignores `$HOME` (and `$SESSIONMAIL_HOME`). Nothing is
    read: only the path is compared."""
    monkeypatch.setenv("HOME", str(tmp_path / "planted"))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "planted-sm"))
    assert REAL_SETTINGS_HOME() == Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert REAL_SETTINGS_HOME() != tmp_path / "planted"


def test_the_operator_is_always_privileged_and_the_match_is_exact(stranger):
    """The operator's mailbox and address are privileged, listed or not; a
    seat name matches exactly and case-blind; an `@` entry matches only that
    full address; a `*` is the literal string, not a glob."""
    stranger('{"operator": {"mailbox": "Alice", "address": "alice@host.example", '
             '"privileged": ["Bob", "*", "", 7, "carol@elsewhere.example"]}}')
    op = _operator.read()
    assert op.privileged == ("*", "alice", "alice@host.example", "bob",
                             "carol@elsewhere.example")
    assert op.is_privileged("alice") and op.is_privileged("ALICE ")
    assert op.is_privileged("bob")
    assert not op.is_privileged("carol")
    assert not op.is_privileged("bob+x")
    assert not op.is_privileged("anyone")
    assert not op.is_privileged(None) and not op.is_privileged("")


#: Malformed blocks (design a.3): each only removes privilege. SM-806's
#: test 3 reuses them as rows that must never be the default.
BAD_BLOCKS = [
    "[]",
    '{"operator": "judy"}',
    '{"operator": {"mailbox": "judy", "privileged": "ivan"}}',
    '{"operator": {"mailbox": "  "}}',
    '{"operator": {"privileged": ["ivan"]}}',
]


@pytest.mark.parametrize("text", BAD_BLOCKS)
def test_a_bad_block_only_removes_privilege(stranger, text):
    """Fail safe (design a.3): a malformed block never privileges ivan."""
    stranger(text)
    op = _operator.read()
    assert not op.is_privileged("ivan")
    assert set(op.as_json()) == {"operator", "mailbox", "privileged"}


def test_no_operator_names_why_in_the_refusal(stranger):
    """A `privileged` list with no operator is nothing privileged, and the
    words say which key is missing."""
    stranger('{"operator": {"privileged": ["ivan"]}}')
    words = _operator.who_may(_operator.read())
    assert _operator.OPERATOR_KEY in words and _operator.PRIVILEGED_KEY in words
    assert "no operator" in words


def test_the_route_goes_to_a_privileged_seat_from_config(stranger):
    """The request-by-mail door names the first privileged SEAT, sorted,
    from config: `ivan` on hq (as it did before SM-727), `alice` on hers,
    and with none, the key to set."""
    stranger(SC6_OPERATOR)
    assert "message send ivan " in _operator.route(_operator.read(), "subscription")
    stranger('{"operator": {"address": "a@x.example", "mailbox": "alice"}}')
    assert "message send alice " in _operator.route(_operator.read(), "subscription")
    stranger(None)
    assert _operator.PRIVILEGED_KEY in _operator.route(_operator.read(), "subscription")


@pytest.mark.parametrize("cli,argv", HELP_PAGES)
def test_the_gate_help_names_no_person(cli, argv):
    """SC4: every gate verb's `--help` says "a privileged seat"."""
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    page = result.output   # fix-002 renamed `set judy` to `set operator`: no exception left
    assert not NAMES.search(page), (argv, NAMES.findall(page))


@pytest.mark.parametrize("host", ["stranger", "alice"])
@pytest.mark.parametrize("argv", [
    ["rule", "add", "--seat", "ride from:*"],
    ["rule", "add", "--locked", "ride from:*"],
    ["focus", "sleep", "--fleet"],
    ["settings", "migrate"],
])
def test_the_pager_gates_refuse_by_config_and_name_no_person(fleet, monkeypatch, stranger,
                                                             host, argv):
    """SC2 + SC4 at the pager's gates: refused, exit 2, the key named, and no
    person's name from code."""
    stranger(HOSTS[host][0])
    as_seat(monkeypatch, "worker", SID)
    result = CliRunner().invoke(pager_mod.cli, argv)
    assert result.exit_code == 2, (argv, result.output)
    said = _refusal_words(result)
    assert _operator.PRIVILEGED_KEY in said, (argv, said)
    assert not NAMES.search(said), (argv, said)


# ── SM-806: no operator block -> the operator DEFAULTS to the installing account ──
#
# Design `findings/design.md` (the SM-806 task dir), tests 1-13. The default
# NAMES the human and TRUSTS no one (R0 built name-only; R1: never
# `$operator`). The conftest patches the seam `_operator.account_name` to
# ACCOUNT_DEFAULT, so no test reads the real account.

#: The REAL account seam, taken at import, before the autouse conftest
#: patches it (as REAL_SETTINGS_HOME is).
REAL_ACCOUNT_NAME = _operator.account_name

#: What every NOTHING-shaped Operator prints: ST-130's frozen three, empty.
EMPTY_JSON = {"operator": None, "mailbox": None, "privileged": []}

#: The merge edge (design e): each merges to `{}` (the leaf merge drops an
#: empty dict), so only the RAW layers can see the `operator` key.
MERGE_EDGE = ['{"operator": {}}', '{"operator": {"mailbox": {}}}',
              '{"operator": {"privileged": {}}}']

#: Every present-but-bad spelling (design e's table, test 3): NOTHING, and
#: never the default. `test_a_bad_block_only_removes_privilege`'s rows, plus
#: the explicit `null`, a non-dict, empty text and bad JSON.
MALFORMED = [
    *BAD_BLOCKS,
    '{"operator": {"mailbox": null}}',
    '{"operator": null}',
    '{"operator": ""}',
    '{"operator": []}',
    "",
    "{not json",
]


def _tool_dir() -> Path:
    return _operator.settings_home() / ".sessionkit" / "tools" / "settings"


def _drop_in(text: str, name: str = "50-test.json") -> Path:
    """TEXT as a drop-in, `session.d/NAME`, in the sandboxed host layer."""
    path = _tool_dir() / "session.d" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _template(text: str) -> Path:
    path = _tool_dir() / "session.template.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_an_absent_block_is_the_default_named_and_trusting_no_one(stranger):
    """Test 1: no block -> `default` is the account, every NOTHING field
    stays, nothing is configured, and ST-130's frozen shape is
    byte-identical to a stranger's before SM-806."""
    stranger(None)
    op = _operator.read()
    assert op.default == ACCOUNT_DEFAULT
    assert op.operator is None and op.mailbox is None and op.privileged == ()
    assert op.problem is None
    assert not op.configured
    assert op.as_json() == EMPTY_JSON
    assert not op.is_privileged(ACCOUNT_DEFAULT)


def test_files_with_no_operator_key_are_the_default(stranger):
    """Test 2a: a host file and a drop-in, neither with an `operator` key,
    and a template with none: still absent, so the default."""
    stranger('{"other": {"x": 1}}')
    _drop_in('{"another": true}')
    _template('{"something": "else"}')
    assert _operator.read() == replace(_operator.NOTHING, default=ACCOUNT_DEFAULT)


@pytest.mark.parametrize("where", ["host", "drop-in", "template"])
@pytest.mark.parametrize("text", MERGE_EDGE)
def test_the_merge_edge_is_judged_on_the_raw_layers(stranger, where, text):
    """Test 2b (design e, the review fix): `{"operator": {}}` and its
    empty-dict cousins MERGE to `{}`, exactly as no block does. The key is
    present in a raw layer, so it is NOT absent: NOTHING, default None."""
    from sessiontools import settings as st
    stranger(None)
    {"host": lambda: stranger(text), "drop-in": lambda: _drop_in(text),
     "template": lambda: _template(text)}[where]()
    # The premise, measured: the merged values cannot tell this from no block.
    assert _operator.BLOCK not in st.merge(_operator.host_sources(_operator.TOOL))[0]
    op = _operator.read()
    assert op.default is None
    assert op == _operator.NOTHING


@pytest.mark.parametrize("text", MALFORMED)
def test_a_malformed_block_is_nothing_and_never_the_default(stranger, text):
    """Test 3: every present-but-bad spelling only REMOVES. `default` is
    None on each: "malformed" never falls through to the default."""
    stranger(text)
    op = _operator.read()
    assert op.default is None, (text, op)
    assert not op.is_privileged(ACCOUNT_DEFAULT)
    # Each row is NOTHING, bar the one whose mailbox stands and whose bad
    # list only drops away (`_from_block`): nobody but that operator.
    assert set(op.privileged) <= {op.mailbox} - {None}, (text, op)


@pytest.mark.parametrize("text", ["{not json", "[]", '{"operator": null}', "",
                                  '{"_locked": "operator"}'])
def test_a_bad_drop_in_over_no_block_is_never_the_default(stranger, text):
    """Test 3, as a drop-in: a bad drop-in (bad JSON, not an object, a bad
    `_locked`, or `null`) beside a host file with no block is NOTHING."""
    stranger('{"other": 1}')
    _drop_in(text)
    assert _operator.read().default is None, text


@pytest.mark.skipif(os.getuid() == 0, reason="root reads a chmod 000 file")
def test_an_unreadable_file_is_nothing_and_never_the_default(stranger):
    """Test 3: a present file that cannot be read is NOTHING with the
    reason, never the default (FAIL SAFE: a bad file only removes)."""
    path = stranger('{"operator": {"mailbox": "alice"}}')
    path.chmod(0)
    try:
        op = _operator.read()
    finally:
        path.chmod(0o600)
    assert op.default is None and op.privileged == ()
    assert op.problem and "could not be read" in op.problem


@pytest.mark.parametrize("seam", ["host_sources", "settings_home"])
def test_a_reader_that_raises_is_nothing_and_never_the_default(stranger, monkeypatch, seam):
    """Test 3: the reader itself failing (no sessiontools, a broken home) is
    NOTHING with the reason, never the default."""
    stranger(None)

    def boom(*_a, **_k):
        raise RuntimeError("cannot read")
    monkeypatch.setattr(_operator, seam, boom)
    op = _operator.read()
    assert op.default is None and op.problem and "cannot read" in op.problem


@pytest.mark.parametrize("where", ["host", "drop-in"])
def test_a_configured_block_overrides_the_default_entirely(stranger, where):
    """Test 4: config wins, whole: no default beside it."""
    block = '{"operator": {"mailbox": "alice"}}'
    if where == "host":
        stranger(block)
    else:
        stranger('{"other": 1}')
        _drop_in(block)
    op = _operator.read()
    assert op.default is None
    assert op.mailbox == "alice" and op.privileged == ("alice",)
    assert op == _operator.Operator(None, "alice", ("alice",))


def test_the_default_is_never_read_from_the_environment(stranger, monkeypatch, tmp_path):
    """Test 5: `$USER`, `$LOGNAME`, `$HOME` and `AGENT_OPERATOR_ADDR` move
    nothing. The name is the seam's (the password database's)."""
    stranger(None)
    planted = tmp_path / "planted"
    write_operator(planted, '{"operator": {"mailbox": "sessionmail+super"}}')
    for var in ("USER", "LOGNAME", "AGENT_OPERATOR_ADDR"):
        monkeypatch.setenv(var, "sessionmail+super")
    monkeypatch.setenv("HOME", str(planted))
    op = _operator.read()
    assert op.default == ACCOUNT_DEFAULT and op.mailbox is None and op.privileged == ()


def test_the_real_account_name_is_the_password_databases(monkeypatch):
    """Test 5, at the seam itself (un-patched): `pw_name` of this uid,
    case-folded, whatever `$USER` and `$LOGNAME` say."""
    monkeypatch.setenv("USER", "sessionmail+super")
    monkeypatch.setenv("LOGNAME", "sessionmail+super")
    if os.getuid() == 0:
        assert REAL_ACCOUNT_NAME() is None
        return
    want = pwd.getpwuid(os.getuid()).pw_name.casefold()
    got = REAL_ACCOUNT_NAME()
    assert got == want or (got is None and _operator._safe_name(want) is None)


@pytest.mark.parametrize("argv", [
    ["rule", "add", "--seat", "ride from:*"],
    ["rule", "add", "--locked", "ride from:*"],
    ["focus", "sleep", "--fleet"],
])
def test_a_seat_named_like_the_account_is_refused_at_the_pager_gates(
        fleet, monkeypatch, stranger, argv):
    """Test 6 (design d): agents are the same uid and can spell the
    account's name. A seat named `acct-default` on a no-block host is
    refused at every privileged gate: the default is never in `privileged`.
    (`message subscription add --seat` is the HOSTS stranger row.)"""
    stranger(None)
    as_seat(monkeypatch, ACCOUNT_DEFAULT, SID)
    result = CliRunner().invoke(pager_mod.cli, argv)
    assert result.exit_code == 2, (argv, result.output)
    assert _operator.PRIVILEGED_KEY in _refusal_words(result)


def test_a_seat_named_like_the_account_may_not_subscribe(fleet, monkeypatch, stranger):
    """Test 6, the subscription gate, said once by name: exit 2, and the
    refusal words name the default as a default."""
    stranger(None)
    as_seat(monkeypatch, ACCOUNT_DEFAULT, SID)
    done = sub("add", "atomnote", "--seat")
    assert done.exit_code == 2, done.output
    said = _refusal_words(done)
    assert "defaults to this account" in said and "privileges no seat" in said


class _Entry:
    def __init__(self, name):
        self.pw_name = name


@pytest.mark.parametrize("name", ["a b", "x+y", "a@b", "", "-x-"])
def test_an_unsafe_name_from_the_seam_is_no_default(stranger, monkeypatch, name):
    """Test 7: a name that is not a safe local part is no default, from a
    patched seam (re-checked in `read`) and from the real one."""
    stranger(None)
    monkeypatch.setattr(_operator, "account_name", lambda: name)
    assert _operator.read() == _operator.NOTHING
    monkeypatch.setattr(_operator.pwd, "getpwuid", lambda uid: _Entry(name))
    monkeypatch.setattr(_operator.os, "getuid", lambda: 501)
    assert REAL_ACCOUNT_NAME() is None


def test_no_pwd_entry_or_root_is_no_default(stranger, monkeypatch):
    """Test 7: no password-database entry (KeyError) and uid 0 (`root`
    installed nothing as a person) give no default. Nothing is guessed."""
    stranger(None)

    def no_entry(uid):
        raise KeyError(uid)
    monkeypatch.setattr(_operator, "account_name", lambda: no_entry(501))
    assert _operator.read() == _operator.NOTHING
    monkeypatch.setattr(_operator, "account_name", REAL_ACCOUNT_NAME)
    monkeypatch.setattr(_operator.os, "getuid", lambda: 501)
    monkeypatch.setattr(_operator.pwd, "getpwuid", no_entry)
    assert REAL_ACCOUNT_NAME() is None
    assert _operator.read() == _operator.NOTHING
    monkeypatch.setattr(_operator.os, "getuid", lambda: 0)
    monkeypatch.setattr(_operator.pwd, "getpwuid", lambda uid: _Entry("root"))
    assert REAL_ACCOUNT_NAME() is None
    assert _operator.read() == _operator.NOTHING


def test_the_account_name_is_case_folded(monkeypatch):
    """Test 7's other side: a safe name is taken, case-folded."""
    monkeypatch.setattr(_operator.os, "getuid", lambda: 501)
    monkeypatch.setattr(_operator.pwd, "getpwuid", lambda uid: _Entry("Acct-Default"))
    assert REAL_ACCOUNT_NAME() == "acct-default"


def test_the_refusal_words_name_the_default_as_a_default(stranger):
    """Test 8 (R2: these words ARE the display): `who_may` on a default
    names the account, says it is a default, and names both keys and the
    file. `route` is unchanged: no seat may grant it."""
    stranger(None)
    op = _operator.read()
    words = _operator.who_may(op)
    assert ACCOUNT_DEFAULT in words and "default" in words
    assert _operator.OPERATOR_KEY in words and _operator.PRIVILEGED_KEY in words
    assert _operator.KEY_FILE in words
    assert "privileges no seat" in words
    assert _operator.route(op, "subscription") == _operator.route(_operator.NOTHING,
                                                                   "subscription")
    # A host whose block names no operator keeps its own words, no default.
    stranger('{"operator": {"privileged": ["ops"]}}')
    assert "defaults to this account" not in _operator.who_may(_operator.read())


def test_r0_the_privilege_seam_is_unwired(stranger):
    """Test 11, R0 (OPEN, carried up; built name-only): the ONE place a default
    would join `privileged` gives nothing, and an absent block IS it. If (B)
    is ruled, THIS test changes, on purpose, in its own slice."""
    seam = _operator._default_operator(ACCOUNT_DEFAULT)
    assert seam.privileged == () and seam.mailbox is None and seam.operator is None
    assert seam.default == ACCOUNT_DEFAULT and not seam.configured
    stranger(None)
    assert _operator.read() == seam


@pytest.mark.parametrize("layers", [
    {"host": '{"operator": null}'},
    {"host": '{"other": 1}', "drop-in": '{"operator": null}'},
    {"host": '{"operator": {"mailbox": "alice"}}', "drop-in": '{"operator": null}'},
])
def test_q3_operator_null_is_the_documented_opt_out(stranger, layers):
    """Test 13, Q3: `"operator": null` opts out of the default, alone, as a
    drop-in over a host file with no block, and as a drop-in over a good
    block (it survives the merge). The `_operator` docstring is the doc."""
    stranger(layers["host"])
    if "drop-in" in layers:
        _drop_in(layers["drop-in"])
    op = _operator.read()
    assert op.default is None and op == _operator.NOTHING, layers
    assert '"operator": null' in _operator.__doc__


#: SM-759: every way the host file can fail to read, each raised through a
#: different door (sessiontools' JSON check, its object check, its merge's
#: `_locked` check, and the OS).
BAD_FILES = {
    "not-json": "{not json",
    "not-an-object": "[]",
    "bad-locked": '{"_locked": 7}',
    "unreadable": '{"operator": {"mailbox": "alice"}}',
}


@pytest.mark.parametrize("why", sorted(BAD_FILES))
def test_a_bad_files_refusal_shows_the_settings_home_as_tilde(fleet, monkeypatch,
                                                                 stranger, why):
    """SM-759: the refusal quotes a bad file as `~/.sessionkit/...`, never by
    its absolute path, which holds the account's name on every real host
    (`/Users/<account>/...`). The sandbox home stands in for that path: it
    must not reach the words, through `read()` or through the gate."""
    path = stranger(BAD_FILES[why])
    if why == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root reads a mode-000 file")
        path.chmod(0)
    home = str(_operator.settings_home())
    try:
        op = _operator.read()
        assert not op.configured and op.privileged == ()  # fail safe: nothing configured
        assert op.problem and "~/.sessionkit/tools/settings/session.json" in op.problem, op.problem
        assert home not in op.problem, op.problem
        as_seat(monkeypatch, "alice", SID)
        done = sub("add", "atomnote", "--seat")
    finally:
        path.chmod(0o600)
    assert done.exit_code == 2, done.output
    said = _refusal_words(done)
    assert "~/.sessionkit/tools/settings/session.json" in said, said
    assert home not in said, said


def test_tilde_matches_the_home_only_as_a_whole_path(monkeypatch):
    """`tilde` shortens the settings home and nothing else: a longer
    sibling (`/home/al` is not `/home/alice`) and a home of `/` are left
    alone; the quoted form an OSError prints is shortened too."""
    monkeypatch.setattr(_operator, "settings_home", lambda: Path("/home/al"))
    assert _operator.tilde("/home/al/.settings/s.json: not JSON") == "~/.settings/s.json: not JSON"
    assert _operator.tilde("denied: '/home/al/.settings/s.json'") == "denied: '~/.settings/s.json'"
    assert _operator.tilde("home is /home/al") == "home is ~"
    assert _operator.tilde("/home/alice/.settings/s.json") == "/home/alice/.settings/s.json"
    assert _operator.tilde("/home/al.old/s.json") == "/home/al.old/s.json"
    monkeypatch.setattr(_operator, "settings_home", lambda: Path("/"))
    assert _operator.tilde("/etc/s.json") == "/etc/s.json"

    def no_home():
        raise KeyError("no such uid")
    monkeypatch.setattr(_operator, "settings_home", no_home)
    assert _operator.tilde("/home/al/s.json") == "/home/al/s.json"
