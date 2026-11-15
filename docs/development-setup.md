# Development

```sh
make install
make
make test-latency
make progress
```

- `make install` runs `uv tool install -e .`: editable, so an edit is live.
- `make` checks, then tests.
- `make test-latency` runs the wall-clock tests, alone: run it on a quiet
  host.
- `make progress` counts how much of the CLI is built, by running it.

`make test` leaves out the tests marked `latency`. Each one bounds how soon
the pager pages, and machine load can push it past that bound with nothing
wrong in the code. Each has a twin in the default run that checks the same
behaviour by order of events instead. Run `make test-latency` by itself on a
quiet machine. Naming one by its id runs it too:
`uv run pytest tests/test_pager_listen.py::<name>_latency`.

The specs are the record of what this package is meant to do: read them
before you design or build. They are kept in the project's OpenSpec store,
outside this tree: neither the repository nor the release archive carries
them. Its `specs/` are ratified; its `changes/` are proposed and not
ratified yet.
