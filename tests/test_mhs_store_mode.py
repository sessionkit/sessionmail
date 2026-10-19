"""`_mhs.run` follows `_index`, with the S1 refusal split (SM-731 do-004; design §3, §13).

The oracles call bare `notmuch`, so `_mhs.run` is their one injection point:

- host mode (the default): every verb runs exactly as before. The child's
  environment is the one handed in, and None is still None (it inherits);
- store mode with a profile: `NOTMUCH_CONFIG=<the resolved profile>`, with
  NOTMUCH_DATABASE and NOTMUCH_PROFILE dropped (E14);
- store mode with no profile: the refusal line goes to stderr first, once.
  `wake` then runs under a NOTMUCH_CONFIG that cannot exist, so its peek
  sections say COULD NOT LOOK. Every other memory verb exits 2 and its
  oracle never runs;
- `verify` reads no index: never injected and never refused, in either mode.

A fake oracle, named by $SESSIONMAIL_MHS_ORACLE, prints the NOTMUCH_CONFIG it
was handed and logs its argv and NOTMUCH_* env to a path baked into the
script, so the log does not depend on the environment under test. Every env
handed to `run` is built from nothing (design §13, test hygiene). Two tests
run the real thing in a sandbox: the bundled wake oracle, and the bundled
verifier through `message verify`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fakes import write_fake
from sessionmail import _index, _mhs
from sessionmail import memory as memory_cli
from sessionmail import message as message_cli

OVERRIDES = ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE")
MEMORY_VERBS = ("ls", "tree", "cat", "run", "wake")
READERS = ("ls", "tree", "cat", "run")          # no could-not-look path: refused before the oracle

#: Planted in every env: each would override the profile (E3, E14).
PLANTED = {"NOTMUCH_CONFIG": "/planted/config", "NOTMUCH_DATABASE": "/planted/db"}

FAKE = """#!{python}
import json, os, sys
with open(@LOG@, "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:],
                          "env": {{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}}}) + "\\n")
sys.stdout.write("NOTMUCH_CONFIG=" + os.environ.get("NOTMUCH_CONFIG", "<unset>") + "\\n")
sys.stderr.write("oracle stderr\\n")
"""

REFUSALS = {
    "missing-profile": {_index.VAR: "store", "NOTMUCH_PROFILE": "nosuch"},
    "empty-profile": {_index.VAR: "store", "NOTMUCH_PROFILE": ""},
    "bad-mode": {_index.VAR: "stores"},
}
#: What each refusal must name (SC3).
NAMED = {"missing-profile": ".notmuch-config.nosuch", "empty-profile": "EMPTY",
         "bad-mode": "'stores' is not a mode"}


@pytest.fixture(autouse=True)
def fresh_resolver(monkeypatch):
    # conftest does not scrub the switch (do-003's observation): a developer
    # who exported it would flip these tests' host cases.
    monkeypatch.delenv(_index.VAR, raising=False)
    _index.clear_cache()
    yield
    _index.clear_cache()


class Fake:
    def __init__(self, tmp: Path):
        (tmp / "bin").mkdir(exist_ok=True)
        self.log = tmp / "oracle.log"
        source = FAKE.replace("@LOG@", repr(str(self.log))).replace("@OVERRIDES@", repr(OVERRIDES))
        self.path = str(write_fake(tmp / "bin" / "fake-mhs", source))

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)


def base_env(tmp: Path, fake: Fake, **extra: str) -> dict[str, str]:
    """Nothing inherited: a scratch HOME and XDG dir, the named fake, plus EXTRA."""
    (tmp / "home").mkdir(exist_ok=True)
    return {"HOME": str(tmp / "home"), "XDG_CONFIG_HOME": str(tmp / "xdg"),
            "SESSION_MAIL_ROOT": str(tmp / "mail"), "PATH": "/usr/bin:/bin",
            _mhs.ORACLE_VAR: fake.path, **extra}


def profile(tmp: Path, name: str = "alpha") -> Path:
    path = tmp / "home" / f".notmuch-config.{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[database]\n")
    return path


def call(fake: Fake, verb: str, env: dict[str, str] | None, *args: str) -> int:
    return _mhs.run([fake.path, verb, *args], env=env)


# --- which verb: read the way `oracle` built the argv ----------------------


def test_the_verb_is_read_from_either_oracle_shape(fake):
    env = {_mhs.ORACLE_VAR: fake.path}
    assert _mhs.verb_of([fake.path, "wake", "eve"], env) == "wake"
    assert _mhs.verb_of([fake.path], env) == ""
    bundled = os.path.join(_mhs.MHS_DIR, "session-mail-verify")
    assert _mhs.verb_of([bundled, "/x.eml", "--trust"], {}) == "verify"
    assert _mhs.verb_of(_mhs.oracle("cat", {}) + ["/BOOT"], {}) == "cat"
    # A Python script runs behind sys.executable (SM-878): its verb is argv[1]'s.
    assert _mhs.verb_of(_mhs.oracle("run", {}) + ["/BOOT/WAKE"], {}) == "run"
    assert _mhs.verb_of(_mhs.oracle("wake", {}) + ["alpha"], {}) == "wake"
    assert _mhs.verb_of([sys.executable, "/somewhere/else.py"], {}) == ""
    assert _mhs.verb_of(["/usr/bin/something", "verify"], {}) == "", \
        "argv[1] counts only after the NAMED oracle"


# --- host mode: the host's sessionkit profile -------------------------------


def test_host_mode_injects_the_host_profile(fake, tmp_path, capfd):
    """Every verb that reads the index gets NOTMUCH_CONFIG=<the host's
    profile>, with the planted overrides dropped; `verify` reads no index and
    is handed the env as it is."""
    env = base_env(tmp_path, fake, **PLANTED, NOTMUCH_PROFILE="inherited")
    prof = _index.profile_config(env=env)
    prof.parent.mkdir(parents=True)
    prof.write_text("[database]\n")
    for verb in (*MEMORY_VERBS, "verify"):
        assert call(fake, verb, env) == 0
    envs = [c["env"] for c in fake.calls()]
    assert envs == [{"NOTMUCH_CONFIG": str(prof)}] * len(MEMORY_VERBS) + \
        [{**PLANTED, "NOTMUCH_PROFILE": "inherited"}]
    assert "no notmuch profile" not in capfd.readouterr().err


@pytest.mark.parametrize("mode", [None, "host"])
def test_host_mode_with_no_env_hands_the_child_the_profile(fake, monkeypatch, mode):
    """`run(argv)` with no env: the child gets this process's env with the
    host's profile injected, never the inherited overrides, plus the
    package's interpreter (SM-878). Nothing else differs from this process's
    environment. Compared by the keys that differ, never as whole dicts (a
    failure would print the environment)."""
    if mode:
        monkeypatch.setenv(_index.VAR, mode)
    monkeypatch.setenv(_mhs.ORACLE_VAR, fake.path)
    seen = []
    real = subprocess.run

    def spy(argv, **kw):
        seen.append(kw.get("env", "absent"))
        return real(argv, **kw)

    monkeypatch.setattr(_mhs.subprocess, "run", spy)
    assert _mhs.run([fake.path, "ls"]) == 0
    assert len(seen) == 1 and isinstance(seen[0], dict), type(seen[0]).__name__
    child = seen[0]
    assert child["NOTMUCH_CONFIG"] == str(_index.profile_config())
    assert "NOTMUCH_PROFILE" not in child and "NOTMUCH_DATABASE" not in child
    assert child[_mhs.PYTHON_VAR] == sys.executable
    differ = {k for k in set(child) | set(os.environ) if child.get(k) != os.environ.get(k)}
    assert differ <= {_mhs.PYTHON_VAR, "NOTMUCH_CONFIG", "NOTMUCH_PROFILE", "NOTMUCH_DATABASE"}, \
        sorted(differ)


# --- store mode with a profile ---------------------------------------------


@pytest.mark.parametrize("verb", MEMORY_VERBS)
def test_store_mode_injects_the_profile_and_drops_the_overrides(fake, tmp_path, capfd, verb):
    prof = profile(tmp_path)
    env = base_env(tmp_path, fake, **PLANTED, **{_index.VAR: "store", "NOTMUCH_PROFILE": "alpha"})
    assert call(fake, verb, env, "/") == 0
    (c,) = fake.calls()
    assert c["argv"] == [verb, "/"]
    assert c["env"] == {"NOTMUCH_CONFIG": str(prof)}, "no NOTMUCH_DATABASE, no NOTMUCH_PROFILE"
    out = capfd.readouterr()
    assert out.out == f"NOTMUCH_CONFIG={prof}\n"
    assert env["NOTMUCH_CONFIG"] == "/planted/config", "the caller's dict is not edited"


ENV_DUMP = """#!{python}
import json, os
with open(@LOG@, "w") as log:
    json.dump(dict(os.environ), log)
"""


@pytest.mark.parametrize("case", ["profile", "refused-wake"])
def test_the_store_env_is_built_from_the_env_handed_in(tmp_path, monkeypatch, case):
    """Mutant M6 (do-004 review N1, ruled into verify-001): the
    store-mode child env must be built from the env handed to `run`, never from
    os.environ. Both paths through `_store_env` are covered: a resolved profile,
    and a refused `wake` running under the sentinel. A key only the handed-in env
    carries must arrive, and a key only os.environ carries must not."""
    (tmp_path / "bin").mkdir()
    log = tmp_path / "env.json"
    dump = str(write_fake(tmp_path / "bin" / "env-dump", ENV_DUMP.replace("@LOG@", repr(str(log)))))
    monkeypatch.setenv("SM731_AMBIENT", "from-os-environ")
    (tmp_path / "home").mkdir()
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
           "PATH": "/usr/bin:/bin", _mhs.ORACLE_VAR: dump, "SM731_HANDED": "from-the-caller",
           **PLANTED, _index.VAR: "store"}
    if case == "profile":
        prof = profile(tmp_path)
        env["NOTMUCH_PROFILE"] = "alpha"
        want_config = str(prof)
    else:
        env["NOTMUCH_PROFILE"] = "nosuch"
        want_config = _mhs.REFUSED_CONFIG
    assert _mhs.run([dump, "wake"], env=env) == 0
    child = json.loads(log.read_text())
    assert child.get("SM731_HANDED") == "from-the-caller", "the env handed in reaches the child"
    assert "SM731_AMBIENT" not in child, "os.environ must not leak into a handed-in env"
    assert child["NOTMUCH_CONFIG"] == want_config
    assert "NOTMUCH_DATABASE" not in child and "NOTMUCH_PROFILE" not in child


def test_store_mode_through_the_memory_door(fake, tmp_path, monkeypatch):
    """The spec scenario: `memory ls /` with NOTMUCH_DATABASE inherited."""
    prof = profile(tmp_path)
    for k, v in base_env(tmp_path, fake, **PLANTED,
                         **{_index.VAR: "store", "NOTMUCH_PROFILE": "alpha"}).items():
        monkeypatch.setenv(k, v)
    r = CliRunner().invoke(memory_cli.cli, ["ls", "/"])
    assert r.exit_code == 0, r.output
    assert f"NOTMUCH_CONFIG={prof}" in r.output
    (c,) = fake.calls()
    assert c["env"] == {"NOTMUCH_CONFIG": str(prof)}


# --- store mode with no profile: the S1 split ------------------------------


@pytest.mark.parametrize("case", sorted(REFUSALS))
@pytest.mark.parametrize("verb", READERS)
def test_a_refused_reader_exits_2_and_never_runs(fake, tmp_path, capfd, verb, case):
    env = base_env(tmp_path, fake, **PLANTED, **REFUSALS[case])
    assert call(fake, verb, env, "/RULES/10") == _mhs.EX_REFUSED == 2
    assert fake.calls() == [], "no oracle ran"
    out = capfd.readouterr()
    assert out.out == ""
    (line,) = out.err.splitlines()
    assert line.startswith(f"memory {verb}: ")
    assert NAMED[case] in line
    if case != "bad-mode":
        assert line.endswith(_index.REFUSES_HOST)


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refused_wake_runs_under_the_sentinel_and_says_why_first(fake, tmp_path, capfd, case):
    env = base_env(tmp_path, fake, **{**PLANTED, "NOTMUCH_PROFILE": "x", **REFUSALS[case]})
    assert call(fake, "wake", env) == 0, "the oracle's own exit"
    (c,) = fake.calls()
    assert c["env"] == {"NOTMUCH_CONFIG": _mhs.REFUSED_CONFIG}
    assert not os.path.exists(_mhs.REFUSED_CONFIG)
    err = capfd.readouterr().err.splitlines()
    assert err[0].startswith("memory wake: ") and NAMED[case] in err[0]
    assert err[1:] == ["oracle stderr"], "the refusal is said once, before the oracle's stderr"


def test_an_unknown_argv_shape_is_refused_like_a_reader(fake, tmp_path, capfd):
    env = base_env(tmp_path, fake, **REFUSALS["missing-profile"])
    assert _mhs.run([str(tmp_path / "bin" / "fake-mhs-2"), "verify"], env=env) == 2
    assert fake.calls() == []
    assert "no notmuch profile" in capfd.readouterr().err


# --- verify: exempt in both modes ------------------------------------------


@pytest.mark.parametrize("extra", [
    REFUSALS["missing-profile"], REFUSALS["empty-profile"], REFUSALS["bad-mode"],
    {_index.VAR: "store", "NOTMUCH_PROFILE": "alpha"},
], ids=["missing-profile", "empty-profile", "bad-mode", "resolved"])
def test_verify_is_never_injected_or_refused(fake, tmp_path, capfd, extra):
    profile(tmp_path)
    env = base_env(tmp_path, fake, **PLANTED, **extra)
    assert call(fake, "verify", env, "/x.eml", "--trust") == 0
    (c,) = fake.calls()
    want = {**PLANTED, **({"NOTMUCH_PROFILE": extra["NOTMUCH_PROFILE"]}
                         if "NOTMUCH_PROFILE" in extra else {})}
    assert c["env"] == want, "the env reaches the verifier exactly as handed in"
    assert "no notmuch profile" not in capfd.readouterr().err


# --- the real verifier, through `message verify` ---------------------------

SIG = Path(__file__).parent / "fixtures" / "sig_v2"
ORIGIN = json.loads((SIG / "vectors.json").read_text())["vectors"]["atom-three-part"]["origin"]


def unsigned(raw: bytes) -> bytes:
    head, sep, body = raw.partition(b"\n\n")
    kept, dropping = [], False
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.lower().startswith(b"x-session-signature:")
        if not dropping:
            kept.append(line)
    out = b"\n".join(kept) + sep + body
    assert b"X-Session-Signature" not in out
    return out


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on this machine")
@pytest.mark.parametrize("mode", [
    {}, REFUSALS["missing-profile"], REFUSALS["empty-profile"], REFUSALS["bad-mode"],
], ids=["host", "missing-profile", "empty-profile", "bad-mode"])
def test_message_verify_keeps_its_verdicts_in_every_mode(tmp_path, monkeypatch, mode):
    """Signed 0, unsigned 2, and --trust ends on exactly one TRUST: line, with
    the store's profile missing: a refusal here would be a false verdict."""
    (tmp_path / "home").mkdir()
    registry = tmp_path / "registry"
    (registry / "certs").mkdir(parents=True)
    for var in (*OVERRIDES, _mhs.ORACLE_VAR):
        monkeypatch.delenv(var, raising=False)
    for k, v in {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                 "SESSIONMAIL_ORIGIN": ORIGIN, "SESSION_REGISTRY": str(registry), **mode}.items():
        monkeypatch.setenv(k, v)
    good = tmp_path / "signed.eml"
    good.write_bytes((SIG / "single.eml").read_bytes())
    bare = tmp_path / "unsigned.eml"
    bare.write_bytes(unsigned(good.read_bytes()))

    def verify(*args):
        return CliRunner().invoke(message_cli.cli, ["verify", *args])

    r = verify(str(good))
    assert r.exit_code == 0, r.output
    assert "no notmuch profile" not in r.output
    assert verify(str(bare)).exit_code == 2, "2 is 'unsigned'"
    r = verify(str(good), "--trust")
    lines = r.output.strip().splitlines()
    assert [ln for ln in lines if ln.startswith("TRUST:")] == [lines[-1]]
    assert lines[-1] == "TRUST: FAIL leg=2-registry reason=no-cert", "leg 1 passed, as in host mode"


# --- the real wake oracle, in a sandbox ------------------------------------


def test_a_real_wake_with_no_profile_renders_boot_and_counts_and_could_not_look(tmp_path):
    """The spec scenario, on the bundled oracle against a sandbox box (never a
    real one, SM-530): stderr names the profile first; BOOT and COUNTS render;
    RECENT says COULD NOT LOOK."""
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / "alpha" / sub).mkdir(parents=True)
    (tmp_path / "home").mkdir()
    env = {k: v for k, v in os.environ.items() if k not in (*OVERRIDES, _mhs.ORACLE_VAR)}
    env.update(SESSION_MAIL_ROOT=str(root), HOME=str(tmp_path / "home"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_CONFIG_HOME=str(tmp_path / "xdg"),
               NOTMUCH_DATABASE=str(tmp_path / "planted-db"),
               **REFUSALS["missing-profile"])
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", "wake", "alpha"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    err = p.stderr.splitlines()
    assert err[0].startswith("memory wake: no notmuch profile for this seat: profile 'nosuch'")
    assert sum("no notmuch profile" in ln for ln in err) == 1, "said once"
    assert "═══ BOOT" in p.stdout and "═══ COUNTS ═══" in p.stdout, p.stdout
    assert "COULD NOT LOOK" in p.stdout, p.stdout
    if shutil.which("notmuch", path=env.get("PATH")):
        assert "cannot load config file" in p.stderr, "the sentinel, not a missing binary"
