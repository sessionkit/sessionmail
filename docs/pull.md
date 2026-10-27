# Pulling a mail host's boxes: `python -m sessionmail._puller`

Some setups have more than one host. Each one delivers its own mail locally
and sends one copy up to a shared **mail host**, an IMAP server. Mail that
reached the mail host another way, from a seat on the mail host itself or
from another host's copy, has to come back down. The puller brings it down.
It watches the mail host's boxes over IMAP and hands each new mail to this
host's postfix, which delivers it the same way as local mail.

It needs postfix on this host, and its installer is macOS only (a
LaunchAgent). It is opt-in: nothing runs it until you install it with
`message setup --pull`.

```sh
python -m sessionmail._puller --once --dry-run   # look; deliver nothing
message setup --pull --dry-run                   # print the plan
message setup --pull                             # install the LaunchAgent
```

## What it does

- **One thread per box**, each waiting in IMAP IDLE. It reissues the IDLE
  every `pull.reissue_seconds` and fetches each time, so a missed push is
  still swept up.
- **Read only.** Every box is opened with `EXAMINE` and every body is
  fetched with `BODY.PEEK`. Nothing on the mail host is marked, moved or
  deleted.
- **Delivery by SMTP to `pull.listener`**, as `BOX@<local domain>`. This
  host's own routing then decides where the mail lands: aliases,
  subscriptions and nested boxes all apply. Each delivered mail gets one
  added header, `X-Mailhost-Pulled`, naming the box, the IMAP UID and this
  host.
- **Two skips.** Each is decided before the mail is handed on:
  - *Already here*: the Message-ID is in the local box. notmuch is asked
    first, through the kit's own profile (the `notmuch.*` keys). The box's
    last 15 minutes are then scanned for mail notmuch has not indexed yet.
  - *Our own leg*: the mail host's top `Received` header names one of this
    host's IPs and a `BOX@<pull.relay_domain>` recipient. That makes it the
    copy this host sent up. It is skipped only if the local copy is there,
    after a short grace, so mail that bounced here is still pulled. An own
    leg for a box with no maildir of its own (an alias) cannot be checked,
    and is skipped.
- **A cursor per box**, `UIDVALIDITY UID`, under `pull.state_dir`. After a
  restart the puller carries on from the cursor. A box's first run starts
  at its oldest mail from the last `pull.backfill_seconds`. The dedup makes
  that safe. `--from-start BOX` takes a box's whole history instead.
- **General delivery** (`pull.unsorted_box`, off when unset). The mail host
  may file mail for boxes it does not have in one catch-all box. Every
  puller watches that box, and takes only mail for a *session* box (a UUID,
  or 8 hex digits, read from `X-Original-To`) that this host's postfix map
  (`pull.vmailbox`) holds. Session ids are unique, so two hosts never take
  the same mail. Any other name in that box is left alone.
- **The audit** (`pull.audit_box`, off when unset). The mail host may write
  digests of mail that no puller took. Each host checks the entries it sent
  for a local copy, and for any with none it sends an alert to
  `pull.audit_to`, through `pull.audit_sendmail`. That is a sendmail-style
  program, not the kit's own carrier, so a host that only uses the [file
  drop][file-drop] needs an MTA at that path or the audit left off. The
  alert carries an `X-Mailhost-Audit` header. The general-delivery and
  audit boxes start at the newest mail on their first run, so an old digest
  does not alert again.
- **A login gate.** At most `pull.login_gate` boxes log in at once, so a
  restart with many boxes does not flood the server.

## Before you install it

- **A local-only listener.** `pull.listener` is the `HOST:PORT` of an smtpd
  on this host (a postfix `master.cf` service on the loopback address, for
  example). That smtpd must not send any copy of what it receives back up
  to the mail host: no `always_bcc`, no relay of the pulled mail.
  Otherwise every pulled mail goes up again and comes back down again.
  Setting that service up is yours to do; `message setup` never edits
  postfix. See [postfix configuration][postfix-configuration].
- **A way to get the secret.** `pull.secret_command` is run by the shell
  and must print the IMAP secret on stdout. While it prints nothing, the
  puller says so once and looks again every 60 seconds. One form that never
  dials out on its own, only reusing an ssh control master that is already
  up:

  ```sh
  ssh -o ProxyCommand=false -o BatchMode=yes <mailhost> cat <path>
  ```

  Any password store's command works the same way.
- **This host's IPs.** The own-leg check needs them. Set `pull.own_ips`,
  or leave it unset and the puller asks `tailscale ip` when it starts.
  When it knows none, it refuses (exit 2) rather than pull this host's own
  mail back down.
- **A box list.** `pull.boxes_file` holds one box per line, and `#` starts
  a comment. The puller reads the file again when it changes. It starts a
  thread for each new box and stops the thread of each box that was
  removed. With no box listed and no `pull.unsorted_box`, there is nothing
  to pull: the puller exits 1, and `message setup --pull` refuses.
- **One puller per host.** The lock below guards one state dir. A second
  puller job on the same host, under another label or another state dir,
  would deliver everything twice. The dedup softens that but does not
  prevent it. If this host already runs a puller of its own, boot it out
  first. That step is yours: setup never touches a job it did not write.

## Config

Every value that names a host, a login, a secret or a box comes from config.
None has a built-in default. The keys are the `pull` map of
`~/.sessionkit/mail/settings/mail.json`. Each one's variable is
`SESSIONMAIL_PULL_` and the key in capitals, and a variable that is set and
not empty wins. Full rules: [settings][settings].

| key | required | default | what it is |
| --- | --- | --- | --- |
| `imap_host` | yes | none | the mail host's IMAP server |
| `imap_port` | no | `993` | its IMAPS port |
| `login` | yes | none | the login for a box, with `{box}` in it once (`{box}*admin`, `{box}@example.org`) |
| `secret_command` | yes | none | a shell command that prints the secret |
| `relay_domain` | yes | none | the domain this host relays its copy up to, for the own-leg check |
| `listener` | yes | none | `HOST:PORT` of the local-only smtpd above |
| `vmailbox` | yes | none | the postfix map that resolves `BOX@<local domain>` (`type:path`) |
| `boxes_file` | no | `~/.sessionkit/mail/settings/pull.boxes` | the box list |
| `state_dir` | no | `~/.sessionmail/puller` | cursors and the lock (not the job's log) |
| `local_domain` | no | the `host` key, else the short host name | the domain in `BOX@<local domain>` |
| `own_ips` | no | ask `tailscale ip` | this host's IPs, a list or comma-separated |
| `unsorted_box` | no | none: general delivery off | the mail host's catch-all box |
| `audit_box` | no | none: the audit off | the mail host's digest box |
| `audit_to` | when `audit_box` is set | none | who the audit alert goes to |
| `audit_sendmail` | no | `/usr/sbin/sendmail` | the program that sends the alert |
| `label` | no | `dev.sessionkit.mail.puller` | the LaunchAgent's label |
| `backfill_seconds` | no | `86400` | how far back a box's first run reaches (`0`: only new mail) |
| `reissue_seconds` | no | `300` | how long one IDLE lasts |
| `login_gate` | no | `4` | how many boxes may log in at once |

In these paths, `~` is your home in the password database. It does not
follow `$HOME` or `SESSIONMAIL_HOME`, so a hand run and the job find the same
state dir. The mail root is the kit's `root` key, and the notmuch call uses
the `notmuch.*` keys. The puller adds no key of its own for either.

With a required key unset, the puller and `message setup --pull` both
refuse, and name each missing key with its variable:

```text
REFUSED - pull is not configured: pull.imap_host (SESSIONMAIL_PULL_IMAP_HOST), ...
```

A value that is set but unusable, such as a `login` with no `{box}` or a
`listener` that is not `HOST:PORT`, is refused the same way, with the reason.

## Installing it: `message setup --pull`

`--pull` adds one step, `pull-agent`, to the end of the
[`message setup`][message-setup] plan. Without `--pull`, setup has no pull
step and does not read the pull config at all.

- **The job reads the settings file, not your shell.** The plist carries
  only `PATH` and `HOME`, never a config value or a secret. So setup works
  the config out twice: once from your shell, and once from the job's
  environment exactly. It refuses (exit 2) when a required key is set only
  by a variable, or when a variable gives a value that the file does not.
  Each refusal names the key and its variable. Write the key into
  `mail.json`, or unset the variable.
- `launchctl setenv` is not checked. A value set there reaches every job
  in your login session, this one too. Do not set a `SESSIONMAIL_*`
  variable that way.
- **The plist** is `~/Library/LaunchAgents/<pull.label>.plist`. It runs
  `<python> -u -m sessionmail._puller`, where `<python>` is the interpreter
  that ran `message setup`. It is started at load and kept alive, with 30
  seconds between restarts. Its output goes to
  `~/.sessionkit/mail/log/puller.log` (see "The log", below). Its `PATH`
  holds the usual install prefixes (`/opt/homebrew/bin`, `/usr/local/bin`)
  and the system ones, so `postmap`, notmuch, `tailscale` and your secret
  command's tools are found.
- **The check only reads.** The step is `DONE` when the plist on disk is
  exactly what setup would write *and* the job is loaded. A second run on
  an unchanged host writes nothing and calls no `launchctl` write.
- **`BLOCKED`, with nothing written,** when:
  - this is not macOS;
  - a file at the plist's path is not a sessionmail puller's plist (it runs
    something else, or it cannot be parsed), or a job is loaded under the
    label with no plist there. Pick another `pull.label`, or remove the
    other job by hand. Setup replaces only its own puller's plist;
  - nothing answers on `pull.listener`. This is asked only when the step
    has something to do.
- **Applying:** setup makes `~/.sessionkit/mail/log` if it is missing
  (mode 0700), writes the plist to a temporary file beside the target, and
  runs `plutil -lint` on it. If the lint fails, nothing is installed.
  Otherwise the file is moved into place.
  A loaded job is booted out, and setup waits for it to go, up to a bound.
  Then the new plist is bootstrapped and must show as loaded. A job that
  does not go, or a bootstrap that fails, leaves the step `FAILED` (exit 1).
  The message names the plist left in place and what to re-run.
- After you change `pull.label`, run `message setup --pull` again: the
  changed plist is rewritten and the job restarted. Any other key, including
  `pull.state_dir`, is not in the plist, so the step stays `DONE` and the
  job reads the new value when it next starts. Restart it with
  `launchctl kickstart -k gui/$(id -u)/<pull.label>`.

### The log

The job's stdout and stderr go to one file,
`~/.sessionkit/mail/log/puller.log`, beside the kit's settings in `~/.sessionkit/mail/settings/`. It does not
follow `pull.state_dir`: that folder holds state (cursors and the lock), and a
log is not state. Setup makes the log folder mode 0700 when it is missing,
because the log names boxes. A folder that is already there keeps its mode.

Before 0.3.2 the job logged to `<pull.state_dir>/log/puller.log`. **A puller
installed before 0.3.2 keeps writing that old path until its plist is
rendered again:** run `message setup --pull`. The step sees our puller's plist
with the old log path, replaces it, and restarts the job. The old log is left
where it is; nothing deletes or moves a log, so remove it by hand when you no
longer want it.

## Running it by hand

```sh
python -m sessionmail._puller --once --dry-run    # classify and log only
python -m sessionmail._puller --once              # deliver what waits, then exit
python -m sessionmail._puller --advance-cursors   # skip every box to now
```

- `--dry-run` connects and logs what it would do for each mail. It
  delivers nothing, moves no cursor and takes no lock, so it can run beside
  the job.
- `--once` delivers what waits past each cursor, then exits. A run without
  it keeps going, as the job does.
- `--boxes FILE` reads another box list. `--from-start BOX` (repeatable)
  starts a box that has no cursor at its first mail.
- `--advance-cursors` sets every box's cursor to the newest mail and exits.
  Run it with the job stopped, after a bulk import into the mail host's
  boxes, so the import is not pulled.
- Use the interpreter the kit is installed into, the one in the plist's
  `ProgramArguments`.

**Exit:** 0 done (`--once`, `--advance-cursors`) · 1 no box to pull · 2
refused before anything was delivered (config not usable, or no own IP
known) · 3 another puller holds the state dir's lock.

## Limits

- **One puller per state dir.** A lock file in `pull.state_dir` makes a
  second puller exit 3 before it connects. Two pullers sharing the cursors
  would deliver every mail twice. The lock cannot see a puller using
  another state dir.
- **One IMAP connection per box.** A server caps how many connections it
  takes, so a long box list can reach that cap.
- **A mail whose recipient this host's postfix refuses** is logged as
  `REFUSED here`, counted and passed over. Any
  other SMTP or IMAP error drops the box's connection without moving its
  cursor, and the box reconnects and tries again.
- **A refusal inside the job repeats.** If the config breaks, the own IPs
  are not known, or the lock is held, the job exits and launchd restarts it
  every 30 seconds. Each restart writes the refusal to the log again. Setup
  checks the config and the listener, but not the own IPs or the secret,
  because those depend on what is live when the job starts.
- **No `message` verb.** The puller is a module run with `python -m`. It
  has no MCP tool and no page on `message --help`.

[file-drop]: file-drop.md
[message-setup]: message-setup.md
[postfix-configuration]: postfix-configuration.md
[settings]: settings.md
