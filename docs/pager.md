---
name: pager
doctype: concept
title: Pager
tags: [sessionmail, pager, mail, notification, ride-along]
created: 2030-06-09
---

# Pager

The pager watches one mailbox, never takes the mail, and pages only when nothing has scooped it for T seconds.

## The model

One process, no children. It watches **one box** and blocks.

Every arrival in that box's `new/` is **hardlinked** into this session's
ride-along queue. Links are free; nothing is moved.

It reads the **age of the oldest entry** in that queue and pages only when the
age crosses **T**. An old entry means no tool call has scooped it, so the agent
is idle. An empty queue means the ride-along is keeping up, and the pager does
nothing.

**A page delivers nothing.** It says "look". The **re-arm is the delivery**: the
next tool call's ride-along scoops the queue. One loop, no second delivery path,
no claim race between pager and ride-along, nothing given twice.

## The loop

- It **looks first, then waits**, so mail already sitting in the box is found
  with no wait at all.
- The wait is a **kqueue** on `new/`, capped at **one second**. It ends on a
  change to the directory or when the cap expires, whichever comes first: the
  event only decides how soon the next look happens.
- Every look **re-reads the directory**. There is no remembered list, so a file
  that has gone is simply absent and pages nothing.
- Age is the **inode's mtime**, which for a hardlink is when the mail was
  **delivered**, not when it was linked. The honest age of the mail.
- When the oldest is not yet due it sleeps `min(poll, T - age)`, waking **at the
  crossing** and never later than a second. So it waits until the mail is T
  seconds **old**, not T seconds **from now**.
- Within one run, already-reported entries are passed back as an ignore list.
  Across runs, dedupe is this session's marks, not that list.

## What a page carries

A page is **quiet by default**: no sender, no subject, no per-message detail
at all (specs/pager, "The pager is a nudge, and the re-arm carries"). The
re-arm IS the delivery - the
next tool call's ride-along carries the entries - so the page only says there
is something to look at.

Four things, in this order:

1. that the pager is now **off**, and the command that arms it again, read
   from the one arm-instruction source (`sessionmail/arm.txt`).
2. **how many** are waiting - a count, never a manifest.
3. the **ride-along's state**, off the mtime of
   `sessions/<session>/stamps/ride-along`: live (they arrive on the next tool call, so
   wait), stale (past the busy TTL, so act), or no stamp at all (it has not
   run for this session - which is not the same as stale).
4. the commands to **list, read and mark** them, for the reader who acts
   anyway. `read` and `mark` take many ids in one call.

One line per entry - box, sender, subject, **never a body** - comes back only
when the switch is set, **oldest first**, and **under** the four things rather
than instead of them. The switch defaults off and is BOTH `SESSIONMAIL_PAGE_LIST`
(`on`, `1`, `yes` or `true`; anything else is off) and `--list/--no-list` on
`pager listen` - and **the flag wins**, in both directions, so `--no-list`
gives a quiet page however the variable is set.

It **claims nothing, moves nothing, removes nothing**. It marks each entry
`seen` once, reason `pager wake`, so the same id is not paged again inside the
reminder window.

## Where things are written

Under `~/.sessionmail/sessions/<session-id>/` - **everything a page reads keys
on the session id, never the seat**: the seat was never the thing. A seat
is a NAME, and it may be absent: a seatless
session reads the same three paths, and gets the same page.

- `ride/new/` - the ride-along **queue**. Hardlinks sharing inodes with the
  maildir. This is what the pager measures the age of.
- `stamps/` - `stopped`, `ride-along`, `reminded`, `listening`. The **stopped**
  stamp is what selects T.
- `marks` - one event line per mark: `seen`, `read` or `done`, with the seat's
  name, the session and a reason. **Per session**: a
  successor does not inherit them.

A run with no session id **at all** is a case of its own: it keeps the
seat's own `ride/-/`, `stamps/-/` and `marks`, and the banner says so.

Under `~/.sessionmail/seats/<seat>/`:

- `subscriptions/` - one file per box the seat is subscribed to, written by
  `message subscription add --seat` and compiled into the alias map by the
  generator. **The pager reads none of them**: the
  born `seat`/`session` saved queries, their birth at the first listen and
  `pager subscriptions` are retired. Plain `pager status` reads this session's
  box instead, which is where every subscription now delivers.

Outside the seat:

- `<mailroot>/<box>/{new,cur,tmp}` - the maildir. The pager never writes here.
- The host's postfix maps, `valias` and `vmailbox` - the doorbell. `pager listen`
  mints this session's alias line when it is missing, and its banner reports the
  doorbell's state.

## T, and the knobs

- **T comes from the stopped stamp**: a stopped session gives **0**, paging at
  once; a running one gives `SESSIONMAIL_PAGE_AGE_SECONDS`, default **60**.
- `--timeout S` gives up after S seconds without a page; exit 124.
- `SESSIONMAIL_REMIND_MINUTES`, default 60, is the window inside which an
  already-paged id is not paged again.
- There is **no flag for the page age**: it is the environment variable only,
  while the timeout has a flag.
- `SESSIONMAIL_PAGE_LIST`, default **off**, and `--list/--no-list` are the same
  switch - one line per message instead of a bare count. Given both, the flag
  wins.

## Identity, and the seatless case

Identity comes from the environment: the seat from `TM_AGENT_ADDRESS`, then
`CLAUDE_CODE_AGENT`; the session from `CLAUDE_CODE_SESSION_ID`, then
`CLAUDE_SESSION_ID`.

**With no seat but a session id, everything still works** - the queue, the
stamps and the marks key on the session id, so a
seatless session gets the same page a seated one gets, off the same files. The
seat is a NAME: it is the column in a marks line and the doorbell's address,
and with none the session id stands in for it (never `none`). `message send`
still refuses with *no seat in the environment to send From*.

**With no session id AND no seat there is no queue.** The banner says so, and
the pager watches the box's own `new/` directly. Two consequences:

- Nothing is hardlinked, so **a second reader of the same box can take the
  message away**. A mail client draining `new/` into `cur/` silences the page
  permanently, not for a one-second window. An identified session is protected:
  once the link exists, the original moving does not touch it.
- There is no marks file, so **every run re-lists everything**.

With a seat but **no session id**, the queue is the seat's own `ride/-` and the
marks line carries `-`. That is the deliberate placeholder for "no session",
and it means every session-less shell on one
seat shares one queue.

## See also

- `openspec/specs/pager/spec.md` - the contract, in the git repository; the
  release archive leaves `openspec/` out. When the help and the spec disagree,
  the help is the bug.
- The slipbox note `mail-hierarchy-standard` - the mailbox as a filesystem,
  which is what the pager is watching. Private: not in this tree.
- The slipbox note `project-conventions` - the thin spine for building here.
  Private: not in this tree.
