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
#                 over the specs, and `skill plugin validate .` over the
#                 plugin + skill. Green means well-formed, not correct.
#                 openspec/ holds only config.yaml, a pointer to the
#                 project's OpenSpec store; the CLI validates the store
#                 through it. The release archive leaves openspec/ out:
#                 there, and in a clone of it, that leg SKIPS, saying so. So
#                 it does where the store is not registered on this machine.
#                 In a checkout that tracks openspec/ it stays strict.
#   make          check, then test
#   make build    wheel + sdist into dist/ (uv build)
#   make pypi-dist  the wheel + sdist PyPI gets: built from tag v<version>,
#                 README links pinned to it, by sessionkit's README rewriter
#                 (KIT_DIR=<sessionkit checkout>, default ../sessionkit).
#                 The tag must be the same commit on PUBLIC_REMOTE.
#   make release  cut a release: bump the ONE version (pyproject.toml, mirrored
#                 into plugin.json and marketplace.json), stamp CHANGELOG.md,
#                 commit, tag.
#                 Never pushes. scripts/release.py, modelled on session's.

SHELL := /usr/bin/env bash

.PHONY: all install check test test-latency test-export build pypi-dist release progress help

all: check test

install:
	@uv tool install -e .

# openspec/ is export-ignored (.gitattributes). Absent and not tracked here
# (no git, no repo, or a clone of the export, which never held it), its
# absence is the release export's, and the leg skips. Absent but TRACKED is a
# broken checkout, and the leg fails. Never keyed on .git alone.
# Present, it is the pointer: config.yaml names the store (`store: <id>`).
# A store this machine has not registered (its id is not in
# `openspec store list --json`) cannot be validated, and the leg skips,
# naming it. With no openspec on PATH at all the leg runs, and fails: the
# tool is missing, not the store.
check:
	@echo "== openspec validate --all --strict"
	@if [ -d openspec ]; then \
	  store=$$(sed -n 's/^store: *//p' openspec/config.yaml 2>/dev/null); \
	  if [ -n "$$store" ] && command -v openspec >/dev/null 2>&1 && \
	     ! openspec store list --json 2>/dev/null | grep -Eq "\"id\": *\"$$store\""; then \
	    echo "openspec/ points at store $$store, not registered here: skipped"; \
	  else openspec validate --all --strict; fi; \
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

# PyPI shows README.md, where the repo's RELATIVE links 404. pypi-dist builds
# from the release TAG, never the checkout: `git archive` into a scratch stage,
# pin the stage's README links to the tag, `uv build` there into dist/, then
# check BOTH the wheel and the sdist for any relative link left. The links
# point at the PUBLIC repo's tag, and the dists are built from the LOCAL one,
# so the two must be the SAME COMMIT: it refuses unless the kit's
# `pypi_readme.py tag-check` finds the tag on PUBLIC_REMOTE at the commit the
# local tag names (a missing public tag, or a moved or stale local one,
# refuses, naming both shas when they differ). A kit too old to have
# tag-check refuses too.
# The rewriter is sessionkit's scripts/pypi_readme.py, the family's one copy;
# this repo keeps none. KIT_DIR is a sessionkit checkout (a sibling of this
# one by default), and PYPI_README runs the script there. All three variables
# are overridable (`make pypi-dist KIT_DIR=...`, or PYPI_README=... outright).
# Left at its default, PYPI_README needs the script under KIT_DIR, and the
# target refuses, naming KIT_DIR=, when it is not there.
KIT_DIR ?= ../sessionkit
PYPI_README ?= python3 $(KIT_DIR)/scripts/pypi_readme.py
PUBLIC_REMOTE ?= https://github.com/sessionkit/sessionmail.git
pypi-dist:  ## wheel + sdist from tag v<version>, README links pinned to it; the tag must be the same commit on PUBLIC_REMOTE
	@if [ "$(origin PYPI_README)" = file ] && [ ! -f "$(KIT_DIR)/scripts/pypi_readme.py" ]; then \
	  echo "pypi-dist: no $(KIT_DIR)/scripts/pypi_readme.py; set KIT_DIR= to a sessionkit checkout (or PYPI_README= to the rewriter)" >&2; exit 1; fi
	@set -euo pipefail; v="v$$(uv version --short)"; \
	$(PYPI_README) tag-check --tag "$$v" --remote "$(PUBLIC_REMOTE)"; \
	stage="$$(mktemp -d)"; trap 'rm -rf "$${stage:?}"' EXIT; \
	git archive "$$v" | tar -x -C "$$stage"; \
	$(PYPI_README) rewrite --repo sessionkit/sessionmail --ref "$$v" --root "$$stage" "$$stage/README.md"; \
	(cd "$$stage" && uv build --out-dir "$(CURDIR)/dist"); \
	dist="dist/sessionmail-$${v#v}"; \
	$(PYPI_README) check "$$dist"-*.whl "$$dist".tar.gz

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
