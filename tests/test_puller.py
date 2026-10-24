"""The host-to-host pull, `python -m sessionmail._puller` (design D1, D2, D5).

Hermetic: no real IMAP, SMTP, postfix, notmuch or tailscale. IMAP is a fake
server behind imaplib itself (`WireIMAP`), so every command the puller sends
is recorded as it goes on the wire; the listener, the map and the secret are
stubs or scratch scripts; the settings home is the suite's sandbox.
"""

from __future__ import annotations

import fcntl
import imaplib
import json
import os
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from sessionmail import _operator, _puller, _pull_imap, _settings

REQUIRED_VARS = {
    "pull.imap_host": "SESSIONMAIL_PULL_IMAP_HOST",
    "pull.login": "SESSIONMAIL_PULL_LOGIN",
    "pull.secret_command": "SESSIONMAIL_PULL_SECRET_COMMAND",
    "pull.relay_domain": "SESSIONMAIL_PULL_RELAY_DOMAIN",
    "pull.listener": "SESSIONMAIL_PULL_LISTENER",
    "pull.vmailbox": "SESSIONMAIL_PULL_VMAILBOX",
}

OWN_IP = "192.0.2.10"
OTHER_IP = "198.51.100.7"
SESSION = "6f1d2c3b-4a59-4e1f-8c7d-2b3a4c5d6e7f"
WHEN = "Sun, 09 Jun 2030 12:34:56 +0000"


def script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    """A full config, by variables, plus the named keys the Config also
    reads (root, notmuch, HOME), each pointed into the scratch dir."""
    home = tmp_path / "user"
    home.mkdir()
    boxes = tmp_path / "pull.boxes"
    boxes.write_text("# boxes\nalpha\n\nbeta  # a comment\nnot a box\n")
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SESSIONMAIL_NOTMUCH": os.environ["SESSIONMAIL_NOTMUCH"],
        "SESSIONMAIL_ROOT": str(tmp_path / "mail" / "agents"),
        "SESSIONMAIL_PULL_IMAP_HOST": "imap.example.org",
        "SESSIONMAIL_PULL_LOGIN": "{box}*master",
        "SESSIONMAIL_PULL_SECRET_COMMAND": "printf s3cret",
        "SESSIONMAIL_PULL_RELAY_DOMAIN": "relay.example",
        "SESSIONMAIL_PULL_LISTENER": "127.0.0.1:2525",
        "SESSIONMAIL_PULL_VMAILBOX": "hash:/nonexistent/vmailbox",
        "SESSIONMAIL_PULL_BOXES_FILE": str(boxes),
        "SESSIONMAIL_PULL_STATE_DIR": str(tmp_path / "state"),
        "SESSIONMAIL_PULL_LOCAL_DOMAIN": "host-a",
        "SESSIONMAIL_PULL_OWN_IPS": OWN_IP,
    }


@pytest.fixture
def cfg(env) -> _puller.Config:
    got = _puller.config(env)
    assert isinstance(got, _puller.Config), got
    return got


def host_file(data: dict) -> Path:
    p = _operator.settings_home() / _settings.RELATIVE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "hooks": False, **data}) + "\n")
    return p


# ── config: the one resolver ───────────────────────────────────────────────

def test_no_config_names_every_required_key_and_its_variable():
    got = _puller.config({})
    assert isinstance(got, _puller.Missing)
    assert got.keys == _puller.REQUIRED == tuple(REQUIRED_VARS)
    why = got.why()
    assert why.startswith("REFUSED - pull is not configured: ")
    for key, var in REQUIRED_VARS.items():
        assert f"{key} ({var})" in why
        assert _settings.KEYS[key].default is None      # no private default to fall back on


def test_each_required_key_alone_is_named(env):
    for key, var in REQUIRED_VARS.items():
        got = _puller.config({k: v for k, v in env.items() if k != var})
        assert isinstance(got, _puller.Missing)
        assert got.keys == (key,)


def test_a_full_config_resolves(cfg, tmp_path):
    assert cfg.imap_host == "imap.example.org" and cfg.imap_port == 993
    assert cfg.login.format(box="alpha") == "alpha*master"
    assert cfg.listener == ("127.0.0.1", 2525)
    assert cfg.state_dir == tmp_path / "state"
    assert cfg.local_domain == "host-a"
    assert cfg.own_ips == (OWN_IP,)
    assert cfg.mail_root == tmp_path / "mail"
    # general delivery and the audit are off unless named
    assert (cfg.unsorted_box, cfg.audit_box, cfg.audit_to) == ("", "", ())
    assert cfg.label == "dev.sessionkit.mail.puller"


def test_the_settings_file_alone_is_a_config(env, tmp_path):
    """The LaunchAgent's case: no variable at all, every key from the file."""
    host_file({"pull": {"imap_host": "imap.example.org", "login": "{box}@host-a",
                        "secret_command": "printf s3cret", "relay_domain": "relay.example",
                        "listener": "[::1]:2525", "vmailbox": "hash:/x/vmailbox",
                        "own_ips": ["192.0.2.1", "192.0.2.1", "2001:db8::1"],
                        "audit_box": "_audit", "audit_to": ["alpha@example.org"],
                        "state_dir": "~/puller-state"}})
    got = _puller.config({"HOME": env["HOME"], "PATH": "/usr/bin:/bin"})
    assert isinstance(got, _puller.Config), got
    assert got.listener == ("::1", 2525)
    assert got.own_ips == ("192.0.2.1", "2001:db8::1")
    assert got.audit_to == ("alpha@example.org",)
    # `~` is the settings home, never $HOME
    assert got.state_dir == _operator.settings_home() / "puller-state"


@pytest.mark.parametrize("var,value,key", [
    ("SESSIONMAIL_PULL_LISTENER", "127.0.0.1", "pull.listener"),
    ("SESSIONMAIL_PULL_LISTENER", "127.0.0.1:99999", "pull.listener"),
    ("SESSIONMAIL_PULL_LOGIN", "master", "pull.login"),
    ("SESSIONMAIL_PULL_LOGIN", "{box}{other}", "pull.login"),
    ("SESSIONMAIL_PULL_IMAP_PORT", "imaps", "pull.imap_port"),
    ("SESSIONMAIL_PULL_IMAP_PORT", "70000", "pull.imap_port"),
    ("SESSIONMAIL_PULL_LOGIN_GATE", "0", "pull.login_gate"),
    ("SESSIONMAIL_PULL_REISSUE_SECONDS", "0", "pull.reissue_seconds"),
    ("SESSIONMAIL_PULL_LABEL", "has space", "pull.label"),
    ("SESSIONMAIL_PULL_UNSORTED_BOX", "../up", "pull.unsorted_box"),
])
def test_an_unusable_value_is_refused_by_name(env, var, value, key):
    got = _puller.config({**env, var: value})
    assert isinstance(got, _puller.Missing)
    assert [k for k, _why in got.invalid] == [key]
    assert f"{key} ({var}): " in got.why()


def test_the_audit_needs_its_recipients(env):
    got = _puller.config({**env, "SESSIONMAIL_PULL_AUDIT_BOX": "_audit"})
    assert isinstance(got, _puller.Missing) and got.keys == ("pull.audit_to",)
    got = _puller.config({**env, "SESSIONMAIL_PULL_AUDIT_BOX": "_audit",
                          "SESSIONMAIL_PULL_AUDIT_TO": "alpha@example.org, beta@example.org"})
    assert got.audit_to == ("alpha@example.org", "beta@example.org")


def test_config_asks_no_one(env, monkeypatch):
    """config() runs nothing: tailscale is main's, never the resolver's."""
    def boom(*a, **k):
        raise AssertionError("config() ran a command")
    monkeypatch.setattr(_puller, "ask_tailscale", boom)
    monkeypatch.setattr(_puller.subprocess, "run", boom)
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_PULL_OWN_IPS"}
    got = _puller.config(env)
    assert isinstance(got, _puller.Config) and got.own_ips is None


def test_the_state_dir_default_reads_no_variable(env):
    """One puller per state dir: the default must not move with
    SESSIONMAIL_HOME (or $HOME), or a hand run would take a second lock."""
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_PULL_STATE_DIR"}
    plain = _puller.config(env)
    moved = _puller.config({**env, "SESSIONMAIL_HOME": "/elsewhere/home", "HOME": "/elsewhere"})
    assert plain.state_dir == moved.state_dir == \
        _operator.settings_home() / ".sessionmail" / "puller"
    assert "SESSIONMAIL_HOME" in os.environ                 # the conftest sets it; still unread


def test_differences_compare_resolved_values(env, tmp_path):
    """N2: `~/.mail` in the file and its expanded path in a variable are the
    same value, so they are no difference."""
    host_file({"root": "~/.mail"})
    job = {k: v for k, v in env.items() if k != "SESSIONMAIL_ROOT"}
    shell = {**job, "SESSIONMAIL_ROOT": str(_operator.settings_home() / ".mail" / "agents")}
    a, b = _puller.config(job), _puller.config(shell)
    assert a.mail_root == _operator.settings_home() / ".mail"
    assert a.differences(b) == []


def test_a_legacy_root_set_only_in_the_shell_is_a_difference(env):
    """N1: the older root spelling is READ (as the kit's other readers read
    it), so a shell that sets it and a job that does not are told apart,
    and the difference names the variable. Never silently ignored."""
    job = {k: v for k, v in env.items() if k != "SESSIONMAIL_ROOT"}
    shell = {**job, "SESSION_MAIL_ROOT": "/srv/legacy/agents"}
    a, b = _puller.config(shell), _puller.config(job)
    assert a.mail_root == Path("/srv/legacy")
    diffs = a.differences(b)
    assert [d.field for d in diffs] == ["mail_root"]
    assert diffs[0].key == "root" and "SESSION_MAIL_ROOT" in diffs[0].variables
    assert diffs[0].mine == Path("/srv/legacy")


def test_differences_name_a_pull_variable(env):
    a = _puller.config(env)
    b = _puller.config({**env, "SESSIONMAIL_PULL_LABEL": "org.example.other"})
    (d,) = a.differences(b)
    assert (d.field, d.key, d.variables) == ("label", "pull.label", ("SESSIONMAIL_PULL_LABEL",))
    # the run flag and the environment are not config
    import dataclasses
    assert a.differences(dataclasses.replace(a, dry_run=True, env={"X": "1"})) == []


def test_every_field_names_its_source():
    import dataclasses
    names = {f.name for f in dataclasses.fields(_puller.Config)
             if f.compare and f.name != "dry_run"}
    assert names == set(_puller.FIELD_SOURCES)


# ── the -m door: refusal before any state ──────────────────────────────────

RUN_MODULE = r"""
import runpy, sys
from pathlib import Path
from sessionmail import _operator
_operator.settings_home = lambda: Path(sys.argv[1])
sys.argv = ["python -m sessionmail._puller", *sys.argv[2:]]
runpy.run_module("sessionmail._puller", run_name="__main__")
"""


def test_the_module_door_refuses_with_exit_2_before_any_state(tmp_path):
    """SC1, the puller half: `python -m sessionmail._puller --once` with no
    pull config exits 2 naming every missing key and its variable, and makes
    no state dir and no lock. The child's settings home is a scratch dir (in
    a real job it is the account's home)."""
    settings_home = tmp_path / "acct"
    settings_home.mkdir()
    child_env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-c", RUN_MODULE, str(settings_home), "--once"],
                          env=child_env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2, proc.stderr
    for key, var in REQUIRED_VARS.items():
        assert f"{key} ({var})" in proc.stderr
    assert list(settings_home.rglob("*")) == []          # no state dir, no lock


def test_main_refuses_before_the_state_dir(monkeypatch, capsys):
    connects = []
    monkeypatch.setattr(_puller.di, "connect", lambda *a, **k: connects.append(a))
    assert _puller.main(["--once"], env={}) == 2
    assert "REFUSED - pull is not configured" in capsys.readouterr().err
    assert not (_operator.settings_home() / ".sessionmail").exists()
    assert connects == []


def test_help_names_the_config_and_the_exits():
    proc = subprocess.run([sys.executable, "-m", "sessionmail._puller", "--help"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "pull.* keys" in proc.stdout and "Exit 2" in proc.stdout


# ── the classifiers ────────────────────────────────────────────────────────

def mail(*headers: str, body: str = "hello\n") -> bytes:
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode()


def received(ip: str, rcpt: str) -> str:
    return (f"Received: from host-a (host-a [{ip}])\r\n\tby imap.example.org with ESMTP\r\n"
            f"\tfor <{rcpt}>; {WHEN}")


def test_head_of_and_top_received():
    raw = mail(received(OWN_IP, "alpha@relay.example"), "Received: from older; x",
               "Message-ID: <a@example.org>", body="Received: in the body\n")
    head = _puller.head_of(raw)
    assert b"in the body" not in head and head.endswith(b"\n")
    top = _puller.top_received(head)
    assert OWN_IP.encode() in top and b"older" not in top
    assert _puller.top_received(b"Subject: none\n") == b""


def test_is_own_leg_uses_the_configured_relay_domain():
    head = _puller.head_of(mail(received(OWN_IP, "alpha@relay.example")))
    assert _puller.is_own_leg(head, {OWN_IP}, "relay.example")
    assert not _puller.is_own_leg(head, {OWN_IP}, "relay.other")      # another domain
    assert not _puller.is_own_leg(head, {OTHER_IP}, "relay.example")  # another host's leg
    direct = _puller.head_of(mail(received(OWN_IP, "alpha@imap.example.org")))
    assert not _puller.is_own_leg(direct, {OWN_IP}, "relay.example")  # sent straight there


def test_orig_to_and_local_rcpt():
    head = _puller.head_of(mail("X-Original-To: alpha+notes@relay.example",
                                "X-Original-To: older@relay.example"))
    assert _puller.orig_to(head) == "alpha+notes"
    assert _puller.local_rcpt("alpha", head) == "alpha+notes"     # the tag is kept
    assert _puller.local_rcpt("beta", head) == "beta"             # not this box's address
    assert _puller.orig_to(b"Subject: x\n") == ""
    odd = _puller.head_of(mail("X-Original-To: <a;b@relay.example>"))
    assert _puller.orig_to(odd) == ""
    assert _puller.base_box("alpha+notes") == "alpha"


def test_read_boxes(tmp_path):
    p = tmp_path / "boxes"
    p.write_text("beta\nalpha # comment\n# all comment\n\nalpha\nnot a box\n-dash\n")
    assert _puller.read_boxes(p) == ["alpha", "beta"]
    assert _puller.read_boxes(tmp_path / "absent") == []


# ── first_base over a fake imap object ─────────────────────────────────────

class SearchImap:
    def __init__(self, younger, since=("OK", [b""])):
        self.younger, self.since, self.calls = younger, since, []

    def uid(self, *args):
        self.calls.append(args)
        if args[1] == "YOUNGER":
            if isinstance(self.younger, Exception):
                raise self.younger
            return self.younger
        return self.since


def test_first_base_takes_the_oldest_in_the_window(cfg):
    imap = SearchImap(("OK", [b"4 7 9 12"]))
    base, why = _puller.first_base(cfg, imap, 10)
    assert base == 4 and "3 in the last 24h" in why
    assert imap.calls == [("SEARCH", "YOUNGER", "86400")]


def test_first_base_falls_back_to_since(cfg):
    imap = SearchImap(imaplib.IMAP4.error("unknown"), ("OK", [b"5"]))
    base, why = _puller.first_base(cfg, imap, 10)
    assert base == 5 and "SINCE" in why
    assert imap.calls[1][:2] == ("SEARCH", "SINCE")
    assert re.fullmatch(r"\d{1,2}-[A-Z][a-z]{2}-\d{4}", imap.calls[1][2])


def test_first_base_empty_or_off_is_uidnext(cfg):
    import dataclasses
    assert _puller.first_base(cfg, SearchImap(("OK", [b""])), 10)[0] == 10
    off = dataclasses.replace(cfg, backfill_seconds=0)
    imap = SearchImap(("OK", [b"1"]))
    assert _puller.first_base(off, imap, 10) == (10, "backfill off: from UIDNEXT")
    assert imap.calls == []


# ── handle(): the decisions ────────────────────────────────────────────────

@pytest.fixture
def stubs(monkeypatch, tmp_path):
    """local_dir, held and inject stubbed; returns their records."""
    rec = {"injected": [], "held": set(), "dirs": {"alpha": tmp_path / "alpha",
                                                   SESSION: tmp_path / "sess"}}
    monkeypatch.setattr(_puller, "local_dir", lambda cfg, box: rec["dirs"].get(
        _puller.base_box(box)))
    monkeypatch.setattr(_puller, "held", lambda cfg, d, mid: (d, mid) in rec["held"])
    monkeypatch.setattr(_puller, "inject",
                        lambda cfg, rcpt, raw, head, tag: rec["injected"].append((rcpt, tag)))
    monkeypatch.setattr(_puller, "OWN_GRACE", 0.0)
    return rec


def puller(cfg, box="alpha", **kw):
    p = _puller.BoxPuller(cfg, box, "s3cret", {OWN_IP}, once=True, from_start=False, **kw)
    p.box_dir = _puller.local_dir(cfg, box)
    p.validity = 7
    return p


def test_handle_pulls_new_mail(cfg, stubs):
    p = puller(cfg)
    v = p.handle(3, mail("Message-ID: <n@example.org>", "X-Original-To: alpha+x@relay.example"),
                 None)
    assert v.startswith("PULLED") and "from the backlog" in v
    assert stubs["injected"] == [("alpha+x", "imap.example.org box alpha uid 7/3")]
    assert p.counts["pulled"] == 1


def test_handle_skips_mail_already_here(cfg, stubs, tmp_path):
    stubs["held"].add((tmp_path / "alpha", "h@example.org"))
    v = puller(cfg).handle(3, mail("Message-ID: <h@example.org>"), None)
    assert v == "already here, skipped" and stubs["injected"] == []


def test_handle_own_leg(cfg, stubs, tmp_path):
    raw = mail(received(OWN_IP, "alpha@relay.example"), "Message-ID: <o@example.org>")
    stubs["held"].add((tmp_path / "alpha", "o@example.org"))
    assert puller(cfg).handle(3, raw, None) == "own leg, here, skipped"
    # no local copy: the own leg is pulled after all (a bounce here)
    stubs["held"].clear()
    assert "(own leg NOT here)" in puller(cfg).handle(4, raw, None)
    # an own leg for a box with no local maildir is skipped
    stubs["dirs"].pop("alpha")
    assert puller(cfg).handle(5, raw, None) == "own leg, no local maildir to check, skipped"


def test_handle_dry_run_injects_nothing(cfg, stubs):
    import dataclasses
    dry = dataclasses.replace(cfg, dry_run=True)
    v = puller(dry).handle(3, mail("Message-ID: <d@example.org>"), None)
    assert "WOULD PULL" in v and stubs["injected"] == []


def test_general_delivery_takes_only_session_mail_held_here(cfg, stubs):
    import dataclasses
    gen = dataclasses.replace(cfg, unsorted_box="_unsorted")
    p = puller(gen, "_unsorted")
    assert p.general
    v = p.handle(1, mail(f"X-Original-To: {SESSION}@relay.example", "Message-ID: <s@x>"), None)
    assert v.startswith(f"PULLED as {SESSION}")
    assert p.handle(2, mail("X-Original-To: alpha@relay.example"), None) == \
        "for alpha: not a session, left"
    assert p.handle(3, mail("X-Original-To: 0123abcd@relay.example"), None) == \
        "for 0123abcd: not on this host, left"
    assert [r for r, _t in stubs["injected"]] == [SESSION]


def test_the_audit_alerts_on_this_hosts_mail_with_no_copy(cfg, stubs, tmp_path):
    import dataclasses
    log = tmp_path / "sendmail.log"
    fake = script(tmp_path / "sendmail", f"import sys\nopen({str(log)!r}, 'ab').write("
                  "(' '.join(sys.argv[1:]) + '\\n').encode() + sys.stdin.buffer.read())\n")
    aud = dataclasses.replace(cfg, audit_box="_audit", audit_to=("alpha@example.org",),
                              audit_sendmail=str(fake))
    entries = [{"ip": OWN_IP, "box": "alpha", "to": "alpha@host-b", "mid": "m1@x", "t": 1},
               {"ip": OTHER_IP, "box": "alpha", "to": "alpha@host-b", "mid": "m2@x", "t": 1}]
    raw = mail("Message-ID: <digest@x>", body=json.dumps({"entries": entries}))
    v = puller(aud, "_audit").handle(1, raw, None)
    assert v == "audit: 2 entries, 1 sent here, 1 with no copy here: mailed alpha@example.org"
    sent = log.read_text()
    assert "-f mailhost-audit@host-a alpha@example.org" in sent
    assert "X-Mailhost-Audit: alert" in sent and "<m1@x>" in sent and "m2@x" not in sent
    dry = dataclasses.replace(aud, dry_run=True)
    assert puller(dry, "_audit").handle(2, raw, None).endswith("dry run, not mailed")


# ── held(): notmuch through the kit's profile, never raising ───────────────

def test_held_asks_notmuch_through_the_profile(cfg, tmp_path):
    import dataclasses
    box = tmp_path / "mail" / "alpha"
    (box / "new").mkdir(parents=True)
    (box / "cur").mkdir()
    profile = Path(cfg.env["HOME"]) / ".config" / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    profile.write_text("[database]\n")
    argv_log = tmp_path / "notmuch.argv"
    fake = script(tmp_path / "notmuch", f"import json, sys\nopen({str(argv_log)!r}, 'w')"
                  f".write(json.dumps(sys.argv[1:]))\nprint({str(box / 'cur' / 'x')!r})\n")
    c = _puller.config({**cfg.env, "SESSIONMAIL_NOTMUCH": str(fake)})
    assert _puller.held(c, box, "a@example.org")
    assert json.loads(argv_log.read_text()) == [
        f"--config={profile}", "search", "--output=files", "id:a@example.org"]
    # not under this box: the answer is no (and the scan finds nothing)
    assert not _puller.held(c, tmp_path / "mail" / "beta", "a@example.org")


@pytest.mark.parametrize("why", ["missing binary", "timeout", "no profile"])
def test_held_falls_through_to_the_scan_when_notmuch_cannot_answer(cfg, tmp_path, why):
    import dataclasses
    box = tmp_path / "mail" / "alpha"
    (box / "new").mkdir(parents=True)
    (box / "cur").mkdir()
    (box / "new" / "1.host-a").write_bytes(mail("Message-ID: <here@example.org>"))
    env = dict(cfg.env)
    if why != "no profile":
        profile = Path(env["HOME"]) / ".config" / "notmuch" / "sessionkit" / "config"
        profile.parent.mkdir(parents=True)
        profile.write_text("[database]\n")
    if why == "missing binary":
        env["SESSIONMAIL_NOTMUCH"] = str(tmp_path / "no-such-notmuch")
    elif why == "timeout":
        env["SESSIONMAIL_NOTMUCH"] = str(script(tmp_path / "slow", "import time\ntime.sleep(30)\n"))
        env["SESSIONMAIL_NOTMUCH_TIMEOUT"] = "0.3"
    c = _puller.config(env)
    assert _puller.held(c, box, "here@example.org")             # found by the scan
    assert not _puller.held(c, box, "gone@example.org")         # and no exception either way


def test_local_dir_reads_the_map_and_never_raises(cfg, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script(bin_dir / "postmap", "import sys\n"
           "print('agents/alpha/' if sys.argv[2] == 'alpha@host-a' else '', end='')\n"
           "sys.exit(0 if sys.argv[2] == 'alpha@host-a' else 1)\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert _puller.local_dir(cfg, "alpha+tag") == tmp_path / "mail" / "agents" / "alpha"
    assert _puller.local_dir(cfg, "beta") is None
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert _puller.local_dir(cfg, "alpha") is None              # no postmap: None, no raise


# ── the wire: a fake IMAP server behind imaplib ────────────────────────────

class Mailbox:
    def __init__(self, messages: dict[int, bytes], validity: int = 7):
        self.messages, self.validity = messages, validity

    @property
    def uidnext(self) -> int:
        return max(self.messages, default=0) + 1


class WireIMAP(imaplib.IMAP4):
    """imaplib.IMAP4 over an in-memory server. Every line the puller sends
    goes through `send`, is recorded in SENT, and is answered here."""

    BOXES: dict[str, Mailbox] = {}
    SENT: list[bytes] = []

    def __init__(self, host="", port=993, ssl_context=None, timeout=None):
        self.mailbox: Mailbox | None = None
        super().__init__(host, port, timeout)

    # imaplib's transport, replaced
    def open(self, host="", port=993, timeout=None):
        self.host, self.port = host, port
        self._buf = bytearray(b"* OK fake server ready\r\n")
        self.sock = None
        self._idle_tag = None

    def read(self, size):
        out, self._buf[:size] = bytes(self._buf[:size]), b""
        return out

    def readline(self):
        i = self._buf.find(b"\n")
        end = len(self._buf) if i < 0 else i + 1
        out, self._buf[:end] = bytes(self._buf[:end]), b""
        return out

    def shutdown(self):
        pass

    def send(self, data):
        type(self).SENT.append(bytes(data))
        line = data.decode().rstrip("\r\n")
        if line == "DONE":
            self._out(f"{self._idle_tag} OK idle done")
            return
        tag, _, rest = line.partition(" ")
        words = rest.split(" ")
        cmd = words[0].upper()
        if cmd == "CAPABILITY":
            self._out("* CAPABILITY IMAP4rev1 IDLE", f"{tag} OK done")
        elif cmd == "LOGIN":
            self.mailbox = type(self).BOXES[words[1].strip('"')]
            self._out(f"{tag} OK logged in")
        elif cmd in ("EXAMINE", "SELECT"):
            mb = self.mailbox
            self._out(f"* {len(mb.messages)} EXISTS", f"* OK [UIDVALIDITY {mb.validity}] v",
                      f"* OK [UIDNEXT {mb.uidnext}] n", f"{tag} OK [READ-ONLY] done")
        elif cmd == "UID" and words[1].upper() == "SEARCH":
            self._out("* SEARCH " + " ".join(str(u) for u in sorted(self.mailbox.messages)),
                      f"{tag} OK done")
        elif cmd == "UID" and words[1].upper() == "FETCH":
            first = int(words[2].split(":")[0])
            uids = [u for u in sorted(self.mailbox.messages) if u >= first] or \
                sorted(self.mailbox.messages)[-1:]
            for n, uid in enumerate(uids, 1):
                raw = self.mailbox.messages[uid]
                self._buf += f"* {n} FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode()
                self._buf += raw + b")\r\n"
            self._out(f"{tag} OK fetched")
        elif cmd == "IDLE":
            self._idle_tag = tag
            self._out("+ idling", f"* {len(self.mailbox.messages)} EXISTS")
        elif cmd == "LOGOUT":
            self._out("* BYE", f"{tag} OK done")
        else:
            self._out(f"{tag} BAD not here")

    def _out(self, *lines: str) -> None:
        for line in lines:
            self._buf += line.encode() + b"\r\n"


#: The IMAP commands that write: never sent, plain or under UID (SC4).
WRITES = {"SELECT", "STORE", "EXPUNGE", "COPY", "MOVE", "APPEND"}


def commands(sent: list[bytes]) -> list[list[str]]:
    """Each tagged command line as its words after the tag ([] for DONE)."""
    out = []
    for raw in sent:
        line = raw.decode().rstrip("\r\n")
        if line == "DONE":
            continue
        out.append(line.split(" ")[1:])
    return out


def violations(sent: list[bytes]) -> list[str]:
    """Every sent line that breaks the read-only rule: a writing command
    (plain or after UID), a FETCH without BODY.PEEK or with a part that sets
    \\Seen (BODY[ without PEEK, RFC822, RFC822.TEXT)."""
    bad = []
    for words in commands(sent):
        names = [w.upper() for w in words[:2]]
        cmd = names[1] if names[0] == "UID" and len(names) > 1 else names[0]
        line = " ".join(words)
        if cmd in WRITES:
            bad.append(line)
        if cmd == "FETCH":
            items = line.upper()
            if "BODY.PEEK[" not in items or re.search(r"BODY\[|RFC822(?!\.SIZE|\.HEADER)",
                                                      items):
                bad.append(line)
    return bad


def test_the_guard_can_fail():
    """The denylist is proven to catch each writing command, so its clean
    result below is a measurement, not a decoration."""
    for line in (b"A1 SELECT INBOX\r\n", b"A2 UID STORE 1 +FLAGS (\\Seen)\r\n",
                 b"A3 EXPUNGE\r\n", b"A4 UID COPY 1 x\r\n", b"A5 UID MOVE 1 x\r\n",
                 b"A6 APPEND x {3}\r\n", b"A7 UID FETCH 1:* (UID BODY[])\r\n",
                 b"A8 FETCH 1 RFC822\r\n"):
        assert violations([line]), line
    assert violations([b"A9 UID FETCH 1:* (UID BODY.PEEK[])\r\n"]) == []


@pytest.fixture
def wire(monkeypatch, tmp_path, stubs):
    """The fake server in place of IMAPS; the map, held and inject stubbed."""
    WireIMAP.SENT = []
    WireIMAP.BOXES = {}
    monkeypatch.setattr(imaplib, "IMAP4_SSL", WireIMAP)
    monkeypatch.setattr(_puller, "nodelay", lambda imap: None)
    return WireIMAP


def test_a_run_is_read_only_on_the_wire(env, wire, stubs, tmp_path):
    """SC4: a whole --once run over a box, general delivery and the audit
    sends no writing command; every box is opened by EXAMINE; every FETCH
    is BODY.PEEK. (imaplib sends CAPABILITY itself: the guard is a
    denylist, not an allowlist.)"""
    sendmail = script(tmp_path / "sendmail", "import sys\nsys.stdin.buffer.read()\n")
    env = {**env, "SESSIONMAIL_PULL_UNSORTED_BOX": "_unsorted",
           "SESSIONMAIL_PULL_AUDIT_BOX": "_audit",
           "SESSIONMAIL_PULL_AUDIT_TO": "alpha@example.org",
           "SESSIONMAIL_PULL_AUDIT_SENDMAIL": str(sendmail)}
    (tmp_path / "pull.boxes").write_text("alpha\n")
    digest = json.dumps({"entries": [{"ip": OWN_IP, "box": "alpha", "to": "alpha@host-b",
                                      "mid": "lost@x", "t": 1}]})
    wire.BOXES.update({
        "alpha*master": Mailbox({1: mail("Message-ID: <a1@x>"), 2: mail("Message-ID: <a2@x>")}),
        "_unsorted*master": Mailbox({1: mail(f"X-Original-To: {SESSION}@relay.example",
                                             "Message-ID: <u1@x>")}),
        "_audit*master": Mailbox({1: mail("Message-ID: <d1@x>", body=digest)}),
    })
    cfg = _puller.config(env)
    for box in ("_unsorted", "_audit"):     # these start at UIDNEXT; give them a cursor
        _pull_imap.write_cursor(_puller.cursor_path(cfg, box), 7, 1)
    assert _puller.main(["--once"], env=env) == 0
    assert sorted(r for r, _t in stubs["injected"]) == sorted(["alpha", "alpha", SESSION])
    sent = commands(wire.SENT)
    assert sent, "nothing reached the wire"
    assert violations(wire.SENT) == []
    opened = [w[0].upper() for w in sent if w[0].upper() in ("EXAMINE", "SELECT")]
    assert opened == ["EXAMINE"] * 3
    fetches = [w for w in sent if len(w) > 1 and w[1].upper() == "FETCH"]
    assert fetches and all("(UID BODY.PEEK[])" in " ".join(w) for w in fetches)
    # cursors moved past what was taken
    assert _pull_imap.read_cursor(_puller.cursor_path(cfg, "alpha")) == (7, 3)


def test_idle_is_read_only_and_fetches_after_the_push(cfg, wire, stubs, monkeypatch):
    """The IDLE leg: IDLE, DONE, then UID FETCH ... BODY.PEEK, nothing else."""
    wire.BOXES["alpha*master"] = Mailbox({1: mail("Message-ID: <i1@x>")})
    monkeypatch.setattr(_puller.di, "_readable", lambda imap, s: True)
    imap = _puller.di.connect("imap.example.org", 993, True, "alpha*master", "s3cret")
    _puller.di.examine(imap, "INBOX")
    p = puller(cfg)
    wire.SENT.clear()
    assert p.idle(imap, 1) == 2
    sent = [" ".join(w) for w in commands(wire.SENT)]
    assert [s.split(" ")[0] for s in sent] == ["IDLE", "UID"]
    assert b"DONE\r\n" in wire.SENT and violations(wire.SENT) == []
    assert [r for r, _t in stubs["injected"]] == ["alpha"] and p.counts["pulled"] == 1


# ── the lock, the dry run, the boxes ───────────────────────────────────────

def hold_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    f = open(state / "puller.lock", "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.write("4242\n")
    f.flush()
    return f


def test_a_second_puller_exits_3_without_delivering(env, wire, stubs, tmp_path, capsys,
                                                    monkeypatch):
    """SC3: another puller holds the state dir; this one exits 3 before it
    reads a secret or connects. Run WITHOUT --dry-run (a dry run takes no
    lock)."""
    wire.BOXES["alpha*master"] = Mailbox({1: mail("Message-ID: <a1@x>")})
    secrets = []
    monkeypatch.setattr(_puller, "read_secret", lambda cfg: secrets.append(1) or "s3cret")
    held = hold_lock(tmp_path / "state")
    try:
        assert _puller.main(["--once"], env=env) == 3
    finally:
        held.close()
    out = capsys.readouterr().out
    assert "another puller holds" in out and "(pid 4242)" in out
    assert wire.SENT == [] and stubs["injected"] == [] and secrets == []


def test_the_lock_is_taken_and_released(env, wire, stubs, tmp_path):
    wire.BOXES.update({"alpha*master": Mailbox({1: mail("Message-ID: <a1@x>")}),
                       "beta*master": Mailbox({})})
    assert _puller.main(["--once"], env=env) == 0
    lock = tmp_path / "state" / "puller.lock"
    assert lock.read_text().strip() == str(os.getpid())
    hold_lock(tmp_path / "state").close()          # free again after main returned


def test_a_dry_run_writes_no_cursor_and_takes_no_lock(env, wire, stubs, tmp_path):
    wire.BOXES.update({"alpha*master": Mailbox({1: mail("Message-ID: <a1@x>")}),
                       "beta*master": Mailbox({1: mail("Message-ID: <b1@x>")})})
    held = hold_lock(tmp_path / "state")           # another puller runs; a dry run does not care
    try:
        assert _puller.main(["--once", "--dry-run"], env=env) == 0
    finally:
        held.close()
    assert stubs["injected"] == []
    assert list((tmp_path / "state").rglob("*.cursor")) == []
    assert any(c[0].upper() == "UID" for c in commands(wire.SENT))    # it did read


def test_advance_cursors_dry_run_writes_nothing(env, wire, stubs, tmp_path):
    wire.BOXES.update({"alpha*master": Mailbox({1: b"x", 2: b"y"}),
                       "beta*master": Mailbox({})})
    assert _puller.main(["--advance-cursors", "--dry-run"], env=env) == 0
    assert list((tmp_path / "state").rglob("*.cursor")) == []
    assert _puller.main(["--advance-cursors"], env=env) == 0
    cfg = _puller.config(env)
    assert _pull_imap.read_cursor(_puller.cursor_path(cfg, "alpha")) == (7, 3)
    assert violations(wire.SENT) == []


def test_no_boxes_exits_1(env, tmp_path):
    (tmp_path / "pull.boxes").write_text("# nothing\n")
    assert _puller.main(["--once"], env=env) == 1


def test_no_own_ip_known_refuses_with_2(env, monkeypatch):
    env = {k: v for k, v in env.items() if k != "SESSIONMAIL_PULL_OWN_IPS"}
    monkeypatch.setattr(_puller, "ask_tailscale", lambda: set())
    connects = []
    monkeypatch.setattr(_puller.di, "connect", lambda *a, **k: connects.append(a))
    assert _puller.main(["--once"], env=env) == 2
    assert connects == []


def test_the_secret_waits_and_says_so_once(cfg, capsys):
    import dataclasses
    flag = cfg.state_dir / "secret"
    c = dataclasses.replace(cfg, secret_command=f"cat {flag} 2>/dev/null")
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("late-secret\n")
    assert _puller.wait_for_secret(c, sleep) == "late-secret"
    assert sleeps == [60.0] * 3
    assert capsys.readouterr().out.count("the secret command gave nothing") == 1


def test_the_secret_command_failing_is_none(cfg):
    import dataclasses
    assert _puller.read_secret(dataclasses.replace(cfg, secret_command="exit 1")) is None
    assert _puller.read_secret(dataclasses.replace(cfg, secret_command="printf ''")) is None
    assert _puller.read_secret(cfg) == "s3cret"
