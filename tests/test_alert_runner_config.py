"""SM-798 fix-001: the alert runner comes from host config, never a name in code.

The task's `designs/alert-runner-config.md`: the runner's LOCAL PART is
`alert_runner` in the mail tool's host settings (`~/.sessionkit/mail/settings/mail.json`,
and its template), read through `_operator.host_values("mail")` (pwd's home,
TEMPLATE and HOST layers only) by `rules.alert_runner()`. The code joins it to
this host's own forms. One test per row of the design's fail-safe table, then
the live re-read (`LiveLayers`) and the end-to-end judgement.

Every row's rule: a bad value can only REMOVE the runner; it never widens the
list, and nothing raises out of the reader. Hosts and names here are neutral
(`.example`, `alerts`), never a real host, tailnet or person.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import RUNNER, RUNNER_SETTINGS, SC6_SETTINGS, write_mail_settings

from sessionmail import _operator, _settings, rules
from sessionmail.rules import Facts, Me, Settings, built_in, judge, urgent_senders

HOST = "box.example"
SHORT = "box"
FORMS = (HOST, SHORT)
SID = "0b5e2c1a-7f3d-4e2b-9a61-5c8d0e4f7a21"
ME = Me.of(session=SID, seat="watcher", host=HOST)
NOW = datetime(2030, 12, 8, 12, 34, 56, tzinfo=timezone.utc)
ENV = {"SESSIONMAIL_HOST": SHORT, "MAGICDNS_SUFFIX": "example"}


def configure(value) -> None:
    """The host file `~/.sessionkit/mail/settings/mail.json`, under the settings home
    (pwd's, the conftest seam), carrying `alert_runner: VALUE`."""
    write_mail_settings(_operator.settings_home(),
                        json.dumps({"version": 1, rules.ALERT_RUNNER_KEY: value}) + "\n")


def mail_dir(home):
    return home / _settings.DIR


def boss() -> tuple[str, ...]:
    """The operator's globs on the suite's host, whoever config says it is."""
    return rules.operator(rules.read_settings())


def default_list() -> tuple[str, ...]:
    return urgent_senders(FORMS)


def urgent(sender: str) -> Facts:
    head = f"From: {sender}\nX-Original-To: subscribed-box\nPriority: urgent\n\n"
    return Facts.from_bytes(head.encode(), env=ENV)


# ── the fail-safe table, row by row ──────────────────────────────────────────

def test_key_absent_is_no_runner():
    """Row 1: the key absent (the shipped template sets none) is no runner,
    the operator only."""
    write_mail_settings(_operator.settings_home(), '{"version": 1, "hooks": true}\n')
    assert rules.alert_runner() is None
    assert default_list() == boss()


def test_no_mail_settings_file_at_all_is_no_runner():
    """Row 1 again, with no mail.json at all: a fresh host."""
    assert not (mail_dir(_operator.settings_home()) / "mail.json").exists()
    assert rules.alert_runner() is None
    assert default_list() == boss()


def test_the_shipped_template_sets_no_runner():
    """The neutral default is IN the package: the template carries no key."""
    assert rules.ALERT_RUNNER_KEY not in json.loads(_settings.template())


def test_a_configured_runner_is_on_the_list_at_each_host_form(alert_runner):
    """Row 2: `"alert_runner": "alerts"` gives `alerts@<host>` for each host
    form, after the operator."""
    assert rules.alert_runner() == RUNNER
    assert default_list() == (*boss(), f"{RUNNER}@{HOST}", f"{RUNNER}@{SHORT}")


def test_the_host_forms_come_from_sm442_when_none_are_given(alert_runner):
    assert urgent_senders(env=ENV) == (*boss(), f"{RUNNER}@{SHORT}", f"{RUNNER}@{SHORT}.example")


def test_a_runner_is_lower_cased_as_the_hosts_are():
    configure("Alerts")
    assert rules.alert_runner() == "alerts"


@pytest.mark.parametrize("value", ["", " ", "\t", "\n"])
def test_an_empty_or_blank_runner_is_no_runner(value):
    """Row 3."""
    configure(value)
    assert rules.alert_runner() is None
    assert default_list() == boss()


@pytest.mark.parametrize("value", [7, 1.5, True, None, [RUNNER], {"name": RUNNER}])
def test_a_runner_that_is_not_a_string_is_no_runner(value):
    """Row 4: a number, a bool, null, a list or an object."""
    configure(value)
    assert rules.alert_runner() is None
    assert default_list() == boss()


@pytest.mark.parametrize("value", [
    f"{RUNNER}@{HOST}",          # a whole address
    f"{RUNNER}@*",               # a whole glob
    "@",
    f"{RUNNER} ops",             # white space inside
    f" {RUNNER}",                # white space around: not a local part either
    f"{RUNNER}\n",
    "$operator",                 # a variable
    "$me",
    f"{RUNNER}$",
    "*",                         # each glob character
    f"{RUNNER}*",
    "alert?",
    "[a]lerts",
    f"{RUNNER}]",
    f"!{RUNNER}",
    f"<{RUNNER}>",               # a placeholder copied from a design
    f"from:{RUNNER}",            # rule syntax
])
def test_a_runner_holding_anything_but_a_local_part_is_no_runner(value):
    """Row 5: `@`, white space, `$` or a glob character (`*?[]!`) means no
    runner: a local part only, never a glob, a whole address or a variable.
    Nothing it holds can widen the list."""
    configure(value)
    assert rules.alert_runner() is None
    assert default_list() == boss()


@pytest.mark.parametrize("value", ["alerts", "alert-runner", "alerts_2", "ops.alerts", "alerts+box"])
def test_a_plain_local_part_is_a_runner(value):
    configure(value)
    assert rules.alert_runner() == value


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"alerts"', ""])
def test_a_mail_file_that_is_not_a_json_object_is_no_runner(text):
    """Row 6: not JSON (or not an object): `host_values` raises, and the
    reader catches it."""
    write_mail_settings(_operator.settings_home(), text)
    assert rules.alert_runner() is None
    assert default_list() == boss()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_mail_file_is_no_runner():
    """Row 6: a file that cannot be read is no runner, never a traceback."""
    configure(RUNNER)
    path = mail_dir(_operator.settings_home()) / "mail.json"
    path.chmod(0)
    try:
        assert rules.alert_runner() is None
        assert default_list() == boss()
    finally:
        path.chmod(0o600)


def test_the_reader_never_raises_when_the_settings_reader_does(monkeypatch):
    """Row 6, by the seam: whatever `host_values` raises, no runner."""
    def boom(tool):
        raise RuntimeError("no sessiontools")
    monkeypatch.setattr(_operator, "host_values", boom)
    assert rules.alert_runner() is None


@pytest.mark.parametrize("where", [
    ".session/seats/watcher/settings/mail.json",
    ".session/sessions/" + SID + "/settings/mail.json",
    ".session/sessions/" + SID + "/settings/mail.d/50-env.json",
    ".session/sessions/" + SID + "/settings/mail.d/70-live.json",
])
def test_a_runner_set_only_in_a_seat_or_session_layer_is_never_read(where):
    """Row 7: the seat and session layers are agent-written (one uid), so an
    agent could put itself on the urgent list. They are never read: not
    under the settings home, and not under `$HOME`."""
    for home in (_operator.settings_home(), Path(os.environ["HOME"])):
        path = home / where
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}))
    assert rules.alert_runner() is None
    assert default_list() == boss()


def test_a_runner_under_home_that_is_not_the_accounts_is_not_read():
    """Row 8: `$HOME` is an environment variable an agent can point at a
    planted file; the reader takes pwd's home (`settings_home`), never it."""
    home = Path(os.environ["HOME"])
    assert home != _operator.settings_home()
    write_mail_settings(home, json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}))
    assert rules.alert_runner() is None
    assert default_list() == boss()


def test_set_urgent_in_the_locked_file_is_the_list_and_the_key_is_not_consulted(alert_runner):
    """Row 9: a locked `set urgent` line is the list, exactly as before; the
    configured runner is not added to it."""
    rules.locked_path().parent.mkdir(parents=True, exist_ok=True)
    rules.locked_path().write_text("set urgent ops@*\n")
    settings = rules.read_settings()
    assert settings.runner == RUNNER                     # read, but not consulted
    assert urgent_senders(FORMS, settings=settings) == ("ops@*",)


def test_a_runner_with_no_host_forms_is_not_on_the_list(alert_runner):
    """Row 10: no host forms, no runner: never `<runner>@*`."""
    assert urgent_senders(()) == boss()
    assert urgent_senders(("", "  ")) == boss()


# ── the layers it is read from: template and host only ───────────────────────

def test_the_template_layer_is_read():
    tool = mail_dir(_operator.settings_home())
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "mail.template.json").write_text(json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}))
    assert rules.alert_runner() == RUNNER


def test_the_host_file_wins_over_the_template():
    tool = mail_dir(_operator.settings_home())
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "mail.template.json").write_text(json.dumps({rules.ALERT_RUNNER_KEY: "template-runner"}))
    configure(RUNNER)
    assert rules.alert_runner() == RUNNER


def test_a_host_drop_in_is_read():
    drop_in = mail_dir(_operator.settings_home()) / "mail.d" / "10-runner.json"
    drop_in.parent.mkdir(parents=True, exist_ok=True)
    drop_in.write_text(json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}))
    assert rules.alert_runner() == RUNNER


# ── Settings.runner: filled by the impure readers, checked by the pure one ───

def test_read_settings_fills_the_runner_on_every_path(alert_runner):
    """With no locked file (the default host's state) and with one."""
    assert rules.read_settings().runner == RUNNER
    rules.locked_path().parent.mkdir(parents=True, exist_ok=True)
    rules.locked_path().write_text("set carry on\n")
    assert rules.read_settings().runner == RUNNER


def test_load_layers_fills_the_runner(alert_runner, tmp_path):
    layers = rules.load_layers("watcher", None, {}, home=tmp_path / "home",
                               locked=tmp_path / rules.LOCKED_FILE)
    assert layers.settings == RUNNER_SETTINGS


def test_parse_settings_never_reads_the_runner(alert_runner):
    """`parse_settings` is pure: only the impure readers fill `runner`."""
    got, errors = rules.parse_settings("set carry on\n", "locked")
    assert errors == () and got.runner is None


@pytest.mark.parametrize("bad", ["*", f"{RUNNER}@*", "$operator", " ", ""])
def test_a_bad_runner_handed_in_never_widens_the_list(bad):
    """`urgent_senders` checks `Settings.runner` again, so a Settings built
    by hand (a test, a future caller) cannot widen the list either."""
    assert urgent_senders(FORMS, settings=replace(SC6_SETTINGS, runner=bad)) == \
        rules.operator(SC6_SETTINGS)


def test_no_runner_in_settings_is_the_operator_alone():
    assert urgent_senders(FORMS, settings=SC6_SETTINGS) == rules.operator(SC6_SETTINGS)
    assert urgent_senders(FORMS, settings=Settings()) == ()


# ── the judgement, end to end ────────────────────────────────────────────────

def test_without_config_the_runners_urgent_counts_as_normal(tmp_path):
    """No runner configured: an urgent From `alerts@<host>` meets neither
    Priority line and rides, as anyone off the list does."""
    layers = rules.load_layers("watcher", None, {}, home=tmp_path / "home",
                               locked=tmp_path / rules.LOCKED_FILE)
    got = judge(urgent(f"{RUNNER}@{HOST}"), layers, ME, NOW, settings=layers.settings)
    assert (got.action, got.rule, got.at_once) == ("ride", "ride *", False)


def test_with_config_the_runners_urgent_wakes_at_once(alert_runner, tmp_path):
    layers = rules.load_layers("watcher", None, {}, home=tmp_path / "home",
                               locked=tmp_path / rules.LOCKED_FILE)
    got = judge(urgent(f"{RUNNER}@{HOST}"), layers, ME, NOW, settings=layers.settings)
    assert (got.action, got.rule, got.at_once) == (
        "wake", "carry priority:urgent from:$urgent", True)


def test_a_runners_urgent_from_another_host_still_rides(alert_runner):
    got = judge(urgent(f"{RUNNER}@elsewhere.example"), [built_in()], ME, NOW,
                settings=RUNNER_SETTINGS)
    assert (got.action, got.at_once) == ("ride", False)


# ── the live reader (design A6, RULED N5) ────────────────────────────────────

def test_a_running_listener_picks_up_a_runner_added_after_it_started(tmp_path):
    """`LiveLayers` watches the mail tool's template, host file and drop-ins
    (`_operator.files("mail")`), so a runner the owner adds applies from
    the next arrival, with no re-arm."""
    live = rules.LiveLayers("watcher", None, {}, home=tmp_path / "home",
                            locked=tmp_path / rules.LOCKED_FILE)
    assert live.current().settings.runner is None
    assert live.loads == 1 and live.current().settings.runner is None
    assert live.loads == 1, "nothing changed, nothing re-read"
    configure(RUNNER)
    assert live.current().settings.runner == RUNNER
    assert live.loads == 2


def test_a_running_listener_picks_up_a_runner_added_by_drop_in(tmp_path):
    live = rules.LiveLayers("watcher", None, {}, home=tmp_path / "home",
                            locked=tmp_path / rules.LOCKED_FILE)
    assert live.current().settings.runner is None
    drop_in = mail_dir(_operator.settings_home()) / "mail.d" / "10-runner.json"
    drop_in.parent.mkdir(parents=True, exist_ok=True)
    drop_in.write_text(json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}))
    assert live.current().settings.runner == RUNNER


def test_the_watched_files_include_the_mail_tools(tmp_path):
    live = rules.LiveLayers("watcher", None, {}, home=tmp_path / "home",
                            locked=tmp_path / rules.LOCKED_FILE)
    tool = mail_dir(_operator.settings_home())
    watched = live.files()
    for path in (tool / "mail.template.json", tool / "mail.json", tool / "mail.d"):
        assert path in watched
    assert set(_operator.files()) <= set(watched)          # the operator's still


def test_operator_files_for_the_mail_tool_names_its_drop_ins():
    drop_in = mail_dir(_operator.settings_home()) / "mail.d" / "20-x.json"
    drop_in.parent.mkdir(parents=True, exist_ok=True)
    drop_in.write_text("{}")
    assert drop_in in _operator.files("mail")
    assert drop_in not in _operator.files()


# ── the schema documents the key ─────────────────────────────────────────────

def test_the_schema_carries_the_key_as_a_local_part():
    prop = json.loads(_settings.schema())["properties"][rules.ALERT_RUNNER_KEY]
    assert prop["type"] == "string" and prop["minLength"] == 1
    pattern = re.compile(prop["pattern"])
    for good in ("alerts", "alert-runner", "ops.alerts", "alerts+box"):
        assert pattern.search(good) and rules.runner_name(good) == good
    for bad in ("a@b", "a b", "$x", "a*", "a?", "[a]", "a!", " a"):
        assert not pattern.search(bad) and rules.runner_name(bad) is None
