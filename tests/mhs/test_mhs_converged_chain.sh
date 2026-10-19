#!/usr/bin/env bash
# SESH-994 (a) — A CONVERGED SUPERSEDE CHAIN LISTS AND RUNS ONCE.
#
# Measured on a live wake: `session mail run
# /BOOT/WAKE --list` printed 18 items for 11 distinct ones (10 x2, 15 x3,
# 17 x3, 18 x2, 70 x2). Each duplicate was ONE survivor reached from several
# dead siblings: v1 and v2 of an item both sit under the label, both are
# superseded by v3, and effective_children() appended v3 once per dead
# sibling. A FORK (two DIFFERENT survivors under one number) must still be
# returned twice so the renderer refuses to pick; that is the second leg.
#
# Hermetic: calls effective_children() on a synthetic flat list; no maildir,
# no notmuch, no network. Exit 0 = both legs hold.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../src/sessionmail/mhs" && pwd)"   # the bundled oracle
"${SESSIONMAIL_PYTHON:-python3}" - "$HERE/mhs-thread" <<'PY'
import sys, importlib.util
from importlib.machinery import SourceFileLoader
path = sys.argv[1]
loader = SourceFileLoader("mhs", path)
spec = importlib.util.spec_from_loader("mhs", loader)
mhs = importlib.util.module_from_spec(spec); loader.exec_module(mhs)

def m(mid, subj, supersedes=None):
    h = {"Subject": subj}
    if supersedes: h["Supersedes"] = "<%s>" % supersedes
    return {"id": mid, "headers": h}

# depth-annotated flat list: label at 0; v1, v2, v3 as direct children;
# v3 supersedes BOTH v1 and v2 (a converged chain)
flat = [(0, m("L", "[WAKE]")),
        (1, m("v1", "15 old")),
        (1, m("v2", "15 older")),
        (1, m("v3", "15 live"))]
pairs = {"v1": ["v3"], "v2": ["v3"]}   # dead id -> its superseders
dead = set(pairs)
got = mhs.effective_children(flat, 0, dead, pairs)
subs = [flat[j][1]["headers"]["Subject"] for j in got]
assert subs == ["15 live"], f"leg 1 (converged chain lists once): got {subs}"
print("leg 1 ok: converged chain -> one survivor:", subs)

# a real FORK: v1 superseded by TWO different live messages -> both returned
flat2 = [(0, m("L", "[WAKE]")),
         (1, m("v1", "15 old")),
         (1, m("a", "15 fork-a")),
         (1, m("b", "15 fork-b"))]
pairs2 = {"v1": ["a", "b"]}
got2 = mhs.effective_children(flat2, 0, set(pairs2), pairs2)
subs2 = sorted(flat2[j][1]["headers"]["Subject"] for j in got2)
assert subs2 == ["15 fork-a", "15 fork-b"], f"leg 2 (a fork stays a fork): got {subs2}"
print("leg 2 ok: a real fork still returns both:", subs2)
PY
echo "PASS test_mhs_converged_chain"
