"""SM-875 fix-002: which home `~` is, per key, and docs/settings.md says so.

SK-95's walk (w2, postfix part) measured it: a `mail.json` naming an MTA
under a scratch `$HOME` was not read, and `message setup` planned the drop.
The doc said "`~` is `$HOME`, except for `registry` and `alert_runner`".
The code says otherwise, and the code is what was designed (`setting`'s
docstring, `_operator.host_sources`): every key read from "the template and
the host" is read under the SETTINGS HOME, the password database's home
(`_operator.settings_home`, the suite's seam), never `$HOME`. The host file
the hooks, the monitor, `pager_required` and `capture` read is under
`$HOME` (`_settings.path(env)`).

Pinned two ways: the behaviour, with a `mail.json` under each home; and the
doc's `~` bullet, held to the keys table and to `_settings.KEYS`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sessionmail import _operator, _seat, _settings

DOC = Path(__file__).resolve().parents[1] / "docs" / "settings.md"

#: The keys read through `_operator.host_values("mail")` beside `setting`'s
#: own table: `registry` (`_mhs.configured_registry`) and `alert_runner`
#: (`rules.alert_runner`). Spelled out: the test does not follow the code.
HOST_VALUES_KEYS = ("registry", "alert_runner")

#: The keys the host file under `$HOME` decides, as docs/settings.md lists them.
HOME_KEYS = ("hooks", "monitor", "pager_required", "capture")

MARKER = "/marker/sendmail"


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Two homes that differ: the settings home (pwd's, in production) and
    `$HOME`, set in the environment and in the env the readers are given."""
    acct, home = tmp_path / "acct", tmp_path / "home"
    acct.mkdir()
    home.mkdir()
    monkeypatch.setattr(_operator, "settings_home", lambda: acct)
    monkeypatch.setenv("HOME", str(home))
    for k in _settings.KEYS.values():
        monkeypatch.delenv(k.var, raising=False)
    return acct, home


def _mail_json(under: Path, data: dict) -> Path:
    p = under / ".sessionkit" / "mail" / "settings" / "mail.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) + "\n")
    return p


def _nested(dotted: str, value: object) -> dict:
    out: object = value
    for part in reversed(dotted.split(".")):
        out = {part: out}
    return out  # type: ignore[return-value]


# ── the behaviour ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(_settings.KEYS))
def test_a_template_and_host_key_under_HOME_is_not_read(homes, key):
    acct, home = homes
    _mail_json(home, _nested(key, MARKER))
    got = _settings.setting(key, {"HOME": str(home)})
    assert got.source == _settings.DEFAULT, got
    assert got.value == _settings.KEYS[key].default


@pytest.mark.parametrize("key", sorted(_settings.KEYS))
def test_a_template_and_host_key_under_the_settings_home_is_read(homes, key):
    acct, home = homes
    _mail_json(acct, _nested(key, MARKER))
    got = _settings.setting(key, {"HOME": str(home)})
    assert got == _settings.Setting(MARKER, _settings.HOST_LAYER)


def test_the_carrier_walk_w2_postfix(homes):
    """SK-95 walk-w2-postfix.txt, as a test: the MTA named under `$HOME` is
    not the carrier (the drop, by default); under the settings home it is."""
    acct, home = homes
    _mail_json(home, {"version": 1, "sendmail": "/usr/sbin/sendmail"})
    assert _settings.setting("sendmail").source == _settings.DEFAULT
    _mail_json(acct, {"version": 1, "sendmail": "/usr/sbin/sendmail"})
    assert tuple(_settings.setting("sendmail")) == ("/usr/sbin/sendmail", _settings.HOST_LAYER)


@pytest.mark.parametrize("key", HOST_VALUES_KEYS)
def test_registry_and_alert_runner_are_read_under_the_settings_home_only(homes, key):
    acct, home = homes
    _mail_json(home, {key: "from-home"})
    assert key not in _operator.host_values(_settings.TOOL)
    _mail_json(acct, {key: "from-acct"})
    assert _operator.host_values(_settings.TOOL)[key] == "from-acct"


def test_hooks_and_monitor_are_read_under_HOME(homes):
    acct, home = homes
    _mail_json(acct, {"version": 1, "hooks": True, "monitor": False})
    env = {"HOME": str(home)}
    assert _settings.read(env).state == _settings.MISSING
    assert not _settings.monitor_host(env).off
    _mail_json(home, {"version": 1, "hooks": True, "monitor": False})
    assert _settings.read(env).hooks
    assert _settings.monitor_host(env).off


def test_pager_required_is_read_under_HOME(homes):
    acct, home = homes
    env = {"HOME": str(home)}
    _mail_json(acct, {"pager_required": True})
    assert not _seat.pager_required(None, "-", env).on
    _mail_json(home, {"pager_required": True})
    assert _seat.pager_required(None, "-", env).on


# ── the doc ──────────────────────────────────────────────────────────────────

def _doc() -> str:
    return DOC.read_text()


def _table_rows() -> dict[str, str]:
    """The keys table: each backticked key in a row's first cell -> its
    "Read from" cell."""
    rows = {}
    for line in _doc().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].startswith("`"):
            for name in re.findall(r"`([^`]+)`", cells[0]):
                rows[name] = cells[3]
    return rows


def _tilde_bullet() -> str:
    doc = _doc()
    start = doc.index("- Which home `~` is depends on the key")
    return doc[start:doc.index("\n\n", start)]


def test_the_doc_drops_the_old_tilde_line():
    assert "`~` is `$HOME`, except" not in _doc()


def test_the_doc_names_the_password_database_for_the_template_and_host_keys():
    bullet = _tilde_bullet()
    group = bullet[bullet.index('"the template and the host" ('):]
    group = group[:group.index(")")]
    named = set(re.findall(r"`([^`]+)`", group))
    rows = _table_rows()
    table = {k for k, where in rows.items() if where == "the template and the host"}
    code = {k.split(".")[0] for k in _settings.KEYS} | set(HOST_VALUES_KEYS)
    assert named == table == code
    assert "password database, never `$HOME`" in " ".join(bullet.split())


def test_the_doc_names_HOME_for_the_host_file_keys():
    bullet = _tilde_bullet()
    line = next(l for l in bullet.splitlines() if l.lstrip().startswith("- `hooks`"))
    rest = bullet[bullet.index(line):]
    rest = " ".join(rest[:rest.index("\n  - ", 1)].split())
    named = set(re.findall(r"`([^`]+)`", rest.split(":")[0]))
    assert named == set(HOME_KEYS)
    assert rest.endswith("`~` is `$HOME`.")
    rows = _table_rows()
    assert {k for k in HOME_KEYS} == {
        k for k, where in rows.items()
        if where in ("the host `mail.json` only", "host, seat and session")}
