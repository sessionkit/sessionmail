"""changes/task-address-doorbell - a plus address gets its own doorbell row.

The sentence ACCEPTED (RATIFICATION.md): `pager listen` for `<base>+<ext>` DECLARES that
address for its session, and the alias map generator writes ONE row for the
full plus form - to `<base>@<host>` and this session's box - and never a row on
the bare base. `EMAIL` carries the address, and a plus seat is
refused its base. The variable that added what else a seat hears,
the refusal's one way round, retired with message-subscriptions
4.8: a subscription does that job (tests/test_hears_retires.py). Sessionmail
knows RFC 5233 and no task grammar.

Pinned here:
  - the address, the plus split and the declaration, in `_seat`;
  - D1's ORDER: a fresh plus seat's FIRST listen reads LIVE - driven through
    the REAL generator (wrapped to write text maps under tmp_path), with fake
    postconf/postmap reading them - and the same listen with the declaration
    seen too late reads NOT COPIED, so the LIVE test can fail;
  - D3: one folder rule (the born query, its HEARS reader and the real-
    notmuch match test retired with message-subscriptions 4.2);
  - D5: the refusal, each of its two remaining conditions flipped alone;
  - the ratified scenario "The generator is absent".

Hermetic: every map, box, seat and corpus is under tmp_path. The real
generator is only ever run with `--out` into tmp_path and `--no-postmap`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from importlib.resources import as_file, files
from pathlib import Path

import pytest
from click.testing import CliRunner

from _seatmap import session_declare, session_live_path, session_log, session_map_path
from fakes import write_fake
from sessionmail import _seat, pager
from sessionmail.pager import cli

PLUS = "sessionmail+sm-122.foreman"
EX_COULD_NOT_LOOK, EX_TIMEOUT = 2, 124
HOST = socket.gethostname().split(".")[0].lower()


# SM-648: THE HOST IS DERIVED, NEVER PINNED. These tests once spelt "this
# host" as a literal `@hq`, while the code reads `socket.gethostname()`; when
# the fleet moved hq -> nas, nine of them went red on every host but hq.
# Every case that needs THIS host's spelling takes the `host` fixture, which
# runs it twice: on the real host, and on one it can never be (`not-<host>`).
# A literal pinned host fails at least one of the two, so the next rename
# cannot hide behind the machine the suite happens to run on.
@pytest.fixture(params=["this-host", "another-host"])
def host(request, monkeypatch):
    """This host's short name, as the code under test will read it."""
    name = HOST if request.param == "this-host" else f"not-{HOST}"
    monkeypatch.setattr(socket, "gethostname", lambda: name)
    return name


def _mail(sid: str) -> Path:
    """Where a session's mail state is since session-settings 3.3a: under
    the session dir sessiontools keeps, `$HOME/.session/sessions/<sid>/mail/`
    (spelt out, not asked of the code, so the test can see it move)."""
    return Path(os.environ["HOME"]) / ".session" / "sessions" / sid / "mail"


# ── _seat: the address ────────────────────────────────────────────────────

def test_split_plus_is_rfc_5233_and_nothing_more():
    assert _seat.split_plus(PLUS) == ("sessionmail", "sm-122.foreman")
    assert _seat.split_plus("a.b+c.d.e") == ("a.b", "c.d.e")        # never splits on `.`
    for not_plus in ("sessionmail", "sessionmail+", "+sm-122", ""):
        assert _seat.split_plus(not_plus) is None


def test_the_declaration_is_EMAIL_first_then_TM_AGENT_ADDRESS_and_only_a_plus_declares(host):
    env = {"EMAIL": f"Sessionmail+SM-122.Foreman@{host}.example.ts.net",
           "TM_AGENT_ADDRESS": "other+x"}
    assert _seat.declared_address(env) == (PLUS, "EMAIL")
    # a human's inherited EMAIL declares nothing and falls through
    env = {"EMAIL": "judy@example.com", "TM_AGENT_ADDRESS": PLUS}
    assert _seat.declared_address(env) == (PLUS, "TM_AGENT_ADDRESS")
    assert _seat.declared_address({"EMAIL": "judy@example.com", "TM_AGENT_ADDRESS": "eve"}) is None
    assert _seat.declared_address({}) is None


def test_the_seat_keeps_its_name_and_gains_its_address_and_folder(tmp_path):
    """D2 + D3: `name` is unchanged for its other callers; the doorbell key
    and the seat folder carry the `+`, the extension intact."""
    env = {"SESSIONMAIL_HOME": str(tmp_path), "TM_AGENT_ADDRESS": PLUS}
    seat = _seat.resolve(env)
    assert seat.name == "sessionmail"
    assert seat.address == PLUS
    assert seat.root == tmp_path / "seats" / PLUS
    bare = _seat.resolve({"SESSIONMAIL_HOME": str(tmp_path), "CLAUDE_CODE_AGENT": "eve",
                          "EMAIL": "judy@example.com"})
    assert (bare.name, bare.address, bare.root) == ("eve", "eve", tmp_path / "seats" / "eve")


def test_EMAIL_declares_for_a_seat_named_by_another_variable(tmp_path, host):
    seat = _seat.resolve({"SESSIONMAIL_HOME": str(tmp_path), "CLAUDE_CODE_AGENT": "foreman",
                          "EMAIL": f"{PLUS}@{host}"})
    assert (seat.name, seat.address) == ("foreman", PLUS)


def test_nothing_in_seat_splits_the_extension(tmp_path):
    """The principle: sessionmail knows no project, task or
    role grammar. Everything it derives keeps `sm-122.foreman` whole."""
    env = {"SESSIONMAIL_HOME": str(tmp_path), "TM_AGENT_ADDRESS": PLUS,
           "CLAUDE_CODE_SESSION_ID": "s"}
    seat = _seat.resolve(env)
    # (The born query's expansion was pinned here too; the born files and
    # their query retired with message-subscriptions 4.2.)
    assert seat.address == PLUS
    assert seat.root.name == PLUS
    path = _seat.write_address("s", seat.address, env)
    assert json.loads(path.read_text()) == {"address": PLUS}         # its layer (3.3b)


def test_the_address_is_keyed_on_the_session_and_overwritten_whole(tmp_path):
    """3.3b: the session layer's `address` key, one value, each change logged;
    3.4: in the file the session's verbs write, `mail.d/70-live.json`."""
    env = {"SESSIONMAIL_HOME": str(tmp_path), "HOME": str(tmp_path)}
    path = _seat.write_address("sess-a", "a+b", env)
    assert path == session_live_path("sess-a", tmp_path)
    _seat.write_address("sess-a", "a+c", env)
    _seat.write_address("sess-a", "a+c", env)                      # unchanged: not logged
    assert json.loads(path.read_text()) == {"address": "a+c"}
    assert _seat.read_address("sess-a", env) == "a+c"
    assert [r["value"] for r in session_log("sess-a", tmp_path)] == ["a+b", "a+c"]
    # No temp left behind: the verbs' file, and the layer's writers' lock.
    assert sorted(p.name for p in path.parent.iterdir()) == ["70-live.json"]
    assert sorted(p.name for p in path.parent.parent.iterdir()) == [".mail.json.lock", "mail.d"]
    assert _seat.write_address("-", "a+b", env) is None


# ── D5: the refusal ───────────────────────────────────────────────────────

def _seat_for(address: str) -> _seat.Seat:
    return _seat.Seat(name=address.split("+")[0], source="TM_AGENT_ADDRESS",
                      root=Path("/nonexistent"), address=address)


def test_the_refusal_holds_only_when_both_conditions_hold():
    """Each condition flipped ALONE changes the outcome. There were three
    until message-subscriptions 4.8: the third, "the base is not declared
    in the HEARS variable", retired with the variable."""
    assert _seat.refuses_base(_seat_for(PLUS), "sessionmail") == "sessionmail"
    # 1. the identity has no plus part
    assert _seat.refuses_base(_seat_for("sessionmail"), "sessionmail") is None
    # 2. BOX is not that identity's base
    assert _seat.refuses_base(_seat_for(PLUS), "voicemode") is None


# ── D3: HEARS, read at every read - RETIRED ─────────────────────────────
#
# The HEARS variable was read into the saved-query reader
# (`_seat.read_subscriptions`) as one more query; message-subscriptions 4.2
# retired that reader. Its last readers - the listen refusal (D5) and
# `own_addresses` - went with 4.8 (tests/test_hears_retires.py).


# ── pager listen, through the real generator ──────────────────────────────

FAKE_POSTCONF = """#!{python}
import os, sys
maps = os.environ["POSTFIX_MAPS"]
print({{"virtual_alias_maps": "hash:" + maps + "/valias",
       "virtual_mailbox_maps": "hash:" + maps + "/vmailbox"}}[sys.argv[2]])
"""

FAKE_POSTMAP = """#!{python}
import sys
key, path = sys.argv[2], sys.argv[3].split(":", 1)[1]
try:
    lines = open(path).read().splitlines()
except OSError:
    sys.exit(1)
for line in lines:
    parts = line.split(None, 1)
    if len(parts) == 2 and parts[0] == key:
        print(parts[1]); sys.exit(0)
sys.exit(1)
"""

#: The generator reads Claude Code's sidecars for LIVE (2030-09-30, no
#: `session` any more); `ps` answers from the fixture so the pid is "running".
FAKE_PS = """#!/bin/sh
cat "$FAKE_PS_TABLE"
"""
#: A synthetic start, the suite's fixture moment. The sidecar and the fake
#: `ps` share it, so the 2 s match holds.
STARTED = "Tue Jan 01 12:00:00 2030"

#: The REAL generator, held at arm's length: always `--out` under tmp_path,
#: never postmap, the host pinned to the one the doorbell folds to.
WRAPPED_GEN = """#!/bin/sh
exec "$REAL_GEN" --out "$POSTFIX_MAPS/vmailbox" --no-postmap --host "$GEN_HOST" "$@"
"""


@pytest.fixture
def fleet(tmp_path, monkeypatch, host):
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("the generator's alias half needs jq")
    agents = tmp_path / "agents"
    agents.mkdir()
    maps = tmp_path / "maps"
    maps.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "postconf", FAKE_POSTCONF)
    write_fake(bin_dir / "postmap", FAKE_POSTMAP)
    for name, text in (("ps", FAKE_PS), ("vmailbox-gen-wrapped", WRAPPED_GEN)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    sidecars = tmp_path / "claude-sessions"
    sidecars.mkdir()
    (sidecars / "40000.json").write_text(json.dumps(
        {"pid": 40000, "sessionId": "sess-a", "agent": "foreman", "procStart": STARTED}))
    ps_table = tmp_path / "ps.txt"
    ps_table.write_text(f"40000 {STARTED}\n")
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as real:
        monkeypatch.setenv("REAL_GEN", str(real))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.path.dirname(jq)}:/usr/bin:/bin")
    monkeypatch.setenv(_seat.GEN_VAR, str(bin_dir / "vmailbox-gen-wrapped"))
    monkeypatch.setenv("GEN_HOST", host)
    monkeypatch.setenv("POSTFIX_MAPS", str(maps))
    monkeypatch.setenv("FAKE_PS_TABLE", str(ps_table))
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(sidecars))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(agents))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(tmp_path / "sessionmail"))
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "foreman")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)

    class Fleet:
        root = tmp_path
        valias = maps / "valias"
        address = session_live_path("sess-a")   # 3.4: the session's verbs' file

    return Fleet()


def listen(*argv: str):
    return CliRunner().invoke(cli, ["listen", *argv], prog_name="pager")


def test_a_fresh_plus_seats_FIRST_listen_reads_LIVE(fleet, monkeypatch, host):
    """D1, the ordering trap: the declaration is written before the first
    doorbell check, the NOT COPIED it reads runs the generator whole, the
    generator sees the declaration and writes the row, and the second check
    reads LIVE - all on the FIRST listen."""
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}.example.ts.net")
    result = listen("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert json.loads(fleet.address.read_text())["address"] == PLUS
    assert f"  address    : {PLUS} (from EMAIL) declared in {fleet.address}" in result.stderr
    assert f"doorbell   : LIVE: {PLUS}@{host} -> this box" in result.stderr
    rows = fleet.valias.read_text()
    assert f"{PLUS}@{host}\tsessionmail@{host}, sess-a@{host}\n" in rows
    assert not any(line.startswith(f"sessionmail@{host}\t") for line in rows.splitlines())
    assert f"seat       : foreman (from CLAUDE_CODE_AGENT)" in result.stderr


def test_the_same_listen_with_the_declaration_seen_too_late_is_NOT_COPIED(fleet, monkeypatch, host):
    """The test above can FAIL: hide the file from the regen - as if it were
    written after the first check - and the first listen reads NOT COPIED."""
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}")
    real_regen = _seat.regen_maps

    def regen_before_the_declaration(root, env=None):
        text = fleet.address.read_text()
        fleet.address.unlink()
        try:
            return real_regen(root, env)
        finally:
            fleet.address.write_text(text)

    monkeypatch.setattr(_seat, "regen_maps", regen_before_the_declaration)
    result = listen("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert f"doorbell   : NOT COPIED - the seat's mail will not reach this box: " \
           f"no alias map postfix reads copies {PLUS}@{host} into this box" in result.stderr


def test_the_fallback_variable_is_named_in_the_banner(fleet, monkeypatch, host):
    monkeypatch.setenv("TM_AGENT_ADDRESS", PLUS)
    result = listen("--timeout", "0.1")
    assert f"  address    : {PLUS} (from TM_AGENT_ADDRESS) declared in" in result.stderr
    assert f"doorbell   : LIVE: {PLUS}@{host} -> this box" in result.stderr
    # message-subscriptions 4.2: the first listen writes no born files now.
    assert not (fleet.root / "sessionmail" / "seats" / PLUS / "subscriptions").exists()


def test_a_bare_identity_declares_nothing_and_its_role_word_gets_no_row(fleet, host):
    """SM-218 re-key (was `..._and_keeps_todays_row`, which pinned
    `doorbell: LIVE: foreman@<host> -> this box`). A bare `foreman` identity
    still declares nothing. But `foreman` has no box of its own, so the
    generator no longer mints the `foreman@` fan-out row. The banner now says
    so truthfully: NOT COPIED. This is the named cost in rca-001 A3, accepted.
    It holds until a worker declares a
    routable address (taskmaster's half; the banner wish is folded into SM-238)."""
    result = listen("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert "address    :" not in result.stderr
    assert not fleet.address.exists()
    assert f"doorbell   : NOT COPIED - the seat's mail will not reach this box: " \
           f"no alias map postfix reads copies foreman@{host} into this box" in result.stderr
    assert f"foreman@{host}" not in fleet.valias.read_text()


def test_the_generator_is_absent(fleet, monkeypatch, tmp_path, host):
    """The ratified-in-part spec's scenario: the banner says the row was NOT
    minted and names the generator; the listener still listens."""
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}")
    missing = tmp_path / "no-such-generator"
    monkeypatch.setenv(_seat.GEN_VAR, str(missing))
    result = listen("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert f"doorbell   : NOT COPIED" in result.stderr
    assert f"generator not found ({missing})" in result.stderr


def test_a_plus_seat_is_refused_its_base_and_pointed_at_the_bare_listen(fleet, monkeypatch, host):
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}")
    (fleet.root / "agents" / "sessionmail" / "new").mkdir(parents=True)
    result = listen("sessionmail", "--timeout", "0.1")
    assert result.exit_code == EX_COULD_NOT_LOOK
    assert (f"refused: sessionmail is the base of this seat's address {PLUS} - run the "
            f"bare `pager listen`, which hears {PLUS} in this session's box; to hear "
            f"sessionmail there too, run message subscription add sessionmail") in result.stderr
    assert "HEARS" not in result.stderr     # the retired way round is never advised


def test_the_retired_HEARS_variable_no_longer_opens_the_base(fleet, monkeypatch, host):
    """message-subscriptions 4.8: the variable that let a seat that OWNS its
    base listen on it is read by nothing. Set, it changes nothing: the
    refusal holds and names the subscription, D9's replacement."""
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}")
    monkeypatch.setenv("SESSIONMAIL_" + "HEARS", "sessionmail")
    (fleet.root / "agents" / "sessionmail" / "new").mkdir(parents=True)
    result = listen("sessionmail", "--timeout", "0.1")
    assert result.exit_code == EX_COULD_NOT_LOOK, result.stderr
    assert "refused: sessionmail is the base" in result.stderr
    assert "message subscription add sessionmail" in result.stderr


# ── a review's finding 5 ────────────────────────────────────────────────────

def test_a_plus_EMAIL_at_another_domain_declares_nothing(tmp_path, host):
    assert _seat.declared_address({"EMAIL": "judy+news@gmail.com"}) is None
    assert _seat.declared_address({"EMAIL": f"{PLUS}@{host}"}) == (PLUS, "EMAIL")
    assert _seat.declared_address({"EMAIL": f"{PLUS}@{host}.example.ts.net"}) == (PLUS, "EMAIL")
    assert _seat.declared_address({"TM_AGENT_ADDRESS": PLUS}) == (PLUS, "TM_AGENT_ADDRESS")
    # a foreign EMAIL falls through to the fallback, it does not block it
    assert _seat.declared_address({"EMAIL": "judy+news@gmail.com",
                                   "TM_AGENT_ADDRESS": PLUS}) == (PLUS, "TM_AGENT_ADDRESS")


def test_clearing_takes_the_address_from_both_files_the_verbs_wrote(tmp_path):
    """3.4: a 3.3b `mail.json` address (or the conversion's) and the verbs'
    `70-live.json` one both go, and the file in force is the one named."""
    env = {"SESSIONMAIL_HOME": str(tmp_path), "HOME": str(tmp_path)}
    old = session_declare("sess-a", "a+old", tmp_path)              # mail.json, as 3.3b wrote it
    live = _seat.write_address("sess-a", "a+new", env)
    assert _seat.read_address("sess-a", env) == "a+new"               # the verbs' file wins
    assert _seat.clear_address("sess-a", env) == live
    assert _seat.read_address("sess-a", env) == ""
    assert not old.exists() and not live.exists()
    assert _seat.clear_address("sess-a", env) is None                 # nothing left: nothing named


def test_a_resumed_session_that_declares_nothing_loses_its_old_declaration(fleet, monkeypatch, host):
    monkeypatch.setenv("EMAIL", f"{PLUS}@{host}")
    assert listen("--timeout", "0.1").exit_code == EX_TIMEOUT
    assert fleet.address.exists()
    monkeypatch.delenv("EMAIL")
    result = listen("--timeout", "0.1")
    assert result.exit_code == EX_TIMEOUT, result.stderr
    assert not fleet.address.exists()
    assert (f"address    : none declared - removed the address from {fleet.address}, "
            "left by an earlier run of this session") in result.stderr   # 3.3b: a key in the map
    again = listen("--timeout", "0.1")
    assert "address    :" not in again.stderr
