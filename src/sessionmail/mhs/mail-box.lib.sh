# mail-box.lib.sh — THE BOX RAIL: one box-name resolution, for every verb that
# touches a maildir BY NAME: the read verbs and the filedrop write path.
#
# It began as the read-side rail; session-mail-send's filedrop transport joined
# it because the write side had the mirror-image disease — it MINTED the box it
# could not find (`mkdir -p`, exit 0), so a mistyped recipient created a second
# spelling of a live identity instead of a message. Read and write now agree on
# what a name means, which is the only way "one physical box, one name" can
# hold: a resolver used by half the tools is a convention, not a rail.
#
# NOT A VERB, AND IT CANNOT BECOME ONE. `bin/session` and `session-mail` build
# their verb tables by globbing `session-*` / `session-mail-*` in this
# directory, so a file named `session-mail-resolve` would be dispatchable the
# moment anybody chmod +x'd it. This name cannot be reached by either glob —
# the guarantee is structural, not a mode bit somebody has to remember. Same
# reasoning, and the same naming trick, as mail-claim.lib.sh next door.
#
# It is SOURCED by session-mail-list, session-mail-read and session-mail-send
# (filedrop transport only — the smtp path never writes a maildir), and
# EXECUTED as a one-shot CLI by session-mail-wake (python — it cannot source
# bash). Both entry points run the same function, so there is exactly one
# resolution and exactly one refusal wording in the tree.
#
# ── WHAT WENT WRONG, MEASURED ───────────────────────────────────────────────
# Against a root holding only the full-uuid box, asked for the 8-hex short
# name:
#
#   session mail list  <short>     -> "<short>: no messages"          exit 0
#   session mail read  <short> 1   -> "no message at index 1 (mailbox
#                                      has 0)"  — blames the INDEX,   exit 2
#   session mail wake  <short>     -> a complete, confident briefing:
#                                     "no shelf mail — predecessor
#                                      left nothing"                  exit 0
#   session mail wait  <short>     -> "no such mailbox '<short>' …"   exit 1
#
# Only `wait` was honest, and `wake` was the worst of the three: a successor
# booting with a short session id is told, exit 0, that its predecessor left
# nothing — precisely the failure the succession design exists to close.
#
# Root cause: `resolve_mbox` was duplicated in list and read, and BOTH fell
# through to `printf '%s' "$ROOT/$lc_me"` for a box that does not exist, while
# wake did the same by construction with no existence check at all. A path is
# not a box. This file is the retired `wait` verb's loud refusal (its
# `[ -d "$MBOX" ] || die` guard), generalised and shared — and now
# `wait` no longer keeps that copy: it resolves through THIS function like
# every other verb, appending only its own "arming would wait forever" clause
# to the refusal. Five verbs, one derivation. That mattered the moment the
# nested layout arrived: a fix here would otherwise have reached four of them
# and left the fifth deaf, which is what happened.
#
# ── THE TWO RULES ───────────────────────────────────────────────────────────
# 1. EXPAND at the edge. Short forms are what humans type (and what shell
#    completion offers). A recognised short session id, i.e. a hex
#    token that is the unique prefix of a full-uuid box on disk, resolves to
#    that box. Expansion happens once, here, at the edge; nothing downstream
#    ever sees two spellings.
# 2. REFUSE LOUDLY otherwise — non-zero, on stderr, NAMING the box and the
#    root, never a confident empty. A quiet non-delivery on this substrate is
#    the disease, not the fix.
#
# An EXISTING box with no mail is still "no messages", exit 0. Empty and
# absent are different answers, and telling them apart is the whole point.
#
# ── SCOPE ────────────────────────────────────────────────────────────────────
# Resolution and refusal only. No verb redesign; no output change beyond the
# refusal. This file resolves a NAME to a PATH and never touches a message.

# Case-insensitive lowercase fold — the on-disk canonical form is lowercase
# (what send/wait write), so every comparison happens down here.
_mbl_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# ── THE SESSION-ID SHAPE: THE ONE COPY IN THE TREE ────────────────────────────
# A full session id: 8-4-4-4-12 lowercase hex.
#
# TWO ANCHORINGS, ONE LITERAL. `_MBL_UUID_BODY` is the shape; `_MBL_UUID_RE` is
# the same shape anchored, ASSIGNED FROM the body rather than written out a
# second time. Callers asking "IS this string a session id" take the anchored
# form; callers asking the weaker "does a session id appear ANYWHERE inside
# this string" (the retired `wait` verb's hearing banner, via `grep -Eo`) take the
# body. Two INDEPENDENT constants would be a second copy that can drift —
# somebody fixes one anchoring and not the other and the two disagree in
# silence — and drift in this shape means "a session id" meaning two different
# things inside one tree, which is the defect R-D exists to delete rather than
# relocate.
#   ⭐ Prefer a construction that CANNOT express the failure over a rule that
#      forbids it. Derived, the two anchorings cannot disagree; two constants
#      would need somebody to remember.
# The derived anchored value is pinned byte-for-byte against its pre-hoist
# literal in test_session_mail_shape_hoist.sh, so "same bytes at runtime"
# is a property the suite defends rather than a promise in a mail.
#
# ⚠️ `_MBL_UUID_RE` IS CONSUMED ACROSS A FILE BOUNDARY, BY NAME AND BY VALUE.
# `mail-ext.lib.sh` sources this file BY PATH and refuses to
# classify anything at all if the symbol is not defined (its
# `mail_ext_load_shape_rule` guard). The name and the value are a contract:
# do not rename it, and do not "tidy" the derivation back into a literal.
#
# WHO READS THIS SHAPE, after the hoist: the retired `wait` verb (`is_session_ext`,
# and the banner's containment question), session-mail-arm (the "my session id
# must actually BE one" refusal), mail_box_resolve below, and
# session-mail-list / session-mail-wake through `mail-ext.lib.sh`'s seam. Before the
# hoist there were THREE literal copies of these bytes — here,
# the retired `wait` verb's, and `session-mail-arm:223`, the copy nobody had
# named — and one of them had already drifted in living memory (the short
# `^[0-9a-f]{8}$` form; the retired `wait` verb's own comment records it).
#
# ⛔ AND THE COST OF THE DERIVATION, MEASURED AFTER IT LANDED — the
# one thing to know before editing these two lines. A composition has a THIRD
# state a literal never had: PRESENT, DERIVED AND WRONG. If `_MBL_UUID_BODY`
# were ever emptied or removed, `_MBL_UUID_RE` composes to `^$` — which passes
# a `[ -n "${_MBL_UUID_RE:-}" ]` presence check (575's loader has one), and
# matches NOTHING at all. Every directed address would then classify as an
# ordinary tag, silently, behind a guard reporting the rail healthy. The
# derivation STAYS (drift between two independent constants is the likelier
# failure, and 575 says they would make the same call again), and the defence
# is a VALUE check, never a presence check:
#   * here: `test_session_mail_shape_hoist.sh` T1 pins this composed value
#     byte-for-byte, and T3 runs the rail's agreed fixture pair —
#     KNOWN-GOOD `7ed6b16b-31bd-fe66-35a0-284b56bbe9ec` MUST match,
#     KNOWN-BAD `not-a-session-id` MUST NOT. If a change to this shape stops
#     the known-good id matching, that is a BREAKING CHANGE to the rail and its
#     consumers must be told BEFORE it lands.
#   * downstream: each consumer validates what it loaded rather than that it
#     loaded something.
_MBL_UUID_BODY='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_MBL_UUID_RE="^${_MBL_UUID_BODY}$"
# The SAME rule as a `case` glob, for the per-directory test. Identical shape,
# no processes: a `printf | grep` inside the scan loop costs two forks PER BOX,
# and on the live root (595 boxes, and every one of them a full uuid once this
# task's re-key lands) that was ~2400 forks and 6.5 s for the SHORT-FORM path —
# the edge convenience this rail exists to protect. The input name is still
# matched with the regex above: that is one test, not one per box.
_MBL_H4='[0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
_MBL_UUID_GLOB="${_MBL_H4}${_MBL_H4}-${_MBL_H4}-${_MBL_H4}-${_MBL_H4}-${_MBL_H4}${_MBL_H4}${_MBL_H4}"
# A short session id as humans type it: bare hex, shorter than a full uuid.
# The floor of 4 is deliberate — a 1-3 char hex token ("ab") is an ambiguous
# prefix of almost anything, and guessing from it is exactly the kind of
# helpfulness this rail exists to remove. Shell completion (which expands
# BEFORE the verb sees the word) covers the shorter forms.
_MBL_SHORT_RE='^[0-9a-f]{4,31}$'

# ── THE STORE-DIRECTORY MARKER (nested-v1) ───────────────────────────────────
# A directory carrying this FILE is a STORE DIRECTORY: a path component whose
# CHILDREN are boxes, and never a box itself. The name is shared with
# `taskmaster.core.mailbox.STORE_MARKER` (which WRITES it) and with
# `vmailbox-gen`'s `STORE_MARKER` (which reads it to emit the nested map pairs).
# Three readers, one spelling: change it here and the resolver stops agreeing
# with the generator, which means resolving to a box postfix does not deliver to.
_MBL_STORE_MARKER='.tm-store'

# mbl_directed_ext_sid <extension> — the session id that a DIRECTED address's
# extension carries, or rc 1 if it carries none. Pure: no identity, no
# environment, no I/O, no maildir. It answers a SHAPE question and nothing else.
#
#   <sid>                -> <sid>
#   worker.<sid>         -> <sid>
#   tm-123.worker.<sid>  -> <sid>
#   worker.notauuid      -> nothing, rc 1
#
# It landed in the retired `wait` verb as `directed_ext_sid` and is
# MOVED here unchanged by R-D — the SHAPE is shared, the CLASSIFIER is not.
# Deciding whether such an address is MINE needs the caller's own session id,
# and this file is the box rail: it resolves names, it does not know who it is.
# So the pure half lives here, next to the shape it is made of, and each
# consumer keeps its own four-line classifier over it.
#
# THE PART THAT IS EASY TO GET WRONG: it is the LAST dot-separated segment, at
# ANY arity. `${ext##*.}` is `awk -F. '{print $NF}'` semantics in pure shell;
# verified equal to awk on a / a.b / a.b.c / worker.SID / tm-123.worker.SID /
# notauuid / 'worker.' / '.SID', fed from a shell variable, i.e. with NO
# trailing newline — the form this code actually runs in.
#   * NOT a positional segment. The ruled grammar is extension =
#     [<task-id>.]<role>, so `tm-123.worker.<sid>` is a legitimate name and a
#     split-on-the-FIRST-dot reader would test `worker` as the id.
#   * NOT an arity threshold. "Three segments means another session" would
#     refuse a seat's OWN directed mail at arity 3 — a seat going quietly deaf
#     to mail it is entitled to claim, which is invisible from the inside.
#   * NOT an enumeration of roles. That is a grammar decision; this is a shape.
# ONE SHAPE REUSED: `$_MBL_UUID_RE` above, never a second literal. The defect
# this closes was never in the regex — it is in WHAT IS FED TO IT, so this is a
# segment selector and nothing else.
mbl_directed_ext_sid() {
  local _tail="${1:-}"; _tail="${_tail##*.}"
  printf '%s' "$_tail" | grep -Eq "$_MBL_UUID_RE" || return 1
  printf '%s' "$_tail"
}

# _mbl_two_boxes <root> <lc-name>
#   rc 0: two or more directories answer to <lc-name> (the flat `$root/<name>`
#         and/or `$root/<store>/<name>` under marker stores). MAIL_BOX_PATH
#         holds the one whose relative path sorts first in C order, the one
#         postfix delivers to, and ONE line on stderr names every candidate.
#   rc 1: zero or one candidate; nothing is set or printed.
# Bash 3.2 safe: no `[[ < ]]` (it collates by locale); the order is `sort`'s,
# with LC_ALL=C, as the generator's.
# Bash 4.4+ safe: every local that is read before it is assigned is given a
# value here. Under the CLI's `set -u`, bash 4.4+ reads a bare `local x` as
# UNSET (3.2 reads it as empty), so a bare `first` made the CLI exit 1 at the
# `[ -z "$first" ]` below, exactly when two boxes answered.
_mbl_two_boxes() {
  local root="$1" me="$2" c s rels="" n=0 first="" others=""
  [ -d "$root/$me" ] && { rels="$me/"; n=1; }
  # The glob skips dot-directories, as the generator does; an unmatched glob
  # stays literal and fails the -d test.
  for c in "$root"/*/"$me"/; do
    [ -d "$c" ] || continue
    s="${c%/}"; s="${s%/*}"                   # the store directory
    [ -f "$s/$_MBL_STORE_MARKER" ] || continue
    rels="$rels
${s##*/}/$me/"; n=$((n + 1))
  done
  [ "$n" -ge 2 ] || return 1
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    if [ -z "$first" ]; then first="$c"; continue; fi
    others="${others:+$others, }$root/${c%/}"
  done <<EOF
$(printf '%s\n' "$rels" | LC_ALL=C sort)
EOF
  MAIL_BOX_PATH="$root/${first%/}"
  printf "mail-box: '%s' is %d boxes; postfix delivers to the first in C order, %s, so it resolves there. Never delivered to: %s. Move it aside.\n" \
    "$me" "$n" "$MAIL_BOX_PATH" "$others" >&2
  return 0
}

# mail_box_resolve <name> <root>
#   rc 0: the resolved maildir path is in $MAIL_BOX_PATH
#   rc 1: nothing resolved — the refusal sentence is in $MAIL_BOX_ERR
#
# It ASSIGNS rather than prints, deliberately: `X="$(resolve …)" || die
# "$MAIL_BOX_ERR"` would read the error out of a subshell that has already
# exited, and die with an empty message — the silent failure this file exists
# to abolish, reintroduced in its own call site.
#
# Resolution order (first hit wins):
#   0. TWO BOXES OF ONE NAME (flat and/or under marker stores): the one
#      postfix delivers to, by the generator's C-order rule, named on stderr
#   1. the canonical lowercase directory — FLAT, `$root/<name>`, a maildir
#      (`new/` and `cur/`)
#   2. NESTED: `$root/<store>/<name>`, for a `<store>` carrying the
#      `.tm-store` marker file — marker-bearing directories only, exactly one
#      level deep, no inference
#   3. a legacy mixed-case directory already on disk
#   4. EXPANSION: a short hex form that is the unique prefix of exactly one
#      full-uuid box
# …then refuse.
mail_box_resolve() {
  MAIL_BOX_ERR=""; MAIL_BOX_PATH=""
  local name="$1" root="$2" lc_me d b n hits=0 hit="" cand_list="" store

  # 0 — AN EMPTY NAME IS NOT A BOX, and it is the same disease wearing the
  # plainest hat. "$root/" is a directory, so step 1 would succeed and hand
  # back the ROOT ITSELF: measured, `list ""` answers "root: no messages" at
  # exit 0, `read "" 1` blames the index, and `wake ""` prints the entire
  # confident briefing — "predecessor left nothing" — at exit 0. That is the
  # whole silent-empty family, reachable any time a caller interpolates a
  # variable that turned out to be unset (`session mail wake "$SID"`), which
  # is exactly how a successor boots with no id. Byte-identical at 94f2e14,
  # so it predates this rail rather than being introduced by it — but the
  # cure belongs in the one place resolution now lives, not in three verbs.
  if [ -z "$name" ]; then
    MAIL_BOX_ERR="no box name given — an empty name is not a mailbox (${root} is the root directory, not a box). Name the box you mean."
    return 1
  fi

  lc_me="$(_mbl_lc "$name")"

  # 0b — TWO BOXES OF ONE NAME: resolve to the one postfix delivers to.
  # A flat `$root/<name>` beside a marker store's `$root/<store>/<name>` (or
  # two stores holding one name) gives the generator two map lines with one
  # key. It pipes them through `LC_ALL=C sort -u`, and `postmap` keeps the
  # FIRST, so delivery goes to whichever RELATIVE PATH sorts first in C order
  # (`<name>/` against `<store>/<name>/`). That rule is read from the TREE
  # here, exactly as the generator applies it, so it agrees with the map by
  # construction without reading the map. The fixed flat-first tie-break
  # below was NOT delivery-accurate: a stray flat copy of a project box that
  # nests under a store sorting before it (`kit-store/` < `mail/`) took every
  # read, while every delivery went to the nested box. The collision is
  # named on stderr, once, because it is a fault to clear rather than a
  # layout: provisioning refuses to create it, so a second box got there
  # some other way.
  _mbl_two_boxes "$root" "$lc_me" && return 0

  # 1 — canonical, FLAT: a MAILDIR, `new/` and `cur/` both. This is the
  # Python rail's `_is_maildir` (`_maildir.resolve_box`), so `message list`
  # and this rail accept the same directory. A bare directory of the name
  # that is not a maildir does not win here; step 2 below still takes it
  # when nothing else answers, so a lone partial box resolves as before.
  if [ -d "$root/$lc_me/new" ] && [ -d "$root/$lc_me/cur" ]; then
    MAIL_BOX_PATH="$root/$lc_me"; return 0
  fi

  # 1b — NESTED: `$root/<store>/<name>`.
  #
  # Under the nested layout (nested-v1) a PROJECT box
  # lives one level down, under a STORE DIRECTORY, and postfix delivers there:
  # the patched generator emits `<project>@<host>  agents/<store>/<project>/`.
  # The map is keyed on the BARE project name, so resolving a bare name here is
  # correct and intended — a caller never types `<store>/<project>`.
  #
  # WHY THIS EXISTS: measured on a live fleet host,
  # 3/3 probe
  # mails landed in `agents/tm3713-r12-throwaway/r12probe/` and 0 were claimed —
  # every verb on this rail refused a box that mail was resting in. Deliverable
  # but unlistenable is a known class; this step closes it for
  # read/list/send/wake, and the retired `wait` verb joins the rail in the same change
  # so there is ONE derivation rather than two.
  #
  # THE RULE, AND THE THREE HALVES OF IT THAT ARE LOAD-BEARING:
  #   * MARKER OVER INFERENCE. Only a directory holding the `.tm-store` FILE is
  #     a store. Never "a directory that contains directories" — the generator
  #     says it outright ("a flat box that happens to contain a subdirectory is
  #     never re-read as a store"), and inference would re-read any flat box
  #     with a stray subdir as a path component and resolve names out of it.
  #   * EXACTLY ONE LEVEL. The generator descends one level and no further, so
  #     a deeper directory is not a box and must not resolve to one.
  #   * NO FIXED TIE-BREAK. A flat box and a nested project whose names
  #     sanitize alike produce two map lines with one key; the map is
  #     `LC_ALL=C sort -u`'d and `postmap` keeps the FIRST. `sort` compares the
  #     WHOLE line, so past the common `agents/` it is `<name>/` against
  #     `<store>/<name>/`, and flat wins only when the store name sorts AFTER
  #     the box name:
  #
  #        agents/foo/  vs  agents/store1/foo/   -> flat   line first
  #        agents/foo/  vs  agents/astore/foo/   -> NESTED line first
  #
  #     So neither fixed precedence is delivery-accurate. This step once used
  #     flat-first and was wrong in the second case: a stray flat copy took
  #     every read while every delivery went nested. Step 0b now applies the
  #     generator's own order, read from the tree, before this step runs, so
  #     by the time we are here the name has at most one candidate.
  #
  #     ⇒ THE COLLISION REFUSAL AT PROVISIONING STILL MATTERS. Resolving a
  #     collision correctly is not the same as it being harmless: the second
  #     box holds mail nobody routes to it, and step 0b says so on stderr.
  #
  # We read the TREE, never the postfix map: the map is generated FROM this
  # tree (a downstream cache that can be stale), its keys are host-qualified,
  # and it only exists on a postfix host at /etc/postfix/agents.d/vmailbox —
  # so a map lookup would break every test, sandbox and $SESSIONMAIL_ROOT.
  # Reading the tree with the generator's own marker rule agrees with the map
  # BY CONSTRUCTION, and keeps this rail dependency-free.
  #
  # No `[ -d "$root" ]` guard is needed: an unmatched glob stays literal, and
  # the marker test then fails. Dotted children are skipped by the glob itself,
  # which is the generator's `case "$subname" in .*) continue` in shell form.
  #
  # ⚠️ KNOWN RESIDUAL, STATED RATHER THAN SILENTLY ARBITRATED.
  # A STORE DIRECTORY IS NOT A BOX — the generator says so and acts on it: it
  # emits NO `<store>@<host>` pair, so a store name is UNROUTED and nothing can
  # ever be delivered to it. Step 1 above, unchanged here, still answers
  # `$root/<store>` for the bare store NAME, because it asks only "is that a
  # directory". So `list <store>` reports an empty box, a filedrop `send
  # <store>` writes into a maildir no address reaches, and `wait <store>` arms
  # on it forever — the same defect class in mirror image, one tier up.
  # It is HARMLESS TODAY (no store directory exists outside a test until the
  # nested-layout cutover) and it is NOT fixed here: the resolution rule was
  # decided as ONE new step with steps 1/3/4/5 untouched, and widening it is a
  # separate decision. Do not read this comment as the hazard being handled.
  for store in "$root"/*/; do
    [ -f "${store}${_MBL_STORE_MARKER}" ] || continue
    if [ -d "${store}${lc_me}" ]; then MAIL_BOX_PATH="${store}${lc_me}"; return 0; fi
  done

  # 2 and 3 in ONE pass over the root — a legacy mixed-case box (§5.7) still
  # wins outright the moment it is seen, exactly as when these were two loops,
  # and expansion candidates are collected on the way past. One pass, and no
  # subprocess per box: the name is cut with parameter expansion rather than
  # `basename`, and it is only handed to `tr` when it actually CONTAINS
  # uppercase to fold (a lowercase name is its own fold, so the compare is
  # byte-for-byte). Real roots are all-lowercase, so the fold fires ~never.
  local want_expand=0
  if printf '%s' "$lc_me" | grep -qE "$_MBL_SHORT_RE"; then want_expand=1; fi

  if [ -d "$root" ]; then
    for d in "$root"/*/; do
      [ -d "$d" ] || continue
      b="${d%/}"; b="${b##*/}"
      n="$b"
      case "$b" in *[[:upper:]]*) n="$(_mbl_lc "$b")" ;; esac

      # 2 — legacy mixed-case box on disk.
      if [ "$n" = "$lc_me" ]; then MAIL_BOX_PATH="${d%/}"; return 0; fi

      # 3 — EXPANSION: short session id -> the full-uuid box it names.
      [ "$want_expand" -eq 1 ] || continue
      # Deliberately UNQUOTED: the variable holds a glob and must be read as
      # one. shellcheck SC2254 warns about exactly the accident this is not.
      # shellcheck disable=SC2254
      case "$n" in $_MBL_UUID_GLOB) ;; *) continue ;; esac
      case "$n" in
        "$lc_me"*)
          hits=$((hits+1)); hit="${d%/}"
          if [ "$hits" -le 4 ]; then cand_list="${cand_list:+$cand_list, }$n"; fi
          ;;
      esac
    done
  fi

  if [ "$want_expand" -eq 1 ]; then
    if [ "$hits" -eq 1 ]; then MAIL_BOX_PATH="$hit"; return 0; fi
    if [ "$hits" -gt 1 ]; then
      MAIL_BOX_ERR="ambiguous box '${name}' under ${root} — ${hits} session boxes start with it (${cand_list}$([ "$hits" -gt 4 ] && printf ', …')). Name one in full; a short form must identify exactly one box."
      return 1
    fi
  fi

  # Refuse, loudly, naming the box — plus whatever near-miss we can see, so
  # the caller is told the ONE thing that would have worked.
  local hint=""
  case "$lc_me" in
    *+*)
      # `+extension` addresses are delivered to the BASE box; there is no such
      # directory, and there never was. Say so rather than let it read as a
      # missing box (this is a member of the same silent-empty family).
      local base="${lc_me%%+*}"
      if [ -d "$root/$base" ]; then
        hint=" '${lc_me}' is a +extension ADDRESS, not a box — its mail is delivered to '${base}', which does exist; read that."
      else
        hint=" ('${lc_me}' is a +extension address, not a box — mail to it is delivered to '${base}')."
      fi
      ;;
    *)
      if printf '%s' "$lc_me" | grep -qE "$_MBL_UUID_RE" && [ -d "$root/${lc_me:0:8}" ]; then
        # A HISTORY box: short-named boxes of retired sessions keep their
        # names, so a full uuid can legitimately
        # miss while its 8-hex ancestor is right there. Name it; do not
        # resolve to it — that would re-mint the two-spellings condition the
        # task abolishes.
        hint=" A HISTORY box '${lc_me:0:8}' exists (retired sessions keep their short names) — use that name."
      fi
      ;;
  esac
  # …AND A NESTED NEAR-MISS: a directory of this name sits one level
  # down, under a directory that carries NO `.tm-store` marker. Marker over
  # inference means that directory is a flat box, not a store, so its children
  # are not mailboxes and postfix's map has no pair for them — the box the
  # operator can SEE is genuinely undeliverable. That is the one refusal here
  # nobody can explain from the outside, so name the marker that would change
  # the answer. Cold path only: this scan runs after resolution has failed.
  if [ -z "$hint" ]; then
    for store in "$root"/*/; do
      [ -d "$store" ] || continue
      [ -f "${store}${_MBL_STORE_MARKER}" ] && continue
      if [ -d "${store}${lc_me}" ]; then
        hint=" A directory '${store}${lc_me}' exists, but '${store%/}' carries no ${_MBL_STORE_MARKER} marker — so it is a flat box, not a store, and its subdirectories are not mailboxes (nothing is delivered to them). If it IS a store, mark it: touch '${store}${_MBL_STORE_MARKER}'."
        break
      fi
    done
  fi
  # Side-neutral wording (E6): this sentence is now read by a WRITER too —
  # session-mail-send's filedrop path refuses through this same rail — so it
  # must not say "there is nothing to read" to somebody who was trying to
  # send. The fact is the same for both: the box does not exist.
  MAIL_BOX_ERR="no such mailbox '${name}' under ${root} — nothing has ever been delivered there.${hint}"
  return 1
}

# mail_box_env_root — the agents root the ENVIRONMENT names, on stdout:
# $SESSIONMAIL_ROOT, else the older $SESSION_MAIL_ROOT (each only when set and
# non-empty), else ~/.mail/agents; a leading `~/` is $HOME. The SAME order as
# the package's `_maildir.env_root`, inline because a bash rail
# cannot import Python; tests/test_root_vars.py pins the two to one answer.
# The settings file's `root` key is NOT read here: it is JSON, and this rail
# stays dependency-free. Python's `_maildir.root` reads it after the variables.
# rc 1, nothing printed, when the root needs $HOME and HOME is unset or empty:
# `${HOME:-}`, never a bare `$HOME`, because under the CLI's `set -u` a bare
# `$HOME` with HOME unset aborts bash ("HOME: unbound variable", rc 1) before
# the CLI can refuse.
mail_box_env_root() {
  local r="${SESSIONMAIL_ROOT:-${SESSION_MAIL_ROOT:-}}" h="${HOME:-}"
  case "$r" in
    "") [ -n "$h" ] || return 1; r="$h/.mail/agents" ;;
    \~) [ -n "$h" ] || return 1; r="$h" ;;   # a LITERAL tilde, escaped: the value is a string
    \~/*) [ -n "$h" ] || return 1; r="$h/${r#\~/}" ;;
  esac
  printf '%s\n' "$r"
}

# CLI form, for callers that cannot source bash (session-mail-wake is python):
#   bash mail-box.lib.sh <name> [root]
# prints the resolved path on stdout (rc 0), or the refusal on stderr (rc 2 —
# the same "bad usage / cannot proceed" code the read verbs use). With no
# [root], the root is mail_box_env_root's.
#   bash mail-box.lib.sh --env-root
# prints mail_box_env_root alone (rc 0): the agreement test's door. Either form
# refuses (rc 2) when no root can be built: no [root], no root variable, no HOME.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  set -uo pipefail
  _mbl_no_root="mail-box.lib.sh: no root — pass one, or set SESSIONMAIL_ROOT (or the older SESSION_MAIL_ROOT) or HOME"
  [ $# -ge 1 ] || { echo "mail-box.lib.sh: usage: mail-box.lib.sh <name> [root] | --env-root" >&2; exit 2; }
  if [ "$1" = "--env-root" ]; then
    mail_box_env_root || { echo "$_mbl_no_root" >&2; exit 2; }
    exit 0
  fi
  _mbl_root="${2:-$(mail_box_env_root)}"
  [ -n "$_mbl_root" ] || { echo "$_mbl_no_root" >&2; exit 2; }
  if mail_box_resolve "$1" "$_mbl_root"; then
    printf '%s\n' "$MAIL_BOX_PATH"
    exit 0
  fi
  echo "$MAIL_BOX_ERR" >&2
  exit 2
fi
