"""message address - the From a send from this shell would carry
(changes/message-address).

`send` takes no `--from`: it works its From out from the environment.
`address` prints that answer, by the functions `send` calls, so a tool that
needs it asks here and keeps no second copy of the rule. Measured before
it existed: a copy in another tool agreed with `_seat.resolve` in 4 of 9
shells, and refused a seat named only by its seat word.

The local part is `_seat.resolve`'s; the domain is the mail origin
(`$SESSIONMAIL_ORIGIN` in the hermetic fixture). `-r ID` is the ratified
reply rule, by `message._reply_from`. A shell that inherited a seat's words
is covered beside its kin, in tests/test_inherited_identity.py."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from conftest import TEST_ORIGIN

from sessionmail import _settings
from sessionmail.message import EX_REFUSED, cli

SID = "0e56fb2f-0000-4000-8000-00000000abcd"


@pytest.fixture
def seat(monkeypatch):
    """A seat named by its seat word alone, as `session start` names one:
    no address variable at all."""
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "Alpha")
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)


def run(*args):
    return CliRunner().invoke(cli, ["address", *args])


def test_a_seat_word_prints_its_address_and_nothing_else(seat):
    r = run()
    assert r.exit_code == 0, r.output
    assert r.stdout == f"alpha@{TEST_ORIGIN}\n"
    assert r.stderr == ""


def test_a_declared_plus_address_wins_over_the_seat_word(seat, monkeypatch):
    """A task seat: its `CLAUDE_CODE_AGENT` is its role, its `EMAIL` its address."""
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "worker")
    monkeypatch.setenv("EMAIL", "Proj+tm-1.worker")
    r = run()
    assert r.exit_code == 0, r.output
    assert r.stdout == f"proj+tm-1.worker@{TEST_ORIGIN}\n"


def test_a_bare_email_beside_a_seat_word_declares_nothing(seat, monkeypatch):
    """A seat that inherited a human's `EMAIL` is still the seat."""
    monkeypatch.setenv("EMAIL", "someone@example.invalid")
    assert run().stdout == f"alpha@{TEST_ORIGIN}\n"


def test_a_session_with_no_seat_prints_its_session_id(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    r = run()
    assert r.exit_code == 0, r.output
    assert r.stdout == f"{SID}@{TEST_ORIGIN}\n"


def test_no_seat_and_no_session_is_refused_in_sends_words(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    r = run()
    assert r.exit_code == EX_REFUSED
    assert r.stdout == ""
    assert "message address: no seat in the environment to send From" in r.stderr


def test_a_plus_email_with_no_seat_names_no_one(monkeypatch):
    """An address variable alone is no identity, as for `send`."""
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setenv("EMAIL", "someone+tag")
    r = run()
    assert r.exit_code == EX_REFUSED and r.stdout == ""


def test_no_origin_prints_the_local_part_bare_and_says_so(seat, tmp_path, monkeypatch):
    """No variable, no setting, and no `postconf` to ask (the hermetic
    fixture already emptied the fallback paths; this empties PATH)."""
    monkeypatch.delenv("SESSIONMAIL_ORIGIN")
    monkeypatch.setenv("PATH", str(tmp_path))
    r = run()
    assert r.exit_code == 0, r.output
    assert r.stdout == "alpha\n"
    assert "the domain is not known" in r.stderr


def test_the_mail_drop_qualifies_with_the_drops_origin(seat, monkeypatch):
    """With the drop as carrier the From is qualified by `_drop.origin`,
    which reads the same variable first."""
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", _settings.CARRIER_DROP)
    monkeypatch.setenv("SESSIONMAIL_ORIGIN", "drop.test.invalid")
    assert run().stdout == "alpha@drop.test.invalid\n"


def test_no_carrier_is_refused_in_the_carriers_words(seat, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "not-a-carrier")
    r = run()
    assert r.exit_code == EX_REFUSED and r.stdout == ""
    assert "message address: no MTA:" in r.stderr and "not a carrier" in r.stderr


# ── -r ID: the reply rule, by its one function ────────────────────────────

def _parent(root, box, *, mid, original_to):
    d = root / box
    for sub in ("tmp", "new", "cur"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    raw = ("\r\n".join([f"X-Original-To: {original_to}",
                        "From: someone@example.invalid",
                        f"To: {box}@example.invalid",
                        "Date: Sun, 09 Jun 2030 12:00:00 +0000",
                        "Subject: parent",
                        f"Message-ID: <{mid}>"]) + "\r\n\r\nbody\r\n")
    (d / "new" / "1923171201.M1P1.host").write_text(raw)
    return mid


@pytest.fixture
def replying(seat, tmp_path, monkeypatch):
    """A session with a seat word AND a session id, and a mail root."""
    root = tmp_path / "mail" / "stores"
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root.parent))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    return root


def test_a_reply_to_a_dm_answers_with_the_session_id(replying):
    mid = _parent(replying, SID, mid="p1@x", original_to=SID)
    r = run("-r", f"<{mid}>")
    assert r.exit_code == 0, r.output
    assert r.stdout == f"{SID}@{TEST_ORIGIN}\n"


def test_a_reply_to_seat_mail_answers_with_the_seat(replying):
    mid = _parent(replying, SID, mid="p2@x", original_to="alpha")
    assert run("-r", mid).stdout == f"alpha@{TEST_ORIGIN}\n"


def test_a_parent_not_in_this_sessions_boxes_falls_back_and_says_why(replying):
    r = run("-r", "<nowhere@x>")
    assert r.exit_code == 0, r.output
    assert r.stdout == f"alpha@{TEST_ORIGIN}\n"
    assert "message address: the parent nowhere@x is not in this session's boxes" in r.stderr


# ── --json: one object, schema 1 ──────────────────────────────────────────

KEYS = {"schema", "address", "kind", "source", "session", "notes"}


def test_json_is_one_object_with_the_six_keys(seat):
    r = run("--json")
    assert r.exit_code == 0, r.output
    got = json.loads(r.stdout)
    assert set(got) == KEYS
    assert got == {"schema": 1, "address": f"alpha@{TEST_ORIGIN}", "kind": "seat",
                   "source": "CLAUDE_CODE_AGENT", "session": "", "notes": []}


def test_json_names_a_plus_address_and_its_variable(seat, monkeypatch):
    monkeypatch.setenv("EMAIL", "proj+tm-1.worker")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    got = json.loads(run("--json").stdout)
    assert (got["kind"], got["source"], got["session"]) == ("plus", "EMAIL", SID)


def test_json_names_a_session_with_no_seat(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    got = json.loads(run("--json").stdout)
    assert (got["kind"], got["source"]) == ("session", "CLAUDE_CODE_SESSION_ID")


def test_json_says_a_dm_reply_came_from_the_parent(replying):
    mid = _parent(replying, SID, mid="p3@x", original_to=SID)
    got = json.loads(run("--json", "-r", mid).stdout)
    assert (got["address"], got["kind"], got["source"]) == (
        f"{SID}@{TEST_ORIGIN}", "session", "X-Original-To")


def test_json_notes_carry_what_stderr_said(replying):
    r = run("--json", "-r", "<nowhere@x>")
    [note] = json.loads(r.stdout)["notes"]
    assert note.startswith("the parent nowhere@x is not in this session's boxes")
    assert note in r.stderr


def test_a_refusal_prints_no_object(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    r = run("--json")
    assert r.exit_code == EX_REFUSED and r.stdout == ""


# ── it reads only ──────────────────────────────────────────────────────────

def test_it_writes_nothing_under_the_mail_root_or_the_home(seat, tmp_path, monkeypatch):
    root, home = tmp_path / "mail", tmp_path / "home"
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(root))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert run("-r", "<nowhere@x>").exit_code == 0
    assert not root.exists() and not home.exists()


# ── hidden: on no group page, and no tool ──────────────────────────────────

def test_the_group_page_does_not_list_it_and_its_own_page_answers():
    group = CliRunner().invoke(cli, ["--help"])
    assert "address" not in group.output
    page = CliRunner().invoke(cli, ["address", "--help"], prog_name="message")
    assert page.exit_code == 0
    assert page.output.startswith("Usage: message address [-r ID] [--json]")


def test_it_is_not_an_mcp_tool():
    from sessionmail import _mcp
    names = {name for name, _handler, _text in _mcp.tools(cli, "message")}
    assert "message_path" in names, "the table is the one that lists the tools"
    assert not any("address" in name for name in names), names
