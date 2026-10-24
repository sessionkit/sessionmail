"""The mail tool's settings-file keys, and the ONE reader every key is read by.

openspec/changes/infra-settings, design D2 (the keys), D3 (the carrier's
names) and D4 (the reader): a variable that is set and not empty wins; else
the value merged from the TEMPLATE and HOST layers (`_operator.host_values`,
under the settings home); else the key's default. The seat and session
layers are never read for these keys, so a stale session copy of a
variable can never outlive a change to the host file.

The settings home is the suite's sandbox (`_operator.settings_home`, the
conftest seam), never the developer's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write_mail_settings

from sessionmail import _drop, _operator, _settings

#: Each key: its dotted name, its variable, a value for the file, a value
#: for the variable, and what the reader gives with neither (None: the
#: consumer works its default out at use, e.g. `hostname -s`).
KEYS = [
    ("root", "SESSIONMAIL_ROOT", "/srv/mail-file", "/srv/mail-var/agents", "~/.mail"),
    ("host", "SESSIONMAIL_HOST", "host-file", "host-var", None),
    ("sendmail", "SESSIONMAIL_SENDMAIL", "/opt/file/sendmail", "/opt/var/sendmail", "drop"),
    ("magicdns_suffix", "MAGICDNS_SUFFIX", "file.example", "var.example", None),
    ("notmuch.command", "SESSIONMAIL_NOTMUCH", "/opt/file/notmuch", "/opt/var/notmuch", "notmuch"),
    ("notmuch.timeout", "SESSIONMAIL_NOTMUCH_TIMEOUT", 7, "3", 10),
    ("notmuch.profile", "SESSIONMAIL_NOTMUCH_PROFILE", "file-profile", "var-profile", "sessionkit"),
    ("postfix.generator", "SESSIONMAIL_VMAILBOX_GEN", "/opt/file/gen", "/opt/var/gen", None),
    ("postfix.relay_domain", "SESSIONMAIL_RELAY_DOMAIN", "relay.file", "relay.var", "relay.home"),
    ("postfix.origin", "SESSIONMAIL_ORIGIN", "origin.file", "origin.var", None),
    # the host-to-host pull: no required key has a default
    ("pull.imap_host", "SESSIONMAIL_PULL_IMAP_HOST", "imap.file.example", "imap.var.example",
     None),
    ("pull.imap_port", "SESSIONMAIL_PULL_IMAP_PORT", 1993, "2993", 993),
    ("pull.login", "SESSIONMAIL_PULL_LOGIN", "{box}*file", "{box}*var", None),
    ("pull.secret_command", "SESSIONMAIL_PULL_SECRET_COMMAND", "cat /opt/file/secret",
     "cat /opt/var/secret", None),
    ("pull.relay_domain", "SESSIONMAIL_PULL_RELAY_DOMAIN", "pull.file", "pull.var", None),
    ("pull.listener", "SESSIONMAIL_PULL_LISTENER", "127.0.0.1:2525", "127.0.0.1:2526", None),
    ("pull.vmailbox", "SESSIONMAIL_PULL_VMAILBOX", "hash:/opt/file/vmailbox",
     "hash:/opt/var/vmailbox", None),
    ("pull.boxes_file", "SESSIONMAIL_PULL_BOXES_FILE", "/opt/file/pull.boxes",
     "/opt/var/pull.boxes", "~/.sessionkit/mail/settings/pull.boxes"),
    ("pull.state_dir", "SESSIONMAIL_PULL_STATE_DIR", "/opt/file/puller", "/opt/var/puller",
     "~/.sessionmail/puller"),
    ("pull.local_domain", "SESSIONMAIL_PULL_LOCAL_DOMAIN", "local-file", "local-var", None),
    ("pull.own_ips", "SESSIONMAIL_PULL_OWN_IPS", ["192.0.2.1"], "192.0.2.2", None),
    ("pull.unsorted_box", "SESSIONMAIL_PULL_UNSORTED_BOX", "unsorted-file", "unsorted-var", None),
    ("pull.audit_box", "SESSIONMAIL_PULL_AUDIT_BOX", "audit-file", "audit-var", None),
    ("pull.audit_to", "SESSIONMAIL_PULL_AUDIT_TO", ["alpha@example.org"], "beta@example.org",
     None),
    ("pull.audit_sendmail", "SESSIONMAIL_PULL_AUDIT_SENDMAIL", "/opt/file/sendmail",
     "/opt/var/sendmail", "/usr/sbin/sendmail"),
    ("pull.label", "SESSIONMAIL_PULL_LABEL", "org.example.file", "org.example.var",
     "dev.sessionkit.mail.puller"),
    ("pull.backfill_seconds", "SESSIONMAIL_PULL_BACKFILL_SECONDS", 3600, "60", 86400),
    ("pull.reissue_seconds", "SESSIONMAIL_PULL_REISSUE_SECONDS", 120, "30", 300),
    ("pull.login_gate", "SESSIONMAIL_PULL_LOGIN_GATE", 2, "8", 4),
]

#: What the reader gives for each key's VARIABLE value: the text, stripped,
#: except `root`, whose variable names the AGENTS root (the mail root's
#: `agents/`), so the key's value is its parent.
VAR_READS_AS = {"root": "/srv/mail-var"}

ALL_VARS = [var for _name, var, *_ in KEYS]


def nested(name: str, value) -> dict:
    """`{"a": {"b": VALUE}}` for NAME `a.b`."""
    out: dict = value
    for part in reversed(name.split(".")):
        out = {part: out}
    return out


def host_file(data: dict | None) -> Path:
    """The host layer, `~/.sessionkit/mail/settings/mail.json`, under the
    sandbox's settings home (None: no file)."""
    text = None if data is None else json.dumps({"version": 1, "hooks": False, **data}) + "\n"
    return write_mail_settings(_operator.settings_home(), text)


def template_file(data: dict | None) -> Path:
    """The installed template beside the host file (None: none)."""
    p = _operator.settings_home() / _settings.DIR / _settings.INSTALLED_TEMPLATE
    if data is None:
        if p.exists():
            p.unlink()
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) + "\n")
    return p


@pytest.fixture
def no_vars(monkeypatch):
    """None of the keys' variables, whatever the suite's fixture set."""
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    # Nothing is returned: a copy of the environment would be printed whole
    # in a failing test's traceback, and the runner's environment can hold
    # credentials. A test that needs an env builds one from NAMED keys.


# ── the names ───────────────────────────────────────────────────────────────

def test_the_names_are_one_definition():
    assert _settings.CARRIER_KEY == "sendmail"
    assert _settings.CARRIER_VAR == "SESSIONMAIL_SENDMAIL"
    assert _settings.CARRIER_DROP == "drop"
    assert _drop.SETTING is _settings.CARRIER_DROP
    assert _settings.NOTMUCH_PROFILE_KEY == ("notmuch", "profile")
    assert set(_settings.KEYS) == {name for name, *_ in KEYS}
    for name, var, *_ in KEYS:
        assert _settings.KEYS[name].var == var


def test_the_schema_names_every_key_with_its_variable():
    """Every key is a property, each naming its variable in `x-env`, and
    the two maps close their members as the top level does (D13 (a))."""
    schema = json.loads(_settings.schema())
    assert schema["additionalProperties"] is False
    for name, var, *_ in KEYS:
        node = schema
        for part in name.split("."):
            node = node["properties"][part]
        assert node["x-env"] == var, name
    for block in ("notmuch", "postfix", "pull"):
        assert schema["properties"][block]["additionalProperties"] is False


def test_the_template_carries_the_drop():
    """The default is set in the file: the template names the drop (design D3)."""
    assert json.loads(_settings.template()) == {"version": 1, "hooks": False, "sendmail": "drop"}


@pytest.mark.parametrize("value", ["drop", "/usr/sbin/sendmail", "/"])
def test_the_schema_accepts_a_carrier(value):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"sendmail": value}, json.loads(_settings.schema()))


@pytest.mark.parametrize("value", ["sendmail", "./sendmail", "bin/sendmail", "~/bin/sendmail",
                                   "Drop", "DROP", "", 1, True, None])
def test_the_schema_refuses_what_is_not_a_carrier(value):
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"sendmail": value}, json.loads(_settings.schema()))


def test_the_schema_accepts_the_design_example():
    """design D2's example host file passes the shipped schema."""
    jsonschema = pytest.importorskip("jsonschema")
    example = {
        "version": 1, "hooks": True, "root": "~/.mail", "host": "host-a",
        "sendmail": "drop", "magicdns_suffix": "example.ts.net",
        "notmuch": {"command": "notmuch", "timeout": 10, "profile": "sessionkit"},
        "postfix": {"generator": "~/.mail/.postfix/vmailbox-gen",
                    "relay_domain": "relay.home", "origin": "host-a.example.ts.net"},
    }
    jsonschema.validate(example, json.loads(_settings.schema()))


def test_the_schema_accepts_a_pull_block():
    """A full `pull` block, as docs/settings.md shows one, passes the shipped
    schema; a typo in it, a login with no {box}, or a listener with no port
    does not."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_settings.schema())
    pull = {"imap_host": "imap.example.org", "imap_port": 993, "login": "{box}*master",
            "secret_command": "cat ~/.config/pull.secret", "relay_domain": "relay.example",
            "listener": "127.0.0.1:2525", "vmailbox": "hash:/etc/postfix/vmailbox",
            "boxes_file": "~/pull.boxes", "state_dir": "~/puller", "local_domain": "host-a",
            "own_ips": ["192.0.2.1", "2001:db8::1"], "unsorted_box": "_unsorted",
            "audit_box": "_audit", "audit_to": "alpha@example.org,beta@example.org",
            "audit_sendmail": "/usr/sbin/sendmail", "label": "org.example.puller",
            "backfill_seconds": 0, "reissue_seconds": 300, "login_gate": 4}
    jsonschema.validate({"pull": pull}, schema)
    for bad in ({"imap_hst": "x"}, {"login": "master"}, {"listener": "127.0.0.1"},
                {"label": "has space"}, {"login_gate": 0}, {"audit_sendmail": "sendmail"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"pull": bad}, schema)


@pytest.mark.parametrize("bad", [{"notmuch": {"profle": "x"}}, {"postfix": {"orgin": "x"}},
                                 {"notmuch": {"timeout": 0}}, {"root": ""}])
def test_the_schema_reports_a_typo_or_bad_value_in_a_map(bad):
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, json.loads(_settings.schema()))


# ── carrier_why: the vocabulary, judged exactly as given ───────────────────

@pytest.mark.parametrize("value", ["drop", "/usr/sbin/sendmail", "/opt/homebrew/sbin/sendmail"])
def test_carrier_why_accepts_a_carrier(value):
    assert _settings.carrier_why(value) == ""


@pytest.mark.parametrize("value", ["sendmail", "./sendmail", "bin/sendmail", "~/bin/sendmail",
                                   "Drop", "DROP", " drop", "", True, 1, None, {"a": 1}])
def test_carrier_why_names_what_is_not_a_carrier(value):
    why = _settings.carrier_why(value)
    assert why == f"{json.dumps(value)} is not a carrier: {_settings.CARRIER_RULE}"
    assert _settings.CARRIER_RULE == ('the carrier is "drop" or an absolute path to an MTA '
                                      "(for example /usr/sbin/sendmail)")


# ── 2.4: file, variable, default, for every key (SC3) ──────────────────────

@pytest.mark.parametrize("name,var,file_value,var_value,default", KEYS,
                         ids=[k[0] for k in KEYS])
def test_each_key_file_variable_default(no_vars, monkeypatch, name, var, file_value,
                                        var_value, default):
    template_file(None)
    # the default: no variable, no template, no host file
    host_file(None)
    assert tuple(_settings.setting(name)) == (default, "default")
    # the file's value
    host_file(nested(name, file_value))
    assert tuple(_settings.setting(name)) == (file_value, "host")
    # the variable beats the file
    monkeypatch.setenv(var, var_value)
    assert tuple(_settings.setting(name)) == (VAR_READS_AS.get(name, var_value), "variable")
    # ...and so does an env handed in, without touching os.environ
    monkeypatch.delenv(var)
    assert tuple(_settings.setting(name, {var: var_value})) == \
        (VAR_READS_AS.get(name, var_value), "variable")
    assert tuple(_settings.setting(name, {})) == (file_value, "host")


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_empty_variable_is_not_set(no_vars, monkeypatch, blank):
    host_file({"sendmail": "/opt/file/sendmail"})
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", blank)
    assert tuple(_settings.setting("sendmail")) == ("/opt/file/sendmail", "host")


def test_a_variable_is_stripped(no_vars):
    assert tuple(_settings.setting("sendmail", {"SESSIONMAIL_SENDMAIL": "  drop\n"})) == \
        ("drop", "variable")


def test_the_template_is_a_layer_and_the_host_beats_it(no_vars):
    host_file(None)
    template_file({"version": 1, "hooks": False, "sendmail": "drop"})
    assert tuple(_settings.setting("sendmail")) == ("drop", "template")
    host_file({"sendmail": "/usr/sbin/sendmail"})
    assert tuple(_settings.setting("sendmail")) == ("/usr/sbin/sendmail", "host")


def test_a_map_merges_key_by_key(no_vars):
    """A host file that sets one `notmuch` member keeps the template's others."""
    template_file({"notmuch": {"command": "/opt/t/notmuch", "timeout": 5}})
    host_file({"notmuch": {"timeout": 9}})
    assert tuple(_settings.setting("notmuch.command")) == ("/opt/t/notmuch", "template")
    assert tuple(_settings.setting("notmuch.timeout")) == (9, "host")
    assert tuple(_settings.setting("notmuch.profile")) == ("sessionkit", "default")


def test_a_map_written_as_a_scalar_is_the_default(no_vars):
    host_file({"notmuch": "sessionkit"})
    assert tuple(_settings.setting("notmuch.profile")) == ("sessionkit", "default")


def test_a_file_value_is_given_as_written(no_vars):
    """The reader never judges a value: `carrier_why` and each consumer do."""
    host_file({"sendmail": "sendmail"})
    assert tuple(_settings.setting("sendmail")) == ("sendmail", "host")
    host_file({"sendmail": True})
    assert tuple(_settings.setting("sendmail")) == (True, "host")


def test_an_unreadable_file_says_why_and_a_variable_never_reads_it(no_vars):
    p = write_mail_settings(_operator.settings_home(), "{not json\n")
    got = _settings.setting("sendmail")
    assert (got.value, got.source) == (None, _settings.UNREADABLE)
    assert got.why and "not JSON" in got.why
    assert str(_operator.settings_home()) not in got.why     # shown under ~
    # a set variable wins without the file being read at all (design D4)
    got = _settings.setting("sendmail", {"SESSIONMAIL_SENDMAIL": "drop"})
    assert (got.value, got.source, got.why) == ("drop", "variable", "")
    assert p.read_text() == "{not json\n"


def test_an_unknown_key_is_refused_by_name():
    with pytest.raises(KeyError, match="sendmial"):
        _settings.setting("sendmial")


def test_setting_never_writes(no_vars):
    home = _operator.settings_home()
    before = sorted(p for p in home.rglob("*"))
    _settings.setting("sendmail")
    assert sorted(p for p in home.rglob("*")) == before


# ── 3.2: a job with an empty environment reads the file ────────────────────

LAUNCHD_JOB = r"""
import json, sys
from pathlib import Path
from sessionmail import _operator, _settings
_operator.settings_home = lambda: Path(sys.argv[1])
print(json.dumps({name: list(_settings.setting(name)) for name in _settings.KEYS}))
"""


def test_launchd_env_reads_file(no_vars, tmp_path):
    """A child with `env -i HOME=… PATH=/usr/bin:/bin` - none of the
    variables, as a launchd job runs - gets the file's values. Nothing is
    copied into its environment: the file is the only source. The child
    points the settings home at the sandbox (in a real job it is the
    account's home, from the password database), so it never reads the
    developer's file."""
    values = {name: file_value for name, _var, file_value, *_ in KEYS}
    data: dict = {}
    for name, value in values.items():
        head, _, tail = name.partition(".")
        if tail:
            data.setdefault(head, {})[tail] = value
        else:
            data[head] = value
    host_file(data)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-c", LAUNCHD_JOB, str(_operator.settings_home())],
                          env=env, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got == {name: [value, "host"] for name, value in values.items()}


# ── 3.3: a seat or session layer cannot move a host key ────────────────────

SID = "6f1d2c3b-4a59-4e1f-8c7d-2b3a4c5d6e7f"
SEAT = "sessionmail+example.worker"


def other_layers(value) -> list[Path]:
    """Seat and session layer files (where sessiontools' cascade reads them,
    under `$HOME`) that set `sendmail` to VALUE: the seat's own file, the
    session's file, and the session's birth copy of the variables
    (`mail.d/50-env.json`)."""
    from sessiontools import settings as st
    session = st.session_dir(SID) / "settings"
    seat = st.seat_dir(SEAT) / "settings"
    paths = [seat / "mail.json", session / "mail.json", session / "mail.d" / "50-env.json"]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"sendmail": value}) + "\n")
    return paths


def test_seat_layer_not_read(no_vars):
    host_file({"sendmail": "/usr/sbin/sendmail"})
    other_layers("drop")
    assert tuple(_settings.setting("sendmail")) == ("/usr/sbin/sendmail", "host")


def test_seat_layer_not_read_stale_session(no_vars):
    """The stale-session case: a session born when the variable said X
    holds X in its birth copy. The host file then changes to Y, and the
    session runs with no variable. The reader gives Y; a full cascade, which
    reads the session layer last, would have given X."""
    from sessiontools import settings as st
    host_file({"sendmail": "/opt/x/sendmail"})
    session_files = other_layers("/opt/x/sendmail")         # born under X
    host_file({"sendmail": "/opt/y/sendmail"})              # the host moves to Y
    assert tuple(_settings.setting("sendmail")) == ("/opt/y/sendmail", "host")
    # the guard: the same files, merged as the full cascade merges them,
    # put the stale X on top, so this test would catch a reader that read them
    cascade = _operator.host_sources(_settings.TOOL) + [
        st.Source("session", p, st.read_json_object(p)) for p in session_files]
    assert st.merge(cascade)[0]["sendmail"] == "/opt/x/sendmail"


def test_a_map_where_a_value_belongs_is_given_as_written(no_vars):
    """The reader never judges: an object where a string belongs comes back
    as written, from its layer, and its consumer refuses or ignores it."""
    host_file({"sendmail": {"path": "/usr/sbin/sendmail"}})
    assert tuple(_settings.setting("sendmail")) == ({"path": "/usr/sbin/sendmail"}, "host")
