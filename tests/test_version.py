"""The ONE version: pyproject.toml is the source; everything else agrees with it.

scripts/release.py is the single writer: it sets the static [project] version
(through `uv version`) and mirrors it into the two plugin manifests.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import sessionmail

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _version():
    return _pyproject()["project"]["version"]


def test_pyproject_version_is_static():
    project = _pyproject()["project"]
    assert "version" not in project.get("dynamic", [])
    assert "version" not in _pyproject().get("tool", {}).get("hatch", {})


def test_package_version_is_pyproject_version():
    assert sessionmail.__version__ == _version()


def test_plugin_json_mirrors_pyproject_version():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert plugin["version"] == _version()


def test_marketplace_mirrors_pyproject_version():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert market["metadata"]["version"] == _version()


def test_lock_records_pyproject_version():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    (own,) = [p for p in lock["package"] if p["name"] == "sessionmail"]
    assert own["version"] == _version()


def test_changelog_has_unreleased_section():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in text
    assert "## [%s]" % _version() in text


def test_release_current_reads_pyproject():
    out = subprocess.run([sys.executable, str(RELEASE), "--current"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == _version()


def test_release_current_refuses_a_dynamic_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndynamic = ["version"]\n')
    out = subprocess.run([sys.executable, str(RELEASE), "--current"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert out.returncode != 0
    assert "no static [project] version" in out.stderr


def test_release_mirror_keeps_formatting():
    sys.path.insert(0, str(RELEASE.parent))
    try:
        import release
    finally:
        sys.path.remove(str(RELEASE.parent))
    path = ROOT / ".claude-plugin/marketplace.json"
    before = path.read_text()
    after = release.set_json_version(path, "9.9.9")
    assert path.read_text() == before  # computed, not written
    assert json.loads(after)["metadata"]["version"] == "9.9.9"
    # Only the version value changed: every other line is byte-identical.
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(diff) == 1 and "9.9.9" in diff[0][1]
