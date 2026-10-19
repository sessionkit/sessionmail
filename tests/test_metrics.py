"""SM-538 do-006: `_metrics`, the shortener's counter in the node_exporter
textfile collector (design 5.2, RULED Q4). Written only if the directory
already exists, under a lock, through a temporary file and a rename; a
counter that cannot be written is a notice, never a failure."""

from __future__ import annotations

import os
import stat
import threading

import pytest

from sessionmail import _metrics

SHORTENED = 'sessionmail_send_total{result="sent",reason="subject_shortened"}'
FOLDED = 'sessionmail_send_total{result="sent",reason="subject_folded"}'
REMOVED = "sessionmail_subject_chars_removed_total"


def series(path) -> dict[str, int]:
    """The file's samples, as a scraper reads them (comments skipped)."""
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            out[name] = int(value)
    return out


@pytest.fixture
def textfile(tmp_path, monkeypatch):
    d = tmp_path / "textfile_collector"
    d.mkdir()
    monkeypatch.setenv(_metrics.DIR_VAR, str(d))
    return d


def test_the_conftest_points_the_counter_away_from_the_live_collector():
    named = os.environ.get(_metrics.DIR_VAR, "")
    assert named and not named.startswith("/opt/homebrew"), named


def test_a_shortened_send_goes_0_to_1_to_2_and_sums_the_chars_removed(textfile):
    path = textfile / _metrics.FILE
    assert not path.exists()
    assert _metrics.count("subject_shortened", 3) == ""
    assert series(path) == {SHORTENED: 1, FOLDED: 0, REMOVED: 3}
    assert _metrics.count("subject_shortened", 4) == ""
    assert series(path) == {SHORTENED: 2, FOLDED: 0, REMOVED: 7}


def test_a_folded_send_has_its_own_series(textfile):
    _metrics.count("subject_shortened", 5)
    _metrics.count("subject_folded", 0)
    assert series(textfile / _metrics.FILE) == {SHORTENED: 1, FOLDED: 1, REMOVED: 5}


def test_the_file_is_a_textfile_the_collector_reads(textfile):
    """HELP and TYPE for each metric, and both are counters."""
    _metrics.count("subject_folded", 0)
    text = (textfile / _metrics.FILE).read_text()
    for name in ("sessionmail_send_total", REMOVED):
        assert f"# HELP {name} " in text and f"# TYPE {name} counter\n" in text
    assert text.endswith("\n")


def test_a_counter_never_goes_down(textfile):
    """A fold can LENGTHEN a subject (… is three dots); a counter of chars
    removed only ever adds what was removed."""
    _metrics.count("subject_folded", -2)
    assert series(textfile / _metrics.FILE)[REMOVED] == 0


def test_no_temporary_file_is_left_for_the_collector(textfile):
    _metrics.count("subject_shortened", 1)
    assert sorted(p.name for p in textfile.iterdir() if p.suffix == ".prom") == [_metrics.FILE]
    assert not [p for p in textfile.iterdir() if "tmp" in p.name]


def test_a_missing_directory_writes_nothing_and_says_nothing(tmp_path, monkeypatch):
    gone = tmp_path / "no-such-dir"
    monkeypatch.setenv(_metrics.DIR_VAR, str(gone))
    assert _metrics.count("subject_shortened", 3) == ""
    assert not gone.exists(), "the directory is never created"


def test_a_named_missing_directory_never_falls_through_to_another(tmp_path, monkeypatch):
    """The first variable that is SET decides, so a sandbox that names a
    missing directory can never reach the live collector."""
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(_metrics.DIR_VAR, str(tmp_path / "missing"))
    monkeypatch.setenv(_metrics.NODE_EXPORTER_VAR, str(other))
    assert _metrics.textfile_dir() == tmp_path / "missing"
    _metrics.count("subject_shortened", 1)
    assert list(other.iterdir()) == []


def test_the_directory_falls_back_in_order(tmp_path, monkeypatch):
    monkeypatch.delenv(_metrics.DIR_VAR, raising=False)
    monkeypatch.setenv(_metrics.NODE_EXPORTER_VAR, str(tmp_path))
    assert _metrics.textfile_dir() == tmp_path
    monkeypatch.delenv(_metrics.NODE_EXPORTER_VAR)
    assert _metrics.textfile_dir() == _metrics.HOMEBREW_DIR
    assert _metrics.textfile_dir({}) == _metrics.HOMEBREW_DIR


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_an_unwritable_directory_is_a_notice_not_a_failure(textfile):
    textfile.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        said = _metrics.count("subject_shortened", 3)
    finally:
        textfile.chmod(stat.S_IRWXU)
    assert said.startswith("message send: counter not written: "), said
    assert not (textfile / _metrics.FILE).exists()


def test_a_garbled_file_is_a_notice_and_is_left_alone(textfile):
    path = textfile / _metrics.FILE
    path.write_text(f"{SHORTENED} not-a-number\n")
    said = _metrics.count("subject_shortened", 1)
    assert said.startswith("message send: counter not written: "), said
    assert path.read_text() == f"{SHORTENED} not-a-number\n"


def test_concurrent_counts_lose_nothing(textfile):
    """The flock: twenty seats at once, twenty counted."""
    threads = [threading.Thread(target=_metrics.count, args=("subject_shortened", 1))
               for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert series(textfile / _metrics.FILE) == {SHORTENED: 20, FOLDED: 0, REMOVED: 20}
