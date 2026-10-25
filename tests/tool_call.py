"""A tool's CHILD keeps the suite's seams, or the test says why not (SM-926).

Every tool's handler is `async` since SM-926: it runs its verb in a fresh
process through `_mcp.run_child`. `call` awaits a handler the way the server
does, for a test that holds one (from `_mcp.tools`).

THE CLASS THIS MODULE CLOSES. A test's `monkeypatch.setattr` lives in THE TEST
PROCESS. The verb no longer does: its child imports a fresh, unpatched package.
So every in-process seam a test (or the conftest) set silently stopped applying
to the verb, and a test went on passing while it read the host it was built
never to read. Review r2 measured one: the operator seams
(`_operator.settings_home`, `_operator.account_name`, `rules.locked_path`) read
the PASSWORD DATABASE's home, which no variable moves, and a subscription
refusal named this host's privileged seats instead of the sandbox's.

So the conftest wraps `_mcp.run_child` (`install`, for every test). Before a
child starts, the wrapper reads what THIS TEST's `monkeypatch` has patched and
judges each patch in turn, deciding how the child gets it:

- the environment: inherited by the child, nothing to do;
- `sessionmail._mcp` and its `COMMANDS`: the HANDLER's side, which runs
  in-process, so it is honoured where it is;
- a value the child can be handed: a plain constant on a module (str, number,
  bool, None, a path, a tuple or list of those); a function of nothing in
  `CONSTANT_FNS`, handed over as its one answer; a function the conftest
  registered in `CARRIED_FNS`, rebuilt in the child as the same rule;
- a patch the test's OWN child program sets itself, named in
  `tool_child.covered` (the conftest fixture): the test's word, in the test;
- ANYTHING ELSE is REFUSED: the call raises `SeamNotCarried`, and the test
  fails at teardown even if a server swallowed the raise. A recorder, a fake
  object, a patch on the module the child runs as `__main__`, none of these
  reaches a child, so a test that relied on one was testing nothing. Refused,
  it is visible and the test can be fixed.

What a child is handed is written into it, ahead of the boot under test: the
first lines of its `-c` program set the carried values (`SEAMS`, reading the
JSON the wrapper puts in the first argument), then the program runs UNCHANGED,
`_mcp.CHILD_BOOT` for the package's own commands. A command run another way (a
probe group's file, `SEND_COMMAND`'s `-m`) is not a `-c` program; it is left
alone.

The child runs its command as `__main__` (`runpy`, as `-m` does), a SECOND copy
of that module, so nothing set on `sessionmail.message` reaches the `message`
child's verbs. Such a seam is carried one level down when it is only a
delegate (`DELEGATES`), or answered by an environment backstop the child
already inherits (`CARRIED_FNS`' backstop), and refused otherwise.

Not reached at all: a `monkeypatch.context()` or a hand-made `MonkeyPatch`
(their patches are not this test's fixture's), and `unittest.mock.patch`. No
MCP-touching test uses one; a test that does is outside this net.
"""

from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import anyio

#: Functions of nothing whose ANSWER is the seam: the child gets a function
#: that returns what the patched one returned in this process. Each must be a
#: constant (a path, a name, a set), as the conftest's are.
CONSTANT_FNS = {
    ("sessionmail._operator", "settings_home"),
    ("sessionmail._operator", "account_name"),
    ("sessionmail.rules", "locked_path"),
    ("sessionmail.message", "_live_sessions"),
}

#: A seam on a command's own module (the child runs that module as
#: `__main__`, where a patch on the package's copy never lands), carried to
#: what it does: `message._live_sessions` has no body but `_boxes.live_sessions()`.
DELEGATES = {("sessionmail.message", "_live_sessions"): ("sessionmail._boxes", "live_sessions")}

#: Functions a test module registered, by identity: how the child rebuilds the
#: same rule, ("pid", None) for "this process's pid", ("returns", VALUE) for a
#: constant answer, ("ref", "module:name") for the function itself, imported
#: in the child from a module in tests/ (appended to the END of its path, so
#: it shadows nothing, and taken off again) that imports no command module;
#: and, when the seam sits on the command's own module, the environment
#: backstop that stands for it there (None: refused).
CARRIED_FNS: dict[Callable[..., Any], tuple[str, Any, str | None]] = {}

#: The child's first lines: every carried value, from the JSON the wrapper put
#: in argv[1], lands on its module WHEN THAT MODULE IS IMPORTED (a finder at the
#: head of `sys.meta_path` runs the module's own loader, then sets them), and at
#: once on a module already imported. So the child imports exactly what the
#: program under test imports, and nothing for the seams' sake: a `message`
#: child that never touches `pager` never loads it.
SEAMS = (
    "import importlib as _il, importlib.abc as _ia, json as _js, os as _os, sys as _sy\n"
    "from pathlib import Path as _P\n"
    f"_TESTS = {str(Path(__file__).resolve().parent)!r}\n"
    "_J = _sy.argv.pop(1)\n"
    "_SM926 = {}\n"
    "for _m, _n, _how, _kind, _raw in _js.loads(_J):\n"
    "    _SM926.setdefault(_m, []).append((_n, _how, _kind, _raw))\n"
    "def _sm926_value(how, kind, raw):\n"
    "    v = {'path': _P, 'set': set, 'tuple': tuple}.get(kind, lambda x: x)(raw)\n"
    "    if how == 'returns':\n"
    "        return lambda *a, **k: v\n"
    "    if how == 'pid':\n"
    "        return lambda *a, **k: _os.getpid()\n"
    "    if how == 'ref':\n"
    "        _sy.path.append(_TESTS)\n"
    "        try:\n"
    "            m, _, a = v.partition(':')\n"
    "            return getattr(_il.import_module(m), a)\n"
    "        finally:\n"
    "            _sy.path.remove(_TESTS)\n"
    "    return v\n"
    "def _sm926_apply(module):\n"
    "    for n, how, kind, raw in _SM926.pop(module.__name__, ()):\n"
    "        setattr(module, n, _sm926_value(how, kind, raw))\n"
    "class _SM926Finder(_ia.MetaPathFinder):\n"
    "    def find_spec(self, name, path, target=None):\n"
    "        if name not in _SM926:\n"
    "            return None\n"
    "        for finder in _sy.meta_path:\n"
    "            if finder is not self and hasattr(finder, 'find_spec'):\n"
    "                spec = finder.find_spec(name, path, target)\n"
    "                if spec is not None and spec.loader is not None:\n"
    "                    break\n"
    "        else:\n"
    "            return None\n"
    "        run = spec.loader.exec_module\n"
    "        def exec_module(module, run=run):\n"
    "            run(module)\n"
    "            _sm926_apply(module)\n"
    "        spec.loader.exec_module = exec_module\n"
    "        return spec\n"
    "for _m in [m for m in _SM926 if m in _sy.modules]:\n"
    "    _sm926_apply(_sy.modules[_m])\n"
    "_sy.meta_path.insert(0, _SM926Finder())\n"
)


class SeamNotCarried(AssertionError):
    """A test patched something in-process that the tool's child cannot see."""


def _encode(value: Any) -> tuple[str, Any] | None:
    """VALUE as (kind, JSON), or None when the child cannot be handed it exactly:
    a path, a set or a tuple of JSON, or JSON itself, each only if it comes back
    from JSON EQUAL (an int key, a nested tuple, an object: refused)."""
    if isinstance(value, Path):
        return "path", str(value)
    try:
        if isinstance(value, (set, frozenset)):
            kind, raw = "set", sorted(value)
        elif isinstance(value, tuple):
            kind, raw = "tuple", list(value)
        else:
            kind, raw = "json", value
        back = json.loads(json.dumps(raw))
    except (TypeError, ValueError):
        return None
    return (kind, raw) if back == raw else None


def _patches(monkeypatch) -> list[tuple[object, str, Any]]:
    """(target, name, current value) for every attribute this test patched and has not put back."""
    first: dict[tuple[int, str], tuple[object, str, Any]] = {}
    for target, name, old in monkeypatch._setattr:
        first.setdefault((id(target), name), (target, name, old))
    out = []
    for target, name, old in first.values():
        now = getattr(target, name, old)
        if now is not old:
            out.append((target, name, now))
    return out


def carries(monkeypatch, covered: set[str], entry: str | None) -> list[list[Any]]:
    """What the child of a command whose module is ENTRY must be handed; raise
    SeamNotCarried naming every patch it cannot be."""
    from sessionmail import _mcp

    out: list[list[Any]] = []
    refused: list[str] = []
    for dic, key, _old in monkeypatch._setitem:
        if dic is os.environ or dic is _mcp.COMMANDS or dic is CARRIED_FNS or f"{_name(dic)}[{key!r}]" in covered:
            continue
        refused.append(f"{_name(dic)}[{key!r}] (a dict entry)")
    for target, name, value in _patches(monkeypatch):
        if not isinstance(target, ModuleType):
            if f"{_name(target)}.{name}" not in covered:
                refused.append(f"{_name(target)}.{name} (not a module)")
            continue
        mod = target.__name__
        where = f"{mod}.{name}"
        if mod in (_mcp.__name__, __name__) or where in covered:
            continue
        if mod == entry:
            if (mod, name) in DELEGATES and callable(value):
                got = _encode(value())
                if got is not None:
                    out.append([*DELEGATES[(mod, name)], "returns", *got])
                    continue
            elif callable(value) and value in CARRIED_FNS and CARRIED_FNS[value][2]:
                continue                                  # the env backstop stands for it
            refused.append(f"{where} (the child runs {mod} as __main__, a second copy)")
            continue
        if (mod, name) in CONSTANT_FNS and callable(value):
            got = _encode(value())
            if got is not None:
                out.append([mod, name, "returns", *got])
                continue
        elif callable(value) and value in CARRIED_FNS:
            how, raw, _backstop = CARRIED_FNS[value]
            got = _encode(raw)
            out.append([mod, name, how, *(got or ("json", None))])
            continue
        elif not callable(value) and (got := _encode(value)) is not None:
            out.append([mod, name, "value", *got])
            continue
        refused.append(f"{where} ({type(value).__name__})")
    if refused:
        raise SeamNotCarried(
            "SM-926: the tool's verb runs in a CHILD, and these in-process patches would not "
            "reach it: " + "; ".join(refused) + ". Carry it (tests/tool_call.py), set it from the "
            "environment, or, if the test's own child program sets it, name it in "
            "tool_child.covered.")
    return out


def _name(obj: object) -> str:
    return getattr(obj, "__name__", None) or type(obj).__name__


def seamed(argv: list[str], monkeypatch, covered: set[str]) -> list[str]:
    """ARGV with this test's seams carried into the child, or ARGV itself when
    it is not a `-c` program."""
    from sessionmail import _mcp

    if argv[:3] != [sys.executable, "-P", "-c"] or len(argv) < 4:
        return argv
    entry = argv[4] if argv[3] == _mcp.CHILD_BOOT and len(argv) > 4 else None
    handed = carries(monkeypatch, covered, entry)
    return [*argv[:3], SEAMS + argv[3], json.dumps(handed), *argv[4:]]


#: After SEAMS, a stdio SERVER of its own (`server_argv`): its `run_child`
#: hands every `-c` child the same seams, then the command's module runs as
#: `-m` would run it.
SERVER_BOOT = (
    "from sessionmail import _mcp as _mc\n"
    "_real = _mc.run_child\n"
    "async def _rc(argv, limit=None):\n"
    "    if argv[:3] == [_sy.executable, '-P', '-c'] and len(argv) > 3:\n"
    "        argv = [*argv[:3], _PRE + argv[3], _J, *argv[4:]]\n"
    "    return await _real(argv, limit)\n"
    "_mc.run_child = _rc\n"
    "import runpy\n"
    "runpy.run_module(_sy.argv.pop(1), run_name='__main__', alter_sys=True)\n"
)


def server_argv(name: str, monkeypatch, covered: set[str] = frozenset()) -> list[str]:
    """`python -P -m sessionmail.NAME mcp`, as a stdio SERVER under this
    test's seams: the server process has them, and so does every child it
    starts. A test that drives a real server over a pipe runs this instead,
    so neither reads the host the conftest keeps it from."""
    module = f"sessionmail.{name}"
    handed = json.dumps(carries(monkeypatch, set(covered), module))
    return [sys.executable, "-P", "-c", SEAMS + f"_PRE = {SEAMS!r}\n" + SERVER_BOOT, handed, module, "mcp"]


def install(monkeypatch, covered: set[str], refused: list[str]) -> None:
    """Every `_mcp.run_child` for this test goes through `seamed`. A refusal
    raises, and is also kept in REFUSED for the conftest's teardown, since a
    server may turn the raise into a tool error."""
    from sessionmail import _mcp

    real = getattr(_mcp, "run_child", None)
    if real is None:                    # a tree from before SM-926: no child to seam
        return

    async def run_child(argv: list[str], limit: float | None = None):
        try:
            argv = seamed(argv, monkeypatch, covered)
        except SeamNotCarried as err:
            refused.append(str(err))
            raise
        return await real(argv, limit)

    run_child.__wrapped__ = real
    monkeypatch.setattr(_mcp, "run_child", run_child)


def call(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """FN's reply to KWARGS: its return value, or what it raised."""
    return anyio.run(functools.partial(fn, **kwargs))
