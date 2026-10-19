# SM-827 D1 hint-root goldens

`golden.json` is what `memory cat /`, `cat / --bodies`, `tree / -b` and `--index -b`
printed BEFORE SM-827 fix-001 for four memory homes whose `[ROOT]` differs: signed (the
control), unsigned, a v1 root outside the v1 shape, and an unsigned root whose header
block ends in CR LF (notmuch shows it no body). tests/test_memory_hint_root.py builds the
same homes afresh (a key made for the test signs them) and checks the fixed readers
against it.

- **Recorded from** sessionmail `4427df2` (the commit fix/SM-827-memory-body-parse was cut
  from), `src/sessionmail` extracted with `git archive 4427df2 src/sessionmail`, under PATH
  python3 3.13 AND `/usr/bin/python3` 3.9.6: the recorder refuses unless both interpreters
  print the same, and they did. notmuch 0.40.
- **Per case and consumer**: `rc`, `out`, `err`, with the scratch paths masked as
  `<SCRATCH>`.
- **Re-record** only on purpose, from the commit the goldens should describe:
  `uv run python tests/test_memory_hint_root.py --record <PKG_DIR>`.

The ruled difference: a hint-admitted root that is
not a member gets ONE label line under `[ROOT]`, `(root admitted by its hint; unsigned)` or
`(root admitted by its hint; signature does not verify)`, and its body is Python's reading of
its first text/plain part. On the unsigned and v1 roots that is the label alone (and the token
estimate it moves); the CR LF root also gains the body notmuch did not show.
