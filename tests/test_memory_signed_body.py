"""SM-827: memory shows and RUNS the text the signature covers, never a second parse of it.

memory cat / cat --full, mhs-thread tree -b and --boot, memory run (--list and the
execution) and the memory wake shelf decided trust on the verifier, which hashes Python's
email parse, but took the body they showed and ran from notmuch's JSON (GMime's parse), or,
on the shelf, from `get_body` (which obeys an unsigned Content-Disposition). A keyless
edit that the two parsers read differently changed what a GOOD item showed or ran.

THE ROWS are SM-827 repro-001's 23 (findings/repro-001/RESULTS.md): 2 honest controls,
SM-823's 13 LIVE rows (its relay edits, imported from tests/test_verify_mime_ambiguous.py),
SM-823's 6 Class B rows (a part's header separator deleted, or its LF made a CR) and 2
`Content-Disposition: attachment` rows. Each edit is applied with no key to the item, the
boot letter and the shelf letter of a scratch memory home.

THE VERIFIER has SM-819's, SM-823's and SM-831's refusals switched off (sm827_rig.BYPASS;
SM-831 refuses any Content-Disposition, so the two CD rows read BAD without it), so every
row still VERIFIES and the test proves the READERS: with those refusals in, the rows read
BAD and a reader test would pass for the wrong reason. Each row's first test checks that
precondition, and that the row was a hole before this card (notmuch's body, or get_body's,
was not the signed text).

Every test runs under PATH's python3 AND /usr/bin/python3 (macOS 3.9), through a PATH
shim. `memory run` executes only echo lines, under `env -i`, in an empty scratch cwd, with
a scratch key and registry. Real notmuch; skipped where it or ssh-keygen is absent.

Run: uv run pytest -q tests/test_memory_signed_body.py
"""
from __future__ import annotations

import concurrent.futures as cf
import email
import email.policy
import json
import shutil
import subprocess

import pytest

import sm827_rig as R
from sessionmail import _sig
from test_verify_mime_ambiguous import LEAF_HTML, LEAF_PLAIN, LIVE, sub

pytestmark = pytest.mark.skipif(not (R.NOTMUCH and shutil.which("ssh-keygen")),
                                reason="needs notmuch and ssh-keygen")

#: The run script: echo lines only; non-ASCII on line 1 so a charset swap shows (repro-001).
SCRIPT = "echo SIGNED-LINE-1 Alïce €\n\n---\n\necho SIGNED-LINE-2\n".encode()
SCRIPT_PAGE = "<p>echo SIGNED-LINE-1 Alïce €</p>\n<hr>\n<p>echo SIGNED-LINE-2</p>\n".encode()
SIGNED = R.norm(SCRIPT.decode())
ECHOED = ["SIGNED-LINE-1 Alïce €", "SIGNED-LINE-2"]
SUBJECT = "1 echo SUBJECT-LINE"   # if the subject ever ran, SUBJECT-LINE would echo


def sep_edit(block: bytes | None, how: str):
    """Class B: the blank line after BLOCK (a leaf's header fields), or after the TOP header
    block when BLOCK is None, DELETED (lfdel) or its LF made a CR (crsub)."""
    def f(raw: bytes) -> bytes:
        if block is None:
            i = raw.index(b"\n\n")
            return raw[:i] + (b"\n" if how == "lfdel" else b"\r\n") + raw[i + 2:]
        return sub(raw, block + b"\n", block if how == "lfdel" else block[:-1] + b"\r\n")
    return f


def leaf1(extra: bytes):
    return lambda r: sub(r, b"\n" + LEAF_PLAIN, b"\n" + LEAF_PLAIN + extra)


def top_after_ct(extra: bytes):
    ct = b"Content-Type: text/plain; charset=utf-8\n"
    return lambda r: r.replace(ct, ct + extra, 1)


#: Where the reader's OLD source was not the signed text (repro-001, at 9723d42): `notmuch`
#: = notmuch's JSON body (cat, tree -b, --boot, run), `get_body` = the shelf's.
NOTMUCH_HOLE = {n for n, _e, _w in LIVE} - {"alt-cr-before-close"} | {
    "alt-b-lfdel-leaf1", "alt-b-crsub-leaf1", "single-b-lfdel-top", "single-b-crsub-top"}
GET_BODY_HOLE = {"alt-cd-attach-leaf1", "single-cd-attach-top"}

ROWS = ([("honest-single", "single", None), ("honest-alt", "alt", None)]
        + [(n, "alt", e) for n, e, _w in LIVE]
        + [("alt-b-lfdel-leaf1", "alt", sep_edit(LEAF_PLAIN, "lfdel")),
           ("alt-b-lfdel-leaf2", "alt", sep_edit(LEAF_HTML, "lfdel")),
           ("alt-b-crsub-leaf1", "alt", sep_edit(LEAF_PLAIN, "crsub")),
           ("alt-b-crsub-leaf2", "alt", sep_edit(LEAF_HTML, "crsub")),
           ("single-b-lfdel-top", "single", sep_edit(None, "lfdel")),
           ("single-b-crsub-top", "single", sep_edit(None, "crsub")),
           ("alt-cd-attach-leaf1", "alt", leaf1(b"Content-Disposition: attachment\n")),
           ("single-cd-attach-top", "single", top_after_ct(b"Content-Disposition: attachment\n"))])
PYTHONS = R.pythons()
#: A row that only runs the READERS takes one interpreter (sm827_rig.reader_pythons): the
#: readers run on the package's Python since SM-878; a reader on 3.9 is refused by design
#: (the guard), which test_bundled_interpreter asserts. Rows that also run the verifier
#: by hand keep PYTHONS.
READER_PYTHONS = R.reader_pythons()


def test_the_rows_are_repro_001s_23():
    assert len(ROWS) == 23 and len({n for n, _b, _e in ROWS}) == 23
    assert len(NOTMUCH_HOLE) == 16 and len(GET_BODY_HOLE) == 2


# ── the homes: one per row, built once ───────────────────────────────────────

@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    root = tmp_path_factory.mktemp("sm827")
    key = R.Key(root / "key")
    bases = {"single": R.memory_set(key, SCRIPT, None, SUBJECT),
             "alt": R.memory_set(key, SCRIPT, SCRIPT_PAGE, SUBJECT)}
    return {"root": root, "key": key, "bases": bases,
            "tree": R.Tree(root / "bypassed", bypass=True), "homes": {}}


def home_of(rig, name: str):
    if name not in rig["homes"]:
        _n, base, edit = next(r for r in ROWS if r[0] == name)
        mails = rig["bases"][base] if edit is None else R.edited(rig["bases"][base], edit)
        home = rig["root"] / "homes" / name
        rig["homes"][name] = (home, R.make_home(home, mails), mails)
    return rig["homes"][name]


def run_all(tree, env, todo: dict) -> dict:
    """Each consumer of TODO ({name: argv}) at once: they only read the home."""
    with cf.ThreadPoolExecutor(len(todo)) as ex:
        futs = {k: ex.submit(tree.run, env, *argv) for k, argv in todo.items()}
        return {k: f.result() for k, f in futs.items()}


def echoed_lines(out: str) -> list[str]:
    """What a run's items ECHOED: the output lines that name a LINE marker, never the
    run's own `═══ N  $ <title>` headers, a listing's `#` lines, or a SKIPPED line."""
    return [l.strip() for l in out.splitlines()
            if "LINE" in l and not l.lstrip().startswith(("#", "|", "═", "SKIPPED"))]


def notmuch_body(env, mid: str) -> str:
    """notmuch's JSON body of MID, as body_text joined it: the readers' OLD source."""
    out = subprocess.run([R.NOTMUCH, "show", "--format=json", "--body=true", "--entire-thread=false",
                          f"id:{mid}"],
                         env=env, capture_output=True, text=True, check=True).stdout
    parts = []

    def walk(p):
        if isinstance(p, list):
            for x in p:
                walk(x)
        elif isinstance(p, dict):
            if p.get("content-type", "").startswith("text/plain") and isinstance(p.get("content"), str):
                parts.append(p["content"])
            walk(p.get("content", []))
            walk(p.get("body", []))
    walk(json.loads(out))
    return "\n".join(parts)


def get_body(raw: bytes) -> str:
    """The shelf's OLD reading: get_body(("plain",)), decoded as UTF-8."""
    part = email.message_from_bytes(raw, policy=email.policy.default).get_body(("plain",))
    return "" if part is None else (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


# ── the precondition: each row verifies, and was a hole before ───────────────

@pytest.mark.parametrize("pid,py", PYTHONS, ids=[p for p, _ in PYTHONS])
@pytest.mark.parametrize("name", [r[0] for r in ROWS])
def test_the_row_verifies_and_was_a_hole_before(rig, name, pid, py):
    home, files, mails = home_of(rig, name)
    env = R.env_for(home, py)
    for k in R.EDITED:
        assert rig["tree"].verify(env, files[k]) == 0, f"{name}: {k} does not verify ({pid})"
    if name in NOTMUCH_HOLE:
        assert R.norm(notmuch_body(env, "item@sm827.invalid")) != SIGNED, name
    else:
        assert R.norm(notmuch_body(env, "item@sm827.invalid")) == SIGNED, name
    shelf_raw = mails["shelf"].split(b"\n", 1)[1]
    assert (R.norm(get_body(shelf_raw)) != SIGNED) == (name in GET_BODY_HOLE), name


# ── the readers show the signed text ─────────────────────────────────────────

@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
@pytest.mark.parametrize("name", [r[0] for r in ROWS])
def test_memory_shows_the_signed_text(rig, name, pid, py):
    home, _files, _mails = home_of(rig, name)
    env = R.env_for(home, py, SESSION_REGISTRY=str(rig["key"].registry))
    got = run_all(rig["tree"], env, R.consumers())
    for k, r in got.items():
        assert r["rc"] == 0, f"{name} {k} ({pid}): rc {r['rc']}\n{r['out']}\n{r['err']}"
        assert "withheld" not in r["out"] + r["err"], f"{name} {k} ({pid})\n{r['out']}"
    assert R.norm(R.body_cat(got["cat"]["out"])) == SIGNED, got["cat"]["out"]
    assert R.norm(R.body_cat(got["cat_full"]["out"])) == SIGNED, got["cat_full"]["out"]
    assert R.norm(R.body_tree_b(got["tree_b"]["out"], SUBJECT)) == SIGNED, got["tree_b"]["out"]
    assert f"· {_sig.UNPINNED}]" in got["boot"]["out"]   # SM-762: never "sig OK"
    assert R.norm(R.body_boot(got["boot"]["out"])) == SIGNED, got["boot"]["out"]
    assert R.shelf_mark(got["wake"]["out"]) == _sig.UNPINNED
    assert R.norm(R.body_shelf(got["wake"]["out"])) == SIGNED, got["wake"]["out"]
    flag, cmd = R.run_list_item(got["run_list"]["out"])
    assert "NOT VERIFIED" not in flag and "REFUSED" not in flag, got["run_list"]["out"]
    assert R.norm(cmd) == SIGNED, got["run_list"]["out"]


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
@pytest.mark.parametrize("name", [r[0] for r in ROWS])
def test_memory_run_executes_the_signed_script(rig, name, pid, py):
    """ONE `memory run /NOTES` execution: echo-only, `env -i`, an empty scratch cwd, a
    scratch registry. It runs the signed lines, never the subject, never altered text."""
    home, _files, _mails = home_of(rig, name)
    cwd = rig["root"] / "cwd" / f"{name}-{pid}"
    cwd.mkdir(parents=True)
    env = R.env_for(home, py, SESSION_REGISTRY=str(rig["key"].registry),
                    SESSIONMAIL_MEMORY_RUN="1")
    r = rig["tree"].run(env, "session-mail-run", "/NOTES", cwd=cwd, envi=True)
    echoed = echoed_lines(r["out"])
    assert echoed == ECHOED, f"{name} ({pid}): rc {r['rc']}\n{r['out']}\n{r['err']}"
    assert "SUBJECT-LINE" not in "\n".join(echoed)
    assert sorted(p.name for p in cwd.iterdir()) == []


# ── a reader that cannot show the signed text fails closed ───────────────────

@pytest.fixture(scope="module")
def withheld(tmp_path_factory):
    """A verifier that says GOOD to everything (not NAMED, so the readers keep their own
    p= re-check), over a home whose item, letter and shelf were edited AFTER signing: the
    leaf no longer hashes to its p= entry. It stands in for a verifier whose reading is not
    the reader's (another interpreter, or another file): the reader must refuse, not show."""
    root = tmp_path_factory.mktemp("sm827w")
    key = R.Key(root / "key")
    mails = R.memory_set(key, SCRIPT, SCRIPT_PAGE, SUBJECT)
    mails = R.edited(mails, lambda r: sub(r, b"SIGNED-LINE-2\n\n--", b"ALTERED-LINE\n\n--"))
    home = root / "home"
    R.make_home(home, mails)
    return {"root": root, "key": key, "home": home, "tree": R.Tree(root / "yes", yes_verifier=True)}


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
def test_cat_exits_3_on_a_withheld_body(withheld, pid, py):
    """Decision 3: nothing on stdout, the withheld line on stderr, exit 3
    ("signature refused", as cat's help documents)."""
    env = R.env_for(withheld["home"], py)
    for argv in (("cat", "/NOTES/1"), ("cat", "/NOTES/1", "--bodies")):
        r = withheld["tree"].run(env, "mhs-thread", *argv)
        assert r["rc"] == 3, r
        assert r["out"] == "", r["out"]
        assert "⊘ body withheld: part 1 does not hash to its p= entry here" in r["err"]
        assert "signature refused" in r["err"] and "ALTERED" not in r["err"]


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
def test_listings_show_the_withheld_line_and_keep_going(withheld, pid, py):
    """tree -b and the wake shelf are listings: the line in place of the body, rc 0, and
    the wake goes on to its RECENT and COUNTS sections. --boot refuses, as on a bad verify."""
    env = R.env_for(withheld["home"], py, SESSION_REGISTRY=str(withheld["key"].registry))
    got = run_all(withheld["tree"], env, R.consumers())
    line = "⊘ body withheld: part 1 does not hash to its p= entry here"
    for k, r in got.items():
        assert "ALTERED" not in r["out"], f"{k} ({pid}) showed the altered text\n{r['out']}"
    assert got["tree_b"]["rc"] == 0 and line in got["tree_b"]["out"]
    assert got["wake"]["rc"] == 0 and line in R.shelf(got["wake"]["out"])
    assert "═══ RECENT" in got["wake"]["out"] and "═══ COUNTS" in got["wake"]["out"]
    assert got["boot"]["rc"] == 1 and "1 letter to self   [" not in got["boot"]["out"]
    assert "memory: REFUSING boot item — " + line in got["boot"]["err"]
    flag, cmd = R.run_list_item(got["run_list"]["out"])
    assert "NOT VERIFIED — would be skipped: has no signed text to run" in flag
    assert "ALTERED" not in cmd


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
def test_run_skips_a_withheld_item_and_says_why_once(withheld, pid, py):
    cwd = withheld["root"] / f"cwd-{pid}"
    cwd.mkdir()
    env = R.env_for(withheld["home"], py, SESSION_REGISTRY=str(withheld["key"].registry),
                    SESSIONMAIL_MEMORY_RUN="1")
    r = withheld["tree"].run(env, "session-mail-run", "/NOTES", cwd=cwd, envi=True)
    assert "SKIPPED: item 1 has no signed text to run" in r["out"], r
    assert echoed_lines(r["out"]) == [], r["out"]
    assert r["err"].count("memory run: SKIPPED item 1 (part 1 does not hash") == 1, r["err"]
    assert sorted(p.name for p in cwd.iterdir()) == []


# ── a signed item with no text/plain part ────────────────────────────────────

NO_TEXT_LINE = "(no text/plain part: nothing signed to show)"


@pytest.fixture(scope="module", params=["text/html; charset=utf-8", "application/octet-stream"])
def no_text(request, tmp_path_factory):
    """A signed v2 item, letter and shelf whose ONE leaf is not text/plain. Before this
    card, `memory run` ran the SUBJECT for it (repro-001)."""
    root = tmp_path_factory.mktemp("sm827n")
    key = R.Key(root / "key")
    ct = request.param.encode()

    def only(raw: bytes) -> bytes:
        return key.sign(raw.replace(b"Content-Type: text/plain; charset=utf-8",
                                    b"Content-Type: " + ct, 1))
    mails = R.memory_set(key, SCRIPT, None, SUBJECT)
    for k in ("item", "boot"):
        mails[k] = only(R.v2_unsigned(mails[k]))
    xo, rest = mails["shelf"].split(b"\n", 1)
    mails["shelf"] = xo + b"\n" + only(R.v2_unsigned(rest))
    home = root / "home"
    files = R.make_home(home, mails)
    return {"root": root, "key": key, "home": home, "files": files, "tree": R.Tree(root / "t"),
            "ctype": request.param}


@pytest.mark.parametrize("pid,py", PYTHONS, ids=[p for p, _ in PYTHONS])
def test_no_text_part_is_a_notice_and_never_runs(no_text, pid, py):
    env = R.env_for(no_text["home"], py, SESSION_REGISTRY=str(no_text["key"].registry))
    assert no_text["tree"].verify(env, no_text["files"]["item"]) == 0
    got = run_all(no_text["tree"], env, R.consumers())
    for k in ("cat", "cat_full", "tree_b", "boot", "wake"):
        assert got[k]["rc"] == 0, (k, got[k])
        assert NO_TEXT_LINE in got[k]["out"], (k, got[k]["out"])
    assert R.shelf_mark(got["wake"]["out"]) == _sig.UNPINNED
    flag, cmd = R.run_list_item(got["run_list"]["out"])
    assert "NOT VERIFIED — would be skipped: has no signed text to run: no-text-part" in flag
    assert "SUBJECT-LINE" not in cmd
    cwd = no_text["root"] / f"cwd-{pid}"
    cwd.mkdir()
    r = no_text["tree"].run({**env, "SESSIONMAIL_MEMORY_RUN": "1"}, "session-mail-run",
                            "/NOTES", cwd=cwd, envi=True)
    assert "SKIPPED: item 1 has no signed text to run: no-text-part" in r["out"], r
    assert echoed_lines(r["out"]) == [], r["out"]
    assert r["err"].count("memory run: SKIPPED item 1 (no-text-part)") == 1, r["err"]
    assert sorted(p.name for p in cwd.iterdir()) == []


# ── the body and the verdict read ONE file ───────────────────────────────────

@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
def test_two_files_with_one_message_id_never_show_the_other_body(tmp_path, pid, py):
    """A second file carrying the item's Message-ID, with another body and no key. notmuch
    indexes ONE message with two filenames; the verdict and the body both read the first
    existing one. Whichever sorts first, the item shows the signed text or nothing at all
    (SM-833 is the availability side), never the other file's body."""
    key = R.Key(tmp_path / "key")
    mails = R.memory_set(key, SCRIPT, None, SUBJECT)
    twin = sub(mails["item"], b"SIGNED-LINE-2", b"ALTERED-LINE")
    home = tmp_path / "home"
    R.make_home(home, mails, extra={"a-twin": twin, "zz-twin": twin})
    tree = R.Tree(tmp_path / "t")
    r = tree.run(R.env_for(home, py), "mhs-thread", "cat", "/NOTES/1")
    assert "ALTERED" not in r["out"] + r["err"], r
    if r["rc"] == 0 and "SIGNED-LINE-1" in r["out"]:
        assert R.norm(R.body_cat(r["out"])) == SIGNED


# ── a NAMED verifier ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
def test_a_named_verifier_reads_the_leaf_of_the_file(tmp_path, pid, py):
    """Under SESSION_MAIL_VERIFY the named verifier is the verifier (mhs-thread never
    second-guesses it): signed_body reads the file's leaf with check=False, and does not
    withhold it. The row is SM-823's alt-ct-dup-charset, where notmuch shows other text:
    the leaf comes from the FILE, by the verifier's parse, not from notmuch."""
    key = R.Key(tmp_path / "key")
    edit = next(e for n, e, _w in LIVE if n == "alt-ct-dup-charset")
    mails = R.edited(R.memory_set(key, SCRIPT, SCRIPT_PAGE, SUBJECT), edit)
    home = tmp_path / "home"
    R.make_home(home, mails)
    stub = tmp_path / "stub-verify"
    stub.write_text(R.YES_VERIFIER)
    stub.chmod(0o755)
    tree = R.Tree(tmp_path / "t")
    env = R.env_for(home, py, SESSION_MAIL_VERIFY=str(stub))
    assert R.norm(notmuch_body(env, "item@sm827.invalid")) != SIGNED
    r = tree.run(env, "mhs-thread", "cat", "/NOTES/1")
    assert r["rc"] == 0, r
    assert R.norm(R.body_cat(r["out"])) == SIGNED, r["out"]
