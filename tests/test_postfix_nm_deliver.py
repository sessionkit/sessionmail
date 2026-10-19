"""nm-deliver: the opt-in pipe(8) wrapper that indexes mail at delivery (SM-791).

`src/sessionmail/postfix/nm-deliver` is package data that master.cf runs by
path. These tests run it the way pipe(8) does: the message on stdin, the
recipient as argv[1], and an environment holding almost nothing (pipe(8)
sets PATH=/usr/bin:/bin and passes only export_environment).

Nothing here reads the host's postfix or index. `postconf` and `postmap` are
shims under tmp_path, answering from a JSON file, reached through the
wrapper's test-only NM_DELIVER_* variables. Where the test is about notmuch
itself (the folder it writes, indexing, the post-insert hook, a held write
lock, a missing database) it runs the real notmuch over a scratch database
named by NOTMUCH_CONFIG, with XDG_CONFIG_HOME moved too; those tests skip
where that notmuch is absent. Everywhere else a fake notmuch records what it
was asked to do.

The wrapper sets HOME from the passwd entry, by design, so HOME in these
runs is the real one. That is safe because every real-notmuch run names its
scratch config, and the post-insert hook's only HOME use (the opt-in
post-new command) is off in that config.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "src" / "sessionmail" / "postfix" / "nm-deliver"
HOOK = ROOT / "src" / "sessionmail" / "notmuch" / "post-new"
PIPE_PATH = "/usr/bin:/bin"
#: The hook's fallback list, read from the hook: the wrapper's pick list too.
NOTMUCH_PATHS = re.search(r'^NOTMUCH_PATHS="([^"]+)"$', HOOK.read_text(), re.M).group(1).split()
#: The notmuch the hook finds under pipe(8)'s PATH (SM-796): notmuch on that
#: PATH, else the list's first runnable (the wrapper's first pick).
NOTMUCH = shutil.which("notmuch", path=PIPE_PATH) or next(
    (p for p in NOTMUCH_PATHS if os.access(p, os.X_OK)), NOTMUCH_PATHS[0])
#: virtual(8)'s name is <sec>.V<dev>I<inode>M<usec>.<host>; insert's is this.
INSERT_NAME = re.compile(r"^\d+\.M\d+P\d+\.")
MAPS = "hash:/maps/vmailbox"

needs_notmuch = pytest.mark.skipif(not os.access(NOTMUCH, os.X_OK),
                                   reason=f"no {NOTMUCH}: the wrapper's default notmuch")


def shim(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return path


#: postconf as the real one answers: -h prints a value RAW ($name kept), -xh
#: expands it. The wrapper must ask with -xh (SM-791 fix-001 review).
POSTCONF = """\
import json, re, sys
cfg = json.load(open({cfg!r}))
a = sys.argv[1:]
def expand(v, depth=0):
    # postconf -x: expand each $name reference (bare or braced) from the params
    if depth > 10:
        return v
    return re.sub(r"\\$(?:\\{{(\\w+)\\}}|(\\w+))",
                  lambda m: expand(cfg["params"].get(m.group(1) or m.group(2), ""), depth + 1), v)
if a[:1] in (["-h"], ["-xh"]) and len(a) == 2:
    if a[1] in cfg["params"]:
        v = cfg["params"][a[1]]
        print(expand(v) if a[0] == "-xh" else v)
    else:
        print(f"postconf: warning: {{a[1]}}: unknown parameter", file=sys.stderr)
    sys.exit(0)
if a[:1] == ["-M"]:
    print(cfg.get("services", {{}}).get(a[1], ""))
    sys.exit(0)
print("postconf shim: unexpected " + " ".join(a), file=sys.stderr)
sys.exit(64)
"""

POSTMAP = """\
import json, sys
cfg = json.load(open({cfg!r}))
a = sys.argv[1:]
assert a[0] == "-q", a
key, maps = a[1], a[2:]
with open({log!r}, "a") as f:
    f.write(key + "\\n")
for m in maps:
    if m in cfg.get("broken", []):
        print(f"postmap: fatal: open {{m}}: No such file or directory", file=sys.stderr)
        sys.exit(1)
    table = cfg["maps"].get(m, {{}})
    if key in table:
        print(table[key])
        sys.exit(0)
sys.exit(1)
"""

#: A notmuch that records argv, HOME and stdin, then does what MODE says.
FAKE_NOTMUCH = """\
import json, os, sys, time
data = sys.stdin.read()
with open({rec!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "home": os.environ.get("HOME"),
                        "cwd": os.getcwd(), "stdin": data}}) + "\\n")
mode = {mode!r}
if mode == "hang":
    time.sleep(60)
if mode == "fail":
    print("Error: boom", file=sys.stderr)
    sys.exit(1)
"""


class Host:
    """Shim postconf/postmap over a JSON config, and a mail root."""

    def __init__(self, tmp: Path, *, delimiter: str = "+"):
        self.tmp = tmp
        self.root = tmp / "mail"
        self.cfg_path = tmp / "postfix.json"
        self.lookups = tmp / "lookups.log"
        self.cfg = {"params": {"recipient_delimiter": delimiter, "virtual_mailbox_maps": MAPS},
                    "maps": {MAPS: {}}}
        self.save()
        bin_dir = tmp / "bin"
        self.postconf = shim(bin_dir / "postconf", POSTCONF.format(cfg=str(self.cfg_path)))
        self.postmap = shim(bin_dir / "postmap",
                            POSTMAP.format(cfg=str(self.cfg_path), log=str(self.lookups)))
        self.notmuch = NOTMUCH
        self.env = {"PATH": PIPE_PATH,
                    "NM_DELIVER_POSTCONF": str(self.postconf),
                    "NM_DELIVER_POSTMAP": str(self.postmap)}

    def save(self) -> None:
        self.cfg_path.write_text(json.dumps(self.cfg))

    def row(self, key: str, value: str) -> None:
        """A vmailbox row, keyed like the generator's: `alpha@host-a` -> `agents/alpha/`."""
        self.cfg["maps"][MAPS][key] = value
        self.save()

    def fake_notmuch(self, mode: str = "ok") -> Path:
        self.record = self.tmp / "notmuch.rec"
        self.notmuch = str(shim(self.tmp / "bin" / "notmuch",
                                FAKE_NOTMUCH.format(rec=str(self.record), mode=mode)))
        return Path(self.notmuch)

    def calls(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def deliver(self, *argv: str, msgid: str = "m1@example.invalid", extra: dict | None = None,
                timeout: float = 60) -> subprocess.CompletedProcess:
        env = {**self.env, "NM_DELIVER_NOTMUCH": str(self.notmuch), **(extra or {})}
        msg = (f"From: beta@example.invalid\nTo: {argv[0] if argv else ''}\n"
               f"Subject: t\nMessage-ID: <{msgid}>\n\nbody of {msgid}\n")
        return subprocess.run([str(WRAPPER), *argv], input=msg, capture_output=True,
                              text=True, env=env, timeout=timeout)

    def files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*") if p.is_file()
                      and ".notmuch" not in p.parts) if self.root.exists() else []


class Db(Host):
    """Host plus a real scratch notmuch database whose hooks are the repo's
    post-new hook, linked as post-new and post-insert as on a host."""

    def __init__(self, tmp: Path, *, database: bool = True, tags: str = "unread;inbox;"):
        super().__init__(tmp)
        for box in ("agents/alpha", "agents/store/proj"):
            for sub in ("cur", "new", "tmp"):
                (self.root / box / sub).mkdir(parents=True)
        hooks = tmp / "hooks"
        hooks.mkdir()
        (hooks / "post-new").symlink_to(HOOK)
        (hooks / "post-insert").symlink_to("post-new")
        self.config = tmp / "notmuch-config"
        path = self.root if database else tmp / "no-database"
        self.config.write_text(
            f"[database]\npath={path}\nhook_dir={hooks}\n"
            "[user]\nname=t\nprimary_email=t@example.invalid\n"
            f"[new]\ntags={tags}\n")
        self.env.update({"NOTMUCH_CONFIG": str(self.config), "XDG_CONFIG_HOME": str(tmp / "xdg")})
        self.row("alpha@host-a", "agents/alpha/")
        self.row("proj@host-a", "agents/store/proj/")
        if database:
            self.nm("new")

    def nm(self, *args: str, **kw) -> subprocess.CompletedProcess:
        env = {**self.env, "HOME": str(self.tmp)}
        run = subprocess.run([NOTMUCH, *args], capture_output=True, text=True, env=env, **kw)
        assert run.returncode == 0, f"notmuch {' '.join(args)}: {run.stderr}"
        return run

    def count(self, query: str) -> int:
        return int(self.nm("count", query).stdout.strip())


# ---------------------------------------------------------------------------
# Real notmuch: what lands, where, and indexed


@needs_notmuch
@pytest.mark.parametrize("rcpt,box", [("alpha@host-a", "agents/alpha"),
                                      ("proj@host-a", "agents/store/proj")])
def test_a_flat_and_a_nested_box_land_in_new_indexed_and_tagged(tmp_path, rcpt, box):
    """The folder comes from the map value: a nested box is
    agents/<store>/<project>/, which `agents/<box>` from the address would
    miss. The file is insert-named, in new/, indexed once, and the
    post-insert hook has tagged it +agent."""
    db = Db(tmp_path)
    run = db.deliver(rcpt, msgid="flat-or-nested@example.invalid")
    assert run.returncode == 0, run.stdout + run.stderr
    (landed,) = db.files()
    assert landed.parent == db.root / box / "new"
    assert INSERT_NAME.match(landed.name), landed.name
    assert landed.stat().st_mode & 0o777 == 0o600
    assert db.count("id:flat-or-nested@example.invalid") == 1
    assert db.count("id:flat-or-nested@example.invalid and tag:agent and tag:unread") == 1


@needs_notmuch
def test_an_extension_lands_in_the_base_box(tmp_path):
    db = Db(tmp_path)
    run = db.deliver("alpha+sm-1.foreman@host-a", msgid="ext@example.invalid")
    assert run.returncode == 0, run.stdout + run.stderr
    (landed,) = db.files()
    assert landed.parent == db.root / "agents" / "alpha" / "new"
    assert db.count("id:ext@example.invalid") == 1


@needs_notmuch
def test_a_box_missing_on_disk_is_recreated(tmp_path):
    """virtual(8) recreates a mapped maildir that is gone (e.g. archived
    before the map was regenerated); --create-folder keeps that parity."""
    db = Db(tmp_path)
    db.row("gone@host-a", "agents/gone/")
    run = db.deliver("gone@host-a", msgid="gone@example.invalid")
    assert run.returncode == 0, run.stdout + run.stderr
    (landed,) = db.files()
    assert landed.parent == db.root / "agents" / "gone" / "new"


@needs_notmuch
def test_no_database_defers_with_a_4xx_line_and_writes_nothing(tmp_path):
    """A bare `notmuch insert` exits 1 here, which pipe(8) would BOUNCE."""
    db = Db(tmp_path, database=False)
    run = db.deliver("alpha@host-a")
    assert run.returncode == 75, run.stdout + run.stderr
    assert run.stdout.startswith("4.3.0 "), run.stdout
    assert "rc 1" in run.stdout
    assert db.files() == []


@needs_notmuch
def test_a_held_write_lock_is_waited_out_then_delivered(tmp_path):
    """Measured in SM-791 (notmuch 0.40): insert WAITS on a held lock and
    writes nothing until it frees. The wrapper must neither fail nor bounce."""
    db = Db(tmp_path)
    env = {**db.env, "HOME": str(tmp_path)}
    holder = subprocess.Popen(f"sleep 4 | {NOTMUCH} tag --batch", shell=True, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        msg = "From: b@example.invalid\nMessage-ID: <locked@example.invalid>\n\nx\n"
        wrapper = subprocess.Popen(
            [str(WRAPPER), "alpha@host-a"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env={**db.env, "NM_DELIVER_NOTMUCH": NOTMUCH})
        wrapper.stdin.write(msg)
        wrapper.stdin.close()
        time.sleep(1.5)
        assert wrapper.poll() is None, "insert did not wait on the held lock"
        assert db.files() == [], "a file landed while the lock was held"
        out, err = wrapper.communicate(timeout=60)
    finally:
        holder.wait(timeout=30)
    assert wrapper.returncode == 0, out + err
    assert len(db.files()) == 1
    assert db.count("id:locked@example.invalid") == 1


@needs_notmuch
def test_new_tags_without_unread_files_into_cur(tmp_path):
    """Why the docs and delivery-check insist on `unread` in new.tags: without
    it insert writes cur/...:2,S, and the pager, which watches new/, hears
    nothing. Pinned so a notmuch that changes this is noticed."""
    db = Db(tmp_path, tags="inbox;")
    run = db.deliver("alpha@host-a", msgid="read@example.invalid")
    assert run.returncode == 0, run.stdout + run.stderr
    (landed,) = db.files()
    assert landed.parent.name == "cur"
    assert landed.name.endswith(":2,S")


# ---------------------------------------------------------------------------
# Fake notmuch: the contract around insert


def test_the_insert_it_runs_and_the_environment_it_builds(tmp_path):
    """Only PATH and the test seams in the environment, as under pipe(8):
    HOME comes from passwd, cwd is HOME, the message reaches notmuch's stdin
    whole, and the folder is the map value without its trailing /. Extra
    argv (the `${user} ${domain}` form) is ignored."""
    host = Host(tmp_path)
    host.fake_notmuch()
    host.row("alpha@host-a", "agents/alpha/")
    run = host.deliver("ALPHA@Host-A", "alpha", "host-a", msgid="env@example.invalid")
    assert run.returncode == 0, run.stdout + run.stderr
    (call,) = host.calls()
    assert call["argv"] == ["insert", "--keep", "--create-folder", "--folder=agents/alpha"]
    home = pwd.getpwuid(os.getuid()).pw_dir
    assert call["home"] == home
    assert os.path.realpath(call["cwd"]) == os.path.realpath(home)
    assert "body of env@example.invalid" in call["stdin"]


@pytest.mark.parametrize("value", ["../x/", "/abs/agents/x/", "agents/alpha", "agents/../x/",
                                   "agents/./x/", "agents//x/", "inbox/", "agents/a b/",
                                   "agents/"])
def test_an_unsafe_map_value_defers_and_runs_no_insert(tmp_path, value):
    host = Host(tmp_path)
    host.fake_notmuch()
    host.row("alpha@host-a", value)
    run = host.deliver("alpha@host-a")
    assert run.returncode == 75, run.stdout
    assert run.stdout.startswith("4.3.5 "), run.stdout
    assert host.calls() == []


def test_an_unknown_recipient_bounces_5_1_1_and_runs_no_insert(tmp_path):
    host = Host(tmp_path)
    host.fake_notmuch()
    run = host.deliver("nobody@host-a")
    assert run.returncode == 67
    assert run.stdout == "5.1.1 <nobody@host-a>: no mailbox mapping\n"
    assert host.calls() == []


def test_the_lookup_order_is_virtual_8s(tmp_path):
    """user+ext@domain, then user@domain, then @domain (virtual(8), TABLE
    SEARCH ORDER), each key over every map."""
    host = Host(tmp_path)
    host.fake_notmuch()
    host.row("@host-a", "agents/catchall/")
    run = host.deliver("alpha+x@host-a")
    assert run.returncode == 0, run.stdout
    assert host.lookups.read_text().split() == ["alpha+x@host-a", "alpha@host-a", "@host-a"]
    assert host.calls()[-1]["argv"][-1] == "--folder=agents/catchall"
    host.row("alpha+x@host-a", "agents/exact/")
    host.deliver("alpha+x@host-a")
    assert host.calls()[-1]["argv"][-1] == "--folder=agents/exact"


def test_every_delimiter_character_cuts_the_extension(tmp_path):
    """recipient_delimiter may be a set (postfix 2.11+): cut at the first."""
    host = Host(tmp_path, delimiter="+-")
    host.fake_notmuch()
    host.row("alpha@host-a", "agents/alpha/")
    for rcpt in ("alpha-ext@host-a", "alpha+ext-more@host-a"):
        run = host.deliver(rcpt)
        assert run.returncode == 0, (rcpt, run.stdout)
    assert len(host.calls()) == 2


def test_an_insert_failure_defers_with_the_status_line_first(tmp_path):
    """notmuch's own error comes after the 4.3.0 line, so pipe(8) reads the
    enhanced status code, and exit 75 defers whatever it reads."""
    host = Host(tmp_path)
    host.fake_notmuch("fail")
    host.row("alpha@host-a", "agents/alpha/")
    run = host.deliver("alpha@host-a")
    assert run.returncode == 75
    assert run.stdout.startswith("4.3.0 nm-deliver: notmuch insert failed (rc 1): Error: boom")


def test_an_insert_that_hangs_is_killed_at_the_timeout_and_deferred(tmp_path):
    host = Host(tmp_path)
    host.fake_notmuch("hang")
    host.row("alpha@host-a", "agents/alpha/")
    t0 = time.monotonic()
    run = host.deliver("alpha@host-a", extra={"NM_DELIVER_TIMEOUT": "1"}, timeout=30)
    took = time.monotonic() - t0
    assert run.returncode == 75, run.stdout
    assert run.stdout.startswith("4.4.1 "), run.stdout
    assert took < 10, f"took {took:.1f}s"


def test_a_failed_map_lookup_defers(tmp_path):
    """postmap's fatal errors exit 1 like a miss; the wrapper must not turn a
    broken map into a 5.1.1 bounce."""
    host = Host(tmp_path)
    host.fake_notmuch()
    host.cfg["broken"] = [MAPS]
    host.save()
    run = host.deliver("alpha@host-a")
    assert run.returncode == 75
    assert run.stdout.startswith("4.3.0 nm-deliver: postmap -q failed"), run.stdout
    assert host.calls() == []


def test_a_map_named_through_a_parameter_is_expanded(tmp_path):
    """main.cf may write `hash:$config_directory/vmailbox`. postfix expands
    it, and so must the wrapper (postconf -xh): with -h, postmap is handed
    the literal `$config_directory` and every message is deferred (SM-791
    fix-001 review, measured with postconf 3.2.2 on a scratch config)."""
    host = Host(tmp_path)
    host.fake_notmuch()
    host.cfg["params"]["config_directory"] = "/maps"
    host.cfg["params"]["virtual_mailbox_maps"] = "hash:$config_directory/vmailbox"
    host.save()
    host.row("alpha@host-a", "agents/alpha/")
    run = host.deliver("alpha@host-a")
    assert run.returncode == 0, run.stdout
    assert host.calls()[-1]["argv"][-1] == "--folder=agents/alpha"


@pytest.mark.parametrize("argv", [[], [""], ["no-at-sign"]])
def test_no_recipient_is_a_config_fault_and_defers(tmp_path, argv):
    host = Host(tmp_path)
    host.fake_notmuch()
    run = host.deliver(*argv)
    assert run.returncode == 75
    assert run.stdout.startswith("4.3.5 "), run.stdout


@pytest.mark.parametrize("var", ["NM_DELIVER_POSTCONF", "NM_DELIVER_POSTMAP",
                                 "NM_DELIVER_NOTMUCH"])
def test_a_missing_binary_defers(tmp_path, var):
    host = Host(tmp_path)
    host.fake_notmuch()
    host.row("alpha@host-a", "agents/alpha/")
    run = host.deliver("alpha@host-a", extra={var: str(tmp_path / "nowhere")})
    assert run.returncode == 75
    assert run.stdout.startswith("4.3.0 "), run.stdout


def test_an_empty_maps_parameter_defers(tmp_path):
    host = Host(tmp_path)
    host.fake_notmuch()
    host.cfg["params"]["virtual_mailbox_maps"] = ""
    host.save()
    run = host.deliver("alpha@host-a")
    assert run.returncode == 75
    assert run.stdout.startswith("4.3.5 "), run.stdout


def test_it_ships_executable_and_is_posix_sh():
    assert os.access(WRAPPER, os.X_OK)
    assert WRAPPER.read_text().startswith("#!/bin/sh\n")
    assert subprocess.run(["/bin/sh", "-n", str(WRAPPER)]).returncode == 0
