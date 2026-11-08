---
name: shorten
title: Over 69, the subject is shortened and sent by default, keeping the original
---

By default `message send` shortens a subject that is over 69 or not ASCII
and sends it, instead of refusing. Turned off, it refuses as before.

- **ON by default:** no line, no file, or `set subject-shorten on` in the
  locked file, `rules.locked`, in `~/.sessionkit/mail/state/` (in
  `~/.sessionmail/` on an existing host where it is still there).
- **OFF:** the host writes `set subject-shorten off` in the locked file; a
  seat sets `SESSIONMAIL_SUBJECT_SHORTEN=off`, and other values are ignored
  with a notice; one mail uses `--no-shorten` (MCP: `no_shorten`). OFF
  refuses as before.
- **How:** fold to ASCII (`—` to `-`), collapse spaces, drop filler words
  from the end, cut at a word with `...`; ` EOM` is kept.
- **Says so** after the MTA accepts: on stderr (stdout stays the id), or in
  `notices`. Exit 0. Every `--json` success also carries
  `"subject": {"shortened": bool, "original": str, "sent": str}`.
- **Original:** in `X-Original-Subject:`, exact. It is NOT signed. To change
  the result, send a reply with `--supersedes`.
- **Still refused:** a line break or control character; a backtick or `$(`
  as typed; a gutted subject, with `(not shortened: why)`.
- **Capped:** a subject more than N characters OVER the limit is refused,
  with `(not shortened: 24 over; the cap is 10: ...)`. N is the host's
  `set subject-shorten-max N` in the locked file, default 10; no seat or
  flag can raise it. It caps the overage, not the characters removed. A
  subject that fits and is only folded is never capped.
- **N must be a whole number above 0.** `0`, a negative or `ten` is
  INVALID: the line is named, skipped, and the default 10 applies. To never
  shorten, write `set subject-shorten off`, not `set subject-shorten-max 0`.
- **Counted:** `sessionmail_send_total{reason="subject_shortened"}`, in the
  textfile collector.
