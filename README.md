# sessionmail

Mail for coding agents: `message` moves messages, `pager` alerts, `memory`
is the filesystem view - three commands over one maildir.

## Quick start

```sh
claude plugin marketplace add sessionkit/sessionmail
claude plugin install sessionmail
uv tool install sessionmail          # the commands the plugin calls
pager listen                         # blocks until mail arrives, then says so
```

Both of the first two lines need the repo and the release to be public:
see **Status**, under Installation, before you copy them.

The last line is the point: an agent that runs `pager listen` in the
background can be reached while it is idle, instead of finding out on its
next manual check. Under an interactive Claude Code seat the plugin does
it for you: its **pager monitor** listens from launch, on by default (see
[the pager monitor][pager-monitor]). The hand `pager listen` is for headless
(`claude -p`) seats, seats where the monitor is switched off, and clients
outside Claude Code.

```sh
message send NAME -s SUBJECT -f BODY.md   # and list, tree, read, mark
pager listen                              # block until mail; page; exit
pager status                              # what would page you now
memory ls / | memory cat PATH             # the box as a filesystem
```

Each command's own `--help` is the reference; `<command> <verb> --help` is
the page for a verb.

## Installation

**Two installs, and you need both.** The plugin brings the skills, the two
hooks and the MCP servers; the package brings `message`, `pager` and
`memory`, which is what all of those call.

```sh
claude plugin install sessionmail    # skills, hooks, MCP entries
uv tool install sessionmail          # the three commands on PATH
```

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
- Reading needs no server either: `message list`, `tree`, `read` and
  `mark`, `pager` and `memory` all read the maildir directly.
- **Mail across machines** goes through postfix, which is opt-in:
  [postfix configuration][postfix-configuration].

**Then make the host ready** with `message setup`: [message
setup][message-setup].

```sh
message setup --dry-run          # print the plan; change nothing
message setup --box NAME         # the mail root, the boxes, the notmuch profile
notmuch --config="$HOME/.config/notmuch/sessionkit/config" new   # once, as setup says
message setup                    # again: it links the notmuch hook
```

- It makes the mail root, the operator's box and each `--box NAME` (the
  file drop never makes a box), and sessionmail's own notmuch profile. It
  links notmuch's [post-new hook][post-new-hook] into that profile's hook
  folder. It prints its whole plan before it applies any of it, and it is
  safe to re-run.
- It never touches your own notmuch config, never edits postfix, and
  writes no settings key.
- **Nothing runs `notmuch new` for you.** `message mark` and `memory`
  read an empty index until something does: launchd, cron, or by hand.

`message`, `pager` and `memory` all run; the specs, in the git repository
(the release archive leaves them out), say what each verb is meant to do
and `make progress` counts how much of it is built.

## Uninstall

```sh
claude plugin uninstall sessionmail                  # skills, hooks, MCP entries
uv tool uninstall sessionmail                        # the three commands
rm ~/.mail/.notmuch/hooks/post-new ~/.mail/.notmuch/hooks/post-insert   # the linked hook (in notmuch's database.hook_dir)
notmuch --config="$HOME/.config/notmuch/sessionkit/config" config set sessionmail.post_new_command   # the optional command, off
rm ~/.config/notmuch/sessionkit/config               # sessionmail's notmuch profile (its index stays in ~/.mail/.notmuch)
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
  `~/.config/notmuch/sessionkit/config`. Your own `~/.notmuch-config` is
  never read.
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
- `~/.mail/.state/regen-failures.log`: each time the map generator
  refused to rewrite the alias map, and why.
- `~/.sessionmail/post-new/log`: the [post-new command][post-new-command]'s
  runs and output, only once you set one. Past 1 MiB it becomes `log.1`.

Follow one as it grows with `tail -F`, for example
`tail -F ~/.mail/.state/regen-failures.log`. `pager status` ends its
`today:` line with the path of this session's `pager.jsonl`.

- **Health:** `pager status`, run by the agent. `monitor: armed (pid N)`,
  or a hand `pager listen` named on that line, means something is
  listening for its mail; `not armed` or `off` alone means nothing is.
  Exit 2 means it could not look (no session, or no box yet). It does not
  test sending: `message send` exits 3 when the mail server did not take
  the message.

## Docs

Everything else is in `docs/`, one subject per file:

- [The pager][pager], the model in one page; [how `pager listen`
  works][pager-listen-internals]; [the pager monitor][pager-monitor]; [not
  running under Claude Code][pager-outside-claude]; [what the pager
  injects][pager-inject-log].
- Three hidden verbs: [`message seat`][message-seat], a seat with no Claude
  session; [`message receipt`][message-receipt], what another session
  recorded; and [`message setup`][message-setup], making a host ready.
- [Gating on `message verify --json`][verify-json-verdict]: the verdict as
  one JSON object.
- Delivery: [where mail comes from][mail-delivery-paths], [postfix
  configuration][postfix-configuration], [the file drop][file-drop],
  [pulling a mail host's boxes][pull].
- [Configuration][configuration]: the files you set up, by component;
  [settings][settings], the keys of `mail.json`.
- Index: [notmuch][], [notmuch settings][notmuch-settings], [the post-new
  command][post-new-command].
- [The plugin's three skills][plugin-skills]; [development][development-setup],
  including `make test-latency`, the wall-clock tests `make test` leaves out.

[pager]: docs/pager.md
[pager-listen-internals]: docs/pager-listen-internals.md
[pager-monitor]: docs/pager-monitor.md
[pager-outside-claude]: docs/pager-outside-claude.md
[pager-inject-log]: docs/pager-inject-log.md
[message-seat]: docs/message-seat.md
[message-receipt]: docs/message-receipt.md
[message-setup]: docs/message-setup.md
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
