"""SM-698: ONE fork predicate. ls, tree, cat, latest, --boot and run give ONE
answer to "is item N a FORK" (MHS 110), and cat refuses a fork.

Measured at 5dc20f8 (SM-698 findings/repro.md, rca.md): `ls /BOOT` said
`FORK: 10 x3` over one live letter (a live box's /BOOT FORK x19), `ls /SPEC` listed a
retired item as live, `tree /SPLIT` missed a fork whose fix replied to the item
it replaced, `cat` opened the newest twin with exit 0, and `--boot` read the
retired v5 while v6 was live. The cause: each verb counted twins its own way,
`ls` from a depth-cut box, `tree` by literal parent, and `cat` not at all.

The fixtures are repro.sh's four labels, plus membership and convergence
controls. Each label's truth is in TRUTH, and every verb must agree with it.

Hermetic: a mktemp HOME, NOTMUCH_CONFIG and maildir with REAL notmuch (skipped
where it is absent), the worktree's own oracle scripts, and a stub verifier
(SESSION_MAIL_VERIFY reads an X-Fixture-Sig header: good -> 0, none -> 2).
`session-mail-run` is imported for its item list only (items_from_mail +
refuse_forks): no item is ever executed.

Run: uv run pytest -q tests/mhs/test_fork_one_definition.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

MHS = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "sessionmail", "mhs"))
AGENT = "reader"
HOST = "host1.session-mail"

pytestmark = pytest.mark.skipif(shutil.which("notmuch") is None,
                                reason="no notmuch: these probes read a real index")

# label -> the forked number, or None when one copy of 10 lives
TRUTH = {"BOOT": None, "SPEC": "10", "SPLIT": "10", "PLAIN": "10",
         "TWINS": None, "CONVERGE": None, "SAME": "10"}
# label -> the live 10 where there is no fork
LIVE = {"TWINS": "10 real", "CONVERGE": "10 v3 merges both"}
FORK_IDS = {"SPEC": {"s2", "s3"}, "SPLIT": {"p2", "p3"}, "PLAIN": {"c1", "c2"},
            "SAME": {"m1", "m2"}}

VERIFY = """#!/usr/bin/env bash
s=$(sed -n '/^$/q;s/^X-Fixture-Sig: *//p' "$1")
case "$s" in good) exit 0;; forged) exit 1;; hang) sleep 5; exit 0;; *) exit 2;; esac
"""


def build(root, six):
    """The box: repro.sh's labels, then the controls. six: /BOOT also holds
    "10 letter v6", a reply to v5 that supersedes it (the live box's shape again)."""
    md = root / "box"
    for sub in ("cur", "new", "tmp"):
        (md / "mail" / sub).mkdir(parents=True)
    n = [0]

    def mail(local, subject, parent=None, sup=(), sig="good"):
        n[0] += 1
        hdr = ["Message-ID: <%s@%s>" % (local, HOST),
               "Date: Sun, 09 Jun 2030 %02d:%02d:00 +1000" % (9 + n[0] // 60, n[0] % 60),
               "From: %s <%s@%s>" % (AGENT, AGENT, HOST),
               "To: %s+memory@%s" % (AGENT, HOST),
               "Subject: %s" % subject]
        if sig != "none":
            hdr.append("X-Fixture-Sig: %s" % sig)
        if parent:
            hdr += ["In-Reply-To: <%s@%s>" % (parent, HOST), "References: <%s@%s>" % (parent, HOST)]
        for s in ([sup] if isinstance(sup, str) else sup):
            hdr.append("Supersedes: <%s@%s>" % (s, HOST))
        (md / "mail" / "cur" / ("%04d.%s:2,S" % (n[0], local))).write_text(
            "\n".join(hdr) + "\n\nbody of " + subject + "\n")

    mail("root", "[ROOT]")
    # (a) the live box's shape: v2 and v4 reply to the PREVIOUS letter, not to [BOOT].
    mail("boot", "[BOOT]", parent="root")
    mail("b1", "10 letter v1", parent="boot")
    mail("b2", "10 letter v2", parent="b1", sup="b1")
    mail("b3", "10 letter v3", parent="boot", sup="b2")
    mail("b4", "10 letter v4", parent="b3", sup="b3")
    mail("b5", "10 letter v5", parent="boot", sup="b4")
    # spec.md:107-111's own scenario: two supersedes of one item.
    mail("spec", "[SPEC]", parent="root")
    mail("s1", "10 v1", parent="spec")
    mail("s2", "10 v2 first fix", parent="s1", sup="s1")
    mail("s3", "10 v3 second fix", parent="s1", sup="s1")
    # (b) twins with different LITERAL parents.
    mail("split", "[SPLIT]", parent="root")
    mail("p1", "10 v1", parent="split")
    mail("p2", "10 other", parent="split")
    mail("p3", "10 v3 fix of v1", parent="p1", sup="p1")
    # (c) two siblings, no supersede.
    mail("plain", "[PLAIN]", parent="root")
    mail("c1", "10 a older", parent="plain")
    mail("c2", "10 b newer", parent="plain")
    # MHS 170 control: an UNSIGNED twin is no twin; the signed 10 lives.
    mail("twins", "[TWINS]", parent="root")
    mail("t1", "10 real", parent="twins")
    mail("t2", "10 fake", parent="twins", sig="none")
    # SESH-994 control: v1 and v2 both retired by ONE v3 is one survivor.
    mail("conv", "[CONVERGE]", parent="root")
    mail("v1", "10 v1", parent="conv")
    mail("v2", "10 v2", parent="conv")
    mail("v3", "10 v3 merges both", parent="v1", sup=("v1", "v2"))
    # Two copies with ONE subject: typing the full subject still names two.
    mail("same", "[SAME]", parent="root")
    mail("m1", "10 same", parent="same")
    mail("m2", "10 same", parent="same")
    # One signed 10 and an OLDER twin whose verify times out: it may be a
    # twin, so every verb withholds the pick; none opens the signed one.
    mail("lagtwin", "[LAGTWIN]", parent="root")
    mail("l1", "10 slow older", parent="lagtwin", sig="hang")
    mail("l2", "10 signed newer", parent="lagtwin")
    if six:
        mail("b6", "10 letter v6", parent="b5", sup="b5")
    (md / "notmuch.cfg").write_text(
        "[database]\npath=%s/mail\n[new]\ntags=inbox\n[maildir]\nsynchronize_flags=false\n" % md)
    return md / "notmuch.cfg"


def make_env(tmp_path_factory, six):
    root = tmp_path_factory.mktemp("six" if six else "base")
    home = root / "home"
    home.mkdir()
    verify = root / "verify"
    verify.write_text(VERIFY)
    verify.chmod(0o755)
    cfg = build(root, six)
    env = {k: v for k, v in os.environ.items() if not k.endswith("_ROOT")}
    env.update(HOME=str(home), NOTMUCH_CONFIG=str(cfg), READER_ROOT="root@" + HOST,
               CLAUDE_CODE_AGENT=AGENT, SESSION_AGENT=AGENT, SESSION_MAIL_VERIFY=str(verify),
               SESSION_MAIL_VERIFY_TIMEOUT="1")
    env.pop("CLAUDE_SESSION_ID", None)
    subprocess.run(["notmuch", "new", "--quiet"], env=env, check=True, capture_output=True)
    return env


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return make_env(tmp_path_factory, six=False)


@pytest.fixture(scope="module")
def six(tmp_path_factory):
    return make_env(tmp_path_factory, six=True)


def verb(env, name, *args):
    """Run a verb as `memory <name>` does (the session-mail-<name> shim)."""
    exe = os.path.join(MHS, "mhs-thread" if name == "mhs-thread" else "session-mail-" + name)
    p = subprocess.run([exe, *args], env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def says_fork(text):
    m = re.search(r"FORK: (\d+(?:\.\d+)*) x(\d+)", text)
    return m.group(1) if m else None


def load(name, path, env, monkeypatch):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def fetched(env, monkeypatch):
    """mhs-thread as a module, and the root thread as the walk fetches it."""
    pt = load("pt_sm698", os.path.join(MHS, "mhs-thread"), env, monkeypatch)
    pt.PRIMED = "root@" + HOST
    flats = []
    for thread in pt.show(["id:root@" + HOST], False):
        flat = []
        for top in thread:
            pt.flatten(top, 0, flat)
        flats.append(flat)
    return pt, flats


def label_index(flat, label):
    return next(j for j, (_d, m) in enumerate(flat)
                if m["headers"]["Subject"].strip() == "[%s]" % label)


# ── F1 / F2: no false FORK where one letter lives ──────────────────────────

@pytest.mark.parametrize("args", [("ls", "/BOOT"), ("tree", "/BOOT", "--depth", "1"), ("tree", "/BOOT")])
def test_f1_boot_has_no_false_fork(base, args):
    rc, out, err = verb(base, *args)
    assert rc == 0, err
    assert "FORK" not in out, out
    assert "10 letter v5" in out, out
    for old in ("v1", "v2", "v3", "v4"):
        assert "10 letter %s" % old not in out, out


@pytest.mark.parametrize("path", ["/BOOT/10", "/BOOT/latest"])
def test_f1_cat_opens_the_one_live_letter(base, path):
    rc, out, err = verb(base, "cat", path)
    assert rc == 0, err
    assert "body of 10 letter v5" in out, out


@pytest.mark.parametrize("args", [("ls", "/BOOT"), ("tree", "/BOOT", "--depth", "1"), ("tree", "/BOOT"),
                                  ("cat", "/BOOT/10"), ("cat", "/BOOT/latest")])
def test_f2_the_letter_that_replies_below_the_cut_is_the_live_one(six, args):
    rc, out, err = verb(six, *args)
    assert rc == 0, err
    assert "FORK" not in out, out
    assert "10 letter v6" in out, out
    assert "10 letter v5" not in out, out


def test_f2_boot_reads_v6_not_the_retired_v5(six):
    rc, out, err = verb(six, "mhs-thread", "--boot")
    assert rc == 0, err
    assert "body of 10 letter v6" in out, out
    assert "body of 10 letter v5" not in out, out


# ── F3 / F4: no missed FORK, no retired item shown as live ─────────────────

@pytest.mark.parametrize("args", [("ls", "/SPEC"), ("tree", "/SPEC", "--depth", "1"), ("tree", "/SPEC")])
def test_f3_spec_scenario_is_a_fork_in_every_listing(base, args):
    rc, out, err = verb(base, *args)
    assert rc == 0, err
    assert "FORK: 10 x2" in out, out
    assert "└ 10 v1" not in out, out          # retired by both twins: never live


@pytest.mark.parametrize("args", [("ls", "/SPLIT"), ("tree", "/SPLIT", "--depth", "1"), ("tree", "/SPLIT")])
def test_f4_a_fix_that_replies_to_its_item_is_still_a_twin(base, args):
    rc, out, err = verb(base, *args)
    assert rc == 0, err
    assert "FORK: 10 x2" in out, out
    assert "10 other" not in out and "10 v3 fix of v1" not in out, out


def test_f4_the_fork_line_sits_at_the_items_depth(base):
    _rc, out, _err = verb(base, "tree", "/SPLIT")
    line = next(ln for ln in out.splitlines() if "FORK" in ln)
    assert line.startswith("  └ ⊘ FORK"), out       # under [SPLIT], not under v1


# ── F5 / F6 / F7: cat, latest and --id refuse a fork, exit 2 ───────────────

@pytest.mark.parametrize("label", ["SPEC", "SPLIT", "PLAIN"])
@pytest.mark.parametrize("leaf", ["10", "latest"])
def test_f5_f6_cat_refuses_a_fork_naming_every_copy(base, label, leaf):
    rc, out, err = verb(base, "cat", "/%s/%s" % (label, leaf))
    assert rc == 2, (rc, out, err)
    assert "body of" not in out + err, out + err
    assert "REFUSED: item 10 is a FORK (MHS 110) under /%s" % label in err, err
    assert "so cat opens NEITHER" in err and "oldest first" in err, err
    named = set(re.findall(r"copy <([^@>]+)@", err))
    assert named == FORK_IDS[label], err
    # oldest first: the fixture's mails are numbered in send order
    order = re.findall(r"copy <([^@>]+)@", err)
    assert order == sorted(order, key=lambda x: (x[0], int(x[1:]))), err
    assert "--supersedes '<ID>'" in err and "UNNUMBERED" in err, err


@pytest.mark.parametrize("label", ["SPEC", "SPLIT", "PLAIN"])
def test_f7_cat_id_refuses_and_prints_no_message_id(base, label):
    rc, out, err = verb(base, "cat", "/%s/10" % label, "--resolve")
    assert rc == 2, (rc, out, err)
    assert "<" not in out, out


def test_a_deeper_path_through_a_fork_refuses_too(base):
    rc, out, err = verb(base, "cat", "/PLAIN/10/anything")
    assert rc == 2, (rc, out, err)
    assert "under /PLAIN" in err, err


def test_a_typed_full_subject_opens_the_copy_it_names(base):
    """Naming one copy by its whole subject is not a pick: it is how an owner
    reads each copy before retiring one."""
    rc, out, err = verb(base, "cat", "/PLAIN/10 a older")
    assert rc == 0, err
    assert "body of 10 a older" in out, out


def test_a_full_subject_two_copies_share_is_still_a_fork(base):
    rc, out, err = verb(base, "cat", "/SAME/10 same")
    assert rc == 2, (rc, out, err)
    assert "REFUSED: item 10 is a FORK" in err, err


# ── F8: one answer, every verb ─────────────────────────────────────────────

@pytest.mark.parametrize("label", sorted(TRUTH))
def test_f8_every_verb_gives_the_one_answer(base, label, monkeypatch):
    truth = TRUTH[label]
    answers = {}
    for args in (("ls", "/" + label), ("tree", "/" + label, "--depth", "1"), ("tree", "/" + label)):
        rc, out, err = verb(base, *args)
        assert rc == 0, err
        answers[" ".join(args)] = says_fork(out)
    rc, out, err = verb(base, "cat", "/%s/10" % label)
    answers["cat"] = "10" if rc == 2 and "FORK" in err else None
    if truth is None:
        assert rc == 0 and "body of " + LIVE.get(label, "10 letter v5") in out, (rc, out, err)
    # run's item list, imported: resolve + list + refuse_forks, NOTHING executed
    pt, flats = fetched(base, monkeypatch)
    run = load("run_sm698", os.path.join(MHS, "session-mail-run"), base, monkeypatch)
    items = run.items_from_mail(pt, "/" + label)
    answers["run"] = next((i["num"] for i in items if i.get("fork")), None)
    # the predicate itself
    flat = flats[0]
    pairs = pt.member_pairs(flats, pt.Members(AGENT))
    forks = pt.item_forks(flat, label_index(flat, label), pairs, pt.Members(AGENT))
    answers["item_forks"] = next(iter(forks), None)
    assert set(answers.values()) == {truth}, answers


# ── F9: the predicate, in process ──────────────────────────────────────────

def test_f9_item_forks_on_each_label(base, monkeypatch):
    pt, flats = fetched(base, monkeypatch)
    flat = flats[0]
    member = pt.Members(AGENT)
    pairs = pt.member_pairs(flats, member)
    ids = lambda js: {flat[j][1]["id"].split("@")[0] for j in js}
    got = {lab: {n: ids(js) for n, js in pt.item_forks(flat, label_index(flat, lab), pairs, member).items()}
           for lab in TRUTH}
    assert got == {"BOOT": {}, "SPEC": {"10": {"s2", "s3"}}, "SPLIT": {"10": {"p2", "p3"}},
                   "PLAIN": {"10": {"c1", "c2"}}, "TWINS": {}, "CONVERGE": {},
                   "SAME": {"10": {"m1", "m2"}}}, got


def test_f9_pairs_from_a_cut_box_break_the_contract(base, monkeypatch):
    """Why the docstring says pairs MUST come from the uncut fetch: built from
    a depth-1 cut, the superseders are never read and /BOOT's three depth-1
    letters come back as twins: the live box's FORK x19, scaled down."""
    pt, flats = fetched(base, monkeypatch)
    flat = flats[0]
    member = pt.Members(AGENT)
    i = label_index(flat, "BOOT")
    d0 = flat[i][0]
    cut = [[(d, m) for d, m in flat if d <= d0 + 1]]
    bad = pt.item_forks(flat, i, pt.member_pairs(cut, member), member)
    assert {n: len(js) for n, js in bad.items()} == {"10": 3}
    assert pt.item_forks(flat, i, pt.member_pairs(flats, member), member) == {}


def test_f9_no_member_counts_every_mail(base, monkeypatch):
    """member None is a plain notmuch query: every mail is a survivor, so the
    unsigned twin counts there (and only there)."""
    pt, flats = fetched(base, monkeypatch)
    flat = flats[0]
    pairs = pt.supersede_pairs(flats)
    assert "10" in pt.item_forks(flat, label_index(flat, "TWINS"), pairs)


# ── F10 / F11: the membership rule and convergence, unchanged ──────────────

@pytest.mark.parametrize("args", [("ls", "/TWINS"), ("tree", "/TWINS"), ("cat", "/TWINS/10"),
                                  ("cat", "/TWINS/latest")])
def test_f10_an_unsigned_twin_is_no_twin(base, args):
    rc, out, err = verb(base, *args)
    assert rc == 0, err
    assert "FORK" not in out + err, out + err
    assert "10 real" in out and "body of 10 fake" not in out, out


@pytest.mark.parametrize("args", [("ls", "/CONVERGE"), ("tree", "/CONVERGE"), ("cat", "/CONVERGE/10")])
def test_f11_converged_chains_are_one_survivor(base, args):
    rc, out, err = verb(base, *args)
    assert rc == 0, err
    assert "FORK" not in out + err, out + err
    assert "10 v3 merges both" in out, out


# ── a twin whose verify timed out: withheld everywhere, never a pick ───────

def test_ls_withholds_a_number_with_a_timed_out_twin(base):
    rc, out, err = verb(base, "ls", "/LAGTWIN")
    assert rc == 0, err
    assert "10 x2 withheld — 1 UNVERIFIED (verify timed out" in out, out
    assert "10 signed newer" not in out, out


@pytest.mark.parametrize("leaf", ["10", "latest"])
def test_cat_and_latest_withhold_and_name_the_timeout(base, leaf):
    rc, out, err = verb(base, "cat", "/LAGTWIN/" + leaf)
    assert rc != 0, (rc, out, err)
    assert "body of" not in out, out
    assert "timed out" in err, err
    # one copy is UNVERIFIED, not a member: a "FORK" here would overclaim
    assert "is a FORK" not in err, err
