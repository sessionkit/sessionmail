---
name: pager
description: >-
  Alerting for coding agents: `pager listen` waits for mail or an alert as
  one process with no children; `pager status` prints the gauges. Use when
  an agent must be reachable while idle, or wants to know its alerts
  without polling. The plugin's pager monitor listens for every
  interactive seat, on by default; `pager mute` / `pager unmute` silence
  and resume it. The plugin ships three pager hooks (Stop, PostToolBatch,
  UserPromptSubmit); they
  act only when ~/.sessionkit/mail/settings/mail.json holds "hooks": true; a
  missing file is written off.
---

# pager

`pager --help` is the reference. The verbs a seat uses:

- `listen` - one process, no children. Wait until mail arrives in your
  box, print the page, and exit. Run it again to keep listening.
  `pager stop` stops it. Your box is this session's (with no session,
  $MAIL), created when your session starts. To hear a project or store box
  too, subscribe to it: message subscription add BOX. A return is
  **returned**, never "dead"; arming again is **reset**.
- `mute` / `unmute` - silence this session's listener, and resume it.
- `status` - the gauges (clock, context, runner tick) as lines; never
  mailed, never stored.

The runner's tick is a dead man switch: a tick older than its interval is
an alert, so silent deafness cannot happen.

## The pager monitor: on by default

The plugin's `monitors/monitors.json` runs `pager listen --monitor` under
every interactive seat, from launch, for the whole session. Each page is a
notification. **A seat its monitor hears for arms nothing.**

- `pager listen` there exits 0 at once: "the pager monitor (pid N) hears
  for this session; you don't need pager listen. Do not re-run it."
- **To silence it, `pager mute`**: it keeps running and pages nothing, and
  mail still rides along on tool calls. `pager unmute` resumes. Unmute
  when not muted does nothing ("not muted: already listening", exit 0).
- **Never `pager off` or `pager stop` on a monitored seat.** They refuse:
  one line naming `pager mute` and `pager stop --kill`, nothing written,
  exit 4. On a hand `pager listen` they stop it, as before.
- **`pager stop --kill` is the only real end, and it is for good.** Claude
  Code does not restart a monitor. Then listen by hand, or restart the
  seat.
- **No monitor running?** `pager unmute` and `pager status` say so: it can
  only start when the seat launches. Two ways out: run `pager listen` in
  the background (the hand arm), or restart the seat.
- **Headless seats (`claude -p`) run no monitor: arm by hand, as before.**
  So does a seat where Claude Code's monitor gate is off, or the switch is.
- **The switch, off only.** `"monitor": false` in
  `~/.sessionkit/mail/settings/mail.json` (the host), `SESSIONMAIL_MONITOR=off` in
  claude's launch environment (one seat; it never turns it back on), or
  `SESSIONMAIL_PAGER=off`. `pager status` shows the `monitor:` line and
  what decided it.
- **`pager_required`** (a boolean at the host, seat or session layer;
  `SESSIONMAIL_PAGER_REQUIRED=1`/`0` wins both ways). On a required seat
  that nothing hears for, past a ~10 s grace, the Stop and PostToolBatch
  hooks say `pager: DEAF` with the reason and the two ways out, even with
  the hooks off. A muted seat is not nagged, except after `pager stop
  --kill`: the monitor is gone, so a required seat stays DEAF even through
  `pager mute` or `pager off`, until a hand `pager listen` or a restart.

## The hooks

The hooks are the pager's own verbs (`pager hook stop`, `pager hook batch`),
registered by `hooks/hooks.json`; UserPromptSubmit runs the same
`pager hook batch` as PostToolBatch. They speak only when
`~/.sessionkit/mail/settings/mail.json` holds `"hooks": true`, the one setting. A
fresh install has no file: the first ride-along writes it OFF
(`{"version": 1, "hooks": false}`) and says so, once; a faulty file is off
and says so once. `SESSIONMAIL_PAGER=off` wins over the file. The commands
ignore it, and so does mail-guard (always on). Off is silent, mail
included: mail rests in its box for `message list` or `pager listen`. The
one exception is the DEAF line on a `pager_required` seat, above.
On, their lines live in `hook_lines.toml`: *pager is not listening*, with
the arm instruction (Stop, exit 2); *pager is listening* (on a monitored
seat it says the monitor hears for you and `pager mute` silences it); or
*pager is muted* after `pager mute`, or after a `pager stop` of a hand
listener. The ride-along gives it after the mail,
once, then only on change. Every seat with no listener is nagged unless
it is muted.
`pager stop` is `pager off`.

Wake carry: every wake carries. A rule line `wake` or
`wake!` (its synonym: `carry` or `carry!`) pages and lets the wake TURN
give the mail. The page still says only "look"; it also leaves a carry
stamp. The notice turn's UserPromptSubmit unlinks it and runs the
ride-along, so the mail arrives with no tool call, once. Built in:
`carry! from:$operator`, `carry call:$me`, `carry call:everyone`,
`carry priority:urgent from:$urgent`, and `wake to:$me`, which carries
too. `carry *` is `wake *`; a draft never carries. Host switches, in the
locked rules file: `set carry off` (every wake is a plain wake) and
`set carry-prompt on` (a typed prompt carries too; default off). Inside a
subagent, never. A mail that could not be judged wakes, never carries.

Standing down a hand-armed seat after a bounded `pager listen --timeout N`
returned 124: run `pager off`. Nothing is listening then, so it exits 3
("nothing listening") - expected: the off stamp is written all the same,
so the Stop hook stays silent and the ride-along says *pager is muted*,
once. A 124 alone writes no stamp; the `pager off` is the stand-down. A
seat its monitor hears for never gets a 124, and never runs `pager off`.

References (flat, 40 lines / 400 tokens each): `references/`.
Specs: `openspec/specs/pager/spec.md`, in the git repository; the release
archive leaves `openspec/` out.
