"""The ONE short-session-id resolver (SM-885): `_boxes.short_form`,
`_boxes.resolve_session` and `_boxes.refusal_text`.

A pane title shows a session's first 8 hex, so a seat types that. The
resolver expands a unique prefix of a box (or session dir) on THIS host,
refuses an ambiguous one naming the candidates, and refuses one that matches
nothing. It is called at each verb's door, never inside
`_maildir.resolve_box`. Design: the task's designs/design.md, sections 3-4.

Cases, as the design names them: U unique resolves; A ambiguous refuses;
N none refuses; F a full id is untouched; L an exact legacy box wins.
Synthetic ids only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sessionmail import _boxes, _maildir, _seat

ONE = "c0ffee01-1111-4111-8111-111111111111"
TWO = "c0ffee01-2222-4222-8222-222222222222"
OTHER = "deadbeef-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def no_map(monkeypatch):
    """`resolve_box`'s last step asks the host's postfix map (a read). No
    test here reads the host: the map has no row."""
    monkeypatch.setattr(_seat, "box_via_map", lambda name: None)


def maildir(root: Path, name: str) -> Path:
    d = root / name
    for leaf in ("new", "cur", "tmp"):
        (d / leaf).mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "mail" / "stores"
    r.mkdir(parents=True)
    return r


@pytest.fixture
def env(root, tmp_path):
    return {"SESSIONMAIL_ROOT": str(root.parent), "SESSIONS_DIR": str(tmp_path / "sessions")}


# ── the shape ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token, want", [
    ("c0ffee01", "c0ffee01"),                       # 8 hex: what a pane title shows
    ("C0FFEE01", "c0ffee01"),                       # case-insensitive, lowercased
    ("c0ffee01-", "c0ffee01-"),
    ("c0ffee01-1", "c0ffee01-1"),
    ("c0ffee01-1111-41", "c0ffee01-1111-41"),
    (ONE[:35], ONE[:35]),                           # the longest strict prefix
])
def test_a_strict_prefix_of_the_uuid_shape_from_8_characters_is_a_short_form(token, want):
    assert _boxes.short_form(token) == want


@pytest.mark.parametrize("token", [
    "c0ffee0",               # 7: under the floor
    "cafe",                  # a short all-hex seat name is never captured
    "c0ffee011111",          # no dash at position 9
    "c0ffee01-111g",         # not hex
    ONE,                     # a FULL id is exact or nothing, never a short form
    ONE + "0",               # longer than the shape
    "c0ffee01+notes",        # the tag is the caller's to split off
    "c0ffee01@host",
    "",
])
def test_anything_else_is_not_a_short_form(token):
    assert _boxes.short_form(token) is None


def test_short_of_reads_the_base_of_an_address():
    assert _boxes.short_of("Gc <c0ffee01+ext@host>") == "c0ffee01"
    assert _boxes.short_of("c0ffee01+notes") == "c0ffee01"
    assert _boxes.short_of(f"Gc <{ONE}>") is None
    assert _boxes.short_of("alpha") is None


# ── source="box" ─────────────────────────────────────────────────────────────

def test_U_a_unique_prefix_resolves_to_the_full_box_and_says_so(root, env):
    maildir(root, ONE)
    maildir(root, OTHER)
    r = _boxes.resolve_session("c0ffee01", env=env)
    assert r.value == ONE and r.full == ONE
    assert r.note == (f"'c0ffee01' is session {ONE} (the only session box on this host "
                      f"that starts with it)")


def test_U_upper_case_and_a_longer_prefix_resolve_too(root, env):
    maildir(root, ONE)
    maildir(root, TWO)
    assert _boxes.resolve_session("C0FFEE01-1", env=env).full == ONE
    assert _boxes.resolve_session("c0ffee01-2222", env=env).full == TWO


@pytest.mark.parametrize("given, want", [
    ("c0ffee01+notes", f"{ONE}+notes"),
    ("Gc <c0ffee01+ext>", f"Gc <{ONE}+ext>"),
    ("c0ffee01@testhost", f"{ONE}@testhost"),
    ('"Gc, the GC" <c0ffee01+ext@testhost>', f'"Gc, the GC" <{ONE}+ext@testhost>'),
])
def test_U_the_tag_the_display_name_and_the_domain_are_kept(root, env, given, want):
    maildir(root, ONE)
    assert _boxes.resolve_session(given, env=env).value == want


def test_A_an_ambiguous_prefix_refuses_naming_every_candidate(root, env):
    maildir(root, TWO)
    maildir(root, ONE)
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("c0ffee01", env=env)
    assert e.value.why == "ambiguous" and e.value.candidates == [ONE, TWO]
    text = _boxes.refusal_text(e.value, "message list")
    assert text.startswith("message list: 'c0ffee01' is ambiguous: it starts 2 session boxes")
    assert ONE in text and TWO in text and "name one in full" in text


def test_A_the_refusal_names_ten_then_the_total(root, env):
    ids = [f"c0ffee01-{i:04x}-4000-8000-000000000000" for i in range(11)]
    for sid in ids:
        maildir(root, sid)
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("c0ffee01", env=env)
    text = _boxes.refusal_text(e.value)
    assert all(sid in text for sid in ids[:10]) and ids[10] not in text
    assert "… 11 in all" in text


def test_N_no_match_refuses_and_says_it_is_never_relayed(root, env):
    maildir(root, OTHER)
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("c0ffee01", env=env)
    assert e.value.why == "none" and e.value.candidates == []
    text = _boxes.refusal_text(e.value)
    assert "no session box on this host starts with it" in text
    assert "never relayed" in text and "use its full id" in text


def test_N_an_empty_or_missing_root_is_no_match(tmp_path):
    with pytest.raises(_boxes.Unresolved):
        _boxes.resolve_session("c0ffee01", env={"SESSIONMAIL_ROOT": str(tmp_path / "gone")})


def test_F_a_full_id_is_untouched_whether_or_not_its_box_exists(root, env):
    assert _boxes.resolve_session(ONE, env=env) == _boxes.Resolved(ONE)
    maildir(root, ONE)
    assert _boxes.resolve_session(ONE, env=env) == _boxes.Resolved(ONE)


def test_a_name_that_is_not_a_short_form_is_untouched(root, env):
    maildir(root, ONE)
    for given in ("alpha", "cafe", "c0ffee0", "store/project", "/abs/path"):
        assert _boxes.resolve_session(given, env=env) == _boxes.Resolved(given)


def test_L_an_exact_legacy_box_wins_over_a_full_box_sharing_its_prefix(root, env):
    maildir(root, "c0ffee01")
    maildir(root, ONE)
    maildir(root, TWO)
    assert _boxes.resolve_session("c0ffee01+tag", env=env) == _boxes.Resolved("c0ffee01+tag")


def test_L_an_exact_box_by_the_map_wins_too(root, env, monkeypatch, tmp_path):
    maildir(root, ONE)
    elsewhere = maildir(tmp_path / "elsewhere", "c0ffee01")
    monkeypatch.setattr(_seat, "box_via_map",
                        lambda name: (elsewhere, "via map") if name == "c0ffee01" else None)
    assert _boxes.resolve_session("c0ffee01", env=env).full == ""


def test_candidates_are_flat_full_id_maildirs_only(root, env):
    (root / ONE).mkdir()                                   # a dir, not a maildir
    (root / "c0ffee01-not-a-uuid").mkdir()
    maildir(root, "c0ffee01-not-a-uuid-either")            # a maildir, not a full id
    store = root / "acme"
    (store / ".tm-store").parent.mkdir(parents=True, exist_ok=True)
    (store / ".tm-store").write_text("")
    maildir(store, TWO)                                    # nested: never a session box
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("c0ffee01", env=env)
    assert e.value.why == "none"


# ── source="session" ─────────────────────────────────────────────────────────

def test_session_source_reads_the_session_dirs_not_the_boxes(root, env, tmp_path):
    sessions = tmp_path / "sessions"
    (sessions / ONE).mkdir(parents=True)
    maildir(root, TWO)                                     # a box, no session dir
    r = _boxes.resolve_session("c0ffee01", source="session", env=env)
    assert r.full == ONE
    assert r.note == (f"'c0ffee01' is session {ONE} (the only session on this host that "
                      f"starts with it)")


def test_session_source_ambiguous_none_full_and_exact(env, tmp_path):
    sessions = tmp_path / "sessions"
    (sessions / ONE).mkdir(parents=True)
    (sessions / TWO).mkdir()
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("c0ffee01", source="session", env=env)
    assert e.value.why == "ambiguous" and "2 sessions" in _boxes.refusal_text(e.value)
    with pytest.raises(_boxes.Unresolved) as e:
        _boxes.resolve_session("deadbeef", source="session", env=env)
    assert e.value.why == "none" and "no session on this host" in str(e.value)
    assert _boxes.resolve_session(ONE, source="session", env=env).full == ""
    (sessions / "c0ffee01").mkdir()                        # an exact dir wins
    assert _boxes.resolve_session("c0ffee01", source="session", env=env).full == ""


def test_an_unknown_source_is_a_programming_error(env):
    with pytest.raises(ValueError):
        _boxes.resolve_session("c0ffee01", source="mail", env=env)


# ── below the door: never expanded ───────────────────────────────────────────

def test_resolve_box_stays_exact(root, env):
    """Internal callers pass names from mail headers and the environment; an
    8-hex local part there must never land in a full-id box (design §3)."""
    maildir(root, ONE)
    with pytest.raises(_maildir.NoSuchBox):
        _maildir.resolve_box("c0ffee01", env)


def test_starting_lists_full_ids_under_any_dir_maildirs_or_not(tmp_path):
    """The one candidate scan, for a dir that is not the mail root (the
    archive): full ids that start the short form, sorted; with MAILDIRS
    false a plain dir counts; a missing dir has none."""
    (tmp_path / TWO).mkdir()
    for leaf in ("new", "cur"):
        (tmp_path / ONE / leaf).mkdir(parents=True)
    (tmp_path / "c0ffee01").mkdir()                   # not a full id
    (tmp_path / "c0ffee02-1111-4111-8111-111111111111").mkdir()
    assert _boxes.starting("c0ffee01", tmp_path) == [ONE]
    assert _boxes.starting("c0ffee01", tmp_path, maildirs=False) == [ONE, TWO]
    assert _boxes.starting("c0ffee01", tmp_path / "nowhere") == []
