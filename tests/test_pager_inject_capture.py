"""SM-518 do-004: full-content capture beside the size rows.

openspec/changes/pager-inject-log, "Full content is captured only when
switched on" (RULED: Q3/Q4/Q8, the seq names, the modes). The switch is
`capture`, layered host -> seat -> session through `_layer.read`, with `SESSIONMAIL_CAPTURE` winning both ways;
off by default. One file per injection, `capture/<seq>.txt`, holding the
exact emitted string; the row's `capture` field names it. 0700/0600 and a
`.gitignore` of `*`. At 64 MB, one `error` row and no more capture.

Hermetic: conftest's sandbox HOME; SESSIONMAIL_HOME under tmp_path.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _injectlog, _layer, _seat, _settings, pager
from sessionmail.pager import cli

SESSION = "11111111-2222-3333-4444-555555555555"
SEAT = "proj+task.worker"          # a seat name, so it has a seat layer (`_layer`)


# ── the rig ──────────────────────────────────────────────────────────────

def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def host(**keys) -> Path:
    return write(_settings.path(), {"version": 1, "hooks": True, **keys})


def seat_layer(**keys) -> Path:
    return write(_layer.settings_dir(SEAT) / "mail.json", keys)


def session_layer(**keys) -> Path:
    return write(_layer.session_settings_dir(SESSION) / "mail.json", keys)


@pytest.fixture
def seat(tmp_path, monkeypatch):
    home = tmp_path / "sessionmail"
    (home / "seats" / SEAT).mkdir(parents=True)
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home))
    monkeypatch.setenv("TM_AGENT_ADDRESS", SEAT)
    monkeypatch.delenv("CLAUDE_CODE_AGENT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv(_seat.PAGER_VAR, raising=False)
    monkeypatch.delenv(_injectlog.CAPTURE_VAR, raising=False)
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "_ps_one", lambda pid: ({}, {}))
    host()
    return _seat.resolve()


def log_dir() -> Path:
    d = _injectlog.log_dir(SESSION)
    assert d is not None
    return d


def rows() -> list[dict]:
    path = log_dir() / _injectlog.FILE
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def captured(row: dict) -> bytes:
    assert row["capture"] == f"capture/{row['seq']:06d}.txt"
    return (log_dir() / row["capture"]).read_bytes()


def batch():
    return CliRunner().invoke(cli, ["hook", "batch"], input=json.dumps(
        {"session_id": SESSION, "hook_event_name": "PostToolBatch", "tool_calls": []}))


def context(result) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def on(seat_obj) -> bool:
    return _injectlog.capture_on(seat_obj, SESSION)


# ── the switch: off by default; each layer; the env wins both ways ──────

def test_capture_is_off_by_default(seat):
    assert on(seat) is False
    r = batch()
    assert context(r), "the state line was said, so a row was written"
    [row] = rows()
    assert "capture" not in row
    assert not (log_dir() / _injectlog.CAPTURE_DIR).exists()


@pytest.mark.parametrize("layer", ["host", "seat", "session"])
def test_each_layer_turns_it_on(seat, layer):
    {"host": lambda: host(capture=True), "seat": lambda: seat_layer(capture=True),
     "session": lambda: session_layer(capture=True)}[layer]()
    assert on(seat) is True


def test_the_most_specific_layer_wins(seat):
    host(capture=True)
    seat_layer(capture=False)
    assert on(seat) is False
    session_layer(capture=True)
    assert on(seat) is True
    session_layer(capture=False)
    host(capture=False)
    seat_layer(capture=True)
    assert on(seat) is False, "session false over seat true"


def test_only_a_boolean_decides(seat):
    host(capture="yes")
    assert on(seat) is False
    session_layer(capture=1)
    assert on(seat) is False


@pytest.mark.parametrize("raw", ["on", "1", "yes", "TRUE", " true "])
def test_the_env_turns_it_on_over_every_layer(seat, monkeypatch, raw):
    host(capture=False)
    seat_layer(capture=False)
    session_layer(capture=False)
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, raw)
    assert on(seat) is True


@pytest.mark.parametrize("raw", ["off", "0", "no", "False"])
def test_the_env_turns_it_off_over_every_layer(seat, monkeypatch, raw):
    host(capture=True)
    seat_layer(capture=True)
    session_layer(capture=True)
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, raw)
    assert on(seat) is False


def test_an_unknown_env_value_leaves_the_layers_to_decide(seat, monkeypatch):
    session_layer(capture=True)
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "maybe")
    assert on(seat) is True


def test_an_unreadable_layer_decides_nothing_and_never_raises(seat):
    host(capture=True)
    write(_layer.session_settings_dir(SESSION) / "mail.json", "{not json")
    assert on(seat) is True, "the broken session layer is skipped; the host decides"


def test_the_schema_names_capture_as_a_boolean_with_its_variable():
    schema = json.loads(_settings.schema())
    prop = schema["properties"]["capture"]
    assert prop["type"] == "boolean" and prop["x-env"] == "SESSIONMAIL_CAPTURE"
    assert "capture" not in json.loads(_settings.template()), "missing means off"


@pytest.mark.parametrize("value", [True, False])
def test_the_schema_accepts_capture(value):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_settings.schema())
    jsonschema.validate({"version": 1, "hooks": False, "capture": value}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"capture": "on"}, schema)


# ── on: one file per injection, the exact bytes, named by seq ────────────

def test_on_the_file_is_the_bytes_and_the_row_names_it(seat, monkeypatch):
    """Spec scenario "On, the file is the bytes"."""
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    r = batch()
    s = context(r)
    [row] = rows()
    assert captured(row) == s.encode("utf-8")
    assert row["chars"] == len(s)


def test_the_file_is_utf8_and_exact_for_any_text(seat, monkeypatch):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    text = "héllo — wörld\n\tsecond line ✓\n"
    seq = _injectlog.write(SESSION, via="batch", s=text, seat=seat)
    [row] = rows()
    assert row["seq"] == seq and captured(row) == text.encode("utf-8")


def test_the_modes_and_the_gitignore(seat, monkeypatch):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    _injectlog.write(SESSION, via="batch", s="x", seat=seat)
    cd = log_dir() / _injectlog.CAPTURE_DIR
    assert cd.as_posix().endswith(f"/{SESSION}/log/mail/capture"), "the rework ruling's path"
    assert stat.S_IMODE(log_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(cd.stat().st_mode) == 0o700
    assert (cd / ".gitignore").read_text() == "*\n", "as mail/'s: ignore everything"
    for p in cd.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name


def test_every_via_is_captured_and_lifecycle_rows_never_are(seat, monkeypatch, capsys):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    out = pager.MonitorOut(spacing=0, log=True)
    out.say("pager:  a   monitor line")
    r = CliRunner().invoke(cli, ["hook", "stop"], input=json.dumps(
        {"hook_event_name": "Stop", "session_id": SESSION}))
    assert r.exit_code == 2
    _injectlog.lifecycle(SESSION, event="start", via="listen", seat=seat, box="b", timeout=None)
    got = rows()
    assert [r["via"] for r in got] == ["monitor", "stop", "listen"]
    assert captured(got[0]) == b"pager: a monitor line", "the normalised line, as printed"
    assert captured(got[1]) == r.stderr.rstrip("\n").encode()
    assert "capture" not in got[2], "a lifecycle row injects nothing"
    assert len(list((log_dir() / "capture").glob("*.txt"))) == 2


def test_capture_off_leaves_no_trace_even_after_it_was_on(seat, monkeypatch):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    _injectlog.write(SESSION, via="batch", s="on", seat=seat)
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "off")
    _injectlog.write(SESSION, via="batch", s="off", seat=seat)
    first, second = rows()
    assert "capture" in first and "capture" not in second
    assert sorted(p.name for p in (log_dir() / "capture").glob("*.txt")) == ["000001.txt"]


# ── never at the row's cost, never over another capture ──────────────────

def test_a_capture_that_cannot_be_written_costs_only_the_capture(seat, monkeypatch):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    d = log_dir()
    d.mkdir(parents=True)
    (d / _injectlog.CAPTURE_DIR).write_text("a FILE where capture/ goes")
    r = batch()
    assert r.exit_code == 0 and context(r)
    [row] = rows()
    assert "capture" not in row and row["chars"] == len(context(r))


def test_a_repeated_seq_never_overwrites_a_capture(seat, monkeypatch):
    """A torn `seq` file restarts the count (do-002 review note 2); the
    capture is made O_EXCL, so the first bytes stay and the row says none."""
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    _injectlog.write(SESSION, via="batch", s="first", seat=seat)
    (log_dir() / _injectlog.SEQ_FILE).write_text("garbage")
    assert _injectlog.write(SESSION, via="batch", s="second", seat=seat) == 1
    first, second = rows()
    assert captured(first) == b"first"
    assert "capture" not in second


# ── the 64 MB stop: one error row, then no more capture ──────────────────

def test_at_the_cap_one_error_row_and_no_more_capture(seat, monkeypatch):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    monkeypatch.setattr(_injectlog, "CAPTURE_CAP", 10)
    _injectlog.write(SESSION, via="batch", s="12345678", seat=seat)    # 8 of 10
    _injectlog.write(SESSION, via="prompt", s="abc", seat=seat)        # 11 > 10: full
    _injectlog.write(SESSION, via="batch", s="z", seat=seat)           # would fit: still full
    got = rows()
    assert [(r["event"], r["via"]) for r in got] == [
        ("inject", "batch"), ("inject", "prompt"), ("error", "prompt"), ("inject", "batch")]
    assert [r["seq"] for r in got] == [1, 2, 3, 4]
    assert captured(got[0]) == b"12345678"
    assert "capture" not in got[1] and "capture" not in got[3]
    err = got[2]
    assert err["what"] == "capture full"
    assert set(err) == {"ts", "tool", "event", "via", "seq", "sid", "seat", "pid", "what"}
    assert sorted(p.name for p in (log_dir() / "capture").glob("*.txt")) == ["000001.txt"]


def test_the_real_cap_is_64_mb():
    assert _injectlog.CAPTURE_CAP == 64 * 1024 * 1024


FULL_WRITER = """
import sys
from sessionmail import _injectlog
_injectlog.CAPTURE_CAP = 600
for i in range(int(sys.argv[2])):
    assert _injectlog.write(sys.argv[1], via=sys.argv[3], s="%s row %03d" % (sys.argv[3], i)) is not None
"""


def test_concurrent_writers_say_capture_full_exactly_once(seat, monkeypatch, tmp_path):
    monkeypatch.setenv(_injectlog.CAPTURE_VAR, "on")
    script = tmp_path / "full.py"
    script.write_text(FULL_WRITER)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(_injectlog.__file__).resolve().parents[1])
    procs = [subprocess.Popen([sys.executable, str(script), SESSION, "40", via], env=env)
             for via in ("monitor", "batch", "prompt")]
    for p in procs:
        assert p.wait(timeout=60) == 0
    got = rows()
    assert sum(1 for r in got if r["event"] == "inject") == 120, "every writer ran"
    assert sum(1 for r in got if r["event"] == "error" and r["what"] == "capture full") == 1
    files = list((log_dir() / "capture").glob("*.txt"))
    assert files and sum(p.stat().st_size for p in files) <= 600
    assert len(files) == sum(1 for r in got if "capture" in r)
