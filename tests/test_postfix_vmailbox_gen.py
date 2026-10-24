"""`vmailbox-gen --print` is pinned, byte for byte, over a synthetic tree.

This generator writes postfix's `virtual_mailbox_maps` — the map that decides
which maildir EVERY agent address on a host delivers into. Break it and the
whole fleet goes deaf at once, silently. SM-74 brought it home
from a loose `~/.mail/.postfix/vmailbox-gen` into this repo as source; this
module is the net under that move, so a later port or tidy-up cannot change
the map's text without a test going red first.

**It ships as PACKAGE DATA, not a fourth command.**
`openspec/specs/package/spec.md` RATIFIES "no fourth top-level command and no
umbrella command the three hang off", so there is deliberately no
`vmailbox-gen` console script. Whether it later becomes a `pager`/`message`
subcommand is not settled here.

**Hermetic by construction.** Every run below is `--print --no-postmap` over a
`--root` under `tmp_path`: `--print` returns before the generator takes its
lock or opens any target, so no file is written anywhere, no `postmap` runs,
and the live map (`/usr/local/etc/postfix-agents/vmailbox`) and the live tree
(`~/.mail`) are never touched — `HOME` is redirected into `tmp_path` as well,
and `test_print_writes_no_files` snapshots the tree to prove it.

What the expected text pins, line by line:

- a flat box emits BOTH domain forms (`@host` and `@fqdn`) — a one-key map is
  a silent no-op, which is why both are always written;
- names are folded to lowercase and stripped to `[a-z0-9_.-]` (postfix's own
  key folding, design §5.7) — note the emitted PATH uses the folded name too,
  so `Worker.SM-74/` is pinned as `agents/worker.sm-74/`. That is recorded as
  observed behaviour of the verbatim script, not endorsed;
- `nested-v1`: a `.tm-store` marker dir is a PATH COMPONENT. It emits no pair
  of its own (`tmstore@host` stays UNROUTED on purpose) and its children are
  emitted one level deeper;
- `store-box-v1`: unless that marker dir carries the WHOLE `new`/`cur`/`tmp`
  triad, in which case it is a box as well as a path component — and its own
  `new`/`cur`/`tmp` are never mistaken for projects (S2-1);
- hidden entries (`.notmuch`, `.hidden-project`) and plain files are skipped;
- output is `LC_ALL=C sort -u`, i.e. DETERMINISTIC: two runs over one tree
  produce a byte-identical map.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from _seatmap import session_declare, session_subscribe
from sessionmail import _subscription

HOST = "testhost"
SUFFIX = "example.ts.net"
FQDN = f"{HOST}.{SUFFIX}"

# The map text, verbatim. Tab-separated key/path, LC_ALL=C sorted, unique.
EXPECTED_MAP = f"""\
boxstore@{HOST}\tagents/boxstore/
boxstore@{FQDN}\tagents/boxstore/
foreman.sm-74@{HOST}\tagents/foreman.sm-74/
foreman.sm-74@{FQDN}\tagents/foreman.sm-74/
proj-a@{HOST}\tagents/boxstore/proj-a/
proj-a@{FQDN}\tagents/boxstore/proj-a/
sessionmail@{HOST}\tagents/tmstore/sessionmail/
sessionmail@{FQDN}\tagents/tmstore/sessionmail/
voicemode@{HOST}\tagents/tmstore/voicemode/
voicemode@{FQDN}\tagents/tmstore/voicemode/
worker.sm-74@{HOST}\tagents/worker.sm-74/
worker.sm-74@{FQDN}\tagents/worker.sm-74/
"""

# `--capabilities` is DETECTION BY CONSTRUCTION: a token is listed only by a
# generator that implements the behaviour, because both ship in one patch. An
# unpatched generator answers it like any unknown argument (exit 2).
EXPECTED_CAPABILITIES = "nested-v1\nvalias-v1\nstore-box-v1\nplus-v1\nno-fqdn-v1\n"


@pytest.fixture(scope="module")
def generator() -> Path:
    """The generator as the package ships it — proves it is package data."""
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as path:
        yield Path(path)


def _maildir(path: Path) -> None:
    for leaf in ("new", "cur", "tmp"):
        (path / leaf).mkdir(parents=True)


@pytest.fixture
def mail_root(tmp_path: Path) -> Path:
    """A synthetic `~/.mail`: no live maildir, no live map, nothing shared."""
    agents = tmp_path / "root" / "agents"
    agents.mkdir(parents=True)

    _maildir(agents / "foreman.sm-74")       # a plain flat box
    _maildir(agents / "Worker.SM-74")        # folded + stripped -> worker.sm-74
    (agents / ".notmuch").mkdir()            # hidden -> skipped
    (agents / "README.txt").write_text("not a box\n")   # a file -> skipped

    # A store dir WITHOUT the triad: a path component only, never a box.
    tmstore = agents / "tmstore"
    tmstore.mkdir()
    (tmstore / ".tm-store").write_text("")
    _maildir(tmstore / "sessionmail")
    (tmstore / "voicemode").mkdir()          # a project need not be a maildir yet
    (tmstore / ".hidden-project").mkdir()    # hidden child -> skipped

    # A store dir WITH the triad: a box AND a path component (store-box-v1).
    boxstore = agents / "boxstore"
    boxstore.mkdir()
    (boxstore / ".tm-store").write_text("")
    _maildir(boxstore)                       # its own new/cur/tmp are NOT projects
    (boxstore / "proj-a").mkdir()

    return tmp_path / "root"


def _run(generator: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the generator with the machine held at arm's length.

    `HOME` points into `tmp_path` so the script's own `$HOME/.mail` default
    cannot reach the real tree even if an argument were ever dropped.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "MAGICDNS_SUFFIX": SUFFIX,
        "LC_ALL": "C",
    }
    return subprocess.run(
        [str(generator), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_generator_ships_as_package_data(generator: Path) -> None:
    assert generator.is_file()
    assert os.access(generator, os.X_OK), "the executable bit is part of the move"
    assert generator.read_text().startswith("#!/usr/bin/env bash\n")


def test_print_map_is_pinned(generator: Path, tmp_path: Path, mail_root: Path) -> None:
    done = _run(
        generator, tmp_path,
        "--print", "--no-postmap", "--host", HOST, "--root", str(mail_root),
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == EXPECTED_MAP


def test_print_map_is_deterministic(
    generator: Path, tmp_path: Path, mail_root: Path
) -> None:
    """Sorted + de-duplicated: two runs over one tree are byte-identical."""
    args = ("--print", "--no-postmap", "--host", HOST, "--root", str(mail_root))
    first = _run(generator, tmp_path, *args)
    second = _run(generator, tmp_path, *args)
    assert first.stdout == second.stdout == EXPECTED_MAP


def test_store_dir_without_the_triad_is_unrouted(
    generator: Path, tmp_path: Path, mail_root: Path
) -> None:
    """`tmstore@host` must appear NOWHERE: a pair for it is a poisoned entry."""
    done = _run(
        generator, tmp_path,
        "--print", "--no-postmap", "--host", HOST, "--root", str(mail_root),
    )
    assert f"tmstore@{HOST}\t" not in done.stdout
    assert f"tmstore@{FQDN}\t" not in done.stdout
    # …while the marker dir that DOES carry the triad is routed as a box.
    assert f"boxstore@{HOST}\tagents/boxstore/\n" in done.stdout
    # A store box's own maildir is never read as a project (S2-1).
    for leaf in ("new", "cur", "tmp"):
        assert f"{leaf}@{HOST}\t" not in done.stdout


def test_capabilities_are_pinned(generator: Path, tmp_path: Path) -> None:
    done = _run(generator, tmp_path, "--capabilities")
    assert done.returncode == 0, done.stderr
    assert done.stdout == EXPECTED_CAPABILITIES


def test_empty_tree_prints_nothing(generator: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "agents").mkdir(parents=True)
    done = _run(
        generator, tmp_path,
        "--print", "--no-postmap", "--host", HOST, "--root", str(empty),
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


def test_print_writes_no_files(
    generator: Path, tmp_path: Path, mail_root: Path
) -> None:
    """`--print` returns before the lock and before any target is opened."""
    (tmp_path / "home").mkdir(exist_ok=True)   # `_run` would otherwise add it
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    done = _run(
        generator, tmp_path,
        "--print", "--no-postmap", "--host", HOST, "--root", str(mail_root),
    )
    assert done.returncode == 0, done.stderr
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert after == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root can make the dir, so the error never fires")
def test_a_map_dir_it_cannot_make_names_the_setup_step_and_its_command_runs(
    generator: Path, tmp_path: Path, mail_root: Path
) -> None:
    """A writing run whose map dir is missing and cannot be made stops with
    exit 2. The error names no private script; it names the docs step and
    prints that step's command for this account and this dir. Run (sudo's
    part played by giving the parent its write bit back, and the test's own
    group standing in for `_postfix`), the command makes a dir the next run
    writes into."""
    import grp
    import pwd
    import shlex

    parent = tmp_path / "locked"
    parent.mkdir()
    maps = parent / "postfix-agents"
    parent.chmod(0o555)
    try:
        args = ("--no-postmap", "--no-valias", "--host", HOST,
                "--root", str(mail_root), "--out", str(maps / "vmailbox"))
        done = _run(generator, tmp_path, *args)
        assert done.returncode == 2, done.stderr
        assert done.stdout == ""
        assert not maps.exists()
        err = done.stderr
        assert "setup-mail" not in err, err
        assert err.startswith(f"vmailbox-gen: error: map dir {maps} does not exist"), err
        assert "docs/postfix-configuration.md, Setup step 1: " in err, err
        command = err.rstrip("\n").split("Setup step 1: ", 1)[1]
        me = pwd.getpwuid(os.geteuid()).pw_name
        argv = shlex.split(command)
        assert argv == ["sudo", "install", "-d", "-o", me, "-g", "_postfix",
                        "-m", "2750", str(maps)], command
    finally:
        parent.chmod(0o755)

    group = grp.getgrgid(os.getegid()).gr_name
    run = argv[1:]
    run[run.index("-g") + 1] = group
    made = subprocess.run(run, capture_output=True, text=True, check=False)
    assert made.returncode == 0, made.stderr
    assert maps.is_dir()
    again = _run(generator, tmp_path, *args)
    assert again.returncode == 0, again.stderr
    assert (maps / "vmailbox").read_text() == EXPECTED_MAP


# ── task-address-doorbell: the plus row, written by the generator ───────────
#
# The sentence ACCEPTED: `pager listen` declares the address; the alias map generator
# writes ONE row for the full plus form, delivering to `<base>@<host>` and the
# session's box, and NEVER a row on the bare `<base>@<host>` for it.
#
# Hermetic like the rest: `--print-valias` writes nothing, the live sessions
# are sidecars under tmp_path (SESSION_SIDECAR_DIR) beside a fake `ps` on PATH
# answering from a fixture, and SESSIONMAIL_HOME is under tmp_path. A fake
# `session` is on PATH too, and it FAILS and leaves a mark: since 2030-12-08
# the generator must not need the session package at all.

FAKE_PS = """#!/bin/sh
# `ps -axo pid=,lstart=`, answered from the fixture. Like the real one it
# reports start times in the local zone, so asked WITHOUT TZ=UTC it answers
# ten hours off (the fixture's local zone is UTC+10, see `_as_local`) and no
# sidecar matches.
if [ "${TZ:-}" = UTC ]; then cat "$FAKE_PS_TABLE"; else cat "$FAKE_PS_TABLE_LOCAL"; fi
"""

FAKE_SESSION = """#!/bin/sh
# the retired session package: any call is a regression
echo "$*" >> "$FAKE_SESSION_CALLS"
exit 1
"""

#: procStart as Claude Code writes it (UTC ctime), and `ps` in UTC agreeing.
STARTED = "Sun Jun 09 12:00:00 2030"

PLUS = "sessionmail+sm-122.foreman"


def _as_local(row: str, hours: int = 10) -> str:
    """One fake `ps` row with its start moved from UTC to UTC+HOURS, in
    ctime's own layout (a one-digit day is space-padded)."""
    from datetime import datetime, timedelta
    pid, _, start = row.strip().partition(" ")
    t = datetime.strptime(" ".join(start.split()), "%a %b %d %H:%M:%S %Y") + timedelta(hours=hours)
    return f"{pid:>5} {t:%a %b} {t.day:>2} {t:%H:%M:%S %Y}    "


def _valias(generator: Path, tmp_path: Path, sessions: list[dict],
            addresses: dict[str, str], boxes: list[str], *args: str,
            subs: dict[str, str] | None = None,
            sidecar_dir: Path | None = None,
            env_over: dict[str, str | None] | None = None) -> subprocess.CompletedProcess:
    """`--print-valias` over a fake fleet: SESSIONS live, a box for each of
    BOXES, and each session's layer, `~/.session/sessions/<sid>/settings/
    mail.json`, declaring ADDRESSES[sid] as its `address` (session-settings
    3.3b). SUBS maps a path under the sessionmail home (`seats/ivan/
    subscriptions/atomnote`) to the file's body: the subscription files
    (message-subscriptions D1). A SESSION's box file that is not a saved
    query becomes a `true` key of that session's map instead, where 3.3b
    reads it. A path that starts `~/` is under the generator's HOME: a
    seat's map, `~/.session/seats/<seat>/settings/mail.json` (3.2).
    ENV_OVER is laid over the environment last; a None value removes the
    variable (SM-660: `MAGICDNS_SUFFIX` unset).

    Each session becomes a sidecar `<pid>.json` and, unless it says
    `"alive": False`, a `ps` row. Optional keys: `"ps_start"` (the start `ps`
    reports, default STARTED - a different one is a reused pid), `"raw"`
    (the sidecar's text verbatim, for a torn or foreign file)."""
    import json
    root = tmp_path / "root"
    for sid in boxes:
        _maildir(root / "agents" / sid)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "sessionmail"
    for sid, address in addresses.items():
        session_declare(sid, address, tmp_path / "home")
    for rel, body in (subs or {}).items():
        parts = rel.split("/")
        if (len(parts) == 4 and parts[0] == "sessions" and parts[2] == "subscriptions"
                and "query:" not in body):
            session_subscribe(parts[1], _subscription.box_name(parts[3]), home=tmp_path / "home")
            continue
        # `~/...` is under the generator's HOME (`_run`): a seat's map,
        # `~/.session/seats/<seat>/settings/mail.json` (session-settings 3.2).
        at = (tmp_path / "home" / rel[2:]) if rel.startswith("~/") else (home / rel)
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_text(body)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("ps", FAKE_PS), ("session", FAKE_SESSION)):
        fake = bin_dir / name
        fake.write_text(text)
        fake.chmod(0o755)
    sidecars = tmp_path / "claude-sessions"
    sidecars.mkdir(exist_ok=True)
    ps_rows = []
    for i, s in enumerate(sessions):
        pid = 40000 + i
        car = {"pid": pid, "sessionId": s["session_id"], "procStart": STARTED}
        if s.get("agent") is not None:
            car["agent"] = s["agent"]
        (sidecars / f"{pid}.json").write_text(s.get("raw", json.dumps(car)))
        if s.get("alive", True):
            ps_rows.append(f"{pid:>5} {s.get('ps_start', STARTED)}    ")
    ps_table = tmp_path / "ps.txt"
    ps_table.write_text("".join(r + "\n" for r in ps_rows))
    (tmp_path / "ps-local.txt").write_text("".join(_as_local(r) + "\n" for r in ps_rows))
    (tmp_path / "home").mkdir(exist_ok=True)
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "MAGICDNS_SUFFIX": SUFFIX,
        "LC_ALL": "C",
        "FAKE_PS_TABLE": str(ps_table),
        "FAKE_PS_TABLE_LOCAL": str(tmp_path / "ps-local.txt"),
        "FAKE_SESSION_CALLS": str(tmp_path / "session-calls"),
        "SESSION_SIDECAR_DIR": str(sidecar_dir or sidecars),
        "SESSIONMAIL_HOME": str(home),
    }
    for key, value in (env_over or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [str(generator), "--print-valias", "--host", HOST, "--root", str(root), *args],
        capture_output=True, text=True, env=env, check=False)


def _withheld_line(stderr: str) -> str | None:
    """The generator's SM-218 stderr line, reduced to its count and its
    `word=sessions` list (or None when nothing was withheld)."""
    import re
    for line in stderr.splitlines():
        m = re.fullmatch(r"vmailbox-gen: withheld (\d+) seat record\(s\) whose seat word is not"
                         r" itself a box \(agents/<word>/new\) - no <word>@ row written,"
                         r" word=sessions: (.*)", line)
        if m:
            return f"withheld {m[1]} seat record(s): {m[2]}"
    return None


def _bare_rows(text: str, base: str, sid: str) -> list[str]:
    """Every row keyed on the BARE base whose destinations name SID - the
    SM-74 fan-out, the one thing a plus declaration must never produce."""
    found = []
    for line in text.splitlines():
        key, _, dests = line.partition("\t")
        if key.split("@", 1)[0] == base and sid in dests:
            found.append(line)
    return found


needs_jq = pytest.mark.skipif(
    not __import__("shutil").which("jq"), reason="the generator's valias half needs jq")


@needs_jq
def test_a_declared_plus_address_gets_its_row_in_both_host_forms_and_no_bare_row(
    generator: Path, tmp_path: Path
) -> None:
    """tasks 3.1: the full-form row, and no bare row."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "foreman"}],
                   {"s1": PLUS}, ["s1"])
    assert done.returncode == 0, done.stderr
    assert f"{PLUS}@{HOST}\tsessionmail@{HOST}, s1@{HOST}\n" in done.stdout
    assert f"{PLUS}@{FQDN}\tsessionmail@{FQDN}, s1@{FQDN}\n" in done.stdout
    assert _bare_rows(done.stdout, "sessionmail", "s1") == []


def test_the_bare_row_check_can_fail() -> None:
    """The assertion above is only worth something if it catches a bare row."""
    poisoned = f"sessionmail@{HOST}\tsessionmail@{HOST}, s1@{HOST}\n"
    assert _bare_rows(poisoned, "sessionmail", "s1") == [poisoned.rstrip("\n")]
    assert _bare_rows(f"{PLUS}@{HOST}\tsessionmail@{HOST}, s1@{HOST}\n", "sessionmail", "s1") == []


@needs_jq
def test_the_whole_alias_map_is_pinned_with_plus_rows_beside_the_seat_rows(
    generator: Path, tmp_path: Path
) -> None:
    """Two sessions of one plus address MERGE; a bare declaration is refused
    and counted; a dead session's file and a box-less session's file do
    nothing; a persona's seat rows (`eve`, which owns `agents/eve/`) are
    unchanged beside the plus rows.

    SM-218 re-key: this map used to pin `foreman@ -> foreman@, s1@, s2@`,
    the bare-role fan-out. `foreman` has no box, so that row (and its
    +memory identity row) is now WITHHELD and named on stderr; the plus rows
    for the same two sessions are byte-identical. s5 has no session box, so
    it is a skip, not a withheld seat: the count says 2, not 3."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "foreman"},
         {"session_id": "s2", "agent": "foreman"},
         {"session_id": "s3", "agent": "eve"},
         {"session_id": "s5", "agent": "foreman"}],
        {"s1": PLUS, "s2": PLUS.upper(), "s3": "eve", "s4": "ghost+dead", "s5": "nobox+x"},
        ["s1", "s2", "s3", "s4", "eve"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"eve+memory@{HOST}\teve+memory@{HOST}\n"
        f"eve+memory@{FQDN}\teve+memory@{FQDN}\n"
        f"eve@{HOST}\teve@{HOST}, s3@{HOST}\n"
        f"eve@{FQDN}\teve@{FQDN}, s3@{FQDN}\n"
        f"{PLUS}@{HOST}\tsessionmail@{HOST}, s1@{HOST}, s2@{HOST}\n"
        f"{PLUS}@{FQDN}\tsessionmail@{FQDN}, s1@{FQDN}, s2@{FQDN}\n"
    )
    assert "plus_rows refused 1 declared address(es)" in done.stderr
    assert "s3=eve" in done.stderr
    assert "ghost" not in done.stdout and "nobox" not in done.stdout
    assert _withheld_line(done.stderr) == "withheld 2 seat record(s): foreman=2"


@needs_jq
def test_no_declarations_leave_the_alias_map_exactly_as_it_was(
    generator: Path, tmp_path: Path
) -> None:
    """SM-218 re-key: this used to pin a lone `foreman@ -> foreman@, s1@`
    row. With no plus declarations the map is the seat rows ALONE, and a
    seat row now needs its word to be a box: the persona `eve` keeps its
    row, `foreman` is withheld and named. stderr carries that line and
    nothing else, exactly as strict as the old `== ""`."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "foreman"},
                    {"session_id": "s2", "agent": "eve"}], {}, ["s1", "s2", "eve"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"eve+memory@{HOST}\teve+memory@{HOST}\n"
        f"eve+memory@{FQDN}\teve+memory@{FQDN}\n"
        f"eve@{HOST}\teve@{HOST}, s2@{HOST}\n"
        f"eve@{FQDN}\teve@{FQDN}, s2@{FQDN}\n"
    )
    assert done.stderr == (
        "vmailbox-gen: withheld 1 seat record(s) whose seat word is not itself a box"
        " (agents/<word>/new) - no <word>@ row written, word=sessions: foreman=1\n")


@pytest.mark.parametrize("address", [
    "sessionmail",          # the bare base itself
    "sessionmail+",         # empty extension
    "+sm-122",              # empty base
    "a+b+c",                # a second delimiter
    "sessionmail+sm 122",   # a character postfix would not fold to itself
    "",
])
def test_plus_rows_on_its_own_refuses_anything_but_base_plus_ext(
    generator: Path, tmp_path: Path, address: str
) -> None:
    """Design D4: the guard is a named function, tested on its own. A refusal
    prints NOTHING - there is no partial row to leak."""
    done = _run(generator, tmp_path, "--host", HOST, "--plus-rows", address, "s1")
    assert done.returncode == 3, (address, done.stdout, done.stderr)
    assert done.stdout == ""


def test_plus_rows_on_its_own_emits_only_plus_keys(generator: Path, tmp_path: Path) -> None:
    done = _run(generator, tmp_path, "--host", HOST, "--plus-rows", PLUS, "s1")
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"{PLUS}@{HOST}\tsessionmail@{HOST}\n"
        f"{PLUS}@{HOST}\ts1@{HOST}\n"
        f"{PLUS}@{FQDN}\tsessionmail@{FQDN}\n"
        f"{PLUS}@{FQDN}\ts1@{FQDN}\n"
    )
    for line in done.stdout.splitlines():
        assert "+" in line.split("\t")[0]


def test_plus_rows_never_splits_the_extension(generator: Path, tmp_path: Path) -> None:
    """sessionmail knows no task grammar: `sm-122.foreman` is one opaque
    extension, and a dotted base is one opaque base."""
    done = _run(generator, tmp_path, "--host", HOST, "--plus-rows", "a.b+c.d.e", "s1")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == f"a.b+c.d.e@{HOST}\ta.b@{HOST}"


# ── message-subscriptions 2.2: the subscription rows (design D1, D2) ────────
#
# A subscription is a FILE whose name is a box (D1):
#
#     $SESSIONMAIL_HOME/seats/<seat>/subscriptions/<box>     durable
#     $SESSIONMAIL_HOME/sessions/<sid>/subscriptions/<box>   while <sid> is live
#
# and the generator turns each one into a COPY row, the box itself first,
# both host forms, merged by key union with every other row (D2):
#
#     <box>@<host>   <box>@<host>, <subscriber>@<host>
#
# A PLUS-form box gets an EXACT row keyed on the plus address, and so CLAIMS
# its tag: postfix stops at the first key it finds, and `user+ext@domain` is
# tried before `user@domain` (`man 5 virtual`, ADDRESS EXTENSION). The bare
# box's subscribers get only the tags nobody claimed: the dead letter office
# (RULED, spine 10.32). `+memory` is the same claim, made by the box
# alone. Hermetic as above: --print-valias, a fake `session`, tmp homes.

# A seat's subscription file as `message subscription add` writes it
# (`_subscribe.note`, "%H:%M %a %Y-%m-%d"). The instant is SYNTHETIC on
# purpose, the epoch in UTC, so the fixture carries no real time.
SEAT_NOTE = (f"# added by ivan {time.strftime('%H:%M %a %Y-%m-%d', time.gmtime(0))} "
             "(message subscription add atomnote --seat)\n")
# The OLD saved-query files share the folder (seats/<seat>/subscriptions/,
# 192 on hq on Sun 2030-12-08, retired only at tasks 6.3). Their bodies
# carry `query:`; read by name each would become a routing row. The shape is
# copied from hq's seats/ivan/subscriptions/seat, measured by `head` only.
OLD_QUERY = (
    "# The seat's own address, born with the seat.\n"
    "query: to:$SEAT and not from:$SEAT@$HOST and date:1d..\n"
    "route: pager,ride-along\n"
    "policy: 3\n"
)


def _rows(text: str) -> dict[str, str]:
    """The alias map as {key: destinations}."""
    return dict(line.split("\t", 1) for line in text.splitlines())


@needs_jq
def test_a_seat_subscription_copies_the_box_to_the_seat_box_first_in_both_host_forms(
    generator: Path, tmp_path: Path
) -> None:
    """D2's first row kind, and the +memory identity rows that come with a
    bare box (the same claim, made by the box alone). The seat's own rows,
    from its live session, are unchanged beside it."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "ivan"}], {}, ["s1", "atomnote", "ivan"],
                   subs={"seats/ivan/subscriptions/atomnote": SEAT_NOTE})
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"atomnote+memory@{HOST}\tatomnote+memory@{HOST}\n"
        f"atomnote+memory@{FQDN}\tatomnote+memory@{FQDN}\n"
        f"atomnote@{HOST}\tatomnote@{HOST}, ivan@{HOST}\n"
        f"atomnote@{FQDN}\tatomnote@{FQDN}, ivan@{FQDN}\n"
        f"ivan+memory@{HOST}\tivan+memory@{HOST}\n"
        f"ivan+memory@{FQDN}\tivan+memory@{FQDN}\n"
        f"ivan@{HOST}\tivan@{HOST}, s1@{HOST}\n"
        f"ivan@{FQDN}\tivan@{FQDN}, s1@{FQDN}\n"
    )
    assert done.stderr == ""


@needs_jq
def test_a_session_subscription_counts_only_while_the_session_is_live_with_a_box(
    generator: Path, tmp_path: Path
) -> None:
    """D2: 'each subscription of a session that `session list` reports LIVE
    and whose box exists'. A dead session's file does nothing, with no
    reaper; a live session that never birthed its box does nothing."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "eve"}, {"session_id": "s2", "agent": "eve"}],
        {}, ["s1", "s9", "atomnote", "kith", "delta", "eve"],   # eve: a persona owns its box (SM-218)
        subs={"sessions/s1/subscriptions/atomnote": "",
              "sessions/s2/subscriptions/kith": "",       # live, no box
              "sessions/s9/subscriptions/delta": ""})     # box, not live
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, s1@{HOST}"
    assert rows[f"atomnote@{FQDN}"] == f"atomnote@{FQDN}, s1@{FQDN}"
    assert not any(k.startswith(("kith", "delta")) for k in rows)
    assert "s9" not in done.stdout
    assert done.stderr == "", "never read, so never counted: it is not a skip"


@needs_jq
def test_subscribers_of_one_box_merge_by_key_union_with_static_and_seat_rows(
    generator: Path, tmp_path: Path
) -> None:
    """D2: 'Rows merge by key union, as valias.static already does, so two
    subscribers of atomnote make one line'. The box itself leads; the
    hand-kept line is folded in, never dropped; a session subscribed to a
    SEAT box joins that seat's generated row rather than making a second."""
    static = tmp_path / "valias.static"
    static.write_text(f"atomnote@{HOST}  judy@{HOST}\n")
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "ivan"}, {"session_id": "s2", "agent": "eve"}],
        {}, ["s1", "s2", "atomnote", "ivan"], "--valias-static", str(static),
        subs={"seats/ivan/subscriptions/atomnote": SEAT_NOTE,
              "sessions/s2/subscriptions/atomnote": "",
              "sessions/s2/subscriptions/ivan": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, ivan@{HOST}, s2@{HOST}, judy@{HOST}"
    assert rows[f"atomnote@{FQDN}"] == f"atomnote@{FQDN}, ivan@{FQDN}, s2@{FQDN}, judy@{HOST}"
    assert rows[f"ivan@{HOST}"] == f"ivan@{HOST}, s1@{HOST}, s2@{HOST}"
    assert rows[f"ivan@{FQDN}"] == f"ivan@{FQDN}, s1@{FQDN}, s2@{FQDN}"
    assert rows[f"ivan+memory@{HOST}"] == f"ivan+memory@{HOST}"
    assert len(done.stdout.splitlines()) == len(rows), "one line per key"


@needs_jq
def test_a_plus_subscription_claims_its_tag_from_the_bare_box_subscribers(
    generator: Path, tmp_path: Path
) -> None:
    """Spec scenario 'A narrow subscription claims its tag': A on
    voicemode+kaizen, B on voicemode. The plus row is EXACT, keyed on the
    plus address, the box itself first, and B is NOT in it."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "sa", "agent": "foreman"}, {"session_id": "sb", "agent": "super"}],
        {}, ["sa", "sb", "voicemode"],
        subs={"sessions/sa/subscriptions/voicemode+kaizen": "",
              "sessions/sb/subscriptions/voicemode": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"voicemode+kaizen@{HOST}"] == f"voicemode+kaizen@{HOST}, sa@{HOST}"
    assert rows[f"voicemode+kaizen@{FQDN}"] == f"voicemode+kaizen@{FQDN}, sa@{FQDN}"
    assert rows[f"voicemode@{HOST}"] == f"voicemode@{HOST}, sb@{HOST}"
    assert rows[f"voicemode@{FQDN}"] == f"voicemode@{FQDN}, sb@{FQDN}"
    # A narrow subscription is not a bare one: no identity row for its tag,
    # and A reaches nothing on the bare key.
    assert "voicemode+kaizen+memory" not in done.stdout
    assert "sa@" not in rows[f"voicemode@{HOST}"]


@needs_jq
def test_an_unclaimed_tag_falls_through_to_the_bare_box_watcher(
    generator: Path, tmp_path: Path
) -> None:
    """Spec scenario 'An unclaimed tag falls through': A's session has gone,
    so at this rebuild NO row is keyed on voicemode+kaizen at all, and
    postfix carries the tag through the bare row to B (the dead letter
    office), with nothing to reap."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "sb", "agent": "super"}],
        {}, ["sa", "sb", "voicemode"],
        subs={"sessions/sa/subscriptions/voicemode+kaizen": "",
              "sessions/sb/subscriptions/voicemode": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert not any(k.startswith("voicemode+kaizen@") for k in rows)
    assert rows[f"voicemode@{HOST}"] == f"voicemode@{HOST}, sb@{HOST}"


@needs_jq
def test_the_memory_tag_stays_claimed_by_the_box_alone_with_no_live_seat_session(
    generator: Path, tmp_path: Path
) -> None:
    """Spec scenario 'A memory exclusion still wins'. Before subscriptions,
    only a LIVE session of the seat wrote `ivan+memory -> ivan+memory`. A
    session subscribed to the box `ivan` writes a row on `ivan` with no ivan
    session live, so the identity row must come with it, or ivan's memory
    writes would be copied into the subscriber's box."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "sm", "agent": "judy"}], {}, ["sm", "ivan"],
        subs={"sessions/sm/subscriptions/ivan": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"ivan@{HOST}"] == f"ivan@{HOST}, sm@{HOST}"
    assert rows[f"ivan+memory@{HOST}"] == f"ivan+memory@{HOST}"
    assert rows[f"ivan+memory@{FQDN}"] == f"ivan+memory@{FQDN}"


@needs_jq
def test_an_old_saved_query_file_is_skipped_and_counted_never_written(
    generator: Path, tmp_path: Path
) -> None:
    """A ruling on do-003: a file whose body
    carries a `query:` line is a retired saved query, not a subscription.
    FAIL SAFE: a stray old file must never route mail. hq's born pair,
    `seat` and `session`, is the fixture, in a seat folder and a live
    session folder; a note that only MENTIONS query: in a comment is still
    a subscription."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "ivan"}], {}, ["s1", "atomnote", "seat", "session"],
        subs={"seats/ivan/subscriptions/seat": OLD_QUERY,
              "seats/ivan/subscriptions/session": OLD_QUERY,
              "sessions/s1/subscriptions/seat": OLD_QUERY,
              "seats/ivan/subscriptions/atomnote":
                  SEAT_NOTE + "# why: replaces the old query: file\n"})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert not any(k.startswith(("seat@", "session@", "seat+", "session+")) for k in rows)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, ivan@{HOST}"
    assert "skipped 3 subscription file(s)" in done.stderr
    assert "seats/ivan/subscriptions/seat" in done.stderr
    assert "sessions/s1/subscriptions/seat" in done.stderr
    assert "query:" in done.stderr


@needs_jq
def test_a_store_project_box_is_stored_with_dashes_and_keyed_by_its_project(
    generator: Path, tmp_path: Path
) -> None:
    """D1: `work/demo` is stored as `work--demo`. Its
    address is the vmailbox key nested-v1 writes for it, `demo@host`.
    A `/` box whose first part is NOT a marked store has no address, so it
    is skipped and counted, never keyed on its last part (that would copy
    whichever box happens to share the name)."""
    root = tmp_path / "root" / "agents"
    (root / "work").mkdir(parents=True)
    (root / "work" / ".tm-store").write_text("")
    _maildir(root / "work" / "demo")
    _maildir(root / "plain" / "ivan")                   # no marker: not a store
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "eve"}], {}, ["s1"],
        subs={"sessions/s1/subscriptions/work--demo": "",
              "sessions/s1/subscriptions/plain--ivan": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"demo@{HOST}"] == f"demo@{HOST}, s1@{HOST}"
    assert rows[f"demo@{FQDN}"] == f"demo@{FQDN}, s1@{FQDN}"
    assert not any(k.startswith("ivan") for k in rows)
    assert "skipped 1 subscription file(s)" in done.stderr
    assert "mail.json#plain/ivan" in done.stderr        # the session's map key (3.3b)


@needs_jq
def test_a_name_that_is_not_a_stored_box_is_skipped_and_counted(
    generator: Path, tmp_path: Path
) -> None:
    """The codec's refusals (`_subscription.box_name`), in the generator:
    a name that would not round-trip, or holds a character postfix would
    not keep, is counted and never stripped into some OTHER box's key. A
    folder is counted; a dotfile (an atomic write in flight) is passed over
    silently. Case is kept in the file and folded in the key. (In an
    AGENT's folder: since 3.3b a session's boxes are map keys, not files.)"""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "eve"}], {}, ["s1", "atomnote"],
        subs={"seats/ivan/subscriptions/AtomNote": "",
              "seats/ivan/subscriptions/a---b": "",
              "seats/ivan/subscriptions/-kith": "",
              "seats/ivan/subscriptions/at!omnote": "",
              "seats/ivan/subscriptions/.kith.tmp": "",
              "seats/ivan/subscriptions/retired/kith": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, ivan@{HOST}"
    assert not any(k.startswith(("a-", "ab", "kith", "-")) for k in rows)
    assert "skipped 4 subscription file(s)" in done.stderr
    for name in ("a---b", "-kith", "at!omnote", "retired"):
        assert name in done.stderr, name
    assert ".kith.tmp" not in done.stderr


@needs_jq
def test_an_unreadable_subscription_file_is_skipped_since_it_could_be_a_saved_query(
    generator: Path, tmp_path: Path
) -> None:
    """Fail safe: a body the generator cannot read cannot be ruled out as an
    old saved query, so it is counted, never written. An unreadable folder
    is counted too, and does not stop the other folders' rows."""
    # builds the dirs; one live session, since an empty `ps` is "could not
    # look" under the sidecar reader (master 2030-12-08, merged at do-015)
    done_setup = _valias(generator, tmp_path, [{"session_id": "s1", "agent": "eve"}], {}, [])
    assert done_setup.returncode == 0, done_setup.stderr
    home = tmp_path / "sessionmail"
    locked = home / "sessions" / "s1" / "subscriptions" / "kith"
    locked.parent.mkdir(parents=True)
    locked.write_text("")
    locked.chmod(0o000)
    shut = home / "seats" / "eve" / "subscriptions"
    shut.mkdir(parents=True)
    (shut / "delta").write_text("")
    shut.chmod(0o000)
    try:
        done = _valias(generator, tmp_path,
                       [{"session_id": "s1", "agent": "eve"}], {}, ["s1", "atomnote", "kith", "delta"],
                       subs={"seats/ivan/subscriptions/atomnote": SEAT_NOTE})
    finally:
        locked.chmod(0o644)
        shut.chmod(0o755)
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, ivan@{HOST}"
    assert not any(k.startswith(("kith", "delta")) for k in rows)
    assert "skipped 2 subscription file(s)" in done.stderr
    assert "cannot read the file" in done.stderr
    assert "cannot read the folder" in done.stderr


@needs_jq
def test_the_written_alias_map_counts_subscriptions(generator: Path, tmp_path: Path) -> None:
    """The write path (not --print-valias) says how many subscriptions it
    wrote and skipped, and writes the same rows."""
    out = tmp_path / "maps" / "vmailbox"
    out.parent.mkdir()
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "ivan"}], {}, ["s1", "atomnote", "seat"],
                   subs={"seats/ivan/subscriptions/atomnote": SEAT_NOTE,
                         "seats/ivan/subscriptions/seat": OLD_QUERY})
    printed = done.stdout
    import json, os as _os
    env = {"PATH": f"{tmp_path / 'bin'}:{_os.environ.get('PATH', '/usr/bin:/bin')}",
           "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path),
           "MAGICDNS_SUFFIX": SUFFIX, "LC_ALL": "C",
           "FAKE_PS_TABLE": str(tmp_path / "ps.txt"),
           "FAKE_PS_TABLE_LOCAL": str(tmp_path / "ps-local.txt"),
           "FAKE_SESSION_CALLS": str(tmp_path / "session-calls"),
           "SESSION_SIDECAR_DIR": str(tmp_path / "claude-sessions"),
           "SESSIONMAIL_HOME": str(tmp_path / "sessionmail")}
    wrote = subprocess.run(
        [str(generator), "--valias-only", "--no-postmap", "--host", HOST,
         "--root", str(tmp_path / "root"), "--out", str(out)],
        capture_output=True, text=True, env=env, check=False)
    assert wrote.returncode == 0, wrote.stderr
    assert "1 subscription(s), 1 skipped" in wrote.stdout
    assert (out.parent / "valias").read_text() == printed


# ── message-subscriptions 2.3 + 2.4: the pinned fixture, and resolvability ──
#
# 2.3: the new rows are pinned, whole-map, over one synthetic root. 2.4: a
# subscription to a box that does not RESOLVE is skipped and counted on
# stderr, never written. "Resolves" is what `message path` means by it
# (`_seat.box_via_map`): postfix's virtual_mailbox_maps has a row for
# `<name>@<host>`, or for its base at the first `+` (the tag is a slot in
# the same maildir). The generator already builds that map from the same
# tree, so it asks its own `generate()` - flat boxes, store boxes and nested
# projects alike (flag 5, ruled: flat boxes too, not only store/project).

def _two_forms(text: str) -> list[str]:
    """Every row naming ONE box in BOTH host forms among its destinations:
    `ivan@<host>` beside `ivan@<fqdn>` is two copies into one maildir (flag
    1, ruled: the fqdn key carries fqdn destinations, like every row)."""
    found = []
    for line in text.splitlines():
        _, _, dests = line.partition("\t")
        locals_by_form: dict[str, set[str]] = {}
        for d in (x.strip() for x in dests.split(",")):
            local, _, domain = d.partition("@")
            locals_by_form.setdefault(local, set()).add(domain)
        if any(len(forms) > 1 for forms in locals_by_form.values()):
            found.append(line)
    return found


def test_the_two_forms_check_can_fail() -> None:
    """The pin below is only worth something if it catches D2's mock-up row."""
    mockup = f"atomnote@{FQDN}\tatomnote@{HOST}, ivan@{HOST}, atomnote@{FQDN}\n"
    assert _two_forms(mockup) == [mockup.rstrip("\n")]
    assert _two_forms(f"atomnote@{FQDN}\tatomnote@{FQDN}, ivan@{FQDN}\n") == []


def _subscription_fleet(tmp_path: Path) -> Path:
    """The 2.3 fixture root: a flat box, two seat boxes, a store with two
    projects, a store box, and two live sessions with boxes."""
    agents = tmp_path / "root" / "agents"
    for flat in ("atomnote", "ivan", "eve"):
        _maildir(agents / flat)
    (agents / "tmstore").mkdir(parents=True)
    (agents / "tmstore" / ".tm-store").write_text("")
    _maildir(agents / "tmstore" / "voicemode")
    (agents / "work").mkdir()
    (agents / "work" / ".tm-store").write_text("")
    _maildir(agents / "work")                       # a store box too
    _maildir(agents / "work" / "demo")
    return tmp_path / "root"


@needs_jq
def test_the_subscription_rows_are_pinned_over_a_fixture(generator: Path, tmp_path: Path) -> None:
    """tasks 2.3: the whole alias map over the fixture, byte for byte. Every
    row kind is here - a seat subscription merged with a seat's own row and
    a static line, a narrow plus subscription beside a bare one on the same
    project, a store/project box, a store box - and three that must NOT be
    written: a flat box, a plus box and a store/project that do not resolve."""
    _subscription_fleet(tmp_path)
    static = tmp_path / "valias.static"
    static.write_text(f"atomnote@{HOST}  judy@{HOST}\n")
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "ivan"}, {"session_id": "s2", "agent": "eve"}],
        {}, ["s1", "s2"], "--valias-static", str(static),
        subs={"seats/ivan/subscriptions/atomnote": SEAT_NOTE,
              "seats/ivan/subscriptions/voicemode": SEAT_NOTE,
              "sessions/s2/subscriptions/voicemode+kaizen": "",
              "sessions/s2/subscriptions/work--demo": "",
              "sessions/s2/subscriptions/work": "",
              "sessions/s2/subscriptions/ivan": "",
              "sessions/s1/subscriptions/nosuchbox": "",
              "sessions/s1/subscriptions/ghost+kaizen": "",
              "sessions/s1/subscriptions/tmstore--nosuchproject": ""})
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"atomnote+memory@{HOST}\tatomnote+memory@{HOST}\n"
        f"atomnote+memory@{FQDN}\tatomnote+memory@{FQDN}\n"
        f"atomnote@{HOST}\tatomnote@{HOST}, ivan@{HOST}, judy@{HOST}\n"
        f"atomnote@{FQDN}\tatomnote@{FQDN}, ivan@{FQDN}, judy@{HOST}\n"
        f"demo+memory@{HOST}\tdemo+memory@{HOST}\n"
        f"demo+memory@{FQDN}\tdemo+memory@{FQDN}\n"
        f"demo@{HOST}\tdemo@{HOST}, s2@{HOST}\n"
        f"demo@{FQDN}\tdemo@{FQDN}, s2@{FQDN}\n"
        f"eve+memory@{HOST}\teve+memory@{HOST}\n"
        f"eve+memory@{FQDN}\teve+memory@{FQDN}\n"
        f"eve@{HOST}\teve@{HOST}, s2@{HOST}\n"
        f"eve@{FQDN}\teve@{FQDN}, s2@{FQDN}\n"
        f"ivan+memory@{HOST}\tivan+memory@{HOST}\n"
        f"ivan+memory@{FQDN}\tivan+memory@{FQDN}\n"
        f"ivan@{HOST}\tivan@{HOST}, s1@{HOST}, s2@{HOST}\n"
        f"ivan@{FQDN}\tivan@{FQDN}, s1@{FQDN}, s2@{FQDN}\n"
        f"voicemode+kaizen@{HOST}\tvoicemode+kaizen@{HOST}, s2@{HOST}\n"
        f"voicemode+kaizen@{FQDN}\tvoicemode+kaizen@{FQDN}, s2@{FQDN}\n"
        f"voicemode+memory@{HOST}\tvoicemode+memory@{HOST}\n"
        f"voicemode+memory@{FQDN}\tvoicemode+memory@{FQDN}\n"
        f"voicemode@{HOST}\tvoicemode@{HOST}, ivan@{HOST}\n"
        f"voicemode@{FQDN}\tvoicemode@{FQDN}, ivan@{FQDN}\n"
        f"work+memory@{HOST}\twork+memory@{HOST}\n"
        f"work+memory@{FQDN}\twork+memory@{FQDN}\n"
        f"work@{HOST}\twork@{HOST}, s2@{HOST}\n"
        f"work@{FQDN}\twork@{FQDN}, s2@{FQDN}\n"
    )
    # Flag 1: the static line names `judy@<host>` on the fqdn key, as a human
    # wrote it; no GENERATED destination is ever in the other host form.
    assert _two_forms(done.stdout) == []
    assert "skipped 3 subscription file(s)" in done.stderr


@needs_jq
def test_a_subscription_to_a_box_that_does_not_resolve_is_skipped_counted_and_not_written(
    generator: Path, tmp_path: Path
) -> None:
    """tasks 2.4 / design D2: 'At build time the generator skips it and
    counts it on stderr'. Flat, plus (its base has no row) and store/project
    alike, in a seat folder and a session folder. A plus box whose BASE
    resolves is not skipped: postfix delivers the tag into the base's box."""
    _subscription_fleet(tmp_path)
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "eve"}], {}, ["s1"],
        subs={"seats/ivan/subscriptions/nosuchbox": SEAT_NOTE,
              "sessions/s1/subscriptions/ghost+kaizen": "",
              "sessions/s1/subscriptions/NoSuchBox": "",
              "sessions/s1/subscriptions/voicemode+vm-2266": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert not any(k.startswith(("nosuchbox", "ghost")) for k in rows)
    assert "nosuchbox@" not in done.stdout.lower() and "ghost" not in done.stdout
    assert rows[f"voicemode+vm-2266@{HOST}"] == f"voicemode+vm-2266@{HOST}, s1@{HOST}"
    assert "skipped 3 subscription file(s)" in done.stderr
    for name in ("seats/ivan/subscriptions/nosuchbox",
                 ".session/sessions/s1/settings/mail.json#ghost+kaizen",   # a map key (3.3b)
                 ".session/sessions/s1/settings/mail.json#NoSuchBox"):
        assert name in done.stderr, name
    assert "does not resolve" in done.stderr


# ── parity: the bash codec and the reader agree (do-002 review flag 2; flag 4)
#
# vmailbox-gen is bash and ships loose, so it cannot import _subscription.py;
# it restates the codec. These tests are what stop the two drifting. The
# table is SHARED: one row, both implementations.

import sessionmail._subscription as _sub  # noqa: E402

CODEC_TABLE = [
    # (stored file name, the box it reads back as, or None = refused)
    ("atomnote", "atomnote"),
    ("work--demo", "work/demo"),
    ("voicemode+vm-2266", "voicemode+vm-2266"),
    ("foreman.VM-1802", "foreman.VM-1802"),       # case is KEPT
    ("a-b", "a-b"),
    ("a--b--c", "a/b/c"),                         # the codec reads it; an address will not
    ("at!omnote", "at!omnote"),                   # the codec reads it; an address will not
    ("--help", None),                             # the named case
    ("--a", None),
    ("a--", None),
    ("a---b", None),
    ("a----b", None),
    ("-x", None),
    ("x-", None),
    (".x", None),
    ("a--.b", None),
    ("a b", None),
    ("a\tb", None),
    ("", None),
    # do-014 (d): NON-ASCII is refused by both, in every locale. Before, bash
    # under LC_ALL=C read NBSP and U+2028 as ordinary bytes and accepted them
    # while Python's \s refused them (330 of 3,000 fuzz names, do-004 review).
    ("a b", None),                           # NBSP
    ("a b", None),                           # LINE SEPARATOR
    ("atomé", None),                              # not white space, still not ASCII
]


def _bash_box(generator: Path, tmp_path: Path, stored: str) -> str | None:
    done = _run(generator, tmp_path, "--subscription-box", stored)
    if done.returncode == 3:
        assert done.stdout == "", "a refusal prints nothing on stdout"
        return None
    assert done.returncode == 0, (stored, done.returncode, done.stderr)
    return done.stdout


def _python_box(stored: str) -> str | None:
    try:
        return _sub.box_name(stored)
    except _sub.BadBox:
        return None


@pytest.mark.parametrize("stored,box", CODEC_TABLE)
def test_the_bash_codec_and_the_reader_read_every_name_alike(
    generator: Path, tmp_path: Path, stored: str, box: str | None
) -> None:
    assert _python_box(stored) == box
    assert _bash_box(generator, tmp_path, stored) == box
    if box is not None:
        assert _sub.file_name(box) == stored, "and it writes back as the same file"


def _generator_skips(stderr: str, home: Path) -> set[str]:
    """The skipped paths the generator named on stderr, relative to HOME."""
    for line in stderr.splitlines():
        head = "vmailbox-gen: subscriptions: skipped "
        if line.startswith(head):
            items = line.split("never written: ", 1)[1].split("; ")
            return {i.split(" (", 1)[0] for i in items}
    return set()


@needs_jq
def test_the_reader_and_the_generator_skip_the_same_files(generator: Path, tmp_path: Path) -> None:
    """Flag 4, ruled: `_subscription.scan` skips exactly what the
    generator skips - old saved queries, unreadable files, bad names,
    folders - so `message subscription list` cannot show a file the map
    never routes. Every good name here resolves, so the only skips are the
    ones both sides can see (the reader has no mail root, by design)."""
    _subscription_fleet(tmp_path)
    locked_rel = "sessions/s1/subscriptions/eve"
    subs = {"seats/ivan/subscriptions/atomnote": SEAT_NOTE,
            "seats/ivan/subscriptions/seat": OLD_QUERY,
            "seats/ivan/subscriptions/session": "  " + OLD_QUERY,
            "seats/ivan/subscriptions/ivan": "# a note that mentions query: only\n",
            "seats/ivan/subscriptions/a---b": "",
            "seats/ivan/subscriptions/retired/old": OLD_QUERY,
            "seats/ivan/subscriptions/.voicemode.tmp": "",
            "sessions/s1/subscriptions/seat": OLD_QUERY,
            "sessions/s1/subscriptions/voicemode": "",
            locked_rel: ""}
    home = tmp_path / "sessionmail"
    locked = home / locked_rel
    for rel, body in subs.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(body)
    # 3.3b: the session's subscription is its map key; the box file left in
    # its old folder routes nothing, and BOTH sides skip it as unconverted.
    session_subscribe("s1", "voicemode", home=tmp_path / "home")
    locked.chmod(0o000)
    try:
        done = _valias(generator, tmp_path,
                       [{"session_id": "s1", "agent": "eve"}], {}, ["s1"])
        scan = _sub.scan(home, live={"s1"}, env={"HOME": str(tmp_path / "home")})
    finally:
        locked.chmod(0o644)
    assert done.returncode == 0, done.stderr
    expected = {"seats/ivan/subscriptions/seat", "seats/ivan/subscriptions/session",
                "seats/ivan/subscriptions/a---b", "seats/ivan/subscriptions/retired",
                "sessions/s1/subscriptions/seat", "sessions/s1/subscriptions/voicemode",
                locked_rel}
    assert {str(p.relative_to(home)) for p, _ in scan.skipped} == expected
    assert _generator_skips(done.stderr, home) == expected
    assert scan.pairs == {("ivan", "atomnote"), ("ivan", "ivan"), ("s1", "voicemode")}


# ── do-014: generator safety ──────────────────────────────────────────────
#
# (a) one box, one copy on a doorbell's key; (b) `<x>+memory` stays an
# identity; (c) the U path refuses a session id san() would change; (d) the
# codecs refuse non-ASCII in every locale; (e) "resolves" means a MAILDIR.

@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("stored", ["a b", "a b", "atomé", "vm+é"])
def test_both_codecs_refuse_non_ascii_in_every_locale(
    generator: Path, tmp_path: Path, locale: str, stored: str
) -> None:
    """(d): the parity table's rows run under LC_ALL=C via `_run`; the SAME
    refusal must hold under a UTF-8 locale, where bash's character classes
    see a multibyte character as one (printable) letter."""
    assert _python_box(stored) is None
    with pytest.raises(_sub.BadBox):
        _sub.file_name(stored)
    env = {**os.environ, "HOME": str(tmp_path), "LC_ALL": locale}
    done = subprocess.run([str(generator), "--subscription-box", stored],
                          capture_output=True, text=True, env=env, check=False)
    assert (done.returncode, done.stdout) == (3, ""), done.stderr
    assert "ASCII" in done.stderr


@needs_jq
def test_a_plus_subscription_on_a_doorbell_key_puts_the_box_in_the_row_once(
    generator: Path, tmp_path: Path
) -> None:
    """(a) ONE BOX, ONE COPY. A task doorbell writes `<base>+<ext> -> <base>,
    <sid>`. A subscription to the same plus address must not add the plus
    form itself as well: `<base>@` and `<base>+<ext>@` both land in <base>'s
    maildir, so the box would get the mail twice. The subscriber IS added."""
    door = "sessionmail+sm-362.foreman"
    done = _valias(
        generator, tmp_path,
        [{"session_id": "sd", "agent": "foreman"}, {"session_id": "s2", "agent": "ivan"}],
        {"sd": door}, ["sd", "s2", "sessionmail"],
        subs={f"sessions/s2/subscriptions/{door}": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"{door}@{HOST}"] == f"sessionmail@{HOST}, sd@{HOST}, s2@{HOST}"
    assert rows[f"{door}@{FQDN}"] == f"sessionmail@{FQDN}, sd@{FQDN}, s2@{FQDN}"


@needs_jq
def test_a_plus_subscription_with_no_doorbell_still_leads_with_itself(
    generator: Path, tmp_path: Path
) -> None:
    """(a)'s other half: the dedupe is for a key whose row already delivers
    to the box, never a licence to drop the box from a fresh plus row."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s2", "agent": "ivan"}], {}, ["s2", "sessionmail"],
        subs={"sessions/s2/subscriptions/sessionmail+sm-362.foreman": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"sessionmail+sm-362.foreman@{HOST}"] == \
        f"sessionmail+sm-362.foreman@{HOST}, s2@{HOST}"


@needs_jq
def test_a_memory_subscription_is_skipped_and_the_identity_row_keeps_only_the_box(
    generator: Path, tmp_path: Path
) -> None:
    """(b): `<x>+memory` is claimed by the box ALONE (the identity row), so a
    subscription to it would defeat the memory exclusion. Skipped, counted,
    never written - in any case, in a seat or a session folder."""
    done = _valias(
        generator, tmp_path,
        [{"session_id": "sc", "agent": "ivan"}, {"session_id": "s2", "agent": "eve"}],
        {}, ["sc", "s2", "ivan", "eve", "atomnote"],   # eve: a persona owns its box (SM-218)
        subs={"sessions/s2/subscriptions/ivan+memory": "",
              "seats/eve/subscriptions/Ivan+Memory": "",
              "sessions/s2/subscriptions/atomnote+memory": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"ivan+memory@{HOST}"] == f"ivan+memory@{HOST}"
    assert rows[f"ivan+memory@{FQDN}"] == f"ivan+memory@{FQDN}"
    assert not any(k.startswith("atomnote") for k in rows)
    # s2 reaches its own seat's rows and nothing else.
    assert {k for k, v in rows.items() if "s2@" in v} == {f"eve@{HOST}", f"eve@{FQDN}"}
    assert "skipped 3 subscription file(s)" in done.stderr
    assert "+memory" in done.stderr


@needs_jq
def test_a_session_id_that_san_would_change_is_refused_not_written(
    generator: Path, tmp_path: Path
) -> None:
    """(c): the D path runs every session id through san(); the U path must
    not trust one either. A sid holding `, ` would otherwise put a SECOND
    destination (`judy@`) into the box's row."""
    bad = "s1, judy"
    done = _valias(
        generator, tmp_path,
        [{"session_id": bad, "agent": "eve"}, {"session_id": "s2", "agent": "eve"}],
        {}, [bad, "s2", "atomnote", "kith"],
        subs={f"sessions/{bad}/subscriptions/atomnote": "",
              "sessions/s2/subscriptions/kith": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert f"atomnote@{HOST}" not in rows
    # No destination is `judy@` (the D path's own san()'d `s1judy@` is fine).
    dests = {d.strip() for v in rows.values() for d in v.split(",")}
    assert not any(d.startswith("judy@") for d in dests), dests
    assert rows[f"kith@{HOST}"] == f"kith@{HOST}, s2@{HOST}"
    assert "skipped 1 subscription file(s)" in done.stderr
    assert bad in done.stderr


@needs_jq
def test_a_directory_that_is_not_a_maildir_does_not_resolve(
    generator: Path, tmp_path: Path
) -> None:
    """(e): 'resolves' is `_seat.box_via_map`'s test, which also needs
    `<path>/new`. generate() maps EVERY directory under agents/, so a plain
    folder (or a store project with no maildir) has a map row but is no box."""
    agents = tmp_path / "root" / "agents"
    (agents / "notabox").mkdir(parents=True)
    (agents / "tmstore" / "halfproject").mkdir(parents=True)
    (agents / "tmstore" / ".tm-store").write_text("")
    done = _valias(
        generator, tmp_path,
        [{"session_id": "s1", "agent": "eve"}], {}, ["s1", "atomnote"],
        subs={"sessions/s1/subscriptions/notabox": "",
              "sessions/s1/subscriptions/notabox+kaizen": "",
              "sessions/s1/subscriptions/tmstore--halfproject": "",
              "sessions/s1/subscriptions/atomnote": ""})
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, s1@{HOST}"
    assert not any(k.startswith(("notabox", "halfproject")) for k in rows)
    assert "skipped 3 subscription file(s)" in done.stderr
    assert "does not resolve" in done.stderr


# ── live sessions come from the sidecars, not from `session` (2030-12-08) ──
#
# The session package goes. The doorbell
# read `session list --json`; with session gone it would refuse and every NEW
# session would go without a doorbell. The generator now reads Claude Code's
# sidecars itself, under the rule `session list` used.

def _seat_rows(text: str, seat: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.split("@", 1)[0] == seat]


@needs_jq
def test_the_generator_never_calls_session(generator: Path, tmp_path: Path) -> None:
    # SM-218 re-key: the seat is the persona `eve`, which owns its box, so a
    # missing row below can only mean "not live", never "withheld".
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "eve"}], {}, ["s1", "eve"])
    assert done.returncode == 0, done.stderr
    assert f"eve@{HOST}\teve@{HOST}, s1@{HOST}\n" in done.stdout
    assert not (tmp_path / "session-calls").exists(), (tmp_path / "session-calls").read_text()


@needs_jq
def test_a_dead_session_has_no_line(generator: Path, tmp_path: Path) -> None:
    """Its sidecar is still on disk (a crash leaves one) but its pid is gone."""
    # SM-218 re-key: the seat is the persona `eve`, which owns its box, so a
    # missing row below can only mean "not live", never "withheld".
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "eve"},
                    {"session_id": "s2", "agent": "eve", "alive": False}],
                   {}, ["s1", "s2", "eve"])
    assert done.returncode == 0, done.stderr
    assert f"eve@{HOST}\teve@{HOST}, s1@{HOST}\n" in done.stdout
    assert "s2@" not in done.stdout
    assert _withheld_line(done.stderr) is None, done.stderr


@needs_jq
@pytest.mark.parametrize("ps_start, live", [
    ("Sun Jun 09 12:00:00 2030", True),     # the same process
    ("Sun Jun 09 12:00:02 2030", True),     # 2 s: session list's tolerance
    ("Sun Jun 09 12:00:03 2030", False),    # 3 s: a reused pid, not the session
    ("Sun Dec 08 01:00:00 2030", False),
])
def test_a_reused_pid_is_not_the_session(generator: Path, tmp_path: Path,
                                         ps_start: str, live: bool) -> None:
    # SM-218 re-key: the seat is the persona `eve`, which owns its box, so a
    # missing row below can only mean "not live", never "withheld".
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "eve", "ps_start": ps_start}],
                   {}, ["s1", "eve"])
    assert done.returncode == 0, done.stderr
    assert ("s1@" in done.stdout) is live, done.stdout
    assert _withheld_line(done.stderr) is None, done.stderr


@needs_jq
def test_ps_single_digit_day_padding_still_matches(generator: Path, tmp_path: Path) -> None:
    """`ps` pads a one-digit day with a space ("Jun  4"); ctime does too."""
    import json
    # SM-218 re-key: the seat is the persona `eve`, which owns its box, so a
    # missing row below can only mean "not live", never "withheld".
    car = {"pid": 40000, "sessionId": "s1", "agent": "eve",
           "procStart": "Sun Jun  9 01:02:03 2030"}
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "eve", "raw": json.dumps(car),
                     "ps_start": "Sun Jun  9 01:02:03 2030"}], {}, ["s1", "eve"])
    assert done.returncode == 0, done.stderr
    assert "s1@" in done.stdout


@needs_jq
def test_a_torn_sidecar_is_skipped_and_the_rest_still_written(
    generator: Path, tmp_path: Path
) -> None:
    # SM-218 re-key: both seat words are personas that own their boxes, so a
    # missing `eve` row can only mean "torn sidecar", never "withheld".
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "ivan"},
                    {"session_id": "s2", "agent": "eve", "raw": '{"pid": 40001, "sessi'},
                    {"session_id": "s3", "agent": "eve", "raw": "[1, 2]"}],
                   {}, ["s1", "s2", "s3", "ivan", "eve"])
    assert done.returncode == 0, done.stderr
    assert f"ivan@{HOST}\tivan@{HOST}, s1@{HOST}\n" in done.stdout
    assert _seat_rows(done.stdout, "eve") == []
    assert _withheld_line(done.stderr) is None, done.stderr


@needs_jq
def test_a_session_without_an_agent_gets_no_seat_line(generator: Path, tmp_path: Path) -> None:
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": None}, {"session_id": "s2", "agent": "eve"}],
                   {}, ["s1", "s2", "eve"])   # eve: a persona owns its box (SM-218)
    assert done.returncode == 0, done.stderr
    assert "s1@" not in done.stdout
    assert f"eve@{HOST}\teve@{HOST}, s2@{HOST}\n" in done.stdout
    assert _withheld_line(done.stderr) is None, "no agent is no seat record, not a withheld one"


@needs_jq
def test_no_sidecar_dir_refuses_and_prints_nothing(generator: Path, tmp_path: Path) -> None:
    """Cannot look is not nobody is live: an empty map would switch every
    seat->session copy off, so it refuses and the old map stays."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": "foreman"}], {}, ["s1"],
                   sidecar_dir=tmp_path / "no-such-dir")
    assert done.returncode == 3, done.stderr
    assert done.stdout == ""
    assert "sidecar" in done.stderr


# ── SM-809: a --print-valias refusal touches NO files ────────────────────────
#
# The header promises `--print-valias` writes "to STDOUT only - touch NO files"
# (vmailbox-gen, the `--print-valias` usage line). Its refusal used to mkdir
# `$MAIL_ROOT/.state` and append `regen-failures.log` there, so a dry run against
# a live tree created a dir in it. The breadcrumb stays on the REGENERATE path.
# Scratch only: `--root` AND `MAIL_ROOT` are both a dir under tmp_path, HOME too,
# and the regenerate case runs `--out` into tmp_path with `--no-postmap`.

REFUSED = "valias NOT regenerated"
SM809_SUFFIX = "example.test"


def _tree(top: Path) -> list[tuple[str, str, bytes]]:
    """Every entry under TOP, with its kind and (for a file) its bytes."""
    out = []
    for p in sorted(top.rglob("*")):
        rel = str(p.relative_to(top))
        if p.is_symlink():
            out.append((rel, "link", os.readlink(p).encode()))
        elif p.is_dir():
            out.append((rel, "dir", b""))
        else:
            out.append((rel, "file", p.read_bytes()))
    return out


def _path_without_jq(tmp_path: Path) -> Path:
    """A PATH dir holding a symlink to every command on the current PATH except
    `jq` (first hit wins, as PATH would resolve it)."""
    bin_dir = tmp_path / "bin-no-jq"
    bin_dir.mkdir()
    for d in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name == "jq" or (bin_dir / name).exists() or (bin_dir / name).is_symlink():
                continue
            src = os.path.join(d, name)
            if os.path.isfile(src) and os.access(src, os.X_OK):
                (bin_dir / name).symlink_to(src)
    assert not (bin_dir / "jq").exists()
    assert (bin_dir / "bash").exists(), "the generator needs bash on PATH"
    return bin_dir


def _sm809_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    _maildir(root / "agents" / "worker.sm-1")
    (tmp_path / "home").mkdir(exist_ok=True)
    return root


def _sm809_env(tmp_path: Path, root: Path, path: str, sidecars: Path) -> dict[str, str]:
    return {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "MAIL_ROOT": str(root),
        "MAGICDNS_SUFFIX": SM809_SUFFIX,
        "LC_ALL": "C",
        "SESSION_SIDECAR_DIR": str(sidecars),
        "SESSIONMAIL_HOME": str(tmp_path / "sessionmail"),
    }


@pytest.mark.parametrize("cause", [
    pytest.param("no-sidecar-dir", marks=needs_jq),
    "no-jq",
])
def test_a_print_valias_refusal_writes_no_file_and_no_dir(
    generator: Path, tmp_path: Path, cause: str
) -> None:
    root = _sm809_root(tmp_path)
    if cause == "no-jq":
        path = str(_path_without_jq(tmp_path))
        sidecars = tmp_path / "claude-sessions"
        sidecars.mkdir()
        reason = "no 'jq' on PATH"
    else:
        path = os.environ.get("PATH", "/usr/bin:/bin")
        sidecars = tmp_path / "no-such-sidecar-dir"
        reason = "could not read the live sessions"
    before_root = _tree(root)
    before_all = _tree(tmp_path)
    done = subprocess.run(
        [str(generator), "--print-valias", "--host", HOST, "--root", str(root)],
        capture_output=True, text=True, check=False,
        env=_sm809_env(tmp_path, root, path, sidecars))
    assert done.returncode == 3, done.stderr
    assert done.stdout == ""
    assert REFUSED in done.stderr and reason in done.stderr, done.stderr
    assert not (root / ".state").exists()
    assert _tree(root) == before_root
    assert _tree(tmp_path) == before_all, "nothing anywhere under the scratch dir"


def test_a_regenerate_refusal_still_leaves_the_breadcrumb(
    generator: Path, tmp_path: Path
) -> None:
    """The regenerate path keeps `.state/regen-failures.log`: stderr there is
    read by nobody (the header's reason). This is also the control that shows
    `_tree` sees a created `.state`."""
    root = _sm809_root(tmp_path)
    maps = tmp_path / "maps"
    maps.mkdir()
    before_root = _tree(root)
    done = subprocess.run(
        [str(generator), "--no-postmap", "--host", HOST, "--root", str(root),
         "--out", str(maps / "vmailbox")],
        capture_output=True, text=True, check=False,
        env=_sm809_env(tmp_path, root, os.environ.get("PATH", "/usr/bin:/bin"),
                       tmp_path / "no-such-sidecar-dir"))
    assert done.returncode == 3, done.stderr
    assert REFUSED in done.stderr
    assert (maps / "vmailbox").is_file(), "vmailbox is written before the exit 3"
    log = root / ".state" / "regen-failures.log"
    assert log.is_file()
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    assert "\tvalias\tREFUSED\t" in lines[0]
    assert _tree(root) != before_root


# ── SM-218: a bare role word must not be a doorbell key ──────────────────────
#
# The DYNAMIC record loop (`vmailbox-gen:632-641 @ a65f434`) keys a row on the
# Claude Code sidecar's `agent` word, and the awk `D` branch (`:703-707`) unions
# every live session sharing that word into ONE row. For a taskmaster role seat
# the word is the bare role (`worker`, `foreman`, `super`, ...), so
# `worker@<host>` fans out to every live worker on the host at once: once
# a reply to `worker@hq` carried an order to two unrelated idle
# workers and never reached the one it was meant for.
#
# A persona's word (`ivan`, `eve`, `rupert`) is a real NAME, with a real box
# `agents/<name>/`; its row is wanted and must survive the fix. The control
# below pins that.

ROLE_WORDS = ["worker", "foreman", "super", "engineer", "general_contractor"]


def _rows_naming(text: str, word: str, sids: list[str]) -> list[str]:
    """Every row keyed on the bare WORD (either host form) that delivers into
    any of SIDS' session boxes: the fan-out SM-218 is about."""
    found = []
    for line in text.splitlines():
        key, _, dests = line.partition("\t")
        if key not in (f"{word}@{HOST}", f"{word}@{FQDN}"):
            continue
        names = [d.strip().split("@", 1)[0] for d in dests.split(",")]
        if any(sid in names for sid in sids):
            found.append(line)
    return found


def test_the_role_word_fan_out_check_can_fail() -> None:
    """The SM-218 assertion is only worth something if it catches the row
    measured live on 2030-12-08 (and leaves a lookalike key alone)."""
    live = (f"worker@{HOST}\tworker@{HOST}, w1@{HOST}, w2@{HOST}\n"
            f"worker@{FQDN}\tworker@{FQDN}, w1@{FQDN}, w2@{FQDN}\n")
    assert len(_rows_naming(live, "worker", ["w1", "w2"])) == 2
    assert _rows_naming(f"worker.sm-218@{HOST}\tw1@{HOST}\n", "worker", ["w1"]) == []
    assert _rows_naming(f"worker@{HOST}\tworker@{HOST}\n", "worker", ["w1"]) == []


@needs_jq
@pytest.mark.parametrize("word", ROLE_WORDS)
def test_two_live_seats_of_one_role_word_share_no_doorbell(
    generator: Path, tmp_path: Path, word: str
) -> None:
    """SM-218 repro: two live seats whose sidecars both say `agent: <role>`,
    each with its own session box. No `<role>@<host>` / `<role>@<fqdn>` key
    may deliver into either box. Before the fix the generator wrote
    `worker@testhost  worker@testhost, w1@testhost, w2@testhost`; now the
    record is withheld, and the word is named on stderr with its count."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "w1", "agent": word},
                    {"session_id": "w2", "agent": word}],
                   {}, ["w1", "w2"])
    assert done.returncode == 0, done.stderr
    assert _rows_naming(done.stdout, word, ["w1", "w2"]) == []
    assert not any(k.split("@", 1)[0] in (word, f"{word}+memory") for k in _rows(done.stdout))
    assert _withheld_line(done.stderr) == f"withheld 2 seat record(s): {word}=2"


@needs_jq
def test_sm218_a_persona_with_a_real_box_keeps_its_doorbell(
    generator: Path, tmp_path: Path
) -> None:
    """SM-218 CONTROL, green before and after the fix: a persona sidecar
    (`agent: ivan`) whose name owns a real box `agents/ivan/` keeps
    `ivan@<host> -> ivan@<host>, <sid>@<host>` in both host forms."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "c1", "agent": "ivan"}],
                   {}, ["c1", "ivan"])
    assert done.returncode == 0, done.stderr
    assert f"ivan@{HOST}\tivan@{HOST}, c1@{HOST}\n" in done.stdout
    assert f"ivan@{FQDN}\tivan@{FQDN}, c1@{FQDN}\n" in done.stdout


@needs_jq
def test_sm218_a_persona_whose_box_is_missing_is_withheld_loudly_not_silently(
    generator: Path, tmp_path: Path
) -> None:
    """THE ABSENCE CASE (rca-001 B). A persona sidecar (`agent: ivan`) whose
    `agents/ivan/` is missing: no `ivan@` row and no `ivan+memory@` row (its
    self-copy would bounce 5.1.1), rc 0, and stderr NAMES the word, so the
    operator sees a persona go deaf instead of the map quietly losing it."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "c1", "agent": "ivan"}], {}, ["c1"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert _withheld_line(done.stderr) == "withheld 1 seat record(s): ivan=1"


@needs_jq
@pytest.mark.parametrize("word", ["worker", "foreman"])
def test_sm218_a_role_word_that_is_a_box_keeps_its_row(
    generator: Path, tmp_path: Path, word: str
) -> None:
    """The rule is "the seat word is a box", NOT a list of role words: a role
    word whose `agents/<word>/` exists gets its row (and its +memory identity
    row) exactly as before, and nothing is withheld."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "w1", "agent": word}, {"session_id": "w2", "agent": word}],
                   {}, ["w1", "w2", word])
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        f"{word}+memory@{HOST}\t{word}+memory@{HOST}\n"
        f"{word}+memory@{FQDN}\t{word}+memory@{FQDN}\n"
        f"{word}@{HOST}\t{word}@{HOST}, w1@{HOST}, w2@{HOST}\n"
        f"{word}@{FQDN}\t{word}@{FQDN}, w1@{FQDN}, w2@{FQDN}\n"
    )
    assert done.stderr == ""


@needs_jq
def test_sm218_a_box_is_not_a_bare_new_dir(generator: Path, tmp_path: Path) -> None:
    """`agents/<word>/` existing is not enough: the test is `<word>/new`,
    the leaf postfix delivers into."""
    (tmp_path / "root" / "agents" / "worker").mkdir(parents=True)
    done = _valias(generator, tmp_path,
                   [{"session_id": "w1", "agent": "worker"}], {}, ["w1"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert _withheld_line(done.stderr) == "withheld 1 seat record(s): worker=1"


@needs_jq
def test_sm218_the_word_is_tested_as_keyed_lowercased_and_stripped(
    generator: Path, tmp_path: Path
) -> None:
    """The box tested is the box the row's first destination names: the awk
    keys `Ivan` as `ivan`, so `agents/ivan/` admits it."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "c1", "agent": "Ivan"}], {}, ["c1", "ivan"])
    assert done.returncode == 0, done.stderr
    assert f"ivan@{HOST}\tivan@{HOST}, c1@{HOST}\n" in done.stdout
    assert done.stderr == ""


@needs_jq
@pytest.mark.parametrize("word, decoy", [
    ("../outside", "../outside"),   # a raw -d would find root/outside/new
    (".hidden", ".hidden"),         # a dot-box
    ("..", ".."),                   # agents/.. is root itself, give it a new/
    ("a/b", "a/b"),                 # a nested path
    ("a/b", "ab"),                  # ... and what the awk would key it as
    ("x..y", "x..y"),               # a .. inside the word
])
def test_sm218_a_traversal_shaped_word_is_withheld_never_tested_raw(
    generator: Path, tmp_path: Path, word: str, decoy: str
) -> None:
    """The seat word gets the sid's traversal guard BEFORE any `-d`. A decoy
    maildir sits exactly where an unguarded test would look, so a pass here
    is the guard, not an absent directory."""
    _maildir(tmp_path / "root" / "agents" / decoy)
    done = _valias(generator, tmp_path,
                   [{"session_id": "s1", "agent": word}], {}, ["s1"])
    assert done.returncode == 0, done.stderr
    assert "s1@" not in done.stdout, done.stdout
    assert _withheld_line(done.stderr) == f"withheld 1 seat record(s): {word}=1"


@needs_jq
def test_sm218_every_withheld_word_is_named_with_its_session_count(
    generator: Path, tmp_path: Path
) -> None:
    """One stderr line names EVERY withheld word with its count, sorted, so a
    persona whose box vanished shows up as a NEW word beside the role words
    (rca-001 B: print the words, not only the count). A minted persona and a
    box-less session are not in it."""
    done = _valias(generator, tmp_path,
                   [{"session_id": "w1", "agent": "worker"},
                    {"session_id": "w2", "agent": "worker"},
                    {"session_id": "f1", "agent": "foreman"},
                    {"session_id": "c1", "agent": "ivan"},
                    {"session_id": "p1", "agent": "eve"},
                    {"session_id": "w9", "agent": "worker"}],      # live, no session box
                   {}, ["w1", "w2", "f1", "c1", "p1", "eve"])
    assert done.returncode == 0, done.stderr
    assert {k.split("@", 1)[0] for k in _rows(done.stdout)} == {"eve", "eve+memory"}
    assert _withheld_line(done.stderr) == "withheld 4 seat record(s): foreman=1 ivan=1 worker=2"


@needs_jq
def test_sm218_the_written_alias_map_counts_withheld_records(
    generator: Path, tmp_path: Path
) -> None:
    """The write path (not --print-valias) puts the withheld count in its
    summary line, names the word on stderr, and writes the same rows."""
    out = tmp_path / "maps" / "vmailbox"
    out.parent.mkdir()
    done = _valias(generator, tmp_path,
                   [{"session_id": "w1", "agent": "worker"},
                    {"session_id": "p1", "agent": "eve"}], {}, ["w1", "p1", "eve"])
    printed = done.stdout
    import os as _os
    env = {"PATH": f"{tmp_path / 'bin'}:{_os.environ.get('PATH', '/usr/bin:/bin')}",
           "HOME": str(tmp_path / "home"), "TMPDIR": str(tmp_path),
           "MAGICDNS_SUFFIX": SUFFIX, "LC_ALL": "C",
           "FAKE_PS_TABLE": str(tmp_path / "ps.txt"),
           "FAKE_PS_TABLE_LOCAL": str(tmp_path / "ps-local.txt"),
           "FAKE_SESSION_CALLS": str(tmp_path / "session-calls"),
           "SESSION_SIDECAR_DIR": str(tmp_path / "claude-sessions"),
           "SESSIONMAIL_HOME": str(tmp_path / "sessionmail")}
    wrote = subprocess.run(
        [str(generator), "--valias-only", "--no-postmap", "--host", HOST,
         "--root", str(tmp_path / "root"), "--out", str(out)],
        capture_output=True, text=True, env=env, check=False)
    assert wrote.returncode == 0, wrote.stderr
    assert "1 live session(s) with a box, 0 live without one skipped, 1 withheld (seat word not a box)" \
        in wrote.stdout
    assert _withheld_line(wrote.stderr) == "withheld 1 seat record(s): worker=1"
    assert (out.parent / "valias").read_text() == printed


# ── session-settings 3.2: a SEAT's subscriptions are its map ───────────────
# `~/.session/seats/<seat>/settings/mail.json` then `mail.d/*.json`, folded
# as one layer, read per owner (call 1 (A)). An
# agent's bare name keeps its old folder until 3.8 (call 3 (b)).

def _seat_json(seat: str, data: dict, name: str = "mail.json") -> dict[str, str]:
    where = "mail.json" if name == "mail.json" else f"mail.d/{name}"
    return {f"~/.session/seats/{seat}/settings/{where}": json.dumps(data)}


SEAT_MAPS = {
    **_seat_json("voicemode+super", {"subscriptions": {
        "atomnote": True, "kith": False, "events": "yes", "a--b": True, "bad\nkey": True}}),
    **_seat_json("voicemode+super", {"subscriptions": {"kith": True}}, "90-judy.json"),
    **_seat_json("ivan", {"subscriptions": {"voicemode": True}}),   # an agent: not read here
    "seats/ivan/subscriptions/events": SEAT_NOTE,                   # an agent's old folder: read
    "seats/voicemode+super/subscriptions/voicemode": SEAT_NOTE,     # a seat's old folder: not
}
SEAT_MAP_BOXES = ["atomnote", "kith", "events", "voicemode"]
#: One live session, so the generator has a fleet to read.
A_LIVE = [{"session_id": "s1", "agent": "worker"}]


@needs_jq
def test_a_seats_map_folds_its_d_files_and_only_true_routes(generator: Path, tmp_path: Path) -> None:
    done = _valias(generator, tmp_path, A_LIVE, {}, ["s1", *SEAT_MAP_BOXES], subs=SEAT_MAPS)
    assert done.returncode == 0, done.stderr
    rows = _rows(done.stdout)
    assert rows[f"atomnote@{HOST}"] == f"atomnote@{HOST}, voicemode+super@{HOST}"
    assert rows[f"kith@{HOST}"] == f"kith@{HOST}, voicemode+super@{HOST}", "mail.d's true wins"
    assert rows[f"events@{HOST}"] == f"events@{HOST}, ivan@{HOST}", "only the agent's folder"
    assert f"voicemode@{HOST}" not in rows, "neither the agent's map nor the seat's old file"
    assert "skipped 3 subscription file(s)" in done.stderr
    assert "holds '--'" in done.stderr and "a control character" in done.stderr
    assert "never converted" in done.stderr and "pager settings migrate" in done.stderr


@needs_jq
def test_the_reader_and_the_generator_agree_on_seat_maps(generator: Path, tmp_path: Path) -> None:
    """Parity, as for the codec: `_subscription.scan` and the generator read
    the same tree into the same (subscriber, box) pairs."""
    from sessionmail import _subscription
    done = _valias(generator, tmp_path, A_LIVE, {}, ["s1", *SEAT_MAP_BOXES], subs=SEAT_MAPS)
    assert done.returncode == 0, done.stderr
    routed = set()
    for key, dests in _rows(done.stdout).items():
        box, _, host = key.partition("@")
        if host == HOST and box in SEAT_MAP_BOXES:
            routed |= {(d.strip().partition("@")[0], box) for d in dests.split(",")[1:]}
    read = _subscription.pairs(tmp_path / "sessionmail", live=set(),
                               env={"HOME": str(tmp_path / "home")})
    assert routed == read == {("voicemode+super", "atomnote"), ("voicemode+super", "kith"),
                              ("ivan", "events")}


@needs_jq
def test_a_seat_map_that_is_not_json_is_skipped_and_the_rest_still_route(
    generator: Path, tmp_path: Path
) -> None:
    subs = {"~/.session/seats/a+b/settings/mail.json": "{ not json",
            **_seat_json("c+d", {"subscriptions": {"atomnote": True}})}
    done = _valias(generator, tmp_path, A_LIVE, {}, ["s1", "atomnote"], subs=subs)
    assert done.returncode == 0, done.stderr
    assert _rows(done.stdout)[f"atomnote@{HOST}"] == f"atomnote@{HOST}, c+d@{HOST}"
    assert "a+b/settings/mail.json" in done.stderr and "cannot be read" in done.stderr
