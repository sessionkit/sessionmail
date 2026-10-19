# Not running under Claude Code?

`pager` learns whether you are busy from Claude Code's own per-session
sidecar (`~/.claude/sessions/<pid>.json`), and failing that from `session
get idle`. A client with neither - a bare shell, your own script, a bot -
never looks idle, so waits written for an agent that is mid-tool-call apply
to it:

- a page waits the running page age (60 s) instead of ringing on arrival;
- and if your client names a seat (`CLAUDE_CODE_AGENT`), a second message
  from the same address inside the coalescing window (300 s) does not ring
  again - a running agent would have scooped it on its next tool call.

Nothing is lost; it is late, or it waits for your next `pager listen`. Turn
both waits off in that client's environment:

```sh
export SESSIONMAIL_PAGE_AGE_SECONDS=0   # ring on arrival
export SESSIONMAIL_BURST_SECONDS=0      # ring for every message
```

`pager listen` says the same thing in its banner when nothing can tell it
whether you are busy.

How the listener decides: [how `pager listen` works][internals].

[internals]: pager-listen-internals.md
