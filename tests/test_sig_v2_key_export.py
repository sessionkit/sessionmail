"""SM-862: the sig_v2 TEST key's PRIVATE half stays out of the release export.

`.gitattributes` export-ignores `tests/fixtures/sig_v2/sessionmail-sig-TEST-VECTORS-ONLY`
and nothing else beside it: the `.pub` ships, and no test needs the private half. Only
`make_vectors.py` does, and with the key absent (as in the export) it exits 1 with one
plain line, before it asks for ssh-agent or writes a byte. With the key present it
re-mints the same bytes.

The suite runs in three shapes (tests/_export.py). In the checkout, which tracks the private
half, these tests ask git and pin its bytes (the directory-hash tests leave it out). In the
export, a tarball or a git clone of it, they see it absent and the `.pub` present.

Run: uv run pytest -q tests/test_sig_v2_key_export.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _export

ROOT = _export.ROOT
SIG_V2 = ROOT / "tests" / "fixtures" / "sig_v2"
CONTROLS = ROOT / "tests" / "fixtures" / "mime_honest" / "controls"
KEY_NAME = "sessionmail-sig-TEST-VECTORS-ONLY"
PRIVATE, PUB = f"tests/fixtures/sig_v2/{KEY_NAME}", f"tests/fixtures/sig_v2/{KEY_NAME}.pub"
#: sha256 of the private half as fix-001 committed it (e0d5372): the directory-hash tests
#: (test_signed_text, test_verify_cte_defect) skip this file, so it is pinned here.
PRIVATE_SHA256 = "cb8a82aa0ae27c347dd5c6f0d4780bd03105a81f15c9b96c86f70a59c0e9700a"
NOT_SHIPPED = "make_vectors: this TEST key is not shipped; generate your own ed25519 key to re-mint\n"


def test_the_export_leaves_out_the_private_half_only():
    """Three shapes (tests/_export.py, SM-857): the checkout tracks the private half, so its
    bytes are pinned and a missing one fails; the tarball and a git clone of the export never
    held it, so there it must be absent. Any tree with git answers check-attr."""
    if _export.in_checkout():
        out = subprocess.run(["git", "-C", str(ROOT), "check-attr", "export-ignore", "--",
                              PRIVATE, PUB], check=True, capture_output=True, text=True).stdout
        assert out.splitlines() == [f"{PRIVATE}: export-ignore: set",
                                    f"{PUB}: export-ignore: unspecified"]
    if _export.tracked(PRIVATE):
        assert hashlib.sha256((ROOT / PRIVATE).read_bytes()).hexdigest() == PRIVATE_SHA256
    else:
        assert not (ROOT / PRIVATE).exists(), "the private TEST key is in the export"
    assert (ROOT / PUB).read_text().startswith("ssh-ed25519 ")


def _tree(tmp_path: Path, with_key: bool) -> Path:
    """A copy of what make_vectors.py reads and writes: src/, sig_v2/ and the controls."""
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "src", root / "src",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SIG_V2, root / "tests" / "fixtures" / "sig_v2",
                    ignore=None if with_key else shutil.ignore_patterns(KEY_NAME))
    shutil.copytree(CONTROLS, root / "tests" / "fixtures" / "mime_honest" / "controls")
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / "tests" / "fixtures").rglob("*")) if p.is_file()}


@pytest.mark.parametrize("path", ["ordinary", "no-ssh-tools"])
def test_without_the_key_make_vectors_says_one_plain_line_and_writes_nothing(tmp_path, path):
    """Before the ssh tools are asked for: an export with none of them gets the same line."""
    root = _tree(tmp_path, with_key=False)
    before = _files(root)
    env = {"PATH": "/usr/bin:/bin" if path == "ordinary" else str(tmp_path / "empty")}
    done = subprocess.run([sys.executable, "-I",
                           str(root / "tests" / "fixtures" / "sig_v2" / "make_vectors.py")],
                          capture_output=True, text=True, env=env, cwd=root)
    assert (done.returncode, done.stdout, done.stderr) == (1, "", NOT_SHIPPED)
    assert "Traceback" not in done.stderr
    assert _files(root) == before


def test_with_the_key_make_vectors_remints_the_same_bytes(tmp_path):
    if not (ROOT / PRIVATE).exists():
        pytest.skip(_export.export_reason(PRIVATE))
    missing = [t for t in ("ssh-keygen", "ssh-agent", "ssh-add") if shutil.which(t) is None]
    if missing:
        pytest.skip(f"not on PATH: {', '.join(missing)}")
    root = _tree(tmp_path, with_key=True)
    before = _files(root)
    done = subprocess.run([sys.executable, "-I",
                           str(root / "tests" / "fixtures" / "sig_v2" / "make_vectors.py")],
                          capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
                          cwd=root)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"make_vectors: wrote 44 files under the TEST key {KEY_NAME}\n"
    assert _files(root) == before
