"""SM-538 do-003: the subject shortener, PURE (`sessionmail._shorten`).

The contract is openspec/changes/subject-shorten (requirements "With the
shortener on, an over-limit or non-ASCII subject is shortened and sent" and
"A subject that folding would gut is still refused") and SM-538's design.md
section 3. No environment, file or clock is read here: the door (do-005)
decides whether the shortener runs at all; this module only decides WHAT the
subject becomes, or that it declines.
"""

from __future__ import annotations

import random

import pytest

from sessionmail import _send, _shorten
from sessionmail._shorten import Fit, shorten

LIMIT = 69


def fits(fit: Fit, limit: int = LIMIT) -> bool:
    """Counted as the sender counts: a trailing ' EOM' is not (eom-not-counted)."""
    return _send.counted(fit.subject) <= limit and fit.subject.isascii()


# ── the entry condition: only a subject the rule would refuse is touched ──────

@pytest.mark.parametrize("subject", [
    "Build green",
    "two  spaces and a\ttab, within the limit",
    "x" * LIMIT,
    "",
])
def test_a_fitting_ascii_subject_comes_back_untouched(subject):
    fit = shorten(subject, LIMIT)
    assert fit.subject == subject and not fit.changed and not fit.declined
    assert fit.report() == ""


# ── fold (3.1) ────────────────────────────────────────────────────────────────

def test_an_em_dash_and_smart_quotes_fold_instead_of_refusing():
    fit = shorten("SM-538 — “ready”", LIMIT)
    assert fit.subject == 'SM-538 - "ready"' and fit.changed and not fit.declined
    assert ("—", "-") in fit.folded and ("“", '"') in fit.folded


@pytest.mark.parametrize("given, sent", [
    ("‘a’ – b ‒ c − d", "'a' - b - c - d"),
    ("wait…", "wait..."),
    ("a → b ⇒ c ← d", "a -> b => c <- d"),
    ("x ≤ 1, y ≥ 2, z ≠ 3", "x <= 1, y >= 2, z != 3"),
    ("3 × 4 • a · b", "3 x 4 * a . b"),
    ("non breaking thin", "non breaking thin"),
    ("SM-538 réglé ﬁx", "SM-538 regle fix"),
])
def test_the_fold_table_then_nfkd(given, sent):
    fit = shorten(given, LIMIT)
    assert fit.subject == sent and fit.subject.isascii()


def test_a_folded_only_subject_keeps_its_length_and_says_folded():
    fit = shorten("SM-538 — design ready", LIMIT)
    assert fit.report() == ('message send: folded 21->21: "SM-538 - design ready" '
                            '(folded "—"->"-"); original in X-Original-Subject')


def test_a_fold_that_lengthens_past_the_limit_is_then_shortened():
    """`…` -> `...` adds two: a 68-char subject comes out of the fold at 70."""
    subject = "Status of the build " + "x" * 44 + " ok…"   # 68 chars
    assert len(subject) == 68
    fit = shorten(subject, LIMIT)
    assert fits(fit) and fit.changed and not fit.declined
    # do-003 review (9262a513): it was CUT, so the report must not call it "folded"
    assert fit.report().startswith("message send: shortened 68->")


def test_a_cut_is_reported_as_shortened_even_when_the_original_fitted():
    """do-003 review (9262a513): a fitting non-ASCII subject whose fold runs
    past the limit and is then cut said `folded 68->37` - a cut that reads as
    a fold is the misstatement design 3.5 forbids."""
    subject = "a" * 30 + " … " + "b" * 35                     # 68 chars, fits until folded
    fit = shorten(subject, LIMIT)
    assert not fit.over and fit.cut
    assert fit.report().startswith(f"message send: shortened 68->{len(fit.subject)}:")


# ── the gutted-subject guard (3.1 step 4) ─────────────────────────────────────

@pytest.mark.parametrize("subject, why", [
    ("修复完成", "folding would drop 4 of 4 letters"),
    ("🎉🎉", "nothing alphanumeric"),
    ("— 🎉 —", "nothing alphanumeric"),
    ("修复 EOM", "folding would drop 2 of 2 letters"),
])
def test_a_gutted_subject_declines_and_is_never_sent_empty_or_dashed(subject, why):
    fit = shorten(subject, LIMIT)
    assert fit.declined and why in fit.declined
    assert fit.subject == subject and not fit.changed, "declined means sent as typed: the door refuses"
    assert fit.report() == ""


def test_the_eom_marker_is_held_aside_before_the_guard_so_a_bare_eom_never_goes():
    """Review round-2 note: counted whole, `修复 EOM` keeps 3 of 5 letters and
    would go as `EOM` - a no-body mail whose whole subject is the marker."""
    fit = shorten("修复 EOM", LIMIT)
    assert fit.declined and fit.subject != "EOM"


@pytest.mark.parametrize("subject, sent", [
    ("修复 SM-538", "SM-538"),          # 2 of 4 letters dropped: exactly half, not more
    ("🎉 done", "done"),                 # emoji are not letters
    ("🎉 538", "538"),                   # digits count as alphanumeric
])
def test_half_the_letters_surviving_still_sends(subject, sent):
    fit = shorten(subject, LIMIT)
    assert not fit.declined and fit.subject == sent
    assert fit.unfoldable                                    # the report names what went


def test_the_report_names_a_character_with_no_ascii_form():
    fit = shorten("🎉 done", LIMIT)
    assert 'dropped "🎉" (no ASCII form)' in fit.report()


def test_a_subject_that_was_only_ever_punctuation_is_folded_not_declined():
    """The guard is about what DROPPING removed; nothing is dropped here."""
    fit = shorten("— —", LIMIT)
    assert fit.subject == "- -" and not fit.declined


# ── collapse, filler drop, trim (3.2 - 3.5) ───────────────────────────────────

def test_the_70_to_72_band_fits_by_dropping_one_filler_from_the_end():
    subject = "SM-538 design ready: the shortener sits at one call site for the doors"
    assert len(subject) == 70
    fit = shorten(subject, LIMIT)
    assert fit.subject == "SM-538 design ready: the shortener sits at one call site for doors"
    assert fit.dropped == ("the",) and fit.cut == 0 and not fit.hard_cut
    assert "..." not in fit.subject, "nothing was cut, so no ellipsis"
    assert fit.report() == ('message send: shortened 70->66: "SM-538 design ready: the shortener '
                            'sits at one call site for doors" (dropped "the"); original in '
                            'X-Original-Subject')


@pytest.mark.parametrize("n", [70, 71, 72])
def test_the_whole_70_to_72_band_fits_without_a_cut_when_fillers_allow(n):
    head = "Deploy finished and the service is really very healthy now"
    subject = (head + " " + "z" * 40)[:n]
    if subject.endswith(" "):
        subject = subject[:-1] + "q"
    fit = shorten(subject, LIMIT)
    assert fits(fit) and fit.dropped and fit.cut == 0


def test_fillers_are_dropped_from_the_end_one_at_a_time_and_stop_when_it_fits():
    subject = "The point is that the build is just very green and " + "x" * 19 + " done"
    assert len(subject) == 75
    fit = shorten(subject, LIMIT)
    assert fit.dropped == ("very", "just")              # the tail first; then it fit
    assert fit.subject.startswith("The point is that the build is green and")


@pytest.mark.parametrize("token", ["the,", "that's", "not", "is", "538"])
def test_only_whole_letter_only_filler_tokens_go(token):
    subject = "Keep " + " ".join([token] * 30)
    fit = shorten(subject, LIMIT)
    assert fit.dropped == ()


def test_the_first_token_is_never_dropped():
    subject = "The " + "y" * 66
    fit = shorten(subject, LIMIT)
    assert fit.subject.startswith("The") and not fit.dropped


def test_a_long_subject_is_trimmed_at_a_word_boundary_with_an_ellipsis():
    subject = "Report: " + " ".join(["word"] * 20)          # 107 chars, no fillers
    fit = shorten(subject, LIMIT)
    assert fits(fit) and fit.subject.endswith("...") and not fit.hard_cut
    assert fit.subject[:-3] == subject[:len(fit.subject) - 3]
    assert subject[len(fit.subject) - 3] == " ", "cut at a word boundary"
    assert fit.cut == len(subject) - (len(fit.subject) - 3)
    assert f"cut {fit.cut} chars at a word boundary" in fit.report()


def test_trailing_punctuation_is_stripped_before_the_ellipsis():
    subject = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda, mu nu xi"
    fit = shorten(subject, 66)
    assert fit.subject == "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda..."


def test_one_token_longer_than_the_room_is_hard_cut():
    subject = "/" + "p" * 79                               # an 80-char path
    fit = shorten(subject, LIMIT)
    assert fit.hard_cut and fit.subject == subject[:66] + "..." and len(fit.subject) == 69
    assert "hard cut: one 80-char word" in fit.report()


def test_a_hard_cut_names_the_word_it_cut_not_the_first_word():
    """do-003 review (9262a513): a punctuation-only first word strips to
    nothing, so the cut lands in the SECOND word; the report said `one
    1-char word` (the dash) while it cut an 80-char one."""
    fit = shorten("— " + "x" * 80, LIMIT)
    assert fit.hard_cut and fits(fit)
    assert fit.hard_word == 80
    assert "hard cut: one 80-char word" in fit.report()


def test_collapse_alone_can_be_enough_when_over():
    subject = "Status:" + "  ok" * 20                      # 87 chars, 67 collapsed
    fit = shorten(subject, LIMIT)
    assert fit.subject == "Status:" + " ok" * 20 and fit.cut == 0 and fit.dropped == ()


def test_collapse_reuses_the_senders_own_rule():
    from sessionmail import _sender
    assert _shorten.collapse is _sender.collapse


# ── EOM (3.6) ─────────────────────────────────────────────────────────────────

def test_a_trailing_eom_survives_a_trim():
    subject = " ".join(["word"] * 15) + " EOM"             # 78 chars
    fit = shorten(subject, LIMIT)
    assert fits(fit) and fit.subject.endswith("... EOM")


def test_a_trailing_eom_survives_a_hard_cut():
    fit = shorten("x" * 80 + " EOM", LIMIT)
    assert fits(fit) and fit.subject == "x" * 66 + "... EOM"   # eom-not-counted: was 62


# ── the limit (Q10) ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("limit", [0, 3, 7])
def test_a_limit_under_8_declines(limit):
    fit = shorten("a subject far too long for that", limit)
    assert fit.declined and f"the limit {limit}" in fit.declined and not fit.changed


def test_a_limit_under_8_does_not_touch_a_subject_that_fits_it():
    fit = shorten("ok", 5)
    assert not fit.declined and not fit.changed


@pytest.mark.parametrize("limit", [8, 20, 40, 100])
def test_the_limit_is_a_parameter_never_a_hard_69(limit):
    subject = " ".join(["alpha"] * 40)
    fit = shorten(subject, limit)
    assert len(fit.subject) <= limit and fit.subject


def test_at_the_floor_an_eom_subject_still_fits():
    fit = shorten("x" * 30 + " EOM", 8)
    assert fit.subject == "xxxxx... EOM"     # eom-not-counted: the marker costs nothing; was "x... EOM"


# ── the model seam (section 8): a hook with no caller ─────────────────────────

def test_the_hook_is_not_called_unless_given_and_over_by_more_than_the_overage():
    calls = []

    def suggest(subject, limit):
        calls.append(subject)
        return "Short and sweet"

    near = "SM-538 " + "w" * 64                             # 71: over by 2, not > 10
    fit = shorten(near, LIMIT, suggest=suggest)
    assert calls == [] and not fit.suggested
    far = "SM-538 " + " ".join(["w" * 8] * 10)              # 96: over by 27
    fit = shorten(far, LIMIT, suggest=suggest)
    assert calls and fit.subject == "Short and sweet" and fit.suggested


@pytest.mark.parametrize("answer", [None, "x" * 70, "line\nbreak", "naïve", "", "   "])
def test_a_bad_suggestion_is_ignored_and_the_mechanical_trim_proceeds(answer):
    far = "SM-538 " + " ".join(["w" * 8] * 10)
    fit = shorten(far, LIMIT, suggest=lambda s, n: answer)
    assert fits(fit) and not fit.suggested and fit.subject.endswith("...")


def test_the_overage_and_floor_are_module_constants():
    assert _shorten.MODEL_OVERAGE == 10 and _shorten.MIN_LIMIT == 8


# ── properties ────────────────────────────────────────────────────────────────

_WORDS = ["the", "a", "build", "green", "SM-538", "really", "deploy", "—", "“ok”", "réglé",
          "🎉", "修复", "x" * 30, "path/" + "q" * 70, "that", "…", "  ", "EOM", "now,"]


def _corpus(seed=538, n=600):
    rng = random.Random(seed)
    for _ in range(n):
        words = [rng.choice(_WORDS) for _ in range(rng.randint(1, 25))]
        subject = " ".join(words)
        if rng.random() < 0.2:
            subject += " EOM"
        yield subject, rng.choice([8, 12, 30, 55, 69, 69, 69, 100])


@pytest.mark.parametrize("subject, limit", list(_corpus()))
def test_property_fits_or_declines_and_is_idempotent(subject, limit):
    fit = shorten(subject, limit)
    if fit.declined:
        assert fit.subject == subject
    else:
        assert _send.counted(fit.subject) <= limit and fit.subject.isascii()
        if fit.changed:
            assert fit.subject.strip(), "a changed subject is never empty"
        if subject.endswith(" EOM") and fit.changed:
            assert fit.subject.endswith(" EOM")
    again = shorten(fit.subject, limit)
    assert again.changed is False                                     # idempotent


# ── SM-538 do-006: `Fit.shortened`, the report's verb and the counter's reason ──

@pytest.mark.parametrize("subject, want", [
    ("SM-538 — “ready”", False),                                              # fold only
    ("SM-538 the shortener is ready for review and it covers the whole door ok", True),
    ("a fitting subject", False),                                             # unchanged
])
def test_shortened_is_the_one_rule_the_report_verb_reads(subject, want):
    fit = shorten(subject, 69)
    assert fit.shortened is want
    if fit.changed:
        assert fit.report().startswith("message send: " + ("shortened" if want else "folded"))


# ── SM-792: the cap, `shorten(..., cap=N)` (RULED Q1: the OVERAGE) ───────────

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
         "judy november oscar papa quebec romeo sierra tango uniform victor")


def over(k: int, limit: int = LIMIT) -> str:
    """An ASCII subject exactly K characters over LIMIT, ending in a letter."""
    s = WORDS[:limit + k]
    s = s[:-1] + "z" if s.endswith(" ") else s
    assert len(s) == limit + k
    return s


def test_cap_measure_is_the_overage_with_eom_not_counted():
    for k in (1, 10, 24):
        assert _shorten.cap_measure(shorten(over(k), LIMIT), LIMIT) == k
        eom = over(k) + " EOM"
        assert _shorten.cap_measure(shorten(eom, LIMIT), LIMIT) == k, "' EOM' is not counted"


@pytest.mark.parametrize("cap", [1, 5, 10, 24])
def test_a_subject_exactly_at_the_cap_shortens(cap):
    fit = shorten(over(cap), LIMIT, cap=cap)
    assert not fit.declined and fit.changed and fits(fit)
    assert fit == shorten(over(cap), LIMIT), "under the cap, the cap changes nothing"


@pytest.mark.parametrize("cap", [1, 5, 10, 24])
def test_one_over_the_cap_declines_naming_both_numbers(cap):
    fit = shorten(over(cap + 1), LIMIT, cap=cap)
    assert fit.declined == f"{cap + 1} over; the cap is {cap}: rewrite it, or front-load the point"
    assert fit.subject == fit.original == over(cap + 1) and not fit.changed
    assert fit.over and fit.report() == ""


def test_no_cap_is_the_sm538_shortener_unchanged():
    fit = shorten(over(60), LIMIT)
    assert not fit.declined and fits(fit)


def test_the_cap_holds_the_overage_not_the_chars_removed():
    """RULED Q1: a subject AT the cap goes even when the trim removes more
    than the cap (the "..." and a partial word), and the report and `cut`
    keep the TRUE number removed."""
    fit = shorten(over(10), LIMIT, cap=10)
    removed = len(fit.original) - len(fit.subject)
    assert not fit.declined and removed > 10, (removed, fit)
    assert f"shortened 79->{len(fit.subject)}" in fit.report()


@pytest.mark.parametrize("k, sent", [(10, True), (11, False)])
def test_the_cap_does_not_count_a_trailing_eom(k, sent):
    """79 + " EOM" is 10 over, not 14: the cap road reads the same `counted`."""
    subject = over(k) + " EOM"
    fit = shorten(subject, LIMIT, cap=10)
    assert bool(fit.declined) is not sent, fit
    if sent:
        assert fit.subject.endswith(" EOM") and fits(fit)
    else:
        assert fit.declined.startswith(f"{k} over; ")


@pytest.mark.parametrize("subject", [
    "SM-538 — “ready”",                                   # folds, shorter
    "x" * 60 + " ok …",                                   # 65 chars; the fold makes it 67
    "y" * 66 + " …",                                      # 68 chars; the fold makes it 70: cut
])
def test_a_folded_subject_that_fits_is_never_capped(subject):
    assert len(subject) <= LIMIT and not subject.isascii()
    fit = shorten(subject, LIMIT, cap=1)
    assert not fit.declined and fit.changed and fits(fit)
    assert fit == shorten(subject, LIMIT)


def test_an_over_and_non_ascii_subject_is_capped_by_its_overage():
    subject = "SM-792 — " + over(6)[9:]                         # 75 chars, non-ASCII
    assert len(subject) == LIMIT + 6
    assert shorten(subject, LIMIT, cap=6).declined == ""
    assert shorten(subject, LIMIT, cap=5).declined.startswith("6 over; the cap is 5")


def test_a_decline_before_the_cap_keeps_its_own_reason():
    """The gutted-subject decline and the no-room floor come first."""
    assert shorten("修复完成" * 30, LIMIT, cap=1).declined.startswith("folding would drop")
    assert shorten(over(20, 7), 7, cap=1).declined.startswith("the limit 7")
