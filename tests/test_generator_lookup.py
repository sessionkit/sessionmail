"""One lookup for the map generator, two orders (SM-660 design-001 part B).

What is pinned here:

- the READ-ONLY order (the mail drop): env, PATH, `<mail root>/.postfix`,
  then the packaged copy LAST;
- the WRITING order (`_seat._run_generator`, `_archive`): the same three
  steps and NEVER the packaged copy. Making the packaged copy write a live
  map is reserved to the project owner (SM-74); sessionmail+super answered Q-B1 NO,
  2030-12-08;
- a SET `$SESSIONMAIL_VMAILBOX_GEN` is the only candidate in both orders, so
  a pin whose file is missing finds nothing (the conftest fence, SM-374
  do-006);
- a candidate without its execute bit runs through bash.

Every env here is an explicit dict, so the runner's own PATH and the live
`~/.mail` are never asked. Nothing outside tmp_path is written.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sessionmail import _archive, _drop, _generator, _seat

FAKE = """#!/bin/bash
# A fake generator: it says which copy ran by touching a file in --root.
while [ $# -gt 0 ]; do
  if [ "$1" = "--root" ]; then touch "$2/ran-{name}"; shift; fi
  shift
done
exit 0
"""


def fake(path: Path, name: str, executable: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE.format(name=name))
    path.chmod(0o755 if executable else 0o644)
    return path


@pytest.fixture
def layout(tmp_path):
    """A mail root plus one generator at each of the three non-packaged
    steps. Each test removes the ones it does not want."""
    root = tmp_path / "mail"
    (root / "agents").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    return {
        "root": root,
        "bin": bin_dir,
        "env_gen": fake(tmp_path / "pinned" / "vmailbox-gen", "env"),
        "path_gen": fake(bin_dir / "vmailbox-gen", "path"),
        "root_gen": fake(root / ".postfix" / "vmailbox-gen", "root"),
    }


#: The carrier these hosts send by: an MTA, because the map writer runs only
#: under one (the drop, the default, reads the maps per send). Never run.
MTA = "/usr/sbin/sendmail"


def env_for(layout, pin: bool = True, path: bool = True) -> dict[str, str]:
    env = {"PATH": str(layout["bin"]) if path else str(layout["root"] / "no-bin"),
           "SESSIONMAIL_SENDMAIL": MTA}
    if pin:
        env[_generator.VAR] = str(layout["env_gen"])
    return env


# ── the order, each step found when the earlier ones are absent ────────────

@pytest.mark.parametrize("packaged", [True, False], ids=["read-only", "writing"])
def test_each_step_is_found_when_the_earlier_ones_are_absent(layout, packaged):
    root = layout["root"]
    found = _generator.find(env_for(layout), root, packaged=packaged)
    assert found == _generator.Found(str(layout["env_gen"]), "env")

    found = _generator.find(env_for(layout, pin=False), root, packaged=packaged)
    assert found == _generator.Found(str(layout["path_gen"]), "PATH")

    found = _generator.find(env_for(layout, pin=False, path=False), root, packaged=packaged)
    assert found == _generator.Found(str(layout["root_gen"]), "mail root")


def test_the_read_only_order_ends_at_the_packaged_copy(layout):
    layout["root_gen"].unlink()
    found = _generator.find(env_for(layout, pin=False, path=False), layout["root"], packaged=True)
    assert found == _generator.Found(str(_generator.PACKAGED), "packaged")
    assert _generator.PACKAGED.is_file()           # the wheel really carries it


def test_the_writing_order_never_reaches_the_packaged_copy(layout):
    """SM-74 / Q-B1: the packaged copy exists, and the writing order still
    answers None on a wheel-only layout."""
    layout["root_gen"].unlink()
    assert _generator.PACKAGED.is_file()
    env = env_for(layout, pin=False, path=False)
    assert _generator.find(env, layout["root"], packaged=False) is None
    sources = [s for _, s in _generator.candidates(env, layout["root"], packaged=False)]
    assert sources == ["PATH", "mail root"]


def test_the_two_orders_differ_only_by_the_last_step(layout):
    env = env_for(layout, pin=False)
    read = _generator.candidates(env, layout["root"], packaged=True)
    write = _generator.candidates(env, layout["root"], packaged=False)
    assert read[:-1] == write
    assert read[-1] == (str(_generator.PACKAGED), "packaged")


def test_packaged_has_no_default(layout):
    with pytest.raises(TypeError):
        _generator.find(env_for(layout), layout["root"])   # type: ignore[call-arg]


# ── the pin: set means the only candidate, in both orders ──────────────────

@pytest.mark.parametrize("packaged", [True, False], ids=["read-only", "writing"])
def test_a_set_pin_whose_file_is_missing_finds_nothing(layout, tmp_path, packaged):
    env = env_for(layout)
    env[_generator.VAR] = str(tmp_path / "no-such-gen")
    # PATH, .postfix and (read-only) the packaged copy are all there.
    assert _generator.find(env, layout["root"], packaged=packaged) is None
    assert _generator.candidates(env, layout["root"], packaged=packaged) == [
        (str(tmp_path / "no-such-gen"), "env")]


def test_an_empty_pin_is_unset(layout):
    env = env_for(layout)
    env[_generator.VAR] = ""
    found = _generator.find(env, layout["root"], packaged=False)
    assert found is not None and found.source == "PATH"


def test_the_compatibility_names_are_the_module_s(layout):
    assert _seat.GEN_VAR == _drop.GEN_VAR == _generator.VAR
    assert _drop.PACKAGED_GEN == _generator.PACKAGED


def test_expected_names_the_pin_else_the_mail_root_copy(layout, tmp_path):
    assert _generator.expected({_generator.VAR: "/x/gen"}, layout["root"]) == "/x/gen"
    assert _generator.expected({}, layout["root"]) == str(layout["root"] / ".postfix" / "vmailbox-gen")


# ── argv: a file without its execute bit runs through bash ─────────────────

def test_an_executable_runs_by_its_shebang(layout):
    gen = str(layout["path_gen"])
    assert _generator.argv(gen, ["--print"], {"PATH": ""}) == [gen, "--print"]


def test_a_file_without_its_execute_bit_runs_through_bash(tmp_path):
    gen = fake(tmp_path / "vmailbox-gen", "noexec", executable=False)
    cmd = _generator.argv(str(gen), ["--print"], {"PATH": os.environ.get("PATH", "")})
    assert Path(cmd[0]).name == "bash" and cmd[1:] == [str(gen), "--print"]


# ── the callers ────────────────────────────────────────────────────────────

def test_the_drop_falls_back_to_the_packaged_copy(layout):
    layout["root_gen"].unlink()
    env = env_for(layout, pin=False, path=False)
    assert _drop.generator(env, layout["root"]) == str(_generator.PACKAGED)


def test_the_drop_refuses_a_pin_whose_file_is_missing(layout, tmp_path):
    """Design-001 B.3.1: today's fall-through is gone; the drop refuses, 69."""
    env = env_for(layout)
    env[_generator.VAR] = str(tmp_path / "no-such-gen")
    with pytest.raises(_drop.NotDelivered) as exc:
        _drop.generator(env, layout["root"])
    assert exc.value.code == _drop.EX_UNAVAILABLE
    assert "no-such-gen" in str(exc.value) and _generator.VAR in str(exc.value)


def test_the_drop_with_nothing_anywhere_keeps_its_refusal(layout, monkeypatch, tmp_path):
    layout["root_gen"].unlink()
    monkeypatch.setattr(_generator, "PACKAGED", tmp_path / "no-packaged-copy")
    env = env_for(layout, pin=False, path=False)
    with pytest.raises(_drop.NotDelivered) as exc:
        _drop.generator(env, layout["root"])
    assert exc.value.code == _drop.EX_UNAVAILABLE
    assert "not in this package" in str(exc.value)


def test_the_archive_never_takes_the_packaged_copy(layout):
    layout["root_gen"].unlink()
    env = env_for(layout, pin=False, path=False)
    assert _archive.generator(env, layout["root"]) is None
    assert _archive.generator(env_for(layout, pin=False), layout["root"]) == str(layout["path_gen"])


def test_a_wheel_only_host_never_runs_the_packaged_copy_to_write(layout):
    """The SC2 writing half: no PATH copy, no .postfix copy, the packaged copy
    present. The writing caller says "not found" and runs nothing."""
    layout["root_gen"].unlink()
    env = env_for(layout, pin=False, path=False)
    ok, line = _seat._run_generator(layout["root"] / "agents", env, ("--valias-only",))
    assert not ok
    assert line.startswith(f"generator not found ({layout['root'] / '.postfix' / 'vmailbox-gen'})")
    assert str(_generator.PACKAGED) not in line
    assert not list(layout["root"].glob("ran-*"))


@pytest.mark.parametrize("pin,path,ran", [(True, True, "env"), (False, True, "path"),
                                          (False, False, "root")])
def test_the_writing_caller_runs_the_copy_its_order_finds(layout, pin, path, ran):
    ok, line = _seat._run_generator(layout["root"] / "agents", env_for(layout, pin, path),
                                    ("--valias-only",))
    assert ok, line
    assert [p.name for p in layout["root"].glob("ran-*")] == [f"ran-{ran}"]


def test_the_writing_caller_runs_a_copy_without_its_execute_bit(layout):
    layout["root_gen"].chmod(0o644)
    env = env_for(layout, pin=False, path=False)
    env["PATH"] = os.environ.get("PATH", "")     # bash, but no vmailbox-gen
    if _generator.find(env, layout["root"], packaged=False).source != "mail root":
        pytest.skip("a vmailbox-gen is on this runner's PATH")
    ok, line = _seat._run_generator(layout["root"] / "agents", env, ("--valias-only",))
    assert ok, line
    assert (layout["root"] / "ran-root").exists()


def test_the_writing_caller_names_a_missing_pin(layout, tmp_path):
    env = env_for(layout)
    env[_generator.VAR] = str(tmp_path / "no-such-gen")
    ok, line = _seat._run_generator(layout["root"] / "agents", env, ("--valias-only",))
    assert not ok and f"generator not found ({tmp_path / 'no-such-gen'})" in line
    assert not list(layout["root"].glob("ran-*"))
