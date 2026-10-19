"""scripts/progress.py counts the verbs by running each one, and its sweep runs
`pager listen --timeout 0` in the caller's own environment. So its send stub
must take exactly what the real send seam takes, or the stub raises TypeError
and the listener's catch-all swallows it: nothing is mailed, by accident."""

import importlib.util
import inspect
from pathlib import Path

from sessionmail import pager

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "progress.py"


def load_script():
    spec = importlib.util.spec_from_file_location("progress_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_progress_send_stub_takes_what_the_send_seam_takes(monkeypatch):
    real = pager._deliver_by_mta
    monkeypatch.setattr(pager, "_mta_send", real)  # restored after the test
    module = load_script()
    module.install_no_send()
    stub = pager._mta_send
    assert stub is not real, "the sweep must not use the real MTA send"
    # The same parameters as the seam (names and order), so a new seam
    # argument that the stub lacks fails here, not silently at a sweep.
    assert list(inspect.signature(stub).parameters) == list(inspect.signature(real).parameters)
    # And it is callable the way send_note calls it: three positionals.
    assert stub(object(), {}, object()) == "<progress-no-send@invalid>"
