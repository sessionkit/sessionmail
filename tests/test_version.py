"""The ONE version: plugin.json is the source; everything else agrees with it."""

import json
from pathlib import Path

import sessionmail

ROOT = Path(__file__).resolve().parents[1]


def _plugin_version():
    return json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]


def test_package_version_is_plugin_json_version():
    assert sessionmail.__version__ == _plugin_version()


def test_marketplace_mirrors_plugin_version():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert market["metadata"]["version"] == _plugin_version()


def test_changelog_has_unreleased_section():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in text
    assert "## [%s]" % _plugin_version() in text
