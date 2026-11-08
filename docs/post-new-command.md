# post-new command

The notmuch [post-new hook][post-new-hook] can run one command of yours
after its tagging, for anything that should follow new mail (a dashboard
redraw, say). It is off unless a notmuch config key names it:

```sh
notmuch config set sessionmail.post_new_command /abs/path/to/command   # on
notmuch config set sessionmail.post_new_command                        # off
```

The value is an absolute path to an executable, run with no arguments and no
shell; a command that needs arguments gets a wrapper script. It never blocks
or fails indexing: the hook spawns a detached runner and exits 0 at once.
Runs are coalesced, one at a time: mail arriving during a run gives exactly
one more run. The command gets cwd `/`, its own process group, stdin
`/dev/null`, and only `HOME`, `PATH=/usr/bin:/bin:/usr/sbin:/sbin`,
`NOTMUCH_CONFIG` and `SESSIONMAIL_IN_POST_NEW_COMMAND=1`; it is killed after
300 s. A `notmuch new` it runs itself is tagged as usual and does not run it
again (a command that clears its environment must pass `--no-hooks`
instead). It must not send mail on every run: sent mail comes back as an
arrival. Its output and a `start`/`end` line per run go to `log` (rolled
to `log.1` past 1 MiB) in the hook's state folder, beside its `lock` and
`again` files. With the key unset, nothing is created and nothing is
spawned.

The state folder is `~/.sessionkit/mail/state/post-new/`, made with its
parents on the first run. On an existing host where
`~/.sessionmail/post-new/` is still a real folder of its own, that one is
used instead (a link from it to the new folder hands over). Only the new
folder is ever made. When both are real folders the old one is used and
the new one is left alone. `HOME` alone decides it, never `TMPDIR` or
`SESSIONMAIL_HOME`: notmuch runs the hook from launchd, postfix and agents
alike, and all of them must take the same lock.

[post-new-hook]: notmuch-settings.md#the-post-new-hook
