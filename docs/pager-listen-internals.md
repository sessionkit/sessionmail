# How `pager listen` works

`pager listen --help` says when it pages, what it prints and how to stop.
This is how, for whoever has to reason about it. Paths below are under
`<home>/sessions/<session>/`, written `<session>/`; `<home>` is
`SESSIONMAIL_HOME`, default `~/.sessionmail`.

- **One box.** It watches this session's box (the session id is a box);
  with no session id, the maildir `$MAIL` names, which must hold `new/`. It
  reads no subscriptions: what else reaches the box is postfix's doing, set
  by `message subscription add BOX`.
- **Birth.** A session's box is created when the session starts, by the
  SessionStart hook (`pager hook start`), and its alias line is minted in
  the same step, bounded in time. `pager listen` runs the same birth
  (`_birth.ensure_session_box`), so a session that has no hook still gets
  its box on its first listen. The banner says which: `born box` names
  the box and either "made here" (this listen) or "born at SessionStart
  HH:MM:SS (source)" plus what the generator did, read from the born record
  `<session>/born`. It also says the doorbell's state: whether mail sent to
  the session will reach the box. The birth also reports the boxes the
  launcher subscribed from `SESSIONMAIL_SUBSCRIBE`, once. With no MagicDNS
  suffix it regenerates no map, and says so. A `$MAIL` box is never
  created: a human's mailbox is theirs to make.
- **The ride queue.** Every arrival in the box's `new/` is hardlinked
  into `<session>/ride/new/`. The ride-along (the PostToolBatch hook)
  scoops that queue on each tool call, so a busy session reads its mail
  without being paged.
- **When it pages.** Idle (not busy for `SESSIONMAIL_IDLE_SECONDS`, 1, by
  Claude Code's own status file, or `session get idle`): at once. Busy:
  when the oldest link has sat in the queue longer than
  `SESSIONMAIL_PAGE_AGE_SECONDS` (60) from its delivery time, because
  nobody scooped it. Then it prints the page and exits; the caller runs it
  again to keep listening. `SESSIONMAIL_PAGE_LIST=on` (or `--list`) adds
  one line per message: box, sender, subject, never a body. The flag wins:
  `--no-list` beats `SESSIONMAIL_PAGE_LIST=on`.
- **Marks.** It claims nothing and moves nothing. It marks each message it
  pages with `seen` (reason `pager wake`), so the same message does not
  page this session again inside the reminder window
  (`SESSIONMAIL_REMIND_MINUTES`, 60). Another session's marks never
  silence this one.
- **Stamps.** A running listener writes its pid to
  `<session>/stamps/listening`; that is what `pager status` and the hooks
  read as "listening", and what `pager stop` signals.
- **Its log on the way out.** A listener logs its life in
  `<session-dir>/log/mail/pager.jsonl`, each row under a lock on `seq`
  (and the `exit` row reads the `quiet` counter under its own lock). On
  its way out (a signal, a timeout, a page, `pager stop`, or the exec of
  a followed session) it waits for those locks at most
  `SESSIONMAIL_LOG_LOCK_SECONDS` (1 by default; 0, a negative value, a
  non-number or a non-finite value read as 1), one budget for the whole
  exit. A lock another process still holds then costs the row, never the
  exit: the row is dropped, or the `exit` row is written without its
  quiet counts if only `quiet` was held, and ONE stderr line starting
  `pager: ` names the lock and the budget. Stdout is never used for it.
  Every other row, a hook's or one written while listening, still waits
  for its lock.
- **Keyed on the session.** The queue, the stamps and the marks key on
  the session id, so a session with no seat gets the same page. With
  neither a session id nor a seat there is no queue; it still listens,
  and the banner says so.

On an interactive Claude Code seat the plugin runs this listener for you,
as `pager listen --monitor`: see [the pager monitor][pager-monitor]. A
client outside Claude Code: see [not running under Claude
Code][pager-outside-claude].

[pager-monitor]: pager-monitor.md
[pager-outside-claude]: pager-outside-claude.md
