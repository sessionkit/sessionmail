"""Each key's CONSUMERS read the settings file, whatever their environment.

openspec/changes/infra-settings, design D4: a reader takes its variables, in
today's order, and only where it would have fallen back to its HARD DEFAULT
does it take the file's value (the template and host layers, `_settings.
setting`). A set variable of the key's own still beats the file:
where a consumer never read that variable, the file is then not read either,
and the consumer keeps today's default. So a launchd job, which has none of
the variables, gets the file's values, and nothing is copied into a plist.

The settings home is the suite's sandbox (`_operator.settings_home`), never
the developer's.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
from conftest import write_mail_settings

from sessionmail import (_drop, _generator, _magicdns, _maildir, _notmuch, _operator,
                         _sender, _settings, _sig, _voice)

#: Every variable the six keys' consumers read, the older spellings included.
CONSUMER_VARS = ("SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT", "MAIL_ROOT",
                 "SESSIONMAIL_HOST", "SESSION_MAIL_HOST", "MAGICDNS_SUFFIX",
                 "SESSIONMAIL_NOTMUCH", "SESSIONMAIL_NOTMUCH_TIMEOUT",
                 "SESSIONMAIL_VMAILBOX_GEN", "SESSIONMAIL_RELAY_DOMAIN", "SESSIONMAIL_ORIGIN")


@pytest.fixture
def bare(monkeypatch):
    """None of the consumers' variables (the suite's sandbox sets some), no
    template, no host file: what a launchd job with a fresh home sees."""
    for var in CONSUMER_VARS:
        monkeypatch.delenv(var, raising=False)
    home = _operator.settings_home()
    (home / _settings.DIR / _settings.INSTALLED_TEMPLATE).unlink(missing_ok=True)
    write_mail_settings(home, None)
    _magicdns.forget()
    # NAMED keys only, never a copy of the environment: a failing test prints
    # its env whole in the traceback, and the runner's environment can hold
    # credentials. HOME and PATH are what the consumers read besides the keys.
    yield {k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ}
    _magicdns.forget()


def host_file(**data) -> Path:
    """The host layer holding DATA (dotted names become maps)."""
    out: dict = {"version": 1, "hooks": False}
    for name, value in data.items():
        head, _, tail = name.replace("__", ".").partition(".")
        if tail:
            out.setdefault(head, {})[tail] = value
        else:
            out[head] = value
    return write_mail_settings(_operator.settings_home(), json.dumps(out) + "\n")


def home() -> Path:
    return _operator.settings_home()


# ── root ────────────────────────────────────────────────────────────────────

def test_root_file_value_reaches_every_consumer(bare, tmp_path):
    host_file(root=str(tmp_path / "m"))
    assert _maildir.root(bare) == tmp_path / "m" / "agents"
    assert _sender.root_of(bare) == tmp_path / "m" / "agents"
    assert _drop.mail_root(bare) == tmp_path / "m"


def test_root_tilde_is_the_account_home(bare):
    host_file(root="~/mailroot")
    assert _maildir.root(bare) == home() / "mailroot" / "agents"
    assert _sender.root_of(bare) == home() / "mailroot" / "agents"
    assert _drop.mail_root(bare) == home() / "mailroot"


def test_root_no_file_is_todays_default(bare):
    assert _maildir.root(bare) == Path(_maildir.DEFAULT_ROOT).expanduser()
    assert _drop.mail_root(bare) == Path(bare.get("HOME") or os.path.expanduser("~")) / ".mail"


def test_root_each_consumers_variables_keep_their_order(bare, tmp_path):
    host_file(root=str(tmp_path / "file"))
    old = {**bare, "SESSION_MAIL_ROOT": str(tmp_path / "old" / "agents")}
    new = {**bare, "SESSIONMAIL_ROOT": str(tmp_path / "new" / "agents")}
    both = {**old, **new}
    mail = {**bare, "MAIL_ROOT": str(tmp_path / "mr")}
    # _maildir reads SESSION_MAIL_ROOT, and never SESSIONMAIL_ROOT
    assert _maildir.root(old) == tmp_path / "old" / "agents"
    # the sender: SESSIONMAIL_ROOT, then SESSION_MAIL_ROOT
    assert _sender.root_of(both) == tmp_path / "new" / "agents"
    assert _sender.root_of(old) == tmp_path / "old" / "agents"
    # the drop: either agents variable (its parent), then MAIL_ROOT
    assert _drop.mail_root(new) == tmp_path / "new"
    assert _drop.mail_root(old) == tmp_path / "old"
    assert _drop.mail_root(mail) == tmp_path / "mr"


def test_root_the_keys_own_variable_beats_the_file_where_it_was_never_read(bare, tmp_path):
    """`_maildir` never read SESSIONMAIL_ROOT. With it set, the variable
    still beats the file, so `_maildir` keeps today's default; it
    does not start reading a variable it never read (D4)."""
    host_file(root=str(tmp_path / "file"))
    env = {**bare, "SESSIONMAIL_ROOT": str(tmp_path / "new" / "agents")}
    assert _maildir.root(env) == Path(_maildir.DEFAULT_ROOT).expanduser()


@pytest.mark.parametrize("bad", ["", "   ", 7, True, None, ["x"]])
def test_root_a_value_that_is_not_a_path_is_the_default(bare, bad):
    host_file(root=bad)
    assert _maildir.root(bare) == Path(_maildir.DEFAULT_ROOT).expanduser()


# ── host ────────────────────────────────────────────────────────────────────

def test_host_file_value_reaches_every_consumer(bare, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "real-host.lan")
    host_file(host="Host-File")
    assert _sender.host_of(bare) == "Host-File"          # as today: case kept
    assert _drop.short_host(bare) == "host-file"          # as today: lowered
    host_file(magicdns_suffix="tail.example", host="host-file")
    assert _voice.host_forms(bare) == ("host-file", "host-file.tail.example")


def test_host_variables_keep_their_order_and_no_file_is_the_hostname(bare, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "real-host.lan")
    assert _sender.host_of(bare) == "real-host"
    assert _drop.short_host(bare) == "real-host"
    host_file(host="host-file")
    for var in ("SESSIONMAIL_HOST", "SESSION_MAIL_HOST"):
        env = {**bare, var: "host-var"}
        assert _sender.host_of(env) == "host-var"
        assert _drop.short_host(env) == "host-var"


# ── magicdns_suffix ─────────────────────────────────────────────────────────

def test_suffix_file_value_answers_without_tailscale(bare, monkeypatch):
    def no_helper(env):
        raise AssertionError("the helper must not run: the file answered")
    monkeypatch.setattr(_magicdns, "_ask_helper", no_helper)
    host_file(magicdns_suffix=".Tail.Example.")
    assert _magicdns.suffix(bare) == "tail.example"        # the helper's normalisation
    assert _magicdns.lookup(bare) == ("tail.example", "")


def test_suffix_variable_beats_the_file(bare):
    host_file(magicdns_suffix="file.example")
    assert _magicdns.suffix({**bare, "MAGICDNS_SUFFIX": "var.example"}) == "var.example"


def test_suffix_a_malformed_file_value_is_refused_naming_the_key(bare):
    host_file(magicdns_suffix="not a suffix!")
    with pytest.raises(_magicdns.SuffixUnknown, match="magicdns_suffix"):
        _magicdns.suffix(bare)


# ── notmuch.command, notmuch.timeout ────────────────────────────────────────

def test_notmuch_file_values(bare):
    assert _notmuch.binary(bare) == "notmuch"
    assert _notmuch.timeout(bare) == 10.0
    host_file(notmuch__command="/opt/file/notmuch", notmuch__timeout=3)
    assert _notmuch.binary(bare) == "/opt/file/notmuch"
    assert _notmuch.timeout(bare) == 3.0
    host_file(notmuch__command="~/bin/notmuch")
    assert _notmuch.binary(bare) == str(home() / "bin" / "notmuch")


def test_notmuch_variables_beat_the_file(bare):
    host_file(notmuch__command="/opt/file/notmuch", notmuch__timeout=3)
    env = {**bare, "SESSIONMAIL_NOTMUCH": "/opt/var/notmuch", "SESSIONMAIL_NOTMUCH_TIMEOUT": "5"}
    assert _notmuch.binary(env) == "/opt/var/notmuch"
    assert _notmuch.timeout(env) == 5.0


@pytest.mark.parametrize("bad", [0, -1, "x", True, None])
def test_notmuch_a_bad_timeout_in_the_file_is_the_default(bare, bad):
    host_file(notmuch__timeout=bad)
    assert _notmuch.timeout(bare) == 10.0


# ── postfix.generator ───────────────────────────────────────────────────────

def test_generator_file_value_is_the_only_candidate(bare, tmp_path):
    gen = tmp_path / "file-gen"
    gen.write_text("#!/bin/sh\n")
    host_file(postfix__generator=str(gen))
    for packaged in (True, False):
        found = _generator.find(bare, tmp_path / "root", packaged=packaged)
        assert found == _generator.Found(str(gen), _generator.SOURCE_FILE)
    assert _generator.expected(bare, tmp_path / "root") == str(gen)
    # a file value that is no file is found by nothing: it pins, as the variable does
    host_file(postfix__generator=str(tmp_path / "missing"))
    assert _generator.find(bare, tmp_path / "root", packaged=True) is None


def test_generator_variable_beats_the_file(bare, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("x")
    b.write_text("x")
    host_file(postfix__generator=str(a))
    found = _generator.find({**bare, "SESSIONMAIL_VMAILBOX_GEN": str(b)}, tmp_path, packaged=True)
    assert found == _generator.Found(str(b), _generator.SOURCE_ENV)


def test_generator_no_file_is_todays_lookup(bare, tmp_path):
    found = _generator.find({**bare, "PATH": ""}, tmp_path / "root", packaged=True)
    assert found == _generator.Found(str(_generator.PACKAGED), _generator.SOURCE_PACKAGED)


# ── the generator's child gets the file's suffix in its variable ───────────

def test_spawn_env_carries_the_files_suffix(bare):
    assert _generator.spawn_env(bare) is None                    # nothing to add
    host_file(magicdns_suffix="tail.example")
    child = _generator.spawn_env(bare)
    assert child == {**bare, "MAGICDNS_SUFFIX": "tail.example"}
    # a set variable is the child's already: nothing is added or changed
    assert _generator.spawn_env({**bare, "MAGICDNS_SUFFIX": "var.example"}) is None


def test_the_drop_runs_the_generator_with_the_files_suffix(bare, tmp_path, monkeypatch):
    seen: list[dict] = []

    def fake_run(cmd, **kw):
        seen.append(dict(kw.get("env") or {}))
        raise OSError("stop here")
    host_file(magicdns_suffix="tail.example")
    monkeypatch.setattr(_drop.subprocess, "run", fake_run)
    with pytest.raises(Exception):
        _drop._run_gen(str(_generator.PACKAGED), ["--capabilities"], bare)
    assert seen and seen[0].get("MAGICDNS_SUFFIX") == "tail.example"


def test_the_map_writer_runs_the_generator_with_the_files_suffix(bare, tmp_path, monkeypatch):
    from sessionmail import _seat
    gen = tmp_path / "gen"
    gen.write_text("#!/bin/sh\nexit 0\n")
    gen.chmod(0o755)
    host_file(magicdns_suffix="tail.example", postfix__generator=str(gen))
    import subprocess
    seen: list = []
    real = subprocess.Popen

    def spy(cmd, **kw):
        seen.append(kw.get("env"))
        return real(cmd, **kw)
    monkeypatch.setattr(subprocess, "Popen", spy)   # `_seat` imports it in the function
    root = tmp_path / "mail" / "agents"
    root.mkdir(parents=True)
    # the map writer runs only when the carrier is an MTA (the drop, the
    # default, reads the maps per send); this one is named and never run
    ok, line = _seat._run_generator(root, {**bare, "SESSIONMAIL_SENDMAIL": "/usr/sbin/sendmail"}, ())
    assert ok, line
    assert seen and seen[0] is not None and seen[0].get("MAGICDNS_SUFFIX") == "tail.example"


# ── postfix.relay_domain ────────────────────────────────────────────────────

def test_relay_domain(bare):
    assert _sender.relay_domain(bare) == "relay.home"
    host_file(postfix__relay_domain="Relay.File")
    assert _sender.relay_domain(bare) == "relay.file"
    assert _sender.relay_domain({**bare, "SESSIONMAIL_RELAY_DOMAIN": "relay.var"}) == "relay.var"


# ── postfix.origin: the signer and the drop read it alike ──────────────────

def test_origin_file_value_reaches_the_signer_and_the_drop(bare, monkeypatch):
    monkeypatch.setattr(_sig.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(_sig, "POSTCONF_FALLBACK", ())
    assert _sig.origin_of(bare) == ""
    host_file(postfix__origin="origin.file")
    assert _sig.origin_of(bare) == "origin.file"
    assert _drop.origin(bare, "h") == "origin.file"
    env = {**bare, "SESSIONMAIL_ORIGIN": "origin.var"}
    assert _sig.origin_of(env) == "origin.var"
    assert _drop.origin(env, "h") == "origin.var"
