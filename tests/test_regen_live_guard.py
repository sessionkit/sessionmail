"""SM-310: the generator's live-tree guard.

`vmailbox-gen` takes its SOURCE from `--root` and its TARGET from `--out`, else
the PINNED live map dir. Before this guard nothing tied the two together, so a
regenerate from a sandbox root (a scratch `SESSION_MAIL_ROOT`, an operator's
test tree) published the sandbox's keys over every real route. The guard, in
the generator's write path, measures postfix (`virtual_mailbox_base`, and the
dirs of `virtual_mailbox_maps` / `virtual_alias_maps`) and refuses such a run:
exit 5, ONE stderr line naming the root and the map dir, nothing written.

**Never the live map dir, never the host's postfix.** Every run below is the
REAL generator, copied under tmp with ONE line changed: `DEFAULT_MAP` points
into tmp (`patched_gen`). So a run with no `--out` targets a scratch dir even
if the guard were wrong. postconf is a fake named by `SESSIONMAIL_POSTCONF`
(the only postconf the generator asks when that is set), postmap is a shim
that logs, and PATH holds neither `/usr/sbin` nor `/opt/homebrew/sbin`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from sessionmail import _seat

HOST = "testhost"

FAKE_POSTCONF = """#!/bin/sh
echo "$*" >> "$FAKE_PC_LOG"
case "$2" in
  virtual_mailbox_base) [ -n "${FAKE_PC_BASE:-}" ] && echo "$FAKE_PC_BASE" ;;
  virtual_mailbox_maps) [ -n "${FAKE_PC_MAPS:-}" ] && echo "hash:$FAKE_PC_MAPS/vmailbox" ;;
  virtual_alias_maps)   [ -n "${FAKE_PC_MAPS:-}" ] && echo "hash:$FAKE_PC_MAPS/valias" ;;
esac
exit 0
"""

#: Logs, and makes the `.db` the generator chmods and moves (`hash:FILE`).
FAKE_POSTMAP = """#!/bin/sh
echo "$*" >> "$FAKE_POSTMAP_LOG"
: > "${1#*:}.db"
exit 0
"""

DEFAULT_LINE = re.compile(r'^DEFAULT_MAP="[^"]*"$', re.MULTILINE)


def _maildir(d: Path) -> Path:
    for leaf in ("new", "cur", "tmp"):
        (d / leaf).mkdir(parents=True, exist_ok=True)
    return d


def _exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


class Rig:
    """Two mail trees (the sandbox and the one postfix "serves"), a scratch
    stand-in for the pinned map dir, a dir the fake postconf names as the
    maps postfix reads, the patched generator and the fakes."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.sandbox = tmp / "sandbox" / "mail"
        _maildir(self.sandbox / "agents" / "sandbox-box")
        self.live = tmp / "live" / "mail"
        _maildir(self.live / "agents" / "live-box")
        self.default_dir = tmp / "pinned-default"          # NOT created: a refusal must not make it
        self.postfix_maps = tmp / "postfix-maps"
        self.postfix_maps.mkdir()
        (self.postfix_maps / "vmailbox").write_text("SENTINEL live row\n")
        (tmp / "sidecars").mkdir()                          # no live sessions
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.postconf = _exe(self.bin / "fake-postconf", FAKE_POSTCONF)
        _exe(self.bin / "postmap", FAKE_POSTMAP)
        self.pc_log = tmp / "postconf.log"
        self.postmap_log = tmp / "postmap.log"
        self.gen = self._patched_gen()

    def _patched_gen(self) -> Path:
        gdir = self.tmp / "gen"
        gdir.mkdir()
        with as_file(files("sessionmail").joinpath("postfix")) as src:
            text = (Path(src) / "vmailbox-gen").read_text()
            shutil.copy2(Path(src) / "magicdns-suffix", gdir / "magicdns-suffix")
        assert len(DEFAULT_LINE.findall(text)) == 1, "expected exactly one DEFAULT_MAP= line"
        text = DEFAULT_LINE.sub(f'DEFAULT_MAP="{self.default_dir}/vmailbox"', text)
        return _exe(gdir / "vmailbox-gen", text)

    def env(self, *, base: Path | None, maps: Path | None,
            postconf: str | None = "fake") -> dict[str, str]:
        env = dict(os.environ)
        jq = shutil.which("jq")
        path = [str(self.bin)] + ([os.path.dirname(jq)] if jq else []) + ["/usr/bin", "/bin"]
        env.update(PATH=":".join(path), HOME=str(self.tmp / "home"),
                   SESSION_SIDECAR_DIR=str(self.tmp / "sidecars"),
                   SESSIONMAIL_HOME=str(self.tmp / "sessionmail-home"),
                   FAKE_PC_LOG=str(self.pc_log), FAKE_POSTMAP_LOG=str(self.postmap_log),
                   FAKE_PC_BASE=str(base) if base else "",
                   FAKE_PC_MAPS=str(maps) if maps else "")
        env.pop("MAIL_ROOT", None)
        if postconf == "fake":
            env["SESSIONMAIL_POSTCONF"] = str(self.postconf)
        elif postconf is None:
            env.pop("SESSIONMAIL_POSTCONF", None)
        else:
            env["SESSIONMAIL_POSTCONF"] = postconf
        return env

    def run(self, root: Path, *args: str, **env_kw) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.gen), "--host", HOST, "--root", str(root), *args],
                              env=self.env(**env_kw), capture_output=True, text=True, check=False,
                              timeout=60)

    def assert_refused(self, done: subprocess.CompletedProcess, root: Path,
                       map_dir: Path) -> str:
        assert done.returncode == 5, (done.returncode, done.stderr)
        lines = done.stderr.strip().splitlines()
        assert len(lines) == 1, lines
        line = lines[0]
        assert line.startswith("vmailbox-gen: REFUSED: "), line
        assert f"root {root} " in line and f"map dir {map_dir} NOT written" in line, line
        assert not (root / ".state").exists(), "a refusal wrote the failure log"
        assert not self.postmap_log.exists(), self.postmap_log.read_text()
        return line


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# --- refused -------------------------------------------------------------


def test_sandbox_root_no_out_is_refused(rig: Rig) -> None:
    done = rig.run(rig.sandbox, "--no-valias", base=rig.live, maps=rig.postfix_maps)
    line = rig.assert_refused(done, rig.sandbox, rig.default_dir)
    assert f"postfix serves {rig.live}" in line
    assert not rig.default_dir.exists(), "a refusal made the map dir"


def test_sandbox_root_valias_only_is_refused(rig: Rig) -> None:
    """What `regen_valias` runs (`message subscription add|remove`)."""
    done = rig.run(rig.sandbox, "--valias-only", base=rig.live, maps=rig.postfix_maps)
    rig.assert_refused(done, rig.sandbox, rig.default_dir)
    assert not rig.default_dir.exists()


def test_sandbox_root_out_into_a_dir_postfix_reads_is_refused(rig: Rig) -> None:
    before = sorted(p.name for p in rig.postfix_maps.iterdir())
    done = rig.run(rig.sandbox, "--out", str(rig.postfix_maps / "vmailbox"), "--no-valias",
                   base=rig.live, maps=rig.postfix_maps)
    rig.assert_refused(done, rig.sandbox, rig.postfix_maps)
    assert sorted(p.name for p in rig.postfix_maps.iterdir()) == before   # no lock, no .db
    assert (rig.postfix_maps / "vmailbox").read_text() == "SENTINEL live row\n"


def test_no_postconf_refuses_even_the_root_it_would_allow(rig: Rig) -> None:
    """Fail closed: nothing to ask, so not even the "live" tree runs."""
    done = rig.run(rig.live, "--no-valias", base=rig.live, maps=rig.postfix_maps,
                   postconf=str(rig.tmp / "no-postconf-here"))
    line = rig.assert_refused(done, rig.live, rig.default_dir)
    assert "no postconf to ask" in line
    assert not rig.pc_log.exists(), "the fake was asked although it was not named"


def test_postconf_silent_on_the_base_is_refused(rig: Rig) -> None:
    done = rig.run(rig.live, "--no-valias", base=None, maps=rig.postfix_maps)
    line = rig.assert_refused(done, rig.live, rig.default_dir)
    assert "postconf could not say virtual_mailbox_base" in line


def test_a_base_that_does_not_resolve_is_refused(rig: Rig) -> None:
    gone = rig.tmp / "no-such-tree"
    done = rig.run(rig.live, "--no-valias", base=gone, maps=rig.postfix_maps)
    line = rig.assert_refused(done, rig.live, rig.default_dir)
    assert "does not resolve" in line


def test_a_root_that_does_not_resolve_is_refused(rig: Rig) -> None:
    gone = rig.tmp / "no-such-root"
    done = rig.run(gone, "--no-valias", base=rig.live, maps=rig.postfix_maps)
    rig.assert_refused(done, gone, rig.default_dir)


def test_a_named_postconf_never_falls_through(rig: Rig) -> None:
    """SESSIONMAIL_POSTCONF set to a missing file means NO postconf, even with
    one on PATH that would allow the run: never PATH, never a system path."""
    _exe(rig.bin / "postconf", FAKE_POSTCONF)
    done = rig.run(rig.live, "--no-valias", base=rig.live, maps=rig.postfix_maps,
                   postconf=str(rig.tmp / "no-postconf-here"))
    rig.assert_refused(done, rig.live, rig.default_dir)
    assert not rig.pc_log.exists()


@pytest.mark.parametrize("postconf", ["fake", "missing"])
def test_out_naming_the_pinned_dir_is_the_pinned_target(rig: Rig, postconf: str) -> None:
    """An --out whose dir IS the pinned default's is held to the no --out rule,
    however it is spelled: refused for a sandbox root, and refused even with no
    postconf to ask (fail closed), where an --out elsewhere would run."""
    rig.default_dir.mkdir()
    link = rig.tmp / "pinned-link"
    link.symlink_to(rig.default_dir)
    named = "fake" if postconf == "fake" else str(rig.tmp / "no-postconf-here")
    done = rig.run(rig.sandbox, "--out", str(link / "vmailbox"), "--no-valias",
                   base=rig.live, maps=rig.postfix_maps, postconf=named)
    rig.assert_refused(done, rig.sandbox, link)
    assert list(rig.default_dir.iterdir()) == [], "a refusal wrote into the pinned dir"


# --- allowed -------------------------------------------------------------


def test_the_live_root_runs_with_no_out(rig: Rig) -> None:
    """The real live regenerate: the root IS the tree postfix serves."""
    rig.default_dir.mkdir()
    done = rig.run(rig.live, "--no-valias", base=rig.live, maps=rig.postfix_maps)
    assert done.returncode == 0, done.stderr
    assert "live-box@testhost\tagents/live-box/" in (rig.default_dir / "vmailbox").read_text()
    assert rig.postmap_log.exists()


def test_the_live_root_runs_through_a_symlinked_path(rig: Rig) -> None:
    """Compared resolved: postfix may name the tree by another spelling."""
    rig.default_dir.mkdir()
    link = rig.tmp / "live-link"
    link.symlink_to(rig.live)
    done = rig.run(link, "--no-valias", base=rig.live, maps=rig.postfix_maps)
    assert done.returncode == 0, done.stderr


def test_the_live_root_runs_with_out_into_the_dir_postfix_reads(rig: Rig) -> None:
    done = rig.run(rig.live, "--out", str(rig.postfix_maps / "vmailbox"), "--no-valias",
                   base=rig.live, maps=rig.postfix_maps)
    assert done.returncode == 0, done.stderr
    assert "live-box@testhost" in (rig.postfix_maps / "vmailbox").read_text()


def test_the_live_root_runs_with_out_naming_the_pinned_dir(rig: Rig) -> None:
    rig.default_dir.mkdir()
    done = rig.run(rig.live, "--out", str(rig.default_dir / "vmailbox"), "--no-valias",
                   base=rig.live, maps=rig.postfix_maps)
    assert done.returncode == 0, done.stderr
    assert "live-box@testhost" in (rig.default_dir / "vmailbox").read_text()


@pytest.mark.parametrize("postconf", ["fake", "missing"])
def test_out_into_an_unrelated_scratch_dir_always_runs(rig: Rig, postconf: str) -> None:
    out = rig.tmp / "scratch-maps"
    out.mkdir()
    named = "fake" if postconf == "fake" else str(rig.tmp / "no-postconf-here")
    done = rig.run(rig.sandbox, "--out", str(out / "vmailbox"), "--no-valias",
                   base=rig.live, maps=rig.postfix_maps, postconf=named)
    assert done.returncode == 0, done.stderr
    assert "sandbox-box@testhost" in (out / "vmailbox").read_text()


def test_unset_postconf_var_asks_postconf_on_path_first(rig: Rig) -> None:
    """With SESSIONMAIL_POSTCONF unset, PATH's postconf answers (the fake here),
    ahead of any system location."""
    _exe(rig.bin / "postconf", FAKE_POSTCONF)
    rig.default_dir.mkdir()
    done = rig.run(rig.live, "--no-valias", base=rig.live, maps=rig.postfix_maps,
                   postconf=None)
    assert done.returncode == 0, done.stderr
    assert "-xh virtual_mailbox_base" in rig.pc_log.read_text()


def test_print_modes_are_not_guarded(rig: Rig) -> None:
    """--print writes nothing, so the guard never stands in its way."""
    done = rig.run(rig.sandbox, "--print", base=rig.live, maps=rig.postfix_maps)
    assert done.returncode == 0, done.stderr
    assert "sandbox-box@testhost" in done.stdout


# --- through the Python door ---------------------------------------------


@pytest.fixture
def py_rig(rig: Rig, monkeypatch) -> Rig:
    for key, value in rig.env(base=rig.live, maps=rig.postfix_maps).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(_seat.GEN_VAR, str(rig.gen))
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(rig.sandbox / "agents"))
    return rig


def test_regen_valias_surfaces_the_refusal(py_rig: Rig) -> None:
    ok, line = _seat.regen_valias(py_rig.sandbox / "agents")
    assert ok is False
    assert "\n" not in line
    assert line.startswith("generator exit 5 in "), line
    assert f"REFUSED: root {py_rig.sandbox} " in line
    assert f"map dir {py_rig.default_dir} NOT written" in line
    assert not py_rig.default_dir.exists()


def test_regen_maps_surfaces_the_refusal(py_rig: Rig) -> None:
    line = _seat.regen_maps(py_rig.sandbox / "agents")
    assert line.startswith("generator exit 5 in "), line
    assert "REFUSED" in line and str(py_rig.sandbox) in line and "\n" not in line
    assert not py_rig.default_dir.exists()
    assert not py_rig.postmap_log.exists()


def test_regen_maps_runs_for_the_live_root(py_rig: Rig, monkeypatch) -> None:
    if not shutil.which("jq"):
        pytest.skip("the generator's alias half needs jq")
    monkeypatch.setenv("FAKE_PC_BASE", str(py_rig.sandbox))   # the sandbox IS what postfix serves
    py_rig.default_dir.mkdir()
    line = _seat.regen_maps(py_rig.sandbox / "agents")
    assert line.startswith("generator ran in "), line
    # No --host on this door: the rows key on `hostname -s`, so only the box is asserted.
    assert "\tagents/sandbox-box/" in (py_rig.default_dir / "vmailbox").read_text()
