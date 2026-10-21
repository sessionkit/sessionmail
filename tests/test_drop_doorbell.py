"""The doorbell under the mail drop judges by the DROP's maps, never postfix's.

`_seat.doorbell(address, session, env)` answers "would mail to
<address>@<host> land in SESSION's box?". On an MTA host the answer lives in
postfix's compiled maps. On a drop host there are none: the drop asks the
map generator's print modes on every send (`_drop.prepare`) and resolves
with `qualify`, `expand` and `box_for`. So the doorbell asks the same rows
a drop send would use, the same way.

The bug these pin: a drop host with `postconf` on PATH (every Mac) asked
postfix, found no row, and printed `NOT COPIED: no alias map postfix reads
copies ...` in the very run where the drop DID copy the seat's mail into the
session's box. Every drop case below puts a postconf/postmap on PATH that
finds nothing, so a doorbell that still asks postfix fails here.

Fixtures are `alpha` and `beta` on `testhost` (convention 9). Every map, box
and generator is under tmp_path; the generator is a stand-in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fakes import write_fake
from sessionmail import _seat, _settings

HOST = "testhost"
SID = "0000aaaa-1111-2222-3333-444455556666"
OTHER = "9999ffff-1111-2222-3333-444455556666"
PLUS = "alpha+notes"

#: The stand-in generator: its print modes answer from the environment, and
#: either can be told to refuse with its own exit code and reason.
FAKE_GEN = """#!{python}
import os, sys
args = sys.argv[1:]
if os.environ.get("GEN_LOG"):
    open(os.environ["GEN_LOG"], "a").write(" ".join(args) + "\\n")
def refuse(var, said):
    rc = int(os.environ.get(var) or 0)
    if rc:
        sys.stderr.write(said + "\\n")
        sys.exit(rc)
if "--print" in args:
    refuse("FAKE_VMAILBOX_RC", "vmailbox-gen: mailbox print refused (fake)")
    sys.stdout.write(os.environ.get("FAKE_VMAILBOX", ""))
elif "--print-valias" in args:
    refuse("FAKE_VALIAS_RC", "vmailbox-gen: no sidecar dir to list live sessions (fake)")
    sys.stdout.write(os.environ.get("FAKE_VALIAS", ""))
"""

#: A postfix that is installed and knows nothing: what a Mac has. It logs
#: every call, so a test can say postfix was never asked.
FAKE_POSTCONF = """#!{python}
import os, sys
open(os.environ["POSTFIX_LOG"], "a").write("postconf " + " ".join(sys.argv[1:]) + "\\n")
sys.stdout.write("hash:/maps/valias\\n")
"""

FAKE_POSTMAP = """#!{python}
import os, sys
open(os.environ["POSTFIX_LOG"], "a").write("postmap " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(1)
"""

VMAILBOX = "".join(f"{n}@{HOST}\tagents/{n}/\n" for n in ("alpha", "beta", SID, OTHER))
VALIAS = (f"alpha@{HOST}\talpha@{HOST}, {SID}@{HOST}\n"
          f"beta@{HOST}\tbeta@{HOST}, {OTHER}@{HOST}\n"
          f"{PLUS}@{HOST}\talpha@{HOST}, {SID}@{HOST}\n")


@pytest.fixture
def mailroot(tmp_path):
    root = tmp_path / "mailroot"
    for box in ("alpha", "beta", SID, OTHER):
        for d in ("cur", "new", "tmp"):
            (root / "agents" / box / d).mkdir(parents=True)
    return root


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    write_fake(d / "vmailbox-gen", FAKE_GEN.format(python=sys.executable))
    write_fake(d / "postconf", FAKE_POSTCONF.format(python=sys.executable))
    write_fake(d / "postmap", FAKE_POSTMAP.format(python=sys.executable))
    return d


@pytest.fixture
def env(tmp_path, bin_dir, mailroot, monkeypatch):
    # The process too, not only the env handed in: postfix's half finds
    # postconf with `shutil.which` and spells the host with `gethostname`,
    # so an empty postfix sits on this "Mac" exactly as on a real one.
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("POSTFIX_LOG", str(tmp_path / "postfix.log"))
    monkeypatch.setattr("socket.gethostname", lambda: HOST)
    env = {
        "HOME": str(tmp_path / "home"),
        "SESSIONMAIL_HOME": str(tmp_path / "sessionmail"),
        "PATH": str(bin_dir),
        "TMPDIR": str(tmp_path),
        "SESSION_MAIL_ROOT": str(mailroot / "agents"), "SESSIONMAIL_ROOT": str(mailroot / "agents"),
        "SESSION_MAIL_HOST": HOST, "SESSIONMAIL_HOST": HOST,
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(bin_dir / "vmailbox-gen"),
        "FAKE_VMAILBOX": VMAILBOX,
        "FAKE_VALIAS": VALIAS,
        "POSTFIX_LOG": str(tmp_path / "postfix.log"),
        "GEN_LOG": str(tmp_path / "gen.log"),
    }
    # The carrier is MEASURED, never trusted from the variable alone: the
    # settings home is the password database's, not $HOME, so only the
    # suite's seam keeps this account's host layer out. Anything but the
    # drop here and the doorbell could reach a real MTA's maps.
    assert _settings.carrier(env).kind == "drop", _settings.carrier(env)
    return env


def postfix_asked(env) -> str:
    log = Path(env["POSTFIX_LOG"])
    return log.read_text() if log.exists() else ""


def no_postfix(live, why, env):
    """Nothing the drop says names postfix, and postfix was never run."""
    assert "postfix" not in why.lower(), (live, why)
    assert postfix_asked(env) == "", postfix_asked(env)


# ── the drop's own resolution ────────────────────────────────────────────────

def test_a_seat_the_drop_copies_into_the_session_box_rings(env):
    """The reported bug: the drop copies alpha's mail into SID's box, so the
    doorbell is LIVE, with postfix installed and empty beside it."""
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is True, why
    assert why.startswith(f"alpha@{HOST} -> this box, in the mail drop's alias map")
    assert f"{SID}@{HOST} -> agents/{SID}/" in why
    no_postfix(live, why, env)


def test_a_plus_seat_rings_on_its_own_plus_row(env):
    """The address keeps its `+` (task-address-doorbell D2): the plus row is
    the one asked."""
    live, why = _seat.doorbell(PLUS, SID, env)
    assert live is True, why
    assert why.startswith(f"{PLUS}@{HOST} -> this box")
    no_postfix(live, why, env)


def test_no_alias_row_for_the_seat_is_NOT_COPIED_naming_the_drop_map(env):
    env["FAKE_VALIAS"] = f"beta@{HOST}\tbeta@{HOST}, {OTHER}@{HOST}\n"
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is False
    assert why == (f"NOT COPIED: no row in the mail drop's alias map (the map generator's "
                   f"--print-valias) copies alpha@{HOST} into this box")
    no_postfix(live, why, env)


def test_a_row_that_copies_into_another_session_is_NOT_COPIED(env):
    live, why = _seat.doorbell("beta", SID, env)
    assert live is False
    assert why.startswith("NOT COPIED: ") and f"beta@{HOST}" in why
    no_postfix(live, why, env)


def test_copied_but_the_session_has_no_mailbox_row_is_NOT_DELIVERABLE(env):
    env["FAKE_VMAILBOX"] = "".join(f"{n}@{HOST}\tagents/{n}/\n" for n in ("alpha", "beta"))
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is False
    assert why.startswith(f"NOT DELIVERABLE: alpha@{HOST} -> this box in the mail drop's alias "
                          f"map, but no row in its mailbox map (the map generator's --print) "
                          f"delivers {SID}@{HOST} to an existing maildir")
    no_postfix(live, why, env)


def test_copied_but_the_session_box_is_not_a_maildir_is_NOT_DELIVERABLE(env, mailroot):
    """The drop resolves a box and never mints one: a row whose maildir is
    gone does not deliver, so it does not ring."""
    for d in ("cur", "new", "tmp"):
        (mailroot / "agents" / SID / d).rmdir()
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is False
    assert why.startswith("NOT DELIVERABLE: ") and "never makes a box" in why
    no_postfix(live, why, env)


def test_a_plus_seat_whose_base_has_no_box_is_NOT_DELIVERABLE(env):
    """The drop delivers a send to all of its boxes or to none (`plan`). A
    plus row lists its base first, and the generator does not ask that the
    base be a box. With no box for the base, a send to the plus address
    delivers nothing, the session's copy included, so the doorbell does not
    ring, and it names the box that stops it."""
    from sessionmail import _drop
    env["FAKE_VALIAS"] = f"gamma+t@{HOST}\tgamma@{HOST}, {SID}@{HOST}\n"
    live, why = _seat.doorbell("gamma+t", SID, env)
    assert live is False, why
    assert why.startswith(f"NOT DELIVERABLE: gamma+t@{HOST} -> this box in the mail drop's "
                          f"alias map, but the drop delivers to all of a send's boxes or none, "
                          f"and no row in its mailbox map delivers gamma@{HOST} to an existing "
                          f"maildir"), why
    no_postfix(live, why, env)
    maps = _drop.prepare(env)                   # the drop's own send agrees
    with pytest.raises(_drop.NotDelivered):
        _drop.plan([f"gamma+t@{HOST}"], maps)


def test_every_box_a_LIVE_seat_names_is_one_a_drop_send_writes(env):
    """LIVE means what the drop's own send does: `plan` resolves the seat's
    address to the session's box, and refuses nothing."""
    from sessionmail import _drop
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is True, why
    finals = {final for _o, final, _box in _drop.plan([f"alpha@{HOST}"], _drop.prepare(env))}
    assert f"{SID}@{HOST}" in finals


def test_an_alias_the_drop_refuses_is_NOT_DELIVERABLE_with_the_drops_reason(env):
    env["FAKE_VALIAS"] = f"alpha@{HOST}\talpha@{HOST}, {SID}@elsewhere\n"
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is False
    assert why.startswith(f"NOT DELIVERABLE: the mail drop refuses alpha@{HOST}: ")
    assert "not on this host" in why
    no_postfix(live, why, env)


# ── the generator refuses ────────────────────────────────────────────────────

def test_a_refused_alias_print_carries_the_generators_reason(env):
    """The drop sends with no alias map (seat mail lands in the seat's own
    box only), so the doorbell is NOT COPIED, and the reason is the
    generator's, never postfix's."""
    env["FAKE_VALIAS_RC"] = "3"
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is False
    assert why.startswith("NOT COPIED: the mail drop has no alias map (generator exit 3: "
                          "vmailbox-gen: no sidecar dir to list live sessions (fake))")
    assert f"alpha@{HOST}" in why
    no_postfix(live, why, env)


def test_a_refused_mailbox_print_is_not_measured_with_the_generators_reason(env):
    env["FAKE_VMAILBOX_RC"] = "2"
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is None
    assert why.startswith("the mail drop could not read its maps: ")
    assert "exit 2" in why and "mailbox print refused (fake)" in why
    no_postfix(live, why, env)


def test_no_generator_at_all_is_not_measured_and_says_so(env, tmp_path):
    env["SESSIONMAIL_VMAILBOX_GEN"] = str(tmp_path / "no-such-generator")
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is None
    assert why.startswith("the mail drop could not read its maps: no map generator")
    no_postfix(live, why, env)


# ── the other two carriers ───────────────────────────────────────────────────

def test_a_refused_carrier_is_not_measured_and_says_the_carriers_why(env):
    env["SESSIONMAIL_SENDMAIL"] = "not-a-carrier"
    live, why = _seat.doorbell("alpha", SID, env)
    assert live is None
    assert why.startswith("the carrier is refused: $SESSIONMAIL_SENDMAIL is \"not-a-carrier\"")
    assert postfix_asked(env) == ""


def test_an_mta_carrier_still_asks_postfix(env, bin_dir):
    """The MTA path is today's: postfix's maps, postfix's words. The drop's
    rows (which WOULD copy) are not consulted."""
    env["SESSIONMAIL_SENDMAIL"] = str(bin_dir / "sendmail")
    live, why = _seat.doorbell("alpha", SID, env)
    assert (live, why) == (False, f"NOT COPIED: no alias map postfix reads copies "
                                  f"alpha@{HOST} into this box")
    assert f"postmap -q alpha@{HOST} hash:/maps/valias" in postfix_asked(env)


# ── the banner a stranger reads ──────────────────────────────────────────────

def test_the_listen_banner_on_a_drop_host_reads_LIVE(env, monkeypatch):
    """End to end, the line that was false: `pager listen` on a drop host
    whose drop copies the seat's mail into the session's box says LIVE."""
    from click.testing import CliRunner
    from sessionmail import pager
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    for var in ("EMAIL", "TM_AGENT_ADDRESS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "alpha")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setenv(pager.POLL_VAR, "0.05")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: None)
    assert _settings.carrier(os.environ).kind == "drop"     # the listen's own env, measured
    result = CliRunner().invoke(pager.cli, ["listen", "--timeout", "0.1"], prog_name="pager")
    assert f"doorbell   : LIVE: alpha@{HOST} -> this box, in the mail drop's alias map" \
        in result.stderr, result.stderr
    assert "postfix reads" not in result.stderr
    assert postfix_asked(env) == ""


# ── session start: a detaching birth never waits on the generator ───────────

def generator_ran(env) -> str:
    log = Path(env["GEN_LOG"])
    return log.read_text() if log.exists() else ""


def as_process_env(env, monkeypatch):
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    for var in ("EMAIL", "TM_AGENT_ADDRESS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_AGENT", "alpha")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))


def test_the_session_start_hook_runs_no_generator_on_a_drop_host(env, monkeypatch):
    """`pager hook start` births with regen="detach", which never waits on
    the generator. The drop's doorbell IS a generator run (`_drop.prepare`),
    so at session start it is not rung: the real doorbell path, not a fake,
    and no generator runs at all, nor postfix."""
    import json
    from click.testing import CliRunner
    from sessionmail import pager
    as_process_env(env, monkeypatch)
    assert _settings.carrier(os.environ).kind == "drop"     # the hook's own env, measured
    data = {"hook_event_name": "SessionStart", "source": "startup", "session_id": SID}
    result = CliRunner().invoke(pager.cli, ["hook", "start"], input=json.dumps(data),
                                prog_name="pager")
    assert result.exit_code == 0, (result.output, result.exception)
    assert generator_ran(env) == "", generator_ran(env)
    assert postfix_asked(env) == ""


def test_a_detaching_birth_on_a_drop_host_says_when_the_doorbell_is_measured(env):
    from sessionmail import _birth
    seat = _seat.Seat("alpha", "test", Path(env["HOME"]))
    birth = _birth.ensure_session_box(SID, seat, env=env, make_root=False, regen="detach")
    assert birth.error is None, birth.error
    assert birth.live is None
    assert ("  doorbell   : not measured: not measured at session start; the first pager "
            "listen measures it") in birth.bell_lines
    assert generator_ran(env) == "", generator_ran(env)
    assert postfix_asked(env) == ""


def test_a_waiting_birth_on_a_drop_host_still_rings_the_drops_doorbell(env):
    """The control: a birth that waits (the listen's) asks the drop, so the
    generator log above would have caught a run."""
    from sessionmail import _birth
    seat = _seat.Seat("alpha", "test", Path(env["HOME"]))
    birth = _birth.ensure_session_box(SID, seat, env=env, make_root=False, regen="wait")
    assert birth.live is True, birth.bell_lines
    assert "--print-valias" in generator_ran(env)
