"""SM-660 design-001 A: the generator's print-only `--no-fqdn` mode.

A host with no MagicDNS suffix and no tailscale cannot ask the generator for
its maps today: it asks for the suffix before any mode runs and exits 4. The
mail drop reads only short-host rows (`_drop.qualify`), so it needs none of
the `@<fqdn>` rows the suffix is for. `--no-fqdn` is the print modes' way to
say so. What is pinned here:

- with `--no-fqdn` the suffix is NEVER asked: a fake tailscale that WOULD
  answer is first on PATH and is never run (its call log stays absent), and
  the same run without the flag is shown asking it, so the silence means
  something;
- the output contract: `--print --no-fqdn` is exactly `--print` less every
  line keyed `@<fqdn>`, and the same for `--print-valias`, over fixtures that
  carry every row kind (seat, plus, subscription, static in three forms);
- a WRITING run given `--no-fqdn` exits 2 and writes nothing; a writing run
  WITHOUT it and with no suffix still exits 4 and writes nothing;
- `--capabilities` lists `no-fqdn-v1` (pinned beside the others in
  `test_postfix_vmailbox_gen.py`).

Fixtures are `testhost` and the boxes of the generator suite (convention 9).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from test_postfix_vmailbox_gen import (  # noqa: F401  (fixtures, used by name)
    EXPECTED_MAP, FQDN, HOST, PLUS, _maildir, _run, _subscription_fleet, _valias, generator,
    mail_root, needs_jq,
)

SYSTEM_PATH = "/usr/bin:/bin"
#: The suffix the fake tailscale answers: not the fixtures' SUFFIX, so a row
#: built from it can only have come from asking tailscale.
TAILNET = "tail.example"


@pytest.fixture(autouse=True)
def _no_system_tailscale() -> None:
    assert shutil.which("tailscale", path=SYSTEM_PATH) is None, (
        "the system dirs carry a tailscale: these tests could not hide it")


def fake_tailscale(bin_dir: Path, calls: Path) -> Path:
    """A tailscale that WOULD answer TAILNET, and logs every run to CALLS."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "tailscale"
    path.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f"printf '{{\"MagicDNSSuffix\": \"{TAILNET}\"}}\\n'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def closed_env(tmp_path: Path, path: str) -> dict[str, str]:
    """No suffix anywhere: MAGICDNS_SUFFIX unset, the app-bundle seam empty,
    and PATH exactly PATH (the system dirs carry no tailscale)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": path, "HOME": str(home), "TMPDIR": str(tmp_path), "LC_ALL": "C",
            "MAGICDNS_TAILSCALE_BUNDLE": ""}


def gen(generator: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(generator), *args], capture_output=True, text=True, env=env,
                          check=False)


def less_fqdn(text: str) -> str:
    """TEXT less every line keyed `@<fqdn>`: the contract, by its definition."""
    return "".join(line + "\n" for line in text.splitlines()
                   if not line.split("\t", 1)[0].endswith("@" + FQDN))


def keys(text: str) -> list[str]:
    return [line.split("\t", 1)[0] for line in text.splitlines()]


# ── the suffix is never asked ────────────────────────────────────────────────

def test_print_with_no_fqdn_asks_no_suffix_and_prints_short_rows_only(
        generator: Path, tmp_path: Path, mail_root: Path) -> None:
    calls = tmp_path / "tailscale-calls"
    fake_tailscale(tmp_path / "fakes", calls)
    env = closed_env(tmp_path, f"{tmp_path / 'fakes'}:{SYSTEM_PATH}")
    args = ("--print", "--host", HOST, "--root", str(mail_root))

    done = gen(generator, env, *args, "--no-fqdn")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "".join(line + "\n" for line in EXPECTED_MAP.splitlines()
                                  if f"@{HOST}\t" in line)
    assert not calls.exists(), "--no-fqdn ran tailscale"

    # The fake is live: without the flag the generator asks it, and builds
    # its FQDN rows from the answer.
    done = gen(generator, env, *args)
    assert done.returncode == 0, done.stderr
    assert calls.exists()
    assert f"foreman.sm-74@{HOST}.{TAILNET}\tagents/foreman.sm-74/\n" in done.stdout


def test_print_with_no_suffix_and_no_flag_still_exits_4(
        generator: Path, tmp_path: Path, mail_root: Path) -> None:
    """Today's refusal, unchanged: the flag is what lifts it, nothing else."""
    done = gen(generator, closed_env(tmp_path, SYSTEM_PATH),
               "--print", "--host", HOST, "--root", str(mail_root))
    assert done.returncode == 4
    assert done.stdout == ""
    assert "set MAGICDNS_SUFFIX" in done.stderr


@needs_jq
def test_print_valias_with_no_fqdn_asks_no_suffix(generator: Path, tmp_path: Path) -> None:
    _subscription_fleet(tmp_path)
    _maildir(tmp_path / "root" / "agents" / "alpha")
    calls = tmp_path / "tailscale-calls"
    fake_tailscale(tmp_path / "fakes", calls)
    done = _valias(
        generator, tmp_path, [{"session_id": "s1", "agent": "alpha"}], {"s1": PLUS}, ["s1"],
        "--no-fqdn",
        env_over={"MAGICDNS_SUFFIX": None, "MAGICDNS_TAILSCALE_BUNDLE": "",
                  "PATH": f"{tmp_path / 'fakes'}:{tmp_path / 'bin'}:{os.environ['PATH']}"})
    assert done.returncode == 0, done.stderr
    assert not calls.exists(), "--no-fqdn ran tailscale"
    assert done.stdout
    assert all(k.endswith("@" + HOST) for k in keys(done.stdout)), done.stdout
    assert f"alpha@{HOST}\talpha@{HOST}, s1@{HOST}\n" in done.stdout
    assert f"{PLUS}@{HOST}\t" in done.stdout


# ── the output contract: the full output less its FQDN-keyed lines ───────────

def test_print_no_fqdn_is_print_less_every_fqdn_line(
        generator: Path, tmp_path: Path, mail_root: Path) -> None:
    args = ("--print", "--host", HOST, "--root", str(mail_root))
    full = _run(generator, tmp_path, *args)
    short = _run(generator, tmp_path, *args, "--no-fqdn")
    assert full.returncode == short.returncode == 0, (full.stderr, short.stderr)
    assert full.stdout == EXPECTED_MAP
    assert short.stdout == less_fqdn(full.stdout)
    assert short.stdout != full.stdout          # the pin can fail: rows were dropped


@needs_jq
def test_print_valias_no_fqdn_is_print_valias_less_every_fqdn_line(
        generator: Path, tmp_path: Path) -> None:
    """Every row kind at once: seat rows and their +memory lines, a declared
    plus address, seat and session subscriptions (bare, plus, store/project),
    and a static file keying a bare name, an `@<host>` and an `@<fqdn>`."""
    _subscription_fleet(tmp_path)
    for sid in ("s1", "s2"):                     # made once: the helper runs twice
        _maildir(tmp_path / "root" / "agents" / sid)
    _maildir(tmp_path / "root" / "agents" / "alpha")
    static = tmp_path / "valias.static"
    static.write_text(f"atomnote@{HOST}  beta@{HOST}\n"
                      f"bare  alpha\n"
                      f"fq@{FQDN}  eve@{FQDN}\n"
                      f"elsewhere@otherhost  alpha\n")

    def run(*extra: str) -> subprocess.CompletedProcess:
        return _valias(
            generator, tmp_path,
            [{"session_id": "s1", "agent": "alpha"}, {"session_id": "s2", "agent": "eve"}],
            {"s1": PLUS}, [], "--valias-static", str(static), *extra,
            subs={"sessions/s2/subscriptions/voicemode+kaizen": "",
                  "sessions/s2/subscriptions/work--demo": "",
                  "sessions/s2/subscriptions/work": ""})

    full, short = run(), run("--no-fqdn")
    assert full.returncode == short.returncode == 0, (full.stderr, short.stderr)
    assert f"fq@{HOST}\teve@{FQDN}\n" in full.stdout          # the fixture reaches the arm
    assert f"{PLUS}@{FQDN}\t" in full.stdout
    assert short.stdout == less_fqdn(full.stdout)
    assert short.stdout != full.stdout
    assert not [k for k in keys(short.stdout) if k.endswith("@")]


@needs_jq
def test_a_static_key_under_this_host_is_keyed_short_when_the_suffix_is_unknown(
        generator: Path, tmp_path: Path) -> None:
    """With no suffix asked, `<host>.<anything>` is this host, as the drop
    reads an address: the static key is kept, by its short form only."""
    static = tmp_path / "valias.static"
    static.write_text(f"fq@{HOST}.{TAILNET}  eve\nfar@otherhost.{TAILNET}  eve\n")
    done = _valias(generator, tmp_path, [{"session_id": "s1"}], {}, ["s1"],
                   "--valias-static", str(static), "--no-fqdn",
                   env_over={"MAGICDNS_SUFFIX": None})
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"far@otherhost.{TAILNET}\teve\nfq@{HOST}\teve\n"


def test_plus_rows_with_no_fqdn_prints_the_short_pair_only(
        generator: Path, tmp_path: Path) -> None:
    env = closed_env(tmp_path, SYSTEM_PATH)
    done = gen(generator, env, "--plus-rows", PLUS, "s1", "--host", HOST, "--no-fqdn")
    assert done.returncode == 0, done.stderr
    base = PLUS.split("+", 1)[0]
    assert done.stdout == f"{PLUS}@{HOST}\t{base}@{HOST}\n{PLUS}@{HOST}\ts1@{HOST}\n"
    # The bare-base guard still refuses, flag or not.
    done = gen(generator, env, "--plus-rows", base, "s1", "--host", HOST, "--no-fqdn")
    assert done.returncode == 3 and done.stdout == ""


# ── writing runs: refused with the flag, unchanged without it ────────────────

@pytest.mark.parametrize("mode", [(), ("--valias-only",), ("--no-valias",)],
                         ids=["both-maps", "valias-only", "no-valias"])
def test_a_writing_run_with_no_fqdn_exits_2_and_writes_nothing(
        generator: Path, tmp_path: Path, mail_root: Path, mode: tuple[str, ...]) -> None:
    maps = tmp_path / "maps"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    done = _run(generator, tmp_path, *mode, "--no-fqdn", "--no-postmap", "--out",
                str(maps / "vmailbox"), "--host", HOST, "--root", str(mail_root))
    assert done.returncode == 2
    assert "--no-fqdn" in done.stderr and "nothing written" in done.stderr
    assert not maps.exists()
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")
                   if p.relative_to(tmp_path) != Path("home"))
    assert after == [p for p in before if p != Path("home")]


def test_a_writing_run_with_no_suffix_still_exits_4_and_writes_nothing(
        generator: Path, tmp_path: Path, mail_root: Path) -> None:
    maps = tmp_path / "maps"
    done = gen(generator, closed_env(tmp_path, SYSTEM_PATH), "--no-postmap", "--out",
               str(maps / "vmailbox"), "--host", HOST, "--root", str(mail_root))
    assert done.returncode == 4
    assert "nothing written" in done.stderr
    assert not maps.exists()


def test_the_flag_is_on_the_help_page(generator: Path, tmp_path: Path) -> None:
    done = gen(generator, closed_env(tmp_path, SYSTEM_PATH), "--help")
    assert done.returncode == 0
    assert "--no-fqdn" in done.stdout and "no-fqdn-v1" in done.stdout
