# Dependencies

Everything sessionmail depends on: what needs each one, whether you need it,
how it gets onto your Mac, and how you upgrade it. The README's
[requirements][readme-requirements] link here.

- **Required:** macOS, Claude Code, uv, Python 3.11 to 3.14, git, OpenSSH's
  `ssh-keygen`, and jq. macOS 27 ships the last two, and Apple's Command
  Line Tools bring git. `uv tool install sessionmail` brings the Python
  packages.
- **Optional:** postfix, for mail across machines, and a few tools that
  single features use. macOS ships most of them.
- **Pending a ruling:** Homebrew and notmuch. Whether they are required is
  not decided yet; their entries say what uses each one today ([pending a
  ruling][pending]).
- **Upgrade:** `uv tool upgrade sessionmail` for the three commands and
  their Python packages, `claude plugin update sessionmail@sessionmail` for
  the plugin ([upgrade][upgrade]).

Required means a default command fails without it. Optional means a feature
you turn on needs it, or a command still works without it but does less.

## Required

| Dependency | What needs it | How you get it | How you upgrade it |
|---|---|---|---|
| macOS | Everything. Tested on macOS 27 on Apple silicon. | Your Mac. | Software Update. |
| [Claude Code][claude-code] | The plugin: its skills, its hooks, its three MCP servers and the pager monitor. The commands also run without it, from a shell or another client ([not running under Claude Code][pager-outside-claude]). | Its installer. | Claude Code's own update. |
| [uv][uv] | `uv tool install sessionmail`, which puts `message`, `pager` and `memory` on your PATH. | The uv installer, or `brew install uv`. | `uv self update`, or `brew upgrade uv` for a Homebrew copy. |
| Python 3.11 to 3.14 | The three commands run on it. | `uv tool install` uses one it finds, or fetches one. | It stays with the install: `uv tool upgrade sessionmail` keeps it. |
| git | Installing the plugin: `claude plugin marketplace add` clones the marketplace with it. Also `message seat init`, which commits a seat's key to its registry, and `memory init --record`, which commits one line to the file you name. | Apple's Command Line Tools: `xcode-select --install`. | Software Update. |
| `ssh-keygen` (OpenSSH) | Signed mail: `message send` signs with it when the session has a key, and `message verify` checks a signature with it. | macOS ships it. | Software Update. |
| jq | `message send` to a seat's name. Without it, the mail still lands in the box it is addressed to, but the seat's session box gets no copy, so that session's pager is not woken. | macOS 27 ships `/usr/bin/jq`. If `command -v jq` prints nothing, `brew install jq`. | Software Update, or `brew upgrade jq`. |
| The Python packages | Every command on your PATH. | `uv tool install sessionmail` brings them from PyPI. | `uv tool upgrade sessionmail`. |

The Python packages, and what each one is for:

- click 8.1 or later: the command line of `message`, `pager` and `memory`.
- cryptography 42 or later: `memory` checks a mail's signature with it, and
  checks through `ssh-keygen` instead when it does not load.
- markdown 3.6 or later and pymdown-extensions 10.0 or later: `message
  send` turns the body into an HTML page with them, unless you send it
  `--raw`.
- mcp 2.2.0 or later: the three MCP servers, `message mcp`, `pager mcp` and
  `memory mcp`, which the plugin starts.
- [sessiontools][pkg-tools] 0.3.0 or later: which sessions are live, and
  their settings.

## Optional

| Dependency | What needs it | Without it | How you get it | How you upgrade it |
|---|---|---|---|---|
| postfix (`sendmail`, `postconf`, `postmap`) | Mail across machines, when the `sendmail` setting names postfix's `sendmail` ([postfix configuration][postfix-configuration]). The puller, `message setup --pull`, hands mail to it ([pull][pull]). | Same-host mail goes through [the file drop][file-drop], the default. Mail to another host is refused by name. | macOS ships it. Its setup is yours, by hand. | Software Update. |
| [Tailscale][tailscale] | The MagicDNS suffix that the postfix maps are written with; the puller's own addresses. | `message setup` asks you to set `magicdns_suffix` instead. The file drop still delivers to this host's own boxes. | The Tailscale app. sessionmail also finds its `tailscale` command on the PATH. | The app's own update. |
| launchd (`launchctl`, `plutil`) | `message seat agent --install`, which keeps a bot seat's ssh-agent running across reboots, and `message setup --pull`, which installs the puller. | Both refuse on a system other than macOS. | macOS ships it. | Software Update. |
| `sudo` | `message setup`'s privileged steps, run at a terminal. | `message setup` prints each privileged line for you to run yourself. | macOS ships it. | Software Update. |
| `python3` on the PATH | The plugin's mail guard, a hook on each Bash call that stops a mail send the shell has damaged before it runs. | The hook exits 0 and the guard is off. | Apple's Command Line Tools bring `/usr/bin/python3`. | Software Update. |
| `/usr/bin/perl` | The [post-new command][post-new-command], once you set one, and postfix delivery straight into the index, once you set that up. | The post-new command is not run, and says so in `~/.sessionmail/post-new/no-perl`; postfix holds the mail and tries again later. | macOS ships it. | Software Update. |

One more tool is used if it is there: `memory run` counts tokens with a
`tokens` command when one is on the PATH, and estimates (bytes / 4) when
none is.

## Pending a ruling

Whether sessionmail requires these two is not decided yet, so this page
gives them no class and no install step. Each entry says what uses it today,
what happens without it, and where it comes from today. tmux and ffmpeg,
held with them for the other packages, are not used by sessionmail.

### [Homebrew][brew]

Status: pending a ruling. It hangs on the ruling for notmuch below.

- **What uses it:** no sessionmail command runs `brew`. It is where notmuch
  comes from today. Where a PATH has no Homebrew directory in it, as under
  launchd, sessionmail also looks in Homebrew's directories for `notmuch`
  and `postconf`.
- **Without it:** notmuch has to come from somewhere else. sessionmail finds
  it on the PATH, or where the `notmuch.command` setting names it
  ([notmuch settings][notmuch-settings]).
- **Where it comes from today:** its installer, from its own site.

### [notmuch][notmuch-org]

Status: pending a ruling. Whether sessionmail keeps notmuch as its search
engine, and a replacement built on SQLite's full-text search, are being
researched. More about how sessionmail uses it: [notmuch][notmuch-page].

- **What uses it:** `memory ls`, `memory tree`, `memory cat` and `memory
  run`, and the recent threads in `memory wake`. `message mark` looks a
  message up in it. `message send` uses it to find a reply's parent
  faster. `message setup` links its hook.
- **Without it:** `memory ls`, `memory tree` and `memory cat` stop with one
  line, "notmuch is not installed", and exit 2, having read nothing.
  `memory wake` says it could not run `notmuch` and goes on without the
  recent threads. `message mark` says it could not check the index and
  looks in the seat's own boxes instead. `message send` finds a reply's
  parent by walking the maildir. `message setup` marks its notmuch-hook
  step `BLOCKED`. Hearing mail and sending it do not need it.
- **Where it comes from today:** Homebrew.

## Upgrade

```bash
uv tool upgrade sessionmail
claude plugin marketplace update sessionmail
claude plugin update sessionmail@sessionmail
```

- **The first line** upgrades `message`, `pager` and `memory` and their
  Python packages, and keeps the Python the install chose.
- **The plugin lines** fetch the marketplace again, then the plugin.
  Restart Claude Code after.
- **Inside Session Kit**, the kit upgrades it: `uv tool upgrade sessionkit`,
  then `claude plugin update sessionmail@sessionkit`.

[readme-requirements]: ../README.md#requirements
[pending]: #pending-a-ruling
[upgrade]: #upgrade
[claude-code]: https://claude.ai/code
[uv]: https://docs.astral.sh/uv/
[tailscale]: https://tailscale.com
[brew]: https://brew.sh
[notmuch-org]: https://notmuchmail.org
[pkg-tools]: https://github.com/sessionkit/sessiontools
[pager-outside-claude]: pager-outside-claude.md
[postfix-configuration]: postfix-configuration.md
[pull]: pull.md
[file-drop]: file-drop.md
[post-new-command]: post-new-command.md
[notmuch-settings]: notmuch-settings.md
[notmuch-page]: notmuch.md
