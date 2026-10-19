#!/usr/bin/env python3
"""The burndown: how much of the CLI is built, counted by running it.

An automated check, usable by the tests, of how much of the CLI is done.

A verb is unbuilt if it exits 69 (`sessionmail._stub.EX_UNIMPLEMENTED`).
Nothing is maintained by hand: the number falls as stubs are filled in.
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from invocations import INVOCATIONS  # noqa: E402
from sessionmail import pager  # noqa: E402

# `pager listen` sends one "listening" mail per arm (pager-filters).
# Counting what is built must never mail anyone: the sweep runs in
# the caller's own environment, so the send seam is a no-op here.


def install_no_send() -> None:
    """Point the send seam at a no-op with the seam's own arity, `(req, env,
    mint)`; tests/test_progress_script.py fails if the two ever drift."""
    pager._mta_send = lambda req, env, mint: "<progress-no-send@invalid>"


EX_UNIMPLEMENTED = 69


def main() -> int:
    install_no_send()
    runner = CliRunner()
    rows, built = [], 0
    for label in INVOCATIONS:
        name, argv = label.split()[0], INVOCATIONS[label]
        cli = import_module(f"sessionmail.{name}").cli
        result = runner.invoke(cli, argv, prog_name=name)
        if result.exit_code == EX_UNIMPLEMENTED:
            requirement = ""
            for line in result.stderr.splitlines():
                if "requirement:" in line:
                    requirement = line.split("requirement:", 1)[1].strip()
            rows.append(("stub", label, requirement))
        else:
            built += 1
            rows.append(("BUILT", label, f"exit {result.exit_code}"))

    width = max(len(label) for _, label, _ in rows)
    for state, label, note in rows:
        print(f"  {state:5}  {label:<{width}}  {note}")
    total = len(rows)
    pct = 100 * built // total if total else 0
    print(f"\n  {built}/{total} verbs built ({pct}%) - "
          f"counted by running each one, not by a checklist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
