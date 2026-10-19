# A seat with no Claude session: `message seat`

A bot or a desk adapter that sends as its own seat (`bot.alice`) needs a
signing key and an ssh-agent to hold it. Two verbs of `message` set that up.
The `seat` noun is **hidden**: it is not on `message --help` and is not an
MCP tool (one mints an identity and the other replaces its own process),
so this page and `message seat --help` are where you find it.

```sh
message seat init --registry DIR ADDRESS   # mint the key + birth cert into DIR
message seat agent ADDRESS [SOCKET]        # the seat's ssh-agent, for launchd
message seat agent --template              # print the LaunchAgent template
```

- `init` has no default `--registry`, so it never touches a live registry
  by accident, and it never blesses the seat: it prints the bless command.
- `agent` becomes the packaged `seat-agent` script (an exec), so launchd's
  TERM and KeepAlive act on it directly. It never runs `launchctl`.
- `--template` prints the plist byte for byte; you render and load it.
  Its ProgramArguments are `@MESSAGE@ seat agent @ADDRESS@ @SOCKET@`.

Details, refusals and the adapter's environment:
[`scripts/seat-agent/README.md`][seat-agent].

## The trust registry

`message verify --trust` and `memory run` read a registry: the directory
`SESSION_REGISTRY` names, or the `"registry"` key in
`~/.sessionkit/mail/settings/mail.json` (the variable wins). A registry is a git work
tree holding a committed `allowed_signers` file and a `certs/` directory,
with one `certs/<sid>.cert` (and its `.cert.sig`) per signer. sessionmail
does not create a registry today, and `init` refuses one that is not
seeded: `allowed_signers` must already hold at least one
`<sid>@session namespaces="..." <public key>` line, whose `namespaces=`
`init` copies onto each seat it adds. To build one by hand, `git init` a
directory, write the seed line into `allowed_signers`, commit it, and point
`SESSION_REGISTRY` at the directory; then `init --registry DIR` can add
seats to it. A seed line looks like this (every value here is made up):

```text
00000000-0000-0000-0000-000000000001@session namespaces="session-mail@example.com" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE
```

[seat-agent]: ../scripts/seat-agent/README.md
