"""The Python sender - the six jobs the bash oracle did, in the package.

RED ON PURPOSE (step 4 of openspec/changes/send-in-python, written before a
line of sender exists): every test imports `sessionmail._sender` lazily, so
each one errors on its own until the module lands, and turns green one job
at a time. Nothing here depends on one of the nine read-back decisions:
environment names are set in BOTH spellings (decision 9), the keyed tests
skip cleanly where there is no key tooling, and the render path (decision
2, a later step) is not tested here.

Hermetic, and honest about what it proves (Charter 3):
  - `sendmail` is a FAKE on PATH that records its argv and captures stdin
    BYTES - the wire is what reaches the MTA, not what a parser re-serialised;
  - signing uses a REAL `ssh-keygen -Y sign` over a throwaway ed25519 key in
    an ephemeral ssh-agent (the bash sig test's own ritual), and the result is
    checked by the verifier this package ships (`mhs/session-mail-verify`,
    the retired session repo's, byte for byte) and by `message verify`, the
    door to it - compatibility proven, not asserted;
  - threading runs over a temp maildir with NO notmuch anywhere;
  - `postconf` / `postmap` are fakes on PATH, so the pre-flight is exercised
    on a machine with no postfix.

The sender's contract under test - the same argv the bash oracle takes, so
step 5's switch and step 6's byte diff are trivial:

    _sender.run(argv, env, *, now=None, pid=None, rand=None) -> (rc, out, err)
    _sender.canonical(from_, to, subject, date, message_id, body) -> bytes
    _sender.check_msgid(value) -> (normalized | None, defect | None)

Sources are the spec's: `send:NNNN` is a line of session-mail-send.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from sessionmail import _mhs

from conftest import SC6_OPERATOR
from fakes import write_fake

EX_OK, EX_REFUSED, EX_MTA, EX_RENDER = 0, 2, 3, 4
HOST = "testhost"
ARROW = "→"   # the bash's own arrow in the transport line (send:2456)

FAKE_SENDMAIL = """#!{python}
import os, sys
with open(os.environ["FAKE_SENDMAIL_ARGV"], "w") as f:
    f.write("\\x1f".join(sys.argv[1:]))
with open(os.environ["FAKE_SENDMAIL_WIRE"], "wb") as f:
    f.write(sys.stdin.buffer.read())
sys.exit(int(os.environ.get("FAKE_SENDMAIL_RC") or 0))
"""

FAKE_POSTCONF = """#!{python}
import os, sys
if os.environ.get("FAKE_POSTCONF_LOG"):          # each call, when a test asks (SM-594)
    with open(os.environ["FAKE_POSTCONF_LOG"], "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
if "myorigin" in sys.argv[1:]:
    # `postconf -xh myorigin` -> the mail origin (SM-594 design 7.3)
    sys.stdout.write(os.environ.get("FAKE_ORIGIN", "fake.origin.example") + "\\n")
    sys.exit(0)
# `postconf -h virtual_mailbox_maps virtual_alias_maps` -> one map per line;
# `postconf -h transport_maps` -> the transport map
if "transport_maps" in sys.argv:
    sys.stdout.write("hash:/maps/transport\\n")
else:
    sys.stdout.write("hash:/maps/vmailbox\\nhash:/maps/valias\\n")
"""

FAKE_POSTMAP = """#!{python}
import os, sys
# `postmap -q ADDR MAP`: 0 when ADDR is in $FAKE_MAPPED (comma list), else 1
addr = sys.argv[sys.argv.index("-q") + 1]
sys.exit(0 if addr in os.environ.get("FAKE_MAPPED", "").split(",") else 1)
"""


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sender():
    """Lazy import: each test is its own red until the module exists."""
    import importlib
    return importlib.import_module("sessionmail._sender")


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


@pytest.fixture
def fake_sendmail(bin_dir, tmp_path, monkeypatch):
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    argv, wire = tmp_path / "sendmail.argv", tmp_path / "sendmail.wire"
    monkeypatch.setenv("FAKE_SENDMAIL_ARGV", str(argv))
    monkeypatch.setenv("FAKE_SENDMAIL_WIRE", str(wire))
    monkeypatch.delenv("FAKE_SENDMAIL_RC", raising=False)

    class Captured:
        @property
        def argv(self):
            return argv.read_text().split("\x1f") if argv.exists() else None

        @property
        def wire(self):
            return wire.read_bytes() if wire.exists() else None

    return Captured()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "mail"
    r.mkdir()
    return r


@pytest.fixture
def env(tmp_path, bin_dir, root, fake_sendmail):
    """A bare seat: no key, no notmuch, no postfix tooling on PATH. Both
    spellings of every variable (decision 9 is not this test's to make)."""
    e = {
        "HOME": str(tmp_path),
        "PATH": str(bin_dir),
        "TMPDIR": str(tmp_path),
        "FAKE_SENDMAIL_ARGV": os.environ["FAKE_SENDMAIL_ARGV"],
        "FAKE_SENDMAIL_WIRE": os.environ["FAKE_SENDMAIL_WIRE"],
        "SESSION_MAIL_ROOT": str(root), "SESSIONMAIL_ROOT": str(root),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail"),
        "SESSIONMAIL_ORIGIN": os.environ["SESSIONMAIL_ORIGIN"],   # conftest's, never the host's
    }
    return e


def deliver(root: Path, box: str, *, mid: str, sender: str, to: str, subject: str,
            body: str = "hi\n", references: list[str] | None = None,
            in_reply_to: str | None = None) -> Path:
    """Put one RFC 5322 file into <root>/<box>/new/, the way postfix would."""
    d = root / box
    for sub in ("new", "cur", "tmp"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    lines = [f"From: {sender}", f"To: {to}", "Date: Tue, 01 Jan 2030 12:00:00 +0000",
             f"Subject: {subject}", f"Message-ID: {mid}"]
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append("References: " + "\n ".join(references))
    lines += ["MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8",
              "Content-Transfer-Encoding: 8bit", f"X-Machine: {HOST}", ""]
    p = d / "new" / f"{int(time.time())}.P1R1.{HOST}.{abs(hash(mid))}"
    p.write_text("\n".join(lines) + "\n" + body)
    return p


@pytest.fixture
def keyed(tmp_path, env):
    """An ephemeral keyholder: ed25519 key in a throwaway ssh-agent, the
    exact ritual test_session_mail_sig.sh performs (its lines 46-52)."""
    for tool in ("ssh-keygen", "ssh-agent", "ssh-add"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on this machine - the keyed path cannot be exercised")
    key = tmp_path / "session-key"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "test-session"],
                   check=True, capture_output=True)
    # The socket gets a SHORT path of its own. Left to itself, this OpenSSH
    # puts it under $HOME/.ssh/agent/, and since the hermetic fixture's HOME
    # is a pytest sandbox (SM-450) that path is over the Unix-socket limit:
    # "too long for Unix domain socket", exit 255. The test needs a socket,
    # not the real HOME, so it gets a socket, and the fixture stays strict.
    sock_dir = tempfile.mkdtemp(prefix="sm-agent-", dir="/tmp")
    agent = subprocess.run(["ssh-agent", "-s", "-a", os.path.join(sock_dir, "s")],
                           check=True, capture_output=True, text=True).stdout
    sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", agent).group(1)
    pid = re.search(r"SSH_AGENT_PID=(\d+);", agent).group(1)
    subprocess.run(["ssh-add", str(key)], check=True, capture_output=True,
                   env={**os.environ, "SSH_AUTH_SOCK": sock})
    env["CLAUDE_SESSION_AGENT_SOCK"] = sock
    env["CLAUDE_SESSION_PUB"] = str(key) + ".pub"
    env["CLAUDE_CODE_SESSION_ID"] = "35bf6365-6686-d084-55b4-1507b25b65a2"
    env["PATH"] = env["PATH"] + os.pathsep + os.path.dirname(shutil.which("ssh-keygen"))
    yield env
    subprocess.run(["kill", pid], capture_output=True)
    shutil.rmtree(sock_dir, ignore_errors=True)


#: The verifier this package ships (`mhs/session-mail-verify`, byte-equal to
#: the retired session repo's). It used to be looked up in the session repo's
#: checkout, and when that repo was retired (Mon 2030-09-30) every
#: compatibility proof below began to SKIP, silently, on the one machine that
#: had always run them (Ivan's retirement audit, R19). The bundled copy is
#: always here, so a skip now means a broken install and nothing else.
VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"


@pytest.fixture
def verifier():
    """The verifier `message verify` runs - the one in this package."""
    if not os.access(VERIFIER, os.X_OK):
        pytest.skip(f"{VERIFIER} is missing or not executable - a broken sessionmail install")
    return VERIFIER


@pytest.fixture
def fake_postfix(bin_dir, env):
    """`env` is the sender's WHOLE environment, children included - the map
    list goes in it, not in os.environ, or the fake postmap never sees it."""
    write_fake(bin_dir / "postconf", FAKE_POSTCONF.format(python=sys.executable))
    write_fake(bin_dir / "postmap", FAKE_POSTMAP.format(python=sys.executable))

    def mapped(*addrs):
        env["FAKE_MAPPED"] = ",".join(addrs)
        return env["FAKE_MAPPED"]

    return mapped


def body_file(tmp_path, data: bytes, name="body.md") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def send(sender, env, tmp_path, *extra, body=b"hello\n", to="eve", subject="Build green",
         **kw):
    argv = [to, "--from", "eve", "--subject", subject,
            "--body-file", body_file(tmp_path, body), *extra]
    return sender.run(argv, env, **kw)


def headers_of(wire: bytes) -> list[tuple[str, str]]:
    head, _, _ = wire.partition(b"\n\n")
    out: list[tuple[str, str]] = []
    for line in head.decode("utf-8").split("\n"):
        if line[:1] in (" ", "\t"):
            k, v = out[-1]
            out[-1] = (k, v + "\n" + line)
        else:
            k, _, v = line.partition(":")
            out.append((k, v.strip()))
    return out


def body_of(wire: bytes) -> bytes:
    return wire.partition(b"\n\n")[2]


# ── job 1: mint ──────────────────────────────────────────────────────────────

def test_message_id_is_epoch_pid_rand_at_host_and_the_only_stdout(sender, env, tmp_path, fake_sendmail):
    rc, out, err = send(sender, env, tmp_path, now=1907536000, pid=4242, rand=777)
    assert rc == EX_OK, err
    assert out == f"<1907536000.4242.777@{HOST}.session-mail>\n"        # send:1789, :2575
    assert dict(headers_of(fake_sendmail.wire))["Message-ID"] == out.strip()


def test_host_comes_from_the_environment_else_the_short_hostname(sender, env, tmp_path, fake_sendmail):
    for k in ("SESSION_MAIL_HOST", "SESSIONMAIL_HOST"):
        env.pop(k)
    rc, out, _ = send(sender, env, tmp_path)
    assert rc == EX_OK
    short = subprocess.run(["hostname", "-s"], capture_output=True, text=True).stdout.strip()
    assert out.strip().endswith(f"@{short}.session-mail>")               # send:1787


# ── job 2: the header block and the body bytes ───────────────────────────────

def test_plain_send_header_order_is_the_bash_order(sender, env, tmp_path, fake_sendmail):
    rc, _, _ = send(sender, env, tmp_path)
    assert rc == EX_OK
    assert [k for k, _ in headers_of(fake_sendmail.wire)] == [
        "From", "To", "Date", "Subject", "Message-ID",
        "MIME-Version", "Content-Type", "Content-Transfer-Encoding", "X-Machine",
    ]                                                                   # send:1887-2118
    h = dict(headers_of(fake_sendmail.wire))
    assert h["MIME-Version"] == "1.0"
    assert h["Content-Type"] == "text/plain; charset=utf-8"
    assert h["Content-Transfer-Encoding"] == "8bit"                     # declared, not applied
    assert h["X-Machine"] == HOST


def test_body_bytes_are_verbatim_no_newline_added_none_eaten(sender, env, tmp_path, fake_sendmail):
    raw = "café — `code` $(not run)\n\n".encode()
    rc, _, _ = send(sender, env, tmp_path, body=raw)
    assert rc == EX_OK
    assert body_of(fake_sendmail.wire) == raw                           # send:2050-2062, D4


def test_cc_bcc_task_project_supersedes_land_in_the_pinned_positions(sender, env, tmp_path, fake_sendmail, root):
    deliver(root, "eve", mid="<p@x.session-mail>", sender="ivan", to="eve", subject="parent")
    rc, _, _ = send(sender, env, tmp_path, "--cc", "ivan", "--bcc", "foreman.sm-18",
                    "--task", "SM-18", "--project", "sessionmail",
                    "--supersedes", "<p@x.session-mail>")
    assert rc == EX_OK
    assert [k for k, _ in headers_of(fake_sendmail.wire)] == [
        "From", "To", "Cc", "Bcc", "Date", "Subject", "Message-ID",
        "In-Reply-To", "References",
        "MIME-Version", "Content-Type", "Content-Transfer-Encoding",
        "X-Project", "X-Task", "Supersedes", "X-Machine",
    ]                                                                   # send:1908-2066


def test_x_session_from_is_the_first_eight_of_the_session_id(sender, env, tmp_path, fake_sendmail):
    env["CLAUDE_CODE_SESSION_ID"] = "35bf6365-6686-d084-55b4-1507b25b65a2"
    send(sender, env, tmp_path)
    h = headers_of(fake_sendmail.wire)
    assert ("X-Session-From", "35bf6365") in h                          # send:1791, :2064
    assert [k for k, _ in h][-2:] == ["X-Session-From", "X-Machine"]


# ── job 3: sign ──────────────────────────────────────────────────────────────

def test_canonical_bytes_follow_the_v1_rule(sender):
    canon = sender.canonical(from_="Eve <eve@hq>", to="ivan", subject="  a   b ",
                             date="Tue, 01 Jan 2030 12:00:00 +0000",
                             message_id="<1.2.3@hq.session-mail>", body=b"body\n\n\n")
    assert canon == (b"from: eve\nto: ivan\nsubject: a b\n"
                     b"date: Tue, 01 Jan 2030 12:00:00 +0000\n"
                     b"message-id: <1.2.3@hq.session-mail>\n\nbody\n")     # send:2151-2157


def test_an_empty_body_signs_with_no_blank_line_at_all(sender):
    canon = sender.canonical(from_="eve", to="ivan", subject="[ROOT]", date="D",
                             message_id="<m@h>", body=b"")
    assert canon.endswith(b"message-id: <m@h>\n")                       # verify:276-300, SESH-653


def test_unkeyed_seat_sends_unsigned_and_says_so(sender, env, tmp_path, fake_sendmail):
    rc, _, err = send(sender, env, tmp_path)
    assert rc == EX_OK
    assert "X-Session-Signature" not in dict(headers_of(fake_sendmail.wire))   # send:2131
    lines = err.strip().splitlines()
    assert len(lines) == 2 and "UNSIGNED" in lines[0]                   # RULED 7(b): says so
    assert lines[1] == f"session-mail-send: sent via smtp (postfix) {ARROW} eve@{HOST}"


def test_keyed_seat_signs_and_the_existing_verifier_says_good(sender, keyed, tmp_path, fake_sendmail, verifier):
    rc, _, err = send(sender, keyed, tmp_path, body=b"signed body\n\n")
    assert rc == EX_OK, err
    h = dict(headers_of(fake_sendmail.wire))
    sig = h["X-Session-Signature"]
    assert " ".join(sig.split()).startswith(                        # v2 only (SM-594); folded
        "v=2; a=sshsig; n=session-mail@example.com; h=from:to:cc:subject:")
    delivered = tmp_path / "delivered.eml"
    delivered.write_bytes(fake_sendmail.wire)
    done = subprocess.run([str(verifier), str(delivered)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr              # the verifier is unchanged


def test_message_verify_says_good_on_what_the_sender_signed(sender, keyed, tmp_path, fake_sendmail, verifier):
    """End to end through the public door: the sender signs, `message verify FILE` runs the shipped verifier on the
    delivered bytes, and its verdict and exit come back - 0 good, 1 when one
    signed byte changes, 2 when the signature is gone."""
    from click.testing import CliRunner
    from sessionmail.message import cli
    rc, _, err = send(sender, keyed, tmp_path, body=b"signed body\n\n")
    assert rc == EX_OK, err
    good = tmp_path / "good.eml"
    good.write_bytes(fake_sendmail.wire)
    r = CliRunner().invoke(cli, ["verify", str(good)])
    assert r.exit_code == 0, r.output
    assert "signature-crypto  : ✅ good" in r.output
    tampered = tmp_path / "tampered.eml"
    tampered.write_bytes(fake_sendmail.wire.replace(b"signed body", b"signed b0dy"))
    assert CliRunner().invoke(cli, ["verify", str(tampered)]).exit_code == 1
    unsigned = tmp_path / "unsigned.eml"
    head, sep, body = fake_sendmail.wire.partition(b"\n\n")
    kept, dropping = [], False
    for line in head.split(b"\n"):                  # the header AND its folded lines
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.startswith(b"X-Session-Signature:")
        if not dropping:
            kept.append(line)
    unsigned.write_bytes(b"\n".join(kept) + sep + body)
    assert CliRunner().invoke(cli, ["verify", str(unsigned)]).exit_code == 2


def test_keyed_but_dead_keyholder_sends_unsigned_and_says_so(sender, keyed, tmp_path, fake_sendmail):
    keyed["CLAUDE_SESSION_AGENT_SOCK"] = str(tmp_path / "no-such-socket")
    # The private key must go too: `ssh-keygen -Y sign -f key.pub` falls back
    # to the private file beside the .pub when the agent is dead, and signs
    # (measured). A real seat's key lives only in its agent.
    (tmp_path / "session-key").unlink()
    rc, _, err = send(sender, keyed, tmp_path)
    assert rc == EX_OK
    assert "X-Session-Signature" not in dict(headers_of(fake_sendmail.wire))
    assert "keyed session but signing FAILED" in err and "UNSIGNED" in err   # send:2166-2169


def test_an_address_that_cannot_be_canonicalised_is_refused_on_a_keyed_seat(sender, keyed, tmp_path, fake_sendmail):
    # NOT '"Last, First" <a@h>': that reduces to `a` and the bash signs it,
    # exit 0 (measured Thu 2030-09-19). A second @ survives reduction.
    argv = ["ivan", "--from", "eve@a@b", "--subject", "x",
            "--body-file", body_file(tmp_path, b"b\n")]
    rc, out, err = sender.run(argv, keyed)
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None   # send:2151


# ── job 4: thread ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, ok", [
    ("<2000000000.11111.11111@host1.session-mail>", True),
    ("<a&b.1788@host1.session-mail>", True),                             # & is atext
    ("2000000000.11111.11111@host1.session-mail", True),                 # bare, normalised
    ("&lt;2000000000.11111.11111@host1.session-mail&gt;", False),
    ("<1788;x@host1.session-mail>", False),
    ("<<1788@host1.session-mail>>", False),
    ("<a@host1> junk", False),
    ("<a@h>\nX-Evil: 1", False),
    ("<>", False),
    ("   ", False),
    ("", False),
])
def test_threading_id_grammar_is_the_standards(sender, value, ok):
    normalized, defect = sender.check_msgid(value)                       # send:282-330, :318-326
    assert (defect is None) is ok
    if ok:
        assert normalized.startswith("<") and normalized.endswith(">")


def test_a_malformed_in_reply_to_refuses_before_anything_is_written(sender, env, tmp_path, fake_sendmail):
    rc, out, err = send(sender, env, tmp_path, "--in-reply-to", "&lt;a@h&gt;")
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None
    assert "RFC 5322" in err


def test_references_is_the_full_ancestor_chain_from_the_maildir(sender, env, tmp_path, fake_sendmail, root):
    deliver(root, "eve", mid="<r@h.session-mail>", sender="ivan", to="eve", subject="root")
    deliver(root, "eve", mid="<a@h.session-mail>", sender="ivan", to="eve", subject="a",
            in_reply_to="<r@h.session-mail>", references=["<r@h.session-mail>"])
    rc, _, _ = send(sender, env, tmp_path, "--in-reply-to", "a@h.session-mail")
    assert rc == EX_OK
    h = dict(headers_of(fake_sendmail.wire))
    assert h["In-Reply-To"] == "<a@h.session-mail>"
    assert h["References"] == "<r@h.session-mail>\n <a@h.session-mail>"  # send:829-845, folded


def test_a_parent_not_findable_gives_one_hop_and_a_notice_and_still_sends(sender, env, tmp_path, fake_sendmail):
    rc, out, err = send(sender, env, tmp_path, "--in-reply-to", "<gone@other.session-mail>")
    assert rc == EX_OK and out
    h = dict(headers_of(fake_sendmail.wire))
    assert h["References"] == "<gone@other.session-mail>"
    assert "<gone@other.session-mail>" in err and "NOT in the local index" in err   # send:1050-1071


def test_parent_check_off_silences_the_notice_only(sender, env, tmp_path, fake_sendmail):
    env["SESSION_MAIL_PARENT_CHECK"] = env["SESSIONMAIL_PARENT_CHECK"] = "off"
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", "<gone@other.session-mail>")
    assert rc == EX_OK and "NOT in the local index" not in err


def test_a_supersede_threads_to_its_target_and_writes_the_header(sender, env, tmp_path, fake_sendmail, root):
    deliver(root, "eve", mid="<x@h.session-mail>", sender="eve", to="ivan", subject="v1")
    rc, _, _ = send(sender, env, tmp_path, "--supersedes", "<x@h.session-mail>")
    assert rc == EX_OK
    h = dict(headers_of(fake_sendmail.wire))
    assert h["In-Reply-To"] == h["Supersedes"] == "<x@h.session-mail>"   # send:1031-1033, :2044
    assert h["References"] == "<x@h.session-mail>"


def test_no_notmuch_is_ever_consulted(sender, env, tmp_path, fake_sendmail, bin_dir):
    tripwire = bin_dir / "notmuch"
    write_fake(tripwire, "#!{python}\nimport sys; sys.exit(99)\n".format(python=sys.executable))
    rc, _, _ = send(sender, env, tmp_path, "--in-reply-to", "<any@h.session-mail>")
    assert rc == EX_OK                                                  # a 99 would have surfaced


# ── job 5: pre-flight ────────────────────────────────────────────────────────

def test_every_same_host_recipient_is_probed_and_one_miss_names_the_field(sender, env, tmp_path, fake_sendmail, fake_postfix):
    fake_postfix(f"eve@{HOST}")
    rc, out, err = send(sender, env, tmp_path, "--cc", "ghost")
    assert rc == EX_MTA and out == "" and fake_sendmail.wire is None      # send:2384-2440
    assert f"Cc 'ghost@{HOST}'" in err


def test_plus_extension_is_stripped_and_cross_host_is_exempt(sender, env, tmp_path, fake_sendmail, fake_postfix):
    fake_postfix(f"eve@{HOST}")
    rc, _, _ = send(sender, env, tmp_path, to="eve+memory", subject="x", **{})
    assert rc == EX_OK                                                  # send:2341 +ext stripped
    rc, _, _ = send(sender, env, tmp_path, "--cc", "carol@vps")
    assert rc == EX_OK                                                  # send:2312-2321 cross-host


SID = "c082f8e6-e3cc-4f02-8ffa-ce173a8115c4"


def test_an_unknown_session_goes_to_the_mail_host_when_it_is_routed(sender, env, tmp_path, fake_sendmail, fake_postfix):
    fake_postfix(f"eve@{HOST}", f"{SID}@relay.home")                    # a transport for relay.home
    rc, _, err = send(sender, env, tmp_path, to=SID)
    assert rc == EX_OK
    assert b"\nTo: " + f"{SID}@relay.home".encode() + b"\n" in fake_sendmail.wire
    assert f"general delivery: To '{SID}@{HOST}'" in err
    fake_postfix(f"eve@{HOST}", "relay.home")                           # a hash map keyed by domain
    rc, _, _ = send(sender, env, tmp_path, "--cc", f"Gc <{SID[:8]}+ext>")
    assert rc == EX_OK                                                  # the 8-hex short form, +ext kept
    assert f"Cc: Gc <{SID[:8]}+ext@relay.home>".encode() in fake_sendmail.wire


def test_an_unknown_session_with_no_route_and_an_unknown_seat_are_still_refused(sender, env, tmp_path, fake_sendmail, fake_postfix):
    fake_postfix(f"eve@{HOST}")                                         # no transport for relay.home
    rc, _, err = send(sender, env, tmp_path, to=SID)
    assert rc == EX_MTA and fake_sendmail.wire is None
    assert "not deliverable on this host" in err
    fake_postfix(f"eve@{HOST}", "ghost@relay.home")
    rc, _, err = send(sender, env, tmp_path, "--cc", "ghost")
    assert rc == EX_MTA and fake_sendmail.wire is None                  # a seat name: a typo, refused
    assert f"Cc 'ghost@{HOST}'" in err


def test_without_postfix_tooling_the_preflight_never_blocks(sender, env, tmp_path, fake_sendmail):
    rc, _, _ = send(sender, env, tmp_path, "--cc", "ghost")
    assert rc == EX_OK                                                  # send:2351, :2436


# ── job 6: hand to the MTA ───────────────────────────────────────────────────

def test_sendmail_gets_t_i_f_from_and_the_exact_wire_bytes(sender, env, tmp_path, fake_sendmail):
    rc, _, _ = send(sender, env, tmp_path)
    assert rc == EX_OK
    assert fake_sendmail.argv == ["-t", "-i", "-f", "eve"]              # send:2450
    assert fake_sendmail.wire.startswith(b"From: eve\nTo: eve\n")


def test_no_mta_is_exit_3_with_nothing_written(sender, env, tmp_path, fake_sendmail, root):
    env["SESSIONMAIL_SENDMAIL"] = str(tmp_path / "no-such-sendmail")
    rc, out, err = send(sender, env, tmp_path)
    assert rc == EX_MTA and out == "" and fake_sendmail.wire is None
    assert not list(root.rglob("*/new/*"))                              # decision 1


def test_an_mta_that_rejects_is_exit_3_and_its_stderr_is_shown(sender, env, tmp_path, fake_sendmail):
    env["FAKE_SENDMAIL_RC"] = "75"
    rc, out, _ = send(sender, env, tmp_path)
    assert rc == EX_MTA and out == ""


def test_the_transport_line_is_pinned_word_for_word(sender, env, tmp_path, fake_sendmail):
    rc, _, err = send(sender, env, tmp_path, "--cc", "ivan")
    assert rc == EX_OK
    assert err.rstrip("\n").splitlines()[-1] == f"session-mail-send: sent via smtp (postfix) {ARROW} eve@{HOST}"   # send:2456, DECISION 8


# ── header values: no line breaks anywhere; the subject guard is the door's ──

@pytest.mark.parametrize("field", ["to", "--from", "--subject", "--cc", "--bcc", "--project", "--task"])
@pytest.mark.parametrize("brk", ["\n", "\r"])
def test_a_line_break_in_any_header_value_refuses(sender, env, tmp_path, fake_sendmail, field, brk):
    evil = f"x{brk}Bcc: evil"
    argv = ["eve", "--from", "eve", "--subject", "s", "--body-file", body_file(tmp_path, b"b\n")]
    if field == "to":
        argv[0] = evil
    elif field in ("--from", "--subject"):
        argv[argv.index(field) + 1] = evil
    else:
        argv += [field, evil]
    rc, out, err = sender.run(argv, env)
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None, err   # spec: no line break


def test_the_sender_does_not_repeat_the_subject_execution_guard(sender, env, tmp_path, fake_sendmail):
    rc, _, err = send(sender, env, tmp_path, subject="fix `x` and $(y)")
    assert rc == EX_OK, err                                             # the door owns it (spec)
    assert dict(headers_of(fake_sendmail.wire))["Subject"] == "fix `x` and $(y)"


def test_the_sender_has_no_command_line_of_its_own(sender):
    assert not hasattr(sender, "main")                                  # no door around the guard


# ── the body file's refusals move with the sender ────────────────────────────

@pytest.mark.parametrize("data, why", [
    (b"", "empty"),
    (b"\n \n\t\n", "whitespace-only"),
    (b"text\x00more", "NUL"),
])
def test_body_file_refusals(sender, env, tmp_path, fake_sendmail, data, why):
    rc, out, err = send(sender, env, tmp_path, body=data)
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None, why   # send:1354-1413


def test_missing_and_unreadable_body_files_refuse(sender, env, tmp_path, fake_sendmail):
    argv = ["eve", "--from", "eve", "--subject", "x", "--body-file", str(tmp_path / "nope")]
    assert sender.run(argv, env)[0] == EX_REFUSED
    if os.geteuid() != 0:
        p = tmp_path / "locked"
        p.write_bytes(b"x\n")
        p.chmod(0)
        argv[-1] = str(p)
        assert sender.run(argv, env)[0] == EX_REFUSED


def test_no_body_with_a_subject_sends_an_empty_single_part_body(sender, env, tmp_path, fake_sendmail):
    rc, _, _ = sender.run(["eve", "--from", "eve", "--subject", "done EOM", "--no-body"], env)
    assert rc == EX_OK
    assert body_of(fake_sendmail.wire) == b""                           # send:1342-1352


# ── step 6's gate: RETIRED 2030-10-01 ───────────────────────────────────────
# Two tests diffed the Python sender's wire against the bash
# session-mail-send, byte for byte except the volatile headers (and the MIME
# boundary on the 4b path). They were send-in-python step 6's gate, and it
# passed: the Python sender became the only sender (RATIFICATION "one Python
# sender everywhere"). The bash retired with the session repo, so from then
# on both tests skipped silently on the one machine that had always run them
# (a retirement audit, R19). They are retired and not repointed at the renamed
# checkout: parity with an oracle nobody runs any more proves nothing new.
# The history has them:
# `git log -S test_wire_is_byte_identical_to_the_bash`.


def test_the_wire_date_and_message_id_keep_their_shape(sender, env, tmp_path, fake_sendmail, root):
    """The half of the retired gate that never needed the bash (review
    R19): its mask hid VALUES, never FORMAT, and it
    held the only assertions on the sender's own Date and Message-ID. An RFC
    5322 Date, an id of `<epoch.pid.rand@HOST.session-mail>`, and the Date
    within a second of the epoch the id was minted from."""
    from email.utils import parsedate_to_datetime

    deliver(root, "eve", mid="<seed@h.session-mail>", sender="ivan", to="eve", subject="seed")
    rc, _, _ = sender.run(["eve", "--from", "eve", "--subject", "fixed", "--body-file",
                           body_file(tmp_path, b"one fixed message\n"),
                           "--in-reply-to", "<seed@h.session-mail>"], env)
    assert rc == EX_OK
    date_shape = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} "
                            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                            r"\d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}")
    id_shape = re.compile(rf"<(\d+)\.\d+\.\d+@{HOST}\.session-mail>")
    h = dict(headers_of(fake_sendmail.wire))
    assert date_shape.fullmatch(h["Date"]), h["Date"]
    shaped = id_shape.fullmatch(h["Message-ID"])
    assert shaped, h["Message-ID"]
    assert abs(parsedate_to_datetime(h["Date"]).timestamp() - int(shaped.group(1))) <= 1


# ── task 4b: a human on the line owes a page ─────────────────────────────────

FAKE_RENDERER = """#!{python}
import os, sys
src, out = sys.argv[1], sys.argv[sys.argv.index("-o") + 1]
title = [a for a in sys.argv if a.startswith("--title=")][0][len("--title="):]
if os.environ.get("FAKE_RENDER_RC"):
    sys.stderr.write("first line\\nfake renderer: told to fail\\n")
    sys.exit(int(os.environ["FAKE_RENDER_RC"]))
with open(os.environ["FAKE_RENDER_LOG"], "w") as log:
    log.write(title + "\\x1f" + oct(os.stat(src).st_mode & 0o777) + "\\x1f" + open(src).read())
open(out, "w").write(os.environ.get("FAKE_RENDER_PAGE", "<!DOCTYPE html><p>fake</p>"))
"""

B = "=_sesh890_test"


def multipart(boundary: str, body: bytes, page: bytes) -> bytes:
    part = "Content-Type: text/{}; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
    return (f"--{boundary}\n{part.format('plain')}".encode() + body
            + f"\n--{boundary}\n{part.format('html')}".encode() + page
            + f"\n--{boundary}--\n".encode())


@pytest.fixture
def fake_renderer(bin_dir, env, tmp_path):
    prog = bin_dir / "my-render"
    write_fake(prog, FAKE_RENDERER.format(python=sys.executable))
    env["FAKE_RENDER_LOG"] = str(tmp_path / "render.log")
    return prog


def leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.glob("session-mail-*"))


def test_mail_to_a_human_is_multipart_with_the_markdown_first_byte_for_byte(sender, env, tmp_path, fake_sendmail):
    raw = "# Status\n\n```sh\n# a comment, not a title\necho $(x)\n```\n\n".encode()
    rc, out, err = send(sender, env, tmp_path, to="judy", body=raw, boundary=B)
    assert rc == EX_OK, err
    h = headers_of(fake_sendmail.wire)
    assert dict(h)["Content-Type"] == f'multipart/alternative; boundary="{B}"'
    assert [k for k, _ in h] == ["From", "To", "Date", "Subject", "Message-ID", "MIME-Version",
                                 "Content-Type", "Content-Transfer-Encoding", "X-Machine"]
    from sessionmail._render import render_page
    page = render_page(raw.decode(), "Build green").encode()
    assert body_of(fake_sendmail.wire) == multipart(B, raw, page)     # send:2074-2107
    assert page.startswith(b"<!DOCTYPE html>\n") and b"<title>Build green</title>" in page
    assert not leftovers(tmp_path)                                     # built-in: no temp files


def test_a_rendered_mail_still_signs_the_markdown_and_verifies(sender, keyed, tmp_path, fake_sendmail, verifier):
    rc, _, err = send(sender, keyed, tmp_path, to="judy", body=b"# hi\n\nsigned markdown\n")
    assert rc == EX_OK, err
    assert "X-Session-Signature" in dict(headers_of(fake_sendmail.wire))
    delivered = tmp_path / "delivered.eml"
    delivered.write_bytes(fake_sendmail.wire)
    done = subprocess.run([str(verifier), str(delivered)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr              # the narrow unwrap, D2


@pytest.mark.parametrize("field, value, owed", [
    ("--cc", "JUDY+notes@elsewhere", True),                              # case-folded, +tag stripped
    ("--bcc", "Judy Hopper <judy@hq>", True),
    ("--cc", "judy.hopper", False),                                     # no dot/dash guessing
    ("--cc", "judy-hopper", False),
])
def test_who_is_human_is_exact(sender, env, tmp_path, fake_sendmail, field, value, owed):
    rc, _, err = send(sender, env, tmp_path, field, value)
    assert rc == EX_OK, err
    assert dict(headers_of(fake_sendmail.wire))["Content-Type"].startswith("multipart/") is owed


@pytest.mark.parametrize("how", ["off", "raw", "agent-only", "no-body"])
def test_the_ways_a_send_is_single_part_and_never_probes_a_renderer(sender, env, tmp_path, fake_sendmail, how):
    env["SESSIONMAIL_RENDERER"] = str(tmp_path / "no-such-renderer")    # a tripwire: owed would be exit 4
    if how == "off":
        env["SESSIONMAIL_RENDER"] = "off"
        rc, _, err = send(sender, env, tmp_path, to="judy")
    elif how == "raw":
        rc, _, err = send(sender, env, tmp_path, "--raw", to="judy", body=b"<!DOCTYPE html>a log\n")
    elif how == "agent-only":
        rc, _, err = send(sender, env, tmp_path, to="eve")
    else:
        rc, _, err = sender.run(["judy", "--from", "eve", "--subject", "done EOM", "--no-body"], env)
    assert rc == EX_OK, err
    assert dict(headers_of(fake_sendmail.wire))["Content-Type"] == "text/plain; charset=utf-8"


def test_a_named_renderer_gets_a_private_file_and_the_typed_subject(sender, env, tmp_path, fake_sendmail, fake_renderer):
    env["SESSIONMAIL_RENDERER"] = str(tmp_path / "no-such-renderer")    # the flag wins
    rc, _, err = send(sender, env, tmp_path, "--renderer", str(fake_renderer), to="judy",
                      body=b"# body\n", subject="Build green", boundary=B)
    assert rc == EX_OK, err
    title, mode, seen = (tmp_path / "render.log").read_text().split("\x1f")
    assert (title, mode, seen) == ("Build green", "0o600", "# body\n")
    assert body_of(fake_sendmail.wire) == multipart(B, b"# body\n", b"<!DOCTYPE html><p>fake</p>")
    assert not leftovers(tmp_path)                                     # both temp files gone


@pytest.mark.parametrize("spelling", ["SESSIONMAIL_RENDERER", "SESSION_MAIL_RENDERER"])
def test_a_named_renderer_that_is_not_there_refuses_4_naming_what_was_not_sent(sender, env, tmp_path, fake_sendmail, spelling):
    env[spelling] = str(tmp_path / "no-such-renderer")
    rc, out, err = send(sender, env, tmp_path, "--cc", "ivan", to="judy", subject="Weekly")
    assert rc == EX_RENDER and out == "" and fake_sendmail.wire is None
    assert "no-such-renderer" in err and "'Weekly'" in err and "To judy, Cc ivan" in err
    assert "--raw" in err                                               # the escape is named


@pytest.mark.parametrize("page, rc_env, words", [
    ("<!DOCTYPE html>ok", "1", "told to fail"),                          # its LAST stderr line
    ("<!DOCTYPE html>ok", "127", "could not START"),
    ("<html>no doctype</html>", "", "sanity predicate"),
    ("", "", "sanity predicate"),
])
def test_a_renderer_that_fails_refuses_4_and_leaves_nothing(sender, env, tmp_path, fake_sendmail, fake_renderer, page, rc_env, words):
    env["FAKE_RENDER_PAGE"] = page
    if rc_env:
        env["FAKE_RENDER_RC"] = rc_env
    rc, out, err = send(sender, env, tmp_path, "--renderer", str(fake_renderer), to="judy")
    assert rc == EX_RENDER and out == "" and fake_sendmail.wire is None
    assert words in err
    assert not leftovers(tmp_path)


def test_a_renderer_that_cannot_load_refuses_4_not_a_crash(sender, env, tmp_path, fake_sendmail, monkeypatch):
    import sessionmail
    monkeypatch.delattr(sessionmail, "_render", raising=False)         # `from . import` checks this first
    monkeypatch.setitem(sys.modules, "sessionmail._render", None)       # and then this: import raises
    rc, out, err = send(sender, env, tmp_path, to="judy")
    assert rc == EX_RENDER and out == "" and fake_sendmail.wire is None
    assert "cannot load" in err and "--raw" in err


@pytest.mark.parametrize("body", [b"<!DOCTYPE html><p>x</p>\n", b"  \n<HTML><body>x</body></HTML>\n"])
def test_a_body_that_is_already_an_html_document_refuses_2(sender, env, tmp_path, fake_sendmail, body):
    rc, out, err = send(sender, env, tmp_path, to="judy", body=body)
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None
    assert "--raw" in err                                               # send:1761


def test_a_set_humans_registry_that_cannot_be_read_refuses_2(sender, env, tmp_path, fake_sendmail):
    env["SESSIONMAIL_HUMANS"] = str(tmp_path / "no-such-humans")
    rc, out, err = send(sender, env, tmp_path, to="judy")
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None   # send:1556


@pytest.mark.parametrize("block, rendered, plain", [
    (SC6_OPERATOR, ["judy"], ["ivan", "eve"]),                   # privileged is not human: ivan
    ('{"operator": {"mailbox": "alice"}}', ["alice"], ["judy"]),
    ('{"operator": {"address": "alice@x.example"}}', [], ["alice", "judy"]),  # an address is no box
    (None, ["acct-default"], ["judy"]),                          # no block: the account (SM-806)
    ('{"operator": null}', [], ["acct-default"]),                # the opt-out: nobody (SM-806 Q3)
])
def test_the_shipped_list_names_nobody_and_the_operator_is_its_human(
        sender, env, tmp_path, fake_sendmail, stranger, block, rendered, plain):
    """SM-727 design h.1: the SHIPPED final fallback held `judy`, a human on
    a stranger's install. It names nobody now; the host's operator mailbox
    (config, pwd's home) is its one human. With NO block, the default
    operator (SM-806) is: the installing account from the password
    database (the conftest seam's `acct-default`). `"operator": null`
    opts out, and then nobody is."""
    assert sender.humans_in(sender.SHIPPED_HUMANS) == set()
    stranger(block)
    for to, owed in [(t, True) for t in rendered] + [(t, False) for t in plain]:
        rc, _, err = send(sender, env, tmp_path, to=to)
        assert rc == EX_OK, err
        assert dict(headers_of(fake_sendmail.wire))["Content-Type"].startswith("multipart/") is owed, to


def test_the_roots_humans_file_overrides_the_shipped_one(sender, env, tmp_path, fake_sendmail, root):
    (root / ".humans").write_text("# local\neve\n")
    rc, _, _ = send(sender, env, tmp_path, to="eve")
    assert rc == EX_OK and dict(headers_of(fake_sendmail.wire))["Content-Type"].startswith("multipart/")
    rc, _, _ = send(sender, env, tmp_path, to="judy")
    assert rc == EX_OK and dict(headers_of(fake_sendmail.wire))["Content-Type"].startswith("text/plain")


# ── the order of complaints (openspec/changes/agents-send-door) ──────────────
#
# RULED Q03: three two-faults-at-once tests that lock TODAY's order, plus a
# fourth (the
# send that does NOTHING must say so; that one lives with the module entry
# and the MCP door, tests/test_mcp_send.py). These three were MEASURED on
# 2632788 before they were written, and are green BEFORE the core is split:
# their whole job is to stay green across it. One complaint, exit 2, nothing
# on stdout, nothing to the MTA.

@pytest.mark.parametrize("argv, says, never_says", [
    (["eve", "--subject", "s", "--no-body", "--in-reply-to", "not an id"],
     "missing --from", "invalid --in-reply-to"),
    (["eve", "--from", "a\nb", "--subject", "s", "--no-body", "--bogus"],
     "unknown flag: --bogus", "line break"),
    (["eve", "--from", "eve", "--subject", "s", "--body-file", "/nonexistent/body",
      "--in-reply-to", "not an id"],
     "invalid --in-reply-to", "no such file"),
], ids=["missing-from-beats-a-msgid-defect",
        "unknown-flag-beats-a-from-line-break",
        "bad-msgid-beats-a-bad-body-file"])
def test_two_faults_one_complaint_in_todays_order(sender, env, fake_sendmail, argv, says, never_says):
    rc, out, err = sender.run(argv, env)
    assert rc == EX_REFUSED and out == "" and fake_sendmail.wire is None
    assert says in err and never_says not in err, err
    assert len(err.strip().splitlines()) == 1, f"one complaint, not a list: {err!r}"


# ── the core takes a request and a body (agents-send-door, RULED D09) ────────
#
# One core, both doors on it from the start, so another door (a web API)
# can be added later. `run` is the
# argv adapter; everything above this line still goes through it, unedited.

def test_send_takes_a_request_and_a_text_body_and_returns_a_receipt(sender, env, fake_sendmail):
    req = sender.Request(to="eve", from_="ivan", subject="the point")
    receipt = sender.send(req, b"words, not a file\n", env, now=1907238896, pid=42, rand=7)
    assert receipt.message_id == f"<1907238896.42.7@{HOST}.session-mail>"
    assert fake_sendmail.wire.endswith(b"\n\nwords, not a file\n")
    assert any("sent via smtp" in n for n in receipt.notices)
    assert any("UNSIGNED" in n for n in receipt.notices), "a warning rides in the receipt"


@pytest.mark.parametrize("body, why", [(b"   ", "no body in it"), (b"a\x00b", "NUL byte")])
def test_a_text_body_meets_the_bodys_own_refusals(sender, env, fake_sendmail, tmp_path, body, why):
    """They belonged to the file reader; a text body never reached them."""
    req = sender.Request(to="eve", from_="ivan", subject="s")
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, body, env)
    assert why in str(refused.value) and refused.value.code == EX_REFUSED
    assert fake_sendmail.wire is None
    # ... and the FILE's words are byte for byte what they were.
    path = tmp_path / "body"
    path.write_bytes(body)
    rc, out, err = sender.run(["eve", "--from", "ivan", "--subject", "s", "--body-file", str(path)], env)
    assert rc == EX_REFUSED and err.startswith(f"session-mail-send: --body-file: {str(path)!r} ")


def test_send_validates_a_request_no_door_has_looked_at(sender, env, fake_sendmail):
    with pytest.raises(sender.Refused, match="missing --from"):
        sender.send(sender.Request(to="eve", subject="s"), b"x\n", env)
    with pytest.raises(sender.Refused, match="invalid --in-reply-to"):
        sender.send(sender.Request(to="eve", from_="ivan", subject="s", in_reply_to="not an id"), b"x\n", env)
    assert fake_sendmail.wire is None


def test_a_refusal_keeps_the_notices_that_came_before_it(sender, env, fake_sendmail):
    """A warning then a refusal: `run` printed both, in that order, and still does."""
    env["FAKE_SENDMAIL_RC"] = "75"
    req = sender.Request(to="eve", from_="ivan", subject="s")
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.code == EX_MTA
    assert any("UNSIGNED" in n for n in refused.value.notices)


# ── no value carries a control character; TAB survives (RULED, decision 6) ───

@pytest.mark.parametrize("field, value, kind", [
    ("subject", "an \x1b[31mescape", "subject"), ("subject", "a\x00nul", "subject"),
    ("to", "pi\x7fp", "address"), ("from_", "co\x08ra", "address"),
    ("task", "SM-\x0c18", "header"), ("project", "session\x0bmail", "header"),
])
def test_a_control_character_in_a_header_value_is_refused_by_its_field(sender, env, fake_sendmail, field, value, kind):
    req = sender.Request(to="eve", from_="ivan", subject="s")
    setattr(req, field, value)
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.kind == kind and refused.value.sent == "no"
    assert "control character" in str(refused.value) and fake_sendmail.wire is None


def test_a_control_character_in_a_cc_is_an_address_refusal(sender, env, fake_sendmail):
    req = sender.Request(to="eve", from_="ivan", subject="s", cc=["al\x01pha"])
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.kind == "address" and fake_sendmail.wire is None


def test_a_tab_in_a_subject_is_sent_and_is_on_the_wire(sender, env, fake_sendmail):
    """A tab is ordinary
    white space in a mail header."""
    req = sender.Request(to="eve", from_="ivan", subject="one\ttwo")
    sender.send(req, b"x\n", env)
    assert b"Subject: one\ttwo\n" in fake_sendmail.wire


def test_a_line_break_keeps_its_own_words_ahead_of_the_control_refusal(sender, env, fake_sendmail):
    req = sender.Request(to="eve", from_="ivan", subject="a\nb\x1b")
    with pytest.raises(sender.Refused, match="line break in the --subject value"):
        sender.send(req, b"x\n", env)


# ── SM-538 do-002: X-Original-Subject, and the core guards every header ──────
#
# openspec/changes/subject-shorten: "X-Original-Subject carries the exact
# original" and "The core guards every header value" (SM-342 DOOR 2). The
# shortener (do-003) and the door (do-005) are not here: these tests hand the
# CORE a structured request, the road a structured caller such as the pager
# takes, so the guard is proven where it sits and not only at the CLI door.

import email
import email.policy


def _parsed(wire: bytes):
    return email.message_from_bytes(wire, policy=email.policy.default)


@pytest.mark.parametrize("value, words", [
    ("a\nX-Evil: 1", "line break"), ("a\rX-Evil: 1", "line break"), ("a\x00b", "control character"),
])
def test_an_original_subject_that_could_inject_is_refused_kind_subject(sender, env, fake_sendmail, value, words):
    req = sender.Request(to="eve", from_="ivan", subject="s", original_subject=value)
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.kind == "subject" and refused.value.sent == "no"
    assert words in str(refused.value) and "X-Original-Subject" in str(refused.value)
    assert fake_sendmail.wire is None


@pytest.mark.parametrize("name, value, words", [
    ("X-Foo", "a\nX-Session-From: forged", "line break"),
    ("X-Foo", "a\rX-Session-From: forged", "line break"),
    ("X-Foo", "a\x00b", "control character"),
    ("X-Foo", "a\x1b[31mb", "control character"),
    ("X-Foo\nX-Session-From", "forged", "header name"),
    ("X-Foo: bar", "v", "header name"),
    # an OWNED name needs no line break: written before the true stamp, it parses
    # FIRST (engineer review of do-002). Every case, any case spelling.
    ("X-Session-From", "forged", "written by send itself"),
    ("x-session-from", "forged", "written by send itself"),
    ("X-Session-Anything", "forged", "written by send itself"),
    ("X-Original-Subject", "a fake original", "written by send itself"),
    ("From", "forged", "written by send itself"),
    ("Message-ID", "<forged@x>", "written by send itself"),
    ("Content-Type", "text/html", "written by send itself"),
    ("X-Foo", "a\r\nX-Session-From: forged", "line break"),
])
def test_door_2_a_structured_header_that_could_inject_is_refused(sender, env, fake_sendmail, name, value, words):
    """SM-342 DOOR 2: `req.headers` reached the wire unguarded by the core;
    only the CLI door's `_send.parse_headers` refused a line break. A
    structured caller (the pager, an MCP or web door) comes straight here."""
    req = sender.Request(to="eve", from_="ivan", subject="s", headers=[(name, value)])
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.kind == "header" and refused.value.sent == "no"
    assert words in str(refused.value)
    assert fake_sendmail.wire is None, "nothing reaches the MTA"


def test_door_2_a_tab_in_a_structured_header_value_is_sent(sender, env, fake_sendmail):
    """TAB is ordinary white space in a header (RULED, decision 6)."""
    req = sender.Request(to="eve", from_="ivan", subject="s", headers=[("X-Foo", "one\ttwo")])
    sender.send(req, b"x\n", env)
    assert b"\nX-Foo: one\ttwo\n" in fake_sendmail.wire


def test_no_original_subject_means_no_header(sender, env, fake_sendmail):
    sender.send(sender.Request(to="eve", from_="ivan", subject="s"), b"x\n", env)
    assert b"X-Original-Subject" not in fake_sendmail.wire


@pytest.mark.parametrize("original", [
    " ".join(["word"] * 24),                                              # 119 chars, ASCII
    "SM-538 — design ready for the foreman to decompose into “slices”",  # em dash + smart quotes
    "see /usr/" + "a" * 101,                                              # a 110-char word: kept whole
    "two  spaces  kept  " + "x" * 70 + "  exactly",                       # runs of spaces survive folding
])
def test_the_original_round_trips_exactly_through_a_parser(sender, env, fake_sendmail, original):
    req = sender.Request(to="eve", from_="ivan", subject="sent", original_subject=original)
    sender.send(req, b"x\n", env)
    msg = _parsed(fake_sendmail.wire)
    assert msg["X-Original-Subject"] == original
    assert msg["Subject"] == "sent"


def test_the_original_is_folded_at_its_own_spaces_within_78_where_it_can_be(sender, env, fake_sendmail):
    original = " ".join(["word"] * 40)                                    # 199 chars
    req = sender.Request(to="eve", from_="ivan", subject="s", original_subject=original)
    sender.send(req, b"x\n", env)
    head = fake_sendmail.wire.partition(b"\n\n")[0].decode()
    start = head.index("X-Original-Subject:")
    lines = [head[start:].split("\n")[0]]
    for line in head[start:].split("\n")[1:]:
        if not line[:1] in (" ", "\t"):
            break
        lines.append(line)
    assert len(lines) > 1, "a 199-char value folds"
    assert all(len(line) <= 78 for line in lines), lines
    assert all(line.strip() for line in lines), "no line is white space alone (RFC 5322 3.2.2)"
    unfolded = "\n".join(lines).replace("\n", "")
    assert unfolded == "X-Original-Subject: " + original, "unfolding gives the value back exactly"


def test_a_non_ascii_original_is_rfc2047_and_no_line_passes_78(sender, env, fake_sendmail):
    original = "SM-538 — design ready for the foreman to decompose into slices"
    req = sender.Request(to="eve", from_="ivan", subject="s", original_subject=original)
    sender.send(req, b"x\n", env)
    head = fake_sendmail.wire.partition(b"\n\n")[0]
    assert head.isascii(), "encoded words on the wire, never raw UTF-8"
    block = head.decode().split("X-Original-Subject:")[1].split("\nX-")[0]
    assert "=?UTF-8?B?" in block
    assert all(len(line) <= 78 for line in ("X-Original-Subject:" + block).split("\n"))


def test_x_original_subject_sits_after_x_task_and_before_custom_headers(sender, env, fake_sendmail):
    req = sender.Request(to="eve", from_="ivan", subject="s", task="SM-538", original_subject="orig",
                         headers=[("X-Foo", "bar")])
    sender.send(req, b"x\n", env)
    names = [k for k, _ in headers_of(fake_sendmail.wire)]
    assert names.index("X-Task") + 1 == names.index("X-Original-Subject") == names.index("X-Foo") - 1


def test_a_word_too_long_for_any_line_drops_the_header_and_says_so(sender, env, fake_sendmail):
    """The do-001 spec's residual edge: a word that cannot be both whole and
    within 998. The MAIL goes; the header does not,
    and the receipt says why. Never split: that breaks the exact round-trip."""
    original = "x" * 1000
    req = sender.Request(to="eve", from_="ivan", subject="s", original_subject=original)
    receipt = sender.send(req, b"x\n", env)
    assert b"X-Original-Subject" not in fake_sendmail.wire
    assert any("X-Original-Subject not written" in n and "998" in n for n in receipt.notices)
    assert all(len(line) <= 998 for line in fake_sendmail.wire.split(b"\n"))


def test_the_original_is_outside_the_signature_and_the_verifier_says_good(sender, keyed, tmp_path, fake_sendmail, verifier):
    """RULED Q5: SIG_FIELDS is unchanged; the signed set covers the subject AS SENT."""
    assert sender.SIG_FIELDS == "from:to:subject:date:message-id"
    req = sender.Request(to="eve", from_="ivan", subject="sent", original_subject="SM-538 — the original")
    sender.send(req, b"signed body\n", keyed)
    delivered = tmp_path / "delivered.eml"
    delivered.write_bytes(fake_sendmail.wire)
    done = subprocess.run([str(verifier), str(delivered)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_door_2_the_pager_own_headers_are_not_owned(sender, env, fake_sendmail):
    """The core's OWNED refusal must not catch the pager's structured headers
    (pager.py note send: Pager-Note, Priority, Pager-Proof)."""
    req = sender.Request(to="eve", from_="ivan", subject="s", no_body=True,
                         headers=[("Pager-Note", "n"), ("Priority", "non-urgent"), ("Pager-Proof", "p")])
    sender.send(req, b"", env)
    assert b"\nPager-Note: n\n" in fake_sendmail.wire


def _owned_names():
    from sessionmail import _send
    return sorted(_send.OWNED) + [p + "anything" for p in sorted(_send.OWNED_PREFIXES)]


@pytest.mark.parametrize("name", _owned_names())
def test_door_2_the_core_refuses_every_owned_name_the_cli_door_refuses(sender, env, fake_sendmail, name):
    """Round-2 re-verify of e537c9b (SM-538 do-002): walk the WHOLE `_send.OWNED`
    list and each prefix, not a sample, in both spellings, so a name added to
    OWNED later is guarded at the core without a test edit. And parity: the
    CLI door (`_send.parse_headers`) refuses the very same name, so the two
    doors cannot drift apart."""
    from sessionmail import _send
    for spelling in (name, name.upper()):
        with pytest.raises(ValueError, match="written by send itself"):
            _send.parse_headers((f"{spelling}: v",))
        req = sender.Request(to="eve", from_="ivan", subject="s", headers=[(spelling, "v")])
        with pytest.raises(sender.Refused) as refused:
            sender.send(req, b"x\n", env)
        assert refused.value.kind == "header" and refused.value.sent == "no"
        assert "written by send itself" in str(refused.value)
        assert fake_sendmail.wire is None, "nothing reaches the MTA"


def test_door_2_a_structured_header_cannot_shadow_the_true_session_stamp(sender, env, fake_sendmail):
    """The forgery itself, end to end: with a session id in env, a structured
    X-Session-From is refused; without it in the request the true stamp is
    the one `email.policy.default` returns."""
    import email
    import email.policy
    seat = dict(env, CLAUDE_CODE_SESSION_ID="35bf6365-0000-0000-0000-000000000000")
    req = sender.Request(to="eve", from_="ivan", subject="s",
                         headers=[("X-Session-From", "forged")])
    with pytest.raises(sender.Refused):
        sender.send(req, b"x\n", seat)
    assert fake_sendmail.wire is None
    sender.send(sender.Request(to="eve", from_="ivan", subject="s", headers=[("X-Foo", "ok")]),
                b"x\n", seat)
    msg = email.message_from_bytes(fake_sendmail.wire, policy=email.policy.default)
    assert msg.get_all("X-Session-From") == ["35bf6365"], "one stamp, the true one"
    assert msg["X-Foo"] == "ok"


# ── SM-716: a caller-chosen Message-ID (`--message-id`) ──────────────────────
# Design: store projects/sessionmail/tasks/SM-716_feat_send-msgid/design.md @
# 8a4f26595, §1, §2, §5 and §10 do-002, with the review's build notes N2-N6.
# "Nothing sent" is N4's: zero sendmail calls AND, on the drop road, no file.

FAKE_VMAILBOX_GEN = """#!{python}
import os, sys
if "--print" in sys.argv[1:]:
    sys.stdout.write(os.environ.get("FAKE_VMAILBOX", ""))
"""


@pytest.fixture
def drop_env(env, tmp_path, bin_dir):
    """The same seat on the mail-drop road (no MTA): `eve` and `ivan` are
    boxes the (fake) map generator names, so an ACCEPTED send lands a file."""
    agents = tmp_path / "dropmail" / "agents"
    for box in ("eve", "ivan"):
        for sub in ("cur", "new", "tmp"):
            (agents / box / sub).mkdir(parents=True)
    write_fake(bin_dir / "vmailbox-gen", FAKE_VMAILBOX_GEN.format(python=sys.executable))
    return dict(env, SESSIONMAIL_SENDMAIL="drop", SESSIONMAIL_ROOT=str(agents),
                SESSION_MAIL_ROOT=str(agents),
                SESSIONMAIL_VMAILBOX_GEN=str(bin_dir / "vmailbox-gen"),
                FAKE_VMAILBOX="".join(f"{b}@{HOST}\tagents/{b}/\n" for b in ("eve", "ivan")))


def dropped(env) -> list[Path]:
    """Every file in every box under the drop root, tmp/ included."""
    return sorted(p for p in Path(env["SESSIONMAIL_ROOT"]).glob("*/*/*") if p.is_file())


def refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail, *extra):
    """Run one send on the sendmail road and on the drop road; return the
    sendmail road's (rc, err) after asserting the drop road said the same and
    that NOTHING was sent on either (N4)."""
    rc, out, err = send(sender, env, tmp_path, *extra)
    assert fake_sendmail.wire is None and fake_sendmail.argv is None, "sendmail was called"
    drc, dout, derr = send(sender, drop_env, tmp_path, *extra)
    assert dropped(drop_env) == [], "the drop wrote a file"
    assert (drc, dout) == (rc, out) == (EX_REFUSED, "")
    assert derr.splitlines()[-1] == err.splitlines()[-1]
    return rc, err


def kind_of(sender, env, **fields) -> str:
    """The same refusal through the structured door: its KIND and its code."""
    req = sender.Request(to="eve", from_="eve", subject="s", **fields)
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.code == EX_REFUSED and refused.value.sent == "no"
    return refused.value.kind


@pytest.mark.parametrize("value", [f"<x.1@{HOST}>", f"x.1@{HOST}"], ids=["brackets", "bare"])
def test_message_id_the_chosen_id_is_the_sent_id(sender, env, tmp_path, fake_sendmail, value):
    """SC1: stdout is the chosen id, normalised; the wire carries exactly ONE
    Message-ID and it is that id; the Date is still minted from `now`."""
    rc, out, err = send(sender, env, tmp_path, "--message-id", value, now=1907536000, pid=1, rand=2)
    assert rc == EX_OK, err
    assert out == f"<x.1@{HOST}>\n"
    h = headers_of(fake_sendmail.wire)
    assert [v for k, v in h if k.lower() == "message-id"] == [f"<x.1@{HOST}>"]
    assert dict(h)["Date"] == sender.mint(HOST, 1907536000, 1, 2)[1]


def test_message_id_only_the_message_id_line_moves(sender, env, tmp_path, fake_sendmail):
    """The substitution is the Message-ID and nothing else: the same send,
    with and without the flag, differs in that one line."""
    minted = sender.mint(HOST, 1907536000, 4242, 777)[0]
    rc, _, _ = send(sender, env, tmp_path, now=1907536000, pid=4242, rand=777)
    assert rc == EX_OK
    without = fake_sendmail.wire
    rc, _, _ = send(sender, env, tmp_path, "--message-id", f"<chosen@{HOST}>",
                    now=1907536000, pid=4242, rand=777)
    assert rc == EX_OK
    assert fake_sendmail.wire == without.replace(f"Message-ID: {minted}\n".encode(),
                                                 f"Message-ID: <chosen@{HOST}>\n".encode())
    assert fake_sendmail.wire != without


def test_message_id_the_host_is_compared_case_insensitively(sender, env, tmp_path, fake_sendmail):
    rc, out, err = send(sender, env, tmp_path, "--message-id", f"x@{HOST.upper()}")
    assert rc == EX_OK, err
    assert out == f"<x@{HOST.upper()}>\n"
    env["SESSIONMAIL_HOST"] = env["SESSION_MAIL_HOST"] = HOST.upper()
    rc, out, err = send(sender, env, tmp_path, "--message-id", f"y@{HOST}")
    assert rc == EX_OK, err


def test_message_id_the_drop_road_carries_the_chosen_id(sender, drop_env, tmp_path):
    """The positive control for every 'no file' below: accepted, it lands."""
    rc, out, err = send(sender, drop_env, tmp_path, "--message-id", f"d.1@{HOST}")
    assert rc == EX_OK, err
    assert out == f"<d.1@{HOST}>\n"
    files = dropped(drop_env)
    assert len(files) == 1 and f"Message-ID: <d.1@{HOST}>\n".encode() in files[0].read_bytes()


@pytest.mark.parametrize("value", ["", "no-at-sign", "<a@b>@c", f"a b@{HOST}",
                                   f"&lt;x@{HOST}&gt;", f"a\nb@{HOST}"],
                         ids=["empty", "no-at", "two-at", "space", "html-escaped", "line-break"])
def test_message_id_a_malformed_id_is_refused_and_nothing_sent(
        sender, env, drop_env, tmp_path, fake_sendmail, value):
    """SC3: exit 2, kind `message-id`, stderr names `--message-id` and the
    parser's defect; refused in `validate`, before the body is read."""
    rc, err = refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail,
                                    "--message-id", value)
    defect = sender.check_msgid(value)[1]
    assert defect
    assert f"invalid --message-id value {value!r}" in err and f"({defect})" in err
    assert "It would become the Message-ID header." in err
    assert "copy it from the Message-ID printed at send time" not in err, "a NEW id is not copied"
    assert kind_of(sender, env, message_id=value) == "message-id"


def test_message_id_the_html_escaped_hint_says_retype_and_not_copy(sender, env, tmp_path, fake_sendmail):
    rc, _, err = send(sender, env, tmp_path, "--message-id", f"&lt;x@{HOST}&gt;")
    assert rc == EX_REFUSED
    assert "THIS IS THE HTML-ESCAPED FORM: retype the id with real angle brackets" in err
    # ... and the threading flags' words are byte for byte what they were.
    rc, _, err = send(sender, env, tmp_path, "--in-reply-to", f"&lt;x@{HOST}&gt;")
    assert rc == EX_REFUSED
    assert err.endswith("It would become the In-Reply-To: header and match nothing. THIS IS THE "
                        "HTML-ESCAPED FORM: retype the id with real angle brackets, --in-reply-to "
                        "'<id@host>' - copy it from the Message-ID printed at send time, not "
                        "from a mail body.\n")
    assert kind_of(sender, env, in_reply_to="x") == "thread-id"


def test_message_id_a_malformed_id_is_refused_before_the_body_is_read(sender, env, tmp_path, fake_sendmail):
    rc, _, err = sender.run(["eve", "--from", "eve", "--subject", "s", "--body-file",
                             str(tmp_path / "no-such-body"), "--message-id", "no-at-sign"], env)
    assert rc == EX_REFUSED and "invalid --message-id" in err and "no-such-body" not in err


@pytest.mark.parametrize("value", ["x@other.example", f"x@{HOST}.example.net",
                                   f"x@{HOST}.session-mail", f"<1907536000.4242.777@{HOST}.session-mail>"],
                         ids=["other-host", "fqdn", "minted-suffix", "a-minted-id"])
def test_message_id_another_host_is_refused_and_nothing_sent(
        sender, env, drop_env, tmp_path, fake_sendmail, value):
    """SC4, BARE ONLY (RULED OQ5): another host, the FQDN and the minted
    `.session-mail` suffix are refused at the top of `_deliver`. N3: the two
    ruled clauses, not one exact string."""
    rc, err = refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail,
                                    "--message-id", value)
    rhs = value.strip("<>").rsplit("@", 1)[1]
    assert "--message-id" in err and repr(rhs) in err
    assert f"use the bare host {HOST!r}" in err
    assert "an id ending .session-mail looks copied from a minted message" in err.lower()
    assert "NOTHING WAS SENT" in err
    assert kind_of(sender, env, message_id=value) == "message-id"


def test_message_id_the_host_refusal_comes_before_render_preflight_and_the_mta(
        sender, env, tmp_path, fake_sendmail, fake_postfix):
    """It sits above everything that touches the world: with a recipient the
    pre-flight would refuse, a human (render owed), and no MTA at all, the
    refusal is still the id's own."""
    fake_postfix()                                          # nobody is mapped
    env["SESSIONMAIL_SENDMAIL"] = str(tmp_path / "no-such-sendmail")
    req = sender.Request(to="nobody", from_="eve", subject="s", message_id="x@other.example")
    with pytest.raises(sender.Refused) as refused:
        sender.send(req, b"x\n", env)
    assert refused.value.kind == "message-id"
    assert refused.value.notices == [], "nothing ran before it to say anything"


def test_message_id_a_non_ascii_id_is_refused(sender, env, drop_env, tmp_path, fake_sendmail):
    """OQ6: `check_msgid` alone accepts it (MEASURED); `--message-id` does not."""
    value = f"é@{HOST}"
    assert sender.check_msgid(value) == (f"<{value}>", None)
    rc, err = refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail,
                                    "--message-id", value)
    assert "invalid --message-id value" in err and "a Message-ID is ASCII" in err
    assert kind_of(sender, env, message_id=value) == "message-id"


def edge_id(sender) -> str:
    """A normalised id exactly at the bound (N6), at this host."""
    suffix = f"@{HOST}>"
    edge = "<" + "a" * (sender.MESSAGE_ID_MAX - 1 - len(suffix)) + suffix
    assert len(edge) == 985 == sender.MESSAGE_ID_MAX
    return edge


def test_message_id_985_normalised_is_sent(sender, env, tmp_path, fake_sendmail):
    """OQ6 as bounded at decompose (N6): 985 characters, angle brackets
    included, keeps `Message-ID: ` (12) and a reply's `In-Reply-To: ` (13)
    within RFC 5322's 998."""
    edge = edge_id(sender)
    rc, out, err = send(sender, env, tmp_path, "--message-id", edge.strip("<>"))
    assert rc == EX_OK, err
    assert out == edge + "\n"
    assert f"\nMessage-ID: {edge}\n".encode() in fake_sendmail.wire
    assert len(f"In-Reply-To: {edge}") == 998


def test_message_id_986_normalised_is_refused(sender, env, drop_env, tmp_path, fake_sendmail):
    over = "<a" + edge_id(sender)[1:]
    rc, err = refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail,
                                    "--message-id", over)
    assert "invalid --message-id value" in err
    assert "986 characters" in err and "may not exceed 985" in err
    assert kind_of(sender, env, message_id=over) == "message-id"


@pytest.mark.parametrize("flag", ["--in-reply-to", "--supersedes"])
def test_message_id_equal_to_the_id_it_threads_to_is_refused(
        sender, env, drop_env, tmp_path, fake_sendmail, flag):
    """OQ7 (TAKEN): compared after BOTH are normalised, so the brackets do
    not matter, and the refusal names the flag the caller typed (N2), even
    though `validate` copies --supersedes into --in-reply-to. (`-r` is the
    `message send` door's spelling of --in-reply-to; the core's argv has none.)"""
    rc, err = refused_on_both_roads(sender, env, drop_env, tmp_path, fake_sendmail,
                                    "--message-id", f"<x@{HOST}>", flag, f"x@{HOST}")
    assert f"is the same id as this send's {flag}" in err
    other = "--in-reply-to" if flag == "--supersedes" else "--supersedes"
    assert other not in err
    field = "supersedes" if flag == "--supersedes" else "in_reply_to"
    assert kind_of(sender, env, message_id=f"x@{HOST}", **{field: f"<x@{HOST}>"}) == "message-id"


def test_message_id_the_checks_are_idempotent(sender):
    """`validate` runs twice on the argv road: a request that passed once
    passes again, unchanged, with supersedes already copied to in_reply_to."""
    req = sender.Request(to="eve", from_="eve", subject="s", message_id=f"n@{HOST}",
                         supersedes=f"old@{HOST}")
    sender.validate(req)
    first = (req.message_id, req.in_reply_to, req.supersedes)
    sender.validate(req)
    assert (req.message_id, req.in_reply_to, req.supersedes) == first == (
        f"<n@{HOST}>", f"<old@{HOST}>", f"<old@{HOST}>")


def test_message_id_the_threading_flags_keep_their_leniency(sender):
    """OQ6 is `--message-id`'s alone: a reply to an odd id already in a box
    still threads to it."""
    req = sender.Request(to="eve", from_="eve", subject="s", in_reply_to="é@elsewhere")
    sender.validate(req)
    assert req.in_reply_to == "<é@elsewhere>"


def test_message_id_unset_mints_exactly_as_before_and_the_pager_seam_is_unchanged(
        sender, env, fake_sendmail):
    """SC8: no flag, no field: `Request.message_id` defaults to None and the
    minted id goes out. The pager's confirmations build their `Request`
    without the field and fix the minted parts through `send` (pager.py
    `_mta_send`); that call is exactly this one."""
    import dataclasses
    assert {f.name: f.default for f in dataclasses.fields(sender.Request)}["message_id"] is None
    req = sender.Request(to="eve", from_="ivan", subject="ok", no_body=True)
    receipt = sender.send(req, b"", env, now=1907536000, pid=4242, rand=777)
    assert receipt.message_id == f"<1907536000.4242.777@{HOST}.session-mail>"
    assert dict(headers_of(fake_sendmail.wire))["Message-ID"] == receipt.message_id
    assert sender.parse_argv(["eve", "--from", "ivan"]).message_id is None


# ── SM-839: one process never mints one Message-ID twice ─────────────────────


def test_one_process_never_draws_one_rand_twice_in_a_second(sender, monkeypatch):
    """SM-839: in process every send shares one pid, so `<epoch.pid.rand>`
    is only the rand within a second. A rand already drawn is drawn again,
    never returned; the next second starts clean."""
    draws = iter([5, 5, 5, 9, 5, 9, 11])
    monkeypatch.setattr(sender.random, "randint", lambda a, b: next(draws))
    monkeypatch.setattr(sender, "_DRAWN", {})
    assert [sender.fresh_rand(1907536000) for _ in range(3)] == [5, 9, 11]
    monkeypatch.setattr(sender.random, "randint", lambda a, b: 5)
    assert sender.fresh_rand(1907536001) == 5          # a new second: 5 is free again


def test_a_full_second_refuses_rather_than_spin(sender, monkeypatch):
    monkeypatch.setattr(sender, "_DRAWN", {1907536000: set(range(sender.RAND_MAX + 1))})
    with pytest.raises(RuntimeError, match="refusing to mint a duplicate"):
        sender.fresh_rand(1907536000)
    assert sender.fresh_rand(1907536001) in range(sender.RAND_MAX + 1)


def test_old_seconds_are_forgotten(sender, monkeypatch):
    monkeypatch.setattr(sender, "_DRAWN", {1907536000: {1}, 1907536050: {2}})
    sender.fresh_rand(1907536061)
    assert set(sender._DRAWN) == {1907536050, 1907536061}


def test_two_sends_in_one_second_that_draw_one_rand_get_two_ids(sender, env, fake_sendmail,
                                                                 monkeypatch):
    """The send itself: pinned clock, pid and a $RANDOM that repeats. The
    second send's id is still its own; a rand the caller FIXES is kept."""
    draws = iter([777, 777, 778])
    monkeypatch.setattr(sender.random, "randint", lambda a, b: next(draws))
    monkeypatch.setattr(sender, "_DRAWN", {})

    def req():
        return sender.Request(to="eve", from_="ivan", subject="ok", no_body=True)

    one = sender.send(req(), b"", env, now=1907536000, pid=4242).message_id
    two = sender.send(req(), b"", env, now=1907536000, pid=4242).message_id
    assert (one, two) == (f"<1907536000.4242.777@{HOST}.session-mail>",
                          f"<1907536000.4242.778@{HOST}.session-mail>")
    fixed = sender.send(req(), b"", env, now=1907536000, pid=4242, rand=777).message_id
    assert fixed == one
