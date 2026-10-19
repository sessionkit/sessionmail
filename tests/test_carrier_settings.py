"""The carrier: the mail drop by default, and an exact vocabulary.

openspec/changes/infra-settings, design D3 and D11. `_settings.carrier(env)`
is the one resolver: the variable when set and not empty, else the
`sendmail` key in the template and host layers, else the drop. A carrier is
exactly `drop` or an absolute path; anything else is REFUSED by name, and a
send exits 3 with nothing sent. The bash oracle is taken only when the
carrier is an MTA. `message setup` refuses its plan on a refused carrier.

The settings home is the suite's sandbox (`_operator.settings_home`).
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import write_mail_settings

from fakes import write_fake
from sessionmail import _drop, _operator, _sender, _settings, _setup
from sessionmail.message import cli
from test_mail_drop import (FAKE_GEN, TRIPWIRE, VALIAS, VMAILBOX, body_file,  # noqa: F401
                            bin_dir, env, landed, mailroot, send, sender)

EX_MTA = 3
RULE = ('the carrier is "drop" or an absolute path to an MTA '
        "(for example /usr/sbin/sendmail)")
SHOWN = "~/.sessionkit/mail/settings/mail.json"

#: Values the variable may carry that are not a carrier (stripped, they
#: are still not one).
REFUSED_TEXT = ["sendmail", "./sendmail", "bin/sendmail", "~/bin/sendmail", "Drop", "DROP",
                "postfix"]
#: The file can also carry what a variable cannot: an empty string, and
#: values that are not strings at all.
REFUSED_FILE = [*REFUSED_TEXT, "", True, 1, None, {"path": "/usr/sbin/sendmail"}]

RECORDING_SENDMAIL = """#!{python}
import os, sys
sys.stdin.buffer.read()
with open(os.environ["SENDMAIL_LOG"], "a") as log:
    log.write(sys.argv[0] + "\\n")
"""


def host_file(data: dict | None) -> Path:
    text = None if data is None else json.dumps({"version": 1, "hooks": False, **data}) + "\n"
    return write_mail_settings(_operator.settings_home(), text)


@pytest.fixture
def no_file():
    """No template and no host file under the settings home."""
    home = _operator.settings_home()
    (home / _settings.DIR / _settings.INSTALLED_TEMPLATE).unlink(missing_ok=True)
    host_file(None)


def recording_sendmail(where: Path, log: Path) -> Path:
    return write_fake(where, RECORDING_SENDMAIL.format(python=sys.executable))


# ── the resolver ─────────────────────────────────────────────────────────────

def test_carrier_default_is_the_drop(no_file):
    c = _settings.carrier({})
    assert (c.kind, c.path, c.source, c.why) == ("drop", None, "default", "")


def test_carrier_reads_the_template_then_the_host(no_file):
    t = _operator.settings_home() / _settings.DIR / _settings.INSTALLED_TEMPLATE
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(_settings.template())
    assert (_settings.carrier({}).kind, _settings.carrier({}).source) == ("drop", "template")
    host_file({"sendmail": "/opt/mta/sendmail"})
    c = _settings.carrier({})
    assert (c.kind, c.path, c.source) == ("mta", "/opt/mta/sendmail", "host")


def test_carrier_variable_wins_and_is_stripped(no_file):
    host_file({"sendmail": "/opt/mta/sendmail"})
    c = _settings.carrier({"SESSIONMAIL_SENDMAIL": " drop\n"})
    assert (c.kind, c.source) == ("drop", "variable")
    c = _settings.carrier({"SESSIONMAIL_SENDMAIL": "/usr/local/sbin/sendmail"})
    assert (c.kind, c.path, c.source) == ("mta", "/usr/local/sbin/sendmail", "variable")


@pytest.mark.parametrize("value", REFUSED_TEXT)
def test_carrier_refuses_a_variable_by_name(no_file, value):
    c = _settings.carrier({"SESSIONMAIL_SENDMAIL": value})
    assert (c.kind, c.path, c.source) == ("refused", None, "variable")
    assert c.why == f"$SESSIONMAIL_SENDMAIL is {json.dumps(value)}, which is not a carrier: {RULE}"


@pytest.mark.parametrize("value", REFUSED_FILE, ids=repr)
def test_carrier_refuses_a_file_value_by_name(no_file, value):
    host_file({"sendmail": value})
    c = _settings.carrier({})
    assert (c.kind, c.path, c.source) == ("refused", None, "host")
    assert c.why == f'"sendmail": {json.dumps(value)} in {SHOWN} is not a carrier: {RULE}'


def test_carrier_unreadable_file_is_refused_unless_the_variable_is_set(no_file):
    write_mail_settings(_operator.settings_home(), "{not json\n")
    c = _settings.carrier({})
    assert (c.kind, c.source) == ("refused", "unreadable")
    assert c.why.startswith(f"{SHOWN} could not be read (")
    assert c.why.endswith("), so the carrier is not known")
    assert _settings.carrier({"SESSIONMAIL_SENDMAIL": "drop"}).kind == "drop"


def test_carrier_never_raises(no_file, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no layers")
    monkeypatch.setattr(_operator, "host_sources", boom)
    c = _settings.carrier({})
    assert c.kind == "refused" and "no layers" in c.why


def test_drop_selected_follows_the_carrier(no_file):
    assert _drop.selected({}) is True                      # the default
    host_file({"sendmail": "/opt/mta/sendmail"})
    assert _drop.selected({}) is False
    assert _drop.selected({"SESSIONMAIL_SENDMAIL": "drop"}) is True
    host_file({"sendmail": "sendmail"})
    assert _drop.selected({}) is False                     # refused is not the drop


# ── 4.2: an empty environment sends by the drop (SC1) ───────────────────────

def test_empty_env_sends_by_drop(no_file, sender, tmp_path, bin_dir):
    """No `SESSIONMAIL_*` (nor its older spellings), HOME a scratch home with
    no mail.json: the send lands by the drop, and no sendmail runs - not the
    conventional one, and not one on PATH."""
    host = socket.gethostname().split(".")[0].lower()
    home = tmp_path / "home"
    for box in ("alpha", "beta"):
        for d in ("cur", "new", "tmp"):
            (home / ".mail" / "agents" / box / d).mkdir(parents=True)
    log = tmp_path / "sendmail.log"
    recording_sendmail(bin_dir / "sendmail", log)
    env = {"HOME": str(home), "PATH": str(bin_dir), "TMPDIR": str(tmp_path),
           "SENDMAIL_LOG": str(log),
           "FAKE_VMAILBOX": "".join(f"{n}@{host}\tagents/{n}/\n" for n in ("alpha", "beta")),
           "FAKE_VALIAS": ""}
    assert not [k for k in env if "MAIL" in k and k.startswith(("SESSIONMAIL_", "SESSION_MAIL_"))]
    rc, out, err = send(sender, env, tmp_path, "alpha")
    assert rc == 0, err
    assert "sent via mail drop (no MTA) → alpha" in err
    assert len(list((home / ".mail" / "agents" / "alpha" / "new").iterdir())) == 1
    assert not log.exists()                                # no sendmail ran


# ── 4.3: the file names the MTA, and the variable beats it (SC2) ────────────

def test_file_mta_and_variable_wins(no_file, sender, env, tmp_path, monkeypatch):
    first, second = tmp_path / "mta-file", tmp_path / "mta-var"
    log = tmp_path / "sendmail.log"
    recording_sendmail(first, log)
    recording_sendmail(second, log)
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    env["SENDMAIL_LOG"] = str(log)
    host_file({"sendmail": str(first)})
    monkeypatch.setattr(_sender, "preflight", lambda *a, **k: None)   # postfix's maps: not here
    rc, _out, err = send(sender, env, tmp_path, "alpha")
    assert rc == 0, err
    assert log.read_text().splitlines() == [str(first)]
    rc, _out, err = send(sender, {**env, "SESSIONMAIL_SENDMAIL": str(second)}, tmp_path, "alpha")
    assert rc == 0, err
    assert log.read_text().splitlines() == [str(first), str(second)]


# ── 4.4: what is not a carrier is refused: exit 3, nothing sent, D3's text ──

def _refused_send(sender, env, tmp_path, mailroot, log):
    rc, out, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_MTA, err
    assert out == ""                                       # nothing minted
    assert landed(mailroot) == {}                          # nothing dropped
    assert not log.exists()                                # no MTA ran
    return err


@pytest.mark.parametrize("value", REFUSED_TEXT)
def test_not_a_carrier_refused_variable(no_file, sender, env, tmp_path, mailroot, bin_dir, value):
    log = tmp_path / "sendmail.log"
    recording_sendmail(bin_dir / "sendmail", log)
    err = _refused_send(sender, {**env, "SESSIONMAIL_SENDMAIL": value,
                                 "SENDMAIL_LOG": str(log)}, tmp_path, mailroot, log)
    assert err.splitlines()[-1] == (
        f"session-mail-send: no MTA: $SESSIONMAIL_SENDMAIL is {json.dumps(value)}, which is "
        f"not a carrier: {RULE} - NOTHING WAS SENT")


@pytest.mark.parametrize("value", REFUSED_FILE, ids=repr)
def test_not_a_carrier_refused(no_file, sender, env, tmp_path, mailroot, bin_dir, value):
    log = tmp_path / "sendmail.log"
    recording_sendmail(bin_dir / "sendmail", log)
    host_file({"sendmail": value})
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    err = _refused_send(sender, {**env, "SENDMAIL_LOG": str(log)}, tmp_path, mailroot, log)
    assert err.splitlines()[-1] == (
        f'session-mail-send: no MTA: "sendmail": {json.dumps(value)} in {SHOWN} is not a '
        f"carrier: {RULE} - NOTHING WAS SENT")


def test_not_a_carrier_refused_unreadable_file(no_file, sender, env, tmp_path, mailroot, bin_dir):
    log = tmp_path / "sendmail.log"
    write_mail_settings(_operator.settings_home(), "{not json\n")
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    err = _refused_send(sender, {**env, "SENDMAIL_LOG": str(log)}, tmp_path, mailroot, log)
    last = err.splitlines()[-1]
    assert last.startswith(f"session-mail-send: no MTA: {SHOWN} could not be read (")
    assert last.endswith("), so the carrier is not known - NOTHING WAS SENT")


def test_an_absolute_path_that_is_not_an_mta_keeps_its_refusal(no_file, sender, env, tmp_path,
                                                                mailroot):
    """A carrier, but not an executable file: today's `no MTA` stands."""
    missing = tmp_path / "no-such-mta"
    err = _refused_send(sender, {**env, "SESSIONMAIL_SENDMAIL": str(missing)}, tmp_path,
                        mailroot, tmp_path / "unused.log")
    assert f"no MTA: {missing} is not an executable file - NOTHING WAS SENT" in err


# ── a settings folder that lists but cannot be searched: refused, never the drop ──
#
# A postfix host names its MTA in the mail settings, and then the folder that
# holds them is mode r--: it lists, but nothing in it can be stat'd. Up to
# CPython 3.13 pathlib's `is_file`/`is_dir` raised PermissionError there; from
# 3.14 they answer False. The layers are listed with those tests (sessiontools'
# `settings.layer_files`, and the template's own test), so on 3.14+ no layer
# was read, the key's default was taken, and the carrier came out as the drop:
# a postfix host's mail left by the drop without a word. `host_sources` stats
# every path those tests look at strictly first, so the read fails, the carrier
# is refused as `unreadable`, and a send sends nothing, on every version.

POSTFIX = {"sendmail": "/usr/sbin/sendmail"}


def _settings_dir() -> Path:
    d = _operator.settings_home() / _settings.DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _layout_host_file(mta: dict) -> Path:
    """The host file sets the MTA; the settings folder is locked."""
    host_file(mta)
    return _settings_dir()


def _layout_dot_d(mta: dict) -> Path:
    """A `.d` entry sets the MTA; the `.d` folder is locked."""
    dot_d = _settings_dir() / f"{_settings.TOOL}.d"
    dot_d.mkdir()
    (dot_d / "10-mta.json").write_text(json.dumps(mta) + "\n")
    return dot_d


def _layout_template(mta: dict) -> Path:
    """The installed template sets the MTA; the settings folder is locked."""
    (_settings_dir() / _settings.INSTALLED_TEMPLATE).write_text(json.dumps(mta) + "\n")
    return _settings_dir()


def _layout_linked(name: str):
    """The host file (or the template) is a link to a file in ANOTHER folder,
    and only that folder is locked: the settings folder stays searchable, so
    the one strict stat that can fail is that file's own."""
    def build(mta: dict) -> Path:
        elsewhere = _operator.settings_home() / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "real.json").write_text(json.dumps({"version": 1, "hooks": False, **mta}))
        (_settings_dir() / name).symlink_to(elsewhere / "real.json")
        return elsewhere
    return build


def _layout_dot_d_linked(mta: dict) -> Path:
    """The `.d` folder is a link to a folder inside ANOTHER folder, and only
    that outer folder is locked: the settings folder and the `.d` folder's
    own entries are fine, so the one strict stat that can fail is the `.d`
    folder's own."""
    outer = _operator.settings_home() / "elsewhere-d"
    inner = outer / "real.d"
    inner.mkdir(parents=True)
    (inner / "10-mta.json").write_text(json.dumps(mta) + "\n")
    (_settings_dir() / f"{_settings.TOOL}.d").symlink_to(inner)
    return outer


LAYOUTS = {
    "host-file": _layout_host_file,
    "dot-d-entry": _layout_dot_d,
    "template": _layout_template,
    "host-file-linked": _layout_linked(f"{_settings.TOOL}.json"),
    "template-linked": _layout_linked(_settings.INSTALLED_TEMPLATE),
    "dot-d-linked": _layout_dot_d_linked,
}


@contextmanager
def unsearchable(folder: Path):
    """FOLDER mode 0o444 for the block: listable, not searchable."""
    folder.chmod(0o444)
    try:
        if os.access(folder, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        yield folder
    finally:
        folder.chmod(0o755)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_carrier_refuses_a_settings_folder_that_cannot_be_searched(no_file, layout):
    locked = LAYOUTS[layout](POSTFIX)
    searchable = _settings.carrier({})
    assert (searchable.kind, searchable.path) == ("mta", "/usr/sbin/sendmail")   # the layout works
    with unsearchable(locked):
        assert os.listdir(locked)                          # it lists
        c = _settings.carrier({})
    assert (c.kind, c.path, c.source) == ("refused", None, "unreadable"), c
    assert c.why.startswith(f"{SHOWN} could not be read (")
    assert "Permission denied" in c.why
    assert c.why.endswith("), so the carrier is not known")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_host_sources_raises_when_a_layer_cannot_be_stat(no_file, layout):
    """The reader under the carrier: a PermissionError, never a shorter list."""
    locked = LAYOUTS[layout](POSTFIX)
    with unsearchable(locked), pytest.raises(PermissionError):
        _operator.host_sources(_settings.TOOL)
    assert _operator.host_sources(_settings.TOOL)          # searchable again: read


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_send_refuses_when_the_settings_folder_cannot_be_searched(
        no_file, sender, env, tmp_path, mailroot, bin_dir, layout):
    """The send path's carrier, end to end: exit 3, nothing minted, nothing
    dropped, no MTA run. The MTA named is a recording fake, so a regression
    can never start this host's real sendmail."""
    log = tmp_path / "sendmail.log"
    mta = recording_sendmail(bin_dir / "sendmail", log)
    locked = LAYOUTS[layout]({"sendmail": str(mta)})
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    with unsearchable(locked):
        err = _refused_send(sender, {**env, "SENDMAIL_LOG": str(log)}, tmp_path, mailroot, log)
    last = err.splitlines()[-1]
    assert last.startswith(f"session-mail-send: no MTA: {SHOWN} could not be read ("), err
    assert last.endswith("), so the carrier is not known - NOTHING WAS SENT")


# ── 4.5: the bash oracle only under an MTA ───────────────────────────────────

@pytest.fixture
def tripwire(env, tmp_path, bin_dir, monkeypatch):
    """`message send`'s environment with the bash oracle NAMED: a tripwire
    that logs when it runs."""
    tripped = tmp_path / "tripwire.log"
    wire = bin_dir / "session-mail-send"
    write_fake(wire, TRIPWIRE.format(python=sys.executable))
    for var in ("CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB",
                "SESSIONMAIL_REQUIRE_SIGNATURE", "SESSIONMAIL_SENDMAIL"):
        monkeypatch.delenv(var, raising=False)
    for k, v in {**env, "TRIPWIRE_LOG": str(tripped), "TM_AGENT_ADDRESS": "beta",
                 "SESSIONMAIL_SEND_ORACLE": str(wire)}.items():
        if k != "SESSIONMAIL_SENDMAIL":
            monkeypatch.setenv(k, v)
    return tripped


def _message_send(tmp_path, subject):
    return CliRunner().invoke(cli, ["send", "alpha", "-s", subject, "-f", body_file(tmp_path)])


def test_bash_only_under_mta(no_file, tripwire, tmp_path, mailroot):
    # an MTA, from the file: the named oracle IS started (the tripwire works)
    host_file({"sendmail": str(tmp_path / "an-mta")})
    _message_send(tmp_path, "via the oracle")
    assert tripwire.exists()
    tripwire.unlink()
    # the drop by DEFAULT (no variable, no key): never the bash
    host_file(None)
    r = _message_send(tmp_path, "via the drop")
    assert r.exit_code == 0, r.output
    assert not tripwire.exists()
    assert "the mail drop (the bash cannot drop)" in r.output
    assert set(landed(mailroot)) >= {"alpha"}
    # a refused carrier: never the bash, and the package's sender refuses it
    host_file({"sendmail": "sendmail"})
    r = _message_send(tmp_path, "refused")
    assert r.exit_code == EX_MTA, r.output
    assert not tripwire.exists()
    assert "is not a carrier" in r.output


# ── setup refuses its plan on a refused carrier ──────────────────────────────

def run_setup(env):
    out, err = io.StringIO(), io.StringIO()
    rc = _setup.main(["--dry-run"], env=env, out=out, err=err, tty=False)
    return rc, out.getvalue(), err.getvalue()


def test_setup_refuses_invalid_carrier(no_file, env):
    env = {**env, "SESSIONMAIL_SENDMAIL": "sendmail"}
    rc, out, err = run_setup(env)
    assert rc == 2, (out, err)
    assert err == (f"message setup: REFUSED - $SESSIONMAIL_SENDMAIL is \"sendmail\", which is "
                   f"not a carrier: {RULE}. Nothing was applied.\n")
    assert out == ""


def test_setup_reads_the_carrier_from_the_file(no_file, env):
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    steps = _setup.carrier_steps(env)
    assert steps[0].id == "carrier"
    assert steps[0].check()[1].startswith("the mail drop")
    host_file({"sendmail": "sendmail"})
    rc, _out, err = run_setup(env)
    assert rc == 2 and f'"sendmail": "sendmail" in {SHOWN} is not a carrier' in err


# ── 5: postfix maps are WRITTEN only under an MTA carrier (D11, D12) ─────────

#: A generator that records each run and, in WRITE mode, touches a marker:
#: the map file a real one would write. Its print modes write nothing; its
#: `--print` lists one box, `alpha`, as `local@host -> agents/alpha/`.
MAP_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ["MAP_GEN_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")
if "--capabilities" in args:
    print("no-fqdn-v1")
elif "--print-valias" in args:
    pass
elif "--print" in args:
    host = args[args.index("--host") + 1] if "--host" in args else "host"
    print(f"alpha@{{host}}\\tagents/alpha/")
else:
    open(os.environ["MAP_GEN_WROTE"], "a").write(" ".join(args) + "\\n")
"""

MTA_CARRIER = "/usr/sbin/sendmail"          # named, never run


@pytest.fixture
def maps_world(tmp_path, no_file):
    """An agents root with one box, a recording generator pinned, and the
    env every writer is handed. NAMED keys only, never a copy of the
    runner's environment."""
    agents = tmp_path / "mail" / "agents"
    for leaf in ("cur", "new", "tmp"):
        (agents / "alpha" / leaf).mkdir(parents=True)
    gen = write_fake(tmp_path / "map-gen", MAP_GEN)
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", ""),
           "SESSION_MAIL_ROOT": str(agents), "SESSIONMAIL_ROOT": str(agents),
           "SESSIONMAIL_HOST": "host", "SESSIONMAIL_VMAILBOX_GEN": str(gen),
           "MAGICDNS_SUFFIX": "example.invalid",
           "MAP_GEN_LOG": str(tmp_path / "gen.log"),
           "MAP_GEN_WROTE": str(tmp_path / "maps-written")}
    return agents, env, tmp_path / "maps-written", tmp_path / "gen.log"


def _every_writer(agents: Path, env: dict[str, str]) -> list[tuple[str, bool, str]]:
    """(who, ok, line) for each verb that regenerated maps before: a box's
    birth, a subscription's rebuild, the archive's regenerate, and setup's
    maps step (a box made). Each is handed ENV, and the process
    environment is made to match it, since some read `os.environ`."""
    from sessionmail import _archive, _seat, _subscribe
    out = []
    ok, line = _seat._run_generator(agents, dict(env), ())
    out.append(("birth", ok, line))
    ok, line = _subscribe.rebuild(dict(env))
    out.append(("subscription", ok, line))
    ctx = _archive.Ctx(root=agents, archive=agents.parent / "archive", env=dict(env))
    out.append(("archive", True, ctx.regen()))
    made_box = _setup.Step("box:alpha", "", check=lambda: (_setup.TODO, "make it"))
    step = _setup.regen_step(agents, dict(env), [made_box], ["alpha"])
    try:
        out.append(("setup", True, step.apply() if step.apply else step.check()[1]))
    except _setup.Left as exc:
        out.append(("setup", False, str(exc)))
    return out


def test_drop_writes_no_map(maps_world, monkeypatch):
    """Under the drop no verb WRITES a map, and each says why; the drop's own
    print-only run still happens on every send (design D11)."""
    agents, env, wrote, log = maps_world
    env = {**env, "SESSIONMAIL_SENDMAIL": "drop"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for who, ok, line in _every_writer(agents, env):
        assert ok, (who, line)
        assert "the carrier is the mail drop" in line, (who, line)
    assert not wrote.exists(), wrote.read_text()
    assert not log.exists() or "--print" not in log.read_text().replace("--print-valias", "")
    # the drop's own resolution: the generator runs, in print mode only
    maps = _drop.prepare(env)
    assert maps.mailbox == {"alpha@host": "agents/alpha/"}
    runs = log.read_text().splitlines()
    assert any(r.split()[-1:] == ["--print"] or " --print" in f" {r} " for r in runs), runs
    assert not wrote.exists()


def test_drop_is_the_default_for_the_writers(maps_world, monkeypatch):
    """With no variable and no settings file, the carrier is the drop, so
    the writers write nothing either."""
    agents, env, wrote, _log = maps_world
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SESSIONMAIL_SENDMAIL", raising=False)
    for who, ok, line in _every_writer(agents, env):
        assert ok and "the carrier is the mail drop" in line, (who, line)
    assert not wrote.exists()


def test_postfix_writers_unchanged(maps_world, monkeypatch):
    """Under an MTA carrier every verb that regenerated maps before still
    does: the generator runs in write mode for each of them."""
    agents, env, wrote, _log = maps_world
    env = {**env, "SESSIONMAIL_SENDMAIL": MTA_CARRIER}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(_setup, "live_maps", lambda: True)
    results = _every_writer(agents, env)
    for who, ok, line in results:
        assert ok and "generator ran" in line, (who, line)
    runs = wrote.read_text().splitlines()
    assert len(runs) == 4, runs
    assert sum("--valias-only" in r for r in runs) == 1      # the subscription's rebuild


def test_a_refused_carrier_writes_no_map_and_says_why(maps_world, monkeypatch):
    from sessionmail import _seat
    agents, env, wrote, log = maps_world
    env = {**env, "SESSIONMAIL_SENDMAIL": "sendmail"}
    ok, line = _seat._run_generator(agents, env, ())
    assert ok is False
    assert line == ("generator not run: $SESSIONMAIL_SENDMAIL is \"sendmail\", which is "
                    f"not a carrier: {RULE}")
    assert not wrote.exists() and not log.exists()


def test_setup_no_maps_writes_none(maps_world, monkeypatch):
    """Under an MTA carrier, on a host with no postfix maps, setup's
    maps step writes nothing and is a MANUAL line naming --write-maps; with
    --write-maps it runs the one writer. Under the drop it never asks."""
    agents, env, wrote, _log = maps_world
    env = {**env, "SESSIONMAIL_SENDMAIL": MTA_CARRIER}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(_setup, "live_maps", lambda: False)
    made_box = _setup.Step("box:alpha", "", check=lambda: (_setup.TODO, "make it"))
    step = _setup.regen_step(agents, env, [made_box], ["alpha"])
    assert step.manual and "--write-maps" in step.what
    assert step.apply is None
    assert not wrote.exists()
    asked = _setup.regen_step(agents, env, [], [], write_maps=True)
    state, _why = asked.check()
    assert state == _setup.TODO
    assert "generator ran" in asked.apply()
    assert wrote.read_text().count("\n") == 1
    # the flag is accepted by the verb, and only by name
    assert _setup.parser().parse_args(["--write-maps"]).write_maps is True
    # under the drop the step never asks postfix and never writes
    monkeypatch.setattr(_setup, "live_maps", lambda: pytest.fail("asked postfix under the drop"))
    drop = _setup.regen_step(agents, {**env, "SESSIONMAIL_SENDMAIL": "drop"}, [made_box],
                             ["alpha"], write_maps=True)
    assert drop.check()[0] == _setup.DONE


def test_live_maps_reads_postconf(monkeypatch, tmp_path):
    from sessionmail import _seat
    mapfile = tmp_path / "vmailbox"
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: [f"hash:{mapfile}"])
    assert _setup.live_maps() is False
    (tmp_path / "vmailbox.db").write_text("")
    assert _setup.live_maps() is True
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)
    assert _setup.live_maps() is False
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: [])
    assert _setup.live_maps() is False


# ── 5.1a: archive restore follows the carrier ────────────────────────────────

@pytest.fixture
def archived(tmp_path, no_file, monkeypatch):
    """A box `alpha` in archive/, nothing in agents/, no generator anywhere
    but the package's copy: a bare Mac. NAMED keys only."""
    import shutil as _shutil
    if _shutil.which("bash") is None:
        pytest.skip("the generator is a bash script")
    mail = tmp_path / "mail"
    agents = mail / "agents"
    agents.mkdir(parents=True)
    for leaf in ("cur", "new", "tmp"):
        (mail / "archive" / "alpha" / leaf).mkdir(parents=True)
    (mail / "archive" / "alpha" / "cur" / "1.M1P1.host:2,S").write_text("Subject: kept\n\nx\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"HOME": str(tmp_path), "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
           "SESSION_MAIL_ROOT": str(agents), "SESSIONMAIL_ROOT": str(agents),
           "SESSIONMAIL_HOST": "host"}
    for var in ("SESSIONMAIL_VMAILBOX_GEN", "SESSIONMAIL_SENDMAIL", "MAGICDNS_SUFFIX",
                "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return mail, env


def test_archive_restore_under_drop(archived, monkeypatch):
    """Under the drop (the default), with no map-writing generator on PATH or
    in the mail root, restore does not refuse: it renames the box back, and
    verifies its row by the drop's own printed map, never a compiled
    postfix map (none exists; postconf is never asked)."""
    from sessionmail import _archive, _seat
    mail, env = archived
    monkeypatch.setattr(_seat, "_postfix_maps",
                        lambda p: pytest.fail(f"postconf was asked for {p} under the drop"))
    out, err = [], []
    ctx = _archive.Ctx(root=mail / "agents", archive=mail / "archive", env=dict(env),
                       out=out.append, err=err.append)
    code = _archive.unarchive(ctx, "alpha", merge_ok=False, do=True)
    assert code == 0, (out, err)
    assert (mail / "agents" / "alpha" / "cur" / "1.M1P1.host:2,S").is_file()
    assert any(line.startswith("UNARCHIVED alpha") for line in out), out
    assert not any("UNROUTED" in line for line in err), err


def test_archive_restore_under_an_mta_still_needs_a_writer(archived, monkeypatch):
    """Under an MTA carrier the precheck is unchanged: no map-writing
    generator, so restore refuses before it moves anything."""
    from sessionmail import _archive
    mail, env = archived
    env = {**env, "SESSIONMAIL_SENDMAIL": MTA_CARRIER}
    out, err = [], []
    ctx = _archive.Ctx(root=mail / "agents", archive=mail / "archive", env=dict(env),
                       out=out.append, err=err.append)
    assert _archive.unarchive(ctx, "alpha", merge_ok=False, do=True) == 3
    assert "no vmailbox-gen to regenerate with" in " ".join(err)
    assert (mail / "archive" / "alpha").is_dir() and not (mail / "agents" / "alpha").exists()


# ── archive under the drop: a host with no postfix can archive a box ─────────

DEAD = "a0000000-1111-2222-3333-444455556666"      # a dead, quiet session box

#: A print-only generator for the drop: `--print` lists every box under the
#: agents root, `--print-valias` prints $FAKE_VALIAS (or refuses, exit 3, when
#: $FAKE_VALIAS_REFUSE is set). Any other run is a WRITE and is recorded.
PRINT_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
if "--capabilities" in args:
    print("no-fqdn-v1")
elif "--print-valias" in args:
    if os.environ.get("FAKE_VALIAS_REFUSE"):
        sys.exit(3)
    sys.stdout.write(os.environ.get("FAKE_VALIAS", ""))
elif "--print" in args:
    root = args[args.index("--root") + 1]
    host = args[args.index("--host") + 1]
    for name in sorted(os.listdir(os.path.join(root, "agents"))):
        print(f"{{name}}@{{host}}\\tagents/{{name}}/")
else:
    open(os.environ["MAP_GEN_WROTE"], "a").write(" ".join(args) + "\\n")
"""


@pytest.fixture
def dead_world(tmp_path, no_file, monkeypatch):
    """One dead session box, quiet for 30 days, in a mail root with no
    postfix and no map-writing generator (PATH holds only what the packaged
    generator's print modes need). postconf is never asked: the test fails if
    it is. NAMED keys only."""
    import shutil as _shutil
    from sessionmail import _seat
    for tool in ("bash", "jq"):
        if _shutil.which(tool) is None:
            pytest.skip(f"the packaged generator needs {tool}")
    mail = tmp_path / "mail"
    agents = mail / "agents"
    box = agents / DEAD
    old = 1_700_000_000.0
    for leaf in ("cur", "new", "tmp"):
        (box / leaf).mkdir(parents=True)
    mail_file = box / "cur" / "1.M1P1.host:2,S"
    mail_file.write_text("Message-ID: <old@test>\nSubject: old\n\nx\n")
    for p in (mail_file, *(box / leaf for leaf in ("cur", "new", "tmp"))):
        os.utime(p, (old, old))
    cars = tmp_path / "sidecars"
    cars.mkdir()
    pid = 4_000_000
    while _seat._pid_alive(pid):
        pid += 1
    (cars / "dead.json").write_text(json.dumps({"pid": pid, "sessionId": DEAD}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "jq").symlink_to(_shutil.which("jq"))
    env = {"HOME": str(tmp_path), "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
           "SESSION_MAIL_ROOT": str(agents), "SESSIONMAIL_ROOT": str(agents),
           "SESSIONMAIL_HOST": "host", "SESSION_SIDECAR_DIR": str(cars),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
           "MAP_GEN_WROTE": str(tmp_path / "maps-written")}
    for var in ("SESSIONMAIL_VMAILBOX_GEN", "SESSIONMAIL_SENDMAIL", "MAGICDNS_SUFFIX",
                "CLAUDE_CODE_SESSION_ID", "NOTMUCH_CONFIG", "FAKE_VALIAS", "FAKE_VALIAS_REFUSE"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(_seat, "_postfix_maps",
                        lambda p: pytest.fail(f"postconf was asked for {p} under the drop"))
    return mail, env


def archive_cli(*args: str):
    return CliRunner().invoke(cli, ["box", "archive", *args], prog_name="message")


def test_archive_under_drop(dead_world):
    """Under the drop (the default), with no map-writing generator and no
    postfix, `message box archive --apply` archives a dead box: no postfix
    pre-flight refuses it, and its row is verified gone from the drop's own
    printed map."""
    mail, _env = dead_world
    res = archive_cli("--apply", "--days", "7")
    assert res.exit_code == 0, (res.stdout, res.stderr)
    assert "preflight: ✗" not in res.stdout + res.stderr
    assert (mail / "archive" / DEAD / "cur" / "1.M1P1.host:2,S").is_file()
    assert not (mail / "agents" / DEAD).exists()
    assert f"ARCHIVED {DEAD}  (1 files; row verified gone)" in res.stdout
    assert "the carrier is the mail drop" in res.stdout


def test_archive_dry_run_under_drop_lists_the_box(dead_world):
    """The listing reads the drop's maps too: the box is a CANDIDATE, with
    its alias criterion judged, and nothing is refused or moved."""
    mail, _env = dead_world
    res = archive_cli()
    assert res.exit_code == 0, (res.stdout, res.stderr)
    assert "preflight: ✗" not in res.stdout
    assert any(line.startswith(f"CANDIDATE {DEAD}  (a) no live sidecar  "
                               "(b) no alias row, now or next") for line in res.stdout.splitlines())
    assert (mail / "agents" / DEAD).is_dir() and not (mail / "archive").exists()


def test_archive_under_drop_keeps_a_box_the_alias_print_names(dead_world, tmp_path, monkeypatch):
    """Criterion (b) under the drop reads the alias map the drop prints: a
    row naming the box keeps it, and nothing is written."""
    mail, _env = dead_world
    gen = write_fake(tmp_path / "print-gen", PRINT_GEN)
    monkeypatch.setenv("SESSIONMAIL_VMAILBOX_GEN", str(gen))
    monkeypatch.setenv("FAKE_VALIAS", f"alpha@host\talpha@host, {DEAD}@host\n")
    res = archive_cli("--apply", "--days", "7")
    assert res.exit_code == 0, (res.stdout, res.stderr)
    assert res.stdout.strip().splitlines()[-1].startswith(
        "-- 0 candidates of 1 boxes at --days 7 (0 dirs, 0 files); kept: 0 live, 1 alias row"), \
        res.stdout
    assert (mail / "agents" / DEAD).is_dir() and not (mail / "archive" / DEAD).exists()
    assert not (tmp_path / "maps-written").exists()


def test_archive_under_drop_refuses_when_the_alias_print_refuses(dead_world, tmp_path, monkeypatch):
    """The drop's send takes a refused alias print as an empty map; the
    archive never does. Unreadable alias rows refuse new moves (P4), and
    nothing moves."""
    mail, _env = dead_world
    gen = write_fake(tmp_path / "print-gen", PRINT_GEN)
    monkeypatch.setenv("SESSIONMAIL_VMAILBOX_GEN", str(gen))
    monkeypatch.setenv("FAKE_VALIAS_REFUSE", "1")
    res = archive_cli("--apply", "--days", "7")
    assert res.exit_code == 3, (res.stdout, res.stderr)
    assert "P4 (b) alias maps unreadable" in res.stderr
    assert (mail / "agents" / DEAD).is_dir() and not (mail / "archive" / DEAD).exists()


def test_archive_under_an_mta_preflight_unchanged(dead_world, monkeypatch):
    """Under an MTA carrier every pre-flight step is unchanged: with no
    postfix maps and no map-writing generator, --apply refuses on P4, P5 and
    P6 and moves nothing."""
    from sessionmail import _seat
    mail, _env = dead_world
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", MTA_CARRIER)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)   # postconf cannot say
    res = archive_cli("--apply", "--days", "7")
    assert res.exit_code == 3, (res.stdout, res.stderr)
    for step in ("P4 (b) alias maps unreadable", "P5 no vmailbox-gen", "P6 the mailbox map cannot be read"):
        assert step in res.stderr, (step, res.stderr)
    assert (mail / "agents" / DEAD).is_dir() and not (mail / "archive").exists()
