# notmuch

notmuch is a mail indexer. It reads maildirs into a search index and answers
queries over it. sessionmail uses it as its one search engine, and you need
it for marking and memory, not for hearing or sending mail.

- **What it is:** an outside program (`notmuch`), not part of sessionmail.
  The ratified spec says search SHALL be notmuch, with no second index
  (`openspec/specs/pager/spec.md:270-272 @ c854322`).
- **Hearing mail does not need it.** `pager listen` watches the box's `new/`
  directory itself (`src/sessionmail/_detect.py:1-2 @ c854322`,
  `src/sessionmail/pager.py:2595 @ c854322`).
- **Sending does not need it.** The send uses notmuch to find a reply's parent
  faster, and walks the maildir when notmuch is missing, fails or is slow
  (`src/sessionmail/_sender.py:751-762 @ c854322`).
- **What needs it:**
  - `message mark` checks that each id exists through notmuch
    (`src/sessionmail/message.py:1080 @ c854322`).
  - Every `memory` verb searches it: `ls`, `tree` and `cat` through the
    `mhs-thread` reader, and `run` and `wake` directly
    (`src/sessionmail/mhs/session-mail-ls:7 @ c854322`, the same line in
    `session-mail-tree` and `session-mail-cat`,
    `src/sessionmail/mhs/mhs-thread:67-78 @ c854322`,
    `src/sessionmail/mhs/session-mail-run:111-113 @ c854322`,
    `src/sessionmail/mhs/session-mail-wake:462 @ c854322`). `message
    verify` reads no index (`src/sessionmail/_mhs.py:53-56 @ c854322`).
  - The post-new hook tags `+agent` and `+memory`, and rebuilds `superseded`
    from each message's `Supersedes:` header
    (`src/sessionmail/notmuch/post-new:7-13 @ c854322`). It can also run
    [one command of yours][post-new-command] after each pass.
- **sessionmail never indexes the host index.** Nothing in it runs
  `notmuch new` there. The host runs it on a schedule (the hook notes a
  launchd agent, `src/sessionmail/notmuch/post-new:15-17 @ c854322`). The one
  exception is a store's own index: the operator tool `store-index`
  (`build`, `update`) runs `notmuch new --no-hooks` on it
  (`src/sessionmail/notmuch/store-index:24-46 @ c854322`).
- **An insert host indexes at delivery.** A host that opts in to
  [index at delivery][index-at-delivery] has postfix pipe each message into
  `notmuch insert` (`src/sessionmail/postfix/nm-deliver @ adc95b6`), so mail
  is searchable as it lands. Its scheduled `notmuch new` becomes a safety
  net, still needed for the file drop's mail and flag renames, and run
  minutes apart: insert waits while `new` holds the write lock.
- **Which index:** the host's by default. `SESSIONMAIL_INDEX=store` reads one
  store's own index instead (`src/sessionmail/_index.py:5-18 @ c854322`).
- **Setup and config:** [notmuch-settings][].

[notmuch-settings]: notmuch-settings.md
[post-new-command]: post-new-command.md
[index-at-delivery]: postfix-configuration.md#index-at-delivery-opt-in
