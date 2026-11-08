"""The BOX-argument doors of the short-session-id resolver: every verb that
takes a BOX expands a short session id (the 8 hex a pane title shows) to the
one full id it starts on this host, saying so once on stderr, or refuses
with that verb's own code. Design: the task's designs/design.md, 4.6 and 5.2.

Readers (`message list`, `read`, `mark`, `verify`, `path`, `box path`,
`subscription list BOX`), `memory` (`wake`, `ls`/`tree --box`, `init`) and
the internal rule that `_maildir.resolve_box` never expands, and the box
moves (`box archive`, `unarchive`), which never expand (4.8). Subscriptions
(`add`/`remove BOX`) are pinned in test_message_subscription.py and
`pager listen BOX` in test_pager_listen.py, on their own fixtures.

Cases: U unique resolves; A ambiguous refuses, naming both; N none refuses;
F the full id behaves as today; L an exact legacy box wins. Synthetic ids
only; every box is a maildir under tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _boxes, _maildir, _seat
from sessionmail import memory
from sessionmail.message import EX_REFUSED, EX_VERIFY_USAGE, cli
from test_message_list import deliver

ONE = "c0ffee01-1111-4111-8111-111111111111"
TWO = "c0ffee01-2222-4222-8222-222222222222"
DIGITS = "31415926-1111-4111-8111-111111111111"     # an all-digit first 8
SELF = "5e1f5e1f-1111-4111-8111-111111111111"       # the caller's own session
SHORT = "c0ffee01"
NOTE = f"'{SHORT}' is session {ONE}"


@pytest.fixture(autouse=True)
def no_map(monkeypatch):
    """`resolve_box`'s last step asks the host's postfix map: no row here."""
    monkeypatch.setattr(_seat, "box_via_map", lambda name: None)


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "mail" / "stores"
    r.mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_ROOT", str(r.parent))
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SELF)
    return r


def maildir(root: Path, name: str) -> Path:
    for leaf in ("new", "cur", "tmp"):
        (root / name / leaf).mkdir(parents=True, exist_ok=True)
    return root / name


def msg(*argv: str):
    return CliRunner().invoke(cli, list(argv))


def marks() -> list[str]:
    p = _seat.marks_path(_seat.resolve(), _seat.session_id())
    return p.read_text().splitlines() if p.exists() else []


def assert_ambiguous(r, code: int) -> None:
    assert r.exit_code == code, r.output
    assert "ambiguous" in r.stderr and ONE in r.stderr and TWO in r.stderr, r.stderr
    assert "name one in full" in r.stderr


def assert_none(r, code: int) -> None:
    assert r.exit_code == code, r.output
    assert "no session box on this host starts with it" in r.stderr, r.stderr
    assert "never relayed" in r.stderr


# ── message list ─────────────────────────────────────────────────────────────

def test_list_U_a_unique_short_id_lists_the_full_box_and_says_so(root):
    deliver(root, ONE, "1923171201", subject="for one")
    r = msg("list", SHORT)
    assert r.exit_code == 0, r.output
    assert "for one" in r.stdout
    assert f"message list: {NOTE}" in r.stderr
    assert NOTE not in r.stdout                       # stderr only: the listing is the listing


def test_list_A_an_ambiguous_short_id_refuses_naming_both(root):
    maildir(root, ONE), maildir(root, TWO)
    assert_ambiguous(msg("list", SHORT), EX_REFUSED)


def test_list_N_a_short_id_that_starts_no_box_refuses(root):
    maildir(root, "c0ffee02-1111-4111-8111-111111111111")
    assert_none(msg("list", SHORT), EX_REFUSED)


def test_list_F_a_full_id_lists_as_today_with_no_note(root):
    deliver(root, ONE, "1923171201", subject="for one")
    r = msg("list", ONE)
    assert r.exit_code == 0 and "for one" in r.stdout
    assert "is session" not in r.stderr


def test_list_L_an_exact_legacy_box_wins_over_a_full_box_sharing_its_prefix(root):
    deliver(root, SHORT, "1923171201", subject="the legacy box")
    deliver(root, ONE, "1923171202", subject="the full box")
    r = msg("list", SHORT)
    assert r.exit_code == 0, r.output
    assert "the legacy box" in r.stdout and "the full box" not in r.stdout
    assert "is session" not in r.stderr


def test_list_to_filters_on_the_full_box_name(root):
    """`--to ''` is the bare box: after the door that is the full id, the
    name the mail was delivered to, so the row still shows."""
    deliver(root, ONE, "1923171201", subject="bare", to=ONE)
    r = msg("list", SHORT, "--to", "")
    assert r.exit_code == 0 and "bare" in r.stdout, r.output


# ── message read / mark: the BOX door and the all-digit rule ────────────────

def test_read_U_reads_from_the_full_box_and_marks_it(root):
    maildir(root, SELF)
    deliver(root, ONE, "1923171201", subject="read me", body="the body\n")
    r = msg("read", SHORT, "1")
    assert r.exit_code == 0, r.output
    assert "read me" in r.stdout and "the body" in r.stdout
    assert f"message read: {NOTE}" in r.stderr
    assert len(marks()) == 1 and "1923171201@example.invalid" in marks()[0]


@pytest.mark.parametrize("make, check", [
    (lambda root: (maildir(root, ONE), maildir(root, TWO)), assert_ambiguous),
    (lambda root: None, assert_none),
])
def test_read_A_and_N_refuse_and_mark_nothing(root, make, check):
    make(root)
    deliver(root, SELF, "1923171201", subject="own")       # an index 1 that IS there
    r = msg("read", SHORT, "1")
    check(r, EX_REFUSED)
    assert marks() == []
    assert "own" not in r.stdout


def test_read_an_all_digit_short_id_before_an_index_is_a_BOX(root):
    """Design 4.6: two tokens and the first is a short form, so it is the
    BOX even though it is all digits. Before the fix it was index
    31415926 in the caller's own box."""
    deliver(root, SELF, "1923171201", subject="own")
    deliver(root, DIGITS, "1923171202", subject="the digits box")
    r = msg("read", "31415926", "1", "--peek")
    assert r.exit_code == 0, r.output
    assert "the digits box" in r.stdout and "own" not in r.stdout
    assert f"'31415926' is session {DIGITS}" in r.stderr


def test_read_a_lone_all_digit_token_is_still_an_index(root):
    deliver(root, SELF, "1923171201", subject="own")
    maildir(root, DIGITS)
    r = msg("read", "31415926", "--peek")
    assert r.exit_code == EX_REFUSED                 # no message 31415926 in the own box
    assert "is session" not in r.stderr


def test_read_seven_digits_before_an_index_are_two_indexes(root):
    """Under the floor (8) nothing changes: `read 1234567 1` is two ids."""
    deliver(root, SELF, "1923171201", subject="own")
    r = msg("read", "1234567", "1", "--peek")
    assert "own" in r.stdout                         # index 1 of the own box was read
    assert "is session" not in r.stderr


def test_read_L_an_exact_all_digit_legacy_box_reads_as_itself(root):
    deliver(root, "31415926", "1923171201", subject="legacy digits")
    deliver(root, DIGITS, "1923171202", subject="full digits")
    r = msg("read", "31415926", "1", "--peek")
    assert r.exit_code == 0, r.output
    assert "legacy digits" in r.stdout and "full digits" not in r.stdout


def test_mark_U_marks_the_message_in_the_full_box(root, monkeypatch):
    """`mark` looks on disk only in the seat's own box (with a full id too),
    so the full box here is the caller's session's."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ONE)
    mid = deliver(root, ONE, "1923171201", subject="mark me")
    r = msg("mark", SHORT, "1", "seen")
    assert r.exit_code == 0, r.output
    assert f"message mark: {NOTE}" in r.stderr
    assert len(marks()) == 1 and mid in marks()[0] and " seen " in marks()[0]


def test_mark_an_all_digit_short_id_is_a_BOX(root, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", DIGITS)
    mid = deliver(root, DIGITS, "1923171201", subject="digits")
    r = msg("mark", "31415926", "1", "seen")
    assert r.exit_code == 0, r.output
    assert len(marks()) == 1 and mid in marks()[0]


@pytest.mark.parametrize("make, check", [
    (lambda root: (maildir(root, ONE), maildir(root, TWO)), assert_ambiguous),
    (lambda root: None, assert_none),
])
def test_mark_A_and_N_refuse_and_mark_nothing(root, make, check):
    make(root)
    deliver(root, SELF, "1923171201", subject="own")
    check(msg("mark", SHORT, "1", "seen"), EX_REFUSED)
    assert marks() == []


# ── message verify ───────────────────────────────────────────────────────────

def test_verify_U_finds_the_message_in_the_full_box(root):
    """An unsigned fixture: the verdict is "unsigned" (2), which is past the
    box step. Unresolved, the box would have been usage (3)."""
    deliver(root, ONE, "1923171201")
    r = msg("verify", SHORT, "1")
    assert r.exit_code != EX_VERIFY_USAGE, r.output
    assert f"message verify: {NOTE}" in r.stderr


def test_verify_A_and_N_refuse_with_the_verifiers_usage_code(root):
    assert_none(msg("verify", SHORT, "1"), EX_VERIFY_USAGE)
    maildir(root, ONE), maildir(root, TWO)
    assert_ambiguous(msg("verify", SHORT, "1"), EX_VERIFY_USAGE)


def test_verify_an_empty_BOX_is_still_the_default_box(root):
    """`verify "" N` read the caller's own box before the door existed; the
    door must not turn that into a box named ''."""
    deliver(root, SELF, "1923171201")
    r = msg("verify", "", "1")
    assert r.exit_code != EX_VERIFY_USAGE, r.output
    assert "no box ''" not in r.stderr


# ── message path / box path ──────────────────────────────────────────────────

@pytest.mark.parametrize("argv, prog", [(["path"], "message path"),
                                        (["box", "path"], "message box path")])
def test_path_U_prints_the_full_box(root, argv, prog):
    full = maildir(root, ONE)
    r = msg(*argv, SHORT)
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(full)
    assert f"{prog}: {NOTE}" in r.stderr


@pytest.mark.parametrize("argv", [["path"], ["box", "path"]])
def test_path_A_N_F_L(root, argv):
    assert_none(msg(*argv, SHORT), EX_REFUSED)
    full = maildir(root, ONE)
    r = msg(*argv, ONE)
    assert r.exit_code == 0 and r.stdout.strip() == str(full) and "is session" not in r.stderr
    legacy = maildir(root, SHORT)
    r = msg(*argv, SHORT)
    assert r.exit_code == 0 and r.stdout.strip() == str(legacy), r.output
    (legacy / "new").rmdir(), (legacy / "cur").rmdir(), (legacy / "tmp").rmdir()
    legacy.rmdir()
    maildir(root, TWO)
    assert_ambiguous(msg(*argv, SHORT), EX_REFUSED)


# ── message subscription list BOX ────────────────────────────────────────────

def test_subscription_list_BOX_U_names_the_hearers_of_the_full_box(root):
    maildir(root, ONE)
    r = msg("subscription", "list", SHORT)
    assert r.exit_code == 0, r.output
    assert f"message subscription list: {NOTE}" in r.stderr
    assert ONE in r.stdout


def test_subscription_list_BOX_A_and_N_refuse(root):
    assert_none(msg("subscription", "list", SHORT), EX_REFUSED)
    maildir(root, ONE), maildir(root, TWO)
    assert_ambiguous(msg("subscription", "list", SHORT), EX_REFUSED)


# ── memory ───────────────────────────────────────────────────────────────────

@pytest.fixture
def oracle(monkeypatch):
    """The bash oracle replaced by a recorder: what argv would it get."""
    calls: list[list[str]] = []

    def go(verb, argv, env=None):
        calls.append([verb, *argv])
        raise SystemExit(0)

    monkeypatch.setattr(memory, "_go", go)
    return calls


def mem(*argv: str):
    return CliRunner().invoke(memory.cli, list(argv))


@pytest.mark.parametrize("argv, want", [
    (["wake", SHORT], ["wake", ONE]),
    (["wake", SHORT, "--dry-run"], ["wake", ONE, "--dry-run"]),
    (["ls", "--box", SHORT], ["ls", "--box", ONE]),
    (["tree", "/ASKS", "--box", SHORT], ["tree", "/ASKS", "--box", ONE]),
])
def test_memory_U_hands_the_bash_the_full_id_and_says_so(root, oracle, argv, want):
    maildir(root, ONE)
    r = mem(*argv)
    assert r.exit_code == 0, r.output
    assert oracle == [want]
    assert f"memory {argv[0]}: {NOTE}" in r.stderr


@pytest.mark.parametrize("argv, code", [(["wake", SHORT], 1), (["ls", "--box", SHORT], 2),
                                        (["tree", "/ASKS", "--box", SHORT], 2)])
def test_memory_A_and_N_refuse_before_the_bash(root, oracle, argv, code):
    assert_none(mem(*argv), code)
    maildir(root, ONE), maildir(root, TWO)
    assert_ambiguous(mem(*argv), code)
    assert oracle == []


@pytest.mark.parametrize("argv", [["wake", ONE], ["wake", "acmecorp"], ["ls", "--box", "eve"],
                                  ["wake"]])
def test_memory_F_a_full_id_or_a_name_goes_to_the_bash_unchanged(root, oracle, argv):
    maildir(root, ONE)
    r = mem(*argv)
    assert r.exit_code == 0, r.output
    assert oracle == [argv]
    assert "is session" not in r.stderr


def test_memory_L_an_exact_legacy_box_goes_as_itself(root, oracle):
    maildir(root, SHORT), maildir(root, ONE)
    assert mem("wake", SHORT).exit_code == 0
    assert oracle == [["wake", SHORT]]


def test_memory_init_U_and_N(root, monkeypatch):
    from sessionmail import _init
    got: list[str] = []
    monkeypatch.setattr(_init, "run", lambda box, **kw: got.append(box))
    assert_none(mem("init", SHORT), 2)
    assert got == []
    maildir(root, ONE)
    r = mem("init", SHORT)
    assert r.exit_code == 0, r.output
    assert got == [ONE]
    assert f"memory init: {NOTE}" in r.stderr


# ── internal: no expansion below the door ───────────────────────────────────

def test_resolve_box_stays_exact(root):
    """Names taken from mail headers and the environment reach
    `_maildir.resolve_box`: a header's short local part must never land in
    a full-id box, so only the doors expand."""
    maildir(root, ONE)
    with pytest.raises(_maildir.NoSuchBox):
        _maildir.resolve_box(SHORT)
    assert _boxes.resolve_session(SHORT).value == ONE      # the door's resolver does


# ── box moves: exact names only (design 4.8) ─────────────────────────────────
#
# `box archive` and `box unarchive` move boxes and change the maps: a short
# form is never expanded there. One that starts full ids refuses (3), naming
# them, and nothing moves. One that starts none is the exact name it is.

def tree_of(*roots: Path) -> list[str]:
    return sorted(str(p) for r in roots if r.exists() for p in r.rglob("*"))


@pytest.mark.parametrize("both", [False, True], ids=["one", "two"])
def test_archive_a_short_id_refuses_naming_the_full_ids_and_moves_nothing(root, both):
    maildir(root, ONE)
    if both:
        maildir(root, TWO)
    before = tree_of(root.parent)
    r = msg("box", "archive", SHORT, "--apply")
    assert r.exit_code == 3, r.output
    assert "a move takes the box's full name" in r.stderr and ONE in r.stderr
    assert (TWO in r.stderr) is both
    assert "Nothing moved" in r.stderr and r.stdout == ""
    assert tree_of(root.parent) == before


@pytest.mark.parametrize("both", [False, True], ids=["one", "two"])
def test_unarchive_a_short_id_refuses_naming_the_archived_full_ids(root, both):
    archive = root.parent / "archive"
    maildir(archive, ONE)
    if both:
        maildir(archive, TWO)
    before = tree_of(root.parent)
    r = msg("box", "unarchive", SHORT, "--apply")
    assert r.exit_code == 3, r.output
    assert "a move takes the box's full name" in r.stderr and ONE in r.stderr
    assert (TWO in r.stderr) is both
    assert tree_of(root.parent) == before


def test_unarchive_a_short_id_that_starts_nothing_is_no_such_archived_box(root):
    (root.parent / "archive").mkdir()
    r = msg("box", "unarchive", SHORT)
    assert r.exit_code == 2, r.output
    assert f"no archived box '{SHORT}'" in r.stderr


def test_unarchive_L_an_exact_legacy_archived_box_goes_as_itself(root):
    archive = root.parent / "archive"
    maildir(archive, SHORT)
    maildir(archive, ONE)
    r = msg("box", "unarchive", SHORT)            # a dry run: says what it would do
    assert r.exit_code == 0, r.output
    assert "full name" not in r.stderr
    assert f"{archive / SHORT} -> {root / SHORT}" in r.stdout


def test_unarchive_F_a_full_id_goes_as_today(root):
    archive = root.parent / "archive"
    maildir(archive, ONE)
    r = msg("box", "unarchive", ONE)
    assert r.exit_code == 0, r.output
    assert f"{archive / ONE} -> {root / ONE}" in r.stdout
