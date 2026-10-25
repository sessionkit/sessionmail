# Making a host ready: `message setup`

A fresh install has no mail root, no boxes, no notmuch profile and no
notmuch hook. `message setup` makes them. It is listed on `message --help`.
It is not an MCP tool: it changes this host, which is the host owner's act,
so it runs from a shell. The kit's installer calls it by its argv.

```sh
message setup --dry-run              # print the plan; change nothing
message setup --box NAME [--box ..]  # apply it, making box NAME too
message setup --write-maps           # on a postfix host with no maps: write them
message setup --pull                 # also install the mail puller's LaunchAgent
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
- **It ends with one list of what is left.** When steps are left, the
  last lines are one numbered block, every step that is not `DONE`, in
  order, each with its state and why. A step that waits on another names
  it. The block ends with the line to run next. A box it did not make is
  in the block, with why.

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
- **The notmuch index** (`notmuch-index`): sessionmail's own index, built
  once. When the profile has no database yet, setup runs
  `notmuch --config=<profile> new --no-hooks` once, in the same run that
  writes the profile, and the dry run prints that line. It runs it only on
  an EMPTY index: notmuch reports no hook folder for the profile, and no
  `.notmuch` is at the `database.path` it reports. If a database is already
  there, it runs nothing and the step is `BLOCKED`, saying what it found. If
  notmuch is not found, the step is `BLOCKED` and names the line it will
  run once notmuch is found. A `new` that fails stops the run (exit 1).
- **The notmuch hook.** It links `post-new` and `post-insert` to the
  packaged [post-new hook][post-new-hook], in the folder notmuch reports
  for the profile (`notmuch --config=<profile> config get
  database.hook_dir`), after the index step, in the same run. Until the
  index exists it is one `notmuch-hook` step that waits on
  `notmuch-index`. With no profile nothing is linked anywhere. It never
  replaces a different file. A link to another sessionmail copy of the
  hook is left as is. A link to our own hook that now points nowhere (a
  rebuilt tool venv) is repaired.
- **What sends your mail** (`carrier`). It reports the carrier and where
  its value came from, such as `the mail drop (the default, from
  ~/.sessionkit/mail/settings/mail.template.json)` or `the MTA at
  /usr/sbin/sendmail (from ~/.sessionkit/mail/settings/mail.json)`. If the
  `sendmail` key names a program that is not an executable file, it prints
  a `MANUAL` line naming the key to write.
- **The MagicDNS suffix**, on a postfix host only: a `MANUAL` line, naming
  the `magicdns_suffix` key, when none is found.
- **The mail puller**, with `--pull` only (`pull-agent`, the last step). It
  installs the LaunchAgent that pulls a mail host's boxes down into this
  host's postfix (macOS only). With the pull config incomplete, the plan is
  refused (exit 2) before any step is read, naming each missing key and its
  variable. The step writes the plist, lints it with `plutil -lint`, boots
  out the old job and bootstraps the new one. A second run on an unchanged
  host reports it `DONE` and writes nothing. It never replaces a job that
  is not a sessionmail puller. Without `--pull`, setup has no pull step and
  never reads the pull config. More: [the pull][pull].

## What it does not do

- **It writes no settings key.** Each `MANUAL` line names the key to write
  in `~/.sessionkit/mail/settings/mail.json`, and the variable that would
  win over it: [settings][settings].
- **It never reads or writes your own notmuch config.** `~/.notmuch-config`
  and any other profile are left exactly as they are, and no hook is linked
  into their folders.
- **It runs `notmuch new` once, while no index exists.** That one run builds
  the empty index the hook folder needs. Nothing in setup keeps the index
  fresh: until something runs `notmuch --config=<profile> new` again
  (launchd, cron, or by hand), `message mark` and `memory` read what was
  indexed.
- It never makes a session box, and it never touches another host.
- It does not install the puller unless you pass `--pull`, and it never
  boots out a puller job it did not write.

[file-drop]: file-drop.md
[notmuch-settings]: notmuch-settings.md
[post-new-hook]: notmuch-settings.md#the-post-new-hook
[postfix-configuration]: postfix-configuration.md
[pull]: pull.md
[settings]: settings.md
