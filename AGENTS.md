# sessionmail

Mail for coding agents. One package, three commands: `message` moves
messages, `pager` alerts, `memory` is the maildir as a filesystem.

## REFER TO THE SPECS BEFORE BUILDING ANYTHING

The specs are the record of what this package is meant to do. They are
kept in the project's OpenSpec store, outside this tree: neither the
repository nor the release archive carries them.
**Read them before you design, before you build, and before you report that
something is not specified.** The specs are what let a change be discussed:
"this isn't a spec", "the spec needs to change", "the spec doesn't seem to
make sense". They are also what you reason about, and where you look for
gaps. A ratified spec is the contract; a proposed change is not ruled until
it is ratified.

Three rules that have each been broken on this repo already:

1. **Do not re-derive a design from the code.** If you find yourself
   working out how addressing or subscriptions behave by reading source,
   stop: the page exists, or its absence is the finding.
2. **Mark what is MEASURED, what is RULED, and what is OPEN**, in the
   requirement itself. A measurement is not a ruling. Nothing may be built
   on an OPEN item.
3. **Know whose scope you are in.** sessionmail knows the GRAMMAR of an
   address and nothing about what a tag MEANS; naming vocabularies belong
   to the project that mints them.

```sh
make install
make
make progress
```

- `make install` runs `uv tool install -e .`: editable, so an edit is live.
- `make` checks, then tests.
- `make progress` counts how much of the CLI is built, by running it.

The slipbox notes cited by name in the specs and docs live in a private
slipbox; none of them ships in this tree. The pager's concept note is a doc
here: [`docs/pager.md`][pager-doc]. The other two commands have a page
each: [`docs/message.md`][message-doc] and [`docs/memory.md`][memory-doc].

[pager-doc]: docs/pager.md
[message-doc]: docs/message.md
[memory-doc]: docs/memory.md
