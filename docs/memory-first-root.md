# Your first memory root, by hand

`memory` has **no automated first step yet**. `memory init` refuses on
every box today (exit 3): the box key it needs has no home, and that
design is not settled. This page is the by-hand recipe that works now:
it gives a fresh seat a root and one label that `memory ls /` shows.

- **Box:** use `claude`, memory's default agent. `message setup` alone
  makes a box named after your login, not `claude`, so you make
  `claude` yourself (step 1).
- **Root:** you send a `[ROOT]` mail to the box and export its
  Message-ID as `$CLAUDE_ROOT` (step 3).
- **Labels:** each one is a reply to the root, addressed `claude@<host>`
  (never bare `claude`) and signed with a session key (steps 2 and 4).
- **The key:** sessionmail ships none for you. You make one with stock
  OpenSSH (`ssh-keygen`, `ssh-agent`, `ssh-add`). Without one, the root
  still shows, but every label is hidden as `NOT A MEMBER ... unsigned`.
- **This page:** it ships in the sessionmail plugin and the release
  archive, not in the Python wheel. A wheel-only install has no copy of
  it, though memory's refusals name it by this path.

Requirements:

- `message` and `memory` on your PATH (the sessionmail install).
- OpenSSH: `ssh-keygen`, `ssh-agent` and `ssh-add`.
- A bash or zsh shell. Run every step in the SAME shell: steps 2 and 3
  export variables the later steps need.

## 1. Make the box `claude`

```bash
message setup --box claude
```

```bash
export CLAUDE_CODE_AGENT=claude
```

## 2. Make a session key and load it

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -N '' -C claude -f ~/.ssh/memory-claude
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/memory-claude
export CLAUDE_SESSION_AGENT_SOCK="$SSH_AUTH_SOCK"
export CLAUDE_SESSION_PUB=~/.ssh/memory-claude.pub
```

- `ssh-keygen` refuses to overwrite an existing `~/.ssh/memory-claude`.
  Already made it once? Skip that line.

## 3. Send the root and export its id

```bash
printf 'The root of claude memory.\n' > ~/memory-root.txt
export CLAUDE_ROOT="$(message send "claude@$(hostname -s)" -s '[ROOT]' -f ~/memory-root.txt)"
echo "$CLAUDE_ROOT"
```

- The last line prints the root's Message-ID, `<...@...>`. Keep it:
  every new shell needs `export CLAUDE_ROOT='<that id>'` again. Put
  that line in your shell profile.

## 4. Send one label under the root

```bash
printf 'Rule one: say what you measured.\n' > ~/memory-rules.txt
message send "claude@$(hostname -s)" -s '[RULES]' -r "$CLAUDE_ROOT" -f ~/memory-rules.txt
```

## 5. See it

```bash
memory ls /
```

- Expect the header `[ROOT] from claude ✓`, then `└ [RULES]` and `1 labels`.
- Read the label back:

```bash
memory cat /RULES
```

## What each refusal means

- `memory: no agent`: `$CLAUDE_CODE_AGENT` is not set in this shell.
  Run step 1's `export` again.
- `memory: no memory yet for claude`: `$CLAUDE_ROOT` is not set in this
  shell. Export it again (step 3).
- `no such BOX 'claude'` (`memory init`, exit 2): run step 1.
- `the box key for 'claude' is absent` (`memory init`, exit 3): expected
  today on every box. Use this recipe instead of `memory init`.
- `⊘ NOT A MEMBER ... To/Cc does not name 'claude'`: the label went to
  bare `claude`. Re-send it to `claude@$(hostname -s)`.
- `⊘ NOT A MEMBER ... unsigned`: the label went out with no session key.
  Run step 2 in this shell, then re-send the label.

## What this is not

- **Not canon.** A root sent this way is admitted by its
  `$CLAUDE_ROOT` hint alone; no box key vouches for it, and the labels
  verify only as `signer not pinned`. `memory init` will mint a canon
  root once the box key's home is settled.
- **Not the fleet's way.** A launcher-kicked seat gets `$<AGENT>_ROOT`
  from its launcher. This page is for a seat that has none.
- A seat with its own address and a trust registry uses
  [`message seat`][message-seat] for its key instead of step 2.

[message-seat]: message-seat.md
