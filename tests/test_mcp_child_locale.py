"""SM-926: a tool's reply does not depend on the LOCALE its child runs under.

In-process, `CliRunner` wrote a verb's stdout and stderr as UTF-8 whatever
the locale (`click.testing`, `charset="utf-8"`; stderr with
`errors="backslashreplace"`). A child left alone writes in its locale's
encoding, and the server decodes what it reads as UTF-8. Under a latin-1
locale, a subject holding an em dash raised `UnicodeEncodeError` in the
verb (`exit 1: Traceback`), and a latin-1 letter came back as U+FFFD
(review r3, 905d15c5, measured at cb4985e). The subject is the SENDER's to
choose. So `_mcp.CHILD_BOOT` pins both streams to UTF-8, with CliRunner's
errors modes, beside its width pin.

Each reply here is compared with CliRunner's own, the in-process door the
tool replaced, under three locales: one that is not UTF-8, one that is,
and C (which Python coerces to UTF-8). A host that has no latin-1 locale
SKIPS that case and says so; it never passes it unmeasured.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from sessionmail import _mcp, message
from tool_call import call

SID = "5a1d0000-0926-4000-8000-0000000000ac"
#: Sender-chosen words: a latin-1 letter, and two that latin-1 has no byte for.
SUBJECT_WORDS = "naïve — dash ✓"
#: In RFC 2047, as a sender's client writes it.
SUBJECT = "=?utf-8?q?na=C3=AFve_=E2=80=94_dash_=E2=9C=93?="

#: The names a non-UTF-8 latin-1 locale goes by (macOS/BSD, glibc spellings).
LATIN1 = ("en_US.ISO8859-1", "en_US.iso88591", "en_US.ISO-8859-1", "de_DE.ISO8859-1")
UTF8 = ("en_US.UTF-8", "C.UTF-8")


def _child_encoding(lc_all: str) -> str:
    """What a bare python child's stdout encodes as under LC_ALL=LC_ALL."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["LC_ALL"] = lc_all
    done = subprocess.run([sys.executable, "-P", "-c", "import sys; print(sys.stdout.encoding)"],
                          env=env, capture_output=True, text=True)
    return codecs.lookup(done.stdout.strip() or "ascii").name


def _locale(kind: str) -> str:
    """An LC_ALL value of KIND this host really has, or a skip naming why."""
    if kind == "C":
        return "C"
    names, want = (LATIN1, "iso8859-1") if kind == "latin-1" else (UTF8, "utf-8")
    for name in names:
        if _child_encoding(name) == want:
            return name
    pytest.skip(f"no {kind} locale on this host (tried {', '.join(names)}): "
                f"a child under none of them encodes its stdout as {want}")


@pytest.fixture(params=["latin-1", "utf-8", "C"])
def lc_all(request, monkeypatch):
    name = _locale(request.param)
    monkeypatch.setenv("LC_ALL", name)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)   # the runner's would mask the locale
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    return name


@pytest.fixture
def box(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / SID / sub).mkdir(parents=True)
    (root / SID / "new" / "1907238896.M1P926Q0.testhost").write_bytes(
        (f"From: alpha@testhost\nTo: {SID}@testhost\nSubject: {SUBJECT}\n"
         "Date: Sun, 09 Jun 2030 12:34:50 +0000\nMessage-ID: <1.0926@testhost.session-mail>\n\n"
         "body\n").encode())
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    return root


def _tool(name: str):
    return {n: fn for n, fn, _ in _mcp.tools(message.cli, "message")}[name]


def _cli(*argv: str):
    return CliRunner().invoke(message.cli, list(argv), prog_name="message")


def test_a_listing_of_a_senders_words_is_the_in_process_reply_under_any_locale(box, lc_all):
    """stdout: the row comes back, byte for byte the CliRunner row, not a traceback."""
    old = _cli("list", SID)
    assert old.exit_code == 0 and SUBJECT_WORDS in old.stdout, old.output
    assert call(_tool("message_list"), box=SID) == old.stdout


def test_a_refusal_naming_a_senders_words_is_the_in_process_reply_under_any_locale(box, lc_all):
    """stderr: the refusal names the box as typed, not U+FFFD or a `\\u2014` escape."""
    name = f"no-such-{SUBJECT_WORDS.replace(' ', '-')}"
    old = _cli("box", "path", name)
    assert old.exit_code == 2 and name in old.stderr, old.output
    with pytest.raises(Exception) as caught:
        call(_tool("message_box_path"), box_=name)
    detail = "\n".join(s for s in (old.stderr.strip(), old.stdout.strip()) if s)
    assert str(caught.value) == f"exit 2: {detail}"


@pytest.mark.parametrize("kind", ["latin-1", "utf-8", "C"])
def test_the_childs_streams_are_cli_runners_utf_8_with_its_errors_modes(kind, tmp_path, monkeypatch):
    """stdout strict, stderr backslashreplace, both UTF-8: CliRunner's three, as
    the boot under test sets them (a bare `reconfigure(encoding=...)` would
    have reset stderr to strict)."""
    monkeypatch.setenv("LC_ALL", _locale(kind))
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    (tmp_path / "sm926_streams_probe.py").write_text(
        "import codecs, sys\n"
        "print(*(f'{codecs.lookup(s.encoding).name}/{s.errors}' for s in (sys.stdout, sys.stderr)))\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    done = subprocess.run([sys.executable, "-P", "-c", _mcp.CHILD_BOOT, "sm926_streams_probe"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["utf-8/strict", "utf-8/backslashreplace"]
