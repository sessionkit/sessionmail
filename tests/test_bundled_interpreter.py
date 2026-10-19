"""SM-878: the bundled scripts run on the package's interpreter, never PATH's `python3`.

The defect: `_mhs.oracle()` handed back `[script]` and `_mhs.run()` executed
it, so the script's `#!/usr/bin/env python3` picked the FIRST `python3` on
PATH. The bash shims (`session-mail-ls|tree|cat`) exec'd `mhs-thread` by path,
and the verifier (`session-mail-verify`) ran `python3 -I -`: PATH again. On a
stock macOS that `python3` is the system 3.9, while the package needs 3.11 or
later (`requires-python`), so `memory run` died at `import tomllib`.

The fix: a Python script runs as `[sys.executable, script]`, and whoever
starts a bundled script sets $SESSIONMAIL_PYTHON to its own interpreter for
the bash ones, which have no `sys.executable` (`_mhs.PYTHON_VAR`).

The rig puts a STUB `python3` first on PATH. It writes a marker file and
exits 97, so it can stand in for any interpreter too old to run the scripts,
with no system Python needed. Each verb is run through the package
(`sys.executable -m sessionmail.<cmd>`), the way an installed entry point
runs it:

  - `memory run --list` (session-mail-run, then mhs-thread from inside it),
    falling back to a `wake.toml` so the `tomllib` import is reached;
  - `memory ls` (the bash shim, then mhs-thread, then the verifier for
    membership);
  - `memory wake` (session-mail-wake);
  - `message verify FILE` (the verifier, run directly).

Two lanes run every scenario:

  - CONTROL: the `python3` on PATH logs its call and execs the package's own
    interpreter. The scenario must work. This is the instrument check: it
    proves the expected output is right, so a STUB-lane failure is the
    interpreter and not a broken rig.
  - STUB: the `python3` on PATH is the stub. The scenario must still work and
    the marker must not exist. (Committed red as a strict xfail by repro-001;
    the fix removed the marker.)

Beside the two lanes: the pager's own verifier call (`_voice.keeper_signed`,
which does not go through `_mhs.run`), an inherited stale value, a named
interpreter that is not there (loud, never a quiet fall back to PATH), the
user's `sh -c` items (which never see the variable), and a static guard over
the sources, so a new call site cannot quietly start a Python script by its
shebang again.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _mhs
from test_memory_oracle import NOTMUCH_STUB, _exe, _thread

#: The stub's exit code: anything a test would not see by accident.
STUB_RC = 97

STUB_PYTHON = """#!/bin/sh
echo "$0 $*" >> {marker}
exit {rc}
"""

#: The control lane's python3: it logs, then runs the package's interpreter.
PASS_PYTHON = """#!/bin/sh
echo "$0 $*" >> {log}
exec {python} "$@"
"""

AGENT = "reader"

WAKE_TOML = """[section.hello]
order = 10
command = "echo hello-from-wake-toml"
"""

UNSIGNED = """From: reader@host1
To: reader+memory@host1
Subject: unsigned
Message-ID: <unsigned@host1>
Date: Sun, 09 Jun 2030 12:34:56 +0000

body
"""


def _rig(tmp_path: Path, lane: str) -> tuple[dict[str, str], Path]:
    """A bin dir first on PATH holding the lane's `python3` and the notmuch
    stub, and an environment for the child. Returns (env, marker)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    marker = tmp_path / "python3-called.log"
    if lane == "stub":
        _exe(bin_ / "python3", STUB_PYTHON.format(marker=marker, rc=STUB_RC))
    else:
        _exe(bin_ / "python3", PASS_PYTHON.format(log=tmp_path / "control.log",
                                                  python=sys.executable))
    root_json, thread_json = _thread(tmp_path)
    _exe(bin_ / "notmuch", NOTMUCH_STUB.format(root=root_json, thread=thread_json))
    # The package sets the variable for its children itself; an inherited
    # one is scrubbed so each lane starts from a stranger's environment.
    env = {k: v for k, v in os.environ.items() if k != _mhs.PYTHON_VAR}
    env.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", CLAUDE_CODE_AGENT=AGENT,
               SESSION_AGENT="")
    return env, marker


def _run_list(tmp_path: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """`memory run --list` on a label that is not there: it falls back to
    ~/.reader/wake.toml, which session-mail-run parses with `tomllib`."""
    home = Path(env["HOME"])
    (home / f".{AGENT}").mkdir(parents=True, exist_ok=True)
    (home / f".{AGENT}" / "wake.toml").write_text(WAKE_TOML)
    return ["memory", "run", "/BOOT/WAKE", "--list"], "echo hello-from-wake-toml"


def _ls(tmp_path: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """`memory ls /` on a three-mail unsigned thread: the shim runs
    mhs-thread, which asks the verifier for [RULES]'s membership."""
    env["READER_ROOT"] = "root@host1"
    return ["memory", "ls", "/"], "⊘ NOT A MEMBER: [RULES]"


def _wake(tmp_path: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """`memory wake alpha` against an empty sandbox box."""
    root = Path(env["SESSION_MAIL_ROOT"])
    for sub in ("new", "cur", "tmp"):
        (root / "alpha" / sub).mkdir(parents=True, exist_ok=True)
    return ["memory", "wake", "alpha"], "═══ COUNTS ═══"


def _verify(tmp_path: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """`message verify FILE` on an unsigned mail: exit 2, from the verifier."""
    mail = tmp_path / "unsigned.eml"
    mail.write_text(UNSIGNED)
    return ["message", "verify", str(mail)], "no X-Session-Signature header"


#: name -> (builder, the exit code the scenario must end with)
SCENARIOS = {
    "memory-run-list": (_run_list, 0),
    "memory-ls": (_ls, 0),
    "memory-wake": (_wake, 0),
    "message-verify": (_verify, 2),
}


def _go(tmp_path: Path, lane: str, name: str):
    env, marker = _rig(tmp_path, lane)
    build, want_rc = SCENARIOS[name]
    (cmd, *args), want_out = build(tmp_path, env)
    p = subprocess.run([sys.executable, "-m", f"sessionmail.{cmd}", *args],
                       capture_output=True, text=True, env=env, timeout=60)
    return p, marker, want_rc, want_out


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_control_each_verb_works_when_path_python3_is_the_package_interpreter(tmp_path, name):
    """The instrument check: with a working `python3` first on PATH, every
    scenario gives its expected exit and output, today and after the fix."""
    p, _marker, want_rc, want_out = _go(tmp_path, "control", name)
    assert p.returncode == want_rc, p.stdout + p.stderr
    assert want_out in p.stdout + p.stderr, p.stdout + p.stderr


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_each_verb_runs_on_the_package_interpreter_not_path_python3(tmp_path, name):
    """A `python3` first on PATH that cannot run the scripts (here a stub that
    exits 97; on a clean Mac, the system 3.9) must not be reached at all."""
    p, marker, want_rc, want_out = _go(tmp_path, "stub", name)
    called = marker.read_text() if marker.exists() else ""
    assert not called, f"PATH python3 was run:\n{called}\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}"
    assert p.returncode == want_rc, p.stdout + p.stderr
    assert want_out in p.stdout + p.stderr, p.stdout + p.stderr


def _called(marker: Path) -> str:
    return marker.read_text() if marker.exists() else ""


def test_a_stale_inherited_value_is_overwritten_not_used(tmp_path):
    """$SESSIONMAIL_PYTHON is SET by the package, never read as a default:
    one inherited from elsewhere, naming the stub, never reaches a script."""
    env, marker = _rig(tmp_path, "stub")
    env[_mhs.PYTHON_VAR] = str(tmp_path / "bin" / "python3")      # the stub itself
    (cmd, *args), want_out = _ls(tmp_path, env)
    p = subprocess.run([sys.executable, "-m", f"sessionmail.{cmd}", *args],
                       capture_output=True, text=True, env=env, timeout=60)
    assert not _called(marker), _called(marker)
    assert p.returncode == 0, p.stdout + p.stderr
    assert want_out in p.stdout + p.stderr


def test_a_named_interpreter_that_is_not_there_fails_loudly(tmp_path):
    """A by-hand run of the verifier with $SESSIONMAIL_PYTHON naming nothing
    exits 126/127, which every caller reads as "could not be verified". It
    never falls back to PATH's python3, which would hide the stale value."""
    env, marker = _rig(tmp_path, "stub")
    env[_mhs.PYTHON_VAR] = str(tmp_path / "gone" / "python3")
    mail = tmp_path / "unsigned.eml"
    mail.write_text(UNSIGNED)
    p = subprocess.run([os.path.join(_mhs.MHS_DIR, "session-mail-verify"), str(mail)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode in (126, 127), (p.returncode, p.stderr)
    assert not _called(marker), "fell back to PATH's python3"


def test_the_users_items_never_see_the_variable(tmp_path):
    """`memory run` sets the variable for the scripts it starts, and drops it
    for the user's `sh -c` items: a value set for this run never outlives it
    in a process of the user's."""
    env, marker = _rig(tmp_path, "stub")
    home = Path(env["HOME"])
    (home / f".{AGENT}").mkdir(parents=True, exist_ok=True)
    (home / f".{AGENT}" / "wake.toml").write_text(
        '[section.probe]\norder = 10\ncommand = "echo probe=${SESSIONMAIL_PYTHON-unset}"\n')
    env["SESSIONMAIL_MEMORY_RUN"] = "1"
    p = subprocess.run([sys.executable, "-m", "sessionmail.memory", "run", "/BOOT/WAKE"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert not _called(marker), _called(marker)
    assert "probe=unset" in p.stdout, p.stdout + p.stderr


# == the pager's verifier call: its own subprocess, not _mhs.run ================

@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on this machine")
def test_keeper_signed_runs_the_verifier_on_the_package_interpreter(tmp_path):
    """`_voice.keeper_signed` starts the verifier itself, so it must name the
    interpreter itself: with the stub first on PATH, a keeper turn signed by
    the blessed keeper cert is still trusted, and the stub is never run."""
    import test_voice_signed as vs
    from sessionmail import _voice
    (tmp_path / "rig").mkdir()
    rig = vs.Rig(tmp_path / "rig")
    reg = rig.registry("keeper", {rig.keeper: "heard"})
    raw = vs.v2(rig.keeper, vs.turn())
    env = rig.env(reg)
    env.pop(_mhs.PYTHON_VAR, None)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    marker = tmp_path / "python3-called.log"
    _exe(bin_ / "python3", STUB_PYTHON.format(marker=marker, rc=STUB_RC))
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    assert _voice.keeper_signed(rig.file(raw), vs.parse(raw), env) is True
    assert not _called(marker), _called(marker)


# == the static guard ===========================================================

MHS = Path(_mhs.MHS_DIR)


def _shebang(path: Path) -> str:
    with open(path, "rb") as fh:
        return fh.readline().decode("utf-8", "replace").strip()


def _python_scripts() -> set[str]:
    return {p.name for p in MHS.iterdir()
            if p.is_file() and _shebang(p).startswith("#!") and "python" in _shebang(p)}


def _bash_scripts() -> set[str]:
    return {p.name for p in MHS.iterdir()
            if p.is_file() and _shebang(p).startswith("#!") and "bash" in _shebang(p)}


def test_python_verbs_is_the_python_shebangs():
    """`oracle` runs exactly the Python `session-mail-VERB` scripts behind
    sys.executable: the set it keeps is the set the shebangs say."""
    by_shebang = {n[len("session-mail-"):] for n in _python_scripts()
                  if n.startswith("session-mail-")}
    assert by_shebang == set(_mhs.PYTHON_VERBS)
    assert "mhs-thread" in _python_scripts(), "the guard below assumes the reader is Python"


def _sources() -> list[Path]:
    pkg = Path(_mhs.__file__).parent
    return sorted(pkg.glob("*.py")) + sorted(MHS / n for n in _python_scripts())


def _mentions(node: ast.AST, scripts: set[str], names: set[str]) -> bool:
    """NODE names a Python mhs script: a string holding its name, or a
    variable this file bound to one."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and \
                any(re.search(rf"(^|[/\-\s]){re.escape(s)}$", sub.value) or sub.value == s
                    for s in scripts):
            return True
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
    return False


def _is_sys_executable(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "executable"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _command_names_a_script(node: ast.AST, scripts: set[str], names: set[str]) -> bool:
    """An item's `"command"` (run by `sh -c`, so by its shebang) whose FIRST
    word is a Python mhs script. A `"title"` is display only, not checked."""
    if not isinstance(node, ast.Dict):
        return False
    return any(isinstance(k, ast.Constant) and k.value == "command"
               and _first_word_names_a_script(v, scripts, names)
               for k, v in zip(node.keys, node.values))


def _first_word_names_a_script(node: ast.AST, scripts: set[str], names: set[str]) -> bool:
    """An f-string whose FIRST word is a Python mhs script."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return False
    head = node.values[0]
    if isinstance(head, ast.FormattedValue):
        return _mentions(head.value, scripts, names)
    return isinstance(head, ast.Constant) and any(str(head.value).startswith(s) for s in scripts)


def test_no_source_starts_a_python_mhs_script_by_its_shebang():
    """Every argv (a list or tuple literal) whose FIRST element is a Python
    mhs script, by name or through a variable bound to one, has
    `sys.executable` in front of it instead; and no item's command starts
    with one. A script's shebang is PATH's python3, which is the defect."""
    scripts = _python_scripts()
    bad = []
    for path in _sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        names = set()
        for node in ast.walk(tree):     # names bound to a script's path
            if isinstance(node, ast.Assign) and _mentions(node.value, scripts, set()):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                # An argv's first element is a path: a name, a call or an
                # attribute (a literal string or a dict is not an argv).
                if isinstance(first, (ast.Name, ast.Call, ast.Attribute)) \
                        and not _is_sys_executable(first) and _mentions(first, scripts, names):
                    bad.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:100]}")
            if _command_names_a_script(node, scripts, names):
                bad.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:100]}")
    assert not bad, "a Python mhs script started by its shebang:\n" + "\n".join(bad)


def test_the_guard_catches_the_shapes_it_was_built_for():
    """The instrument check for the guard above: the defect's own shapes, as
    they stood before the fix, are caught."""
    scripts = _python_scripts()
    src = ('PT = os.path.join(d, "mhs-thread")\n'
           'a = [PT, "--path", p]\n'
           'b = [os.path.join(_HERE, "session-mail-wake"), agent]\n'
           'c = [{"command": f"{PT} --boot", "title": f"{PT} --boot"}]\n'
           'ok = [sys.executable, PT, "--boot"]\n'
           'ok2 = [{"command": f"{shlex.quote(sys.executable)} {PT} --boot"}]\n')
    tree = ast.parse(src)
    names = {"PT"}
    caught = [n.lineno for n in ast.walk(tree)
              if (isinstance(n, ast.List) and isinstance(n.elts[0], (ast.Name, ast.Call))
                  and _mentions(n.elts[0], scripts, names))
              or _command_names_a_script(n, scripts, names)]
    assert sorted(caught) == [2, 3, 4]


def test_no_bash_script_runs_python_by_its_bare_name():
    """In the bundled bash: every `exec` of a Python mhs script names
    $SESSIONMAIL_PYTHON in front, and every python3 is
    `${SESSIONMAIL_PYTHON:-python3}`. Comments are skipped."""
    scripts = _python_scripts()
    bad = []
    for name in sorted(_bash_scripts()):
        for n, line in enumerate((MHS / name).read_text().splitlines(), 1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            if re.search(r"\bexec\b", code) and any(s in code for s in scripts) \
                    and '${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"}' not in code:
                bad.append(f"{name}:{n}: {line.strip()}")
            if re.search(r"(?<![\w:-])python3\b", code.replace("${SESSIONMAIL_PYTHON:-python3}", "")):
                bad.append(f"{name}:{n}: {line.strip()}")
    assert not bad, "bash that runs python by PATH:\n" + "\n".join(bad)


# == the by-hand shape: an old python3 refuses in one line ======================

#: script -> the `memory` verb its refusal names (mhs-thread: from its argv)
GUARDED = {"session-mail-run": ("/BOOT/WAKE", "run"),
           "session-mail-wake": ("alpha", "wake"),
           "mhs-thread": ("tree", "tree")}

#: Run SCRIPT as __main__ with sys.version_info reporting 3.9.6: the guard's
#: logic, on any host. The script never gets past its guard.
FAKE_39 = """import runpy, sys
sys.version_info = (3, 9, 6, "final", 0)
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")
"""


def _want(name: str, version: str, verb: str) -> str:
    return (f"{name}: needs Python 3.11 or later (this is {version}); run it through "
            f"`memory {verb}`, which uses sessionmail's own Python")


@pytest.mark.parametrize("name", sorted(GUARDED))
def test_a_by_hand_run_on_an_old_python_refuses_in_one_line(tmp_path, name):
    """A Python older than requires-python (here the version is faked) gets
    exit 2 and ONE stderr line naming the script, its version and the
    `memory` verb to use instead: never a tomllib traceback."""
    arg, verb = GUARDED[name]
    env = {k: v for k, v in os.environ.items() if k != _mhs.PYTHON_VAR}
    p = subprocess.run([sys.executable, "-c", FAKE_39, str(MHS / name), arg],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert p.stderr.splitlines() == [_want(name, "3.9.6", verb)], p.stderr
    assert p.stdout == ""


SYS_PY = "/usr/bin/python3"


def _sys_py_version() -> tuple[int, ...] | None:
    if not os.access(SYS_PY, os.X_OK):
        return None
    out = subprocess.run([SYS_PY, "-c", "import sys; print(*sys.version_info[:3])"],
                         capture_output=True, text=True, timeout=30).stdout.split()
    return tuple(int(x) for x in out) if len(out) == 3 else None


@pytest.mark.parametrize("name", sorted(GUARDED))
def test_the_guard_parses_and_refuses_on_a_real_old_python(tmp_path, name):
    """The same, on a REAL interpreter below 3.11 where this host has one
    (the system python3 on a stock macOS). That also proves the guard
    is written in syntax the old interpreter can parse."""
    v = _sys_py_version()
    if v is None or v >= (3, 11):
        pytest.skip(f"no {SYS_PY} older than 3.11 here ({v})")
    arg, verb = GUARDED[name]
    p = subprocess.run([SYS_PY, str(MHS / name), arg], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert p.stderr.splitlines() == [_want(name, ".".join(map(str, v)), verb)], p.stderr


def test_the_guard_is_a_no_op_for_an_in_process_load():
    """session-mail-run loads mhs-thread in-process (SourceFileLoader, the
    product's one in-process load); on this interpreter (3.11 or later) the
    guard passes and nothing exits."""
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("sm878_mhs_thread", str(MHS / "mhs-thread"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)          # a SystemExit here would fail the test
    assert callable(mod.effective_children)
