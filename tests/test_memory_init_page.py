"""`memory init` - the page first, the body a stub (SM-186 do-003).

Help-driven development (slipbox `help-driven-development`): the page is the
artefact under review, so what it PROMISES is pinned here against the spec
it was authored from - `openspec/changes/memory-init/specs/memory/spec.md`,
a PROPOSED change. The body is built by do-004/do-005 (`tests/test_root.py`,
`tests/test_init_mint.py`), and `--supersede` on a box with a root by do-007:
no stub is left on `init`.

The budget (40 lines / 400 tokens) and the link-back are `test_scaffold.py`'s
gates and are not repeated here.
"""

from __future__ import annotations

import re

import click
from click.testing import CliRunner

from _export import export_ignored
from sessionmail.memory import INIT_REQUIREMENT, INIT_SPEC, cli


#: The ruled set: six by default, four selectable.
DEFAULT = ("CHARTER", "RULES", "HOW-TO", "BOOT", "CONVO", "ASKS")
SELECTABLE = ("IDEAS", "RULINGS", "LESSONS", "TOOLS")

#: The closed table - "init refuses loudly and never forks": these codes and
#: no others. 1 is usage, NOT click's default 2 (do-002 review N2); the remap
#: in code is do-004's, but the page must never promise 2 for usage.
EXIT_LINE = "Exit: 0 ok · 1 usage · 2 not found or ambiguous · 3 signature refused"


def page() -> str:
    result = CliRunner().invoke(cli, ["init", "-h"], prog_name="memory")
    assert result.exit_code == 0, result.output
    return result.output


def init_command() -> click.Command:
    return cli.get_command(None, "init")


def test_box_is_a_required_argument_never_the_environment():
    """Q7, RULED: `init` takes the box as an ARGUMENT
    rather than from the environment (spec delta, "The root hint comes from
    the environment", MODIFIED)."""
    args = [p for p in init_command().params if isinstance(p, click.Argument)]
    assert [(a.name, a.required, a.envvar) for a in args] == [("box", True, None)]
    assert page().startswith("Usage: memory init BOX ")


def test_no_box_is_refused_before_the_body_runs():
    """A missing BOX is a parse refusal, so the stub - and later the body -
    never runs. It exits 1, the page's `1 usage`, not click's default 2
    (do-002 review N2; the remap landed in do-004, `memory.InitCommand`)."""
    result = CliRunner().invoke(cli, ["init"], prog_name="memory")
    assert result.exit_code == 1, result.exit_code
    assert "BOX" in result.output
    assert "NOT IMPLEMENTED" not in result.output


def test_every_flag_on_the_page_is_a_real_parameter():
    """HDD step 2: every flag on the page becomes a real parameter, so the
    parser is honest. The four are the spec's: --labels, --plus, --record,
    --supersede."""
    opts = {o for p in init_command().params for o in getattr(p, "opts", [])}
    assert {"--labels", "--plus", "--record", "--supersede"} <= opts
    text = page()
    for flag in ("--labels", "--plus", "--record", "--supersede"):
        assert flag in text


def test_no_flag_touches_a_key_and_none_offers_mail_grammar():
    """do-002 ("Canon is the box key, and init consumes the key without
    owning it"): init SHALL NOT offer any flag that mints, rotates, hands over
    or revokes a key, and SHALL NOT accept a key on the command line or from
    a file. `mail_grammar` is FROZEN:
    --record writes mail_root: only."""
    opts = [o for p in init_command().params for o in getattr(p, "opts", [])]
    for opt in opts:
        assert not re.search(r"key|revoke|rotate|handover|grammar", opt), opt
    assert "mail_grammar" not in page()


def test_the_exit_line_is_the_closed_table_and_nothing_else():
    text = page()
    assert EXIT_LINE in text
    assert "69" not in text, "the stub's code is the burndown's, never the page's"


def test_the_page_names_the_ruled_ten_and_the_default_six():
    text = " ".join(page().split())
    assert "Default labels: " + " ".join(DEFAULT) in text
    for name in SELECTABLE:
        assert name in text


def test_the_page_says_the_pointer_is_the_blessing():
    """Q8, RULED: a new root is a NEW TREE, and the
    pointer moves only after it verifies. D11's "a replacement root that
    Supersedes: the old one" predates the ruling and must not come back."""
    text = " ".join(page().split())
    assert "a new tree" in text
    assert "the pointer is the blessing" in text
    assert "replacement root" not in text


def test_supersede_is_built_and_no_stub_is_left_on_init(tmp_path, monkeypatch):
    """do-007 built `--supersede` on a box that HAS a root, the last stub on
    `init`. The library has no `NotBuilt` left, the CLI hands `--supersede`
    to the body and exits with ITS code, never the stub's 69, and the
    requirement the page cites still exists in the change's delta (the next
    test; the proof of the supersede itself is `tests/test_init_mint.py`)."""
    from sessionmail import _init

    assert not hasattr(_init, "NotBuilt")
    asked = []

    def body(box, **kw):
        asked.append((box, kw["supersede"]))
        return _init.InitResult(root_id="r2@example.invalid", minted=(), existing=(),
                                recorded=None, superseded="r1@example.invalid")

    monkeypatch.setattr(_init, "run", body)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["init", "somebox", "--supersede"], prog_name="memory")
    assert list(tmp_path.iterdir()) == []
    assert (result.exit_code, asked) == (0, [("somebox", True)])
    assert "requirement:" not in result.output


def test_the_cited_requirement_is_in_the_change_delta():
    """The requirement the page cites still exists in the change's delta.
    openspec/ is export-ignored, so in the release export this SKIPS (SM-849);
    the CLI half above still runs there."""
    delta = export_ignored(INIT_SPEC).read_text()
    assert f"### Requirement: {INIT_REQUIREMENT}\n" in delta
