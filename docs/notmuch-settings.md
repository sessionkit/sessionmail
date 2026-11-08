# notmuch settings

sessionmail reads ONE notmuch config: its own **sessionkit profile**.
[`message setup`][message-setup] writes it, with its database at the mail
root. Where the file is depends on the host:

- **A fresh host:** `~/.sessionkit/mail/settings/notmuch/sessionkit/config`.
  It hangs off `HOME`; `$XDG_CONFIG_HOME` does not move it.
- **A host that already has a profile:**
  `$XDG_CONFIG_HOME/notmuch/sessionkit/config` (`$XDG_CONFIG_HOME` defaults
  to `~/.config`), else `~/.notmuch-config.sessionkit`. While that old file
  is real, sessionmail reads it and `message setup` leaves it where it is.
  Nothing is moved or copied. The exact rule: [where sessionmail
  looks][where-sessionmail-looks].
- **The index does not move.** It stays in the mail root's `.notmuch`,
  whichever file holds the profile.

- **Your own notmuch config is never read or written.** `~/.notmuch-config`
  and any other profile stay yours: sessionmail passes
  `--config=<the profile>` on every notmuch call and drops
  `NOTMUCH_CONFIG`, `NOTMUCH_DATABASE` and `NOTMUCH_PROFILE` from notmuch's
  environment, so none of them can point it elsewhere.
- **Nothing needs exporting.** A launchd job, cron or `ssh host cmd` finds
  the profile the same way a shell does.
- **No profile, no search.** With no profile file, each call refuses,
  naming the profile and the paths it tried. Run `message setup`.
- The `notmuch.profile` key names another profile ([settings][]).

## Where sessionmail looks

For the profile `<p>` (`sessionkit`, unless `notmuch.profile` names
another), host mode reads the first of:

1. `$XDG_CONFIG_HOME/notmuch/<p>/config`, while it is real.
2. `~/.notmuch-config.<p>`, while it is real.
3. `~/.sessionkit/mail/settings/notmuch/<p>/config`, when it is a file.

- **Real** means a file, symlinks followed, that is not the third path
  itself. An old path that is a symlink to the new file hands over to the
  new one. A dangling symlink or a directory counts as no file.
- **Old and new both real:** the old one is read, and the new one is never
  read or merged.
- **None of the three:** each call refuses, naming all three paths.
  `message setup` writes the third, making its folders, and never writes
  over a file.
- sessionmail names the file it found with `--config`, so notmuch's own
  search (next section) never chooses it.
- The new path is not one notmuch searches by itself. To reach it by hand,
  pass `--config`; `NOTMUCH_PROFILE=sessionkit` does not find it.
- Store mode keeps notmuch's own order for a store's profile, unchanged
  (`SESSIONMAIL_INDEX`, under Settings below).

## Where notmuch looks (notmuch 0.40)

- `NOTMUCH_CONFIG`, if set. A path that does not exist fails.
- Else `$XDG_CONFIG_HOME/notmuch/<profile>/config` (`$XDG_CONFIG_HOME`
  defaults to `~/.config`, the profile to `default`).
- Else `~/.notmuch-config`, or `~/.notmuch-config.<profile>` when
  `NOTMUCH_PROFILE` is set.
- The XDG file wins when both exist.
- This is notmuch's search, not sessionmail's: sessionmail finds its
  profile as [above][where-sessionmail-looks], then names it with
  `--config`, so this search never reaches `~/.notmuch-config` for
  sessionmail.
- Each of these exits 1 with `Error: cannot load config file.`: no file at
  any default path, `HOME` pointing elsewhere, `NOTMUCH_PROFILE` set but
  empty, or naming a missing profile, and `NOTMUCH_CONFIG` naming a missing
  file. `notmuch config get` is the exception: with no config, or a missing
  profile, it exits 0 (measured; `src/sessionmail/_index.py:33-35 @ c854322`),
  so test with a search.
- A non-login shell on macOS may also lack `/opt/homebrew/bin` on `PATH`
  (the hook notes this of launchd,
  `src/sessionmail/notmuch/post-new:15-17 @ 93ad8d6`). That fails as
  `command not found`, which is a different error.

## The sessionkit profile

`message setup` writes it from the package's template, when there is no
profile yet and no notmuch database in the mail root. It never writes over
a file, and it adopts no database: with a `.notmuch` already in the mail
root, it prints a MANUAL line and leaves the profile to you. In short, the
profile holds (measured: `notmuch --config=<file> new` indexes with it and an
empty environment apart from `HOME` and `PATH`):

```ini
[database]
path=/Users/alice/.mail

[new]
tags=unread;inbox;
ignore=.DS_Store;/^sent$/

[search]
exclude_tags=deleted;spam;

[maildir]
synchronize_flags=true

[index]
header.Supersedes=Supersedes
header.XSupersedes=X-Supersedes
```

- `database.path` is the mail root (`message path --root`): the parent of
  the boxes folder. sessionmail defaults it to `~/.sessionkit/mail`
  (`_maildir.DEFAULT_MAIL_ROOT`); a host born before the move keeps
  `~/.mail` by its `root` key.
- `[new]`, `[search]` and `[maildir]` are the settings in use today, kept as
  they were in the store-profile template
  (`src/sessionmail/notmuch/notmuch-config.store.template:18-30 @ c854322`).
- The template has no `hook_dir` line: notmuch's default,
  `<mail root>/.notmuch/hooks`, is where `message setup` links the hook.
- The two `index.header` lines are the ones the post-new hook needs
  (`README.md:44-51 @ c854322`). The template indexes more headers (`Task`,
  `Type`, `X-Session-From` and others) for queries
  (`src/sessionmail/notmuch/notmuch-config.store.template:32-51 @ c854322`).
- Headers added to an existing index need one `notmuch reindex '*'`
  (`README.md:54 @ c854322`).
- **On an insert host, keep `unread` in `new.tags`.** postfix's
  `notmuch insert` files a message into `new/` only when its new tags
  include `unread`; without it the file goes to `cur/…:2,S` and the pager,
  which watches `new/`, never hears it (measured, notmuch 0.40;
  [index at delivery][index-at-delivery]).

## The post-new hook

- `hooks/notmuch-post-new` tags mail at index time (`+agent`, `+memory`)
  and re-derives `superseded` from each message's `Supersedes:` header, so
  a correction survives rebuilding the index.
- A supersede counts only from the target's own sender: the carrier's
  `From:` addr-spec must match the target's, case-blind. A carrier from
  anyone else, or with no `From:`, leaves its target alone and is tagged
  `supersedes-refused` (find them with `notmuch search
  tag:supersedes-refused`). This stops accidents, not a forger: a `From:`
  can be copied.
- Not installed by the plugin or the package. [`message setup`][message-setup]
  links it as both `post-new` and `post-insert` in the folder notmuch
  reports (`database.hook_dir`), never over a different file.
  Or link it by hand (`README.md:41-52 @ c854322`). The first
  `notmuch new` makes `<mail root>/.notmuch/` but no `hooks/` in it, so make
  that first (measured; the README at `c854322` left the `mkdir` out):

  ```sh
  R="$(message path --root)"; mkdir -p "$R/.notmuch/hooks"
  ln -s <repo>/hooks/notmuch-post-new "$R/.notmuch/hooks/post-new"
  ln -s <repo>/hooks/notmuch-post-new "$R/.notmuch/hooks/post-insert"
  ```

- It needs the two indexed headers (the `[index]` lines above). On a
  config that lacks them, set them on the profile's file
  ([where sessionmail looks][where-sessionmail-looks]).
- A fresh host:

  ```sh
  notmuch --config="$HOME/.sessionkit/mail/settings/notmuch/sessionkit/config" config set index.header.Supersedes Supersedes
  notmuch --config="$HOME/.sessionkit/mail/settings/notmuch/sessionkit/config" config set index.header.XSupersedes X-Supersedes
  ```

- A host with the old XDG profile (for the dot file, name
  `"$HOME/.notmuch-config.sessionkit"` instead):

  ```sh
  notmuch --config="$HOME/.config/notmuch/sessionkit/config" config set index.header.Supersedes Supersedes
  notmuch --config="$HOME/.config/notmuch/sessionkit/config" config set index.header.XSupersedes X-Supersedes
  ```

- On a host that delivers through `notmuch insert`
  ([index at delivery][index-at-delivery]), the `post-insert` link is
  required: it is what tags each message as it lands. `delivery-check`
  reports a missing one.
- `<mail root>/.notmuch/hooks` is notmuch's default `database.hook_dir`,
  unless `$XDG_CONFIG_HOME/notmuch/default/hooks` exists: then notmuch runs
  the hooks there instead, whichever config file it read. The profile there
  is `default` because sessionmail drops `NOTMUCH_PROFILE` from notmuch's
  environment (measured, `notmuch config get database.hook_dir`).
- The hook finds notmuch (`src/sessionmail/notmuch/post-new:21-50
  @ 93ad8d6`): `SESSIONMAIL_NOTMUCH` (a bare name is looked up on `PATH`),
  else `notmuch` on `PATH`, else the first runnable of `NOTMUCH_PATHS`:
  `/opt/homebrew/bin/notmuch`, `/usr/local/bin/notmuch`,
  `/usr/bin/notmuch`, the list `postfix/nm-deliver` uses. The list is what
  keeps it working under launchd, whose `PATH` has no Homebrew prefix.
  A `SESSIONMAIL_NOTMUCH` that is not runnable does not fall back. With no
  notmuch found, the hook prints one `post-new: notmuch not found` line on
  stderr and exits 1, so `notmuch new` reports a failed hook.
- Its config keys, read with `notmuch config get`:
  - `sessionmail.agent_query`, default `path:agents/**`
    (`src/sessionmail/notmuch/post-new:33-37 @ c854322`).
  - `sessionmail.memory_query`, default `path:agents/memory/**` (same lines).
  - `sessionmail.post_new_command`: an absolute path, run after each
    indexing pass; unset is off (`README.md:56-77 @ c854322`). What it
    runs and how: [post-new command][post-new-command].

## Settings

The `notmuch` keys in `mail.json` ([settings][]). Each one's variable, when
set and not empty, wins over the file.

- `notmuch.profile`: the profile sessionmail reads in host mode, default
  `sessionkit`. Variable: `SESSIONMAIL_NOTMUCH_PROFILE`. notmuch's own
  `NOTMUCH_PROFILE` never chooses it.
- `notmuch.command`: the notmuch binary, default `notmuch` on `PATH`.
  Variable: `SESSIONMAIL_NOTMUCH`. `message mark` and `message setup` read
  the key and its variable. The send's parent lookup and the `memory`
  scripts run `notmuch` from `PATH`.
  - The post-new hook and `store-index` read the variable only, never the
    key: the variable first, then
    `PATH`, then the hook's list
    (`src/sessionmail/notmuch/post-new:21-50 @ 93ad8d6`,
    `src/sessionmail/notmuch/store-index:121-148 @ 93ad8d6`). `store-index`
    passes the binary it found to the hook it runs, as
    `SESSIONMAIL_NOTMUCH` (`store-index:327-332 @ 93ad8d6`).
- `notmuch.timeout`: seconds per call, default 10, for `message mark`. The
  send's parent lookup has a fixed 2 s.
- `notmuch.refresh`: seconds the readers' index refresh may take, default 2;
  `0` turns it off. In host mode `memory` runs
  `notmuch --config=<profile> new --no-hooks --quiet` before it reads, and
  `message mark` runs it once when an id is missing. At the limit notmuch
  is stopped with SIGINT, keeping what it indexed. The refresh runs no
  hooks, so on a host with no hooked `notmuch new` the profile's saved
  queries and store-index stats do not see the `agent`, `memory` or
  `superseded` tags: run a hooked `notmuch new` (a job, or by hand) if you
  use them.

Environment only (no key):

- `SESSIONMAIL_INDEX`: `host` (unset, the default) or `store`. Host mode
  reads the profile above; store mode reads a store's own profile, in
  notmuch's own order (`$XDG_CONFIG_HOME/notmuch/<store>/config`, else
  `~/.notmuch-config.<store>`).
- `NOTMUCH_PROFILE`: in store mode only, the store's profile. Without it
  the seat's store is found from its box. A store profile is written by
  `store-index profile STORE` into `~/.notmuch-config.STORE`
  (`src/sessionmail/notmuch/store-index:12-22 @ c854322`).

Back to [notmuch][].

[notmuch]: notmuch.md
[post-new-command]: post-new-command.md
[index-at-delivery]: postfix-configuration.md#index-at-delivery-opt-in
[message-setup]: message-setup.md
[settings]: settings.md
[where-sessionmail-looks]: #where-sessionmail-looks
