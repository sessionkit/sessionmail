#!/usr/bin/env bash
# `memory run` NEVER RUNS A FORKED ITEM, AND "NOT A MEMBER" SAYS WHICH HALF FAILED.
#
# WHAT WAS WRONG, MEASURED (SM-694; reported on kit 0.1.0). A
# 3-link supersede chain under [WAKE]: 10 v1 -> 10 v2 (NOT A MEMBER) ->
# 10 v3. A non-member's Supersedes: retires nothing (MHS 170, by design),
# so v1 stays live; v3 is a member and retires v2. Two copies of item 10
# survive: a FORK. `ls` said "FORK: 10 x2 (MHS 110)", and `memory run
# /BOOT/WAKE` EXECUTED BOTH: the retired v1 ran `pager stop` at wake and
# the agent went deaf. `run` never checked for a fork (the memory spec:
# it "SHALL refuse forks"). And "NOT A MEMBER" printed only From's local
# part, so a To or signature failure showed the owner's own name.
#
#   F1-F9   flat / mixed / flat2 threading x the middle link failing on
#           From / To / signature: run executes NEITHER copy, names both
#           (Message-ID), says how to resolve, exits 2; item 05 still runs
#   F10     --list: REFUSED, neither copy's command left uncommented, exit 2
#   F11     --json: refused_forks ["10"], the section names both copies
#   R1-R3   the owner "resolves" with a NUMBERED superseder (A: under WAKE,
#           B: under v1, D: superseding the live copy): still a fork, refused
#   R4      ...with an UNNUMBERED superseder (C): resolved, v3 runs, exit 0
#   W1-W3   NOT A MEMBER names the half: From / To / signature, in `tree -a`
#           and in run's SKIPPED line
#   K1      MEMBERSHIP IS NOT WEAKENED: the non-member v2 never runs, and
#           its Supersedes still retires nothing (v1 is still a copy)
#   N1-N3   nested threading (the `--supersedes` default) x From / To /
#           signature: NOT a fork. Behaviour UNCHANGED (v1 still runs, exit
#           0), and run WARNS loudly: item 10, the non-member link, which half
#           failed, and the fix
#   N4      the warning's "retire" recipe works: an UNNUMBERED member
#           superseder retires v1, and the warning goes
#   N5      its "replace" recipe works: a NUMBERED member superseder of v1
#           runs instead of v1, with no fork and no warning
#
# Hermetic like test_session_mail_member_walk.sh: a `notmuch` stub serves
# each fixture thread as JSON, a verify stub answers from X-Fixture-Sig
# (good -> 0, forged -> 1, absent -> 2: the verifier's own codes), HOME=$T.
# Each wake body touches a marker, so the suite sees what EXECUTED.
#
# Run directly:
#   bash tests/mhs/test_session_mail_run_fork.sh
# MHS_ORACLE_DIR=DIR runs it against another copy of the oracle (e.g. a
# base commit's, to see it fail there).
set -uo pipefail
HERE="${MHS_ORACLE_DIR:-$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)}"
TREE="$HERE/session-mail-tree"
RUN="$HERE/session-mail-run"
# SM-780: run is OFF by default. This suite runs the executor on its own
# sandboxed fixtures (HOME=$T), on purpose, so it opts in here and nowhere else.
export SESSIONMAIL_MEMORY_RUN=1

pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }
has(){ grep -qF -- "$1" <<<"$2"; }

T="$(mktemp -d)"
trap 'rm -rf "${T:?}"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"

AGENT="reader"
HOSTN="host1.session-mail"
ROOTID="root@$HOSTN"

# ── the fixtures ────────────────────────────────────────────────────────────
# [ROOT] > [BOOT] > [WAKE] > 05 control, the 10 chain, and 20 stray (a
# non-member failing the same way as v2, a direct child nobody supersedes:
# run must SKIP it and say why). Fixture names are SHAPE-BAD, or RES-X for
# the resolution variants (flat shape, From failure, plus a member v4).
python3 - "$T" "$AGENT" "$HOSTN" <<'PY'
import json, os, sys
T, agent, host = sys.argv[1:4]

def build(name, shape, bad, extra=None):
    md = os.path.join(T, "mail", name); os.makedirs(md)
    os.makedirs(os.path.join(T, "marks", name))
    order = []
    def msg(local, subject, sup=None, frm=agent, to=None, sig="good", run=None):
        fn = os.path.join(md, local)
        to = to or "%s+memory@%s" % (agent, host)
        frm_h = "%s <%s@%s>" % (frm, frm, host)
        hdr = ["Message-ID: <%s@%s>" % (local, host), "From: %s" % frm_h,
               "To: %s" % to, "Subject: %s" % subject]
        if sup:
            hdr.append("Supersedes: <%s@%s>" % (sup, host))
        if sig != "none":
            hdr.append("X-Fixture-Sig: %s" % sig)
        body = ("echo %s-ran; touch %s/marks/%s/%s" % (run, T, name, run)) if run else subject
        with open(fn, "w") as fh:
            fh.write("\n".join(hdr) + "\n\n" + body + "\n")
        m = {"id": "%s@%s" % (local, host), "timestamp": 0, "filename": [fn],
             "headers": {"Subject": subject, "From": frm_h, "To": to,
                         "Date": "Sun, 09 Jun 2030 12:00:00 +0000"},
             "body": [{"id": 1, "content-type": "text/plain", "content": body}]}
        order.append(m)
        return m
    def node(m, kids=()):
        return [m, list(kids)]
    kw = {"from": dict(frm="intruder"), "to": dict(to="someone-else@%s" % host),
          "sig": dict(sig="forged")}[bad]
    # Document order is time order: v1, v2, v3 (the reported chain).
    root = msg("root", "[ROOT]"); boot = msg("boot", "[BOOT]"); wake = msg("wake", "[WAKE]")
    c05 = node(msg("w05", "05 echo control", run="c05"))
    v1 = msg("w10v1", "10 v1 retired script", run="v1")
    v2 = msg("w10v2", "10 v2 middle link", sup="w10v1", run="v2", **kw)
    v3 = msg("w10v3", "10 v3 live script", sup="w10v2", run="v3")
    stray = node(msg("w20", "20 stray", run="s20", **kw))
    if shape == "nested":     # --supersedes X alone, each time
        chain = [node(v1, [node(v2, [node(v3)])])]
    elif shape == "flat":     # -r WAKE --supersedes X, each time
        chain = [node(v1), node(v2), node(v3)]
    elif shape == "mixed":    # v2 by --supersedes alone, v3 by -r WAKE
        chain = [node(v1, [node(v2)]), node(v3)]
    elif shape == "flat2":    # v2 by -r WAKE, v3 by --supersedes alone
        chain = [node(v1), node(v2, [node(v3)])]
    wake_kids = [c05] + chain + [stray]
    if extra:                 # the 4th, MEMBER mail: an attempted resolution
        local, subject, sup, under = extra
        v4 = node(msg(local, subject, sup=sup, run="v4"))
        if under == "wake":
            wake_kids.append(v4)
        else:                 # threaded under v1 (In-Reply-To = the target)
            for n in chain:
                if n[0] is v1:
                    n[1].append(v4)
    for i, m in enumerate(order):
        m["timestamp"] = 2000 + i
    tree = node(root, [node(boot, [node(wake, wake_kids)])])
    with open(os.path.join(T, "thread-%s.json" % name), "w") as fh:
        json.dump([[tree]], fh)
    with open(os.path.join(T, "root-%s.json" % name), "w") as fh:
        json.dump([[[root, []]]], fh)

for shape in ("nested", "flat", "mixed", "flat2"):
    for bad in ("from", "to", "sig"):
        build("%s-%s" % (shape, bad), shape, bad)
build("RES-A", "flat", "from", ("w10v4", "10 v4 retire v1", "w10v1", "wake"))
build("RES-B", "flat", "from", ("w10v4", "10 v4 retire v1", "w10v1", "v1"))
build("RES-C", "flat", "from", ("w10v4", "retired", "w10v1", "v1"))
build("RES-D", "flat", "from", ("w10v4", "10 v4 replaces v3", "w10v3", "wake"))
build("NES-R", "nested", "from", ("w10v4", "retired", "w10v1", "v1"))
build("NES-P", "nested", "from", ("w10v4", "10 v4 replaces v1", "w10v1", "v1"))
PY

# ── the stubs ───────────────────────────────────────────────────────────────
# notmuch: --entire-thread=false is the priming call (the root alone), any
# other show a walk or render, with or without bodies (SM-668: a reader asks
# for bodies only when it prints one, as in test_session_mail_member_walk.sh),
# search --output=threads the run's thread lookup. Anything else EXITS 9.
cat > "$BIN/notmuch" <<STUB
#!/usr/bin/env bash
v="\${FIXTURE:?}"
for a in "\$@"; do
  case "\$a" in
    --output=threads) echo "thread:00000000000000aa"; exit 0 ;;
    --entire-thread=false) cat "$T/root-\$v.json"; exit 0 ;;
  esac
done
for a in "\$@"; do
  case "\$a" in
    --body=false|--body=true) cat "$T/thread-\$v.json"; exit 0 ;;
  esac
done
echo "notmuch stub: unexpected invocation: \$*" >&2
exit 9
STUB
cat > "$BIN/verify" <<'STUB'
#!/usr/bin/env bash
s="$(sed -n '/^$/q; s/^X-Fixture-Sig: //p' "$1")"
case "$s" in good) exit 0 ;; forged) exit 1 ;; *) exit 2 ;; esac
STUB
chmod +x "$BIN/notmuch" "$BIN/verify"

run(){ # run <fixture> <script> <args...>; sets $rc
  local v="$1" script="$2"; shift 2
  env PATH="$BIN:$PATH" HOME="$T" FIXTURE="$v" SESSION_MAIL_VERIFY_TIMEOUT=5 \
      SESSION_MAIL_VERIFY="$BIN/verify" \
      CLAUDE_CODE_AGENT="$AGENT" SESSION_AGENT="" READER_ROOT="$ROOTID" \
      "$script" "$@" 2>&1
}
ran(){ ls "$T/marks/$1" | sort | tr '\n' ' ' | sed 's/ $//'; }

n=0
for shape in flat mixed flat2; do
  for bad in from to sig; do
    n=$((n+1)); f="$shape-$bad"
    echo "── F$n — $f: run executes NEITHER copy of the fork"
    out="$(run "$f" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
    [ "$(ran "$f")" = "c05" ] && ok "$f: only item 05 executed" \
      || no "$f: executed [$(ran "$f")], want only c05:
$out"
    [ "$rc" -eq 2 ] && ok "$f: exit 2 (refused)" || no "$f: exit $rc, want 2"
    has "REFUSED: item 10 is a FORK (MHS 110)" "$out" && ok "$f: says item 10 is a FORK, REFUSED" \
      || no "$f: no REFUSED FORK line:
$out"
    has "<w10v1@$HOSTN>" "$out" && has "<w10v3@$HOSTN>" "$out" && ok "$f: names both copies by Message-ID" \
      || no "$f: does not name both copies:
$out"
    has "UNNUMBERED" "$out" && ok "$f: says how to resolve (an UNNUMBERED superseder)" \
      || no "$f: gives no working resolution:
$out"
    has "supersede one" "$out" && no "$f: tells the owner to 'supersede one' (does not resolve a fork)" \
      || ok "$f: never says 'supersede one'"
    has "v2-ran" "$out" && no "$f: K1 the non-member v2 ran" || ok "$f: K1 the non-member v2 never ran"
    has "<w10v2@$HOSTN>" "$out" && ok "$f: names the non-member link that kept v1 alive" \
      || no "$f: does not name the non-member link:
$out"
  done
done

echo "── F10 — --list: REFUSED, neither copy left as a runnable line"
out="$(run flat-from ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"; rc=$?
[ "$rc" -eq 2 ] && ok "--list exits 2" || no "--list exits $rc, want 2"
has "REFUSED — FORK x2" "$out" && ok "--list marks item 10 REFUSED — FORK x2" || no "--list does not mark the fork:
$out"
grep -qE '^echo v[13]-ran' <<<"$out" && no "--list leaves a fork copy uncommented:
$out" || ok "--list comments out both copies"
grep -qE '^echo c05-ran' <<<"$out" && ok "--list still materializes item 05" || no "--list lost item 05:
$out"
[ -z "$(ran flat-from | sed 's/c05//')" ] && ok "--list executed no copy" || no "--list executed [$(ran flat-from)]"

echo "── F11 — --json names the refusal"
out="$(run flat-to ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --json)"; rc=$?
[ "$rc" -eq 2 ] && ok "--json exits 2" || no "--json exits $rc, want 2"
python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
assert d["refused_forks"] == ["10"], d["refused_forks"]
s = [x for x in d["sections"] if x.get("refused") == "fork"]
assert len(s) == 1 and {c["id"] for c in s[0]["fork"]} == {"w10v1@'"$HOSTN"'", "w10v3@'"$HOSTN"'"}, s
# SM-733 R4: a refused section ran nothing, so it carries elapsed_s 0, and the run a total_s.
assert s[0]["elapsed_s"] == 0 and isinstance(d["total_s"], (int, float)) and d["total_s"] >= 0, (s[0].get("elapsed_s"), d.get("total_s"))
' <<<"$out" && ok "--json: refused_forks [10], one section naming both copies" || no "--json does not name the refusal:
$out"

i=0
for x in A B D; do
  i=$((i+1)); f="RES-$x"
  echo "── R$i — $f: a NUMBERED superseder does not resolve the fork"
  out="$(run "$f" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
  [ "$(ran "$f")" = "c05" ] && ok "$f: only item 05 executed" || no "$f: executed [$(ran "$f")], want only c05:
$out"
  [ "$rc" -eq 2 ] && has "REFUSED: item 10 is a FORK" "$out" && ok "$f: still REFUSED, exit 2" \
    || no "$f: not refused (exit $rc):
$out"
done

echo "── R4 — RES-C: an UNNUMBERED superseder resolves it"
out="$(run RES-C ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
[ "$(ran RES-C)" = "c05 v3" ] && ok "RES-C: 05 and v3 executed, v1 did not" || no "RES-C: executed [$(ran RES-C)], want c05 v3:
$out"
[ "$rc" -eq 0 ] && ok "RES-C: exit 0" || no "RES-C: exit $rc, want 0"
has "REFUSED" "$out" && no "RES-C: still says REFUSED:
$out" || ok "RES-C: no refusal"

j=0
for bad in from to sig; do
  j=$((j+1)); f="flat-$bad"
  case "$bad" in
    from) want="From is 'intruder', not the owner 'reader'" ;;
    to)   want="To/Cc does not name 'reader'" ;;
    sig)  want="signature does not verify" ;;
  esac
  echo "── W$j — NOT A MEMBER names the half that failed: $bad"
  out="$(run "$f" "$TREE" -a /BOOT/WAKE)"
  grep -F "NOT A MEMBER: 10 v2" <<<"$out" | grep -qF -- "$want" && ok "tree -a: v2's NOT A MEMBER line says \"$want\"" \
    || no "tree -a: v2's NOT A MEMBER line does not say \"$want\":
$out"
  out="$(run "$f" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"
  grep -F "SKIPPED: item 20" <<<"$out" | grep -qF -- "$want" && ok "run: item 20's SKIPPED line says \"$want\"" \
    || no "run: item 20's SKIPPED line does not say \"$want\":
$out"
done

k=0
for bad in from to sig; do
  k=$((k+1)); f="nested-$bad"
  case "$bad" in
    from) want="From is 'intruder', not the owner 'reader'" ;;
    to)   want="To/Cc does not name 'reader'" ;;
    sig)  want="signature does not verify" ;;
  esac
  echo "── N$k — $f: not a fork; v1 still runs, and run WARNS which half failed"
  out="$(run "$f" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
  [ "$(ran "$f")" = "c05 v1" ] && ok "$f: behaviour unchanged, 05 and v1 executed" \
    || no "$f: executed [$(ran "$f")], want c05 v1 (unchanged):
$out"
  [ "$rc" -eq 0 ] && ok "$f: exit 0 (unchanged)" || no "$f: exit $rc, want 0"
  has "REFUSED" "$out" && no "$f: calls a nested chain a fork:
$out" || ok "$f: no refusal"
  w="$(grep -F "WARNING: item 10 <w10v1@$HOSTN> still runs" <<<"$out")"
  [ -n "$w" ] && ok "$f: WARNING names item 10 and the copy that runs" || no "$f: no WARNING for item 10:
$out"
  has "<w10v2@$HOSTN>" "$w" && has "NOT A MEMBER" "$w" && ok "$f: the WARNING names the non-member link" \
    || no "$f: the WARNING does not name <w10v2>:
$out"
  has "$want" "$w" && ok "$f: the WARNING says \"$want\"" || no "$f: the WARNING does not say \"$want\":
$out"
  has "re-send the supersede as a member, UNNUMBERED" "$out" && ok "$f: says how to fix it" \
    || no "$f: gives no fix:
$out"
  out="$(run "$f" ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
  grep -q "^# WARNING: item 10" <<<"$out" && ok "$f: --list carries the WARNING as a comment" \
    || no "$f: --list has no WARNING:
$out"
done

echo "── N4 — NES-R: the warning's retire recipe (UNNUMBERED member) works"
out="$(run NES-R ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
[ "$(ran NES-R)" = "c05" ] && ok "NES-R: v1 retired, only 05 executed" || no "NES-R: executed [$(ran NES-R)], want c05:
$out"
has "WARNING" "$out" && no "NES-R: still WARNS:
$out" || ok "NES-R: no warning once a member retired v1"

echo "── N5 — NES-P: the warning's replace recipe (NUMBERED member) works"
out="$(run NES-P ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)"; rc=$?
[ "$(ran NES-P)" = "c05 v4" ] && ok "NES-P: v4 runs in v1's place" || no "NES-P: executed [$(ran NES-P)], want c05 v4:
$out"
[ "$rc" -eq 0 ] && ! has "REFUSED" "$out" && ! has "WARNING" "$out" && ok "NES-P: exit 0, no fork, no warning" \
  || no "NES-P: exit $rc or a refusal/warning:
$out"
has "WARNING" "$(run RES-C ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE)" && no "RES-C: warns with no non-member retirer" \
  || ok "RES-C: an item nobody tried to retire draws no warning"

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
