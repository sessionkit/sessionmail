"""The MHS suites, run against the BUNDLED oracle (src/sessionmail/mhs/).

The suites in tests/mhs/ are copied with two kinds of
edit only: HERE points at src/sessionmail/mhs/ instead of the session
checkout, and the two that stub the verifier pass their unchanged
`session` stub as SESSION_MAIL_VERIFY="$BIN/session mail-verify" (the
bundled mhs-thread no longer looks `session` up on the PATH). They are the
spec for the move, so the counts are pinned at what they were in the
session repo: a suite that silently ran fewer probes would still exit 0.

A third kind of edit (SM-878): a suite starts a bundled PYTHON script the
way the package does, on the package's interpreter. `"$RUN"` (session-mail-run)
is started as `${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} "$RUN"`, the
shims' own form, and the in-process load of mhs-thread runs on
`"${SESSIONMAIL_PYTHON:-python3}"`. This runner hands every suite
$SESSIONMAIL_PYTHON = `sys.executable`; the bash shims read it themselves.
Run by hand with it unset, each suite starts the scripts exactly as before
(by the shebang, PATH's python3). The pinned counts are unchanged.

test_named_from_readers.py sits beside them and pytest collects it as is.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent / "mhs"

#: suite -> (regex over its output, the count it must report)
SUITES = {
    "test_session_mail_member_walk.sh": (r"── (\d+) passed, 0 failed", 113),
    "test_session_mail_label_path.sh": (r"passed: (\d+)\s+failed: 0", 47),
    "test_session_mail_empty_root.sh": (r"── (\d+) passed, 0 failed", 14),
    "test_mhs_converged_chain.sh": (r"(PASS) test_mhs_converged_chain", "PASS"),
    # SM-694, this package's own (not session 2fcd1d1's): run refuses a FORK.
    "test_session_mail_run_fork.sh": (r"── (\d+) passed, 0 failed", 123),
    # SM-733: a timed-out item's whole process group is killed.
    "test_session_mail_run_timeout.sh": (r"── (\d+) passed, 0 failed", 6),
    # SM-733: each item's seconds and the run's, in the headers, total and --json.
    "test_session_mail_run_seconds.sh": (r"── (\d+) passed, 0 failed", 10),
}


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_suite_passes_against_the_bundled_oracle(suite):
    # The package's interpreter, as the package hands it to every script it
    # starts (`_mhs.child_env`): set, never inherited.
    env = {**os.environ, "SESSIONMAIL_PYTHON": sys.executable}
    p = subprocess.run(["bash", str(HERE / suite)], capture_output=True, text=True, timeout=300,
                       env=env)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    pattern, expect = SUITES[suite]
    m = re.search(pattern, out)
    assert m, f"{suite}: no summary line matching {pattern!r}\n{out}"
    got = m.group(1)
    assert (int(got) if isinstance(expect, int) else got) == expect, out


#: The bundled Python scripts a suite can start or load (the rest are bash).
PY_SCRIPTS = ("session-mail-run", "session-mail-wake", "mhs-thread")


def test_every_suite_starts_a_python_script_on_the_package_interpreter():
    """The third kind of edit, pinned: every start of a bundled Python script BY PATH
    (`"$RUN"`, or `"$HERE/<script>"`) has $SESSIONMAIL_PYTHON in front of it, in the
    shims' form, and a `python3 -` that loads one is `"${SESSIONMAIL_PYTHON:-python3}" -`.
    Otherwise, on a host whose PATH python3 is older than 3.11, the script refuses
    (exit 2) and the suite is red for the environment, not for the oracle. Assignments
    (`RUN=...`) and comments are skipped."""
    prefix = '${SESSIONMAIL_PYTHON:+"$SESSIONMAIL_PYTHON"} '
    starts = ['"$RUN"', *(f'"$HERE/{s}"' for s in PY_SCRIPTS)]
    loader = '"${SESSIONMAIL_PYTHON:-python3}" - '
    bad = []
    for name in sorted(SUITES):
        for n, line in enumerate((HERE / name).read_text().splitlines(), 1):
            code = line.strip()
            if code.startswith("#") or re.match(r"[A-Z_]+=", code):
                continue
            for start in starts:
                for i in [m.start() for m in re.finditer(re.escape(start), line)]:
                    if line[:i].endswith(prefix) or line[:i].endswith(loader):
                        continue
                    bad.append(f"{name}:{n}: {code}")
            if re.search(r"(?<![\w{:-])python3\s+-\s", line) and any(s in line for s in PY_SCRIPTS):
                bad.append(f"{name}:{n}: {code}")
    assert not bad, "a suite starts a bundled Python script on PATH's python3:\n" + "\n".join(bad)
