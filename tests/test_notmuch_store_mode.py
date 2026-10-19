"""The four notmuch calls follow `_index` (SM-731 do-003; design §3, §13).

`_notmuch.search`, `.revision` and `.wake_text`, and `_sender.notmuch_files`,
each take their argv prefix and child environment from the resolver:

- host mode (the default): today's argv, no `--config`, and today's child
  environment (`_notmuch` inherits this process's; `notmuch_files` passes
  its `env`), NOTMUCH_* overrides included;
- store mode: `--config=<the resolved profile>`, with NOTMUCH_CONFIG,
  NOTMUCH_DATABASE and NOTMUCH_PROFILE out of the child's env (E3, E14);
- store mode with no profile: `search` and `wake_text` raise CouldNotLook
  naming the profile, `revision` is None, `notmuch_files` is empty and the
  walk answers. notmuch is never run.

A fake notmuch logs its argv and the NOTMUCH_* variables it was handed to a
log whose path is baked into the script, so the log does not depend on the
very environment under test. `_notmuch` finds its fake through
SESSIONMAIL_NOTMUCH; `_sender.notmuch_files` takes `notmuch` from env's PATH
(`_sender.py`, `shutil.which("notmuch", path=...)`), so its fake goes on
PATH. Every store-mode env is built from nothing (design §13, test hygiene).
One test runs the real notmuch, when it is installed, against two scratch
databases: E14's planted NOTMUCH_DATABASE must not reach a store-mode read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _index, _notmuch, _sender

NOTMUCH = shutil.which("notmuch")
OVERRIDES = ("NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "NOTMUCH_PROFILE")

PARENT = "<1908540800.1.2@alpha.example.invalid>"

#: `write_fake` formats `{python}` itself, so the baked-in paths go in by
#: replacement and the braces stay doubled for its `.format`.
FAKE = """#!{python}
import json, os, sys
with open(@LOG@, "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:],
                          "env": {{k: os.environ[k] for k in @OVERRIDES@ if k in os.environ}}}}) + "\\n")
if "--lastmod" in sys.argv:
    sys.stdout.write("1\\tfake-uuid\\t7\\n")
    raise SystemExit(0)
sys.stdout.write(open(@OUT@).read())
"""


@pytest.fixture(autouse=True)
def fresh_resolver():
    _index.clear_cache()
    yield
    _index.clear_cache()


class Fake:
    """The fake engine: `says(text)` sets its stdout, `calls()` reads its log."""

    def __init__(self, tmp: Path, name: str = "notmuch"):
        self.bin = tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.log, self.out = tmp / "nm.log", tmp / "nm.out"
        self.out.write_text("[]")
        source = (FAKE.replace("@LOG@", repr(str(self.log))).replace("@OUT@", repr(str(self.out)))
                  .replace("@OVERRIDES@", repr(OVERRIDES)))
        self.path = write_fake(self.bin / name, source)

    def says(self, text: str) -> None:
        self.out.write_text(text)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def base_env(tmp: Path, **extra: str) -> dict[str, str]:
    """Nothing inherited: a scratch HOME and XDG dir, plus EXTRA."""
    (tmp / "home").mkdir(exist_ok=True)
    return {"HOME": str(tmp / "home"), "XDG_CONFIG_HOME": str(tmp / "xdg"),
            "SESSION_MAIL_ROOT": str(tmp / "mail"), "PATH": "/usr/bin:/bin", **extra}


def profile(tmp: Path, name: str) -> Path:
    path = tmp / "home" / f".notmuch-config.{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[database]\n")
    return path


#: Planted in every store-mode env: each one would override the profile.
PLANTED = {"NOTMUCH_CONFIG": "/planted/config", "NOTMUCH_DATABASE": "/planted/db"}

ONE = json.dumps([[[{"id": "a@x", "timestamp": 1, "date_relative": "now",
                     "headers": {"From": "alpha@example.invalid", "Subject": "hi"},
                     "body": [{"content-type": "text/plain", "content": "body\n"}]}, []]]])


# --- _notmuch: search, revision, wake_text ---------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    fake = Fake(tmp_path)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(fake.path))
    monkeypatch.delenv(_index.VAR, raising=False)
    return fake


def store_env(tmp: Path, fake: Fake, prof: str = "alpha", **extra: str) -> dict[str, str]:
    return base_env(tmp, **{_notmuch.BINARY_VAR: str(fake.path), _index.VAR: "store",
                            "NOTMUCH_PROFILE": prof, **PLANTED, **extra})


def test_host_mode_search_passes_the_host_profile_and_scrubs(engine, monkeypatch):
    # Host mode reads the host's sessionkit profile (the sandbox's), and no
    # inherited override reaches the child: a stranger may export them.
    monkeypatch.setenv("NOTMUCH_DATABASE", "/host/db")
    monkeypatch.setenv("NOTMUCH_PROFILE", "inherited")
    engine.says(ONE)
    assert [m.id for m in _notmuch.search("tag:x")] == ["a@x"]
    (call,) = engine.calls()
    assert call["argv"] == [f"--config={_index.profile_config()}", "show", "--format=json",
                            "--body=false", "--entire-thread=false", "tag:x"]
    assert call["env"] == {}, "NOTMUCH_CONFIG, _DATABASE and _PROFILE never reach the child"


def test_host_mode_is_explicit_too(engine, tmp_path):
    env = base_env(tmp_path, **{_notmuch.BINARY_VAR: str(engine.path), _index.VAR: "host"})
    prof = _index.profile_config(env=env)
    prof.parent.mkdir(parents=True)
    prof.write_text("[database]\n")
    _notmuch.search("tag:x", env)
    _notmuch.revision(env)
    engine.says(ONE)
    _notmuch.wake_text("a@x", env)
    assert all(c["argv"][0] == f"--config={prof}" for c in engine.calls())
    assert len(engine.calls()) == 3


def test_store_mode_search_passes_the_profile_and_scrubs_the_overrides(engine, tmp_path):
    prof = profile(tmp_path, "alpha")
    engine.says(ONE)
    assert [m.id for m in _notmuch.search("tag:x", store_env(tmp_path, engine))] == ["a@x"]
    (call,) = engine.calls()
    assert call["argv"] == [f"--config={prof}", "show", "--format=json", "--body=false",
                            "--entire-thread=false", "tag:x"]
    assert call["env"] == {}, "NOTMUCH_CONFIG, _DATABASE and _PROFILE never reach the child"


def test_store_mode_revision_passes_the_profile(engine, tmp_path):
    prof = profile(tmp_path, "alpha")
    assert _notmuch.revision(store_env(tmp_path, engine)) == 7
    (call,) = engine.calls()
    assert call["argv"] == [f"--config={prof}", "count", "--lastmod", "*"]
    assert call["env"] == {}


def test_store_mode_wake_text_passes_the_profile(engine, tmp_path):
    prof = profile(tmp_path, "alpha")
    engine.says(ONE)
    text = _notmuch.wake_text("<a@x>", store_env(tmp_path, engine))
    assert "Subject: hi" in text and "body" in text
    (call,) = engine.calls()
    assert call["argv"] == [f"--config={prof}", "show", "--format=json",
                            "--include-html=false", "--entire-thread=false", "id:a@x"]
    assert call["env"] == {}


def test_store_mode_without_a_profile_refuses_by_name_and_never_runs_notmuch(engine, tmp_path):
    env = store_env(tmp_path, engine, prof="nosuch")
    with pytest.raises(_notmuch.CouldNotLook) as search:
        _notmuch.search("tag:x", env)
    with pytest.raises(_notmuch.CouldNotLook) as wake:
        _notmuch.wake_text("a@x", env)
    for exc in (search.value, wake.value):
        assert "no notmuch profile" in str(exc)
        assert "'nosuch'" in str(exc) and ".notmuch-config.nosuch" in str(exc)
        assert _index.REFUSES_HOST in str(exc)
    assert _notmuch.revision(env) is None, "revision's contract: None, the caller polls unscoped"
    assert engine.calls() == [], "a refusal is never a host-wide answer"


def test_a_bad_mode_refuses_every_call(engine, tmp_path):
    env = base_env(tmp_path, **{_notmuch.BINARY_VAR: str(engine.path), _index.VAR: "stor"})
    with pytest.raises(_notmuch.CouldNotLook, match="'stor' is not a mode"):
        _notmuch.search("tag:x", env)
    assert _notmuch.revision(env) is None
    assert engine.calls() == []


def test_an_empty_profile_is_refused_not_read_as_the_default(engine, tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".notmuch-config").write_text("[database]\n")
    with pytest.raises(_notmuch.CouldNotLook, match="EMPTY"):
        _notmuch.search("tag:x", store_env(tmp_path, engine, prof=""))
    assert engine.calls() == []


def test_a_missing_engine_is_still_named_first(tmp_path):
    env = base_env(tmp_path, **{_notmuch.BINARY_VAR: str(tmp_path / "absent"),
                                _index.VAR: "store", "NOTMUCH_PROFILE": "nosuch"})
    with pytest.raises(_notmuch.CouldNotLook, match="no search engine"):
        _notmuch.search("tag:x", env)


# --- _sender.notmuch_files: the fake goes on PATH ---------------------------


def mail(path: Path, msgid: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"From: a\nTo: b\nMessage-ID: {msgid}\n\nbody\n".encode())
    return str(path)


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake `notmuch` on env's PATH, a root holding PARENT, and a watched walk."""
    fake = Fake(tmp_path)
    root = tmp_path / "mail"
    parent = mail(root / "alpha" / "cur" / "1908540800.M1P1.x:2,S", PARENT)
    fake.says(parent + "\n")
    walked: list[set[str]] = []
    real_walk = _sender.find_messages

    def watched(r, ids):
        walked.append(set(ids))
        return real_walk(r, ids)

    monkeypatch.setattr(_sender, "find_messages", watched)
    return fake, root, parent, walked


def sender_env(tmp: Path, fake: Fake, **extra: str) -> dict[str, str]:
    return base_env(tmp, PATH=str(fake.bin), **extra)


def test_notmuch_files_host_mode_passes_the_host_profile(box, tmp_path):
    fake, root, parent, walked = box
    env = sender_env(tmp_path, fake, **PLANTED)
    prof = _index.profile_config(env=env)
    prof.parent.mkdir(parents=True)
    prof.write_text("[database]\n")
    assert _sender.find_parents(root, {PARENT}, env) == {PARENT: parent}
    (call,) = fake.calls()
    assert call["argv"] == [f"--config={prof}", "search", "--output=files",
                            'id:"1908540800.1.2@alpha.example.invalid"']
    assert call["env"] == {}, "the planted overrides never reach the child"
    assert walked == []


def test_notmuch_files_store_mode_passes_the_profile_and_scrubs(box, tmp_path):
    fake, root, parent, walked = box
    prof = profile(tmp_path, "alpha")
    env = sender_env(tmp_path, fake, **PLANTED, SESSIONMAIL_INDEX="store", NOTMUCH_PROFILE="alpha")
    assert _sender.find_parents(root, {PARENT}, env) == {PARENT: parent}
    (call,) = fake.calls()
    assert call["argv"] == [f"--config={prof}", "search", "--output=files",
                            'id:"1908540800.1.2@alpha.example.invalid"']
    assert call["env"] == {}
    assert walked == [], "the store index answered: no walk"


@pytest.mark.parametrize("extra", [
    {"SESSIONMAIL_INDEX": "store", "NOTMUCH_PROFILE": "nosuch"},
    {"SESSIONMAIL_INDEX": "store", "NOTMUCH_PROFILE": ""},
    {"SESSIONMAIL_INDEX": "bogus"},
], ids=["missing-profile", "empty-profile", "bad-mode"])
def test_notmuch_files_refusal_is_empty_and_the_walk_answers(box, tmp_path, extra):
    fake, root, parent, walked = box
    env = sender_env(tmp_path, fake, **extra)
    assert _sender.notmuch_files(root, {PARENT}, env) == {}
    assert _sender.find_parents(root, {PARENT}, env) == {PARENT: parent}, \
        "a send never fails for want of an index"
    assert fake.calls() == [], "notmuch is never asked on a refusal"
    assert walked == [{PARENT}]


# --- the real engine: a planted NOTMUCH_DATABASE never answers (E14) --------


def _index_of(tmp: Path, name: str, msgid: str) -> tuple[Path, Path]:
    """A one-message maildir NAME, its config and its built database."""
    root = tmp / name
    for sub in ("cur", "new", "tmp"):
        (root / sub).mkdir(parents=True)
    (root / "cur" / f"{name}:2,S").write_text(
        f"From: {name}@example.invalid\nTo: b@example.invalid\nSubject: {name}\n"
        f"Message-ID: <{msgid}>\n\nbody\n")
    config = tmp / f".notmuch-config.{name}"
    config.write_text(f"[database]\npath={tmp / ('db-' + name)}\nmail_root={root}\n"
                      "[new]\ntags=unread;\n[maildir]\nsynchronize_flags=false\n")
    env = {"HOME": str(tmp), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run([NOTMUCH, f"--config={config}", "new", "--quiet", "--no-hooks"],
                   env=env, check=True, capture_output=True, timeout=60)
    return config, tmp / ("db-" + name)


@pytest.mark.skipif(NOTMUCH is None, reason="notmuch is not installed")
def test_real_notmuch_store_mode_reads_the_profile_not_a_planted_database(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    host_config, host_db = _index_of(home, "host", "host-only@x")
    _index_of(home, "alpha", "alpha-only@x")
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
           "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           _notmuch.BINARY_VAR: NOTMUCH, _index.VAR: "store", "NOTMUCH_PROFILE": "alpha",
           "NOTMUCH_CONFIG": str(host_config), "NOTMUCH_DATABASE": str(host_db)}
    assert [m.id for m in _notmuch.search("*", env)] == ["alpha-only@x"]
    assert _notmuch.search("id:host-only@x", env) == []
    assert "Subject: alpha" in _notmuch.wake_text("alpha-only@x", env)
    assert isinstance(_notmuch.revision(env), int)
    # And under the very same overrides, host mode answers from the host
    # database: the planted variables were live, so the scrub is what held.
    host = {**env, _index.VAR: "host"}
    host_child = {k: v for k, v in host.items() if k != _notmuch.BINARY_VAR}
    done = subprocess.run([NOTMUCH, "search", "--output=messages", "*"], env=host_child,
                          capture_output=True, text=True, timeout=30, check=False)
    assert done.stdout.split() == ["id:host-only@x"]
