"""The mail drop: `SESSIONMAIL_SENDMAIL=drop`, delivery with no MTA.

A host with no postfix
delivers by mail drop. What is pinned here:

- recipients resolve through the MAP GENERATOR's own output (a fake one for
  the resolution rules; the REAL packaged one, once, end to end), so the drop
  lands mail exactly where postfix would;
- a seat's mail reaches the seat's box AND the live session's (the doorbell
  copy the pager listens for);
- the gate: an unknown recipient refuses by name, all or nothing, BEFORE the
  mint, and writes nothing anywhere - watched failing, not assumed;
- the drop resolves a box and never mints one;
- drop mode swaps postfix's pre-flight for its own (the postfix one is shown
  live first, so the swap is real).

Fixtures are `alpha`, `beta` and `gamma` on `testhost` (convention 9).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TEST_ORIGIN
from fakes import write_fake
from sessionmail import _sig
from test_sender import VERIFIER, keyed  # noqa: F401  (keyed: a fixture, used by name)

EX_OK, EX_MTA = 0, 3
HOST = "testhost"
SID = "0000aaaa-1111-2222-3333-444455556666"

FAKE_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
if "--print" in args:
    sys.stdout.write(os.environ.get("FAKE_VMAILBOX", ""))
elif "--print-valias" in args:
    rc = int(os.environ.get("FAKE_VALIAS_RC") or 0)
    if rc:
        sys.stderr.write("vmailbox-gen: valias refused (fake)\\n")
        sys.exit(rc)
    sys.stdout.write(os.environ.get("FAKE_VALIAS", ""))
"""

FAKE_POSTCONF = """#!{python}
import sys
sys.stdout.write("hash:/maps/vmailbox\\nhash:/maps/valias\\n")
"""

FAKE_POSTMAP = """#!{python}
import sys
sys.exit(1)   # no address is in postfix's maps: the postfix pre-flight refuses all
"""

FAKE_SENDMAIL = """#!{python}
import sys
sys.stdin.buffer.read()
"""

VMAILBOX = "".join(f"{n}@{HOST}\tagents/{n}/\n" for n in ("alpha", "beta", SID))
VALIAS = (f"alpha@{HOST}\talpha@{HOST}, {SID}@{HOST}\n"
          f"alpha+memory@{HOST}\talpha+memory@{HOST}\n")


@pytest.fixture
def sender():
    import importlib
    return importlib.import_module("sessionmail._sender")


@pytest.fixture
def mailroot(tmp_path):
    root = tmp_path / "mailroot"
    for box in ("alpha", "beta", SID):
        for d in ("cur", "new", "tmp"):
            (root / "agents" / box / d).mkdir(parents=True)
    return root


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    write_fake(d / "vmailbox-gen", FAKE_GEN.format(python=sys.executable))
    return d


@pytest.fixture
def env(tmp_path, bin_dir, mailroot):
    return {
        "HOME": str(tmp_path),
        "PATH": str(bin_dir),
        "TMPDIR": str(tmp_path),
        "SESSION_MAIL_ROOT": str(mailroot / "agents"), "SESSIONMAIL_ROOT": str(mailroot / "agents"),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(bin_dir / "vmailbox-gen"),
        "FAKE_VMAILBOX": VMAILBOX,
        "FAKE_VALIAS": VALIAS,
    }


def body_file(tmp_path: Path, data: bytes = b"hello\n") -> str:
    p = tmp_path / "body.md"
    p.write_bytes(data)
    return str(p)


def landed(mailroot: Path) -> dict[str, list[Path]]:
    """Every file in every box's new/ AND tmp/ - tmp/ too, so 'nothing
    written' cannot hide a half-written copy."""
    out: dict[str, list[Path]] = {}
    for box in sorted((mailroot / "agents").iterdir()):
        files = sorted([*(box / "new").glob("*"), *(box / "tmp").glob("*")])
        if files:
            out[box.name] = files
    return out


def send(sender, env, tmp_path, *argv):
    return sender.run([*argv, "--from", "beta", "--subject", "drop test",
                       "--body-file", body_file(tmp_path)], env)


# ── delivery ─────────────────────────────────────────────────────────────────

def test_seat_mail_lands_in_the_seat_box_and_the_live_session_box(sender, env, tmp_path, mailroot):
    rc, out, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err
    boxes = landed(mailroot)
    assert set(boxes) == {"alpha", SID}, boxes
    for name, files in boxes.items():
        assert len(files) == 1 and files[0].parent.name == "new"
        raw = files[0].read_bytes()
        # The drop qualifies a bare From as postfix would; this fake
        # generator asks for no suffix, so the domain is the short host.
        assert raw.startswith(b"Return-Path: <beta@testhost>\nX-Original-To: alpha\n")
        assert f"Delivered-To: {name}@{HOST}\n".encode() in raw
        assert f"Message-ID: {out.strip()}".encode() in raw
        assert raw.endswith(b"\n\nhello\n")
    assert f"sent via mail drop (no MTA) → alpha, {SID}" in err


def test_bcc_is_delivered_and_its_header_is_gone(sender, env, tmp_path, mailroot):
    rc, _, err = send(sender, env, tmp_path, "alpha", "--bcc", "beta")
    assert rc == EX_OK, err
    boxes = landed(mailroot)
    assert "beta" in boxes
    for files in boxes.values():
        assert b"\nBcc:" not in files[0].read_bytes()


def test_a_plus_address_with_an_identity_line_lands_in_the_base_box_only(sender, env, tmp_path, mailroot):
    """The generator's `<seat>+memory` identity line: memory writes are NOT
    copied into session boxes (vmailbox-gen, THE ALIAS MAP)."""
    rc, _, err = send(sender, env, tmp_path, "alpha+memory")
    assert rc == EX_OK, err
    assert set(landed(mailroot)) == {"alpha"}


def test_a_fully_qualified_local_address_is_this_host(sender, env, tmp_path, mailroot):
    rc, _, err = send(sender, env, tmp_path, f"beta@{HOST}.example.invalid")
    assert rc == EX_OK, err
    assert set(landed(mailroot)) == {"beta"}


# ── the gate: refuse by name, all or nothing, nothing written ────────────────

def test_an_unknown_recipient_refuses_by_name_and_writes_nothing(sender, env, tmp_path, mailroot):
    rc, out, err = send(sender, env, tmp_path, "gamma")
    assert rc == EX_MTA
    assert out == ""                              # nothing minted: no Message-ID
    assert "REFUSING TO SEND (mail drop)" in err and "'gamma'" in err
    assert "NOTHING WAS DELIVERED" in err
    assert landed(mailroot) == {}


def test_one_unknown_recipient_refuses_the_whole_send(sender, env, tmp_path, mailroot):
    rc, _, err = send(sender, env, tmp_path, "alpha", "--cc", "gamma")
    assert rc == EX_MTA, err
    assert landed(mailroot) == {}


def test_a_row_without_a_maildir_refuses_and_no_box_is_made(sender, env, tmp_path, mailroot):
    env["FAKE_VMAILBOX"] = VMAILBOX + f"delta@{HOST}\tagents/delta/\n"
    rc, _, err = send(sender, env, tmp_path, "delta")
    assert rc == EX_MTA, err
    assert "never makes a box" in err
    assert not (mailroot / "agents" / "delta").exists()


def test_another_host_is_refused(sender, env, tmp_path, mailroot):
    rc, _, err = send(sender, env, tmp_path, "alpha@elsewhere.example.invalid")
    assert rc == EX_MTA
    assert "not on this host" in err
    assert landed(mailroot) == {}


def test_a_map_value_cannot_lead_out_of_the_mail_root(sender, env, tmp_path, mailroot):
    outside = tmp_path / "outside"
    for d in ("cur", "new", "tmp"):
        (outside / d).mkdir(parents=True)
    env["FAKE_VMAILBOX"] = VMAILBOX + f"delta@{HOST}\t../outside/\n"
    rc, _, _ = send(sender, env, tmp_path, "delta")
    assert rc == EX_MTA
    assert list((outside / "new").iterdir()) == []


def test_no_alias_map_is_announced_and_seat_mail_still_lands_in_the_seat_box(sender, env, tmp_path, mailroot):
    env["FAKE_VALIAS_RC"] = "3"
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert "no alias map" in err and "doorbell" in err
    assert set(landed(mailroot)) == {"alpha"}


# ── drop mode swaps the pre-flight; the swapped-out one is shown live ────────

def test_the_postfix_preflight_is_live_and_drop_mode_replaces_it(sender, env, tmp_path, bin_dir, mailroot):
    write_fake(bin_dir / "postconf", FAKE_POSTCONF.format(python=sys.executable))
    write_fake(bin_dir / "postmap", FAKE_POSTMAP.format(python=sys.executable))
    write_fake(bin_dir / "sendmail", FAKE_SENDMAIL.format(python=sys.executable))
    via_mta = {**env, "SESSIONMAIL_SENDMAIL": str(bin_dir / "sendmail")}
    rc, _, err = send(sender, via_mta, tmp_path, "alpha")
    assert rc == EX_MTA and "postfix has no route" in err       # the gate we swap, watched failing
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert "alpha" in landed(mailroot)


def test_no_mta_names_the_drop_as_a_way_out(sender, env, tmp_path):
    env["SESSIONMAIL_SENDMAIL"] = str(tmp_path / "no-such-sendmail")
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_MTA
    assert "SESSIONMAIL_SENDMAIL=drop" in err


RECORDING_SENDMAIL = """#!{python}
import os, sys
open(os.environ["SENDMAIL_LOG"], "ab").write(sys.stdin.buffer.read())
"""


def test_no_postfix_and_one_copy_per_box_when_recipients_overlap(sender, env, tmp_path, mailroot,
                                                                 monkeypatch):
    """SM-735 SC2: with NO postfix (no sendmail, postmap or postconf on the
    PATH, and the absolute default sendmail a tripwire), a send whose
    recipients overlap - To alpha (which the alias map fans out to alpha AND
    the live session), Cc the session itself, Bcc alpha again by its full
    name - lands exactly ONE copy in each resolved box, and none elsewhere."""
    for tool in ("sendmail", "postmap", "postconf"):
        assert shutil.which(tool, path=env["PATH"]) is None, tool   # no postfix, measured
    tripped = tmp_path / "default-sendmail.log"
    monkeypatch.setattr(sender, "DEFAULT_SENDMAIL", str(tmp_path / "default-sendmail"))
    write_fake(tmp_path / "default-sendmail", RECORDING_SENDMAIL.format(python=sys.executable))
    env = {**env, "SENDMAIL_LOG": str(tripped)}

    rc, out, err = send(sender, env, tmp_path, "alpha", "--cc", SID, "--bcc", f"alpha@{HOST}")
    assert rc == EX_OK, err
    boxes = landed(mailroot)
    assert set(boxes) == {"alpha", SID}, boxes                 # beta untouched
    for name, files in boxes.items():
        assert len(files) == 1, (name, files)                  # one copy, not one per mention
        assert files[0].parent.name == "new"
        assert f"Message-ID: {out.strip()}".encode() in files[0].read_bytes()
    assert not tripped.exists()                                # no MTA was ever started


def test_unset_sendmail_goes_by_the_drop_and_a_named_mta_never_falls_back_to_it(
        sender, env, tmp_path, mailroot, monkeypatch):
    """The drop is the default carrier. With SESSIONMAIL_SENDMAIL unset and
    no `sendmail` key, the send lands by the drop, and the conventional
    sendmail is never run. A NAMED MTA that is absent refuses `no MTA`; it
    does not quietly drop instead."""
    from conftest import write_mail_settings
    from sessionmail import _operator
    log = tmp_path / "default-sendmail.log"
    fake = tmp_path / "default-sendmail"
    write_fake(fake, RECORDING_SENDMAIL.format(python=sys.executable))
    monkeypatch.setattr(sender, "DEFAULT_SENDMAIL", str(fake))
    default = {k: v for k, v in env.items() if k != "SESSIONMAIL_SENDMAIL"}
    default["SENDMAIL_LOG"] = str(log)
    write_mail_settings(_operator.settings_home(), None)

    rc, out, err = send(sender, default, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert not log.exists()                                    # no MTA ran
    assert "sent via mail drop (no MTA)" in err and "smtp" not in err
    boxes = landed(mailroot)
    assert set(boxes) == {"alpha", SID}
    assert f"Message-ID: {out.strip()}".encode() in boxes["alpha"][0].read_bytes()
    for files in boxes.values():
        for f in files:
            f.unlink()

    write_mail_settings(_operator.settings_home(),
                        '{"version": 1, "hooks": false, "sendmail": "%s"}\n'
                        % (tmp_path / "no-such-sendmail"))
    rc, out, err = send(sender, default, tmp_path, "alpha")
    assert rc == EX_MTA and out == ""
    assert "no MTA" in err and "NOTHING WAS SENT" in err
    assert landed(mailroot) == {}                              # no silent fallback to the drop


def test_must_sign_refuses_in_drop_mode_before_the_drop(sender, env, tmp_path, mailroot):
    """SM-590 holds on the drop road too: a seat that MUST sign, with no key,
    is refused before the pre-flight and the mint, and the drop writes
    nothing. The same unkeyed send lands when signing is not required, so
    the refusal is the setting's doing, not the env's."""
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err                                   # unkeyed, not required: it lands
    shutil.rmtree(mailroot / "agents" / "alpha" / "new")
    shutil.rmtree(mailroot / "agents" / SID / "new")
    for box in ("alpha", SID):
        (mailroot / "agents" / box / "new").mkdir()
    rc, out, err = send(sender, {**env, sender.REQUIRE_SIGNATURE: "1"}, tmp_path, "alpha")
    assert rc != EX_OK
    assert out == ""                              # nothing minted: no Message-ID
    assert sender.REQUIRE_SIGNATURE in err and "NOTHING WAS SENT" in err
    assert "mail drop" not in err                 # refused before the drop was asked
    assert landed(mailroot) == {}


# ── the real generator, end to end ───────────────────────────────────────────

GEN = Path(__file__).resolve().parents[1] / "src" / "sessionmail" / "postfix" / "vmailbox-gen"


@pytest.mark.skipif(shutil.which("bash") is None, reason="the generator is a bash script")
def test_the_real_generator_decides_where_mail_lands(sender, env, tmp_path, mailroot):
    """No fake: the packaged generator's `--print` reads the tree. With no
    `session` on PATH its alias map refuses, which the drop announces."""
    system = os.pathsep.join(p for p in ("/usr/bin", "/bin", str(Path(shutil.which("bash")).parent)))
    real = {k: v for k, v in env.items() if not k.startswith("FAKE_")}
    real["SESSIONMAIL_VMAILBOX_GEN"] = str(GEN)
    real["PATH"] = system
    rc, out, err = send(sender, real, tmp_path, "beta")
    assert rc == EX_OK, err
    assert set(landed(mailroot)) == {"beta"}
    rc, _, err = send(sender, real, tmp_path, "gamma")
    assert rc == EX_MTA and "'gamma'" in err


def _system_path(tmp_path: Path) -> str:
    """The system dirs, plus a dir holding ONLY a link to the runner's bash
    (the generator needs a modern one). Not bash's own dir: on a Homebrew
    host that is /opt/homebrew/bin, which carries tailscale too."""
    only = tmp_path / "bash-only"
    if not only.exists():
        only.mkdir()
        (only / "bash").symlink_to(shutil.which("bash"))
    return os.pathsep.join(("/usr/bin", "/bin", str(only)))


def _script(path: Path, text: str) -> Path:
    """An executable shell script, written verbatim (`write_fake` formats)."""
    path.write_text(text)
    path.chmod(0o755)
    return path


PREDATES_GEN = """#!/bin/bash
# The real generator, but answering --capabilities as one that predates
# no-fqdn-v1 does: the drop must then run it exactly as before.
[ "${{1:-}}" = --capabilities ] && {{ printf 'nested-v1\\nvalias-v1\\nstore-box-v1\\nplus-v1\\n'; exit 0; }}
exec "{gen}" "$@"
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="the generator is a bash script")
def test_with_no_suffix_and_no_tailscale_the_real_generator_still_delivers(
        sender, env, tmp_path, mailroot):
    """SM-660 design-001 A, repro-001 gap 1 inverted: no MAGICDNS_SUFFIX, no
    tailscale on PATH or in the app-bundle seam, the real generator - and
    the send lands. A tailscale that WOULD answer is first on PATH and is
    never run. The same host with a generator that predates `no-fqdn-v1`
    still refuses (exit 69 in the drop, 3 at `message send`), and says why:
    the token is what lifts it."""
    calls = tmp_path / "tailscale-calls"
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    _script(fakes / "tailscale", "#!/bin/sh\n"
               f'echo "$*" >> "{calls}"\n'
               "printf '{\"MagicDNSSuffix\": \"tail.example\"}\\n'\n")
    real = {k: v for k, v in env.items() if not k.startswith("FAKE_")}
    real.pop("MAGICDNS_SUFFIX", None)
    # The sender qualifies a bare From under the drop and, with no
    # SESSIONMAIL_ORIGIN, would ask tailscale for the suffix (the mail drop
    # From test above). Pin the origin here so the only possible tailscale
    # caller left is the generator, which this test proves never asks.
    real.update(SESSIONMAIL_VMAILBOX_GEN=str(GEN), MAGICDNS_TAILSCALE_BUNDLE="",
                SESSIONMAIL_ORIGIN="beta.example",
                PATH=f"{fakes}{os.pathsep}{_system_path(tmp_path)}")
    assert shutil.which("tailscale", path=_system_path(tmp_path)) is None
    rc, out, err = send(sender, real, tmp_path, "beta")
    assert rc == EX_OK, err
    assert [p.parent.name for p in landed(mailroot)["beta"]] == ["new"]
    assert set(landed(mailroot)) == {"beta"}
    assert not calls.exists(), "the drop's generator ran tailscale"
    # An unknown box still refuses by name: the gate did not go away.
    rc, _, err = send(sender, real, tmp_path, "gamma")
    assert rc == EX_MTA and "'gamma'" in err

    old = tmp_path / "old-gen"
    _script(old, PREDATES_GEN.format(gen=GEN))
    rc, out, err = send(sender, {**real, "SESSIONMAIL_VMAILBOX_GEN": str(old),
                                 "PATH": _system_path(tmp_path)}, tmp_path, "beta")
    assert rc == EX_MTA, err
    assert out == ""
    assert "exit 4" in err and "predates no-fqdn-v1" in err and str(old) in err
    assert set(landed(mailroot)) == {"beta"}           # nothing more was written


RECORDING_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ARGV_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")
if args == ["--capabilities"]:
    sys.stdout.write(os.environ.get("FAKE_CAPS", ""))
elif "--print" in args:
    sys.stdout.write(os.environ.get("FAKE_VMAILBOX", ""))
elif "--print-valias" in args:
    sys.stdout.write(os.environ.get("FAKE_VALIAS", ""))
"""


@pytest.mark.parametrize("caps, flagged", [
    ("nested-v1\nvalias-v1\nstore-box-v1\nplus-v1\nno-fqdn-v1\n", True),
    ("nested-v1\nvalias-v1\nstore-box-v1\nplus-v1\n", False),
    ("", False),
], ids=["lists-the-token", "predates-it", "answers-nothing"])
def test_the_drop_asks_for_no_fqdn_only_of_a_generator_that_lists_it(
        sender, env, tmp_path, bin_dir, mailroot, caps, flagged):
    """One `--capabilities` probe per send; then BOTH print calls carry
    `--no-fqdn` when the token is listed, and neither does otherwise (the
    old argv, byte for byte)."""
    log = tmp_path / "argv.log"
    write_fake(bin_dir / "vmailbox-gen", RECORDING_GEN.format(python=sys.executable))
    rc, _, err = send(sender, {**env, "FAKE_ARGV_LOG": str(log), "FAKE_CAPS": caps},
                      tmp_path, "alpha")
    assert rc == EX_OK, err
    calls = log.read_text().splitlines()
    base = f"--root {mailroot} --host {HOST}"
    tail = " --no-fqdn" if flagged else ""
    assert calls == ["--capabilities", f"{base}{tail} --print", f"{base}{tail} --print-valias"]


# ── sendmail's interface, `python -m sessionmail._drop` ──────────────────────

def test_the_module_speaks_sendmail(env, tmp_path, mailroot):
    msg = b"From: beta\nTo: alpha\nSubject: raw\n\nbody\n"
    run_env = {**env, "PATH": os.environ["PATH"]}
    done = subprocess.run([sys.executable, "-m", "sessionmail._drop", "-t", "-i", "-f", "beta"],
                          input=msg, capture_output=True, env=run_env)
    assert done.returncode == 0, done.stderr
    assert set(landed(mailroot)) == {"alpha", SID}
    bad = subprocess.run([sys.executable, "-m", "sessionmail._drop", "-t", "-i", "-f", "beta"],
                         input=msg.replace(b"To: alpha", b"To: gamma"), capture_output=True,
                         env=run_env)
    assert bad.returncode == 67
    assert b"'gamma'" in bad.stderr


# ── the command line takes the package's sender in drop mode ─────────────────

TRIPWIRE = """#!{python}
import os
open(os.environ["TRIPWIRE_LOG"], "w").write("the oracle was started")
raise SystemExit(99)
"""


def test_message_send_in_drop_mode_never_starts_the_bash_oracle(env, tmp_path, mailroot, bin_dir,
                                                                  monkeypatch):
    """The bash oracle cannot carry the drop, and a postfix-less host may not
    have one: drop mode takes the package's own sender, as --json and SM-402
    custom headers already do - even while a seat still NAMES the oracle in
    $SESSIONMAIL_SEND_ORACLE (the one case, since send-in-python, where the
    bash would otherwise run). Without drop mode the named oracle IS started -
    the tripwire is shown live, so its silence below means something."""
    from click.testing import CliRunner
    from sessionmail.message import cli

    tripped = tmp_path / "tripwire.log"
    tripwire = bin_dir / "session-mail-send"
    write_fake(tripwire, TRIPWIRE.format(python=sys.executable))
    for var in ("CLAUDE_CODE_AGENT", "CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB",
                "SESSIONMAIL_REQUIRE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in {**env, "TRIPWIRE_LOG": str(tripped), "TM_AGENT_ADDRESS": "beta",
                 "SESSIONMAIL_SEND_ORACLE": str(tripwire)}.items():
        monkeypatch.setenv(k, v)
    body = body_file(tmp_path)

    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(bin_dir / "no-mta"))
    CliRunner().invoke(cli, ["send", "alpha", "-s", "via the oracle", "-f", body])
    assert tripped.exists()                                   # the tripwire works
    tripped.unlink()

    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    result = CliRunner().invoke(cli, ["send", "alpha", "-s", "via the drop", "-f", body])
    assert result.exit_code == 0, result.output
    assert not tripped.exists()
    assert "the mail drop (the bash cannot drop)" in result.output
    assert result.stdout.strip().startswith("<") and result.stdout.strip().endswith(".session-mail>")
    assert set(landed(mailroot)) == {"alpha", SID}



# ── with SM-594: a keyed seat in drop mode (the merge-up's two compositions) ─
# `keyed` is test_sender's keyholder; the `env` it asks for resolves to THIS
# module's drop-mode env, so the key goes into a seat that drops.

def test_a_keyed_seat_in_drop_mode_lands_mail_signed_v2_that_verifies(sender, keyed, tmp_path,
                                                                        mailroot):
    """The drop gate (SM-735) and v2 signing (SM-594) each auto-merged into
    `_deliver`, and neither branch's tests ran them together. A keyed seat's drop
    send is signed BEFORE `drop_deliver` writes, so every landed copy carries one
    v2 signature, and the shipped verifier says good on the file as it sits in new/."""
    if not os.access(VERIFIER, os.X_OK):
        pytest.skip(f"{VERIFIER} is missing or not executable")
    keyed["SESSIONMAIL_ORIGIN"] = TEST_ORIGIN
    rc, _, err = send(sender, keyed, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert "signing FAILED" not in err and "UNSIGNED" not in err, err
    boxes = landed(mailroot)
    assert set(boxes) == {"alpha", SID}, boxes
    for files in boxes.values():
        raw = files[0].read_bytes()
        assert raw.count(b"\nX-Session-Signature:") == 1, raw[:600]
        assert _sig.parse_header(_sig.signature_value(raw))["v"] == "2"
        done = subprocess.run([str(VERIFIER), str(files[0])], capture_output=True, text=True,
                              env={**os.environ, "SESSIONMAIL_ORIGIN": TEST_ORIGIN})
        assert done.returncode == 0, done.stdout + done.stderr


def test_a_keyed_seat_in_drop_mode_is_told_the_drop_not_the_key(env, tmp_path, mailroot, bin_dir,
                                                               monkeypatch):
    """With the bash named, a keyed seat in drop mode has two reasons to skip it.
    The notice names ONE, the drop: the bash cannot drop at all, keyed or not.
    The key is enough for `_sender.keyed()` (a socket named, a public key file);
    no ssh-keygen is on this PATH, so the send goes unsigned - this proves the ROUTE."""
    from click.testing import CliRunner
    from sessionmail.message import cli

    tripped = tmp_path / "tripwire.log"
    tripwire = bin_dir / "session-mail-send"
    write_fake(tripwire, TRIPWIRE.format(python=sys.executable))
    pub = tmp_path / "session-key.pub"
    pub.write_text("ssh-ed25519 AAAA test\n")
    for var in ("CLAUDE_CODE_AGENT", "SESSIONMAIL_REQUIRE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    for k, v in {**env, "TRIPWIRE_LOG": str(tripped), "TM_AGENT_ADDRESS": "beta",
                 "SESSIONMAIL_SEND_ORACLE": str(tripwire),
                 "CLAUDE_SESSION_AGENT_SOCK": str(tmp_path / "agent.sock"),
                 "CLAUDE_SESSION_PUB": str(pub)}.items():
        monkeypatch.setenv(k, v)
    result = CliRunner().invoke(cli, ["send", "alpha", "-s", "keyed drop", "-f", body_file(tmp_path)])
    assert result.exit_code == 0, result.output
    assert not tripped.exists()
    ours = [line for line in result.output.splitlines() if "is not consulted" in line]
    assert len(ours) == 1, result.output
    assert "the mail drop (the bash cannot drop)" in ours[0]
    assert "keyed seat" not in ours[0]
    assert set(landed(mailroot)) == {"alpha", SID}


# ── SM-838: a name collision never clobbers, and retries under a fresh name ──
#
# The recipe (findings/rca-001.md, "Tests for fix-001"): pin the drop's clock
# so the FIRST name of each copy is known, and plant a file at it. A retry
# name adds `R<hex>` from `secrets.token_hex`; pinning that too makes every
# attempt collide. Copies are written in plan order: `alpha` is Q0 in alpha's
# box, then Q1 in its session box (SID); a `--cc beta` is Q2 in beta's.

from types import SimpleNamespace  # noqa: E402

from sessionmail import _drop, _maildir  # noqa: E402

T = 1_923_171_200.25
PLANT = b"PLANTED - not ours, never to be touched\n"
STUCK = "deadbeef"       # a pinned token_hex: every retry gets the same name


def first_name(seq: int, pid: int | None = None) -> str:
    return f"{int(T)}.M{int((T % 1) * 1e6)}P{pid or os.getpid()}Q{seq}.{HOST}"


def retry_name(seq: int, pid: int | None = None) -> str:
    return first_name(seq, pid).replace(f"Q{seq}.", f"Q{seq}R{STUCK}.")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(_drop, "time", SimpleNamespace(time=lambda: T))


@pytest.fixture
def stuck(monkeypatch):
    """Every retry name is `…R deadbeef…`, so a plant there blocks them all."""
    calls: list[int] = []
    monkeypatch.setattr(_maildir, "secrets",
                        SimpleNamespace(token_hex=lambda n=None: calls.append(n) or STUCK))
    return calls


def plant(mailroot: Path, box: str, sub: str, name: str) -> Path:
    p = mailroot / "agents" / box / sub / name
    p.write_bytes(PLANT)
    return p


def ours(mailroot: Path) -> dict[str, list[Path]]:
    """`landed`, less the plants: what THIS delivery left in new/ and tmp/."""
    out = {box: [p for p in files if p.read_bytes() != PLANT]
           for box, files in landed(mailroot).items()}
    return {box: files for box, files in out.items() if files}


def test_a_planted_new_file_survives_and_the_copy_lands_under_a_fresh_name(
        sender, env, tmp_path, mailroot, clock):
    """HEADLINE: before SM-838 `os.replace` overwrote the planted file at exit 0."""
    planted = plant(mailroot, "alpha", "new", first_name(0))
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert planted.read_bytes() == PLANT
    got = ours(mailroot)
    assert set(got) == {"alpha", SID}
    [copy] = got["alpha"]
    assert copy.parent.name == "new" and copy.name != planted.name
    assert re.fullmatch(rf"{int(T)}\.M250000P{os.getpid()}Q0R[0-9a-f]{{8}}\.{HOST}", copy.name)
    assert b"Subject: drop test" in copy.read_bytes()
    assert copy.stat().st_mode & 0o777 == 0o600 and copy.stat().st_nlink == 1


def test_a_planted_tmp_file_is_retried_past_and_no_tmp_file_of_ours_is_left(
        sender, env, tmp_path, mailroot, clock):
    planted = plant(mailroot, "alpha", "tmp", first_name(0))
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_OK, err
    assert planted.read_bytes() == PLANT
    assert list((mailroot / "agents" / "alpha" / "tmp").iterdir()) == [planted]
    [copy] = ours(mailroot)["alpha"]
    assert copy.parent.name == "new"


def test_a_collision_on_the_second_copy_retries_and_every_box_has_one_copy(
        sender, env, tmp_path, mailroot, clock):
    """After a partial write, no duplicate: the retry is in
    process, so the copy already written is never written again."""
    planted = plant(mailroot, SID, "new", first_name(1))
    rc, _, err = send(sender, env, tmp_path, "alpha", "--cc", "beta")
    assert rc == EX_OK, err
    assert planted.read_bytes() == PLANT
    got = ours(mailroot)
    assert {box: len(files) for box, files in got.items()} == {"alpha": 1, SID: 1, "beta": 1}
    assert all(p.parent.name == "new" for files in got.values() for p in files)


#: `python -m sessionmail._drop` with its clock, pid and token pinned - the
#: real module entry point, so the exit status is the one postfix would read.
PINNED_MAIN = """
import os, runpy, secrets, sys, time
now, pid, token = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
time.time = lambda: now
os.getpid = lambda: pid
secrets.token_hex = lambda n=None: token
sys.argv = ["sessionmail._drop", *sys.argv[4:]]
runpy.run_module("sessionmail._drop", run_name="__main__")
"""
PID = 4242


def pinned_main(env, msg: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", PINNED_MAIN, repr(T), str(PID), STUCK,
                           "-t", "-i", "-f", "beta"], input=msg, capture_output=True,
                          env={**env, "PATH": os.environ["PATH"]})


def test_a_persistent_collision_before_any_write_exits_75_and_writes_nothing(env, mailroot):
    """EX_TEMPFAIL: under pipe(8) 75 defers and retries; 73 would bounce."""
    plants = [plant(mailroot, "alpha", "new", first_name(0, PID)),
              plant(mailroot, "alpha", "new", retry_name(0, PID))]
    done = pinned_main(env, b"From: beta\nTo: alpha\nSubject: raw\n\nbody\n")
    assert done.returncode == _drop.EX_TEMPFAIL == 75, done.stderr
    assert ours(mailroot) == {}
    assert all(p.read_bytes() == PLANT for p in plants)
    err = done.stderr.decode()
    assert "NOTHING WAS DELIVERED" in err and "ALREADY DELIVERED" not in err
    assert f"NOT ATTEMPTED: {mailroot / 'agents' / SID}" in err


def test_a_persistent_collision_before_any_write_is_sent_no_in_message_send(
        sender, env, tmp_path, mailroot, clock, stuck):
    plant(mailroot, "alpha", "new", first_name(0))
    plant(mailroot, "alpha", "new", retry_name(0))
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_MTA and "NOTHING WAS SENT" in err and "ALREADY DELIVERED" not in err
    with pytest.raises(sender.Refused) as refused:
        sender.send_request(sender.parse_argv(["alpha", "--from", "beta", "--subject", "s",
                                               "--body-file", body_file(tmp_path)]), env)
    assert (refused.value.kind, refused.value.sent) == ("mta", "no")
    assert ours(mailroot) == {}


def test_a_persistent_collision_after_a_partial_write_is_73_never_75(env, mailroot):
    """75 here would make pipe(8) re-run the whole message: alpha twice. The
    report names what landed, what failed and what was never attempted."""
    plant(mailroot, SID, "new", first_name(1, PID))
    plant(mailroot, SID, "new", retry_name(1, PID))
    done = pinned_main(env, b"From: beta\nTo: alpha\nCc: beta\nSubject: raw\n\nbody\n")
    assert done.returncode == _drop.EX_CANTCREAT == 73, done.stderr
    got = ours(mailroot)
    assert {box: len(files) for box, files in got.items()} == {"alpha": 1}
    err = done.stderr.decode()
    assert f"could not write into {mailroot / 'agents' / SID}" in err
    assert f"ALREADY DELIVERED to {got['alpha'][0]}" in err
    assert f"NOT ATTEMPTED: {mailroot / 'agents' / 'beta'}" in err


def test_a_partial_write_names_all_three_in_message_send_and_is_sent_unknown(
        sender, env, tmp_path, mailroot, clock, stuck):
    plant(mailroot, SID, "new", first_name(1))
    plant(mailroot, SID, "new", retry_name(1))
    rc, _, err = send(sender, env, tmp_path, "alpha", "--cc", "beta")
    assert rc == EX_MTA, err
    [alpha] = ours(mailroot)["alpha"]
    assert f"could not write into {mailroot / 'agents' / SID}" in err
    assert err.count("ALREADY DELIVERED") == 1 and f"ALREADY DELIVERED to {alpha}" in err
    assert f"NOT ATTEMPTED: {mailroot / 'agents' / 'beta'}" in err
    assert "NOTHING WAS SENT" not in err
    with pytest.raises(sender.Refused) as refused:
        sender.send_request(sender.parse_argv(["alpha", "--cc", "beta", "--from", "beta",
                                               "--subject", "s", "--body-file",
                                               body_file(tmp_path)]), env)
    assert (refused.value.kind, refused.value.sent) == ("mta", "unknown")


def test_a_collision_is_tried_three_times_in_all(sender, env, tmp_path, mailroot, clock, stuck,
                                                 monkeypatch):
    plant(mailroot, "alpha", "new", first_name(0))
    plant(mailroot, "alpha", "new", retry_name(0))
    links: list[str] = []
    real_link = os.link

    def counting_link(src, dst):
        links.append(Path(dst).name)
        return real_link(src, dst)

    monkeypatch.setattr(_maildir.os, "link", counting_link)
    rc, _, err = send(sender, env, tmp_path, "alpha")
    assert rc == EX_MTA, err
    assert _maildir.PUBLISH_ATTEMPTS == 3
    assert links == [first_name(0), retry_name(0), retry_name(0)]
    assert len(stuck) == 2                     # a fresh field for each retry, none for the first


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a 0500 directory")
def test_a_tmp_that_cannot_be_written_still_exits_73(env, mailroot):
    """Not a collision, so not transient and not retried: today's code."""
    tmp = mailroot / "agents" / "alpha" / "tmp"
    tmp.chmod(0o500)
    try:
        done = pinned_main(env, b"From: beta\nTo: alpha\nSubject: raw\n\nbody\n")
    finally:
        tmp.chmod(0o700)
    assert done.returncode == _drop.EX_CANTCREAT == 73, done.stderr
    assert b"Permission denied" in done.stderr and ours(mailroot) == {}


# ── The drop qualifies a bare From, as postfix's myorigin does ──────────────
# Postfix appends myorigin to a bare From in transit; the drop had no transit,
# so a heard turn landed as `From: Ears <heard+airpods>` and `keeper_sent`
# (which needs @host) failed closed. The sender now qualifies a bare From on
# the drop branch, BEFORE build and signing; the Return-Path follows.

from email import message_from_bytes  # noqa: E402

from sessionmail import _drop, _voice  # noqa: E402

SUFFIX = "sm847.invalid"

#: A heard turn as the drop wrote it before the fix, verbatim (a synthetic
#: host and date). The control: keeper_sent is False.
R1_BYTES = (b"Return-Path: <heard+airpods>\nX-Original-To: probe\nDelivered-To: probe@vps\n"
            b"Received: by vps (sessionmail mail drop); Tue, 01 Jan 2030 12:00:00 +0000\n"
            b"From: Ears <heard+airpods>\nTo: probe\nDate: Tue, 01 Jan 2030 12:00:00 +0000\n"
            b"Subject: R1 probe turn\nMessage-ID: <1902000000.11111.11111@vps.session-mail>\n"
            b"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
            b"Content-Transfer-Encoding: 8bit\nEars-Kind: spoken\nX-Machine: vps\n\n"
            b"a heard turn\n")


def heard(sender, env, tmp_path, frm="Ears <heard+airpods>"):
    """A heard turn, as sessionvoice sends one, to `alpha`; the landed copy's bytes."""
    rc, out, err = sender.run(["alpha", "--from", frm, "--subject", "R1 probe turn",
                               "--body-file", body_file(tmp_path, b"a heard turn\n")], env)
    assert rc == EX_OK, err
    files = landed(env_root(env))["alpha"]
    assert len(files) == 1
    return files[0].read_bytes(), err


def env_root(env) -> Path:
    return Path(env["SESSIONMAIL_ROOT"]).parent


def head_field(raw: bytes, name: str) -> list[str]:
    return [line.decode() for line in raw.split(b"\n\n", 1)[0].split(b"\n")
            if line.lower().startswith(name.lower().encode() + b":")]


@pytest.mark.parametrize("value, domain, want", [
    ("Ears <heard+airpods>", "hq.x.ts.net", "Ears <heard+airpods@hq.x.ts.net>"),
    ("heard+airpods", "hq", "heard+airpods@hq"),
    ("  beta ", "hq", "beta@hq"),
    ("Ears <heard+airpods@vps>", "hq", "Ears <heard+airpods@vps>"),          # already qualified
    ("heard@vps", "hq", "heard@vps"),
    ("=?utf-8?q?Ear=C3=A9s?= <heard>", "hq", "=?utf-8?q?Ear=C3=A9s?= <heard@hq>"),  # phrase kept
    ('"a@b <c>" <heard>', "hq", '"a@b <c>" <heard@hq>'),                   # innermost <...>
    ("Ears < heard >", "hq", "Ears <heard@hq>"),
    ("Ears <heard>", "", "Ears <heard>"),                                   # no domain: as given
    ("Ears <>", "hq", "Ears <>"),                                           # no address: as given
])
def test_qualify_bare_appends_the_domain_to_a_bare_address_only(sender, value, domain, want):
    assert sender.qualify_bare(value, domain) == want
    assert sender.addr_spec(sender.qualify_bare(value, domain)) == sender.addr_spec(want)


def test_r1_control_the_bare_bytes_are_not_the_keeper(env):
    """The control, watched failing: the pre-fix bytes fail the keeper test
    under the host they came from, and the same bytes at the FQDN pass."""
    vps = {**env, "SESSIONMAIL_HOST": "vps", "SESSION_MAIL_HOST": "vps", "MAGICDNS_SUFFIX": SUFFIX}
    assert _voice.keeper_sent(message_from_bytes(R1_BYTES), vps) is False
    fixed = R1_BYTES.replace(b"From: Ears <heard+airpods>\n",
                             f"From: Ears <heard+airpods@vps.{SUFFIX}>\n".encode())
    assert _voice.keeper_sent(message_from_bytes(fixed), vps) is True


def test_a_heard_turn_under_the_drop_is_the_keeper_at_the_fqdn(sender, env, tmp_path):
    """THE REGRESSION: with a MagicDNS suffix, the drop writes From and
    Return-Path at `<short>.<suffix>` - what postfix writes when myorigin is
    `$myhostname` - and the landed copy passes `keeper_sent`."""
    env = {**env, "MAGICDNS_SUFFIX": SUFFIX}
    raw, err = heard(sender, env, tmp_path)
    fqdn = f"{HOST}.{SUFFIX}"
    assert head_field(raw, "From") == [f"From: Ears <heard+airpods@{fqdn}>"]
    assert raw.startswith(f"Return-Path: <heard+airpods@{fqdn}>\n".encode())
    assert _voice.keeper_sent(message_from_bytes(raw), env) is True
    assert head_field(raw, "To") == ["To: alpha"]                 # To/Cc stay bare (out of scope)
    assert f"Delivered-To: alpha@{HOST}\n".encode() in raw       # trace keeps the short host
    assert "suffix unknown" not in err.lower()


def test_without_the_fix_the_same_send_is_not_the_keeper(sender, env, tmp_path, mailroot,
                                                         monkeypatch):
    """The same send with the qualification taken out is the pre-fix shape:
    bare, and keeper_sent False. So the test above is what moved."""
    env = {**env, "MAGICDNS_SUFFIX": SUFFIX}
    monkeypatch.setattr(sender, "qualify_bare", lambda value, domain: value)
    raw, _ = heard(sender, env, tmp_path)
    assert head_field(raw, "From") == ["From: Ears <heard+airpods>"]
    assert raw.startswith(b"Return-Path: <heard+airpods>\n")
    assert _voice.keeper_sent(message_from_bytes(raw), env) is False


def test_with_no_suffix_the_drop_qualifies_with_the_short_host_and_says_so(sender, env, tmp_path):
    """No MAGICDNS_SUFFIX and no tailscale (both seams pointed at nothing): the
    short host, which is host_forms' one form there, so keeper_sent holds."""
    env = {**env, "MAGICDNS_TAILSCALE_BUNDLE": ""}
    env.pop("MAGICDNS_SUFFIX", None)
    raw, err = heard(sender, env, tmp_path)
    assert head_field(raw, "From") == [f"From: Ears <heard+airpods@{HOST}>"]
    assert raw.startswith(f"Return-Path: <heard+airpods@{HOST}>\n".encode())
    assert _voice.keeper_sent(message_from_bytes(raw), env) is True
    assert "MagicDNS suffix unknown" in err and f"short host only (@{HOST})" in err


def test_an_already_qualified_from_is_left_as_given(sender, env, tmp_path):
    env = {**env, "MAGICDNS_SUFFIX": SUFFIX}
    raw, _ = heard(sender, env, tmp_path, frm="Ears <heard+airpods@elsewhere.invalid>")
    assert head_field(raw, "From") == ["From: Ears <heard+airpods@elsewhere.invalid>"]
    assert raw.startswith(b"Return-Path: <heard+airpods@elsewhere.invalid>\n")


def test_sessionmail_origin_is_the_drops_domain(sender, env, tmp_path):
    """When SESSIONMAIL_ORIGIN is set, it IS the drop's domain, over the
    suffix: it is the signer's origin, so the signer and the drop agree."""
    env = {**env, "MAGICDNS_SUFFIX": SUFFIX, "SESSIONMAIL_ORIGIN": " x.origin.invalid "}
    assert _drop.origin(env, HOST) == "x.origin.invalid"
    raw, _ = heard(sender, env, tmp_path)
    assert head_field(raw, "From") == ["From: Ears <heard+airpods@x.origin.invalid>"]
    assert raw.startswith(b"Return-Path: <heard+airpods@x.origin.invalid>\n")


def test_origin_resolution_order(env):
    base = {k: v for k, v in env.items() if k != "SESSIONMAIL_ORIGIN"}
    base["MAGICDNS_TAILSCALE_BUNDLE"] = ""
    assert _drop.origin({**base, "MAGICDNS_SUFFIX": SUFFIX}, HOST) == f"{HOST}.{SUFFIX}"
    assert _drop.origin({**base, "MAGICDNS_SUFFIX": SUFFIX, "SESSIONMAIL_ORIGIN": "o.invalid"},
                        HOST) == "o.invalid"
    assert _drop.origin({**base, "MAGICDNS_SUFFIX": SUFFIX, "SESSIONMAIL_ORIGIN": "  "},
                        HOST) == f"{HOST}.{SUFFIX}"                 # blank is unset
    notes: list[str] = []
    assert _drop.origin(base, HOST, notes) == HOST
    assert len(notes) == 1 and _magicdns_unknown() in notes[0]


def _magicdns_unknown() -> str:
    from sessionmail import _magicdns
    return _magicdns.UNKNOWN


@pytest.mark.parametrize("origin", [TEST_ORIGIN, "x.origin.invalid"])
def test_a_keyed_drop_send_qualified_by_origin_still_verifies_v2(sender, keyed, tmp_path,
                                                                 mailroot, origin):
    """SESSIONMAIL_ORIGIN=x qualifies From and Return-Path
    with @x, and the v2 signature the sender made over the QUALIFIED From
    verifies, with the shipped verifier, on the copy as it sits in new/."""
    if not os.access(VERIFIER, os.X_OK):
        pytest.skip(f"{VERIFIER} is missing or not executable")
    keyed["SESSIONMAIL_ORIGIN"] = origin
    raw, err = heard(sender, keyed, tmp_path, frm="beta")
    assert "signing FAILED" not in err and "UNSIGNED" not in err, err
    assert head_field(raw, "From") == [f"From: beta@{origin}"]
    assert raw.startswith(f"Return-Path: <beta@{origin}>\n".encode())
    assert _sig.parse_header(_sig.signature_value(raw))["v"] == "2"
    # The reader shares the signer's origin: the bare `To: alpha` still needs
    # that (pre-existing: the drop does not qualify To or Cc).
    done = subprocess.run([str(VERIFIER), str(landed(mailroot)["alpha"][0])],
                          capture_output=True, text=True,
                          env={**os.environ, "SESSIONMAIL_ORIGIN": origin})
    assert done.returncode == 0, done.stdout + done.stderr


def test_v1_over_drop_delivered_bytes_still_verifies_and_signs_the_same_text(sender, keyed,
                                                                             tmp_path, mailroot):
    """v1 never signs the domain (From and To reduce to the local part), so the
    qualified From gives the v1 text the bare one did, and a v1 signature over
    a drop-delivered copy verifies with the shipped verifier."""
    if not os.access(VERIFIER, os.X_OK):
        pytest.skip(f"{VERIFIER} is missing or not executable")
    from sessionmail import _root
    keyed["MAGICDNS_SUFFIX"] = SUFFIX
    keyed.pop("SESSIONMAIL_ORIGIN", None)
    raw, _ = heard(sender, keyed, tmp_path)
    assert head_field(raw, "From") == [f"From: Ears <heard+airpods@{HOST}.{SUFFIX}>"]
    head, body = raw.split(b"\n\n", 1)
    head = b"\n".join(line for line in head.split(b"\n")
                      if not line.startswith(b"X-Session-Signature:"))   # v2 off; v1 alone
    text = _root.canonical((head + b"\n\n" + body).decode())
    bare = _root.canonical((head + b"\n\n" + body).decode().replace(
        f"heard+airpods@{HOST}.{SUFFIX}>", "heard+airpods>", 1))
    assert text == bare and text.startswith(b"from: heard+airpods\nto: alpha\n")
    b64 = sender.sign_bytes(text, keyed, sender.sig_ns(keyed))
    assert b64
    v1 = tmp_path / "v1.eml"
    v1.write_bytes(head + b"\n" + _sig.format_v1(sender.sig_ns(keyed), b64).encode()
                   + b"\n\n" + body)
    done = subprocess.run([str(VERIFIER), str(v1)], capture_output=True, text=True,
                          env={k: v for k, v in os.environ.items() if k != "SESSIONMAIL_ORIGIN"})
    assert done.returncode == 0, done.stdout + done.stderr


ARGV_SENDMAIL = """#!{python}
import os, sys
open(os.environ["SENDMAIL_LOG"], "ab").write(sys.stdin.buffer.read())
open(os.environ["SENDMAIL_ARGV"], "w").write(" ".join(sys.argv[1:]))
"""


def test_the_postfix_path_never_qualifies_and_its_bytes_are_unchanged(sender, env, tmp_path,
                                                                      mailroot, monkeypatch):
    """With an MTA as the carrier the send goes by the MTA with From and
    `-f` bare, as before - postfix qualifies in transit. The drop's code is
    never reached: both of its entry points raise if called."""
    def never(*_a, **_k):
        raise AssertionError("the postfix path reached the drop's qualification")
    monkeypatch.setattr(sender, "qualify_bare", never)
    monkeypatch.setattr(_drop, "origin", never)
    log, argv = tmp_path / "mta.eml", tmp_path / "mta-argv.txt"
    fake = tmp_path / "default-sendmail"
    write_fake(fake, ARGV_SENDMAIL.format(python=sys.executable))
    default = {**env, "SESSIONMAIL_SENDMAIL": str(fake)}
    default.update(SENDMAIL_LOG=str(log), SENDMAIL_ARGV=str(argv), MAGICDNS_SUFFIX=SUFFIX)
    rc, _, err = sender.run(["alpha", "--from", "Ears <heard+airpods>", "--subject", "R1 probe turn",
                             "--body-file", body_file(tmp_path, b"a heard turn\n")], default)
    assert rc == EX_OK, err
    wire = log.read_bytes()
    assert head_field(wire, "From") == ["From: Ears <heard+airpods>"]
    assert b"Return-Path" not in wire
    assert argv.read_text() == "-t -i -f heard+airpods"
    assert landed(mailroot) == {}
