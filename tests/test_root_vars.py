"""The mail root: ONE resolver, ONE variable, one folder rule (the move to
`~/.sessionkit/mail`).

- `_maildir.mail_root_why`: `$SESSIONMAIL_ROOT` (set, non-empty), else the
  settings file's `root`, else `~/.sessionkit/mail`. `_maildir.root`,
  `_drop.mail_root`, `_sender.root_of` and `_setup.roots` all go through it,
  so no two sides can pick two trees (SM-910's bug, for good).
- The boxes folder under it (`_maildir.boxes_name`): `stores`, except
  `agents` while only that one is a directory. An old tree keeps its name;
  a fresh host gets `stores`. The one conditional of the move.
- The retired spellings (`SESSION_MAIL_ROOT`, which named the boxes folder,
  and `MAIL_ROOT`, the generator's) are read by NOTHING; a set one is named
  by `message path --why` and `message setup`.
- The rails that cannot import the package carry the order inline and ASK
  `message path --root` for the file's value: `mhs/mail-box.lib.sh`,
  `mhs/session-mail-wake`, `notmuch/store-index`, `postfix/vmailbox-gen`.
  The agreement tests below pin each to the package's answer.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _drop, _maildir, _seat, _sender, _settings, _setup
from sessionmail.message import cli
from test_mail_drop import FAKE_GEN, HOST

SRC = Path(_maildir.__file__).resolve().parent
LIB = SRC / "mhs" / "mail-box.lib.sh"
WAKE = SRC / "mhs" / "session-mail-wake"
STORE_INDEX = SRC / "notmuch" / "store-index"
GEN = SRC / "postfix" / "vmailbox-gen"
VAR = "SESSIONMAIL_ROOT"
BOX = "present"
NO_MESSAGE = "/usr/bin:/bin"          # a PATH with no `message` on it: the rails take the default


def mkbox(d: Path) -> Path:
    for s in ("new", "cur", "tmp"):
        (d / s).mkdir(parents=True, exist_ok=True)
    return d


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ── the one name, the one default ────────────────────────────────────────────

def test_the_one_variable_is_the_schemas_and_names_the_root():
    schema = json.loads((SRC / "mail.schema.json").read_text())
    assert schema["properties"]["root"]["x-env"] == VAR == _maildir.ROOT_VAR
    assert _settings.KEYS["root"].default == _maildir.DEFAULT_MAIL_ROOT == "~/.sessionkit/mail"
    assert not hasattr(_settings.KEYS["root"], "parent"), "the variable names the root itself now"
    assert _maildir.RETIRED_ROOT_VARS == ("SESSION_MAIL_ROOT", "MAIL_ROOT")
    assert _maildir.BOXES == "stores" and _maildir.OLD_BOXES == "agents"
    assert _maildir.DEFAULT_ROOT == "~/.sessionkit/mail/stores"


@pytest.mark.parametrize("given,want", [
    ({}, None),
    ({VAR: "/new"}, "/new"),
    ({VAR: ""}, None),                                   # set but empty: not set
    ({"SESSION_MAIL_ROOT": "/old/agents"}, None),        # retired: read by nothing
    ({"MAIL_ROOT": "/old"}, None),
    ({VAR: "/new", "SESSION_MAIL_ROOT": "/old/agents", "MAIL_ROOT": "/older"}, "/new"),
])
def test_env_root_reads_the_one_variable_and_no_retired_one(given, want):
    assert _maildir.env_root(given) == (None if want is None else Path(want))


def test_env_root_expands_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _maildir.env_root({VAR: "~/m"}) == tmp_path / "m"


def test_mail_root_why_names_its_source(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    assert _maildir.mail_root_why({}) == (tmp_path / ".sessionkit" / "mail", _maildir.FROM_DEFAULT)
    monkeypatch.setattr(_settings, "file_path",
                        lambda key, env=None: str(tmp_path / "file") if key == "root" else None)
    assert _maildir.mail_root_why({}) == (tmp_path / "file", _maildir.FROM_FILE)
    assert _maildir.mail_root_why({VAR: str(tmp_path / "var")}) == (tmp_path / "var", _maildir.FROM_VAR)


@pytest.mark.parametrize("make,want", [
    ((), "stores"),                       # a fresh tree
    (("agents",), "agents"),              # an old tree keeps its name
    (("stores",), "stores"),
    (("agents", "stores"), "stores"),     # both: the new name wins
])
def test_the_boxes_folder_is_stores_except_on_an_old_tree(tmp_path, make, want):
    for d in make:
        (tmp_path / d).mkdir()
    assert _maildir.boxes_name(tmp_path) == want
    assert _maildir.boxes_dir(tmp_path) == tmp_path / want
    assert _maildir.root({VAR: str(tmp_path)}) == tmp_path / want


def test_a_file_named_agents_is_not_a_folder(tmp_path):
    (tmp_path / "agents").write_text("")
    assert _maildir.boxes_name(tmp_path) == "stores"


# ── the four Python sides are one function ──────────────────────────────────

@pytest.mark.parametrize("given", [{VAR: "/srv/new"}, {}])
def test_the_readers_the_sender_the_drop_and_setup_pick_one_tree(given, monkeypatch, tmp_path):
    monkeypatch.setattr(_settings, "file_path",
                        lambda key, env=None: str(tmp_path / "file") if key == "root" else None)
    env = {**given, "SESSION_MAIL_ROOT": "/never/agents", "MAIL_ROOT": "/never"}
    mail_root = _maildir.mail_root(env)
    assert mail_root == (Path("/srv/new") if given else tmp_path / "file")
    assert _drop.mail_root(env) == mail_root
    assert _maildir.root(env) == _sender.root_of(env) == _setup.roots(env) == mail_root / "stores"


def test_retired_variables_are_named_not_read(monkeypatch, tmp_path):
    env = {"SESSION_MAIL_ROOT": str(tmp_path / "old"), "MAIL_ROOT": "", VAR: str(tmp_path)}
    assert _maildir.retired_root_vars(env) == ["SESSION_MAIL_ROOT"]
    assert _maildir.retired_root_vars({"MAIL_ROOT": "/x"}) == ["MAIL_ROOT"]
    assert _maildir.retired_root_vars({}) == []
    # setup does not refuse (there is one root), and its mail-root step says so
    agents = _setup.roots(env)
    assert agents == tmp_path / "stores"
    agents.mkdir()
    state, text = _setup.mail_root_step(agents, env).check()
    assert state == _setup.DONE and text == f"{agents}; ignored, retired: $SESSION_MAIL_ROOT"
    state, text = _setup.mail_root_step(agents, {VAR: str(tmp_path)}).check()
    assert text == str(agents)


# ── message path --root | --boxes ────────────────────────────────────────────

@pytest.fixture
def quiet_root(monkeypatch, tmp_path):
    """A root under tmp_path, by the variable; no retired spelling set."""
    for v in ("SESSION_MAIL_ROOT", "MAIL_ROOT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(VAR, str(tmp_path / "mail"))
    return tmp_path / "mail"


def test_path_root_and_boxes_print_the_places_exit_0(quiet_root):
    r = CliRunner().invoke(cli, ["path", "--root", "--why"])
    assert r.exit_code == 0 and r.stdout == f"{quiet_root}\n", r.output
    assert f"--root: the mail root, from ${VAR}" in r.output
    r = CliRunner().invoke(cli, ["path", "--boxes", "--why"])
    assert r.exit_code == 0 and r.stdout == f"{quiet_root / 'stores'}\n", r.output
    assert "--boxes: stores/ under" in r.output and "old tree" not in r.output
    (quiet_root / "agents").mkdir(parents=True)
    r = CliRunner().invoke(cli, ["path", "--boxes", "--why"])
    assert r.stdout == f"{quiet_root / 'agents'}\n"
    assert "an old tree, agents/ is a directory and stores/ is not" in r.output, r.output
    r = CliRunner().invoke(cli, ["box", "path", "--root"])
    assert r.exit_code == 0 and r.stdout == f"{quiet_root}\n"


def test_path_with_a_place_flag_takes_no_box(quiet_root):
    r = CliRunner().invoke(cli, ["path", BOX, "--root"])
    assert r.exit_code != 0 and "--root takes no BOX" in r.output


def test_path_why_names_a_retired_variable_that_is_set(quiet_root, monkeypatch):
    monkeypatch.setenv("MAIL_ROOT", "/retired")
    r = CliRunner().invoke(cli, ["path", "--root", "--why"])
    assert r.exit_code == 0 and r.stdout == f"{quiet_root}\n"
    assert "ignored, retired: $MAIL_ROOT" in r.output
    r = CliRunner().invoke(cli, ["path", "--root"])
    assert r.output == f"{quiet_root}\n", "without --why, the path and nothing else"


# ── message send, then path / list / read: one tree (the SM-910 measurement) ─

@pytest.fixture
def drop_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "vmailbox-gen", FAKE_GEN.format(python=sys.executable))
    for var in (VAR, "SESSION_MAIL_ROOT", "MAIL_ROOT", "CLAUDE_CODE_AGENT",
                "CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB", "SESSIONMAIL_REQUIRE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in {
        "HOME": str(tmp_path / "home"),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(bin_dir / "vmailbox-gen"),
        "FAKE_VMAILBOX": f"{BOX}@{HOST}\tstores/{BOX}/\nbeta@{HOST}\tstores/beta/\n",
        "FAKE_VALIAS": "",
        "TM_AGENT_ADDRESS": "beta",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)     # no postfix here
    body = tmp_path / "body.md"
    body.write_text("hello\n")
    return tmp_path, body


@pytest.mark.parametrize("folder", ["stores", "agents"])
def test_path_list_and_read_find_the_box_send_delivered_to(folder, drop_env, monkeypatch):
    """The variable names the ROOT; the box is under its folder, whichever
    the tree has. A retired spelling set to a losing tree changes nothing."""
    tmp_path, body = drop_env
    root = tmp_path / "root"
    boxes = root / folder
    mkbox(boxes / BOX)
    mkbox(boxes / "beta")
    losing = mkbox(tmp_path / "losing" / BOX).parent
    monkeypatch.setenv("FAKE_VMAILBOX", f"{BOX}@{HOST}\t{folder}/{BOX}/\nbeta@{HOST}\t{folder}/beta/\n")
    monkeypatch.setenv(VAR, str(root))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(losing))
    runner = CliRunner()

    sent = runner.invoke(cli, ["send", BOX, "-s", f"root {folder}", "-f", str(body)])
    assert sent.exit_code == 0, sent.output
    assert len(list((boxes / BOX / "new").iterdir())) == 1
    assert not list((losing / BOX / "new").iterdir()), "the drop wrote the retired variable's tree"

    path = runner.invoke(cli, ["path", BOX])
    assert path.exit_code == 0 and path.stdout == f"{boxes / BOX}\n", path.output
    listed = runner.invoke(cli, ["list", BOX])
    assert listed.exit_code == 0 and f"root {folder}" in listed.stdout, listed.output
    read = runner.invoke(cli, ["read", BOX, "1", "--peek"])
    assert read.exit_code == 0 and f"root {folder}" in read.stdout, read.output


# ── the rails agree with the package ─────────────────────────────────────────

def rail_env_root(env: dict[str, str]) -> str:
    r = subprocess.run(["bash", str(LIB), "--env-root"], capture_output=True, text=True,
                       env={"PATH": NO_MESSAGE, **env})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


AGREE = [
    {},
    {VAR: "/srv/new"},
    {VAR: ""},
    {VAR: "~/tilde"},
    {VAR: "/srv/new", "SESSION_MAIL_ROOT": "/srv/old/agents", "MAIL_ROOT": "/srv/older"},
    {"SESSION_MAIL_ROOT": "/srv/old/agents"},
]


@pytest.mark.parametrize("given", AGREE)
@pytest.mark.parametrize("folder", [None, "agents", "stores"])
def test_the_rails_and_the_package_give_one_boxes_root(given, folder, tmp_path, monkeypatch):
    """With no `message` on PATH the rails take the package's default under
    HOME, so the file is not in play: variable, else default, then the
    folder rule. Each rail is pinned to `_maildir.root`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    env = {"HOME": str(home), **given}
    want = _maildir.root(env)
    if folder:
        if not want.is_relative_to(tmp_path):
            pytest.skip(f"{want.parent} is not under tmp_path: no folder to make there")
        want.parent.mkdir(parents=True, exist_ok=True)
        (want.parent / folder).mkdir(exist_ok=True)
        want = _maildir.root(env)
        assert want.name == folder
    assert rail_env_root(env) == str(want)
    assert load(WAKE, "wake_rail").env_root({"PATH": NO_MESSAGE, **env}) == str(want)
    si = load(STORE_INDEX, "store_index_rail")
    assert si.default_mail_root("acme", {"PATH": NO_MESSAGE, **env}) == want / "acme"
    assert si.stores_dir({"PATH": NO_MESSAGE, **env}) == want.parent / ".notmuch" / "stores"


def test_the_rails_ask_message_path_root_when_no_variable_is_set(tmp_path, monkeypatch):
    """The settings file's `root` is JSON the rails do not read: with no
    variable they ask `message path --root` (a fake here, answering a root
    with an old tree under it) and apply the folder rule to its answer."""
    answer = tmp_path / "answered"
    (answer / "agents").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "message", f"#!/bin/sh\n[ \"$1 $2\" = 'path --root' ] || exit 1\nprintf '%s\\n' '{answer}'\n")
    env = {"HOME": str(tmp_path / "home"), "PATH": f"{bin_dir}:{NO_MESSAGE}"}
    r = subprocess.run(["bash", str(LIB), "--env-root"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == str(answer / "agents"), r.stderr
    assert load(WAKE, "wake_rail_ask").env_root(env) == str(answer / "agents")
    si = load(STORE_INDEX, "store_index_rail_ask")
    assert si.mail_root(env) == answer
    assert si.default_mail_root("acme", env) == answer / "agents" / "acme"
    # the variable still wins over the answer
    assert rail_env_root({**env, VAR: str(tmp_path / "var")}) == str(tmp_path / "var" / "stores")


@pytest.mark.parametrize("given", [{}, {VAR: "~/tilde"}, {VAR: "~"}])
def test_a_path_less_env_never_asks_the_hosts_message_or_reads_the_hosts_home(
        given, tmp_path, monkeypatch):
    """ENV is the whole world. The HOST has a `message` on its PATH that
    answers a fake live root, and a HOME of its own; an ENV naming a HOME and
    no PATH must take ENV's own default (or `~` under ENV's HOME), never the
    host's answer and never the host's HOME. The rail, the package and the
    wake script give one answer, and the host's `message` is never run."""
    live = tmp_path / "live"
    (live / "stores").mkdir(parents=True)
    asked = tmp_path / "asked"
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    write_fake(host_bin / "message",
               f"#!/bin/sh\n: > '{asked}'\nprintf '%s\\n' '{live}'\n")
    host_home = tmp_path / "host-home"
    host_home.mkdir()
    monkeypatch.setenv("PATH", f"{host_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(host_home))
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), **given}
    want = _maildir.root(env)
    assert want.is_relative_to(home), want

    r = subprocess.run([shutil.which("bash") or "/bin/bash", str(LIB), "--env-root"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == str(want), r.stderr
    assert load(WAKE, "wake_rail_path_less").env_root(env) == str(want)
    assert not asked.exists(), "the host's message was asked for a PATH-less env"
    # PATH named: ENV's own `message` is asked, as before
    assert load(WAKE, "wake_rail_path_named").env_root({**env, "PATH": f"{host_bin}:{NO_MESSAGE}"}) == (
        str(want) if given else str(live / "stores"))
    assert asked.exists() is (not given)


def test_vmailbox_gen_bare_resolves_the_root_the_same_way(tmp_path, monkeypatch):
    """Run with no --root: the variable, else `message path --root`, else the
    default; never $MAIL_ROOT. The map values carry the folder the tree has."""
    root = tmp_path / "root"
    mkbox(root / "agents" / "alpha")
    env = {"HOME": str(tmp_path / "home"), "PATH": NO_MESSAGE, "MAIL_ROOT": str(tmp_path / "retired"),
           VAR: str(root)}
    r = subprocess.run(["bash", str(GEN), "--print", "--host", "h", "--no-fqdn"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "alpha@h\tagents/alpha/\n", r.stdout
    fresh = tmp_path / "fresh"
    mkbox(fresh / "stores" / "beta")
    r = subprocess.run(["bash", str(GEN), "--print", "--host", "h", "--no-fqdn"],
                       capture_output=True, text=True, env={**env, VAR: str(fresh)})
    assert r.stdout == "beta@h\tstores/beta/\n", r.stdout
    r = subprocess.run(["bash", str(GEN), "--print", "--host", "h", "--no-fqdn"],
                       capture_output=True, text=True, env={k: v for k, v in env.items() if k != VAR})
    assert r.returncode == 0 and r.stdout == "", "the retired MAIL_ROOT decided the root"


# ── an old tree nobody named is said once, never read ─────────────────────────

def _old_host(tmp_path: Path, monkeypatch) -> dict[str, str]:
    """A host born before the move that took the code without naming its
    root: `~/.mail/agents` full of boxes, `~/.sessionkit/mail` holding only
    settings/ (a live host, measured before the move landed)."""
    home = tmp_path / "home"
    (home / ".mail" / "agents" / "alpha" / "new").mkdir(parents=True)
    (home / ".sessionkit" / "mail" / "settings").mkdir(parents=True)
    monkeypatch.setattr(_maildir, "_OLD_TREE_SAID", set())
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    return {"HOME": str(home)}


def test_an_unnamed_old_tree_is_said_once_on_stderr_and_never_read(tmp_path, monkeypatch, capsys):
    env = _old_host(tmp_path, monkeypatch)
    root, why = _maildir.mail_root_why(env)
    assert (root, why) == (tmp_path / "home" / ".sessionkit" / "mail", _maildir.FROM_DEFAULT)
    assert _maildir.root(env) == root / "stores", "the default answers; the old tree is never read"
    err = capsys.readouterr().err
    assert err.count("sessionmail: the mail root is the default") == 1, err
    assert str(tmp_path / "home" / ".mail" / "agents") in err and "EMPTY tree" in err
    assert '"root": "~/.mail"' in err and "mail.json" in err and "SESSIONMAIL_ROOT" in err
    _maildir.mail_root_why(env)
    _maildir.root(env)
    assert capsys.readouterr().err == "", "once per process, not per call"


def test_a_named_root_or_a_boxes_folder_silences_it(tmp_path, monkeypatch, capsys):
    env = _old_host(tmp_path, monkeypatch)
    assert _maildir.mail_root_why({**env, VAR: str(tmp_path / "home" / ".mail")})[1] == _maildir.FROM_VAR
    assert capsys.readouterr().err == "", "the variable names the root: nothing to say"
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: "~/.mail")
    assert _maildir.mail_root_why(env)[1] == _maildir.FROM_FILE
    assert capsys.readouterr().err == "", "the file names the root: nothing to say"
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    (tmp_path / "home" / ".sessionkit" / "mail" / "stores").mkdir()
    assert _maildir.mail_root_why(env)[1] == _maildir.FROM_DEFAULT
    assert capsys.readouterr().err == "", "a fresh tree with its own boxes folder: nothing to say"
    assert _maildir.abandoned_old_tree(tmp_path / "nowhere", {"HOME": str(tmp_path / "empty")}) is None
