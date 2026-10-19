"""SESSIONMAIL_HEARS retires (message-subscriptions tasks.md 4.8, design D9).

Design D9 (openspec/changes/message-subscriptions/design.md:300-301): *"the
HEARS variable retires with BOX. It only let a seat listen on its own base
box, and a subscription does that job."* Proposal: *"`_seat.py`: HEARS_VAR
and its readers retire."*

It had two readers at 519e324:

- the `pager listen <base>` refusal (changes/task-address-doorbell D5) took
  it as the one way a plus seat could listen on its base. The refusal
  STAYS (D5 stands, and this change does not retire it); only the
  way round it changes: the bare verb, plus `message subscription add
  <base>`, the replacement D9 names.
- `own_addresses`, which `message send -r` asks which address a reply is
  sent FROM. A base the session used to "hear" is no longer one of its
  own, so a reply to mail delivered there falls back to the declared seat
  address and SAYS so (specs/message "The parent was delivered to an
  address this session does not answer to").

4.8b's boundary is tested beside it: `--timeout` stays HONOURED here,
because taskmaster (tasks.md 5.2, its own repo) still prints `pager listen
... --timeout 15`.
"""
from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner

from sessionmail import _seat, pager

SRC = Path(__file__).resolve().parents[1] / "src" / "sessionmail"
RETIRED = "SESSIONMAIL_" + "HEARS"      # split, so this file is not a hit of its own grep
EX_TIMEOUT = 124


def test_no_source_file_reads_or_names_the_retired_variable():
    """tasks.md 4.8: the variable and its readers retire - not one line of
    src names it, nor the `_seat.HEARS_VAR` constant that carried it."""
    hits = [f"{p.relative_to(SRC)}:{n}"
            for p in sorted(SRC.rglob("*")) if p.is_file() and p.suffix in ("", ".py", ".sh")
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
            if RETIRED in line or re.search(r"\bHEARS_VAR\b", line)]
    assert hits == [], hits
    assert not hasattr(_seat, "HEARS_VAR")
    assert not hasattr(_seat, "hears")


def test_the_retired_variable_adds_no_address_this_session_answers_to():
    """A base the variable named is not one of the session's own addresses
    any more: a reply to mail delivered there is not sent FROM it."""
    env = {"TM_AGENT_ADDRESS": "sessionmail+sm-1.foreman", "CLAUDE_CODE_SESSION_ID": "sess-h",
           RETIRED: "sessionmail, voicemode"}
    own = _seat.own_addresses(env)
    assert "sessionmail" not in own and "voicemode" not in own, own
    assert "sessionmail+sm-1.foreman" in own and "sess-h" in own, own
    assert _seat.answers_to("sessionmail", env) == ""
    assert _seat.answers_to("sessionmail+sm-1.foreman", env) == "sessionmail+sm-1.foreman"


def test_the_refusal_no_longer_has_a_door_through_the_retired_variable(monkeypatch):
    """D5's refusal holds whatever the environment says: only the identity
    and BOX decide it now. The variable is SET here, naming the base, which
    at 519e324 opened the door."""
    monkeypatch.setenv(RETIRED, "voicemode, Sessionmail")
    seat = _seat.Seat(name="sessionmail", source="TM_AGENT_ADDRESS",
                      root=Path("/nonexistent"), address="sessionmail+sm-1.foreman")
    assert _seat.refuses_base(seat, "sessionmail") == "sessionmail"
    assert _seat.refuses_base(seat, "voicemode") is None


# ── 4.8b: --timeout stays HONOURED (the boundary) ───────────────────────

def test_timeout_is_still_honoured_while_taskmaster_calls_it(tmp_path, monkeypatch):
    """tasks.md 4.8b is RULED (spine 10.34) but NOT done here: taskmaster
    5.2 still prints `pager listen ... --timeout 15`, so removing the flag
    would leave every dispatch line it prints a usage error. Until 5.2
    lands, `--timeout S` (and `-t S`) returns 124 with nothing paged."""
    root = tmp_path / "mail"
    root.mkdir()
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-t")
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    for flag in ("--timeout", "-t"):
        result = CliRunner().invoke(pager.cli, ["listen", flag, "0.1"], prog_name="pager")
        assert result.exit_code == EX_TIMEOUT, (flag, result.output)
        assert "returned - nothing paged in 0.1s" in result.stderr
