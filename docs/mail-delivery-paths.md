# Where mail comes from

sessionmail reads and writes maildirs; it does not require an MTA. On a
host with postfix, mail arrives the ordinary way. Without one, anything
that can write a maildir is a delivery agent - your own MTA, a script, or
a chat bridge that drops each message into `new/` (one door in, an outbox
out). The pager watches the directory, whatever filled it.

- **postfix**, the ordinary way: [postfix configuration][postfix-configuration].
- **No postfix, same host:** the [file drop][file-drop] has `message send`
  write each copy straight into the recipient's maildir.

[postfix-configuration]: postfix-configuration.md
[file-drop]: file-drop.md
