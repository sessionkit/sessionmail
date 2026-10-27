# A seat with no Claude session: `message seat`

A bot or a desk adapter that sends as its own seat (`bot.alice`) needs a
signing key and an ssh-agent to hold it. Two verbs of `message` set that up.
The `seat` noun is **hidden**: it is not on `message --help` and is not an
MCP tool (one mints an identity and the other replaces its own process),
so this page and `message seat --help` are where you find it.

```sh
message seat init --registry DIR ADDRESS        # mint the key + birth cert into DIR
message seat agent ADDRESS [SOCKET]             # the seat's ssh-agent, for launchd
message seat agent --install ADDRESS [SOCKET]   # load it as a LaunchAgent (macOS)
message seat agent --uninstall ADDRESS          # unload it and remove its plist
message seat agent --template                   # print the LaunchAgent template
```

- `init` has no default `--registry`, so it never touches a live registry
  by accident, and it never blesses the seat: it prints the bless command.
- `agent` becomes the packaged `seat-agent` script (an exec), so launchd's
  TERM and KeepAlive act on it directly. Run this way, it never runs
  `launchctl`.
- `agent --install` renders the template, loads it with `launchctl
  bootstrap`, and waits until the agent answers. The seat's signer then
  comes back after a reboot. `--uninstall` takes it away. The contract is
  below.
- `--template` prints the plist byte for byte, for a render by hand.
  Its ProgramArguments are `@MESSAGE@ seat agent @ADDRESS@ @SOCKET@`.

Details, refusals and the adapter's environment:
[`scripts/seat-agent/README.md`][seat-agent].

## The trust registry

`message verify --trust` and `memory run` read a trust ledger: the
directory `SESSION_REGISTRY` names, or the `"registry"` key in
`~/.sessionkit/mail/settings/mail.json` (the variable wins). A ledger is a git work
tree holding a committed `allowed_signers` file and a `certs/` directory,
with one `certs/<sid>.cert` (and its `.cert.sig`) per signer. sessionmail
does not create a ledger, and `memory run` reads only `SESSION_REGISTRY`
or the `"registry"` key, by design: with neither set there is no default
ledger to fall back to, and every mail item is refused. `init` refuses a
ledger that is not seeded: `allowed_signers` must already hold at least one
`<sid>@session namespaces="..." <public key>` line, whose `namespaces=`
`init` copies onto each seat it adds. To build one by hand, `git init` a
directory, write the seed line into `allowed_signers`, commit it, and point
`SESSION_REGISTRY` at the directory; then `init --registry DIR` can add
seats to it. A seed line looks like this (every value here is made up):

```text
00000000-0000-0000-0000-000000000001@session namespaces="session-mail@example.com" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE
```

The ledger also holds the keys that bless. `--trust` checks a signer's
blessing against the blessing key in the ledger's own `allowed_signers`,
so whoever can write to the ledger, or push to the repository it is a
clone of, can add a blessing key of their own: a new root of trust. They
need not go that far for `memory run`, which by policy runs an UNBLESSED
item (its signature and its cert verify, with no blessing) as well as a
FULL one, and names it in the header. So a cert written into `certs/`, for
a key of the writer's choosing, is enough to make mail signed by that key
run. Write access to the ledger decides whose mail runs: keep it as tight
as the keys themselves.

## Installing the agent: `--install` and `--uninstall`

macOS only: on any other system both refuse (exit 2). The seat must be
minted first (`message seat init`). Neither ever touches the key, the
registry or the cert.

### `message seat agent --install ADDRESS [SOCKET]`

1. **Checks, with nothing written.** It is macOS. ADDRESS is a seat name or a plus address (`heard+airpods`).
   `$SESSIONMAIL_HOME/seat-keys/ADDRESS/` holds `id_ed25519` and `seat.pub`.
   SOCKET is absolute, 103 bytes at most, and its directory exists; it
   defaults to `agent.sock` beside the key. `message` is on PATH.
2. **Render.** The packaged template, with each placeholder filled and
   XML-escaped: `@MESSAGE@` is what `command -v message` prints,
   `@SESSIONMAIL_HOME@` is absolute, `@LOG_DIR@` is `$HOME/.sessionkit/mail/log`
   (see "The log", below).
   The render must parse as a plist, and its `Label` must be
   `dev.sessionkit.mail.seat-agent.ADDRESS`.
3. **Compare.** `launchctl print gui/<uid>/<label>` says whether the job is
   loaded. Loaded with the same plist: a re-run, so it goes on to step 5.
   Loaded with any other plist, or a different plist at the path: refused.
   A job's plist is never overwritten; `--uninstall` it first.
4. **Load.** It makes the log folder if it is missing (mode 0700), writes the plist to `$HOME/Library/LaunchAgents/<label>.plist`
   (mode 0644, through a temp file), then runs `launchctl bootstrap gui/<uid>
   <plist>`.
5. **Verify the effect.** For up to 10 s it polls `ssh-add -L` on SOCKET, in a
   clean env, until the agent lists `seat.pub`'s key.

**stdout**, on success only: exactly these five lines, values shell-quoted,
so `eval "$(message seat agent --install bot.alice)"` sets them:

```text
SEAT_LABEL=dev.sessionkit.mail.seat-agent.<ADDRESS>
SEAT_PLIST=<abs $HOME>/Library/LaunchAgents/dev.sessionkit.mail.seat-agent.<ADDRESS>.plist
SEAT_SOCKET=<abs socket>
SEAT_PUB=<abs $SESSIONMAIL_HOME>/seat-keys/<ADDRESS>/seat.pub
SEAT_LOG=<abs $HOME>/.sessionkit/mail/log/seat-agent.<ADDRESS>.log
```

The sender's seam is `CLAUDE_SESSION_AGENT_SOCK=$SEAT_SOCKET` and
`CLAUDE_SESSION_PUB=$SEAT_PUB`.

**stderr**: one narration line,
`message seat agent: installed and loaded <label>; the agent answers on <socket>`
or `message seat agent: <label> already installed; the agent answers on <socket>`.
A refusal is `message seat agent: REFUSED - <why>. Nothing was written.`, and a
failure is `message seat agent: FAILED - <why>; <what was put back>`.

**Exit**:

| code | meaning |
| --- | --- |
| 0 | loaded, and the agent answers on SEAT_SOCKET holding seat.pub's key. A re-run that finds it so is 0 too, and loads nothing. |
| 1 | `launchctl` failed, the plist could not be written, or the agent never answered. A job this run loaded is booted out, and a plist this run wrote is removed. A job that was already loaded and does not answer is reported and left as found. The job's last log lines are quoted. |
| 2 | usage or refused, nothing written: not macOS; the seat is not minted; the socket is relative, over 103 bytes or has no directory; `message` is not on PATH; another plist or job holds the label; or `--install`, `--uninstall` and `--template` combined. |

### `message seat agent --uninstall ADDRESS`

If the job is loaded, it runs `launchctl bootout gui/<uid>/<label>` and waits
up to 5 s for the job to go. Then it removes the plist, but only one whose
`Label` is ours. The agent removes its own socket as it stops. It never
deletes or moves a log, in `~/.sessionkit/mail/log/` or in `~/Library/Logs`.

**stdout**: nothing. **stderr**: `message seat agent: booted out <label>; removed <plist>`
(either part alone when only that was there), or
`message seat agent: nothing installed for <ADDRESS>`.

**Exit**: 0 gone, or nothing was there (so a second run is 0) · 1 the
bootout failed, the job did not go, or the plist could not be removed; the
plist is kept · 2 usage or
refused: a plist at our path whose `Label` is not ours, or not macOS. Nothing
is changed.

### The log

The agent's stdout and stderr go to one file,
`~/.sessionkit/mail/log/seat-agent.<ADDRESS>.log`: the kit keeps its own logs,
beside its settings in `~/.sessionkit/mail/settings/`, and writes nothing to
`~/Library/Logs`. `--install` makes the folder mode 0700 when it is missing,
because a seat log can name addresses and mail subjects. A folder that is
already there keeps its mode.

Before 0.3.2 the agent logged to `~/Library/Logs/seat-agent.<ADDRESS>.log`.
**An agent installed before 0.3.2 keeps writing that old path until it is
uninstalled and installed again** (`--uninstall ADDRESS`, then `--install
ADDRESS`): `--install` never overwrites a job's plist, so an upgrade alone
does not move it. Until then, when `--install` quotes the job's last log
lines and finds no file at the new path, it reads the old one and says so.
That fallback is dropped in the next release, 0.3.3.

Nothing here deletes or moves a log. The old file stays in `~/Library/Logs`
after the move; removing it is the operator's choice.

### Detecting the capability

`message seat agent --help` exits 0 and prints a line that is exactly
`Capability: install`. An installer checks it this way:

```sh
message seat agent --help 2>/dev/null | grep -qx 'Capability: install'
```

An older sessionmail fails the check: its page has no such line, and one with
no `seat` noun exits 2.

### `launchctl`

It runs `$SESSIONMAIL_LAUNCHCTL` when that is set and not empty, else
`/bin/launchctl`. The test suite points the variable at a fake that simulates
launchd, so no test loads a job on the real launchd.

[seat-agent]: ../scripts/seat-agent/README.md
