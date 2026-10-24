# SM-827 honest-output goldens

`golden.json` is what memory showed for 44 HONEST mails BEFORE SM-827 moved the readers onto
`_sig.signed_text`. tests/test_memory_honest_unchanged.py builds the same 44 mails afresh
(a key made for the test signs them, so no signed exhibit is shipped) and checks that the
fixed readers print exactly this.

- **Recorded from** sessionmail `4427df2` (origin/master with SM-823 in: the commit
  fix/SM-827-memory-body-parse was cut from), `src/sessionmail` extracted with
  `git archive 4427df2 src`, under PATH python3 3.13 AND `/usr/bin/python3` 3.9.6: the
  recorder refuses unless both interpreters print the same, and they did. notmuch 0.40.
- **Per fixture**: `cat`, `cat_full`, `tree_b`, `boot`, `run_list` (each `rc`, `out`, `err`,
  with the scratch paths masked as `<SCRATCH>`), and `shelf`: memory wake's BOOT section,
  with NOTHING masked.
- **Re-record** only on purpose, from the commit the goldens should describe:
  `uv run python tests/test_memory_honest_unchanged.py --record <PKG_DIR>`.
  A golden that moves is a change to what every seat reads at wake: say why in the commit.

The one known difference after SM-827 is the invalid-UTF-8 pair (`v2-single-bad-utf8`,
`v2-alt-bad-utf8`): notmuch dropped the bytes (`caf  bytes`), the readers now show U+FFFD
(`caf� �� bytes`), as the shelf already did. The test asserts that difference on its own.

- **SM-873**: the settings path inside the old one-line no-registry banner (each
  `run_list`) was rewritten in place to `~/.sessionkit/mail/settings/mail.json`, the mail
  settings file's home. No other byte of `golden.json` moved; the test's `OLD_BANNER`
  carries the same path.
- **SM-917** (two-Sunday rule): every Date in `golden.json` was rewritten in place from
  its old January weekday date to `Sun, 08 Dec 2030`, clocks kept (08:00 from
  `test_verify_v1_shape.DATE`, 14:00 from `sm827_rig.DATE`, both moved with it). The
  replacement is the same length, so no `≈N tok` figure moved; no other byte did.
