# The frozen-turn watch

The pager monitor can watch its own seat for a turn frozen mid-request: Claude
Code is busy, a main-thread turn is open, and nothing has been written to the
transcript for a long time. **It only detects.** It sends no keys, captures no
pane, sends no mail and starts no process. It ships **off**.

## Turning it on

One host-wide switch, in `~/.sessionkit/mail/settings/mail.json`:

```json
{"frozen_watch": true}
```

- Only the boolean `true` turns it on. A missing key, `false`, or any other
  value (`"true"`, `1`) leaves it off, and `pager status` says why it ignored
  the value.
- The monitor reads the switch at every check (every 30 seconds), so turning
  it off takes effect without restarting the seat.
- A hand `pager listen` never runs the watch. Only the monitor does.

The other keys are optional, and [settings][settings] lists them all.

- `frozen_rules`: which rules run. `["silence"]` is the default. `"chain"`
  is opt-in.
- `frozen_after_seconds` (N, default 720): how long the `silence` rule waits.
- `frozen_chain_seconds` (R, default 900): how long the `chain` rule waits.

N and R each have a floor that follows Claude Code's own timeouts. A lower
value is raised to the floor, and the gauge says so.

## The two rules

- **silence**: the sidecar says `busy`, the main turn is open, every tool call
  has its result, and no main-thread entry has been written for N seconds.
  Entries that mail arriving mid-turn writes (`queue-operation`, a
  `queued_command` attachment) do not count as progress.
- **chain**: the sidecar says `busy`, the main turn is open, and the
  turn's model requests have gone unanswered for R seconds, counted from the
  first request of the run.

A tool call that runs for a long time, a long think, and a retry Claude Code
is still making are never frozen.

## What you see

Once per frozen **turn**, whichever rule found it:

- **One line from the monitor** to the seat. A frozen turn cannot read it, so
  it lands when the turn ends, whether by Claude Code's own abort or by a human
  pressing Escape:

  ```text
  pager: this seat looked frozen mid-turn: its open turn wrote nothing for 12m from 18:26 (rule silence, frozen_watch). Detect only: no keys were sent and no mail. If you can read this, the turn has ended. `pager status` shows the frozen: gauge.
  ```

- **A `frozen:` line in `pager status`**, on stderr:

  ```text
  frozen: off (frozen_watch is not true)
  frozen: watching (silence; N 720 s)
  frozen: silent 12m mid-turn since 18:26 (rule silence; noticed 18:38)
  frozen: not judged (no transcript matches ~/.claude/projects/*/<session>.jsonl)
  ```

- **The stamp file** (below).

A second episode in the same turn updates the stamp and the gauge, and says no
second line. So does a `chain` episode that overlaps a `silence` one. When the
seat moves again, the episode is marked resolved in the stamp and the gauge. It
is never announced.

## The stamp: `<session dir>/mail/stamps/frozen.json`

The stamp sits beside the session's other stamps. Another seat or an
instrument can read it. The monitor writes it atomically (a temporary file,
then a rename), and only when something a reader would see changes. It writes
none while the switch is off. Because the stamp outlives a monitor that starts
again on the same session, the same turn is never noticed twice. A `/clear`
gives a new session id, and so a new stamp.

| Field | What it holds |
|---|---|
| `version` | `1`. A reader ignores a stamp of another version |
| `session` | the session id the stamp belongs to |
| `checked` | the last check's standing: `{"judged": true, "why": ""}`, or `judged` false and why (no transcript, two of them, a read that failed) |
| `active` | the episodes open now, by rule: `{"silence": {"anchor", "onset", "turn", "why"}}`. Empty when nothing is frozen |
| `rule` | the rule of the latest episode to start |
| `key` | that episode's key, `[session, rule, anchor]` |
| `turn` | the uuid of the prompt that opened the frozen turn |
| `anchor`, `onset` | when the silence (or the unanswered run) began: epoch seconds, and the same as an ISO UTC stamp |
| `detected` | when the monitor saw the episode start (ISO UTC) |
| `silent` | the seconds measured at that moment |
| `why` | the rule's own words at that moment |
| `noticed` | when the turn's one line was said (ISO UTC) |
| `noticed_turn` | the turn that line was for |
| `episodes` | how many episodes that turn has had |
| `message_id` | always `null`: this build sends no mail |
| `resolved` | when the last episode ended (ISO UTC), or `null` while one is open |
| `resolved_why` | what ended it, per rule |

An example, mid-episode:

```json
{
 "active": {"silence": {"anchor": 1907193601.0, "onset": "2030-06-09T00:00:01.000Z",
                        "turn": "00000000-0000-4000-8000-000000000001",
                        "why": "silence: 721 s since the clock 2030-06-09T00:00:01.000Z, at or over N 720 s"}},
 "anchor": 1907193601.0,
 "checked": {"judged": true, "why": ""},
 "detected": "2030-06-09T00:12:02.000Z",
 "episodes": 1,
 "key": ["<session id>", "silence", 1907193601.0],
 "message_id": null,
 "noticed": "2030-06-09T00:12:02.000Z",
 "noticed_turn": "00000000-0000-4000-8000-000000000001",
 "onset": "2030-06-09T00:00:01.000Z",
 "resolved": null,
 "resolved_why": null,
 "rule": "silence",
 "session": "<session id>",
 "silent": 721.0,
 "turn": "00000000-0000-4000-8000-000000000001",
 "version": 1,
 "why": "silence: 721 s since the clock 2030-06-09T00:00:01.000Z, at or over N 720 s"
}
```

## No mail yet

The watch has one hook, `_frozen.notify`, which is called once per frozen
turn and does nothing today. A notice sender would plug in there, once a
channel is chosen and the pager spec's *No children* rule allows it.

[settings]: settings.md
