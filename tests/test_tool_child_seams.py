"""SM-926: a tool's CHILD keeps the suite's seams, or the test is told it does not.

Since SM-926 every tool runs its verb in a fresh process (`_mcp.run_child`),
and a test's `monkeypatch` lives in the test process. `tests/tool_call.py`
closes that gap for the whole suite: the conftest wraps `run_child` so the
child is handed every seam this test patched, and REFUSES one it cannot be
handed. These gates hold the net itself:

- every seam the conftest sets reaches a child, the operator ones by value;
- a patch the child cannot be handed fails the call and the test, unless the
  test's own child program says it sets it (`tool_child.covered`);
- a seam on the command's own module (run as `__main__`, a second copy) is
  carried only to what it delegates to, and refused otherwise;
- what the child is handed adds nothing to a reply: byte for byte the reply
  of the same child with nothing handed;
- a real stdio server carries them too (`tool_call.server_argv`).
- a function seam that takes arguments is carried BY REFERENCE, by identity
  (SM-964: `_host.scutil_binary`), and another function on it is refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import anyio
import pytest

import tool_call
from conftest import ACCOUNT_DEFAULT, NO_SEND_ID
from fakes import fake_scutil_only, fake_sign_bytes
from sessionmail import _host, _mcp, _operator, _sender, _seat, message, rules
from test_message_subscription import fleet  # noqa: F401  (a fixture: a synthetic host)
from test_mcp_overlap import _replies, _req

#: A child that reports, for each (module, name) in its JSON argument, what it
#: holds there: a constant's value, or for a function its `__module__` and,
#: where it can be called with nothing, its answer.
REPORT = """
import importlib, json, sys
out = {}
for mod, name in json.loads(sys.argv[1]):
    v = getattr(importlib.import_module(mod), name)
    if callable(v):
        out[f"{mod}.{name}"] = {"module": getattr(v, "__module__", None)}
    else:
        out[f"{mod}.{name}"] = {"value": list(v) if isinstance(v, tuple) else v}
from sessionmail import _operator, _seat, pager, rules
out["answers"] = {"settings_home": str(_operator.settings_home()),
                  "account_name": _operator.account_name(),
                  "locked_path": str(rules.locked_path()),
                  "claude_for": _seat.claude_for("sess-any"),
                  "pid": __import__("os").getpid(),
                  "mta_send": pager._mta_send(None, None)}
print(json.dumps(out))
"""


def _child(program: str, *args: str):
    """PROGRAM as a tool's `-c` child, through the conftest's `run_child`."""
    code, out, err = anyio.run(_mcp.run_child, [sys.executable, "-P", "-c", program, *args])
    assert code == 0, err.decode()
    return out.decode()


def _conftest_seams(monkeypatch) -> list[tuple[str, str]]:
    """(module, name) of every attribute the conftest patched for this test,
    `_mcp`'s own (the handler's side, in-process) left out."""
    return sorted({(t.__name__, n) for t, n, _old in monkeypatch._setattr
                   if hasattr(t, "__name__") and t.__name__ != _mcp.__name__})


def test_every_seam_the_conftest_sets_reaches_a_tools_child(monkeypatch):
    seams = _conftest_seams(monkeypatch)
    assert ("sessionmail._operator", "settings_home") in seams        # the net is not empty
    got = json.loads(_child(REPORT, json.dumps(seams)))
    for mod, name in seams:
        here = getattr(sys.modules[mod], name)
        there = got[f"{mod}.{name}"]
        if callable(here):
            assert there["module"] != mod, f"{mod}.{name} is the package's own in the child"
        else:
            assert there["value"] == (list(here) if isinstance(here, tuple) else here), (mod, name)
    answers = got["answers"]
    assert answers["settings_home"] == str(_operator.settings_home())    # the sandbox's operator
    assert answers["account_name"] == ACCOUNT_DEFAULT
    assert answers["locked_path"] == str(rules.locked_path())
    assert answers["claude_for"] == answers["pid"] != os.getpid()        # "our pid", the child's
    assert answers["mta_send"] == NO_SEND_ID


def test_a_seam_the_child_cannot_be_handed_fails_the_call_and_the_test(monkeypatch, tool_child):
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))           # a lambda: no name to carry
    with pytest.raises(tool_call.SeamNotCarried, match=r"sessionmail\._seat\._ps_table"):
        _child("print('never')")
    assert tool_child.refused and "_ps_table" in tool_child.refused[0]
    tool_child.refused.clear()                                           # this test's own refusal


def test_a_seam_the_tests_own_child_sets_is_named_and_left_to_it(monkeypatch, tool_child):
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    tool_child.covered.add("sessionmail._seat._ps_table")
    assert _child("print('ran')").strip() == "ran"


def test_a_dict_entry_the_child_cannot_see_is_refused(monkeypatch, tool_child):
    table = {"k": 1}
    monkeypatch.setitem(table, "k", 2)
    with pytest.raises(tool_call.SeamNotCarried, match="a dict entry"):
        _child("print('never')")
    tool_child.refused.clear()


def test_a_seam_on_the_commands_own_module_is_carried_to_its_delegate_or_refused(monkeypatch):
    monkeypatch.setattr(message, "_live_sessions", lambda: {"sess-b", "sess-a"})
    handed = tool_call.carries(monkeypatch, set(), "sessionmail.message")
    assert ["sessionmail._boxes", "live_sessions", "returns", "set", ["sess-a", "sess-b"]] in handed
    assert not [h for h in handed if h[0] == "sessionmail.message"]
    monkeypatch.setattr(message, "BOX_LIST_CHARS", 5)
    with pytest.raises(tool_call.SeamNotCarried, match="as __main__"):
        tool_call.carries(monkeypatch, set(), "sessionmail.message")


def test_a_function_is_carried_by_reference_from_a_module_that_imports_no_command(monkeypatch):
    monkeypatch.setitem(tool_call.CARRIED_FNS, fake_sign_bytes, ("ref", "fakes:fake_sign_bytes", None))
    monkeypatch.setattr(_sender, "sign_bytes", fake_sign_bytes)
    got = json.loads(_child(
        "import json, sys; from sessionmail import _sender; "
        "print(json.dumps([_sender.sign_bytes.__module__, _sender.sign_bytes.__name__, "
        f"{os.path.dirname(tool_call.__file__)!r} in sys.path, "
        "[m for m in ('sessionmail.message', 'sessionmail.pager', 'sessionmail.memory') if m in sys.modules]]))"))
    assert got == ["fakes", "fake_sign_bytes", False, []]                 # off the path again



def test_the_macos_names_seam_reaches_a_tools_child_by_reference(monkeypatch, tmp_path):
    """SM-964: the conftest's `_host.scutil_binary` (SM-941) takes an ENV, so no
    one answer stands for it; the child imports the same function from
    tests/fakes.py. It answers the fake on the PATH, and never the host's own
    `scutil`, which the package's real seam falls back to when the PATH has none."""
    fake = tmp_path / "bin" / "scutil"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    host_dir = os.path.dirname(_host.SCUTIL)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{host_dir}")
    got = json.loads(_child(
        "import json, sys; from sessionmail import _host; "
        "print(json.dumps([_host.scutil_binary.__module__, _host.scutil_binary.__name__, "
        f"_host.scutil_binary(), _host.scutil_binary({{'PATH': {host_dir!r}}}), "
        "[m for m in ('sessionmail.message', 'sessionmail.pager', 'sessionmail.memory') if m in sys.modules]]))"))
    assert got == ["fakes", "fake_scutil_only", str(fake), None, []]


def test_the_macos_names_seam_is_carried_by_identity_and_any_other_function_there_is_refused(monkeypatch):
    """The carry names THIS function, not the seam: the guard is not weakened
    for `_host.scutil_binary`, and a test that puts its own function there is
    told so (SeamNotCarried), not quietly handed the conftest's."""
    assert _host.scutil_binary is fake_scutil_only                       # the conftest's, as handed
    handed = tool_call.carries(monkeypatch, set(), None)
    assert ["sessionmail._host", "scutil_binary", "ref", "json", "fakes:fake_scutil_only"] in handed

    def other_scutil(env=None):
        return None

    monkeypatch.setattr(_host, "scutil_binary", other_scutil)
    with pytest.raises(tool_call.SeamNotCarried, match=r"sessionmail\._host\.scutil_binary \(function\)"):
        tool_call.carries(monkeypatch, set(), None)
    handed = tool_call.carries(monkeypatch, {"sessionmail._host.scutil_binary"}, None)
    assert not [h for h in handed if h[:2] == ["sessionmail._host", "scutil_binary"]]  # covered: the test's own

@pytest.mark.parametrize("argv", [["box", "path", "no-such-box-here"], ["list", "--no-such-flag"],
                                  ["box", "list"]])
def test_what_the_child_is_handed_adds_nothing_to_a_reply(monkeypatch, argv):
    """No runpy warning, no line of the prelude's: the reply is the bare child's, byte for byte."""
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(os.environ["SESSION_MAIL_ROOT"]))
    monkeypatch.setattr(message, "_live_sessions", lambda: set())
    command = [*_mcp.COMMANDS["message"], *argv]
    seamed = anyio.run(_mcp.run_child, command)
    bare = anyio.run(_mcp.run_child.__wrapped__, command)
    assert seamed == bare
    assert b"Warning" not in seamed[2]


def test_a_real_stdio_server_and_its_children_carry_the_seams(fleet, monkeypatch, tmp_path):
    """The subscription gate over a PIPE, as an MCP client drives it: the refusal
    names the sandbox's privileged seats (conftest: ivan, judy), so the server's
    child read the sandbox's operator file and not this host's."""
    proc = subprocess.Popen(tool_call.server_argv("message", monkeypatch), cwd=tmp_path,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    buf = bytearray()
    try:
        proc.stdin.write(_req(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "sm926", "version": "0"}}))
        _replies(proc, {1}, time.monotonic() + 30, buf)
        proc.stdin.write(_req(None, "notifications/initialized"))
        proc.stdin.write(_req(2, "tools/call", {"name": "message_subscription_add",
                                                "arguments": {"box_": "atomnote", "seat": True}}))
        got = _replies(proc, {2}, time.monotonic() + 30, buf)[2]["result"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    said = "".join(c.get("text", "") for c in got["content"])
    assert got["isError"] is True and "request it by mail, from a privileged seat" in said, said
    assert "message send ivan " in said, said
    assert fleet.files() == [] and fleet.runs() == 0
