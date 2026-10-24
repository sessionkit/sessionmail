"""SM-827 D1: a [ROOT] admitted by its HINT, not a signature, shows its leaf under a label.

MHS 90 primes the root from `$<AGENT>_ROOT` and vets it by Subject and From alone, so
`is_anchor` admits the primed root with no signature check: an UNSIGNED root, or a v1
root outside the v1 shape (which the verifier refuses since SM-822), is a supported
state. fix-001 first gave every body under an owner `signed_body`, so such a root
was WITHHELD: `memory cat /` exited 3 saying "signature refused" when the root never
had one, and `tree / -b` and `--index -b` printed `⊘ body withheld` in place of it
(the re-review's D1, reviews/2030-12-08T1557-fix-001/REPORT.md in the SM-827 task).

THE RULING (SM-827 D1 (b): Python leaf, unsigned label, no exit 3): a
hint-admitted NON-member root shows the file's leaf as PYTHON reads it (`_sig.signed_text(check=False)`), never notmuch's body; it stays OUT of
`render.withheld` (no exit 3, no "signature refused"); and ONE label line, the same in
`cat`, `tree -b` and `--index -b`, says it carries no signature claim. Every other byte
of the output is what the tree before fix-001 printed.

THE CASES, each a memory home whose [ROOT] is:
- `signed-root`: the honest control, signed v2. Byte-identical, no label.
- `unsigned-root`: the same root with its X-Session-Signature removed.
- `v1-offshape-root`: a v1-signed root with one parameter the v1 sender never wrote
  (`format=flowed`, SM-822's `extra-param`): the verifier refuses it (v1-shape).
- `unsigned-root-crsub-top`: an unsigned root whose blank line after the headers ends
  in CR LF where it ended in LF (SM-823's Class B `single-b-crsub-top`). notmuch's JSON
  gives no body for it, and Python's leaf is the text: the case that tells the two
  readings apart.

THE CONSUMERS: `memory cat /`, `cat / --bodies` (`--full`), `tree / -b`, `--index -b`.
GOLDENS: tests/fixtures/sm827_hint_root/golden.json, recorded from 4427df2 (the tree
before fix-001; see its README), both interpreters agreeing.

Both interpreters (PATH's python3 and /usr/bin/python3), real notmuch, scratch homes.

Run:    uv run pytest -q tests/test_memory_hint_root.py
Record: uv run python tests/test_memory_hint_root.py --record PKG_DIR
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

import sm827_rig as R
from sessionmail import _sig

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "fixtures" / "sm827_hint_root" / "golden.json"

pytestmark = pytest.mark.skipif(not (R.NOTMUCH and shutil.which("ssh-keygen")),
                                reason="needs notmuch and ssh-keygen")

ROOT_BODY = b"alice's memory\n"
CONSUMERS = {
    "cat": ("mhs-thread", "cat", "/"),
    "cat_full": ("mhs-thread", "cat", "/", "--bodies"),
    "tree_b": ("mhs-thread", "tree", "/", "-b"),
    "index_b": ("mhs-thread", "--index", "-b"),
}
#: The label each non-member root gets: the reason it is not a member, said plainly.
LABEL = {
    "unsigned-root": "(root admitted by its hint; unsigned)",
    "v1-offshape-root": "(root admitted by its hint; signature does not verify)",
    "unsigned-root-crsub-top": "(root admitted by its hint; unsigned)",
}
#: The cases whose output must be the old one, the label line aside.
SAME_BUT_LABEL = ("unsigned-root", "v1-offshape-root")
TOK = re.compile(r"≈\d+ tok")


# ── the roots ────────────────────────────────────────────────────────────────

def v1_offshape_root(rig_dir: Path) -> bytes:
    """A v1-signed [ROOT], from alice to alice, as SM-822's `single()` builds a v1 mail,
    but with `; format=flowed` on its Content-Type: a parameter the v1 sender never wrote
    (test_verify_v1_shape's `extra-param`), so the verifier refuses it as v1-shape."""
    import test_verify_v1_shape as m
    rig_dir.mkdir(parents=True, exist_ok=True)
    rig = m.Rig(rig_dir)
    subject, mid = "[ROOT]", "<root@sm827.invalid>"
    head = (f"From: {R.A}\nTo: {R.A}\nDate: {m.DATE}\nSubject: {subject}\nMessage-ID: {mid}\n"
            "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8; format=flowed\n"
            "Content-Transfer-Encoding: 8bit\n").encode()
    return head + rig.sign(R.A, R.A, subject, m.DATE, mid, ROOT_BODY).encode() + b"\n\n" + ROOT_BODY


def crsub_top(raw: bytes) -> bytes:
    """The LF of the blank line after RAW's header block made CR LF (test_memory_signed_body's
    sep_edit(None, "crsub"), said here so this file does not import the attack rows)."""
    i = raw.index(b"\n\n")
    return raw[:i] + b"\r\n" + raw[i + 2:]


def fixtures(root: Path) -> dict[str, dict[str, bytes]]:
    """{case: mails}: the rig's honest memory set, its [ROOT] swapped per case."""
    key = R.Key(root / "key")
    base = R.memory_set(key, b"echo hi\n", None, "1 echo hi")
    unsigned = R.v2_unsigned(base["root"])
    return {
        "signed-root": base,
        "unsigned-root": {**base, "root": unsigned},
        "v1-offshape-root": {**base, "root": v1_offshape_root(root / "v1rig")},
        "unsigned-root-crsub-top": {**base, "root": crsub_top(unsigned)},
    }


# ── the readings ─────────────────────────────────────────────────────────────

def read(tree: R.Tree, home: Path, root: Path, python: str) -> dict:
    env = R.env_for(home, python)
    out = {}
    for k, argv in CONSUMERS.items():
        r = tree.run(env, *argv)
        out[k] = {"rc": r["rc"], "out": r["out"].replace(str(root), "<SCRATCH>"),
                  "err": r["err"].replace(str(root), "<SCRATCH>")}
    return out


def record(src: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sm827-hint-golden-") as td:
        root = Path(td).resolve()
        tree = R.Tree(root / "tree", src=src)
        golden = {}
        for case, mails in fixtures(root).items():
            home = root / "homes" / case
            R.make_home(home, mails)
            per = {pid: read(tree, home, root, py) for pid, py in R.pythons()}
            first = next(iter(per.values()))
            assert all(v == first for v in per.values()), f"{case}: the interpreters differ"
            golden[case] = first
        return golden


def unlabel(out: str, label: str) -> str:
    """OUT with the label's one line removed and the token estimate masked (the label's
    own characters move it): what must equal the old output."""
    return TOK.sub("≈N tok", out.replace("  " + label + "\n", "", 1))


# ── the test ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def hint(tmp_path_factory):
    root = tmp_path_factory.mktemp("sm827d1").resolve()
    homes, files = {}, {}
    for case, mails in fixtures(root).items():
        home = root / "homes" / case
        files[case] = R.make_home(home, mails)
        homes[case] = home
    return {"root": root, "homes": homes, "files": files, "tree": R.Tree(root / "tree"),
            # SM-762: the goldens AS FENCED (sm827_rig.fenced). Only a MEMBER's cat footer
            # gains the badge: a root admitted by its hint alone is no member.
            "golden": {c: R.fenced(v, member_cat=(c == "signed-root"))
                       for c, v in json.loads(GOLDEN.read_text()).items()}}


PYTHONS = R.pythons()
PY = pytest.mark.parametrize("pid,py", PYTHONS, ids=[p for p, _ in PYTHONS])
#: A row that only runs the READERS takes one interpreter (sm827_rig.reader_pythons): the
#: readers run on the package's Python since SM-878; a reader on 3.9 is refused by design
#: (the guard), which test_bundled_interpreter asserts. Rows that also run the verifier
#: by hand keep PY.
READER_PYTHONS = R.reader_pythons()
RPY = pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])


def test_the_cases():
    golden = json.loads(GOLDEN.read_text())
    assert sorted(golden) == sorted(["signed-root", *LABEL])
    assert all(sorted(v) == sorted(CONSUMERS) for v in golden.values())


@PY
@pytest.mark.parametrize("case", ["signed-root", *LABEL])
def test_the_root_is_what_the_case_says(hint, case, pid, py):
    """The precondition: only the control's root verifies, so each other case really is
    a root admitted by its hint alone, and before fix-001 every consumer printed it rc 0."""
    env = R.env_for(hint["homes"][case], py)
    rc = hint["tree"].verify(env, hint["files"][case]["root"])
    assert (rc == 0) == (case == "signed-root"), (case, rc)
    for k, want in hint["golden"][case].items():
        assert want["rc"] == 0, (case, k, want)


@RPY
def test_a_signed_root_is_byte_identical_and_unlabelled(hint, pid, py):
    got = read(hint["tree"], hint["homes"]["signed-root"], hint["root"], py)
    assert got == hint["golden"]["signed-root"]
    assert all("admitted by its hint" not in v["out"] for v in got.values())


@RPY
@pytest.mark.parametrize("case", SAME_BUT_LABEL)
def test_a_hint_root_shows_its_body_under_one_label(hint, case, pid, py):
    """The ruling, on an honest root: rc 0, nothing withheld, ONE label line right under
    [ROOT] (the same words in all four consumers), and otherwise the old output."""
    got = read(hint["tree"], hint["homes"][case], hint["root"], py)
    want = hint["golden"][case]
    label = LABEL[case]
    for k, g in got.items():
        lines = g["out"].split("\n")
        assert g["rc"] == 0, (k, g)
        assert lines[:3] == ["[ROOT]", "  " + label, "  alice's memory"], (k, g["out"])
        assert g["out"].count("admitted by its hint") == 1, (k, g["out"])
        assert "withheld" not in g["out"] + g["err"] and "signature refused" not in g["err"], (k, g)
        assert unlabel(g["out"], label) == TOK.sub("≈N tok", want[k]["out"]), k
        assert g["err"] == want[k]["err"], k


@RPY
def test_cat_slash_exits_0_on_an_unsigned_root(hint, pid, py):
    """`memory cat /` is how a seat reads its root: an unsigned root is no refusal."""
    env = R.env_for(hint["homes"]["unsigned-root"], py)
    r = hint["tree"].run(env, "mhs-thread", "cat", "/")
    assert r["rc"] == 0, r
    assert r["out"].startswith("[ROOT]\n  (root admitted by its hint; unsigned)\n  alice's memory\n"), r
    assert "signature refused" not in r["err"], r


@RPY
def test_a_hint_root_body_is_pythons_leaf_never_notmuchs(hint, pid, py):
    """Where the two readings differ, the root shows Python's: notmuch's JSON has no body
    for a header block ended by CR LF (the old output printed none), and the leaf
    `_sig.signed_text(check=False)` reads is the text."""
    case = "unsigned-root-crsub-top"
    raw = hint["files"][case]["root"].read_bytes()
    text, why = _sig.signed_text(raw, check=False)
    assert (text, why) == (ROOT_BODY.decode(), "")
    got = read(hint["tree"], hint["homes"][case], hint["root"], py)
    want = hint["golden"][case]
    for k, g in got.items():
        assert "alice's memory" not in want[k]["out"], (k, want[k])     # notmuch: no text
        assert g["rc"] == 0, (k, g)
        assert g["out"].split("\n")[:3] == ["[ROOT]", "  " + LABEL[case], "  alice's memory"], (k, g["out"])


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--record":
        sys.exit(__doc__)
    pkg = Path(sys.argv[2]).resolve()
    assert (pkg / "mhs" / "mhs-thread").exists(), f"{pkg} is not a sessionmail package dir"
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(record(pkg), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
