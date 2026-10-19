---
name: subject
title: A subject is 69 ASCII characters, front-loaded, and it never executes
---

A subject is 69 ASCII characters or fewer, the point in the first ~55,
and it never contains a backtick, `$(` or a line break.

- **69** - RFC 5322's 78 minus `Subject: `. The sender refuses over,
  unless the host shortens it (`shorten.md`).
  Measured before the limit: 71% of 1,932 subjects on one box broke it.
- **ASCII** - RFC 2047 base64 in the maildir makes a raw grep miss. Put
  the character in the body.
- **Front-loaded** - notmuch keeps 288 whole; every human view cuts.
- **Never executes** - a subject once ran `git ready approve`
  when a script quoted it later. So a backtick, `$(` or a newline is
  refused at EVERY door by default - command line and MCP alike -
  because a stored subject is read back into shells all day. A seat can
  switch the guard off: `SESSIONMAIL_SUBJECT_GUARD=off`. Remedy: a code
  span in the body.
- A reply's subject carries only what is new; never restate the parent.
- A correction is a reply with `Supersedes:`; nothing is edited in place.

Depth: `note read mail-rules`.
