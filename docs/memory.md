---
name: memory
doctype: reference
title: memory
tags: [sessionmail, memory, mail, command]
created: 2030-06-09
---

# memory

`memory` reads an agent's own mailbox as a filesystem: a root, labels under it, and numbered items under each label.

- **A PATH walks from the root by subject:** `/ASKS/12`, `/BOOT/latest`,
  `/CONVO/today`. Every name a listing prints is a valid next segment.
- **The root** is the Message-ID in `$<AGENT>_ROOT`. The agent is the
  environment. `--box NAME` reads a shared box instead.
- **Writing is sending.** You add an item with [`message send`][message],
  replying to the label's Message-ID and addressed `<agent>+memory`.
- **Nothing is edited.** A change is a new item that supersedes the old
  one (`message send --supersedes ID`). The old one is hidden, never
  deleted. `--all` shows it again.
- **Only members count.** An item must be signed, and be From and To the
  box's owner. A listing marks anything else `⊘ NOT A MEMBER`, with the
  reason, and `cat` refuses it.
- **Two live items under one number are a FORK.** `run` refuses it.
  Retire the unwanted copy with an unnumbered mail that supersedes it.
- `memory` reads through the notmuch index, so it needs `notmuch` on
  PATH and sessionmail's notmuch profile.

## The verbs

Each verb's `--help` is the full page: every flag, and its exit codes.

| Verb | What it does |
| --- | --- |
| `ls` | One level under a PATH: its labels and items. The default PATH is `/`. |
| `tree` | The subtree under a PATH, subjects only. Ends with its own token cost. |
| `cat` | One item with its body. `--id` prints only its Message-ID. |
| `run` | Run the numbered items under a PATH, in order. Off unless opted in, and trusted signers only. |
| `wake` | The boot readout: the BOOT letter, recent threads, and counts. Peek-only. |
| `init` | Print a box's root, or mint the root and its standard labels. |
| `mcp` | Serve the verbs above as MCP tools, except `run`. Blocks. |

What each one does today:

- **`cat`** shows only the text the signature covers. Its footer says the
  signature verifies and the signer is not pinned: the key is the one
  the mail carries, and nothing yet says whose it is.
- **`run`** runs nothing unless `SESSIONMAIL_MEMORY_RUN=1`. `--list`
  prints what would run and needs no opt-in. An item runs only if its
  signer passes `message verify --trust`. It is not an MCP tool, because
  it runs script-mail as you.
- **`wake`** claims nothing and marks nothing. Its box defaults to the
  seat, then the session, the same one `pager listen` resolves.
- **`init`** refuses on every box today (exit 3): the box key it signs
  with has no settled home yet. A first root is made by hand:
  [your first memory root][memory-first-root].

## How `memory` exits

`memory --help` gives the codes they share. Each verb's own `--help` adds
the rest.

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 3 | a signature was refused |
| 4 | `run` is off (`SESSIONMAIL_MEMORY_RUN` is not 1) |

## One example

Wake, then walk the box down to one item.

- Print the boot readout. It reads, and marks nothing:

```sh
memory wake
```

- List the labels under the root:

```sh
memory ls /
```

- List the items under one label:

```sh
memory ls /ASKS
```

- Read one item, with its body:

```sh
memory cat /ASKS/1
```

## See also

- [`message`][message]: sending, which is how memory is written.
- [The pager][pager]: waiting for mail, and being paged when it lands.
- [Your first memory root, by hand][memory-first-root].

[message]: message.md
[pager]: pager.md
[memory-first-root]: memory-first-root.md
