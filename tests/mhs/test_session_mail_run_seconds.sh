#!/usr/bin/env bash
# `memory run` SAYS HOW LONG EACH ITEM AND THE WHOLE RUN TOOK (SM-733 R2-R4).
#
# Does the output tell you how long each thing took? It did not: each
# section header carried tokens only.
#
#   S1  each section header reads `(≈N tok, S.S s)`; a `sleep 1` item shows
#       >= 0.9 s, so the number is measured, not a constant
#   S2  `, truncated` is kept after the seconds when the budget cuts an item
#   S3  the run total carries seconds: `run total ≈N tok (...), S.S s`
#   S4  --json: every section has `elapsed_s` and the run has `total_s`,
#       all non-negative numbers (not strings, not booleans)
#   S5  total_s is measured from the START of the resolve (R3): the stub
#       `notmuch` sleeps 1 s before failing, so total_s exceeds the items'
#       own seconds by >= 0.9 s
#
# Hermetic, like test_session_mail_run_timeout.sh: a `notmuch` stub that
# fails, so /BOOT/WAKE is unreachable and the run takes its toml fallback at
# $HOME/.reader/wake.toml with HOME=$T. Nothing outlives the run.
#
# Run directly:
#   bash tests/mhs/test_session_mail_run_seconds.sh
# MHS_ORACLE_DIR=DIR runs it against another copy of the oracle (e.g. a
# base commit's, to see it fail there).
set -uo pipefail
HERE="${MHS_ORACLE_DIR:-$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)}"
RUN="$HERE/session-mail-run"
# SM-780: run is OFF by default. This suite runs the executor on its own
# sandboxed fixtures (HOME=$T), on purpose, so it opts in here and nowhere else.
export SESSIONMAIL_MEMORY_RUN=1

pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }

T="$(mktemp -d)"
trap 'rm -rf "${T:?}"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"

# The resolve's cost, made measurable: every notmuch call takes 1 s, then fails.
cat > "$BIN/notmuch" <<'STUB'
#!/usr/bin/env bash
sleep 1
exit 1
STUB
chmod +x "$BIN/notmuch"

mkdir -p "$T/.reader"
cat > "$T/.reader/wake.toml" <<'TOML'
[section.slow]
order = 10
command = "sleep 1; echo slow-item-done"

[section.fast]
order = 20
command = "echo fast-item-done"

[section.long]
order = 30
budget = 5
command = "seq 1 2000"
TOML

run(){ env PATH="$BIN:$PATH" HOME="$T" CLAUDE_CODE_AGENT=reader SESSION_AGENT="" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE "$@" 2>&1; }

echo "── S1-S3 — the text output"
out="$(run)"; rc=$?
[ "$rc" -eq 0 ] && ok "exit 0" || no "exit $rc, want 0:
$out"
grep -qF "SHIPWRECK fallback" <<<"$out" && ok "the run took the toml fallback, as staged" \
  || no "the run did not take the toml fallback:
$out"

# The three headers, in order, each with seconds.
heads="$(grep -E '^═══ [0-9]+  \$ ' <<<"$out")"
n="$(grep -cE '\(≈[0-9]+ tok, [0-9]+\.[0-9] s(, truncated)?\) ═══$' <<<"$heads")"
[ "$n" -eq 3 ] && ok "S1 all 3 headers read (≈N tok, S.S s)" || no "S1 $n of 3 headers carry seconds:
$heads"
slow="$(sed -nE 's/^═══ 10  .*\(≈[0-9]+ tok, ([0-9]+\.[0-9]) s\) ═══$/\1/p' <<<"$heads")"
python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= 0.9 else 1)' "${slow:-x}" 2>/dev/null \
  && ok "S1 the sleep-1 item's header says ${slow} s (>= 0.9)" || no "S1 the sleep-1 item's header seconds are [${slow}]:
$heads"
grep -qE '^═══ 30  \$ seq 1 2000  \(≈5 tok, [0-9]+\.[0-9] s, truncated\) ═══$' <<<"$heads" \
  && ok "S2 a cut item keeps ', truncated' after the seconds" || no "S2 no '(≈5 tok, S.S s, truncated)' header:
$heads"
grep -qE '^═══ run total ≈[0-9]+ tok \((tokens|bytes/4)\), [0-9]+\.[0-9] s · 3 items · ' <<<"$out" \
  && ok "S3 the run total carries seconds" || no "S3 the run total has no seconds:
$(grep 'run total' <<<"$out")"

echo "── S4-S5 — --json"
js="$(run --json)"; rc=$?
[ "$rc" -eq 0 ] && ok "--json exit 0" || no "--json exit $rc, want 0:
$js"
check(){ python3 -c "$1" <<<"$js" 2>&1; }
num='def num(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0'
if r="$(check "import json, sys
$num
d = json.loads(sys.stdin.read())
bad = [s['num'] for s in d['sections'] if not num(s.get('elapsed_s'))]
assert len(d['sections']) == 3 and not bad, ('sections without a non-negative elapsed_s', bad, d['sections'])
assert num(d.get('total_s')), ('total_s', d.get('total_s'))")"; then
  ok "S4 every section has elapsed_s, the run has total_s: non-negative numbers"
else no "S4 $r"; fi
if r="$(check "import json, sys
d = json.loads(sys.stdin.read())
e = {s['num']: s['elapsed_s'] for s in d['sections']}
assert e['10'] >= 0.9, ('the sleep-1 item', e)")"; then
  ok "S4 the sleep-1 section's elapsed_s is >= 0.9"
else no "S4 $r"; fi
if r="$(check "import json, sys
d = json.loads(sys.stdin.read())
items = sum(s['elapsed_s'] for s in d['sections'])
assert d['total_s'] - items >= 0.9, ('total_s', d['total_s'], 'items', items)")"; then
  ok "S5 total_s includes the 1 s resolve (measured from the start of items_from_mail)"
else no "S5 $r"; fi

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
