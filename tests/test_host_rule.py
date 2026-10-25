"""SM-941 (folds SM-326): ONE host rule, `_host`, checked against the spec's
table of cases (openspec/changes/host-rule/proposal.md, rows N1-N12 NAME and
I1-I14 IS-ME), plus the pin `_settings.ensure` writes at creation and the
writer `message setup` adds a missing `host` with.

Safety: nothing here reads or changes this host's real names or settings.
`socket.gethostname` is monkeypatched in every test that judges a name, the
settings home is the suite's sandbox (conftest `hermetic`), and `scutil` is
a fake on the PATH each test hands over: the suite's `_host.scutil_binary`
never returns the host's own (`conftest.fake_scutil_only`, checked below).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from conftest import fake_scutil_only, write_mail_settings
from fakes import write_fake
from sessionmail import _drop, _host, _operator, _seat, _sender, _settings, _subscribe

#: The resolver as shipped, taken before `hermetic` replaces it for each test.
REAL_SCUTIL_BINARY = _host.scutil_binary

#: The D6 machine, a MacBook Air after the rename.
D6_HOSTNAME = "admins-Air.localdomain"
D6_LOCAL_HOST_NAME = "admins-MacBook-Air"
D6_COMPUTER_NAME = "MacBook Air"

FAKE_SCUTIL = """#!{python}
import os, sys
names = {{"LocalHostName": os.environ.get("FAKE_LHN"), "ComputerName": os.environ.get("FAKE_CN")}}
args = sys.argv[1:]
if len(args) == 2 and args[0] == "--get" and names.get(args[1]):
    sys.stdout.write(names[args[1]] + "\\n")
    sys.exit(0)
sys.stdout.write((args[-1] if args else "") + ": not set\\n")
sys.exit(1)
"""


@pytest.fixture
def scutil_bin(tmp_path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    write_fake(d / "scutil", FAKE_SCUTIL)
    return d


def d6_env(scutil_bin: Path, **extra: str) -> dict[str, str]:
    """The D6 machine's scutil answers, on a PATH holding only the fake."""
    return {"PATH": str(scutil_bin), "FAKE_LHN": D6_LOCAL_HOST_NAME,
            "FAKE_CN": D6_COMPUTER_NAME, **extra}


def hostname(monkeypatch, value: str) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: value)


def pin(value: object) -> None:
    """The `host` setting, in the sandboxed settings home (`None`: no file)."""
    home = _operator.settings_home()
    write_mail_settings(home, None if value is None else json.dumps({"host": value}) + "\n")


# ── NAME: rows N1-N12 ────────────────────────────────────────────────────────

LIVE = "live.example.invalid"

NAME_ROWS = [
    # row, SESSIONMAIL_HOST, SESSION_MAIL_HOST, setting, gethostname, NAME
    ("N1", "pinned", None, "MacBook-Air", LIVE, "pinned"),
    ("N2", "Pinned", None, None, LIVE, "pinned"),
    ("N3", "   ", "Old", None, LIVE, "old"),
    ("N4", "Foo.Local", None, None, LIVE, "foo.local"),
    ("N5", None, "old", "MacBook-Air", D6_HOSTNAME, "old"),
    ("N6", None, None, "MacBook-Air", D6_HOSTNAME, "macbook-air"),
    ("N7", None, None, " host.lan ", D6_HOSTNAME, "host.lan"),
    ("N8", None, None, "  ", D6_HOSTNAME, "admins-air"),
    ("N9", None, None, 42, D6_HOSTNAME, "admins-air"),
    ("N10", None, None, None, "MacBook-Air.local", "macbook-air"),
    ("N11", None, None, None, "ms9.example.invalid", "ms9"),
    ("N12", None, None, None, "", ""),
]


@pytest.mark.parametrize("row,var,old,setting,live,want", NAME_ROWS, ids=[r[0] for r in NAME_ROWS])
def test_name_rows_and_every_host_function_agrees(monkeypatch, row, var, old, setting, live, want):
    """The table's NAME, from `_host.name` and from each delegate: the
    sender's, the drop's and the seat's (SM-326: they once disagreed on the
    variable, the setting and case), with ENV handed in and from os.environ."""
    hostname(monkeypatch, live)
    pin(setting)
    env = {k: v for k, v in (("SESSIONMAIL_HOST", var), ("SESSION_MAIL_HOST", old)) if v is not None}
    for k in ("SESSIONMAIL_HOST", "SESSION_MAIL_HOST"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    got = {"_host.name": _host.name(env), "_sender.host_of": _sender.host_of(env),
           "_drop.short_host": _drop.short_host(env), "_seat.short_host(env)": _seat.short_host(env),
           "_seat.short_host()": _seat.short_host(), "_host.name()": _host.name()}
    assert set(got.values()) == {want}, (row, got)


def test_the_map_readers_key_by_the_live_name(monkeypatch):
    """The three readers of postfix's maps (`_seat.box_via_map`,
    `_seat.doorbell`'s MTA branch, `_subscribe.row`) key by the LIVE short
    name, as the generator that WRITES those maps does (`vmailbox-gen`
    `HOST="$(hostname -s)"`, O1 DEFERRED to SM-942), never by the NAME: here
    the pin says `macbook-air`, and every lookup is at `@admins-air`."""
    hostname(monkeypatch, D6_HOSTNAME)
    pin("MacBook-Air")
    monkeypatch.delenv("SESSIONMAIL_HOST", raising=False)
    monkeypatch.delenv("SESSION_MAIL_HOST", raising=False)
    asked: list[str] = []
    monkeypatch.setattr(_seat, "_postfix_maps", lambda parameter: ["hash:/nowhere"])
    monkeypatch.setattr(_seat, "_postmap_lookup", lambda key, maps: asked.append(key))
    assert _seat.box_via_map("alpha") is None
    assert _subscribe.row("projects/widget") == "row: none for widget@admins-air: its mail " \
                                                "reaches its own box only"
    monkeypatch.setattr(_settings, "carrier",
                        lambda env=None: _settings.Carrier("mta", "/x/sendmail", "variable"))
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/x/{name}")
    live, why = _seat.doorbell("alpha+sm-1.worker", "sess", {})
    assert live is False and "alpha+sm-1.worker@admins-air" in why
    assert asked == ["alpha@admins-air", "widget@admins-air", "alpha+sm-1.worker@admins-air"]
    assert _seat.short_host() == "macbook-air"      # the NAME the writers use is unchanged


#: `postconf -h PARAMETER` for an MTA host whose maps live under $POSTFIX_MAPS.
MAPS_POSTCONF = """#!{python}
import os, sys
maps = os.environ["POSTFIX_MAPS"]
print({{"virtual_alias_maps": "hash:" + maps + "/valias",
       "virtual_mailbox_maps": "hash:" + maps + "/vmailbox",
       "virtual_mailbox_base": os.environ["MAILBOX_BASE"]}}[sys.argv[2]])
"""

#: `postmap -q KEY hash:MAP` as postfix answers it by default (no `-f`): the
#: key is folded to lower case when the map is built AND when it is asked, the
#: value comes back as written. Measured Sun 2026-10-25, with
#: /usr/sbin/postmap: a row `alpha@MacBook-Air` answers `alpha@macbook-air`.
FOLDING_POSTMAP = """#!{python}
import sys
key, path = sys.argv[2].lower(), sys.argv[3].split(":", 1)[1]
try:
    lines = open(path).read().splitlines()
except OSError:
    sys.exit(1)
for line in lines:
    parts = line.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == key:
        print(parts[1]); sys.exit(0)
sys.exit(1)
"""


def generator_maps(tmp_path: Path, gen_host: str) -> tuple[Path, Path]:
    """An MTA host's two maps, keyed as the generator WRITES them: the REAL
    `vmailbox-gen --host GEN_HOST` (what `hostname -s` gives, case AS GIVEN),
    print modes only, so it touches nothing. vmailbox: the boxes `alpha` and
    `sess`. valias: the plus rows for `alpha+sm-1.worker` -> `sess`, joined by
    key as the generator folds them, plus the project row `widget@<host>` ->
    `sess@<host>` in its `%s@%s` form (a subscription row needs the session
    layers to emit)."""
    base, maps = tmp_path / "mailbase", tmp_path / "maps"
    for box in ("alpha", "sess"):
        for sub in ("new", "cur", "tmp"):
            (base / "agents" / box / sub).mkdir(parents=True)
    maps.mkdir()
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as gen:
        def run(*args: str) -> str:
            done = subprocess.run([str(gen), "--host", gen_host, "--no-fqdn", *args],
                                  capture_output=True, text=True, check=True, timeout=60)
            return done.stdout
        (maps / "vmailbox").write_text(run("--root", str(base), "--print"))
        union: dict[str, list[str]] = {}       # BY KEY UNION, `, `-joined, as it writes valias
        for line in run("--plus-rows", "alpha+sm-1.worker", "sess").splitlines():
            key, dest = line.split("\t")
            union.setdefault(key, []).append(dest)
        union[f"widget@{gen_host}"] = [f"sess@{gen_host}"]
        (maps / "valias").write_text("".join(f"{k}\t{', '.join(d)}\n" for k, d in union.items()))
    assert f"alpha@{gen_host}\tagents/alpha/" in (maps / "vmailbox").read_text()
    return base, maps


@pytest.mark.parametrize("postmap", ["folding-fake", "real"])
@pytest.mark.parametrize("live_hostname", ["ms9.example.invalid", "MacBook-Air.local"],
                         ids=["lowercase", "mixed-case"])
def test_a_name_that_is_not_the_live_name_still_finds_the_generators_rows(
        monkeypatch, tmp_path, live_hostname, postmap):
    """F1 (fix-002): SESSIONMAIL_HOST=other on an MTA host whose maps the
    generator keyed by the live `hostname -s`. Every map reader still finds
    its row: `box_via_map` the box, the doorbell LIVE, `_subscribe.row` the
    project row. Mixed case: the generator writes `@MacBook-Air` as given,
    the readers ask `@macbook-air`, and postmap folds both sides. `real` asks
    this host's own postmap of a map built in tmp_path (skipped without one)."""
    gen_host = live_hostname.split(".")[0]          # `hostname -s`: case AS GIVEN
    hostname(monkeypatch, live_hostname)
    monkeypatch.setenv("SESSIONMAIL_HOST", "other")
    base, maps = generator_maps(tmp_path, gen_host)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake(bin_dir / "postconf", MAPS_POSTCONF)
    if postmap == "real":
        real = shutil.which("postmap")
        if real is None:
            pytest.skip("no postmap on this host")
        for m in ("vmailbox", "valias"):
            if subprocess.run([real, f"hash:{maps / m}"], capture_output=True).returncode:
                pytest.skip("this host's postmap cannot build a hash: map")
        path = f"{bin_dir}:{os.path.dirname(real)}:{os.environ['PATH']}"
    else:
        write_fake(bin_dir / "postmap", FOLDING_POSTMAP)
        path = f"{bin_dir}:{os.environ['PATH']}"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("POSTFIX_MAPS", str(maps))
    monkeypatch.setenv("MAILBOX_BASE", str(base))
    monkeypatch.setattr(_settings, "carrier",
                        lambda env=None: _settings.Carrier("mta", "/x/sendmail", "variable"))
    live = gen_host.lower()
    assert _seat.short_host() == "other"            # the NAME is not the maps' key
    assert _seat._postmap_lookup("alpha@other", [f"hash:{maps / 'vmailbox'}"]) is None

    found = _seat.box_via_map("alpha")
    assert found is not None and found[0] == base / "agents" / "alpha", found
    assert f"alpha@{live} -> agents/alpha/" in found[1]
    rang, why = _seat.doorbell("alpha+sm-1.worker", "sess", dict(os.environ))
    assert rang is True, why
    assert f"alpha+sm-1.worker@{live} -> this box" in why
    assert _subscribe.row("projects/widget") == f"row: widget@{live} -> sess@{gen_host}"


def test_a_seat_address_under_a_dotted_name_is_local(monkeypatch):
    """`_seat.local_domain` compares the domain to the whole NAME, so a
    dotted name (row N4) is local, and so is anything under it."""
    hostname(monkeypatch, LIVE)
    monkeypatch.setenv("SESSIONMAIL_HOST", "Foo.Local")
    assert _seat.local_domain("alpha+t.worker@foo.local")
    assert _seat.local_domain("alpha+t.worker@FOO.local.tail.example")
    assert not _seat.local_domain("alpha+t.worker@foo")
    assert not _seat.local_domain("name+tag@gmail.com")


# ── IS-ME: rows I1-I14 ───────────────────────────────────────────────────────

D6_PINNED = [
    ("I1", "MacBook-Air", True),
    ("I2", "admins-Air", True),
    ("I3", "admins-air.localdomain", True),
    ("I4", "admins-MacBook-Air", True),
    ("I5", "admins-macbook-air.local", True),
    ("I6", "localhost", True),
    ("I9", "elsewhere.example.invalid", False),
    ("I10", "macbook", False),
    ("I11", "macbook-airx", False),
]


@pytest.mark.parametrize("row,domain,me", D6_PINNED, ids=[r[0] for r in D6_PINNED])
def test_is_me_on_the_d6_machine_pinned(monkeypatch, scutil_bin, row, domain, me):
    hostname(monkeypatch, D6_HOSTNAME)
    pin("MacBook-Air")
    assert _host.is_me(domain, d6_env(scutil_bin)) is me, (row, _host.names(d6_env(scutil_bin)))


def test_i7_unpinned_the_install_name_is_the_computer_name_slugged(monkeypatch, scutil_bin):
    hostname(monkeypatch, D6_HOSTNAME)
    pin(None)
    env = d6_env(scutil_bin)
    assert _host.name(env) == "admins-air"
    assert _host.names(env) == {"admins-air", "admins-macbook-air", "macbook-air", "localhost"}
    assert _host.is_me("MacBook-Air", env)


def test_i8_a_stored_mixed_case_address_is_accepted_and_written_lowercase(monkeypatch, scutil_bin):
    """Lowercasing changes the WRITTEN form; an address stored mixed-case is
    still this host, and the drop resolves it under the NAME."""
    hostname(monkeypatch, D6_HOSTNAME)
    pin("MacBook-Air")
    env = d6_env(scutil_bin)
    maps = _drop.Maps(Path("/nowhere"), _host.name(env), {}, {}, env=env)
    assert _drop.qualify("alpha+sm-1.worker@MacBook-Air", maps.host, maps.names) \
        == "alpha+sm-1.worker@macbook-air"
    assert _drop.qualify("alpha@admins-MacBook-Air.local", maps.host, maps.names) \
        == "alpha@macbook-air"
    assert _drop.qualify("alpha@elsewhere.example.invalid", maps.host, maps.names) is None
    assert _drop.qualify("alpha@admins-Air", maps.host) is None, "no set: the NAME only, as before"


def test_i12_a_variable_live_and_localhost(monkeypatch, tmp_path):
    hostname(monkeypatch, LIVE)
    env = {"SESSIONMAIL_HOST": "pinned", "PATH": str(tmp_path)}      # no scutil there
    assert _host.names(env) == {"pinned", "live", "localhost"}
    assert all(_host.is_me(d, env) for d in ("pinned", "live", "localhost"))


def test_i12_the_setting_stays_a_name_under_a_variable(monkeypatch, tmp_path):
    hostname(monkeypatch, LIVE)
    pin("MacBook-Air")
    env = {"SESSIONMAIL_HOST": "pinned", "PATH": str(tmp_path)}
    assert _host.name(env) == "pinned"
    assert "macbook-air" in _host.names(env)


def test_i13_linux_no_scutil(monkeypatch, tmp_path):
    hostname(monkeypatch, "box1.lan")
    env = {"PATH": str(tmp_path)}
    assert _host.names(env) == {"box1", "localhost"}
    assert _host.is_me("box1", env) and _host.is_me("localhost", env)
    assert not _host.is_me("MacBook-Air", env)


def test_i14_no_at_sign_stays_local(monkeypatch):
    assert _drop.qualify("Alpha", "macbook-air", lambda: frozenset()) == "alpha@macbook-air"


def test_the_set_is_asked_only_for_a_domain_that_is_not_the_name(monkeypatch):
    """Lazily: mail to the NAME runs no `scutil`; a send asks the set once."""
    calls: list[str] = []
    monkeypatch.setattr(_host, "names", lambda env=None: calls.append("names") or frozenset({"b"}))
    maps = _drop.Maps(Path("/nowhere"), "a", {}, {})
    assert _drop.qualify("x@a", maps.host, maps.names) == "x@a"
    assert calls == []
    assert _drop.qualify("x@b", maps.host, maps.names) == "x@a"
    assert _drop.qualify("y@b.lan", maps.host, maps.names) == "y@a"
    assert calls == ["names"]


# ── the parts: slug, scutil, the seam ────────────────────────────────────────

@pytest.mark.parametrize("given,want", [
    ("MacBook Air", "macbook-air"),
    ("Alex's MacBook Air", "alexs-macbook-air"),
    ("Alex’s MacBook Air", "alexs-macbook-air"),
    ("  Ünïcode  Box!! ", "unicode-box"),
    ("---", ""),
    ("", ""),
])
def test_slug(given, want):
    assert _host.slug(given) == want


def test_scutil_answers_and_refusals(scutil_bin):
    env = d6_env(scutil_bin)
    assert _host.scutil_get("LocalHostName", env) == D6_LOCAL_HOST_NAME
    assert _host.scutil_get("ComputerName", env) == D6_COMPUTER_NAME
    assert _host.scutil_get("LocalHostName", {**env, "FAKE_LHN": ""}) == "", "not set"
    assert _host.scutil_get("HostName", env) == ""


def test_scutil_is_bounded(tmp_path, monkeypatch):
    d = tmp_path / "slow"
    d.mkdir()
    write_fake(d / "scutil", "#!{python}\nimport time\ntime.sleep(5)\n")
    monkeypatch.setattr(_host, "SCUTIL_TIMEOUT", 0.2)
    assert _host.scutil_get("LocalHostName", {"PATH": str(d)}) == ""


def test_the_shipped_resolver_takes_path_then_the_system_scutil(tmp_path, scutil_bin, monkeypatch):
    assert REAL_SCUTIL_BINARY({"PATH": str(scutil_bin)}) == str(scutil_bin / "scutil")
    monkeypatch.setattr(_host, "SCUTIL", str(tmp_path / "no-scutil-here"))
    assert REAL_SCUTIL_BINARY({"PATH": str(tmp_path / "empty")}) is None
    monkeypatch.setattr(_host, "SCUTIL", str(scutil_bin / "scutil"))
    assert REAL_SCUTIL_BINARY({"PATH": str(tmp_path / "empty")}) == str(scutil_bin / "scutil")


@pytest.mark.skipif(not os.path.isfile(_host.SCUTIL), reason="no system scutil on this host")
def test_the_suite_seam_never_answers_with_the_hosts_own_scutil():
    assert _host.scutil_binary is fake_scutil_only
    assert fake_scutil_only({"PATH": os.path.dirname(_host.SCUTIL)}) is None
    assert _host.scutil_get("LocalHostName", {"PATH": os.path.dirname(_host.SCUTIL)}) == ""


# ── writers refuse an empty name ─────────────────────────────────────────────

def test_the_sender_refuses_when_this_host_has_no_name(monkeypatch, tmp_path):
    hostname(monkeypatch, "")
    body = tmp_path / "body.md"
    body.write_text("hello\n")
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path), "SESSIONMAIL_SENDMAIL": "drop",
           "SESSIONMAIL_ROOT": str(tmp_path / "agents")}
    rc, out, err = _sender.run(["alpha", "--from", "beta", "--subject", "s", "--body-file",
                                str(body)], env)
    assert (rc, out) == (_sender.EX_REFUSED, "")
    assert "this host has no name - refusing to send" in err and "NOTHING WAS SENT" in err
    assert not (tmp_path / "agents").exists()


def test_the_drop_refuses_when_this_host_has_no_name(monkeypatch, tmp_path):
    hostname(monkeypatch, "")
    with pytest.raises(_drop.NotDelivered, match="this host has no name"):
        _drop.prepare({"HOME": str(tmp_path), "SESSIONMAIL_ROOT": str(tmp_path / "agents")})


# ── the pin `ensure` writes at creation ──────────────────────────────────────

def test_ensure_pins_the_live_name_lowercased_when_it_creates_the_file(monkeypatch, tmp_path):
    hostname(monkeypatch, "MacBook-Air.local")
    settings, created = _settings.ensure({"HOME": str(tmp_path)})
    assert (settings.state, created) == (_settings.OFF, True)
    text = _settings.path({"HOME": str(tmp_path)}).read_text()
    assert json.loads(text) == {"version": 1, "hooks": False, "sendmail": "drop",
                                "host": "macbook-air"}
    assert text == _settings.template().replace('"drop"}', '"drop", "host": "macbook-air"}')


def test_ensure_pins_nothing_when_the_hostname_is_empty(monkeypatch, tmp_path):
    hostname(monkeypatch, "")
    _settings.ensure({"HOME": str(tmp_path)})
    assert _settings.path({"HOME": str(tmp_path)}).read_text() == _settings.template()


def test_the_conversion_adds_no_pin(monkeypatch, tmp_path):
    hostname(monkeypatch, "MacBook-Air.local")
    env = {"HOME": str(tmp_path)}
    old = _settings.old_path(env)
    old.parent.mkdir(parents=True)
    old.write_text('{"version": 1, "hooks": true}\n')
    _settings.ensure(env)
    assert _settings.path(env).read_text() == '{"version": 1, "hooks": true}\n'


def test_an_existing_file_is_never_given_a_pin_by_ensure(monkeypatch, tmp_path):
    hostname(monkeypatch, "MacBook-Air.local")
    env = {"HOME": str(tmp_path)}
    p = _settings.path(env)
    p.parent.mkdir(parents=True)
    p.write_text('{"version": 1, "hooks": true}\n')
    _settings.ensure(env)
    assert p.read_text() == '{"version": 1, "hooks": true}\n'


# ── the writer: one key added, every other byte kept ─────────────────────────

@pytest.mark.parametrize("raw,want", [
    ('{"a": 1}', '{"a": 1, "host": "h"}'),
    ('{"a": 1}\n', '{"a": 1, "host": "h"}\n'),
    ('{"a": 1}  \n\n', '{"a": 1, "host": "h"}  \n\n'),
    ('{}', '{"host": "h"}'),
    ('{\n}\n', '{"host": "h"\n}\n'),
    ('{\n  "a": 1,\n  "b": {"c": [1, 2]}\n}\n', '{\n  "a": 1,\n  "b": {"c": [1, 2]},\n  "host": "h"\n}\n'),
    ('{\n    "a": {\n        "c": 1\n    }\n}', '{\n    "a": {\n        "c": 1\n    },\n    "host": "h"\n}'),
])
def test_with_key_adds_the_key_last_and_keeps_every_other_byte(raw, want):
    assert _settings.with_key(raw.encode(), "host", "h") == want.encode()


def test_with_key_leaves_a_present_key_and_refuses_what_is_not_an_object():
    assert _settings.with_key(b'{"host": ""}', "host", "h") is None
    for bad in (b"[1]", b"not json", b"\xff", b'"x"'):
        with pytest.raises(ValueError):
            _settings.with_key(bad, "host", "h")


def test_add_key_writes_through_a_symlink_and_keeps_the_mode(tmp_path):
    real = tmp_path / "real.json"
    real.write_text('{"a": 1}\n')
    real.chmod(0o640)
    link = tmp_path / "mail.json"
    link.symlink_to(real)
    assert _settings.add_key(link, "host", "h") is True
    assert link.is_symlink() and real.read_text() == '{"a": 1, "host": "h"}\n'
    assert real.stat().st_mode & 0o777 == 0o640
    assert _settings.add_key(link, "host", "other") is False
    assert real.read_text() == '{"a": 1, "host": "h"}\n'
    assert sorted(os.listdir(tmp_path)) == ["mail.json", "real.json"], "no temp file left"
    with pytest.raises(FileNotFoundError):
        _settings.add_key(tmp_path / "missing.json", "host", "h")


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
