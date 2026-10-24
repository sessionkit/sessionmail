"""The fake search engine, shared by the suites that need a real answer.

Beside `invocations.py` and for the same reason: it is test data, not test
logic. `tests/conftest.py` gives every test an engine that answers "no
results"; this is the one a test drives on purpose.

Fixtures are `alpha` and `beta` at `example.invalid`, never a real seat or
host - `slipbox/project-conventions.md` convention 9.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

#: It logs the argv it was handed so a test can assert the query went
#: through unedited, and it can be told to fail, so the failure path is
#: watched rather than assumed (Charter 3).
FAKE_NOTMUCH = """#!{python}
import os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args and args[0].startswith("--config="):
    args = args[1:]     # every call names its profile first (test_notmuch_profile_host)
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("Error: Cannot open database\\n")
    sys.exit(1)
if os.environ.get("FAKE_GARBAGE"):
    sys.stdout.write("not json at all")
    sys.exit(0)
if args[0:1] == ["count"] and "--lastmod" in args:
    if os.environ.get("FAKE_NO_LASTMOD"):
        sys.exit(1)
    sys.stdout.write("0\\tfake-uuid\\t" + os.environ.get("FAKE_REVISION", "0") + "\\n")
    sys.exit(0)
sys.stdout.write(open(os.environ["FAKE_JSON"]).read())
"""

#: The same engine, but it ANSWERS THE QUERY: `FAKE_KNOWN` is the comma-
#: separated list of ids that exist, and an `id:` query for anything else
#: answers `[]` - which is what real notmuch does. Needed by the many-id
#: scenario (specs/message, "Read and mark take many ids in one call"): one
#: replayed payload cannot express "two of these three are in the box".
FAKE_NOTMUCH_BY_ID = """#!{python}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("\\x1f".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args and args[0].startswith("--config="):
    args = args[1:]     # every call names its profile first (test_notmuch_profile_host)
if args[0:1] == ["count"] and "--lastmod" in args:
    sys.stdout.write("0\\tfake-uuid\\t0\\n")
    sys.exit(0)
known = [i for i in os.environ.get("FAKE_KNOWN", "").split(",") if i]
wanted = sys.argv[-1]
wanted = wanted[3:] if wanted.startswith("id:") else wanted
if wanted in known:
    one = {{"id": wanted, "timestamp": 1, "date_relative": "Today 09:00",
           "headers": {{"From": "beta@example.invalid", "Subject": wanted, "Date": "..."}}}}
    sys.stdout.write(json.dumps([[[one, []]]]))
else:
    sys.stdout.write("[]")
"""


def message(mid: str, timestamp: int, sender: str, subject: str) -> dict:
    """One message in notmuch's `show --format=json` shape."""
    return {
        "id": mid,
        "timestamp": timestamp,
        "date_relative": "Today 09:00",
        "headers": {"From": sender, "Subject": subject, "Date": "..."},
    }


def forest(*messages: dict) -> list:
    """notmuch wraps messages as a forest of [message, [replies]] pairs."""
    return [[[m, []] for m in messages]]


def write_fake(path: Path, source: str = FAKE_NOTMUCH) -> Path:
    """Write SOURCE as an executable script with an ABSOLUTE interpreter.

    Not `#!/usr/bin/env python3`: a test may replace $PATH (the version
    -mismatch scenario does exactly that), and a fake that cannot start
    because `env` lost python3 looks exactly like the code under test
    refusing to run. Measured Sun 2030-06-09.
    """
    path.write_text(source.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path
