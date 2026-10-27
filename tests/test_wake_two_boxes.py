"""Two boxes of one name resolve to the one postfix delivers to, on BOTH rails,
and wake points at the letters addressed to the seat.

A project box nests under its store (`<root>/<store>/<project>/`). A stray FLAT
directory of the same name (`<root>/<project>/`) makes a second map line with
the same key; the generator sorts the lines in C order and postmap keeps the
first, so delivery goes to whichever relative path sorts first. Both the shell
rail (`mhs/mail-box.lib.sh`, which wake uses) and the Python rail
(`_maildir.resolve_box`, which `message list`/`read` use) must apply that rule,
so wake and list/read stay on one box (specs/message, "One physical box, on
both sides").

The live shape of the stray is `new/` and `tmp/` with no `cur/`; the tests
also cover a full maildir, a store that sorts AFTER the name (flat is then the
delivered box), two stores, and no marker. Temp roots only, and an environment
built from nothing, as in test_wake_project_box_brief.py.

Every test that runs the shell rail runs it on BOTH bashes (conftest's
`rail_bash`, SM-963): `/bin/bash` (3.2 on a Mac) and the PATH bash (5.x on a
Homebrew Mac or any Linux), which is the one `session-mail-wake` runs. Bash
4.4+ reads a bare `local x` as unset under the lib's `set -u`; 3.2 does not,
so a suite pinned to `/bin/bash` was green over a rail that exited 1.
"""
from __future__ import annotations

import email.utils
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sessionmail import _boxes, _maildir, _mhs

MHS = Path(_mhs.MHS_DIR)
WAKE = MHS / "session-mail-wake"
LIB = MHS / "mail-box.lib.sh"

PROJECT = "mail"
SEAT = f"{PROJECT}+ab-12.foreman"
OTHER = f"{PROJECT}+ab-12.worker"
SID = "7ed6b16b-31bd-fe66-35a0-284b56bbe9ec"
HOST = "wake.invalid"
DATE = "Sun, 09 Jun 2030 12:00:00 +0000"
#: The letters' delivery time, derived from DATE: a clock on that day.
T0 = email.utils.parsedate_to_datetime(DATE).timestamp()


def letter(mid: str, stamp: str, subject: str) -> str:
    """A delivered letter as postfix writes it: the stamps first, then the headers."""
    return (f"Return-Path: <kick@{HOST}>\n"
            f"X-Original-To: {stamp}\n"
            f"Delivered-To: {PROJECT}@{HOST}\n"
            f"From: Kick <kick@{HOST}>\n"
            f"To: {stamp}@{HOST}\n"
            f"Date: {DATE}\n"
            f"Subject: {subject}\n"
            f"Message-ID: <{mid}>\n"
            "\n"
            "The body.\n")


def box(d: Path, cur: bool = True) -> Path:
    """A maildir at D; cur=False is the stray's live shape (new/ and tmp/ only)."""
    for sub in ("new", "tmp") + (("cur",) if cur else ()):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def store(root: Path, name: str) -> Path:
    s = root / name
    s.mkdir(parents=True, exist_ok=True)
    (s / ".tm-store").write_text("")
    return s


def drop(d: Path, name: str, text: str, mtime: float | None = None) -> Path:
    p = d / "new" / name
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def shell_resolve(bash: str, name: str, root: Path) -> subprocess.CompletedProcess:
    """The lib's CLI form on BASH, as wake runs it (`bash mail-box.lib.sh <name> <root>`)."""
    return subprocess.run([bash, str(LIB), name, str(root)], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin"})


@pytest.fixture
def env(tmp_path: Path, rail_bash: str) -> dict[str, str]:
    """An environment built from nothing: a temp root, a stub notmuch, and RAIL_BASH
    first on PATH as `bash`, so wake's `system_bash` (the first bash on PATH) runs
    the shell rail on it, and so does the verifier's `env bash` shebang."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(rail_bash)
    notmuch = bin_dir / "notmuch"
    notmuch.write_text("#!/bin/sh\nexit 0\n")
    notmuch.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "mail").mkdir()
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home),
            "SESSION_MAIL_ROOT": str(tmp_path / "mail"),
            "SESSIONMAIL_PYTHON": sys.executable}


def wake(env: dict[str, str], *argv: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WAKE), *argv], capture_output=True, text=True,
                          timeout=120, env={**env, **extra})


def section(out: str, heading: str) -> str:
    """The lines of one wake section, from its heading to its cost line."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"═══ {heading}"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("— "))
    return "\n".join(lines[start:end + 1])


# ── the two rails, one rule ─────────────────────────────────────────────────────────────

LAYOUTS = {
    # name: (build(root), the relative path postfix delivers to)
    "stray-live-shape": (lambda r: (box(r / PROJECT, cur=False),
                                    box(store(r, "kit-store") / PROJECT)), f"kit-store/{PROJECT}"),
    "stray-full-maildir": (lambda r: (box(r / PROJECT),
                                      box(store(r, "kit-store") / PROJECT)), f"kit-store/{PROJECT}"),
    "store-sorts-after": (lambda r: (box(r / PROJECT),
                                     box(store(r, "zz-store") / PROJECT)), PROJECT),
    "two-stores": (lambda r: (box(store(r, "b-store") / PROJECT),
                              box(store(r, "a-store") / PROJECT)), f"a-store/{PROJECT}"),
}


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_both_rails_resolve_two_boxes_to_the_delivered_one(tmp_path, layout, rail_bash):
    root = tmp_path / "mail"
    build, delivered = LAYOUTS[layout]
    build(root)
    want = root / delivered

    sh = shell_resolve(rail_bash, PROJECT, root)
    assert sh.returncode == 0, sh.stderr
    assert sh.stdout.strip() == str(want)
    # ONE line on stderr, naming the delivered box and the stray.
    err = [ln for ln in sh.stderr.splitlines() if ln.strip()]
    assert len(err) == 1, sh.stderr
    assert str(want) in err[0] and "Move it aside" in err[0], err[0]
    others = {str(p) for p in root.glob(f"**/{PROJECT}") if p.is_dir()} - {str(want)}
    assert others and all(o in err[0] for o in others), (others, err[0])

    path, how = _maildir.resolve_box(PROJECT, {"SESSION_MAIL_ROOT": str(root)})
    assert path == want, how
    assert "move it aside" in how and all(o in how for o in others), how


@pytest.mark.parametrize("layout", ["lone-full", "lone-partial", "no-marker"])
def test_one_box_resolves_as_before_and_says_nothing(tmp_path, layout, rail_bash):
    """No collision, no stderr: a lone flat box (full or partial) and a flat box beside
    an UNMARKED directory of the same child name resolve flat, as they always did."""
    root = tmp_path / "mail"
    box(root / PROJECT, cur=(layout != "lone-partial"))
    if layout == "no-marker":
        box(root / "kit-store" / PROJECT)          # no .tm-store: a flat box, not a store
    sh = shell_resolve(rail_bash, PROJECT, root)
    assert sh.returncode == 0, sh.stderr
    assert sh.stdout.strip() == str(root / PROJECT)
    assert sh.stderr.strip() == ""
    assert _maildir.two_boxes(PROJECT, root) is None


def test_a_nested_box_beside_a_partial_stray_wins_even_without_the_collision_rule(tmp_path,
                                                                                     rail_bash):
    """Step 1's maildir predicate (new/ AND cur/, the Python rail's `_is_maildir`)
    on its own: a partial flat directory no longer wins step 1."""
    root = tmp_path / "mail"
    box(root / PROJECT, cur=False)
    nested = box(store(root, "kit-store") / PROJECT)
    assert shell_resolve(rail_bash, PROJECT, root).stdout.strip() == str(nested)


def test_the_python_rail_marker_matches_the_boxes_module():
    assert _maildir.STORE_MARKER == _boxes.STORE_MARKER


def test_a_store_made_after_a_resolution_is_seen(tmp_path):
    """The store list is cached on the root's mtime; making a store moves it."""
    root = tmp_path / "mail"
    flat = box(root / PROJECT)
    assert _maildir.resolve_box(PROJECT, {"SESSION_MAIL_ROOT": str(root)})[0] == flat
    nested = box(store(root, "kit-store") / PROJECT)
    assert _maildir.resolve_box(PROJECT, {"SESSION_MAIL_ROOT": str(root)})[0] == nested


# ── the CLI under its own `set -u`, on both bashes (SM-963) ───────────────────────────

SHORT_SID = SID[:8]
SID_TWIN = SID[:9] + "0000" + SID[13:]          # shares the 8-hex prefix: ambiguous


def cli_tree(root: Path) -> None:
    """One root that reaches every resolution step of `mail_box_resolve`."""
    box(root / "flat")
    box(store(root, "kit-store") / "nested")
    box(root / PROJECT)
    box(root / "kit-store" / PROJECT)                # two boxes: flat + store
    box(store(root, "a-store") / "pair")
    box(store(root, "b-store") / "pair")             # two boxes: two stores
    box(root / SID)
    box(root / "Legacy")                             # a legacy mixed-case box
    box(root / "abcd1234")                           # a HISTORY short box
    box(root / "plainbox" / "orphan")                # unmarked: a near-miss


CLI_CASES = {
    # name: (argv name, rc)
    "empty-name": ("", 2),
    "flat": ("flat", 0),
    "nested": ("nested", 0),
    "two-boxes-flat-and-store": (PROJECT, 0),
    "two-boxes-two-stores": ("pair", 0),
    "full-uuid": (SID, 0),
    "short-id-unique": (SHORT_SID, 0),
    "short-id-no-match": ("deadbeef", 2),
    "legacy-mixed-case": ("legacy", 0),
    "upper-name": ("FLAT", 0),
    "plus-ext-base-exists": ("flat+boot", 2),
    "plus-ext-no-base": ("nobox+boot", 2),
    "uuid-miss-history-box": ("abcd1234-0000-0000-0000-000000000000", 2),
    "unmarked-near-miss": ("orphan", 2),
    "missing": ("nobox", 2),
}


@pytest.mark.parametrize("case", sorted(CLI_CASES))
def test_every_resolution_path_answers_under_set_u(tmp_path, case, rail_bash):
    """Each step of the rail ends in its own answer (rc 0 or the rc 2 refusal), never
    in bash aborting on an unset variable (rc 1): 4.4+ is strict where 3.2 is not."""
    root = tmp_path / "mail"
    cli_tree(root)
    name, rc = CLI_CASES[case]
    sh = shell_resolve(rail_bash, name, root)
    assert "unbound variable" not in sh.stderr, sh.stderr
    assert sh.returncode == rc, sh.stderr


def test_an_ambiguous_short_id_is_refused_not_aborted(tmp_path, rail_bash):
    root = tmp_path / "mail"
    box(root / SID)
    box(root / SID_TWIN)
    sh = shell_resolve(rail_bash, SHORT_SID, root)
    assert "unbound variable" not in sh.stderr, sh.stderr
    assert sh.returncode == 2 and "ambiguous box" in sh.stderr, sh.stderr


def test_no_root_and_no_home_is_the_clis_refusal_not_a_bash_abort(rail_bash):
    """With no root argument, no SESSION_MAIL_ROOT and no HOME, the default root
    cannot be built. That is a refusal (rc 2, saying what to pass), never bash's
    "HOME: unbound variable" (rc 1) out of the CLI's own `set -u`."""
    r = subprocess.run([rail_bash, str(LIB), PROJECT], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"})
    assert "unbound variable" not in r.stderr, r.stderr
    assert r.returncode == 2, r.stderr
    assert "no root" in r.stderr and "SESSION_MAIL_ROOT" in r.stderr, r.stderr
    assert "SESSIONMAIL_ROOT" in r.stderr, r.stderr


@pytest.mark.parametrize("var", ["SESSIONMAIL_ROOT", "SESSION_MAIL_ROOT"])
def test_a_root_variable_needs_no_home(tmp_path, rail_bash, var):
    """An absolute root from either spelling resolves with HOME unset: the guard
    sits inside mail_box_env_root, on the one branch that needs HOME."""
    flat = box(tmp_path / "flat")
    r = subprocess.run([rail_bash, str(LIB), "flat"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", var: str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(flat)


@pytest.mark.parametrize("given", [{}, {"SESSIONMAIL_ROOT": "~/m/agents"},
                                   {"SESSIONMAIL_ROOT": "~"}])
def test_env_root_without_home_is_the_clis_refusal(rail_bash, given):
    """`--env-root`, and a tilde root, need HOME. With none, the CLI refuses (rc 2),
    never bash's "HOME: unbound variable" (rc 1) and never a root built on ''."""
    for argv in (["--env-root"], [PROJECT]):
        r = subprocess.run([rail_bash, str(LIB), *argv], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", **given})
        assert "unbound variable" not in r.stderr, (argv, r.stderr)
        assert r.returncode == 2 and "no root" in r.stderr, (argv, r.stderr)
        assert r.stdout == "", (argv, r.stdout)


def test_the_default_root_is_under_home(tmp_path, rail_bash):
    flat = box(tmp_path / ".mail" / "agents" / "flat")
    r = subprocess.run([rail_bash, str(LIB), "flat"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(flat)


# ── wake: the pointer section ───────────────────────────────────────────────────────────

def test_wake_on_the_live_shape_points_at_the_brief_and_names_the_stray(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    stray = box(root / PROJECT, cur=False)
    for i in range(3):
        drop(stray, f"old.{i}", letter(f"old.{i}@{HOST}", f"{PROJECT}+super", f"old letter {i}"))
    nested = box(store(root, "kit-store") / PROJECT)
    drop(nested, "brief.1", letter(f"brief.1@{HOST}", SEAT, "ab-12: the brief"))
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    assert r.returncode == 0, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert "ab-12: the brief" in got, r.stdout
    assert f"message read {PROJECT} brief.1@{HOST} --peek" in got, got
    assert "1 shown of 1" in got, got
    assert str(stray) in r.stderr and str(nested) in r.stderr, r.stderr
    assert f"1 message(s) addressed exactly to {SEAT}" in r.stdout, r.stdout


def test_the_pointer_section_is_newest_first_and_only_this_seats(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    base = box(store(root, "kit-store") / PROJECT)
    t = T0
    drop(base, "a", letter(f"older@{HOST}", SEAT, "the older brief"), mtime=t)
    drop(base, "b", letter(f"newer@{HOST}", SEAT, "the newer brief"), mtime=t + 600)
    drop(base, "c", letter(f"other@{HOST}", OTHER, "a letter to another seat"), mtime=t + 900)
    drop(base, "d", letter(f"shelf@{HOST}", f"{SEAT}.boot", "the boot shelf"), mtime=t + 1200)
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    # The shelf letter is unsigned, so BOOT refuses it and the wake exits 3, after
    # every section has printed. The shelf is BOOT's, never a pointer here.
    assert r.returncode == 3 and "BOOT REFUSED" in r.stderr, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert got.index("the newer brief") < got.index("the older brief"), got
    assert "another seat" not in got and "boot shelf" not in got, got
    assert "2 shown of 2" in got, got


def test_the_pointer_section_says_none_when_nothing_is_addressed(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    base = box(store(root, "kit-store") / PROJECT)
    drop(base, "c", letter(f"other@{HOST}", OTHER, "a letter to another seat"))
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    assert r.returncode == 0, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert "(none)" in got and "0 shown" in got, got


def test_the_pointer_section_caps_and_says_how_many_more(env):
    root = Path(env["SESSION_MAIL_ROOT"])
    base = box(store(root, "kit-store") / PROJECT)
    for i in range(12):
        drop(base, f"m{i:02d}", letter(f"m{i}@{HOST}", SEAT, f"brief {i}"), mtime=T0 + i)
    r = wake(env, TM_AGENT_ADDRESS=SEAT)
    got = section(r.stdout, "ADDRESSED")
    assert "brief 11" in got and "brief 0\n" not in got + "\n", got
    assert f"…and 2 more: message list {PROJECT}" in r.stdout, r.stdout
    assert "10 shown of 12" in got, got


def test_a_session_box_wake_shows_a_copy_stamped_with_the_seat_address(env):
    """A copy delivered to the seat's SESSION box by its alias row keeps the stamp it
    was sent to (the seat's address). A wake of the session-id box must match the
    address too, or it shows nothing of its own copies."""
    root = Path(env["SESSION_MAIL_ROOT"])
    sbox = box(root / SID)
    drop(sbox, "copy.1", letter(f"copy.1@{HOST}", SEAT, "the copy in my session box"))
    r = wake(env, SID, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID)
    assert r.returncode == 0, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert "the copy in my session box" in got, r.stdout
    assert f"message read {SID} copy.1@{HOST} --peek" in got, got


def test_a_wake_of_another_box_does_not_list_the_callers_own_letters(env):
    """`memory wake <other>` from this seat lists the letters addressed to <other>
    only: the caller's own address must not leak in under <other>'s heading."""
    root = Path(env["SESSION_MAIL_ROOT"])
    base = box(store(root, "kit-store") / PROJECT)
    other = f"{PROJECT}+super"
    drop(base, "mine.1", letter(f"mine.1@{HOST}", SEAT, "a letter to the caller"))
    drop(base, "theirs.1", letter(f"theirs.1@{HOST}", other, "a letter to the other box"))
    r = wake(env, other, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID)
    assert r.returncode == 0, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert "a letter to the other box" in got, got
    assert "a letter to the caller" not in got, got


def test_a_seat_whose_mail_is_copied_to_both_boxes_sees_it_once(env):
    """No regression for a seat whose alias row is live: the letter rests in the base
    box AND the session box; the plus seat's wake reads the base box and shows it once."""
    root = Path(env["SESSION_MAIL_ROOT"])
    base = box(store(root, "kit-store") / PROJECT)
    sbox = box(root / SID)
    text = letter(f"both.1@{HOST}", SEAT, "copied to both boxes")
    drop(base, "both.1", text)
    drop(sbox, "both.1", text)
    r = wake(env, TM_AGENT_ADDRESS=SEAT, CLAUDE_CODE_SESSION_ID=SID)
    assert r.returncode == 0, r.stderr
    got = section(r.stdout, "ADDRESSED")
    assert got.count("copied to both boxes") == 1, got
    assert f"1 message(s) addressed exactly to {SEAT}" in r.stdout, r.stdout
