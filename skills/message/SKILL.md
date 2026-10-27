---
name: message
description: >-
  Moves signed mail between agents and the user over one maildir: send, list,
  tree, read, mark, verify. Use when an agent needs to send a message,
  reply or supersede one, list or read a box, mark a message seen, or check
  who signed one. `message`
  IS on the PATH - it is the verb to reach for, not `session mail`.
---

# message

`message --help` is the whole of what there is to know: one verb a
line; `message <verb> --help` holds the flags.

Several agents in one seat? See the sessionkit skill's card, "Several agents in one seat".

- `send` - To/Cc/Bcc, `--in-reply-to`, `--supersedes`; body from a file
  or the MCP argument, never a shell string; refuses a subject over 69
  ASCII characters (unless the host shortens it) or one that could
  execute. `--header 'Name: v'` carries any header; a name we
  mint has no `X-` (the `mail-headers` note).
- `list` - subjects for a box; says what it filtered.
- `tree` - a thread as a tree.
- `read` - 40 lines / 400 tokens of body by default; claims.
- `mark` - seen and reactions, as headers on a reply; never wakes anyone.
- `receipt` - hidden: what SID RECORDED, never that it read it; docs/message-receipt.md has exits.
- `verify` - who signed a message; `--trust` judges the signer. The
  verifier's exits: 0 good, 1 bad, 2 unsigned, 3 usage.

References (flat, 40 lines / 400 tokens each): `references/`.
Specs: `openspec/specs/message/spec.md`, in the git repository; the release
archive leaves `openspec/` out.
