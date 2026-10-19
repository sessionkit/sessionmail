---
name: memory
description: >-
  An agent's own mailbox read as a filesystem: `memory ls|tree|cat|run`
  over a root, labels and numbered items, with script-mails that run at
  wake, and `memory wake`, the boot readout. Use when an agent wakes,
  reads its rules or letter to self, or writes memory by replying under
  a label. `memory` IS on the PATH - it
  is the verb to reach for, not `session mail`.
---

# memory

`memory --help` is the whole of what there is to know: five verbs.

- `ls PATH` - the labels under a path (`/` is the root: labels only).
- `tree PATH` - items under a label, in number order; forks reported.
- `cat PATH` - one item, or `/BOOT/latest` for the live letter to self.
  The body is the text the signature covers; one it cannot vouch
  for is withheld: nothing printed, exit 3. No text/plain part: a notice.
  A `[ROOT]` admitted by its hint alone shows under one line, `(root
  admitted by its hint; unsigned)` or why it does not verify; exit 0.
- `run PATH` - execute the script-mails under a label. OFF by default:
  `SESSIONMAIL_MEMORY_RUN=1` opts in, and it is not an MCP tool.
  An item runs only if its signer passes `--trust`: a registry cert whose
  agent is the From. An untrusted one is refused, named, not run,
  exit 3. `--list` runs nothing. The subject never executes, the body is
  the program: the text the signature covers. An item with none (no
  text/plain part, or one that does not hash) is skipped, said on stderr.
- `wake [BOX]` - the boot readout: the live letter to self, recent
  threads, unread counts. Peek-only: a wake is a notification, it claims
  nothing. Was `session mail wake`.

The root comes from the environment (`<AGENT>_ROOT`), never a note.
Nothing is ever edited: an item changes by a reply that supersedes it.

References (flat, 40 lines / 400 tokens each): `references/`.
Specs: `openspec/specs/memory/spec.md`, in the git repository; the release
archive leaves `openspec/` out.
