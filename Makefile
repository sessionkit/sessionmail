# sessionmail — Makefile
#
#   make install  put message, pager and memory on the PATH, EDITABLE
#                 (uv tool install -e .) so an edit is live at once
#   make test     the pytest suite (network-free; no maildir is touched),
#                 less the `latency` tests (tests/conftest.py leaves them out)
#   make test-latency  the `latency` tests alone: bounds on how SOON the
#                 pager pages. Load reddens them with no product change, so
#                 run them on a quiet host, by themselves. Not in `make`.
#   make test-export  `make check` and the suite inside `git archive HEAD`,
#                 the tree the release ships (no .git, no CLAUDE.md, no
#                 openspec/). Not in `make`: it runs the whole suite again.
#   make progress how much of the CLI is built, counted by running
#                 every verb: a stub exits 69 and names its requirement
#   make check    the two structural gates: `openspec validate --all --strict`
#                 over openspec/, and `skill plugin validate .` over the
#                 plugin + skill. Green means well-formed, not correct.
#                 The release archive leaves openspec/ out: there, and in a
#                 clone of it, that leg SKIPS, saying so. In a checkout that
#                 tracks openspec/ it stays strict.
#   make          check, then test
#   make build    wheel + sdist into dist/ (uv build)
#   make release  cut a release: bump the ONE version (plugin.json, mirrored
#                 into marketplace.json), stamp CHANGELOG.md, commit, tag.
#                 Never pushes. scripts/release.py, modelled on session's.

SHELL := /usr/bin/env bash

.PHONY: all install check test test-latency test-export build release progress help

all: check test

install:
	@uv tool install -e .

# openspec/ is export-ignored (.gitattributes). Absent and not tracked here
# (no git, no repo, or a clone of the export, which never held it), its
# absence is the release export's, and the leg skips. Absent but TRACKED is a
# broken checkout, and the leg fails. Never keyed on .git alone.
check:
	@echo "== openspec validate --all --strict"
	@if [ -d openspec ]; then openspec validate --all --strict; \
	elif git ls-files --error-unmatch -- openspec >/dev/null 2>&1; then \
	  echo "openspec/ is missing from this git checkout" >&2; exit 1; \
	else echo "openspec/ not in this tree (release archive): skipped"; fi
	@echo "== skill plugin validate ."
	@skill plugin validate .

test:
	@uv run --quiet pytest -q

# The wall-clock lane: each `latency` test keeps a bound on how soon a page
# lands, unchanged, beside a default-lane twin that asserts the event. Run
# it alone on a quiet host; a red here under load is the host, not the code.
# `pager listen` runs every `pager` on PATH with --version (the version
# alarm), inside those bounds. A fresh venv's own `pager` is slow on its very
# first exec (0.35 s measured, 0.06 s after), so it is exec'd once first.
test-latency:
	@uv run --quiet pager --version >/dev/null
	@uv run --quiet pytest -q -m latency

# The suite as a stranger runs it from the release tarball. The
# export is unpacked into a fresh temp dir; GIT_CEILING_DIRECTORIES stops a
# test that shells to git from finding an enclosing repo. PYTEST_ARGS narrows
# it, e.g. `make test-export PYTEST_ARGS=tests/test_readme_docs.py`.
test-export:
	@set -euo pipefail; \
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	git archive HEAD | tar -x -C "$$tmp"; \
	sha=$$(git rev-parse --short HEAD); \
	cd "$$tmp"; export GIT_CEILING_DIRECTORIES="$$(dirname "$$tmp")"; \
	echo "== make check in git archive $$sha ($$tmp)"; \
	$(MAKE) --no-print-directory check; \
	echo "== pytest in git archive $$sha ($$tmp)"; \
	uv run --quiet pytest -q $(PYTEST_ARGS)

progress:  ## How much of the CLI is built, counted by running every verb
	@uv run --quiet python scripts/progress.py

build:
	@uv build

release:  ## Cut a release (bump the version, stamp the changelog, commit, tag)
	@echo "Current version: $$(python3 scripts/release.py --current)"
	@read -p "New version (MAJOR.MINOR.PATCH): " version; \
	if [ -z "$$version" ]; then echo "Error: a version is required"; exit 1; fi; \
	python3 scripts/release.py $$version

help:
	@awk 'NR > 1 && !/^#/ { exit } NR > 1' Makefile | sed 's/^#//'

# The pre-publish scrub check. The scanner and its word list are PRIVATE and never
# live in this repo: SESSIONKIT_SCAN names the release-scan tool (it finds its list,
# or takes SESSIONKIT_SCRUB_RULES). Exits non-zero on any P0 term in HEAD's tree.
.PHONY: scan scrub
scan:  ## scrub check: HEAD's tree against the private word list; fails on any P0 term
	@[ -n "$$SESSIONKIT_SCAN" ] || { echo "scan: set SESSIONKIT_SCAN to the release-scan tool" >&2; exit 2; }
	@"$$SESSIONKIT_SCAN" --here --fail-on P0

scrub: scan  ## the same check, by its other name
