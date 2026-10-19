"""message box archive|unarchive (SM-790): dead session boxes, moved out of
notmuch's scan, and back.

Every test runs in a SCRATCH mail root under tmp_path: the boxes, the map
dir, the sidecars, the notmuch configs and the archive. The generator is the
REAL one, run through a wrapper that writes its maps into the scratch map dir
with no postmap and a made-up host, as `test_postfix_vmailbox_gen.py` holds
it. `--maps` is always given, so postconf is never asked (`_postfix_maps` is
patched to fail the test if it is). No real box is ever moved.

The numbers in the test names follow the design's §9 test plan.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _archive, _mcp, _seat, memory, message
from sessionmail.message import cli

HOST = "testhost"
FQDN_TAIL = "hermetic.invalid"          # conftest's MAGICDNS_SUFFIX
DAY = 86400.0

A = "a0000000-1111-2222-3333-444455556666"     # dead, quiet: a candidate
B = "b0000000-1111-2222-3333-444455556666"     # dead, quiet: a candidate
C = "c0000000-1111-2222-3333-444455556666"     # dead, quiet: a candidate
LIVE = "d0000000-1111-2222-3333-444455556666"  # a live sidecar owns it
FRESH = "e0000000-1111-2222-3333-444455556666"  # dead, but mail yesterday
SHORT = "f0000000"                             # an 8-hex box

WRAPPER = """#!/bin/sh
# The real generator, at arm's length: its maps go to the scratch map dir, no
# postmap, a made-up host. Switches a test sets in the environment:
#   WRAP_FAIL_VALIAS  --print-valias exits 3 (it refused)
#   WRAP_NOOP         a full run exits 0 and writes nothing (6b)
#   WRAP_RM_MAP       a full run removes the mailbox map after it (6c)
#   WRAP_APPEND       a full run appends this line to the mailbox map (6d)
#   WRAP_FAIL_AFTER   a full run after this many full runs fails, writing nothing (9c)
for a in "$@"; do
  if [ "$a" = --print-valias ]; then
    [ -n "$WRAP_FAIL_VALIAS" ] && exit 3
    exec "$REAL_GEN" "$@" --host testhost --out "$WRAP_MAPS/vmailbox" --no-postmap
  fi
done
n=$(( $(cat "$WRAP_MAPS/.runs" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$WRAP_MAPS/.runs"
[ -n "$WRAP_NOOP" ] && exit 0
if [ -n "$WRAP_FAIL_AFTER" ] && [ "$n" -gt "$WRAP_FAIL_AFTER" ]; then exit 1; fi
"$REAL_GEN" "$@" --host testhost --out "$WRAP_MAPS/vmailbox" --no-postmap >/dev/null
rc=$?
[ -n "$WRAP_RM_MAP" ] && rm -f "$WRAP_MAPS/vmailbox"
[ -n "$WRAP_APPEND" ] && printf '%s\\n' "$WRAP_APPEND" >> "$WRAP_MAPS/vmailbox"
exit $rc
"""

FAIL_AND_MARK = """#!/bin/sh
echo "$0 $*" >> "$MARKS"
exit 1
"""


def _dead_pid() -> int:
    pid = 4_000_000
    while _seat._pid_alive(pid):
        pid += 1
    return pid


def mail_file(leaf: Path, name: str, mid: str, subject: str, when: float) -> Path:
    leaf.mkdir(parents=True, exist_ok=True)
    f = leaf / name
    f.write_bytes(f"Message-ID: {mid}\nSubject: {subject}\nFrom: a@{HOST}\n\nbody {mid}\n".encode())
    os.utime(f, (when, when))
    return f


@dataclass
class World:
    tmp: Path
    mail: Path
    agents: Path
    archive: Path
    maps: Path
    cars: Path
    monkeypatch: pytest.MonkeyPatch

    # ── building the scratch world ──
    def box(self, name: str, age_days: float = 30.0, files: int = 2) -> Path:
        d = self.agents / name
        when = time.time() - age_days * DAY
        for i in range(files):
            leaf = "cur" if i % 2 else "new"
            info = ":2,S" if leaf == "cur" else ""
            mail_file(d / leaf, f"{1000 + i}.M{i}P1.{HOST}{info}", f"<{name[:8]}.{i}@test>",
                      f"old mail {i}", when)
        for leaf in _archive.LEAVES:
            (d / leaf).mkdir(parents=True, exist_ok=True)
            os.utime(d / leaf, (when, when))
        return d

    def store(self, store: str, project: str, age_days: float = 30.0) -> None:
        s = self.agents / store
        (s / ".tm-store").parent.mkdir(parents=True, exist_ok=True)
        (s / ".tm-store").write_text("")
        self.box(store, age_days)
        self.box(f"{store}/{project}", age_days)

    def live(self, sid: str) -> None:
        car = {"pid": os.getpid(), "sessionId": sid, "procStart": "Sun Jun  9 12:34:56 2030"}
        (self.cars / f"{os.getpid()}-{sid[:8]}.json").write_text(json.dumps(car))

    def dead(self, sid: str) -> None:
        car = {"pid": _dead_pid(), "sessionId": sid, "procStart": "Sun Jun  9 12:34:56 2030"}
        (self.cars / f"dead-{sid[:8]}.json").write_text(json.dumps(car))

    def regen(self) -> None:
        line = _seat.regen_maps(self.agents)
        assert "ran" in line, line

    def runs(self) -> int:
        return int((self.maps / ".runs").read_text())

    def vmailbox(self) -> str:
        return (self.maps / "vmailbox").read_text()

    def rows(self, sid: str) -> list[str]:
        return _archive.mailbox_keys(self.vmailbox(), sid)

    def ledger(self) -> list[list[str]]:
        try:
            return [l.split("\t") for l in (self.archive / _archive.LEDGER).read_text().splitlines()]
        except FileNotFoundError:
            return []

    def notmuch(self, body: str) -> Path:
        cfg = Path(os.environ["HOME"]) / ".notmuch-config"
        cfg.write_text(body)
        return cfg

    # ── running ──
    def archive_run(self, *args: str):
        return CliRunner().invoke(cli, ["box", "archive", *args, "--maps", str(self.maps)],
                                  prog_name="message")

    def unarchive_run(self, *args: str):
        return CliRunner().invoke(cli, ["box", "unarchive", *args, "--maps", str(self.maps)],
                                  prog_name="message")


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    mail = tmp_path / "mail"
    agents = mail / "agents"
    agents.mkdir(parents=True)
    maps = tmp_path / "maps"
    maps.mkdir()
    cars = tmp_path / "sidecars"
    cars.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrap = bin_dir / "vmailbox-gen-wrap"
    wrap.write_text(WRAPPER)
    wrap.chmod(0o755)
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as real:
        monkeypatch.setenv("REAL_GEN", str(real))
    monkeypatch.setenv("WRAP_MAPS", str(maps))
    monkeypatch.setenv(_seat.GEN_VAR, str(wrap))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(agents))
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(cars))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("NOTMUCH_CONFIG", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    for var in ("WRAP_FAIL_VALIAS", "WRAP_NOOP", "WRAP_RM_MAP", "WRAP_APPEND", "WRAP_FAIL_AFTER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_seat, "_postfix_maps",
                        lambda p: pytest.fail(f"postconf was asked for {p}: --maps is given"))
    monkeypatch.setattr(_archive, "HOOKS", {})
    return World(tmp_path, mail, agents, mail / "archive", maps, cars, monkeypatch)


@pytest.fixture
def fleet(world: World) -> World:
    """Three dead quiet session boxes, a live one, a fresh one, a seat, and
    the maps written once, as a birth would have."""
    for sid in (A, B, C, LIVE, FRESH):
        world.box(sid, age_days=1 if sid == FRESH else 30)
    world.box("alpha")
    for sid in (A, B, C, FRESH):
        world.dead(sid)
    world.live(LIVE)
    world.regen()
    return world


def manifest(root: Path) -> dict[str, tuple]:
    """Every regular file under ROOT: (mode, size, mtime_ns, sha256), and
    every directory's name; the bytes, not the inode."""
    out: dict[str, tuple] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file():
            st = p.stat()
            out[rel] = (st.st_mode, st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        elif p.is_dir():
            out[rel] = ("dir",)
    return out


def lines(out: str, word: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith(word)]


# ── 1. census ──────────────────────────────────────────────────────────────

def test_1_the_listing_gives_one_verdict_row_per_box_and_a_total_by_reason(world):
    world.box(A)
    world.dead(A)
    world.box(SHORT)
    world.box("alpha")
    world.box("kaizen")
    world.store("store1", "proj1")
    world.regen()
    res = world.archive_run("--all")
    assert res.exit_code == 0, res.output
    rows = lines(res.stdout, "CANDIDATE") + lines(res.stdout, "KEEP")
    names = sorted(r.split()[1] for r in rows)
    assert names == sorted([A, SHORT, "alpha", "kaizen", "store1", "store1/proj1"])
    quiet = "(a) no live sidecar  (b) no alias row, now or next  (c) quiet 30.0d (2 files)"
    assert lines(res.stdout, "CANDIDATE") == [f"CANDIDATE {A}  {quiet}", f"CANDIDATE {SHORT}  {quiet}"]
    keep = {r.split()[1]: r for r in lines(res.stdout, "KEEP")}
    for name, kind in (("alpha", "seat"), ("kaizen", "seat"), ("store1", "store"),
                       ("store1/proj1", "project")):
        assert keep[name] == f"KEEP      {name}  kind {kind}, not archived in v1"
    assert res.stdout.strip().splitlines()[-1] == (
        "-- 2 candidates of 6 boxes at --days 7 (8 dirs, 4 files); "
        "kept: 0 live, 0 alias row, 0 newer than 7d, 4 not a session box")


def test_1_the_default_listing_shows_candidates_only(fleet):
    res = fleet.archive_run()
    assert res.exit_code == 0, res.output
    assert sorted(l.split()[1] for l in lines(res.stdout, "CANDIDATE")) == [A, B, C]
    assert not lines(res.stdout, "KEEP")
    assert res.stdout.strip().splitlines()[-1] == (
        "-- 3 candidates of 6 boxes at --days 7 (12 dirs, 6 files); "
        "kept: 1 live, 0 alias row, 1 newer than 7d, 1 not a session box")


# ── 2. (a) liveness ────────────────────────────────────────────────────────

def test_2_a_live_sidecar_keeps_its_box(fleet):
    res = fleet.archive_run("--all", LIVE)
    assert lines(res.stdout, "KEEP") == [
        f"KEEP      {LIVE}  (a) live session d0000000  (b) no alias row, now or next  "
        f"(c) quiet 30.0d (2 files)"]


def test_2_an_8_hex_box_is_kept_by_a_live_full_id_with_that_prefix(world):
    world.box(SHORT)
    world.live(SHORT + "-1111-2222-3333-444455556666")
    world.regen()
    res = world.archive_run("--all")
    assert lines(res.stdout, "KEEP")[0].startswith(f"KEEP      {SHORT}  (a) live session {SHORT}")


@pytest.mark.parametrize("break_it", ["no sidecar dir", "torn sidecar", "caller not live"])
def test_2_unreadable_liveness_refuses_apply_and_moves_nothing(fleet, break_it):
    if break_it == "no sidecar dir":
        fleet.monkeypatch.setenv("SESSION_SIDECAR_DIR", str(fleet.tmp / "nowhere"))
    elif break_it == "torn sidecar":
        (fleet.cars / "torn.json").write_text('{"pid": 1, "sess')
    else:
        fleet.monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "9" * 8 + "-1111-2222-3333-444455556666")
    before = manifest(fleet.agents)
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3, res.output
    assert "P3 (a) liveness unreadable" in res.stderr
    assert manifest(fleet.agents) == before
    assert not fleet.archive.exists()


# ── 3. (b) alias rows ──────────────────────────────────────────────────────

@pytest.mark.parametrize("row", [
    f"{A}@{HOST}\t{A}@{HOST}",                                  # a key
    f"{A}+x@{HOST}\tsomeone@{HOST}",                            # a plus key
    f"alpha@{HOST}\talpha@{HOST}, {A}@{HOST}",                  # a destination
])
def test_3_a_row_in_the_current_alias_map_keeps_the_box(fleet, row):
    with open(fleet.maps / "valias", "a") as fh:
        fh.write(row + "\n")
    res = fleet.archive_run("--all", A)
    assert lines(res.stdout, "KEEP") and "(b) alias row: now" in lines(res.stdout, "KEEP")[0], res.output


def test_3_a_row_only_the_next_regenerate_would_mint_keeps_the_box(fleet):
    (fleet.mail / ".postfix").mkdir()
    (fleet.mail / ".postfix" / "valias.static").write_text(f"{A}@{HOST} other@{HOST}\n")
    res = fleet.archive_run("--all", A)
    assert "(b) alias row: next" in lines(res.stdout, "KEEP")[0], res.output


def test_3_a_refusing_print_valias_refuses_apply(fleet):
    fleet.monkeypatch.setenv("WRAP_FAIL_VALIAS", "1")
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3
    assert "P4 (b) alias maps unreadable: --print-valias exit 3" in res.stderr
    assert not fleet.archive.exists()


# ── 4. (c) quiet ───────────────────────────────────────────────────────────

def test_4_one_file_inside_the_window_keeps_the_box(fleet):
    mail_file(fleet.agents / A / "new", "2000.X.h", "<new@test>", "new", time.time() - 3600)
    res = fleet.archive_run("--all", A)
    assert "(c) newer than 7d: 0.0d" in lines(res.stdout, "KEEP")[0]


def test_4_a_recent_cur_dir_mtime_alone_keeps_the_box(fleet):
    os.utime(fleet.agents / A / "cur", None)
    res = fleet.archive_run("--all", A)
    assert "(c) newer than 7d" in lines(res.stdout, "KEEP")[0]


def test_4_fractional_days_list_but_apply_needs_one_or_more(fleet):
    assert fleet.archive_run("--days", "0.5").exit_code == 0
    res = fleet.archive_run("--apply", "--days", "0.5")
    assert res.exit_code == 2
    assert "--apply needs --days 1 or more" in res.stderr
    assert not fleet.archive.exists()


# ── 5. the dry run touches nothing ─────────────────────────────────────────

def test_5_the_dry_run_touches_nothing(fleet):
    before = manifest(fleet.tmp)
    res = fleet.archive_run("--all")
    assert res.exit_code == 0
    assert manifest(fleet.tmp) == before
    assert not fleet.archive.exists()


# ── 6. --apply ─────────────────────────────────────────────────────────────

def test_6_apply_moves_only_dead_quiet_unrouted_boxes_and_verifies_the_written_map(fleet):
    for sid in (A, B, C):
        assert len(fleet.rows(sid)) == 2                  # both host forms, before
    res = fleet.archive_run("--apply")
    assert res.exit_code == 0, res.output
    for sid in (A, B, C):
        assert (fleet.archive / sid / "new").is_dir() and not (fleet.agents / sid).exists()
        assert fleet.rows(sid) == []                      # the WRITTEN map
    for keep in (LIVE, FRESH, "alpha"):
        assert (fleet.agents / keep).is_dir()
    states = [(f[1], f[2]) for f in fleet.ledger()]
    for sid in (A, B, C):
        assert [s for s, r in states if r == sid] == ["moving", "archive"]
    assert res.stdout.strip().splitlines()[-1] == "-- 3 archived, 0 back"
    assert not (fleet.archive / _archive.LOCK).exists()


@pytest.fixture(params=[0o022, 0o000], ids=lambda u: f"umask{u:03o}")
def umask(request):
    old = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(old)


def _mode(p: Path) -> int:
    return os.lstat(p).st_mode & 0o7777


def test_6_apply_makes_the_archive_dir_0700_sm783(fleet, umask):
    """SM-783 (findings/rca.md A3): the archive dir apply makes holds whole
    maildirs, so it is the owner's alone under any umask; the mail root it is
    made in keeps its mode. The ledger (0640, its author's choice) and the
    lock dir are OUT of SM-783 and not pinned here."""
    os.chmod(fleet.mail, 0o755)
    assert not fleet.archive.exists()
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 0, res.output
    assert _mode(fleet.archive) == 0o700
    assert _mode(fleet.mail) == 0o755
    assert (fleet.archive / A / "new").is_dir()


def test_6_apply_never_re_modes_an_archive_dir_that_was_there_sm783(fleet, umask):
    fleet.archive.mkdir()
    os.chmod(fleet.archive, 0o755)                # the host owner's choice
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 0, res.output
    assert _mode(fleet.archive) == 0o755
    assert (fleet.archive / A / "new").is_dir()


def test_6_the_moving_line_records_the_rows_keys_bn2(fleet):
    fleet.archive_run("--apply", A)
    moving = [f for f in fleet.ledger() if f[1] == "moving"][0]
    assert sorted(moving[8].split(",")) == sorted([f"{A}@{HOST}", f"{A}@{HOST}.{FQDN_TAIL}"])


def test_6b_a_regenerate_that_does_not_take_sends_every_box_back(fleet):
    before = {sid: manifest(fleet.agents / sid) for sid in (A, B, C)}
    fleet.monkeypatch.setenv("WRAP_NOOP", "1")
    res = fleet.archive_run("--apply")
    assert res.exit_code == 4, res.output
    for sid in (A, B, C):
        assert manifest(fleet.agents / sid) == before[sid]
        assert not (fleet.archive / sid).exists()
        assert f"BACK {sid}: regenerate did not take" in res.stderr
    assert [f[6] for f in fleet.ledger() if f[1] == "back"] == ["regenerate did not take"] * 3


def test_6c_an_unreadable_map_is_never_a_pass(fleet):
    fleet.monkeypatch.setenv("WRAP_RM_MAP", "1")
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    assert (fleet.agents / A).is_dir() and not (fleet.archive / A).exists()
    assert f"BACK {A}: map unreadable" in res.stderr


def test_6d_a_row_left_in_one_host_form_is_present(fleet):
    fleet.monkeypatch.setenv("WRAP_APPEND", f"{A}@{HOST}.{FQDN_TAIL}\tagents/{A}/")
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    assert (fleet.agents / A).is_dir()
    assert f"BACK {A}: regenerate did not take" in res.stderr


def test_6e_an_interrupted_run_stops_moving_and_still_verifies_what_it_moved(fleet):
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move",
                              lambda rel: os.kill(os.getpid(), signal.SIGINT))
    res = fleet.archive_run("--apply")
    assert res.exit_code == 4, res.output
    assert (fleet.archive / A).is_dir() and fleet.rows(A) == []
    assert (fleet.agents / B).is_dir() and (fleet.agents / C).is_dir()
    assert res.stdout.strip().splitlines()[-1] == "-- 1 archived, 0 back; interrupted after 1 of 3"
    assert signal.getsignal(signal.SIGINT) is not None      # restored, not left ours
    assert not isinstance(signal.getsignal(signal.SIGINT), _archive._Stop)


def _died_after_moving(w: World, sid: str) -> None:
    """What a SIGKILLed run leaves: the intent line, the rename, no regenerate."""
    keys = w.rows(sid)
    w.archive.mkdir(exist_ok=True)
    _archive.ledger_append(w.archive, "moving", sid, actor="-", keys=keys)
    os.rename(w.agents / sid, w.archive / sid)


def test_6f_a_dead_runs_box_is_listed_then_finished_first(fleet):
    _died_after_moving(fleet, A)
    _archive.ledger_append(fleet.archive, "moving", B, actor="-", keys=fleet.rows(B))  # never moved
    assert fleet.rows(A)                                  # still routed: the exposure (L5)
    before = manifest(fleet.tmp)
    dry = fleet.archive_run()
    assert f"INTERRUPTED {A}: a run stopped between its move and its verify" in dry.stdout
    assert manifest(fleet.tmp) == before
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 0, res.output
    assert fleet.rows(A) == [] and (fleet.archive / A).is_dir()
    last = _archive.ledger_last(fleet.archive)
    assert last[A][1] == "archive"
    assert last[B][1] == "back" and last[B][6] == "never moved"


def test_6g_a_held_lock_refuses_and_is_left_as_found(fleet):
    fleet.archive.mkdir()
    (fleet.archive / ".lock").mkdir()
    (fleet.archive / ".lock" / "owner").write_text("pid=1 session=x since=then\n")
    before = manifest(fleet.tmp)
    dry = fleet.archive_run()                     # the dry run names it, and its pid's state
    assert f"LOCK {fleet.archive / '.lock'} held by pid=1 session=x since=then (pid running)" in dry.stdout
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3
    assert "held by pid=1 session=x" in res.stderr and "rmdir" in res.stderr
    assert manifest(fleet.tmp) == before


def test_bn5_recovery_runs_under_a_failing_preflight_then_new_moves_are_refused(fleet):
    _died_after_moving(fleet, A)
    fleet.notmuch(f"[database]\npath={fleet.mail}\n[new]\nignore=.DS_Store\n")
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3, res.output
    assert "P2 notmuch" in res.stderr
    assert fleet.rows(A) == [] and _archive.ledger_last(fleet.archive)[A][1] == "archive"
    assert (fleet.agents / B).is_dir() and (fleet.agents / C).is_dir()


# ── 7. preflight ───────────────────────────────────────────────────────────

def test_7_an_archive_on_another_device_refuses(fleet):
    real = _archive._dev
    fleet.monkeypatch.setattr(_archive, "_dev",
                              lambda p: real(p) + (1 if Path(p) == fleet.mail else 0))
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3 and "is on another device" in res.stderr
    assert not fleet.archive.exists()


def test_7_a_notmuch_config_that_scans_the_archive_refuses_naming_the_line(fleet):
    cfg = fleet.notmuch(f"[database]\npath={fleet.mail}\n[new]\nignore=.DS_Store;/^sent$/\n")
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3
    assert f"notmuch {cfg} scans it" in res.stderr and "/^archive$/" in res.stderr
    assert not fleet.archive.exists()
    assert "ignore=.DS_Store;/^sent$/" in cfg.read_text()        # never edited


@pytest.mark.parametrize("ignore", ["archive", "/^archive$/", ".DS_Store;archive"])
def test_7_a_config_that_ignores_the_archive_passes(fleet, ignore):
    fleet.notmuch(f"[database]\npath={fleet.mail}\n[new]\nignore={ignore}\n")
    assert fleet.archive_run("--apply", A).exit_code == 0


def test_7_a_config_whose_database_does_not_hold_the_archive_passes(fleet):
    fleet.notmuch(f"[database]\npath={fleet.tmp / 'elsewhere'}\n")
    assert fleet.archive_run("--apply", A).exit_code == 0


def test_7_a_profile_config_is_checked_too(fleet):
    prof = fleet.tmp / "xdg" / "notmuch" / "work" / "config"
    prof.parent.mkdir(parents=True)
    prof.write_text(f"[database]\nmail_root={fleet.mail}\n")
    res = fleet.archive_run("--apply")
    assert res.exit_code == 3 and str(prof) in res.stderr


# ── 8. re-checked before the move ──────────────────────────────────────────

def test_8_a_file_written_between_listing_and_move_keeps_the_box(fleet):
    fleet.monkeypatch.setitem(
        _archive.HOOKS, "before_move",
        lambda rel: rel == A and mail_file(fleet.agents / A / "new", "3000.Y.h", "<late@test>",
                                           "late", time.time()))
    res = fleet.archive_run("--apply", A, B)
    assert res.exit_code == 0, res.output
    assert (fleet.agents / A).is_dir() and (fleet.archive / B).is_dir()
    assert f"KEEP      {A}  (c) a new file since the listing" in res.stdout


# ── 9. re-created in the window (RULED W) ─────────────────────────────────

def _inject(w: World, name: str = "5000.Z.h", leaf: str = "new") -> Path:
    for l in _archive.LEAVES:
        (w.agents / A / l).mkdir(parents=True, exist_ok=True)
    return mail_file(w.agents / A / leaf, name, "<injected@test>", "arrived in the window", time.time())


@pytest.mark.parametrize("seam", ["after_move", "after_regen"])
def test_9_a_delivery_between_the_move_and_the_verify_is_merged_back(fleet, seam):
    archived = {p.name: (p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns,
                         hashlib.sha256(p.read_bytes()).hexdigest())
                for p in (fleet.agents / A).rglob("*") if p.is_file()}
    runs = fleet.runs()
    fleet.monkeypatch.setitem(_archive.HOOKS, seam,
                              lambda *a: (a == (A,) or a == (1,)) and _inject(fleet))
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    assert not (fleet.archive / A).exists()
    got = {p.name: p for p in (fleet.agents / A).rglob("*") if p.is_file()}
    assert set(got) == set(archived) | {"5000.Z.h"}
    for name, (ino, mode, mtime, sha) in archived.items():
        st = got[name].stat()
        assert (st.st_ino, st.st_mode, st.st_mtime_ns) == (ino, mode, mtime)
        assert hashlib.sha256(got[name].read_bytes()).hexdigest() == sha
    assert fleet.runs() == runs + 2                       # a second regenerate ran
    assert len(fleet.rows(A)) == 2                        # routed again
    assert f"BACK {A}: RE-CREATED: 1 mail arrived: new/5000.Z.h <injected@test> " \
           f"\"arrived in the window\"" in res.stderr


def test_9b_a_name_clash_in_the_merge_back_keeps_both(fleet):
    clash = next((fleet.agents / A / "cur").iterdir()).name          # `1001.M1P1.testhost:2,S`
    old_bytes = (fleet.agents / A / "cur" / clash).read_bytes()
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: _inject(fleet, clash, "cur"))
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    base = clash.partition(":2,")[0]
    kept = fleet.agents / A / "cur" / f"{base}-arch1:2,S"
    assert kept.read_bytes() == old_bytes
    assert b"<injected@test>" in (fleet.agents / A / "cur" / clash).read_bytes()
    assert f"name clash cur/{clash}: both kept, archived copy at cur/{kept.name}" in res.stderr


def test_9c_back_but_unrouted_when_the_second_regenerate_fails(fleet):
    fleet.monkeypatch.setenv("WRAP_FAIL_AFTER", str(fleet.runs() + 1))
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_regen", lambda n: n == 1 and _inject(fleet))
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    assert (fleet.agents / A).is_dir() and not (fleet.archive / A).exists()
    assert "BACK but UNROUTED" in res.stderr


def test_9d_a_box_in_both_trees_is_listed_re_created_and_left_alone(fleet):
    fleet.archive.mkdir()
    (fleet.archive / A / "new").mkdir(parents=True)
    before = manifest(fleet.tmp)
    res = fleet.archive_run()
    assert f"RE-CREATED {A}: 2 file(s) in agents/ and a box under archive/; " \
           f"message box unarchive {A} --merge --apply joins them" in res.stdout
    assert A not in " ".join(lines(res.stdout, "CANDIDATE"))
    assert manifest(fleet.tmp) == before


# ── 10, 11. unarchive ──────────────────────────────────────────────────────

def test_10_archive_then_unarchive_is_byte_identical_and_routed_again(fleet):
    before = manifest(fleet.agents / A)
    assert fleet.archive_run("--apply", A).exit_code == 0
    dry = fleet.unarchive_run(A)
    assert dry.exit_code == 0 and dry.stdout.startswith("would rename back")
    assert (fleet.archive / A).is_dir()
    res = fleet.unarchive_run(A, "--apply")
    assert res.exit_code == 0, res.output
    assert manifest(fleet.agents / A) == before
    assert len(fleet.rows(A)) == 2
    assert _archive.ledger_last(fleet.archive)[A][1] == "unarchive"


def test_11_unarchive_onto_an_empty_birth_triad(fleet):
    fleet.archive_run("--apply", A)
    for leaf in _archive.LEAVES:
        (fleet.agents / A / leaf).mkdir(parents=True)
    res = fleet.unarchive_run(A, "--apply")
    assert res.exit_code == 0, res.output
    assert len(list((fleet.agents / A).rglob("*.testhost*"))) == 2


def test_11_unarchive_onto_mail_needs_merge_and_never_overwrites(fleet):
    fleet.archive_run("--apply", A)
    clash = next((fleet.archive / A / "cur").iterdir()).name
    mail_file(fleet.agents / A / "cur", clash, "<newer@test>", "newer", time.time())
    (fleet.agents / A / "new").mkdir()
    (fleet.archive / A / "tmp" / "half.delivery").write_text("unfinished")
    refused = fleet.unarchive_run(A, "--apply")
    assert refused.exit_code == 3 and "--merge" in refused.stderr
    res = fleet.unarchive_run(A, "--merge", "--apply")
    assert res.exit_code == 0, res.output
    base = clash.partition(":2,")[0]
    assert b"<newer@test>" in (fleet.agents / A / "cur" / clash).read_bytes()
    assert b"<a0000000.1@test>" in (fleet.agents / A / "cur" / f"{base}-arch1:2,S").read_bytes()
    assert "left in archive/" in res.stdout and "tmp/half.delivery" in res.stdout
    assert (fleet.archive / A / "tmp" / "half.delivery").exists()


def test_unarchive_of_no_such_box_is_a_usage_error(fleet):
    assert fleet.unarchive_run("nothing-here").exit_code == 2


# ── 12. MCP ────────────────────────────────────────────────────────────────

def test_12_neither_verb_is_a_tool_and_list_path_keep_their_places():
    names = [n for n, _f, _d in _mcp.tools(message.cli, "message")]
    assert "message_box_archive" not in names and "message_box_unarchive" not in names
    i = names.index("message_box_list")
    assert names[i + 1] == "message_box_path"


def test_12_each_withheld_verb_gives_its_own_reason_on_its_page():
    def flat(group, name):
        return " ".join(CliRunner().invoke(group, ["mcp", "-h"], prog_name=name).output.split())
    msg = flat(message.cli, "message")
    assert ("Not a tool: `message box archive`, `message box unarchive`. "
            "A host owner's act.") in msg
    assert "script-mail" not in msg
    mem = flat(memory.cli, "memory")
    assert ("Not a tool: `memory run`. It executes script-mail as you, so it is a CLI act on "
            "purpose, never a tool call.") in mem
    assert "SM-790" not in mem


# ── 13. no git, no notmuch ────────────────────────────────────────────────

def test_13_neither_git_nor_notmuch_is_ever_run(fleet):
    fake = fleet.tmp / "fakebin"
    fake.mkdir()
    for name in ("git", "notmuch"):
        (fake / name).write_text(FAIL_AND_MARK)
        (fake / name).chmod(0o755)
    marks = fleet.tmp / "marks"
    fleet.monkeypatch.setenv("MARKS", str(marks))
    fleet.monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    (fleet.mail / ".git").mkdir()                 # a git host: noticed, never run
    dry = fleet.archive_run()
    assert "a git work tree" in dry.stdout
    assert fleet.archive_run("--apply").exit_code == 0
    assert fleet.unarchive_run(A, "--apply").exit_code == 0
    assert not marks.exists(), marks.read_text()


# ── build notes ────────────────────────────────────────────────────────────

def test_bn1_mailbox_rows_match_the_value_exactly_never_a_substring():
    text = (f"ab@{HOST}\tagents/ab/\nab@{HOST}.{FQDN_TAIL}\tagents/ab/\n"
            f"abc@{HOST}\tagents/abc/\nx@{HOST}\tagents/store/ab/\n")
    assert _archive.mailbox_keys(text, "ab") == [f"ab@{HOST}", f"ab@{HOST}.{FQDN_TAIL}"]
    assert _archive.mailbox_keys(text, "a") == []


def test_bn3_output_errors_never_abort_the_run(fleet, monkeypatch):
    def broken(*a, **k):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(message.click, "echo", broken)
    res = CliRunner().invoke(cli, ["box", "archive", "--apply", A, "--maps", str(fleet.maps)])
    assert res.exit_code == 0, res.output
    assert (fleet.archive / A).is_dir() and fleet.rows(A) == []


def test_the_generator_is_never_changed_by_this_task():
    """M is FUTURE (ruling W): the build ships no generator change."""
    gen = files("sessionmail").joinpath("postfix/vmailbox-gen").read_text()
    assert "archiv" not in gen.lower()



# ── review do-001 (worker 7d920e2a): the gaps a peer review found ─────────

def test_r1_recovery_with_no_generator_puts_the_box_back_by_rename(fleet):
    """Design §4 *Interrupted run*: without P5 (or a readable map) a dead
    run's boxes go BACK by rename, no regenerate, and the run exits 4. Never
    left archived while the old row still routes them."""
    _died_after_moving(fleet, A)
    fleet.monkeypatch.setenv(_seat.GEN_VAR, str(fleet.tmp / "no-such-gen"))
    fleet.monkeypatch.setenv("PATH", str(fleet.tmp / "empty-bin"))
    res = fleet.archive_run("--apply")
    assert res.exit_code == 4, res.output
    assert (fleet.agents / A / "new").is_dir() and not (fleet.archive / A).exists()
    last = _archive.ledger_last(fleet.archive)[A]
    assert last[1] == "back" and "P5" in last[6], last
    assert (fleet.agents / B).is_dir()                        # no new move
    assert not (fleet.archive / _archive.LOCK).exists()


def test_r2_a_rename_that_fails_mid_batch_still_verifies_what_moved(fleet):
    """An OSError on one box's rename must not abandon the boxes already
    moved: they still get the regenerate and the verify (R1 end state)."""
    real = os.rename

    def flaky(src, dst):
        if Path(src).name == B and Path(dst).parent == fleet.archive:
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    fleet.monkeypatch.setattr(_archive.os, "rename", flaky)
    res = fleet.archive_run("--apply")
    assert res.exit_code == 4, res.output
    for sid in (A, C):
        assert (fleet.archive / sid).is_dir() and fleet.rows(sid) == []
    assert (fleet.agents / B).is_dir() and fleet.rows(B)
    last = _archive.ledger_last(fleet.archive)
    assert last[A][1] == last[C][1] == "archive"
    assert last[B][1] == "back" and "rename failed" in last[B][6]
    assert not (fleet.archive / _archive.LOCK).exists()


def _postconf_world(w: World, db_rows: dict[str, set[str]]):
    """Postconf 'answers' with the scratch maps; postmap -q answers from
    DB_ROWS ({'vmailbox'|'valias': keys}), so the .db can disagree with
    the text the way it does after a failed postmap."""
    def maps(parameter):
        name = {"virtual_mailbox_maps": "vmailbox", "virtual_alias_maps": "valias"}[parameter]
        return [f"hash:{w.maps / name}"]

    def lookup(key, ms):
        name = Path(ms[0].split(":", 1)[1]).name
        return (ms[0], "x") if key in db_rows.get(name, set()) else None

    w.monkeypatch.setattr(_seat, "_postfix_maps", maps)
    w.monkeypatch.setattr(_seat, "_postmap_lookup", lookup)
    return CliRunner().invoke(cli, ["box", "archive", "--apply", A], prog_name="message")


def test_r3_a_db_that_still_routes_the_box_sends_it_back(fleet):
    res = _postconf_world(fleet, {"vmailbox": {f"{A}@{HOST}.{FQDN_TAIL}"}})
    assert res.exit_code == 4, res.output
    assert (fleet.agents / A).is_dir() and not (fleet.archive / A).exists()
    assert f"BACK {A}: the .db still routes {A}@{HOST}.{FQDN_TAIL}" in res.stderr


def test_r3_the_db_path_archives_when_the_db_agrees(fleet):
    res = _postconf_world(fleet, {})
    assert res.exit_code == 0, res.output
    assert (fleet.archive / A).is_dir() and fleet.rows(A) == []


def test_r4_q7_an_alias_row_only_in_the_db_keeps_the_box(fleet):
    """Q7's default: when postconf answers, the alias .db is asked for the
    box's own keys too (the text and the .db differ after a failed postmap)."""
    res = _postconf_world(fleet, {"valias": {f"{A}@{HOST}"}})
    assert res.exit_code == 0, res.output
    assert (fleet.agents / A).is_dir() and not (fleet.archive / A).exists()
    assert f"KEEP      {A}  (b) the alias .db still names {A}@{HOST}" in res.stdout


def test_r5_bn6_a_keyless_recovery_is_reported_text_only_on_the_db_path(fleet):
    keys = fleet.rows(A)
    fleet.archive.mkdir()
    _archive.ledger_append(fleet.archive, "moving", A, actor="-")       # no keys (pre-BN6)
    os.rename(fleet.agents / A, fleet.archive / A)
    fleet.regen()
    assert keys and fleet.rows(A) == []
    res = _postconf_world(fleet, {})
    assert res.exit_code == 0, res.output
    assert f"note {A}: text-only verify: no keys were recorded" in res.stdout


def test_r6_a_box_re_created_after_the_second_regenerate_is_merged_and_named(fleet):
    def inject(box: str) -> None:
        for l in _archive.LEAVES:
            (fleet.agents / box / l).mkdir(parents=True, exist_ok=True)
        mail_file(fleet.agents / box / "new", "6000.Q.h", f"<{box[:8]}.late@test>", "late", time.time())

    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: rel == B and inject(B))
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_regen", lambda n: n == 2 and inject(A))
    res = fleet.archive_run("--apply", A, B)
    assert res.exit_code == 4, res.output
    for sid in (A, B):
        assert (fleet.agents / sid).is_dir() and not (fleet.archive / sid).exists()
    assert f"BACK {A}: RE-CREATED after the second regenerate: 1 mail arrived" in res.stderr
    assert "BACK but UNROUTED" in res.stderr


# ── review do-001 round 2 (worker 0bfa5577): one BACK must not abandon the rest

def test_r7_a_back_that_fails_never_abandons_the_other_backs(fleet):
    """R1's end state on an ordinary error in the BACK step: A was re-created
    in the window and its merge back fails (EACCES); B's regenerate did not
    take. B must still go BACK. A is reported NOT BACK, keeps a `moving`
    last line, and the next --apply finishes it (merged, nothing lost)."""
    fleet.monkeypatch.setenv("WRAP_NOOP", "1")
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: rel == A and _inject(fleet))
    real = os.link

    def bad_link(src, dst, *a, **kw):
        if Path(src).parent.parent == fleet.archive / A:
            raise PermissionError(13, "Permission denied")
        return real(src, dst, *a, **kw)

    fleet.monkeypatch.setattr(_archive.os, "link", bad_link)
    res = fleet.archive_run("--apply", A, B)
    assert res.exit_code == 4, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert (fleet.agents / B).is_dir() and not (fleet.archive / B).exists() and fleet.rows(B)
    assert f"NOT BACK {A}: Permission denied" in res.stderr
    assert res.stdout.strip().splitlines()[-1].startswith("-- 0 archived, 1 back, 1 NOT back")
    last = _archive.ledger_last(fleet.archive)
    assert last[A][1] == "moving" and last[A][8] != "-"        # its keys, for the recovery
    assert last[B][1] == "back"
    assert not (fleet.archive / _archive.LOCK).exists()
    # The next run finds A INTERRUPTED and finishes it: merged back, nothing lost.
    fleet.monkeypatch.setattr(_archive.os, "link", real)
    fleet.monkeypatch.delenv("WRAP_NOOP")
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: None)
    again = fleet.archive_run("--apply", A)
    assert again.exit_code == 4, again.output
    assert not (fleet.archive / A).exists()
    assert len([p for p in (fleet.agents / A).rglob("*") if p.is_file()]) == 3   # 2 archived + 1 injected
    assert _archive.ledger_last(fleet.archive)[A][1] == "back"


def test_r8_a_print_valias_that_fails_after_recovery_still_verifies_it(fleet):
    """The re-ask of the next-regenerate map before new moves can fail
    (Unknown). Recovered boxes must still get their regenerate and verify,
    and new moves stop: exit 4, never a traceback with A left unverified."""
    _died_after_moving(fleet, A)
    calls = {"n": 0}
    real = _archive.Ctx.alias_next

    def flaky(self):
        calls["n"] += 1
        if calls["n"] > 1:                                    # the census's ask passes
            raise _archive.Unknown("--print-valias exit 3: refused")
        return real(self)

    fleet.monkeypatch.setattr(_archive.Ctx, "alias_next", flaky)
    res = fleet.archive_run("--apply")
    assert res.exit_code == 4, res.output
    assert "stopped moving: --print-valias exit 3" in res.stderr
    assert fleet.rows(A) == [] and _archive.ledger_last(fleet.archive)[A][1] == "archive"
    for sid in (B, C):
        assert (fleet.agents / sid).is_dir()                  # no new move


# ── round 3 (review 8df65e84, H1): W's header read of a re-created box is total ──

#: A Message-ID the CPython 3.13 stdlib parser raises on (UnboundLocalError in
#: `_header_value_parser._get_ptext_to_endchars`). Any sender controls it.
POISON = "<x@[ "


def _inject_poison(w: World) -> Path:
    for l in _archive.LEAVES:
        (w.agents / A / l).mkdir(parents=True, exist_ok=True)
    return mail_file(w.agents / A / "new", "5000.Z.h", POISON, "arrived in the window", time.time())


def test_h1_arrived_never_raises(tmp_path):
    """arrived() runs before any BACK: a raise there would abandon them all."""
    box = tmp_path / "box"
    mail_file(box / "new", "1.a.h", POISON, "poison", time.time())
    mail_file(box / "cur", "2.b.h:2,S", "<ok@test>", "fine", time.time())
    (box / "tmp").mkdir()
    got = _archive.arrived(box)
    assert len(got) == 2 and got[1] == 'cur/2.b.h:2,S <ok@test> "fine"', got
    assert got[0].startswith("new/1.a.h "), got
    os.chmod(box / "new", 0)
    try:
        got = _archive.arrived(box)
    finally:
        os.chmod(box / "new", 0o755)
    assert got[0].startswith("new/ <unreadable: "), got
    assert got[1].startswith("cur/2.b.h:2,S"), got


def test_h1a_a_poison_message_id_in_a_re_created_box_never_abandons_the_backs(fleet):
    """R1's end state: A is re-created in the window by a mail whose header
    the stdlib parser chokes on; B's regenerate did not take, so its row
    still routes it. B must end BACK, A merged back, exit 4: never a
    traceback with B left under archive/ while routed."""
    fleet.monkeypatch.setenv("WRAP_NOOP", "1")
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: rel == A and _inject_poison(fleet))
    res = fleet.archive_run("--apply", A, B)
    assert res.exit_code == 4, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert (fleet.agents / B).is_dir() and not (fleet.archive / B).exists() and fleet.rows(B)
    assert not (fleet.archive / A).exists()
    assert len([p for p in (fleet.agents / A).rglob("*") if p.is_file()]) == 3   # 2 archived + 1 injected
    assert f"BACK {A}: RE-CREATED: 1 mail arrived: new/5000.Z.h " in res.stderr
    last = _archive.ledger_last(fleet.archive)
    assert last[A][1] == "back" and last[B][1] == "back"


def test_h1b_an_unlistable_leaf_in_a_re_created_box_never_abandons_the_backs(fleet):
    """The OSError form: the re-created box's new/ cannot be listed. B still
    goes BACK. A cannot be merged into that leaf either, so it is NOT BACK,
    keeps `moving`, and is named (round 2's G1 path); the exit is 4."""
    fleet.monkeypatch.setenv("WRAP_NOOP", "1")

    def inject(rel):
        if rel == A:
            mail_file(fleet.agents / A / "new", "5000.Z.h", "<ok@test>", "s", time.time())
            os.chmod(fleet.agents / A / "new", 0)

    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", inject)
    try:
        res = fleet.archive_run("--apply", A, B)
    finally:
        os.chmod(fleet.agents / A / "new", 0o755)
    assert res.exit_code == 4, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert (fleet.agents / B).is_dir() and not (fleet.archive / B).exists() and fleet.rows(B)
    assert "new/ <unreadable: Permission denied>" in res.stderr
    assert f"NOT BACK {A}: Permission denied" in res.stderr
    last = _archive.ledger_last(fleet.archive)
    assert last[A][1] == "moving" and last[B][1] == "back"


def test_h1c_one_poison_mail_never_wedges_later_runs(fleet):
    """One fault, the regenerate working: W merges A back and exits 4 naming
    it; a later --apply is not stopped by the same mail."""
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: rel == A and _inject_poison(fleet))
    res = fleet.archive_run("--apply", A)
    assert res.exit_code == 4, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert not (fleet.archive / A).exists() and fleet.rows(A)
    assert _archive.ledger_last(fleet.archive)[A][1] == "back"
    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", lambda rel: None)
    again = fleet.archive_run("--apply", A, B)
    assert again.exit_code == 0, again.output
    assert (fleet.archive / B).is_dir() and fleet.rows(B) == []
    assert (fleet.agents / A).is_dir()                    # its new mail keeps it: (c)


# ── do-002 (review 688c9d88, K7): an unsearchable re-created box is unreadable, not a raise ──

def test_k7_end_state(fleet):
    """A re-created box that cannot itself be searched denies the stat of its
    new/ (`arrived` stats strictly: `Path.is_dir()` raised there only up to
    CPython 3.13, and from 3.14 says False, SM-877). The verify must not
    abort over it, and must not read it as empty: its new/ is reported
    unreadable, and B, whose regenerate did not take, still goes BACK while
    its row routes it."""
    fleet.monkeypatch.setenv("WRAP_NOOP", "1")

    def inject(rel):
        if rel == A:
            mail_file(fleet.agents / A / "new", "5000.Z.h", "<ok@test>", "s", time.time())
            os.chmod(fleet.agents / A, 0)

    fleet.monkeypatch.setitem(_archive.HOOKS, "after_move", inject)
    try:
        res = fleet.archive_run("--apply", A, B)
    finally:
        os.chmod(fleet.agents / A, 0o755)
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert res.exit_code == 4, res.output
    assert (fleet.agents / B).is_dir() and not (fleet.archive / B).exists() and fleet.rows(B)
    assert _archive.ledger_last(fleet.archive)[B][1] == "back"
    assert "new/ <unreadable: Permission denied>" in res.stderr


def test_merge_from_a_box_that_cannot_be_searched_raises_and_moves_nothing(tmp_path):
    """SM-877: `merge` stats SRC's leaves strictly. An archived box that lists
    but cannot be searched (r--) raises, so `_back` keeps it `moving` and the
    next --apply tries again. It never returns "nothing left in SRC" while
    SRC's mail sits there untouched, which is what `Path.is_dir()` made of it
    from CPython 3.14."""
    src, dst = tmp_path / "archive" / A, tmp_path / "agents" / A
    held = mail_file(src / "new", "5000.Z.h", "<held@test>", "s", time.time())
    (dst / "new").mkdir(parents=True)
    os.chmod(src, 0o444)
    try:
        if os.access(src, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        with pytest.raises(PermissionError):
            _archive.merge(src, dst)
    finally:
        os.chmod(src, 0o755)
    assert held.is_file() and list((dst / "new").iterdir()) == []
