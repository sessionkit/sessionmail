#!/usr/bin/env python3
"""Cut a sessionmail release.

The ONE version lives in pyproject.toml (a static `version` in [project]).
This script is its single writer: it sets it with `uv version --frozen`,
records it on uv.lock's own root line (textual), and mirrors it into
.claude-plugin/plugin.json and marketplace.json textually, so their
formatting survives. It then stamps CHANGELOG.md's [Unreleased] section with
the version and date, commits the files by name and makes the annotated tag.
This is the family rule, and the sibling tools' release scripts work the
same way.

A RELEASE NEVER RE-LOCKS, as in sessionkit's script. A plain `uv version X`,
with or without --no-sync, is a project write: it re-locks against the
index, and this script commits uv.lock. Cut before the family's releases are
on PyPI, that fails (unsatisfiable); offline, it fails; with UV_FIND_LINKS
set, it writes a local-path lock and the release commits it. --no-sync only
skips the venv sync. So `uv version` always runs with --frozen, which writes
pyproject only and resolves nothing, on the dry path too, so a rehearsal
runs the same uv call as the release. (uv refuses --no-sync beside --frozen;
--frozen already implies no sync.) --frozen leaves uv.lock's own
`[[package]] name = "sessionmail"` version line on the old version, so the
script edits that one line as text, as it edits the manifests. The result is
the bytes uv's own re-lock writes when the deps are unchanged, and
`uv lock --check` passes. The lock is checked BEFORE `uv version` writes
anything, so a refusal leaves pyproject untouched. `make release` runs this
with python3, never a re-locking `uv run`.

Pushing is deliberately NOT done for you.

    python3 scripts/release.py --current      # print current version
    python3 scripts/release.py 0.1.0          # cut 0.1.0
    python3 scripts/release.py 0.1.0 --dry-run
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: requires-python excludes it
    raise SystemExit("release.py needs Python 3.11 or newer (tomllib)")

PYPROJECT = Path("pyproject.toml")
LOCK = Path("uv.lock")
PLUGIN = Path(".claude-plugin/plugin.json")
MARKET = Path(".claude-plugin/marketplace.json")
CHANGELOG = Path("CHANGELOG.md")
MIRRORS = (PLUGIN, MARKET)

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def current_version():
    project = tomllib.loads(PYPROJECT.read_text()).get("project", {})
    if "version" not in project:
        raise SystemExit("pyproject.toml has no static [project] version - nothing to read")
    return project["version"]


def set_json_version(path, version):
    """Rewrite the first version field TEXTUALLY so formatting survives."""
    text = path.read_text()
    new, n = re.subn(r'("version"\s*:\s*")[^"]+(")', r"\g<1>" + version + r"\g<2>", text, count=1)
    if n != 1:
        raise SystemExit("could not find a version field in %s" % path)
    json.loads(new)  # refuse to write invalid JSON
    return new


def stamp_changelog(version):
    if not CHANGELOG.exists():
        raise SystemExit("CHANGELOG.md not found - a release without a changelog is refused")
    text = CHANGELOG.read_text()
    if "## [Unreleased]" not in text:
        raise SystemExit("CHANGELOG.md has no '## [Unreleased]' section - nothing to stamp")
    return text.replace(
        "## [Unreleased]",
        "## [Unreleased]\n\n## [%s] - %s" % (version, date.today().isoformat()),
        1,
    )


def lock_root(text, name):
    """The (start, end) span of the version VALUE on uv.lock's root line.

    The root is the one [[package]] block named `name` whose source is the
    project itself (editable or virtual "."), and it must hold exactly one
    top-level `version = "..."` line. Anything else is refused: no guess, no
    write.
    """
    starts = [m.start() for m in re.finditer(r"^\[\[package\]\]$", text, re.MULTILINE)]
    roots = []
    for i, start in enumerate(starts):
        block = text[start : starts[i + 1] if i + 1 < len(starts) else len(text)]
        if re.search(r'^name = "%s"$' % re.escape(name), block, re.MULTILINE) and re.search(
            r'^source = \{ (?:editable|virtual) = "\." \}$', block, re.MULTILINE
        ):
            roots.append((start, block))
    if len(roots) != 1:
        raise SystemExit(
            '%s has %d root [[package]] blocks for %s (editable or virtual source "."), not one'
            " - refusing; nothing was written" % (LOCK, len(roots), name)
        )
    start, block = roots[0]
    versions = list(re.finditer(r'^version = "([^"]*)"$', block, re.MULTILINE))
    if len(versions) != 1:
        raise SystemExit(
            "%s's root %s block has %d version lines, not one - refusing; nothing was written"
            % (LOCK, name, len(versions))
        )
    return start + versions[0].start(1), start + versions[0].end(1)


def set_lock_root(text, span, version, dry):
    """Write `version` onto the root line found by lock_root(), and nothing else."""
    print("  ~ %s: root version %s -> %s (textual; nothing resolved)" % (LOCK, text[span[0] : span[1]], version))
    if not dry:
        LOCK.write_text(text[: span[0]] + version + text[span[1] :])


def set_version(version, dry):
    """pyproject.toml through `uv version --frozen`: it resolves nothing.

    The dry path runs the SAME call plus --dry-run (uv writes nothing then),
    so a rehearsal runs what the release runs. Never --no-sync: it does not
    stop the re-lock, and uv refuses it beside --frozen.
    """
    run("uv", "version", version, "--frozen", *(["--dry-run"] if dry else []), dry=False)


def run(*args, dry):
    print("  $ " + " ".join(args), flush=True)
    if not dry:
        subprocess.run(args, check=True)


def main():
    args = list(sys.argv[1:])
    if "--current" in args:
        print(current_version())
        return
    dry = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) != 1 or not SEMVER.match(args[0]):
        raise SystemExit("usage: release.py <MAJOR.MINOR.PATCH> [--dry-run]")
    version = args[0]
    tag = "v%s" % version

    if version == current_version():
        raise SystemExit("version %s is already current" % version)
    if subprocess.run(["git", "diff", "--quiet"]).returncode != 0:
        raise SystemExit("working tree is dirty - commit or stash first")
    if subprocess.run(["git", "tag", "-l", tag], capture_output=True, text=True).stdout.strip():
        raise SystemExit("tag %s already exists" % tag)
    if subprocess.run(["git", "var", "GIT_AUTHOR_IDENT"], capture_output=True).returncode != 0:
        raise SystemExit(
            "git has no author identity here - set user.name/user.email first.\n"
            "Refusing to edit files before a commit that cannot succeed."
        )

    if not LOCK.exists():
        raise SystemExit("%s not found - the release records its version there; nothing was written" % LOCK)

    print("sessionmail %s -> %s" % (current_version(), version), flush=True)
    # Compute every edit before writing any, so a refusal leaves the tree as it was.
    # The uv.lock root line is found here, BEFORE `uv version` writes pyproject.
    lock = LOCK.read_text()
    span = lock_root(lock, tomllib.loads(PYPROJECT.read_text())["project"]["name"])
    edits = [(path, set_json_version(path, version)) for path in MIRRORS]
    edits.append((CHANGELOG, stamp_changelog(version)))

    files = [PYPROJECT, LOCK] + [p for p, _ in edits]
    try:
        # pyproject.toml through uv, frozen: nothing is resolved or re-locked.
        set_version(version, dry)
        print("  updated %s" % PYPROJECT)
        # uv.lock's own root version line, as text: the one line --frozen leaves stale.
        set_lock_root(lock, span, version, dry)
        for path, content in edits:
            print("  updated %s" % path)
            if not dry:
                path.write_text(content)
        run("git", "add", "--", *[str(p) for p in files], dry=dry)
        run("git", "commit", "-m", "chore: release %s" % tag, dry=dry)
        run("git", "tag", "-a", tag, "-m", "sessionmail %s" % tag, dry=dry)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        cmd = " ".join(exc.cmd) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise SystemExit(
            "\nstep failed: %s\n"
            "Some version files MAY already be edited on disk. Either finish by hand,\n"
            "or undo with:  git checkout -- %s"
            % (cmd, " ".join(str(p) for p in files))
        )
    if dry:
        print("\nDry run: nothing written, nothing committed, nothing tagged.")
        return
    print("\nTagged %s. Not pushed - when you are ready:" % tag)
    print("  git push && git push --tags")
    print("Then: uv build && uv publish   (PyPI), and refresh the installed plugin.")


if __name__ == "__main__":
    main()
