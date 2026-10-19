# postfix configuration

**Same-host mail needs no postfix.** By default `message send` writes each
copy straight into this host's maildirs: the [file drop][file-drop].
postfix is for mail between hosts, and it is **opt-in**: a host uses it
only when the `sendmail` key ([settings][]) names postfix's `sendmail`.
sessionmail never edits postfix's configuration; the setup below is yours.

This page is postfix for sessionmail: what it does and why, the one-time
setup, what needs no sudo afterwards, and what can read the maps. Then the
parameters sessionmail reads, the map generator, index at delivery and its
settings. sessionmail reads five postfix parameters and writes two maps;
everything else in `main.cf` is the host's. Every file it touches is named
in [configuration][].

## What postfix does for sessionmail

postfix is a mail server (an MTA). By default sessionmail hands it every
message it sends, and postfix delivers each copy into a box under
`~/.mail/agents/`.

- **What it is:** an outside program. sessionmail does not install it, and
  nothing in this package configures it: the setup below is done by hand.
- **Sending:** with `"sendmail": "/usr/sbin/sendmail"`, `message send`
  pipes the message to `/usr/sbin/sendmail -t -i -f FROM`. The key (or its
  variable, `SESSIONMAIL_SENDMAIL`) may name another sendmail by its
  absolute path.
- **The pre-flight:** before sending, each recipient on this host is looked
  up in postfix's maps with `postmap -q`. One miss refuses the whole send
  by name (`src/sessionmail/_sender.py:927-950 @ c854322`,
  `src/sessionmail/_sender.py:963-968 @ c854322`), unless the
  address looks like a session and postfix has a transport for the relay
  domain: then it is relayed to the mail host
  (`src/sessionmail/_sender.py:951-962 @ c854322`,
  `src/sessionmail/_sender.py:971-987 @ c854322`). With no `postmap` on
  `PATH`, or no maps named by `postconf`, there is no pre-flight
  (`src/sessionmail/_sender.py:935-943 @ c854322`).
- **The maps:** `vmailbox-gen`, shipped in this package, writes both:
  - `vmailbox` (`virtual_mailbox_maps`): each box under `~/.mail/agents/`,
    its address to its maildir
    (`src/sessionmail/postfix/vmailbox-gen:29-33 @ c854322`).
  - `valias` (`virtual_alias_maps`): a seat's mail copied into each live
    session's box, the doorbell
    (`src/sessionmail/postfix/vmailbox-gen:111-116 @ c854322`). Live
    sessions are read from Claude Code's per-session files
    (`~/.claude/sessions/`), and only a session with a box gets a row
    (`src/sessionmail/postfix/vmailbox-gen:118-125 @ c854322`).
- **Who runs the generator:** three callers write the maps.
  - A session's birth, from `pager listen` or the session-start hook
    (`src/sessionmail/pager.py:2102 @ c854322`,
    `src/sessionmail/pager.py:5042 @ c854322`). It runs when the box is new
    or its doorbell is not live
    (`src/sessionmail/_birth.py:347-357 @ c854322`), and when a seatless
    birth wrote a subscription
    (`src/sessionmail/_birth.py:377-392 @ c854322`).
  - `message subscription add|remove`, the alias map only
    (`src/sessionmail/message.py:1558 @ c854322`,
    `src/sessionmail/_subscribe.py:521-523 @ c854322`).
  - `pager settings migrate`, the alias map only, when it changes something
    (`src/sessionmail/pager.py:5162-5164 @ c854322`).
- The file drop also runs the generator, in its print modes, which write
  no map (`src/sessionmail/_drop.py:25-35 @ c854322`).
- **Delivery:** virtual(8) by default. Opt-in, a pipe into `notmuch
  insert` through the packaged `nm-deliver`, so each message is indexed as
  it lands; `delivery-check` says which a host uses
  ([index at delivery][index-at-delivery]).
- **Hearing mail:** postfix only delivers. The pager watches the box
  itself (`src/sessionmail/pager.py:2595 @ c854322`).
- **Other hosts:** mail to another host's address goes through postfix. The
  file drop refuses a remote domain
  (`src/sessionmail/_drop.py:46-49 @ c854322`).

## Setup (once, with sudo)

Three steps need root, once: the map directory, the `main.cf` lines and
the reload. The maps, written in between, are yours. `<you>` is the account
that owns `~/.mail`. Check each step before the next.

1. **Make the map directory**, owned by you, group `_postfix` (the group
   postfix's daemons run as: `id -gn $(postconf -h mail_owner)`), mode
   `2750`:

   ```sh
   sudo install -d -o <you> -g _postfix -m 2750 /usr/local/etc/postfix-agents
   ls -ld /usr/local/etc/postfix-agents    # drwxr-s---  <you>  _postfix
   ```

   Check the `ls`. Without root, where it can make the directory at all,
   `install` cannot set the group: it prints `Operation not permitted`,
   leaves the directory with its parent's group, not `_postfix`, and still
   exits 0 (measured, macOS `/usr/bin/install`).
   The directory is outside `/etc/postfix` because macOS guards that path
   (`src/sessionmail/postfix/vmailbox-gen:247-256 @ c854322`).

2. **Add the lines to `main.cf`** (root's file). Keep any table the host
   already lists: each `*_maps` value is a comma-separated list, so add
   sessionmail's map to it rather than replacing it.

   ```text
   # /etc/postfix/main.cf
   virtual_mailbox_base    = /Users/<you>/.mail
   virtual_mailbox_domains = <host>, <host>.<tailnet>.ts.net
   virtual_mailbox_maps    = hash:/usr/local/etc/postfix-agents/vmailbox
   virtual_alias_maps      = hash:/usr/local/etc/postfix-agents/valias
   virtual_uid_maps        = static:<your uid>
   virtual_gid_maps        = static:<your gid>
   ```

   `id -u` and `id -g` print the two numbers. Edit the file as root, or set
   each line with `sudo postconf -e 'name = value'`. The domains are the
   short host name and its tailnet name, the two key forms the generator
   writes.

3. **Link the generator in and write the maps, as you** (no sudo),
   straight after step 2. The generator, `vmailbox-gen`, ships inside the
   package, not as a command on `PATH` (`pyproject.toml:105 @ 148dd21`).
   The callers that write the maps look for it through
   `SESSIONMAIL_VMAILBOX_GEN`, then `vmailbox-gen` on `PATH`, then
   `<mail root>/.postfix/vmailbox-gen`, and never run the copy inside the
   package (`src/sessionmail/_generator.py:10-30 @ 148dd21`). So link the
   packaged script into the third place. Ask sessionmail's own Python where
   it is: `uv tool install` gives sessionmail an environment of its own, so
   a bare `python3` cannot import it. From a clone's editable install
   (`make install`) the same line prints the clone's copy.

   ```sh
   GEN="$("$(uv tool dir)/sessionmail/bin/python" -c 'import sessionmail, pathlib; print(pathlib.Path(sessionmail.__file__).parent / "postfix" / "vmailbox-gen")')"
   mkdir -p ~/.mail/.postfix
   ln -sfn "$GEN" ~/.mail/.postfix/vmailbox-gen
   ~/.mail/.postfix/vmailbox-gen --root ~/.mail   # writes vmailbox and valias, and their .db
   ls -l /usr/local/etc/postfix-agents            # -rw-r-----  <you>  _postfix, four files
   ```

   Exit 3, with only `vmailbox` and its `.db` written, means the generator
   would not write the alias map. The stderr line with
   `valias NOT regenerated` ends with the reason. On a first setup there is
   no earlier `valias.db`, so after step 4 postfix could not open the alias
   map: fix the reason, then run step 3 again.

   - `no sidecar dir .../.claude/sessions`: that directory does not exist
     yet. The generator reads Claude Code's running sessions from it
     (`src/sessionmail/postfix/vmailbox-gen:817-819 @ 148dd21`,
     `src/sessionmail/postfix/vmailbox-gen:845-849 @ 148dd21`). Start one
     Claude Code session, which creates it. The generator needs the
     directory, not a running session: with an empty `~/.claude/sessions`
     it writes all four files.
   - `no 'jq' on PATH`: install `jq`
     (`src/sessionmail/postfix/vmailbox-gen:847 @ 148dd21`).

   Link it; do not copy it. The script finds its helper, `magicdns-suffix`,
   beside the file the link points to, so a copy stops with exit 4 and
   writes nothing (`src/sessionmail/postfix/vmailbox-gen:252-258 @ 148dd21`,
   `src/sessionmail/postfix/vmailbox-gen:336-340 @ 148dd21`). The link
   follows an upgrade of sessionmail. If uv reinstalls it under another
   Python, the path moves and the link breaks: run the first and third
   lines again.

   The generator reads `virtual_mailbox_base` from `main.cf` and refuses to
   write the map directory until it names the tree it is given (exit 5,
   `src/sessionmail/postfix/vmailbox-gen:1104-1133 @ 148dd21`). postfix, for
   its part, cannot open a `hash:` map whose `.db` is missing. A run also
   needs a MagicDNS suffix ([the map generator][map-generator]).

4. **Reload postfix:**

   ```sh
   sudo postfix reload
   postconf -n | grep '^virtual_'          # the lines above, as postfix reads them
   ```

5. **Name postfix as this host's carrier**, as you (no sudo): set the
   `sendmail` key in `~/.sessionkit/mail/settings/mail.json`. Until you
   do, `message send` uses the file drop, and mail to another host is
   refused.

   ```json
   {"version": 1, "sendmail": "/usr/sbin/sendmail"}
   ```

   Keep the keys the file already holds. `message setup` reports the
   carrier and where it came from, and writes no key itself.

## After setup, no sudo

Nothing sessionmail does needs root again. The three callers above run the
generator as you, and it rewrites both maps in place: a temporary file in
the map directory, `postmap`, then a rename over the old map, under a lock
(`src/sessionmail/postfix/vmailbox-gen:1160-1210 @ 148dd21`). postfix reads
the new maps through group `_postfix`. A `message subscription add`, for
one, rewrites `valias` and `valias.db` as the user, with no reload.

Still root's, and kept by hand: `main.cf`, `master.cf` (only for
[index at delivery][index-at-delivery]), and any table of the host's own
that `main.cf` lists beside sessionmail's maps, such as
`/etc/postfix/virtual_aliases` or `/etc/postfix/virtual_mailboxes` on some
hosts. sessionmail never writes them. A change to one is a sudo edit, a
`postmap` if it is a `hash:` table, and a reload for `main.cf`.

## Security

> - **Root-owned:** `main.cf`, `master.cf` and the host's own tables in
>   `/etc/postfix` (root, group `wheel` on macOS). sessionmail only reads
>   them, through `postconf`.
> - **The map directory** is `<you>:_postfix`, mode `2750`: you can write
>   it; group `_postfix` can enter it and list it; nobody else can. Every
>   file made in it takes group `_postfix`: macOS gives a new file its
>   directory's group, and the setgid bit makes Linux do the same.
> - **The maps** are clamped to `0640` after each write: you read and
>   write, group `_postfix` reads, nobody else does. Without the clamp the
>   umask would leave them world-readable
>   (`src/sessionmail/postfix/vmailbox-gen:1180-1189 @ 148dd21`,
>   `src/sessionmail/postfix/vmailbox-gen:1204-1208 @ 148dd21`).
> - **Who reads them:** postfix's daemons, as `_postfix`, through the group,
>   and you: the pre-flight's `postmap -q` runs as the sender. postfix reads
>   only from that directory; it writes into your home only by delivery
>   under your uid (`virtual_uid_maps`)
>   (`src/sessionmail/postfix/vmailbox-gen:14-23 @ 148dd21`).
> - **What a map can do:** each row only steers delivery inside
>   `virtual_mailbox_base`, and box names with `..` or `/` are refused
>   (`src/sessionmail/postfix/vmailbox-gen:25-27 @ 148dd21`). Anyone who can
>   write the map directory can route this host's agent mail: keep it yours.

## The parameters sessionmail reads

Read with `postconf -h` (`src/sessionmail/_seat.py:2253-2273 @ c854322`,
`src/sessionmail/_seat.py:2352 @ c854322`,
`src/sessionmail/_seat.py:2361 @ c854322`,
`src/sessionmail/_sender.py:938-941 @ c854322`), except `myorigin`:

- `virtual_mailbox_base`: the mail root, the parent of `agents/`, e.g.
  `/Users/alice/.mail` (`src/sessionmail/_seat.py:2315 @ c854322`).
- `virtual_mailbox_maps`: must include `hash:<map dir>/vmailbox`. The
  doorbell check reads only `hash:` and `lmdb:` maps
  (`src/sessionmail/_seat.py:2262-2263 @ c854322`).
- `virtual_alias_maps`: must include `hash:<map dir>/valias`, the one line
  that wires the doorbell (`src/sessionmail/postfix/vmailbox-gen:111-116 @ c854322`).
- `transport_maps`: read only when a pre-flight miss looks like a session,
  to see whether postfix can relay it
  (`src/sessionmail/_sender.py:980 @ c854322`).
- `myorigin`: read with `postconf -xh` only when a signed header holds a
  bare address, to qualify it before signing
  (`src/sessionmail/_sig.py:163-180 @ c854322`,
  `src/sessionmail/_sender.py:873-878 @ c854322`).

A host running today has these, measured with `postconf`
(names and ids replaced):

```text
virtual_mailbox_base    = /Users/alice/.mail
virtual_mailbox_maps    = hash:/usr/local/etc/postfix-agents/vmailbox
virtual_alias_maps      = hash:/usr/local/etc/postfix-agents/valias
virtual_uid_maps        = static:<the mail owner's uid>
virtual_gid_maps        = static:<the mail owner's gid>
virtual_mailbox_domains = host-a, host-a.example.ts.net
```

- Delivery runs as the mail owner's uid, so every file it writes in
  `~/.mail` is the owner's (`src/sessionmail/postfix/vmailbox-gen:22-27 @ c854322`).
- The domains are the short host name and its tailnet name, the two key
  forms the generator writes (`src/sessionmail/postfix/vmailbox-gen:29-33 @ c854322`).

## The map generator, `vmailbox-gen`

- Shipped as package data at `src/sessionmail/postfix/vmailbox-gen`, not a
  command on `PATH` (`pyproject.toml:100 @ c854322`).
- Found in this order: `SESSIONMAIL_VMAILBOX_GEN`, `vmailbox-gen` on `PATH`,
  `<mail root>/.postfix/vmailbox-gen`
  (`src/sessionmail/_seat.py:2386-2394 @ c854322`).
- Writes `/usr/local/etc/postfix-agents/vmailbox` by default, and `valias`
  beside it (`src/sessionmail/postfix/vmailbox-gen:286 @ 148dd21`,
  `src/sessionmail/postfix/vmailbox-gen:315-319 @ c854322`). The directory
  was moved there because macOS guards
  `/etc/postfix` (`src/sessionmail/postfix/vmailbox-gen:247-256 @ c854322`).
- Does not set the map directory up: that is setup step 1. When the
  directory is missing it tries a plain `mkdir -p`, and when that fails it
  stops and names a `setup-mail.sh`, a script that does not ship; the
  [setup][] above is what it means
  (`src/sessionmail/postfix/vmailbox-gen:1146-1152 @ 148dd21`). If you can
  write the parent, the `mkdir -p` succeeds, in your umask's mode and not
  in group `_postfix` (macOS gives it the parent's group), so postfix
  cannot read the maps: do step 1 first.
- `--out FILE` moves both maps; `--print` and `--print-valias` write to
  stdout and touch nothing (`src/sessionmail/postfix/vmailbox-gen:61-91 @ c854322`).
- Folds in a hand-kept alias file, `<mail root>/.postfix/valias.static`, when
  it exists (`src/sessionmail/postfix/vmailbox-gen:92-93 @ c854322`).
- **Needs a MagicDNS suffix.** It takes `MAGICDNS_SUFFIX`, else asks
  tailscale (`src/sessionmail/postfix/magicdns-suffix:19-32 @ c854322`).
  With neither it writes nothing and exits 4
  (`src/sessionmail/postfix/vmailbox-gen:297-311 @ c854322`; measured).
- **Refuses a root postfix does not serve**. A writing run whose
  `--root` is not `postconf -xh virtual_mailbox_base`, aimed at the pinned map
  dir or at an `--out` dir postfix reads, writes nothing, prints one
  `REFUSED` line naming the root and the map dir, and exits 5. With no
  postconf answer, a run aimed at the pinned dir is refused whatever the root.
  `SESSIONMAIL_POSTCONF`, when set, is the only postconf it asks
  (`src/sessionmail/postfix/vmailbox-gen:1023-1088 @ 48dc52a`).
- `--host NAME` sets the short host name, default `hostname -s`
  (`src/sessionmail/postfix/vmailbox-gen:65 @ c854322`).

## Index at delivery (opt-in)

With virtual(8), postfix writes the file and the index catches up at the
next scheduled `notmuch new`, which scans every box. On a host with many
boxes one scan can take tens of seconds, and anything that waits for the
index waits for that scan. Index at delivery pipes each message into
`notmuch insert`, which indexes that one message as it writes it. Nothing
in sessionmail switches a host: it is a deploy, done by hand, and the
default stays virtual(8).

### The lines

```text
# /etc/postfix/master.cf (one service; user = the mail owner)
nminsert  unix  -       n       n       -       -       pipe
  flags=DORhu user=alice argv=/path/to/site-packages/sessionmail/postfix/nm-deliver ${recipient}

# /etc/postfix/main.cf
virtual_transport = nminsert
nminsert_destination_recipient_limit = 1
```

- `argv` is the packaged wrapper, run by path: `nm-deliver` sits beside
  `vmailbox-gen` in the installed package
  (`src/sessionmail/postfix/nm-deliver @ adc95b6`). Find it with
  `python3 -c 'import sessionmail, pathlib; print(pathlib.Path(sessionmail.__file__).parent / "postfix" / "nm-deliver")'`
  in the environment sessionmail is installed in.
- `user=` is the mail owner, the uid in `virtual_uid_maps`. pipe(8) refuses
  root and the mail system owner. Files land as that user, mode 0600, as
  under virtual(8).
- `flags=DORhu`: `D`, `O` and `R` write `Delivered-To`, `X-Original-To` and
  `Return-Path`, the three headers virtual(8) writes; readers depend on
  them. `h` and `u` fold the address to lower case, like the generator's
  keys.
- `nminsert_destination_recipient_limit = 1`: one recipient per run. The
  `D` and `O` flags need it, and each box gets its own insert.
- `virtual_transport` switches every `virtual_mailbox_domains` entry. To
  switch one domain at a time instead, leave `virtual_transport` alone and
  add a `transport_maps` row per domain (`host-a nminsert:` and
  `host-a.example.ts.net nminsert:`), then `postmap` that table if it is a
  `hash:` table. The table must be named in `transport_maps`: on a host
  that sets none, add `transport_maps = hash:/etc/postfix/transport` to
  `main.cf` as well, or the rows do nothing. Rows suit a host that relays
  some domains elsewhere, or a trial on one name.
- Keep `virtual_mailbox_domains`, `virtual_mailbox_maps` and
  `virtual_mailbox_base`. postfix still uses the maps to reject unknown
  recipients, and the wrapper reads the box's folder from them.
  `virtual_uid_maps` and `virtual_gid_maps` are unused under the pipe and
  harmless to keep.
- `postfix reload` applies the change. `postconf -n virtual_transport` and
  `postconf -M nminsert/unix` show it.

### Preconditions

Check each one before the reload. `delivery-check`, run as the mail owner,
reports the first four; the schedule is the host's to check.

- **A notmuch config at a default path**, `~/.notmuch-config` of the mail
  owner. pipe(8) passes no `NOTMUCH_CONFIG`, and the wrapper sets `HOME`
  from the passwd entry, so an exported variable never reaches it
  ([notmuch-settings][]).
- **`unread` in `new.tags`.** With it, insert writes the file to `new/`.
  Without it, insert writes `cur/…:2,S`, and the pager, which watches
  `new/`, never hears the mail (measured, notmuch 0.40;
  `tests/test_postfix_nm_deliver.py @ adc95b6`,
  `test_new_tags_without_unread_files_into_cur`).
- **The hook linked as `post-insert`**, as well as `post-new`
  ([notmuch-settings][]). Without it mail is indexed but not tagged
  `+agent`, `+memory` or `+superseded` until the next `notmuch new`.
- **`virtual_mailbox_base` equal to notmuch's `database.mail_root`.**
  insert's `--folder` is relative to `mail_root`, and the map values are
  relative to `virtual_mailbox_base`.
- **A periodic `notmuch new`, every few minutes, not every few seconds.**
  Keep it: it still indexes mail the [file-drop][] writes, flag renames made
  outside notmuch, and any message whose indexing failed. But insert waits
  while a `notmuch new` holds the database's write lock, and nothing reaches
  `new/` until the lock frees (measured, notmuch 0.40). So a short interval
  delays delivery itself. Five minutes is a safe floor.

### What the wrapper does

`nm-deliver RECIPIENT` (`src/sessionmail/postfix/nm-deliver:19-29 @ 9e84a75`):

- reads `recipient_delimiter` and `virtual_mailbox_maps` with `postconf -xh`,
  so a map written `hash:$config_directory/vmailbox` is expanded as postfix
  expands it (`src/sessionmail/postfix/nm-deliver:91-94 @ 8a126ec`);
- looks the recipient up as virtual(8) does: `user+ext@domain`, then
  `user@domain`, then `@domain`, over every map
  (`src/sessionmail/postfix/nm-deliver:115-129 @ adc95b6`);
- takes the folder from the map value, so a nested box
  (`agents/<store>/<project>/`) lands in its own maildir;
- runs `notmuch insert --keep --create-folder --folder=<value>` under a
  120 s timeout (`src/sessionmail/postfix/nm-deliver:158-189 @ adc95b6`).

pipe(8) bounces any exit status that is not a sysexits temporary code, and
a bare `notmuch insert` exits 1 on a config or database fault. So the
wrapper turns every index-side failure into a deferral, and postfix retries
it from the queue (`src/sessionmail/postfix/nm-deliver:29-39 @ adc95b6`):

| situation | first line of output | exit | postfix |
| --- | --- | --- | --- |
| delivered (`--keep` keeps a file whose indexing failed) | | 0 | done |
| no map entry for the recipient | `5.1.1 <rcpt>: no mailbox mapping` | 67 | bounce, as virtual(8) |
| map value not a maildir under `agents/` | `4.3.5 …` | 75 | defer |
| postconf, postmap, notmuch or `HOME` not usable | `4.3.0 …` | 75 | defer |
| `notmuch insert` failed, any non-zero status | `4.3.0 … (rc N)` | 75 | defer |
| `notmuch insert` ran past the timeout | `4.4.1 … index busy` | 75 | defer |

- The timeout must exceed the host's slowest `notmuch new`, since insert
  waits on it, and stay well under postfix's `command_time_limit` (1000 s by
  default): pipe(8) bounces a command that runs past that limit.
- Deferred mail shows in `mailq`. postfix bounces it after
  `maximal_queue_lifetime` (5 days by default).
- notmuch, postconf and postmap are found by absolute path, because pipe(8)
  sets `PATH=/usr/bin:/bin`. The `NM_DELIVER_*` variables that override
  them are for tests; pipe(8) never passes them.

### Which delivery a host uses

`delivery-check`, beside the wrapper, reads the config and never writes
(`src/sessionmail/postfix/delivery-check @ adc95b6`):

```sh
/path/to/site-packages/sessionmail/postfix/delivery-check --box agents/alice
```

- It prints `delivery: virtual`, `insert`, `mixed` (some domains each way)
  or `unknown`, then its evidence, then each gap: a missing service or
  recipient limit, header flags, `unread`, the `post-insert` hook, the mail
  root.
- It reads notmuch's settings as the wrapper's insert will find them:
  `HOME` from the passwd entry, and no `NOTMUCH_CONFIG`, `NOTMUCH_PROFILE`
  or `XDG_*` from the caller, since pipe(8) passes none. Run it as the
  service's `user=`; run as anyone else, it reads that user's config, and
  says so as a gap.
- A `virtual_mailbox_domains` entry that names a table (postfix's default,
  `$virtual_mailbox_maps`) is reported as a table, by `virtual_transport`.
  A table cannot be listed, so with `transport_maps` set its domains' rows
  are a gap to check by hand with `postmap -q`.
- It exits 0 when it could look and 2 when it could not.
- `--box DIR` adds the file-name test over the box's newest files. The two
  writers name a file differently, so the files show which wrote them with
  no config read (`src/sessionmail/postfix/delivery-check:61-62 @ adc95b6`):
  - virtual(8): `<sec>.V<dev>I<inode>M<usec>.<host>`, e.g.
    `2000000000.V100000fI1000001M100000.host-a`;
  - `notmuch insert`: `<sec>.M<usec>P<pid>.<host>`, e.g.
    `2000000060.M100000P11111.host-a`.

### Rollback

Delete the `virtual_transport = nminsert` line from `main.cf` (or the
`transport_maps` rows, then `postmap` the table), and run `postfix reload`.
virtual(8) delivers the next message. The `nminsert` service can stay in
`master.cf` unused. Mail the wrapper deferred should go through virtual(8)
on its next retry, since postfix looks the transport up again for each
attempt (a prediction; not measured here). `mailq` shows what is waiting.

## Settings

Keys in `mail.json` ([settings][]). Each one's variable, when set and not
empty, wins over the file.

- `sendmail`: an absolute path selects that sendmail (postfix's is
  `/usr/sbin/sendmail`); `"drop"`, the default, selects the [file-drop][].
  Variable: `SESSIONMAIL_SENDMAIL`.
- `postfix.generator`: the generator path. Variable:
  `SESSIONMAIL_VMAILBOX_GEN`.
- `postfix.relay_domain`: where the pre-flight relays a session-shaped
  miss, default `relay.home`. Variable: `SESSIONMAIL_RELAY_DOMAIN`.
- `postfix.origin`: the domain for a bare address, read instead of
  `myorigin`. Variable: `SESSIONMAIL_ORIGIN`.
- `magicdns_suffix`: the tailnet suffix, e.g. `example.ts.net`, for the
  generator's helper; unset, it asks tailscale. Variable: `MAGICDNS_SUFFIX`.
- `SESSIONMAIL_POSTCONF` (environment only): the one `postconf` the
  generator asks ([the map generator][map-generator]).

Back to [configuration][].

[configuration]: configuration.md
[file-drop]: file-drop.md
[index-at-delivery]: #index-at-delivery-opt-in
[map-generator]: #the-map-generator-vmailbox-gen
[notmuch-settings]: notmuch-settings.md
[settings]: settings.md
[setup]: #setup-once-with-sudo
