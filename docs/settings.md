# settings

sessionmail's own settings are JSON keys in a file named `mail.json`, the
settings of the mail tool (`message`, `pager` and `memory`). This page names
each key, its type and default, what it does, and which file it is read
from. How settings work across the kit: [sessionkit's docs/settings.md][kit-settings].

Not on this page: postfix's own files ([postfix configuration][postfix-configuration])
and notmuch's ([notmuch settings][notmuch-settings]). The keys sessionmail
reads for them are below: [sendmail][sendmail], [notmuch][notmuch-key] and
[postfix][postfix-key].

## The files

`mail.json` can sit in three places, lowest first:

| Layer   | File                                                  |
| ------- | ----------------------------------------------------- |
| host    | `~/.sessionkit/mail/settings/mail.json`               |
| seat    | `~/.session/seats/<seat>/settings/mail.json`          |
| session | `~/.session/sessions/<session id>/settings/mail.json` |

- Each `mail.json` may have a `mail.d/` directory beside it. Its `*.json`
  files are read after `mail.json`, in name order, and a later file wins
  within that layer. Not every key is read through them: see
  [the keys][the-keys].
- A layer file holds only the keys it sets.
- `<seat>` is a seat address: `<project>+<role>` or `<role>.<project>`. A
  folder whose name has neither a `+` nor a `.` (an agent's name, say) is
  not read as a seat layer. There is no agent layer.
- The host file is made the first time the plugin's PostToolBatch hook runs
  and finds it missing, or by `pager settings hooks on|off`. It starts as
  `{"version": 1, "hooks": false}` (or, on an older host, a copy of
  `~/.session/mail/settings.json`).
  When the hook makes it, on that run and only that run, you see one line
  naming the file: ``PostToolBatch says: sessionmail wrote <path> with its
  hooks off. To turn them on, run `pager settings hooks on`.`` The agent gets
  its own note.
- Beside the host file, sessionmail installs `mail.schema.json` (the schema,
  where the keys below come from) and `mail.template.json` (the template),
  and replaces each when a release changes it. Do not edit either.
  `pager settings install` puts them there before a first launch.
- Which home `~` is depends on the key, as [the keys][the-keys] table
  groups them:
  - Keys read from "the template and the host" (`sendmail`, `root`, `host`,
    `magicdns_suffix`, `notmuch`, `postfix`, `pull`, `registry` and
    `alert_runner`):
    `~` is the account's home in the password database, never `$HOME`. A
    changed `$HOME` cannot move them, and a `mail.json` under another
    `$HOME` is not read for them.
  - `hooks`, `monitor`, `pager_required` and `capture`, and the seat and
    session layers: `~` is `$HOME`.
  - Where `$HOME` is the account's home, as it usually is, both are the
    same folder.

### Upgrading from 0.1.0

0.1.0 kept the host files in `~/.settings/mail/`. Move the files in it to
`~/.sessionkit/mail/settings/`. Until you do, sessionmail writes nothing
in either folder: the hooks stay off and say why,
`pager settings install` exits 3, and `pager settings hooks on|off` is
refused (exit 2). Nothing is read from the old folder.

## The keys

Today each key has its own reader, and the readers do not all read the same
files:

| Key                 | Type             | Default            | Read from                         |
| ------------------- | ---------------- | ------------------ | --------------------------------- |
| `hooks`             | boolean          | `false` (off)      | the host `mail.json` only         |
| `sendmail`          | `"drop"` or a path | `"drop"`         | the template and the host         |
| `root`              | string           | `~/.mail`          | the template and the host         |
| `host`              | string           | the short host name | the template and the host        |
| `magicdns_suffix`   | string           | ask tailscale      | the template and the host         |
| `notmuch`           | map              | see below          | the template and the host         |
| `postfix`           | map              | see below          | the template and the host         |
| `pull`              | map              | see below          | the template and the host         |
| `monitor`           | boolean          | on                 | the host `mail.json` only         |
| `pager_required`    | boolean          | `false`            | host, seat and session            |
| `capture`           | boolean          | `false`            | host, seat and session            |
| `address`           | string           | none               | session only                      |
| `subscriptions`     | map              | none               | seat and session, each on its own |
| `registry`          | string           | none               | the template and the host         |
| `alert_runner`      | string           | none               | the template and the host         |
| `rules`             | map              | none               | not read                          |
| `version`           | integer          | `1`                | not read                          |
| `$schema`, `_about` | string           | none               | ignored                           |
| `_locked`           | list of strings  | none               | see [`_locked`][locked]           |

"Host, seat and session" means `mail.json` and `mail.d/` at each layer, and
the most specific layer that holds the key decides: session over seat over
host. "The template and the host" means `mail.template.json`, then the host's
`mail.json` and `mail.d/`. The template ships holding `sendmail` alone of
these keys.

Each of `sendmail`, `root`, `host`, `magicdns_suffix` and the keys under
`notmuch` and `postfix` has one variable. When that variable is set and not
empty, it wins over every layer; otherwise the file decides, and then the
default. A program started with none of the variables (a launchd job, say)
reads the file, so nothing needs copying into its environment.

### hooks

Whether the plugin's Stop, PostToolBatch and UserPromptSubmit hooks speak.

- Only the boolean `true` turns them on. `false` is the quiet off.
- A host file with no `hooks` key, or a value that is not a boolean (`"true"`,
  `1`), is off as well, and sessionmail reads it as a mistake in the file.
- Read from the host `mail.json` alone: not `mail.d/`, not a seat or session
  layer.
- `SESSIONMAIL_PAGER=off` silences the hooks whatever the file says.
- Turn them on or off with `pager settings hooks on|off`; `pager settings
  hooks` says which (exit 0 on, 3 off, 2 off by mistake). It changes that
  one value and keeps every other key and byte. It makes the file first if
  there is none, writes nothing when the file already says so, and refuses
  (exit 2) a file that is not a JSON object, a host whose old settings are
  not moved yet, and a run as root or under `sudo`.
- Each change it makes adds one JSON line to
  `~/.sessionkit/mail/log/settings.jsonl`: when, who (the seat, else your
  account), the session if there is one, from and to, and the file. The
  switch is the whole host's, so a run from your own shell is logged too.

```json
{"version": 1, "hooks": true}
```

### monitor

Whether the pager monitor starts: `pager listen --monitor`, which the plugin
runs for each interactive seat.

- Missing means on. Only the boolean `false` turns it off, for every seat on
  the host. Any other value is ignored, and `pager status` says so.
- Read from the host `mail.json` alone. `"monitor": false` in `mail.d/`, or
  in a seat or session layer, changes nothing.
- One seat turns it off for itself with `SESSIONMAIL_MONITOR=off` in its
  launch environment. That can never turn it back on over the host's
  `false`. `SESSIONMAIL_PAGER=off` turns it off too.
- More: [the pager monitor][pager-monitor].

### sendmail

How `message send` hands mail on. Variable: `SESSIONMAIL_SENDMAIL`.

- `"drop"`, the default: the [file drop][file-drop] writes each copy into
  this host's maildirs. No mail server is needed, and mail to another host
  is refused by name.
- An absolute path names a mail server's `sendmail` (postfix's is
  `/usr/sbin/sendmail`). This is how mail reaches other hosts; it is
  opt-in: [postfix configuration][postfix-configuration].
- Only those two forms: exactly `drop`, or a path starting with `/`. Any
  other value (`Drop`, `./sendmail`, `~/bin/sendmail`, an empty string,
  `true`) is refused by name, and nothing is sent.
- The postfix maps are written only when this names a mail server.

```json
{"version": 1, "sendmail": "/usr/sbin/sendmail"}
```

### root

The mail root: the folder that holds `agents/`. Default `~/.mail`; `~` is
the account's home. Variable: `SESSIONMAIL_ROOT`, which names the agents
folder itself, so its parent is the mail root. The older spelling,
`SESSION_MAIL_ROOT`, is still read, after it. Every reader (`message path`,
`list`, `read`, the pager, `memory wake`) and the file drop read the two in
that one order, so a send and a read never pick different folders.

### host

This host's short name, as mail addresses use it. Default: the short host
name. Variable: `SESSIONMAIL_HOST`.

### magicdns_suffix

The tailnet's MagicDNS suffix, such as `example.ts.net`, for a host where
tailscale cannot be asked. Default: ask tailscale. Variable:
`MAGICDNS_SUFFIX`. A host using the file drop needs none; a postfix host's
maps are not rebuilt without one.

### notmuch

How sessionmail runs notmuch. A map:

| Key       | Default                    | Variable                      |
| --------- | -------------------------- | ----------------------------- |
| `command` | `notmuch` on `PATH`        | `SESSIONMAIL_NOTMUCH`         |
| `timeout` | `10` (seconds per call)    | `SESSIONMAIL_NOTMUCH_TIMEOUT` |
| `profile` | `sessionkit`               | `SESSIONMAIL_NOTMUCH_PROFILE` |

- `profile` is the notmuch profile sessionmail's own calls read:
  `$XDG_CONFIG_HOME/notmuch/<profile>/config`. No other notmuch config is
  read, your own `~/.notmuch-config` included. `message setup` writes the
  `sessionkit` profile. notmuch's own `NOTMUCH_PROFILE` and
  `NOTMUCH_CONFIG` do not choose it. More: [notmuch settings][notmuch-settings].

```json
{"version": 1, "notmuch": {"command": "/opt/homebrew/bin/notmuch"}}
```

### postfix

The settings named for postfix. A map:

| Key            | Default                                          | Variable                   |
| -------------- | ------------------------------------------------ | -------------------------- |
| `generator`    | `PATH`, then `<root>/.postfix/`, then the package's copy | `SESSIONMAIL_VMAILBOX_GEN` |
| `relay_domain` | `relay.home`                                     | `SESSIONMAIL_RELAY_DOMAIN` |
| `origin`       | postfix's `myorigin`                             | `SESSIONMAIL_ORIGIN`       |

- `generator` is the map generator, `vmailbox-gen`. The file drop runs it
  too, to print where an address lands; it writes maps only for a mail
  server.
- `relay_domain` is where a session-shaped address that misses on this host
  is relayed. `origin` is the domain a bare address is completed with.

### pull

The host-to-host pull, `python -m sessionmail._puller`: a mail host's IMAP
boxes, delivered into this host's postfix. A map. Each key's variable is
`SESSIONMAIL_PULL_` and the key in capitals (`imap_host` is
`SESSIONMAIL_PULL_IMAP_HOST`); when set and not empty, it wins over every
layer.

| Key                | Default                                     |
| ------------------ | ------------------------------------------- |
| `imap_host`        | none: required                              |
| `imap_port`        | `993`                                       |
| `login`            | none: required, with `{box}` in it          |
| `secret_command`   | none: required                              |
| `relay_domain`     | none: required                              |
| `listener`         | none: required, `HOST:PORT`                 |
| `vmailbox`         | none: required, a postfix map (`type:path`) |
| `boxes_file`       | `~/.sessionkit/mail/settings/pull.boxes`    |
| `state_dir`        | `~/.sessionmail/puller`                     |
| `local_domain`     | the `host` key, else the short host name    |
| `own_ips`          | ask tailscale                               |
| `unsorted_box`     | none: general delivery off                  |
| `audit_box`        | none: the audit off                         |
| `audit_to`         | none: required when `audit_box` is set      |
| `audit_sendmail`   | `/usr/sbin/sendmail`                        |
| `label`            | `dev.sessionkit.mail.puller`                |
| `backfill_seconds` | `86400`                                     |
| `reissue_seconds`  | `300`                                       |
| `login_gate`       | `4`                                         |

- With a required key unset, the puller refuses (exit 2) and names each
  one with its variable.
- `state_dir` moves only when `pull.state_dir` is set: one puller runs per
  state dir, and a second exits 3.

### pager_required

Whether this seat's pager is required to be listening.

- When `true`, the plugin's hooks say DEAF, with the reason and the two ways
  out, when nothing listens for the session. They say it even with
  `"hooks": false`. A muted seat (`pager mute`) is not told.
- Missing everywhere means not required: the check runs and stays silent.
- Host, seat and session; only a boolean decides, and another value is
  ignored and said.
- `SESSIONMAIL_PAGER_REQUIRED` (`1`, `true`, `on` or `0`, `false`, `off`)
  wins over every layer, both ways. `SESSIONMAIL_PAGER=off` turns the check
  off.
- Without sessiontools' settings reader installed, only the host `mail.json`
  is read, and that is said.

### capture

Whether the full text of each injection is captured beside its size row.

- When `true`, every string the pager and its hooks put into the agent's
  context is written to a file under the session's log, until the session's
  capture reaches 64 MB. Missing means off: the rows (sizes and Message-IDs,
  never text) are written either way. More:
  [capture][pager-inject-log-capture].
- Host, seat and session; only a boolean decides.
- `SESSIONMAIL_CAPTURE` (`on`, `1`, `yes`, `true` or `off`, `0`, `no`,
  `false`) wins over every layer, both ways. Any other value is ignored,
  silently.

### address

The plus address this session listens for, as `<project>+<role>`. Absent:
the session's own box only.

- Session layer only. A host or seat `address` is not read.
- Not set by hand: `pager listen` writes it, lower case, into the session's
  `mail.d/70-live.json`. It takes it from `EMAIL` or `TM_AGENT_ADDRESS`
  when either holds a plus address with no domain or at this host, and
  removes it when neither does.
- The alias map generator reads it to write the session's row:
  [the map generator][map-generator].

### subscriptions

Boxes whose mail is also delivered here: a map of box name to `true`.

- Seat and session layers. Each layer's map makes its own rows; the seat's
  and the session's are never merged with each other. A session's map
  counts only while the session is live.
- Only `true` subscribes. `false` is the same as absent, so a later file in
  the same layer sets one `false` to remove it and keep the rest.
- A host `subscriptions` map is not read.
- Written by `message subscription add|remove`: a session's into its
  `mail.d/70-live.json`, a seat's (with `--seat`) into its `mail.json`.
- `SESSIONMAIL_SUBSCRIBE` (box names, commas or spaces) is copied once, at a
  session's birth, by the launcher into the session's `mail.d/50-env.json`.
  After that the file is what is read, and `70-live.json` sorts after it, so
  a box the verbs removed stays removed.

```json
{"subscriptions": {"<project>": true, "<project>+review": false}}
```

### registry

The seat registry directory (`allowed_signers`, `certs/`) that
`message verify --trust` and `memory run` read.

- The template and the host, under the account's home. A seat or session
  `registry` is not read.
- `~` is the account's home.
- `SESSION_REGISTRY` wins when it is set.
- Unset: `--trust` ends `registry-absent`, and says how to set it.

### alert_runner

The local part of this host's alert runner, such as `alerts`. Its urgent
mail, From `<alert_runner>@<this host>`, is honoured, so the pager's default
urgent list is the operator and this runner.

- The template and the host only. A seat or session layer is never read, so
  no agent can add a runner.
- Letters, digits, `.`, `_`, `+` and `-` only, read in lower case. Anything
  else (an `@`, white space, `$`, a glob character) means no runner.
- A `set urgent` line in `~/.sessionmail/rules.locked` replaces the whole
  default list, and this key is not read.

### rules

The schema describes pager rules as a map of name to rule line. sessionmail
does not read this key at any layer today. Pager rules are read from `rules`
files: `pager rule --help`.

### version

The file format's version, an integer of 1 or more. The template writes `1`;
nothing reads it today.

### `$schema` and `_about`

A layer file may name its schema, or carry a note for the person reading it.
The reader ignores both.

### `_locked`

Dotted key paths a file locks: every file below it is ignored for them
(`subscriptions.<box>` locks one box). The lock holds only where one merge
reads the files:

- inside one layer: `"_locked": ["capture"]` in the host `mail.json` keeps
  the host's `mail.d/` from changing `capture`;
- between the template and the host, for `registry` and `alert_runner`.

It does not hold across host, seat and session. `pager_required` and
`capture` read each layer on its own, and the most specific one decides,
lock or no lock. `hooks` and `monitor` read the host `mail.json` directly,
and a lock there changes nothing.

## Environment variables are not a layer

No variable is folded into the layers. A few keys have a variable that is
read beside them, each in its own way:

| Variable                     | Key                                   | What it does                                    |
| ---------------------------- | ------------------------------------- | ----------------------------------------------- |
| `SESSIONMAIL_PAGER_REQUIRED` | `pager_required`                      | wins over every layer, both ways                |
| `SESSIONMAIL_CAPTURE`        | `capture`                             | wins over every layer, both ways                |
| `SESSIONMAIL_MONITOR`        | `monitor`                             | `off` turns it off for one seat, never on       |
| `SESSIONMAIL_PAGER`          | `hooks`, `monitor`, `pager_required`  | `off` (or `0`, `no`, `false`) silences the hooks and the DEAF check, and stops the monitor |
| `SESSION_REGISTRY`           | `registry`                            | wins when set                                   |
| `SESSIONMAIL_SENDMAIL`, `SESSIONMAIL_ROOT`, `SESSIONMAIL_HOST`, `MAGICDNS_SUFFIX` | `sendmail`, `root`, `host`, `magicdns_suffix` | wins over every layer when set and not empty |
| `SESSIONMAIL_NOTMUCH`, `SESSIONMAIL_NOTMUCH_TIMEOUT`, `SESSIONMAIL_NOTMUCH_PROFILE` | `notmuch.command`, `.timeout`, `.profile` | wins over every layer when set and not empty |
| `SESSIONMAIL_VMAILBOX_GEN`, `SESSIONMAIL_RELAY_DOMAIN`, `SESSIONMAIL_ORIGIN` | `postfix.generator`, `.relay_domain`, `.origin` | wins over every layer when set and not empty |
| `SESSIONMAIL_PULL_*` (`SESSIONMAIL_PULL_IMAP_HOST`, ...) | `pull.*` (`pull.imap_host`, ...) | wins over every layer when set and not empty |
| `SESSIONMAIL_SUBSCRIBE`      | `subscriptions`                       | copied once, at birth, into a session file      |
| `EMAIL`, `TM_AGENT_ADDRESS`  | `address`                             | `pager listen` writes the key from it           |

## What a seat or a session can override

Partly, and per key:

- `pager_required` and `capture`: a seat or a session overrides the host.
- `subscriptions` and `address`: seat and session only; the host's are not
  read.
- `hooks`, `monitor`, `registry` and `alert_runner`: host only. A seat or a
  session cannot change them in a file; for `monitor`, a seat's variable can
  only turn it off.
- `sendmail`, `root`, `host`, `magicdns_suffix`, `notmuch`, `postfix` and
  `pull`: the template and the host only. A seat or session file cannot move them;
  each one's variable can.

[kit-settings]: https://github.com/sessionkit/sessionkit/blob/HEAD/docs/settings.md
[file-drop]: file-drop.md
[locked]: #_locked
[map-generator]: postfix-configuration.md#the-map-generator-vmailbox-gen
[notmuch-settings]: notmuch-settings.md
[notmuch-key]: #notmuch
[pager-inject-log-capture]: pager-inject-log.md#capture
[pager-monitor]: pager-monitor.md
[postfix-configuration]: postfix-configuration.md
[postfix-key]: #postfix
[sendmail]: #sendmail
[the-keys]: #the-keys
