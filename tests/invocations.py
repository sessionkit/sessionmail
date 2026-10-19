"""One valid minimal call per verb - shared by the gate and the burndown.

Kept beside the tests because it is test data: if a verb cannot be
invoked from this table, its arguments changed and both the gate and
`make progress` should say so.
"""

from __future__ import annotations

#: One valid minimal call per verb. Doubles as documentation: if a verb
#: cannot be invoked from this table, its arguments changed.
#:
#: A verb that BLOCKS needs a bound here, or the burndown - which counts
#: by running every verb - hangs for ever on it. `pager listen` blocks by
#: design; `--timeout 0` makes it look once and return 124. Measured
#: Wed 2030-09-11, the first time a built verb was a blocking one.

#: RFC 2606 reserves `.invalid` so that it can never resolve. The recipient
#: below is in it DELIBERATELY, and `tests/test_scaffold.py` asserts it stays
#: that way - see "the recipient is reserved, not merely wrong".
RESERVED_TLD = ".invalid"

INVOCATIONS = {
    # NEVER a real recipient here. This table is RUN - by the gate and by
    # `make progress` - through the real MTA, so a name anyone can receive on
    # is mail that actually arrives. With "judy" it delivered one bare
    # `Subject: subject` to that box per run: 189 of them piled up, one per
    # run since the table was written.
    #
    # A name postfix has no route for is refused by the oracle's own
    # deliverability preflight, which submits NOTHING - "NOTHING WAS
    # SUBMITTED - not to this recipient and not to any other on this send".
    # `message send` turns the oracle's exit 3 into its own exit 3 (message.py:
    # `EX_REFUSED if code == 2 else 3`), and the gate and the burndown read
    # any exit but 69 as "built", so the verb stays exercised and stays counted.
    "message send": ["send", f"nobody-zz-not-a-box{RESERVED_TLD}", "-s", "subject", "--no-body"],
    "message list": ["list"],
    "message tree": ["tree", "id@host"],
    "message read": ["read", "1"],
    "message mark": ["mark", "id@host", "done"],
    "message verify": ["verify", "id@host"],  # no such message: exit 3, nothing run
    "message mcp": ["mcp"],  # stdio; an empty stdin closes it at once (exit 0)
    # SM-748: a full session id with no state in the sandbox: exit 3, no such
    # session - built, and read-only, so the burndown can run it anywhere.
    "message receipt": ["receipt", "id@host", "--session",
                        "00000000-0000-4000-8000-000000000000"],
    "pager status": ["status"],
    "pager listen": ["listen", "--timeout", "0"],
    "pager mcp": ["mcp"],
    "memory ls": ["ls"],
    "memory tree": ["tree", "/ASKS"],
    "memory cat": ["cat", "/BOOT/latest"],
    "memory run": ["run", "/BOOT/WAKE"],
    # A box no map can resolve, for the same reason as `message send` above:
    # this table is RUN, and once do-004/do-005 build `init` a real box here
    # would be MINTED INTO on every gate run. The spec refuses a missing BOX
    # with exit 2 and mints nothing (changes/memory-init, "init refuses loudly
    # and never forks"), so the row stays exercised and stays harmless.
    "memory init": ["init", f"nobody-zz-not-a-box{RESERVED_TLD}"],
    "memory mcp": ["mcp"],
}
