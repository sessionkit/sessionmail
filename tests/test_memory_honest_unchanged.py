"""SM-827: for HONEST mail, what memory shows is byte-identical to before, but for one thing.

The readers now take a trusted item's body from `_sig.signed_text` (the verifier's parse)
instead of notmuch's JSON or `get_body`. On honest mail that must change nothing, and this
pins it: 44 honest fixtures, each read by every changed consumer, compared with a GOLDEN
recorded from the tree BEFORE the fix (tests/fixtures/sm827_honest/, see its README).

THE FIXTURES (SM-827 findings/rca-001/prove.py):
- 26 v2 mails from the sender's own `_sender.build`: 13 bodies, each single-part and as a
  multipart/alternative (ascii; utf-8 with an emoji; a line over 998 octets, which goes
  quoted-printable; CRLF; a lone CR; INVALID UTF-8; edge whitespace; `x`; empty; a script;
  markdown with a fence; FF and U+2028; no final LF);
- 18 v1 mails: SM-822's `honest()` (tests/test_verify_v1_shape.py), its builders run
  unchanged but for the names (its example names become alice) and a thread hook. Three
  real wake shapes are among them (`wake-*`).

THE CONSUMERS: memory cat, cat --full, mhs-thread tree -b, --boot, run --list (no
registry: the listing prints every command, and nothing key-dependent), and the memory
wake shelf (its BOOT section, compared with NOTHING masked). Elsewhere only the scratch
paths are masked.

THE ONE CHANGE (decision 2): a body that is not valid UTF-8 now reads U+FFFD in
the readers that used notmuch, which DROPPED those bytes; the shelf already showed U+FFFD.
The 2 fixtures that carry it are asserted on their own.

SM-762 (the fence): no reader says "sig OK" any more. The goldens predate it, so each is
compared AS FENCED (sm827_rig.fenced): the shelf and --boot badge reads "signature
verifies · signer NOT pinned", the shelf's cost moves by that badge's length, and cat's
footer gains the same words. Every other byte is still the golden's.

Both interpreters (PATH's python3 and /usr/bin/python3), real notmuch, scratch everything.

Run:    uv run pytest -q tests/test_memory_honest_unchanged.py
Record: uv run python tests/test_memory_honest_unchanged.py --record PKG_DIR
        (PKG_DIR: a `sessionmail/` package dir from the commit to record, e.g. extracted
        with `git archive`; it rewrites tests/fixtures/sm827_honest/golden.json)
"""
from __future__ import annotations

import concurrent.futures as cf
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

import sm827_rig as R

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "fixtures" / "sm827_honest" / "golden.json"
SUBJECT = "1 pay alice"

pytestmark = pytest.mark.skipif(not (R.NOTMUCH and shutil.which("ssh-keygen")),
                                reason="needs notmuch and ssh-keygen")

V2_BODIES = {
    "ascii": b"Pay Alice 100 dollars.\nThanks, Bob.\n",
    "utf8": "Pay Alïce 100 € — thanks 🎉\nnaïve café\n".encode(),
    "qp-long": b"echo " + b"a" * 1200 + b"\nsecond line\n",
    "crlf": b"line one\r\nline two\r\n",
    "lone-cr": b"before\rafter\nend\n",
    "bad-utf8": b"caf\xe9 \xff\xfe bytes\nok line\n",
    "edge-ws": b"\n\n   indented first\n\ttabbed\ntrailing spaces   \n\n\n",
    "x": b"x\n",
    "empty": b"",
    "script": b"echo one\necho two\n# a comment\n",
    "markdown": "# Title\n\n- item one\n\n```sh\necho hi\n```\n".encode(),
    "ff-u2028": "a\x0cb c\n".encode(),
    "no-final-lf": b"no newline at the end",
}
#: The fixtures where invalid UTF-8 now reads U+FFFD (decision 2): notmuch dropped the bytes.
BAD_UTF8 = ("v2-single-bad-utf8", "v2-alt-bad-utf8")
DROPPED, REPLACED = "caf  bytes", "caf� �� bytes"
CHECKS = ("cat", "cat_full", "tree_b", "boot", "run_list", "shelf")
#: SM-771 (a script-mail's subject never executes): an item whose body is a placeholder
#: (empty or `x`) has no program, so `run --list` names its subject as documentation and
#: never shows it as the command. The goldens predate it (recorded at 4427df2), so these
#: cases' run_list differs by exactly that one line; test_placeholder_subject_is_not_a_command
#: asserts it, and every other check stays byte-identical.
PLACEHOLDER = ("v1-empty-body", "v2-alt-empty", "v2-alt-x", "v2-single-empty", "v2-single-x")
AS_COMMAND, AS_SUBJECT = "#   | " + SUBJECT.split(" ", 1)[1], "#   subject: " + SUBJECT.split(" ", 1)[1]
#: SM-853: the no-registry banner grew lines under its headline (what a registry is, that
#: sessionmail does not create one, and the page that says how to build one by hand). The
#: goldens predate it, and every case runs `run --list` with no registry, so each golden
#: run_list carries the ONE-line banner. The headline itself did not move: the `honest`
#: fixture swaps that one line for the lines the executor prints today, and
#: test_the_banner_is_the_only_run_list_change pins that the swap is exactly that.
#: SM-873: the settings path in that one line was rewritten in the golden, in place, to
#: the mail settings file's home (`_settings.SHOWN`); no other byte of the golden moved.
OLD_BANNER = ("# memory run: NO TRUST REGISTRY (set SESSION_REGISTRY, or \"registry\" in "
              "~/.sessionkit/mail/settings/mail.json): --trust cannot be run, so every mail item is "
              "REFUSED. Fix the environment, not the mail.\n")


def executor_banner() -> str:
    """NO_REGISTRY from the executor the test runs, as `--list` prints it.

    Imported, not run: its gate is in main(). Its top level does refuse to load with no
    agent, so the import runs under a scoped agent that is undone on return, the way
    test_memory_run_trust's `executor` fixture does it. Called only from a fixture, never
    at module import: collection must not depend on the caller's environment."""
    path = R.PKG / "mhs" / "session-mail-run"
    loader = SourceFileLoader("smr_sm853", str(path))
    mod = module_from_spec(spec_from_loader("smr_sm853", loader))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CLAUDE_CODE_AGENT", "nobody")
        loader.exec_module(mod)
    return "".join("# " + ln + "\n" for ln in mod.NO_REGISTRY.splitlines())


@pytest.fixture(scope="module")
def new_banner() -> str:
    return executor_banner()


#: SM-878: the DEFAULT item (no items under the path, no wake.toml) runs `mhs-thread --boot`
#: through `sh -c`, so its command now names the executor's own interpreter in front: by
#: its shebang alone the reader ran on PATH's python3. The goldens predate it, so each
#: DEFAULT run_list differs by that one word. `read` masks the MEASURED interpreter
#: (`interpreter`) as PYTHON_MASK, golden_878 puts the same word in the golden, and
#: test_the_interpreter_is_the_only_default_change pins that this is the whole delta.
DEFAULT_BOOT = "\n<SCRATCH>/tree/sessionmail/mhs/mhs-thread --boot\n"
PYTHON_MASK = "<PYTHON>"


def golden_878(golden: dict) -> dict:
    """The golden after SM-878: each DEFAULT run_list's boot command gains the masked
    interpreter in front. Nothing else is touched."""
    out = json.loads(json.dumps(golden))
    for case in out.values():
        case["run_list"]["out"] = case["run_list"]["out"].replace(
            DEFAULT_BOOT, f"\n{PYTHON_MASK} {DEFAULT_BOOT[1:]}")
    return out


_INTERPRETERS: dict[str, str] = {}


def interpreter(env: dict) -> str:
    """`sys.executable` as the executor reports it, quoted as it prints it: measured,
    never assumed. The rig starts the executor as `memory` does, `[sys.executable,
    script]` (sm827_rig.Tree.run), so the probe is started the same way under ENV."""
    key = env["PATH"]
    if key not in _INTERPRETERS:
        _INTERPRETERS[key] = subprocess.run(
            [sys.executable, "-c", "import shlex, sys; print(shlex.quote(sys.executable))"],
            env=env, capture_output=True, text=True, check=True).stdout.strip()
    return _INTERPRETERS[key]


def golden_now(golden: dict, new_banner: str) -> dict:
    """The golden as it reads after SM-853: each run_list's one banner line becomes the
    lines the executor prints today. Nothing else is touched."""
    out = json.loads(json.dumps(golden))
    for case in out.values():
        case["run_list"]["out"] = case["run_list"]["out"].replace(OLD_BANNER, new_banner)
    return out


def page_of(body: bytes) -> bytes:
    return b"<pre>" + body.replace(b"<", b"&lt;") + b"</pre>\n"


# ── v1: SM-822's honest() shapes, renamed and threaded ──────────────────────

def v1_shapes(rig_dir: Path) -> dict[str, tuple[dict[str, bytes], str]]:
    """{name: (mails, item_subject)}. SM-822's builders, exec'd into a namespace of their
    own with alice's names (the imported module is not touched: its own tests read it)."""
    import test_verify_v1_shape as m
    ns = dict(m.__dict__)
    ns.update(FROM=R.A, TO=R.A, MID="<v1item@sm827.invalid>")
    for fname in ("single", "alternative", "wake", "honest"):
        code = inspect.getsource(getattr(m, fname))
        if fname == "wake":
            code = (code.replace("ivan", "alice").replace("nas.example", "sm827.invalid")
                    .replace("<wake-label@", "<notes@"))
        exec(compile(code, f"<sm822 {fname}, renamed>", "exec"), ns)
    thread = b"In-Reply-To: <notes@sm827.invalid>\nReferences: <root@sm827.invalid> <notes@sm827.invalid>\n"
    single, alternative = ns["single"], ns["alternative"]

    def single_t(rig, body=m.BODY, mime=m.PLAIN, subject="s sm822", extra=b""):
        return single(rig, body=body, mime=mime, subject=SUBJECT, extra=extra + thread)

    def alt_t(rig, body=m.BODY, page=b"<p>Pay Alice 100 dollars.</p>\n", plain=m.PLAIN,
              html=b"Content-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: 8bit\n",
              container=b"Content-Transfer-Encoding: 8bit\n", tail=b""):
        return alternative(rig, body=body, page=page, plain=plain, html=html,
                           container=container + thread, tail=tail)
    ns["single"], ns["alternative"] = single_t, alt_t
    rig_dir.mkdir(parents=True, exist_ok=True)
    rig = m.Rig(rig_dir)
    return ns["honest"](rig)


def item_subject(raw: bytes) -> str:
    for line in raw.replace(b"\r\n", b"\n").split(b"\n"):
        if line.lower().startswith(b"subject: "):
            return line[9:].decode()
    raise AssertionError("no subject")


def fixtures(root: Path) -> dict[str, tuple[dict[str, bytes], str]]:
    """{case: (mails, the item's subject)}: the 44."""
    key = R.Key(root / "key")
    out = {}
    for name, body in V2_BODIES.items():
        for alt in (False, True):
            out[f"v2-{'alt' if alt else 'single'}-{name}"] = (
                R.memory_set(key, body, page_of(body) if alt else None, SUBJECT), SUBJECT)
    labels = R.labels(key)
    for name, raw in v1_shapes(root / "v1rig").items():
        # The shelf: the SAME v1 bytes, addressed by delivery (postfix prepends
        # X-Original-To, which no v1 signature covers). No [BOOT] item: the boot letter is
        # v2-only in this set, as in rca-001's.
        out[f"v1-{name}"] = ({**labels, "item": raw,
                              "shelf": b"X-Original-To: alice+boot@sm827.invalid\n" + raw},
                             item_subject(raw))
    return out


# ── the readings ─────────────────────────────────────────────────────────────

def mask(text: str, *paths: Path) -> str:
    """The ONLY normalisation outside the shelf: the scratch paths."""
    for p in paths:
        text = text.replace(str(p), "<SCRATCH>")
    return text


def read(tree: R.Tree, home: Path, root: Path, python: str, subject: str) -> dict:
    env = R.env_for(home, python)
    todo = {k: v for k, v in R.consumers("/NOTES/" + subject).items()
            if k in CHECKS or k == "wake"}
    with cf.ThreadPoolExecutor(len(todo)) as ex:
        futs = {k: ex.submit(tree.run, env, *argv) for k, argv in todo.items()}
        got = {k: f.result() for k, f in futs.items()}
    out = {}
    for k, r in got.items():
        if k == "wake":
            out["shelf"] = {"rc": r["rc"], "out": R.shelf(r["out"])}   # RAW: nothing masked
        else:
            out[k] = {"rc": r["rc"], "out": mask(r["out"], root),
                      "err": mask(r["err"], root)}
    # SM-878: the DEFAULT boot command's interpreter, masked only in that one place.
    exe = mask(interpreter(env), root)
    out["run_list"]["out"] = out["run_list"]["out"].replace(
        f"\n{exe} {DEFAULT_BOOT[1:]}", f"\n{PYTHON_MASK} {DEFAULT_BOOT[1:]}")
    return out


def record(src: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sm827-golden-") as td:
        root = Path(td).resolve()
        tree = R.Tree(root / "tree", src=src)
        golden = {}
        for case, (mails, subject) in fixtures(root).items():
            home = root / "homes" / case
            R.make_home(home, mails)
            per = {pid: read(tree, home, root, py, subject) for pid, py in R.pythons()}
            first = next(iter(per.values()))
            assert all(v == first for v in per.values()), f"{case}: the interpreters differ"
            golden[case] = first
        return golden


# ── the test ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def honest(tmp_path_factory, new_banner):
    root = tmp_path_factory.mktemp("sm827h").resolve()
    cases = fixtures(root)
    homes = {}
    for case, (mails, subject) in cases.items():
        home = root / "homes" / case
        R.make_home(home, mails)
        homes[case] = (home, subject)
    return {"root": root, "homes": homes, "tree": R.Tree(root / "tree"),
            # SM-762: the golden AS FENCED (sm827_rig.fenced), after SM-853's banner:
            # `sig OK` became the UNPINNED badge, the only other byte allowed to move.
            # SM-878: and the DEFAULT boot command names the interpreter (golden_878).
            "golden": {c: R.fenced(v) for c, v in
                       golden_878(golden_now(json.loads(GOLDEN.read_text()), new_banner)).items()}}


def test_the_set_is_the_44():
    golden = json.loads(GOLDEN.read_text())
    assert len(golden) == 44
    assert sum(c.startswith("v2-") for c in golden) == 26
    assert sum(c.startswith("v1-") for c in golden) == 18
    assert {"v1-wake-1lf-ascii-supersedes", "v1-wake-2lf-utf8", "v1-wake-0lf-no-supersedes"} <= set(golden)


def test_the_banner_load_needs_no_agent_and_leaves_the_env_as_found(monkeypatch):
    """The gate's environment, CI's and a stranger's have no agent: loading the banner must
    work there, and must not leave one behind for the tests after it."""
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.delenv("SESSION_AGENT", raising=False)
    before = dict(os.environ)
    banner = executor_banner()
    assert banner.startswith(OLD_BANNER), banner
    assert dict(os.environ) == before


def test_the_banner_is_the_only_run_list_change(new_banner):
    """SM-853: a golden run_list holds the old one-line banner at most once; today's
    banner starts with that same headline and adds lines under it; and swapping it is the
    whole of what golden_now does."""
    golden = json.loads(GOLDEN.read_text())
    assert new_banner.startswith(OLD_BANNER), new_banner
    assert new_banner.count("\n") > 1, "the banner grew lines under its headline"
    assert all(ln.startswith("# ") for ln in new_banner.splitlines()), new_banner
    now = golden_now(golden, new_banner)
    held = 0
    for case, want in golden.items():
        # A case whose path has no mail item (the DEFAULT listing) has no banner to swap.
        assert want["run_list"]["out"].count(OLD_BANNER) in (0, 1), case
        held += OLD_BANNER in want["run_list"]["out"]
        moved = {k for k in want if now[case][k] != want[k]}
        assert moved == ({"run_list"} if OLD_BANNER in want["run_list"]["out"] else set()), (case, moved)
        assert now[case]["run_list"]["out"].replace(new_banner, OLD_BANNER) == want["run_list"]["out"]
    assert held > len(golden) // 2, held


def test_the_interpreter_is_the_only_default_change():
    """SM-878: golden_878 moves exactly the DEFAULT run_lists, each by the one masked word
    in front of the boot command, and undoing that word gives the golden back."""
    golden = json.loads(GOLDEN.read_text())
    now = golden_878(golden)
    defaults = {c for c, v in golden.items() if DEFAULT_BOOT in v["run_list"]["out"]}
    assert len(defaults) == 6, sorted(defaults)
    for case, want in golden.items():
        moved = {k for k in want if now[case][k] != want[k]}
        assert moved == ({"run_list"} if case in defaults else set()), (case, moved)
        assert now[case]["run_list"]["out"].replace(f"{PYTHON_MASK} ", "") == want["run_list"]["out"]


CASES = sorted(json.loads(GOLDEN.read_text())) if GOLDEN.exists() else []
PYTHONS = R.pythons()
#: A row that only runs the READERS takes one interpreter (sm827_rig.reader_pythons): the
#: readers run on the package's Python since SM-878; a reader on 3.9 is refused by design
#: (the guard), which test_bundled_interpreter asserts. Rows that also run the verifier
#: by hand keep PYTHONS.
READER_PYTHONS = R.reader_pythons()


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
@pytest.mark.parametrize("case", CASES)
def test_honest_output_is_unchanged(honest, case, pid, py):
    home, subject = honest["homes"][case]
    got = read(honest["tree"], home, honest["root"], py, subject)
    want = honest["golden"][case]
    # The shelf, the boot readout every seat reads at wake: byte-identical on ALL 44,
    # invalid UTF-8 included (it already showed U+FFFD).
    assert got["shelf"] == want["shelf"], f"{case} ({pid}): the shelf moved"
    for k in CHECKS:
        if k == "shelf":
            continue
        if case in BAD_UTF8 and REPLACED in got[k]["out"]:
            continue    # test_invalid_utf8_reads_u_fffd says what changed, and only that
        if case in PLACEHOLDER and k == "run_list":
            continue    # test_placeholder_subject_is_not_a_command says what changed (SM-771)
        assert got[k] == want[k], f"{case} ({pid}): {k} moved"


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
@pytest.mark.parametrize("case", PLACEHOLDER)
def test_placeholder_subject_is_not_a_command(honest, case, pid, py):
    """SM-771: for a placeholder body the listing shows the subject as documentation, never as
    the command. That one line is the whole of the difference from the pre-SM-771 golden."""
    home, subject = honest["homes"][case]
    got = read(honest["tree"], home, honest["root"], py, subject)["run_list"]
    want = honest["golden"][case]["run_list"]
    assert AS_COMMAND in want["out"].splitlines(), want
    assert AS_COMMAND not in got["out"].splitlines(), got
    assert AS_SUBJECT in got["out"].splitlines(), got
    assert got == {**want, "out": want["out"].replace(AS_COMMAND + "\n", AS_SUBJECT + "\n")}


@pytest.mark.parametrize("pid,py", READER_PYTHONS, ids=[p for p, _ in READER_PYTHONS])
@pytest.mark.parametrize("case", BAD_UTF8)
def test_invalid_utf8_reads_u_fffd(honest, case, pid, py):
    """Decision 2, the ONE change for honest mail: where notmuch dropped the bytes that are
    not UTF-8, the readers show U+FFFD (as the shelf and `message read` already did). That
    substitution and the token estimate it moves are the whole of the difference."""
    home, subject = honest["homes"][case]
    got = read(honest["tree"], home, honest["root"], py, subject)
    want = honest["golden"][case]
    tok = re.compile(r"≈\d+ tok")
    for k in ("cat", "cat_full", "tree_b", "boot", "run_list"):
        assert DROPPED in want[k]["out"] and REPLACED not in want[k]["out"], (k, want[k])
        assert REPLACED in got[k]["out"] and DROPPED not in got[k]["out"], (k, got[k])
        back = {**got[k], "out": tok.sub("≈N tok", got[k]["out"].replace(REPLACED, DROPPED))}
        assert back == {**want[k], "out": tok.sub("≈N tok", want[k]["out"])}, k
    assert REPLACED in got["shelf"]["out"] and got["shelf"] == want["shelf"]


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--record":
        sys.exit(__doc__)
    pkg = Path(sys.argv[2]).resolve()
    assert (pkg / "mhs" / "mhs-thread").exists(), f"{pkg} is not a sessionmail package dir"
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(record(pkg), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
