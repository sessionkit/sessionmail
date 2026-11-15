# sessionmail

Mail for coding agents: `message` moves messages, `pager` alerts, `memory`
is the filesystem view - three commands over one maildir.

The idea behind it: [The Mailbox Is the Memory][paper], a paper written on
the mail it describes.

[paper]: https://cora7.com/blog/the-mailbox-is-the-memory/

## Quick start

```sh
claude plugin marketplace add sessionkit/sessionmail
claude plugin install sessionmail
uv tool install sessionmail
pager listen
```

Both of the first two lines need the repo and the release to be public:
see **Status**, under Installation, before you copy them. The third
installs the commands the plugin calls.

The last line is the point: `pager listen` blocks until mail arrives, then
says so. An agent that runs it in the background can be reached while it
is idle, instead of finding out on its next manual check. Under an
interactive Claude Code seat the plugin does it for you: its
**pager monitor** listens from launch, on by default (see
[the pager monitor][pager-monitor]). The hand `pager listen` is for long-lived
headless (`claude -p`) seats, seats where the monitor is switched off, and
clients outside Claude Code. A one-shot `claude -p` arms nothing, and the
hooks never ask it to.

```sh
message send NAME -s SUBJECT -f BODY.md
pager listen
pager status
memory ls /
memory cat PATH
```

- `message send` sends a message; `message` also has `list`, `tree`,
  `read` and `mark`.
- `pager listen` blocks until mail arrives, pages, and exits.
- `pager status` says what would page you now.
- `memory ls` and `memory cat` read the box as a filesystem.

To check that mail moves, send a box a message and list that same box:

```sh
message setup --box claude
message send claude -s hi --no-body
message list claude
```

The first line makes the host ready, and a box named `claude` to test
with. The second sends that box a message, and the third lists the box
you sent to: `hi` is there.

Name the box in `message list` too. With no box, `message list` reads
this session's box once it exists, else the seat's box (stderr says so if
the session has none yet). Under Claude Code the plugin makes the session
box when the session starts, so a bare `message list` there does not show
`hi`.

Each command's own `--help` is the reference; `<command> <verb> --help` is
the page for a verb.

## Requirements

- [x] macOS (tested on macOS 27, Apple silicon)
- [ ] Linux
- [ ] WSL
- [ ] Windows

What sessionmail depends on, what needs each one, and how to upgrade it:
[dependencies][dependencies].

## Installation

**Two installs, and you need both.** The plugin brings the skills, the
hooks, the pager monitor and the MCP servers; the package brings `message`,
`pager` and `memory`, which is what all of those call.

```sh
claude plugin install sessionmail
uv tool install sessionmail
```

The first line brings the skills, the hooks and the MCP entries; the
second puts the three commands on your PATH.

On Claude Code 2.1.275 or later the plugin installs from its marketplace in
one step, from inside a session:

```text
/plugin install sessionmail --marketplace sessionkit/sessionmail
```

With only the plugin, the hooks exit quietly and the MCP servers do not
start: you get skills describing commands the machine does not have. The
hook says so once per session rather than leaving you to guess.

**Same-host mail needs no mail server.**

- `message send` writes each copy straight into this host's maildirs: [the
  file drop][file-drop], the default. Mail to another host is refused by
  name.
- Reading needs no server either: `message list`, `tree` and `read`, and
  `pager`, read the maildir directly. `message mark` and `memory` read the
  [notmuch][] index, and refresh it themselves before they read (below).
- **Mail across machines** goes through postfix, which is opt-in:
  [postfix configuration][postfix-configuration].

**Then make the host ready** with `message setup`: [message
setup][message-setup].

```sh
message setup --dry-run
message setup --box NAME
```

The first line prints the plan and changes nothing. The second applies
it: the mail root, the boxes, the notmuch profile and its hook.

- It makes the mail root, the operator's box and each `--box NAME` (the
  file drop never makes a box), and sessionmail's own notmuch profile. It
  links notmuch's [post-new hook][post-new-hook] into that profile's hook
  folder. It prints its whole plan before it applies any of it, and it is
  safe to re-run.
- It never touches your own notmuch config and never edits postfix. It
  writes one settings key, `host`, and only into a settings file that has
  none.
- **Setup runs `notmuch new --no-hooks` once**, on its own empty profile,
  to build the index the hook needs. It schedules nothing and installs no
  job.
- **The readers keep the index fresh.** `memory` runs a bounded
  `notmuch new --no-hooks` before it reads, and `message mark` runs one
  when an id is missing. The bound is `notmuch.refresh`: 2 seconds by
  default, and 0 turns it off: [notmuch][].

`message`, `pager` and `memory` all run; the specs, in the git repository
(the release archive leaves them out), say what each verb is meant to do
and `make progress` counts how much of it is built.

### What an install changes

`uv tool install sessionmail` changes two places and fills uv's own
cache. `claude plugin install` changes Claude Code's own files. Neither
touches a shell startup file. Everything else below appears when you run
`message setup`, when a Claude Code session starts with the plugin, or only
when you run the verb named beside it. On a fresh host, every file
sessionmail keeps is under `~/.sessionkit/mail/`, apart from the mail state
in each session's folder. Each line says what puts it there, and
[Uninstall][uninstall] has the lines that take each one away.

- **PATH:** `message`, `pager` and `memory` in `~/.local/bin/`, or in
  `UV_TOOL_BIN_DIR` when that is set: `uv tool install sessionmail`. uv
  says so if the folder is not on your PATH, and edits no shell file.
- **The program:** `~/.local/share/uv/tools/sessionmail/`, or in
  `UV_TOOL_DIR`: its own Python environment, with the sessiontools library
  in it but not its `session` command. The notmuch hook links below point
  into it.
- **uv's cache:** what the install downloaded and built. It is uv's,
  shared by everything uv installs. `uv cache dir` prints where it is.
- **The plugin:** `claude plugin marketplace add` and `claude plugin
  install`. Claude Code names the marketplace and the plugin in
  `~/.claude/settings.json` (`extraKnownMarketplaces`, `enabledPlugins`),
  lists them in `~/.claude/plugins/`, and keeps a copy in
  `~/.claude/plugins/cache/sessionmail/`. The plugin brings:
  - three skills: `message`, `pager` and `memory`;
  - three MCP servers of the same names, each running `<command> mcp`;
  - six hook entries: SessionStart, Stop, PostToolBatch and
    UserPromptSubmit run `pager hook`, and two PreToolUse entries, one for
    Bash and one for the mail tools, run the mail guard;
  - one monitor, `pager`, which runs `pager listen --monitor` in every
    interactive session: [the pager monitor][pager-monitor].
- **Services (launchd agents):** none until you install one. Each writes a
  plist in `~/Library/LaunchAgents/` and loads it, and the job starts
  again at every login.
  - `dev.sessionkit.mail.seat-agent.<ADDRESS>`: `message seat agent
    --install ADDRESS`, the ssh-agent of a seat with no Claude session
    ([`message seat`][message-seat]).
  - `dev.sessionkit.mail.puller`, or the label in the `pull.label` key:
    `message setup --pull` ([the pull][pull]).
- **The notmuch hook:** two links, `post-new` and `post-insert`, to the
  packaged hook, in the hook folder notmuch reports for sessionmail's
  profile (`database.hook_dir`, inside the index): `message setup`.
- **The notmuch profile:**
  `~/.sessionkit/mail/settings/notmuch/sessionkit/config`: `message setup`,
  only when there is none. A host that already has
  `~/.config/notmuch/sessionkit/config` or `~/.notmuch-config.sessionkit`
  keeps that file, and setup writes no other. `message path
  --notmuch-config` names the one in use. Your own `~/.notmuch-config` is
  never read or written.
- **The mail root:** `~/.sessionkit/mail/` (`message path --root`). A host
  whose `root` key or `SESSIONMAIL_ROOT` names another folder keeps its
  mail there.
  - `stores/`, made 0700, with the operator's box and each `--box NAME`
    in it: `message setup`.
  - `stores/<session-id>/`, one box for each Claude Code session: the
    SessionStart hook, or `pager listen`. Neither makes the mail root
    itself.
  - `.notmuch/`, sessionmail's index: `message setup` builds it once, with
    `notmuch new --no-hooks`. `memory` and `message mark` keep it fresh.
  - `archive/`: `message box archive --apply`, which moves dead session
    boxes there.
- **Settings:** in `~/.sessionkit/mail/settings/`.
  - `mail.schema.json` and `mail.template.json`: the SessionStart hook, or
    `pager settings install`.
  - `mail.json`: the first PostToolBatch hook writes it from the
    template, with the hooks off, and says so once. `pager settings hooks
    on` turns them on. `message setup` adds `host` to a file that has none.
- **State:** in `~/.sessionkit/mail/state/`, each folder made by a verb:
  `seat-keys/` (`message seat init`), `seats/` (`message mark` from a seat
  with no session), `puller/` and `imap/` (the pull), and `post-new/` (only
  once you set [the post-new command][post-new-command]).
- **Logs:** in `~/.sessionkit/mail/log/`: `settings.jsonl` (`pager settings
  hooks`), `seat-agent.<ADDRESS>.log` and `puller.log`. [Logs][logs] has
  each one.
- **In each session's folder:** sessiontools makes the folder,
  `~/.sessionkit/tools/state/sessions/<session-id>/` on a fresh host, or
  `~/.session/sessions/<session-id>/` on a host that still has that.
  sessionmail adds `mail/`, the session's mail state, and `log/mail/`, the
  pager's log.
- **Only on a postfix host** ([postfix configuration][postfix-configuration]):
  the maps the generator writes, as you, in `/usr/local/etc/postfix-agents/`
  (a folder you make once with `sudo`); the delivery wrapper `nm-deliver`
  beside them, on a host whose postfix runs an `nminsert` service (`message
  setup`, with `sudo`, keeping its copy in
  `~/.sessionkit/mail/settings/postfix/`); and the generator's
  `<mail root>/.state/` folder. sessionmail never edits `main.cf` or
  `master.cf`.
- **Nothing else:** no shell startup file, no crontab, no `~/.mail` on a
  fresh host, and nothing in your own notmuch or postfix configuration.

## Uninstall

Each item in [what an install changes][install-changes] has its lines
here. Skip the lines for what you never installed. Quit every `claude`
session first, and paste the lines in one terminal, in this order: the
first lines need the `message` command, which the tool's line removes.
The lines carry no comments, because in zsh a `#` typed at the prompt is
not a comment.

**Before you start**, note where the mail root and the notmuch profile
are. The later lines read these two names:

```sh
R="$(message path --root)"
P="$(message path --notmuch-config)"
```

**The services**, if you installed one. Put the seat's address for
`ADDRESS`, and your own label if you set `pull.label`:

```sh
message seat agent --uninstall ADDRESS
launchctl bootout "gui/$(id -u)/dev.sessionkit.mail.puller"
rm ~/Library/LaunchAgents/dev.sessionkit.mail.puller.plist
```

**The notmuch hook and profile.** The first line turns off [the post-new
command][post-new-command], which notmuch also keeps in its index. The
second finds the hook folder. The third removes each hook only when it is
a link to sessionmail's hook, so a hook of your own there stays. The last
removes the profile. If `P` is empty there is no profile: skip these
lines. Each one stops, doing nothing, when `P` is empty, because notmuch
given no profile answers for your own database. The notmuch lines run
without `NOTMUCH_CONFIG`, `NOTMUCH_DATABASE` and `NOTMUCH_PROFILE`, as
setup does, because each of them would point notmuch at another database:

```sh
env -u NOTMUCH_CONFIG -u NOTMUCH_DATABASE -u NOTMUCH_PROFILE notmuch --config="${P:?}" config set sessionmail.post_new_command
H="$(env -u NOTMUCH_CONFIG -u NOTMUCH_DATABASE -u NOTMUCH_PROFILE notmuch --config="${P:?}" config get database.hook_dir)"
for f in "${H:?}/post-new" "${H:?}/post-insert"; do case "$(readlink "$f")" in */sessionmail/notmuch/post-new|*/hooks/notmuch-post-new) rm "$f" ;; esac; done
rm "${P:?}"
```

**The plugin.** The first line removes the plugin, and the second removes
its marketplace. The third deletes the copy Claude Code keeps, which the
first two leave:

```sh
claude plugin uninstall sessionmail@sessionmail
claude plugin marketplace remove sessionmail
rm -rf "${HOME:?}/.claude/plugins/cache/sessionmail"
```

**The commands and the program:**

```sh
uv tool uninstall sessionmail
```

uv's cache stays, with the other things uv installed. `uv cache clean
sessionmail` drops this package's entries.

**Your mail and sessionmail's files, only if you want them gone.** This
cannot be undone. The first line deletes the mail root: every box, the
index and the archive. The second deletes the settings, the state and the
logs. On a fresh host the mail root is the second folder, so the second
line finds nothing left:

```sh
rm -rf "${R:?}"
rm -rf "${HOME:?}/.sessionkit/mail"
```

The mail state in the session folders. In zsh, a line prints `no matches
found` when there is nothing to delete; that is fine:

```sh
rm -rf "${HOME:?}"/.sessionkit/tools/state/sessions/*/mail
rm -rf "${HOME:?}"/.sessionkit/tools/state/sessions/*/log/mail
rm -rf "${HOME:?}"/.session/sessions/*/mail
rm -rf "${HOME:?}"/.session/sessions/*/log/mail
```

**On a postfix host**, take the maps and the `nminsert` service out of
postfix's configuration first ([postfix
configuration][postfix-configuration]). Then delete the folder they were
in:

```sh
sudo rm -rf /usr/local/etc/postfix-agents
```

## Configuration

Which files, which environment variables, and where each one lives, one
component at a time: [configuration][configuration].

- **sessionmail:** `~/.sessionkit/mail/settings/mail.json` for the host, and one for
  a seat or a session if you want them. Its keys: [settings][settings].
- **postfix**, only to send: `/etc/postfix/main.cf`, root's and set once,
  and the two maps the generator writes as you, in
  `/usr/local/etc/postfix-agents/`.
- **notmuch**, the index: sessionmail's own profile,
  `~/.sessionkit/mail/settings/notmuch/sessionkit/config` on a fresh host.
  An older `~/.config/notmuch/sessionkit/config` (or
  `~/.notmuch-config.sessionkit`) is still read while it exists, and is
  never moved. Your own `~/.notmuch-config` is never read.
- **Environment variables**: each settings key has one (the `sendmail`
  key's is `SESSIONMAIL_SENDMAIL`). When it is set and not empty, it wins
  over the file.

## Logs

sessionmail keeps four logs. Each is written only when it has something
to record:

- `~/.session/sessions/<session-id>/log/mail/pager.jsonl`: one row for
  each thing the pager put into an agent's context, sizes and Message-IDs
  only ([the pager log][pager-inject-log]). Past 16 MB it becomes
  `pager.1.jsonl`.
- `~/.session/sessions/<session-id>/log/settings.jsonl`, and the same file
  under `~/.session/seats/<seat>/`: each settings change for that session
  or seat, such as a subscription or its address, with the command that
  made it.
- `<mail root>/.state/regen-failures.log` (`message path --root`): each time the map generator
  refused to rewrite the alias map, and why.
- `~/.sessionkit/mail/state/post-new/log` (`~/.sessionmail/post-new/log` on
  an existing host where that folder is still real): the [post-new command][post-new-command]'s
  runs and output, only once you set one. Past 1 MiB it becomes `log.1`.

Follow one as it grows with `tail -F`, for example
`tail -F "$(message path --root)/.state/regen-failures.log"`. `pager status` ends its
`today:` line with the path of this session's `pager.jsonl`.

- **Health:** `pager status`, run by the agent. `monitor: armed (pid N)`,
  or a hand `pager listen` named on that line, means something is
  listening for its mail; `not armed` or `off` alone means nothing is.
  Exit 2 means it could not look (no session, or no box yet). It does not
  test sending: `message send` exits 3 when the mail server did not take
  the message.

## Docs

Everything else is in `docs/`, one subject per file:

- A page for each command: [`message`][message-doc], [`memory`][memory-doc]
  and [the pager][pager].
- [The pager][pager], the model in one page; [how `pager listen`
  works][pager-listen-internals]; [the pager monitor][pager-monitor] and
  its [frozen-turn watch][frozen-watch], off by default; [not running
  under Claude Code][pager-outside-claude]; [what the pager
  injects][pager-inject-log]; [outside mail, paged by
  pointer][outside-pointer].
- Four hidden verbs: [`message seat`][message-seat], a seat with no Claude
  session; [`message receipt`][message-receipt], what another session
  recorded; [`message address`][message-address], the address this shell
  sends from; and [`message setup`][message-setup], making a host ready.
- [Your first memory root, by hand][memory-first-root]: `memory` has no
  automated first step yet.
- [Gating on `message verify --json`][verify-json-verdict]: the verdict as
  one JSON object.
- [The writing model][session-model]: the signed `Session-Model:` header,
  and where its id comes from for Claude Code and for another harness.
- Delivery: [where mail comes from][mail-delivery-paths], [postfix
  configuration][postfix-configuration], [the file drop][file-drop],
  [pulling a mail host's boxes][pull].
- [Configuration][configuration]: the files you set up, by component;
  [settings][settings], the keys of `mail.json`.
- [Dependencies][dependencies]: what sessionmail needs, and how to upgrade
  each one.
- Index: [notmuch][], [notmuch settings][notmuch-settings], [the post-new
  command][post-new-command].
- [The plugin's three skills][plugin-skills]; [development][development-setup],
  including `make test-latency`, the wall-clock tests `make test` leaves out.

[dependencies]: docs/dependencies.md
[pager]: docs/pager.md
[message-doc]: docs/message.md
[memory-doc]: docs/memory.md
[pager-listen-internals]: docs/pager-listen-internals.md
[pager-monitor]: docs/pager-monitor.md
[frozen-watch]: docs/frozen-watch.md
[pager-outside-claude]: docs/pager-outside-claude.md
[pager-inject-log]: docs/pager-inject-log.md
[outside-pointer]: docs/outside-pointer.md
[message-seat]: docs/message-seat.md
[session-model]: docs/session-model.md
[message-receipt]: docs/message-receipt.md
[message-address]: docs/message-address.md
[message-setup]: docs/message-setup.md
[memory-first-root]: docs/memory-first-root.md
[verify-json-verdict]: docs/verify-json-verdict.md
[mail-delivery-paths]: docs/mail-delivery-paths.md
[postfix-configuration]: docs/postfix-configuration.md
[configuration]: docs/configuration.md
[settings]: docs/settings.md
[file-drop]: docs/file-drop.md
[pull]: docs/pull.md
[notmuch]: docs/notmuch.md
[notmuch-settings]: docs/notmuch-settings.md
[post-new-hook]: docs/notmuch-settings.md#the-post-new-hook
[post-new-command]: docs/post-new-command.md
[plugin-skills]: docs/plugin-skills.md
[development-setup]: docs/development-setup.md
[uninstall]: #uninstall
[install-changes]: #what-an-install-changes
[logs]: #logs
