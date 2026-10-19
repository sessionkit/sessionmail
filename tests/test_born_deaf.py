"""SM-721 repro-001: the first seat born under a fresh HOME is DEAF to its
SESSIONMAIL_SUBSCRIBE subscription. Two halves, pinned RED by repro-001
(`xfail(strict=True)`) and turned green by fix-001, except the first seat's
end-to-end test, which needs a pre-launch caller in another repo (below).

S1, the schema. sessiontools' launcher birth (`write_env_at_birth`,
sessiontools `src/sessiontools/settings.py:608-666 @ 61ab434`) reads
SESSIONMAIL_SUBSCRIBE only when `mail.schema.json` is installed in the mail
tool's settings folder (`settings_root()/mail/` at that commit) and names it
with `x-env` (`schema_tools`, `:528-533`; `env_keys`,
`:537-559`). sessionmail's only installer is `_settings.install()`
(`src/sessionmail/_settings.py:341-363 @ 8d35912`), reached only through
`_settings.ensure()` (`:366-402`), whose only caller is `hook_batch`
(`src/sessionmail/pager.py:5121 @ 8d35912`), the PostToolBatch hook. So no
birth (`pager listen`, `pager hook start`) installs it, and a fresh HOME's
first seat is born with no `50-env.json`.

S2, the order. In `_birth.ensure_session_box`, `birth_subscriptions`
(`src/sessionmail/_birth.py:309 @ 8d35912`) gates each birth-file box with
`_subscribe.check_box`, which resolves a bare project name by the maps only
(`_maildir.resolve_box` -> `_seat.box_via_map`). It runs BEFORE the born
box's `_seat.regen_maps` (`:356`), so a box whose maildir exists but whose
row the generator has not written yet is refused, and the refusal is written
`false` into `mail.d/70-live.json` (`:122-129`), which wins over the birth
file and is never retried.

THE FIX ((i) + (c)).
S1a: both births (`_birth.ensure_session_box`) install the schema and the
template (`_settings.install(make=True)`), never `mail.json`; and
`pager settings install` is the public verb a launcher runs BEFORE its birth
writer. S2 (c): the birth's gate (`check_box(..., tree=True)`) falls back on
the generator's own tree test when the installed map does not deliver a box
yet, so a box on disk is heard; a box with no maildir anywhere still refuses,
`false`, journaled. The FIRST seat on a fresh HOME still needs a pre-launch
caller of the verb (sessiontools `session start`, taskmaster `tm kick`): its
end-to-end test stays `xfail(strict=True)` naming that dependency.

`launcher_birth` below mirrors sessiontools' birth writer (it is another
repo's code, not a dependency of this one): it writes `50-env.json` only when
the installed schema names the variable, as the real one does. Everything
runs on `test_message_subscription`'s fleet: a mail root, maps and HOME under
tmp_path, the REAL generator writing under tmp - never the live map.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from _seatmap import session_dir, session_env_path, session_live_path
from sessionmail import _birth, _boxes, _settings, _subscribe, pager
from sessionmail.pager import cli
from test_message_subscription import HOST, _maildir, fleet, make_live, rows  # noqa: F401
from test_pager_subscribe_at_birth import EX_TIMEOUT, NEW, banner, fresh, listen  # noqa: F401

#: Why the first seat's end-to-end test stays red on sessionmail alone.
PRE_LAUNCH = ("SM-721 (i): the launcher's birth writer runs BEFORE any sessionmail "
              "code, so seat 1 on a fresh HOME hears its subscription only once a "
              "pre-launch caller (sessiontools `session start`, taskmaster `tm kick`) "
              "runs `pager settings install` first; asks sent to both")


def schema_path() -> Path:
    """`~/.sessionkit/mail/settings/mail.schema.json` under the sandbox HOME."""
    return _settings.path().parent / _settings.SCHEMA


def template_path() -> Path:
    """`~/.sessionkit/mail/settings/mail.template.json` under the sandbox HOME."""
    return _settings.path().parent / _settings.INSTALLED_TEMPLATE


def install(*argv: str):
    """`pager settings install`, the pre-launch verb."""
    return CliRunner().invoke(cli, ["settings", "install", *argv], prog_name="pager")


def names_subscribe(path: Path) -> bool:
    """Does the installed schema name SESSIONMAIL_SUBSCRIBE with `x-env`?"""
    if not path.is_file():
        return False
    props = json.loads(path.read_text()).get("properties", {})
    return props.get("subscriptions", {}).get("x-env") == pager.SUBSCRIBE_VAR


def launcher_birth(sid: str, boxes: str, home: Path) -> Path | None:
    """What sessiontools' `write_env_at_birth` does for SESSIONMAIL_SUBSCRIBE
    (settings.py:608-666 @ 61ab434): writes the birth file only when the
    host's installed mail schema names the variable with `x-env`; else
    nothing (it logs `birth-no-schema`, which this mirror leaves out)."""
    if not names_subscribe(home / _settings.DIR / _settings.SCHEMA):
        return None
    p = session_env_path(sid, home)
    p.parent.mkdir(parents=True, exist_ok=True)
    names = [b for b in boxes.replace(",", " ").split() if b]
    p.write_text(json.dumps({"subscriptions": {b: True for b in names}}, indent=2) + "\n")
    return p


# ── S1: the schema is absent until a hook runs ──────────────────────────────

def test_a_fresh_home_has_no_mail_schema(fresh):
    """The precondition, asserted so the tests below cannot pass vacuously:
    the fleet's HOME has never seen a sessionmail hook."""
    assert not schema_path().exists()
    assert not _settings.path().exists()


def test_the_first_listen_on_a_fresh_home_installs_the_mail_schema(fresh):
    """`pager listen` births the seat's box: by then the schema must be there
    for the NEXT seat's launcher, with SESSIONMAIL_SUBSCRIBE named. The
    template comes with it; `mail.json` does not (SM-450 D3)."""
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert (fresh.agents / NEW / "new").is_dir()                     # it did birth
    assert names_subscribe(schema_path()), f"no schema at {schema_path()}"
    assert template_path().read_text() == _settings.template()
    assert not _settings.path().exists()


def test_the_sessionstart_birth_on_a_fresh_home_installs_the_mail_schema(fresh, monkeypatch):
    """`pager hook start` (SessionStart) births the box too, and is the
    earliest sessionmail code a session runs. The detached generator is
    stubbed: the schema is the question, not the maps."""
    monkeypatch.setattr(_birth, "_detach", lambda *a, **k: None)
    data = {"hook_event_name": "SessionStart", "source": "startup", "session_id": NEW}
    result = CliRunner().invoke(cli, ["hook", "start"], input=json.dumps(data),
                                prog_name="pager")
    assert result.exit_code == 0, result.output
    assert (fresh.agents / NEW / "new").is_dir()                     # it did birth
    assert names_subscribe(schema_path()), f"no schema at {schema_path()}"


@pytest.mark.xfail(strict=True, reason=PRE_LAUNCH)
def test_a_fresh_homes_first_seat_hears_its_subscription_with_no_hook_run(fresh, monkeypatch):
    """End to end: the launcher runs first (no schema, so no birth file),
    then the seat's first listen. The seat must hear `atomnote` with no
    PostToolBatch hook ever run under this HOME. At base it is DEAF: the
    banner says NOT applied, and nothing routes it."""
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote")
    assert launcher_birth(NEW, "atomnote", fresh.userhome) is None   # the launcher wrote nothing
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.subscribed(NEW) == ["atomnote"], banner(result)
    assert rows(fresh.valias)[f"atomnote@{HOST}"].endswith(f", {NEW}@{HOST}")


def test_after_the_hooks_ensure_the_next_seat_hears_it(fresh, monkeypatch):
    """The control (TM-5730 repro case E): once `_settings.ensure()` has run,
    which only hook_batch does today, the next seat's launcher writes the
    birth file and its first listen routes the box. Green at base."""
    _settings.ensure()
    assert names_subscribe(schema_path())
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote")
    assert launcher_birth(NEW, "atomnote", fresh.userhome) is not None
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.subscribed(NEW) == ["atomnote"], banner(result)


# ── S2: the gate runs before regen_maps, and its false is sticky ────────────

@pytest.fixture
def unmapped(fresh):
    """A project box `st/newproj` whose maildir EXISTS (what `tm kick`'s
    `ensure_maildir` makes) but that no generator run has mapped yet: the
    fleet's one regen ran before it was made. A bare project name resolves
    only through the map (`_maildir.resolve_box`), so it does not resolve."""
    _maildir(fresh.agents / "st" / "newproj")
    assert (fresh.agents / "st" / _boxes.STORE_MARKER).exists()
    with pytest.raises(_subscribe.Denied, match="no such box"):
        _subscribe.check_box("newproj", _subscribe.caller())
    return fresh


def test_a_box_born_unmapped_is_heard_and_a_never_routable_box_still_refuses(unmapped, monkeypatch):
    """The fix's control, rewritten from repro-001's as-it-IS control
    (`..._is_refused_at_base_and_the_false_sticks`, which pinned the refusal
    and the sticky `false`). A CHANGED behaviour, not a loosened assertion
    (ruling (c)): `newproj`, on disk and not in the installed map, is heard
    at birth with no `false` written; `ghost`, with no maildir anywhere, is
    still refused loudly, written `false` and journaled, on the unchanged
    path, and a second listen does not retry it."""
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "newproj ghost")
    _settings.ensure()
    assert launcher_birth(NEW, "newproj ghost", unmapped.userhome) is not None
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    lines = banner(result)
    assert len(lines) == 2, result.stderr
    (heard,) = [x for x in lines if "newproj" in x]
    (refused,) = [x for x in lines if "ghost" in x]
    assert "newproj (from $SESSIONMAIL_SUBSCRIBE" in heard and "REFUSED" not in heard, heard
    assert "ghost REFUSED" in refused and "no such box 'ghost'" in refused, refused
    live = session_live_path(NEW, unmapped.userhome)
    assert json.loads(live.read_text()) == {"subscriptions": {"ghost": False}}
    (rec,) = [json.loads(x) for x in
              (session_dir(NEW, unmapped.userhome) / "log" / "settings.jsonl").read_text().splitlines()]
    assert rec["key"] == "subscriptions.ghost" and rec["value"] is False, rec
    assert rec["by"] == "pager listen" and "no such box 'ghost'" in rec["why"], rec
    assert unmapped.subscribed(NEW) == ["newproj"]
    assert f"newproj@{HOST}" in (unmapped.maps / "vmailbox").read_text()   # the regen mapped it
    again = listen()
    assert again.exit_code == EX_TIMEOUT, again.output
    assert json.loads(live.read_text()) == {"subscriptions": {"ghost": False}}
    assert unmapped.subscribed(NEW) == ["newproj"]


def test_the_tree_fallback_is_the_births_gate_only(unmapped):
    """Ruling (c), birth path only: `message subscription add`'s gate
    (`check_box` without `tree`) still asks the installed map, so it refuses
    `newproj` until a regen maps it; the birth's gate finds the maildir."""
    who = _subscribe.caller()
    with pytest.raises(_subscribe.Denied, match="no such box"):
        _subscribe.check_box("newproj", who)
    assert _subscribe.check_box("newproj", who, tree=True) == "newproj"
    assert _subscribe._resolve("newproj", None, tree=True) == unmapped.agents / "st" / "newproj"


def test_the_tree_fallback_finds_only_what_the_generator_would_map(unmapped):
    """The generator's tree test, no wider: a project under a store with NO
    marker, a project dir with no `new/`, a hidden dir and a plus name are
    not found; a plus name falls back on its base, as `sub_resolve` does;
    names fold to lower case, as the generator's do."""
    who = _subscribe.caller()
    _maildir(unmapped.agents / "nomark" / "loose")                   # no STORE_MARKER
    (unmapped.agents / "st" / "halfbox").mkdir()                       # no new/
    _maildir(unmapped.agents / "st" / ".hidden")
    for box in ("loose", "halfbox", ".hidden", "ghost"):
        with pytest.raises(_subscribe.Denied):
            _subscribe.check_box(box, who, tree=True)
    assert _subscribe._resolve("NewProj", None, tree=True) == unmapped.agents / "st" / "newproj"
    assert _subscribe._resolve("newproj+tag", None, tree=True) == unmapped.agents / "st" / "newproj"


# ── the public pre-launch verb: `pager settings install` ────────────────────

def test_pager_settings_install_writes_the_schema_and_template_and_never_mail_json(fresh):
    """On a HOME with no `~/.sessionkit/mail/settings/` at all, the verb makes the
    directory and installs both files, one line each, exit 0; it never
    writes `mail.json`, so the first PostToolBatch hook still creates it and
    still says the first-run note (SM-450 D3: `ensure()` says created)."""
    assert not _settings.path().parent.exists()
    result = install()
    assert result.exit_code == 0, result.output
    assert names_subscribe(schema_path())
    assert schema_path().read_text() == _settings.schema()
    assert template_path().read_text() == _settings.template()
    assert result.output.count("installed ") == 2, result.output
    assert not _settings.path().exists()
    _settings_now, created = _settings.ensure()
    assert created is True


def test_pager_settings_install_is_idempotent_and_replaces_a_hand_edit(fresh):
    """A second run writes nothing and says both are current, exit 0; a
    hand edit to the schema (the release owns it) is put back, alone."""
    assert install().exit_code == 0
    before = schema_path().stat().st_mtime_ns, template_path().stat().st_mtime_ns
    again = install()
    assert again.exit_code == 0, again.output
    assert "already current" in again.output and "installed" not in again.output, again.output
    assert (schema_path().stat().st_mtime_ns, template_path().stat().st_mtime_ns) == before
    schema_path().write_text("{}\n")
    fixed = install()
    assert fixed.exit_code == 0, fixed.output
    assert fixed.output.strip() == f"pager settings install: installed {schema_path()}"
    assert schema_path().read_text() == _settings.schema()


def test_pager_settings_install_names_what_it_could_not_write(fresh):
    """A `~/.sessionkit/mail/settings` that is a FILE cannot hold the schema: exit 2,
    each file named on stderr, the file left as found."""
    d = _settings.path().parent
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("not a directory\n")
    result = install()
    assert result.exit_code == 2, result.output
    assert f"could not write {d / _settings.SCHEMA}" in result.stderr, result.stderr
    assert f"could not write {d / _settings.INSTALLED_TEMPLATE}" in result.stderr, result.stderr
    assert d.read_text() == "not a directory\n"


def test_pager_settings_install_has_a_help_page():
    """The launchers' asks cite the verb by its page."""
    result = CliRunner().invoke(cli, ["settings", "install", "--help"], prog_name="pager")
    assert result.exit_code == 0, result.output
    for words in ("mail.schema.json", "SESSIONMAIL_SUBSCRIBE", "Idempotent", "never writes mail.json"):
        assert words in " ".join(result.output.split()), (words, result.output)


def test_a_launcher_that_installs_first_gives_the_first_seat_its_subscription(fresh, monkeypatch):
    """Route (i), end to end: the launcher runs `pager settings install`
    BEFORE its birth writer, so the writer finds the schema, writes the
    birth file, and the seat's first listen hears `atomnote` with no hook
    ever run under this HOME. What the xfail above waits on the launchers
    to do."""
    monkeypatch.setenv(pager.SUBSCRIBE_VAR, "atomnote")
    assert install().exit_code == 0
    assert launcher_birth(NEW, "atomnote", fresh.userhome) is not None
    result = listen()
    assert result.exit_code == EX_TIMEOUT, result.output
    assert fresh.subscribed(NEW) == ["atomnote"], banner(result)
    assert rows(fresh.valias)[f"atomnote@{HOST}"].endswith(f", {NEW}@{HOST}")
    assert not _settings.path().exists()


def test_a_hook_birth_with_no_mail_root_installs_nothing(fresh, monkeypatch):
    """`pager hook start` never makes a mail root (SM-498 R4), and a birth it
    refuses installs nothing either: no `~/.sessionkit/mail/settings/` on a host with
    no mail."""
    import shutil
    shutil.rmtree(fresh.agents)
    monkeypatch.setattr(_birth, "_detach", lambda *a, **k: None)
    data = {"hook_event_name": "SessionStart", "source": "startup", "session_id": NEW}
    result = CliRunner().invoke(cli, ["hook", "start"], input=json.dumps(data),
                                prog_name="pager")
    assert result.exit_code == 0, result.output
    assert not fresh.agents.exists()
    assert not _settings.path().parent.exists()
