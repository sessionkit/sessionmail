"""The latency lane is real (SM-863): `make test` cannot go red on load alone.

A bound on how SOON something happens (`time.monotonic() - t0 < 0.3`) goes red
under machine load with no product change, which blocks every gate that runs
the suite. So in `tests/test_pager_listen.py` such a bound lives only in a test
marked `latency`: the default run leaves it out, and `make test-latency` runs
it alone, on a quiet host. Each one is a `<name>_latency` twin of a default-lane
test that asserts the event instead. A floor (`>=`) is not a latency bound:
load only ever makes things later.

These gates read the source, so each is shown to fail on a source that breaks
it (a gate that cannot fail proves nothing).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTEN = ROOT / "tests" / "test_pager_listen.py"
CLOCKS = {("time", "monotonic"), ("time", "time"), ("time", "perf_counter")}
MARK = "latency"


def _reads_clock(node: ast.AST, clocked: set[str]) -> bool:
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and (n.func.value.id, n.func.attr) in CLOCKS):
            return True
        if isinstance(n, ast.Name) and n.id in clocked:
            return True
    return False


def upper_bounds(fn: ast.FunctionDef) -> list[int]:
    """The lines in FN that assert a clock reading is BELOW something:
    `clock < x`, `clock <= x`, `x > clock`, `x >= clock`, chained or not. A
    name assigned from a clock delta (`took = time.monotonic() - t0`) is a
    clock reading too. `t0 = time.monotonic()` alone is a start, not a
    reading, so a bare `t0` is not counted."""
    clocked: set[str] = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)
                and _reads_clock(n.value, clocked)):
            clocked.update(t.id for t in n.targets if isinstance(t, ast.Name))
    lines = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assert):
            continue
        for cmp in (c for c in ast.walk(n.test) if isinstance(c, ast.Compare)):
            terms = [cmp.left, *cmp.comparators]
            for i, op in enumerate(cmp.ops):
                lhs, rhs = terms[i], terms[i + 1]
                if ((isinstance(op, (ast.Lt, ast.LtE)) and _reads_clock(lhs, clocked))
                        or (isinstance(op, (ast.Gt, ast.GtE)) and _reads_clock(rhs, clocked))):
                    lines.append(n.lineno)
    return sorted(set(lines))


def marked(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        d = d.func if isinstance(d, ast.Call) else d
        if isinstance(d, ast.Attribute) and d.attr == MARK:
            return True
    return False


def _tests_of(source: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def lane_faults(source: str) -> list[str]:
    """Every way SOURCE breaks the lane, one line each; [] = it holds."""
    tests = _tests_of(source)
    faults = []
    for name, fn in tests.items():
        bounds = upper_bounds(fn)
        if bounds and not marked(fn):
            faults.append(f"{name}: a latency bound at line(s) {bounds} in the default lane")
        if marked(fn):
            if not name.endswith("_latency"):
                faults.append(f"{name}: marked {MARK} but not named <twin>_latency")
                continue
            twin = name.removesuffix("_latency")
            if twin not in tests:
                faults.append(f"{name}: no default-lane twin {twin}")
            elif marked(tests[twin]):
                faults.append(f"{name}: its twin {twin} is marked {MARK} too")
            if not bounds:
                faults.append(f"{name}: marked {MARK} but bounds nothing")
    return faults


# ── the gates ───────────────────────────────────────────────────────────────

def test_no_latency_bound_in_test_pager_listen_runs_in_the_default_lane():
    assert lane_faults(LISTEN.read_text()) == []


def test_the_lane_is_not_empty_and_holds_both_named_reds():
    """The two reds of a full-suite run of 637888d under load are in the lane,
    bound and all."""
    lane = {n for n, fn in _tests_of(LISTEN.read_text()).items() if marked(fn)}
    assert {
        "test_nobody_scooped_so_it_pages_at_the_crossing_and_moves_nothing_latency",
        "test_age_is_the_delivery_so_a_stale_message_linked_now_pages_at_once_latency",
    } <= lane
    assert len(lane) == 7, sorted(lane)


def _collected(*args: str) -> tuple[list[str], str]:
    """What a pytest run with ARGS would run, from the repo root: (test ids,
    the summary line). Collection only, in a child, so this suite's own
    selection is the one measured."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                          "-p", "no:cacheprovider", *args],
                         cwd=ROOT, capture_output=True, text=True, timeout=120)
    lines = out.stdout.splitlines()
    ids = [line for line in lines if "::" in line]
    return ids, (lines[-1] if lines else out.stderr)


def test_the_default_run_leaves_the_lane_out():
    """`make test` is a bare `pytest -q`, so a bare run is what a gate gets."""
    ids, summary = _collected(str(LISTEN.relative_to(ROOT)))
    assert ids and not [i for i in ids if i.endswith("_latency")], ids
    assert "7 deselected" in summary, summary


def test_make_test_latency_runs_the_lane_and_only_the_lane():
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\ntest-latency:\n", 1)[1].split("\n\n", 1)[0]
    assert f"pytest -q -m {MARK}" in recipe
    assert "test-latency" in makefile.split(".PHONY:", 1)[1].splitlines()[0]
    ids, _ = _collected("-m", MARK, str(LISTEN.relative_to(ROOT)))
    assert len(ids) == 7 and all(i.endswith("_latency") for i in ids), ids


def test_a_latency_test_named_by_its_id_runs():
    """A by-name re-run gets the test it names, never "1 deselected" (exit 5)."""
    name = "test_age_is_the_delivery_so_a_stale_message_linked_now_pages_at_once_latency"
    rel = LISTEN.relative_to(ROOT)
    ids, _ = _collected(f"{rel}::{name}")
    assert ids == [f"{rel}::{name}"], ids
    # by an absolute path too, and beside a default-lane test it does not pull in
    ids, _ = _collected(f"{LISTEN}::{name}", str(ROOT / "tests" / "test_latency_lane.py"))
    assert sum(i.endswith("_latency") for i in ids) == 1, ids


# ── each gate can fail ──────────────────────────────────────────────────────

BROKEN = '''
import time, pytest

def test_bare_bound():
    t0 = time.monotonic()
    assert time.monotonic() - t0 < 0.3

def test_named_reading():
    t0 = time.monotonic()
    took = time.monotonic() - t0
    assert 0.1 <= took < 0.8

def test_reversed():
    t0 = time.monotonic()
    assert 0.3 > time.monotonic() - t0

def test_a_floor_is_fine():
    t0 = time.monotonic()
    assert time.monotonic() - t0 >= 0.4

@pytest.mark.latency
def test_orphan_latency():
    t0 = time.monotonic()
    assert time.monotonic() - t0 < 0.3

@pytest.mark.latency
def test_misnamed():
    t0 = time.monotonic()
    assert time.monotonic() - t0 < 0.3

def test_empty():
    pass

@pytest.mark.latency
def test_empty_latency():
    pass
'''


def test_the_lane_gate_can_fail():
    faults = lane_faults(BROKEN)
    assert faults == [
        "test_bare_bound: a latency bound at line(s) [6] in the default lane",
        "test_named_reading: a latency bound at line(s) [11] in the default lane",
        "test_reversed: a latency bound at line(s) [15] in the default lane",
        "test_orphan_latency: no default-lane twin test_orphan",
        "test_misnamed: marked latency but not named <twin>_latency",
        "test_empty_latency: marked latency but bounds nothing",
    ], faults
