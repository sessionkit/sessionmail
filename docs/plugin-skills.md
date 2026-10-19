# The plugin: three skills, one per command

`skills/message`, `skills/pager`, `skills/memory` - each seed is one
sentence of what and when; each body points at that command's `--help`
and repeats nothing; each `references/` is a flat slipbox of items under
40 lines / 400 tokens. The README and docs/ are the overview of how the
three go together. Three skills keep the three commands clearly separate;
the split stays flexible and may be reviewed.

The notes the specs cite by name live in a private slipbox and are not
part of this tree. The pager's concept note is a doc here, [pager.md][pager].

[pager]: pager.md
