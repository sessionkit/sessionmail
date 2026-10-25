"""The setup verb (PROVISIONAL name `message setup`), SM-660 fix-003.

design-001 parts C and D: the plan is printed whole, privileged steps first,
before anything is applied; it makes the agents root and the first boxes the
mail drop needs; it links notmuch's post-new and post-insert hooks into the
hook_dir notmuch reports; `--dry-run` writes nothing; a second run is all
DONE and writes nothing.

Everything here runs on a scratch HOME and mail root with a FAKE notmuch
(`SESSIONMAIL_NOTMUCH`). Nothing here touches a live host's ~/.mail or its
notmuch hooks: installing the hook on a live host is a deploy.

Fixtures are `alpha` and `beta` on `testhost` (convention 9).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from conftest import ACCOUNT_DEFAULT, write_mail_settings
from fakes import write_fake
from sessionmail import _index, _operator, _seat, _settings, _setup
from sessionmail.message import cli as message_cli

HOST = "testhost"

#: notmuch, as far as setup asks it. Every call is logged.
#: - `config get database.path`: the `path=` in the `--config` file.
#: - `config get database.hook_dir`: $FAKE_HOOK_DIR when set; else
#:   `<path>/.notmuch/hooks` once a database is there (as notmuch answers
#:   after its first `new`), else empty. $FAKE_OLD_NOTMUCH: always empty
#:   (a notmuch older than 0.32 has no such key).
#: - `new --no-hooks`: makes `<path>/.notmuch/`, or exits $FAKE_NEW_EXIT.
FAKE_NOTMUCH = """#!{python}
import os, sys
with open(os.environ["FAKE_NOTMUCH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
path = ""
if args and args[0].startswith("--config="):
    config, args = args[0][len("--config="):], args[1:]
    if os.path.isfile(config):
        for line in open(config):
            if line.startswith("path="):
                path = line.strip()[len("path="):]
database = os.path.join(path, ".notmuch") if path else ""
if args == ["config", "get", "database.path"]:
    sys.stdout.write(path + ("\\n" if path else ""))
    sys.exit(0)
if args == ["config", "get", "database.hook_dir"]:
    value = os.environ.get("FAKE_HOOK_DIR", "")
    if not value and database and os.path.isdir(database) and not os.environ.get("FAKE_OLD_NOTMUCH"):
        value = os.path.join(database, "hooks")
    sys.stdout.write(value + ("\\n" if value else ""))
    sys.exit(0)
if args == ["new", "--no-hooks"] and database:
    code = int(os.environ.get("FAKE_NEW_EXIT", "0"))
    if code:
        sys.stderr.write("fake notmuch: new failed\\n")
        sys.exit(code)
    os.makedirs(database, exist_ok=True)
    sys.exit(0)
sys.exit(1)
"""


@pytest.fixture
def mailroot(tmp_path: Path) -> Path:
    return tmp_path / "mailroot"


@pytest.fixture
def hooks(tmp_path: Path) -> Path:
    return tmp_path / "notmuch-db" / ".notmuch" / "hooks"


@pytest.fixture
def env(tmp_path: Path, mailroot: Path, hooks: Path, stranger) -> dict[str, str]:
    """A stranger's Mac (SK-95): no operator block, so the operator's box is
    the account's default name; no MTA, the mail drop selected."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    notmuch = write_fake(bin_dir / "notmuch", FAKE_NOTMUCH.format(python=sys.executable))
    agents = str(mailroot / "agents")
    # The host's notmuch profile (`_index`): setup asks it, and no other.
    profile = tmp_path / ".config" / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={mailroot}\n")
    return {
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "TMPDIR": str(tmp_path),
        "SESSION_MAIL_ROOT": agents, "SESSIONMAIL_ROOT": agents,
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(tmp_path / "no-generator-here"),
        "SESSIONMAIL_NOTMUCH": str(notmuch),
        "FAKE_NOTMUCH_LOG": str(tmp_path / "notmuch.log"),
        "FAKE_HOOK_DIR": str(hooks),
    }


def setup(env, *argv, tty=False):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(list(argv), env, out=out, err=err, tty=tty)
    return rc, out.getvalue(), err.getvalue()


def state(out: str, step: str) -> str:
    """STEP's state on the plan's `steps:` lines ("" when it is not there)."""
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split()
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[1]
    return ""


def detail(out: str, step: str) -> str:
    """STEP's detail on the plan's `steps:` lines (after its state)."""
    for line in out.split("\nsteps:\n", 1)[-1].splitlines():
        parts = line.split(None, 2)
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[2] if len(parts) > 2 else ""
    return ""


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Every path under ROOT, with its mode and mtime (lstat: a link is
    itself, never its target)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_mtime_ns)
    return out


# ── the name (its page and the MCP door: tests/test_setup_visible.py) ──────────

def test_the_name_is_one_constant():
    assert _setup.VERB == "setup"
    assert _setup.COMMAND == f"message {_setup.VERB}"
    assert message_cli.commands[_setup.VERB].callback.__doc__ is None   # the page reads COMMAND


def test_the_cli_delegates_to_main(env, mailroot, monkeypatch):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = CliRunner().invoke(message_cli, ["setup", "--dry-run", "--box", "alpha"],
                                prog_name="message")
    assert result.exit_code == _setup.EX_REMAINS, result.output
    assert "privileged steps (sudo):" in result.output
    assert not mailroot.exists()


# ── the plan, and --dry-run ──────────────────────────────────────────────────

def test_dry_run_prints_the_whole_plan_and_writes_nothing(env, tmp_path, mailroot, hooks):
    before = snapshot(tmp_path)
    rc, out, err = setup(env, "--box", "alpha", "--dry-run")
    assert rc == _setup.EX_REMAINS, out + err
    assert snapshot(tmp_path) == {**before, "notmuch.log": snapshot(tmp_path)["notmuch.log"]}
    assert not mailroot.exists() and not hooks.exists()
    assert out.index("privileged steps (sudo):") < out.index("steps:")
    assert "  none" in out
    for step in ("mail-root", f"box:{ACCOUNT_DEFAULT}", "box:alpha",
                 "notmuch-hook:post-new", "notmuch-hook:post-insert"):
        assert state(out, step) == "TODO", (step, out)
    # the drop host's maps step is DONE: the generator is never run, nothing is written
    assert state(out, "maps") == "DONE", out
    assert "the carrier is the mail drop: it reads the maps per send; none are written" in out
    assert state(out, "carrier") == "DONE"
    assert "dry run: nothing applied" in out
    assert "applying:" not in out
    assert state(out, "notmuch-index") == "DONE"
    assert "setup runs `notmuch new` once, to build an empty index" in out


def test_a_run_makes_the_root_the_boxes_and_the_hooks(env, mailroot, hooks):
    rc, out, err = setup(env, "--box", "alpha")
    assert rc == _setup.EX_OK, out + err
    agents = mailroot / "agents"
    for name in (ACCOUNT_DEFAULT, "alpha"):
        for sub in ("cur", "new", "tmp"):
            assert (agents / name / sub).is_dir()
        assert (agents / name).stat().st_mode & 0o777 == 0o700
    assert agents.stat().st_mode & 0o777 == 0o700
    for name in _setup.HOOK_NAMES:
        assert os.readlink(hooks / name) == str(_setup.PACKAGED_HOOK)
    assert _setup.PACKAGED_HOOK.is_file() and os.access(_setup.PACKAGED_HOOK, os.X_OK)
    assert "the carrier is the mail drop: it reads the maps per send; none are written" in out
    assert out.index("steps:") < out.index("applying:")
    assert out.rstrip().endswith("every step DONE")


def test_a_second_run_is_all_done_and_writes_nothing(env, tmp_path):
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    before = snapshot(tmp_path / "mailroot"), snapshot(tmp_path / "notmuch-db")
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    assert (snapshot(tmp_path / "mailroot"), snapshot(tmp_path / "notmuch-db")) == before
    assert "applying:" not in out
    steps = out.split("\nsteps:\n", 1)[1].split("\nnote:", 1)[0].splitlines()
    assert steps and all(line.split()[1] == "DONE" for line in steps), steps


def test_a_bad_box_name_is_refused_before_anything(env, tmp_path):
    for name in ("Alpha", "al pha", ".hidden", "a+b", "0000aaaa-1111-2222-3333-444455556666"):
        before = snapshot(tmp_path)
        rc, out, err = setup(env, "--box", name)
        assert rc == _setup.EX_USAGE, (name, out, err)
        assert out == "" and "REFUSED" in err and repr(name) in err
        assert snapshot(tmp_path) == before


def test_unknown_arguments_are_usage(env):
    rc, out, err = setup(env, "--frobnicate")
    assert rc == _setup.EX_USAGE and "--frobnicate" in err and out == ""


def test_the_roots_differing_is_refused(env, tmp_path):
    rc, out, err = setup({**env, "SESSIONMAIL_ROOT": str(tmp_path / "other" / "agents")})
    assert rc == _setup.EX_USAGE
    assert "REFUSED" in err and str(tmp_path / "other" / "agents") in err
    assert str(tmp_path / "mailroot" / "agents") in err
    assert not (tmp_path / "mailroot").exists() and not (tmp_path / "other").exists()


# ── the boxes ────────────────────────────────────────────────────────────────

def test_the_operator_box_is_the_configured_mailbox(env, mailroot, stranger):
    stranger('{"operator": {"mailbox": "beta"}}')
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert (mailroot / "agents" / "beta" / "new").is_dir()
    assert not (mailroot / "agents" / ACCOUNT_DEFAULT).exists()


def test_with_no_operator_at_all_the_line_names_the_key(env, mailroot, monkeypatch):
    from sessionmail import _operator
    monkeypatch.setattr(_operator, "account_name", lambda: None)
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "box:operator") == "MANUAL"
    assert _operator.OPERATOR_KEY in out
    assert (mailroot / "agents" / "alpha" / "new").is_dir()


#: SK-95's walk w2, hazard 1 (SM-875 fix-002): the box `sessionkit setup
#: --mailbox` names, and a box an old `~/.settings/session/` file names.
WALK_OPERATOR, WALK_DECOY = "stranger", "legacy-op"


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_re_run_plans_the_operator_the_kit_wrote_not_the_legacy_one(
        env, mailroot, tmp_path, monkeypatch, dry_run):
    """SK-95 walk w2 (hazard 1): `sessionkit setup --mailbox stranger` wrote
    the operator as a drop-in, `~/.sessionkit/tools/settings/session.d/
    10-operator.json`, and `session operator` read back "stranger"; a re-run
    of `message setup` still planned the box from the legacy
    `~/.settings/session/session.json`, which names someone else.

    The re-run plans "stranger" and nothing from the legacy file, whichever
    home that file sits under (the settings home or `$HOME`). Red at
    sessionmail a7818e8 (the session folder was `settings_root / session`);
    green from SM-875 fix-001 (`_operator.DIR`)."""
    home = tmp_path / "acct"          # the settings home: pwd's, in production
    monkeypatch.setattr(_operator, "settings_home", lambda: home)
    drop_in = home / ".sessionkit" / "tools" / "settings" / "session.d" / "10-operator.json"
    drop_in.parent.mkdir(parents=True)
    drop_in.write_text(json.dumps({"operator": {"mailbox": WALK_OPERATOR}}) + "\n")
    decoy = json.dumps({"operator": {"mailbox": WALK_DECOY}}) + "\n"
    for under in (home, Path(env["HOME"])):
        legacy = under / ".settings" / "session" / "session.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(decoy)
    rc, out, err = setup(env, *(["--dry-run"] if dry_run else []))
    assert f"box:{WALK_OPERATOR}" in out, out + err
    assert WALK_DECOY not in out + err
    assert not (mailroot / "agents" / WALK_DECOY).exists()
    if not dry_run:
        assert rc == _setup.EX_OK, out + err
        assert (mailroot / "agents" / WALK_OPERATOR / "new").is_dir()


def test_an_existing_box_is_done_and_left_alone(env, mailroot):
    box = mailroot / "agents" / "alpha"
    for sub in ("cur", "new", "tmp"):
        (box / sub).mkdir(parents=True)
    (box / "new" / "1.mail").write_text("kept")
    before = snapshot(box)
    rc, out, _ = setup(env, "--box", "alpha", "--dry-run")
    assert state(out, "box:alpha") == "DONE"
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    assert snapshot(box) == before


def test_on_a_postfix_host_a_made_box_runs_the_generator_once(env, tmp_path, mailroot):
    log = tmp_path / "gen.log"
    gen = write_fake(tmp_path / "gen", f"#!/bin/sh\necho \"$*\" >> {log}\n")
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(mta), "SESSIONMAIL_VMAILBOX_GEN": str(gen),
                        "MAGICDNS_SUFFIX": "example.invalid"}, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    assert log.read_text().splitlines() == [f"--root {mailroot}"]
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(mta), "SESSIONMAIL_VMAILBOX_GEN": str(gen),
                        "MAGICDNS_SUFFIX": "example.invalid"}, "--box", "alpha")
    assert rc == _setup.EX_OK and len(log.read_text().splitlines()) == 1   # nothing made, not run


def test_a_failed_rebuild_leaves_the_boxes_and_says_so(env, tmp_path, mailroot):
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(mta),
                        "MAGICDNS_SUFFIX": "example.invalid"}, "--box", "alpha")
    assert rc == _setup.EX_REMAINS, out
    assert "maps" in out and "BLOCKED: generator not found" in out
    assert (mailroot / "agents" / "alpha" / "new").is_dir()


# ── the carrier and the suffix (the seam) ────────────────────────────────────

def test_no_mta_and_no_drop_is_a_manual_line_naming_the_variable(env, tmp_path):
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(tmp_path / "no-mta")})
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "carrier") == "MANUAL"
    assert (f'write "sendmail": "drop" in {_settings.SHOWN} (or set SESSIONMAIL_SENDMAIL=drop): '
            f"the MTA at {tmp_path / 'no-mta'} (from $SESSIONMAIL_SENDMAIL) is not an executable "
            "file") in out
    assert state(out, "suffix") == ""            # nothing on the drop path needs one


def test_a_postfix_host_with_no_suffix_is_a_manual_line(env, tmp_path):
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    no_tailscale = {k: v for k, v in env.items() if k != "MAGICDNS_SUFFIX"}
    no_tailscale.update(SESSIONMAIL_SENDMAIL=str(mta), MAGICDNS_TAILSCALE_BUNDLE="")
    rc, out, _ = setup(no_tailscale, "--dry-run")
    assert state(out, "carrier") == "DONE"
    assert state(out, "suffix") == "MANUAL"
    assert (f'write "magicdns_suffix": "<suffix>" in {_settings.SHOWN} (or set MAGICDNS_SUFFIX), '
            "or start tailscale: the postfix maps are not regenerated without it") in out


def test_a_postfix_host_with_a_bad_suffix_is_a_manual_line(env, tmp_path):
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(mta), "MAGICDNS_SUFFIX": "not a suffix!"},
                       "--dry-run")
    assert state(out, "suffix") == "MANUAL" and "is not a DNS suffix" in out
    rc, out, _ = setup({**env, "SESSIONMAIL_SENDMAIL": str(mta), "MAGICDNS_SUFFIX": "ex.invalid"},
                       "--dry-run")
    assert state(out, "suffix") == "DONE" and "ex.invalid" in out


def test_the_seam_reads_the_carrier_through_the_resolver(tmp_path):
    """The carrier comes from the one resolver: the settings file's key,
    and a set variable over it."""
    from conftest import write_mail_settings
    from sessionmail import _operator, _settings
    write_mail_settings(_operator.settings_home(), '{"sendmail": "/usr/sbin/sendmail"}')
    assert _setup.settings({}) == _setup.Wanted(
        carrier=_settings.Carrier("mta", "/usr/sbin/sendmail", "host"), suffix=None)
    assert _setup.settings({"SESSIONMAIL_SENDMAIL": "drop", "MAGICDNS_SUFFIX": "x.invalid"}) \
        == _setup.Wanted(carrier=_settings.Carrier("drop", None, "variable"), suffix="x.invalid")


def _installed_template() -> Path:
    t = _operator.settings_home() / _settings.DIR / _settings.INSTALLED_TEMPLATE
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(_settings.template())
    return t


@pytest.mark.parametrize("where", ["default", "template", "host-drop", "host-mta",
                                   "variable-drop", "variable-mta"])
def test_the_carrier_step_names_its_value_and_source(env, tmp_path, where):
    """DONE whatever the carrier, naming its value and where it came from."""
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    run = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    run["MAGICDNS_SUFFIX"] = "ex.invalid"
    tmpl = _operator.settings_home() / _settings.DIR / _settings.INSTALLED_TEMPLATE
    tmpl.unlink(missing_ok=True)
    write_mail_settings(_operator.settings_home(), None)
    if where == "template":
        _installed_template()
    elif where == "host-drop":
        write_mail_settings(_operator.settings_home(), '{"version": 1, "sendmail": "drop"}')
    elif where == "host-mta":
        write_mail_settings(_operator.settings_home(), json.dumps({"version": 1, "sendmail": str(mta)}))
    elif where == "variable-drop":
        run["SESSIONMAIL_SENDMAIL"] = "drop"
    elif where == "variable-mta":
        run["SESSIONMAIL_SENDMAIL"] = str(mta)
    rc, out, _ = setup(run, "--dry-run")
    want = {
        "default": "the mail drop (the default); no suffix is needed",
        "template": ("the mail drop (the default, from "
                     f"{_settings.SHOWN_DIR}{_settings.INSTALLED_TEMPLATE}); no suffix is needed"),
        "host-drop": f"the mail drop (from {_settings.SHOWN}); no suffix is needed",
        "host-mta": f"the MTA at {mta} (from {_settings.SHOWN})",
        "variable-drop": "the mail drop (from $SESSIONMAIL_SENDMAIL); no suffix is needed",
        "variable-mta": f"the MTA at {mta} (from $SESSIONMAIL_SENDMAIL)",
    }[where]
    assert state(out, "carrier") == "DONE", out
    assert detail(out, "carrier") == want, out


@pytest.mark.parametrize("host_file", [None, '{"version": 1, "hooks": true, "host": "Pinned"}\n'])
def test_setup_writes_no_settings_key(env, host_file):
    """Applied in full, setup leaves the settings folder exactly as it was:
    no host file made, and an existing one byte for byte unchanged. Its one
    key, `host` (SM-941), is written only into a file that lacks it: the
    file here either is not there or already pins a host (the tests below)."""
    home = _operator.settings_home()
    path = write_mail_settings(home, host_file)
    folder = path.parent
    before = snapshot(folder) if folder.exists() else None
    text = path.read_bytes() if path.exists() else None
    setup(env)
    assert (path.read_bytes() if path.exists() else None) == text
    assert (snapshot(folder) if folder.exists() else None) == before


# ── the host pin (SM-941) ────────────────────────────────────────────────────

def host_steps(out: str) -> tuple[str, str]:
    return state(out, "host"), detail(out, "host")


def test_the_host_step_comes_after_the_mail_root(env):
    _rc, out, _ = setup(env, "--dry-run")
    lines = out.split("\nsteps:\n", 1)[1].splitlines()
    ids = [line.split()[0] for line in lines if line.startswith("  ")]
    assert ids[:2] == ["mail-root", "host"], ids


def test_no_settings_file_yet_is_done_and_writes_nothing(env):
    """The fresh Mac before its first session: the hook that alone creates the
    file pins the host when it does (`_settings.fresh_body`), so the step is
    DONE, setup's exit is unchanged, and no settings file is made."""
    home = _operator.settings_home()
    path = write_mail_settings(home, None)
    before = snapshot(home)
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    assert host_steps(out) == ("DONE", _setup.HOST_AT_BIRTH)
    assert "pinned when the first session creates the settings file" in out
    assert not path.exists()
    assert snapshot(home) == before


@pytest.mark.parametrize("text", [
    '{"version": 1, "hooks": true}\n',
    '{\n  "version": 1,\n  "hooks": true,\n  "sendmail": "drop"\n}\n',
    "{}",
])
def test_a_file_with_no_host_gains_the_live_name_and_nothing_else(env, monkeypatch, text):
    """The pin is the live short hostname, LOWERCASED, added as the file's
    last key; every other byte is kept. --dry-run shows the step and writes
    nothing; the run writes it; a second run is DONE and changes nothing."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Air.local")
    path = write_mail_settings(_operator.settings_home(), text)
    path.chmod(0o640)
    rc, out, _ = setup(env, "--dry-run")
    assert host_steps(out) == ("TODO", f'write "host": "macbook-air" in {_settings.SHOWN} '
                                       f"($SESSIONMAIL_HOST={HOST} still wins while it is set)")
    assert path.read_text() == text
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    got = path.read_text()
    assert json.loads(got) == {**json.loads(text), "host": "macbook-air"}
    assert list(json.loads(got))[-1] == "host"
    assert got.replace(', "host": "macbook-air"', "").replace(',\n  "host": "macbook-air"', "") \
        .replace('"host": "macbook-air"', "") == text, "every other byte kept"
    assert path.stat().st_mode & 0o777 == 0o640, "the file's mode kept"
    assert [n for n in os.listdir(path.parent) if n.startswith(".")] == [], "no temp left"
    before = path.read_bytes()
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK and "applying:" not in out, out
    assert host_steps(out) == ("DONE", f"'macbook-air', from {_settings.SHOWN}")
    assert path.read_bytes() == before


def test_the_pin_line_says_nothing_of_a_variable_when_none_is_set(env, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "box1.lan")
    write_mail_settings(_operator.settings_home(), '{"version": 1, "hooks": true}\n')
    bare = {k: v for k, v in env.items() if k not in ("SESSIONMAIL_HOST", "SESSION_MAIL_HOST")}
    _rc, out, _ = setup(bare, "--dry-run")
    assert host_steps(out) == ("TODO", f'write "host": "box1" in {_settings.SHOWN}')


@pytest.mark.parametrize("given", ["MacBook-Air", " host.lan "])
def test_a_host_already_set_is_never_overwritten(env, monkeypatch, given):
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "admins-Air.localdomain")
    text = json.dumps({"version": 1, "hooks": True, "host": given}) + "\n"
    path = write_mail_settings(_operator.settings_home(), text)
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert host_steps(out) == ("DONE", f"{given.strip().lower()!r}, from {_settings.SHOWN}")
    assert path.read_text() == text


@pytest.mark.parametrize("value", ["", "  ", 42, None])
def test_a_host_that_is_not_a_name_is_manual_and_never_rewritten(env, value):
    text = json.dumps({"version": 1, "hooks": True, "host": value}) + "\n"
    path = write_mail_settings(_operator.settings_home(), text)
    rc, out, _ = setup(env)
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "host") == "MANUAL", out
    assert f'"host": {json.dumps(value)} in {_settings.SHOWN} is not a host name' in out
    assert path.read_text() == text


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "\xff"])
def test_a_host_file_that_is_not_an_object_is_blocked_and_never_written(env, text):
    path = write_mail_settings(_operator.settings_home(), "")
    path.write_bytes(text.encode("latin-1"))
    before = path.read_bytes()
    rc, out, _ = setup(env)
    assert rc == _setup.EX_REMAINS, out
    assert host_steps(out) == ("BLOCKED", f"{_settings.SHOWN} is not a JSON object: setup "
                                          "never writes it")
    assert path.read_bytes() == before


def test_an_empty_hostname_is_blocked(env, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "")
    write_mail_settings(_operator.settings_home(), '{"version": 1, "hooks": true}\n')
    _rc, out, _ = setup(env, "--dry-run")
    assert state(out, "host") == "BLOCKED", out
    assert "the hostname is empty" in out


# ── the notmuch hook ─────────────────────────────────────────────────────────

def test_no_hook_dir_yet_builds_the_index_once_then_links(env, tmp_path, mailroot, hooks):
    """SM-899: notmuch reports no hook dir and no database is at its
    database.path: setup runs `new --no-hooks` once, then links the hooks
    in the dir notmuch reports after it. (Before: BLOCKED, naming the `new`.)"""
    rc, out, _ = setup({**env, "FAKE_HOOK_DIR": ""})
    assert rc == _setup.EX_OK, out
    assert state(out, "notmuch-index") == "TODO" and state(out, "notmuch-hook") == "TODO"
    profile = _index.profile_config(env=env)
    calls = (tmp_path / "notmuch.log").read_text().splitlines()
    assert calls.count(f"--config={profile} new --no-hooks") == 1, calls
    for name in _setup.HOOK_NAMES:
        assert os.readlink(mailroot / ".notmuch" / "hooks" / name) == str(_setup.PACKAGED_HOOK)
    assert not hooks.exists()


def test_no_notmuch_is_blocked(env, tmp_path):
    rc, out, _ = setup({**env, "SESSIONMAIL_NOTMUCH": str(tmp_path / "no-notmuch")})
    assert rc == _setup.EX_REMAINS
    assert state(out, "notmuch-index") == "BLOCKED" and "'" + str(tmp_path / "no-notmuch") + \
        "' not found ($SESSIONMAIL_NOTMUCH, else PATH)" in out
    assert state(out, "notmuch-hook") == "BLOCKED"


def test_setup_only_reads_notmuch(env, tmp_path):
    setup(env)
    calls = (tmp_path / "notmuch.log").read_text().splitlines()
    profile = _index.profile_config(env=env)
    assert calls and set(calls) == {f"--config={profile} config get database.hook_dir"}


def test_a_foreign_file_is_never_replaced(env, hooks):
    hooks.mkdir(parents=True)
    (hooks / "post-new").write_text("#!/bin/sh\n# someone else's\n")
    rc, out, _ = setup(env)
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "notmuch-hook:post-new") == "BLOCKED"
    assert (hooks / "post-new").read_text() == "#!/bin/sh\n# someone else's\n"
    assert os.readlink(hooks / "post-insert") == str(_setup.PACKAGED_HOOK)


def test_a_foreign_link_is_never_replaced(env, hooks, tmp_path):
    other = tmp_path / "theirs"
    other.write_text("#!/bin/sh\n")
    hooks.mkdir(parents=True)
    (hooks / "post-new").symlink_to(other)
    rc, out, _ = setup(env)
    assert rc == _setup.EX_REMAINS
    assert state(out, "notmuch-hook:post-new") == "BLOCKED"
    assert os.readlink(hooks / "post-new") == str(other)


def test_a_checkout_or_other_wheel_hook_is_done_and_left_as_is(env, hooks, tmp_path):
    checkout = tmp_path / "checkout" / "hooks" / "notmuch-post-new"
    wheel = tmp_path / "venv" / "site-packages" / "sessionmail" / "notmuch" / "post-new"
    for f in (checkout, wheel):
        f.parent.mkdir(parents=True)
        f.write_text("#!/bin/sh\n")
    hooks.mkdir(parents=True)
    (hooks / "post-new").symlink_to(checkout)
    (hooks / "post-insert").symlink_to(wheel)
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert state(out, "notmuch-hook:post-new") == "DONE" and "left as is" in out
    assert os.readlink(hooks / "post-new") == str(checkout)
    assert os.readlink(hooks / "post-insert") == str(wheel)


def test_a_dangling_link_to_our_own_hook_is_repaired(env, hooks, tmp_path):
    gone = tmp_path / "old-venv" / "lib" / "python3.12" / "sessionmail" / "notmuch" / "post-new"
    hooks.mkdir(parents=True)
    (hooks / "post-new").symlink_to(gone)
    rc, out, _ = setup(env, "--dry-run")
    assert state(out, "notmuch-hook:post-new") == "TODO" and "repair the dangling" in out
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert os.readlink(hooks / "post-new") == str(_setup.PACKAGED_HOOK)


def test_a_dangling_link_that_is_not_ours_is_blocked(env, hooks, tmp_path):
    hooks.mkdir(parents=True)
    (hooks / "post-new").symlink_to(tmp_path / "gone" / "their-hook")
    rc, out, _ = setup(env)
    assert rc == _setup.EX_REMAINS
    assert state(out, "notmuch-hook:post-new") == "BLOCKED"
    assert os.readlink(hooks / "post-new") == str(tmp_path / "gone" / "their-hook")


# ── the engine's privileged path ─────────────────────────────────────────────

FAKE_SUDO = """#!/bin/sh
echo "$*" >> "{log}"
[ "$1" = -- ] && shift
exec "$@"
"""


def _privileged_plan(tmp_path: Path, out: io.StringIO) -> tuple[list[_setup.Step], Path, list[str]]:
    target = tmp_path / "root-owned"
    seen: list[str] = []

    def apply_plain() -> str:
        seen.append(out.getvalue())               # what was printed before ANY apply
        return "ok"
    steps = [
        _setup.Step("plain", "an owner-space step", check=lambda: (_setup.TODO, "do it"),
                    apply=apply_plain),
        _setup.Step("needs-root", "a fake privileged step", privileged=True,
                    check=lambda: ((_setup.DONE, "") if target.exists() else (_setup.TODO, "")),
                    argv=["touch", str(target)]),
    ]
    return steps, target, seen


def test_the_privileged_block_prints_before_any_apply_and_no_tty_runs_nothing(tmp_path):
    out = io.StringIO()
    steps, target, seen = _privileged_plan(tmp_path, out)
    log = tmp_path / "sudo.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sudo", FAKE_SUDO.format(log=log))
    rc = _setup.run_plan(steps, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, out, dry_run=False, tty=False)
    text = out.getvalue()
    assert rc == _setup.EX_REMAINS, text
    assert seen and f"sudo -- touch {target}" in seen[0]
    assert text.index("privileged steps (sudo):") < text.index("applying:")
    assert f"run it yourself: sudo -- touch {target}" in text
    assert not target.exists() and not log.exists()


def test_at_a_tty_the_privileged_step_runs_through_sudo(tmp_path):
    out = io.StringIO()
    steps, target, _ = _privileged_plan(tmp_path, out)
    log = tmp_path / "sudo.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "sudo", FAKE_SUDO.format(log=log))
    rc = _setup.run_plan(steps, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, out, dry_run=False, tty=True)
    assert rc == _setup.EX_OK, out.getvalue()
    assert target.exists()
    assert log.read_text().splitlines() == [f"-- touch {target}"]


def test_a_failed_step_stops_the_run_and_keeps_what_ran(tmp_path):
    out = io.StringIO()
    ran: list[str] = []

    def boom() -> str:
        raise OSError("disk full (fake)")
    steps = [
        _setup.Step("one", "", check=lambda: (_setup.TODO, ""), apply=lambda: ran.append("one") or "ok"),
        _setup.Step("two", "", check=lambda: (_setup.TODO, ""), apply=boom),
        _setup.Step("three", "", check=lambda: (_setup.TODO, ""), apply=lambda: ran.append("3") or "ok"),
    ]
    rc = _setup.run_plan(steps, {}, out, dry_run=False, tty=False)
    assert rc == _setup.EX_FAILED
    assert ran == ["one"]
    assert "FAILED: disk full (fake)" in out.getvalue() and "stopped at two" in out.getvalue()


# ── B.3.4: a drop host runs no generator ─────────────────────────────────────

def test_a_drop_host_birth_runs_no_generator(tmp_path):
    log = tmp_path / "gen.log"
    gen = write_fake(tmp_path / "gen", f"#!/bin/sh\necho ran >> {log}\n")
    agents = tmp_path / "mail" / "agents"
    agents.mkdir(parents=True)
    ok, line = _seat._run_generator(agents, {"SESSIONMAIL_SENDMAIL": "drop",
                                             _seat.GEN_VAR: str(gen)}, ())
    assert ok is True
    assert line == ("generator not run: the carrier is the mail drop; "
                    "it reads the maps per send")
    assert not log.exists()
    ok, _ = _seat._run_generator(agents, {"SESSIONMAIL_SENDMAIL": "/usr/sbin/sendmail",
                                          _seat.GEN_VAR: str(gen),
                                          "MAGICDNS_SUFFIX": "example.invalid"}, ())
    assert log.read_text() == "ran\n"           # not the drop: it runs, as before


# ── end to end: repro-001 gaps 1 and 2 inverted ──────────────────────────────

GEN = Path(__file__).resolve().parents[1] / "src" / "sessionmail" / "postfix" / "vmailbox-gen"


@pytest.mark.skipif(shutil.which("bash") is None, reason="the generator is a bash script")
def test_on_a_fresh_root_setup_then_send_lands_with_no_suffix(env, tmp_path, mailroot):
    """No boxes, no MTA, no MagicDNS suffix, no tailscale: `message send`
    refuses the unknown box (the drop never makes one); after `message setup
    --box alpha` the same send lands in alpha's new/."""
    from sessionmail import _sender
    only = tmp_path / "bash-only"
    only.mkdir()
    (only / "bash").symlink_to(shutil.which("bash"))
    real = {k: v for k, v in env.items() if k != "MAGICDNS_SUFFIX"}
    real.update(SESSIONMAIL_VMAILBOX_GEN=str(GEN), MAGICDNS_TAILSCALE_BUNDLE="",
                PATH=f"{Path(env['SESSIONMAIL_NOTMUCH']).parent}{os.pathsep}/usr/bin"
                     f"{os.pathsep}/bin{os.pathsep}{only}")
    body = tmp_path / "body.md"
    body.write_text("hello\n")
    argv = ["alpha", "--from", "beta", "--subject", "after setup", "--body-file", str(body)]
    rc, _, err = _sender.run(argv, real)
    assert rc == 3 and "'alpha'" in err, err          # before: no box, refused
    rc, out, err = setup(real, "--box", "alpha")
    assert rc == _setup.EX_OK, out + err
    rc, out, err = _sender.run(argv, real)
    assert rc == 0, err
    assert [p.parent.name for p in (mailroot / "agents" / "alpha").glob("*/*")] == ["new"]


# ── the carrier row is a contract: a downstream parser reads it ──────────────

@pytest.mark.parametrize("argv", [["--dry-run"], []], ids=["dry-run", "applied"])
@pytest.mark.parametrize("carrier", ["drop", "mta"])
def test_the_carrier_row_is_byte_for_byte(env, tmp_path, carrier, argv):
    """`message setup`'s carrier row, dry-run and applied, exactly: a reader
    outside this package parses it (the row's id, its state, then a detail
    that starts "the mail drop" or "the MTA at"). Any change to it is a
    change to that contract, never a tidy-up."""
    mta = write_fake(tmp_path / "sendmail", "#!/bin/sh\ncat >/dev/null\n")
    run = ({**env, "SESSIONMAIL_SENDMAIL": "drop"} if carrier == "drop" else
           {**env, "SESSIONMAIL_SENDMAIL": str(mta), "MAGICDNS_SUFFIX": "example.invalid"})
    _rc, out, _ = setup(run, *argv)
    rows = [ln for ln in out.splitlines() if ln.startswith("  carrier ")]
    want = ("  carrier                   DONE     the mail drop (from $SESSIONMAIL_SENDMAIL); "
            "no suffix is needed" if carrier == "drop" else
            f"  carrier                   DONE     the MTA at {mta} (from $SESSIONMAIL_SENDMAIL)")
    assert rows == [want], out
