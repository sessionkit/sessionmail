# file drop

The file drop delivers mail on a host with no postfix: `message send` writes
each copy straight into the recipient's maildir. The code calls it the
**mail drop**. It is **the default**: with no `sendmail` key and no
`SESSIONMAIL_SENDMAIL`, a send uses it, and no mail server is needed.

- **What it replaces SMTP for:** the hand-off from `message send` to
  postfix, for mail between boxes on the same host. Each recipient is
  resolved with the same maps postfix uses, so switching to postfix later
  changes nothing about where mail lands
  (`src/sessionmail/_drop.py:25-35 @ c854322`).
- **What it does not replace:** mail to another host (refused by name),
  queueing and retry, and postfix's header rewriting
  (`src/sessionmail/_drop.py:46-49 @ c854322`).
- **Receiving needs neither:** the pager watches the box's `new/` itself
  (`src/sessionmail/_drop.py:5-8 @ c854322`).

## How it behaves (measured in a scratch mail root)

- Delivered into `agents/alice/new/`, exit 0.
- A recipient with no box: refused, nothing delivered, exit 67 (`message
  send` exits 3). The drop never makes a box, and one miss stops every copy
  (`src/sessionmail/_drop.py:40-44 @ c854322`;
  `src/sessionmail/_sender.py:1086-1094 @ a714df9`).
- **The first boxes** come from [`message setup`][message-setup]: the
  operator's box and each `--box NAME`
  (`src/sessionmail/_setup.py:442-467 @ a714df9`). The drop itself still
  never makes one.
- The generator does not trust its live-session list (no sidecar
  directory, no `jq`, `ps` failing, or, run inside a session, a list that
  does not show that session): the alias map is skipped with a warning.
  Mail then lands in the seat's own box only, and no session box gets its
  doorbell copy
  (`src/sessionmail/postfix/vmailbox-gen:201-209 @ c854322`,
  `src/sessionmail/_drop.py:184-191 @ c854322`).

## With no MagicDNS suffix

- **A host with no tailnet delivers.** No `MAGICDNS_SUFFIX` and no
  tailscale is no longer a refusal. A generator that lists `no-fqdn-v1`
  in `--capabilities` is always asked with `--no-fqdn`. Then it prints the
  short-host rows only and asks for no suffix. The drop reads no other rows:
  every address on this host is looked up as `local@<short host>`
  (`src/sessionmail/_drop.py:204-224 @ a714df9`).
- The packaged generator lists the token
  (`src/sessionmail/postfix/vmailbox-gen:102-113 @ a714df9`).
- **An older generator**, one without the token, is run as before. On a
  host with no suffix it still exits 4, and the drop refuses with exit 69,
  naming the generator that predates `no-fqdn-v1`
  (`src/sessionmail/_drop.py:219-223 @ a714df9`).
- `MAGICDNS_SUFFIX` is still needed on a **postfix** host. Its maps are not
  rebuilt without one.

## Settings

Each is a key in `mail.json` ([settings][settings]); its variable, when set
and not empty, wins over the file.

- `sendmail`: `"drop"` (the default) selects the drop; an absolute path
  selects a mail server instead. Variable: `SESSIONMAIL_SENDMAIL`.
- `root`: the mail root, default `~/.mail`. Variable: `SESSIONMAIL_ROOT`,
  which names the agents folder, so its parent is the mail root (the older
  `SESSION_MAIL_ROOT` is read the same way).
- `host`: this host's short name, default the short host name. Variable:
  `SESSIONMAIL_HOST` (or the older `SESSION_MAIL_HOST`).
- `postfix.generator`: the map generator, found by one shared lookup
  (`src/sessionmail/_generator.py:10-30 @ a714df9`):
  - The key, or its variable `SESSIONMAIL_VMAILBOX_GEN`, if set, is the
    **only** candidate. If it
    names a file that is not there, the drop **refuses**, exit 69, and
    `message send` exits 3. There is no fall-through to the copies below
    (`src/sessionmail/_drop.py:149-160 @ a714df9`).
  - Otherwise: `vmailbox-gen` on `PATH`, then `<mail root>/.postfix/`,
    then the copy in the package, LAST.
  - The packaged copy is for the drop only, because the drop only runs the
    generator's print modes. The lookups that WRITE postfix's live maps
    (the map rebuild when a box is born, and `message box archive`) stop at
    `<mail root>/.postfix/`. They never run the packaged copy
    (`src/sessionmail/_seat.py:2477 @ a714df9`,
    `src/sessionmail/_archive.py:158 @ a714df9`).
  - On a host using the drop, a box birth runs no generator at all. There
    are no maps to keep, because the drop asks the generator on every send
    (`src/sessionmail/_seat.py:2473-2475 @ a714df9`).
- The generator's own settings, `magicdns_suffix` included:
  [postfix configuration][postfix-configuration].

[postfix-configuration]: postfix-configuration.md
[message-setup]: message-setup.md
[settings]: settings.md
