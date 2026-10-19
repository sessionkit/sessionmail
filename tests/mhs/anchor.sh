# shellcheck shell=bash
# The test-time anchors (SM-869), for a shell test to source:
# a lone typed time is one of these plus a delta, never a real date. Both are
# Sundays in 2030: ANCHOR in Sydney's standard time (+10:00), ANCHOR_SUMMER in
# its daylight time (+11:00). tests/conftest.py holds the Python pair, and
# tests/test_anchor.py holds the two definitions equal.
ANCHOR=2030-06-09T12:34:56Z
ANCHOR_SUMMER=2030-12-08T12:34:56Z
