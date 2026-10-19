"""SM-398: the MHS readers reduce a NAMED From the way the verifier does.

sessionmail's `message send --json` (the MCP tool's road) now writes
`From: Ivan <ivan@hq>`. Two readers here split the raw header and read the
NAME: mhs-thread's short_from() got "Ivan" (so a named memory item was judged
NOT the owner's and hidden, MHS 170), and session-mail-run got "ivan <ivan"
(so a named WAKE item read as unverified and never ran). Both now take the
innermost <...> first, as session-mail-verify's local_part() does; a bare
From reduces exactly as before. (Since 2030-09-30 session-mail-run has no
reducer of its own: it verifies with mhs-thread's Members.)

Run: uv run pytest -q tests/mhs/test_named_from_readers.py
"""
import importlib.machinery
import importlib.util
import os

import pytest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "sessionmail", "mhs")  # the bundled oracle


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), os.path.join(HERE, name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def load_verifier_local_part():
    """session-mail-verify runs at import (no __main__ guard), so its
    local_part() is lifted from the source text, not imported."""
    src = open(os.path.join(HERE, "session-mail-verify")).read()
    start = src.index("def local_part(")
    end = src.index("\ndef ", start + 1)
    ns = {}
    exec(src[start:end], ns)
    return ns["local_part"]


FORMS = [
    ("ivan", "ivan"),
    ("ivan@hq.example.ts.net", "ivan"),
    ("Ivan <ivan@hq.example.ts.net>", "ivan"),
    ("Ivan 7 <ivan@hq>", "ivan"),
    ('"Ivan, 7" <ivan@hq>', "ivan"),
    ("Eve <eve+repro-oracle@hq>", "eve+repro-oracle"),
    ("<ivan@hq>", "ivan"),
]


@pytest.mark.parametrize("frm, want", FORMS)
def test_the_owner_test_reduces_like_the_verifier(frm, want):
    """session-mail-run no longer reduces From itself: since the member walk
    (2030-09-30) it verifies with mhs-thread's Members, the one owner test
    every reader uses. So the parity pinned here is Members' owner check
    against the verifier's local_part() — case-blind, as that one is."""
    mhs = load("mhs-thread")
    assert load_verifier_local_part()(frm) == want
    msg = {"headers": {"From": frm, "To": "%s@host1" % want}}
    assert mhs.Members(want).headers_ok(msg)
    assert mhs.Members(want.upper()).headers_ok(msg)
    assert not mhs.Members("someone-else").headers_ok(msg)


def test_session_mail_run_verifies_with_the_readers_owner_test():
    src = open(os.path.join(HERE, "session-mail-run")).read()
    assert "pt.Members(AGENT)" in src
    assert "def from_local(" not in src


@pytest.mark.parametrize("frm, want", FORMS)
def test_mhs_thread_short_from_reads_the_address_not_the_name(frm, want):
    mhs = load("mhs-thread")
    assert mhs.short_from({"From": frm}).lower() == want


def test_a_bare_from_is_unchanged_in_mhs_thread_case_and_all():
    mhs = load("mhs-thread")
    for frm in ("ivan", "ivan@hq", "Ivan@hq", "eve+x@hq"):
        assert mhs.short_from({"From": frm}) == frm.split("@")[0]
    assert mhs.short_from({}) == "?"
