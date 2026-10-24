"""`memory init`'s read half (`_root`), SM-186 do-004.

The fixtures are a FAKE maildir and a FAKE box key, and the key is a real
ed25519 pair made by `ssh-keygen` in a temp dir. The box key does not exist in
reality yet (TM-5054), but a signature check that is mocked proves nothing
about signatures. So the messages here are signed the way the sender signs
(`X-Session-Signature`, canonicalization v1) and checked with
`ssh-keygen -Y verify`, the reader's own leg, with the principal replaced by the
box's bare address.

The box is `alpha`, the stranger is `beta`, and the hosts are `example.invalid`
(`slipbox/project-conventions.md` convention 9). Nothing here touches a real
box: `SESSION_MAIL_ROOT` is a temp dir, and the postfix map is replaced for
every test.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _init, _root, _seat
from sessionmail._init import InitError
from sessionmail.memory import cli

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                                reason="needs ssh-keygen to make and check the fake box key")

BOX = "alpha"
NS = "session-mail@example.com"
DATE = "Sun, 09 Jun 2030 12:00:00 +0000"


# ── the fake key ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def keydir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("keys")
    for name in ("box", "stranger"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name,
                        "-f", str(d / name)], check=True)
    pub = (d / "box.pub").read_text().split()
    (d / "allowed_signers").write_text(f"{BOX} {pub[0]} {pub[1]}\n")
    return d


@pytest.fixture
def key(keydir) -> _root.BoxKey:
    return _root.BoxKey(principal=BOX, allowed_signers=keydir / "allowed_signers")


@pytest.fixture
def keyed(key):
    """A key source that finds the fake box key for `alpha` and nothing else."""
    return lambda box, env: key if box == BOX else None


# ── the fake maildir ─────────────────────────────────────────────────────


@pytest.fixture
def mailroot(tmp_path) -> Path:
    root = tmp_path / "mail"
    for sub in ("new", "cur", "tmp"):
        (root / BOX / sub).mkdir(parents=True)
    return root


@pytest.fixture
def env(mailroot) -> dict[str, str]:
    return {"SESSION_MAIL_ROOT": str(mailroot)}


@pytest.fixture(autouse=True)
def no_postfix(monkeypatch):
    """The postfix map is the machine's, so every test gets a map that
    delivers nothing unless the test says otherwise."""
    monkeypatch.setattr(_seat, "box_via_map", lambda name: None)


class Box:
    """Drops signed (or unsigned) messages into the fake box, in order."""

    def __init__(self, mailroot: Path, keydir: Path) -> None:
        self.dir = mailroot / BOX
        self.keydir = keydir
        self.n = 0

    def put(self, mid: str, subject: str, *, signer: str | None = "box",
            to: str = f"{BOX}@example.invalid", in_reply_to: str = "",
            supersedes: str | tuple[str, ...] = "", body: str = "",
            retired: bool = False, raw_supersedes: tuple[str, ...] = (),
            cc: str = "") -> Path:
        """One file. SUPERSEDES may name several ids, one header line each
        (a merge); RAW_SUPERSEDES are written verbatim (a malformed value).
        Cc:, Supersedes: and X-Retired: are outside canonicalization v1, so
        a copy that differs only there still verifies, as a replay would."""
        self.n += 1
        head = ["From: beta <beta@example.invalid>", f"To: {to}", f"Subject: {subject}",
                f"Date: {DATE}", f"Message-ID: <{mid}>"]
        if cc:
            head.append(f"Cc: {cc}")
        if in_reply_to:
            head.append(f"In-Reply-To: <{in_reply_to}>")
        for sup in ((supersedes,) if isinstance(supersedes, str) else supersedes):
            if sup:
                head.append(f"Supersedes: <{sup}>")
        head += [f"Supersedes: {raw}" for raw in raw_supersedes]
        if retired:
            head.append("X-Retired: yes")
        raw = "\n".join(head) + "\n\n" + body
        if signer:
            raw = self.signature(raw, signer) + "\n" + raw
        path = self.dir / "cur" / f"{1907540800 + self.n}.M{self.n}.example:2,S"
        path.write_text(raw)
        return path

    def signature(self, raw: str, signer: str) -> str:
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(self.keydir / signer),
                               "-n", NS, "-"], input=_root.canonical(raw),
                              capture_output=True, check=True)
        b64 = "".join(line for line in done.stdout.decode().splitlines()
                      if "SSH SIGNATURE" not in line)
        return (f"X-Session-Signature: v=1; a=sshsig; n={NS}; "
                f"h=from:to:subject:date:message-id; b={b64}")

    def snapshot(self) -> list[tuple[str, int]]:
        return sorted((str(p.relative_to(self.dir)), p.stat().st_mtime_ns)
                      for p in self.dir.rglob("*"))


@pytest.fixture
def box(mailroot, keydir) -> Box:
    return Box(mailroot, keydir)


@pytest.fixture
def only_ssh_keygen(monkeypatch):
    """The read half runs ssh-keygen and nothing else: no sender, no
    sendmail, no git. Anything else it tried to run would fail the test."""
    real = subprocess.run

    def guarded(argv, *a, **kw):
        assert Path(argv[0]).name == "ssh-keygen", f"the read half ran {argv[0]}"
        return real(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded)


def refused(fn, *args, **kwargs) -> InitError:
    with pytest.raises(InitError) as caught:
        fn(*args, **kwargs)
    return caught.value


# ── the verifier: the reader's leg, principal replaced ───────────────────


def test_a_box_key_signature_verifies(box, key):
    assert _root.ssh_verify(box.put("r1@example.invalid", "[ROOT]"), key)


def test_a_session_signature_does_not_verify_as_the_box(box, key):
    """The spec's own scenario: a [ROOT] that verifies under some session's
    key and not under the box principal is not canon."""
    assert not _root.ssh_verify(box.put("r1@example.invalid", "[ROOT]", signer="stranger"), key)


def test_an_unsigned_message_does_not_verify(box, key):
    assert not _root.ssh_verify(box.put("r1@example.invalid", "[ROOT]", signer=None), key)


def test_a_signed_field_changed_after_signing_does_not_verify(box, key):
    path = box.put("r1@example.invalid", "[ROOT]")
    path.write_text(path.read_text().replace("Subject: [ROOT]", "Subject: [RULES]"))
    assert not _root.ssh_verify(path, key)


def test_the_principal_is_the_bare_box_and_nothing_else(box, keydir):
    """The allowed_signers line names `alpha`; asking for `alpha+super` (or
    any other principal) against it fails even though the bytes are good."""
    path = box.put("r1@example.invalid", "[ROOT]")
    assert not _root.ssh_verify(path, _root.BoxKey("alpha+super", keydir / "allowed_signers"))


def test_canonical_folds_what_transit_changes_and_keeps_the_tag():
    raw = ("From: Beta <BETA@example.invalid>\nTo: alpha+memory@example.invalid\n"
           "Subject:  [ROOT] \nDate: " + DATE + "\nMessage-ID: <r@x>\n\n")
    assert _root.canonical(raw) == (
        b"from: beta\nto: alpha+memory\nsubject: [ROOT]\n"
        b"date: " + DATE.encode() + b"\nmessage-id: <r@x>\n")


@pytest.mark.parametrize("body", ["", "one line\n", "two\r\nlines\n\n\n"])
def test_the_read_side_rebuilds_what_the_sender_signs(body):
    """What `_root.canonical` rebuilds from a file must be byte-for-byte what
    `_sender.canonical` signs, or no box-key signature would ever verify.
    (Also measured against a real delivered mail and `session mail-verify`:
    see the do-004 log.)"""
    from sessionmail import _sender
    head = {"from_": "Beta <beta@example.invalid>", "to": f"{BOX}+memory@example.invalid",
            "subject": "[ROOT]", "date": DATE, "message_id": "<r1@example.invalid>"}
    raw = (f"From: {head['from_']}\nTo: {head['to']}\nSubject: {head['subject']}\n"
           f"Date: {DATE}\nMessage-ID: {head['message_id']}\n\n{body}")
    assert _root.canonical(raw) == _sender.canonical(**head, body=body.replace("\r\n", "\n").encode())


# ── criterion: the tests cover no root, one signed root, an absent-id hint,
#    an unverified [ROOT] ─────────────────────────────────────────────────


def test_no_root_is_a_fresh_box(env, keyed, only_ssh_keygen):
    state = _root.read_root(BOX, env=env, key_source=keyed)
    assert (state.root_id, dict(state.labels), state.not_canon) == (None, {}, ())


def test_one_signed_root_and_its_labels(box, env, keyed, only_ssh_keygen):
    root = "r1@example.invalid"
    box.put(root, "[ROOT]")
    box.put("asks@example.invalid", "[ASKS]", in_reply_to=root)
    box.put("rules@example.invalid", "[ RULES ]", in_reply_to=root, to=f"{BOX}+memory@example.invalid")
    box.put("item@example.invalid", "an item, not a label", in_reply_to=root)
    box.put("crew@example.invalid", "[BOOT]", in_reply_to=root, signer="stranger")
    box.put("elsewhere@example.invalid", "[CONVO]", in_reply_to=root, to="beta@example.invalid")
    state = _root.read_root(BOX, env=env, key_source=keyed)
    assert state.root_id == root
    # [BOOT] is a seat's own (not canon), [CONVO] is To another box, and
    # an unbracketed reply is an item, not a label.
    assert dict(state.labels) == {"[ASKS]": "asks@example.invalid",
                                  "[RULES]": "rules@example.invalid"}


def test_a_hint_naming_an_absent_id_refuses_naming_it(box, env, keyed):
    env["ALPHA_ROOT"] = "<nowhere@example.invalid>"
    before = box.snapshot()
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind, err.result) == (2, "hint", None)
    assert "$ALPHA_ROOT" in str(err) and "nowhere@example.invalid" in str(err)
    assert box.snapshot() == before


def test_an_unverified_root_refuses_exit_3_and_is_not_adopted(box, env, keyed):
    """sessionmail's own case (seam.md §4 row 4): a root minted by a
    session before the box had a key. Never adopted, never minted
    beside."""
    box.put("ivan-root@example.invalid", "[ROOT]", signer="stranger")
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind) == (3, "signature")
    assert "ivan-root@example.invalid" in str(err) and "--supersede" in str(err)


def test_an_unsigned_root_refuses_exit_3(box, env, keyed):
    box.put("bare@example.invalid", "[ROOT]", signer=None)
    assert refused(_root.read_root, BOX, env=env, key_source=keyed).code == 3


def test_a_non_canon_root_beside_a_canonical_one_is_named_not_adopted(box, env, keyed):
    box.put("old@example.invalid", "[ROOT]", signer="stranger")
    box.put("r1@example.invalid", "[ROOT]")
    state = _root.read_root(BOX, env=env, key_source=keyed)
    assert (state.root_id, state.not_canon) == ("r1@example.invalid", ("old@example.invalid",))


# ── criterion: existence from the maildir, identity from the hint ─────────


def test_a_hint_that_agrees_is_exit_0_with_the_root(box, env, keyed, tmp_path):
    """D9: a root exists and no --supersede was asked for; not a refusal."""
    box.put("r1@example.invalid", "[ROOT]")
    env["ALPHA_ROOT"] = "<r1@example.invalid>"
    record = tmp_path / "PROJECT.md"
    record.write_text('---\nname: alpha\nmail_root: "r1@example.invalid"   # a comment\n---\nbody\n')
    assert _root.read_root(BOX, env=env, record=record, key_source=keyed).root_id == "r1@example.invalid"


def test_a_missing_hint_does_not_prove_a_missing_root(box, env, keyed):
    """Spec delta scenario "The root hint is absent but the root is not"."""
    box.put("r1@example.invalid", "[ROOT]")
    assert _root.read_root(BOX, env=env, key_source=keyed).root_id == "r1@example.invalid"


def test_a_hint_that_disagrees_refuses_naming_both(box, env, keyed):
    box.put("r1@example.invalid", "[ROOT]")
    env["ALPHA_ROOT"] = "r0@example.invalid"
    before = box.snapshot()
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind) == (2, "hint")
    assert "r0@example.invalid" in str(err) and "r1@example.invalid" in str(err)
    assert box.snapshot() == before


def test_the_record_file_is_a_hint_too(box, env, keyed, tmp_path):
    box.put("r1@example.invalid", "[ROOT]")
    record = tmp_path / "PROJECT.md"
    record.write_text("---\nmail_root: '<r0@example.invalid>'\n---\n")
    err = refused(_root.read_root, BOX, env=env, record=record, key_source=keyed)
    assert (err.code, err.kind) == (2, "hint")
    assert f"mail_root: in {record}" in str(err)


def test_a_hint_naming_a_superseded_root_names_the_live_one(box, env, keyed):
    box.put("r1@example.invalid", "[ROOT]")
    box.put("r2@example.invalid", "[ROOT]", supersedes="r1@example.invalid")
    assert _root.read_root(BOX, env=env, key_source=keyed).superseded == ("r1@example.invalid",)
    env["ALPHA_ROOT"] = "r1@example.invalid"
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind) == (2, "hint")
    assert "SUPERSEDED" in str(err) and "r2@example.invalid" in str(err)


@pytest.mark.parametrize("twin", ["[asks]", "[ASKS] ", "[ Asks ]"])
def test_two_labels_of_one_canonical_name_are_a_fork(box, env, keyed, twin):
    """Label identity is the CANONICAL name (a case variant and a trailing
    space are the same label), and a fork refuses naming both ids."""
    box.put("r1@example.invalid", "[ROOT]")
    box.put("a1@example.invalid", "[ASKS]", in_reply_to="r1@example.invalid")
    box.put("a2@example.invalid", twin, in_reply_to="r1@example.invalid")
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind) == (2, "fork")
    assert "a1@example.invalid" in str(err) and "a2@example.invalid" in str(err)



# ── the one live-node rule: ONE table, BOTH graphs, the requirement's 33 rows ─
#
# The delta's "One live-node rule resolves every Supersedes: graph, the root's
# and the labels' alike" (openspec/changes/memory-init/specs/memory/spec.md:446
# @ 85513d2) and its shape table. Each row below is ONE abstract shape, and the
# test puts it into a box twice: once as the ROOT graph (every node a `[ROOT]`,
# threaded nowhere unless the row says so) and once as the LABEL graph (every
# node a `[NAME]` label replying to a live root R0). A new shape later is one
# new row here, not a new test (tasks.md 3.1a).
#
# Where do-004's earlier graph tests went (they were at e63bc6d):
#   two live roots                      -> row 3b
#   cycle {self, two, three}            -> rows 6, 7a, 7b
#   cycle beside a standalone (two/self)-> rows 8a, 8b
#   a tail into the cycle               -> row 9
#   two cycles; cycle beside a chain    -> rows 10a, 10b
#   unverified root cannot supersede    -> row 12
#   unverified root inside a chain      -> row 14
#   canonical superseding unverified    -> row 13
#   unverified root inside a cycle      -> row 15
#   supersedes an absent id             -> row 16
#   absent id does not join two roots   -> row 17a
#   chain beside a root naming an absent-> row 17b
#   chain {two, three, out of order}    -> rows 2a, 2b, 2c
#   a superseded label is not a twin    -> row 2a, label graph
# One change of expectation, by the rule: a fork now names every id of the
# FORK component ("naming every id in that component"), not every signed root
# in the box, so rows 8a/8b/10b no longer require the standalone twin or the
# healthy chain beside a cycle to be named.

TOP, NONE, ROOT = "<top>", "<none>", "<root>"
R0 = "root0@example.invalid"


@dataclasses.dataclass(frozen=True)
class N:
    """One delivered file of a shape. `name` is the label in the label graph
    (every node is `[ROOT]` in the root graph). `irt`: TOP is the graph's own
    parent (none for a root, R0 for a label), ROOT is R0, NONE is no header."""

    id: str
    name: str = "ASKS"
    sup: tuple[str, ...] = ()
    irt: str = TOP
    signer: str | None = "box"
    retired: bool = False
    subject: str = ""                 # overrides the name, in both graphs
    raw_sup: tuple[str, ...] = ()     # literal Supersedes: values (malformed)
    cc: str = ""


@dataclasses.dataclass(frozen=True)
class Row:
    n: str
    shape: str
    nodes: tuple[N, ...]
    R: tuple | None                   # None: not a root-graph shape (n/a)
    L: tuple | None                   # None: not a label-graph shape (n/a)
    base_root: bool = False           # the root graph also carries R0 standalone
    root_sup: str = ""                # label graph: R0 supersedes this node
    says: str = ""                    # a refusal must say this, so it refuses for its reason


def live(end, gone=()):              # root graph: this end is the live root
    return ("live", end, set(gone))


def present(labels, vacated=None):   # label graph: these PRESENT, these VACATED
    return ("present", labels, vacated or {})


def fork(*ids):                      # exit 2, kind fork, naming every id given
    return ("fork", set(ids))


def copies(mid):                     # exit 2, the id AND every file of it named
    return ("copies", {mid})


def retired(*ids):                   # root graph: only RETIRED components
    return ("retired", set(ids))


ROWS = [
    Row("1", "standalone", (N("a"),), live("a"), present({"[ASKS]": "a"})),
    Row("2a", "chain of 2", (N("a"), N("b", sup=("a",))),
        live("b", ["a"]), present({"[ASKS]": "b"})),
    Row("2b", "chain of 3", (N("a"), N("b", sup=("a",)), N("c", sup=("b",))),
        live("c", ["a", "b"]), present({"[ASKS]": "c"})),
    Row("2c", "chain of 3, out of order", (N("c", sup=("b",)), N("a"), N("b", sup=("a",))),
        live("c", ["a", "b"]), present({"[ASKS]": "c"})),
    Row("3a", "chain + standalone twin", (N("a"), N("b", sup=("a",)), N("c")),
        fork("b", "c"), fork("b", "c")),
    Row("3b", "two standalone", (N("a"), N("b")), fork("a", "b"), fork("a", "b")),
    Row("4", "two supersedes of one node, same name",
        (N("a"), N("b", sup=("a",)), N("c", sup=("a",))),
        fork("a", "b", "c"), fork("a", "b", "c"), says="ends"),
    Row("5", "two supersedes of one node, two names",
        (N("a", "RULES"), N("b", "RULINGS", sup=("a",)), N("c", "LESSONS", sup=("a",))),
        None, fork("a", "b", "c"), says="ends"),
    Row("6", "self-cycle", (N("a", sup=("a",)),), fork("a"), fork("a"), says="cycle"),
    Row("7a", "2-cycle", (N("a", sup=("b",)), N("b", sup=("a",))),
        fork("a", "b"), fork("a", "b"), says="cycle"),
    Row("7b", "3-cycle", (N("a", sup=("b",)), N("b", sup=("c",)), N("c", sup=("a",))),
        fork("a", "b", "c"), fork("a", "b", "c"), says="cycle"),
    Row("8a", "2-cycle + standalone twin", (N("a", sup=("b",)), N("b", sup=("a",)), N("c")),
        fork("a", "b"), fork("a", "b"), says="cycle"),
    Row("8b", "self-cycle + standalone twin", (N("a", sup=("a",)), N("c")),
        fork("a"), fork("a"), says="cycle"),
    Row("9", "tail into a cycle", (N("a", sup=("b",)), N("b", sup=("a",)), N("c", sup=("a",))),
        fork("a", "b", "c"), fork("a", "b", "c"), says="cycle"),
    Row("10a", "two cycles", (N("a", sup=("b",)), N("b", sup=("a",)),
                              N("c", sup=("d",)), N("d", sup=("c",))),
        fork("a", "b", "c", "d"), fork("a", "b", "c", "d"), says="cycle"),
    Row("10b", "cycle + chain", (N("a", sup=("b",)), N("b", sup=("a",)),
                                 N("c"), N("d", sup=("c",))),
        fork("a", "b"), fork("a", "b"), says="cycle"),
    Row("11", "cross-name cycle [RULES] <-> [RULINGS]",
        (N("a", "RULES", sup=("b",)), N("b", "RULINGS", sup=("a",))),
        None, fork("a", "b"), says="cycle"),
    Row("12", "unverified message supersedes a node",
        (N("a"), N("b", sup=("a",), signer="stranger")), live("a"), present({"[ASKS]": "a"})),
    Row("13", "node supersedes an unverified message",
        (N("a", signer="stranger"), N("b", sup=("a",))), live("b"), present({"[ASKS]": "b"})),
    Row("14", "unverified message inside a chain",
        (N("a"), N("b", sup=("a",), signer="stranger"), N("c", sup=("b",))),
        fork("a", "c"), fork("a", "c")),
    Row("15", "unverified link in a would-be cycle",
        (N("a", sup=("b",)), N("b", sup=("a",), signer="stranger")),
        live("a"), present({"[ASKS]": "a"})),
    Row("16", "Supersedes: names an absent id, alone", (N("a", sup=("gone",)),),
        live("a"), present({"[ASKS]": "a"})),
    Row("17a", "a node naming an absent id beside a twin", (N("a"), N("b", sup=("gone",))),
        fork("a", "b"), fork("a", "b")),
    Row("17b", "a chain beside a node naming an absent id",
        (N("a"), N("b", sup=("a",)), N("c", sup=("gone",))),
        fork("b", "c"), fork("b", "c")),
    # The root column ("root unaffected") is asserted by every label run that
    # does not refuse: its root is R0.
    Row("18", "label whose Supersedes: names the root", (N("a", sup=(ROOT,)),),
        None, present({"[ASKS]": "a"})),
    # In the label graph it is not a label node, and the root graph it joins
    # forks first: R0 and it are two live roots.
    Row("19", "[ROOT]-titled reply under the root, no supersede",
        (N("b", "ROOT", irt=ROOT),), fork(R0, "b"), fork(R0, "b"), base_root=True),
    Row("20", "[ROOT]-titled reply that supersedes the live root",
        (N("b", "ROOT", irt=ROOT, sup=(ROOT,)),), live("b", [R0]), None, base_root=True),
    Row("21", "two delivered copies of one Message-ID",
        (N("a"), N("a", cc=f"{BOX}+tag@example.invalid")), live("a"), present({"[ASKS]": "a"})),
    Row("22", "verified copies of one id that disagree on a read field",
        (N("c", "RULES"), N("a", "RULINGS"), N("a", "RULINGS", sup=("c",))),
        copies("a"), copies("a"), says="disagree"),
    Row("23", "unverified copy of a node's Message-ID",
        (N("a"), N("a", sup=("x",), signer="stranger")), live("a"), present({"[ASKS]": "a"})),
    Row("24", "cross-name rename, [RULINGS] supersedes [RULES]",
        (N("a", "RULES"), N("b", "RULINGS", sup=("a",))),
        None, present({"[RULINGS]": "b"}, {"[RULES]": "b"})),
    Row("25a", "successor threaded under its predecessor, renamed",
        (N("a", "RULES"), N("b", "RULINGS", sup=("a",), irt="a")),
        None, present({"[RULINGS]": "b"}, {"[RULES]": "b"})),
    Row("25b", "successor threaded under its predecessor, same name",
        (N("a"), N("b", sup=("a",), irt="a")), None, present({"[ASKS]": "b"})),
    Row("26", "label under the new root supersedes one in the old tree",
        (N("o", "ROOT", irt=NONE), N("x", irt="o"), N("b", sup=("x",))),
        None, present({"[ASKS]": "b"}), root_sup="o"),
    Row("27", "merge: one node supersedes two",
        (N("a"), N("b"), N("c", sup=("a", "b"))), live("c", ["a", "b"]), present({"[ASKS]": "c"})),
    # Resolution clause 1's own counter-example: n -> m1, n -> m2, m1 -> m2 is
    # not a directed cycle, whatever an undirected walk would say. One end.
    Row("27b", "merge over a chain: the redundant edge is not a cycle",
        (N("a"), N("b", sup=("a",)), N("c", sup=("a", "b"))),
        live("c", ["a", "b"]), present({"[ASKS]": "c"})),
    Row("28", "label retired",
        (N("a"), N("x", sup=("a",), irt="a", retired=True)),
        None, present({}, {"[ASKS]": "x"})),
    Row("29", "one node superseded by a successor AND a retirement",
        (N("a"), N("b", sup=("a",)), N("x", sup=("a",), irt="a", retired=True)),
        None, fork("a", "b", "x")),
    Row("30", "root retired, no other root",
        (N("a"), N("x", sup=("a",), irt="a", retired=True)), retired("a", "x"), None),
    # Alone, so no name collision can refuse it for another reason.
    Row("31", "Supersedes: value is not one Message-ID",
        (N("b", raw_sup=("<a@example.invalid> <c@example.invalid>",)),),
        fork("b"), fork("b"), says="not exactly one Message-ID"),
    Row("32", "a non-label, non-retirement reply supersedes a label",
        (N("a"), N("b", subject="just an item", sup=("a",), irt="a")),
        None, present({"[ASKS]": "a"})),
    Row("33a", "box-signed [root], alone", (N("a", subject="[root]"),), fork("a"), fork("a"),
        says="not EXACTLY [ROOT]"),
    Row("33b", "box-signed [ Root ] beside a root", (N("a", subject="[ Root ]"),),
        fork("a"), fork("a"), base_root=True, says="not EXACTLY [ROOT]"),
]


def test_both_graphs_go_through_one_resolver(box, env, keyed, monkeypatch):
    """tasks.md 3.1a: `read_root` and `_labels` call ONE shared resolver,
    and neither keeps graph logic of its own. Spied: one call per graph."""
    real, asked = _root._resolve_graph, []
    monkeypatch.setattr(_root, "_resolve_graph",
                        lambda j, ids, box_, what: asked.append((what, set(ids)))
                        or real(j, ids, box_, what))
    box.put(R0, "[ROOT]")
    box.put("a@example.invalid", "[ASKS]", in_reply_to=R0)
    assert dict(_root.read_root(BOX, env=env, key_source=keyed).labels) == {
        "[ASKS]": "a@example.invalid"}
    assert asked == [("[ROOT]s", {R0}), ("labels", {"a@example.invalid"})]


def test_the_table_is_the_requirements_33_rows():
    """Every row of the delta's table is here, and no row is invented."""
    assert {int(r.n.rstrip("abc")) for r in ROWS} == set(range(1, 34))
    assert len({r.n for r in ROWS}) == len(ROWS)


def _mid(x: str) -> str:
    return R0 if x == ROOT else x if "@" in x else f"{x}@example.invalid"


def _cases():
    for row in ROWS:
        for graph in ("R", "L"):
            if getattr(row, graph) is not None:
                yield pytest.param(row, graph, id=f"{row.n}-{graph}-{row.shape}")


@pytest.mark.parametrize("row,graph", list(_cases()))
def test_one_live_node_rule(box, env, keyed, row, graph):
    """The one rule, one row, one graph. A refusal is exit 2, kind fork,
    names every id the row expects, and leaves the box untouched."""
    if graph == "L" or row.base_root:
        box.put(R0, "[ROOT]", supersedes=_mid(row.root_sup) if row.root_sup else "")
    for n in row.nodes:
        if n.subject:
            subject = n.subject
        elif n.retired:
            subject = "retired: no longer in force"
        else:
            subject = "[ROOT]" if graph == "R" else f"[{n.name}]"
        irt = {TOP: "" if graph == "R" else R0, NONE: ""}.get(n.irt, _mid(n.irt))
        box.put(_mid(n.id), subject, signer=n.signer, in_reply_to=irt,
                supersedes=tuple(_mid(s) for s in n.sup), retired=n.retired,
                raw_supersedes=n.raw_sup, cc=n.cc)
    want = getattr(row, graph)
    before = box.snapshot()
    hints = [{}]
    if graph == "R" and want[0] != "live":
        hints.append({"ALPHA_ROOT": _mid(row.nodes[0].id)})   # a fork refuses first
    for extra in hints:
        run = lambda: _root.read_root(BOX, env={**env, **extra}, key_source=keyed)  # noqa: E731
        if want[0] in ("fork", "copies", "retired"):
            err = refused(run)
            assert (err.code, err.kind, err.result) == (2, "fork", None), str(err)
            named = {_mid(x) for x in want[1]}
            assert all(m in str(err) for m in named), (named, str(err))
            if want[0] == "copies":
                files = sorted(box.dir.rglob("*:2,S"))
                assert sum(str(p) in str(err) for p in files) == 2, str(err)
            if want[0] == "retired":
                assert "retired" in str(err)
            assert row.says in str(err), (row.says, str(err))
        elif want[0] == "live":
            state = run()
            assert state.root_id == _mid(want[1])
            assert set(state.superseded) == {_mid(x) for x in want[2]}
            unverified = {_mid(n.id) for n in row.nodes if n.signer != "box"} - {
                _mid(n.id) for n in row.nodes if n.signer == "box"}
            assert set(state.not_canon) == unverified
        else:
            state = run()
            assert state.root_id == R0, "a label-graph shape must leave the root alone"
            assert dict(state.labels) == {k: _mid(v) for k, v in want[1].items()}
            assert dict(state.vacated) == {k: _mid(v) for k, v in want[2].items()}
    assert box.snapshot() == before


# ── criterion: the key check decides what counts as canon ─────────────────


def test_no_key_home_is_exit_3_even_on_a_perfect_box(box, env):
    """The default key source finds nothing: the key's home is TM-5054's.
    With no key nothing can be judged canon, so nothing may be minted."""
    box.put("r1@example.invalid", "[ROOT]")
    before = box.snapshot()
    err = refused(_root.read_root, BOX, env=env)
    assert (err.code, err.kind) == (3, "key")
    assert "principal 'alpha'" in str(err) and "the caller's to provide" in str(err)
    assert box.snapshot() == before


def test_a_key_for_another_principal_is_unusable(env, keydir):
    other = _root.BoxKey("beta", keydir / "allowed_signers")
    err = refused(_root.read_root, BOX, env=env, key_source=lambda b, e: other)
    assert (err.code, err.kind) == (3, "key")


def test_a_key_with_no_allowed_signers_file_is_unusable(env, tmp_path):
    gone = _root.BoxKey(BOX, tmp_path / "no-such-allowed-signers")
    assert refused(_root.read_root, BOX, env=env, key_source=lambda b, e: gone).code == 3


def test_the_verifier_is_what_decides(box, env, keyed):
    """Canon is whatever the key check says, not the From: line: the same
    box reads as a canonical root, or as a refusal, by the verifier alone."""
    box.put("r1@example.invalid", "[ROOT]")
    assert _root.read_root(BOX, env=env, key_source=keyed,
                           verify=lambda p, k: True).root_id == "r1@example.invalid"
    assert refused(_root.read_root, BOX, env=env, key_source=keyed,
                   verify=lambda p, k: False).code == 3


# ── criterion: R6 read-side refusals name what they wanted, mint nothing ──


def test_no_such_box_refuses_exit_2_naming_it(env, keyed, mailroot):
    before = sorted(mailroot.rglob("*"))
    err = refused(_root.read_root, "gamma", env=env, key_source=keyed)
    assert (err.code, err.kind, err.result) == (2, "box", None)
    assert "'gamma'" in str(err) and "init births no box" in str(err)
    assert sorted(mailroot.rglob("*")) == before, "a refusal must not birth the box"


@pytest.mark.parametrize("bad", ["alpha+super", "/tmp/alpha", "~/alpha", "a/b", "", "-alpha"])
def test_box_must_be_a_bare_name(bad, env, keyed):
    err = refused(_root.read_root, bad, env=env, key_source=keyed)
    assert (err.code, err.kind) == (1, "usage")


def test_a_record_file_that_does_not_exist_is_usage(env, keyed, tmp_path):
    err = refused(_root.read_root, BOX, env=env, key_source=keyed, record=tmp_path / "nope.md")
    assert (err.code, err.kind) == (1, "record") and "nope.md" in str(err)


def test_a_record_file_without_front_matter_is_usage(env, keyed, tmp_path):
    flat = tmp_path / "README.md"
    flat.write_text("# no front matter\nmail_root: r1@example.invalid\n")
    err = refused(_root.read_root, BOX, env=env, key_source=keyed, record=flat)
    assert (err.code, err.kind) == (1, "record")


def test_record_hint_reads_the_front_matter_only(tmp_path):
    f = tmp_path / "PROJECT.md"
    f.write_text("---\nname: alpha\n---\nmail_root: r9@example.invalid\n")
    assert _root.record_hint(f) is None
    f.write_text('---\nmail_root: ""\n---\n')
    assert _root.record_hint(f) is None
    f.write_text("---\nmail_root: <r1@example.invalid>  # set by hand\n---\n")
    assert _root.record_hint(f) == "r1@example.invalid"


def test_root_var_is_the_readers_spelling():
    assert _root.root_var("delta-chat") == "DELTA_CHAT_ROOT"
    assert _root.root_var("vms.bob") == "VMS_BOB_ROOT"


# ── SEAM: BOX is resolved only by resolve_box; flat vs map disagreement ───


def test_flat_and_map_disagreeing_refuses_exit_2_naming_both(env, keyed, tmp_path, monkeypatch):
    """seam.md A4: resolve_box takes the FLAT <root>/<name> before the map,
    so a flat `alpha` would win the read while postfix delivers alpha@host
    elsewhere. Reading one box and minting into another is a fork."""
    elsewhere = tmp_path / "store" / BOX
    for sub in ("new", "cur", "tmp"):
        (elsewhere / sub).mkdir(parents=True)
    monkeypatch.setattr(_seat, "box_via_map",
                        lambda name: (elsewhere, f"{name}@example -> store/{name}"))
    err = refused(_root.read_root, BOX, env=env, key_source=keyed)
    assert (err.code, err.kind) == (2, "ambiguous")
    assert str(elsewhere) in str(err) and str(Path(env["SESSION_MAIL_ROOT"]) / BOX) in str(err)


def test_flat_and_map_agreeing_is_one_box(env, keyed, mailroot, monkeypatch):
    monkeypatch.setattr(_seat, "box_via_map", lambda name: (mailroot / BOX, "same"))
    assert _root.read_root(BOX, env=env, key_source=keyed).maildir == mailroot / BOX


def test_a_box_found_only_by_the_map_is_read_there(env, keyed, tmp_path, monkeypatch):
    nested = tmp_path / "store" / "delta"
    for sub in ("new", "cur", "tmp"):
        (nested / sub).mkdir(parents=True)
    monkeypatch.setattr(_seat, "box_via_map", lambda name: (nested, "delta@example -> store/delta"))
    state = _root.read_root("delta", env=env,
                            key_source=lambda b, e: _root.BoxKey("delta", Path(__file__)))
    assert (state.maildir, state.how.startswith("via map")) == (nested, True)


def test_resolution_goes_through_resolve_box(env, keyed, monkeypatch):
    """ONE resolver: the read half asks `_maildir.resolve_box` and nothing
    that re-implements it."""
    from sessionmail import _maildir
    asked = []
    real = _maildir.resolve_box
    monkeypatch.setattr(_maildir, "resolve_box",
                        lambda box, env=None: asked.append(box) or real(box, env))
    _root.read_root(BOX, env=env, key_source=keyed)
    assert asked and asked[0] == BOX


# ── InitError: .code, .kind, .result on the closed table ─────────────────


def test_init_error_carries_code_kind_and_result():
    err = InitError("x", 2, kind="hint")
    assert (err.code, err.kind, err.result) == (2, "hint", None)
    result = _init.InitResult(root_id="r", minted=("[ROOT]",), existing=(), recorded=None)
    assert InitError("y", 3, kind="signature", result=result).result is result


@pytest.mark.parametrize("code", [0, 4, 69, 124])
def test_init_error_refuses_a_code_off_the_table(code):
    with pytest.raises(ValueError):
        InitError("x", code, kind="usage")


def test_init_error_refuses_a_kind_it_does_not_know():
    with pytest.raises(ValueError):
        InitError("x", 2, kind="oops")
    with pytest.raises(TypeError):
        InitError("x", 2)  # kind is required and keyword-only


def test_init_result_is_frozen_with_the_seam_fields():
    r = _init.InitResult(root_id="r", minted=(), existing=(), recorded=None)
    assert (dict(r.labels), r.superseded) == ({}, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.root_id = "other"  # type: ignore[misc]


# ── the exit table, asserted as a table (tasks.md 3.2) ───────────────────


def _code_of(fn) -> int:
    try:
        fn()
    except InitError as exc:
        return exc.code
    return 0


def test_the_exit_table_is_closed_and_every_row_holds(box, env, keyed, tmp_path):
    """Every row of "init refuses loudly and never forks" that the READ half
    can reach, with the code it must give. The mint rows (a minted root
    that does not verify, --labels with --plus, an unknown label) are
    do-005's to add here. Together the rows reach every code on the table
    and no other."""
    root = "r1@example.invalid"

    def fresh(**kw):
        return lambda: _root.read_root(BOX, env=env, key_source=keyed, **kw)

    def with_env(**extra):
        return lambda: _root.read_root(BOX, env={**env, **extra}, key_source=keyed)

    rows = []
    rows.append(("fresh box, no hint", fresh(), 0))
    rows.append(("BOX not a bare name", lambda: _root.read_root("a+b", env=env, key_source=keyed), 1))
    rows.append(("--record FILE missing", fresh(record=tmp_path / "gone.md"), 1))
    rows.append(("BOX does not exist", lambda: _root.read_root("gamma", env=env, key_source=keyed), 2))
    rows.append(("hint names a message not in BOX", with_env(ALPHA_ROOT="x@example.invalid"), 2))
    rows.append(("the box key is absent", lambda: _root.read_root(BOX, env=env), 3))
    table = [(name, _code_of(fn), want) for name, fn, want in rows]

    box.put(root, "[ROOT]")
    later = [
        ("a root exists, no --supersede (D9)", fresh(), 0),
        ("a hint that disagrees with the scan", with_env(ALPHA_ROOT="r0@example.invalid"), 2),
    ]
    table += [(name, _code_of(fn), want) for name, fn, want in later]
    box.put("r2@example.invalid", "[ROOT]")
    table.append(("two live roots", _code_of(fresh()), 2))

    wrong = [(name, got, want) for name, got, want in table if got != want]
    assert not wrong, wrong
    assert {got for _, got, _ in table} == {0, 1, 2, 3}


@pytest.mark.parametrize("argv", [
    ["init"],                          # BOX is required
    ["init", "--bogus", BOX],          # an unknown flag: the do-002 N2 row
    [
        "init", BOX, "--labels",       # a flag without its value
    ],
    ["init", BOX, "extra"],            # one BOX, not two
])
def test_a_usage_error_on_init_exits_1_not_clicks_2(argv):
    """The page promises `1 usage`, and the table gives 2 to "not found".
    Click's default for a usage error is 2, so `init` remaps it."""
    result = CliRunner().invoke(cli, argv, prog_name="memory")
    assert result.exit_code == 1, (result.exit_code, result.output)
    assert "NOT IMPLEMENTED" not in result.output


def test_the_remap_is_init_s_alone():
    """The remap is scoped to `init`, the verb this slice owns. `ls` still
    exits 2 on an unknown flag. Its page ALSO says `1 usage`, so `ls`, `tree`,
    `cat` and `run` carry the same mismatch. That is recorded as a follow-up
    (do-004 log) and is not widened here: those verbs shell to the bash oracle,
    and their exit contract is not this change's."""
    assert CliRunner().invoke(cli, ["ls", "--bogus"], prog_name="memory").exit_code == 2


def test_help_is_not_a_usage_error():
    assert CliRunner().invoke(cli, ["init", "-h"], prog_name="memory").exit_code == 0


# ── --supersede: what the read half hands the transition (SM-186 do-007) ──
#
# `read_root(..., supersede=True)` is the custodian's read, and it differs from
# the plain read in two readings and nothing else (do-007 decision (a), the
# do-004 review N1): a box whose only [ROOT]s do not verify is RETURNED, and a
# hint naming the live root's DIRECT predecessor is step 2 left undone. What
# the act supersedes comes back as `predecessor`.

R1, R2, R3 = "r1@example.invalid", "r2@example.invalid", "r3@example.invalid"


def sup(env, keyed, **kw):
    return _root.read_root(BOX, env=env, key_source=keyed, supersede=True, **kw)


def test_supersede_returns_a_box_whose_only_root_does_not_verify(box, env, keyed):
    """sessionmail's own box: its root predates the box key. The plain read
    refuses exit 3 (unchanged); the supersede read returns the state, root_id
    None, with that root as the one to supersede."""
    box.put(R1, "[ROOT]", signer="stranger")
    assert refused(_root.read_root, BOX, env=env, key_source=keyed).code == 3
    state = sup(env, keyed)
    assert (state.root_id, state.not_canon, state.predecessor) == (None, (R1,), R1)
    assert dict(state.labels) == {} and state.key.principal == BOX


def test_supersede_with_two_unverified_roots_needs_a_hint(box, env, keyed):
    box.put(R1, "[ROOT]", signer="stranger")
    box.put(R2, "[ROOT]", signer=None)
    err = refused(sup, env, keyed)
    assert (err.code, err.kind) == (2, "hint")
    assert R1 in str(err) and R2 in str(err) and "does not pick" in str(err)
    assert sup({**env, "ALPHA_ROOT": R2}, keyed).predecessor == R2


def test_supersede_of_a_live_root_names_the_live_root(box, env, keyed):
    box.put(R1, "[ROOT]")
    box.put("a@example.invalid", "[ASKS]", in_reply_to=R1)
    state = sup({**env, "ALPHA_ROOT": R1}, keyed)
    assert (state.root_id, state.predecessor) == (R1, R1)
    assert dict(state.labels) == {"[ASKS]": "a@example.invalid"}
    # the plain read never sets it
    assert _root.read_root(BOX, env=env, key_source=keyed).predecessor is None


def test_a_fresh_box_has_nothing_to_supersede(box, env, keyed):
    assert sup(env, keyed).predecessor is None


def test_a_hint_naming_the_direct_predecessor_is_step_2_left_undone(box, env, keyed, tmp_path):
    """design-002 review N-2: the pointer step failed after R2 was minted, so
    the pointer still names R1. The supersede read accepts it and names R1;
    the plain read still refuses (moving a pointer is an act with authority),
    and now says which command completes it."""
    box.put(R1, "[ROOT]")
    box.put(R2, "[ROOT]", supersedes=R1)
    record = tmp_path / "PROJECT.md"
    record.write_text(f'---\nname: alpha\nmail_root: "{R1}"\n---\n')
    state = sup(env, keyed, record=record)
    assert (state.root_id, state.predecessor, state.superseded) == (R2, R1, (R1,))
    err = refused(_root.read_root, BOX, env=env, key_source=keyed, record=record)
    assert (err.code, err.kind) == (2, "hint")
    assert "SUPERSEDED" in str(err) and "--supersede" in str(err) and "completes" in str(err)


def test_a_hint_two_roots_back_still_refuses_naming_the_live_one(box, env, keyed):
    """Only the DIRECT predecessor reads as an unfinished step 2; a pointer
    two supersedes behind is the spec's own refusal, in either mode."""
    box.put(R1, "[ROOT]")
    box.put(R2, "[ROOT]", supersedes=R1)
    box.put(R3, "[ROOT]", supersedes=R2)
    for supersede in (False, True):
        err = refused(_root.read_root, BOX, env={**env, "ALPHA_ROOT": R1}, key_source=keyed,
                      supersede=supersede)
        assert (err.code, err.kind) == (2, "hint")
        assert "SUPERSEDED" in str(err) and R3 in str(err)
        assert "completes" not in str(err)


def test_a_transition_left_at_step_1_is_resumed(box, env, keyed):
    """The transition's own half-done state: R1 the box key never signed, R2
    box-signed and superseding it, the pointer still on R1."""
    box.put(R1, "[ROOT]", signer="stranger")
    box.put(R2, "[ROOT]", supersedes=R1)
    state = sup({**env, "ALPHA_ROOT": R1}, keyed)
    assert (state.root_id, state.predecessor, state.not_canon) == (R2, R1, (R1,))
    # once the pointer names R2, a plain read is an ordinary live root
    plain = _root.read_root(BOX, env={**env, "ALPHA_ROOT": R2}, key_source=keyed)
    assert (plain.root_id, plain.not_canon, plain.predecessor) == (R2, (R1,), None)


def test_a_stranger_root_the_live_root_does_not_name_refuses(box, env, keyed):
    box.put(R1, "[ROOT]")
    box.put("x@example.invalid", "[ROOT]", signer="stranger")
    err = refused(sup, {**env, "ALPHA_ROOT": "x@example.invalid"}, keyed)
    assert (err.code, err.kind) == (2, "hint")
    assert R1 in str(err) and "x@example.invalid" in str(err)


def test_two_hints_naming_two_predecessors_refuse(box, env, keyed, tmp_path):
    """A merge root supersedes R1 and R2; one hint says R1, the other R2."""
    box.put(R1, "[ROOT]")
    box.put(R2, "[ROOT]")
    box.put(R3, "[ROOT]", supersedes=(R1, R2))
    record = tmp_path / "PROJECT.md"
    record.write_text(f'---\nmail_root: "{R2}"\n---\n')
    err = refused(sup, {**env, "ALPHA_ROOT": R1}, keyed, record=record)
    assert (err.code, err.kind) == (2, "hint")
    assert R1 in str(err) and R2 in str(err)


def test_the_supersede_read_is_read_only(box, env, keyed, only_ssh_keygen):
    box.put(R1, "[ROOT]", signer="stranger")
    before = box.snapshot()
    sup(env, keyed)
    assert box.snapshot() == before
