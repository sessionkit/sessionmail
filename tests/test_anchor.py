"""The test-time anchors are defined once per language, and the two agree.

tests/conftest.py defines ANCHOR and ANCHOR_SUMMER for Python; tests/mhs/
anchor.sh defines them for a sourced shell test (SM-869). These tests read
the shell values from the file, so no date is typed twice.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import ANCHOR, ANCHOR_SUMMER

SH = Path(__file__).parent / "mhs" / "anchor.sh"
SYDNEY = ZoneInfo("Australia/Sydney")
PAIR = {"ANCHOR": ANCHOR, "ANCHOR_SUMMER": ANCHOR_SUMMER}


def sourced(name: str) -> str:
    """$name after `. tests/mhs/anchor.sh` in a clean bash."""
    out = subprocess.run(["bash", "--norc", "--noprofile", "-c", '. "$1"; printf %s "${!2}"',
                          "_", str(SH), name], capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.parametrize("name", sorted(PAIR))
def test_the_shell_anchor_equals_the_python_one(name):
    text = sourced(name)
    assert text.endswith("Z"), text
    assert datetime.datetime.fromisoformat(text[:-1] + "+00:00") == PAIR[name]


@pytest.mark.parametrize("name", sorted(PAIR))
def test_each_anchor_is_an_aware_utc_sunday_in_2030(name):
    value = PAIR[name]
    assert value.utcoffset() == datetime.timedelta(0)
    assert value.year == 2030
    assert value.weekday() == value.astimezone(SYDNEY).weekday() == 6


def test_anchor_is_sydney_standard_time_and_summer_is_daylight_time():
    assert ANCHOR.astimezone(SYDNEY).utcoffset() == datetime.timedelta(hours=10)
    assert ANCHOR_SUMMER.astimezone(SYDNEY).utcoffset() == datetime.timedelta(hours=11)
