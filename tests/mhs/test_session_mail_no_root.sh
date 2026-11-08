#!/usr/bin/env bash
# NO ROOT YET IS THE NORMAL FIRST RUN, NOT A SHIPWRECK (SM-1023).
#
# WHAT WAS WRONG, MEASURED (sessionmail 963e307). On a fresh default seat
# (agent `claude`, no $CLAUDE_ROOT, no [ROOT] minted), `memory ls /` printed
#   memory: no $CLAUDE_ROOT in the environment — falling back to the subject
#   search (shipwreck path)
#   no canonical root found (subject:"claude ROOT")
# and exited 1, which ls's own exit table calls "usage". The shipwreck line was
# printed BEFORE the subject search ran, so it said "shipwreck" whether or not
# a root existed. Only a root that EXISTS while the hint is missing is the old
# launcher's shipwreck; no root at all is the state every new seat starts in.
#
# FIXTURE-BASED, like test_session_mail_empty_root.sh: a `notmuch` stub on
# PATH logs every call. It answers the subject search with NOTHING (no root)
# unless NM_ROOT is set, when it answers with one thread (a root found).
#
#   N1  ls /     no hint, no root -> one plain line, no "shipwreck", exit 2
#   N2  tree /   the same
#   N3  --ls     (the `ls /` line memory run's default item prints) the same
#   N4  --boot   (memory run's DEFAULT wake item) the same
#   N5  --index  the same
#   N6  cat      (path resolution) the same
#   N7  --box    a shared box with no root: the same, naming the box
#   C1  CONTROL: no hint, a root FOUND -> "shipwreck" still said, once (ls /, --ls)
#   C2  CONTROL: a set-but-empty hint still refuses (unchanged)
set -u
HERE="$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)"   # the bundled oracle
PT="$HERE/mhs-thread"
pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN" "$T/home"; LOG="$T/notmuch.log"
cat > "$BIN/notmuch" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$LOG"
case "\$*" in
  *--output=threads*) [ -n "\${NM_ROOT:-}" ] && echo "thread:00000000000000aa" ;;
  *) echo "[]" ;;
esac
STUB
chmod +x "$BIN/notmuch"

# A tmp HOME and the SAFETY env on every run: nothing here may send or regen.
run(){ # run <args...>   (NM_ROOT and CLAUDE_ROOT come from the caller's env)
  : > "$LOG"
  env -u CLAUDE_ROOT -u SHARED_ROOT HOME="$T/home" PATH="$BIN:$PATH" \
      CLAUDE_CODE_AGENT=claude SESSION_AGENT="" \
      SESSIONMAIL_SENDMAIL=drop SESSIONMAIL_VMAILBOX_GEN=/usr/bin/true \
      ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$PT" "$@" 2>"$T/err" >"$T/out"
}

no_root(){ # no_root <name> <who> <var> <args...>
  local name="$1" who="$2" var="$3"; shift 3
  echo "── $name — memory $* with no \$$var and no root"
  run "$@"; local rc=$?
  local err; err="$(cat "$T/err")"
  [ "$rc" -eq 2 ] && ok "$name exits 2 (not found)" || no "$name exits $rc, not 2:
$err"
  grep -qi 'shipwreck' "$T/err" "$T/out" && no "$name still says shipwreck:
$err" || ok "$name says no 'shipwreck'"
  [ "$(wc -l < "$T/err" | tr -d ' ')" -eq 1 ] && ok "$name prints ONE stderr line" || no "$name stderr is not one line:
$err"
  grep -q "no memory yet for $who" "$T/err" && ok "$name names the state for $who" || no "$name does not say 'no memory yet for $who':
$err"
  grep -q "\$$var" "$T/err" && ok "$name names \$$var, the variable it wanted" || no "$name does not name \$$var:
$err"
  grep -q 'memory init  *claude\|run .memory init' "$T/err" && no "$name tells a stranger to run memory init (it refuses today)" || ok "$name does not send a stranger to a step that refuses"
  grep -q -- '--output=threads' "$LOG" && ok "$name ran the subject search" || no "$name did not run the subject search"
  [ ! -s "$T/out" ] && ok "$name prints nothing on stdout" || no "$name printed on stdout:
$(cat "$T/out")"
}

no_root N1 claude CLAUDE_ROOT ls /
no_root N2 claude CLAUDE_ROOT tree /
no_root N3 claude CLAUDE_ROOT --ls
no_root N4 claude CLAUDE_ROOT --boot
no_root N5 claude CLAUDE_ROOT --index
no_root N6 claude CLAUDE_ROOT cat /BOOT/latest
no_root N7 "box shared" SHARED_ROOT ls / --box shared

for a in "ls /" "--ls"; do
  echo "── C1 — CONTROL: memory $a, no \$CLAUDE_ROOT but a root IS found: the shipwreck path, said once"
  # The stub's `show` answers nothing, so the render refuses after the search;
  # the control is the line the search half prints, not a rendered tree.
  NM_ROOT=1 run $a
  [ "$(grep -c 'shipwreck' "$T/err")" -eq 1 ] && ok "C1 $a says shipwreck once on stderr" || no "C1 $a does not say shipwreck once:
$(cat "$T/err")"
  grep -q 'no memory yet' "$T/err" && no "C1 $a says 'no memory yet' beside a found root" || ok "C1 $a does not say 'no memory yet'"
done

echo "── C2 — CONTROL: a set-but-empty \$CLAUDE_ROOT still refuses, before any search"
: > "$LOG"
env HOME="$T/home" PATH="$BIN:$PATH" CLAUDE_CODE_AGENT=claude SESSION_AGENT="" CLAUDE_ROOT='<>' \
    SESSIONMAIL_SENDMAIL=drop SESSIONMAIL_VMAILBOX_GEN=/usr/bin/true \
    ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$PT" ls / 2>"$T/err" >"$T/out"; rc=$?
grep -qi 'set but empty' "$T/err" && ok "C2 says set but empty" || no "C2 lost the set-but-empty refusal:
$(cat "$T/err")"
grep -q -- '--output=threads' "$LOG" && no "C2 ran the subject search" || ok "C2 never ran the subject search"
[ "$rc" -eq 1 ] && ok "C2 exits 1, unchanged" || no "C2 exits $rc"

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
