# mail-ext.lib.sh — THE STAMP RAIL: one derivation of "which address of this
# box was this message delivered to", and one VERSIONED cache of the answer.
#
# The question every per-extension filter has to answer is per-COPY, not
# per-thread: postfix stamps each delivered copy with the address it was
# delivered to (`X-Original-To` / `Delivered-To`), and that stamp is the only
# place the `+extension` survives. `session-mail-wake`'s boot_section() already
# trusts exactly that evidence; this file is that read, generalised and shared.
#
# WHY A CACHE AND NOT AN INDEX. The notmuch index cannot query `+` (tokenizer;
# measured as a 29k-token burn in print-thread's history) and is a derived
# artefact boot must not depend on — both statements are shipped and
# load-bearing (`session-mail-wake`'s own docstring and `boot_section()`). So
# selection keeps the shipped doctrine — INDEX FINDS, FILESYSTEM ARBITRATES
# (the retired `wait` verb's `notmuch_candidates()`)
# — with a candidate source that is never asked a `+` question: a per-box file
# mapping the maildir basekey to the extension it was delivered to. A basekey
# names immutable content, so an entry can never go stale; the only states are
# PRESENT and NOT-YET-DERIVED.
#
# It carries a SECOND half: the CLASSIFICATION SEAM — what
# KIND of address that extension is (bare / mine / other-session / tag). The two
# halves are the same question asked twice about one copy of one message ("which
# address" then "what kind of address"), they are consumed by the same two verbs
# on the same pass, and keeping them in one file is what lets `session-mail-wake`
# reach both through the ONE shell-out it already has. See the seam's own block,
# below `mail_ext_map`, for why that half owns no predicate of its own.
#
# NOT A VERB, AND IT CANNOT BECOME ONE — same structural trick as
# mail-box.lib.sh and mail-claim.lib.sh next door: `bin/session` and
# `session-mail` build their verb tables by globbing `session-*` /
# `session-mail-*` in this directory, and this name is reachable by neither
# glob however anybody's umask lands.
#
# ── WHERE THE CACHE LIVES, AND WHY IT CARRIES A VERSION ─────────────────────
#   ${XDG_CACHE_HOME:-~/.cache}/session-mail/ext/v<N>/<boxname>
#
# OUTSIDE the box, so a peek verb's "the box is byte-identical afterwards"
# proof stays a plain `diff -r` rather than an argument.
#
# VERSIONED by $MAIL_EXT_VERSION below (design.md §2 Amendment A). The cache's
# one wrongness channel is a bug in the DERIVATION, and its recovery — "delete
# the cache" — is manual, per host, and silent when skipped: the partial-
# propagation hazard §7 names for the scripts themselves. With the version in
# the path, a reader never opens another version's cache, so a derivation fix
# invalidates every host's cache BY CONSTRUCTION the moment the fixed script
# runs. Old version directories are garbage: small, delete opportunistically or
# never. Deleting the cache is always safe.
#
#   ⚠ $MAIL_EXT_VERSION IS ONE SHARED DERIVATION VERSION, read by both writers
#     (session-mail-list sources this file; session-mail-wake shells out to the
#     CLI form below). It is NOT either script's release counter — bump it when
#     the RULE below changes, not when a verb ships a change. Both verbs expose
#     it as `--ext-cache-version`, and a test asserts they agree: two writers,
#     one cache, one number.
#
# ── THE EXCLUSION DIRECTION IS THE DANGEROUS ONE ────────────────────────────
# For INCLUSION the cache is a hint that the caller re-checks against the file
# when it displays a row. For EXCLUSION it is authoritative: a message whose
# cache line is well-formed but WRONG is never a candidate, is never opened,
# and the omission is invisible — the very silent-omission class, living
# inside the mechanism meant to enforce it. Hence the two disciplines, which
# are not optional and are the reason this file is small:
#
#   1. ONE ENTRY = ONE COMPLETE LINE = ONE `write(2)` through an O_APPEND
#      descriptor. POSIX makes the seek+write of a single call atomic on a
#      regular file, which is what stops two appenders interleaving. (This is
#      NOT a PIPE_BUF claim — PIPE_BUF is about pipes and does not apply here.)
#      Concretely: fd 9 is opened `>>` once per run and each entry is ONE
#      printf. Identical duplicate lines from racing appenders are harmless.
#   2. STRICT READERS. A line that does not parse as exactly
#      `<basekey><TAB><extension>`, or whose basekey is not in the name spine
#      this run was handed, is IGNORED and its basekey treated as NOT YET
#      DERIVED — re-opened, re-derived, re-appended. A torn line can therefore
#      cost a re-derivation; it can never cost a message its visibility.
#
# Item 1 is adequate and item 2 does not depend on it being perfect: that is
# the whole safety argument, and it is why "the cache got corrupted" is a
# performance event here rather than a correctness one.

# The DERIVATION version — see the block above before you touch it.
MAIL_EXT_VERSION=1

_mail_ext_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# mail_ext_cache_dir — the versioned directory holding every box's cache.
mail_ext_cache_dir() {
  printf '%s/session-mail/ext/v%s' \
    "${XDG_CACHE_HOME:-$HOME/.cache}" "$MAIL_EXT_VERSION"
}

# mail_ext_cache_path <boxname>
#   rc 0: the cache file path for that box is in $MAIL_EXT_CACHE_PATH
#   rc 1: the name cannot key a cache file — refusal in $MAIL_EXT_ERR
#
# ASSIGNS rather than prints, for the same reason mail_box_resolve does: an
# error read out of a dead subshell dies with an empty message.
mail_ext_cache_path() {
  MAIL_EXT_CACHE_PATH=""; MAIL_EXT_ERR=""
  local name; name="$(_mail_ext_lc "${1:-}")"
  if [ -z "$name" ]; then
    MAIL_EXT_ERR="no box name given — an empty name cannot key a stamp cache."
    return 1
  fi
  case "$name" in
    */*|.|..)
      MAIL_EXT_ERR="box name '${1}' cannot key a stamp cache (a box name is a directory NAME, never a path)."
      return 1 ;;
  esac
  MAIL_EXT_CACHE_PATH="$(mail_ext_cache_dir)/$name"
  return 0
}

# The one derivation, as an awk program — deliberately ONE process for a whole
# batch of misses rather than one per message: a cold pass over a 20-30K box is
# the case this design exists to make survivable, and a fork per message would
# put the cost straight back.
#
# Input : lines of `<basekey><TAB><path>` on stdin.
# Output: lines of `<basekey><TAB><extension>` on stdout, input order kept.
#
# THE BASEKEY IS THE CALLER'S TO GET RIGHT, and it is the one thing that makes
# an entry immortal: it must be the maildir UNIQUE NAME with cur/'s
# `:2,<flags>` info suffix STRIPPED — exactly what `session-mail-list`'s
# `enumerate()` already emits in its second column. Hand this the raw
# `basename` of a cur/ file instead and nothing breaks and nothing lies: the
# key simply changes the moment a message is claimed, so every claimed message
# is a permanent MISS. That is a performance leak with no red test anywhere,
# which is why the contract is spelled out here rather than assumed.
#
# THE RULE (one place, one wording):
#   * read the FIRST `X-Original-To:` or `Delivered-To:` header of the file —
#     the same first-match shape as `session-mail-wake`'s `boot_section()`,
#     stopping at the end of the header block;
#   * take its localpart (up to `@`, `>` or whitespace), lowercased;
#   * localpart == <box>            -> extension is EMPTY (bare-box mail);
#   * localpart == <box>+<ext>      -> extension is <ext>;
#   * no stamp, or a localpart naming some OTHER box -> extension is EMPTY.
#     A copy carrying another box's stamp is not directed at any extension OF
#     THIS BOX, and "" is the honest answer to the question actually asked. It
#     is also the SAFE answer: "" is the class the default listing SHOWS, so a
#     surprise here surfaces as a visible row, never as a silent omission.
# shellcheck disable=SC2016  # an awk PROGRAM: nothing here may be expanded by
# the shell — the box name is handed to awk with -v, not interpolated.
_MAIL_EXT_DERIVE_AWK='
BEGIN { FS = "\t" }
{
  base = $1; path = $2
  if (base == "" || path == "") next
  stamp = ""
  while ((getline line < path) > 0) {
    sub(/\r$/, "", line)
    if (line == "") break            # end of the header block
    low = tolower(line)
    if (index(low, "x-original-to:") == 1 || index(low, "delivered-to:") == 1) {
      sub(/^[^:]*:[ \t]*/, "", line)
      sub(/^</, "", line)
      # localpart only: stop at @, > or any whitespace.
      if (match(line, /[@>[:space:]]/)) line = substr(line, 1, RSTART - 1)
      stamp = tolower(line)
      break
    }
  }
  close(path)
  ext = ""
  if (stamp == box) ext = ""
  else if (index(stamp, box "+") == 1) ext = substr(stamp, length(box) + 2)
  printf "%s\t%s\n", base, ext
}'

# mail_ext_derive <boxname>   (stdin: <basekey>\t<path> · stdout: <basekey>\t<ext>)
# The uncached derivation. Every caller of the cache goes through the SAME
# program, so a cache hit and a cache miss can never mean different rules.
mail_ext_derive() {
  awk -v box="$(_mail_ext_lc "${1:-}")" "$_MAIL_EXT_DERIVE_AWK"
}

# mail_ext_map <boxname>   (stdin: <basekey>\t<path> · stdout: <basekey>\t<ext>)
#
# THE JOIN. One pass of the name spine against the box's cache: hits are
# answered from the cache, misses are derived (one awk for the whole batch) and
# appended. Output is one line per input line, IN INPUT ORDER, so a caller can
# paste it straight back onto its own enumeration.
#
# Degradation is LOUD AND OPEN, never closed: a cache that is
# absent, unreadable or unwritable costs a scan and ONE stderr line saying so —
# it never yields a thinner answer presented as complete. The count in that line
# is the number of messages this run actually derived, measured, never assumed
# (§4bis: a figure names its basis).
#
# After the call the caller may read:
#   $MAIL_EXT_MISSES    messages derived this run (0 on a fully warm cache)
#   $MAIL_EXT_HITS      messages answered from the cache
#   $MAIL_EXT_COLD      1 if this run had no usable cache to read
#   $MAIL_EXT_CONFLICTS basekeys carrying two well-formed cache lines that
#                       DISAGREE. Counted here and treated as not-yet-derived
#                       (so the file wins); REPORTING them is Amendment B's
#                       job, and this counter is the seam it consumes.
# shellcheck disable=SC2034  # MAIL_EXT_{HITS,MISSES,COLD,CONFLICTS} are this
# function's RETURN CHANNEL, read by its callers (see the docblock above): a
# verb that degraded has to be able to weaken its own summary wording, and
# Amendment B's mismatch report consumes CONFLICTS.
mail_ext_map() {
  local box="${1:-}" cache cachef spine cls conf out fd_ok=0 k v
  MAIL_EXT_MISSES=0; MAIL_EXT_HITS=0; MAIL_EXT_COLD=0; MAIL_EXT_CONFLICTS=0

  if ! mail_ext_cache_path "$box"; then
    printf '%s: %s\n' "${MAIL_EXT_PROG:-session-mail}" "$MAIL_EXT_ERR" >&2
    return 2
  fi
  cache="$MAIL_EXT_CACHE_PATH"

  spine="$(mktemp)"; cls="$(mktemp)"; conf="$(mktemp)"; out="$(mktemp)"
  cat > "$spine"

  # An absent or unreadable cache is read as an EMPTY one — /dev/null, so the
  # classifier below still runs and still emits a full answer. (Handing awk a
  # path that does not exist would make it refuse the whole run: a cold cache
  # would then look like a failure, which is the closed degradation §4 forbids.)
  cachef="$cache"
  if [ ! -r "$cache" ]; then cachef="/dev/null"; MAIL_EXT_COLD=1; fi

  # Classify every spine line as HIT (with its extension) or MISS, reading the
  # cache STRICTLY: exactly two tab-separated fields, and a basekey this run
  # actually knows. Anything else is not-yet-derived. Duplicate identical lines
  # are harmless; duplicates that DISAGREE are counted and demoted to a miss.
  #
  # The two files are told apart by FILENAME, not by the NR==FNR idiom: NR==FNR
  # is true for BOTH files when the first one is empty, and an empty cache is
  # this function's most ordinary input.
  awk -F'\t' -v cachef="$cachef" -v conf="$conf" '
    FILENAME == cachef {
      if (NF != 2) next                     # torn / unparseable -> ignore
      if ($1 == "") next
      if ($1 in seen) { if (val[$1] != $2) conflict[$1] = 1 }
      else { seen[$1] = 1; val[$1] = $2 }
      next
    }
    {
      if ($1 in seen && !($1 in conflict)) { print "H\t" $1 "\t" val[$1] }
      else {
        if ($1 in conflict) print $1 > conf
        print "M\t" $1 "\t" $2
      }
    }
  ' "$cachef" "$spine" > "$cls"
  MAIL_EXT_CONFLICTS="$(wc -l < "$conf" | tr -d ' ')"

  # Derive the misses — ONE awk for the whole batch.
  awk -F'\t' '$1 == "M" { print $2 "\t" $3 }' "$cls" | mail_ext_derive "$box" > "$out"
  MAIL_EXT_MISSES="$(wc -l < "$out" | tr -d ' ')"
  MAIL_EXT_HITS="$(awk -F'\t' '$1 == "H" { n++ } END { print n + 0 }' "$cls")"

  # Append the newly derived entries: ONE O_APPEND descriptor for the run, ONE
  # printf (one write(2)) per entry. A cache we cannot open for append is a
  # degradation, never a failure — the answers are already computed.
  #
  # The `exec` sits inside a braced group so its 2>/dev/null is scoped to the
  # group: `exec 9>>f 2>/dev/null` on its own would redirect THE SHELL's stderr
  # for the rest of the run, silencing every degradation line this file exists
  # to print.
  if [ "$MAIL_EXT_MISSES" -gt 0 ]; then
    if mkdir -p "$(dirname "$cache")" 2>/dev/null && { exec 9>>"$cache"; } 2>/dev/null; then
      fd_ok=1
      while IFS="$(printf '\t')" read -r k v; do
        printf '%s\t%s\n' "$k" "$v" >&9
      done < "$out"
      exec 9>&-
    fi
  fi

  # ONE line, on stderr, naming the degradation and its MEASURED count — never
  # a count this run assumed (§4bis: a figure names its basis). Note the three
  # cases are distinct sentences on purpose: "rebuilding" is a claim about what
  # happens NEXT run, so a run that could not write must not make it. A
  # readout may never assert a property it did not enforce, and "rebuilding"
  # over an unwritable cache is that sentence in its smallest costume.
  if [ "$MAIL_EXT_COLD" -eq 1 ] && { [ "$fd_ok" -eq 1 ] || [ "$MAIL_EXT_MISSES" -eq 0 ]; }; then
    printf '%s: ext cache cold — rebuilding (%s messages)\n' \
      "${MAIL_EXT_PROG:-session-mail}" "$MAIL_EXT_MISSES" >&2
  elif [ "$MAIL_EXT_COLD" -eq 1 ]; then
    printf '%s: ext cache cold and unwritable at %s — derived by scan (%s messages), not cached\n' \
      "${MAIL_EXT_PROG:-session-mail}" "$cache" "$MAIL_EXT_MISSES" >&2
  elif [ "$MAIL_EXT_MISSES" -gt 0 ] && [ "$fd_ok" -eq 0 ]; then
    printf '%s: ext cache unwritable at %s — derived by scan (%s messages), not cached\n' \
      "${MAIL_EXT_PROG:-session-mail}" "$cache" "$MAIL_EXT_MISSES" >&2
  fi

  # Emit in INPUT order: hits straight through, misses paired with the answer
  # just derived. FILENAME again, not NR==FNR — with every message a hit, the
  # freshly-derived file is empty.
  awk -F'\t' -v derived="$out" '
    FILENAME == derived { val[$1] = $2; next }
    $1 == "H" { print $2 "\t" $3; next }
    { print $2 "\t" val[$2] }
  ' "$out" "$cls"

  rm -f "$spine" "$cls" "$conf" "$out"
  return 0
}

# ══ THE CLASSIFICATION SEAM ═══════════════════════════════
#
# mail_ext_map above answers "which ADDRESS of this box was this copy delivered
# to". This half answers the next question — "what KIND of address is that" —
# and it is the question the disclosure default and the per-extension
# filter both turn on.
#
# ⛔ THE ONE RULE THIS SECTION EXISTS TO ENFORCE: WE DO NOT OWN THE PREDICATE.
# The session-id-vs-topic-tag shape rule is the retired `wait` verb's
# `is_session_ext()` / `SESSION_EXT_RE`, and that shape is byte-identical to
# `_MBL_UUID_RE` in `mail-box.lib.sh` AND to `UUID_RE` in `session-mail-arm`,
# which no comment anywhere names and which gates that verb's SID resolution:
# THREE live spellings of ONE concept, plus a FOURTH inside a comment in
# the retired `wait` verb.
#
# ⚠ That count is MEASURED (grep, 2030-06-09), not carried over from notes.
# This line read "two spellings of ONE concept today" until a review grepped
# it, though three documents agreed on "two". Three documents agreeing is ONE
# unchecked claim with three copies; it is not a measurement. Re-grep before you quote this number.
#
# Wait's own comment, directly above SESSION_EXT_RE, records what the SECOND
# copy cost: "a second copy that could (and, before this, would) drift out of
# step with the first". The third arrived anyway, unnamed and uncounted, which
# is the whole argument for this section owning no fourth. design.md §10
# rejects a local re-derivation BY NAME; §7 explains why a divergent copy goes
# silently HOST-dependent under dual-ship (two shipped copies of these scripts,
# one per host, and nothing diffs them).
#
# (They are cited by CONSTRUCT rather than by line, deliberately:
# the retired `wait` verb and `session-mail-arm` are both being edited in parallel
# elsewhere, so a line number written here would be stale before
# it was read. Grep the constructs — they are unique.)
#
# So:
#
#   * there is exactly ONE delegation point below (`_mail_ext_session_shaped`),
#     and it CONSUMES the shared regex by reference — it never writes it out;
#   * there is exactly ONE classification function (`classify_for_list`), which
#     both verbs call and neither re-implements;
#   * `test_session_mail_ext_classify.sh` greps the tree and fails if a second
#     literal copy of the regex appears, or if the delegation point acquires a
#     second call-site.
#
# ── WHY THE ANSWERS BELOW ARE TODAY'S, AND NOT THE DESIGNED TABLE ───────────
# The designed table has `role.<other-sid>` HIDDEN as *session-directed*.
# That class DOES NOT EXIST YET on either side of the seam: `SESSION_EXT_RE` is
# anchored `^…$` over a BARE uuid, so `worker.<uuid>` does not match it and
# today's predicate calls it a `tag`. Creating it belongs to the shared box
# rail, in its own files. Implementing the widened rule HERE as a stopgap is
# the second-copy drift trap wearing a new hat, so this shim delegates and
# reports what the shared predicate says TODAY. When the widened predicate
# lands, the shim's answers change and NOTHING IN THIS FILE DOES. That is the
# entire point of having a seam.
#
# ── ON HOIST, THIS BODY COLLAPSES ──────────────────────────────────────────
# The design expects the predicate to be HOISTED into `mail-box.lib.sh`.
# When they do, `_mail_ext_session_shaped` becomes a one-line call to whatever
# they name it, and `classify_for_list` may collapse into a call to a hoisted
# `classify_ext`. No name is guessed here in advance: a speculative branch on a
# function that does not exist yet is a THIRD spelling of the thing, betting on
# a name nobody has ruled.

# This file's own directory — where it finds the box rail. Same idiom, and the
# same reason, as `session-mail-list`'s SML_DIR: a plugin-cache copy must use
# the rails it SHIPPED WITH, never a checkout's (design.md §7 dual-ship).
_MAIL_EXT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]:-$0}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]:-$0}")")"

# mail_ext_load_shape_rule — make the shared shape rule available, or refuse.
#
# It sources `mail-box.lib.sh` — the SHARED box rail, which this file does not
# EDIT (any change there is an agreement with the rail's owner, not a local
# decision). We READ it. Sourcing is idempotent: it defines
# functions and constants only, its CLI form being fenced off by the standard
# BASH_SOURCE guard at the foot of the file. And it is skipped outright when a
# caller has already sourced it, which `session-mail-list` has by the time it
# lists anything.
#
#   rc 0: $_MBL_UUID_RE is loaded
#   rc 1: it is not — the refusal sentence is in $MAIL_EXT_ERR
mail_ext_load_shape_rule() {
  MAIL_EXT_ERR=""
  [ -n "${_MBL_UUID_RE:-}" ] && return 0
  local lib="$_MAIL_EXT_DIR/mail-box.lib.sh"
  if [ ! -r "$lib" ]; then
    MAIL_EXT_ERR="cannot read the box-resolution library at ${lib} (broken checkout?) — it carries the session-id shape rule this filter delegates to, and this filter will not guess at one."
    return 1
  fi
  # shellcheck source=mail-box.lib.sh
  . "$lib"
  if [ -z "${_MBL_UUID_RE:-}" ]; then
    MAIL_EXT_ERR="the box-resolution library at ${lib} no longer defines the session-id shape rule — the classification seam has come unstuck from the rail it delegates to. Refusing rather than guessing."
    return 1
  fi
  return 0
}

# _mail_ext_session_shaped <string>  ->  rc 0 if it is SESSION-ID SHAPED
#
# ⭐ THE ONE DELEGATION POINT. Exactly one call-site in this tree (the shim
# below), and the shape itself is CONSUMED from the shared rail rather than
# written out here. If you are about to add a second caller, or to paste the
# regex, read the block at the top of this section first.
_mail_ext_session_shaped() {
  printf '%s' "$1" | grep -Eq "$_MBL_UUID_RE"
}

# mail_ext_my_sid — this session's id, lowercased; empty when there is none.
#
# ONE env read for our lane, so "mine" cannot mean two things in two verbs. The
# cascade is the FULL uuid, deliberately, and flips in lockstep with
# the retired `wait` verb's MY_SID, `session mail arm`'s SID_BOX and
# `session-mail-wake`'s resolve_box. (Named by construct, not by line: two of
# these three files are being edited elsewhere as this ships.)
# Truncate it
# here and my own directed address stops matching itself.
mail_ext_my_sid() {
  _mail_ext_lc "${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
}

# classify_for_list <ext> [<my-sid>]  ->  bare | mine | other-session | tag
#
# ⭐ THE SHIM. The single classification call-site in our tree: both read verbs
# call THIS, and neither carries a rule of its own. The vocabulary is
# the retired `wait` verb's `classify_ext()`, word for word, because two
# descriptions of one classification that disagree are worse than one that is
# wrong — disagreement is invisible until somebody diffs them by hand.
#
#   bare           no extension at all — the @here work queue
#   mine           my own session id, in EITHER spelling (the bare id, or the
#                  roster NAME form that ends in it: `worker.<my-sid>`)
#   other-session  somebody ELSE's reserved session id, in either
#                  spelling — see THE SEGMENT SELECTOR below
#   tag            a topic tag, and a SEAT ([<task-id>.]<role>): an office is
#                  not a session (Q2 reading (a))
#
# ── THE SEGMENT SELECTOR ──────────────────────────────────────────────────────
# To decide whether an extension is SESSION-DIRECTED, take its LAST
# dot-separated segment and test THAT, whole, against the shared shape rule:
#
#   <other-sid>                  -> other-session      (the bare form)
#   worker.<other-sid>           -> other-session      (the roster NAME form)
#   tm-123.worker.<other-sid>    -> other-session      (…at any arity)
#   worker.<my-sid>              -> mine
#   tm-123.worker.<my-sid>       -> mine
#   worker                       -> tag                (a seat)
#   tm-123.worker                -> tag                (a task-scoped seat)
#
# NOT a `<role>.` prefix test, NOT a fixed number of parts, and NO enumeration
# of role names — enumerating roles is a design decision this filter is not
# licensed to make. The ruled address grammar is `[<task-id>.]<role>`
# (the address grammar, big-endian), so `tm-123.worker.<sid>` is an
# ordinary present-day name: a first-dot reader would test `worker` as the id,
# and an "arity >= 3 is foreign" reader would hide THIS session's own directed
# mail. Both reuse the shape rule faithfully; the defect is in WHAT IS FED TO
# IT. Both wrong readers are BUILT AND RUN against the seven-row table in
# test_session_mail_ext_classify.sh, each required to go red on its own row.
#
# ⚠ WHY THIS IS A SELECTOR AND NOT A SECOND PREDICATE. design.md §10 refuses a
# local re-derivation of the shape rule by name (the drift trap
# the retired `wait` verb's own comments warn about). The shape stays DELEGATED —
# one call to `_mail_ext_session_shaped`, which consumes `$_MBL_UUID_RE` by
# reference; this file still carries no copy of the uuid pattern, and the
# classify suite's T7/T8 hold us to it. What is added here is which SEGMENT is
# fed to that rule, which is the grammar's own rule (mail to the full roster
# name is "a DIRECTED MENTION OF THAT SESSION"), not a second opinion about
# shape.
#
# ── DEBT: THE TAIL-SELECTOR NOW EXISTS IN TWO PLACES ───────────────────────
# The words DEBT, OWNER and TRIGGER are spelled out on purpose: a
# deferred item without an owner and a trigger decays into "it is fine" by
# attrition, and whoever lands the hoist may be a newcomer no mail
# thread can reach. It has to FIND this by reading — or grepping — the file.
#
# The twin, measured at 7ef1a96: the retired `wait` verb's `directed_ext_sid()`
# (INTERIM) does exactly this selection on the CLAIM side,
# `${ext##*.}` and the same shared regex. It lives at top level of a script
# with no source guard, so it cannot be called from here. Until the trigger
# below fires, the two sides are the same predicate seen from opposite ends —
# HIDDEN/SHOWN here, REFUSED/ACCEPTED there — and our `--classify-ext`
# diagnostic is what makes our half observable.
#
#   NOT DUPLICATED: the SHAPE. `_mail_ext_session_shaped` consumes
#            `$_MBL_UUID_RE` by reference; nothing here writes a pattern out.
#            What is duplicated is `${ext##*.}` — one parameter expansion of a
#            grammar already ratified in AC14.
#   OWNER:   the work that makes `session-mail-arm`'s and
#            the retired `wait` verb's identity resolution callable.
#   TRIGGER: a callable shared predicate lands ⇒ THIS BODY COLLAPSES TO A CALL,
#            and nothing else in our tree moves.
#
# The design's fence on duplication was struck and annotated rather than
# deleted, so a reader who finds the fence also finds that it was crossed
# deliberately and why. The reason: a second copy of a
# one-token SELECTOR and a second copy of the REGEX are not the same risk. The
# first cannot drift into a wrong answer, only into redundancy; the second is
# the trap. ⛔ THE REGEX FENCE REMAINS ABSOLUTE.
#
# <my-sid> defaults to mail_ext_my_sid. It is a PARAMETER so a test can pin it
# without pretending to be another session, and so `session-mail-wake` can hand
# the batch CLI one value for a whole run instead of re-reading the env per
# message.
#
# rc 0 always when it answered; rc 1 (nothing printed, $MAIL_EXT_ERR set) when
# the shared shape rule could not be loaded. It does NOT fall back to a local
# rule: a filter that hides mail on a guessed predicate is the same
# silent-omission class with the guess buried one level down.
#
# The BARE box short-circuits before the rule is loaded, deliberately: "no
# extension at all" is recognisable without any notion of a session id, and it
# is the class the default listing SHOWS, so answering it costs nothing and
# hides nothing. Note that mail_ext_classify_batch below does NOT inherit that
# exemption — see its own block for why a per-RUN answer must not depend on
# which messages happened to be in the box.
classify_for_list() {
  local ext="${1:-}" mine="${2-}" tail
  [ -z "$ext" ] && { printf 'bare'; return 0; }
  mail_ext_load_shape_rule || return 1
  [ $# -ge 2 ] || mine="$(mail_ext_my_sid)"
  ext="$(_mail_ext_lc "$ext")"; mine="$(_mail_ext_lc "$mine")"
  # THE SEGMENT SELECTOR (see the block above). `${ext##*.}` is `awk -F.
  # '{print $NF}'` in pure shell, and an extension with no dot is its own tail
  # — so the bare-id case needs no branch of its own.
  tail="${ext##*.}"
  if _mail_ext_session_shaped "$tail"; then
    [ -n "$mine" ] && [ "$tail" = "$mine" ] && { printf 'mine'; return 0; }
    printf 'other-session'; return 0
  fi
  printf 'tag'
  return 0
}

# mail_ext_classify_batch [<my-sid>]
#   stdin : one extension per line (an EMPTY line is the bare box — a real
#           address, and the one the default listing shows)
#   stdout: <extension><TAB><class>, input order kept
#
# BATCHED ON PURPOSE, for the same reason mail_ext_map derives a whole batch in
# one awk: `session-mail-wake` is python3 and reaches this seam by SHELLING OUT,
# so a per-message call would be a fork per message on a 20-30K box — the cost
# this design exists to avoid, reintroduced by the seam meant to be free. One
# call per RUN; the shape rule is loaded once and the session id resolved once.
#
# ⚠ IT LOADS THE SHAPE RULE UP FRONT AND REFUSES THE WHOLE RUN IF IT CANNOT —
# it does NOT inherit classify_for_list's bare-box short-circuit. The refusal
# is a fact about the CHECKOUT, not about any message, so it must not depend on
# what the box happened to hold: a broken rail that refuses a box of tag mail
# and cheerfully answers a box of bare mail gives two verdicts for one fault,
# and the second one looks like a working filter. Loud, and the same either way
# (R10: degrade loudly or refuse — never a thinner answer presented as
# complete).
mail_ext_classify_batch() {
  local mine cls ext
  if [ $# -ge 1 ]; then mine="$1"; else mine="$(mail_ext_my_sid)"; fi
  mail_ext_load_shape_rule || return 1
  while IFS= read -r ext || [ -n "$ext" ]; do
    cls="$(classify_for_list "$ext" "$mine")" || return 1
    printf '%s\t%s\n' "$ext" "$cls"
  done
  return 0
}

# CLI form, for callers that cannot source bash (session-mail-wake is python3 —
# same two-languages-one-rail arrangement as mail-box.lib.sh):
#
#   bash mail-ext.lib.sh version
#   bash mail-ext.lib.sh cache-path <boxname>
#   bash mail-ext.lib.sh map <boxname>     < <basekey>TAB<path> lines
#   bash mail-ext.lib.sh derive <boxname>  < <basekey>TAB<path> lines  (no cache)
#   bash mail-ext.lib.sh classify [<my-sid>]  < extension-per-line
#
# `map` and `classify` are meant to be called ONCE PER RUN with the whole batch
# on stdin: one process, one cache open, one derivation pass — not once per
# message. `classify` resolves no box and opens no message: it is a pure
# question about a NAME.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  set -uo pipefail
  _mel_die() { echo "mail-ext.lib.sh: $*" >&2; exit 2; }
  [ $# -ge 1 ] || _mel_die "usage: mail-ext.lib.sh version|cache-path <box>|map <box>|derive <box>|classify [<my-sid>]"
  case "$1" in
    version) printf '%s\n' "$MAIL_EXT_VERSION" ;;
    cache-path)
      [ $# -ge 2 ] || _mel_die "cache-path needs a box name"
      mail_ext_cache_path "$2" || _mel_die "$MAIL_EXT_ERR"
      printf '%s\n' "$MAIL_EXT_CACHE_PATH" ;;
    map)
      [ $# -ge 2 ] || _mel_die "map needs a box name"
      mail_ext_map "$2" ;;
    derive)
      [ $# -ge 2 ] || _mel_die "derive needs a box name"
      mail_ext_derive "$2" ;;
    classify)
      shift
      mail_ext_classify_batch "$@" || _mel_die "$MAIL_EXT_ERR" ;;
    *) _mel_die "unknown subcommand '$1' (want version|cache-path|map|derive|classify)" ;;
  esac
fi
