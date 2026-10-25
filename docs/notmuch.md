# notmuch

notmuch is a mail indexer, an outside program, not part of sessionmail. You need
it for `memory` and for marking mail outside your own box. Hearing, sending,
listing and reading mail do not need it.

- **NEEDS:** the command fails without notmuch. **DEGRADES:** it works, with less.
- Every row runs the `notmuch` binary. Nothing imports the Python bindings.

## What needs notmuch

| command | without notmuch | what it uses notmuch for |
|---|---|---|
| `memory ls` | NEEDS: one stderr line naming notmuch, exit 2 | finds the `[ROOT]` thread (search, or a check of the root hint), then fetches the thread to walk it |
| `memory cat` | NEEDS: one stderr line naming notmuch, exit 2 | the same root search, then the thread with bodies |
| `memory tree` | NEEDS: one stderr line naming notmuch, exit 2 | the same root search and thread fetch |
| `memory run` | NEEDS, and still exits 0: it runs the default item, which fails and says notmuch is missing | finds a label's thread, then shows it with bodies to run its items |
| `memory wake` | DEGRADES: RECENT says it could not look, exit 0 | threads to the box from the last 2 days, and which threads are closed |
| `message mark`, mail in another box | NEEDS: `not marked`, exit 2 | finds the files that hold a Message-ID |
| `message mark`, mail in your own box | DEGRADES: warns, then finds it on disk | the same lookup |
| `message send -r`, `--supersedes` | DEGRADES: a slower walk of the box | finds the parent by Message-ID, to thread the send; for `-r`, also to read where the parent was delivered and pick the From |
| `message setup` | DEGRADES: exit 4, the index and hook steps are left | builds its own empty index once (`notmuch new --no-hooks`), then asks for the hook dir, to link the post-new and post-insert hooks |
| the puller (`message setup --pull` installs it) | DEGRADES: dedup checks only the last 15 minutes | dedup: is this Message-ID already in the box? |
| post-new / post-insert hook | NEEDS: it is a notmuch hook | tags `+agent`, `+memory` and `+superseded`, and runs your [post-new command][post-new-command] |
| `store-index build`, `update` | NEEDS | indexes a store's own mail (`notmuch new --no-hooks`) |
| `store-index count`, `search` | NEEDS | counts, and searches across stores |
| `nm-deliver` ([index at delivery][index-at-delivery], opt-in) | NEEDS: postfix defers the mail (exit 75), so it is held, not lost | `notmuch insert` as each message lands |
| `delivery-check`, on an insert host | DEGRADES: reports a gap | reads notmuch's `new.tags` and hook settings |

The MCP tools run the same verbs in-process, so each one gets its verb's row.
`message_mark` and the `memory_*` tools are affected.

**Unaffected:** `message send` (without `-r` or `--supersedes`), `list`, `read`,
`tree`, `verify`, `path`, `receipt`, `box`, `subscription` and `seat`; every `pager`
verb and the plugin's hooks; `memory init`; and default delivery, by postfix
`virtual(8)` or the mail drop. `message box archive` and `unarchive`, and
`store-index profile`, read or write notmuch config files but never run notmuch.

## How sessionmail uses it

- **One search engine.** The ratified pager spec says search SHALL be notmuch,
  with no second index.
- **Hearing does not need it.** `pager listen` watches the box's `new/` itself.
- **sessionmail does not keep the host index fresh.** `message setup` runs
  `notmuch new --no-hooks` once, only on sessionmail's own profile while its
  index is empty, never under `~/.notmuch-config`. After that the host runs
  `notmuch new` on a schedule, such as a launchd agent. The other exception is
  `store-index`, which indexes a store's own index.
- **An insert host indexes at delivery.** postfix pipes each message into
  `notmuch insert`, so mail is searchable as it lands. The scheduled `notmuch new`
  is still needed, for the drop's mail and flag renames. See
  [index at delivery][index-at-delivery].
- **Which index:** the host's own profile by default. `SESSIONMAIL_INDEX=store`
  reads one store's index instead.
- **Outside this package:** sessionview's sidebar runs notmuch itself, not through
  sessionmail. Without notmuch the messages tab still opens, with no sidebar.
- **Setup and config:** [notmuch-settings][].

[notmuch-settings]: notmuch-settings.md
[post-new-command]: post-new-command.md
[index-at-delivery]: postfix-configuration.md#index-at-delivery-opt-in
