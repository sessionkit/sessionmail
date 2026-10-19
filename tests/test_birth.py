"""SM-498 build-001: `_birth.ensure_session_box`, the ONE birth path (R5).

The function is a pure move of what `pager listen` did inline; the listen's
own tests (test_pager_listen.py, test_task_address_doorbell.py,
test_pager_subscribe_at_birth.py) pin its banner, unedited. These pin the
function on its own, as the SessionStart hook (build-002) will call it:

  - born / not born, and the mail root it may or may not create (R4);
  - declare / clear, and declare BEFORE the doorbell (D1);
  - the doorbell: LIVE -> no generator; NOT COPIED -> generator, re-measured;
    a fresh birth -> generator even when nothing was measured (D9);
  - a seatless birth that subscribed owes one rebuild, a seated one does not;
  - no session -> nothing at all.

`_seat.doorbell` and `_seat.regen_maps` are faked here, through the module
attribute - the same attribute `_birth` calls - so a counter sees every run.
The real generator and postfix maps are test_task_address_doorbell.py's.
"""

from __future__ import annotations

import os

from pathlib import Path
from types import SimpleNamespace

import pytest

from sessionmail import _birth, _seat, pager

PLUS = "sessionmail+sm-498.worker"
SID = "sess-498"
NOT_COPIED = (False, f"NOT COPIED: no alias map postfix reads copies {PLUS}@hq into this box")
LIVE = (True, f"{PLUS}@hq -> this box, in /fake/valias; {SID}@hq -> {SID}/, in /fake/vmailbox")
UNMEASURED = (None, "no postconf/postmap on PATH to read the alias maps")
SEAT = SimpleNamespace(address=PLUS)


class Rig:
    """A mail root and a home under tmp_path, and the doorbell and generator
    faked: `rings` answers each doorbell in turn (the last one repeats);
    `calls` records what ran, in order."""

    def __init__(self, tmp_path: Path, monkeypatch, rings=(LIVE,)):
        self.root = tmp_path / "mail" / "agents"
        self.home = tmp_path / "sessionmail"
        # SM-674 (merged at land-prep): a "wait" birth asks for the MagicDNS
        # suffix in ENV and skips every regen without one; the conftest's
        # hermetic suffix, so these rigs regenerate as they did.
        self.env = {"SESSION_MAIL_ROOT": str(self.root), "SESSIONMAIL_HOME": str(self.home),
                    "MAGICDNS_SUFFIX": os.environ["MAGICDNS_SUFFIX"]}
        self.rings = list(rings)
        self.calls: list[str] = []
        self.address_at_regen: list[bool] = []
        monkeypatch.setattr(_seat, "doorbell", self.doorbell)
        monkeypatch.setattr(_seat, "regen_maps", self.regen)

    @property
    def box(self) -> Path:
        return self.root / SID

    def address(self) -> str:
        """The declared address, read the way the generator reads it: since
        session-settings (merged at land-prep) it is the `address` key of
        the session's layer, not an `address` file."""
        return _seat.read_address(SID, self.env)

    def doorbell(self, address, session, env=None):
        assert (address, session) == (PLUS, SID)
        self.calls.append("doorbell")
        return self.rings.pop(0) if len(self.rings) > 1 else self.rings[0]

    def regen(self, root, env=None):
        assert root == self.root
        self.calls.append("regen")
        self.address_at_regen.append(self.address() == PLUS)
        return "generator ran in 0.1s (3 boxes)"

    def ensure(self, seat=SEAT, session=SID, **kw):
        return _birth.ensure_session_box(session, seat, env=self.env, **kw)


def maildir(box: Path) -> None:
    for d in ("cur", "new", "tmp"):
        (box / d).mkdir(parents=True, exist_ok=True)


# ── born / not born ─────────────────────────────────────────────────────────

def test_a_fresh_session_is_born_and_its_birth_runs_the_generator(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    birth = rig.ensure()
    assert birth.error is None
    assert birth.born is True and birth.box == rig.box
    assert all((rig.box / d).is_dir() for d in ("cur", "new", "tmp"))
    assert rig.calls == ["doorbell", "regen", "doorbell"]          # re-measured
    assert (birth.live, birth.regen) == (True, "ran")
    assert birth.regen_line == "generator ran in 0.1s (3 boxes)"
    assert birth.bell_lines[-1] == (f"  doorbell   : LIVE: {LIVE[1]}; "
                                    "generator ran in 0.1s (3 boxes)")


def test_a_birth_runs_the_generator_even_when_the_doorbell_already_reads_LIVE(tmp_path,
                                                                            monkeypatch):
    """D9: "a listen that births the box runs the generator in the same step"
    - the box is new, so its mailbox row may not be; LIVE is not re-measured."""
    rig = Rig(tmp_path, monkeypatch, rings=(LIVE,))
    birth = rig.ensure()
    assert birth.born and rig.calls == ["doorbell", "regen"]
    assert birth.live is True and birth.regen == "ran"


def test_an_existing_box_is_not_born_again_and_LIVE_runs_no_generator(tmp_path, monkeypatch):
    """Success criterion 3's mechanism: a box already born (by the hook) and a
    doorbell already LIVE cost the next caller no regen."""
    rig = Rig(tmp_path, monkeypatch, rings=(LIVE,))
    maildir(rig.box)
    (rig.box / "new" / "1.mail").write_text("kept")
    birth = rig.ensure()
    assert birth.born is False and birth.error is None
    assert rig.calls == ["doorbell"]
    assert (birth.live, birth.regen, birth.regen_line) == (True, "", "")
    assert birth.bell_lines == [f"  doorbell   : LIVE: {LIVE[1]}"]
    assert (rig.box / "new" / "1.mail").read_text() == "kept"


def test_NOT_COPIED_on_an_existing_box_runs_the_generator_and_measures_again(tmp_path,
                                                                           monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    maildir(rig.box)
    birth = rig.ensure()
    assert not birth.born
    assert rig.calls == ["doorbell", "regen", "doorbell"]
    assert birth.live is True and birth.regen == "ran"


def test_NOT_COPIED_that_survives_the_generator_is_said_with_its_reason(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED,))
    maildir(rig.box)
    birth = rig.ensure()
    assert rig.calls == ["doorbell", "regen", "doorbell"]
    assert birth.live is False
    assert birth.bell_lines[-1] == (
        "  doorbell   : NOT COPIED - the seat's mail will not reach this box: "
        f"no alias map postfix reads copies {PLUS}@hq into this box; "
        "generator ran in 0.1s (3 boxes)")


def test_an_unmeasured_doorbell_on_an_existing_box_runs_no_generator(tmp_path, monkeypatch):
    """No postconf/postmap: `None` is "not measured", never "missing", so only a
    birth would run the generator (build brief N5: test what the code does)."""
    rig = Rig(tmp_path, monkeypatch, rings=(UNMEASURED,))
    maildir(rig.box)
    birth = rig.ensure()
    assert rig.calls == ["doorbell"]
    assert birth.live is None and birth.regen == ""
    assert birth.bell_lines[-1] == f"  doorbell   : not measured: {UNMEASURED[1]}"


def test_box_label_names_the_sessions_box_in_the_doorbell_line(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED,))
    maildir(rig.box)
    label = f"this session's box {rig.box}"
    birth = rig.ensure(box_label=label)
    assert "this box" not in birth.bell_lines[-1]
    assert f"copies {PLUS}@hq into {label}" in birth.bell_lines[-1]


# ── the mail root, and a refused birth (R4) ────────────────────────────────

def test_make_root_False_with_no_mail_root_is_an_error_and_creates_nothing(tmp_path,
                                                                         monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    birth = rig.ensure(make_root=False)
    assert birth.error == f"no mail root at {rig.root} - no box born"
    assert birth.born is False and birth.box == rig.box
    assert not rig.root.exists() and not (tmp_path / "mail").exists()
    assert rig.calls == [] and not rig.home.exists()       # nothing after the refusal ran


def test_make_root_False_with_a_mail_root_births_as_usual(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.root.mkdir(parents=True)
    birth = rig.ensure(make_root=False)
    assert birth.error is None and birth.born and (rig.box / "new").is_dir()


def test_make_root_True_creates_the_mail_root_as_the_listen_always_has(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    birth = rig.ensure()
    assert birth.error is None and birth.born and (rig.box / "new").is_dir()


def test_a_birth_the_filesystem_refuses_is_an_error_and_nothing_after_it_runs(tmp_path,
                                                                            monkeypatch):
    rig = Rig(tmp_path, monkeypatch)

    def refuse(root, name):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(_seat, "birth_box", refuse)
    birth = rig.ensure()
    assert birth.error == (f"could not create the session box at {rig.box}: "
                           "[Errno 13] Permission denied")
    assert birth.born is False and rig.calls == [] and birth.bell_lines == []


# ── declare / clear (D1) ────────────────────────────────────────────────────

def test_a_plus_address_is_declared_BEFORE_the_generator_runs(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    rig.env["EMAIL"] = f"{PLUS}@hq"
    monkeypatch.setattr(_seat, "short_host", lambda: "hq")
    birth = rig.ensure()
    assert rig.address() == PLUS
    assert rig.address_at_regen == [True]                  # D1: the regen saw it
    head = f"  address    : {PLUS} (from EMAIL) declared in "
    tail = " - the alias map generator writes its row"
    line = birth.bell_lines[0]
    assert line.startswith(head) and line.endswith(tail), line
    assert Path(line[len(head):-len(tail)]).is_file()      # names the file it wrote


def test_no_declaration_clears_one_left_by_an_earlier_run(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    where = _seat.write_address(SID, PLUS, rig.env)
    assert rig.address() == PLUS
    birth = rig.ensure()
    assert rig.address() == ""
    assert birth.bell_lines[0] == (f"  address    : none declared - removed the address from "
                                   f"{where}, left by an earlier run of this session; its row "
                                   "goes at the next regen")


def test_no_declaration_and_nothing_to_clear_says_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    birth = rig.ensure()
    assert not any(line.startswith("  address") for line in birth.bell_lines)


# ── subscribe-at-birth (D4) and the seatless rebuild ───────────────────────

def test_the_birth_file_is_reported_only_by_the_call_that_births(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(_birth, "birth_subscriptions",
                        lambda session, env=None, via=None: seen.append((session, env, via))
                        or (["  subscribe  : x"], True))
    first = rig.ensure()
    second = rig.ensure()
    assert seen == [(SID, rig.env, "pager listen")]       # once, with the caller's env
    assert first.sub_lines == ["  subscribe  : x"]          # the birth's regen routes it
    assert second.sub_lines == []


def test_a_seatless_birth_that_subscribed_rebuilds_once(tmp_path, monkeypatch):
    from sessionmail import _subscribe
    rig = Rig(tmp_path, monkeypatch)
    rebuilt = []
    monkeypatch.setattr(_birth, "birth_subscriptions",
                        lambda session, env=None, via=None: (["  subscribe  : x"], True))
    monkeypatch.setattr(_subscribe, "rebuild",
                        lambda env=None: rebuilt.append(env) or (True, "valias rebuilt"))
    birth = rig.ensure(seat=None)
    assert birth.born and rig.calls == []                 # no seat: no doorbell, no regen
    assert rebuilt == [rig.env]
    assert birth.sub_lines == ["  subscribe  : x", "  map        : valias rebuilt (--valias-only)"]
    assert birth.live is None and birth.bell_lines == []


def test_a_seatless_rebuild_that_fails_says_the_subscriptions_stand(tmp_path, monkeypatch):
    from sessionmail import _subscribe
    rig = Rig(tmp_path, monkeypatch)
    monkeypatch.setattr(_birth, "birth_subscriptions",
                        lambda session, env=None, via=None: (["  subscribe  : x"], True))
    monkeypatch.setattr(_subscribe, "rebuild", lambda env=None: (False, "generator not found"))
    birth = rig.ensure(seat=None)
    assert birth.sub_lines[-1] == ("  map        : NOT rebuilt - generator not found; the "
                                   "subscriptions stand, and the next rebuild (any add, remove "
                                   "or birth) routes them")


# ── no session ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("session", ["", "-"])
def test_no_session_is_no_box_and_nothing_else(tmp_path, monkeypatch, session):
    rig = Rig(tmp_path, monkeypatch)
    rig.env["EMAIL"] = f"{PLUS}@hq"
    birth = rig.ensure(session=session)
    assert birth == _birth.Birth()
    assert rig.calls == [] and not rig.root.exists() and not rig.home.exists()


# ── the move kept pager's names ────────────────────────────────────────────

def test_pager_re_exports_what_moved():
    assert pager.SUBSCRIBE_VAR is _birth.SUBSCRIBE_VAR == "SESSIONMAIL_SUBSCRIBE"
    # land-prep: master's task-3.4 reader replaced D4's writer, moved the same way
    assert pager.birth_subscriptions is _birth.birth_subscriptions
    assert not hasattr(_birth, "subscribe_at_birth")


# ── build-002: regen="detach" and the born record ──────────────────────────

def test_detach_starts_the_generator_and_does_not_wait_or_measure_again(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    started = []
    monkeypatch.setattr(_birth, "detach_regen",
                        lambda root, env=None, record=None: started.append((root, record))
                        or (True, "generator started in the background (pid 1)"))
    birth = rig.ensure(regen="detach", record_source="startup")
    assert rig.calls == ["doorbell"]                     # no regen_maps, no second ring
    assert birth.regen == "detached" and birth.live is False
    assert started == [(rig.root, birth.record)]
    assert birth.record.read_text().split()[1:] == ["startup", "regen=detached"]
    assert birth.bell_lines[-1].endswith("; generator started in the background (pid 1)")


def test_detach_on_a_LIVE_existing_box_starts_nothing_and_records_nothing(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(LIVE,))
    maildir(rig.box)
    monkeypatch.setattr(_birth, "detach_regen", lambda *a, **kw: pytest.fail("detached"))
    birth = rig.ensure(regen="detach", record_source="compact")
    assert birth.regen == "" and birth.record is None
    assert not _birth.born_record(SID, rig.env).exists()


def test_a_seatless_detaching_birth_that_subscribed_detaches_instead_of_rebuilding(tmp_path,
                                                                                 monkeypatch):
    from sessionmail import _subscribe
    rig = Rig(tmp_path, monkeypatch)
    monkeypatch.setattr(_birth, "birth_subscriptions",
                        lambda session, env=None, via=None: (["  subscribe  : x"], True))
    monkeypatch.setattr(_subscribe, "rebuild", lambda env=None: pytest.fail("waited"))
    monkeypatch.setattr(_birth, "detach_regen", lambda *a, **kw: (True, "started"))
    birth = rig.ensure(seat=None, regen="detach", record_source="startup")
    assert birth.regen == "detached" and birth.sub_lines == ["  subscribe  : x"]


def test_a_seatless_birth_with_nothing_to_route_records_regen_skipped(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    birth = rig.ensure(seat=None, regen="detach", record_source="startup")
    assert birth.record.read_text().split()[1:] == ["startup", "regen=skipped"]


def test_the_listen_writes_no_born_record(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    birth = rig.ensure()
    assert birth.record is None and not _birth.born_record(SID, rig.env).exists()


# ── land-prep: the seams with master (SM-674 suffix, 3.3b conversion) ──────

def _no_suffix(monkeypatch):
    from sessionmail import _magicdns
    monkeypatch.setattr(_magicdns, "lookup", lambda env=None: (None, "no tailscale here"))


def test_without_a_suffix_a_waiting_birth_runs_no_generator_and_says_so(tmp_path, monkeypatch):
    """SM-674: the maps' FQDN rows need the suffix, so with none the birth
    regenerates nothing - the listen's words, kept - and reports it."""
    _no_suffix(monkeypatch)
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    birth = rig.ensure()
    assert birth.born and birth.suffix_unknown is True
    assert rig.calls == ["doorbell"]                     # no regen, no second ring
    assert birth.regen == "" and birth.live is False
    assert birth.bell_lines[-1].endswith("; generator NOT run (MagicDNS suffix unknown)")


def test_with_a_suffix_the_birth_says_nothing_of_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    birth = rig.ensure()
    assert birth.suffix_unknown is False and birth.regen == "ran"


def test_without_a_suffix_a_seatless_birth_that_subscribed_does_not_rebuild(tmp_path,
                                                                             monkeypatch):
    from sessionmail import _subscribe
    _no_suffix(monkeypatch)
    rig = Rig(tmp_path, monkeypatch)
    monkeypatch.setattr(_birth, "birth_subscriptions",
                        lambda session, env=None, via=None: (["  subscribe  : x"], True))
    monkeypatch.setattr(_subscribe, "rebuild", lambda env=None: pytest.fail("rebuilt"))
    birth = rig.ensure(seat=None)
    assert birth.sub_lines == ["  subscribe  : x",
                               "  map        : NOT rebuilt - MagicDNS suffix unknown; the "
                               "subscriptions stand, and the next rebuild with a suffix "
                               "routes them"]


@pytest.mark.parametrize("converted, rebuilds", [(True, 1), (False, 0)])
def test_a_conversion_owes_the_map_one_rebuild_when_no_generator_ran(tmp_path, monkeypatch,
                                                                     converted, rebuilds):
    """Session-settings 3.3b (merged at land-prep): the listen's conversion
    wrote the layer, so like a subscription it owes one rebuild - here on a
    seatless session whose box is already born: no doorbell, no regen."""
    from sessionmail import _subscribe
    rig = Rig(tmp_path, monkeypatch)
    maildir(rig.box)
    rebuilt = []
    monkeypatch.setattr(_subscribe, "rebuild",
                        lambda env=None: rebuilt.append(env) or (True, "valias rebuilt"))
    birth = rig.ensure(seat=None, converted=converted)
    assert len(rebuilt) == rebuilds
    assert birth.sub_lines == (["  map        : valias rebuilt (--valias-only)"] if rebuilds else [])


def test_a_conversion_is_routed_by_the_birth_regen_and_not_rebuilt_again(tmp_path, monkeypatch):
    from sessionmail import _subscribe
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED, LIVE))
    monkeypatch.setattr(_subscribe, "rebuild", lambda env=None: pytest.fail("rebuilt"))
    birth = rig.ensure(converted=True)
    assert birth.regen == "ran" and rig.calls == ["doorbell", "regen", "doorbell"]


def test_a_detaching_birth_never_asks_for_the_suffix(tmp_path, monkeypatch):
    """The lookup can cost the hook's whole budget (0.335 s measured on hq),
    so a detaching birth leaves it to its child."""
    from sessionmail import _magicdns
    monkeypatch.setattr(_magicdns, "lookup", lambda env=None: pytest.fail("asked"))
    monkeypatch.setattr(_birth, "detach_regen", lambda *a, **kw: (True, "started"))
    rig = Rig(tmp_path, monkeypatch, rings=(NOT_COPIED,))
    birth = rig.ensure(regen="detach", record_source="startup")
    assert birth.regen == "detached" and birth.suffix_unknown is False


def test_the_detached_child_without_a_suffix_runs_no_generator_and_records_why(tmp_path,
                                                                               monkeypatch):
    _no_suffix(monkeypatch)
    monkeypatch.setattr(_seat, "regen_maps", lambda *a, **kw: pytest.fail("generator ran"))
    record = tmp_path / "born"
    record.write_text("2030-12-17T23:34:56+11:00 startup regen=detached\n")
    assert _birth._main(["--regen", str(tmp_path), "--record", str(record)]) == 0
    assert record.read_text().splitlines()[1] == ("generator NOT run (MagicDNS suffix unknown: "
                                                  "no tailscale here)")


def test_the_detached_child_with_a_suffix_runs_the_generator(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(_seat, "regen_maps", lambda root, env=None: ran.append(root) or "ran ok")
    record = tmp_path / "born"
    record.write_text("x startup regen=detached\n")
    _birth._main(["--regen", str(tmp_path), "--record", str(record)])
    assert ran == [tmp_path] and record.read_text().splitlines()[1] == "ran ok"
