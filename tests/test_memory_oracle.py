"""The memory oracle is the bundled copy in `mhs/`, never `session` off the PATH.

session 2fcd1d1's MHS scripts ship as package data in src/sessionmail/mhs/
(the session repo is being retired; see _mhs.py). Pinned here: all nine
ship and the seven that run are executable; without $SESSIONMAIL_MHS_ORACLE
each verb resolves to its bundled script even with a `session` first on the
PATH; a broken bundle is loud, not a fallback; and the door runs the bundle
end to end - reader, membership, the sibling verifier - without ever
calling `session`.

Hermetic: a notmuch stub serves a three-mail thread from tmp_path; HOME is
tmp_path; the `session` on the PATH is a stub that logs and APPROVES every
`mail-verify`, so a hidden call would both log and flip the verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs

VERBS = ("ls", "tree", "cat", "run", "wake")
EXECUTABLE = ("session-mail-ls", "session-mail-tree", "session-mail-cat", "session-mail-run",
              "session-mail-wake", "session-mail-verify", "mhs-thread")
SOURCED = ("mail-ext.lib.sh", "mail-box.lib.sh")

SESSION_STUB = """#!/usr/bin/env bash
echo "$*" >> {log}
[ "${{1:-}}" = "mail-verify" ] && exit 0
exit 99
"""

# The priming call is told apart by --entire-thread=false, not by the body
# flag: a reader asks for bodies only when it prints one (SM-668).
NOTMUCH_STUB = """#!/usr/bin/env bash
for a in "$@"; do case "$a" in
  --output=threads) echo thread:00aa; exit 0;;
  --entire-thread=false) cat {root}; exit 0;;
esac; done
for a in "$@"; do case "$a" in
  --body=false|--body=true) cat {thread}; exit 0;;
esac; done
echo "notmuch stub: unexpected: $*" >&2; exit 9
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def stub_session(tmp_path, monkeypatch):
    """A `session` first on the PATH: it logs and approves every mail-verify."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "session.log"
    _exe(bin_ / "session", SESSION_STUB.format(log=log))
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(_mhs.ORACLE_VAR, raising=False)
    return bin_, log


def test_the_oracle_ships_as_package_data():
    d = Path(_mhs.MHS_DIR)
    for name in EXECUTABLE:
        assert os.access(d / name, os.X_OK), f"{name}: the executable bit is part of the move"
    for name in SOURCED:
        assert (d / name).is_file(), name


@pytest.mark.parametrize("verb", VERBS)
def test_each_verb_is_its_bundled_script_not_session(stub_session, verb):
    """A Python script runs on the package's interpreter, named in front
    (SM-878: by its shebang it would run on PATH's python3); a bash one
    by path."""
    script = os.path.join(_mhs.MHS_DIR, f"session-mail-{verb}")
    want = [sys.executable, script] if verb in _mhs.PYTHON_VERBS else [script]
    assert _mhs.oracle(verb) == want


def test_a_named_oracle_still_wins(stub_session, monkeypatch):
    monkeypatch.setenv(_mhs.ORACLE_VAR, "/pinned/oracle")
    assert _mhs.oracle("cat") == ["/pinned/oracle", "cat"]


def test_a_broken_bundle_is_loud_and_never_falls_back_to_session(stub_session, monkeypatch, tmp_path):
    monkeypatch.setattr(_mhs, "MHS_DIR", str(tmp_path / "gone"))
    with pytest.raises(FileNotFoundError, match="no memory oracle"):
        _mhs.oracle("ls")


def _thread(tmp_path: Path) -> tuple[Path, Path]:
    """[ROOT] > [RULES] > 10 echo hi, all from reader to reader+memory, UNSIGNED."""
    mail = tmp_path / "mail"
    mail.mkdir()

    def msg(mid, subject, parent=None):
        fn = mail / mid
        fn.write_text(f"From: reader@host1\nTo: reader+memory@host1\nSubject: {subject}\n"
                      f"Message-ID: <{mid}@host1>\n"
                      + (f"In-Reply-To: <{parent}@host1>\n" if parent else "") + "\nbody\n")
        return {"id": f"{mid}@host1", "filename": [str(fn)], "timestamp": 1,
                "headers": {"From": "reader@host1", "To": "reader+memory@host1", "Subject": subject},
                "body": [{"content-type": "text/plain", "content": "echo hi"}]}

    root, rules, item = msg("root", "[ROOT]"), msg("rules", "[RULES]", "root"), msg("item", "10 echo hi", "rules")
    root_json, thread_json = tmp_path / "root.json", tmp_path / "thread.json"
    root_json.write_text(json.dumps([[[root, []]]]))
    thread_json.write_text(json.dumps([[[root, [[rules, [[item, []]]]]]]]))
    return root_json, thread_json


def test_the_door_runs_the_bundle_end_to_end_and_never_calls_session(stub_session, tmp_path):
    bin_, log = stub_session
    root_json, thread_json = _thread(tmp_path)
    _exe(bin_ / "notmuch", NOTMUCH_STUB.format(root=root_json, thread=thread_json))
    env = {k: v for k, v in os.environ.items() if not k.endswith("_ROOT")}
    env.update(HOME=str(tmp_path), CLAUDE_CODE_AGENT="reader", SESSION_AGENT="",
               READER_ROOT="root@host1")
    env.pop("SESSION_MAIL_VERIFY", None)
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", "ls", "/"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    # The bundled reader primed and rendered ...
    assert "═══ ls /   $READER_ROOT = root@host1 · [ROOT] from reader ✓ ═══" in p.stdout
    # ... and its verifier is the sibling session-mail-verify, which refuses
    # an unsigned mail. Had `session mail-verify` been asked, the stub would
    # have approved it and [RULES] would be a live label.
    assert "⊘ NOT A MEMBER: [RULES]" in p.stdout, p.stdout
    assert not log.exists(), f"`session` was called: {log.read_text()}"


def test_the_wake_ends_on_live_verbs_that_read_the_box_it_counted(tmp_path):
    """The wake's last line is what the agent types next, so it must name
    verbs that exist. It said `session mail list BOX · then read <n>`: a
    retired verb (review 2030-10-01, H1), and a bare `read` that `message
    read` would look up in the caller's own box, not the one just counted.
    Run through the door against a sandbox box; conftest keeps the index
    and the runner's seat out of it."""
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / "alpha" / sub).mkdir(parents=True)
    env = {**os.environ, "SESSION_MAIL_ROOT": str(root), "HOME": str(tmp_path / "home"),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", "wake", "alpha"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "═══ COUNTS ═══" in p.stdout, p.stdout
    assert "— next hop: message list alpha · then message read alpha <n>" in p.stdout, p.stdout
    assert "session mail" not in p.stdout


def test_a_reader_refusal_names_memory_not_the_retired_verb(stub_session):
    """mhs-thread's refusals began `session mail:`, naming a verb retired
    with the session repo, though `memory` is the only door to it now
    (ls|tree|cat through the shims, run through session-mail-run). The
    refusal must name the verb the user typed. `--depth -1` passes click's
    int check and is refused by the reader itself, before it reads any box;
    the usage text it prints after the line must not teach the old verb."""
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", "tree", "/", "--depth", "-1"],
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stdout + p.stderr
    assert p.stderr.startswith("memory: --depth needs a non-negative integer"), p.stderr
    assert "session mail" not in p.stderr, p.stderr


#: The three scripts a `memory` verb reaches: mhs-thread (ls|tree|cat, and
#: run's path resolution), session-mail-run (`memory run`) and
#: session-mail-wake (`memory wake`).
MEMORY_REACHED = ("mhs-thread", "session-mail-run", "session-mail-wake")

#: A refusal or warning that opens with the retired verb: `sys.exit(`,
#: `print(` or `stderr.write(`, any spacing, then a plain or f/r-prefixed
#: string literal starting `session mail`. A string on the next line counts.
RETIRED_OPENER = re.compile(
    r"""(?:sys\.exit|print|stderr\.write)\s*\(\s*[rRfF]{0,2}["']session mail""")


@pytest.mark.parametrize("name", MEMORY_REACHED)
def test_no_refusal_or_warning_opens_with_the_retired_verb(name):
    """A LOW review finding: after e452133 mhs-thread said `memory:`, but the two
    scripts `memory run` and `memory wake` actually execute still opened every
    refusal and stderr warning with `session mail run:` / `session mail wake:`,
    a verb retired with the session repo. Source-level, so a refusal no test
    can reach cheaply (a missing library, a bash that will not exec) is
    pinned too."""
    text = (Path(_mhs.MHS_DIR) / name).read_text()
    hits = [text.count("\n", 0, m.start()) + 1 for m in RETIRED_OPENER.finditer(text)]
    assert not hits, f"{name}: lines {hits} open with the retired `session mail`"


def test_the_wake_delegation_refusal_blames_the_bundled_script():
    """mhs-thread --wake runs its sibling session-mail-wake, not `session` off
    the PATH, so a failure must not send the user to install the retired
    session repo front-end (a LOW review finding)."""
    text = (Path(_mhs.MHS_DIR) / "mhs-thread").read_text()
    assert "no `session` on PATH" not in text
    assert "session mail-wake verb" not in text
