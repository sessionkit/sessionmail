"""SM-771: a script-mail's SUBJECT never executes.

The memory spec (openspec/specs/memory/spec.md, "A script-mail's subject never
executes"): "the subject after the number is documentation of one shell line;
the body is the program", and its scenario: GIVEN a [WAKE] item whose subject
is `rm -rf ~` and whose body is a placeholder, WHEN `memory run` reaches it,
THEN nothing is executed, because a placeholder body is not a program.

What was wrong (repro-001): session-mail-run fell back to the SUBJECT when the
body was a placeholder (empty, whitespace or `x`) and ran it as `sh -c`. Now
the item is SKIPPED and named, nothing runs for it, every other item still
runs, and the exit is unchanged.

ORDER (fix-002): every MAIL item goes to
--trust FIRST, as SM-785 did; the placeholder check applies only to an item
that PASSES. An untrusted signer's item is REFUSED, exit 3, whatever its body:
"SKIPPED with exit 0 makes a forged item QUIETER than it was before this fix."

NOTHING HERE RUNS `memory run` FOR REAL (SM-530): the SM-785 fixture (temp
HOME, a notmuch stub, marker files) from test_memory_run_trust.py. A subject
that would be executed touches a marker, so the tests see exactly what ran.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from test_memory_run_trust import AGENT, EXECUTOR, HOST, Box, _load, good, needs_ssh, real, stub  # noqa: F401

WHY = "no program: the body is a placeholder; the subject is documentation"


def touch(b: Box, name: str) -> str:
    """A shell line that leaves a marker: the subject a regression would run."""
    return f"touch {b.T}/marks/{name}"


# ── the bug, measured: signed for real, under the REAL verifier ────────────

@pytest.fixture
def placeholders(tmp_path):
    """Owner-signed, owner's cert names `reader` (UNBLESSED: --trust passes).

    10 subject `touch PWNED`        body `x`           -> skipped
    20 subject `touch PWNED_EMPTY`  body empty         -> skipped
    25 subject `touch PWNED_WS`     body whitespace    -> skipped
    30 a real body, no shebang                         -> runs (no shebang rule)
    40 a real body with a shebang                      -> runs
    """
    b = Box(tmp_path)
    b.key("owner")
    b.item("w10", "10 " + touch(b, "PWNED"), "owner", body="x\n")
    b.item("w20", "20 " + touch(b, "PWNED_EMPTY"), "owner", body="")
    b.item("w25", "25 " + touch(b, "PWNED_WS"), "owner", body="   \n\n")
    b.item("w30", "30 control, plain body", "owner", run="plain")
    b.item("w40", "40 control, shebang body", "owner",
           body=f"#!/bin/sh\ntouch {b.T}/marks/shebang\n")
    b.seal("owner")
    b.cert("owner", AGENT)
    return b


@needs_ssh
def test_a_placeholder_body_runs_nothing_and_the_rest_still_run(placeholders):
    b = placeholders
    p = b.run()
    out = p.stdout
    assert b.ran() == ["plain", "shebang"], "a subject was EXECUTED:\n" + out + p.stderr
    assert p.returncode == 0, "a skip leaves the exit unchanged:\n" + out
    for n in ("10", "20", "25"):
        assert f"SKIPPED: item {n} <w{n}@{HOST}> — {WHY}. Nothing was run." in out, out
        head = next(l for l in out.splitlines() if l.startswith(f"═══ {n} "))
        assert f"═══ {n}  # touch " in head and ", SKIPPED: no program)" in head, head
        assert "$ touch" not in head, "the header must not show a subject as a command"
    assert "═══ SKIPPED: item 10, 20, 25 — no program (placeholder body)" in out, out
    assert "put the command in the body" in out


@needs_ssh
def test_json_never_presents_the_subject_as_the_command(placeholders):
    b = placeholders
    p = b.run("--json")
    assert p.returncode == 0, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["skipped_placeholder"] == ["10", "20", "25"]
    by = {s["num"]: s for s in d["sections"]}
    for n in ("10", "20", "25"):
        s = by[n]
        assert s["command"] == "" and s["placeholder"] is True and s["skipped"] == "placeholder", s
        assert s["title"].startswith("touch "), "the subject stays, as documentation"
        assert s["rc"] == -1 and WHY in s["output"]
        # Asked --trust FIRST (fix-002), and it passed: only then a skip.
        assert s["trust"].startswith("TRUST: UNBLESSED"), s
        assert "refused" not in s, s
    assert d["refused_trust"] == []
    assert by["30"]["rc"] == 0 and "placeholder" not in by["30"]
    assert b.ran() == ["plain", "shebang"]


@needs_ssh
def test_list_comments_out_the_skip_and_runs_nothing(placeholders):
    b = placeholders
    p = b.run("--list")
    assert p.returncode == 0, p.stdout + p.stderr
    out = p.stdout
    assert b.ran() == [], "--list runs nothing"
    for n in ("10", "20", "25"):
        assert f"# {n}  budget=600   # SKIPPED — {WHY}, would not run" in out, out
    live = [l for l in out.splitlines() if "/marks/PWNED" in l]
    assert len(live) == 3 and all(l.startswith("#") for l in live), live
    assert f"\ntouch {b.T}/marks/shebang\n" in out, "a real body is still the script"


# ── the spec's own scenario, with the subject defused ──────────────────────

def test_the_spec_scenario_rm_rf_home_executes_nothing(stub):
    """The subject is the spec's literal `rm -rf ~`. A stub `rm` first on
    PATH records a call, so a regression is a marker, not a lost HOME."""
    rm = stub.T / "bin" / "rm"
    rm.write_text(f'#!/bin/sh\necho "$*" > "{stub.T}/marks/rm-called"\n')
    rm.chmod(0o755)
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 rm -rf ~", body="x\n", extra=good("0"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    assert stub.ran() == ["c05"], "the subject ran:\n" + p.stdout + p.stderr
    assert p.returncode == 0
    assert "═══ 10  # rm -rf ~  (" in p.stdout, p.stdout
    # Asked --trust first (fix-002), as every member's mail item is.
    assert [l for l in stub.verify_log() if "--trust" in l and l.split()[0].endswith("w10")]


# ── fix-002: --trust FIRST; an untrusted placeholder is REFUSED, exit 3 ───

UNTRUSTED = "1 TRUST: FAIL leg=2-registry reason=no-cert"


def test_an_untrusted_placeholder_is_refused_exit_3_beside_a_trusted_skip(stub):
    """The order, pinned both ways in one run: the untrusted
    signer's placeholder is REFUSED (exit 3), the trusted one SKIPPED."""
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 " + touch(stub, "PWNED_T"), body="x", extra=good("0"))
    stub.item("w20", "20 " + touch(stub, "PWNED_U"), body="", extra=good(UNTRUSTED))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert stub.ran() == ["c05"], "a subject ran:\n" + out + p.stderr
    assert p.returncode == 3, "an untrusted signer's item is REFUSED whatever its body:\n" + out
    assert f"SKIPPED: item 10 <w10@{HOST}> — {WHY}" in out, out
    assert f"REFUSED: item 20 <w20@{HOST}> is NOT TRUSTED: TRUST: FAIL leg=2-registry" in out, out
    assert f"SKIPPED: item 20" not in out, "a refusal is never told as a skip:\n" + out
    head = next(l for l in out.splitlines() if l.startswith("═══ 20 "))
    assert "═══ 20  # touch " in head and ", REFUSED: NOT TRUSTED)" in head, head
    assert "SKIPPED" not in head, head
    assert "═══ SKIPPED: item 10 — no program" in out, out
    assert "═══ REFUSED: item 20 NOT TRUSTED — not run; every item not refused or skipped above ran (exit 3)" in out, out


def test_an_untrusted_placeholder_alone_is_exit_3_not_0(stub):
    """Before fix-002 this was SKIPPED with exit 0: quieter than a forged
    item with a real body, which is refused with exit 3."""
    stub.item("w10", "10 " + touch(stub, "PWNED"), body="", extra=good(UNTRUSTED))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    assert stub.ran() == [] and p.returncode == 3, p.stdout + p.stderr
    assert f"REFUSED: item 10 <w10@{HOST}> is NOT TRUSTED" in p.stdout
    assert "SKIPPED" not in p.stdout, p.stdout
    assert "every other item did (exit 3)" in p.stdout, p.stdout


def test_an_untrusted_placeholder_in_json(stub):
    stub.item("w10", "10 " + touch(stub, "PWNED_T"), body="x", extra=good("0"))
    stub.item("w20", "20 " + touch(stub, "PWNED_U"), body="x", extra=good(UNTRUSTED))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run("--json")
    assert p.returncode == 3, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["refused_trust"] == ["20"] and d["skipped_placeholder"] == ["10"], d
    by = {s["num"]: s for s in d["sections"]}
    u = by["20"]
    assert u["refused"] == "trust" and u["placeholder"] is True and "skipped" not in u, u
    assert u["command"] == "" and u["rc"] == -1 and u["trust"].startswith("TRUST: FAIL"), u
    assert by["10"]["skipped"] == "placeholder" and "refused" not in by["10"]
    assert stub.ran() == []


def test_an_untrusted_placeholder_in_list(stub):
    stub.item("w10", "10 " + touch(stub, "PWNED_T"), body="x", extra=good("0"))
    stub.item("w20", "20 " + touch(stub, "PWNED_U"), body="x", extra=good(UNTRUSTED))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run("--list")
    out = p.stdout
    assert p.returncode == 3 and stub.ran() == [], out + p.stderr
    assert "# 20  budget=600   # REFUSED — NOT TRUSTED, would not run: TRUST: FAIL" in out, out
    assert f"#   subject: touch {stub.T}/marks/PWNED_U\n" in out, out
    assert f"# 10  budget=600   # SKIPPED — {WHY}, would not run" in out, out
    live = [l for l in out.splitlines() if "/marks/PWNED" in l]
    assert len(live) == 2 and all(l.startswith("#") for l in live), live


def test_an_untrusted_placeholder_a_non_member_tried_to_retire_is_not_said_to_have_run(stub):
    """The fix-001 review's trailer fix, kept for the refusal path: a
    refused item did NOT run, so the WARNING trailer must not name it."""
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 " + touch(stub, "PWNED"), body="x", extra=good(UNTRUSTED))
    stub.item("w11", "retired", extra={"X-Fixture-Sig": "bad", "X-Fixture-Trust": "0",
                                       "Supersedes": f"<w10@{HOST}>"})
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert p.returncode == 3 and stub.ran() == ["c05"], out + p.stderr
    assert f"REFUSED: item 10 <w10@{HOST}> is NOT TRUSTED" in out, out
    assert "item 10 ran" not in out, out


@needs_ssh
def test_a_forged_placeholder_is_refused_under_the_real_verifier(tmp_path):
    """Signed for real: a fresh key with no cert, and a registered intruder
    forging From, each with a placeholder body, are REFUSED (exit 3); the
    owner's placeholder is SKIPPED."""
    b = Box(tmp_path)
    for k in ("owner", "self", "intruder"):
        b.key(k)
    b.item("w10", "10 " + touch(b, "PWNED_OWNER"), "owner", body="x\n")
    b.item("w20", "20 " + touch(b, "PWNED_SELF"), "self", body="")
    b.item("w30", "30 " + touch(b, "PWNED_INTRUDER"), "intruder", body="x\n")
    b.item("w40", "40 control", "owner", run="plain")
    b.seal("owner")
    b.cert("owner", AGENT)
    b.cert("intruder", "intruder")
    p = b.run()
    out = p.stdout
    assert b.ran() == ["plain"], "a subject was EXECUTED:\n" + out + p.stderr
    assert p.returncode == 3, out + p.stderr
    assert f"SKIPPED: item 10 <w10@{HOST}> — {WHY}" in out, out
    for n in ("20", "30"):
        assert f"REFUSED: item {n} <w{n}@{HOST}> is NOT TRUSTED: TRUST: FAIL" in out, out
    d = json.loads(b.run("--json").stdout)
    assert d["refused_trust"] == ["20", "30"] and d["skipped_placeholder"] == ["10"], d


def test_a_non_members_placeholder_keeps_the_non_member_line(stub):
    stub.item("w10", "10 " + touch(stub, "PWNED"), body="x",
              extra={"X-Fixture-Sig": "bad", "X-Fixture-Trust": "0"})
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    assert stub.ran() == [] and p.returncode == 0, p.stdout + p.stderr
    assert "SKIPPED: item 10 is NOT A MEMBER" in p.stdout
    assert json.loads(stub.run("--json").stdout)["skipped_placeholder"] == []


def test_an_empty_title_and_no_program_does_not_crash_the_header(stub):
    """Subject `10 # budget=5` leaves an empty title; with no program the
    command is empty too, and `"".splitlines()[0]` was an IndexError."""
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 # budget=5", body="x", extra=good("0"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Traceback" not in p.stderr
    assert re.search(r"^═══ 10  #   \(≈0 tok", p.stdout, re.M), p.stdout


def test_with_a_fork_the_trailer_does_not_claim_the_skip_ran(stub):
    stub.item("w05", "05 " + touch(stub, "PWNED"), body="x", extra=good("0"))
    stub.item("w10a", "10 copy a", run="a10", extra=good("0"))
    stub.item("w10b", "10 copy b", run="b10", extra=good("0"))
    stub.item("w20", "20 control", run="c20", extra=good("0"))
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert p.returncode == 2 and stub.ran() == ["c20"], out
    assert "every other item did" not in out, out
    assert ("REFUSED: FORK at item 10 — neither copy ran; "
            "every item not refused or skipped above ran (exit 2)") in out, out


def test_a_skip_a_non_member_tried_to_retire_is_not_said_to_have_run(stub):
    """The WARNING trailer says "item N ran though a NON-MEMBER tried to
    retire it". A placeholder item did NOT run, so it must not be named
    there (SM-771 review: the skip path made that line false)."""
    stub.item("w05", "05 control", run="c05", extra=good("0"))
    stub.item("w10", "10 " + touch(stub, "PWNED"), body="x", extra=good("0"))
    stub.item("w11", "retired", extra={"X-Fixture-Sig": "bad", "X-Fixture-Trust": "0",
                                       "Supersedes": f"<w10@{HOST}>"})
    stub.seal(extra={"X-Fixture-Sig": "good"})
    p = stub.run()
    out = p.stdout
    assert p.returncode == 0 and stub.ran() == ["c05"], out + p.stderr
    assert f"SKIPPED: item 10 <w10@{HOST}> — {WHY}" in out, out
    assert "item 10 ran" not in out, out

# ── the ONE program test, used at both item sites ─────────────────────────

@pytest.fixture
def smr(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "nobody")
    return _load()


@pytest.mark.parametrize("body", ["", "x", " x ", "x\n", "   ", "\n\n", None])
def test_program_is_none_for_a_placeholder(smr, body):
    assert smr.program(body) is None


@pytest.mark.parametrize("body", ["echo one", "#!/bin/sh\necho one", "xx", "x; echo"])
def test_program_is_the_body_otherwise_with_no_shebang_rule(smr, body):
    assert smr.program(body) == body


def test_mail_item_never_takes_the_subject(smr):
    it = smr.mail_item("10", "x", title="rm -rf ~", budget=600)
    assert it["command"] == "" and it["placeholder"] is True and it["title"] == "rm -rf ~"
    it = smr.mail_item("10", "echo hi", title="rm -rf ~")
    assert it["command"] == "echo hi" and "placeholder" not in it


def test_both_mail_item_sites_use_the_one_helper():
    """The live item and the verify-timeout superseder (SM-764's block) both
    build through mail_item(); no subject fallback is left in the source."""
    src = EXECUTOR.read_text()
    body = src[src.index("def items_from_mail("):src.index("def nonmember_retirers(")]
    assert body.count("mail_item(") == 2, "both item sites go through mail_item()"
    assert 'not in ("", "x")' not in src
    assert not re.search(r"\belse rest2?\b", src), "a subject fallback is back"


def test_the_docs_no_longer_teach_the_subject_as_a_shell_line():
    from sessionmail import memory
    page = memory.run.epilog
    assert "one shell line" not in page and "never runs" in page
    assert "one shell line; a body" not in EXECUTOR.read_text()
    assert "NEVER executes" in EXECUTOR.read_text()
