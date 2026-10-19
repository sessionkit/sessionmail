---
name: root-labels-items
title: A mailbox is a filesystem - one root, labels only under it, numbered items
---

The root is one signed message whose id lives in the agent's environment.
Under it, labels; under labels, items; nothing else.

- **Root** - `<AGENT>_ROOT` in the agent definition's environment block.
  Never `SESSION_MAIL_ROOT`: that name is the mailstore directory.
- **Label** - one `[WORD]`, plural, no body: `[RULES] [HOW-TO] [BOOT]
  [CONVO] [LESSONS] [RULINGS]`. An item threaded to the root beside the
  labels is reported, not hidden.
- **Item** - numbered (`10`, `20`, `20.1`), subject = the claim, body =
  the why. Written by replying under the label, To `<owner>+memory`,
  From the owner. Unverified is not an edge; verified is self-signed
  (the mail's own key), so it does not prove the owner sent it.
- **Supersede** - a reply declaring `Supersedes:` (ONE id per mail; a
  repeated `--supersedes` keeps only the last). Two live copies of one
  number are a FORK: readers report it, `memory run` runs neither copy.
  Resolve it with an UNNUMBERED superseder; a numbered one is a new copy.
- **`[BOOT]`** - one letter to self, rewritten whole; `[WAKE]` items are
  script-mails `memory run` executes in number order, only when
  `SESSIONMAIL_MEMORY_RUN=1` and the signer passes `--trust`.

Depth: `note read the-mailbox-is-the-memory`.
