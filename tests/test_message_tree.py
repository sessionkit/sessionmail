"""`message tree` - a thread's subjects as a tree, superseded hidden, and
the last line is what it cost. Hermetic: the maildir helper from
test_message_list writes real files under tmp_path.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sessionmail.message import cli
from test_message_list import deliver, box  # noqa: F401

EX_OK, EX_NO_MESSAGE = 0, 2


def run(*args):
    return CliRunner().invoke(cli, ["tree", *args])


def reply(box, key, subject, parent, **kw):
    mid = deliver(box, "alpha", key, subject=subject, **kw)
    p = box / "alpha" / "new" / f"{key}.M1P1.host"
    p.write_text(p.read_text().replace("Subject:", f"In-Reply-To: <{parent}>\r\nReferences: <{parent}>\r\nSubject:", 1))
    return mid


@pytest.fixture
def thread(box):
    root = deliver(box, "alpha", "1923171201", subject="root", sender="alpha@example.invalid")
    a = reply(box, "1923171202", "reply a", root)
    reply(box, "1923171204", "reply b", root)
    reply(box, "1923171203", "a's child", a)
    old = reply(box, "1923171205", "draft v1", root)
    reply(box, "1923171206", "draft v2", root, supersedes=old)
    deliver(box, "alpha", "1923171207", subject="unrelated")
    return root


def test_tree_is_rooted_at_the_id_and_names_what_is_above(thread, box):
    r = run("1923171203@example.invalid")
    assert r.exit_code == EX_OK, r.output
    assert r.output.splitlines()[:2] == ["(under: reply a; 2 above)", "a's child  [beta]"]
    assert "1 shown" in r.output


def test_tree_from_the_root_renders_the_whole_thread_oldest_first(thread, box):
    r = run(thread)
    assert r.exit_code == EX_OK, r.output
    assert r.output.splitlines()[:5] == [
        "root  [alpha]",
        "  └ reply a  [beta]",
        "    └ a's child  [beta]",
        "  └ reply b  [beta]",
        "  └ draft v2  [beta]",
    ]
    assert "unrelated" not in r.output
    assert r.output.splitlines()[-1].startswith("-- 5 shown, 1 hidden (superseded) · ≈")


def test_all_keeps_the_superseded_and_depth_cuts(thread):
    r = run(thread, "--all")
    assert "draft v1" in r.output and "6 shown, 0 hidden" in r.output
    assert "a's child" in r.output  # two hops from the root, found to a fixed point
    r = run(thread, "--depth", "0")
    assert r.output.splitlines()[0] == "root  [alpha]" and "1 shown" in r.output and "cut below depth 0" in r.output
    r = run(thread, "--depth", "1")
    assert "reply a" in r.output and "a's child" not in r.output


def test_dates_shows_sender_and_date(thread):
    r = run(thread, "--dates")
    assert "root  [alpha Sun 09 Jun 09:00]" in r.output  # 1923171201 % 24 = 9


def test_no_such_id_is_exit_2_and_by_index_works(thread):
    assert run("nobody@example.invalid").exit_code == EX_NO_MESSAGE
    assert run("1").output.startswith("root  [alpha]")


def test_no_identity_is_usage(box, monkeypatch):
    from sessionmail import _seat
    deliver(box, "alpha", "1923171201")
    for var in _seat.IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    assert run("1").exit_code == 2  # click usage
