#!/usr/bin/env bash
# A CHILD THE WALKER LISTS MUST BE OPENABLE BY PATH — SESH-1098 repro-001.
#
# WHAT IS WRONG, MEASURED. `session mail ls PATH` / `session mail tree PATH`
# refuse a child that the very same message prints in its own Children list.
# Three shapes of one defect were measured on a live box, Sun 2030-06-09:
#
#   session mail ls   "/CONVO/2030-06-09"          -> "/CONVO has no child '2030-06-09'"
#                                                     …with '2030-06-09' in the Children list
#   session mail ls   "/CORE/[FAMILY]"             -> same, '[FAMILY]' in its own child list
#   session mail ls   "/ASKS/[session]"            -> same, '[session]' in its own child list
#   session mail tree "/ASKS/[session]" --depth 1  -> same error
#
# and the CONTROL that makes it a resolver bug rather than a tree bug:
#
#   session mail tree /ASKS --depth 2              -> WORKS: [session], [taskmaster], [catalog]
#
# The WALKER descends fine. Only the PATH RESOLVER refuses. The two disagree,
# and that is the whole defect: a grouping the reader cannot open by path makes
# the inbox write-only for anyone who addresses it the documented way.
#
# WHY THIS SUITE IS FIXTURE-BASED AND NOT LIVE. Labels are per-box. A seat with
# no canonical root gets "no canonical root found" and renders EMPTY — which is
# not the error, it is a FALSE GREEN wearing the error's clothes. A suite that
# needs a live box is not a regression test; it is a suite that reports on
# whoever happens to run it. So the whole tree is built here, in a mktemp dir,
# behind a `notmuch` STUB on PATH: mhs-thread shells out to `notmuch show`
# (the `sh()` helper in mhs-thread — cited by construct, not by line), so a
# stub earlier on PATH is the seam, no notmuch database and no mail required.
#
# ⭐ P0 RUNS FIRST AND IS GREEN AT BASE, ON PURPOSE. It is the measured control
# (`tree /ASKS --depth 2` works). If the fixture were malformed every probe
# below would go red for the wrong reason and read exactly like the defect.
# P0 red means THE FIXTURE is broken; P0 green with P1..P6 red is the defect.
#
# THE PROBES (each names the success criterion it defends):
#   P0  control — the walker descends and LISTS the three children (green at base)
#   P1  a bracketed child resolves by path: `ls /ASKS/[session]`      (criterion 2)
#   P2  a second bracketed child, upper-case: `ls /CORE/[FAMILY]`     (criterion 2)
#   P3  a child whose name is a PREFIX of two siblings' resolves to
#       ITSELF, exactly: `ls /CONVO/2030-06-09`                       (criterion 3)
#   P4  `tree` resolves the same path `ls` does — one resolver, not two
#                                                                     (criterion 1)
#   P5  the general property, driven off the walker's OWN output: every child
#       `tree /X --depth 1` lists is openable as `ls /X/<child>`      (criterion 1)
#   P6  a genuinely absent child still REFUSES, and the refusal names the
#       resolver and the rule that refused                            (criterion 4)
#   P7  ANTI-HAZARD (rca-001): a missing day label REFUSES and names the
#       near-misses it did not open — it never silently opens a sibling
#   P8  the bare word of a lower-case label opens it: `/ASKS/session`,
#       `/ASKS/SESSION`                                               (criterion 1)
#   P9  a two-spelling FORK refuses as AMBIGUOUS under the bare word, and
#       still opens by exact name                                     (criteria 2,4)
#   P10 regression guard — the two shapes the OLD resolver got right (a numbered
#       item, and `latest`) still resolve (green at base, like P0). Its paths go
#       through /CORE/FAMILY on purpose: the one bare word the OLD rule mapped
#   P11 the POSITIVE half of P7 — `today`/`yesterday`/the typed ISO date open a
#       day label that DOES exist, and take the label rather than the prefix
#       sibling standing next to it                                   (criterion 1)
#
# ⚠️ WHY P7..P9 EXIST — 24/24 IS NOT PROOF OF THE FIX (rca-001, measured).
# Two different matchers both score 24/24 on P0..P6: the recommended one, and a
# loose "exact -> [UPPER] -> prefix on EVERY segment" one. The loose matcher
# SILENTLY OPENS `2030-06-09 evening` for `ls /X/2030-06-09` — and for
# `ls /X/today` — when the day label was never written: a refusal turned into a
# WRONG ANSWER with rc 0, which is worse than the bug being fixed. P0..P6
# cannot tell a silent-sibling-opener from a refuser, and cannot see that the
# bare-word form is still dead. P7..P9 are exactly that discrimination.
#
# ⚠️ AND WHY P11 EXISTS — A REFUSER THAT REFUSES EVERYTHING ALSO SCORES WELL.
# P7 demands a refusal; nothing in P0..P10 demands that `today` ever OPENS
# anything, so a resolver that dropped the date mapping altogether, or that
# refused every day label, would keep P7 green and lose nothing. P11 is the
# other half of that pair: the same three spellings against a day label that
# EXISTS, next to a prefix sibling, asserting by Message-ID which node was
# opened. P7 without P11 measures only the direction it is afraid of.
#
# HISTORY, AND WHAT RED MEANS NOW. This file was written RED, as the repro
# slice's deliverable, and wired into `make test` while failing: a warning about
# a trap does not catch the trap — only a probe shown failing does. Measured
# scores, all against this same fixture:
#
#   at base (before fix-001)                    15 passed / 27 failed
#   with fix-001                                42 passed /  0 failed
#   with the PREFIX-UNGATED variant — fix-001
#     with the `is_item` gate removed, which
#     also passes P0..P6 and silently opens a
#     sibling                                   36 passed /  6 failed, ALL SIX in P7
#
# (Those three were measured on the 42-probe file, before P11. "Prefix-ungated"
# is the exact name for the third row: it is this fix minus one gate, not
# rca-001's true naive matcher, which scores 31/11. The distinction matters
# because the row's whole point is that the variant is ONE gate away from the
# shipped one — nothing about it looks careless from the outside.)
#
# So red here no longer means "unfixed"; it means the resolver has changed shape.
# Read WHICH probe went red: P0 red = the fixture is broken, P1..P6 red = the
# original defect is back, P7 red = something opens a sibling it should refuse,
# P11 red = it refuses a day label that EXISTS (or opens the wrong node),
# P10 red = the fix broke the half that always worked.
#
# Hermetic: mktemp trees only, a stubbed notmuch, no real mailbox, no network,
# no notmuch database, nothing mutated outside $T.
#
# Run directly:
#   bash tests/mhs/test_session_mail_label_path.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)"   # the bundled oracle
LS="$HERE/session-mail-ls"
TREE="$HERE/session-mail-tree"

pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"

# The fixture agent is a made-up name on a made-up host: the shipped repo may
# not carry a real persona or a real hostname (see the sanitize classes in
# `make check`), and a fixture has no business naming one anyway.
AGENT="reader"
HOSTN="host1.session-mail"
ROOTID="mhs-root@$HOSTN"

# ── the fixture tree ────────────────────────────────────────────────────────
# [ROOT]
#   [ASKS]                     ← resolves today ("ASKS" -> "[ASKS]")
#     [session]                ← SHAPE 1: bracketed, lower-case
#       10 first ask
#       80 last ask
#     [taskmaster]
#     [catalog]
#   [CORE]
#     [FAMILY]                 ← SHAPE 2: bracketed, upper-case
#       10 a family note
#   [CONVO]
#     2030-06-09               ← SHAPE 3: exact name, and a PREFIX of both siblings
#       10 the day's thread
#     2030-06-09 morning
#     2030-06-09 evening
#   [WEEK]                     ← the HAZARD tree: TODAY's day label was never
#     <today> morning            written, only two siblings that START with it.
#     <today> evening            /WEEK/<today> and /WEEK/today must REFUSE.
#   [DIARY]                    ← the MIRROR of [WEEK]: today's day label DOES
#     <today>                    exist, standing next to a prefix sibling.
#       10 today's entry         /DIARY/today must OPEN <today> — not the
#     <today> evening            sibling, and not a refusal.
#     <yesterday>
#   [FORK]                     ← two spellings of ONE label (MHS 110 fork)
#     [session]
#     [Session]
#
# The WEEK and DIARY children are built from the REAL current date, not a frozen
# one, so that the `today` and `yesterday` magic names land inside those
# subtrees on whatever day the suite is run. Nothing else in the fixture is
# date-sensitive. WEEK and DIARY are deliberately the same shape apart from the
# one node whose presence is the whole question.
#
# Membership (MHS 170) is enforced on every edge below the anchor: From the
# owner AND addressed to the owner AND signed. Every fixture message satisfies
# all three — each is written to a file the `session mail-verify` stub below
# passes — so a hidden node means a resolver/render bug and never a
# membership accident. (Before the signature was part of the path walk,
# 2030-12-08, this fixture carried no files at all; test_session_mail_member_walk.sh
# is where an UNSIGNED node is the point.)
DAYID="convo-day@$HOSTN"
FORK_LOWER="fork-lower@$HOSTN"
FORK_UPPER="fork-upper@$HOSTN"
TODAY="$(date +%F)"
# Derived the same way the fixture derives it (python, not `date -v`/`date -d`,
# which disagree between BSD and GNU) so the two ends cannot drift apart.
YESTERDAY="$(python3 -c 'import datetime; print((datetime.date.today() - datetime.timedelta(days=1)).isoformat())')"
mkdir -p "$T/mail"
python3 - "$T/thread.json" "$T/root.json" "$AGENT" "$HOSTN" "$T/mail" <<'PY'
import datetime, json, os, sys
out_thread, out_root, agent, host, maildir = sys.argv[1:6]
today = datetime.date.today().isoformat()   # the `today` magic name must land in [WEEK]/[DIARY]
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
ts = [1000]

def msg(subject, local):
    ts[0] += 1
    # A file per message, for the signature check alone: no Supersedes:
    # header, so still no supersede chains here.
    fn = os.path.join(maildir, local)
    with open(fn, "w") as fh:
        fh.write("Subject: %s\nX-Fixture-Sig: good\n\n%s\n" % (subject, subject))
    return {
        "id": "%s@%s" % (local, host),
        "timestamp": ts[0],
        "filename": [fn],
        "headers": {
            "Subject": subject,
            "From": "%s <%s@%s>" % (agent, agent, host),
            "To": "%s+memory@%s" % (agent, host),
            "Date": "Sun, 09 Jun 2030 12:00:00 +0000",
        },
        "body": [{"id": 1, "content-type": "text/plain", "content": subject}],
    }

def node(subject, local, children=()):
    return [msg(subject, local), list(children)]

root = node("[ROOT]", "mhs-root", [
    node("[ASKS]", "asks", [
        node("[session]", "asks-session", [
            node("10 first ask", "ask-10"),
            node("80 last ask", "ask-80"),
        ]),
        node("[taskmaster]", "asks-taskmaster"),
        node("[catalog]", "asks-catalog"),
    ]),
    node("[CORE]", "core", [
        node("[FAMILY]", "core-family", [
            node("10 a family note", "family-10"),
        ]),
    ]),
    node("[CONVO]", "convo", [
        node("2030-06-09", "convo-day", [
            node("10 the day thread", "day-10"),
        ]),
        node("2030-06-09 morning", "convo-morning"),
        node("2030-06-09 evening", "convo-evening"),
    ]),
    # The hazard tree: TODAY's day label does not exist, only two children that
    # start with it. A prefix rule on a label segment opens the LAST of these
    # (newest wins) and exits 0 — the silent wrong answer P7 forbids.
    node("[WEEK]", "week", [
        node("%s morning" % today, "week-morning"),
        node("%s evening" % today, "week-evening"),
    ]),
    # The mirror of the hazard tree: here TODAY's day label DOES exist, and it
    # sits next to a sibling that starts with it. `today` must open the label
    # (exact tier), never the sibling (prefix tier) and never refuse — the
    # positive case P7 alone cannot ask for. `<yesterday>` covers the second
    # magic name, which no other probe touches at all.
    node("[DIARY]", "diary", [
        node(today, "diary-today", [
            node("10 today's entry", "diary-10"),
        ]),
        node("%s evening" % today, "diary-evening"),
        node(yesterday, "diary-yesterday"),
    ]),
    # Two spellings of one label (MHS 110 fork). Under a case-differing bare
    # word neither spelling is the answer, so the resolver must say AMBIGUOUS
    # and name both, not pick one.
    node("[FORK]", "fork", [
        node("[session]", "fork-lower"),
        node("[Session]", "fork-upper"),
    ]),
])

# notmuch show --format=json shape: [ thread, ... ] where a thread is a list of
# toplevel nodes and a node is [message, [child-node, ...]].
with open(out_thread, "w") as fh:
    json.dump([[root]], fh)
# The PRIMING call (--body=false --entire-thread=false id:<hint>) sees the root
# message alone: mhs-thread checks its Subject is [ROOT] and its From is the
# agent before it will render anything.
with open(out_root, "w") as fh:
    json.dump([[[root[0], []]]], fh)
PY

# ── the notmuch stub ────────────────────────────────────────────────────────
# Two calls are made against it, and they are told apart by the flag that
# defines the priming call: --entire-thread=false (the root alone). Every
# render fetches the whole thread, with or without bodies (SM-668: a reader
# asks for bodies only when it prints one, so the body flag no longer tells
# the two apart). Anything else is a fixture bug, so it EXITS 9 rather than
# answering with a plausible tree.
cat > "$BIN/notmuch" <<STUB
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    --entire-thread=false) cat "$T/root.json"; exit 0 ;;
  esac
done
for a in "\$@"; do
  case "\$a" in
    --body=false|--body=true) cat "$T/thread.json"; exit 0 ;;
  esac
done
echo "notmuch stub: unexpected invocation: \$*" >&2
exit 9
STUB
chmod +x "$BIN/notmuch"
# `session mail-verify FILE`: every fixture file is signed (see Membership).
cat > "$BIN/session" <<STUB
#!/usr/bin/env bash
[ "\${1:-}" = "mail-verify" ] && grep -q '^X-Fixture-Sig: good' "\$2" && exit 0
echo "session stub: unexpected invocation or unsigned file: \$*" >&2
exit 9
STUB
chmod +x "$BIN/session"

# One door for every probe: the fixture environment, and nothing inherited that
# could point the reader at a real box.
run(){ # run <script> <args...>
  local script="$1"; shift
  env PATH="$BIN:$PATH" SESSION_MAIL_VERIFY="$BIN/session mail-verify" \
      CLAUDE_CODE_AGENT="$AGENT" SESSION_AGENT="" \
      READER_ROOT="$ROOTID" \
      bash "$script" "$@" 2>&1
}

echo "── P0 — CONTROL: the walker descends and lists the children (green at base)"
out="$(run "$TREE" /ASKS --depth 2)"; rc=$?
if [ "$rc" -eq 0 ]; then
  ok "tree /ASKS --depth 2 exits 0"
else
  no "tree /ASKS --depth 2 exits $rc — THE FIXTURE IS BROKEN, not the resolver:
$out"
fi
for child in '[session]' '[taskmaster]' '[catalog]'; do
  if grep -qF -- "$child" <<<"$out"; then
    ok "the walker lists $child under /ASKS"
  else
    no "the walker does not list $child under /ASKS — fixture broken:
$out"
  fi
done
if grep -qF -- "10 first ask" <<<"$out"; then
  ok "the walker descends INTO [session] (depth 2 reaches its items)"
else
  no "the walker did not descend into [session] — fixture broken:
$out"
fi

echo "── P1 — a bracketed child resolves BY PATH: ls /ASKS/[session] (criterion 2)"
out="$(run "$LS" '/ASKS/[session]')"; rc=$?
if [ "$rc" -eq 0 ]; then
  ok "ls '/ASKS/[session]' exits 0"
else
  no "ls '/ASKS/[session]' exits $rc — the resolver refuses a child the walker lists:
$out"
fi
if grep -qF -- "10 first ask" <<<"$out"; then
  ok "ls '/ASKS/[session]' shows the label's items"
else
  no "ls '/ASKS/[session]' did not show the label's items:
$out"
fi
# The refusal it must NOT be — pinned by its own words, so a DIFFERENT failure
# (say, a stub misfire) cannot be mistaken for this defect having survived.
if grep -qF -- "has no child" <<<"$out"; then
  no "the measured refusal is present: the resolver denies a child it is listing"
else
  ok "no 'has no child' refusal for a listed child"
fi

echo "── P2 — a second bracketed child, upper-case: ls /CORE/[FAMILY] (criterion 2)"
out="$(run "$LS" '/CORE/[FAMILY]')"; rc=$?
if [ "$rc" -eq 0 ]; then
  ok "ls '/CORE/[FAMILY]' exits 0"
else
  no "ls '/CORE/[FAMILY]' exits $rc:
$out"
fi
if grep -qF -- "10 a family note" <<<"$out"; then
  ok "ls '/CORE/[FAMILY]' shows the label's items"
else
  no "ls '/CORE/[FAMILY]' did not show the label's items:
$out"
fi

echo "── P3 — an EXACT child whose name prefixes two siblings (criterion 3)"
out="$(run "$LS" /CONVO/2030-06-09)"; rc=$?
if [ "$rc" -eq 0 ]; then
  ok "ls /CONVO/2030-06-09 exits 0"
else
  no "ls /CONVO/2030-06-09 exits $rc — refused while listing '2030-06-09' as a child:
$out"
fi
# EXACTNESS, not merely success: --resolve prints the Message-ID the walk
# landed on. An exact match must never be beaten by a longer sibling, and the
# id is the only assertion that can tell the three apart — all three subjects
# start with the same nine characters.
resout="$(run "$LS" /CONVO/2030-06-09 --resolve)"; resrc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$resrc" -eq 0 ] && [ "$got" = "<$DAYID>" ]; then
  ok "it resolves to the EXACT child <$DAYID>, not a prefix sibling"
else
  no "--resolve gave rc=$resrc, want rc=0 and <$DAYID>; first line was:
$(head -1 <<<"$resout")"
fi

echo "── P4 — tree resolves the same path ls does: ONE resolver (criterion 1)"
lsout="$(run "$LS" '/ASKS/[session]')";   lsrc=$?
trout="$(run "$TREE" '/ASKS/[session]' --depth 1)"; trrc=$?
if [ "$trrc" -eq 0 ]; then
  ok "tree '/ASKS/[session]' --depth 1 exits 0"
else
  no "tree '/ASKS/[session]' --depth 1 exits $trrc:
$trout"
fi
# ⚠️ AGREEMENT ALONE IS NOT THE CRITERION, and asserting it alone would hand
# this suite a passing probe AT BASE: today ls and tree agree perfectly — they
# both refuse, with the same message. Two resolvers that are equally broken are
# indistinguishable from one that works, by rc equality. So the probe demands
# agreement AT ZERO, and names the difference when it gets the other kind.
if [ "$lsrc" -eq "$trrc" ] && [ "$lsrc" -eq 0 ]; then
  ok "ls and tree resolve '/ASKS/[session]' identically, and both OPEN it"
elif [ "$lsrc" -eq "$trrc" ]; then
  no "ls and tree agree but BOTH REFUSE (rc=$lsrc) — agreement in failure is not one working resolver"
else
  no "ls rc=$lsrc but tree rc=$trrc for the same path — two resolvers, not one"
fi

echo "── P5 — every child the walker LISTS is openable by path (criterion 1)"
# Driven off the walker's own output rather than a hand-written list: this is
# the property the defect violates, stated once, over whatever the tree holds.
for parent in /ASKS /CORE /CONVO; do
  listing="$(run "$TREE" "$parent" --depth 1)"
  # depth-1 lines are the ones indented exactly two spaces under the anchor;
  # strip the tree glyph and any "   [sender]" tag the renderer appends.
  while IFS= read -r child; do
    [ -n "$child" ] || continue
    if run "$LS" "$parent/$child" >/dev/null 2>&1; then
      ok "$parent/$child — listed by the walker, opens by path"
    else
      no "$parent/$child — LISTED by the walker, REFUSED by the resolver"
    fi
  done < <(sed -n 's/^  └ //p' <<<"$listing" | sed 's/   \[[^]]*\]$//')
done

echo "── P6 — an absent child refuses, and the refusal NAMES itself (criterion 4)"
out="$(run "$LS" '/ASKS/[no-such-label]')"; rc=$?
if [ "$rc" -ne 0 ]; then
  ok "ls '/ASKS/[no-such-label]' still refuses (rc=$rc) — the fix must not open everything"
else
  no "ls '/ASKS/[no-such-label]' exited 0 — a resolver that refuses nothing is not a resolver"
fi
if grep -qF -- "mhs-thread" <<<"$out"; then
  ok "the refusal names the RESOLVER (mhs-thread)"
else
  no "the refusal does not name the resolver — charter 2 wants the site named:
$out"
fi
# The RULE, not just the outcome: a reader must learn WHY it refused (which
# match was attempted), not merely that something did not match.
if grep -qiE 'exact|literal|match|rule' <<<"$out"; then
  ok "the refusal names the RULE that refused"
else
  no "the refusal states no rule — charter 2 wants the rule that refused:
$out"
fi

echo "── P7 — ANTI-HAZARD: a MISSING day label refuses and names what it did NOT open"
# rca-001 measured this: with `exact -> [UPPER] -> prefix` applied to every
# segment, `/WEEK/<today>` opens `<today> evening` with rc 0 although no day
# label was ever written. The suite must be able to tell a refuser from a
# silent-sibling-opener, and the Message-ID is the only witness that can.
for seg in "$TODAY" today; do
  out="$(run "$LS" "/WEEK/$seg")"; rc=$?
  if [ "$rc" -ne 0 ]; then
    ok "ls /WEEK/$seg refuses — the day label was never written"
  else
    no "ls /WEEK/$seg exited 0 — it silently opened a SIBLING; a prefix rule must not run on a label segment:
$out"
  fi
  resout="$(run "$LS" "/WEEK/$seg" --resolve)"
  if grep -qF -- "week-evening@" <<<"$resout"; then
    no "ls /WEEK/$seg --resolve landed on the evening sibling — a wrong answer, not a refusal:
$resout"
  else
    ok "ls /WEEK/$seg --resolve does not land on a prefix sibling"
  fi
  # Naming the near-misses is the charter-2 half: a reader must see WHICH
  # children were deliberately left shut, not just that nothing matched.
  if grep -qi 'near miss' <<<"$out" \
     && grep -qF -- "$TODAY morning" <<<"$out" && grep -qF -- "$TODAY evening" <<<"$out"; then
    ok "the refusal names the near-misses it did not open"
  else
    no "the refusal does not name its near-misses ('$TODAY morning', '$TODAY evening'):
$out"
  fi
done

echo "── P8 — the BARE WORD of a lower-case label opens it (criterion 1)"
# The MHS documented form is /LABEL for "[LABEL]" (mhs-thread docstring). The
# live tree writes project labels lower-case, so the documented form must reach
# them: case is not identity (MHS §Kinds — "differing spaces/case are the SAME
# label — resolvers canonicalise", which is the rule the defect broke).
for seg in session SESSION; do
  out="$(run "$LS" "/ASKS/$seg")"; rc=$?
  if [ "$rc" -eq 0 ] && grep -qF -- "10 first ask" <<<"$out"; then
    ok "ls /ASKS/$seg opens [session]"
  else
    no "ls /ASKS/$seg exits $rc — the documented bare-word form cannot reach a lower-case label:
$out"
  fi
  resout="$(run "$LS" "/ASKS/$seg" --resolve)"
  got="$(tr -d '[:space:]' <<<"$resout")"
  if [ "$got" = "<asks-session@$HOSTN>" ]; then
    ok "ls /ASKS/$seg resolves to [session] exactly"
  else
    no "ls /ASKS/$seg --resolve gave '$got', want '<asks-session@$HOSTN>'"
  fi
done

echo "── P9 — a two-spelling FORK: ambiguous by bare word, exact by name (criteria 2,4)"
out="$(run "$LS" /FORK/SESSION)"; rc=$?
if [ "$rc" -ne 0 ]; then
  ok "ls /FORK/SESSION refuses — two spellings, and picking one would be a guess"
else
  no "ls /FORK/SESSION exited 0 — it picked one spelling of a fork silently (MHS 110):
$out"
fi
if grep -qi 'ambiguous' <<<"$out"; then
  ok "the refusal says AMBIGUOUS — the reader learns it is a fork, not an absence"
else
  no "the refusal does not name the ambiguity; a fork must not read as 'no such child':
$out"
fi
if grep -qF -- '[session]' <<<"$out" && grep -qF -- '[Session]' <<<"$out"; then
  ok "the refusal names BOTH spellings, so the reader can type one"
else
  no "the refusal does not name both spellings:
$out"
fi
# …and the escape hatch the refusal points at must actually work: an exact
# name is never ambiguous, and never resolves to its sibling.
for pair in "[session]:$FORK_LOWER" "[Session]:$FORK_UPPER"; do
  seg="${pair%%:*}"; want="${pair#*:}"
  resout="$(run "$LS" "/FORK/$seg" --resolve)"; resrc=$?
  got="$(tr -d '[:space:]' <<<"$resout")"
  if [ "$resrc" -eq 0 ] && [ "$got" = "<$want>" ]; then
    ok "ls '/FORK/$seg' opens exactly <$want>"
  else
    no "ls '/FORK/$seg' --resolve gave rc=$resrc '$got', want rc=0 '<$want>'"
  fi
done

echo "── P10 — REGRESSION GUARD: numbered items and 'latest' still resolve (green at base)"
# These two shapes were the only ones the old resolver got RIGHT, and they run
# through the block fix-001 rewrote: an item segment reaches "10 ..." by prefix,
# and `latest` takes the newest numbered item. Every path here is one that
# WORKED AT BASE — deliberately, including the /CORE/FAMILY spelling (the one
# upper-case bare word the old "[%s]" % seg.upper() rule could map). Like P0
# this probe is green before the fix as well as after: its job is to notice if
# the fix breaks the half that already worked, which no other probe would see.
resout="$(run "$LS" /CORE/FAMILY/10 --resolve)"; rc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$rc" -eq 0 ] && [ "$got" = "<family-10@$HOSTN>" ]; then
  ok "an item segment still resolves by number: /CORE/FAMILY/10"
else
  no "/CORE/FAMILY/10 --resolve gave rc=$rc '$got', want '<family-10@$HOSTN>'"
fi
resout="$(run "$LS" /CORE/FAMILY/latest --resolve)"; rc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$rc" -eq 0 ] && [ "$got" = "<family-10@$HOSTN>" ]; then
  ok "'latest' still takes the newest numbered item under a label"
else
  no "/CORE/FAMILY/latest --resolve gave rc=$rc '$got', want '<family-10@$HOSTN>'"
fi
out="$(run "$LS" /CORE/latest)"; rc=$?
if [ "$rc" -ne 0 ] && grep -qF -- "no numbered items" <<<"$out"; then
  ok "'latest' under a label holding only LABELS still refuses, saying so"
else
  no "/CORE/latest gave rc=$rc, want a refusal naming 'no numbered items':
$out"
fi

echo "── P11 — the POSITIVE half of P7: a day label that EXISTS opens, by all three spellings"
# P7 proves the resolver REFUSES a day label that was never written. On its own
# that is one-sided: a resolver that had lost the date mapping, or that refused
# every day label, would keep P7 green. [DIARY] is [WEEK] with the one node
# added, so these probes fail if `today` stops resolving, and P7 fails if it
# starts guessing — the pair pins both directions.
#
# Every assertion is by Message-ID (`--resolve`), never by rc alone: `<today>`
# and `<today> evening` render similar text, so an rc-only probe could not tell
# the label from the prefix sibling standing beside it — which is the exact
# confusion this card exists to kill.
for seg in today "$TODAY"; do
  resout="$(run "$LS" "/DIARY/$seg" --resolve)"; rc=$?
  got="$(tr -d '[:space:]' <<<"$resout")"
  if [ "$rc" -eq 0 ] && [ "$got" = "<diary-today@$HOSTN>" ]; then
    ok "ls /DIARY/$seg opens the day label ITSELF (exact tier beats the prefix sibling)"
  else
    no "ls /DIARY/$seg --resolve gave rc=$rc '$got', want '<diary-today@$HOSTN>' —
    a day label that EXISTS must open; landing on <diary-evening@$HOSTN> would be
    the prefix sibling (P7's wrong answer), and a non-zero rc a refusal of a
    label the walker lists (the original defect):
$resout"
  fi
done
resout="$(run "$LS" /DIARY/yesterday --resolve)"; rc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$rc" -eq 0 ] && [ "$got" = "<diary-yesterday@$HOSTN>" ]; then
  ok "'yesterday' maps to an ISO date and opens that label"
else
  no "ls /DIARY/yesterday --resolve gave rc=$rc '$got', want '<diary-yesterday@$HOSTN>':
$resout"
fi
# The walk must CONTINUE below a magic-name segment: `today` is resolved to a
# node, not to a terminus.
resout="$(run "$LS" /DIARY/today/10 --resolve)"; rc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$rc" -eq 0 ] && [ "$got" = "<diary-10@$HOSTN>" ]; then
  ok "the walk continues BELOW a magic-name segment: /DIARY/today/10"
else
  no "ls /DIARY/today/10 --resolve gave rc=$rc '$got', want '<diary-10@$HOSTN>':
$resout"
fi
# And the sibling is still reachable by its own exact name — P11 must not be
# satisfiable by a resolver that simply prefers the shorter name always.
resout="$(run "$LS" "/DIARY/$TODAY evening" --resolve)"; rc=$?
got="$(tr -d '[:space:]' <<<"$resout")"
if [ "$rc" -eq 0 ] && [ "$got" = "<diary-evening@$HOSTN>" ]; then
  ok "the prefix SIBLING still opens by its own exact name, alongside the label"
else
  no "ls '/DIARY/$TODAY evening' --resolve gave rc=$rc '$got', want '<diary-evening@$HOSTN>':
$resout"
fi

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ] || exit 1
