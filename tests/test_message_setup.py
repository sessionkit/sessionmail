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
    # The host's notmuch profile (`_index`): setup asks it, and no other.
    profile = tmp_path / ".config" / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={mailroot}\n")
    return {
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "TMPDIR": str(tmp_path),
        "SESSIONMAIL_ROOT": str(mailroot),
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
    boxes = mailroot / "stores"
    for name in (ACCOUNT_DEFAULT, "alpha"):
        for sub in ("cur", "new", "tmp"):
            assert (boxes / name / sub).is_dir()
        assert (boxes / name).stat().st_mode & 0o777 == 0o700
    assert boxes.stat().st_mode & 0o777 == 0o700
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


def test_a_retired_root_variable_is_named_never_read(env, tmp_path):
    """One resolver, so no two sides can differ and nothing is refused. The
    retired spellings (MAIL_ROOT, SESSION_MAIL_ROOT) decide nothing; a set
    one is NAMED on the mail-root step, so a host still exporting it hears
    "ignored" rather than silence."""
    rc, out, err = setup({**env, "MAIL_ROOT": str(tmp_path / "other"),
                          "SESSION_MAIL_ROOT": str(tmp_path / "older")}, "--dry-run")
    assert "REFUSED" not in err, err
    line = next(l for l in out.splitlines() if "mail-root" in l)
    assert "ignored, retired: $SESSION_MAIL_ROOT, $MAIL_ROOT" in line, out
    assert not (tmp_path / "other").exists() and not (tmp_path / "older").exists()


# ── the boxes ────────────────────────────────────────────────────────────────

def test_the_operator_box_is_the_configured_mailbox(env, mailroot, stranger):
    stranger('{"operator": {"mailbox": "beta"}}')
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert (mailroot / "stores" / "beta" / "new").is_dir()
    assert not (mailroot / "stores" / ACCOUNT_DEFAULT).exists()


def test_with_no_operator_at_all_the_line_names_the_key(env, mailroot, monkeypatch):
    from sessionmail import _operator
    monkeypatch.setattr(_operator, "account_name", lambda: None)
    rc, out, _ = setup(env, "--box", "alpha")
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "box:operator") == "MANUAL"
    assert _operator.OPERATOR_KEY in out
    assert (mailroot / "stores" / "alpha" / "new").is_dir()


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
    assert not (mailroot / "stores" / WALK_DECOY).exists()
    if not dry_run:
        assert rc == _setup.EX_OK, out + err
        assert (mailroot / "stores" / WALK_OPERATOR / "new").is_dir()


def test_an_existing_box_is_done_and_left_alone(env, mailroot):
    box = mailroot / "stores" / "alpha"
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
    assert (mailroot / "stores" / "alpha" / "new").is_dir()


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
    assert [p.parent.name for p in (mailroot / "stores" / "alpha").glob("*/*")] == ["new"]


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


# ── the delivery step: nm-deliver with the kit profile baked in (SM-1025) ────

#: postconf, as far as the delivery step asks it: `-M nminsert/unix` prints
#: $FAKE_MASTER's text (none: no such service), then exits $FAKE_POSTCONF_EXIT.
FAKE_POSTCONF = """#!/bin/sh
[ "$1" = "-M" ] && [ "$2" = "nminsert/unix" ] || exit 64
[ -n "$FAKE_MASTER" ] && [ -f "$FAKE_MASTER" ] && cat "$FAKE_MASTER"
exit "${{FAKE_POSTCONF_EXIT:-0}}"
"""
ME = __import__("pwd").getpwuid(os.getuid()).pw_name


def insert_host(env: dict[str, str], tmp_path: Path, *, argv: str | None = None,
                user: str = ME) -> tuple[dict[str, str], Path]:
    """ENV on an insert host: postconf names an nminsert service whose argv
    runs the wrapper in a throwaway install folder (or ARGV). Returns the
    env and the install target."""
    tools = tmp_path / "postfix-tools"
    tools.mkdir(exist_ok=True)
    postconf = write_fake(tools / "postconf", FAKE_POSTCONF)
    folder = tmp_path / "postfix-agents"
    folder.mkdir(exist_ok=True)
    target = folder / "nm-deliver"
    master = tmp_path / "master.txt"
    master.write_text("nminsert unix - n n - - pipe flags=DORhu "
                      f"user={user} argv={argv or target} ${{recipient}}\n")
    return {**env, "NM_DELIVER_POSTCONF": str(postconf), "FAKE_MASTER": str(master),
            _setup.NM_DELIVER_DIR_VAR: str(folder)}, target


def kit_profile(env: dict[str, str]) -> Path:
    return _index.profile_config(_index.host_profile(env)[0], env)


def test_dry_run_lists_the_delivery_step(env, tmp_path):
    """SM-1025: `message setup --dry-run` shows the step. A host
    with no postconf to ask is not an insert host: DONE, nothing installed."""
    _rc, out, _ = setup(env, "--dry-run")
    assert state(out, "delivery") == "DONE", out
    assert detail(out, "delivery").startswith("not an insert host (no postconf to ask)")
    run, target = insert_host(env, tmp_path)
    before = snapshot(tmp_path)
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "delivery") == "TODO", out
    assert detail(out, "delivery").startswith(
        f"install {target}, NOTMUCH_CONFIG={kit_profile(run)} (profile sessionkit")
    assert not target.exists()
    assert {k for k in snapshot(tmp_path) if k not in before} <= {"notmuch.log"}


def test_a_host_with_no_nminsert_service_gets_no_wrapper(env, tmp_path):
    run, target = insert_host(env, tmp_path)
    Path(run["FAKE_MASTER"]).unlink()
    _rc, out, _ = setup(run)
    assert state(out, "delivery") == "DONE", out
    assert "master.cf has no nminsert/unix service" in detail(out, "delivery")
    assert not target.exists()


def test_the_step_installs_the_wrapper_with_the_kit_profile_then_is_done(env, tmp_path):
    run, target = insert_host(env, tmp_path)
    rc, out, err = setup(run, "--box", "alpha")
    assert rc == _setup.EX_OK, out + err
    assert target.read_bytes() == _setup.render_nm_deliver(kit_profile(run))
    assert f"KIT_NOTMUCH_CONFIG='{kit_profile(run)}'" in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o755
    assert f"installed {target}, NOTMUCH_CONFIG={kit_profile(run)}" in out
    before = snapshot(target.parent)
    rc, out, _ = setup(run, "--box", "alpha")
    assert rc == _setup.EX_OK, out
    assert state(out, "delivery") == "DONE"
    assert detail(out, "delivery").endswith(f"nminsert/unix runs {target}")
    assert snapshot(target.parent) == before


def test_a_stale_installed_copy_is_refreshed(env, tmp_path):
    run, target = insert_host(env, tmp_path)
    target.write_bytes(_setup.PACKAGED_NM_DELIVER.read_bytes())    # hand-installed, no profile
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "delivery") == "TODO"
    assert detail(out, "delivery").startswith(f"refresh (it is not what setup would write) {target}")
    _rc, out, _ = setup(run)
    assert target.read_bytes() == _setup.render_nm_deliver(kit_profile(run))


def test_a_service_that_runs_another_path_is_reported_never_edited(env, tmp_path):
    """The transport is reported, read-only: setup installs, and the repoint
    of master.cf is the human's."""
    elsewhere = str(_setup.PACKAGED_NM_DELIVER)
    run, target = insert_host(env, tmp_path, argv=elsewhere)
    master = Path(run["FAKE_MASTER"]).read_text()
    rc, out, _ = setup(run)
    assert f"nminsert/unix runs {elsewhere}, not {target}: repoint its argv= by hand" in out
    assert target.read_bytes() == _setup.render_nm_deliver(kit_profile(run))
    rc, out, _ = setup(run)
    assert rc == _setup.EX_REMAINS
    assert state(out, "delivery") == "MANUAL", out
    assert Path(run["FAKE_MASTER"]).read_text() == master


def test_a_service_run_as_another_user_is_blocked(env, tmp_path):
    run, target = insert_host(env, tmp_path, user="nobody-else")
    _rc, out, _ = setup(run)
    assert state(out, "delivery") == "BLOCKED", out
    assert "as user=nobody-else, but setup runs as" in detail(out, "delivery")
    assert not target.exists()


def test_a_failing_postconf_is_blocked(env, tmp_path):
    run, _target = insert_host(env, tmp_path)
    _rc, out, _ = setup({**run, "FAKE_POSTCONF_EXIT": "1"}, "--dry-run")
    assert state(out, "delivery") == "BLOCKED", out
    assert "cannot tell whether postfix delivers by nminsert" in detail(out, "delivery")


def test_a_missing_install_folder_is_manual(env, tmp_path):
    run, target = insert_host(env, tmp_path)
    target.parent.rmdir()
    _rc, out, _ = setup(run)
    assert state(out, "delivery") == "MANUAL", out
    assert f"`sudo -- mkdir -p {target.parent}`" in detail(out, "delivery")
    assert not target.parent.exists()


def test_a_profile_path_it_cannot_bake_is_blocked_at_install(env, tmp_path, monkeypatch):
    run, target = insert_host(env, tmp_path)
    monkeypatch.setattr(_index, "profile_config", lambda *_a, **_k: Path("/a b/config"))
    _rc, out, _ = setup(run)
    assert state(out, "delivery") == "BLOCKED", out
    assert "cannot be baked into nm-deliver" in detail(out, "delivery")
    assert not target.exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root writes any folder")
def test_a_folder_it_cannot_write_is_a_privileged_step_printed_first(env, tmp_path):
    """Never sudo silently: the install is printed in the privileged block
    with its exact line; without a tty it is not run. Setup stages the
    rendered copy (no root) so the printed line works as it stands."""
    run, target = insert_host(env, tmp_path)
    target.parent.chmod(0o555)
    try:
        staged = _setup.staged_nm_deliver(run)
        line = _setup.sudo_line([_setup.INSTALL, "-m", "0755", str(staged), str(target)])
        rc, out, _ = setup(run, "--dry-run")
        privileged = out.split("privileged steps (sudo):\n", 1)[1].split("\nsteps:\n", 1)[0]
        assert "delivery" in privileged and line in privileged, out
        assert "  none" not in privileged
        assert not staged.exists()
        rc, out, _ = setup(run, tty=False)
        assert rc == _setup.EX_REMAINS, out
        assert f"NOT RUN (no tty to ask for a password): run it yourself: {line}" in out
        assert staged.read_bytes() == _setup.render_nm_deliver(kit_profile(run))
        assert not target.exists()
    finally:
        target.parent.chmod(0o755)


def a_database_and_no_profile(env: dict[str, str], mailroot: Path) -> Path:
    """An insert host's usual state before setup: a notmuch database in the
    mail root (delivery indexed it through notmuch's default config) and no
    kit profile, so `notmuch-profile` adopts nothing and stays MANUAL.
    Returns the kit profile path, which is not there."""
    kit_profile(env).unlink()                   # the old file the fixture wrote
    config = kit_profile(env)                   # now the kit's own path
    (mailroot / ".notmuch").mkdir(parents=True)
    assert not config.exists()
    return config


def test_no_wrapper_bakes_in_a_profile_setup_will_not_write(env, tmp_path, mailroot):
    """SM-1025 peer review: the step baked in the kit profile's path whether
    or not the file was there. On a host whose profile step cannot write it
    (a database already in the mail root), the refreshed wrapper would then
    defer every delivery (4.3.0, the config is not a file). Setup waits on
    the profile step instead, and leaves the wrapper the service runs as it is."""
    run, target = insert_host(env, tmp_path)
    config = a_database_and_no_profile(run, mailroot)
    hand = _setup.PACKAGED_NM_DELIVER.read_bytes()
    target.write_bytes(hand)                    # hand-installed, delivering today
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "notmuch-profile") == "MANUAL", out
    assert state(out, "delivery") == "BLOCKED", out
    assert detail(out, "delivery").startswith(f"{config} is not there and notmuch-profile is MANUAL: "
                                              "waits on notmuch-profile"), out
    rc, out, _ = setup(run)
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "delivery") == "BLOCKED", out
    assert target.read_bytes() == hand


def test_an_installed_copy_whose_profile_is_gone_is_not_done(env, tmp_path, mailroot):
    """A current copy is DONE only while the file it bakes in is there."""
    run, target = insert_host(env, tmp_path)
    # The order matters: render AFTER the old file is gone, so the copy bakes
    # in the path setup resolves now and is current (byte for byte), and the
    # DONE gate is what is tested, not the refresh branch.
    config = a_database_and_no_profile(run, mailroot)
    target.write_bytes(_setup.render_nm_deliver(config))
    assert target.read_bytes() == _setup.render_nm_deliver(kit_profile(run))
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "delivery") == "BLOCKED", out
    line = detail(out, "delivery")
    assert "waits on notmuch-profile" in line
    # A copy IS installed: the line says what it does now, never that none is.
    assert f"{target} bakes it in, so every delivery through it defers (4.3.0)" in line, out
    assert "installs no wrapper" not in line


def applied_line(out: str, step: str) -> str:
    """STEP's line under `applying:` (after its id), "" when it has none."""
    for line in out.split("\napplying:\n", 1)[-1].splitlines():
        parts = line.split(None, 1)
        if line.startswith("  ") and parts and parts[0] == step:
            return parts[1] if len(parts) > 1 else ""
    return ""


def a_profile_step_that_writes_nothing(monkeypatch) -> None:
    """`notmuch-profile` reads TODO, and its apply 'succeeds' without writing
    the file: the one way the delivery step reaches its apply-time re-check
    (`present`), since its check passes a profile that is still TODO."""
    real = _setup.profile_step

    def fake(*args, **kwargs):
        step = real(*args, **kwargs)
        if step.apply is not None:
            step.apply = lambda: "wrote nothing"
        return step
    monkeypatch.setattr(_setup, "profile_step", fake)


def test_the_apply_checks_the_profile_again(env, tmp_path, monkeypatch):
    """The check let the install through (the profile step was TODO); the
    apply looks again after the profile step has run, and the step ends
    BLOCKED, the run going on, rather than writing a wrapper that defers."""
    run, target = insert_host(env, tmp_path)
    kit_profile(run).unlink()
    config = kit_profile(run)
    a_profile_step_that_writes_nothing(monkeypatch)
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "delivery") == "TODO", out
    rc, out, _ = setup(run, "--box", "alpha")
    assert rc == _setup.EX_REMAINS, out
    applied = applied_line(out, "delivery")
    assert applied.startswith(f"BLOCKED: {config} is not there"), out
    assert "Setup installs no wrapper" in applied
    assert not config.exists()
    assert not target.exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root writes any folder")
def test_the_privileged_stage_checks_the_profile_again(env, tmp_path, monkeypatch):
    """The same on the privileged path: run_plan treats a Left from the
    stage (the apply that prepares the sudo line) as BLOCKED. Nothing is
    staged and the sudo line is never offered or run."""
    run, target = insert_host(env, tmp_path)
    kit_profile(run).unlink()
    a_profile_step_that_writes_nothing(monkeypatch)
    target.parent.chmod(0o555)
    try:
        staged = _setup.staged_nm_deliver(run)
        rc, out, _ = setup(run, "--box", "alpha", tty=False)
        assert rc == _setup.EX_REMAINS, out
        applied = applied_line(out, "delivery")
        assert applied.startswith("BLOCKED: ") and "waits on notmuch-profile" in applied, out
        assert "NOT RUN" not in out.split("\napplying:\n", 1)[1]
        assert "(BLOCKED): " in out.split("step(s) left for you:", 1)[1]
        assert not staged.exists()
        assert not target.exists()
    finally:
        target.parent.chmod(0o755)


def test_a_profile_written_in_the_same_run_is_baked_in(env, tmp_path):
    """No database yet: the profile step writes the kit profile, and the
    delivery step, read after it, installs the wrapper in the same run."""
    run, target = insert_host(env, tmp_path)
    kit_profile(run).unlink()
    config = kit_profile(run)
    assert not config.exists()
    _rc, out, _ = setup(run, "--dry-run")
    assert state(out, "notmuch-profile") == "TODO", out
    assert state(out, "delivery") == "TODO", out
    rc, out, err = setup(run, "--box", "alpha")
    assert config.is_file(), out
    assert target.read_bytes() == _setup.render_nm_deliver(config), out + err


@pytest.mark.skipif(os.getuid() == 0, reason="root writes any folder")
def test_the_privileged_install_waits_on_the_profile_too(env, tmp_path, mailroot):
    """Nothing is staged and no sudo line is offered for a wrapper that
    would defer every delivery."""
    run, target = insert_host(env, tmp_path)
    a_database_and_no_profile(run, mailroot)
    target.parent.chmod(0o555)
    try:
        staged = _setup.staged_nm_deliver(run)
        rc, out, _ = setup(run, tty=False)
        assert rc == _setup.EX_REMAINS, out
        assert "waits on notmuch-profile" in out
        assert "NOT RUN" not in out
        assert not staged.exists()
        assert not target.exists()
        # The plan's privileged block offers no line either: it says why.
        block = out.split("privileged steps (sudo):\n", 1)[1].split("\nsteps:\n", 1)[0]
        assert "delivery" in block and "BLOCKED" in block, out
        assert "waits on notmuch-profile" in block, out
        assert "sudo --" not in block, out
    finally:
        target.parent.chmod(0o755)


@pytest.mark.skipif(os.getuid() == 0, reason="root writes any folder")
def test_a_stale_staged_copy_is_never_offered_while_blocked(env, tmp_path, mailroot):
    """A copy staged by an earlier run (its sudo line never run) is still on
    disk. While the profile's file is missing, the plan must not print the
    line that would install it: that wrapper defers every delivery."""
    run, target = insert_host(env, tmp_path)
    a_database_and_no_profile(run, mailroot)
    staged = _setup.staged_nm_deliver(run)
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"#!/bin/sh\n")
    target.parent.chmod(0o555)
    try:
        _rc, out, _ = setup(run, "--dry-run")
        block = out.split("privileged steps (sudo):\n", 1)[1].split("\nsteps:\n", 1)[0]
        assert block.split()[:2] == ["delivery", "BLOCKED"], out
        assert str(staged) not in out, out
        assert "sudo --" not in out, out
    finally:
        target.parent.chmod(0o755)
