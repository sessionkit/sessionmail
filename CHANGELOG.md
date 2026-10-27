# Changelog

All notable changes to sessionmail are documented here.

The format is [Keep a Changelog](https://keepachangelog.com/en/1.0.0/);
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The one version lives in `pyproject.toml`; `scripts/release.py` is its only
writer and mirrors it into `.claude-plugin/plugin.json` and `marketplace.json`.

## [Unreleased]

## [0.3.2] - 2026-10-28

### Added

- **`pager settings hooks on|off` turns the mail hooks on and off**
  (SM-983). It changes only `"hooks"` in
  `~/.sessionkit/mail/settings/mail.json`, in place, and keeps every other
  key and byte; the file is replaced in one atomic step, and a symlinked
  `mail.json` stays a link. With no file it makes one from the template
  first, as the first hook would. A second run writes nothing. It refuses,
  exit 2, a file that is not a JSON object, a host whose old settings in
  `~/.settings/mail/` are not moved yet, and a run as root or under
  `sudo`. Bare, `pager settings hooks` writes nothing and says which: exit
  0 on, 3 off, 2 off by mistake. It prints one line that starts with the
  state it left. Every flip, from a seat or from your own shell, adds one
  JSON line to `~/.sessionkit/mail/log/settings.jsonl` (when, who, from,
  to, the path), since the file it changes is the whole host's. The first
  run's line for you (SM-890) now names the verb. The verb sits under the
  hidden `pager settings` noun, so it is no MCP tool, and the subagent
  mail-guard denies a subagent `on` and `off` (the bare read stays
  allowed). `message setup` never runs it, and a fresh install still
  starts with the hooks off. For installers (SK-236): no more hand edits
  of the JSON.
- **`memory wake` says who the session is, first** (SM-980). A new IDENTITY
  section, ahead of BOOT and RECENT, runs `session identity <sid>`
  (sessiontools ST-272) with this process's own session id and prints its
  stdout verbatim under ``═══ IDENTITY — from `session identity`, at launch ═══``,
  40 lines at most. It waits 2 s, then kills the child's process group. No
  session id, no `session` on PATH, exit 3 (no block), any other exit or a
  timeout each print one line saying which, and the wake's exit code never
  changes because of it. `memory wake -n` prints the command and runs
  nothing. Until ST-272's verb is deployed, the section reads
  `identity: could not look (session identity exit 2)`.
- **The `message`, `pager` and `memory` skills point at the family's one
  etiquette card** (SM-979): when several agents share one seat or one
  call, the sessionkit skill's "Several agents in one seat" says who
  answers. Each skill carries the kit's pointer line after its lead
  paragraph and keeps no copy of the card.
  The `message` skill's `send` line no longer says every send is signed:
  a send signs only when the session holds a key, and on a kit install it
  does not, as the card says.

### Changed

- **Needs sessiontools 0.3.2 or newer** (was 0.3.0). sessiontools 0.3.2
  moves its state under `~/.sessionkit/tools/`; an older copy beside it on
  the same Mac would split that state between the old and new folders.
- **The seat agent logs to `~/.sessionkit/mail/log/`, not `~/Library/Logs`**
  (SM-966). `message seat agent --install` renders the LaunchAgent to write
  `~/.sessionkit/mail/log/seat-agent.<ADDRESS>.log`, and makes the folder
  mode 0700 when it is missing. An agent installed before this release keeps
  writing `~/Library/Logs/seat-agent.<ADDRESS>.log` until it is uninstalled
  and installed again; until then, `--install` reads the old log when there
  is none at the new path. That fallback is dropped in 0.3.3. Nothing
  deletes or moves a log. [docs/message-seat.md](docs/message-seat.md#the-log).
- **The puller logs to `~/.sessionkit/mail/log/puller.log`, not under
  `pull.state_dir`** (SM-966). `message setup --pull` renders the
  LaunchAgent to write there, and makes the folder mode 0700 when it is
  missing. `pull.state_dir` keeps its default and still holds the cursors
  and the lock; setting it no longer changes the plist. A puller installed
  before this release keeps writing `<pull.state_dir>/log/puller.log` until
  `message setup --pull` renders its plist again. The old log is left where
  it is. [docs/pull.md](docs/pull.md#the-log).

### Fixed

- **A `mail.json` that is a symlink to the old settings file no longer
  turns the two into a symlink loop** (SM-985). When
  `~/.sessionkit/mail/settings/mail.json` linked to a real
  `~/.session/mail/settings.json`, the two read the same bytes, so the
  settings check every batch of tool calls runs replaced the old file with
  a link back to `mail.json`. The only copy of the settings was gone and the
  hooks went off as a fault ("Too many levels of symbolic links"). The old
  file is now replaced only by a link to a real file that is not itself.

- **A background subagent can no longer take the seat's mail by putting a
  `--` before `--peek`, `--help` or the verb** (SM-837), and it may no
  longer write the seat's memory or change its state. Inside a subagent,
  `message read 1 -- --peek` read the mail and marked it as read for the
  SEAT while the mail-guard took it for a peek, because click stops reading
  options at `--`. `message -- read 1` and `message read 1 -- --help` did
  the same, through Bash and through `message_read`'s `args`. Now `--peek`,
  `--list` and `--help` count only before a `--`. The same subagent is now
  also denied, in both doors, `memory init` / `memory_init` (the whole
  verb), `memory run` without `--list`, `message subscription add|remove`
  (and their tools), `pager rule add|remove` and `pager focus` /
  `pager_focus`, each with a one-line reason naming what it would do and
  the read-only way. Reads stay allowed: `memory ls|tree|cat|wake`,
  `memory run --list`, `message subscription list`, `pager rule list`,
  `pager status --stopped`. `hooks/hooks.json` routes `memory_init` to the
  guard, the only memory tool routed, so `memory_ls|cat|tree|wake` on a
  main thread cost nothing new. Main-thread decisions are unchanged, byte
  for byte.
- **The first run tells you where it wrote `mail.json`** (SM-890). The run
  of the PostToolBatch hook that creates the settings file now shows you one
  line, beside the agent's unchanged note: ``sessionmail wrote <path> with
  its hooks off. To turn them on, run `pager settings hooks on`.`` Claude
  Code shows it as `PostToolBatch says: ...`. Only the creating run says
  it; when sessions race, only the one that created the file does.
  [docs/settings.md](docs/settings.md).
- **`memory wake` no longer dies under bash 4.4+ when two boxes answer to
  one name** (SM-963). The shell rail's two-box check read a variable it
  had declared but never set; bash 4.4+ treats that as unset under the
  rail's `set -u`, so on a Homebrew Mac or any Linux wake exited 1 in the
  very case the two-box rule exists for. `/bin/bash` 3.2 hid it, and the
  tests now run the rail on both bashes. The rail also refuses a missing
  root (rc 2, saying what to set) when no root argument, no
  `SESSIONMAIL_ROOT`/`SESSION_MAIL_ROOT` and no `HOME` is given, instead of
  aborting on `HOME: unbound variable`.

## [0.3.1] - 2026-10-25

### Added

- **Every mail names the model that wrote it** (SM-918). `message send`
  writes `Session-Model: <exact id>`, for example `claude-opus-5-5`,
  beside `X-Session-From`, and the signature covers it. A changed or
  removed header fails `message verify`, and so does one added to a mail
  signed without it. On Claude Code the id is the last assistant turn's
  model in the session's transcript. Any other harness sets
  `SESSIONMAIL_MODEL` when it launches the agent. With neither, no header
  is written. The id is never typed: `--header` and structured headers
  refuse it. Mail without the header verifies as before.
  [docs/session-model.md](docs/session-model.md).
- **What sessionmail depends on, in one place.** The README has a
  `## Requirements` section: the operating systems, and a link to the new
  [docs/dependencies.md](docs/dependencies.md), which lists every
  dependency with what needs it, whether you need it, where it comes from
  and how to upgrade it. `.github/dependabot.yml` asks Dependabot for a
  daily look at the Python dependencies in `uv.lock`. The README no longer
  says `message mark` and `memory` read the maildir directly: they read the
  notmuch index.
- **`message subscription list` says whether each row is in the alias map**
  (SM-938): `in map`, `NOT in map`, or `map not measured` (with why, in the
  footer) when there is no map to ask. It never guesses. Before, it listed
  the settings only, so a seat could read "subscribed" while it heard nothing.

### Fixed

- **A burst of births no longer makes queued regens give up on the lock**
  (SM-939). With the alias text read under the lock, each run held it
  longer, and in a burst of concurrent writing runs over a large tree many
  gave up after the 10 s spin (exit 2, `could not acquire regen lock`).
  Queued runs now coalesce. Each writing run leaves a request beside the
  lock; the run holding the lock lists the requests before its read and,
  once its maps are written, answers each one with the exit code that run
  would have had. An answered run writes nothing and exits with that code,
  so exit 0 still means the maps on disk come from a read made after the
  call began. A run refused for valias passes its reason on as the last
  stderr line, so callers still report why. A request made after the
  holder's listing waits for the next holder; one from a session the
  holder's live list does not show is answered 3, as its own self-check
  would have. A holder whose valias is refused only because its OWN session
  is not in the list answers no alias request whose session is: that waiter
  takes the lock and writes valias itself. Only runs whose inputs match coalesce (root, host, map path,
  static file, sidecar and settings dirs, and the generator itself). A
  holder that dies before its write answers nothing, and its waiters run for
  themselves. The spin's 10 s bound stays as the backstop, now a clock
  rather than a count of `sleep` forks. `regen-failures.log` gets one
  `REFUSED` row per run that read, not per queued run.
- **A subscription set by `SESSIONMAIL_SUBSCRIBE` at session start is live
  by the first listener, or that listener says loudly `NOT in map`**
  (SM-938). This is not "live before the start returns": a SessionStart
  hook cannot wait out a generator. The detached generator at birth now
  measures the session's subscription rows after its run, runs once more
  when one is missing, and writes what it found to the born record. The
  first `pager listen` (the monitor's included, seated or not) measures
  them again, rebuilds once, and prints `subscribe  : <box> NOT in map` if
  a row is still missing, or `subscribe  : <box> map not measured (<why>)`
  when there is no map to ask (the mail drop keeps none, so it stays
  quiet). Behind it, `vmailbox-gen` now builds the alias
  text under its lock, so an older run can no longer write last and drop a
  newer session's rows; and it reads a sidecar that is empty or will not
  parse once more before it stops counting that session live, naming the
  file on stderr if it still fails.

### Fixed

- **Two MCP tool calls that overlap on one server no longer swap or merge
  their replies** (SM-926). Every tool but `message_send` ran its verb
  in-process through click's `CliRunner`, which swaps the process-global
  `sys.stdout`/`sys.stderr`; two calls in flight at once crossed their swaps,
  so one came back blank and the other carried both verbs' output (measured:
  95 runs in 100 for `message_list` beside `message_mark`). Every tool now runs
  its verb in a fresh process through the one runner `message_send` already
  used: D10 (*a long-running door starts each send afresh*) extended to every
  tool, on D09's one shared core. Replies are unchanged (exit 0 is stdout;
  otherwise `exit N: ` + stderr + stdout, a stub's line bare). A verb that
  crashes is now that error with its traceback, and a client's cancel kills
  any tool's child, not only send's.

### Changed

- **sessionmail needs sessiontools 0.3.0 or later** (SM-947). The floor
  was 0.1.0, so `uv.lock` stayed at 0.1.0 while a fresh `uv tool install`
  takes the newest release. The tests ran against a sessiontools version
  that no new install gets, and the four SM-875 agreement tests in
  `tests/test_operator_home.py` were skipped. The lock now resolves 0.3.0.
- **`message setup` runs `notmuch new --no-hooks` once, on its own empty
  profile, and one run names every step left** (SM-899). A fresh host took
  three runs before. A new step, `notmuch-index`, builds sessionmail's own
  index once, through `--config=<profile>`, and only when no database is
  there. A database already there is left alone, and the step says so.
  With no notmuch found, the plan still names that `new` and the hook step
  that waits on it. The run ends with one numbered list of what is left
  and the line to run next. Exit codes are unchanged. This changes a
  documented promise: setup used to run no `notmuch new`.
  [docs/message-setup.md](docs/message-setup.md).

### Fixed

- **`pager -h` states the default and its real exits** (SM-466). The top
  page now says that mail to this session's id wakes this session and
  other mail in its box rides along, and points at `pager rule` for what
  pages and what rides. It no longer names postfix as the carrier. Its
  exit line said `1 usage`, but `pager` exits 2 for a usage error, the
  same code as could-not-look; it now reads `0 ok · 2 usage, or could not
  look`. Help text only: no behaviour changed.

### Fixed

- **`memory wake` shows a seat the letters addressed to it, from the box
  postfix delivers to.** A stray flat directory with a project's name, beside
  the project box under its store, took wake's reads while every delivery went
  to the store's box, so a kick brief resting there counted 0. When two boxes
  answer to one name, both box rails (wake's, and `message list`/`read`'s) now
  resolve to the one the map delivers to, by the generator's own C-order rule
  read from the tree. Wake says so on stderr, naming the stray to move aside,
  and `message box path --why` names it too. A flat box must now be a maildir
  (`new/` and `cur/`) to win outright, as it already had to for `message list`.
  Wake has a new section, ADDRESSED: each letter resting in the base box whose
  delivery stamp is this seat (its address or its session id), newest first,
  with its subject, its age and the `message read <box> <Message-ID> --peek`
  line that opens it. It claims and marks nothing.
- **A release no longer re-locks uv.lock** (SM-937, the kit's SK-180).
  `scripts/release.py` ran `uv version X --no-sync`, and `--no-sync` does
  not stop a re-lock: a bump re-resolved against the index, so a release
  cut before the family's releases were on PyPI failed as unsatisfiable,
  and an offline bump failed. It now runs `uv version X --frozen` on the
  dry and the real path alike, then edits only uv.lock's own root
  `sessionmail` version line, as text. That line is checked before
  `uv version` writes anything: with no root block, two, or a root block
  without exactly one version line, the release refuses and pyproject is
  untouched. A missing uv.lock is refused too. A bump needs no network,
  changes no other uv.lock line, and `uv lock --check` passes.

## [0.3.0] - 2026-10-24

### Added

- `message seat init` accepts a plus seat address (`base+tag`) as well as a dotted name.
- **`message setup --pull` installs a puller that brings a mail host's IMAP boxes down into this host's postfix**, read-only, every host-specific value from config (`pull.*`; docs/pull.md) (SM-921).

### Fixed

- **A verifier fault no longer reads as a forged mail** (SM-901). An
  unexpected error inside `session-mail-verify`, for example no
  `ssh-keygen` on PATH, printed a traceback and exited 1, the code for a
  bad signature. Every caller then reported the mail as forged. It now
  exits as a broken install already did: 3, `verifier-incomplete` (it
  could not decide). Under `--trust` the exit is 5 with
  `TRUST: ERROR reason=verifier-incomplete`, and under `--json` the one
  object is still written. stderr names the error in one line, with no
  traceback. A genuine bad signature still exits 1.
- **A new session is no longer told "pager is not listening" before its
  pager monitor arms** (SM-891, SK-104 F8). The SessionStart hook ran
  beside the monitor's start and could read the state first, so a seat
  about to be heard armed a second `pager listen`. `pager hook start` now
  says the box line only; whether the session listens is said on its first
  turn, by the first PostToolBatch (or a UserPromptSubmit that carries the
  ride-along; a typed prompt does not by default) and by Stop. A seat that
  nothing hears still hears "not listening" on that first turn.

### Changed

- **The one version now lives in `pyproject.toml`** (SM-922). It is a
  static `version` in `[project]`, which `scripts/release.py` sets through
  `uv version` (so `uv.lock` stays in step) and mirrors into
  `.claude-plugin/plugin.json` and `marketplace.json`. Before, it lived in
  `plugin.json` and the build read it with hatchling's regex source. The
  bundled `plugins/pager-monitor` keeps its own version.

- **The re-arm instruction stands alone** (SM-891, walk-145). It reads
  "re-arm with `pager listen`, in the background (Bash run_in_background:
  true), never a trailing `&`", plus the monitor rider. It no longer points
  at "the `pager listen` line your boot instruction gave you", which a seat
  with no boot brief could not follow. Every teller reads `arm.txt`, so the
  Stop hook, the ride-along, `pager status` and the pages all move together.

## [0.2.1] - 2026-10-21

### Added

- **`message seat agent --install` and `--uninstall` load and remove the
  seat's LaunchAgent** (SM-898), so a seat's signer comes back after a
  reboot.
  - `--install ADDRESS [SOCKET]` renders the packaged template into
    `~/Library/LaunchAgents`, runs `launchctl bootstrap`, and exits 0 only
    once the agent answers on the socket holding `seat.pub`'s key. stdout
    is five shell-quoted `KEY=VALUE` lines, for `eval`.
  - A re-run is idempotent. A different plist or job under the label is
    refused, never overwritten. A failed load, or an agent that does not
    answer, is rolled back.
  - `--uninstall ADDRESS` boots the job out and removes the plist when its
    `Label` is the seat agent's.
  - A plist that cannot be written or removed is a `FAILED - ...` line on
    stderr, exit 1, that says what was done and what was kept.
  - `launchctl` is `$SESSIONMAIL_LAUNCHCTL`, else `/bin/launchctl`.
    `message seat agent --help` carries `Capability: install` as a whole
    line.

### Changed

- **A newline in a message's body is a line break in its HTML part**
  (SM-913). Lines written under each other stay under each other, and a
  bold line followed straight by a numbered list no longer runs into one
  paragraph. A fenced code block and a table carry no added breaks.

### Fixed

- **`message verify` no longer fails on a bare address when
  `SESSIONMAIL_ORIGIN` is unset** (SM-898). The bundled verifier is loaded
  by path, where reading the settings file raised, so it exited 1 (read as
  a bad signature) and `--trust` printed no `TRUST:` line.
  - Loaded by path, it takes the variable, then `postconf`, then leaves
    the address bare.
  - A script the package starts is given `SESSIONMAIL_ORIGIN` from the
    `postfix.origin` key when the variable is unset, so the verifier reads
    the origin the signer used.
- **A mail-drop host with no tailnet is not told to install tailscale**
  (SM-892). With the drop as carrier and no MagicDNS suffix (no
  `MAGICDNS_SUFFIX`, no `magicdns_suffix` key, no tailscale CLI), the short
  host is the right origin, not a fault.
  - Sends, the session's born record and the PostToolBatch hook no longer
    warn. The `pager listen` banner and `pager status` say it once, as
    INFO, with nothing to install or set.
  - A tailscale that is installed but does not answer still warns. What
    the origin resolves to, and every line on an MTA carrier, are
    unchanged.
- **On a mail-drop host, the doorbell reads the drop's own maps**
  (SM-908). It read postfix's compiled maps on every host, so a drop host
  with postfix installed beside it said `NOT COPIED: no alias map postfix
  reads ...` while the drop did copy the seat's mail to the session's box.
  - Under the drop it resolves the seat's address the way a drop send
    does. It is LIVE only when every box the address expands to exists,
    the session's included; otherwise `NOT COPIED` or `NOT DELIVERABLE`
    names the drop's maps and the box that stops delivery.
  - A refused carrier is reported as not measured, with the carrier's
    reason. An MTA host is unchanged.
  - The session-start birth on the drop does not run the map generator:
    it says the first `pager listen` measures the doorbell.
- **`memory run`'s no-ledger banner says what holds** (SM-905).
  sessionmail creates no trust ledger, and `memory run` reads only
  `SESSION_REGISTRY` or the `registry` settings key, with no default. The
  headline line, the gate and the exit codes are unchanged.
  - docs/message-seat.md, "The trust registry", says the same, and says
    that the blessing keys are read from `allowed_signers` inside the
    ledger: whoever can write or push the ledger can add a root of trust.
    `memory run` runs an unblessed item by policy.

## [0.2.0] - 2026-10-20

### Added

- **`message setup`: make a fresh host ready** (SM-660). Its name is
  PROVISIONAL, so it is hidden: on no group page and no MCP tool.
  `message setup --help` answers, and `docs/message-setup.md` is its page.
  - It prints the whole plan before it applies anything: the sudo steps
    first, each with its exact command (none in this version), then every
    step's state.
  - It makes the agents root (0700), the operator's box
    (`operator.mailbox`, else the installing account's name) and each
    `--box NAME`. It refuses, before anything is applied, a name the map
    generator would rewrite, a dot-name or a session id. It also refuses
    when the readers' agents root and the mail drop's differ.
  - It rebuilds the maps once if a box was made.
  - It links notmuch's `post-new` and `post-insert` hooks to the packaged
    hook in the folder `notmuch config get database.hook_dir` reports. It
    never replaces a different file, and it repairs our own dangling link.
  - It prints a MANUAL line when there is no MTA and no drop
    (`SESSIONMAIL_SENDMAIL=drop`), and when a postfix host has no MagicDNS
    suffix.
  - `--dry-run` writes nothing, and a second run writes nothing.
  - Exits: 0 every step done · 1 a step failed · 2 usage or refused · 4
    steps left for you.
  - It writes no settings key and no notmuch config, and it does not run
    `notmuch new`. The unruled settings go through one seam that reads the
    environment only.
- **`vmailbox-gen --no-fqdn`** (SM-660), for the print modes only
  (`--print`, `--print-valias`, `--plus-rows`). It prints the short-host
  rows only and asks for no MagicDNS suffix. `--capabilities` lists it as
  `no-fqdn-v1`. A writing run refuses the flag. Without the flag, every mode
  prints exactly what it printed before.
- **No time of day beside a dated stamp in the release archive** (SM-808).
  A stamp that names a time, a weekday and a date keeps its weekday and
  date but loses its time of day, at any hour on any day, in every file
  `git archive HEAD` ships. The words around a stamp are unchanged.
  - `scripts/strip-work-stamps [repo]` does the strip, on the tree it is
    given, and a second run changes nothing. `--check` is a dry run that
    prints counts and paths, never the matched text, and exits 1 on any
    hit; `--self-test` runs its built-in checks.
  - `tests/test_no_work_hour_stamps.py` fails on any such stamp in the
    archive of HEAD, so a new one cannot land. It also fails on a commit
    sha in the archived `CHANGELOG.md`, which points into history the
    exported repository does not carry.
- **No operator block: the operator defaults to the installing account,
  by name only** (SM-806). When no template or host layer of
  `~/.settings/session/session.json` has an `operator` key at all, the
  operator defaults to this process's account name, read from the password
  database (never `$USER`). The default NAMES the human and TRUSTS no one:
  it privileges no seat, does not fill `$operator` (so no carry, urgent or
  voice-hold rule changes) and is not in `session operator --json`. Its
  only readers are the privileged-verb refusal words and the humans
  fallback. A malformed or unreadable block is still nothing configured,
  never the default. `"operator": null` is the opt-out, and it survives
  the merge as a drop-in over a good block.
- **`memory init BOX`: mint a box's `[ROOT]` and its standard labels**
  (SM-186). It mints a `[ROOT]` if the box has no live one, then each
  missing root label as a reply to it: `[CHARTER] [RULES] [HOW-TO] [BOOT]
  [CONVO] [ASKS]` by default, with `[IDEAS] [RULINGS] [LESSONS] [TOOLS]`
  selectable (`--plus L,..` adds to the default, `--labels L,..` is the
  exact set). It is additive and idempotent: a root is never minted twice,
  and a second run sends nothing. Every mint is a mail To BOX, signed with
  the box key (never the session's) and verified in BOX's maildir before
  the next is sent. `--record FILE` writes `mail_root:` into FILE's front
  matter and commits FILE alone; `--supersede` mints a new root and moves
  the pointer only after it verifies. The last line printed is `export
  <BOX>_ROOT='<id>'`. It is also served as the `memory_init` MCP tool.
  **Until the box key has a home
  (TM-5054), it refuses with exit 3 on every real box, before it reads the
  box's items or sends anything: nothing is minted and no file is
  written.**
- **`message box archive` / `unarchive`: move dead session boxes out of the
  scan** (SM-790). `message box archive` lists the session boxes that have
  been quiet for N days (`--days`, default 7) and are safe to move, as a dry
  run; `--all` also lists the KEEP rows with the reason. `--apply` moves them
  to the archive dir and needs `--days` of 1 or more. `message box unarchive
  BOX` moves an archived box back under agents/, also a dry run until
  `--apply`. Both are a host owner's act: neither verb is served as
  an MCP tool, and `message mcp --help` says so.
- **`message verify --json`: the verdict as one JSON object** (SM-788).
  `message verify --json` and `session-mail-verify --json` print ONE
  object on ONE stdout line, `"schema": 1`: `rc`, `result`, `reason`,
  `signature` (version, `h`, signer key and session, parts, and
  `covered`, the values read back from the bytes `ssh-keygen` verified)
  and, under `--trust`, `trust` (verdict, leg, reason, agent, line). It
  fails closed: `signature` is null on every FAIL or ERROR, and no object
  (a usage error, `--help`, no such message) means no verdict. Under
  `--json` the `TRUST:` text is `trust.line`, byte for byte, not a stdout
  line; per-leg lines go to stderr. Exit codes do not change, and without
  `--json` nothing changes. Gate on it, never re-parse the header:
  `docs/verify-json-verdict.md`.
- **`set subject-shorten-max N`: a cap on the subject shortener** (SM-792).
  With the shortener on, a subject more
  than N characters OVER the limit is refused as before, with `(not
  shortened: <k> over; the cap is <N>: ...)`, so the sender rewrites it. N
  is the host's line in `~/.sessionmail/rules.locked`, default 10; no seat
  variable, rules file or flag can raise it. The cap counts the overage (a
  trailing ` EOM` not counted), not the characters removed: a subject 10
  over can lose ~10 + 3 + a partial word. The notice and the counter still report the true count removed. A
  subject that fits and is only folded is never capped. `0` and negative
  values are INVALID, as is `ten`: the line is named and skipped, every send
  while the shortener is on names it, and the default 10 applies. `0` does
  not mean "never shorten": for that, write `set subject-shorten off`.
  `pager rule add --locked` refuses such a line and writes nothing.
- **The pager logs what it injects, sized, in the session dir** (SM-518).
  Each string the pager and its hooks put into an agent's context gets one
  JSONL row in `~/.session/sessions/<sid>/log/mail/pager.jsonl`. That
  covers the monitor's lines, a hand page, SessionStart, UserPromptSubmit,
  PostToolBatch, Stop's exit-2 line and the mail-guard's deny. Each row holds
  the size of the exact string (`chars`, `lines`, `tokens_est` as chars/4), the
  Message-IDs behind it and which cap bit. It holds no subject, sender or
  body. Listeners add `start`/`exit`/`error` rows, and empty hook runs are
  counted rather than logged. `pager status` gains a `today:` line.
  `capture` (a setting, or `SESSIONMAIL_CAPTURE=on`) also keeps the text, in
  `capture/<seq>.txt`, up to 64 MB per session. It is off by default.
  `docs/pager-inject-log.md` describes it.

- **`message seat init` and `message seat agent`: a seat with no Claude
  session, from a plain install** (SM-715, for PHONE-106). SM-590's
  `scripts/seat-init` and `scripts/seat-agent/seat-agent` now ship in the
  wheel and are reached through `message`, so PATH still holds exactly
  `message`, `pager` and `memory`.
  The `seat` noun is hidden from `message --help` and from the MCP tools;
  `message seat --help` answers. `init` hands its arguments to the same
  minter, unchanged: `--registry` has no default, the exits are 0/1/2, and
  it never blesses. `agent` restores SIGPIPE and SIGXFSZ and execs the
  packaged bash, so launchd's pid is the wrapper's; a broken install exits
  2 with nothing started. `message seat agent --template` prints the
  LaunchAgent template byte for byte and never renders or loads it. The
  scripts/ paths stay as thin exec shims.

### Changed

- **The seat agent's launchd label is `dev.sessionkit.mail.seat-agent.<address>`**
  in the template `message seat agent --template` prints. Every launchd label
  the kit ships is `dev.sessionkit.<package>.<job>`; mail's package is `mail`.
  - **Upgrading:** a job rendered from the old template still runs under the
    old label, `com.failmode.seat-agent.<address>`. Boot it out
    (`launchctl bootout gui/$(id -u)/<old label>`) and remove its plist, then
    render the new template, `plutil -lint` it and bootstrap it.
- **The operator block is read from `~/.sessionkit/tools/settings/session.json`**,
  the `session` tool's folder in the kit's home, where sessiontools writes
  it. Its drop-ins (`session.d/`) and template are read from the same
  folder. Nothing is read from `~/.settings/session/` any more, and nothing
  there is used as a fallback. sessionmail now requires `sessiontools>=0.1.0`.
  - **Upgrading:** move the files in `~/.settings/session/` to
    `~/.sessionkit/tools/settings/` first. Until you do, no operator is
    configured: the operator defaults to your account, which privileges no
    seat, so every privileged verb refuses and names the new file.
- **The file drop is the default carrier.** With no `sendmail` key and no
  `SESSIONMAIL_SENDMAIL`, `message send` writes into this host's maildirs
  and runs no sendmail. postfix is opt-in: the `sendmail` key (or the
  variable) names its `sendmail` by an absolute path. Any value other than
  exactly `drop` or an absolute path refuses the send by name, exit 3,
  nothing sent. The postfix maps are written only when the carrier is a
  mail server.
  - **Upgrading a postfix host:** write `"sendmail": "/usr/sbin/sendmail"`
    in `~/.sessionkit/mail/settings/mail.json` first. Without it, every
    send is delivered locally and mail to other hosts is refused.
- **mail's settings are keys in `mail.json`**, read whatever the
  environment, so a launchd job needs nothing copied into its plist:
  `sendmail`, `root`, `host`, `magicdns_suffix`, `notmuch.command`,
  `notmuch.timeout`, `notmuch.profile`, `postfix.generator`,
  `postfix.relay_domain` and `postfix.origin`. Each has one variable;
  when it is set and not empty, it wins over every layer. They are read
  from the template and host layers only.
- **notmuch: sessionmail reads its own profile, never yours.** In host mode
  every notmuch call names the `sessionkit` profile
  (`$XDG_CONFIG_HOME/notmuch/sessionkit/config`; `notmuch.profile` names
  another) with `--config`, and drops `NOTMUCH_CONFIG`, `NOTMUCH_DATABASE`
  and `NOTMUCH_PROFILE` from notmuch's environment. `~/.notmuch-config` is
  never read or written. With no profile, a notmuch call refuses by name
  (the send's parent lookup falls back to its walk). Store mode is
  unchanged.
  - **Upgrading a host that already has an index:** before upgrading,
    COPY your existing notmuch config to the profile, byte for byte,
    making the profile's folder first:
    `d="${XDG_CONFIG_HOME:-$HOME/.config}/notmuch/sessionkit"; mkdir -p "$d" && cp ~/.notmuch-config "$d/config"`.
    The database stays where it is. A profile written by hand with only
    `database.path` keeps the count but loses the header fields, the
    named queries and `new.ignore`. `message setup` adopts no database.
- **`message setup` is listed on `message --help`**, under that name. It
  is not an MCP tool: it changes this host.
  - It writes the notmuch profile from a packaged template when there is
    none and no database is in the mail root, and it links the post-new
    hook into that profile's hook folder only. With no profile, the hook
    step is BLOCKED and nothing is linked anywhere.
  - On a postfix host with no maps it writes none unless `--write-maps`
    asks. It never edits postfix's configuration.
  - It writes no settings key. The carrier step names the carrier and
    where its value came from; each MANUAL line names the key to write.
- `message --help` drops its own `-h, --help` row and the blank line
  before `Options:`, so every verb, `mcp` last, stays in its first 20
  lines. Each verb's page keeps its `-h` row, and `-h` still answers.
- **Requires Python 3.11 to 3.14.** Python 3.15 is not yet supported.
- **A script-mail's subject never executes** (SM-771, the memory spec).
  When a wake item's body is a placeholder (empty, whitespace or `x`),
  `memory run` used to run the item's SUBJECT as `sh -c`. Now nothing runs:
  the item is skipped and named ("no program: the body is a placeholder;
  the subject is documentation"), every other item still runs and the exit
  is unchanged. Every item still goes to `--trust` first, so an untrusted
  signer's item is REFUSED (exit 3) whatever its body. `--list` comments a
  skipped item out, and `--json` gives `command: ""` with `placeholder`.
  **To migrate**, move a wake item's command from its subject into its body.
- **The mail drop delivers on a host with no MagicDNS suffix** (SM-660).
  - When the generator lists `no-fqdn-v1`, the drop always asks it with
    `--no-fqdn`. So a host with no tailnet and no `MAGICDNS_SUFFIX` delivers
    to its own boxes instead of refusing with exit 69.
  - An older generator is run as before, and its refusal names it.
- **One lookup for the map generator** (SM-660). The mail drop, the map
  rebuild when a box is born, and `message box archive` now share one
  lookup. The order is `$SESSIONMAIL_VMAILBOX_GEN`, then `PATH`, then
  `<mail root>/.postfix/`, and then, **for the drop only**, the copy in the
  package.
  - The lookups that write postfix's live maps never run the packaged copy.
  - A **set** `SESSIONMAIL_VMAILBOX_GEN` is the only candidate. If it names
    a missing file, the drop now refuses with exit 69 (`message send` exits
    3), where it used to fall through to the next copy.
  - A generator without its execute bit now runs through bash in every
    caller.
  - The birth's lookup reads `PATH` from the environment it is handed.
- **A box birth on a mail-drop host runs no generator** (SM-660). It says
  `generator not run: the mail drop is selected (SESSIONMAIL_SENDMAIL=drop);
  it reads the maps per send` instead of reporting a missing generator. A
  drop host has no compiled maps to keep.
- **`message mark`'s receipt leads with the count** (SM-812). With more
  than one id, or any refused, stdout is ONE line, the first one written:
  `N of M marked <mark> - ids; K refused: ids` (it was `<marks file>: N
  <mark> - ids; K not found: ids`, printed last and only when something was
  marked). Each refusal follows on stderr, and stderr's last line is the
  tally again with the marks file: `message mark: 0 of 43 marked, 43
  refused (named above); no rows written`. So a `head`, `tail` or `cut` of
  either stream, or of both, keeps the count; 43 refusals through `2>&1 |
  tail -3` read as "3 failed" before.
  Exit is unchanged: 2 when any id is refused. One id marked alone still
  prints `<marks file>: <row>`.
- **The README is the front door only** (SM-755): what it is, install, quickstart, a new Uninstall (new text) and a docs/ list; every other section moved to nine new `docs/` files, its text kept as written, and `message receipt --help` now points at `docs/message-receipt.md`. The agent instructions moved to `AGENTS.md`, for any coding agent; `CLAUDE.md` is now `@AGENTS.md` plus Claude-only notes and the map of the specs. The release archive ships `AGENTS.md`, which links only to files inside it, and leaves `CLAUDE.md` out.
- **The pager's concept note is `docs/pager.md`.** It was `slipbox/pager.md`,
  and `slipbox/` no longer ships. The README's `docs/` list now opens its
  pager line with it, and `AGENTS.md` and `docs/plugin-skills.md` link to
  the new path.
- **The seat agent's LaunchAgent template runs `message seat agent`.**
  ProgramArguments is now `@MESSAGE@ seat agent @ADDRESS@ @SOCKET@`, with
  `@MESSAGE@` the path `command -v message` prints. The old `@SEAT_AGENT@`
  placeholder is gone, so a render line written for it leaves `@MESSAGE@`
  literal and fails loudly. The wrapper and the minter now name themselves
  `message seat agent` and `message seat init` in their usage lines.
- **Release metadata points at sessionkit/sessionmail.** The project
  Homepage and Repository URLs, the plugin's `homepage` and `repository`,
  and the marketplace's `homepage` and add-marketplace hint all name
  `https://github.com/sessionkit/sessionmail`; the Homepage used to name a
  different repo. The version is 0.2.0, the classifier is "Development
  Status :: 3 - Alpha", and the author carries `mike@sessionkit.ai` in the
  package, plugin and marketplace metadata. The README gains the one-step
  `/plugin install sessionmail --marketplace sessionkit/sessionmail` line.
- **The postfix setup is written out, and the map generator's missing-dir
  error names the step.** `docs/postfix-configuration.md` (it was
  `docs/postfix-settings.md`, and `docs/postfix.md` is folded into it) now
  carries the one-time setup with sudo, step by step, then what runs after
  it with no sudo. `docs/settings.md` lists every key of `mail.json` and
  which file each is read from, `docs/configuration.md` names the files
  each component reads, and the README gains `## Configuration` and
  `## Logs`. When `vmailbox-gen` finds its map dir missing and cannot make
  it, it writes no map, and its error gives the setup step and the one
  `sudo install -d` command that makes the dir, filled in with this account
  and this path. It used to name a setup script that does not ship.
- **`make test` leaves out the wall-clock tests; `make test-latency` runs
  them.** A test marked `latency` bounds how soon the pager pages, and
  machine load can push it past that bound with nothing wrong in the code.
  `make test`, and any plain `pytest` run, now leaves them out; each has a
  twin in the default run that checks the same behaviour by the order of
  events. `make test-latency` runs them alone, for a quiet machine, and
  `-m latency` or naming a test by its id runs one too.
- **Every date and time typed into the tests and docs is synthetic, in
  2030** (SM-869). Test literals, fixtures, epochs, comments, docstrings
  and the docs now carry 2030 dates that keep the weekday and the
  daylight-saving state of the dates they replace, and dates a test
  compares keep their gaps in days. Two anchors, `ANCHOR` and
  `ANCHOR_SUMMER`, are defined once, in `tests/conftest.py` and
  `tests/mhs/anchor.sh`, and a lone value is written from one of them.
  The code still reads the real clock, and no test fakes it. A date that
  belongs to someone else's standard or tool keeps its real value, and so
  do the release headings in this file.
- **The settings live in `~/.sessionkit/mail/settings/`** (SM-873). The
  host file is `~/.sessionkit/mail/settings/mail.json`, with
  `mail.schema.json` and `mail.template.json` beside it. Every reader and
  writer of the mail settings uses that folder, and there is no variable
  to move it. Nothing is read from or written to `~/.settings/mail/`.
  - **To upgrade from 0.1.0**, move the files in `~/.settings/mail/` to
    `~/.sessionkit/mail/settings/`. Until you do, sessionmail writes
    nothing in either folder: the hooks stay off and say why once, and
    `pager settings install` exits 3. Nothing is read from the old folder.
    If both folders are real, the new file is used, and sessionmail says
    once a session to remove the old one.
  - `mail.schema.json` names the `sendmail` key, so a host file carrying
    it passes the schema.

### Fixed

- **`message setup` plans the operator's box from the operator `sessionkit
  setup` wrote**, in `~/.sessionkit/tools/settings/session.d/`, and never
  from an old `~/.settings/session/session.json` that names someone else.
- **docs/settings.md says which home `~` is for each key.** It said
  `$HOME` for every key but `registry` and `alert_runner`. In fact every key
  read from the template and the host (`sendmail`, `root`, `host`,
  `magicdns_suffix`, `notmuch`, `postfix`, `registry`, `alert_runner`) is
  read under the account's home in the password database, so a `mail.json`
  under another `$HOME` is not read for them. `hooks`, `monitor`,
  `pager_required` and `capture` are read under `$HOME`.
- **A user's global git config can no longer refuse sessionmail's own
  commits.** `message seat init`'s registry commit and the
  `memory init --record` commit run the repo's OWN hooks (its local
  `core.hooksPath`, else `.git/hooks`), never a global or system
  `core.hooksPath`, so the repo's owner can still gate them. The registry
  commit signs with the seat key through `ssh-keygen` whatever
  `gpg.ssh.program` the user configured, and is authored by the seat even
  when `GIT_AUTHOR_*`/`GIT_COMMITTER_*` are set. The record commit is
  never signed (`commit.gpgsign=false`); it is still authored by the
  user's own git identity. Before, a global pre-commit hook or a signer
  stopped seat birth.
- **The test suite ignores the runner's git config.** It clears
  `GIT_CONFIG_COUNT` and its `GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, and
  reads no global or system config, so a run from a shell that forces
  commit signing, or has a refusing global hook, is no longer red.
- **An unreadable box or folder is reported as unreadable**, not as empty
  or as a missing file.
- **The bundled scripts run on the package's own Python** (SM-878). The
  `memory` verbs, `message verify` and the pager's voice check started the
  scripts in `mhs/` by path, so each `#!/usr/bin/env python3` picked the
  first `python3` on PATH. On a stock macOS that is the system 3.9, below
  `requires-python`, and `memory run` failed at `import tomllib`.
  - A Python script now runs as `[sys.executable, script]`, from the
    package and from the scripts themselves (`memory run`'s path resolve
    and its default boot item, `memory wake`'s handoff).
  - Whoever starts a bundled script sets `SESSIONMAIL_PYTHON` to its own
    interpreter in the child's environment. The bash scripts (the `ls`,
    `tree` and `cat` shims, and the verifier's wrapper, still `-I`) run
    that interpreter. It is set every time, never inherited, and it is
    dropped from the user's own `memory run` items. It is not an operator
    setting. With it unset, a script run by hand behaves as before.
  - A `SESSIONMAIL_PYTHON` that names no executable fails the verifier
    loudly (exit 126 or 127, "could not be verified"), never a fall back
    to PATH's `python3`.
  - The in-process verify port's gate reads the same rule, so on a host
    whose PATH `python3` is not the package's, the port is on.
    `VERIFY_PORT_PIN` is re-pinned to the wrapper's new exec line; the
    Python it verifies with is unchanged.
  - The shebangs stay, for a person running a script by hand. Run that
    way on a Python older than 3.11, `session-mail-run`, `session-mail-wake`
    and `mhs-thread` now refuse in one line, naming the `memory` verb to
    use instead (exit 2), not a traceback. The verifier, `store-index`,
    `delivery-check` and the mail-guard hook keep no such guard: they are
    built to run on older Pythons.
  - The shell suites in `tests/mhs/` start `session-mail-run`, and load
    `mhs-thread`, on the package's interpreter, as the package does.
- **The README says what needs a mail server** (SM-660).
  - `message send` needs postfix's `/usr/sbin/sendmail`, or
    `SESSIONMAIL_SENDMAIL=drop` on a host with no postfix.
  - Reading, `pager` and `memory` need neither.
  - It also names `message setup` and the `notmuch new` requirement: nothing
    in sessionmail runs `notmuch new`.
  - `docs/file-drop.md` covers the no-suffix delivery, the first boxes, the
    refusal for a pin that names a missing file, and the packaged copy
    being for the drop only.
- **The mail drop qualifies a bare `From:` as postfix would** (SM-847).
  Under `SESSIONMAIL_SENDMAIL=drop`, `message send` wrote `From: Ears
  <heard+airpods>` with no `@host`, and the drop wrote the same bare
  address in `Return-Path:`. Postfix appends its myorigin in transit; the
  drop rewrote nothing, so on a host with no postfix a heard turn failed
  the keeper test (`keeper_sent` False, failing closed). The sender now
  appends a domain to a bare From on the drop path only, before it builds
  and signs the message, so the signature covers the qualified From and v1
  and v2 still verify; the Return-Path follows. The domain is
  `$SESSIONMAIL_ORIGIN` when set (the signer's origin, so the two never
  disagree), else `<short host>.<MagicDNS suffix>` (what postfix writes
  when myorigin is `$myhostname`), else the short host, with a warning. An
  already-qualified From is left as given. The postfix path is unchanged:
  its From and `-f` stay bare. Still not reproduced: To and Cc stay bare
  under the drop, and `Delivered-To:` and `Received:` keep the short host.
- **The suite passes in the release export, and a shipped doc links only
  inside it** (SM-849). `git archive` leaves out `CLAUDE.md` and `openspec/`
  (`.gitattributes`). A test that reads one now SKIPS there, with a reason
  naming the export, and still asserts in a checkout, where a missing file
  fails. Three docs (`message-receipt`, `pager-inject-log`,
  `verify-json-verdict`) linked into `openspec/`, dead in the export; they
  now name the path in prose. `tests/test_readme_docs.py` covers every
  shipped doc, found rather than hand-listed, and fails a shipped link whose
  target is export-ignored, in a checkout too. The README, the skills,
  `docs/development-setup.md`, `docs/pager.md` and the mail-guard README
  named `openspec/` paths in prose; each now says the specs are in the git
  repository and the release archive leaves them out, as `AGENTS.md` does,
  and a test fails any shipped Markdown file that points into `openspec/`
  without saying so (a commit-pinned `path:line @ sha` citation is history
  and allowed). `make check` skips its openspec leg when `openspec/` is
  absent and git does not track it (the release archive, or a git clone of
  it), saying the release archive left it out; in a checkout that tracks
  it, it stays strict. `make test-export` runs `make check`
  and the suite inside `git archive HEAD`.
- **`session-mail-verify --trust` passes a keyed plus-grammar seat as
  itself** (SM-803). Leg 2 compared the cert's `agent:` as written with the
  From reduced and stripped of its `+extension`, so a cert naming
  `proj+t-1.worker` FAILed for that same seat, and every keyed plus seat
  failed `memory run`'s trust. Both sides now reduce through the same
  `local_part()` that leg 1 verifies: lowercased local part, `+extension`
  kept, domain dropped. This also tightens one case: a project key (`proj`)
  no longer passes for one of its plus seats (`proj+t-1.worker`), and a base
  key no longer passes for a tagged From (`alice` for `alice+memory`). A cert
  agent that carries `@` still FAILs, as before (SM-606). An empty reduction
  never matches. Leg 1 and the signature format are unchanged.
- **One process no longer mints one Message-ID twice in one second**
  (SM-839). Every id is `<EPOCH.PID.RAND>`, with RAND drawn from 0..32767.
  The bash sender started a fresh process for each send, so the pid kept
  two sends apart. In one process (`memory init`'s seven mails, the MCP
  door, the pager's notes), two sends in one second could share one id.
  `memory init`'s tests hit this about one run in 2,000: the box was then a
  fork, and the next run refused it with exit 2. On a real box `memory
  init` still refuses with exit 3 until TM-5054. Now a process never draws
  one RAND twice in the same second. The wire format is unchanged.
- **Maildir publish never clobbers a file in `new/`, and a name collision
  retries in process** (SM-838). The mail drop and the IMAP puller moved a
  finished message from `tmp/` into `new/` with a rename, which silently
  replaced any file already at that name: a delivered message could be lost
  at exit 0. Both now publish the way postfix's own maildir writer does:
  they link the `tmp/` file into `new/`, which refuses an existing name,
  then unlink it. A collision in `tmp/` or `new/` retries under a fresh
  name, 3 attempts in all; a retry name adds the maildir random field
  `R<hex>`, and a first attempt's name is unchanged. `python -m
  sessionmail._drop` exits 75 (EX_TEMPFAIL, which postfix pipe(8) defers)
  only when every attempt collided and nothing was written; once a copy
  is written any failure exits 73, so a retry cannot deliver it twice. A
  failed drop now names the copies delivered, the box that failed and the
  boxes never attempted, in `message send`'s refusal too. The puller no
  longer dies on a collision. If all 3 names collide, its error still
  escapes as before, with nothing written and the cursor left on the UID.
- **The not-done reminder names its boxes** (SM-812). `[mail] 141 not done,
  oldest 4h` now goes on `(team 55, own 40, +2 more 46)`: the count split
  by the address each mail came for, read from the ride's `done/` only
  when the line is due. The session box is `own`; a mail the ride never
  gave is `other`. The line stays within 120 characters, dropping the
  door's `--reason ...` and shortening names when it must.
- **An id held by two files is marked and receipted once** (SM-812).
  `message mark` and `message read` wrote two rows and named the id twice
  (`4 read - a, dup, dup, b`) when two files carried one Message-ID, or one
  id was given twice. `read` still shows both files.
- **One end signal now ends a pager listener even while another process
  holds the session log's lock** (SM-817). Before, a SIGTERM, SIGINT or
  SIGHUP to `pager listen --monitor` (and a Ctrl-C to a hand `pager
  listen`) waited with no deadline on `seq` or `quiet` under
  `log/mail/` to write its `exit` row; a hook stalled inside its write
  kept the listener alive until a second signal. Now a listener's way
  out (that `exit` row, the row before a followed exec, and a hand
  listen's page row) waits at most `SESSIONMAIL_LOG_LOCK_SECONDS`
  (new; default 1 s; 0, a negative or a non-number read as the default)
  for those locks, ONE budget per exit. Past it the row is dropped (or
  the `exit` row is written without its quiet counts, when only `quiet`
  was held) and stderr says so once, naming the lock and the budget:
  `pager: exit row not logged: <session>/log/mail/seq still locked by
  another process after the 1 s exit budget (SESSIONMAIL_LOG_LOCK_SECONDS);
  an exit never waits on its log`. Every other writer, the hooks, the
  mail-guard and the in-loop rows, waits for the lock as before. A
  second signal still ends a shutdown at once (SM-753).
- **The first seat on a fresh HOME is no longer born deaf for want of the
  schema, and a box on disk is no longer refused at birth for a stale map**
  (SM-721). Two causes. (1) `mail.schema.json`, which the launcher's birth
  writer needs before it applies `SESSIONMAIL_SUBSCRIBE`, was installed only
  by the PostToolBatch hook, after the birth it was needed for. Both pager
  births (`pager listen`, the SessionStart hook) now install the schema and
  the template into `~/.settings/mail/` (idempotent, never `mail.json`, which
  the first PostToolBatch hook still creates and announces). A launcher runs
  the new hidden verb `pager settings install` before its birth writer;
  until `session start` and `tm kick` call it, the very FIRST seat on a
  HOME still needs it run by hand. (2) The birth's gate asked postfix's
  installed map, the generator the tree: a project box made on disk before
  the next regen was refused and its `false` outranked the birth file for
  the session's life. The birth's gate now falls back on the generator's own
  tree test (a `<store>/<name>/new` under a marked store); `message
  subscription add` is unchanged. A box with no maildir anywhere is still
  refused, turned off and journaled. No subscription file format changed.
  A `false` already written by a live session does not heal: run `message
  subscription add BOX` in that session.
- **A body line over 998 octets arrives intact** (SM-818). `message send`
  (and the MCP `message_send`) wrote every body as `Content-Transfer-Encoding:
  8bit`. RFC 5322 caps a line at 998 octets, so postfix folded a longer line
  (a CRLF and a space after octet 998) and the sender still exited 0.
  Single-line JSON arrived unparseable, and a signed mail failed `message
  verify` (`part 1 ALTERED`). Now a MIME leaf with any line over 998 octets
  goes `quoted-printable`. Every encoded line is 76 characters or fewer, and
  the decoded bytes are the body's own, byte for byte. A page mail
  (`multipart/alternative`) encodes each part on its own and keeps its top
  level at 8bit. A body whose lines all fit is sent 8bit, exactly as before.
  The signature is unchanged: v2 already signs each part's DECODED bytes and
  never the transfer-encoding header, so such a mail verifies on receipt and
  a tampered one still fails. Readers (`message read`, the pager, `memory`,
  wake) already decoded, and they show the text, never the QP.

- **The mail-guard refuses a subagent's take or mark of its seat's mail**
  (SM-446). A background subagent's Bash shares its seat's session, so
  `message read`, `message mark` or `pager status --new` run there took or
  marked the SEAT's mail, and the seat's pager did not page. On a PreToolUse
  payload that carries `agent_id` (a subagent's), the guard now denies
  `pager status --new`, `message read` without `--peek`, `message mark`,
  `pager stop|off|mute|unmute|listen` and `pager hook <event>`, with a
  one-line reason naming the read-only way (`message list`, `message read
  --peek N`). Read-only verbs and every send stay allowed. A main-thread
  decision is unchanged. Text-matched and fail-open; this is the Bash path
  (the MCP path is the next entry, SM-815).
- **The mail-guard refuses the same takes and marks through a subagent's
  MCP tools** (SM-815). A background subagent is handed the plugin's
  message and pager MCP tools, and those servers run with the seat's
  environment, so `message_read` or `message_mark` there took or marked the
  SEAT's mail as its Bash already could not (measured on Claude Code
  2.1.301). On any host whose sessionmail checkout includes this change:
  a background subagent's MCP call to the message or
  pager server that would take or mark the seat's mail is denied, as its
  Bash call already is. Main-thread MCP calls are unchanged. A second
  PreToolUse group in `hooks/hooks.json`, matcher
  `mcp__plugin_sessionmail_(message|pager)__.*` (memory's tools are not
  routed: none is denied), runs the same mail-guard. With `agent_id` in the
  payload it denies `message_read` without `peek`, `message_mark`,
  `pager_status` with `new_only` and `pager_stop|off|mute|unmute|listen`,
  judged by the Bash door's own table, with a one-line reason naming the
  tool and the read-only way (`message_list`, `message_read` with `peek:
  true`). Sends, `message_list`, `pager_status` without `new_only`,
  `pager_why` and the bare `pager_hook` stay allowed, as in Bash. A flag
  counts as set when the server would read it as true (`"peek": "yes"`
  peeks; `"peek": "false"` does not). Fail-open: a malformed payload, a
  non-object input or an unknown tool is allowed, and so is a subagent
  whose payload carries no `agent_id` (an older Claude Code), as on the
  Bash door. The hook adds about 23 ms to each main-thread MCP call to
  those two servers (median of 50, on one Mac), once per call: `pager_listen`
  pays it once, not per wait.
- **A monitor ended in its first moments no longer leaves a stale
  `listening` stamp, and the stamp is never read empty** (SM-753). A
  SIGINT, SIGTERM or SIGHUP that reached `pager listen --monitor` just as it
  wrote its stamp ended it with the stamp still naming its now-dead pid, so
  `pager status` and the hooks took the seat for listening. The stamp's
  cleanup is now armed before the stamp is written. The stamp is written to
  a temp file and renamed into place, so a reader sees no stamp or a whole
  pid; it used to be able to read an empty one. A signal while the monitor
  puts the caller's signal handlers back no longer leaves its own handler
  installed. A second signal during a shutdown that hangs still ends the
  run, as before.
- **The notmuch post-new hook quotes a Message-ID the notmuch way, and
  finds notmuch instead of assuming Homebrew's** (SM-796). The
  `Supersedes:` retag spliced ids into `id:"..."` raw, so an id holding a
  `"` matched nothing: its target was never tagged `superseded`, and the
  carrier was re-read on every run. Each embedded `"` is now doubled, at
  all three splices. The hook, and `store-index`, no longer run
  `/opt/homebrew/bin/notmuch` as the only binary (wrong on Linux and Intel
  macs). They use `SESSIONMAIL_NOTMUCH`, else `notmuch` on `PATH`, else the
  first of `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin` that has one,
  so launchd's `PATH` still works. A missing notmuch is one stderr line and
  a non-zero exit, never a silent no-op.
- **Maildirs sessionmail makes are 0700 and mail it writes is 0600,
  whatever the umask** (SM-783). Boxes and mail took the process umask, so
  under the usual 022 every box was 0755 and every dropped or pulled mail
  0644, open to other local users. Now every directory sessionmail CREATES
  for mail is 0700, the missing parents included: a born box, its `tmp/
  new/ cur/` and the mail root above it if missing, the IMAP puller's
  `--into` maildir and its cursor dir, `message box archive`'s archive dir,
  and the `new/` or `cur/` its merge makes. The mail files the drop and the
  puller write (and the puller's cursor file) are 0600. Each is made with
  `O_EXCL`, so it never truncates or follows a file already there. It never
  widens or re-modes a directory or a mail file that already exists: boxes
  born before this keep their modes. Narrowing them is the host owner's
  step, and nothing here runs it. The one exception is the cursor file. A
  pull rewrites it whole, so an existing cursor becomes 0600 at its next
  write, while its directory keeps its mode. Not changed: `vmailbox-gen`'s
  `.state/` dir and its refusal log, and the archive's `.ledger` (0640 by
  its author's choice) and its lock.
- **`message seat init` signs the birth cert under the registry's
  namespace.** It used to sign and self-verify every birth cert under one
  fixed namespace, whatever the registry listed. Now it reads the
  `namespaces=` list of the registry's newest `@session` line and uses the
  one `session-birth@` entry there, or `session-birth@example.com` when that
  list has none: the same `@example.com` fallback the mail and bless
  namespaces use. Two or more such entries in that list are refused before
  anything is written. Its output names the namespace it used, and says
  when it is the fallback.

### Security

- **Heard-voice mail is trusted only when the keeper's signature
  verifies.** A mail From the keeper's address (`heard@<host>`, at any
  +tag) with `Ears-Kind` used to be trusted on that unsigned `From:` alone,
  and any local sender can write it: up to 16,000 characters of its body
  went into the seat's context as the operator's spoken words, with a
  forged `Ears-Speaker` shown as the speaker. Now voice is trusted only
  when `session-mail-verify --trust` gives a full verdict on the keeper's
  certificate and the signature covers every `Ears-*` header the mail
  carries; v1 signatures never qualify. Anything less is unverified voice
  and is handled like mail: the text view, cut at the mail cap and counted
  against the batch budget, under `[heard UNVERIFIED <time> <device>]`
  with no speaker, its words led by a line saying they are not the keeper's
  verified words. It is never marked `done` and is not a `pager say` turn. Its
  calls can page only as plain mail can: a `start`, `wake!` or `carry!`
  line on `call:` is lowered to `carry` for any call that is not trusted
  voice. A missing verifier, a timeout or a verdict that cannot be read
  counts as unverified.
- **`memory wake` and `memory cat` refuse an unsigned or foreign BOOT
  letter, and no reader says "sig OK"**. `memory wake` used to show the
  newest letter on the shelf whoever sent it. It badged a letter "sig OK" if
  it was signed by any key at all, showed an unsigned one, and exited 0
  every time. Now the shelf shows its newest letter only if the signature
  verifies AND its one From header names the box. Any other letter is
  refused: unsigned, a bad signature, From someone else, or more than one
  From. The refusal prints only the letter's Message-ID and the reason. The
  body is withheld and no older letter is shown in its place. The other
  sections still print, and the wake exits 3, as its help says. `memory cat`
  of a path that names a non-member (unsigned, a bad signature, or From/To
  not the owner) exits 3, with the reason and the Message-ID on stderr; it
  used to say "no child" and exit 1. The verifier checks a mail against the
  key the mail carries and cannot say whose key that is. So a letter that
  verifies is now badged "signature verifies · signer NOT pinned": on the
  wake shelf, on `--boot`, and in the footer `cat` prints for a member.
  **Known gap:** a letter From the owner, signed by a key nobody has pinned,
  is still shown, fenced, and exits 0. Refusing it needs key pinning,
  which is not designed yet. The help pages of `cat`, `run`, `wake` and the
  group now give the exit codes the code returns.
- **A v1 memory item's membership reads the To its signature covers, and
  nothing else** (SM-846). v1 signs only the address part of To (its local
  part), never Cc, but the membership test (MHS 170) searched notmuch's To
  and Cc. So a relay with no key could make a v1 item alice signed To bob a
  member of alice's tree: it verified GOOD, `memory ls` showed it and
  `memory run` ran it. Adding one `Cc: alice` was enough, and so was
  rewriting To to `"alice@h" <bob@h>`, `bob@h (alice@h)` or `bob@h,
  alice@h`. Now a mail read as v1 is a member only if its To names the
  owner and the To in the file, reduced the way the verifier signs it, is
  the owner (at any +tag). Only `v=2` counts as v2; an absent `v=`, or a
  header that cannot be read, reads as v1. v2 signs Cc, so its membership
  is unchanged. This only narrows. No honest v1 mail loses membership,
  because the v1 sender signs a To that holds one address. A mail it now
  refuses says why: `v1 signs only To's address ('bob'), never Cc or the
  rest of To`. v1 mail that carries a Cc is not refused; its Cc just
  confers nothing.
- **`session-mail-verify` refuses a v1 mail that carries a signed header
  twice** (SM-832). v1 signs the first `From`, `To`, `Subject`, `Date` and
  `Message-ID` it finds, and never how many there are. notmuch shows the
  last Subject and Date and joins every To. So a relay with no key could add
  a second Subject after the signed one and rename or renumber a memory
  item, or add a second To and make a non-member a member, while the mail
  verified GOOD. Now any of the five present more than once, in any case,
  with any value, is BAD, rc 1, `reason=v1-shape`, with `the mail: 2
  subject fields: v1 signs the first only`. The membership port in
  `mhs-thread` and the readers' body refuse it by the same function
  (`_sig.v1_defects`). A duplicate placed BEFORE the signed one is still
  `bad-signature`. A second copy of any other header (`Received`, `Cc`,
  `References`) is not refused. Honest v1 mail still verifies, with
  unchanged output. v2 and the signature format are unchanged.
- **memory shows and runs the text the signature covers, never a second
  parse of it** (SM-827). `memory cat` (and `--full`), `mhs-thread tree -b`
  and `--boot`, `memory run` (the listing and the execution) and the `memory
  wake` shelf decided trust on `session-mail-verify` (Python's email parse)
  but took the body they showed and RAN from notmuch's JSON (GMime's parse),
  or, on the shelf, from `get_body`, which obeys an unsigned
  `Content-Disposition`. A keyless edit the two parsers read differently
  changed what a GOOD item showed or ran. Now every one of them reads
  `_sig.signed_text`: the first text/plain part of the verifier's own
  reading, the bytes p= hashes, re-hashed against p= in the reader's own
  process (v1: the v1 shape). notmuch stays the index; it is never the body
  of a trusted item. A body the reader cannot vouch for is never shown:
  `memory cat` exits 3 ("signature refused", the code its help documents)
  with nothing on stdout and `⊘ body withheld (SM-827): <why>` on stderr;
  `tree -b` and the wake shelf print that line in place of the body and keep
  going; `--boot` refuses; `memory run` skips the item as not verified and
  says why once on stderr. A signed item with no text/plain part prints
  `(no text/plain part: nothing signed to show)` and never runs (it used to
  run the SUBJECT). Two changes for honest mail: a body that is not valid
  UTF-8 reads U+FFFD where notmuch silently dropped the bytes, as the shelf
  and `message read` already showed it; and a `[ROOT]` admitted by its
  `$<AGENT>_ROOT` hint that is NOT a member (unsigned, or a v1 root outside
  the v1 shape) shows its text/plain part as Python reads it, under one line,
  `(root admitted by its hint; unsigned)` (or the reason it does not verify),
  in `cat`, `tree -b` and `--index -b` alike: it is no refusal (`cat /`
  exits 0) and makes no signature claim.
- **`session-mail-verify` refuses a mail with a Content-Disposition on any
  part** (SM-831). No signature covers a disposition, so a relay with no key
  could add `Content-Disposition: attachment` to a signed text part: the mail
  verified GOOD while `message read`, the pager preview, the voice and the
  wake shelf showed no text. Any Content-Disposition, on any part, the top
  included, is now BAD, rc 1: `reason=disposition` on v2 (`part N : ❌
  DISPOSITION`), `reason=v1-shape` on v1. A bad signature, an altered part,
  a `mime-ambiguous` or a `cte-defect` keep their own token. sessionmail has
  never written a Content-Disposition, so honest mail still verifies. The
  signature format and every `p=` are unchanged.
- **`session-mail-verify` refuses a v2 mail whose part headers or delimiters
  two MIME readers may split differently** (SM-823): BAD, rc 1,
  `reason=mime-ambiguous`.
- **`session-mail-verify` refuses a v2 part whose transfer encoding is not
  strict** (SM-819). A v2 signature covers each part's DECODED bytes, and
  `Content-Transfer-Encoding` is not signed, so an honest re-encoding in
  transit keeps verifying. A loose encoding can decode to different bytes in
  Python (what the verifier checks) and in GMime (what notmuch shows), so a
  verified mail could display other text. Such a mail is now BAD, rc 1, with
  `part N : ❌ CTE-DEFECT` and `TRUST: FAIL leg=1-signature
  reason=cte-defect`. Refused: more than one CTE field, or one that is not a
  single plain token (folded, commented, quoted, padded, unknown, or one
  the email package reads as body: written with whitespace before its colon,
  or after a header line it cannot read); a CTE field written after a bare
  CR in a header block, which GMime reads inside the line before (a bare CR
  in a body is body text to both, and still verifies); a
  quoted-printable or base64 body that breaks RFC 2045 6.7 / 6.8; a part whose
  decoding reports a defect; and a multipart or message/* part that declares
  any encoding but 7bit, 8bit or binary. What sessionmail sends (8bit, or
  SM-818's strict quoted-printable) and an honest re-encoding to
  quoted-printable or base64 still verify. A v1 part's CTE field is read by
  the same rule (SM-822, below). The signature format and every `p=` are
  unchanged.
- **`session-mail-verify` refuses a v1 mail that is not in the shape the v1
  sender wrote** (SM-822). A v1 signature covers five headers and one body
  string, and no MIME header. A relay with no key could add a
  `Content-Transfer-Encoding`, re-wrap or append a part, change the charset,
  or turn LF into CR. The signature still verified, while notmuch and
  `memory run` read other text. Such a mail is now BAD, rc 1. A part whose
  CTE is not one `7bit`, `8bit` or `binary` token is `reason=cte-defect`.
  Anything else that is not exactly text/plain, or multipart/alternative of
  [text/plain, text/html], is `reason=v1-shape`: a charset other than utf-8
  or us-ascii, a bare CR, or a MIME parse defect. Honest v1 mail still
  verifies. v2 and the signature format are unchanged.
- **`vmailbox-gen` refuses to publish a sandbox's keys over the live maps**
  (SM-310). The generator takes its source from `--root` and its target from
  `--out`, else the pinned live map dir. Before this release nothing tied the
  two together, so a regenerate from a scratch mail root replaced every real
  route with the scratch tree's rows. This includes a seat's first
  `pager listen`, `message subscription add|remove` and a hand run. Now a
  writing run is measured against postfix (`postconf -xh
  virtual_mailbox_base` and the dirs of `virtual_mailbox_maps` /
  `virtual_alias_maps`):
  - With no `--out`, or an `--out` naming the pinned map dir, it runs only
    when the root IS the tree postfix serves. With no postconf answer it is
    refused whatever the root: it fails closed.
  - With an `--out` into a dir postfix reads, it is refused when the root is
    not that tree.
  - A refused run writes nothing, prints one line naming the root and the
    map dir, and exits 5. Through `regen_maps` / `regen_valias` that
    surfaces as `generator exit 5 ... - vmailbox-gen: REFUSED: ...`.
  - `--print` modes and an `--out` into scratch are untouched.
    `SESSIONMAIL_POSTCONF`, when set, is the only postconf it asks; the test
    suite points it at nothing.
- **mail-guard fails CLOSED on a flag it does not know** (SM-777, found
  in review). On a `message send` (or the retired
  `session mail send`) the guard has matched, a flag in none of its tables
  used to make it return no decision. A backtick subject after that flag
  then reached the shell, which ran it before click refused the flag:
  `message send bob --frobnicate x -s "`cmd`" -f F` ran `cmd`. Now such a
  flag is DENIED, whether long (`--x`, `--x=v`) or short (`-z`, `-zx`), in
  one line. The line names the flag and, for `message send`, the tables to
  grow and the safe form. click refuses every one of these shapes anyway,
  so no send that could have worked is blocked. Not denied, exactly as
  click parses them: the help cluster (`-h`, `-hs X`), whose subject is
  still judged, and a known boolean given a value (`--json=1`), which is
  stepped over. **This changes a live PreToolUse hook on every seat.**

- **`memory run` executes an item only if its signer passes `--trust`**
  (SM-785, fix (a) for SK-80 B1, the follow-on to SM-780). Membership
  checks a mail against the key the mail carries, so a self-signed item
  from anyone who could deliver to the box used to run. Now every mail
  item is also put to `session-mail-verify --trust` before it runs: its
  signing key needs a registry cert whose `agent:` is the item's From. An
  item that fails (`FAIL`, `ERROR`, a timeout, any exit the contract does
  not define as a pass) is refused, named with its `TRUST:` line, and not
  run. Every other item still runs, and `run` exits 3, which outranks a
  fork's 2. An `UNBLESSED` signer runs and its header says so. The
  threshold is one constant, `REQUIRE_BLESSING` in the executor. The
  registry is `$SESSION_REGISTRY`, else `"registry"` in
  `~/.settings/mail/mail.json`, which `memory run` now fills in as
  `message verify` does. With neither set, every mail item is refused,
  under one banner. `--list` shows each refusal commented out, and
  `--json` adds `refused_trust`. The opt-in and the MCP exclusion stay.

- **`message verify` reads the signature header with one parser** (SM-787,
  asked by sessionvoice EARS-335). The version used to be chosen from a
  text parse, which treated Unicode whitespace as space and let the last
  `v=` win, while v2 was checked from bytes. So a relay could rewrite a
  captured, honestly signed v1 mail's header to read as v2, with any `h=`,
  to anything that parses bytes, and the verifier still passed it as v1:
  rc 0, or `TRUST: UNBLESSED` under `--trust`. Now `_sig`'s byte parse,
  the one v2 already used, reads the version and every tag for every mail.
  A header it cannot read one way is refused: plain rc 1, and under
  `--trust` `FAIL leg=1-signature reason=sig-header-ambiguous`. That covers
  a non-ASCII byte in the value, a tag that appears twice (`v=` included,
  agreeing or not), a segment with no `=`, and a mail with more than one
  `X-Session-Signature` field. Before, only the first field was read, so a
  relay could add a v2 field to a captured v1 for any reader of another
  field. A bare CR in the header block is refused too: the byte parse
  splits fields on LF only, but Python's email parser ends a line at a CR,
  so a field hidden behind one was a second field, and its FIRST, to it.
  A v1 mail that does not decode as text gives rc 1 and
  `reason=undecodable`, not a traceback with no `TRUST:` line. A v2 mail
  with such a byte still verifies, because v2 reads bytes. mhs-thread's
  in-process port reads the header through the same functions, lifted by
  name, and is re-pinned.
  **Behaviour changes:**
  - an install with no `_sig.py` beside `mhs/` now refuses v1 mail too: rc
    3, or `TRUST: ERROR reason=verifier-incomplete` (5);
  - an unsigned mail on such an install gets rc 3, not 2;
  - an **unsigned** mail that does not decode as UTF-8 moves from plain
    **rc 1** (a traceback) to **rc 2** (unsigned). Under `--trust` it is
    rc 1 before and after, but now prints `TRUST: FAIL leg=1-signature
    reason=unsigned` where it printed only the traceback;
  - v1's `n=` and `b=` lose all whitespace inside the value, as v2's always
    have, so `n= NS` now verifies where it used to fail;
  - a **v2** header with a repeated tag or a segment with no `=` moves from
    plain **rc 3** (`signature parse failed`) to **rc 1**. Under `--trust` it
    was rc 1 before and is rc 1 now; only the reason changes, from
    `bad-signature` to `sig-header-ambiguous`. A v1 header with a segment
    with no `=` moves from rc 0 to rc 1;
  - a mail with two or more `X-Session-Signature` fields, even identical ones
    or an empty one, moves from "the first field decides" to rc 1;
  - a mail with a bare CR (a CR not before an LF) in its header block moves
    to rc 1, whatever it signs, an **unsigned** one too: plain **rc 2 -> 1**,
    and under `--trust` (rc 1 before and after) the reason moves from
    `unsigned` to `sig-header-ambiguous`. A bare-CR v2 that reached v2's own
    checks (rc 1 `bad-signature` or `below-floor`) now ends
    `sig-header-ambiguous` too. Send has always refused a CR in a header
    value, and CRLF mail is unchanged.

  Honest v1 and v2 mail verifies as before.

- **`memory run` is OFF by default and is no longer an MCP tool** (SM-780,
  the interim fix for SK-80 B1). A script-mail's signature is checked
  against the key the mail itself carries, so it is self-signed: the
  signer is NOT checked. Anyone who could deliver to a box could sign an
  item with a fresh key and have it run as the owner. `memory run` now
  runs nothing unless `SESSIONMAIL_MEMORY_RUN=1` is set, and exits 4
  otherwise. The bundled executor holds the same gate when it is run by
  its path. `--list` runs nothing and needs no opt-in. `memory mcp` no
  longer serves `memory_run`, and the verb stays on `memory --help`. The
  run page, the memory skill and the unsigned-send warning had promised
  that only the owner's items run. They now say the signer is not checked.
- **`mcp --transport http` binds loopback only** (SM-781, SK-80 M2). The
  http door has no auth, and every read verb runs as the serving seat, so
  a bind off loopback handed that seat's mail to anyone who could reach
  the port. `message mcp`, `pager mcp` and `memory mcp` now refuse any
  `--host` that is not `localhost` or a loopback IP literal (127.0.0.0/8,
  ::1) before anything is built or bound: `0.0.0.0`, `::`, empty, a LAN
  address and every other name (names are never resolved). The refusal
  exits 1 and gives the safe way off-box, an ssh tunnel to the loopback
  port (`ssh -L 8790:127.0.0.1:8790 <this-host>`). stdio is untouched. The
  page no longer says http is for a client on another box. Its Exit line
  now reads `1 --host not loopback · 2 usage or no bind`: it had said
  `1 usage`, but a usage error has always exited 2. No auth was added.
  Every accepted host also keeps the MCP SDK's DNS-rebinding guard (Host
  and Origin checked): the SDK switched it on only for `127.0.0.1`,
  `localhost` and `::1` spelled exactly, so a bind to `127.0.0.2` or
  `LOCALHOST` answered a rebinding web page.

## [0.1.0] - 2026-10-10

### Removed

- **BREAKING: `session mail-verify` is gone. Use `message verify`.**
  The verifier is no longer reached through the retired `session` repo's
  `session mail verify`. It ships inside this package as data
  (`mhs/session-mail-verify`) and is not a command on PATH. Its one door is
  `message verify [BOX] N|ID|FILE [--trust]`, with the same exit codes
  (see `message verify` under Added). Scripts that called
  `session mail-verify` or `session mail verify` must switch to it.

### Changed

- **A seat's subscriptions live in its settings map**
  (sessiontools `openspec/changes/session-settings`, task 3.2, first part).
  They were files in `~/.sessionmail/seats/<seat>/subscriptions/`; now they
  are the `subscriptions` map of `~/.session/seats/<seat>/settings/mail.json`
  (then `mail.d/*.json`), with each `true` key a box. They are read per
  owner, so routing is unchanged and `false` means the same as absent.
  `message subscription add|remove --seat` writes that file only, under a
  lock, and logs who and why to `~/.session/seats/<seat>/log/settings.jsonl`.
  The postfix map generator and `message subscription list` read the map.
  An agent's bare name (`alice`, `bob`: no `+`, no `.`) is not a seat, so its
  subscriptions stay in the old folder until a later step reads the agent layer. A box
  file left in a seat's old folder routes nothing and is reported as never
  converted. There is no symlink at the old path, because that folder also
  holds the retired saved queries; this departs from the move rule and is
  an open question. Needs sessiontools 0.0.4 (`sessiontools.settings`). Without
  it, a seat's map is skipped with that reason, never a crash.

- **An old host file that differs from the new one is named, once.**
  When `~/.session/mail/settings.json` is a real file whose bytes differ
  from `~/.settings/mail/mail.json`, the move keeps it rather than link it,
  and the two then say different things: the hooks read the new file and
  anything not yet moved reads the old. The ride-along now says so once per
  session, before anything else it says, even with the hooks off (the off
  may be the drift), from its own record so the state line is never said
  again because of it. The usual way in is a writer that saves by temp file
  and rename, which turns the old path's symlink back into a file. Merge the
  two by hand; once the bytes match, the old path becomes the symlink.

- **The hooks' settings file moved to `~/.settings/mail/mail.json`**
  (sessiontools `openspec/changes/session-settings`, task 3.1). Host settings
  for every tool live in `~/.settings/<tool>/<tool>.json`, and message,
  pager and memory are the one tool `mail`. The move ships three things
  together: the first ride-along after the upgrade CONVERTS
  `~/.session/mail/settings.json` (its bytes become the new file, so a
  host with `"hooks": true` stays on and gets no first-run note), only the
  new path is written from then on, and the old path becomes a relative
  symlink to the new file for readers not yet updated. An old file edited
  after the copy is never replaced. The Stop hook only reads, so it never
  converts. `mail.schema.json` (JSON Schema 2020-12) and the template are
  installed beside the file, and the schema ships as package data.

### Added

- **The notmuch hook can run one command after each index run**
  (changes/post-new-command; proposed). Opt-in by a notmuch config key,
  `notmuch config set sessionmail.post_new_command /abs/path`; unset, the
  hook is unchanged (one `config get`, no file, no process). Set, the hook
  touches an `again` flag and hands off to a detached perl runner, then
  exits 0 without waiting, so the command can never block or fail
  indexing. One run at a time under a kernel lock; mail arriving during a
  run gives exactly one more. The command gets no arguments, cwd `/`, its
  own process group, a fixed environment (HOME, launchd's PATH,
  NOTMUCH_CONFIG and a guard that stops its own `notmuch new` re-firing it),
  a 300 s timeout, and `~/.sessionmail/post-new/log` for its output.
  `tests/test_notmuch_post_new.py` covers it over a throwaway database,
  holding commands on gate files instead of clocks.
  Credit: the `again` flag and `--no-hooks` ideas came from an outside contributor (reimplemented, no code copied).
- **`message verify` checks a message's signature** (changes/message-verify).
  `message verify [BOX] N|ID|FILE [--trust [--require-blessing]] [--quiet]`
  replaces the retired `session mail verify`.
  - It runs the bundled `mhs/session-mail-verify` on the file that `read`
    would open (or on a path) and returns its exit codes unchanged: 0 good,
    1 bad, 2 unsigned; with `--trust`, 0 FULL, 1 FAIL, 4 UNBLESSED,
    5 ERROR.
  - A usage error, or a message that is not there, exits 3 and never 2.
  - The sender's compatibility proofs now run against the bundled verifier.
    They used to skip once the session repo was gone.
- **The notmuch `post-new` hook moves in from session**
  (changes/mail-leaves-session). Its only source
  was the retired `session` repo, and an older version;
  `hooks/notmuch-post-new` is byte-identical to the live copy. It tags
  `+agent`/`+memory` by path and re-derives `superseded` from each
  message's `Supersedes:`/`X-Supersedes:` header, which is the only way
  a correction survives an index wipe. `tests/test_notmuch_post_new.py`
  drives it through a real `notmuch new` over a throwaway database,
  rebuild-from-nothing included. Installed by hand (README), not by the
  plugin or the package.
- **`memory wake` is the boot readout** (changes/memory-wake; Q11, A37).
  `memory wake [BOX] [--dry-run]` is the click door onto the bash
  `session mail wake` through the same oracle seam as `ls|tree|cat|run`:
  stdout, stderr and exit code are the oracle's. Peek-only
  (R5): nothing is claimed. The bash verb's `-a|--all` is
  NOT carried: it was accepted and dropped (`session-mail-wake` line
  605), a dead flag, and the new door does not teach it. `memory mcp`
  serves it as `memory_wake` for free.
- **A plus address gets its own doorbell row** (changes/task-address-doorbell).
  `pager listen` DECLARES a `<base>+<ext>` address from `EMAIL`
  (`TM_AGENT_ADDRESS` is the fallback) in
  `sessions/<sid>/address`, before its first doorbell check; `vmailbox-gen`
  writes `<base>+<ext>@<host> -> <base>@<host>, <sid>@<host>` for live
  sessions with a box, through one guard, `plus_rows`, that never writes a
  row on the bare base (`--plus-rows ADDRESS SID` runs it alone; capability
  `plus-v1`). The doorbell asks for the plus key; the seat folder is
  `seats/<address>/`; the born query is `to:"$ADDRESS"`;
  `SESSIONMAIL_HEARS` adds what else a seat hears, at read
  time. A plus seat is refused `pager listen <base>` unless its base is
  declared. `message mark N` resolves N in the box a bare
  `list` numbered. Sessionmail knows RFC 5233 and no task grammar.
- **The plugin ships its hooks** (SM-30): every
  hook it relies on is installed when a user installs the plugin.
  `hooks/hooks.json` registers `Stop` and `PostToolBatch`, each
  running `pager hook <event>` through the one-line shim `hooks/pager-hook`
  - so seat identity has ONE resolver (the pager's `_seat`), never a shell
  copy of it (the drift SM-30's specimen 1 recorded). `pager hook stop`
  stamps the session stopped and, when no listener is live, says so on
  stderr with exit 2 (the one way a Stop hook is heard; never when the
  harness says `stop_hook_active`). `pager hook batch` is the ride-along:
  this session's queued mail, once, then which of the two lines holds.
  PostToolBatch fires once per batch of tool calls (measured). Measured on a fresh `claude -p --plugin-dir .` with an
  isolated `SESSIONMAIL_HOME`: the `stopped` stamp appears under the
  session the harness names, and the model repeats the not-listening line.
- **The two standard lines live in one file**, `src/sessionmail/hook_lines.toml`
  ("pager is not listening, run `pager listen`" / "pager is listening, run
  `pager stop` to stop"), so both hooks read the wording from one place. The
  ride-along's nudge (`PAGE_NOT_ARMED`) is the same line now.
- `pager stop` - `pager off` by the name the standard line uses. One
  command, two names; the spec's `pager off` stands.
- The arm instruction has ONE source: `src/sessionmail/arm.txt`, package
  data, read through `sessionmail._arm`. Every teller reads it and none
  restates it — the three page texts (`PAGE_WAKE`, `PAGE_NOTHING`,
  `PAGE_NOT_ARMED`), the `pager on` refusal, and the test that pins the
  Stop hook's line. It now also says "never a trailing `&`", which no copy
  said before. `tests/test_arm_instruction.py` fails if the sentence
  appears verbatim anywhere else in the tree. Ratified in
  `openspec/specs/pager/spec.md` ("The arm instruction has one source");
  SM-122 `the-quiet-page`.

- `vmailbox-gen` comes home as SOURCE: the generator that writes postfix's
  `virtual_mailbox_maps` now lives in this repo at
  `src/sessionmail/postfix/vmailbox-gen`, moved VERBATIM (sha256
  `41c82e497310abe59c673a364d9521c4addd6b359e82b9a193d1f8c765bb4c78`, `cmp`
  clean) from the loose `~/.mail/.postfix/vmailbox-gen`, which was in nobody's
  repo while this package's own doorbell depended on it — `pager listen
  sessionmail` resolves a bare box name THROUGH that map. It ships
  as PACKAGE DATA, not a fourth console script: `openspec/specs/package/spec.md`
  ratifies three commands and no umbrella, and whether it later becomes a
  `pager`/`message` subcommand is not settled here. Nothing switched over: the
  loose copy is still the one that runs, and retiring it is a separate,
  deliberate act. `tests/test_postfix_vmailbox_gen.py` pins `--print` byte for
  byte over a synthetic `--root` (flat boxes, lowercase folding, `nested-v1`
  store dirs, `store-box-v1`'s triad, the `new`/`cur`/`tmp` skip, determinism)
  and pins `--capabilities`; no live maildir, no `postmap`, no write outside
  `tmp_path`. SM-74.

- `pager status --new`, the scoop, is ARRIVALS ONLY: the first scoop of a
  session records the index revision (`<seat>/stamps/<session>/scoop-rev`)
  and reports residents as depth, not givings; later scoops ask only for
  what was indexed since (the listener's own `lastmod:` scope). Measured
  on one Mac: without it, 77 day-old residents dripped
  onto the ride-along three per tool call. Silent when there is nothing (a
  ride-along line, not a sentence). Then: only what THIS session has not been
  given in the last 60 minutes (`SESSIONMAIL_REMIND_MINUTES`, inferred);
  every line printed is then recorded as a six-field `seen` mark with reason
  `ride-along`, and this session's `<seat>/stamps/<session>/ride-along` is
  touched. Plain `status` is the resident view, unchanged. `--stopped` is
  the Stop hook's form: stamps the session stopped and marks nothing (its
  output reaches nobody).

- `pager listen` wakes only a stopped agent: at every hear it
  compares this session's two stamps under `<seat>/stamps/<session>/`
  (sessions share a seat), and a busy verdict expires after
  `SESSIONMAIL_BUSY_TTL_SECONDS` (600, inferred) so a frozen stamp cannot
  keep it deaf (hooks are read at session start); busy = the message is
  deferred ("left for the ride-along") and it keeps listening; stopped = it
  wakes, and a deferred message no ride-along gave is the wake. A wake now
  writes the `seen` mark (reason `pager wake`) - the writer that had none.
  A seat with no stopped stamp keeps the old behaviour and the banner says
  `busy-check : OFF`. `--from-stop` touches `stopped` first, for a Stop hook
  that blocks (an unattended seat only: measured, a blocking
  Stop hook queues the human's next prompt).

- `pager listen` scopes every poll to what was indexed since the revision
  the previous poll saw (`_notmuch.revision()`, notmuch `lastmod:`), and its
  banner says so (`poll : every 1s, scoped to index revision > N`). The
  resident set remains the rule (arrivals only); the scope is what makes a
  poll nearly free - measured: a whole-box query 80 ms
  unscoped, ~0 scoped. An engine that reports no revision is polled unscoped
  and the banner says UNSCOPED.

- The first `pager listen` on a seat with no subscriptions directory writes
  the two born files - `seat` (`to:$SEAT and not from:$SEAT and date:1d..`,
  routed `pager,ride-along`) and `session` (`to:$SESSION`, routed `pager`) -
  and says so in its banner (`_seat.write_born`, `_seat.BORN`), as
  `session mail arm` does with the maildir. `pager status` still refuses on a bare
  seat (a ride-along never creates what it measures) and now names `pager
  listen`. Before this, every seat's two files had been typed by hand.

### Changed

- **The pager's hooks are OFF until `~/.session/mail/settings.json` turns
  them on, and sessionmail no longer knows about roles** (SM-450,
  changes/pager-settings-file).
  The Stop and PostToolBatch hooks speak only when that file holds
  `"hooks": true`, the one setting. When the file is missing, the
  ride-along writes it from the template, `{"version": 1, "hooks": false}`,
  atomically and never over an existing file, and says so once. A faulty
  file is off and says so once. `SESSIONMAIL_PAGER=off` still wins, the
  `pager` commands ignore the file, and mail-guard stays on. Off means
  silent, mail included. With the hooks on, every seat with no listener is
  nagged: SM-410's `TASKMASTER_ROLE` and address-role exemption is gone,
  and its `off` stamp stays. The ride-along gives its state line once,
  then only on change. **Upgrading turns the hooks off** until the file
  says `"hooks": true`. `pager listen --help` is trimmed back under the
  fold, with nothing dropped.
- **Idle is one second, and the pager reads Claude Code's status file
  itself** (changes/idle-one-second): five seconds added too much latency,
  for voice most of all. `DEFAULT_IDLE_SECONDS`
  60 -> 1 and `IDLE_POLL_SECONDS` 5 -> 1. The 5 existed because the verdict
  came from `session get idle`, a subprocess (0.37 s wall, measured)
  that ten listeners polling every second would turn into a core; now
  `_seat.idle_seconds` reads the sidecar Claude Code writes under
  `~/.claude/sessions/<pid>.json` (matched on `sessionId`; same directory
  rule as session-list's `SESSION_SIDECAR_DIR`, same verdict rule as
  session-get's: `busy` -> 0, else seconds since `updatedAt`), in
  microseconds, and shells out only when no sidecar names the session.
  A seat that has stopped is paged within about a second of going idle;
  `SESSIONMAIL_IDLE_SECONDS` still overrides.
- **`message list` shows the newest 40 by default**, and its last line says
  how many rows it hid. `-n 0` or `-n all` show every row; an explicit `-n N`
  is unchanged: one unbounded listing had put 8,416 rows in a shell
  (`openspec/specs/message/spec.md`, "A listing defaults to the newest 40");
  SM-122 `the-quiet-page`.

  **The index it prints did not change and deliberately cannot**: it is the
  box's own, 1-based, oldest first over `new/` and `cur/`, assigned before any
  filter or cut runs — so a row's number is the same in a cut listing and a
  full one, and a number pasted into `message read` opens the same message
  from either. The default cut is disclosed like every other filter, as
  `N older than the newest 40`; a user-typed `-n` still reads `older than -n N`
  rather than being reported as a default nobody chose.

  Two smaller consequences, both visible: `-n` is now parsed by the command
  rather than by click's `int`, so `all` is a value instead of a usage error
  and a bad one names what it takes; and a NEGATIVE `-n`, which used to mean
  "show everything" in silence, is now a usage error. Footer COUNTS are
  printed grouped (`8,376 hidden`) now that a four-figure one is ordinary —
  the index column above is not, because that number gets pasted.

- **The queue, the stamps and the marks moved under the session id**:
  `~/.sessionmail/sessions/<session-id>/{ride,stamps,marks}`, where they were
  `~/.sessionmail/seats/<seat>/{ride/<session>,stamps/<session>,marks}`.
  Everything a page reads keys on the SESSION, never the seat: the seat was
  never the thing. A seat is now a NAME (the
  column in a marks line, the doorbell's address) and it MAY be absent: a
  seatless session gets the same page a seated one gets, off the same files —
  measured byte for byte with the real `pager listen`. **Marks are per
  session**: a successor inherits none of them, and `message mark`'s
  page no longer promises that they survive succession. `message list
  --unread` is now what THIS SESSION has not read, not what a seat invented
  from the box's name had marked. Subscriptions are unchanged and stay with
  the seat, as does the layout for a run with **no session id at all**.
  **Nothing migrates existing files**: an old
  `seats/<seat>/marks` is left where it is, unread — which for marks is the
  intended behaviour, and for the queue and the stamps costs at most one
  re-page. SM-122.
- The marks line is now `<stamp> <message-id> <mark> <seat> <session-id>
  [reason ...]`: time
  first because the file is a log, seat before session because the seat is
  the name a human reads, the reason free text and last. The seat column is
  what the resolver said - a bare session writes its own id, never `none` -
  and a seat with no session writes `-`. `message mark` gains `--reason
  TEXT`. The four-field line every seat wrote before this change is still
  read (as seat `-`) for one release, so a `done` already on disk stays
  done; `_seat.read_mark_events` keeps every field, `read_marks` stays the
  id-to-marks view.

### Fixed

- **The tailnet suffix is derived at runtime; there is no default** (SM-674).
  The FQDN half of every map row (`postfix/vmailbox-gen`) and the pager's
  host forms (`_voice.host_forms`) each carried a literal suffix. Both now
  read ONE definition, `postfix/magicdns-suffix`, shipped beside the
  generator: `MAGICDNS_SUFFIX` when set, else `tailscale status --json`'s
  `MagicDNSSuffix` (tailscale on PATH, then the macOS app bundle's CLI,
  each bounded to 2 s), else a loud failure that says where it looked and
  to set `MAGICDNS_SUFFIX` or install/start tailscale. With no answer the
  generator writes nothing and exits 4. The pager does NOT go deaf: `pager
  listen` skips every regen, says "MagicDNS suffix unknown: set
  MAGICDNS_SUFFIX or start Tailscale; maps NOT regenerated" on stderr and in
  its banner, and listens on the existing maps; `host_forms` falls back to
  the short host alone and says so once per process (a keeper or call
  address at the FQDN is then not recognised; `to:$me` still matches). It
  never invents a suffix. The pager memoises a runtime answer per process,
  never a failure. Measured on the home host with `MAGICDNS_SUFFIX`
  unset: `--print` is byte-identical to the generator before SM-670's scrub.

- **A timed-out verify no longer blocks a path it cannot change** (two
  low findings from a review). The walk refused `cat /X/10` whenever any mail in a matching
  chain had a verify that timed out, even when no verdict for that mail
  could change the answer. Two examples: a v1 already retired by a signed
  v2, and an older duplicate beside a newer signed one. The walk now tries
  every true/false verdict for those mails (at most 8 mails, so 2^8
  combinations; past that it refuses). It opens the item only when every
  combination picks the same child and the walk without the mails agrees.
  Otherwise it refuses, saying the timeout "may change which item this
  opens". A newer timed-out duplicate ([DUPE]), a timed-out superseder
  ([LAGCHAIN]), and a mixed case where the all-in and all-out verdicts
  agree ([LAGMIX]) all still refuse. The `ls` FORK line now names a
  timed-out twin: `FORK: 10 x3, 1 UNVERIFIED (...)` when two or more
  verified twins remain, and `10 x2 withheld — 1 UNVERIFIED (...) may make
  a FORK` when the fork exists only because of the timeout. Before this,
  it read as a plain FORK. The member-walk suite goes from 89 to 106 probes
  (P34-P38). 8 mutants were run and every one was killed. Two test gaps
  found in review are closed, taking it to 113:
  P39 pins `LAG_CAP` at both edges (8 timed-out mails are weighed and open,
  9 refuse with `too many to weigh (9 > 8)`), and P40 pins the plain line,
  `FORK: 10 x2 — refusing to pick (MHS 110); supersede one`, with two
  signed twins and no timeout. 5 more mutants (cap 7, cap 9, no cap, `>=`
  for `>`, the plain line in the timeout wording) are all killed.
- **The arm instruction points at the seat's own `pager listen` line, not
  bare for everyone** (A42).
  `arm.txt` - the ONE source every hook and page quotes - said bare
  `pager listen` for every seat. For a legacy `role.scope` seat (no `+`)
  bare listens on the SESSION box, which is not where its mail lands, and
  births that box; a seat that followed the guard's remedy ran two
  listeners. The boot
  instruction already carries the right line per seat (TM-5102's three
  forms), so the re-arm now says "the `pager listen` line your boot
  instruction gave you (bare if it gave none)". Text only, zero logic; the
  grammar-aware alternative would duplicate taskmaster's `listen_box`.
- **The pager's page names the re-arm as the way to the mail; the manual
  recipe is the fallback and says so** (changes/page-says-ride-along). Item 4 of the page offered `message list --unread` /
  `read` / `mark` unconditionally, one line after item 3 said "wait rather
  than act", and a fresh seat read the recipe as the next step - every
  seat, every session (two seats measured the same day each re-armed AND
  listed in one batch, the list redundant both times). The
  line now leads with the re-arm and prefixes the recipe with "Only if
  they do not". The spec's item-4 requirement (a page never leaves the
  reader without a way to the mail) is unchanged: the way is named, twice.
- **`message read` prints the To and Cc HEADERS; the box is its own field**.
  The compact header block
  printed `X-Original-To` under the name `To:` and omitted `Cc:` entirely,
  so a `To: alice`, `Cc: bob` message read from bob's box said `To: bob`
  - a false correction and an empty forward followed. Now `To:` and `Cc:`
  are the headers as written (Cc omitted when absent; no To header - a
  Bcc-only mail - is `To: -`, never the box) and the delivered address
  prints as `Box:` on the Date line. `Entry` gains `to` and `cc`;
  `--raw` and `--full` were always right and are unchanged.
- `memory ls|tree|cat|run` under `memory mcp` answered with EMPTY content while
  the oracle's listing landed on the server's stderr: the door ran the bash with
  an inherited fd, which the MCP handler (CliRunner over sys.stdout) never sees.
  The oracle's bytes now pass through sys.stdout / sys.stderr, still byte for
  byte on a terminal (measured: `memory ls /` cmp-identical to `session mail
  ls /`, 1269 bytes), and the tool result carries them (measured: 27 lines,
  server stderr empty).

### Added

- `memory ls|tree|cat|run` have bodies at the strangler level:
  each is the click front door handing its authored flags to the bash
  `session mail ls|tree|cat|run` as the oracle (`src/sessionmail/_mhs.py`;
  `--id` becomes `--resolve`, `--full` becomes `--bodies`), printing what it
  printed and exiting as it exited. The filesystem view, signed membership,
  the root hint and the signature-checked wake are the bash's until each is
  ported. `--box` is refused as not carried, not dropped. Nine hermetic tests
  over a fake oracle. Fifteen of fifteen verbs run; nothing exits 69.
- `message tree ID [--depth N] [--all] [--dates]` has a body: a thread's
  subjects as a tree from the seat's own box, read by bytes (In-Reply-To
  and References to a fixed point, so a reply to a reply is found from the
  root), oldest first at every level, superseded hidden and counted unless
  `--all`, `--depth` cutting below the top, `--dates` adding the sender's
  date, and the last line the token cost of what was printed (an estimate,
  marked ≈; `src/sessionmail/_tokens.py`). Five hermetic tests. Eleven
  verbs built; the `message` group is complete.
- `message send TO [TO...] -s SUBJECT (-f FILE | --no-body) [options]` has a
  body: the click front door with the bash `session-mail-send` as the ORACLE
  behind it (the strangle plan) for signing, the multipart form
  for a human reader, threading-id lookup, the deliverability preflight and the
  transport. Checked at the door, before anything is written: a subject over
  69 characters (`SESSIONMAIL_SUBJECT_LIMIT`) or not ASCII is refused with
  the remedy; a subject that could execute is refused unless
  `SESSIONMAIL_SUBJECT_GUARD=off`; a comma or line break in a recipient and
  the HTML-escaped id form are refused; no body is usage, and a subject
  ending ` EOM` implies `--no-body`; `-f -` spools stdin as bytes into a
  private file the oracle reads. A second To rides as Cc of the same
  message (one To is what the transport carries), and `--header` carries
  what the oracle can (`X-Task`, `X-Project`) and refuses the rest.
  `src/sessionmail/_send.py`. Twelve hermetic tests over a fake oracle.
  Ten verbs built.
- `message read [BOX] N|ID [--peek] [--thread] [--full] [--raw]` has a
  body: compact headers and the text part (cut at 40 lines / ~400 tokens
  unless `--full`, and it says so), `--thread` the whole thread oldest
  first, `--raw` the file verbatim. The read is an EVENT: one line appended
  to the reader's seat marks by `_seat.write_mark`, the only writer; the
  maildir is never renamed or flagged, and `--peek` prints where the file
  still sits as its proof (specs/pager, "Read is not terminal"). Nine
  hermetic tests. Nine verbs built.
- `message list [BOX] [--unread] [--from M] [--subject RE] [--to ADDR] [-n N]`
  has a body: one line per message - index, N/. for new/cur, sender, subject -
  read by bytes from the physical box (`src/sessionmail/_maildir.py`: the
  session-mail-list enumeration, new/ and cur/ sorted by maildir key, 1-based
  over all, headers only; never renames, flags or claims). Superseded
  messages are hidden; `--unread` reads the seat's marks, never the maildir
  flag; an EOM message with no body carries `(no body)`. The footer says
  what was hidden and why, every filter counted (specs/message, "A listing
  discloses what it hid" and the EOM requirement). Nine hermetic tests;
  4,334 real messages listed in 1.2 s. Eight verbs built.
- The MCP door (a stateless MCP server
  from the same model as the CLI): `message mcp [--transport stdio|http]`
  serves message's verbs as MCP tools. `src/sessionmail/_mcp.py` reads
  each tool off its click command - flags become the input schema with
  the page's help on every property, the page prose is the description,
  the group page is the server's instructions - so nothing is declared
  twice and a stub is a stub on both doors (exit 69 becomes a tool error
  carrying the same words). `http` is Streamable HTTP run stateless: no
  session id, every request self-contained. `.mcp.json` wires stdio into
  the plugin. Gates in `tests/test_mcp.py`: same surface, same 400-token
  budget, same stub over a real client session, and a bare POST answered
  with no initialize. Thirteen verbs now; `make progress` counts it built.
- Three servers, one per command:
  `pager mcp` and `memory mcp` join `message mcp`; the verb is declared
  once (`_mcp.attach`) and `.mcp.json` wires all three. Fifteen verbs.
- The CLI scaffold, help-driven:
  `message`, `pager` and `memory` on the PATH as three console scripts,
  every verb and flag taken from its authored help page. Bodies are stubs
  that name the OpenSpec requirement they will satisfy and exit 69, so
  `make progress` counts what is built by running it. New gates in
  `tests/test_scaffold.py`: the 40-line / 400-token budget on all 15 pages,
  `-h` as well as `--help`, and every cited requirement proved to exist.
- Iteration zero: its own repo; OpenSpec
  in `openspec/` (package, help, message, memory, pager; two changes) with
  `RATIFICATION.md` as the record; a Claude Code plugin and skill
  (`.claude-plugin/`, `skills/sessionmail/`); `make check`, `make test`,
  `make build`, `make release`.

## [0.0.1] - 2026-09-09

### Added

- The name, reserved on PyPI: one `__init__` and the metadata.
  "This release reserves the name. The tool follows."
