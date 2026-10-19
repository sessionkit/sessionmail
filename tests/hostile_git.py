"""SM-866: a stranger's git config that refuses or re-signs any plain commit.

The GC's recipe (a global core.hooksPath to a pre-commit that exits 1, plus
commit.gpgsign = true), widened with a foreign signer program (a fake
gpg.ssh.program that exits 1, standing in for an agent signer such as
1Password's op-ssh-sign) and a foreign signing key. Two shapes carry it:

- ``global``: all of it in a GIT_CONFIG_GLOBAL file, beside the user's
  identity, as a stranger's ~/.gitconfig would hold it.
- ``count``: all of it in GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / VALUE_n, as a
  Claude Code seat exports it (command scope: it beats a repo's own config),
  with the identity alone in GIT_CONFIG_GLOBAL.

``hostile_env(tmp_path, shape)`` returns only the variables to lay over a
base environment. The tests that use it prove sessionmail's OWN commits land
under it, and prove the rig is hostile first: a plain commit is refused.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

SHAPES = ("global", "count")
USER_NAME = "Global User"
USER_EMAIL = "global@example.invalid"
HOOK_SAYS = "a global hook says no"


def _hostile_keys(tmp_path: Path) -> list[tuple[str, str]]:
    hooks = hooks_dir(tmp_path)
    hook(hooks, refuse=HOOK_SAYS)
    signer = tmp_path / "hostile-signer"
    signer.write_text("#!/bin/sh\necho 'a foreign signer says no' >&2\nexit 1\n")
    os.chmod(signer, 0o755)
    return [("core.hooksPath", str(hooks)), ("commit.gpgsign", "true"),
            ("gpg.format", "ssh"), ("gpg.ssh.program", str(signer)),
            ("user.signingkey", str(tmp_path / "no-such-signing-key"))]


def _config_file(path: Path, pairs: list[tuple[str, str]]) -> Path:
    lines = []
    for key, value in pairs:
        section, _, name = key.rpartition(".")
        head, _, sub = section.partition(".")
        lines.append(f'[{head} "{sub}"]' if sub else f"[{head}]")
        lines.append(f"\t{name} = {value}")
    path.write_text("\n".join(lines) + "\n")
    return path


def hostile_env(tmp_path: Path, shape: str) -> dict[str, str]:
    """The env to lay over a base env (which must carry GIT_CONFIG_NOSYSTEM)."""
    identity = [("user.name", USER_NAME), ("user.email", USER_EMAIL)]
    hostile = _hostile_keys(tmp_path)
    if shape == "global":
        return {"GIT_CONFIG_GLOBAL": str(_config_file(tmp_path / "hostile.gitconfig",
                                                      identity + hostile))}
    if shape == "count":
        env = {"GIT_CONFIG_GLOBAL": str(_config_file(tmp_path / "identity.gitconfig", identity)),
               "GIT_CONFIG_COUNT": str(len(hostile))}
        for n, (key, value) in enumerate(hostile):
            env[f"GIT_CONFIG_KEY_{n}"] = key
            env[f"GIT_CONFIG_VALUE_{n}"] = value
        return env
    raise ValueError(f"no such shape: {shape}")


def plain_commit_is_refused(tmp_path: Path, env: dict[str, str]) -> str:
    """The control: a plain `git commit` in a fresh repo under ENV fails.
    Returns git's words, so a caller can say which part refused."""
    repo = tmp_path / "control"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    done = subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
                          capture_output=True, text=True, env=env)
    assert done.returncode != 0, "the hostile config let a plain commit through: the rig is not hostile"
    return done.stderr


def signed(repo: Path, rev: str = "HEAD", env: dict[str, str] | None = None) -> bool:
    """Whether REV's commit object carries a signature header."""
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "commit", rev], capture_output=True,
                         text=True, check=True, env=env).stdout
    return any(line.startswith("gpgsig") for line in raw.split("\n\n", 1)[0].splitlines())


def people(repo: Path, rev: str = "HEAD", env: dict[str, str] | None = None) -> tuple[str, str]:
    """REV's author and committer, each `Name <email>`."""
    out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>%n%cn <%ce>", rev],
                         capture_output=True, text=True, check=True, env=env).stdout.splitlines()
    return out[0], out[1]


def hook(hooks: Path, *, refuse: str | None = None, mark: Path | None = None) -> Path:
    """A pre-commit in HOOKS that refuses, saying REFUSE on stderr, or lets the
    commit through and touches MARK, so a test can see WHICH hooks dir git ran."""
    hooks.mkdir(parents=True, exist_ok=True)
    body = (f"echo {shlex.quote(refuse)} >&2\nexit 1\n" if refuse
            else f"touch {shlex.quote(str(mark))}\nexit 0\n")
    path = hooks / "pre-commit"
    path.write_text("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


def hooks_dir(tmp_path: Path) -> Path:
    """The hostile hooksPath ``hostile_env`` names, in either shape."""
    return tmp_path / "hostile-hooks"
