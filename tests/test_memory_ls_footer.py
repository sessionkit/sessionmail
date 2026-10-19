"""SM-779: the `ls /` footer never says a bare "signed", and an unsigned label's
⊘ reason says "unsigned".

What was wrong (repro-001, rca-001, @ 4b57c95): the footer said `all reader,
signed` (`all to alpha, signed` for a box) whenever no label failed
membership. Membership's signature half is plain-mode verify, which checks a
mail against the key the mail CARRIES, so a label From the owner signed by
ANOTHER key passed it and was listed under "signed". The footer now says what
was checked: `all members (reader), signatures verify (signer not pinned)`.

Which labels are SHOWN is trust policy (SM-530, SM-762) and is NOT changed
here: the forged-key label is still shown, and these tests pin that too, so
a change to it is a deliberate one.

Second, `Members.why` folded the verifier's rc 2 (unsigned) into
"signature", so an unsigned label's ⊘ line read "signature does not verify".
It now reads "unsigned"; a label whose signature is present and fails still
reads "signature does not verify". The verdicts (member / not / unknown) are
unchanged.

Real SSHSIG signatures (the `_sig.py` helpers, as test_memory_run_trust.py),
the real bundled verifier, a notmuch stub serving the thread. Each test runs
with $SESSION_MAIL_VERIFY unset (the in-process port decides v1 mails and
unsigned ones) and set to the bundled verifier (the port is off: the
subprocess decides every mail).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

from sessionmail import _mhs
from test_memory_run_trust import AGENT, HOST, NOTMUCH, NS, Box, _sigmod, needs_ssh

MHS = Path(_mhs.MHS_DIR)
READER = MHS / "mhs-thread"
VERIFY = MHS / "session-mail-verify"
BOXNAME = "alpha"

pytestmark = needs_ssh


class Root(Box):
    """A temp HOME with a [ROOT] whose children are LABELS, To TO+memory."""

    def __init__(self, root: Path, to: str = AGENT):
        super().__init__(root)
        self.to = to
        self.labels: list[list] = []

    def mail(self, local: str, subject: str, key: str | None, v: int = 2,
             frm: str = AGENT, tamper: bool = False) -> list:
        """One mail To <to>+memory; KEY signs it for real. TAMPER alters the
        body AFTER signing, so the signature is present and fails."""
        fn = self.T / "mail" / local
        to = f"{self.to}+memory@{HOST}"
        frm_h = f"{frm} <{frm}@{HOST}>"
        date = "Sun, 08 Dec 2030 08:00:%02d +1100" % len(self.order)
        mid = f"<{local}@{HOST}>"
        body = subject + "\n"
        head = [f"From: {frm_h}", f"To: {to}", f"Date: {date}", f"Subject: {subject}",
                f"Message-ID: {mid}", "MIME-Version: 1.0",
                "Content-Type: text/plain; charset=utf-8", "Content-Transfer-Encoding: 8bit"]
        if key:
            S = _sigmod()
            wire = ("\n".join(head) + "\n\n" + body).encode()
            if v == 2:
                h, p = S.h_for(wire), S.digests(wire)
                head.append(S.format_header(NS, h, p, self._sign(key, S.canonical(wire, NS, h, p, ""))))
            else:
                canon = (f"from: {frm}\nto: {self.to}+memory\nsubject: {subject}\ndate: {date}\n"
                         f"message-id: {mid}\n\n{body}").rstrip("\n") + "\n"
                head.append(S.format_v1(NS, self._sign(key, canon.encode())))
        if tamper:
            body = subject + " (altered after signing)\n"
        fn.write_text("\n".join(head) + "\n\n" + body)
        m = {"id": mid.strip("<>"), "timestamp": 2000 + len(self.order), "filename": [str(fn)],
             "headers": {"Subject": subject, "From": frm_h, "To": to, "Date": date},
             "body": [{"id": 1, "content-type": "text/plain", "content": body}]}
        self.order.append(m)
        return [m, []]

    def label(self, *a, **kw) -> dict:
        node = self.mail(*a, **kw)
        self.labels.append(node)
        return node[0]

    def seal(self) -> None:
        root = self.mail("root", "[ROOT]", "owner")
        for i, m in enumerate([root[0]] + [n[0] for n in self.labels]):
            m["timestamp"] = 2000 + i
        root[1] = self.labels
        (self.T / "thread.json").write_text(json.dumps([[root]]))
        (self.T / "root.json").write_text(json.dumps([[[root[0], []]]]))
        (self.T / "bin" / "notmuch").write_text(NOTMUCH.format(T=self.T))

    def run(self, verb: list[str], verifier: str | None, box: bool = False,
            agent: bool = True) -> subprocess.CompletedProcess:
        """mhs-thread VERB [--box alpha]. AGENT False is SM-186 verify-002's
        env shape: NO CLAUDE_CODE_AGENT and NO SESSION_AGENT (both removed,
        not set empty), so a --box render has no owner."""
        e = {k: v for k, v in os.environ.items() if not k.endswith("_ROOT")}
        e.update(PATH=f"{self.T / 'bin'}{os.pathsep}{os.environ['PATH']}", HOME=str(self.T),
                 CLAUDE_CODE_AGENT=AGENT, SESSION_AGENT="", SESSION_MAIL_VERIFY_TIMEOUT="10")
        if not agent:
            e.pop("CLAUDE_CODE_AGENT")
            e.pop("SESSION_AGENT")
        e[f"{(BOXNAME if box else AGENT).upper()}_ROOT"] = f"root@{HOST}"
        e.pop("SESSION_MAIL_VERIFY", None)
        e.pop("SESSION_REGISTRY", None)
        if verifier:
            e["SESSION_MAIL_VERIFY"] = verifier
        argv = [sys.executable, str(READER), *verb] + (["--box", BOXNAME] if box else [])
        return subprocess.run(argv, capture_output=True, text=True, env=e, timeout=120)

    def ls(self, verifier: str | None, box: bool = False,
           agent: bool = True) -> subprocess.CompletedProcess:
        return self.run(["--ls"], verifier, box=box, agent=agent)


VERIFIERS = pytest.mark.parametrize("verifier", [None, str(VERIFY)], ids=["port", "subprocess"])


def footer(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.startswith("— ")]
    assert len(lines) == 1, out
    return lines[0]


def nonmember(out: str, label: str) -> str:
    lines = [ln for ln in out.splitlines() if f"⊘ NOT A MEMBER: {label}" in ln]
    assert len(lines) == 1, out
    return lines[0]


# ── (1) the footer: no bare "signed", over a forged-key label ─────────────

@VERIFIERS
@pytest.mark.parametrize("box", [False, True], ids=["owner", "box"])
def test_a_forged_key_label_never_draws_a_signed_footer(tmp_path, verifier, box):
    """repro-001 forged-only: [FORGED] is From the owner, signed by ANOTHER
    key (v2 and v1). It passes plain-mode membership, so the footer is the
    all-members branch: that branch must say the signer was not checked."""
    r = Root(tmp_path, to=BOXNAME if box else AGENT)
    r.label("rules", "[RULES]", "owner")
    r.label("boot", "[BOOT]", "owner")
    r.label("forged", "[FORGED]", "forger")
    r.label("forgedv1", "[FORGEDV1]", "forger", v=1)
    r.seal()
    p = r.ls(verifier, box=box)
    assert p.returncode == 0, p.stdout + p.stderr
    who = f"to {BOXNAME}" if box else AGENT
    f = footer(p.stdout)
    assert re.fullmatch(rf"— 4 labels · all members \({who}\), signatures verify "
                        r"\(signer not pinned\) · ≈\d+ tok", f), f
    assert not re.search(r"\bsigned\b", f), f"a bare 'signed' in the footer: {f}"
    # Which labels show is SM-530's, not this fix's: the forged ones still do.
    for lab in ("[RULES]", "[BOOT]", "[FORGED]", "[FORGEDV1]"):
        assert re.search(rf"^ *└ {re.escape(lab)}(\s|$)", p.stdout, re.M), p.stdout
    assert "⊘" not in p.stdout, p.stdout


@VERIFIERS
def test_all_owner_signed_labels_get_the_same_honest_footer(tmp_path, verifier):
    """Even when every label IS the owner's, the footer cannot know that
    (no signer is pinned), so it says the same thing: no bare 'signed'."""
    r = Root(tmp_path)
    r.label("rules", "[RULES]", "owner")
    r.label("boot", "[BOOT]", "owner", v=1)
    r.seal()
    p = r.ls(verifier)
    assert p.returncode == 0, p.stdout + p.stderr
    f = footer(p.stdout)
    assert " · all members (reader), signatures verify (signer not pinned) · " in f, f
    assert not re.search(r"\bsigned\b", f), f


# ── (2) the ⊘ reason: unsigned is not a bad signature ─────────────────────

@VERIFIERS
@pytest.mark.parametrize("box", [False, True], ids=["owner", "box"])
def test_an_unsigned_label_reads_unsigned_and_the_not_members_footer_stays(tmp_path, verifier, box):
    r = Root(tmp_path, to=BOXNAME if box else AGENT)
    r.label("rules", "[RULES]", "owner")
    r.label("unsigned", "[UNSIGNED]", None)
    r.seal()
    p = r.ls(verifier, box=box)
    assert p.returncode == 0, p.stdout + p.stderr
    line = nonmember(p.stdout, "[UNSIGNED]")
    assert "— unsigned (no X-Session-Signature); subtree hidden" in line, line
    assert "signature does not verify" not in line, line
    # hidden as before, and the footer's NOT MEMBERS branch is unchanged
    assert not re.search(r"^ *└ \[UNSIGNED\]", p.stdout, re.M), p.stdout
    assert re.fullmatch(r"— 1 labels · 1 NOT MEMBERS \(see ⊘\) · ≈\d+ tok", footer(p.stdout)), p.stdout


@VERIFIERS
@pytest.mark.parametrize("v", [1, 2])
def test_a_present_but_failing_signature_still_reads_signature_does_not_verify(tmp_path, verifier, v):
    """The other side of the split: a signature that is THERE and fails is
    not called unsigned."""
    r = Root(tmp_path)
    r.label("rules", "[RULES]", "owner")
    r.label("bad", "[BAD]", "owner", v=v, tamper=True)
    r.seal()
    p = r.ls(verifier)
    assert p.returncode == 0, p.stdout + p.stderr
    line = nonmember(p.stdout, "[BAD]")
    assert "— signature does not verify; subtree hidden" in line, line
    assert "unsigned" not in line, line
    assert re.fullmatch(r"— 1 labels · 1 NOT MEMBERS \(see ⊘\) · ≈\d+ tok", footer(p.stdout)), p.stdout


# ── (3) do-002: --box with NO agent env checks no membership; say so ─────
#
# SM-186 verify-002 (@ 4b57c95): render() builds Members only when an owner is
# set (`member = ... if owner else None`). With --box and no CLAUDE_CODE_AGENT
# / SESSION_AGENT the owner is '', nothing is checked, an UNSIGNED label is
# SHOWN, and the footer said `all to alpha, signed`. What is shown is not
# changed here (pinned below); the footer now says membership was NOT checked.

UNCHECKED = "membership NOT checked (no owner set: no CLAUDE_CODE_AGENT or SESSION_AGENT)"


def _unsigned_box(tmp_path) -> Root:
    r = Root(tmp_path, to=BOXNAME)
    r.label("rules", "[RULES]", "owner")
    r.label("boot", "[BOOT]", "owner")
    r.label("unsigned", "[UNSIGNED]", None)
    r.seal()
    return r


@VERIFIERS
def test_no_owner_box_listing_says_membership_was_not_checked(tmp_path, verifier):
    r = _unsigned_box(tmp_path)
    p = r.ls(verifier, box=True, agent=False)
    assert p.returncode == 0, p.stdout + p.stderr
    f = footer(p.stdout)
    assert re.fullmatch(rf"— 3 labels · {re.escape(UNCHECKED)} · ≈\d+ tok", f), f
    assert not re.search(r"\bsigned\b", f), f"'signed' over an unchecked listing: {f}"
    assert not re.search(r"\bmembers\b", f), f"'members' over an unchecked listing: {f}"
    # show/hide is NOT this fix's: with no owner the unsigned label is still
    # shown, and no ⊘ line is drawn (nothing was checked to draw one).
    for lab in ("[RULES]", "[BOOT]", "[UNSIGNED]"):
        assert re.search(rf"^ *└ {re.escape(lab)}(\s|$)", p.stdout, re.M), p.stdout
    assert "⊘" not in p.stdout, p.stdout


@VERIFIERS
def test_the_same_box_with_the_agent_set_is_still_checked(tmp_path, verifier):
    """The control: the SAME fixture with CLAUDE_CODE_AGENT set hides the
    unsigned label and says NOT MEMBERS, with no unchecked note."""
    r = _unsigned_box(tmp_path)
    p = r.ls(verifier, box=True, agent=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert re.fullmatch(r"— 2 labels · 1 NOT MEMBERS \(see ⊘\) · ≈\d+ tok", footer(p.stdout)), p.stdout
    assert "NOT checked" not in p.stdout, p.stdout
    assert "unsigned (no X-Session-Signature)" in nonmember(p.stdout, "[UNSIGNED]")


@pytest.mark.parametrize("verb, shape", [
    (["--index"], r"— 4 categories\+root shown, 0 filtered"),
    (["--path", "/", "--depth", "1"], r"— / = <root@[^>]+> · 4 shown, 0 filtered"),
], ids=["index", "tree"])
def test_the_other_render_footers_say_it_too(tmp_path, verb, shape):
    """`memory tree` (--path) and --index feed the same render; their footers
    count 'filtered', and a filter that never ran must say so."""
    r = _unsigned_box(tmp_path)
    p = r.run(verb, None, box=True, agent=False)
    assert p.returncode == 0, p.stdout + p.stderr
    assert re.fullmatch(rf"{shape} · {re.escape(UNCHECKED)} · ≈\d+ tok", footer(p.stdout)), p.stdout
    # and with the agent set, the footer is byte-for-byte what it was
    p = r.run(verb, None, box=True, agent=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "NOT checked" not in footer(p.stdout), p.stdout


# ── Members.why: only the reason is new; the verdicts are not ─────────────

def _reader(name: str):
    loader = SourceFileLoader(name, str(READER))
    mod = module_from_spec(spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


@VERIFIERS
def test_why_splits_unsigned_from_signature_and_keeps_every_verdict(tmp_path, monkeypatch, verifier):
    monkeypatch.delenv("SESSION_MAIL_VERIFY", raising=False)
    if verifier:
        monkeypatch.setenv("SESSION_MAIL_VERIFY", verifier)
    r = Root(tmp_path)
    good = r.label("rules", "[RULES]", "owner")
    forged = r.label("forged", "[FORGED]", "forger")
    unsigned = r.label("unsigned", "[UNSIGNED]", None)
    bad = r.label("bad", "[BAD]", "owner", tamper=True)
    stranger = r.label("stranger", "[STRANGER]", None, frm="intruder")
    pt = _reader(f"mhs_thread_sm779_{'sub' if verifier else 'port'}")
    mem = pt.Members(AGENT)
    want = {  # message: (why, member?, unknown?)
        "rules": (None, True, False),
        "forged": (None, True, False),          # plain mode: any key passes (SM-530)
        "unsigned": ("unsigned", False, False),
        "bad": ("signature", False, False),
        "stranger": ("from", False, False),     # a header slip: no verify, as before
    }
    for name, m in (("rules", good), ("forged", forged), ("unsigned", unsigned),
                    ("bad", bad), ("stranger", stranger)):
        assert (mem.why(m), mem(m), mem.unknown(m)) == want[name], name
    assert mem.why_text(unsigned) == "unsigned (no X-Session-Signature)"
    assert mem.why_text(bad) == "signature does not verify"
