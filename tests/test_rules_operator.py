"""SM-727 fix-002: `$operator`, the host's alias for it, and no default in code.

Design `designs/operator-config.md` (the SM-727 task dir) sections (d) and
(g.2). Who the operator is comes from the host's config (`_operator.read()`,
the conftest seam), never a name in code:

- `$operator` is the variable and `set operator` the setting. Their value
  is the config's (`<mailbox>@*` and the full address), filled on EVERY
  loader path, a host with no locked file included (d.2: hq's state).
- The operator's MAILBOX is an alias for `$operator` on that host only
  (d.3, Q2): `$judy` where the mailbox is `judy`, `$alice` where it is
  `alice`. Elsewhere it is an unknown variable, a named bad line.
- With no config, `$operator` matches nothing, and a `set urgent $operator`
  line is named with the key to set.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import SC6_OPERATOR
from test_pager_listen_judges import SESSION, rig  # noqa: F401 - `rig` is a fixture

from sessionmail import _operator, _seat, pager, rules

HOST = "hq.example"
SID = "0be1a70e-1111-2222-3333-444455556666"
ME = rules.Me.of(session=SID, seat="eve", host=HOST)
NOW = datetime(2030, 12, 16, 12, 34, 56, tzinfo=timezone.utc)
SUBSCRIBED = ("X-Original-To", "atomnote")   # not to me: only a carry! line wakes it

ALICE_HOST = '{"operator": {"mailbox": "alice", "privileged": ["alice"]}}'


def mail(sender: str, *more: tuple[str, str]) -> rules.Facts:
    head = "".join(f"{k}: {v}\n" for k, v in (("From", sender), SUBSCRIBED, *more))
    return rules.Facts.from_bytes((head + "\n").encode(), env={})


def load(tmp_path: Path, *, seat_rules: str | None = None,
         locked: str | None = None) -> rules.Layers:
    """`load_layers` on a fake host under TMP_PATH: a seat `eve` with
    SEAT_RULES, and a locked file holding LOCKED (None: no locked file)."""
    home = tmp_path / "home"
    if seat_rules is not None:
        path = home / "seats" / "eve" / rules.RULES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(seat_rules)
    locked_path = tmp_path / "etc" / rules.LOCKED_FILE
    if locked is not None:
        locked_path.parent.mkdir(parents=True, exist_ok=True)
        locked_path.write_text(locked)
    return rules.load_layers("eve", None, {}, home=home, locked=locked_path)


def judged(layers: rules.Layers, facts: rules.Facts) -> tuple[str, str | None, str]:
    got = rules.judge(facts, layers, ME, NOW)
    return got.action, got.rule, got.layer


def test_operator_variable_and_config_alias(tmp_path, stranger):
    """THE fix-002 acceptance test (design g.2)."""
    judy = "Judy <Judy@hq.example>"

    # ── the SC6 host: {"operator": {"mailbox": "judy", ...}} ──
    stranger(SC6_OPERATOR)
    # With NO locked file (hq's state, d.2): both impure readers fill it.
    bare = load(tmp_path)
    assert rules.operator(bare.settings) == ("judy@*",)
    assert rules.operator(rules.read_settings(tmp_path / "no" / "locked")) == ("judy@*",)
    assert judged(bare, mail(judy)) == ("wake", "carry! from:$operator", rules.BUILT_IN)
    # `$operator` and the host's alias `$judy` both match; the rule keeps
    # the line as written, which is what a verdict and `pager why` show.
    for line in ("carry! from:$operator", "carry! from:$judy"):
        got = load(tmp_path / line[-4:], seat_rules=f"{line}\n")
        assert got.errors == (), got.errors
        assert judged(got, mail(judy)) == ("wake", line, rules.SEAT)
    # `set urgent $judy` and `set judy` parse in the locked file, as
    # `$operator` and `operator`.
    got = load(tmp_path / "urgent", locked="set urgent $judy alerts@hq\n")
    assert got.errors == ()
    assert got.settings.urgent == ("$operator", "alerts@hq")
    assert rules.urgent_senders(settings=got.settings) == ("judy@*", "alerts@hq")
    got = load(tmp_path / "boss", locked="set judy boss@*\n")
    assert got.errors == () and rules.operator(got.settings) == ("boss@*",)

    # ── alice's host: the alias is HER mailbox, and `$judy` is nobody ──
    stranger(ALICE_HOST)
    got = load(tmp_path / "alice", seat_rules="carry! from:$judy\ncarry! from:$alice\n")
    assert [e.reason for e in got.errors] == [
        "unknown variable '$judy' (known: $alice, $me, $operator, $urgent)"]
    assert judged(got, mail("alice@x.example")) == ("wake", "carry! from:$alice", rules.SEAT)
    assert judged(got, mail(judy))[0] == "ride"
    assert judged(load(tmp_path / "alice-bare"), mail("alice@x.example"))[1] == \
        "carry! from:$operator"

    # ── a stranger's host: no config, so `$operator` is nobody ──
    stranger(None)
    got = load(tmp_path / "stranger", locked="set urgent $operator eve@*\n")
    assert rules.operator(got.settings) == ()
    assert judged(got, mail(judy)) == ("ride", "ride *", rules.BUILT_IN)
    assert rules.urgent_senders(settings=got.settings) == ("eve@*",)
    # The line is kept (config may land after it), and named with the key.
    (named,) = got.errors
    assert named.line == 1 and rules.OPERATOR_EMPTY == named.reason
    assert _operator.OPERATOR_KEY in named.reason and _operator.KEY_FILE in named.reason
    assert [e.reason for e in load(tmp_path / "s2", seat_rules="wake from:$judy\n").errors] == [
        "unknown variable '$judy' (known: $me, $operator, $urgent)"]

    # ── no default in code ──
    assert not hasattr(rules, "MI" "KE") and not hasattr(rules, "mi" "ke")
    assert rules.operator(rules.NO_SETTINGS) == ()


@pytest.mark.parametrize("block, aliases", [
    ({"mailbox": "Judy"}, {"judy"}),
    ({"mailbox": "alice", "address": "ops@elsewhere.example"}, {"alice"}),
    ({"address": "judy@hq.example"}, set()),        # an address is not a box here (ST-116)
    ({"mailbox": "me"}, set()),                     # a built-in is never an alias
    ({"mailbox": "operator"}, set()),
    ({"mailbox": "sessionmail+super"}, set()),      # not an identifier
    ({"mailbox": "carry"}, set()),                  # a setting name is never an alias
    ({"privileged": ["judy"]}, set()),              # no operator: nothing
])
def test_the_alias_is_the_operators_mailbox_only(block, aliases):
    op = _operator._from_block(block)
    assert rules.operator_aliases(op) == frozenset(aliases)


def test_the_operator_globs_are_the_mailbox_on_any_host_and_the_address():
    op = _operator._from_block({"mailbox": "Alice", "address": "Alice@Home.example"})
    assert rules.operator_globs(op) == ("alice@*", "alice@home.example")
    assert rules.operator_globs(_operator.NOTHING) == ()


def test_an_alias_is_a_whole_glob_or_named(stranger):
    """An alias is a variable: a partial one is a bad line, as `$me*` is."""
    _, errors = rules.parse("wake from:$judy*\n", "seat", aliases=frozenset({"judy"}))
    assert [e.reason for e in errors] == ["$judy must be the whole glob, not part of '$judy*'"]


def test_set_refuses_what_cannot_stand_in_it_by_its_written_name():
    got, errors = rules.parse_settings("set judy $judy\nset carry $operator\n", "locked",
                                       aliases=frozenset({"judy"}))
    assert [e.reason for e in errors] == [
        "'set operator' cannot name $judy: it is the value being set",
        "'set carry' takes one word, on or off (got '$operator')"]
    _, errors = rules.parse_settings("set operator $me\n", "locked")
    assert [e.reason for e in errors] == [
        "$me cannot stand in 'set operator' (only $operator, or this host's alias for it, "
        "and only in 'set urgent')"]


def test_a_running_listener_re_reads_the_operator_when_the_config_changes(tmp_path, stranger):
    """design A6 (RULED N5): `LiveLayers` re-reads at the next mail after a
    change, and the operator's config is one of the files it watches."""
    stranger(SC6_OPERATOR)
    live = rules.LiveLayers("eve", None, {}, home=tmp_path / "home",
                            locked=tmp_path / rules.LOCKED_FILE)
    assert rules.operator(live.current().settings) == ("judy@*",)
    assert live.loads == 1 and rules.operator(live.current().settings) == ("judy@*",)
    assert live.loads == 1, "nothing changed, nothing re-read"
    stranger(ALICE_HOST)
    assert rules.operator(live.current().settings) == ("alice@*",)
    assert live.loads == 2


def test_a_locked_alias_line_is_written_through_the_verb(rig, monkeypatch):  # noqa: F811
    """`pager rule add --locked 'set urgent $judy ...'` is checked as the
    loader reads it: on the SC6 host the alias holds, so it is written."""
    monkeypatch.setenv("TM_AGENT_ADDRESS", "ivan")
    _seat.resolve()
    result = CliRunner().invoke(pager.cli, ["rule", "add", "--locked", "set urgent $judy eve@*"],
                                prog_name="pager")
    assert result.exit_code == 0, result.output
    assert rules.locked_path().read_text() == "set urgent $judy eve@*\n"
    assert rules.load_layers(None, SESSION).settings.urgent == ("$operator", "eve@*")


def test_a_mailbox_named_after_a_setting_never_shadows_it(tmp_path, stranger):
    """Review of fix-002: on a host whose operator's mailbox is `carry`,
    `set carry off` is still the switch. Were `carry` an alias it would be
    `set operator off`: carry silently left on and the operator `off`."""
    stranger('{"operator": {"mailbox": "carry"}}')
    got = load(tmp_path, locked="set carry off\n")
    assert got.errors == ()
    assert rules.carry_on(got.settings) is False
    assert rules.operator(got.settings) == ("carry@*",)


@pytest.mark.parametrize("token", ["priority:$judy", "draft:$judy"])
def test_an_alias_in_a_value_kind_is_named_as_written(token):
    """`priority:` and `draft:` take a value, not a variable: an alias is
    refused as `$operator` is, and the reason names the line's own word."""
    _, errors = rules.parse(f"wake {token}\n", "seat", aliases=frozenset({"judy"}))
    kind = token.split(":")[0]
    assert [e.reason for e in errors] == [f"{kind}: takes a value, not a variable ('$judy')"]


def test_a_drop_in_edited_in_place_is_re_read(tmp_path, stranger):
    """design A6 (RULED N5): a `session.d/*.json` drop-in rewritten in
    place leaves the folder's stamp alone; the drop-in's own stamp moves,
    so the running listener still re-reads `$operator`."""
    stranger(SC6_OPERATOR)
    drop_in = _operator.settings_home() / _operator.DIR / "session.d" / "x.json"
    drop_in.parent.mkdir(parents=True, exist_ok=True)
    drop_in.write_text('{"operator": {"mailbox": "alice"}}')
    live = rules.LiveLayers("eve", None, {}, home=tmp_path / "home",
                            locked=tmp_path / rules.LOCKED_FILE)
    assert rules.operator(live.current().settings) == ("alice@*",)
    folder = drop_in.parent.stat().st_mtime_ns
    with drop_in.open("r+") as fh:            # in place: same inode, folder untouched
        fh.write('{"operator": {"mailbox": "bobby"}}')
        fh.truncate()
    assert drop_in.parent.stat().st_mtime_ns == folder
    assert rules.operator(live.current().settings) == ("bobby@*",)
    assert live.loads == 2


# ── SM-806: the DEFAULT operator names the account and never fills `$operator` ──

def test_r1_the_default_never_fills_operator(tmp_path, stranger):
    """Test 12, R1 (ruled NO). On a host
    with NO operator block the operator defaults to the installing account
    (the conftest seam's `acct-default`), and that default stays out of
    `$operator`: `operator_globs` and `operator_aliases` read only the
    configured `mailbox`/`address`, so `carry! from:$operator` matches no
    From, the urgent default marks nobody urgent, and the N7 voice hold
    holds for nobody. `$operator` stays config-only."""
    from fnmatch import fnmatchcase

    from conftest import ACCOUNT_DEFAULT

    from sessionmail import _say
    stranger(None)
    op = _operator.read()
    assert op.default == ACCOUNT_DEFAULT                       # the default is there...
    assert rules.operator_globs(op) == ()                      # ...and fills nothing
    assert rules.operator_aliases(op) == frozenset()
    assert rules.operator(rules.read_settings(tmp_path / "no" / "locked")) == ()
    bare = load(tmp_path)
    assert rules.operator(bare.settings) == ()
    account = f"{ACCOUNT_DEFAULT}@h.example"
    for sender in (account, f"Acct <{account}>"):
        assert judged(bare, mail(sender)) == ("ride", "ride *", rules.BUILT_IN), sender
    urgent = rules.urgent_senders(("h.example",), settings=bare.settings)
    assert not any(fnmatchcase(account, rules._glob(g)) for g in urgent), urgent
    assert not _say.is_operator(account, bare.settings)
    assert not _say.is_operator(account)
    # A seat's own `carry! from:$operator` line matches nothing from it either.
    got = load(tmp_path / "alias", seat_rules="carry! from:$operator\n")
    assert judged(got, mail(account))[0] == "ride"


def test_a_seat_named_like_the_account_is_not_the_operator(tmp_path, stranger):
    """Test 6 (design d), the rules side: same uid, so a seat can be named
    `acct-default`. On a no-block host it is not the operator to the rules
    (`rules.operator` is `()`) nor to N7 (`_say.is_operator`)."""
    from conftest import ACCOUNT_DEFAULT

    from sessionmail import _say
    stranger(None)
    assert rules.operator(rules.read_settings(tmp_path / "no" / "locked")) == ()
    assert not _say.is_operator(f"{ACCOUNT_DEFAULT}@h.example")
    assert not _operator.read().is_privileged(ACCOUNT_DEFAULT)
