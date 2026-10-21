# `message seat agent`: a seat's own ssh-agent under launchd

A seat with no Claude session (`bot.alice`) signs its mail through an ssh-agent
that launchd keeps alive. The adapter that sends as the seat lives outside this
repo; this is its seam, unchanged.

The wrapper and its template ship in the wheel, and both are reached
through `message`, which keeps the package at three commands:

    message seat init --registry DIR ADDRESS     mint the seat's key (was scripts/seat-init)
    message seat agent ADDRESS [SOCKET]          run its agent (was scripts/seat-agent/seat-agent)
    message seat agent --install ADDRESS [SOCKET]   render and load the LaunchAgent
    message seat agent --uninstall ADDRESS           unload it and remove its plist
    message seat agent --template                print the LaunchAgent template

The `seat` noun is hidden from `message --help` and from the MCP tools; `message
seat --help` answers when typed.

| File | What it is |
|---|---|
| `src/sessionmail/seat/seat-agent` | The wrapper, packaged (moved verbatim from its old path). `message seat agent ADDRESS [SOCKET]` restores SIGPIPE and SIGXFSZ and **execs** it, so the pid launchd started is the wrapper's own. It runs `ssh-agent -D -a SOCKET` in the foreground, loads the key that `message seat init` minted, and checks it against `seat.pub`. |
| `src/sessionmail/seat/seat-agent.plist.template` | The LaunchAgent, packaged beside the wrapper, as a **template** with `@PLACEHOLDERS@`. `message seat agent --template` prints it byte for byte. It also documents the adapter's env. |
| `scripts/seat-agent/seat-agent` | A thin shim at the old path: it `exec`s the packaged wrapper with the same arguments. |

## The wrapper

- **The key** is `$SESSIONMAIL_HOME/seat-keys/ADDRESS/id_ed25519`, which is
  `message seat init`'s layout. The pub the adapter names is `seat.pub` in the same dir.
- **It refuses before starting anything (exit 2)** when:
  - the sessionmail install is broken: the packaged wrapper is missing or not
    executable (said by `message` itself);
  - the key or the pub is missing;
  - the key or its dir has group or world bits;
  - a private key sits beside `seat.pub`'s stem (probe 8c);
  - the socket path is over 103 bytes;
  - a live agent already answers on the socket;
  - the socket path is something other than a socket.
- **A stale socket** (nothing answers on it) is removed.
- **A failed load exits 1.** That covers a key that will not load, and an agent that does
  not hold `seat.pub`'s key. The agent is stopped first.
- **The agent dying is exit 1**, so `KeepAlive` restarts the job, with
  `ThrottleInterval` 10 s between tries.
- **A TERM** (launchd stopping the job) stops the agent and removes the socket, exit 0.
- **The socket cap is 103 bytes.** macOS's `sun_path` is 104 bytes including the NUL.
  Measured: ssh-agent binds a 103-byte path and refuses a 104-byte one. The default,
  `~/.sessionmail/seat-keys/<address>/agent.sock`, is well under the cap.
- **An orphan agent.** If the wrapper is SIGKILLed outside launchd, its agent can outlive it,
  and the next start then refuses ("a live agent already answers"). Kill the orphan by
  hand. Under launchd this does not arise: a job's process group goes with the job.

## The template

`message seat agent --install ADDRESS [SOCKET]` renders it, writes it into
`~/Library/LaunchAgents`, loads it with `launchctl bootstrap`, and checks that the agent
answers. `--uninstall ADDRESS` boots it out and removes the plist. Their contract
(output, exits, the `Capability: install` marker) is in
[`docs/message-seat.md`](../../docs/message-seat.md).

`message seat agent --template` prints it, and nothing else: it never renders it, loads
it or runs launchctl. To render it by hand, replace each `@PLACEHOLDER@`, then lint it:

    message seat agent --template |
      sed -e 's|@ADDRESS@|bot.alice|g' -e "s|@MESSAGE@|$(command -v message)|g" \
        -e "s|@SESSIONMAIL_HOME@|$HOME/.sessionmail|g" \
        -e "s|@SOCKET@|$HOME/.sessionmail/seat-keys/bot.alice/agent.sock|g" \
        -e "s|@LOG_DIR@|$HOME/Library/Logs|g" > /some/scratch/dir/dev.sessionkit.mail.seat-agent.bot.alice.plist
    plutil -lint /some/scratch/dir/dev.sessionkit.mail.seat-agent.bot.alice.plist

`@MESSAGE@` is the path `command -v message` prints, not resolved: `~/.local/bin/message`
under uv tool or pipx, `<venv>/bin/message` in a venv. It stays the same across upgrades,
and its launcher is absolute, so launchd's `PATH=/usr/bin:/bin` is enough.
ProgramArguments renders to `message seat agent ADDRESS SOCKET`. The placeholder was
`@SEAT_AGENT@` before the wrapper was packaged; an old render line that fills only that one leaves
`@MESSAGE@` literal, and launchd fails loudly rather than run the wrong file.

On a host that runs `message` from an editable checkout, merging there changes the
seat's agent at its next start.

**No test and no make target loads a job on the real launchd.** The suite runs
`--install` against a fake `launchctl` (`SESSIONMAIL_LAUNCHCTL`). A live install is the
host operator's act: `message seat agent --install`, or the hand render above.

## The adapter's env (an example; the adapter owns it)

The process that sends as the seat carries these. The template's header comment shows
them as a plist dict.

| Variable | Value |
|---|---|
| `TM_AGENT_ADDRESS` | the seat, `bot.alice` |
| `DESK_SIGNER_SOCK` | this job's `SOCKET` |
| `DESK_SIGNER_PUB` | `$SESSIONMAIL_HOME/seat-keys/<address>/seat.pub`, apart from the private key |
| `SESSIONMAIL_REQUIRE_SIGNATURE` | `1`: a send that cannot sign is refused |
| `SESSION_REGISTRY` | the registry that `message seat init` registered into, for `message verify --trust` |

The adapter strips the caller's `CLAUDE_SESSION_AGENT_SOCK`/`PUB` and sets them from
`DESK_SIGNER_SOCK`/`PUB`. The sender itself is unchanged.

Neither file is on PATH, and nothing new is: `message seat agent` runs the wrapper,
and `message seat agent --template` prints the template.
