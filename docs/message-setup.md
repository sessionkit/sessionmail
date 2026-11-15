# Making a host ready: `message setup`

A fresh install has no mail root, no boxes, no notmuch profile and no
notmuch hook. `message setup` makes them. It is listed on `message --help`.
It is not an MCP tool: it changes this host, which is the host owner's act,
so it runs from a shell. The kit's installer calls it by its argv.

```sh
message setup --dry-run
message setup --box NAME
message setup --write-maps
message setup --pull
```

- `--dry-run` prints the plan and changes nothing.
- `--box NAME` applies it, making box `NAME` too. Give `--box` again for
  each further box.
- `--write-maps`, on a postfix host with no maps, writes them.
- `--pull` also installs the mail puller's LaunchAgent.

- **It prints first.** The whole plan is printed before anything is
  applied: the sudo steps first, each with its exact command (or `none`),
  then every step with its state, `DONE`, `TODO`, `BLOCKED` or `MANUAL`.
  A sudo step runs only at a terminal. Without one, it is printed for you
  to run. A sudo step that is not `TODO` prints why instead of a command,
  so there is never a line to copy that setup itself would not run. The
  one sudo step is the delivery wrapper's install, below, when its folder
  is not yours to write.
- **It is safe to re-run.** Every check only reads. A second run reports
  every step it finished as `DONE` and writes nothing.
- **Exit:** 0 every step is DONE · 1 a step failed (what ran before it
  stays; re-run) · 2 usage, or refused before anything was applied · 3
  the puller's job is installed and loaded, but its service file could not
  be written (the job is up; re-run `message setup --pull` to write it) · 4
  what setup can do is done, and steps are left for you. When two apply, 1
  wins, then 3, then 4: a run that exits 3 still prints its block of steps
  left for you.
- **It ends with one list of what is left.** When steps are left, the
  last lines are one numbered block, every step that is not `DONE`, in
  order, each with its state and why. A step that waits on another names
  it. The block ends with the line to run next. A box it did not make is
  in the block, with why.

## What it does

- **The boxes root**, `message path --boxes`: `stores/` under the mail
  root (`$SESSIONMAIL_ROOT` when set, else the `root` key, else
  `~/.sessionkit/mail`), or `agents/` on a tree born before the move, made
  0700. The readers, the sender and the [file drop][file-drop] share one
  resolver, so there is no second root to refuse. A retired root variable
  that is still set (`SESSION_MAIL_ROOT`, `MAIL_ROOT`) is named on this
  step as ignored; it decides nothing.
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
  sessionmail reads (the `notmuch.profile` key names another). When there
  is none, setup writes it at
  `~/.sessionkit/mail/settings/notmuch/sessionkit/config`, from the
  package's template, its database the mail root. An existing
  `$XDG_CONFIG_HOME/notmuch/sessionkit/config`, else
  `~/.notmuch-config.sessionkit`, is the profile while it is a real file:
  the step is done, and setup writes, moves and copies nothing. It never
  writes over a file. If the mail root already holds a notmuch database
  (`<root>/.notmuch`), it writes nothing and prints a `MANUAL` line: setup
  adopts no database. More: [notmuch settings][notmuch-settings].
- **The notmuch index** (`notmuch-index`): sessionmail's own index, built
  once. When the profile has no database yet, setup runs
  `notmuch --config=<profile> new --no-hooks` once, in the same run that
  writes the profile, and the dry run prints that line. It runs it only on
  an EMPTY index: notmuch opens no database for the profile
  (`notmuch --config=<profile> count '*'` fails), and no `.notmuch/xapian`
  is at the `database.path` it reports. A hook folder alone proves nothing:
  a profile that sets `hook_dir` reports it before any database exists. If
  a database is already there, it runs nothing and the step is `BLOCKED`,
  saying what it found. If notmuch is not found, the step is `BLOCKED` and
  names the line it will run once notmuch is found. A `new` that fails
  stops the run (exit 1).
- **The notmuch hook.** It links `post-new` and `post-insert` to the
  packaged [post-new hook][post-new-hook], in the folder notmuch reports
  for the profile (`notmuch --config=<profile> config get
  database.hook_dir`), after the index step, in the same run. Until the
  index exists it is one `notmuch-hook` step that waits on
  `notmuch-index`. With no profile nothing is linked anywhere. It never
  replaces a different file. A link to another sessionmail copy of the
  hook is left as is, and so is a chain of links that ends at one (a
  relative `post-insert -> post-new`, say): the chain is judged by where
  it ends, not by its first link's text. A link to our own hook that now points nowhere (a
  rebuilt tool venv) is repaired.
- **Only sessionmail's own hook folder.** Unless a profile sets
  `hook_dir`, notmuch runs the hooks in
  `$XDG_CONFIG_HOME/notmuch/default/hooks` whenever that folder exists,
  whichever config it read: the folder your own notmuch uses. So the
  profile setup writes sets `hook_dir=<mail root>/.notmuch/hooks`. On an
  older profile that sets none, when notmuch reports a folder outside the
  profile's `database.path`, the hook step is `BLOCKED`: setup links
  nothing there, and never edits the profile. Its line names the folder and
  the one setting to add, by `notmuch --config=<profile> config set
  database.hook_dir <mail root>/.notmuch/hooks` or by hand under
  `[database]`, then run setup again. Sessionmail links already in that
  folder are named with the `rm` that removes them; setup removes nothing
  there itself. A `hook_dir` your profile sets is yours: setup links there,
  wherever it is.
- **The delivery wrapper** (`delivery`), on a host that indexes at
  delivery: postfix's `master.cf` has an `nminsert` service
  ([index at delivery][index-at-delivery]). Setup installs or refreshes
  `/usr/local/etc/postfix-agents/nm-deliver` from the package, with the
  notmuch profile's path baked in, so delivery indexes through the same
  config `message` reads. It reports, and never edits, the service: when
  its `argv=` runs another path, a `MANUAL` line says to repoint it. When
  the folder is not yours to write, the install is a privileged step,
  printed first with its exact `sudo` line, and run only at a terminal;
  setup writes the copy it installs under
  `~/.sessionkit/mail/settings/postfix/` first. Run setup as the
  service's `user=`: under another user the step is `BLOCKED`. It is
  `BLOCKED` too while the profile's file is not there and the profile
  step will not write it (a notmuch database already in the mail root):
  a wrapper baking in a missing file would defer every delivery, so the
  copy the service runs is left as it is. If the installed copy already
  bakes in a file that has gone, the `BLOCKED` line says so: every
  delivery through it defers until the file is back. Before it writes
  over a wrapper the service runs, setup looks every address in
  `virtual_mailbox_maps` up the way the kit's wrapper will (the maps in
  postconf order, the first hit). If an address that delivers today (some
  map holds an `agents/` or `stores/` maildir for it) would defer under the
  new copy, the step is `BLOCKED`, naming each address, the value it would
  take and the map it is in; the wrapper is left as it is. Fix the maps or
  their order (setup never edits postfix), then run setup again. A map
  that `postmap -s` cannot list is `BLOCKED` too: the swap cannot be proven
  safe. A host with
  no `nminsert` service, or no `postconf`, gets no wrapper: the step is
  `DONE`.
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
  out the old job and bootstraps the new one. Then it writes the puller's
  service file, `~/.sessionkit/mail/services/puller.json`, so `sessionkit
  service` finds the job. If that file cannot be written, the step says
  `FAILED`, the job stays loaded, and setup exits 3. A second run on an unchanged
  host reports it `DONE` and writes nothing. It never replaces a job that
  is not a sessionmail puller. Without `--pull`, setup has no pull step and
  never reads the pull config. More: [the pull][pull].

## What it does not do

- **It writes one settings key, `host`, and no other.** It adds `host`
  only to a `~/.sessionkit/mail/settings/mail.json` that has none, and
  never changes one that is set. For every other key, a `MANUAL` line
  names the key to write and the variable that would win over it:
  [settings][settings].
- **It never reads or writes your own notmuch config.** `~/.notmuch-config`
  and any other profile are left exactly as they are, and no hook is linked
  into their folders.
- **It never edits a profile that exists.** A setting it needs and the
  profile lacks is a line for you to run, never a write.
- **It runs `notmuch new` once, while no index exists.** That one run builds
  the empty index the hook folder needs. Setup schedules nothing.
- **The readers keep the index fresh.** `memory` refreshes it before it
  reads, and `message mark` when an id is missing, each with a bounded
  `notmuch new --no-hooks` (`notmuch.refresh`, seconds, default 2; 0 turns
  it off). More: [notmuch][notmuch].
- It never makes a session box, and it never touches another host.
- It does not install the puller unless you pass `--pull`, and it never
  boots out a puller job it did not write.

[file-drop]: file-drop.md
[index-at-delivery]: postfix-configuration.md#index-at-delivery-opt-in
[notmuch]: notmuch.md
[notmuch-settings]: notmuch-settings.md
[post-new-hook]: notmuch-settings.md#the-post-new-hook
[postfix-configuration]: postfix-configuration.md
[pull]: pull.md
[settings]: settings.md
