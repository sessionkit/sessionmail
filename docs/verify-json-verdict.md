# verify json verdict

`message verify --json` (and `session-mail-verify --json`) prints the
verdict as ONE JSON object on ONE stdout line, `"schema": 1`. Gate on it;
never re-parse the mail's `X-Session-Signature` yourself.

- **Gate:** with `--trust`, accept only if `trust.verdict` is `FULL` or
  `UNBLESSED` **and** `signature` is not null. Plain, only if `signature`
  is not null (the mail matches its own key; anyone can make a key).
- **Take values from `signature.covered`, never from the mail.** Each entry
  is `{"name", "values"}`: exactly what was signed, read back from the
  bytes `ssh-keygen` verified. A field outside the verified `h` is never
  listed; on v1, `h` is always the five fixed names.
- **`signature` is null on every failure**, a FAIL or ERROR at any leg
  included. It is set only on exit 0, or 4 under `--trust`.
- **`trust.agent` is a diagnostic, not a gate:** it is set on a leg-3 FAIL.
- **No object means no verdict.** Fail closed on a missing or unparseable
  object.

```sh
message verify --json --trust ./mail.eml | jq -e '.signature != null
  and (.trust.verdict == "FULL" or .trust.verdict == "UNBLESSED")'
```

## Unchanged, and where things go

- **Exit codes do not change** under `--json`; `rc` in the object is the
  exit status.
- **No `TRUST:` line on stdout under `--json`:** it is `trust.line`, byte
  for byte. Without `--json` nothing changed: the `TRUST:` line is still
  the last stdout line.
- **Per-leg lines go to stderr** under `--json`; `--quiet` drops them. The
  object is the same either way.
- **No object** on a usage error, `--help`, an unreadable file or "no such
  message".
- **`trust.agent`** is set once leg 2 passed, so it is set on a leg-3 FAIL
  (`bless-invalid`, or `bless-absent` under `--require-blessing`) while
  `signature` is null.

## The object

A good v2 mail under `--trust`, unblessed (exit 4). Keys are sorted, the
text is ASCII, and it is shown wrapped here:

```json
{"rc": 4, "reason": "bless-absent", "result": "good", "schema": 1,
 "signature": {"covered": [{"name": "from", "values": ["Ann <ann@host.example>"]},
                           {"name": "x-mouth-device", "values": ["call:1317"]}],
               "h": ["from", "x-mouth-device"],
               "parts": [{"digest": "<p= entry>", "index": 1, "type": "text/plain"}],
               "signer": {"key": "ssh-ed25519 AAAA...", "session_id": "<uuid>"},
               "version": "2"},
 "trust": {"agent": "ann", "leg": null, "line": "TRUST: UNBLESSED reason=bless-absent",
           "reason": "bless-absent", "verdict": "UNBLESSED"}}
```

(A real v2 `h` is longer; it is cut to two names here.) Within schema 1,
keys are only ever added, never renamed or removed.

| key | what it holds |
|---|---|
| `schema` | `1` |
| `rc` | the exit status, for a caller that keeps only stdout |
| `result` | leg 1, in plain mode's words: `good`, `bad`, `unsigned`, `unverified` (could not be judged; never a judgement of the mail) |
| `reason` | one token or null. Plain: null on `good`, else the leg-1 token. `--trust`: the `TRUST:` line's token (null on FULL), so UNBLESSED is `good` + `bless-absent` |
| `signature` | null, or the verified signature (below) |
| `trust` | null without `--trust`; else `{verdict, leg, reason, line, agent}` |

- **`signature.version`:** the `v=` verified, `"1"` or `"2"`.
- **`signature.h`:** the list verified. v2: the signed `h=`, names as
  signed and in order. v1: the five fixed names.
- **`signature.covered`:** one entry per `h` name, in `h` order. A field the
  mail lacks is signed as empty, so it reads `"values": [""]`: the signature
  cannot tell absent from empty, and the object does not claim to. Names are
  as signed, case included: an upper-case name in `h=` matches no field and
  reads `[""]`. A value that is not strict UTF-8 is carried as `values_b64`,
  with **no** `values` key, so a reader of `values` fails with a KeyError,
  never a lossy string.
- **`signature.parts`:** v2, one `{index, type, digest}` per `p=` entry. v1,
  `[{"index": 1, "type": "text/plain", "digest": null}]` (the first text
  part, signed inline).
- **`signature.signer`:** `session_id` and `key`, the key the mail was
  verified with.
- **`trust.verdict`:** `FULL`, `UNBLESSED`, `FAIL` or `ERROR`; `leg` is set
  on FAIL only.

Leg-1 `reason` tokens are the `--trust` ones (`unsigned`, `bad-signature`,
`bad-principal`, `below-floor`, `part-altered`, `cte-defect`, `v1-shape`,
`mime-ambiguous`, `disposition`, `unsupported-version`,
`sig-header-ambiguous`, `undecodable`). `cte-defect` is a v2 part whose transfer encoding is
not strict, so a reader may decode it to other bytes than the signature
covers. On a v1 mail, `cte-defect` is a part whose transfer encoding
is not one `7bit`, `8bit` or `binary` token, and `v1-shape` is any other way
the mail is not in the shape the v1 sender wrote. `mime-ambiguous` is
a v2 mail whose part headers or delimiters two MIME readers may split
differently. `disposition` is a v2 mail where any part, the top
included, carries a `Content-Disposition`, which no signature covers; on v1
the same is `v1-shape`. The `unverified` outcomes add
`signature-unparseable`, `canonicalization-limit`, `verifier-incomplete`
and, from `message verify` only, `verifier-absent`.

## Edge cases

- **`--trust` with no `TRUST:` line.** A v1 mail past the canonicalization
  limit exits 3 under `--trust` too, and has always printed no `TRUST:`
  line. Its object has `result: "unverified"`, `reason:
  "canonicalization-limit"`, and every member of `trust` null. A gate on
  `trust.verdict` fails closed.
- **A broken install.** `message verify` with no verifier writes the one
  object the CLI writes itself: `result: "unverified"`, `reason:
  "verifier-absent"`, exit 3; under `--trust`, exit 5 with `trust.verdict`
  `ERROR` and `trust.line` `TRUST: ERROR reason=verifier-absent`. Its keys
  are the verifier's own ([CLI tests][cli-tests]).
- **A `TRUST:` line the object cannot parse.** None exists today. If one
  ever does, the object is still written, with `trust.verdict` null and
  `signature` null.

## Why `covered` can be trusted

`covered` is not recomputed: it is the signed bytes, split by line. On v2,
the lines after `x-session-signature:` in the text `ssh-keygen` verified;
on v1, the first five lines of it. The split is checked: the parsed pairs
are re-joined and must equal the signed text byte for byte, or `covered` is
null.

That check guards order, completeness and the `name: value` form. It does
not, by itself, rule out a value holding a newline followed by another
name. That case cannot arise because the signer's canonicalization strips
every newline from a value before it is signed: `unfold()` removes the
`\n` of a folded line and `collapse()` splits on all whitespace
([_sig.py][sig]), and a bare CR in the header block is refused before any
of this. The guarantee rests on that step. If it ever changes, this read-back
has to change with it.

## Over MCP

The `message mcp` tool `message_verify` takes the flag as `as_json` (the
click parameter's name, as `send` names it). MEASURED
([CLI tests][cli-tests]):

- **Exit 0** (plain good, or `--trust` FULL): the tool returns the bare
  object, the verifier's stdout byte for byte.
- **Any other exit, UNBLESSED's 4 included:** the tool raises a ToolError
  whose text is `exit N: `, then stderr, a newline, and the object. The
  object is the last line. Under `quiet`, stderr is empty, so the object
  shares its line with the `exit N: ` prefix, and `json.loads` on the whole
  text fails.

So a consumer that gates, and has to accept UNBLESSED, runs the CLI. The
MCP tool is not changed by this work.

## Status

`--json` is a PROPOSED requirement, in the message-verify change
(`openspec/changes/message-verify/`, in the git repository; the release
archive leaves `openspec/` out). It is not ratified yet. The byte-exact
cases are in the [golden files][golden], checked by the
[verifier tests][json-tests].

[cli-tests]: ../tests/test_verify_json_cli.py
[json-tests]: ../tests/test_verify_json.py
[golden]: ../tests/fixtures/verify_json/
[sig]: ../src/sessionmail/_sig.py
