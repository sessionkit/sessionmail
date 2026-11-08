"""SM-969 do-005: sessionmail reads sessiontools' leaves only through
sessiontools' resolver (ST-266), never by spelling `~/.session`.

Three readers: `_layer.seats_root` (the seat layer), `_reach`'s owner of a
skipped map path, and the mail-guard hook's session dir (system python3,
stdlib only). Each is pinned twice: with the resolver present (a module
standing in for sessiontools 6c15df2's API, and the real one when the
installed sessiontools carries it) and absent (sessiontools before the 0.3.2
cut, which answers as it always did).

Hermetic: conftest's sandbox HOME; nothing outside tmp is read or written.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

from sessionmail import _layer, _reach, _subscription

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "mail-guard" / "hook.py"
SID = "11111111-2222-3333-4444-555555555555"


def _load_hook():
    spec = importlib.util.spec_from_file_location("mail_guard_share", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_st():
    from sessiontools import settings
    return settings


#: The installed sessiontools carries ST-266's resolver (0.3.2 cut and on).
HAS_ST266 = hasattr(_real_st(), "leaf_path")
needs_st266 = pytest.mark.skipif(not HAS_ST266, reason="sessiontools before ST-266 (0.3.2 cut)")
before_st266 = pytest.mark.skipif(HAS_ST266, reason="sessiontools carries ST-266")


class _Old:
    """sessiontools before ST-266: `session_root` and `sessions_root`, no
    `seats_root`."""

    def __init__(self, home: Path):
        self.home = home
        self.calls: list[str] = []

    def session_root(self, env=None):
        self.calls.append("session_root")
        return self.home / ".session"

    def sessions_root(self, env=None):
        return self.home / ".session" / "sessions"

    def session_dir(self, sid, env=None):
        return self.sessions_root(env) / sid


class _New(_Old):
    """sessiontools 6c15df2's API: `seats_root` and `leaf_path` too."""

    def __init__(self, home: Path, seats: Path, sessions: Path):
        super().__init__(home)
        self.seats, self.sessions = seats, sessions
        self.envs: list[object] = []

    def seats_root(self, env=None):
        self.envs.append(env)
        return self.seats

    def sessions_root(self, env=None):
        return self.sessions

    def leaf_path(self, leaf, env=None):
        return {"seats": self.seats, "sessions": self.sessions}[leaf], "new"


# ── _layer ───────────────────────────────────────────────────────────────────

def test_seats_root_is_sessiontools_seats_root_when_it_has_one(monkeypatch, tmp_path):
    fake = _New(tmp_path, tmp_path / "tools" / "state" / "seats", tmp_path / "s")
    monkeypatch.setattr(_layer, "_st", lambda: fake)
    env = {"HOME": str(tmp_path)}
    assert _layer.seats_root(env) == tmp_path / "tools" / "state" / "seats"
    assert fake.envs == [env], "ENV passes through to the resolver"
    assert "session_root" not in fake.calls, "the old root is never spelled"


def test_seats_root_is_todays_path_before_st266(monkeypatch, tmp_path):
    fake = _Old(tmp_path)
    monkeypatch.setattr(_layer, "_st", lambda: fake)
    assert _layer.seats_root() == tmp_path / ".session" / "seats"


def test_sessions_root_is_sessiontools(monkeypatch, tmp_path):
    fake = _New(tmp_path, tmp_path / "a", tmp_path / "b")
    monkeypatch.setattr(_layer, "_st", lambda: fake)
    assert _layer.sessions_root() == tmp_path / "b"


def test_no_sessiontools_is_a_layer_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sessiontools", None)
    with pytest.raises(_layer.LayerError):
        _layer.seats_root()
    with pytest.raises(_layer.LayerError):
        _layer.sessions_root()


@before_st266
def test_the_installed_sessiontools_answers_todays_seats(tmp_path):
    assert _layer.seats_root({"HOME": str(tmp_path)}) == tmp_path / ".session" / "seats"


@needs_st266
@pytest.mark.parametrize("case", ["old-real", "old-link-to-new", "new-only", "neither"])
def test_the_installed_sessiontools_rule_reaches_the_seat_layer(tmp_path, case):
    """ST-266's rule, through `_layer`: old while real, else new, else the
    rule's own fresh answer for this HOME (fresh-old at 0.3.2, where seats
    keep the old default until tools' wave 4; fresh-new once the rule moves
    it). That answer is asked of sessiontools BEFORE the layer runs, and is
    one of the two leaves, never a third path."""
    st = _real_st()
    env = {"HOME": str(tmp_path)}
    old, new = st.leaf_old("seats", env), st.leaf_new("seats", env)
    fresh, source = st.leaf_path("seats", env)
    assert source in ("fresh-old", "fresh-new") and fresh in (old, new), (fresh, source)
    if case in ("old-link-to-new", "new-only"):
        new.mkdir(parents=True)
    if case == "old-real":
        old.mkdir(parents=True)
    if case == "old-link-to-new":
        old.parent.mkdir(parents=True)
        old.symlink_to(new)
    want = (new if case in ("old-link-to-new", "new-only")
            else old if case == "old-real" else fresh)
    assert _layer.seats_root(env) == want
    assert _layer.seat_dir("a+b", env) == want / "a+b", "reader and writer agree"


# ── _reach: a skipped map path is owned under EITHER leaf ────────────────────

def _scan_with(skipped):
    return _subscription.Scan(entries=(), skipped=tuple(skipped))


@pytest.mark.parametrize("split", [False, True], ids=["one-parent", "split-leaves"])
def test_a_skipped_map_is_owned_under_the_leaf_it_was_read_from(monkeypatch, tmp_path, split):
    """After ST-266 seats and sessions need not share a parent: each skipped
    path is matched against the leaf sessiontools names, not seats' parent."""
    seats = tmp_path / ("tools/state/seats" if split else ".session/seats")
    sessions = tmp_path / ".session" / "sessions"
    fake = _New(tmp_path, seats, sessions)
    monkeypatch.setattr(_layer, "_st", lambda: fake)
    skipped = [(seats / "a+b" / "settings" / "mail.json", "not JSON"),
               (sessions / "S1" / "settings" / "mail.json", "not JSON")]
    monkeypatch.setattr(_subscription, "scan", lambda *a, **k: _scan_with(skipped))
    (tmp_path / "root").mkdir()
    g = _reach.graph(tmp_path / "root", live=[], env={"HOME": str(tmp_path)})
    assert g.dropped == {"a+b": 1, "s1": 1}


def test_no_sessiontools_owns_no_map_path_and_does_not_raise(monkeypatch, tmp_path):
    def gone():
        raise _layer.LayerError(_layer.NOT_INSTALLED)
    monkeypatch.setattr(_layer, "_st", gone)
    skipped = [(tmp_path / ".session" / "seats" / "a+b" / "settings" / "mail.json", "x")]
    monkeypatch.setattr(_subscription, "scan", lambda *a, **k: _scan_with(skipped))
    (tmp_path / "root").mkdir()
    assert _reach.graph(tmp_path / "root", live=[], env={"HOME": str(tmp_path)}).dropped == {}


# ── the mail-guard hook (stdlib): its session dir ────────────────────────────

STUB = """#!/bin/sh
echo "$*" >> "{calls}"
{body}
"""


def _stub(tmp_path: Path, body: str) -> dict[str, str]:
    """A `session` on PATH whose body is BODY: the env to run under."""
    d = tmp_path / "stub-bin"
    d.mkdir(exist_ok=True)
    f = d / "session"
    f.write_text(STUB.format(calls=tmp_path / "calls", body=body))
    f.chmod(0o755)
    return {"HOME": str(tmp_path / "home"), "PATH": f"{d}:/usr/bin:/bin"}


#: sessiontools' NEW sessions leaf: step 4's answer on a fresh HOME.
NEW_SESSIONS = Path(".sessionkit", "tools", "state", "sessions")


def _answers(path: str, source: str = "new") -> str:
    return f"echo '{json.dumps({'path': path, 'source': source})}'"


@pytest.fixture
def hook(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_sessiontools", lambda: None)   # system python3: none
    return mod


def test_hook_takes_an_importable_st266_resolver_first(monkeypatch, tmp_path):
    mod = _load_hook()
    fake = _New(tmp_path, tmp_path / "x", tmp_path / "tools" / "state" / "sessions")
    monkeypatch.setattr(mod, "_sessiontools", lambda: fake)
    env = _stub(tmp_path, _answers("/elsewhere"))
    assert mod.log_dir(SID, env) == str(tmp_path / "tools/state/sessions" / SID / "log" / "mail")
    assert not (tmp_path / "calls").exists(), "the verb is not run when the import answers"


@pytest.mark.parametrize("source", ["override", "old", "new", "fresh-old", "fresh-new"])
def test_hook_asks_session_path_when_it_cannot_import(hook, tmp_path, source):
    want = str(tmp_path / "moved" / SID)
    env = _stub(tmp_path, _answers(want, source))
    assert hook.log_dir(SID, env) == os.path.join(want, "log", "mail")
    assert (tmp_path / "calls").read_text() == f"path sessions {SID} --json\n"


@pytest.mark.parametrize("body", [
    pytest.param("echo \"session: unknown verb 'path'\" >&2; exit 2", id="no-verb"),
    pytest.param(_answers("/m/x") + "; exit 1", id="nonzero"),
    pytest.param("echo not json", id="not-json"),
    pytest.param(_answers("/m/x", "elsewhere"), id="unknown-source"),
    pytest.param(_answers("m/x"), id="relative"),
    pytest.param("echo '[\"/m/x\"]'", id="not-an-object"),
    pytest.param("echo '{\"source\": \"new\"}'", id="no-path"),
])
def test_hook_falls_back_to_the_leaf_rule_when_the_verb_does_not_answer(hook, tmp_path, body):
    """Step 4 on a fresh HOME: the NEW leaf (SM-1005 fix-002; the rule's
    cases are tests/test_mail_guard_leaf_rule.py's)."""
    env = _stub(tmp_path, body)
    assert hook.log_dir(SID, env) == str(tmp_path / "home" / NEW_SESSIONS / SID / "log" / "mail")


def test_hook_with_no_session_on_path_is_the_leaf_rule(hook, tmp_path):
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "empty")}
    assert hook.log_dir(SID, env) == str(tmp_path / NEW_SESSIONS / SID / "log" / "mail")
    (tmp_path / ".session" / "sessions").mkdir(parents=True)
    assert hook.log_dir(SID, env) == str(tmp_path / ".session" / "sessions" / SID / "log" / "mail")


def test_hook_does_not_wait_on_a_hung_verb(hook, monkeypatch, tmp_path):
    import time
    monkeypatch.setattr(hook, "PATH_TIMEOUT", 0.5)
    env = _stub(tmp_path, "exec sleep 30")
    t = time.monotonic()
    assert hook.log_dir(SID, env).startswith(str(tmp_path / "home" / NEW_SESSIONS))
    assert time.monotonic() - t < 10


def test_hook_takes_an_older_importable_sessiontools_after_the_verb(monkeypatch, tmp_path):
    """An importable sessiontools WITHOUT the resolver: the verb first (it
    may be newer), then that module's own `session_dir` (SESSIONS_DIR)."""
    mod = _load_hook()
    fake = _Old(tmp_path / "o")
    monkeypatch.setattr(mod, "_sessiontools", lambda: fake)
    env = _stub(tmp_path, _answers(str(tmp_path / "v" / SID)))
    assert mod.session_dir(SID, env) == str(tmp_path / "v" / SID)
    env = _stub(tmp_path, "exit 2")
    assert mod.session_dir(SID, env) == str(tmp_path / "o" / ".session" / "sessions" / SID)


def test_hook_resolver_bug_falls_through(monkeypatch, tmp_path):
    mod = _load_hook()
    broken = types.SimpleNamespace(leaf_path=None,
                                   session_dir=lambda *a: (_ for _ in ()).throw(RuntimeError("bug")))
    monkeypatch.setattr(mod, "_sessiontools", lambda: broken)
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "empty")}
    assert mod.session_dir(SID, env) == str(tmp_path / NEW_SESSIONS / SID)


def test_hook_and_sessionmail_agree_under_the_installed_sessiontools(monkeypatch, tmp_path):
    """The deny row lands where `_injectlog` writes, whichever sessiontools
    is installed: both go through sessiontools when the hook can import it."""
    from sessionmail import _injectlog
    mod = _load_hook()
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "empty")}
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mod.log_dir(SID, env) == str(_injectlog.log_dir(SID, env))
    env["SESSIONS_DIR"] = str(tmp_path / "sd")
    assert mod.log_dir(SID, env) == str(_injectlog.log_dir(SID, env)) == str(
        tmp_path / "sd" / SID / "log" / "mail")


def test_the_deny_rows_ms_does_not_count_finding_the_session_dir(hook, tmp_path):
    """`ms` is hook entry to the emit (docs/pager-inject-log.md). Finding the
    session dir runs AFTER the emit and may run `session path`; a slow verb
    must not show in the row. The row still lands where the verb said."""
    import time
    moved = tmp_path / "moved" / SID
    env = _stub(tmp_path, "sleep 1; " + _answers(str(moved)))
    payload = {"tool_name": "Bash", "session_id": SID, "tool_input": {"command": "x"}}
    assert hook.log_deny(payload, "mail-guard: no", time.monotonic(), env) is not None
    rows = (moved / "log" / "mail" / "pager.jsonl").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["ms"] < 500, rows[0]
