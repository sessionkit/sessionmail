"""What a printed thing costs the reader, in tokens - an estimate, marked.

`specs/help` budgets pages in tokens and `tests/test_scaffold.py` counts
them with the fleet's `skill tokens` when it is there. A verb's footer
cannot afford a subprocess per call, so this is the same fallback that
test uses: about four characters per token, always printed with an
approximately sign so nobody reads it as measured.
"""

from __future__ import annotations


def estimate(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
