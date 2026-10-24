# Signature v2 golden vectors

The contract both sides of `X-Session-Signature: v=2` are tested against, as the signature v2
design specifies it. These files are the design's, not output to refresh casually: a change to
one is a change to the wire format.

Per vector NAME:

- `NAME.wire`: the bytes the sender hands to sendmail, before the signature header. Addresses
  are BARE where the sender writes them bare, as today; postfix qualifies them in transit.
- `NAME.canon`: the exact bytes the signature is made over.
- `NAME.eml`: the delivered form: postfix-style `Return-Path` / `X-Original-To` /
  `Delivered-To` / `Received`, then the wire with the v2 header inserted before the blank line,
  and with From/To/Cc as postfix re-writes them (each bare addr-spec qualified, the list folded
  at `,\n\t`). For `postfix-reformatted*` those lines are a REAL postfix 3.2.2 delivery (probes
  P3 and P5), with the session id, the names and the host name replaced.
- `canonical(NAME.wire) == canonical(NAME.eml) == NAME.canon`, with origin `hq.example.net`.
  That equality is the contract the design rests on.

`vectors.json` has, per vector, `ns`, `origin`, `h` (the sender's `h=`), `p` (the part digests),
`postfix_rewrote` (the address headers whose text postfix changed) and the leaf content types,
plus the public key.

**The key is a TEST key**: `sessionmail-sig-TEST-VECTORS-ONLY` (and `.pub`), an ed25519 key
made for these vectors alone. Its private half lives in the source repository only, committed
ON PURPOSE so the vectors can be re-minted there, and is not shipped in release archives; to
re-mint from a release, generate your own ed25519 key (`make_vectors.py` says so and exits). It
belongs to no seat, host or person, and nothing trusts it. Tests need only the
public key, which every `b=` embeds. Domain `hq.example.net`, session `0a1b2c3d`, date
`Sun, 09 Jun 2030 12:00:00 +0000`.

`make_vectors.py` makes every file here but this README, and the 13 signed controls in
`../mime_honest/controls/`, from constants it holds, signing with this key through the
package's own signing code. From the repository root:

    uv run --offline python tests/fixtures/sig_v2/make_vectors.py

A re-run writes the same bytes (an ed25519 SSH signature is deterministic), so
`git status --porcelain tests/fixtures` stays empty.

| vector | shape |
| --- | --- |
| single | text/plain, agent to agent |
| empty-body | a `[BOOT]` label mail with no body |
| two-part | multipart/alternative: markdown, then html; display names |
| atom-three-part | markdown, application/json (8bit), html; Atom-* headers |
| atom-three-part-b64 | the same, with the JSON part base64 |
| cc | three Cc addresses: bare, qualified with a display name, short host (`eve@hq`, left alone) |
| reply | In-Reply-To, References (three ids, folded), Supersedes |
| atom-headers | X-Project, X-Task, X-Original-Subject (NOT signed, RULED Q5), Atom-*, RFC 2047 |
| postfix-reformatted | REAL postfix output: `J. Smith` arrives `J.Smith`, `Judy<x>` gains a space, a comment, an escaped quote |
| postfix-reformatted-2 | REAL postfix output: `<x>` with no name, `""`, a comment in the name, `Uma . Lee` arrives `Uma.Lee` |
