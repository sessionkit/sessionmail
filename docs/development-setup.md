# Development

```sh
make install     # uv tool install -e . - editable, so an edit is live
make             # check, then test
make test-latency  # the wall-clock tests, alone, on a quiet host
make progress    # how much of the CLI is built, counted by running it
```

`make test` leaves out the tests marked `latency`. Each one bounds how soon
the pager pages, and machine load can push it past that bound with nothing
wrong in the code. Each has a twin in the default run that checks the same
behaviour by order of events instead. Run `make test-latency` by itself on a
quiet machine. Naming one by its id runs it too:
`uv run pytest tests/test_pager_listen.py::<name>_latency`.

The specs are the record of what this package is meant to do: read them
before you design or build. They live in `openspec/` in the git repository;
the release archive leaves them out. `openspec/specs/` is ratified,
`openspec/changes/` is proposed and not ratified yet.
