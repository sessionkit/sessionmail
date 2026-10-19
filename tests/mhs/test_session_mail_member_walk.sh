#!/usr/bin/env bash
# AN UNSIGNED MAIL IS NOT MEMORY — NOT BY PATH, NOT BY SUPERSEDE, NOT AS ROOT.
#
# WHAT WAS WRONG, MEASURED. A review found that
# `memory ls /RULES`, `tree` and `cat` list and read an unsigned item, and one
# carrying a signature copied from another message, as live memory: the path
# render never passed verify=True (only `ls /` and `--boot` did). Reading the
# reader for it found three more edges nobody checked:
#
#   H1  the path render (ls/tree/cat PATH) checks From/To headers only, which
#       any local sender can write. HUGO's finding.
#   H2  the WALK picks children by subject with no membership test, and the
#       newest of one spelling wins: a newer unsigned "20 …" beside a signed
#       "20 …" is what `cat /X/20` opens, `latest` takes an unsigned newest,
#       and a numbered twin makes a FORK that hides the signed item.
#   H3  `Supersedes:` is honoured from ANY mail in the thread, signed or not —
#       in the path render, the verified `ls /`, and `session mail run` (the
#       wake). An unsigned reply naming a signed item hides it, and the walk
#       hoists into the forger. The same holds for the subject-tag shadows
#       (a newer "R05 …" or "NAME vN" hides the older one).
#   H4  the walk and `ls /` anchor on the NEWEST message whose subject is
#       [ROOT] in the root's thread, not on the primed hint: an unsigned
#       "[ROOT]" reply takes over every path below it.
#
# `memory run` never EXECUTED an unverified item (it verifies each one), so
# the wake was never a code path for a forger — but H3 let one DROP a wake
# step, and H1/H2/H4 let one put words in the agent's memory.
#
# THE RULE THIS SUITE PINS (MHS 170): a mail is memory iff From the owner AND
# To the owner AND its signature verifies. Only a member is an edge, a
# supersede, a shadow, a fork twin, a `latest`, or a root.
#
# FIXTURE-BASED, like test_session_mail_label_path.sh: a `notmuch` stub serves
# the thread, and a `session` stub answers `session mail-verify FILE` from an
# X-Fixture-Sig header in each fixture file (good -> 0, forged -> 1, absent ->
# 2, the verifier's own codes). Every call to the verifier is logged, so the
# suite can also say the check stays LAZY: verifying the whole thread up front
# costs ~55 s on a real box (1596 mails, 668 superseders, 83 ms each).
#
#   C1-C3  controls, green at base: the walker lists, a SIGNED supersede
#          still replaces in place (tree and cat), run lists a signed item
#   P1-P3  H1: unsigned and forged items are ⊘ NOT A MEMBER in ls/tree, and
#          `cat` refuses them
#   P4-P6  H2: an unsigned twin loses the walk, makes no FORK, and is never
#          `latest`
#   P7-P10 H3: an unsigned supersede hides nothing — item (tree, cat), wake
#          step (run --list), label (ls /) — and an unsigned "R05" tag shadows
#          nothing
#   P11-P12 H4: an unsigned [ROOT] reply takes over neither a path nor `ls /`
#   P14    H2/H3: the --boot fallback reads the newest signed letter
#   P15    H2/H3: `latest` down reply chains is the newest survivor
#   P16-P17 an unsigned "Superseded by …" hides no listing, not even `ls /`
#   P18    `--index`: an unsigned top-level node in the thread is not a member
#   P19    a verifier that hangs is a timeout, counted NOT verified
#   P20    `run`: a signed wake item To someone else is not memory
#   P21    a signed root "Superseded by": `ls /` says NOTHING SHOWN
#   P22-P26 a verify that times out is UNKNOWN, never a pass and never a way
#          back: a superseder that cannot be checked retires its target
#          anyway, and run / cat / latest / --boot refuse and name the timeout
#   P27-P32 ... and wherever else a mail takes effect: a root "Superseded
#          by", a tag or NAME vN shadow, a newer duplicate, an unnumbered
#          superseder under latest / --boot, a newer [ROOT] or [BOOT]
#   P33    run's SKIPPED line claims only what is true
#   P34-P38 ... but a timed-out mail that CANNOT change the answer is no
#          reason to refuse: the walk tries every verdict for it, opens what
#          they all agree on ([LAGOLD], [DUPEOLD]), refuses when any two
#          differ ([LAGMIX], a mixed verdict) or when the walk without them
#          disagrees with all of them ([SPELL]), and a FORK line names the
#          timeout ([DUPE], [DUPEOLD], [FORK3])
#   P39    LAG_CAP: 8 timed-out mails are weighed and open; 9 refuse
#          ([LAGRET8], [LAGRET9]: each stale copy retired by the signed
#          one), so the cap is pinned at both edges; 8 UNRETIRED stale
#          copies ([LAGCAP8]) may be twins and withhold (SM-698)
#   P40    two SIGNED twins and no timeout: the plain FORK line ([FORK2])
#   P13    cost: a path read never verifies mails outside what it walked
#
# Hermetic: mktemp trees only, stubbed notmuch and session, HOME=$T.
#
# Run directly:
#   bash tests/mhs/test_session_mail_member_walk.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../../src/sessionmail/mhs" && pwd)"   # the bundled oracle
LS="$HERE/session-mail-ls"
TREE="$HERE/session-mail-tree"
CAT="$HERE/session-mail-cat"
RUN="$HERE/session-mail-run"

pass=0; fail=0
ok(){ echo "  ok  $1"; pass=$((pass+1)); }
no(){ echo "FAIL  $1"; fail=$((fail+1)); }
has(){ grep -qF -- "$1" <<<"$2"; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN" "$T"/mail/{A,B,C,D,E,F,G,H,I}

AGENT="reader"
HOSTN="host1.session-mail"
ROOTID="mhs-root@$HOSTN"

# ── the fixture ─────────────────────────────────────────────────────────────
# sig: good (signed), none (unsigned), forged (a copied signature). Every
# mail, forged or not, carries From: reader and To: reader+memory — the
# headers a local sender can write — so only the signature tells them apart.
#
# [ROOT]
#   [RULES]
#     10 real rule                      good
#     R05 real rule                     good
#     R05 fake rule                     none   newer: a tag shadow (P10)
#     30 unsigned rule                  none   (P1-P3)
#     40 forged rule                    forged (P1-P3)
#   [ASKS]
#     10 real ask                       good
#       10 hijacked ask                 none   Supersedes: 10 real ask (P7, P8)
#     30 old                            good
#       30 new                          good   Supersedes: 30 old (C2)
#   [TWINS]
#     20 real twenty                    good
#     20 fake twenty                    none   newer twin (P4, P5)
#   [BOOT]
#     10 LETTER v1                      good
#     10 LETTER v2                      good   Supersedes: v1, a sibling as live
#     20 LETTER forged                  none   newer: never `latest` (P6)
#     [WAKE]
#       10 echo one                     good
#       20 echo two                     good
#         20 echo evil                  none   Supersedes: 20 echo two (P9)
#   [LESSONS]
#     10 a lesson                       good
#     [LESSONS]                         none   Supersedes: [LESSONS] (P10)
#   [NOTES]                             `latest` down reply chains (P15)
#     20 early note                     good
#     10 note v1                        good
#       10 note v2                      good   Supersedes: v1
#         10 note v3                    none   Supersedes: v2
#     30 x v1                           good   survives: its superseder is unsigned
#       30 x v2                         none   Supersedes: x v1
#         30 x v3                       good   Supersedes: x v2 — newest, signed,
#                                              but hung off an unsigned link
#   [CONVO]                             40 signed items, each superseded by a
#     …                                 signed successor: the cost probe (P13)
#
# Fixture B is A plus an unsigned "[ROOT]" reply to the root, newest in the
# thread, holding its own "[RULES]" / "10 hijack rule" (P11, P12).
python3 - "$T" "$AGENT" "$HOSTN" <<'PY'
import json, os, sys
T, agent, host = sys.argv[1:4]
ts = [1000]

def build(variant):
    maildir = os.path.join(T, "mail", variant)
    def msg(subject, local, sig="good", supersedes=None, body=None, to=None):
        ts[0] += 1
        mid = "%s@%s" % (local, host)
        fn = os.path.join(maildir, local)
        to = to or "%s+memory@%s" % (agent, host)
        hdr = ["Message-ID: <%s>" % mid,
               "From: %s <%s@%s>" % (agent, agent, host),
               "To: %s" % to,
               "Subject: %s" % subject]
        for sup in ([supersedes] if isinstance(supersedes, str) else supersedes or []):
            hdr.append("Supersedes: <%s@%s>" % (sup, host))
        if sig != "none":
            hdr.append("X-Fixture-Sig: %s" % sig)
        text = body if body is not None else subject + " body"
        with open(fn, "w") as fh:
            fh.write("\n".join(hdr) + "\n\n" + text + "\n")
        return {"id": mid, "timestamp": ts[0], "filename": [fn],
                "headers": {"Subject": subject,
                            "From": "%s <%s@%s>" % (agent, agent, host),
                            "To": to,
                            "Date": "Tue, 01 Jan 2030 12:00:00 +0000"},
                "body": [{"id": 1, "content-type": "text/plain", "content": text}]}
    def node(subject, local, children=(), **kw):
        return [msg(subject, local, **kw), list(children)]

    convo = []
    for n in range(40):
        convo.append(node("%d old convo item" % (10 * (n + 1)), "convo-old-%d" % n, [
            node("%d new convo item" % (10 * (n + 1)), "convo-new-%d" % n,
                 supersedes="convo-old-%d" % n)]))
    kids = [
        node("[RULES]", "rules", [
            node("10 real rule", "rule-10"),
            node("R05 real rule", "rule-r05"),
            node("R05 fake rule", "rule-r05-fake", sig="none"),
            node("30 unsigned rule", "rule-30", sig="none"),
            node("40 forged rule", "rule-40", sig="forged"),
        ]),
        node("[ASKS]", "asks", [
            node("10 real ask", "ask-10", [
                node("10 hijacked ask", "ask-10-hijack", sig="none", supersedes="ask-10"),
            ]),
            node("30 old", "ask-30-old", [
                node("30 new", "ask-30-new", supersedes="ask-30-old"),
            ]),
        ]),
        # Twins in a label of their own: after a FORK line the render's prune
        # outlives a superseded sibling and swallows its successor too (seen
        # building this suite) — a separate defect this fixture steps around.
        node("[TWINS]", "twins", [
            node("20 real twenty", "ask-20"),
            node("20 fake twenty", "ask-20-fake", sig="none"),
        ]),
        node("[BOOT]", "boot", [
            # Parented to the LABEL, as live boot letters are (the child view
            # never walks a reply-chain: SESH-671's tombstones).
            node("10 LETTER v1", "letter-v1"),
            node("10 LETTER v2", "letter-v2", supersedes="letter-v1"),
            node("20 LETTER forged", "letter-forged", sig="none"),
            node("[WAKE]", "wake", [
                node("10 echo one", "wake-10", body="echo one"),
                node("20 echo two", "wake-20", body="echo two", children=[
                    node("20 echo evil", "wake-20-evil", sig="none",
                         supersedes="wake-20", body="echo evil"),
                ]),
                # Signed, From the owner, but addressed to someone else:
                # not memory (MHS 170 is To the owner as well).
                node("30 echo misaddressed", "wake-30", body="echo misaddressed",
                     to="other@%s" % host),
                # A superseder whose verify never answers: it must not run,
                # and it must not hand the step back to the version it
                # retired (a review found v1 ran, silently).
                node("40 echo old", "wake-40", body="echo old", children=[
                    node("40 echo new", "wake-40-new", sig="hang",
                         supersedes="wake-40", body="echo new"),
                ]),
                node("50 echo slow", "wake-50", sig="hang", body="echo slow"),
                # v1 <- unsigned v2 <- timed-out v3: v1 still runs (v2 retires
                # nothing), so v3's SKIPPED line must not say otherwise.
                node("60 echo sixty", "wake-60", body="echo sixty", children=[
                    node("60 echo unsigned", "wake-60-u", sig="none",
                         supersedes="wake-60", body="echo unsigned", children=[
                        node("60 echo lag", "wake-60-lag", sig="hang",
                             supersedes="wake-60-u", body="echo lag"),
                    ]),
                ]),
            ]),
        ]),
        node("[LESSONS]", "lessons", [
            node("10 a lesson", "lesson-10"),
            node("[LESSONS]", "lessons-fake", sig="none", supersedes="lessons"),
        ]),
        # `latest` down REPLY chains: it is found newest-first, walking each
        # candidate back to a direct child through member superseders.
        node("[NOTES]", "notes", [
            node("20 early note", "note-20"),
            node("10 note v1", "note-v1", [
                node("10 note v2", "note-v2", supersedes="note-v1", children=[
                    node("10 note v3", "note-v3", sig="none", supersedes="note-v2"),
                ]),
            ]),
            node("30 x v1", "x-v1", [
                node("30 x v2", "x-v2", sig="none", supersedes="x-v1", children=[
                    node("30 x v3", "x-v3", supersedes="x-v2"),
                ]),
            ]),
        ]),
        # An unsigned "Superseded by …" child: the render skipped a whole
        # listing on that subject, with no membership test.
        node("[HIDE]", "hide", [
            node("10 kept item", "hide-10"),
            node("Superseded by nothing", "hide-sup", sig="none"),
        ]),
        # A signed item whose verifier never answers (P19).
        node("[SLOW]", "slow", [
            node("10 slow item", "slow-10", sig="hang"),
            node("20 quick item", "slow-20"),
        ]),
        # The same, for the readers: cat, tree and latest.
        node("[LAGCHAIN]", "lagchain", [
            node("10 chain v1", "chain-v1", [
                node("10 chain v2", "chain-v2", sig="hang", supersedes="chain-v1"),
            ]),
        ]),
        node("[LAGLATEST]", "laglatest", [
            node("05 older", "lag-05"),
            node("10 letter a", "lag-a"),
            node("10 letter b", "lag-b", sig="hang", supersedes="lag-a"),
        ]),
        # Shadows by subject tag and by NAME vN, where the newer is unknown.
        node("[TAGS]", "tags", [
            node("R07 real tag", "tag-r07"),
            node("R07 slow tag", "tag-r07-slow", sig="hang"),
            node("NOTE v1 real", "note-v1-real"),
            node("NOTE v2 slow", "note-v2-slow", sig="hang"),
        ]),
        # A newer duplicate of one number, unknown: not a fallback to the older.
        node("[DUPE]", "dupe", [
            node("10 first", "dupe-10"),
            node("10 first again", "dupe-10-again", sig="hang"),
        ]),
        # The newest item retired only by an UNNUMBERED superseder that timed
        # out: `latest` must refuse, not return "05 older".
        node("[LAGLATEST2]", "laglatest2", [
            node("05 older", "lag2-05"),
            node("10 newest", "lag2-10", [
                node("retract", "lag2-retract", sig="hang", supersedes="lag2-10"),
            ]),
        ]),
        # A timed-out v1 already retired by a signed v2: whatever its
        # verdict, /LAGOLD/10 is v2 (a low review finding).
        node("[LAGOLD]", "lagold", [
            node("10 old v1", "lagold-v1", sig="hang", children=[
                node("10 old v2", "lagold-v2", supersedes="lagold-v1"),
            ]),
        ]),
        # A timed-out duplicate OLDER than the signed one: if it is a member
        # it is a TWIN, so the pick is withheld (SM-698: cat fails closed, as
        # `ls` already said "withheld"); it was "the newer wins either way".
        node("[DUPEOLD]", "dupeold", [
            node("10 stale", "dupeold-stale", sig="hang"),
            node("10 fresh", "dupeold-fresh"),
        ]),
        # All-in and all-out both open "10 a" (b retired, or b not a member),
        # but b in with its retraction out opens "10 b": the corners agree
        # and the walk must still refuse.
        node("[LAGMIX]", "lagmix", [
            node("10 a", "lagmix-a"),
            node("10 b", "lagmix-b", sig="hang", children=[
                node("retitled", "lagmix-retitled", sig="hang", supersedes="lagmix-b"),
            ]),
        ]),
        # Every verdict says AMBIGUOUS ("[notes]" either way, beside
        # "[NOTES]"), but without the timed-out mail "[notes]" is retired and
        # unreplaced, so the walk alone would open "[NOTES]": fail closed.
        node("[SPELL]", "spell", [
            node("[notes]", "spell-lo", [
                node("[notes]", "spell-lo-again", sig="hang", supersedes="spell-lo"),
            ]),
            node("[NOTES]", "spell-up"),
        ]),
        # Two signed twins and a timed-out third: a FORK either way.
        node("[FORK3]", "fork3", [
            node("10 one", "fork3-one"),
            node("10 two", "fork3-two"),
            node("10 three", "fork3-three", sig="hang"),
        ]),
        # Two SIGNED twins and nothing timed out: the plain FORK line. [TWINS]
        # has no fork (its twin is unsigned), so nothing pinned that line
        # (a low review finding).
        node("[FORK2]", "fork2", [
            node("10 one", "fork2-one"),
            node("10 two", "fork2-two"),
        ]),
        # LAG_CAP (8): timed-out mails a signed "10 fresh" RETIRES (one
        # Supersedes: per stale copy, [LAGOLD]'s shape x8) change nothing,
        # so 8 of them are weighed (2^8) and open it; 9 are too many and
        # refuse. Pins the cap at both edges (a low review
        # finding: nothing tested LAG_CAP). SM-698: stale duplicates that
        # are NOT retired ([LAGCAP8]) may be twins, so they now withhold.
        node("[LAGCAP8]", "lagcap8",
             [node("10 stale %d" % n, "lagcap8-%d" % n, sig="hang") for n in range(8)]
             + [node("10 fresh", "lagcap8-fresh")]),
        node("[LAGRET8]", "lagret8",
             [node("10 stale %d" % n, "lagret8-%d" % n, sig="hang") for n in range(8)]
             + [node("10 fresh", "lagret8-fresh", supersedes=["lagret8-%d" % n for n in range(8)])]),
        node("[LAGRET9]", "lagret9",
             [node("10 stale %d" % n, "lagret9-%d" % n, sig="hang") for n in range(9)]
             + [node("10 fresh", "lagret9-fresh", supersedes=["lagret9-%d" % n for n in range(9)])]),
        node("[CONVO]", "convo", convo),
    ]
    if variant == "D":
        # A SIGNED "Superseded by" at the root: everything is retired, and
        # `ls /` must say so rather than "all signed" over nothing.
        kids.append(node("Superseded by the next root", "root-sup-signed"))
    if variant == "E":
        # [BOOT]'s newest letter cannot be verified: --boot and latest must
        # refuse, never fall back to the letter it replaced.
        boot = next(k for k in kids if k[0]["headers"]["Subject"] == "[BOOT]")
        boot[1].append(node("10 LETTER v3", "letter-v3", sig="hang",
                            supersedes="letter-v2"))
    if variant == "F":
        # An unknown root "Superseded by": it may retire the listing, so
        # `ls /` must not show the labels as live.
        kids.append(node("Superseded by the slow root", "root-sup-slow", sig="hang"))
    if variant == "G":
        # [BOOT]'s newest letter retired by an UNNUMBERED unknown superseder.
        boot = next(k for k in kids if k[0]["headers"]["Subject"] == "[BOOT]")
        boot[1].append(node("fixup", "boot-fixup", sig="hang", supersedes="letter-v2"))
        # ...and an older letter nothing supersedes: the fallback to refuse.
        boot[1].insert(0, node("05 early letter", "boot-05"))
    if variant == "H":
        # A newer [ROOT] that cannot be verified: it may be the real root.
        kids.append(node("[ROOT]", "root-slow", sig="hang"))
    if variant == "I":
        # A newer [BOOT] label that cannot be verified.
        kids.append(node("[BOOT]", "boot-slow", sig="hang"))
    if variant == "C":
        # At the ROOT: an unsigned "Superseded by" child hid all of `ls /`
        # ("-1 labels · all signed"), and an unsigned second top-level node
        # in the thread sits at depth 0, where `--index` never looked.
        kids.append(node("Superseded by evil", "root-sup", sig="none"))
    if variant == "B":
        kids.append(node("[ROOT]", "root-hijack", sig="none", children=[
            node("[RULES]", "rules-hijack", sig="none", children=[
                node("10 hijack rule", "rule-hijack", sig="none"),
            ]),
        ]))
    ts[0] = 1000   # both variants share ids and timestamps; B only adds
    root = node("[ROOT]", "mhs-root", kids)
    # node() built children first, so re-stamp in document order: a parent is
    # older than its children, and a later sibling is newer than an earlier one.
    order = []
    def walk(n):
        order.append(n[0])
        for c in n[1]:
            walk(c)
    walk(root)
    for i, m in enumerate(order):
        m["timestamp"] = 2000 + i
    tops = [root]
    if variant == "C":
        # Named [ORPHAN], not after a real label: the probe must see the
        # depth-0 node ITSELF, not a namesake (review: P18 passed with the
        # fix reverted while it was called [RULES]).
        orphan = node("[ORPHAN]", "orphan-rules", sig="none", children=[
            node("10 orphan rule", "orphan-rule", sig="none")])
        orphan[0]["timestamp"] = 9000          # newest in the thread
        orphan[1][0][0]["timestamp"] = 9001
        tops.append(orphan)
    with open(os.path.join(T, "thread-%s.json" % variant), "w") as fh:
        json.dump([tops], fh)
    with open(os.path.join(T, "root-%s.json" % variant), "w") as fh:
        json.dump([[[root[0], []]]], fh)

for v in "ABCDEFGHI":
    build(v)
PY

# ── the stubs ───────────────────────────────────────────────────────────────
# notmuch: --entire-thread=false is the priming call (the root alone), any
# other show a render, with or without bodies (SM-668: a reader asks for
# bodies only when it prints one), search --output=threads the run's thread
# lookup. Anything else is a fixture bug and EXITS 9 rather than answering
# with a plausible tree.
cat > "$BIN/notmuch" <<STUB
#!/usr/bin/env bash
v="\${FIXTURE:-A}"
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
# session: only `session mail-verify FILE [--quiet]`, logged, answered from
# the fixture's X-Fixture-Sig header with the verifier's own exit codes.
cat > "$BIN/session" <<STUB
#!/usr/bin/env bash
[ "\${1:-}" = "mail-verify" ] || { echo "session stub: unexpected: \$*" >&2; exit 9; }
f="\$2"
basename "\$f" >> "$T/verify.log"
sig="\$(sed -n '/^\$/q; s/^X-Fixture-Sig: //p' "\$f")"
case "\$sig" in
  hang)   sleep 30; exit 0 ;;
  good)   exit 0 ;;
  forged) exit 1 ;;
  *)      exit 2 ;;
esac
STUB
chmod +x "$BIN/notmuch" "$BIN/session"

run(){ # run <A|B> <script> <args...>
  local v="$1" script="$2"; shift 2
  : > "$T/verify.log"
  env PATH="$BIN:$PATH" HOME="$T" FIXTURE="$v" SESSION_MAIL_VERIFY_TIMEOUT=1 \
      SESSION_MAIL_VERIFY="$BIN/session mail-verify" \
      CLAUDE_CODE_AGENT="$AGENT" SESSION_AGENT="" \
      READER_ROOT="$ROOTID" \
      "$script" "$@" 2>&1
}
live(){ # live <subject> <output>: the subject printed as a LIVE node, not a ⊘ line
  grep -E -- "^[[:space:]]*(└ )?$(printf '%s' "$1" | sed 's/[][\.*^$]/\\&/g')([[:space:]]|$)" <<<"$2" | grep -vq '⊘'
}

echo "── C1 — CONTROL: the walker lists signed items (green at base)"
out="$(run A "$TREE" /RULES --depth 1)"; rc=$?
[ "$rc" -eq 0 ] && ok "tree /RULES exits 0" || no "tree /RULES exits $rc — THE FIXTURE IS BROKEN:
$out"
live "10 real rule" "$out" && ok "tree /RULES lists 10 real rule" || no "tree /RULES does not list 10 real rule:
$out"

echo "── C2 — CONTROL: a SIGNED supersede replaces in place (green at base)"
# tree, not ls: `ls` cuts at depth 1 BEFORE supersedes are read, so a
# superseder one level down is invisible to it (live: `ls /BOOT` prints
# "FORK: 10 x19"). That is its own defect (SESH-671's family), not this one.
out="$(run A "$TREE" /ASKS)"
live "30 new" "$out" && ok "tree /ASKS shows 30 new" || no "tree /ASKS does not show 30 new:
$out"
live "30 old" "$out" && no "tree /ASKS still shows 30 old" || ok "tree /ASKS hides 30 old"
out="$(run A "$CAT" /ASKS/30)"
has "30 new body" "$out" && ok "cat /ASKS/30 hoists to 30 new" || no "cat /ASKS/30 did not hoist to 30 new:
$out"

echo "── C3 — CONTROL: a signed supersede chain under [WAKE] item 10 is untouched"
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
has "echo one" "$out" && ok "run --list materializes item 10 (echo one)" || no "run --list lost item 10:
$out"

echo "── P1 — H1: ls /RULES shows unsigned and forged items as NOT A MEMBER"
out="$(run A "$LS" /RULES)"
for s in "30 unsigned rule" "40 forged rule"; do
  live "$s" "$out" && no "ls /RULES lists '$s' as live memory:
$out" || ok "ls /RULES does not list '$s' as live"
  has "NOT A MEMBER: $s" "$out" && ok "ls /RULES reports '$s' as ⊘ NOT A MEMBER" || no "ls /RULES does not report '$s' as NOT A MEMBER:
$out"
done

echo "── P2 — H1: tree /RULES the same"
out="$(run A "$TREE" /RULES)"
live "40 forged rule" "$out" && no "tree /RULES lists the forged rule as live:
$out" || ok "tree /RULES does not list the forged rule as live"

echo "── P3 — H1: cat refuses an unsigned or forged item"
for n in 30 40; do
  out="$(run A "$CAT" /RULES/$n)"; rc=$?
  [ "$rc" -ne 0 ] && ok "cat /RULES/$n refuses (rc $rc)" || no "cat /RULES/$n exits 0:
$out"
  has "rule body" "$out" && no "cat /RULES/$n printed the item's body:
$out" || ok "cat /RULES/$n printed no body"
done

echo "── P4 — H2: an unsigned newer twin loses the walk"
out="$(run A "$CAT" /TWINS/20)"; rc=$?
[ "$rc" -eq 0 ] && ok "cat /TWINS/20 exits 0" || no "cat /TWINS/20 exits $rc:
$out"
has "20 real twenty body" "$out" && ok "cat /TWINS/20 opens the signed item" || no "cat /TWINS/20 did not open the signed item:
$out"
has "20 fake twenty body" "$out" && no "cat /TWINS/20 opened the unsigned twin" || ok "cat /TWINS/20 did not open the unsigned twin"

echo "── P5 — H2: an unsigned twin makes no FORK"
out="$(run A "$LS" /TWINS)"
has "FORK" "$out" && no "ls /TWINS reports a FORK made by an unsigned twin:
$out" || ok "ls /TWINS reports no FORK"
live "20 real twenty" "$out" && ok "ls /TWINS shows 20 real twenty" || no "ls /TWINS hides 20 real twenty:
$out"

echo "── P6 — H2: latest is the newest SIGNED item"
out="$(run A "$CAT" /BOOT/latest)"
has "10 LETTER v2 body" "$out" && ok "cat /BOOT/latest opens LETTER v2" || no "cat /BOOT/latest did not open LETTER v2:
$out"
has "LETTER forged body" "$out" && no "cat /BOOT/latest opened the unsigned newest" || ok "cat /BOOT/latest did not open the unsigned newest"

echo "── P7 — H3: an unsigned supersede hides no item (tree, cat)"
out="$(run A "$TREE" /ASKS)"
live "10 real ask" "$out" && ok "tree /ASKS shows 10 real ask" || no "tree /ASKS hides 10 real ask:
$out"
live "10 hijacked ask" "$out" && no "tree /ASKS shows the hijacker as live" || ok "tree /ASKS does not show the hijacker as live"
out="$(run A "$CAT" /ASKS/10)"
has "10 real ask body" "$out" && ok "cat /ASKS/10 opens the signed item" || no "cat /ASKS/10 did not open the signed item:
$out"
has "hijacked ask body" "$out" && no "cat /ASKS/10 hoisted into the unsigned supersede" || ok "cat /ASKS/10 did not hoist into the unsigned supersede"

echo "── P8 — H3: an unsigned subject tag shadows nothing"
out="$(run A "$LS" /RULES)"
live "R05 real rule" "$out" && ok "ls /RULES shows R05 real rule" || no "ls /RULES hides R05 real rule behind an unsigned R05:
$out"

echo "── P9 — H3: an unsigned supersede drops no wake step"
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
has "echo two" "$out" && ok "run --list still materializes item 20 (echo two)" || no "run --list dropped item 20:
$out"
has "echo evil" "$out" && no "run --list lists the unsigned supersede in its place:
$out" || ok "run --list does not list the unsigned supersede"

echo "── P10 — H3: an unsigned supersede hides no label, even under ls /"
out="$(run A "$LS" /)"
live "[LESSONS]" "$out" && ok "ls / shows [LESSONS]" || no "ls / hides [LESSONS]:
$out"
out="$(run A "$LS" /LESSONS)"
live "10 a lesson" "$out" && ok "ls /LESSONS shows 10 a lesson" || no "ls /LESSONS does not show 10 a lesson:
$out"

echo "── P11 — H4: an unsigned [ROOT] reply takes over no path"
out="$(run B "$LS" /RULES)"
live "10 real rule" "$out" && ok "ls /RULES (B) shows 10 real rule" || no "ls /RULES (B) lost the real rules:
$out"
has "hijack rule" "$out" && no "ls /RULES (B) walked the hijacked root:
$out" || ok "ls /RULES (B) did not walk the hijacked root"

echo "── P12 — H4: an unsigned [ROOT] reply takes over no ls /"
out="$(run B "$LS" /)"
for l in "[RULES]" "[ASKS]" "[BOOT]"; do
  live "$l" "$out" && ok "ls / (B) shows $l" || no "ls / (B) does not show $l:
$out"
done

echo "── P14 — H2/H3: the --boot fallback reads the newest SIGNED letter"
# `session mail run`'s DEFAULT when /BOOT/WAKE is unreachable. It took the
# newest item under the last [BOOT] by subject, any sender, and its verify
# then REFUSED the whole wake over an unsigned newer item.
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$HERE/mhs-thread" --boot)"; rc=$?
[ "$rc" -eq 0 ] && ok "--boot exits 0" || no "--boot exits $rc:
$out"
has "10 LETTER v2 body" "$out" && ok "--boot reads LETTER v2" || no "--boot did not read LETTER v2:
$out"

echo "── P15 — H2/H3: latest down reply chains is the newest SURVIVOR"
# The newest signed mail in the label is "30 x v3", but it supersedes an
# unsigned link, so no member chain reaches it from a child: "30 x v1"
# survives, and it is newer than "10 note v2" (whose unsigned v3 is ignored).
out="$(run A "$CAT" /NOTES/latest)"
has "30 x v1 body" "$out" && ok "cat /NOTES/latest opens 30 x v1" || no "cat /NOTES/latest did not open 30 x v1:
$out"
out="$(run A "$CAT" /NOTES/10)"
has "10 note v2 body" "$out" && ok "cat /NOTES/10 hoists to 10 note v2, not the unsigned v3" || no "cat /NOTES/10 did not open 10 note v2:
$out"
out="$(run A "$TREE" /NOTES)"
live "30 x v1" "$out" && ok "tree /NOTES shows 30 x v1 (the same answer, eagerly)" || no "tree /NOTES does not show 30 x v1:
$out"

echo "── P16 — an unsigned \"Superseded by\" child hides no listing"
out="$(run A "$LS" /HIDE)"
live "10 kept item" "$out" && ok "ls /HIDE shows 10 kept item" || no "ls /HIDE hid its listing behind an unsigned 'Superseded by':
$out"

echo "── P17 — ... nor all of ls /, whose summary never says 'all signed' over nothing"
out="$(run C "$LS" /)"
live "[RULES]" "$out" && ok "ls / (C) shows [RULES]" || no "ls / (C) hid its labels behind an unsigned 'Superseded by':
$out"
has "-1 labels" "$out" && no "ls / (C) counts -1 labels:
$out" || ok "ls / (C) counts no negative labels"

echo "── P18 — --index: an unsigned second top-level node is NOT A MEMBER"
out="$(run C ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$HERE/mhs-thread" --index)"
live "[ORPHAN]" "$out" && no "--index shows the unsigned top-level [ORPHAN] as live:
$out" || ok "--index does not show the unsigned top-level [ORPHAN] as live"
has "NOT A MEMBER: [ORPHAN]" "$out" && ok "--index reports [ORPHAN] as ⊘ NOT A MEMBER" || no "--index does not report [ORPHAN]:
$out"
live "[RULES]" "$out" && ok "--index still shows the real [RULES]" || no "--index lost the real [RULES]:
$out"

echo "── P19 — a verifier that never answers is NOT verified, and the read ends"
s0=$(date +%s)
out="$(run A "$LS" /SLOW)"; rc=$?
el=$(( $(date +%s) - s0 ))
[ "$el" -lt 15 ] && ok "ls /SLOW returned in ${el}s (verifier timeout)" || no "ls /SLOW took ${el}s — no timeout on the verifier"
live "10 slow item" "$out" && no "ls /SLOW shows the unverified item as live:
$out" || ok "ls /SLOW does not show the unanswered item as live"
live "20 quick item" "$out" && ok "ls /SLOW still shows 20 quick item" || no "ls /SLOW lost 20 quick item:
$out"

echo "── P20 — run: a wake item To someone else is not memory"
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
if grep -A1 -- 'echo misaddressed' <<<"$out" | grep -q 'NOT VERIFIED' || grep -B1 -- 'echo misaddressed' <<<"$out" | grep -q 'NOT VERIFIED'; then
  ok "run --list marks the misaddressed item NOT VERIFIED"
elif has "echo misaddressed" "$out"; then
  no "run --list would run a wake item addressed to someone else:
$out"
else
  ok "run --list does not list the misaddressed item"
fi

echo "── P21 — a SIGNED root 'Superseded by': ls / says NOTHING SHOWN, not all members"
out="$(run D "$LS" /)"
has "NOTHING SHOWN" "$out" && ok "ls / (D) says NOTHING SHOWN" || no "ls / (D) does not say NOTHING SHOWN:
$out"
# SM-779: the all-members verdict is "all members (reader), signatures verify
# (signer not pinned)"; it was "all reader, signed".
has "all members (reader)" "$out" && no "ls / (D) calls nothing 'all members':
$out" || ok "ls / (D) does not call nothing 'all members'"

echo "── P22 — run: a superseder that cannot be verified runs NEITHER version"
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
if grep -B1 -- '^echo old$' <<<"$out" | grep -q 'NOT VERIFIED' || ! grep -q -- '^echo old$' <<<"$out"; then
  ok "run --list does not offer the retired 'echo old'"
else
  no "run --list un-supersedes 'echo old' when its superseder times out:
$out"
fi
if grep -B1 -- '^echo new$' <<<"$out" | grep -q 'timed out'; then
  ok "run --list skips 'echo new' and names the timeout"
else
  no "run --list does not name the timeout on 'echo new':
$out"
fi

echo "── P23 — run: an item whose verify times out is SKIPPED, and says why"
if grep -B1 -- '^echo slow$' <<<"$out" | grep -q 'timed out'; then
  ok "run --list names the timeout on 'echo slow'"
else
  no "run --list does not name the timeout on 'echo slow':
$out"
fi

echo "── P24 — cat/tree: an unverifiable superseder retires, it does not resurrect"
out="$(run A "$CAT" /LAGCHAIN/10)"; rc=$?
has "10 chain v1 body" "$out" && no "cat /LAGCHAIN/10 fell back to the retired v1:
$out" || ok "cat /LAGCHAIN/10 did not fall back to v1"
[ "$rc" -ne 0 ] && ok "cat /LAGCHAIN/10 refuses (rc $rc)" || no "cat /LAGCHAIN/10 exits 0:
$out"
has "timed out" "$out" && ok "cat /LAGCHAIN/10 names the timeout" || no "cat /LAGCHAIN/10 does not name the timeout:
$out"
out="$(run A "$TREE" /LAGCHAIN)"
live "10 chain v1" "$out" && no "tree /LAGCHAIN shows the retired v1 as live:
$out" || ok "tree /LAGCHAIN does not show v1 as live"
has "UNVERIFIED" "$out" && ok "tree /LAGCHAIN marks v2 UNVERIFIED" || no "tree /LAGCHAIN does not mark v2:
$out"

echo "── P25 — latest: an unverifiable newest item refuses, never an older one"
out="$(run A "$CAT" /LAGLATEST/latest)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /LAGLATEST/latest refuses (rc $rc)" || no "cat /LAGLATEST/latest exits 0:
$out"
if has "05 older body" "$out" || has "10 letter a body" "$out"; then
  no "cat /LAGLATEST/latest fell back to an older item:
$out"
else
  ok "cat /LAGLATEST/latest opened no older item"
fi
has "timed out" "$out" && ok "cat /LAGLATEST/latest names the timeout" || no "cat /LAGLATEST/latest does not name the timeout:
$out"

echo "── P26 — --boot and /BOOT/latest refuse when the newest letter is unverifiable"
out="$(run E ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$HERE/mhs-thread" --boot)"; rc=$?
[ "$rc" -ne 0 ] && ok "--boot (E) refuses (rc $rc)" || no "--boot (E) exits 0:
$out"
has "LETTER v2 body" "$out" && no "--boot (E) fell back to LETTER v2:
$out" || ok "--boot (E) did not fall back to LETTER v2"
has "timed out" "$out" && ok "--boot (E) names the timeout" || no "--boot (E) does not name the timeout:
$out"
out="$(run E "$CAT" /BOOT/latest)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /BOOT/latest (E) refuses (rc $rc)" || no "cat /BOOT/latest (E) exits 0:
$out"
has "LETTER v2 body" "$out" && no "cat /BOOT/latest (E) fell back to LETTER v2" || ok "cat /BOOT/latest (E) did not fall back"

echo "── P27 — an unknown root 'Superseded by' withholds ls /, and says why"
out="$(run F "$LS" /)"
live "[RULES]" "$out" && no "ls / (F) shows labels live under an unverifiable 'Superseded by':
$out" || ok "ls / (F) shows no label live"
has "timed out" "$out" && ok "ls / (F) names the timeout" || no "ls / (F) does not name the timeout:
$out"

echo "── P28 — an unknown newer tag or NAME vN still shadows the older"
out="$(run A "$LS" /TAGS)"
live "R07 real tag" "$out" && no "ls /TAGS shows R07 real tag though a newer R07 is unverified:
$out" || ok "ls /TAGS: R07 real tag stays shadowed"
live "NOTE v1 real" "$out" && no "ls /TAGS shows NOTE v1 though NOTE v2 is unverified:
$out" || ok "ls /TAGS: NOTE v1 stays shadowed"

echo "── P29 — a newer unknown duplicate: cat refuses, never opens the older"
out="$(run A "$CAT" /DUPE/10)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /DUPE/10 refuses (rc $rc)" || no "cat /DUPE/10 exits 0:
$out"
has "10 first body" "$out" && no "cat /DUPE/10 opened the older duplicate" || ok "cat /DUPE/10 did not open the older duplicate"
has "timed out" "$out" && ok "cat /DUPE/10 names the timeout" || no "cat /DUPE/10 does not name the timeout:
$out"

echo "── P30 — latest: an unknown UNNUMBERED superseder of the newest refuses"
out="$(run A "$CAT" /LAGLATEST2/latest)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /LAGLATEST2/latest refuses (rc $rc)" || no "cat /LAGLATEST2/latest exits 0:
$out"
has "05 older body" "$out" && no "cat /LAGLATEST2/latest fell back to 05 older" || ok "cat /LAGLATEST2/latest did not fall back"

echo "── P31 — --boot: an unknown UNNUMBERED superseder of the newest letter refuses"
out="$(run G ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$HERE/mhs-thread" --boot)"; rc=$?
[ "$rc" -ne 0 ] && ok "--boot (G) refuses (rc $rc)" || no "--boot (G) exits 0:
$out"
if has "LETTER v1 body" "$out" || has "05 early letter body" "$out"; then
  no "--boot (G) fell back to an older letter:
$out"
else
  ok "--boot (G) did not fall back"
fi
has "timed out" "$out" && ok "--boot (G) names the timeout" || no "--boot (G) does not name the timeout:
$out"

echo "── P32 — an unknown newer [ROOT] or [BOOT] is not skipped for an older one"
out="$(run H "$LS" /)"; rc=$?
[ "$rc" -ne 0 ] && ok "ls / (H) refuses (rc $rc)" || no "ls / (H) exits 0 past an unverifiable newer [ROOT]:
$out"
out="$(run H "$LS" /RULES)"; rc=$?
[ "$rc" -ne 0 ] && ok "ls /RULES (H) refuses (rc $rc)" || no "ls /RULES (H) exits 0 past an unverifiable newer [ROOT]:
$out"
has "timed out" "$out" && ok "ls /RULES (H) names the timeout" || no "ls /RULES (H) does not name the timeout:
$out"
out="$(run I ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$HERE/mhs-thread" --boot)"; rc=$?
[ "$rc" -ne 0 ] && ok "--boot (I) refuses (rc $rc)" || no "--boot (I) exits 0 past an unverifiable newer [BOOT]:
$out"
has "timed out" "$out" && ok "--boot (I) names the timeout" || no "--boot (I) does not name the timeout:
$out"

echo "── P33 — run: a SKIPPED line claims only what is true"
out="$(run A ${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN" /BOOT/WAKE --list)"
if grep -B1 -- '^echo sixty$' <<<"$out" | grep -q 'NOT VERIFIED'; then
  no "run --list skips 'echo sixty', which nothing verified retires:
$out"
else
  ok "run --list still runs 'echo sixty'"
fi
if grep -B1 -- '^echo lag$' <<<"$out" | grep -q 'does not run either'; then
  no "run --list says item 60 does not run, while 'echo sixty' does:
$out"
else
  ok "run --list does not claim item 60 is retired"
fi

echo "── P34 — a timed-out v1 already retired by a signed v2: cat opens v2"
out="$(run A "$CAT" /LAGOLD/10)"; rc=$?
[ "$rc" -eq 0 ] && ok "cat /LAGOLD/10 exits 0" || no "cat /LAGOLD/10 exits $rc:
$out"
has "10 old v2 body" "$out" && ok "cat /LAGOLD/10 opens v2" || no "cat /LAGOLD/10 does not open v2:
$out"
has "10 old v1 body" "$out" && no "cat /LAGOLD/10 opened the retired v1:
$out" || ok "cat /LAGOLD/10 does not open v1"

echo "── P35 — an OLDER timed-out duplicate may be a twin: cat withholds (SM-698)"
out="$(run A "$CAT" /DUPEOLD/10)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /DUPEOLD/10 refuses (rc $rc), as ls says withheld" || no "cat /DUPEOLD/10 exits 0:
$out"
if has "10 fresh body" "$out" || has "10 stale body" "$out"; then
  no "cat /DUPEOLD/10 opened a copy:
$out"
else
  ok "cat /DUPEOLD/10 opened neither copy"
fi
has "timed out" "$out" && ok "cat /DUPEOLD/10 names the timeout" || no "cat /DUPEOLD/10 does not name the timeout:
$out"

echo "── P36 — the corners agree, a mixed verdict does not: cat refuses"
out="$(run A "$CAT" /LAGMIX/10)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /LAGMIX/10 refuses (rc $rc)" || no "cat /LAGMIX/10 exits 0:
$out"
if has "10 a body" "$out" || has "10 b body" "$out"; then
  no "cat /LAGMIX/10 opened an item:
$out"
else
  ok "cat /LAGMIX/10 opened nothing"
fi
has "may change which item this opens" "$out" && ok "cat /LAGMIX/10 says the timeout may change the answer" \
  || no "cat /LAGMIX/10 does not say why:
$out"

echo "── P37 — every verdict says AMBIGUOUS, the walk alone would open one: refuse"
out="$(run A "$CAT" /SPELL/Notes)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /SPELL/Notes refuses (rc $rc)" || no "cat /SPELL/Notes exits 0:
$out"
has "[NOTES] body" "$out" && no "cat /SPELL/Notes opened [NOTES]:
$out" || ok "cat /SPELL/Notes did not open [NOTES]"
has "timed out" "$out" && ok "cat /SPELL/Notes names the timeout" || no "cat /SPELL/Notes does not name the timeout:
$out"

echo "── P38 — a FORK line names the timed-out twin"
out="$(run A "$LS" /DUPE)"
has "10 x2 withheld — 1 UNVERIFIED (verify timed out" "$out" && ok "ls /DUPE: the pick is withheld, and it names the timeout" \
  || no "ls /DUPE does not name the timeout:
$out"
has "FORK: 10" "$out" && no "ls /DUPE calls one signed item and a timeout a FORK:
$out" || ok "ls /DUPE does not call it a FORK"
out="$(run A "$LS" /DUPEOLD)"
has "10 x2 withheld — 1 UNVERIFIED (verify timed out" "$out" && ok "ls /DUPEOLD: withheld, and it names the timeout" \
  || no "ls /DUPEOLD does not name the timeout:
$out"
out="$(run A "$LS" /FORK3)"
has "FORK: 10 x3, 1 UNVERIFIED (verify timed out" "$out" && ok "ls /FORK3: a FORK, and it names the timeout" \
  || no "ls /FORK3 does not say FORK x3 with the timeout:
$out"
out="$(run A "$LS" /TWINS)"
has "UNVERIFIED" "$out" && no "ls /TWINS names a timeout it does not have:
$out" || ok "ls /TWINS names no timeout"

echo "── P39 — LAG_CAP: 8 timed-out mails are weighed, 9 are too many"
out="$(run A "$CAT" /LAGCAP8/10)"; rc=$?
if [ "$rc" -ne 0 ] && ! has "10 fresh body" "$out"; then
  ok "cat /LAGCAP8/10 withholds (rc $rc): 8 unretired stale copies may be twins (SM-698)"
else
  no "cat /LAGCAP8/10 opened a copy past 8 possible twins (rc $rc):
$out"
fi
out="$(run A "$CAT" /LAGRET8/10)"; rc=$?
[ "$rc" -eq 0 ] && has "10 fresh body" "$out" && ok "cat /LAGRET8/10 weighs 8 retired timed-out mails and opens 10 fresh" \
  || no "cat /LAGRET8/10 does not open 10 fresh (rc $rc):
$out"
out="$(run A "$CAT" /LAGRET9/10)"; rc=$?
[ "$rc" -ne 0 ] && ok "cat /LAGRET9/10 refuses (rc $rc)" || no "cat /LAGRET9/10 exits 0:
$out"
has "10 fresh body" "$out" && no "cat /LAGRET9/10 opened 10 fresh past the cap:
$out" || ok "cat /LAGRET9/10 opened nothing"
has "too many to weigh (9 > 8)" "$out" && ok "cat /LAGRET9/10 says 9 is past the cap of 8" \
  || no "cat /LAGRET9/10 does not name the cap:
$out"

echo "── P40 — two signed twins, nothing timed out: the plain FORK line"
out="$(run A "$LS" /FORK2)"
has "FORK: 10 x2 — refusing to pick (MHS 110); supersede one" "$out" && ok "ls /FORK2 prints the plain FORK line" \
  || no "ls /FORK2 does not print the plain FORK line:
$out"
has "UNVERIFIED" "$out" && no "ls /FORK2 names a timeout it does not have:
$out" || ok "ls /FORK2 names no timeout"

echo "── P13 — cost: a path read verifies only what it touched"
out="$(run A "$CAT" /ASKS/10)"
n="$(grep -c '^convo-' "$T/verify.log" || true)"
[ "$n" -eq 0 ] && ok "cat /ASKS/10 verified no [CONVO] mail" || no "cat /ASKS/10 verified $n [CONVO] mails it never walked"
total="$(wc -l < "$T/verify.log" | tr -d ' ')"
echo "      (cat /ASKS/10 ran the verifier $total times)"

echo "── $pass passed, $fail failed"
[ "$fail" -eq 0 ]
