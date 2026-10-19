"""SM-877: `_maildir.strict_mode`, the stat that tells ABSENT from UNREADABLE.

Up to CPython 3.13 `Path.is_dir()`, `is_file()` and `exists()` raised
PermissionError when a folder on the way could not be searched; from 3.14
they answer False, as `os.path.isdir` always did. The callers that must say
"unreadable" (the archive's verify, the subscription reader, `pager rule
list`) stat through this helper instead, so its contract is pinned here,
independent of the interpreter: if a future CPython changes `os.stat`, this
goes red before any of them misreports.
"""
from __future__ import annotations

import os
import stat

import pytest

from sessionmail._maildir import strict_mode


def test_strict_mode_raises_under_an_unsearchable_folder_and_is_0_when_absent(tmp_path):
    locked = tmp_path / "locked"
    (locked / "new").mkdir(parents=True)
    (locked / "dangling").symlink_to(tmp_path / "nowhere")
    assert stat.S_ISDIR(strict_mode(locked / "new"))
    assert strict_mode(tmp_path / "absent") == 0
    assert strict_mode(tmp_path / "absent" / "below") == 0
    assert strict_mode(locked / "dangling") == 0, "a dangling link is nothing there"
    assert stat.S_ISLNK(strict_mode(locked / "dangling", follow_symlinks=False))
    os.chmod(locked, 0o444)              # r--: it lists, but cannot be searched
    try:
        if os.access(locked, os.X_OK):
            pytest.skip("running as a user who can search a mode-0o444 folder")
        assert sorted(os.listdir(locked)) == ["dangling", "new"]
        with pytest.raises(PermissionError):
            strict_mode(locked / "new")
        with pytest.raises(PermissionError):
            strict_mode(locked / "dangling", follow_symlinks=False)
    finally:
        os.chmod(locked, 0o755)
