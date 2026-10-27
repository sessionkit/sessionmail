"""SM-910: the agents root's two variables, read in ONE order by every side.

`mail.schema.json` (`root`, x-env) says `SESSIONMAIL_ROOT`, set and not
empty, wins over every layer. The drop read it, then the older
`SESSION_MAIL_ROOT`; the readers (`_maildir.root`: `message path`, `list`,
`read`, the pager) read only the older one. So with only the canonical
spelling set, `message send` delivered into a box `message path` said was
not there, and with both set the two sides picked different trees
(measured: the task's repro-001, sessionmail d607ecf).

The order now lives in one function, `_maildir.env_root`, that
`_maildir.root` and `_drop.mail_root` both call. The bundled scripts cannot
import the package, so `mhs/mail-box.lib.sh` and `mhs/session-mail-wake`
carry the same order inline, and the agreement test below pins all three to
one answer.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _drop, _maildir, _seat, _setup
from sessionmail.message import cli
from test_mail_drop import FAKE_GEN, HOST

MHS = Path(_maildir.__file__).resolve().parent / "mhs"
LIB = MHS / "mail-box.lib.sh"
WAKE = MHS / "session-mail-wake"
NEW, OLD = "SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT"     # the canonical spelling, the older one
BOX = "present"


def mkbox(d: Path) -> Path:
    for s in ("new", "cur", "tmp"):
        (d / s).mkdir(parents=True, exist_ok=True)
    return d


def test_the_canonical_spelling_is_the_schemas_variable():
    import json
    schema = json.loads((Path(_maildir.__file__).parent / "mail.schema.json").read_text())
    assert schema["properties"]["root"]["x-env"] == NEW == _maildir.ROOT_VAR
    assert _maildir.LEGACY_ROOT_VAR == OLD
    assert _maildir.ROOT_VARS == (NEW, OLD)


# ── the one order ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,want", [
    ({}, None),
    ({OLD: "/old/agents"}, "/old/agents"),
    ({NEW: "/new/agents"}, "/new/agents"),
    ({NEW: "/new/agents", OLD: "/old/agents"}, "/new/agents"),
    ({NEW: "", OLD: "/old/agents"}, "/old/agents"),      # set but empty: not set
    ({NEW: "", OLD: ""}, None),
])
def test_env_root_reads_the_canonical_spelling_then_the_older_one(given, want):
    got = _maildir.env_root(given)
    assert got == (None if want is None else Path(want))


def test_env_root_expands_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _maildir.env_root({NEW: "~/m/agents"}) == tmp_path / "m" / "agents"


@pytest.mark.parametrize("given", [
    {OLD: "/old/agents"},
    {NEW: "/new/agents"},
    {NEW: "/new/agents", OLD: "/old/agents"},
    {NEW: "", OLD: "/old/agents"},
])
def test_the_readers_and_the_drop_pick_the_same_tree(given):
    """The drop's mail root is the parent of the readers' agents root, for
    every way of setting the two variables (the drop's own MAIL_ROOT step
    is after both, so it never decides here)."""
    env = {**given, "MAIL_ROOT": "/mail-root-is-never-reached"}
    assert _drop.mail_root(env) / "agents" == _maildir.root(env)


def test_a_variable_beats_the_settings_files_root_for_the_readers(monkeypatch, tmp_path):
    from sessionmail import _settings
    monkeypatch.setattr(_settings, "file_path",
                        lambda key, env=None: str(tmp_path / "file") if key == "root" else None)
    assert _maildir.root({}) == tmp_path / "file" / "agents"
    assert _maildir.root({NEW: str(tmp_path / "new")}) == tmp_path / "new"
    assert _maildir.root({OLD: str(tmp_path / "old")}) == tmp_path / "old"


def test_setup_no_longer_refuses_two_spellings_naming_two_places(tmp_path):
    """Both sides take SESSIONMAIL_ROOT, so `_setup.roots` sees one tree."""
    env = {NEW: str(tmp_path / "new" / "agents"), OLD: str(tmp_path / "old" / "agents")}
    assert _setup.roots(env) == tmp_path / "new" / "agents"


def test_setup_still_refuses_mail_root_that_only_the_drop_reads(monkeypatch, tmp_path):
    """MAIL_ROOT is the generator's variable and the drop's alone: SM-910 did
    not widen the readers to it, and the refusal that guards it stands."""
    from sessionmail import _settings
    monkeypatch.setattr(_settings, "file_path", lambda key, env=None: None)
    with pytest.raises(_setup.PlanRefused) as err:
        _setup.roots({"MAIL_ROOT": str(tmp_path / "mr")})
    assert str(tmp_path / "mr" / "agents") in str(err.value)
    assert "$MAIL_ROOT" in str(err.value) and f"${NEW}" in str(err.value)


# ── message send, then path / list / read (the card's measurement) ───────────

CASES = {
    "only-canonical": {NEW: "new"},
    "only-legacy": {OLD: "old"},
    "both": {NEW: "new", OLD: "old"},
}


@pytest.fixture
def drop_env(tmp_path, monkeypatch):
    """A drop-mode host whose only root is whatever the case sets: the
    default ~/.mail/agents under a scratch HOME is absent, so a reader that
    ignores the case's variable finds nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "vmailbox-gen", FAKE_GEN.format(python=sys.executable))
    for var in (NEW, OLD, "MAIL_ROOT", "CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK",
                "CLAUDE_SESSION_PUB", "SESSIONMAIL_REQUIRE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in {
        "HOME": str(tmp_path / "home"),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(bin_dir / "vmailbox-gen"),
        "FAKE_VMAILBOX": f"{BOX}@{HOST}\tagents/{BOX}/\nbeta@{HOST}\tagents/beta/\n",
        "FAKE_VALIAS": "",
        "TM_AGENT_ADDRESS": "beta",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(_seat, "_postfix_maps", lambda p: None)     # no postfix here
    body = tmp_path / "body.md"
    body.write_text("hello\n")
    return tmp_path, body


@pytest.mark.parametrize("case", sorted(CASES))
def test_path_list_and_read_find_the_box_send_delivered_to(case, drop_env, monkeypatch):
    tmp_path, body = drop_env
    roots = {}
    for var, name in CASES[case].items():
        roots[var] = tmp_path / name / "agents"
        mkbox(roots[var] / BOX)
        mkbox(roots[var] / "beta")
        monkeypatch.setenv(var, str(roots[var]))
    want = roots.get(NEW, roots.get(OLD))          # SESSIONMAIL_ROOT wins when both are set
    runner = CliRunner()

    sent = runner.invoke(cli, ["send", BOX, "-s", f"root {case}", "-f", str(body)])
    assert sent.exit_code == 0, sent.output
    assert len(list((want / BOX / "new").iterdir())) == 1
    for other in set(roots.values()) - {want}:
        assert not list((other / BOX / "new").iterdir()), "the drop wrote the losing tree"

    path = runner.invoke(cli, ["path", BOX])
    assert path.exit_code == 0, path.output
    assert path.stdout == f"{want / BOX}\n"

    listed = runner.invoke(cli, ["list", BOX])
    assert listed.exit_code == 0, listed.output
    assert f"root {case}" in listed.stdout

    read = runner.invoke(cli, ["read", BOX, "1", "--peek"])
    assert read.exit_code == 0, read.output
    assert f"root {case}" in read.stdout


# ── the rails: bash and the wake script carry the order inline ───────────────

def _wake_module():
    loader = importlib.machinery.SourceFileLoader("sm910_wake", str(WAKE))
    spec = importlib.util.spec_from_loader("sm910_wake", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


AGREE = [
    {},
    {OLD: "/srv/old/agents"},
    {NEW: "/srv/new/agents"},
    {NEW: "/srv/new/agents", OLD: "/srv/old/agents"},
    {NEW: "", OLD: "/srv/old/agents"},
    {NEW: "", OLD: ""},
    {NEW: "~/tilde/agents"},
    {OLD: "~/old-tilde/agents"},
    {NEW: "/srv/with space/agents"},
]


@pytest.mark.parametrize("given", AGREE, ids=lambda g: ",".join(f"{k}={v!r}" for k, v in g.items()) or "none")
def test_bash_wake_and_python_name_the_same_root(given, tmp_path, monkeypatch):
    """The agreement test: for every way of setting the two variables, the
    bash rail, the wake script and the package name ONE agents root (no
    settings file here: the rails do not read it, by design)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for var in (NEW, OLD):
        monkeypatch.delenv(var, raising=False)
    python = str(_maildir.env_root(given) or Path(_maildir.DEFAULT_ROOT).expanduser())
    shell = subprocess.run(["bash", str(LIB), "--env-root"], capture_output=True, text=True,
                           env={"HOME": str(home), "PATH": "/usr/bin:/bin", **given}, check=True)
    wake = _wake_module().env_root({"HOME": str(home), **given})
    assert shell.stdout == f"{python}\n"
    assert wake == python


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_bash_rail_resolves_a_box_under_the_winning_root(case, tmp_path):
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}
    roots = {}
    for var, name in CASES[case].items():
        roots[var] = tmp_path / name / "agents"
        mkbox(roots[var] / BOX)
        env[var] = str(roots[var])
    want = roots.get(NEW, roots.get(OLD))
    done = subprocess.run(["bash", str(LIB), BOX], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{want / BOX}\n"


@pytest.mark.parametrize("case", sorted(CASES))
def test_wake_reads_the_box_under_the_winning_root(case, tmp_path):
    """`memory wake BOX` under each case: the banner names the box, and it
    is never refused as absent. A decoy under the losing spelling holds no
    box, so a wake that read the wrong one would refuse."""
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin",
           "SESSIONMAIL_PYTHON": sys.executable,
           "NOTMUCH_CONFIG": str(tmp_path / "no-notmuch-config")}
    for var, name in CASES[case].items():
        env[var] = str(tmp_path / name / "agents")
    winner = Path(env.get(NEW) or env[OLD])
    mkbox(winner / BOX)
    done = subprocess.run([sys.executable, str(WAKE), BOX, "--dry-run"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert "no such mailbox" not in done.stderr, done.stderr
    assert f"memory wake: box {BOX} " in done.stderr, done.stderr
