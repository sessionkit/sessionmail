"""One `message setup` run names every step left (SM-899).

Before the fix, the notmuch chain was ONE step resolved while the plan was
built, so each run showed only its first missing link, and a fresh host with
no notmuch took three runs (a Linux walk's own output): no binary; then
"run `notmuch --config=... new` once"; then, after a hand `new`, the links.
A host with the binary but no profile also took three: the hook step was
read before the profile step wrote the profile.

Now (rca-001's design call: BOTH):

- `notmuch-index` is a step of its own. It runs `notmuch --config=<profile>
  new --no-hooks` ONCE, and only on an EMPTY index of sessionmail's own
  profile (guard i: notmuch reports no hook dir, or the profile is written
  this run; guard ii: no `.notmuch` at the `database.path` notmuch reports).
- With no binary it is BLOCKED and still names that `new`, and the ONE
  `notmuch-hook` step says it waits on it: one run names every link.
- The run ends with ONE numbered next-steps block, then the re-run line.

Everything runs on the setup suite's own fixtures (`env`, a scratch HOME
and mail root, the FAKE notmuch at `SESSIONMAIL_NOTMUCH`). A scratch HOME
alone would not isolate the real host (SM-923); the fixtures set every
override the code reads. No test here runs a real `notmuch`. "No binary" is
`SESSIONMAIL_NOTMUCH` pointed at a path that is not there; "no database" is
the fake's `FAKE_HOOK_DIR` empty, as notmuch answers before its first `new`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from test_message_setup import env, hooks, mailroot, setup, snapshot, state, detail  # noqa: F401
from sessionmail import _index, _setup

#: The wording hold on notmuch, pending a ruling: a line that names it states
#: what was measured, never calls it required or optional, never says to install
#: it, never names Homebrew.
HOLD = re.compile(r"install notmuch|required|optional|homebrew", re.IGNORECASE)


def _profile(env) -> Path:
    return _index.profile_config(env=env)


def _no_profile(tmp_path: Path) -> None:
    """The fixture writes the host's profile; a fresh host has none."""
    (tmp_path / ".config" / "notmuch" / "sessionkit" / "config").unlink()


def _no_binary(env, tmp_path: Path) -> dict[str, str]:
    return {**env, "SESSIONMAIL_NOTMUCH": str(tmp_path / "no-notmuch-here")}


def _empty_index(env) -> dict[str, str]:
    """notmuch reports no hook dir: it has opened no database yet."""
    return {**env, "FAKE_HOOK_DIR": ""}


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "notmuch.log"
    return log.read_text().splitlines() if log.exists() else []


def _news(tmp_path: Path) -> list[str]:
    return [c for c in _calls(tmp_path) if c.split()[1:] == list(_setup.NEW_ARGS)]


def _block(out: str) -> list[str]:
    """The next-steps block: its header, numbered lines and last line."""
    lines = out.splitlines()
    heads = [i for i, line in enumerate(lines) if "step(s) left for you:" in line]
    assert len(heads) == 1, out                      # printed ONCE
    return lines[heads[0]:]


def _new_line(env) -> str:
    return f"{env['SESSIONMAIL_NOTMUCH']} --config={_profile(env)} new --no-hooks"


# ── one run names every step left ─────────────────────────────────────────────

def test_a_fresh_host_with_no_binary_takes_two_runs_not_three(env, tmp_path, mailroot):
    """Run 1 has no binary: it writes the profile, and names the binary,
    THEN the `new`, THEN the hook. Run 2, once notmuch is found, finishes."""
    _no_profile(tmp_path)
    profile = _profile(env)
    gone = _no_binary(env, tmp_path)

    rc1, out1, _ = setup(gone)
    assert rc1 == _setup.EX_REMAINS, out1
    assert profile.is_file()                                  # the profile is written
    assert state(out1, "notmuch-index") == "BLOCKED"
    assert f"{gone['SESSIONMAIL_NOTMUCH']} --config={profile} new --no-hooks" in out1
    assert state(out1, "notmuch-hook") == "BLOCKED"
    assert detail(out1, "notmuch-hook") == "waits on notmuch-index"
    block = _block(out1)
    assert block[1].startswith("  1. notmuch-index (BLOCKED): notmuch: ")
    assert block[2] == "  2. notmuch-hook (BLOCKED): waits on notmuch-index"
    assert block[-1] == "then run `message setup` again."

    rc2, out2, _ = setup(_empty_index(env))                   # notmuch is found now
    assert rc2 == _setup.EX_OK, out2
    assert _news(tmp_path) == [f"--config={profile} new --no-hooks"]
    for name in _setup.HOOK_NAMES:
        assert os.readlink(mailroot / ".notmuch" / "hooks" / name) == str(_setup.PACKAGED_HOOK)
    assert out2.rstrip().endswith("every step DONE")


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
def test_no_binary_the_plan_also_names_notmuch_new(env, tmp_path, dry_run):
    argv = ["--dry-run"] if dry_run else []
    gone = _no_binary(env, tmp_path)
    rc, out, _ = setup(gone, *argv)
    assert rc == _setup.EX_REMAINS, out
    assert f"--config={_profile(env)} new" in out
    assert (f"not found ($SESSIONMAIL_NOTMUCH, else PATH), so the index and hook steps are "
            f"left. Once it is found, setup runs `{gone['SESSIONMAIL_NOTMUCH']} "
            f"--config={_profile(env)} new --no-hooks` once and links the hooks") in out
    ids = [line.split()[1] for line in _block(out)[1:-1]]
    assert ids == ["notmuch-index", "notmuch-hook"], out
    if dry_run:
        assert out.splitlines()[-1].startswith("run `message setup` to apply the ")
    assert _calls(tmp_path) == []                             # no notmuch was there to ask


def test_binary_no_profile_one_run_finishes(env, tmp_path, mailroot):
    """(a') The binary is there, the profile is not. Before: the run that
    wrote the profile said "no notmuch profile" and named no `new`, and the
    host took three runs. Now that one run writes the profile, builds the
    index and links both hooks: exit 0 (was 4, the old pin's, by design)."""
    _no_profile(tmp_path)
    rc, out, _ = setup(_empty_index(env))
    assert rc == _setup.EX_OK, out
    assert state(out, "notmuch-profile") == "TODO"
    assert state(out, "notmuch-index") == "TODO"
    assert detail(out, "notmuch-index") == (
        f"run `{_new_line(env)}` once, after the profile is written: the index is empty")
    assert state(out, "notmuch-hook") == "TODO"
    assert "no notmuch profile for this host" not in out
    assert _news(tmp_path) == [f"--config={_profile(env)} new --no-hooks"]
    for name in _setup.HOOK_NAMES:
        assert os.readlink(mailroot / ".notmuch" / "hooks" / name) == str(_setup.PACKAGED_HOOK)


def test_empty_index_one_run_links_the_hooks(env, tmp_path, mailroot):
    """(b) Binary and profile, no database: setup builds it once, then links."""
    rc, out, _ = setup(_empty_index(env))
    assert _news(tmp_path) == [f"--config={_profile(env)} new --no-hooks"], out
    assert rc == _setup.EX_OK, out
    assert detail(out, "notmuch-index") == (
        f"run `{_new_line(env)}` once: no index is at {mailroot / '.notmuch'} yet")
    assert all((mailroot / ".notmuch" / "hooks" / n).is_symlink() for n in _setup.HOOK_NAMES)
    rc, out, _ = setup(_empty_index(env))                     # a second run: all DONE
    assert rc == _setup.EX_OK, out
    assert state(out, "notmuch-index") == "DONE"
    assert len(_news(tmp_path)) == 1, "a second run ran `notmuch new` again"


# ── the guards: only an EMPTY index, only sessionmail's own profile ─────────

def test_an_existing_database_is_skipped(env, tmp_path, mailroot):
    """Guard (ii): notmuch reports no hook dir (an old notmuch has no such
    key), but a `.notmuch` IS at its `database.path`: setup runs no `new`."""
    database = mailroot / ".notmuch"
    (database / "xapian").mkdir(parents=True)
    (database / "xapian" / "iamglass").write_text("x")
    before = snapshot(database)
    rc, out, _ = setup({**_empty_index(env), "FAKE_OLD_NOTMUCH": "1"})
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "notmuch-index") == "BLOCKED"
    assert detail(out, "notmuch-index").startswith(f"a notmuch database is at {database}, ")
    assert "setup runs no `notmuch new` over an existing database" in out
    assert detail(out, "notmuch-hook") == "waits on notmuch-index"
    assert _news(tmp_path) == []
    assert snapshot(database) == before
    assert not (database / "hooks").exists()


def test_the_apply_asks_again_and_skips_a_database_that_appeared(env, tmp_path, mailroot):
    """The guard is asked at apply time too: a database that is there by
    then is `Left` (BLOCKED), and no `new` runs."""
    (mailroot / ".notmuch").mkdir(parents=True)
    with pytest.raises(_setup.Left, match="over an existing database"):
        _setup.build_index(_empty_index(env))
    assert _news(tmp_path) == []


def test_the_empty_index_is_built_where_the_profile_says(env, tmp_path, mailroot):
    """Guard (ii) in the other direction: no `.notmuch` at database.path, so
    `new` runs, through `--config=<profile>`, and the index is made there."""
    assert not (mailroot / ".notmuch").exists()
    line = _setup.build_index(_empty_index(env))
    assert line.startswith("ran `") and line.endswith(f"the index is at {mailroot / '.notmuch'}")
    assert (mailroot / ".notmuch").is_dir()
    assert _news(tmp_path) == [f"--config={_profile(env)} new --no-hooks"]


def test_a_strangers_notmuch_config_is_never_read(env, tmp_path, mailroot):
    """A stranger's `~/.notmuch-config`, exported as NOTMUCH_CONFIG, names
    their own mail: every call carries `--config=<sessionmail's profile>`,
    and their file and their mail are left exactly as they were."""
    _no_profile(tmp_path)
    theirs = tmp_path / "their-mail"
    theirs.mkdir()
    default = Path(env["HOME"]) / ".notmuch-config"
    default.write_text(f"[database]\npath={theirs}\n")
    stranger = {**_empty_index(env), "NOTMUCH_CONFIG": str(default),
                "NOTMUCH_DATABASE": str(theirs), "NOTMUCH_PROFILE": "theirs"}
    rc, out, _ = setup(stranger)
    assert rc == _setup.EX_OK, out
    calls = _calls(tmp_path)
    assert calls and all(c.startswith(f"--config={_profile(env)} ") for c in calls), calls
    assert default.read_text() == f"[database]\npath={theirs}\n"
    assert os.listdir(theirs) == []
    assert (mailroot / ".notmuch").is_dir()


def test_a_failed_new_stops_the_run(env, tmp_path, mailroot):
    """`notmuch new` exits non-zero: a real failure, exit 1. The run stops
    there, says where, and no hook is linked. No next-steps block."""
    rc, out, _ = setup({**_empty_index(env), "FAKE_NEW_EXIT": "1"})
    assert rc == _setup.EX_FAILED, out
    failed = [line.split() for line in out.splitlines() if " FAILED: " in line]
    assert len(failed) == 1 and failed[0][0] == "notmuch-index", out
    assert f"FAILED: `{_new_line(env)}` exited 1: fake notmuch: new failed" in out
    assert "stopped at notmuch-index" in out
    assert "left for you" not in out
    assert not (mailroot / ".notmuch").exists()


def test_the_dry_run_shows_the_new_and_runs_none(env, tmp_path, mailroot):
    before = snapshot(tmp_path)
    rc, out, _ = setup(_empty_index(env), "--dry-run")
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "notmuch-index") == "TODO"
    assert f"run `{_new_line(env)}` once" in detail(out, "notmuch-index")
    assert state(out, "notmuch-hook") == "TODO"
    assert _news(tmp_path) == []
    assert snapshot(tmp_path) == {**before, "notmuch.log": snapshot(tmp_path)["notmuch.log"]}
    # no step is the human's, so no block: only the line that applies the TODOs
    assert "left for you" not in out
    todo = sum(1 for line in out.split("\nsteps:\n", 1)[1].splitlines()
               if line.startswith("  ") and line.split()[1] == "TODO")
    assert out.splitlines()[-1] == f"run `message setup` to apply the {todo} TODO step(s)."


# ── the next-steps block, and every box it did not make ────────────────────────

def test_a_usable_account_name_makes_the_operator_box(env, mailroot):
    """(c) A stranger's host whose account has a usable name: the operator's
    box is planned and made in the one run (the walk's `box:admin`)."""
    from conftest import ACCOUNT_DEFAULT
    rc, out, _ = setup(env)
    assert rc == _setup.EX_OK, out
    assert state(out, f"box:{ACCOUNT_DEFAULT}") == "TODO"
    assert (mailroot / "agents" / ACCOUNT_DEFAULT / "new").is_dir()
    assert "no operator box" not in out


def test_the_block_names_every_step_left_once_in_plan_order(env, tmp_path, monkeypatch):
    """(c) "no operator box" is not reproduced (rca-001): the line is
    setup's own, and it is now a numbered line in the one block, with why,
    beside the notmuch chain."""
    from sessionmail import _operator
    monkeypatch.setattr(_operator, "account_name", lambda: None)
    rc, out, _ = setup(_no_binary(env, tmp_path))
    assert rc == _setup.EX_REMAINS, out
    assert state(out, "box:operator") == "MANUAL"
    block = _block(out)
    assert block[0] == "message setup: done what it can; 3 step(s) left for you:"
    assert block[1].startswith("  1. box:operator (MANUAL): no operator box: ")
    assert "pass --box NAME" in block[1]
    assert block[2].startswith("  2. notmuch-index (BLOCKED): notmuch: ")
    assert block[3] == "  3. notmuch-hook (BLOCKED): waits on notmuch-index"
    assert block[4:] == ["then run `message setup` again."]


def test_the_block_line_is_the_rows_detail(env, tmp_path):
    rc, out, _ = setup(_no_binary(env, tmp_path))
    for line in _block(out)[1:-1]:
        _n, step, rest = line.split(None, 2)
        st, text = rest.split(" ", 1)
        assert st == f"({state(out, step)}):" and text == detail(out, step), line


def test_a_blocked_apply_lands_in_the_block_with_its_measured_line(env, tmp_path, mailroot):
    """A step its apply leaves (`Left`) shows the apply's line in the block,
    not the plan's TODO text."""
    import io
    steps, notes = _setup.plan(_empty_index(env), [])
    (mailroot / ".notmuch").mkdir(parents=True)          # appears after the plan was built
    out = io.StringIO()
    rc = _setup.run_plan(steps, _empty_index(env), out, dry_run=False, tty=False, notes=notes)
    text = out.getvalue()
    assert rc == _setup.EX_REMAINS, text
    block = _block(text)
    assert any(line.startswith("  1. notmuch-index (BLOCKED): a notmuch database is at ")
               for line in block), text
    assert "  2. notmuch-hook (BLOCKED): waits on notmuch-index" in block


# ── the HOLD on notmuch wording ─────────────────────────────────────────────

@pytest.mark.parametrize("case", ["no-binary", "no-binary-dry", "fresh", "existing-db",
                                  "no-profile"])
def test_setup_output_keeps_the_hold(env, tmp_path, mailroot, case):
    if case == "no-binary":
        run = setup(_no_binary(env, tmp_path))
    elif case == "no-binary-dry":
        run = setup(_no_binary(env, tmp_path), "--dry-run")
    elif case == "fresh":
        run = setup(_empty_index(env))
    elif case == "existing-db":
        (mailroot / ".notmuch").mkdir(parents=True)
        run = setup({**_empty_index(env), "FAKE_OLD_NOTMUCH": "1"})
    else:
        _no_profile(tmp_path)
        run = setup(_no_binary(env, tmp_path))
    _rc, out, err = run
    assert not HOLD.search(out + err), HOLD.search(out + err)


def test_the_touched_docs_keep_the_hold():
    """The setup page and the README's setup lines, as this fix leaves them."""
    root = Path(__file__).resolve().parent.parent
    page = (root / "docs" / "message-setup.md").read_text()
    assert not HOLD.search(page), HOLD.search(page)
    readme = (root / "README.md").read_text()
    setup_part = readme.split("**Then make the host ready**", 1)[1].split("\n\n`message`", 1)[0]
    assert not HOLD.search(setup_part), setup_part
    assert "--no-hooks" in page and "notmuch new" in setup_part
