"""SM-674: the MagicDNS suffix is derived at runtime, from ONE definition.

`postfix/magicdns-suffix` is the definition; `postfix/vmailbox-gen` runs it
from its own directory, and `_magicdns` (behind `_voice.host_forms`) runs it
from the package path. Its order: MAGICDNS_SUFFIX, else `tailscale status
--json`'s MagicDNSSuffix (tailscale on PATH, then the macOS app bundle's CLI),
else a LOUD failure naming the fix. No default suffix exists anywhere.

Every source is covered for every consumer: the helper itself, the generator
and the pager's host forms. No test here touches a real tailscale: each
builds its own PATH (a dir of fakes, then /usr/bin:/bin) and points the
bundle seam (MAGICDNS_TAILSCALE_BUNDLE) at a fake or at nothing.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from sessionmail import _magicdns, _voice

SRC = Path(_magicdns.__file__).parent
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
HOST = "testhost"
FIX = "set MAGICDNS_SUFFIX"
TAILSCALE_FIX = "install/start tailscale"


@pytest.fixture(scope="module")
def helper() -> Path:
    """The helper as the package ships it: package data, executable."""
    with as_file(files("sessionmail").joinpath("postfix/magicdns-suffix")) as path:
        yield Path(path)


@pytest.fixture(scope="module")
def generator() -> Path:
    with as_file(files("sessionmail").joinpath("postfix/vmailbox-gen")) as path:
        yield Path(path)


def fake_tailscale(path: Path, suffix: str | None = "tail.example", *,
                   hang: bool = False, fail: bool = False, calls: Path | None = None) -> Path:
    """A `tailscale` CLI that answers `status --json` with SUFFIX (None: the
    key is absent), or hangs, or fails. CALLS, when given, gets a line per run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh"]
    if calls is not None:
        lines.append(f'echo "$*" >> "{calls}"')
    lines.append('[ "$1 $2" = "status --json" ] || { echo "bad args: $*" >&2; exit 64; }')
    if hang:
        lines.append("exec sleep 30")
    elif fail:
        lines.append('echo "failed to connect to local tailscaled" >&2; exit 1')
    else:
        body = (f'  "MagicDNSSuffix": "{suffix}",' if suffix is not None else "")
        lines += ["cat <<'EOF'", "{", '  "Version": "1.0",', '  "BackendState": "Running",',
                  body, '  "CurrentTailnet": {', f'    "MagicDNSSuffix": "{suffix or ""}"',
                  "  }", "}", "EOF"]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def env_of(tmp_path: Path, *, suffix: str | None = None, path_dir: Path | None = None,
           bundle: Path | str | None = None, timeout: str | None = None) -> dict[str, str]:
    """An environment with nothing of the runner's in it: PATH is PATH_DIR
    (fakes) then the system dirs, which carry no tailscale; the bundle seam
    names BUNDLE, else a path that does not exist."""
    bin_dir = path_dir if path_dir is not None else tmp_path / "empty-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{bin_dir}:{SYSTEM_PATH}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "LC_ALL": "C",
        "MAGICDNS_TAILSCALE_BUNDLE": str(bundle if bundle is not None
                                         else tmp_path / "no-app" / "Tailscale"),
    }
    if suffix is not None:
        env["MAGICDNS_SUFFIX"] = suffix
    if timeout is not None:
        env["MAGICDNS_TAILSCALE_TIMEOUT"] = timeout
    return env


def run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False, timeout=60)


def test_the_system_dirs_carry_no_tailscale():
    """The premise of every "no tailscale" test below. If this host ever
    puts one in /usr/bin or /bin, those tests would be asking it."""
    for d in SYSTEM_PATH.split(":"):
        assert not (Path(d) / "tailscale").exists(), d


# -- the helper: the one definition ------------------------------------------

def test_helper_ships_as_executable_package_data(helper):
    assert helper.is_file()
    assert os.access(helper, os.X_OK)


def test_helper_env_wins_over_the_runtime(helper, tmp_path):
    bins = tmp_path / "bin"
    calls = tmp_path / "calls"
    fake_tailscale(bins / "tailscale", "runtime.example", calls=calls)
    got = run([str(helper)], env_of(tmp_path, suffix="env.example", path_dir=bins))
    assert (got.returncode, got.stdout) == (0, "env.example\n")
    assert not calls.exists(), "tailscale was asked although MAGICDNS_SUFFIX was set"


def test_helper_env_is_trimmed_and_folded(helper, tmp_path):
    got = run([str(helper)], env_of(tmp_path, suffix="  Env.EXAMPLE.  "))
    assert (got.returncode, got.stdout) == (0, "env.example\n")


def test_helper_refuses_a_malformed_env_suffix_and_never_asks_tailscale(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "runtime.example")
    got = run([str(helper)], env_of(tmp_path, suffix="not a/suffix", path_dir=bins))
    assert got.returncode == 1
    assert got.stdout == ""
    assert "MAGICDNS_SUFFIX=not a/suffix is not a DNS suffix" in got.stderr


def test_helper_blank_env_falls_through_to_the_runtime(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "Runtime.Example")
    got = run([str(helper)], env_of(tmp_path, suffix="   ", path_dir=bins))
    assert (got.returncode, got.stdout) == (0, "runtime.example\n")


def test_helper_asks_tailscale_on_path_when_env_is_unset(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "runtime.example")
    got = run([str(helper), "--trace"], env_of(tmp_path, path_dir=bins))
    assert (got.returncode, got.stdout) == (0, "runtime.example\n")
    assert f"answered by PATH {bins / 'tailscale'}" in got.stderr


def test_helper_falls_back_to_the_app_bundle_when_path_has_none(helper, tmp_path):
    bundle = fake_tailscale(tmp_path / "Tailscale.app/Contents/MacOS/Tailscale", "bundle.example")
    got = run([str(helper), "--trace"], env_of(tmp_path, bundle=bundle))
    assert (got.returncode, got.stdout) == (0, "bundle.example\n")
    assert f"answered by app bundle {bundle}" in got.stderr


def test_helper_falls_back_to_the_app_bundle_when_path_tailscale_fails(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", fail=True)
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", "bundle.example")
    got = run([str(helper)], env_of(tmp_path, path_dir=bins, bundle=bundle))
    assert (got.returncode, got.stdout) == (0, "bundle.example\n")


def test_helper_bounds_a_hung_tailscale_then_tries_the_bundle(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", hang=True)
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", "bundle.example")
    t0 = time.monotonic()
    got = run([str(helper)], env_of(tmp_path, path_dir=bins, bundle=bundle, timeout="1"))
    took = time.monotonic() - t0
    assert (got.returncode, got.stdout) == (0, "bundle.example\n")
    assert took < 10, f"a hung tailscale held the helper {took:.1f}s"


def test_helper_reports_a_hung_tailscale_as_no_answer(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", hang=True)
    got = run([str(helper)], env_of(tmp_path, path_dir=bins, timeout="1"))
    assert got.returncode == 1
    assert "no answer in 1s" in got.stderr


def test_helper_an_empty_runtime_suffix_is_not_an_answer(helper, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", None)          # logged out: key absent
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", "")
    got = run([str(helper)], env_of(tmp_path, path_dir=bins, bundle=bundle))
    assert got.returncode == 1
    assert got.stdout == ""
    assert got.stderr.count("no MagicDNSSuffix") == 2


def test_helper_with_neither_fails_loudly_naming_where_it_looked_and_the_fix(helper, tmp_path):
    bundle = tmp_path / "no-app" / "Tailscale"
    got = run([str(helper)], env_of(tmp_path, bundle=bundle))
    assert got.returncode == 1
    assert got.stdout == ""
    assert "PATH: no tailscale" in got.stderr
    assert f"app bundle {bundle}: not installed" in got.stderr
    assert FIX in got.stderr and TAILSCALE_FIX in got.stderr


def test_helper_an_empty_bundle_seam_disables_the_bundle(helper, tmp_path):
    got = run([str(helper)], env_of(tmp_path, bundle=""))
    assert got.returncode == 1
    assert "app bundle: not looked for" in got.stderr


def test_helper_runs_under_macos_system_bash(helper, tmp_path):
    """A thin PATH (launchd, hooks) finds /bin/bash, 3.2 on macOS."""
    if not Path("/bin/bash").exists():
        pytest.skip("no /bin/bash")
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "runtime.example")
    got = run(["/bin/bash", str(helper)], env_of(tmp_path, path_dir=bins))
    assert (got.returncode, got.stdout) == (0, "runtime.example\n")
    nothing = run(["/bin/bash", str(helper)], env_of(tmp_path))
    assert nothing.returncode == 1 and FIX in nothing.stderr


# -- the generator ------------------------------------------------------------

def _mail_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for leaf in ("new", "cur", "tmp"):
        (root / "agents" / "eve" / leaf).mkdir(parents=True, exist_ok=True)
    return root


def _rows(generator: Path, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return run([str(generator), "--host", HOST, "--root", str(_mail_root(tmp_path)), "--print"],
               env)


def test_generator_env_wins_over_the_runtime(generator, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "runtime.example")
    got = _rows(generator, tmp_path, env_of(tmp_path, suffix="env.example", path_dir=bins))
    assert got.returncode == 0, got.stderr
    assert got.stdout == (f"eve@{HOST}\tagents/eve/\neve@{HOST}.env.example\tagents/eve/\n")


def test_generator_asks_the_runtime_when_env_is_unset(generator, tmp_path):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "runtime.example")
    got = _rows(generator, tmp_path, env_of(tmp_path, path_dir=bins))
    assert got.returncode == 0, got.stderr
    assert f"eve@{HOST}.runtime.example\tagents/eve/" in got.stdout.splitlines()


def test_generator_asks_the_app_bundle_when_path_has_no_tailscale(generator, tmp_path):
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", "bundle.example")
    got = _rows(generator, tmp_path, env_of(tmp_path, bundle=bundle))
    assert got.returncode == 0, got.stderr
    assert f"eve@{HOST}.bundle.example\tagents/eve/" in got.stdout.splitlines()


def test_generator_with_neither_writes_nothing_and_names_the_fix(generator, tmp_path):
    out_dir = tmp_path / "maps"
    out_dir.mkdir()
    got = run([str(generator), "--host", HOST, "--root", str(_mail_root(tmp_path)),
               "--out", str(out_dir / "vmailbox"), "--no-postmap"], env_of(tmp_path))
    assert got.returncode == 4
    assert got.stdout == ""
    assert list(out_dir.iterdir()) == [], "the generator wrote with no suffix"
    last = got.stderr.strip().splitlines()[-1]      # what `_seat._run_generator` reports
    assert "nothing written" in last and FIX in last and TAILSCALE_FIX in last


@pytest.mark.parametrize("mode", ["--print", "--print-valias", "--valias-only"])
def test_generator_with_neither_fails_in_every_row_mode(generator, tmp_path, mode):
    out_dir = tmp_path / "maps"
    out_dir.mkdir()
    got = run([str(generator), "--host", HOST, "--root", str(_mail_root(tmp_path)),
               "--out", str(out_dir / "vmailbox"), mode], env_of(tmp_path))
    assert got.returncode == 4
    assert got.stdout == ""
    assert list(out_dir.iterdir()) == []


def test_generator_codec_needs_no_suffix(generator, tmp_path):
    got = run([str(generator), "--subscription-box", "store--proj"], env_of(tmp_path))
    assert (got.returncode, got.stdout) == (0, "store/proj")


def test_generator_through_a_symlink_finds_the_helper_beside_its_real_self(generator, tmp_path):
    """Live, it runs as ~/.mail/.postfix/vmailbox-gen, a symlink into the
    checkout; the helper is beside the TARGET, not the link."""
    link_dir = tmp_path / "dotpostfix"
    link_dir.mkdir()
    (link_dir / "vmailbox-gen").symlink_to(generator)
    # a relative link, one hop further, as a hand-made link would be
    (tmp_path / "again").symlink_to(Path("dotpostfix") / "vmailbox-gen")
    for entry in (link_dir / "vmailbox-gen", tmp_path / "again"):
        got = _rows(entry, tmp_path, env_of(tmp_path, suffix="env.example"))
        assert got.returncode == 0, got.stderr
        assert f"eve@{HOST}.env.example\tagents/eve/" in got.stdout.splitlines()


def test_generator_without_its_helper_writes_nothing(generator, tmp_path):
    alone = tmp_path / "alone"
    alone.mkdir()
    copy = alone / "vmailbox-gen"
    copy.write_bytes(generator.read_bytes())
    copy.chmod(0o755)
    out_dir = tmp_path / "maps"
    out_dir.mkdir()
    got = run([str(copy), "--host", HOST, "--root", str(_mail_root(tmp_path)),
               "--out", str(out_dir / "vmailbox"), "--no-postmap"],
              env_of(tmp_path, suffix="env.example"))
    assert got.returncode == 4
    assert "magicdns-suffix is missing" in got.stderr
    assert list(out_dir.iterdir()) == []


# -- the pager's side: _magicdns and _voice.host_forms -------------------------

def test_python_env_wins_and_forks_nothing(tmp_path, monkeypatch):
    def no_fork(*a, **k):
        raise AssertionError("the helper was run although MAGICDNS_SUFFIX was set")
    monkeypatch.setattr(_magicdns.subprocess, "run", no_fork)
    env = env_of(tmp_path, suffix=" Env.Example. ")
    assert _magicdns.suffix(env) == "env.example"
    assert _voice.host_forms({**env, "SESSIONMAIL_HOST": "HQ"}) == ("hq", "hq.env.example")


def test_python_asks_the_runtime_and_memoises_it(tmp_path):
    bins = tmp_path / "bin"
    calls = tmp_path / "calls"
    fake_tailscale(bins / "tailscale", "runtime.example", calls=calls)
    env = {**env_of(tmp_path, path_dir=bins), "SESSIONMAIL_HOST": "hq"}
    assert _voice.host_forms(env) == ("hq", "hq.runtime.example")
    assert _voice.host_forms(env) == ("hq", "hq.runtime.example")
    assert calls.read_text().splitlines() == ["status --json"], "asked more than once"


def test_python_asks_the_app_bundle(tmp_path):
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", "bundle.example")
    assert _magicdns.suffix(env_of(tmp_path, bundle=bundle)) == "bundle.example"


def test_python_suffix_with_neither_raises_naming_the_fix(tmp_path):
    """`suffix()` is for a caller that cannot go on without one: it raises,
    with the helper's own words. The pager never calls it bare."""
    with pytest.raises(_magicdns.SuffixUnknown) as caught:
        _magicdns.suffix(env_of(tmp_path))
    said = str(caught.value)
    assert FIX in said and TAILSCALE_FIX in said
    assert "PATH: no tailscale" in said


def test_python_lookup_with_neither_never_raises(tmp_path):
    found, why = _magicdns.lookup(env_of(tmp_path))
    assert found is None
    assert FIX in why and "PATH: no tailscale" in why
    assert _magicdns.lookup(env_of(tmp_path, suffix="env.example")) == ("env.example", "")


def test_host_forms_with_neither_are_the_short_host_only_and_say_so(tmp_path, capsys):
    """The review bar, 5b: no FQDN form, and it says so (once a process).
    On an MTA carrier, whose maps need the suffix (the conftest's)."""
    env = {**env_of(tmp_path), "SESSIONMAIL_HOST": "HQ",
           "SESSIONMAIL_SENDMAIL": os.environ["SESSIONMAIL_SENDMAIL"]}
    assert _voice.host_forms(env) == ("hq",)
    assert _voice.host_forms(env) == ("hq",)
    err = capsys.readouterr().err
    assert err.count(_magicdns.UNKNOWN) == 1
    assert "short host only ('hq')" in err


def test_the_pagers_call_sites_never_raise_with_no_suffix(tmp_path, monkeypatch, capsys):
    """Every pager call site of host_forms, with no source answering: none
    raises, and each works on the short host."""
    from email.parser import BytesParser
    from email.policy import default as policy

    from dataclasses import replace

    from sessionmail import _views, pager, rules

    for k, v in {**env_of(tmp_path), "SESSIONMAIL_HOST": "hq"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MAGICDNS_SUFFIX", raising=False)

    me = pager.listener_me(None, "sess-a")                     # pager.py listener_me
    assert me.host == "hq" and me.domains == ("hq",)
    assert me.local("hq.any.example")                          # to:$me still matches
    sc6 = replace(rules.read_settings(), runner="alerts")      # the SC6 host (SM-727), a runner (SM-798)
    assert rules.urgent_senders(env=None, settings=sc6) == (
        *rules.operator(sc6), "alerts@hq")                      # rules.py urgent_senders

    def mail(sender: str):
        raw = f"From: {sender}\r\nTo: eve@hq\r\nEars-Kind: spoken\r\n\r\nhey\r\n"
        return BytesParser(policy=policy).parsebytes(raw.encode())

    signed = lambda m: True                                     # noqa: E731 - SM-782's seam
    assert _voice.keeper_sent(mail("heard@hq"))                # the short form: the keeper
    assert _voice.recognise(mail("heard+airpods@hq"), None, signed).voice
    assert _voice.recognise(mail("heard+airpods@hq")).unverified    # unsigned: SM-782
    assert not _voice.keeper_sent(mail("heard@hq.tail.example"))   # FQDN: fails closed
    assert not _voice.recognise(mail("heard@hq.tail.example"), None, signed).voice
    assert _voice.recognise(mail("heard@hq.tail.example")).state == _voice.PLAIN
    assert _views.speaker_label("Judy <judy@hq>") == "judy"   # _views.py speaker_label
    assert _views.speaker_label("judy@hq.tail.example") == "judy@hq.tail.example"
    assert capsys.readouterr().err.count(_magicdns.UNKNOWN) == 1


def test_python_never_memoises_a_failure(tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    env = env_of(tmp_path, path_dir=bins)
    with pytest.raises(_magicdns.SuffixUnknown):
        _magicdns.suffix(env)
    fake_tailscale(bins / "tailscale", "late.example")     # tailscale started
    assert _magicdns.suffix(env) == "late.example"


def test_python_process_env_is_read_when_none_is_handed(tmp_path, monkeypatch):
    bins = tmp_path / "bin"
    fake_tailscale(bins / "tailscale", "process.example")
    for k, v in env_of(tmp_path, path_dir=bins).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MAGICDNS_SUFFIX", raising=False)
    monkeypatch.setenv("SESSIONMAIL_HOST", "hq")
    assert _voice.host_forms() == ("hq", "hq.process.example")


@pytest.mark.parametrize("given", [
    "tail.example", "Tail.Example", " tail.example ", ".tail.example.", "a-b.c-d.example",
    "x", "under_score.example", "spa ce.example", "tail..example", "ü.example", "...",
    "\u212aelvin.example",                       # KELVIN SIGN: folds to k under str.lower()
])
def test_python_and_the_helper_give_one_answer_for_the_env(helper, tmp_path, given):
    """Step 1 is answered in-process; it must be the helper's answer, or the
    helper's refusal, for any value (the "one definition" in practice)."""
    env = env_of(tmp_path, suffix=given, bundle="")
    shell = run([str(helper)], env)
    try:
        py: str | None = _magicdns.suffix(env)
    except _magicdns.SuffixUnknown:
        py = None
    assert (shell.stdout.strip() if shell.returncode == 0 else None) == py


# -- no literal tailnet suffix in the shipped source --------------------------

TAILNET = re.compile(r"[a-z0-9-]+\.ts\.net")


def test_no_literal_tailnet_suffix_in_src():
    hits = [f"{p.relative_to(SRC)}:{n}"
            for p in sorted(SRC.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
            if TAILNET.search(line)]
    assert hits == []
    # CONTROL: the same pattern does hit a suffix, so empty means empty.
    assert TAILNET.search("mail for eve@hq.example.ts.net")


# -- a mail-drop host with no tailnet: the short host is right, not a fault --

def test_drop_without_tailnet_decides_what_is_said_from_what_is_there(tmp_path):
    """No operator word, no tailscale CLI, and the drop: True. Any one of
    them there, or an MTA carrier: False (those hosts keep their warning)."""
    drop = {**env_of(tmp_path), "SESSIONMAIL_SENDMAIL": "drop"}
    assert _magicdns.drop_without_tailnet(drop) is True
    assert _magicdns.drop_without_tailnet(
        {**drop, "SESSIONMAIL_SENDMAIL": os.environ["SESSIONMAIL_SENDMAIL"]}) is False
    assert _magicdns.drop_without_tailnet({**drop, "MAGICDNS_SUFFIX": "env.example"}) is False
    assert _magicdns.drop_without_tailnet({**drop, "MAGICDNS_SUFFIX": "Not A Suffix"}) is False
    assert _magicdns.drop_without_tailnet({**drop, "MAGICDNS_SUFFIX": " . "}) is True   # blank
    bins = tmp_path / "bins"
    fake_tailscale(bins / "tailscale", fail=True)
    assert _magicdns.drop_without_tailnet(env_of(tmp_path, path_dir=bins)
                                          | {"SESSIONMAIL_SENDMAIL": "drop"}) is False
    bundle = fake_tailscale(tmp_path / "app" / "Tailscale", fail=True)
    assert _magicdns.drop_without_tailnet(env_of(tmp_path, bundle=bundle)
                                          | {"SESSIONMAIL_SENDMAIL": "drop"}) is False


def test_drop_without_tailnet_reads_the_settings_files_suffix(tmp_path):
    import json
    from conftest import write_mail_settings
    from sessionmail import _operator
    write_mail_settings(_operator.settings_home(),
                        json.dumps({"version": 1, _magicdns.FILE_KEY: "file.example"}))
    drop = {**env_of(tmp_path), "SESSIONMAIL_SENDMAIL": "drop"}
    assert _magicdns.drop_without_tailnet(drop) is False


def test_the_bundle_default_is_the_helpers_own(helper):
    """The presence check looks where the helper looks: one default path."""
    line = next(l for l in helper.read_text().splitlines() if l.startswith("BUNDLE_DEFAULT="))
    assert line == f'BUNDLE_DEFAULT="{_magicdns.BUNDLE_DEFAULT}"'


@pytest.mark.parametrize("where", ["none", "path", "bundle", "bundle-off"])
def test_tailscale_present_agrees_with_the_helpers_trace(tmp_path, helper, where):
    """On the same seams, `tailscale_present` is True exactly when the
    helper's --trace says it ASKED a CLI (not "no tailscale", not "not
    installed", not "not looked for")."""
    bins = tmp_path / "bins"
    bins.mkdir()
    bundle: Path | str = tmp_path / "no-app" / "Tailscale"
    if where == "path":
        fake_tailscale(bins / "tailscale", fail=True)
    elif where == "bundle":
        bundle = fake_tailscale(tmp_path / "app" / "Tailscale", fail=True)
    elif where == "bundle-off":
        bundle = ""
    env = env_of(tmp_path, path_dir=bins, bundle=bundle)
    err = run([str(helper), "--trace"], env).stderr
    asked = [l for l in err.splitlines() if l.strip().startswith("looked:")
             and not l.rstrip().endswith(("PATH: no tailscale", "not installed",
                                          "(MAGICDNS_TAILSCALE_BUNDLE is empty)"))]
    assert _magicdns.tailscale_present(env) is bool(asked), err
    assert _magicdns.tailscale_present(env) is (where in ("path", "bundle"))


def test_host_forms_on_a_drop_host_with_no_tailnet_say_nothing(tmp_path, capsys):
    """The pager status and PostToolBatch hook sites: the same short host,
    and nothing on stderr."""
    env = {**env_of(tmp_path), "SESSIONMAIL_HOST": "HQ", "SESSIONMAIL_SENDMAIL": "drop"}
    assert _voice.host_forms(env) == ("hq",)
    assert _voice.host_forms(env) == ("hq",)
    assert capsys.readouterr().err == ""


def test_host_forms_on_a_drop_host_whose_tailscale_fails_still_warn(tmp_path, capsys):
    bins = tmp_path / "bins"
    fake_tailscale(bins / "tailscale", fail=True)
    env = {**env_of(tmp_path, path_dir=bins), "SESSIONMAIL_HOST": "HQ",
           "SESSIONMAIL_SENDMAIL": "drop"}
    assert _voice.host_forms(env) == ("hq",)
    assert _voice.host_forms(env) == ("hq",)
    err = capsys.readouterr().err
    assert err.count(_magicdns.UNKNOWN) == 1
    assert "short host only ('hq')" in err


def test_no_tailnet_says_no_advice_and_names_nothing():
    """The INFO words: nothing to install or set, no product named."""
    words = _magicdns.NO_TAILNET.lower()
    for advice in ("install", "start", "set ", "tailscale", "magicdns_suffix"):
        assert advice not in words
