"""SM-941 repro: the mail drop and "this host" after the hostname changes.

RED FIRST, then green. repro-001 wrote eight of these as strict xfails, each
a failure measured on master 306ddc4 (the refusal text is kept below and in
the slice notes); fix-001 (`_host`, the one host rule) turned them into
plain tests. The four that passed at 306ddc4 guard what the fix must not
break. The rule's own table of cases is `tests/test_host_rule.py`.

(a) Case D6 (a fresh MacBook Air, 2026-10-25). Mail was
    addressed `...@MacBook-Air`, the name the host had at install. The
    hostname then changed under the running seats to `admins-Air.localdomain`
    (LocalHostName `admins-MacBook-Air`), and the drop refused the mail:

        'alpha@MacBook-Air' is not on this host (the drop delivers locally
        only). NOTHING WAS DELIVERED, to this recipient or any other.

    (`_drop.plan`, src/sessionmail/_drop.py:386 @ 306ddc4.) With no `host`
    setting, "this host" follows the LIVE hostname (`_drop.short_host`,
    _drop.py:166-173 @ 306ddc4).

(b) SM-326: three host functions, three rules (all @ 306ddc4):

    - `_sender.host_of(env)`, _sender.py:111-114: SESSIONMAIL_HOST, else the
      settings `host`, else the short hostname, case KEPT.
    - `_drop.short_host(env)`, _drop.py:166-173: SESSIONMAIL_HOST or
      SESSION_MAIL_HOST, else the settings `host`, else the short hostname,
      LOWERCASED.
    - `_seat.short_host()`, _seat.py:702-704: the short hostname only, no
      variable, no settings, LOWERCASED. `_seat.local_domain` (_seat.py:182)
      judges "is this address on this host" with it.

Safety (the slice's rule): nothing here reads or changes this host's real
names or settings. `socket.gethostname` is monkeypatched, the settings home is
the suite's sandbox (conftest `hermetic`), the mail root is tmp_path, and a
fake `scutil` stands first on PATH for the macOS names. No probe of
LocalHostName or ComputerName exists in the code at 306ddc4 (`grep -rn scutil
src` is empty); the fake is there so a fix that asks `scutil` on PATH is
answered by it, never by the host. A fix that runs `/usr/sbin/scutil` by its
absolute path must give the tests a seam instead.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

from conftest import write_mail_settings
from fakes import write_fake
from sessionmail import _drop, _operator, _seat, _sender

EX_OK, EX_MTA = 0, 3
SID = "0000aaaa-1111-2222-3333-444455556666"

#: The D6 names, as measured on the fresh Air.
INSTALL_NAME = "MacBook-Air"                  # the name at install; mail is addressed to it
LIVE_HOSTNAME = "admins-Air.localdomain"      # `hostname` after the change
LOCAL_HOST_NAME = "admins-MacBook-Air"        # scutil --get LocalHostName after it
COMPUTER_NAME = "MacBook Air"                 # scutil --get ComputerName (slugs to macbook-air)

#: A map generator that, unlike test_mail_drop's, honours `--host`: its rows
#: are written for the host the drop asks for, as the real generator's are.
FAKE_GEN = """#!{python}
import sys
args = sys.argv[1:]
host = args[args.index("--host") + 1] if "--host" in args else "nohost"
sid = "%s"
if "--capabilities" in args:
    sys.stdout.write("no-fqdn-v1\\n")
elif "--print" in args:
    for name in ("alpha", "beta", sid):
        sys.stdout.write(f"{{name}}@{{host}}\\tagents/{{name}}/\\n")
elif "--print-valias" in args:
    sys.stdout.write(f"alpha@{{host}}\\talpha@{{host}}, {{sid}}@{{host}}\\n")
""" % SID

#: `scutil --get <name>` as macOS answers it, from the D6 measurement.
FAKE_SCUTIL = """#!{python}
import sys
names = {{"LocalHostName": "%s", "ComputerName": "%s", "HostName": None}}
args = sys.argv[1:]
if len(args) == 2 and args[0] == "--get" and args[1] in names:
    if names[args[1]] is None:
        sys.stdout.write(args[1] + ": not set\\n")
        sys.exit(1)
    sys.stdout.write(names[args[1]] + "\\n")
    sys.exit(0)
sys.exit(1)
""" % (LOCAL_HOST_NAME, COMPUTER_NAME)


@pytest.fixture
def mailroot(tmp_path):
    root = tmp_path / "mailroot"
    for box in ("alpha", "beta", SID):
        for d in ("cur", "new", "tmp"):
            (root / "agents" / box / d).mkdir(parents=True)
    return root


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    write_fake(d / "vmailbox-gen", FAKE_GEN)
    write_fake(d / "scutil", FAKE_SCUTIL)
    return d


@pytest.fixture
def renamed(monkeypatch):
    """The live hostname is the AFTER name: the change D6 measured. Neither
    host variable is set (the suite's `hermetic` drops SESSIONMAIL_*; the
    old spelling is dropped here)."""
    monkeypatch.setattr(socket, "gethostname", lambda: LIVE_HOSTNAME)
    monkeypatch.delenv("SESSION_MAIL_HOST", raising=False)
    monkeypatch.delenv("SESSIONMAIL_HOST", raising=False)


@pytest.fixture
def env(tmp_path, bin_dir, mailroot, renamed):
    """A drop host with NO host variable: "this host" is the settings, else
    the live hostname - the only state in which the hostname can move it."""
    return {
        "HOME": str(tmp_path),
        "PATH": str(bin_dir),
        "TMPDIR": str(tmp_path),
        "SESSIONMAIL_ROOT": str(mailroot / "agents"),
        "SESSIONMAIL_SENDMAIL": "drop",
        "SESSIONMAIL_VMAILBOX_GEN": str(bin_dir / "vmailbox-gen"),
    }


def pin(host: str):
    """The settings `host`, as `message setup` would pin it (fix part 2), in
    the suite's sandboxed settings home - never the real one."""
    write_mail_settings(_operator.settings_home(), json.dumps({"host": host}) + "\n")


def body_file(tmp_path: Path) -> str:
    p = tmp_path / "body.md"
    p.write_bytes(b"hello\n")
    return str(p)


def landed(mailroot: Path) -> set[str]:
    return {box.name for box in (mailroot / "agents").iterdir()
            if any((box / "new").iterdir()) or any((box / "tmp").iterdir())}


def send(env, tmp_path, *to):
    return _sender.run([*to, "--from", "beta", "--subject", "SM-941 drop host",
                        "--body-file", body_file(tmp_path)], env)


# ── (a) D6: the drop after the hostname changes ──────────────────────────────

def test_d6_unpinned_mail_to_the_install_name_is_delivered_after_the_rename(
        env, tmp_path, mailroot):
    """The D6 host exactly: setup wrote no host, the hostname changed. The
    install-time name is still a name this machine has (its ComputerName,
    slugged), so the drop must take it as local (fix part 3, ACCEPT)."""
    rc, _, err = send(env, tmp_path, f"alpha@{INSTALL_NAME}")
    assert rc == EX_OK, err
    assert landed(mailroot) == {"alpha", SID}


def test_d6_a_pinned_host_survives_the_rename(env, tmp_path, mailroot):
    """With the host pinned in settings (what fix part 2 makes setup write),
    the rename no longer moves "this host", and mail to the pinned name
    lands. This passes at 306ddc4 already: the guard the pin relies on."""
    pin(INSTALL_NAME)
    rc, _, err = send(env, tmp_path, f"alpha@{INSTALL_NAME}")
    assert rc == EX_OK, err
    assert landed(mailroot) == {"alpha", SID}


def test_d6_pinned_mail_to_the_live_hostname_is_delivered_too(env, tmp_path, mailroot):
    """Pinned to the install name, the live short hostname is still a name
    this machine has (fix part 3)."""
    pin(INSTALL_NAME)
    rc, _, err = send(env, tmp_path, f"alpha@{LIVE_HOSTNAME.split('.')[0]}")
    assert rc == EX_OK, err
    assert landed(mailroot) == {"alpha", SID}


def test_d6_mail_to_the_local_host_name_is_delivered(env, tmp_path, mailroot):
    rc, _, err = send(env, tmp_path, f"alpha@{LOCAL_HOST_NAME}")
    assert rc == EX_OK, err
    assert landed(mailroot) == {"alpha", SID}


def test_d6_mail_to_localhost_is_delivered(env, tmp_path, mailroot):
    rc, _, err = send(env, tmp_path, "alpha@localhost")
    assert rc == EX_OK, err
    assert landed(mailroot) == {"alpha", SID}


def test_d6_a_name_this_machine_never_had_is_still_refused(env, tmp_path, mailroot):
    """Generous, not open: a domain that is none of this machine's names
    refuses by name, before and after the fix."""
    pin(INSTALL_NAME)
    rc, _, err = send(env, tmp_path, "alpha@elsewhere.example.invalid")
    assert rc == EX_MTA
    assert "'alpha@elsewhere.example.invalid' is not on this host" in err
    assert landed(mailroot) == set()


# ── (b) SM-326: one host rule ────────────────────────────────────────────────

def hosts(env) -> dict[str, str]:
    """Every host function's answer, each by its signature at 306ddc4."""
    return {"_sender.host_of": _sender.host_of(env),
            "_drop.short_host": _drop.short_host(env),
            "_seat.short_host": _seat.short_host()}


@pytest.fixture
def live(monkeypatch):
    """os.environ is what `_seat.short_host()` would read if it read one; the
    other two are handed the same mapping. Neither host variable is set."""
    monkeypatch.delenv("SESSION_MAIL_HOST", raising=False)
    monkeypatch.delenv("SESSIONMAIL_HOST", raising=False)
    import os
    return os.environ


def test_sm326_the_variable_is_honoured_by_every_host_function(live, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "live.example.invalid")
    monkeypatch.setenv("SESSIONMAIL_HOST", "pinned")
    assert len(set(hosts(live).values())) == 1, hosts(live)


def test_sm326_every_host_function_folds_case_alike(live, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Air.local")
    assert len(set(hosts(live).values())) == 1, hosts(live)


def test_sm326_the_settings_host_is_honoured_by_every_host_function(live, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: LIVE_HOSTNAME)
    pin(INSTALL_NAME)
    assert len(set(hosts(live).values())) == 1, hosts(live)


def test_sm326_a_seat_keeps_its_pinned_address_local_after_the_rename(live, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: LIVE_HOSTNAME)
    pin(INSTALL_NAME)
    assert _seat.local_domain(f"alpha+sm-1.worker@{INSTALL_NAME}")


def test_sm326_with_no_variable_and_no_settings_all_agree_on_a_lowercase_name(
        live, monkeypatch):
    """The one state they agree in today: a lowercase hostname, nothing set.
    A guard, so the fix cannot break the common case."""
    monkeypatch.setattr(socket, "gethostname", lambda: "ms9.example.invalid")
    assert set(hosts(live).values()) == {"ms9"}, hosts(live)


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
