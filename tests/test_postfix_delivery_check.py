"""delivery-check: which delivery a host's postfix uses (SM-791).

`src/sessionmail/postfix/delivery-check` is a read-only report, package data
run by path. Every host shape here is a shim: `postconf`, `postmap` and
`notmuch` under tmp_path answer from a JSON file, reached through the
NM_DELIVER_* variables. Placeholder hosts only (host-a, example.ts.net).

The shapes: a virtual host (postfix's default, what the docs prescribed
before SM-791); an insert host switched by virtual_transport (the documented
opt-in); an insert host switched by transport_maps rows, one per domain; a
host with the two mixed; and the gaps an insert host can carry.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "src" / "sessionmail" / "postfix" / "delivery-check"
DOCS = ROOT / "docs" / "postfix-configuration.md"

DOMAINS = "host-a, host-a.example.ts.net"
#: The pipe runs notmuch as the service's user=, so a service whose user is
#: not the one running the check is a gap. The fixtures name the runner.
ME, MY_HOME = pwd.getpwuid(os.getuid()).pw_name, pwd.getpwuid(os.getuid()).pw_dir
VIRTUAL_SVC = "virtual   unix  -       n       n       -       -       virtual"
WRAPPER_SVC = ("nminsert  unix  -       n       n       -       -       pipe "
               f"flags=DORhu user={ME} argv=/opt/sessionmail/postfix/nm-deliver ${{recipient}}")
#: A per-domain switch's service: the older three-argument argv.
ROWS_SVC = (f"nminsert unix - n n - - pipe flags=DROhu user={ME} "
            "argv=/usr/local/etc/postfix-agents/nm-deliver ${recipient} ${user} ${domain}")

#: postconf as the real one answers: -h prints a value RAW ($name kept), -xh
#: expands it. The check must ask with -xh (SM-791 fix-001 review).
POSTCONF = """\
import json, re, sys
cfg = json.load(open({cfg!r}))
a = sys.argv[1:]
def expand(v, depth=0):
    # postconf -x: expand each $name reference (bare or braced) from the params
    if depth > 10:
        return v
    return re.sub(r"\\$(?:\\{{(\\w+)\\}}|(\\w+))",
                  lambda m: expand(cfg["params"].get(m.group(1) or m.group(2)) or "", depth + 1), v)
if a[:1] in (["-h"], ["-xh"]) and len(a) == 2:
    v = cfg["params"].get(a[1])
    if v is None:
        print(f"postconf: warning: {{a[1]}}: unknown parameter", file=sys.stderr)
    else:
        print(expand(v) if a[0] == "-xh" else v)
    sys.exit(cfg.get("rc", 0))
if a[:1] == ["-M"]:
    print(cfg["services"].get(a[1], ""))
    sys.exit(0)
sys.exit(64)
"""
POSTMAP = """\
import json, sys
cfg = json.load(open({cfg!r}))
a = sys.argv[1:]
for m in a[2:]:
    t = cfg["maps"].get(m, {{}})
    if a[1] in t:
        print(t[a[1]]); sys.exit(0)
sys.exit(1)
"""
NOTMUCH = """\
import json, os, sys
cfg = json.load(open({cfg!r}))
a = sys.argv[1:]
with open({env_log!r}, "a") as f:
    f.write(json.dumps(dict(os.environ)) + "\\n")
if a[:2] == ["config", "get"]:
    v = cfg["notmuch"].get(a[2], [])
    print("\\n".join(v)) if v else None
    sys.exit(0)
sys.exit(64)
"""


def shim(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return path


class Host:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.base = tmp / "mail"
        self.base.mkdir()
        hooks = tmp / "hooks"
        hooks.mkdir()
        self.hook = hooks / "post-insert"
        self.cfg_path = tmp / "host.json"
        self.env_log = tmp / "notmuch-env.log"
        self.cfg = {
            "params": {"virtual_transport": "virtual", "virtual_mailbox_domains": DOMAINS,
                       "transport_maps": "", "virtual_mailbox_base": str(self.base)},
            "services": {"virtual/unix": VIRTUAL_SVC},
            "maps": {},
            "notmuch": {"new.tags": ["unread", "inbox"], "database.hook_dir": [str(hooks)],
                        "database.mail_root": [str(self.base)]},
        }
        bin_dir = tmp / "bin"
        c = str(self.cfg_path)
        self.env = {"PATH": "/usr/bin:/bin",
                    "NM_DELIVER_POSTCONF": str(shim(bin_dir / "postconf", POSTCONF.format(cfg=c))),
                    "NM_DELIVER_POSTMAP": str(shim(bin_dir / "postmap", POSTMAP.format(cfg=c))),
                    "NM_DELIVER_NOTMUCH": str(shim(bin_dir / "notmuch",
                                                   NOTMUCH.format(cfg=c, env_log=str(self.env_log))))}

    def link_hook(self) -> None:
        self.hook.write_text("#!/bin/sh\n")
        self.hook.chmod(0o755)

    def insert_by_transport(self, svc: str = WRAPPER_SVC) -> None:
        """The documented opt-in: virtual_transport names the pipe service."""
        self.cfg["params"]["virtual_transport"] = "nminsert"
        self.cfg["params"]["nminsert_destination_recipient_limit"] = "1"
        self.cfg["services"]["nminsert/unix"] = svc
        self.link_hook()

    def insert_by_rows(self, *domains: str) -> None:
        """The per-domain alternative: transport_maps rows, virtual_transport left alone."""
        self.cfg["params"]["transport_maps"] = "hash:/etc/postfix/transport"
        self.cfg["params"]["nminsert_destination_recipient_limit"] = "1"
        self.cfg["services"]["nminsert/unix"] = ROWS_SVC
        self.cfg["maps"]["hash:/etc/postfix/transport"] = {d: "nminsert:" for d in domains}
        self.link_hook()

    def run(self, *args: str) -> subprocess.CompletedProcess:
        self.cfg_path.write_text(json.dumps(self.cfg))
        return subprocess.run([str(CHECK), *args], capture_output=True, text=True,
                              env=self.env, timeout=60)


def verdict(run: subprocess.CompletedProcess) -> str:
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout.splitlines()[0]


def gaps(run: subprocess.CompletedProcess) -> list[str]:
    return [line.strip()[len("gap: "):] for line in run.stdout.splitlines()
            if line.strip().startswith("gap: ")]


def test_a_virtual_host(tmp_path):
    run = Host(tmp_path).run()
    assert verdict(run) == "delivery: virtual"
    assert "  domain host-a: virtual_transport -> virtual (virtual: virtual)" in run.stdout
    assert "gaps: none" in run.stdout


def test_an_insert_host_by_virtual_transport(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport()
    run = host.run()
    assert verdict(run) == "delivery: insert"
    assert "nminsert_destination_recipient_limit = 1" in run.stdout
    assert "notmuch new.tags = unread;inbox" in run.stdout
    assert gaps(run) == []


def test_an_insert_host_by_transport_maps_rows(tmp_path):
    host = Host(tmp_path)
    host.insert_by_rows("host-a", "host-a.example.ts.net")
    run = host.run()
    assert verdict(run) == "delivery: insert"
    assert "domain host-a: transport_maps -> nminsert: (nminsert: insert)" in run.stdout
    assert gaps(run) == []


def test_one_domain_switched_is_mixed(tmp_path):
    host = Host(tmp_path)
    host.insert_by_rows("host-a")
    assert verdict(host.run()) == "delivery: mixed"


def test_another_transport_is_unknown(tmp_path):
    host = Host(tmp_path)
    host.cfg["params"]["virtual_transport"] = "lmtp:unix:private/dovecot-lmtp"
    host.cfg["services"]["lmtp/unix"] = "lmtp unix - - n - - lmtp"
    assert verdict(host.run()) == "delivery: unknown"


def test_a_transport_with_no_service_is_unknown_and_a_gap(tmp_path):
    host = Host(tmp_path)
    host.cfg["params"]["virtual_transport"] = "nminsert"
    run = host.run()
    assert verdict(run) == "delivery: unknown"
    assert any("no nminsert/unix service" in g for g in gaps(run))


def test_an_insert_host_without_unread_is_a_gap(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport()
    host.cfg["notmuch"]["new.tags"] = ["inbox"]
    run = host.run()
    assert verdict(run) == "delivery: insert"
    (gap,) = gaps(run)
    assert "lacks unread" in gap and "never hears" in gap


def test_an_insert_host_without_post_insert_is_a_gap(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport()
    host.hook.unlink()
    run = host.run()
    (gap,) = gaps(run)
    assert "post-insert" in gap


def test_a_bare_notmuch_insert_argv_is_a_gap(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport(
        f"nminsert unix - n n - - pipe flags=DORhu user={ME} "
        "argv=/opt/homebrew/bin/notmuch insert --folder=agents/x")
    run = host.run()
    assert verdict(run) == "delivery: insert"
    (gap,) = gaps(run)
    assert "BOUNCES" in gap


def test_missing_header_flags_and_recipient_limit_are_gaps(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport(WRAPPER_SVC.replace("flags=DORhu", "flags=hu"))
    del host.cfg["params"]["nminsert_destination_recipient_limit"]
    found = gaps(host.run())
    assert any("lacks DOR" in g for g in found), found
    assert any("recipient_limit is unset" in g for g in found), found


def test_parameters_are_read_expanded(tmp_path):
    """An unset per-transport limit is `$default_destination_recipient_limit`
    under `postconf -h` and its value under -xh; the check reports the value
    postfix uses (SM-791 fix-001 review)."""
    host = Host(tmp_path)
    host.insert_by_transport()
    host.cfg["params"]["default_destination_recipient_limit"] = "50"
    host.cfg["params"]["nminsert_destination_recipient_limit"] = \
        "$default_destination_recipient_limit"
    run = host.run()
    assert "nminsert_destination_recipient_limit = 50" in run.stdout, run.stdout
    assert any("recipient_limit is 50, not 1" in g for g in gaps(run)), gaps(run)


def table_domains(host: "Host") -> None:
    """postfix's default: virtual_mailbox_domains = $virtual_mailbox_maps, so
    under -xh the domain list is the map's own table name."""
    host.cfg["params"]["config_directory"] = "/maps"
    host.cfg["params"]["virtual_mailbox_maps"] = "hash:$config_directory/vmailbox"
    host.cfg["params"]["virtual_mailbox_domains"] = "$virtual_mailbox_maps"


def test_a_domain_list_that_names_a_table_is_not_probed_as_a_domain(tmp_path):
    """A table entry has no domain key to look up in transport_maps: the check
    reports it as a table, by virtual_transport, and calls no domain by its
    name (SM-791 fix-001, review item 1)."""
    host = Host(tmp_path)
    host.insert_by_transport()
    table_domains(host)
    run = host.run()
    assert verdict(run) == "delivery: insert"
    assert "  table hash:/maps/vmailbox: virtual_transport -> nminsert (nminsert: insert)" \
        in run.stdout, run.stdout
    assert "domain hash:" not in run.stdout
    assert gaps(run) == []


def test_a_table_domain_list_beside_transport_maps_is_a_gap(tmp_path):
    """With transport_maps set, rows for the table's domains may route them
    elsewhere; the check cannot enumerate a table, so it says so."""
    host = Host(tmp_path)
    host.insert_by_rows("host-a")
    table_domains(host)
    run = host.run()
    assert any("names a lookup table (hash:/maps/vmailbox)" in g for g in gaps(run)), gaps(run)


def test_notmuch_is_read_in_the_pipes_environment(tmp_path):
    """pipe(8) passes nm-deliver no NOTMUCH_CONFIG, no XDG_* and no HOME (the
    wrapper takes HOME from passwd), so notmuch finds its config at a default
    path. The check must read that config, not the caller's: a caller with
    NOTMUCH_CONFIG set would otherwise be told `unread` is there when the
    config insert reads lacks it (SM-791 fix-001, second review)."""
    host = Host(tmp_path)
    host.insert_by_transport()
    host.env.update({"NOTMUCH_CONFIG": str(tmp_path / "callers-config"),
                     "NOTMUCH_PROFILE": "other", "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                     "HOME": str(tmp_path), "TZ": "UTC"})
    run = host.run()
    assert gaps(run) == []
    assert f"notmuch read as the pipe reads it: user {ME}, HOME={MY_HOME}" in run.stdout
    envs = [json.loads(line) for line in host.env_log.read_text().splitlines()]
    assert envs, "notmuch was never asked"
    for env in envs:
        assert not {"NOTMUCH_CONFIG", "NOTMUCH_PROFILE", "XDG_CONFIG_HOME"} & env.keys(), env
        assert env["HOME"] == MY_HOME
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["TZ"] == "UTC"


@pytest.mark.skipif(ME == "alice", reason="the runner is the service's user")
def test_a_service_run_as_another_user_is_a_gap(tmp_path):
    """The notmuch reads are the runner's; the pipe's are its user='s."""
    host = Host(tmp_path)
    host.insert_by_transport(WRAPPER_SVC.replace(f"user={ME}", "user=alice"))
    (gap,) = gaps(host.run())
    assert "runs as user=alice" in gap and f"ran as {ME}" in gap


def test_a_mail_root_that_differs_is_a_gap(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport()
    host.cfg["notmuch"]["database.mail_root"] = [str(tmp_path / "elsewhere")]
    (gap,) = gaps(host.run())
    assert "mail_root" in gap


def write_files(box: Path, names: list[str]) -> None:
    (box / "new").mkdir(parents=True)
    (box / "cur").mkdir()
    for i, name in enumerate(names):
        f = box / "new" / name
        f.write_text("x")
        os.utime(f, (1_908_540_800 + i, 1_908_540_800 + i))


VIRTUAL_FILE = "2000000000.V100000fI1000001M100000.host-a"
INSERT_FILE = "2000000060.M100000P11111.host-a"


def test_a_mixed_tree_by_file_names(tmp_path):
    """A box that changed delivery holds both names; the newest says which
    wrote last, and a config that disagrees with it is a gap."""
    host = Host(tmp_path)
    write_files(host.base / "agents" / "alpha", [VIRTUAL_FILE, INSERT_FILE])
    run = host.run("--box", "agents/alpha")
    assert verdict(run) == "delivery: virtual"
    assert re.search(r"files: newest 2 in .*: virtual 1, insert 1, other 0 -> mixed "
                     r"\(newest written by insert\)", run.stdout), run.stdout
    (gap,) = gaps(run)
    assert "written by insert, but the config says virtual" in gap


def test_a_virtual_tree_by_file_names(tmp_path):
    host = Host(tmp_path)
    box = tmp_path / "abs-box"
    write_files(box, [VIRTUAL_FILE, "2000000001.V100000fI1000002M1.host-a"])
    run = host.run("--box", str(box), "--files", "1")
    assert "files: newest 1 in" in run.stdout
    assert "-> virtual (newest written by virtual)" in run.stdout
    assert gaps(run) == []


def test_no_postconf_cannot_look(tmp_path):
    host = Host(tmp_path)
    host.env["NM_DELIVER_POSTCONF"] = str(tmp_path / "nowhere")
    run = host.run()
    assert run.returncode == 2
    assert run.stdout == ""
    assert "cannot look" in run.stderr


def test_a_failing_postconf_cannot_look(tmp_path):
    host = Host(tmp_path)
    host.cfg["rc"] = 1
    assert host.run().returncode == 2


def test_it_writes_nothing(tmp_path):
    host = Host(tmp_path)
    host.insert_by_transport()
    write_files(host.base / "agents" / "alpha", [INSERT_FILE])
    host.run()   # writes the shims' host.json and env log, which the snapshot leaves out

    def snapshot() -> dict:
        return {p: (p.stat().st_mtime_ns, p.stat().st_mode) for p in tmp_path.rglob("*")
                if p not in (host.cfg_path, host.env_log)}

    before = snapshot()
    host.run("--box", "agents/alpha")
    assert snapshot() == before


# ---------------------------------------------------------------------------
# The docs' lines are the ones the check accepts


def documented_block() -> str:
    text = DOCS.read_text()
    m = re.search(r"## Index at delivery \(opt-in\).*?```text\n(.*?)```", text, re.S)
    assert m, "docs/postfix-configuration.md has no 'Index at delivery (opt-in)' text block"
    return m.group(1)


def test_the_documented_lines_name_nm_deliver_by_its_package_path():
    block = documented_block()
    assert re.search(r"argv=\S*/sessionmail/postfix/nm-deliver \$\{recipient\}", block), block
    assert "virtual_transport = nminsert" in block
    assert "nminsert_destination_recipient_limit = 1" in block


def test_the_documented_service_passes_the_check(tmp_path):
    """master.cf continues a service on indented lines: join them, as
    `postconf -M` prints it, and the check must call it insert with no gap."""
    block = documented_block()
    lines = [ln for ln in block.splitlines() if ln and not ln.startswith("#")]
    start = next(i for i, ln in enumerate(lines) if ln.startswith("nminsert "))
    svc = lines[start]
    for ln in lines[start + 1:]:
        if not ln[0].isspace():
            break
        svc += " " + ln.strip()
    host = Host(tmp_path)
    assert " user=alice " in svc, svc
    host.insert_by_transport(svc.replace(" user=alice ", f" user={ME} "))
    run = host.run()
    assert verdict(run) == "delivery: insert"
    assert gaps(run) == []


#: Real names that must never ship: hosts, the tailnet, people. Spelled in
#: pieces so this file is not itself a hit.
BANNED = [r"\bms" + "2\\b", r"\bm" + "5\\b", r"\bmb" + "a\\b", "sparrow" + "-hydra",
          r"\bji" + "m\\b", r"\bta" + "rs\\b", "fail" + "mode"]
SHIPPED = [ROOT / "src" / "sessionmail" / "postfix" / "nm-deliver", CHECK,
           ROOT / "tests" / "test_postfix_nm_deliver.py",
           Path(__file__)]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_no_real_names_in_the_new_files(path):
    text = path.read_text()
    for pat in BANNED:
        assert not re.search(pat, text, re.I), f"{path.name}: {pat}"


def test_no_real_names_in_the_index_at_delivery_section():
    text = DOCS.read_text()
    m = re.search(r"## Index at delivery \(opt-in\)(.*?)(?=\n## |\Z)", text, re.S)
    assert m
    for pat in BANNED:
        assert not re.search(pat, m.group(1), re.I), pat
