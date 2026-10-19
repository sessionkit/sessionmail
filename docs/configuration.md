# configuration

The files you set up to configure sessionmail, one component at a time.
Same-host mail needs no postfix at all: the [file drop][file-drop].

## Postfix

The whole setup, and why: [postfix configuration][postfix-configuration].

- `/etc/postfix/main.cf`: root's. The `virtual_*` lines name the mail root,
  the domains, the uid and the two maps below.
- `/etc/postfix/master.cf`: root's. Edited only for
  [index at delivery][index-at-delivery], the opt-in `nminsert` service.
- `/usr/local/etc/postfix-agents/vmailbox` and `vmailbox.db`: the mailbox
  map, written by the generator as you.
- `/usr/local/etc/postfix-agents/valias` and `valias.db`: the alias map, the
  doorbell, written by the generator as you.
- `/etc/postfix/virtual_aliases` and `/etc/postfix/virtual_mailboxes`: on
  hosts that have them, the host's own tables, root's and kept by hand.
  sessionmail never writes them.
- `<mail root>/.postfix/valias.static`: standing aliases you keep by hand,
  folded into `valias` at each write.

Its settings, keys in `mail.json` ([settings][settings]); each one's
variable, when set and not empty, wins over the file:

- `sendmail` (`SESSIONMAIL_SENDMAIL`): `"drop"`, the default, is the file
  drop; postfix is opt-in, by its sendmail's path, `/usr/sbin/sendmail`.
- `postfix.generator` (`SESSIONMAIL_VMAILBOX_GEN`): the map generator to run.
- `postfix.relay_domain` (`SESSIONMAIL_RELAY_DOMAIN`): where the pre-flight
  relays a session-shaped address it cannot find, default `relay.home`.
- `postfix.origin` (`SESSIONMAIL_ORIGIN`): the domain for a bare address,
  read instead of postfix's `myorigin`.
- `magicdns_suffix` (`MAGICDNS_SUFFIX`): the tailnet suffix the generator
  writes keys for; unset, it asks tailscale.
- `SESSIONMAIL_POSTCONF`, environment only: the one `postconf` the
  generator asks, when set.

Detail and defaults: [postfix's settings][postfix-switches], and
`SESSIONMAIL_POSTCONF` under [the map generator][map-generator].

## notmuch

What it is for: [notmuch][]. Its config and hook: [notmuch
settings][notmuch-settings].

- `~/.config/notmuch/sessionkit/config` (`$XDG_CONFIG_HOME/notmuch/sessionkit/config`):
  the sessionkit profile, the one notmuch config sessionmail reads.
  `message setup` writes it. Your own `~/.notmuch-config` is never read.
- `~/.notmuch-config.<store>`: a store's profile, in store mode only.
- `post-new` and `post-insert` in the profile's `database.hook_dir`
  (`~/.mail/.notmuch/hooks` by default): links to the packaged post-new
  hook.

Its settings, the `notmuch` keys in `mail.json` ([settings][settings]); each
one's variable, when set and not empty, wins over the file:

- `notmuch.profile` (`SESSIONMAIL_NOTMUCH_PROFILE`): the profile, default
  `sessionkit`.
- `notmuch.command` (`SESSIONMAIL_NOTMUCH`): the notmuch binary.
- `notmuch.timeout` (`SESSIONMAIL_NOTMUCH_TIMEOUT`): seconds per call for
  `message mark`, default 10.
- `SESSIONMAIL_INDEX`, environment only: `host` (the default) or `store`.
- `NOTMUCH_PROFILE`, notmuch's own: in store mode only, the store's
  profile. An inherited `NOTMUCH_CONFIG` never reaches a notmuch sessionmail runs.

Detail and defaults: [notmuch's settings][notmuch-switches].

## Settings

sessionmail's own settings, the keys of `mail.json`: [settings][settings].
The host file is `~/.sessionkit/mail/settings/mail.json`; a seat and a session can
each have one too. No variable is read as a settings layer: a key's
variable, when set, wins over every layer.

[file-drop]: file-drop.md
[index-at-delivery]: postfix-configuration.md#index-at-delivery-opt-in
[map-generator]: postfix-configuration.md#the-map-generator-vmailbox-gen
[notmuch]: notmuch.md
[notmuch-settings]: notmuch-settings.md
[notmuch-switches]: notmuch-settings.md#settings
[postfix-configuration]: postfix-configuration.md
[postfix-switches]: postfix-configuration.md#settings
[settings]: settings.md
