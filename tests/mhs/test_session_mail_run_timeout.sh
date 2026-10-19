#!/usr/bin/env bash
# `memory run` KILLS A TIMED-OUT ITEM'S WHOLE PROCESS GROUP (SM-733).
#
# WHAT WAS WRONG, MEASURED: each item ran
# as `subprocess.run(["sh", "-c", cmd], timeout=20)`. On the timeout Python
# killed only that `sh`; the pipeline under it (`memory tree` -> `mhs-thread`
# -> `notmuch show` of a 16 MB thread) was re-parented to launchd and ran on
# beside the next items: pid 40061, PPID 1, 15 processes below it, load 101.
#
#   T1  an item `sleep 60 | cat` with a 1 s timeout leaves NO process behind
#       (`pgrep -f` on a marker unique to this run is empty afterwards)
#   T2  its section still says `(failed rc=124) timeout`
#   T3  the run goes on to the next item, and that item runs
#   T4  the run returns in seconds, not after the 60 s sleep
#
# The marker is in the PROGRAM NAMES (symlinks `sleep-<marker>`,
# `cat-<marker>`), so `pgrep -f` sees the pipeline's own children, not just
# the `sh -c` line above them. It holds this script's pid and a random
# number, so it can match only processes this run started.
#
# Hermetic: a `notmuch` stub that fails, so /BOOT/WAKE is unreachable and
# the run takes its toml fallback: HOME=$T, so the default
# ~/.reader/wake.toml (an absolute `--toml` would read as a second PATH).
# The timeout comes from SESSION_MAIL_RUN_TIMEOUT (default 20).
#
# Cleanup is in the EXIT trap, BY PID: only pids `pgrep -f <marker>` returns
# (they can only be ours), never a pattern kill (TM-5742).
#
# Run directly:
#   bash tests/mhs/test_session_mail_run_timeout.sh
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
has(){ grep -qF -- "$1" <<<"$2"; }

M="sm733-$$-$RANDOM$RANDOM"
T="$(mktemp -d)"
cleanup(){
  local pid
  for pid in $(pgrep -f -- "$M"); do
    kill -KILL "$pid" 2>/dev/null
  done
  rm -rf "${T:?}"
}
trap cleanup EXIT
BIN="$T/bin"; mkdir -p "$BIN"

ln -s "$(command -v sleep)" "$BIN/sleep-$M"
ln -s "$(command -v cat)" "$BIN/cat-$M"
cat > "$BIN/notmuch" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$BIN/notmuch"

mkdir -p "$T/.reader"
cat > "$T/.reader/wake.toml" <<TOML
[section.slow]
order = 10
command = "$BIN/sleep-$M 60 | $BIN/cat-$M"

[section.next]
order = 20
command = "echo next-item-ran"
TOML

alive(){ pgrep -f -- "$M"; }

echo "── T1-T4 — a 1 s timeout on \`sleep 60 | cat\`"
start=$SECONDS
out="$(env PATH="$BIN:$PATH" HOME="$T" CLAUDE_CODE_AGENT=reader SESSION_AGENT="" \
        SESSION_MAIL_RUN_TIMEOUT=1 ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE 2>&1)"; rc=$?
took=$((SECONDS - start))

# The kill is SIGKILL; give the kernel a moment to tear the group down, far
# less than the 60 s a survivor would live.
left=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  left="$(alive)"
  [ -z "$left" ] && break
  sleep 0.3
done
[ -z "$left" ] && ok "T1 no process left behind (pgrep -f $M is empty)" \
  || no "T1 left behind: $(ps -o pid=,ppid=,pgid=,command= -p "$(tr '\n' ',' <<<"$left" | sed 's/,$//')")"

has "(failed rc=124) timeout" "$out" && ok "T2 the slow item's section says (failed rc=124) timeout" \
  || no "T2 no rc=124 timeout line:
$out"
has "SHIPWRECK fallback" "$out" && ok "T2 (the run took the toml fallback, as staged)" \
  || no "T2 the run did not take the toml fallback:
$out"
has "next-item-ran" "$out" && ok "T3 the next item ran" || no "T3 the next item did not run:
$out"
[ "$rc" -eq 0 ] && ok "T3 exit 0" || no "T3 exit $rc, want 0:
$out"
[ "$took" -lt 30 ] && ok "T4 the run returned in ${took}s (< 30 s)" || no "T4 the run took ${took}s"

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
