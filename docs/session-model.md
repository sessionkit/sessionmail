# The writing model: `Session-Model`

Every mail `message send` writes names the model that wrote it, when the
harness can say which:

```text
X-Session-From: 0123abcd
Session-Model: claude-opus-5-5
X-Machine: hq
```

The MCP `message_send` tool, the pager's notes and every other door that
sends through the package's sender stamp it too. A mail read later, by anyone,
says which model wrote it, not only which session did.

## It is signed

`Session-Model` is written before the signature is made, so a v2 signature
covers it like `X-Session-From` and `X-Machine`. `message verify` then
refuses a mail when:

- the header was changed after signing (`signature-crypto: BAD`);
- the header was removed after signing (`BAD`);
- the header was added to a mail that was signed without one
  (`BELOW FLOOR: h= lacks session-model`).

A mail with no `Session-Model` verifies exactly as before. That covers old
mail, a human's mail, and a seat whose model is unknown. No new signature
version was needed. A v1 signature covers only its five headers, so on a v1
mail, as with any other header, `Session-Model` is not covered.

## Where the id comes from

The id is captured from the harness. Nobody types it. `message send` has no
flag for it, the MCP tool has no argument for it, and
`--header 'Session-Model: ...'` is refused, as a structured caller's
`headers=` is.

It is looked for in this order. The first hit wins:

1. **Claude Code.** `CLAUDE_CODE_SESSION_ID` names the session's transcript,
   `<config>/projects/*/<session-id>.jsonl`. `<config>` is
   `$CLAUDE_CONFIG_DIR`, else `~/.claude`. Every project directory is
   searched, because the directory is named for the cwd the session was
   launched in, not the one it sends from. The id is the `message.model` of
   the transcript's last assistant turn, such as `claude-opus-5-5`. That is
   the exact id Claude Code recorded. The `--model opus` alias the seat was
   launched with is never used. A turn Claude Code made up itself
   (`<synthetic>`) and a subagent's own turn (`isSidechain`) are skipped.
   Only the end of the file is read, starting with its last 256 KiB.
2. **Another harness**, such as a local model standing in when Claude is
   offline: `SESSIONMAIL_MODEL`. The harness sets it in the agent's
   environment when it launches the agent, for example
   `SESSIONMAIL_MODEL=gemma-3-27b-it`. It is taken as written, less
   surrounding white space, and must be one id: a letter or digit, then
   letters, digits and `. _ : / @ + -`, 128 characters at most. Set the
   exact id the harness is running, never an alias.
3. **Neither:** no header. The send never fails for want of a model.

If a value cannot be read, or is not shaped like an id, it is treated as
no value. Nothing else is written in its place: no `unknown`, no guess.

### `SESSIONMAIL_MODEL` is the harness's, not the agent's

Only the harness that launches the agent may set `SESSIONMAIL_MODEL`. An
agent's prompt must never tell it to export one. sessionmail cannot stop a
shell the agent runs from setting it, so the value is the harness's word,
signed by the seat's key. It is not proof of what ran. On a Claude Code
seat the transcript answers first, so the variable is not read at all when
the transcript names a model.

## Known limit: a subagent shares its parent's session

A Claude Code subagent runs with its parent's `CLAUDE_CODE_SESSION_ID`. Mail
it sends is stamped with the model of the parent transcript's latest main
turn, which may not be the subagent's own model. This is not solved yet.
