"""eom-not-counted: a trailing ' EOM' is not counted against the subject limit.

The senders stop counting those 4 characters. The check and the shortener
count the same way, through `_send.counted`. The case that showed it: a
66-character subject plus ' EOM' refused as 70.
"""
from __future__ import annotations

import pytest

from sessionmail import _send
from sessionmail._shorten import shorten

LIMIT = 69


@pytest.mark.parametrize("subject, n", [
    ("x" * 69 + " EOM", 69),     # the marker is not counted
    ("x" * 69, 69),              # no marker: counted whole, as before
    ("x" * 69 + "EOM", 72),      # no space: not the marker, counted whole
    ("x" * 69 + " eom", 73),     # the marker is upper case, as is_eom reads it
    ("x" * 69 + " EOM ", 74),    # a trailing space: not the marker
])
def test_counted(subject, n):
    assert _send.counted(subject) == n


def test_69_plus_the_marker_passes_the_check():
    assert _send.check_subject("x" * 69 + " EOM", env={}) == []


def test_70_plus_the_marker_is_refused_and_the_refusal_says_why():
    [why] = _send.check_subject("x" * 70 + " EOM", env={})
    assert "70 characters, not counting ' EOM'" in why and "limit is 69" in why


def test_a_subject_without_the_marker_reads_as_before():
    [why] = _send.check_subject("x" * 70, env={})
    assert why.startswith("refused - the subject is 70 characters; the limit is 69")


def test_the_shortener_leaves_69_plus_the_marker_alone():
    fit = shorten("x" * 69 + " EOM", LIMIT)
    assert not fit.changed and not fit.declined and fit.subject == "x" * 69 + " EOM"


def test_the_shortener_cuts_to_69_before_the_marker():
    fit = shorten("x" * 80 + " EOM", LIMIT)
    assert fit.subject == "x" * 66 + "... EOM"
    assert _send.counted(fit.subject) == LIMIT and not _send.check_subject(fit.subject, env={})
