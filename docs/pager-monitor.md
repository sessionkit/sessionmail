# The pager monitor: on by default

The plugin ships `monitors/monitors.json`. For every interactive seat,
Claude Code runs its one entry, `hooks/pager-monitor`, which execs
`pager listen --monitor` with the `pager` on PATH: the same pager the
hooks run, never a checkout's `.venv`. It runs under claude for the whole
session, and each line it prints is a notification. Nothing has to arm it.

- **No hand arm.** On a seat whose monitor is listening, `pager listen`
  is not needed. Run anyway, it exits 0 at once, saying "the pager
  monitor (pid N) hears for this session; you don't need pager listen.
  Do not re-run it." If the monitor was muted, that `pager listen`
  unmutes it and says so. `pager listen BOX` on a BOX the monitor does
  not hear (neither this session's box nor a subscription of this
  session or seat) is refused instead, exit 3, and names the repair:
  "refused: the pager monitor (pid N) hears this session's box, not BOX -
  to hear BOX, run message subscription add BOX".
- **Mute, unmute.** `pager mute` silences whatever listens for the
  session, the monitor or a hand `pager listen`: it keeps running, pages
  nothing, and mail rides along on tool calls as usual. `pager unmute`
  resumes. Unmute on a seat that is not muted does nothing: "not muted:
  already listening", exit 0. With nothing listening, both say the monitor
  is not running, that it can only start when the seat launches, and name
  the two ways out: run `pager listen` in the background, or restart the
  seat (exit 3).
- **`pager off` / `pager stop` refuse on the monitor.** They print one
  line naming `pager mute` (to silence it) and `pager stop --kill` (to
  end it for good), write nothing, and exit 4. On a hand `pager listen`
  they stop it, as before.
- **`--kill` is the only real end.** `pager stop --kill` (or `pager off
  --kill`) ends the monitor, and Claude Code does not restart a monitor
  that exits. Until the seat restarts, listen by hand.
- **The switch.** The monitor is on unless something turns it off, and
  only ever off:
  - for every seat on the host, `"monitor": false` in
    `~/.sessionkit/mail/settings/mail.json` (a missing key means on);
  - for one seat, `SESSIONMAIL_MONITOR=off` in claude's launch
    environment. It cannot turn the monitor back on over the host's
    `false`; any other value is ignored, and `pager status` says so;
  - `SESSIONMAIL_PAGER=off` turns the monitor off too, with the rest of
    the pager.

  Switched off, the monitor prints one line naming what switched it off
  and exits 0. `pager status` shows a `monitor:` line: armed, not armed or
  off, and the setting that decided.
- **Headless seats run no monitor.** Claude Code arms monitors only in an
  interactive session, so a `claude -p` seat arms by hand with `pager
  listen` in the background, as before. The same holds when Claude Code's
  own monitor gate (`tengu_amber_sentinel`) is off: nothing arms, and
  nothing in Claude Code says so.
- **`pager_required`: say DEAF when nothing listens.** A boolean in the
  mail settings, read at the host, seat and session layers (the most
  specific wins). `SESSIONMAIL_PAGER_REQUIRED` (`1`/`true`/`on` or
  `0`/`false`/`off`) wins over every layer, both ways, and
  `SESSIONMAIL_PAGER=off` beats it. Missing means not required. On a
  required seat, when nothing listens past a grace of about 10 seconds
  after claude starts, the Stop and PostToolBatch hooks say `pager: DEAF`
  with the reason (the monitor did not arm, was ended, or none was
  expected) and the two ways out. They say it even with `"hooks": false`,
  where it is the only thing they say. A muted seat is not nagged, with
  one exception: after `pager stop --kill`, a required seat stays DEAF
  even through `pager mute` or `pager off`, because the monitor is gone.
  Only a hand `pager listen` or a restart of the seat clears it. `pager
  status` shows a `required:` line and its source.

What the listener under it does: [how `pager listen` works][internals].

## How `--monitor` behaves

The flag is hidden from `pager listen --help`, because its one caller is
`monitors/monitors.json`. Claude Code does not restart a monitor that
exits, so `--monitor` never exits on a page:

- ONE line per page on stdout, flushed, and nothing else there: the count,
  the address each mail was sent to, what woke it - no sender, no subject,
  no re-arm. At most 480 characters (Claude Code cuts at 500).
- Lines at least `SESSIONMAIL_MONITOR_SPACING_SECONDS` apart (2.5): Claude
  Code drops output past a bucket of 10 refilled 1 per 2 s, and a dropped
  page is a wake that never happens.
- After a page it keeps listening. The `seen` marks, the carry stamp and
  the woke records are written as for any page, BEFORE the line, so the
  wake turn finds the carry stamp. A mail paged and never scooped pages
  again after the reminder window, as a re-arm would.
- The "listening" confirm mail is sent once per monitor process, never per
  page.
- SIGTERM, SIGINT or SIGHUP end it with exit 0 and clear its `listening`
  stamp. When it cannot look it prints `pager: DEAF - ...` and exits 2.
- A monitor that finds another listener on its session waits, and takes
  over when that one ends.
- It refuses `--timeout`, `BOX` and `--quiet`.

## The old plugin

`plugins/pager-monitor`, the old separate plugin, is retired: it now
arms nothing. Drop its `--plugin-dir` from your launch when convenient.
Why it stays one release, and what it still holds: [its
README][old-plugin].

[internals]: pager-listen-internals.md
[old-plugin]: ../plugins/pager-monitor/README.md
