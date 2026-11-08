"""SM-1004 do-001: the frozen-turn watch's settings (design.md 2.6, store).

Four flat keys in the host file `~/.sessionkit/mail/settings/mail.json`,
read raw from the host file alone by `_settings.frozen`:

- `frozen_watch`: ON only for the boolean `true` (the `hooks` rule); any
  other value is ignored with a reason and the watch stays off.
- `frozen_rules`: default ["silence"] alone (rule 2b, "chain", is opt-in
  until O2-4); an unknown name is ignored with a reason.
- `frozen_after_seconds` (N): default 720, floored at
  max(600, API_TIMEOUT_MS/1000 + 120).
- `frozen_chain_seconds` (R): default 900, floored at
  max(600, first-byte s + API_TIMEOUT_MS/1000 + 120).
- The timers default to 600 s and 180 s when the env cannot be read.
- No `frozen_notice_to`: this build sends no mail (O2-6).

Hermetic: every test passes its own env mapping (HOME under tmp_path, and
the two timers set or not), so the running seat's own API_TIMEOUT_MS never
reaches a test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from sessionmail import _settings
from sessionmail._settings import (
    API_TIMEOUT_VAR,
    CHAIN,
    FIRST_BYTE_VAR,
    FROZEN_AFTER,
    FROZEN_CHAIN,
    FROZEN_RULES,
    FROZEN_WATCH,
    SHOWN,
    SILENCE,
    frozen,
)


@pytest.fixture
def env(tmp_path):
    """An env with its own HOME and neither timer set."""
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home)}


def host(env, data) -> Path:
    """Write DATA (a JSON-able object, or raw text) as ENV's host file."""
    p = _settings.path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


# ── the names, and what this build does NOT carry ──────────────────────────

def test_the_four_keys_and_their_names():
    assert _settings.FROZEN_KEYS == ("frozen_watch", "frozen_rules",
                                     "frozen_after_seconds", "frozen_chain_seconds")
    assert _settings.FROZEN_RULE_NAMES == ("silence", "chain")
    assert _settings.FROZEN_RULES_DEFAULT == ("silence",)
    assert (API_TIMEOUT_VAR, FIRST_BYTE_VAR) == ("API_TIMEOUT_MS",
                                                 "CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS")


def test_there_is_no_notice_address_in_this_build(env):
    """O2-6: no mail, so no frozen_notice_to anywhere: not a key, not a
    field, not in the schema (which then refuses it)."""
    assert "frozen_notice_to" not in _settings.FROZEN_KEYS
    assert not any("notice" in name for name in vars(_settings) if name.startswith("FROZEN"))
    w = frozen(env)
    assert not any("notice" in f for f in w.__dataclass_fields__)
    assert "frozen_notice_to" not in json.loads(_settings.schema())["properties"]


# ── no file, and a file that decides nothing ───────────────────────────────

def test_no_file_is_off_with_every_default_and_no_reason(env):
    w = frozen(env)
    assert w.on is False
    assert w.rules == ("silence",)
    assert (w.after_seconds, w.chain_seconds) == (720, 900)
    assert (w.after.floor, w.chain.floor) == (720, 900)   # the env defaults' floors
    assert not w.after.raised and not w.chain.raised
    assert w.ignored == () and w.why == "" and w.reasons == () and w.floored == ()


def test_reading_writes_nothing(env):
    frozen(env)
    assert not _settings.path(env).parent.exists()


@pytest.mark.parametrize("text, why", [
    ("{not json", "is not JSON"),
    (b"\xff\xfe{}", "is not JSON"),
    ("[]", "top level is a list, not an object"),
    ('"frozen_watch"', "top level is a string, not an object"),
    ("true", "top level is a boolean, not an object"),
])
def test_a_file_that_is_not_an_object_is_off_with_a_why(env, text, why):
    p = _settings.path(env)
    p.parent.mkdir(parents=True)
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text)
    w = frozen(env)
    assert w.on is False and w.rules == ("silence",)
    assert (w.after_seconds, w.chain_seconds) == (720, 900)
    assert why in w.why and SHOWN in w.why
    assert w.reasons[0] == w.why


def test_a_directory_where_the_file_belongs_is_off_with_a_why(env):
    _settings.path(env).mkdir(parents=True)
    w = frozen(env)
    assert w.on is False and "could not be read" in w.why


def test_a_dangling_symlink_is_off_with_a_why(env, tmp_path):
    p = _settings.path(env)
    p.parent.mkdir(parents=True)
    p.symlink_to(tmp_path / "nowhere.json")
    w = frozen(env)
    assert w.on is False and "symlink to nothing" in w.why


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="mode 000 needs a non-root POSIX")
def test_an_unreadable_file_is_off_with_a_why(env):
    p = host(env, {FROZEN_WATCH: True})
    p.chmod(0)
    try:
        w = frozen(env)
    finally:
        p.chmod(0o600)
    assert w.on is False and "could not be read" in w.why


# ── frozen_watch: on only for the boolean true ────────────────────────────

def test_true_turns_it_on(env):
    host(env, {FROZEN_WATCH: True})
    w = frozen(env)
    assert w.on is True and w.reasons == ()


@pytest.mark.parametrize("data", [{}, {"version": 1, "hooks": True}, {FROZEN_WATCH: False}])
def test_absent_or_false_is_off_silently(env, data):
    host(env, data)
    w = frozen(env)
    assert w.on is False and w.ignored == () and w.reasons == ()


@pytest.mark.parametrize("value", ["true", 1, 0, None, "yes", [True], {"on": True}, 1.0])
def test_a_value_that_is_not_a_boolean_is_ignored_with_a_reason(env, value):
    """Design 2.6 / SC4 4c: "true" and 1 are OFF, never truthy-on."""
    host(env, {FROZEN_WATCH: value})
    w = frozen(env)
    assert w.on is False
    (reason,) = w.ignored
    assert reason.startswith(f'"frozen_watch": {json.dumps(value)} in {SHOWN} is ignored')
    assert "only the boolean true turns it on, so it is off" in reason
    assert w.reasons == (reason,)


def test_a_long_ignored_value_is_bounded_in_its_reason(env):
    host(env, {FROZEN_WATCH: "x" * 500})
    (reason,) = frozen(env).ignored
    shown = reason.split(": ", 1)[1].split(" in ~/", 1)[0]
    assert len(shown) == _settings._FROZEN_SHOWN_MAX and shown.endswith("...")


@pytest.mark.parametrize("data", [{"hooks": False, FROZEN_WATCH: True},
                                  {FROZEN_WATCH: True},          # no `hooks` key: a hooks fault
                                  {"hooks": "true", FROZEN_WATCH: True},
                                  {"monitor": False, FROZEN_WATCH: True}])
def test_the_switch_is_independent_of_hooks_and_monitor(env, data):
    """Read raw from the host file, never folded into `hooks` (the monitor
    key's own off is the monitor's to act on, not this reader's)."""
    host(env, data)
    assert frozen(env).on is True


def test_it_is_read_at_each_call_not_once(env):
    """Design 2.6: read at each check, so turning it off takes effect at the
    next check without a relaunch."""
    host(env, {FROZEN_WATCH: True})
    assert frozen(env).on is True
    host(env, {FROZEN_WATCH: False})
    assert frozen(env).on is False
    _settings.path(env).unlink()
    assert frozen(env).on is False


# ── frozen_rules ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, rules", [
    (["silence"], ("silence",)),
    (["chain"], ("chain",)),
    (["silence", "chain"], ("silence", "chain")),
    (["chain", "silence"], ("silence", "chain")),           # FROZEN_RULE_NAMES order
    (["chain", "chain", "silence", "chain"], ("silence", "chain")),
])
def test_the_rules_named_run(env, value, rules):
    host(env, {FROZEN_RULES: value})
    w = frozen(env)
    assert w.rules == rules and w.ignored == ()
    assert (SILENCE, CHAIN) == ("silence", "chain")


def test_absent_rules_is_silence_alone(env):
    """Rule 2b is opt-in: absent `frozen_rules` runs rule 2a alone."""
    host(env, {FROZEN_WATCH: True})
    assert frozen(env).rules == ("silence",)


@pytest.mark.parametrize("value, rules, bad", [
    (["bogus", "chain"], ("chain",), ['"bogus"']),
    (["silence", 1], ("silence",), ["1"]),
    (["Silence", "chain", None], ("chain",), ['"Silence"', "null"]),
])
def test_an_unknown_name_is_ignored_with_a_reason(env, value, rules, bad):
    host(env, {FROZEN_RULES: value})
    w = frozen(env)
    assert w.rules == rules
    assert len(w.ignored) == len(bad)
    for reason, shown in zip(w.ignored, bad):
        assert reason == (f'"frozen_rules" entry {shown} in {SHOWN} is ignored: '
                          'the rules are "silence" or "chain"')


@pytest.mark.parametrize("value", [[], ["bogus"], ["chains", "Silence"]])
def test_a_list_naming_no_known_rule_is_the_default_with_a_reason(env, value):
    host(env, {FROZEN_RULES: value})
    w = frozen(env)
    assert w.rules == ("silence",)
    assert w.ignored[-1] == f'"frozen_rules" in {SHOWN} names no rule, so silence'
    assert len(w.ignored) == len(value) + 1


@pytest.mark.parametrize("value", ["silence", None, {"silence": True}, True, 2])
def test_rules_that_are_not_a_list_are_the_default_with_a_reason(env, value):
    host(env, {FROZEN_RULES: value})
    w = frozen(env)
    assert w.rules == ("silence",)
    (reason,) = w.ignored
    assert reason == (f'"frozen_rules": {json.dumps(value)} in {SHOWN} is ignored: '
                      "it is not a list of rule names, so silence")


# ── N and R: the numbers, as written ──────────────────────────────────────

@pytest.mark.parametrize("key, value", [(FROZEN_AFTER, 1000), (FROZEN_AFTER, 720.5),
                                        (FROZEN_CHAIN, 1800), (FROZEN_CHAIN, 900.25)])
def test_a_number_above_the_floor_is_used_as_written(env, key, value):
    host(env, {key: value})
    w = frozen(env)
    n = w.after if key == FROZEN_AFTER else w.chain
    assert n.key == key and n.seconds == value and n.asked == value
    assert not n.raised and n.floored == "" and n.ignored == "" and w.reasons == ()


@pytest.mark.parametrize("key, default", [(FROZEN_AFTER, 720), (FROZEN_CHAIN, 900)])
@pytest.mark.parametrize("text", ["true", "false", '"720"', "0", "-5", "null", "NaN",
                                  "Infinity", "-Infinity", "[720]", "{}"])
def test_a_value_that_is_not_a_positive_number_is_the_default_with_a_reason(env, key, default, text):
    host(env, f'{{"{key}": {text}}}')
    w = frozen(env)
    n = w.after if key == FROZEN_AFTER else w.chain
    assert n.seconds == default and n.asked == default and not n.raised
    assert n.ignored.startswith(f'"{key}": ')
    assert n.ignored.endswith(f"in {SHOWN} is ignored: it is not a number of seconds above "
                              f"zero, so {default} s")
    assert w.ignored == (n.ignored,) and w.reasons == (n.ignored,)


# ── the floors ─────────────────────────────────────────────────────────────

def test_case_2n_300_under_a_300_s_api_timeout_is_floored_to_600_with_the_reason(env):
    """Design 2.9 row 2n: `frozen_after_seconds: 300` with
    API_TIMEOUT_MS=300000 is floored to max(600, 300 + 120) = 600, and the
    reason names the floor and its input."""
    host(env, {FROZEN_WATCH: True, FROZEN_AFTER: 300})
    env[API_TIMEOUT_VAR] = "300000"
    w = frozen(env)
    assert w.after_seconds == 600
    assert w.after.asked == 300 and w.after.floor == 600 and w.after.raised
    assert w.after.floored == ("frozen_after_seconds 300 s is raised to 600 s, the floor: "
                               "max(600 s, API_TIMEOUT_MS=300000 + 120 s)")
    assert w.floored == (w.after.floored,) and w.reasons == (w.after.floored,)
    assert w.after.ignored == ""


def test_n_under_the_default_api_timeout_is_floored_to_720(env):
    host(env, {FROZEN_AFTER: 300})
    w = frozen(env)
    assert w.after_seconds == 720 and w.after.raised
    assert w.after.floored == ("frozen_after_seconds 300 s is raised to 720 s, the floor: "
                               "max(600 s, API_TIMEOUT_MS unset, so 600 s + 120 s)")


def test_a_long_api_timeout_raises_the_default_n_too(env):
    """The default is floored like a written value: API_TIMEOUT_MS=900000
    puts N's floor at 1020 s, over the 720 s default."""
    env[API_TIMEOUT_VAR] = "900000"
    w = frozen(env)
    assert w.after_seconds == 1020 and w.after.asked == 720 and w.after.raised
    assert "frozen_after_seconds 720 s is raised to 1020 s" in w.after.floored
    assert w.after.ignored == ""


def test_n_exactly_at_its_floor_is_not_raised(env):
    host(env, {FROZEN_AFTER: 720})
    w = frozen(env)
    assert w.after_seconds == 720 and not w.after.raised and w.floored == ()


def test_an_ignored_n_whose_default_is_under_the_floor_gives_both_reasons(env):
    host(env, {FROZEN_AFTER: "soon"})
    env[API_TIMEOUT_VAR] = "900000"
    w = frozen(env)
    assert w.after_seconds == 1020
    assert w.after.ignored and w.after.floored
    # 180 + 900 + 120 = 1200 s raises R's default 900 s as well
    assert w.chain_seconds == 1200 and w.chain.raised
    assert w.reasons == (w.after.ignored, w.after.floored, w.chain.floored)


def test_r_under_its_default_floor_is_floored_to_900(env):
    host(env, {FROZEN_CHAIN: 300})
    w = frozen(env)
    assert w.chain_seconds == 900 and w.chain.raised
    assert w.chain.floored == (
        "frozen_chain_seconds 300 s is raised to 900 s, the floor: "
        "max(600 s, CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS unset, so 180 s "
        "+ API_TIMEOUT_MS unset, so 600 s + 120 s)")


def test_r_follows_both_timers(env):
    env[FIRST_BYTE_VAR] = "300000"
    env[API_TIMEOUT_VAR] = "600000"
    w = frozen(env)
    assert w.chain.floor == 1020 and w.chain_seconds == 1020 and w.chain.raised
    assert "CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS=300000 + API_TIMEOUT_MS=600000" in w.chain.floored


def test_short_timers_floor_at_600(env):
    """max(600, ...): 60 s + 300 s + 120 s = 480 s is under the 600 s minimum."""
    host(env, {FROZEN_CHAIN: 300, FROZEN_AFTER: 300})
    env[FIRST_BYTE_VAR] = "60000"
    env[API_TIMEOUT_VAR] = "300000"
    w = frozen(env)
    assert (w.after.floor, w.chain.floor) == (600, 600)
    assert (w.after_seconds, w.chain_seconds) == (600, 600)
    assert w.floored == (w.after.floored, w.chain.floored)


def test_a_fractional_api_timeout_is_kept_in_seconds(env):
    env[API_TIMEOUT_VAR] = "600500"
    w = frozen(env)
    assert w.api_timeout.seconds == 600.5 and w.after.floor == 720.5
    assert "720.5 s" in w.after.floored


# ── the timers: unset, empty or unreadable is the default ─────────────────

@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_or_empty_timer_is_its_default_silently(env, value):
    if value is not None:
        env[API_TIMEOUT_VAR] = value
        env[FIRST_BYTE_VAR] = value
    w = frozen(env)
    assert (w.api_timeout.seconds, w.first_byte.seconds) == (600, 180)
    assert w.api_timeout.ignored == "" and w.first_byte.ignored == ""
    assert w.api_timeout.source == "API_TIMEOUT_MS unset, so 600 s"
    assert (w.after.floor, w.chain.floor) == (720, 900) and w.reasons == ()


@pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5e5", "600s", "300000.0"])
def test_an_unreadable_timer_is_its_default_with_a_reason(env, value):
    """Design 2.6: "If a value cannot be read, the defaults (600 s, 180 s)
    give floors of 720 s and 900 s"."""
    env[API_TIMEOUT_VAR] = value
    env[FIRST_BYTE_VAR] = value
    w = frozen(env)
    assert (w.api_timeout.seconds, w.first_byte.seconds) == (600, 180)
    assert (w.after.floor, w.chain.floor) == (720, 900)
    assert w.api_timeout.ignored == (
        f"API_TIMEOUT_MS={json.dumps(value)} is ignored: it is not a whole number of "
        "milliseconds above zero, so the floor takes 600 s")
    assert w.first_byte.ignored.startswith("CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS=")
    assert w.first_byte.ignored.endswith("so the floor takes 180 s")
    assert w.reasons == (w.api_timeout.ignored, w.first_byte.ignored)


def test_a_timer_is_read_with_its_whitespace_stripped(env):
    env[API_TIMEOUT_VAR] = " 300000\n"
    assert frozen(env).api_timeout.seconds == 300


def test_the_timers_come_from_the_env_given_not_the_file(env):
    """A host-file key named like a timer is not a timer: the floors read
    the monitor's environment alone."""
    host(env, {"API_TIMEOUT_MS": 60000, FROZEN_AFTER: 300})
    w = frozen(env)
    assert w.after.floor == 720


def test_the_default_env_is_os_environ(env, monkeypatch):
    monkeypatch.setenv("HOME", env["HOME"])
    monkeypatch.setenv(API_TIMEOUT_VAR, "300000")
    monkeypatch.delenv(FIRST_BYTE_VAR, raising=False)
    host(env, {FROZEN_WATCH: True, FROZEN_AFTER: 300})
    w = frozen()
    assert w.on and w.after_seconds == 600


# ── all four at once, and the order of the reasons ────────────────────────

def test_every_reason_in_one_tuple_in_order(env):
    host(env, {FROZEN_WATCH: "on", FROZEN_RULES: ["chain", "x"],
               FROZEN_AFTER: 100, FROZEN_CHAIN: False})
    env[FIRST_BYTE_VAR] = "soon"
    w = frozen(env)
    assert w.on is False and w.rules == ("chain",)
    watch, rule, chain = w.ignored
    assert watch.startswith('"frozen_watch"') and rule.startswith('"frozen_rules" entry "x"')
    assert chain.startswith('"frozen_chain_seconds": false')
    assert w.reasons == (watch, rule, chain, w.first_byte.ignored, w.after.floored)
    assert w.chain_seconds == 900 and not w.chain.raised   # its default is its floor


# ── the schema ─────────────────────────────────────────────────────────────

def test_the_schema_names_the_four_keys_with_no_variable():
    props = json.loads(_settings.schema())["properties"]
    assert props[FROZEN_WATCH]["type"] == "boolean"
    assert props[FROZEN_RULES]["items"] == {"enum": ["silence", "chain"]}
    assert props[FROZEN_AFTER]["type"] == props[FROZEN_CHAIN]["type"] == "number"
    for key in _settings.FROZEN_KEYS:
        assert "x-env" not in props[key], key


def test_the_template_carries_none_of_them():
    assert not set(json.loads(_settings.template())) & set(_settings.FROZEN_KEYS)


def test_the_schema_accepts_the_four_keys():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"version": 1, "hooks": True, FROZEN_WATCH: True,
                         FROZEN_RULES: ["silence", "chain"], FROZEN_AFTER: 720,
                         FROZEN_CHAIN: 900.5}, json.loads(_settings.schema()))


@pytest.mark.parametrize("data", [{FROZEN_WATCH: "true"}, {FROZEN_RULES: ["bogus"]},
                                  {FROZEN_RULES: "silence"}, {FROZEN_AFTER: 0},
                                  {FROZEN_CHAIN: "900"}, {"frozen_notice_to": "x@y"}])
def test_the_schema_refuses_what_the_reader_ignores(data):
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, json.loads(_settings.schema()))
