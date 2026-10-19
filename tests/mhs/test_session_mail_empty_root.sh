#!/usr/bin/env bash
# A ROOT HINT THAT IS SET BUT EMPTY MUST REFUSE, NOT PASS AS PRIMED.
#
# WHAT WAS WRONG, MEASURED. With EVE_ROOT='<>',
# `memory ls /` fell back to the subject search (the shipwreck path), and the
# header then printed `$EVE_ROOT = thread:… · [ROOT] from eve ✓`: a tick for a
# hint that was never used. Unset, the same call says "shipwreck" aloud. A set
# but empty hint is a launcher that TRIED and failed, so it is refused (MHS
# 90: prime or refuse loudly; never render a tree the hint did not prime).
#
# FIXTURE-BASED, like test_session_mail_label_path.sh: a `notmuch` stub on
# PATH logs every call and answers the shipwreck search with one thread, so
# the probes see what the reader asked for without a mail store.
#
#   P1  EVE-style '<>'   -> exits non-zero, says "set but empty", no search
#   P2  whitespace '  '  -> the same
#   P3  '< >'            -> the same
#   C1  CONTROL: unset   -> the shipwreck path, announced (unchanged)
set -u
HERE="$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)"   # the bundled oracle
LS="$HERE/session-mail-ls"
pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"; LOG="$T/notmuch.log"
cat > "$BIN/notmuch" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$LOG"
case "\$*" in
  *--output=threads*) echo "thread:00000000000000aa" ;;
  *) echo "[]" ;;
esac
STUB
chmod +x "$BIN/notmuch"

run(){ # run <READER_ROOT value | __UNSET__> <args...>
  local v="$1"; shift; : > "$LOG"
  if [ "$v" = "__UNSET__" ]; then
    env -u READER_ROOT PATH="$BIN:$PATH" CLAUDE_CODE_AGENT=reader SESSION_AGENT="" \
        bash "$LS" "$@" 2>&1
  else
    env PATH="$BIN:$PATH" CLAUDE_CODE_AGENT=reader SESSION_AGENT="" READER_ROOT="$v" \
        bash "$LS" "$@" 2>&1
  fi
}

for probe in "P1:<>" "P2:  " "P3:< >"; do
  name="${probe%%:*}"; val="${probe#*:}"
  echo "── $name — READER_ROOT='$val'"
  out="$(run "$val" /)"; rc=$?
  [ "$rc" -ne 0 ] && ok "$name exits non-zero ($rc)" || no "$name exits 0:
$out"
  grep -qi 'set but empty' <<<"$out" && ok "$name says the hint is set but empty" || no "$name does not say 'set but empty':
$out"
  grep -q -- '--output=threads' "$LOG" && no "$name ran the subject search anyway" || ok "$name never ran the subject search"
  grep -q '✓' <<<"$out" && no "$name printed a priming tick" || ok "$name printed no tick"
done

echo "── C1 — CONTROL: READER_ROOT unset takes the announced shipwreck path"
out="$(run __UNSET__ /)"; rc=$?
grep -q 'shipwreck' <<<"$out" && ok "C1 announces the shipwreck path" || no "C1 does not announce shipwreck:
$out"
grep -q -- '--output=threads' "$LOG" && ok "C1 ran the subject search" || no "C1 did not run the subject search"

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
