"""SM-590 do-002: seat-init mints a seat key and cert into a NAMED registry.

Design §1 K1 and §4 Mint: the adapter signs as desk.uma, and its cert
matches exactly. Hermetic: a scratch
registry (a tmp git repo), a scratch $SESSIONMAIL_HOME, a scratch ssh-agent and
a fake sendmail. Mail is signed by the sender's OWN `sign()` (design §9 V6) and
judged by the verifier this package ships. No live key, agent or registry is
touched.

SM-715 do-001 moved the script verbatim into the package, and do-002 made it a verb:
these tests RUN it as `message seat init`, through the test venv's own `message`
(design §6), and read the PACKAGED file, src/sessionmail/seat_init.py. Never in-process:
the mint sets the process umask. The old path, scripts/seat-init, is a shim:
tests/test_seat_package.py smokes it.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

import hostile_git
from test_sender import EX_OK, EX_REFUSED, bin_dir, env, fake_sendmail, headers_of, root, sender, verifier  # noqa: F401

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "sessionmail" / "seat_init.py"
#: The test venv's `message` (SM-715 do-002): every run goes through the installed verb.
MESSAGE = Path(sys.executable).parent / "message"
SEAT_INIT = (str(MESSAGE), "seat", "init")
NAMESPACES = ("session-birth@example.com,session-bless@example.com,"
              "session-mail@example.com,session-death@example.com,git")
TOOLS = ("ssh-keygen", "ssh-agent", "ssh-add", "git")


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture
def tools():
    found = {t: shutil.which(t) for t in TOOLS}
    for t, where in found.items():
        if where is None:
            pytest.skip(f"{t} not on this machine - seat-init cannot be exercised")
    return found


@pytest.fixture
def git_env(tmp_path, tools):
    """No global or system git config: the scratch repo answers to nothing of the host's."""
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "SESSIONMAIL_HOME": str(tmp_path / "smhome")}


def keypub(tmp_path: Path, name: str) -> str:
    k = tmp_path / "other-keys" / name
    k.parent.mkdir(exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
    return " ".join(Path(f"{k}.pub").read_text().split()[:2])


@pytest.fixture
def registry(tmp_path, git_env):
    """A scratch claude-agents shape: <repo>/registry/{allowed_signers,certs/}. The OLDER
    @session line carries a different namespaces= list, so 'the newest' is measurable."""
    repo = tmp_path / "agents"
    reg = repo / "registry"
    (reg / "certs").mkdir(parents=True)
    (reg / "allowed_signers").write_text(
        f'11111111-1111-1111-1111-111111111111@session namespaces="old@x" {keypub(tmp_path, "a")}\n'
        f'22222222-2222-2222-2222-222222222222@session namespaces="{NAMESPACES}" '
        f'{keypub(tmp_path, "b")}\n')
    (reg / "certs" / ".keep").write_text("")
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=git_env)
    subprocess.run([*g, "add", "-A"], check=True, env=git_env)
    subprocess.run([*g, "commit", "-q", "-m", "seed"], check=True, env=git_env)
    return reg


def seat_init(git_env, *args):
    done = subprocess.run([*SEAT_INIT, *map(str, args)],
                          capture_output=True, text=True, env=git_env)
    return done.returncode, done.stdout, done.stderr


def head(reg: Path, git_env) -> str:
    return subprocess.run(["git", "-C", str(reg), "rev-parse", "HEAD"], capture_output=True,
                          text=True, env=git_env, check=True).stdout.strip()


def nothing_written(git_env, reg: Path | None = None, before: str | None = None):
    assert not (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys").exists() or not any(
        (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys").rglob("id_ed25519")), "a key was written"
    if reg is not None:
        assert head(reg, git_env) == before, "the registry moved"


# ── refusals: nothing written ────────────────────────────────────────────────

def test_registry_is_required_there_is_no_default(git_env, tools):
    rc, _, err = seat_init(git_env, "desk.uma")
    assert rc == 2 and "--registry" in err
    assert not Path(git_env["SESSIONMAIL_HOME"]).exists()


def test_a_missing_registry_is_refused(git_env, tmp_path, tools):
    rc, _, err = seat_init(git_env, "--registry", tmp_path / "nowhere", "desk.uma")
    assert rc == EX_REFUSED and "not a directory" in err and "Nothing was written" in err
    nothing_written(git_env)


def test_a_registry_that_is_not_git_is_refused(git_env, tmp_path, tools):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "allowed_signers").write_text(f'x@session namespaces="{NAMESPACES}" {keypub(tmp_path, "c")}\n')
    rc, _, err = seat_init(git_env, "--registry", plain, "desk.uma")
    assert rc == EX_REFUSED and "not inside a git work tree" in err
    nothing_written(git_env)


def test_no_session_namespaces_line_is_refused(git_env, registry):
    (registry / "allowed_signers").write_text("judy@bless ssh-ed25519 AAAA\n")
    subprocess.run(["git", "-C", str(registry), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-am", "no session lines"], check=True, env=git_env)
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "no @session line with namespaces=" in err
    nothing_written(git_env, registry, before)


def test_uncommitted_allowed_signers_is_refused(git_env, registry):
    with open(registry / "allowed_signers", "a") as f:
        f.write("# someone else's edit\n")
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "uncommitted changes" in err
    nothing_written(git_env, registry, before)


# A plus address (heard+airpods) is a seat address now: tests/test_seat_plus.py
# mints one and pins what the '+' does. A malformed one is still refused, here.
@pytest.mark.parametrize("address", ["Desk.uma", "../uma", "desk/uma", ".uma", "", "desk uma",
                                     "uma+", "+desk", "uma++desk", "uma+desk+x", "uma+Desk"])
def test_an_address_that_is_not_a_seat_name_is_refused(git_env, registry, address):
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, address)
    assert rc == EX_REFUSED and "not a seat address" in err
    nothing_written(git_env, registry, before)


def test_an_existing_key_is_never_overwritten(git_env, registry):
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma"
    home.mkdir(parents=True, mode=0o700)
    os.chmod(home.parent, 0o700)
    (home / "id_ed25519").write_bytes(b"the old key")
    os.chmod(home / "id_ed25519", 0o600)
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "never overwrites" in err
    assert (home / "id_ed25519").read_bytes() == b"the old key"
    assert head(registry, git_env) == before


@pytest.mark.parametrize("which", ["seat-keys", "address"])
def test_a_key_dir_with_group_or_world_bits_is_refused(git_env, registry, which):
    keys = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys"
    (keys / "desk.uma").mkdir(parents=True)
    os.chmod(keys, 0o755 if which == "seat-keys" else 0o700)
    os.chmod(keys / "desk.uma", 0o700 if which == "seat-keys" else 0o750)
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "group or world bits" in err
    nothing_written(git_env, registry, before)


def test_a_loose_existing_key_file_is_named_loose(git_env, registry):
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma"
    home.mkdir(parents=True, mode=0o700)
    os.chmod(home.parent, 0o700)
    (home / "seat.pub").write_text("ssh-ed25519 AAAA\n")
    os.chmod(home / "seat.pub", 0o644)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "never overwrites" in err and "loose" in err


def test_a_failed_registry_commit_puts_everything_back(git_env, registry):
    """A pre-commit hook that says no: the line, the cert files and the new key
    all go, so a re-run is not refused as 'existing key'."""
    failing_hook(registry)
    signers = (registry / "allowed_signers").read_bytes()
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == 1 and "registry commit failed" in err and "the registry says no" in err
    assert head(registry, git_env) == before
    assert (registry / "allowed_signers").read_bytes() == signers
    assert sorted(p.name for p in (registry / "certs").iterdir()) == [".keep"]
    assert subprocess.run(["git", "-C", str(registry), "status", "--porcelain"], capture_output=True,
                          text=True, env=git_env).stdout == ""
    assert not (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma").exists()


def failing_hook(registry, say_on="stderr"):
    hook = registry.parent / ".git" / "hooks" / "pre-commit"
    redirect = " >&2" if say_on == "stderr" else ""
    hook.write_text(f"#!/bin/sh\necho 'the registry says no'{redirect}\nexit 1\n")
    os.chmod(hook, 0o755)
    return hook


def test_a_failed_commit_into_a_pre_existing_key_dir_removes_exactly_what_it_wrote(git_env, registry):
    """do-002 review F1: the dir was there before (empty, 0700), so it stays, but
    every file this run wrote goes, the message stays true, and a re-run mints."""
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma"
    home.mkdir(parents=True)
    os.chmod(home.parent, 0o700)
    os.chmod(home, 0o700)
    hook = failing_hook(registry)
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == 1 and "back as they were" in err
    assert home.is_dir() and list(home.iterdir()) == [], "only what this run wrote is removed"
    assert head(registry, git_env) == before
    hook.unlink()
    rc, out, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == 0 and "minted" in out, err


def test_a_commit_refusal_said_on_stdout_is_still_named(git_env, registry):
    """do-002 review F2: a hook that speaks on stdout still gives a reason.
    MEASURED: git itself routes a hook's stdout to stderr, so this passes at
    96e243c too; it pins the behaviour. The fallback (stdout, then the exit
    code) in the script covers a git failure that says nothing on stderr."""
    failing_hook(registry, say_on="stdout")
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == 1 and "registry commit failed: the registry says no" in err


def test_a_leftover_birth_cert_is_refused_never_overwritten(git_env, registry):
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma"
    home.mkdir(parents=True)
    os.chmod(home.parent, 0o700)
    os.chmod(home, 0o700)
    (home / "birth-cert.txt").write_text("someone's cert\n")
    os.chmod(home / "birth-cert.txt", 0o600)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "birth-cert.txt exists" in err
    assert (home / "birth-cert.txt").read_text() == "someone's cert\n"


# ── the mint ─────────────────────────────────────────────────────────────────

@pytest.fixture
def minted(git_env, registry):
    before = head(registry, git_env)
    rc, out, err = seat_init(git_env, "--registry", registry, "--machine", "hq", "desk.uma")
    assert rc == 0, err
    sid = re.search(r"sid\s+: (\S+)", out).group(1)
    home = Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma"
    return dict(out=out, sid=sid, home=home, reg=registry, before=before,
                pub=home / "seat.pub", key=home / "id_ed25519")


def test_the_key_lives_0600_in_0700_and_its_pub_has_no_private_beside_its_stem(minted):
    home = minted["home"]
    assert mode(home) == 0o700 and mode(home.parent) == 0o700
    assert mode(minted["key"]) == 0o600
    assert minted["pub"].is_file()
    assert not (home / "seat").exists(), "a private key beside seat.pub's stem (probe 8c)"
    assert not (home / "id_ed25519.pub").exists(), "the pub is published as seat.pub, once"


def test_the_cert_is_lamp_checks_field_for_field_with_agent_exact_and_kind_seat(minted):
    publine = " ".join(minted["pub"].read_text().split()[:2])
    cert = (minted["reg"] / "certs" / f"{minted['sid']}.cert").read_text().splitlines()
    assert [line.split(":")[0] for line in cert] == [
        "session-birth-v1", "session-id", "pubkey", "born", "agent", "machine", "kind"]
    assert cert[1] == f"session-id: {minted['sid']}" and cert[2] == f"pubkey: {publine}"
    assert re.fullmatch(r"born: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", cert[3])
    assert cert[4:] == ["agent: desk.uma", "machine: hq", "kind: seat"]
    assert (minted["reg"] / "certs" / f"{minted['sid']}.cert.sig").is_file()


def test_the_self_signature_verifies_under_session_birth(minted, git_env):
    publine = " ".join(minted["pub"].read_text().split()[:2])
    allowed = minted["home"].parent.parent / "allowed.tmp"
    allowed.write_text(f"{minted['sid']}@session {publine}\n")
    cert = minted["reg"] / "certs" / f"{minted['sid']}.cert"
    with open(cert, "rb") as data:
        ok = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I",
                             f"{minted['sid']}@session", "-n", "session-birth@example.com",
                             "-s", f"{cert}.sig"], stdin=data, capture_output=True)
    assert ok.returncode == 0, ok.stderr


def test_allowed_signers_gains_one_line_with_the_newest_namespaces(minted):
    publine = " ".join(minted["pub"].read_text().split()[:2])
    lines = (minted["reg"] / "allowed_signers").read_text().splitlines()
    assert len(lines) == 3
    assert lines[-1] == f'{minted["sid"]}@session namespaces="{NAMESPACES}" {publine}'


def test_one_commit_by_pathspec_signed_by_the_new_key(minted, git_env):
    reg, sid = minted["reg"], minted["sid"]
    log = subprocess.run(["git", "-C", str(reg), "rev-list", f"{minted['before']}..HEAD"],
                         capture_output=True, text=True, env=git_env).stdout.split()
    assert len(log) == 1, "ONE commit"
    files = subprocess.run(["git", "-C", str(reg), "show", "--name-only", "--format=", "HEAD"],
                           capture_output=True, text=True, env=git_env).stdout.split()
    assert sorted(files) == sorted(["registry/allowed_signers", f"registry/certs/{sid}.cert",
                                    f"registry/certs/{sid}.cert.sig"])
    assert subprocess.run(["git", "-C", str(reg), "status", "--porcelain"], capture_output=True,
                          text=True, env=git_env).stdout == ""
    ok = subprocess.run(["git", "-C", str(reg), "-c",
                         f"gpg.ssh.allowedSignersFile={reg / 'allowed_signers'}",
                         "verify-commit", "HEAD"], capture_output=True, text=True, env=git_env)
    assert ok.returncode == 0, ok.stderr
    assert f"{sid}@session" in ok.stderr


# ── SM-866: a stranger's git config cannot refuse, re-sign or rename a birth ─

ENV_IDENTITY = {"GIT_AUTHOR_NAME": "Env Author", "GIT_AUTHOR_EMAIL": "env@example.invalid",
                "GIT_COMMITTER_NAME": "Env Committer", "GIT_COMMITTER_EMAIL": "env@example.invalid"}


@pytest.mark.parametrize("shape", hostile_git.SHAPES)
def test_birth_lands_signed_by_the_seat_key_under_a_hostile_git_config(git_env, registry,
                                                                       tmp_path, shape):
    """The GC's hostile global (a hooksPath to a pre-commit that exits 1,
    commit.gpgsign = true), plus a foreign signer program and key, as a
    global file or as GIT_CONFIG_COUNT entries, AND a seat's exported
    GIT_AUTHOR_*/GIT_COMMITTER_*: the birth still lands as ONE commit,
    signed by the new seat key, authored and committed by the seat."""
    hostile = {**git_env, **hostile_git.hostile_env(tmp_path, shape), **ENV_IDENTITY}
    said = hostile_git.plain_commit_is_refused(tmp_path, hostile)
    assert hostile_git.HOOK_SAYS in said
    before = head(registry, git_env)
    rc, out, err = seat_init(hostile, "--registry", registry, "--machine", "hq", "desk.uma")
    assert rc == 0 and "minted" in out, err
    sid = re.search(r"sid\s+: (\S+)", out).group(1)
    reg = registry
    log = subprocess.run(["git", "-C", str(reg), "rev-list", f"{before}..HEAD"],
                         capture_output=True, text=True, env=git_env).stdout.split()
    assert len(log) == 1, "ONE commit"
    assert hostile_git.signed(reg, env=git_env)
    ok = subprocess.run(["git", "-C", str(reg), "-c",
                         f"gpg.ssh.allowedSignersFile={reg / 'allowed_signers'}",
                         "verify-commit", "HEAD"], capture_output=True, text=True, env=git_env)
    assert ok.returncode == 0 and f"{sid}@session" in ok.stderr, ok.stderr
    seat = f"desk.uma (seat) <{sid}@session>"
    assert hostile_git.people(reg, env=git_env) == (seat, seat)


# ── SM-866 ruling: the registry's OWN hooks run; a global hooksPath never does ─


def nothing_left_behind(git_env, registry, before, signers):
    assert head(registry, git_env) == before, "nothing committed"
    assert (registry / "allowed_signers").read_bytes() == signers
    assert sorted(p.name for p in (registry / "certs").iterdir()) == [".keep"]
    assert subprocess.run(["git", "-C", str(registry), "status", "--porcelain"], capture_output=True,
                          text=True, env=git_env).stdout == "", "nothing staged or left untracked"
    assert not (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys" / "desk.uma").exists()


@pytest.mark.parametrize("shape", ["plain", *hostile_git.SHAPES])
def test_the_registry_repos_own_failing_hook_blocks_the_birth_loudly(git_env, registry,
                                                                     tmp_path, shape):
    """The registry is a trust repo: its owner's pre-commit may gate what
    enters trust, so a failing hook in its own .git/hooks refuses the birth.
    Loudly: exit 1, the hook's own words under a named error, and nothing
    committed, staged or left behind. Under a hostile config too, and it is
    the REPO's hook that refuses, never the global one."""
    env = git_env if shape == "plain" else {**git_env, **hostile_git.hostile_env(tmp_path, shape)}
    failing_hook(registry)
    signers = (registry / "allowed_signers").read_bytes()
    before = head(registry, git_env)
    rc, out, err = seat_init(env, "--registry", registry, "desk.uma")
    assert rc == 1 and "minted" not in out
    assert "the registry commit failed: the registry says no" in err, err
    assert hostile_git.HOOK_SAYS not in err
    nothing_left_behind(git_env, registry, before, signers)


@pytest.mark.parametrize("shape", hostile_git.SHAPES)
def test_a_hostile_hooks_path_never_leaks_into_the_birth_through_the_fallback(
        git_env, registry, tmp_path, shape):
    """With no repo-local core.hooksPath the birth runs the repo's
    <git-common-dir>/hooks, never the global or GIT_CONFIG_COUNT hooksPath.
    The rig first shows why the fallback is not `rev-parse --git-path hooks`:
    under this config that names the hostile dir. Then a hook in the repo's
    own dir that lets the commit through and leaves a mark: it ran, and the
    hostile hook (which refuses) did not."""
    hostile = {**git_env, **hostile_git.hostile_env(tmp_path, shape)}
    named = subprocess.run(["git", "-C", str(registry), "rev-parse", "--git-path", "hooks"],
                           capture_output=True, text=True, env=hostile, check=True).stdout.strip()
    assert Path(named) == hostile_git.hooks_dir(tmp_path), "the rig must make --git-path leak"
    mark = tmp_path / "the-registrys-own-hook-ran"
    hostile_git.hook(registry.parent / ".git" / "hooks", mark=mark)
    rc, out, err = seat_init(hostile, "--registry", registry, "desk.uma")
    assert rc == 0 and "minted" in out, err
    assert mark.exists(), "the repo's own hook ran"


@pytest.mark.parametrize("form", ["relative", "absolute"])
def test_a_repo_local_hooks_path_is_honoured_for_the_birth(git_env, registry, tmp_path, form):
    """A repo-local core.hooksPath names the registry's own hooks, and its
    failing pre-commit blocks the birth, under the hostile global too. A
    RELATIVE value resolves against the repo's TOP, as git resolves it: the
    script runs from another cwd, which holds a hooks dir of that name that
    would let the commit through, and `-C`s into registry/, a subdir that
    holds none. Resolved against either, the birth would land."""
    top = registry.parent
    hostile_git.hook(top / "owner-hooks", refuse="the owner's hooksPath says no")
    subprocess.run(["git", "-C", str(top), "config", "--local", "core.hooksPath",
                    "owner-hooks" if form == "relative" else str(top / "owner-hooks")],
                   check=True, env=git_env)
    elsewhere = tmp_path / "elsewhere"
    hostile_git.hook(elsewhere / "owner-hooks", mark=tmp_path / "the-cwd-hook-ran")
    hostile = {**git_env, **hostile_git.hostile_env(tmp_path, "global")}
    before = head(registry, git_env)
    done = subprocess.run([*SEAT_INIT, "--registry", str(registry), "desk.uma"], cwd=elsewhere,
                          capture_output=True, text=True, env=hostile)
    assert done.returncode == 1, done.stdout
    assert "the registry commit failed: the owner's hooksPath says no" in done.stderr, done.stderr
    assert not (tmp_path / "the-cwd-hook-ran").exists()
    assert head(registry, git_env) == before


def test_own_hooks_names_a_relative_local_path_in_a_bare_repo_against_its_git_dir(tmp_path):
    """git runs a bare repo's hooks from its git dir, so a relative
    core.hooksPath there resolves against it, not a work tree it lacks."""
    from sessionmail.seat_init import own_hooks
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    assert Path(own_hooks(bare)).resolve() == (bare / "hooks").resolve()
    subprocess.run(["git", "-C", str(bare), "config", "core.hooksPath", "mine"], check=True)
    assert Path(own_hooks(bare)).resolve() == (bare / "mine").resolve()


def test_own_hooks_on_a_dir_that_is_no_repo_raises_with_gits_words(tmp_path):
    from sessionmail.seat_init import own_hooks
    with pytest.raises(RuntimeError, match="git repository"):
        own_hooks(tmp_path)


def test_it_prints_the_sid_fingerprint_and_the_bless_command_and_never_blesses(minted):
    out, sid, reg = minted["out"], minted["sid"], minted["reg"]
    assert "NOT blessed" in out and re.search(r"fingerprint : 256 SHA256:\S+", out)
    assert "LAMP_BLESS=1" in out and "session-bless" in out and f" {sid}\n" in out
    assert f"certs/{sid}.cert.blessed-by.alice@bless.sig" in out
    assert not list(reg.glob("certs/*blessed-by*")), "seat-init never blesses"
    assert not (minted["home"] / "bless-input").exists()


def test_the_bless_principal_is_config_with_verifys_neutral_fallback(minted, git_env, registry):
    """SM-795 D8: no person's name is a default. With SESSION_BLESS_PRINCIPAL unset,
    the printed command names mhs/session-mail-verify's own neutral fallback,
    `alice@bless`, and the output names nobody; with it set, both the blessed file
    and the commit message follow it, the variable verify reads."""
    out = minted["out"]
    assert "alice@bless" in out
    assert not re.search(r"\bmike\b", out, re.I), "a person's name in seat-init's output"
    rc, out, err = seat_init({**git_env, "SESSION_BLESS_PRINCIPAL": "ops@bless"},
                             "--registry", registry, "desk.other")
    assert rc == 0, err
    sid = re.search(r"sid\s+: (\S+)", out).group(1)
    assert f"certs/{sid}.cert.blessed-by.ops@bless.sig" in out
    assert f"seat {sid} (ops@bless)" in out and "the ops@bless key's holder only" in out
    assert "alice@bless" not in out


def test_a_second_run_refuses_and_leaves_the_registry_alone(minted, git_env):
    after = head(minted["reg"], git_env)
    rc, _, err = seat_init(git_env, "--registry", minted["reg"], "desk.uma")
    assert rc == EX_REFUSED and "never overwrites" in err
    assert head(minted["reg"], git_env) == after


# ── end to end: a mail from the seat, through the seat agent ─────────────────

@pytest.fixture
def seat_agent(minted, env):
    """A scratch ssh-agent holding the minted key: the seat agent's shape
    (design §8, Road 1). The sender sees it through the seam's two names."""
    d = tempfile.mkdtemp(prefix="sm-seat-", dir="/tmp")         # a short socket path
    out = subprocess.run(["ssh-agent", "-s", "-a", os.path.join(d, "s")], check=True,
                         capture_output=True, text=True).stdout
    sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", out).group(1)
    pid = re.search(r"SSH_AGENT_PID=(\d+);", out).group(1)
    subprocess.run(["ssh-add", str(minted["key"])], check=True, capture_output=True,
                   env={**os.environ, "SSH_AUTH_SOCK": sock})
    env["CLAUDE_SESSION_AGENT_SOCK"] = sock
    env["CLAUDE_SESSION_PUB"] = str(minted["pub"])
    env["PATH"] = env["PATH"] + os.pathsep + os.path.dirname(shutil.which("ssh-keygen"))
    yield env
    subprocess.run(["kill", pid], capture_output=True)
    shutil.rmtree(d, ignore_errors=True)


def mail_from(sender, env, tmp_path, fake_sendmail, address: str) -> Path:
    body = tmp_path / "body.md"
    body.write_text("from the desk\n")
    rc, _, err = sender.run(["eve", "--from", address, "--subject", "desk", "--body-file",
                             str(body)], env)
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    eml = tmp_path / f"{address}.eml"
    eml.write_bytes(fake_sendmail.wire)
    return eml


def trust(verifier, eml: Path, reg: Path):
    done = subprocess.run([str(verifier), str(eml), "--trust"], capture_output=True, text=True,
                          env={**os.environ, "SESSION_REGISTRY": str(reg)})
    return done.returncode, done.stdout + done.stderr


def test_a_seat_mail_is_unblessed_rc_4_naming_its_agent(sender, seat_agent, tmp_path, fake_sendmail,
                                                         verifier, minted):
    eml = mail_from(sender, seat_agent, tmp_path, fake_sendmail, "desk.uma")
    rc, said = trust(verifier, eml, minted["reg"])
    assert rc == 4, said
    assert f"sender-session-id : {minted['sid']}" in said, "the verifier derives the same sid"
    assert "agent: desk.uma" in said and "TRUST: UNBLESSED reason=bless-absent" in said


@pytest.mark.parametrize("address", ["uma", "uma+desk"])
def test_the_seat_key_cannot_speak_as_another_local_part(sender, seat_agent, tmp_path, fake_sendmail,
                                                         verifier, minted, address):
    eml = mail_from(sender, seat_agent, tmp_path, fake_sendmail, address)
    rc, said = trust(verifier, eml, minted["reg"])
    assert rc == 1 and "TRUST: FAIL leg=2-registry reason=agent-mismatch" in said, said


def test_with_the_seat_agent_dead_a_must_sign_send_is_refused(sender, seat_agent, tmp_path,
                                                              fake_sendmail):
    """Probe 8c, closed: no private key beside seat.pub's stem, so a dead agent
    cannot be papered over by the file, and must-sign (do-001) refuses."""
    seat_agent["CLAUDE_SESSION_AGENT_SOCK"] = str(tmp_path / "no-such-socket")
    seat_agent["SESSIONMAIL_REQUIRE_SIGNATURE"] = "1"
    body = tmp_path / "body.md"
    body.write_text("x\n")
    rc, _, err = sender.run(["eve", "--from", "desk.uma", "--subject", "s", "--body-file",
                             str(body)], seat_agent)
    assert rc == EX_REFUSED and "signing FAILED" in err
    assert fake_sendmail.wire is None


def test_seat_init_is_a_message_verb_and_no_command_of_its_own(git_env):
    """Ruling (b): the package
    keeps exactly three commands, and seat-init ships as `message seat init`.
    So [project.scripts] is message, pager and memory (no console entry point
    of its own; the literal names are not in pyproject at all), the wheel
    packages src/sessionmail only, and `message seat init` reaches the PACKAGED
    file: after an option click does not know, argparse's own help answers,
    with the prog that file sets (design §3)."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert set(tomllib.loads(text)["project"]["scripts"]) == {"message", "pager", "memory"}
    assert "seat-init" not in text and "seat_init" not in text
    assert 'packages = ["src/sessionmail"]' in text
    assert os.access(SCRIPT, os.X_OK)
    assert 'prog="message seat init"' in SCRIPT.read_text()
    rc, out, err = seat_init(git_env, "--registry", "x", "--help")
    assert rc == 0 and out.startswith("usage: message seat init [-h] --registry"), out + err
    assert MESSAGE.is_file(), "the test venv has no message: the verbs cannot be run as installed"


# ── the birth namespace comes from the registry ──────────────────────────────
# The cert is signed under the ONE session-birth@ entry in the newest @session
# line's namespaces= list; with none, under session-birth@example.com (as mail and
# bless fall back); with two or more, refused. The registry-ABSENT refusals above
# never fall back.

def reseed(reg: Path, git_env, tmp_path: Path, *lines: str) -> str:
    """Replace allowed_signers with LINES (each `<sid> <options>`; a key is appended),
    commit it, and return the new HEAD."""
    text = "".join(f"{line} {keypub(tmp_path, f'k{i}')}\n" for i, line in enumerate(lines))
    (reg / "allowed_signers").write_text(text)
    subprocess.run(["git", "-C", str(reg), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-am", "reseed"], check=True, env=git_env)
    return head(reg, git_env)


def verifies_under(reg: Path, sid: str, namespace: str, tmp_path: Path) -> bool:
    """Does the minted cert's .sig verify, under NAMESPACE, by the key its cert names?"""
    cert = reg / "certs" / f"{sid}.cert"
    publine = cert.read_text().splitlines()[2].removeprefix("pubkey: ")
    allowed = tmp_path / "verify.allowed"
    allowed.write_text(f"{sid}@session {publine}\n")
    with open(cert, "rb") as data:
        return subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I",
                               f"{sid}@session", "-n", namespace, "-s", f"{cert}.sig"],
                              stdin=data, capture_output=True).returncode == 0


def mint(git_env, reg: Path) -> tuple[str, str]:
    rc, out, err = seat_init(git_env, "--registry", reg, "desk.uma")
    assert rc == 0, err
    return re.search(r"sid\s+: (\S+)", out).group(1), out


def test_the_birth_namespace_is_the_one_the_registry_lists(git_env, registry, tmp_path):
    """(a, e) Signed under the list's session-birth@ entry, never session-birth@wrong.example.
    rc 0 is the self-verify passing, so sign and self-verify used the SAME namespace:
    the .sig verifies under it alone. The wrong namespace is NOT src's @example.com
    fallback, so the "never" names one wrong pick (the older entry), not the default."""
    reseed(registry, git_env, tmp_path,
           '11111111-1111-1111-1111-111111111111@session namespaces="session-birth@wrong.example"',
           '22222222-2222-2222-2222-222222222222@session '
           'namespaces="session-mail@other.test,session-birth@other.test,git"')
    sid, out = mint(git_env, registry)
    assert verifies_under(registry, sid, "session-birth@other.test", tmp_path)
    assert not verifies_under(registry, sid, "session-birth@wrong.example", tmp_path)
    assert "birth ns    : session-birth@other.test\n" in out and "fallback" not in out


def test_a_list_with_no_session_birth_entry_falls_back_to_example_com(git_env, registry, tmp_path):
    """(b) The list is there but names no session-birth@: the neutral fallback."""
    reseed(registry, git_env, tmp_path,
           '22222222-2222-2222-2222-222222222222@session namespaces="session-mail@other.test,git"')
    sid, out = mint(git_env, registry)
    assert verifies_under(registry, sid, "session-birth@example.com", tmp_path)
    assert not verifies_under(registry, sid, "session-birth@wrong.example", tmp_path)  # no rule picks it
    assert "birth ns    : session-birth@example.com  (the fallback" in out


def test_two_session_birth_entries_are_refused_naming_both(git_env, registry, tmp_path):
    """(c) Never guess: refused, both named, nothing written anywhere."""
    before = reseed(registry, git_env, tmp_path,
                    '22222222-2222-2222-2222-222222222222@session '
                    'namespaces="session-birth@a.test,session-mail@a.test,session-birth@b.test"')
    signers = (registry / "allowed_signers").read_bytes()
    rc, out, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "Nothing was written" in err and out == ""
    assert "session-birth@a.test" in err and "session-birth@b.test" in err
    assert not (Path(git_env["SESSIONMAIL_HOME"]) / "seat-keys").exists(), "the key home was made"
    assert head(registry, git_env) == before
    assert (registry / "allowed_signers").read_bytes() == signers
    assert sorted(p.name for p in (registry / "certs").iterdir()) == [".keep"]


def test_the_newest_line_with_a_list_wins_over_a_newer_line_without_one(git_env, registry, tmp_path):
    """(f) A live registry's shape: old @session lines with NO namespaces= are not
    "a list with no session-birth@". The newest line that HAS a list is read, and
    it names X, so X is used and nothing falls back."""
    reseed(registry, git_env, tmp_path,
           '11111111-1111-1111-1111-111111111111@session namespaces="session-birth@x.test,git"',
           "33333333-3333-3333-3333-333333333333@session")
    sid, out = mint(git_env, registry)
    assert verifies_under(registry, sid, "session-birth@x.test", tmp_path)
    assert not verifies_under(registry, sid, "session-birth@example.com", tmp_path)
    assert "fallback" not in out


def test_only_lines_without_namespaces_are_refused_never_fallen_back(git_env, registry, tmp_path):
    """(d) Every @session line lacks namespaces=: the registry has no list at all,
    so it is today's refusal, not the @example.com fallback."""
    before = reseed(registry, git_env, tmp_path, "33333333-3333-3333-3333-333333333333@session")
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "no @session line with namespaces=" in err
    assert "example.com" not in err
    nothing_written(git_env, registry, before)


def test_a_git_registry_with_no_allowed_signers_is_refused(git_env, registry):
    """(d) The one absent-registry refusal the tests above did not pin."""
    subprocess.run(["git", "-C", str(registry), "-c", "user.name=t", "-c", "user.email=t@t",
                    "rm", "-q", "allowed_signers"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(registry), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "no signers"], check=True, env=git_env)
    before = head(registry, git_env)
    rc, _, err = seat_init(git_env, "--registry", registry, "desk.uma")
    assert rc == EX_REFUSED and "no allowed_signers file" in err and "Nothing was written" in err
    nothing_written(git_env, registry, before)
