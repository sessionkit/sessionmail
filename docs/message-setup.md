# Making a host ready: `message setup`

A fresh install has no mail root, no boxes, no notmuch profile and no
notmuch hook. `message setup` makes them. It is listed on `message --help`.
It is not an MCP tool: it changes this host, which is the host owner's act,
so it runs from a shell. The kit's installer calls it by its argv.

```sh
message setup --dry-run              # print the plan; change nothing
message setup --box NAME [--box ..]  # apply it, making box NAME too
message setup --write-maps           # on a postfix host with no maps: write them
```

- **It prints first.** The whole plan is printed before anything is
  applied: the sudo steps first, each with its exact command (or `none`),
  then every step with its state, `DONE`, `TODO`, `BLOCKED` or `MANUAL`.
  A sudo step runs only at a terminal. Without one, it is printed for you
  to run. This version has no sudo step.
- **It is safe to re-run.** Every check only reads. A second run reports
  every step it finished as `DONE` and writes nothing.
- **Exit:** 0 every step is DONE · 1 a step failed (what ran before it
  stays; re-run) · 2 usage, or refused before anything was applied · 4
  what setup can do is done, and steps are left for you.

## What it does

- **The agents root**, `~/.mail/agents` (the `root` key, or
  `$SESSIONMAIL_ROOT`), made 0700. If the readers' root and the
  [file drop][file-drop]'s root differ (`SESSION_MAIL_ROOT` and
  `SESSIONMAIL_ROOT` set to different places), it refuses (exit 2): the
  drop would deliver into a tree nothing reads.
- **The first boxes.** The file drop never makes a box, so mail to a box
  nobody made is refused. Setup makes:
  - the operator's box: `operator.mailbox` (in the session tool's
    settings, `session.json`) if it is set, else the installing
    account's name;
  - each `--box NAME`. A name must already be lowercase `[a-z0-9_.-]`
    (the map generator rewrites any other name, so mail to it would still
    be refused), must not start with a dot, and must not be a session id.
    A session's box is made by `pager listen` and the SessionStart hook,
    never by setup.
- **The maps**, by the carrier:
  - the file drop (the default): none. The drop reads them on each send.
  - postfix, on a host that has maps: rebuilt once if a box was made. If a
    rebuild fails, the boxes stay made and the step is left `BLOCKED`.
  - postfix, on a host with no maps: none are written. A `MANUAL` line
    names `--write-maps`, which writes them as a box birth would.

  **Setup never edits postfix**: not `main.cf`, not `master.cf`, not a
  table of the host's own. That setup is yours: [postfix
  configuration][postfix-configuration].
- **The notmuch profile** (`notmuch-profile`): the one notmuch config
  sessionmail reads, `$XDG_CONFIG_HOME/notmuch/sessionkit/config` (the
  `notmuch.profile` key names another). When there is none, setup writes it
  from the package's template, its database the mail root. It never writes
  over a file. If the mail root already holds a notmuch database
  (`<root>/.notmuch`), it writes nothing and prints a `MANUAL` line: setup
  adopts no database. More: [notmuch settings][notmuch-settings].
- **The notmuch hook.** It links `post-new` and `post-insert` to the
  packaged [post-new hook][post-new-hook], in the folder notmuch reports
  for the profile (`notmuch --config=<profile> config get
  database.hook_dir`). With no profile, the step is `BLOCKED` and nothing
  is linked anywhere. It never replaces a different file. A link to
  another sessionmail copy of the hook is left as is. A link to our own
  hook that now points nowhere (a rebuilt tool venv) is repaired.
- **What sends your mail** (`carrier`). It reports the carrier and where
  its value came from, such as `the mail drop (the default, from
  ~/.sessionkit/mail/settings/mail.template.json)` or `the MTA at
  /usr/sbin/sendmail (from ~/.sessionkit/mail/settings/mail.json)`. If the
  `sendmail` key names a program that is not an executable file, it prints
  a `MANUAL` line naming the key to write.
- **The MagicDNS suffix**, on a postfix host only: a `MANUAL` line, naming
  the `magicdns_suffix` key, when none is found.

## What it does not do

- **It writes no settings key.** Each `MANUAL` line names the key to write
  in `~/.sessionkit/mail/settings/mail.json`, and the variable that would
  win over it: [settings][settings].
- **It never reads or writes your own notmuch config.** `~/.notmuch-config`
  and any other profile are left exactly as they are, and no hook is linked
  into their folders.
- **It does not run `notmuch new`, and nothing else in sessionmail does.**
  notmuch reports no hook folder until the profile's first `notmuch new`,
  so on a fresh host the hook step is left for you: run the line it prints
  (`notmuch --config=<profile> new`), then `message setup` again. Until
  something runs it (launchd, cron, or by hand), `message mark` and
  `memory` read an empty index.
- It never makes a session box, and it never touches another host.

[file-drop]: file-drop.md
[notmuch-settings]: notmuch-settings.md
[post-new-hook]: notmuch-settings.md#the-post-new-hook
[postfix-configuration]: postfix-configuration.md
[settings]: settings.md
