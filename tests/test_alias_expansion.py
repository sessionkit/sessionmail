"""The generated alias rows, EXPANDED the way postfix expands them (SM-374 do-017, C2).

The row tests in `test_postfix_vmailbox_gen.py` pin each row's TEXT. They
leave the delivery chain to postfix: a task doorbell is
`<base>+<ext> -> <base>, <sid>`, and whether that reaches the session
subscribed to `<base>` depends on postfix expanding `<base>@` AGAIN, into
the subscription row. verify-001 named that gap, and C2 asked for it
measured. This file measures it over a synthetic root with the REAL generator: `--print` gives the mailbox map,
`--print-valias` the alias map, and a small resolver below expands a
recipient through them.

THE RESOLVER IS A TRANSCRIPTION, NOT A GUESS. Each rule is postfix's own,
read at vdukhovni/postfix master (the rules are old; they are not new in
3.x):

- `cleanup/cleanup_map1n.c`, `cleanup_map1n_internal`: the recipient list
  is walked slot by slot; a slot is looked up again and again until it has
  no row. The first result REPLACES the slot and the rest are appended.
  *"Allow an address to expand into itself once"*: a result equal to the
  address looked up is marked, and a marked address is never looked up
  again, wherever it appears in the list. Past 1000 entries, or 1000 lookups
  of one slot, or a 1000-byte result (`virtual_alias_expansion_limit`,
  `virtual_alias_recursion_limit`, `virtual_alias_address_length_limit`),
  the message is DEFERRED: *"4.6.0 Alias expansion error"*.
- `global/mail_addr_map.c`, `mail_addr_map_opt`: the full address is looked
  up first; failing that, the address with its extension stripped, and the
  unmatched extension is PROPAGATED into every result
  (`propagate_unmatched_extensions = canonical, virtual`, measured on hq in
  design D2). `global/mail_addr_crunch.c` appends it to each result's local
  part UNCONDITIONALLY, even one that already carries an extension.
  `global/strip_addr.c` splits at the FIRST delimiter.
- `cleanup/cleanup_out_recipient.c`: the expanded list goes through the
  duplicate filter (`been_here(state->dups, ...)`), so a recipient repeated
  in the list is written once.
- virtual(8) then looks each recipient up in `virtual_mailbox_maps`, the
  same full-then-stripped way. Two DIFFERENT recipients that land in one
  maildir are two copies there: that is what "no duplicate copies" checks.

Not modelled, because no generated row uses them: the `user+ext`/`user`
local-part forms (local domains only) and `@domain` catch-alls.

Hermetic like the files it borrows from: --print/--print-valias write
nothing, the fleet is sidecars and a fake `ps` under tmp_path, and neither
the live `~/.mail` nor `/usr/local/etc/postfix-agents` is ever read.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from test_postfix_vmailbox_gen import (  # noqa: F401 (generator is a fixture)
    FQDN,
    HOST,
    _run,
    _valias,
    generator,
    needs_jq,
)

# postfix's defaults (postconf -d), the three limits cleanup_map1n enforces.
EXPANSION_LIMIT = 1000
RECURSION_LIMIT = 1000
ADDRESS_LENGTH_LIMIT = 1000
DELIM = "+"


class Deferred(Exception):
    """cleanup_map1n gave up: `4.6.0 Alias expansion error`, the message is
    not accepted and the sender's queue tries again later."""


def _find(table: dict, address: str):
    """mail_addr_find + mail_addr_map's extension rule: the full address,
    else the address with its extension stripped at the FIRST delimiter.
    Returns (value, unmatched extension or "")."""
    address = address.lower()
    if address in table:
        return table[address], ""
    local, _, domain = address.rpartition("@")
    cut = local.find(DELIM)
    if cut > 0:
        bare = f"{local[:cut]}@{domain}"
        if bare in table:
            return table[bare], local[cut:]
    return None, ""


def _lookup(aliases: dict[str, list[str]], address: str) -> list[str] | None:
    """One alias lookup, the extension propagated into EVERY result, as
    mail_addr_crunch does it: no check for an extension already there."""
    found, ext = _find(aliases, address)
    if found is None:
        return None
    out = []
    for dest in found:
        local, _, domain = dest.rpartition("@")
        out.append(f"{local}{ext}@{domain}")
    return out


def expand(aliases: dict[str, list[str]], address: str) -> list[str]:
    """cleanup_map1n_internal, loop for loop. Raises Deferred at a limit."""
    argv = [address.lower()]
    marked: set[str] = set()
    arg = 0
    while arg < len(argv):
        if len(argv) > EXPANSION_LIMIT:
            raise Deferred(f"unreasonable map expansion size for {address}")
        count = 0
        while True:
            if argv[arg] in marked:
                break
            if count >= RECURSION_LIMIT:
                raise Deferred(f"unreasonable map nesting for {address}")
            results = _lookup(aliases, argv[arg])
            if results is None:
                break
            lhs = argv[arg]
            for i, result in enumerate(results):
                if len(result) > ADDRESS_LENGTH_LIMIT:
                    raise Deferred(f"unreasonable result {result[:60]}...")
                if i == 0:
                    argv[arg] = result
                else:
                    argv.append(result)
                if result == lhs:
                    marked.add(lhs)
            count += 1
        arg += 1
    return argv


def deliver(maps: tuple[dict, dict], address: str) -> list[tuple[str, str]]:
    """(recipient, maildir) for every copy one mail To ADDRESS makes: the
    expansion, the duplicate filter, then virtual(8)'s mailbox lookup. A
    recipient with no mailbox row would bounce, so it fails here."""
    aliases, mailboxes = maps
    recipients = list(dict.fromkeys(expand(aliases, address)))   # the dup filter
    copies = []
    for rcpt in recipients:
        box, _ = _find(mailboxes, rcpt)
        assert box is not None, f"{rcpt} has no mailbox row: it would bounce"
        copies.append((rcpt, box))
    return copies


def boxes(copies: list[tuple[str, str]]) -> Counter:
    """How many copies each maildir gets."""
    return Counter(box for _, box in copies)


# ── the resolver, checked against postfix's rules on hand-written maps ──────


def test_an_address_that_expands_into_itself_is_delivered_not_expanded_again() -> None:
    assert expand({"a@h": ["a@h", "b@h"]}, "a@h") == ["a@h", "b@h"]


def test_a_row_found_by_the_bare_key_carries_the_tag_into_every_result() -> None:
    assert expand({"v@h": ["v@h", "s@h"]}, "V+k@h") == ["v+k@h", "s+k@h"]


def test_an_exact_plus_row_wins_over_the_bare_row() -> None:
    table = {"v+k@h": ["v+k@h", "a@h"], "v@h": ["v@h", "b@h"]}
    assert expand(table, "v+k@h") == ["v+k@h", "a@h"]


def test_a_marked_address_met_again_is_kept_and_the_filter_writes_it_once() -> None:
    table = {"a@h": ["a@h", "b@h"], "b@h": ["a@h", "c@h"]}
    assert expand(table, "a@h") == ["a@h", "a@h", "c@h"]
    assert deliver((table, {"a@h": "A/", "c@h": "C/"}), "a@h") == [("a@h", "A/"), ("c@h", "C/")]


def test_a_tag_propagated_onto_its_own_base_row_runs_away_and_is_deferred() -> None:
    """`a -> a, a+x`: `a+x` has no row, so it is looked up by `a`, gets
    `+x` again, and every step makes a new, longer address."""
    with pytest.raises(Deferred):
        expand({"a@h": ["a@h", "a+x@h"]}, "a@h")


# ── the generated maps, over a synthetic fleet ──────────────────────────────

PROJECT = "sessionmail"
TASK_SEAT = "sessionmail+sm-374.foreman"
SUPER_SEAT = "sessionmail+super"
FOREMAN, SUPER, OTHER = "sf", "ss", "s2"


def _maps(generator: Path, tmp_path: Path, sessions: list[dict],
          addresses: dict[str, str], extra_boxes: list[str],
          subs: dict[str, str]) -> tuple[dict, dict]:
    """The alias map and the mailbox map the REAL generator writes for this
    fleet, as lookup tables. Every live session has a box."""
    sids = [s["session_id"] for s in sessions]
    done = _valias(generator, tmp_path, sessions, addresses,
                   [*sids, *extra_boxes], subs=subs)
    assert done.returncode == 0, done.stderr
    aliases = {}
    for line in done.stdout.splitlines():
        key, _, dests = line.partition("\t")
        aliases[key] = [d.strip() for d in dests.split(",") if d.strip()]
    printed = _run(generator, tmp_path, "--print", "--no-postmap",
                   "--host", HOST, "--root", str(tmp_path / "root"))
    assert printed.returncode == 0, printed.stderr
    mailboxes = dict(line.split("\t", 1) for line in printed.stdout.splitlines())
    return aliases, mailboxes


def _box(name: str) -> str:
    return f"agents/{name}/"


SUPER_BY_SESSION = {f"sessions/{SUPER}/subscriptions/{PROJECT}": ""}
#: A SEAT's subscription is its map in the new tree (session-settings 3.2).
SUPER_BY_SEAT = {f"~/.session/seats/{SUPER_SEAT}/settings/mail.json":
                 json.dumps({"subscriptions": {PROJECT: True}})}


@needs_jq
@pytest.mark.parametrize("domain", [HOST, FQDN])
@pytest.mark.parametrize("subscribed", [
    pytest.param(SUPER_BY_SESSION, id="super-session-subscribed"),
    pytest.param(SUPER_BY_SEAT, id="super-plus-seat-subscribed"),
])
def test_C2_mail_to_a_live_task_seat_reaches_the_session_subscribed_to_its_base(
    generator: Path, tmp_path: Path, subscribed: dict, domain: str
) -> None:
    """C2. A live foreman declared `sessionmail+sm-374.foreman`, so its
    doorbell is `<that> -> sessionmail, sf`. The super is subscribed to
    `sessionmail`: by its SESSION (what `SESSIONMAIL_SUBSCRIBE=sessionmail
    pager listen` writes, the HEARS replacement), or by its plus SEAT, whose
    own doorbell leads back to `sessionmail`, a loop postfix breaks.

    One mail To the task seat makes exactly three copies: the project box,
    the foreman's box and the super's box, one each. The super's copy is
    addressed to its bare session id, not `<sid>+<tag>`: the doorbell row
    matched EXACTLY, so no extension propagates. Only the header
    (X-Original-To) names the task seat."""
    copies = deliver(_maps(
        generator, tmp_path,
        [{"session_id": FOREMAN, "agent": "foreman"}, {"session_id": SUPER, "agent": "super"}],
        {FOREMAN: TASK_SEAT, SUPER: SUPER_SEAT}, [PROJECT], subscribed),
        f"{TASK_SEAT}@{domain}")
    assert boxes(copies) == Counter({_box(PROJECT): 1, _box(FOREMAN): 1, _box(SUPER): 1})
    assert (f"{SUPER}@{domain}", _box(SUPER)) in copies


@needs_jq
def test_C2_the_super_gets_nothing_from_the_task_seat_without_a_subscription(
    generator: Path, tmp_path: Path
) -> None:
    """The control: with no subscription to `sessionmail`, the same doorbell
    reaches the project box and the foreman only. So the super's copy above
    comes from the subscription row, reached THROUGH the doorbell's `<base>`
    destination."""
    copies = deliver(_maps(
        generator, tmp_path,
        [{"session_id": FOREMAN, "agent": "foreman"}, {"session_id": SUPER, "agent": "super"}],
        {FOREMAN: TASK_SEAT, SUPER: SUPER_SEAT}, [PROJECT], {}),
        f"{TASK_SEAT}@{HOST}")
    assert boxes(copies) == Counter({_box(PROJECT): 1, _box(FOREMAN): 1})


@needs_jq
def test_C2_a_task_seat_whose_session_has_gone_falls_through_to_the_base_subscriber(
    generator: Path, tmp_path: Path
) -> None:
    """The dead letter office, for a seat that is not live: no doorbell row
    at this rebuild, so postfix finds the bare row and the super gets it as
    `<sid>+<tag>`."""
    copies = deliver(_maps(
        generator, tmp_path, [{"session_id": SUPER, "agent": "super"}],
        {}, [PROJECT, FOREMAN], SUPER_BY_SESSION),
        f"{TASK_SEAT}@{HOST}")
    assert boxes(copies) == Counter({_box(PROJECT): 1, _box(SUPER): 1})
    assert (f"{SUPER}+sm-374.foreman@{HOST}", _box(SUPER)) in copies


NARROW = {"sessions/sa/subscriptions/voicemode+kaizen": "",
          "sessions/sb/subscriptions/voicemode": ""}
VM_SEAT = "voicemode+vm-2266.foreman"


@needs_jq
def test_a_narrow_subscription_claims_its_tag_and_a_doorbell_is_not_a_claim(
    generator: Path, tmp_path: Path
) -> None:
    """Spec 'A narrow subscription claims its tag': A on voicemode+kaizen, B
    on voicemode. Kaizen mail reaches the box and A, never B. An unclaimed
    tag falls through to B as `sb+<tag>`. And a live task seat's doorbell
    (`voicemode+vm-2266.foreman`) claims nothing: B still gets that mail."""
    maps = _maps(
        generator, tmp_path,
        [{"session_id": "sa", "agent": "worker"}, {"session_id": "sb", "agent": "super"},
         {"session_id": "sv", "agent": "foreman"}],
        {"sv": VM_SEAT}, ["voicemode"], NARROW)

    kaizen = deliver(maps, f"voicemode+kaizen@{HOST}")
    assert boxes(kaizen) == Counter({_box("voicemode"): 1, _box("sa"): 1})

    other = deliver(maps, f"voicemode+vm-2267@{HOST}")
    assert boxes(other) == Counter({_box("voicemode"): 1, _box("sb"): 1})
    assert (f"sb+vm-2267@{HOST}", _box("sb")) in other

    doorbell = deliver(maps, f"{VM_SEAT}@{HOST}")
    assert boxes(doorbell) == Counter({_box("voicemode"): 1, _box("sv"): 1, _box("sb"): 1})


# ── FINDINGS: pinned as they are, so a fix turns them red on purpose ────────


@needs_jq
def test_FINDING_a_narrow_claim_on_a_live_task_seat_does_not_hold(
    generator: Path, tmp_path: Path
) -> None:
    """FINDING 1. The claim rule and the doorbell meet on one key. s2 is
    subscribed NARROWLY to the live task seat `sessionmail+sm-374.foreman`,
    and the super to `sessionmail`. The generator merges both into one row,
    `<seat> -> sessionmail, sf, s2` (do-014 (a) keeps the box once). But the
    doorbell's `sessionmail` destination is expanded AGAIN, into the bare
    row, so the super gets the mail too: the tag is claimed and the base
    subscriber still hears it. The spec's claim scenario says it should not;
    'a doorbell is not a claim' says the base subscriber should. No spec line
    covers both at once. Reported for a ruling; nothing is designed here."""
    copies = deliver(_maps(
        generator, tmp_path,
        [{"session_id": FOREMAN, "agent": "foreman"}, {"session_id": SUPER, "agent": "super"},
         {"session_id": OTHER, "agent": "ivan"}],
        {FOREMAN: TASK_SEAT}, [PROJECT],
        {**SUPER_BY_SESSION, f"sessions/{OTHER}/subscriptions/{TASK_SEAT}": ""}),
        f"{TASK_SEAT}@{HOST}")
    assert boxes(copies) == Counter(
        {_box(PROJECT): 1, _box(FOREMAN): 1, _box(OTHER): 1, _box(SUPER): 1})


@needs_jq
@pytest.mark.parametrize("to", [PROJECT, TASK_SEAT, f"{PROJECT}+kaizen"])
def test_FINDING_a_plus_seat_subscribed_to_its_base_defers_all_base_mail_while_no_session_holds_it(
    generator: Path, tmp_path: Path, to: str
) -> None:
    """FINDING 2. Spec 'The tree shows what feeds what' has the plus seat
    `acmecorp+gc` subscribed to `acmecorp`; here it is `sessionmail+super`
    on `sessionmail`. The row is `sessionmail -> sessionmail,
    sessionmail+super`. While a super session holds the seat, its doorbell
    row answers `sessionmail+super` (the C2 case above). When NO live
    session declares it, `sessionmail+super` has no row of its own, so
    postfix looks it up by `sessionmail`, propagates `+super`, and gets
    `sessionmail+super+super`, and so on: a new address each step, until
    cleanup DEFERS the message (4.6.0). Every mail to the project box, to a
    live task seat, or to any tag falls into it. Nothing is lost at once:
    the sender's queue retries until the seat is held again. The resolver
    shows the mechanism; it was NOT measured against a live postfix."""
    maps = _maps(
        generator, tmp_path, [{"session_id": FOREMAN, "agent": "foreman"}],
        {FOREMAN: TASK_SEAT}, [PROJECT], SUPER_BY_SEAT)
    assert maps[0][f"{PROJECT}@{HOST}"] == [f"{PROJECT}@{HOST}", f"{SUPER_SEAT}@{HOST}"]
    assert f"{SUPER_SEAT}@{HOST}" not in maps[0]
    with pytest.raises(Deferred):
        deliver(maps, f"{to}@{HOST}")
