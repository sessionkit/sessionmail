"""SM-538 verify-001: the live-shaped check from design s12.

One 72-char subject goes through all three doors (the CLI, `--json` and the
MCP tool) with the host switch ON. The three delivered files must be
byte-identical apart from Message-ID and Date.

Every door here is a REAL CHILD PROCESS running the real `message send`.
The MCP door's child is the one `_mcp` spawns itself, not a fake. The locked
file has no environment seam on purpose (rules.py `account_home`, D3), so
the one seam crosses the process boundary in a small wrapper. The wrapper
points `rules.locked_path` at the sandbox's file and then runs the real
CLI, so the host's own file is never read or written. With the wrapper
absent, the MCP child reads the host's file, which is the 9994ef61 caveat.
`test_the_mcp_child_reads_the_locked_file_not_the_parents_seam` pins that.
"""

from __future__ import annotations

import email
import email.policy
import os
import re
import subprocess
import sys

import anyio
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from sessionmail import _mcp, message
from test_message_send_json import LONG, world  # noqa: F401  (the fixture)

WRAPPER = """\
import os, sys
from pathlib import Path
from sessionmail import rules
rules.locked_path = lambda: Path(os.environ["VERIFY_LOCKED"])
from sessionmail.message import cli
cli(args=sys.argv[1:], prog_name="message")
"""

#: The two headers that differ between any two sends, whatever the door.
VARYING = re.compile(rb"^(Message-ID|Date): .*\r?\n", re.MULTILINE | re.IGNORECASE)


def _mcp_send(args):
    server = _mcp.server(message.cli, "message")

    async def main():
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("message_send", args)

    return anyio.run(main)


def _setup(world, monkeypatch, *, host_on=True):  # noqa: F811
    locked = world.tmp / "locked" / "rules.locked"
    locked.parent.mkdir()
    if host_on:
        locked.write_text("set subject-shorten on\n")
    monkeypatch.setenv("VERIFY_LOCKED", str(locked))
    wrapper = world.tmp / "message-wrapper.py"
    wrapper.write_text(WRAPPER)
    return wrapper


def _door(world, monkeypatch, wrapper, door):  # noqa: F811
    """Send LONG through DOOR in a child process; return (exit, stdout, stderr, wire)."""
    wire = world.tmp / f"wire-{door}"
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    if door == "mcp":
        monkeypatch.setattr(_mcp, "SEND_COMMAND", [sys.executable, str(wrapper), "send"])
        result = _mcp_send({"to": ["beta"], "subject": LONG, "body": "dear beta\n"})
        texts = [getattr(c, "text", "") for c in result.content]
        return ((1 if result.is_error else 0), texts[0], "\n".join(texts[1:]),
                wire.read_bytes() if wire.exists() else None)
    argv = [sys.executable, str(wrapper), "send", "beta", "-s", LONG, "-f", str(world.body)]
    p = subprocess.run([*argv, *(["--json"] if door == "json" else [])],
                       capture_output=True, text=True, env=dict(os.environ), timeout=60)
    return p.returncode, p.stdout, p.stderr, wire.read_bytes() if wire.exists() else None


def test_one_72_char_subject_through_three_doors_delivers_one_message(world, monkeypatch):  # noqa: F811
    wrapper = _setup(world, monkeypatch)
    sent = {door: _door(world, monkeypatch, wrapper, door) for door in ("cli", "json", "mcp")}
    for door, (code, out, err, wire) in sent.items():
        assert code == 0 and wire, (door, out, err)
    # The CLI's stdout is the Message-ID alone; the report is on stderr.
    code, out, err, _ = sent["cli"]
    assert re.fullmatch(r"<[^>\s]+>\n", out), out
    assert "message send: shortened 72->" in err, err
    # The three files, with Message-ID and Date taken out, are one file.
    bodies = {door: VARYING.sub(b"", wire) for door, (_, _, _, wire) in sent.items()}
    assert bodies["cli"] == bodies["json"] == bodies["mcp"], bodies
    parsed = email.message_from_bytes(sent["cli"][3], policy=email.policy.default)
    assert len(parsed["Subject"]) <= 69 and parsed["Subject"].isascii(), parsed["Subject"]
    assert parsed["X-Original-Subject"] == LONG


def test_the_mcp_child_reads_the_locked_file_not_the_parents_seam(world, monkeypatch):  # noqa: F811
    """The caveat (9994ef61): the MCP tool's child is a new process, so a
    parent-side patch of `rules.locked_path` does not reach it. The child
    reads the file the wrapper names, and that file is OFF here, so the
    send is refused exactly as today (kind subject, exit 2)."""
    wrapper = _setup(world, monkeypatch, host_on=False)
    monkeypatch.setattr("sessionmail.rules.locked_path",
                        lambda: world.tmp / "parent-only" / "rules.locked")
    (world.tmp / "parent-only").mkdir()
    (world.tmp / "parent-only" / "rules.locked").write_text("set subject-shorten on\n")
    code, out, err, wire = _door(world, monkeypatch, wrapper, "mcp")
    assert code == 1 and not wire, (out, err)
    assert "subject" in out + err, (out, err)
