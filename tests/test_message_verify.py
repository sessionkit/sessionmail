"""`message verify` - the retired `session mail verify`, at the message door.

The verb lives in `message`, is public, takes a row number,
a Message-ID or a file, and keeps the old exit codes and `--trust`.

Hermetic: $SESSIONMAIL_MHS_ORACLE names a fake verifier that logs the argv
it got and exits as told, so what is pinned here is the DOOR - which file
it hands over, which flags, and that the verifier's exit code comes back
untouched. The verifier's own judgement on real signed mail is proven in
test_sender.py, against the copy this package ships.
"""

from __future__ import annotations

import stat
import sys

import pytest
from click.testing import CliRunner

from sessionmail.message import cli
from test_message_list import deliver, box  # noqa: F401  (fixture reused)

FAKE = """#!{python}
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
sys.stdout.write("verifier says: " + " ".join(sys.argv[2:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_RC") or 0))
"""

#: The verifier's usage bucket - and, here, the code for "no such message".
EX_USAGE = 3


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    fake = tmp_path / "fake-verify"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "oracle.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(fake))
    monkeypatch.delenv("FAKE_RC", raising=False)

    class Oracle:
        @property
        def calls(self):
            if not log.exists():
                return []
            return [line.split("\x1f") for line in log.read_text().splitlines()]

    return Oracle()


def run(*args):
    return CliRunner().invoke(cli, ["verify", *args])


def test_a_row_number_hands_the_verifier_the_file_read_would_open(box, oracle):
    deliver(box, "alpha", "1923171201", subject="first")
    deliver(box, "alpha", "1923171202", subject="second")
    r = run("2")
    assert r.exit_code == 0, r.output
    (call,) = oracle.calls
    assert call[0] == "verify"                       # the named oracle gets the verb
    assert call[1] == str(box / "alpha" / "new" / "1923171202.M1P1.host")
    assert call[2:] == []                            # plain mode: no flags added


def test_an_id_bare_or_bracketed_names_the_same_file(box, oracle):
    mid = deliver(box, "alpha", "1923171201")
    assert run(mid).exit_code == 0
    assert run(f"<{mid}>").exit_code == 0
    assert oracle.calls[0][1] == oracle.calls[1][1]


def test_a_box_first_reads_that_box(box, oracle):
    deliver(box, "beta", "1923171209", subject="beta's")
    assert run("beta", "1").exit_code == 0
    assert oracle.calls[0][1].endswith("/beta/new/1923171209.M1P1.host")


def test_a_path_with_a_slash_is_a_file_and_needs_no_box(tmp_path, oracle, monkeypatch):
    for var in ("TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    f = tmp_path / "some.eml"
    f.write_text("Subject: x\n\nbody\n")
    r = run(str(f), "--trust", "--quiet")
    assert r.exit_code == 0, r.output
    assert oracle.calls == [["verify", str(f), "--trust", "--quiet"]]


@pytest.mark.parametrize("rc", [0, 1, 2, 4, 5])
def test_the_verifiers_exit_code_comes_back_untouched(box, oracle, monkeypatch, rc):
    """The contract callers parse: plain 0 good, 1 bad, 2 unsigned; --trust
    0 FULL, 1 FAIL, 4 UNBLESSED, 5 ERROR. Nothing is re-mapped."""
    deliver(box, "alpha", "1923171201")
    monkeypatch.setenv("FAKE_RC", str(rc))
    r = run("1", "--trust")
    assert r.exit_code == rc, r.output
    assert "verifier says:" in r.output              # its stdout, passed through


def test_require_blessing_is_passed_on_for_the_verifier_to_judge(box, oracle):
    deliver(box, "alpha", "1923171201")
    run("1", "--trust", "--require-blessing")
    assert oracle.calls[0][2:] == ["--trust", "--require-blessing"]


def test_no_such_message_is_3_never_2_which_means_unsigned(box, oracle):
    deliver(box, "alpha", "1923171201")
    r = run("7")
    assert r.exit_code == EX_USAGE and "index spans 1..1" in r.output
    assert run("nobody@example.invalid").exit_code == EX_USAGE
    assert run("gamma", "1").exit_code == EX_USAGE   # no such box
    assert run("./no/such/file.eml").exit_code == EX_USAGE
    assert oracle.calls == []                        # nothing was handed over


@pytest.mark.parametrize("argv", [
    ["1", "--bogus"],                                # click's parse error
    ["alpha", "1", "2"],                             # one message at a time
    ["alpha", "./a/file.eml"],                       # a file takes no box
    [],                                              # nothing to verify
])
def test_a_usage_error_is_3_not_clicks_2(box, oracle, argv):
    """click exits 2 on a usage error; under this verb 2 says UNSIGNED, so a
    typo would read as a verdict on the mail. It exits 3, the verifier's own
    usage code, and hands nothing over."""
    deliver(box, "alpha", "1923171201")
    r = run(*argv)
    assert r.exit_code == EX_USAGE, (argv, r.exit_code, r.output)
    assert oracle.calls == []


def test_no_seat_and_no_box_is_a_usage_error_at_3(tmp_path, oracle, monkeypatch):
    """The OTHER door a UsageError comes through: raised inside the verb,
    when there is nothing to default the box to."""
    from sessionmail import _seat
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    r = run("1")
    assert r.exit_code == EX_USAGE, r.output


def test_a_missing_verifier_under_trust_still_ends_on_one_trust_line(box, monkeypatch, tmp_path):
    """--trust's invariant: every run that parsed its arguments ends on
    exactly one TRUST: line. A broken install is an environment error, 5."""
    from sessionmail import _mhs
    deliver(box, "alpha", "1923171201")
    monkeypatch.delenv("SESSIONMAIL_MHS_ORACLE", raising=False)
    monkeypatch.setattr(_mhs, "MHS_DIR", str(tmp_path / "gone"))
    r = run("1", "--trust")
    assert r.exit_code == 5, r.output
    assert r.stdout.strip().splitlines()[-1] == "TRUST: ERROR reason=verifier-absent"
    assert run("1").exit_code == EX_USAGE            # plain mode has no ERROR code


def test_an_oracle_named_but_missing_is_a_clean_refusal_not_a_traceback(box, oracle, monkeypatch, tmp_path):
    """Review R2, MED 2: the variable names a file that is not there.
    The failure is at the exec, not the lookup; an escaped traceback exits
    1, which a caller reads as "bad signature"."""
    deliver(box, "alpha", "1923171201")
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(tmp_path / "gone"))
    r = run("1")
    assert r.exit_code == EX_USAGE and not isinstance(r.exception, FileNotFoundError), r.output
    r = run("1", "--trust")
    assert r.exit_code == 5 and r.stdout.strip().splitlines()[-1] == "TRUST: ERROR reason=verifier-absent"


BAD = """#!{python}
import sys
sys.stdout.write("signer: someone\\nTRUST: FAIL reason=bad-signature\\n")
sys.stderr.write("incorrect signature\\n")
sys.exit(1)
"""


def test_the_mcp_tool_keeps_the_verdict_when_the_verifier_also_writes_stderr(box, oracle, monkeypatch, tmp_path):
    """Review R2, MED 1: the tool answered "incorrect signature" alone
    and dropped the signer and the TRUST line, because the handler took
    stderr OR stdout. It carries both now, the verdict last."""
    from mcp.server.mcpserver.exceptions import ToolError

    from sessionmail import _mcp

    deliver(box, "alpha", "1923171201")
    bad = tmp_path / "bad-verify"
    bad.write_text(BAD.format(python=sys.executable))
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SESSIONMAIL_MHS_ORACLE", str(bad))
    tool = {n: fn for n, fn, _ in _mcp.tools(cli, "message")}["message_verify"]
    with pytest.raises(ToolError) as err:
        tool(args=["1"], trust=True)
    text = str(err.value)
    assert "incorrect signature" in text and "signer: someone" in text
    assert text.splitlines()[-1] == "TRUST: FAIL reason=bad-signature"


def test_the_verify_page_keeps_the_version_and_ambiguity_rules_and_the_exit_contract():
    """SM-787 fix-003 trimmed `message verify --help` under the 400-token fold (422 -> 373).
    A trim cuts redundancy, never these: the version-confusion rule, the ambiguous-header
    rule, and the exit contract callers parse. Pinned here so the next trim cannot drop them."""
    page = CliRunner().invoke(cli, ["verify", "--help"], prog_name="message").output
    flat = " ".join(page.split())
    assert 'Another v= is exit 3 (--trust: FAIL), never "bad".' in flat
    assert ("A header that cannot be read one way (a non-ASCII byte, a tag twice, a second "
            "signature field, a bare CR) is bad: exit 1.") in flat
    assert ("Exit, plain: 0 good · 1 bad signature · 2 unsigned · 3 usage or no such message. "
            "--trust: 0 FULL · 1 FAIL · 4 UNBLESSED · 5 ERROR (the test could not run) · 3 usage"
            ) in flat
