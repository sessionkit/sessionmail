"""Every refusal carries a KIND, and the gate that holds the vocabulary.

openspec/changes/agents-send-door, "A refusal says what KIND it is". RULED
AS A STRUCTURE: three groups, and a test that forces every refusal to carry
a kind. Review R2: a list in a spec holds nothing on its own, so the word is
a REQUIRED argument, and this file walks every site in the package.
The fourteen WORDS (thirteen until SM-716 added `message-id`) are proposed
and may change before release; when they do, `_sender.KINDS_*` and the spec
move together and this gate says what is left.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sessionmail import _sender

PACKAGE = Path(_sender.__file__).parent

#: Modules whose `Refused` is their OWN exception, not the sender's. seat_init.py is
#: SM-590's stdlib-only mint script, moved verbatim into the package by SM-715 do-001;
#: _launchd.py loads and unloads the seat agent's LaunchAgent, seat init's sibling.
#: Their refusals are exit 2 with "Nothing was written", outside the send door's
#: vocabulary. Named, never inferred: the pin below proves each still is its own.
OWN_REFUSED = ("seat_init.py", "_launchd.py")


def refusal_sites() -> list[tuple[str, int, ast.Call]]:
    sites = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in OWN_REFUSED:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "Refused":
                sites.append((path.name, node.lineno, node))
    return sites


@pytest.mark.parametrize("name", OWN_REFUSED)
def test_a_module_left_out_of_the_walk_refuses_with_its_own_class(name):
    tree = ast.parse((PACKAGE / name).read_text())
    own = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Refused"]
    assert len(own) == 1, f"{name} must define its own Refused to stay out of the walk"
    imported = [a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names]
    assert "Refused" not in imported and not any("_sender" in a for a in imported), \
        f"{name} reaches the sender's Refused: walk it"


def test_the_walk_finds_the_sites_it_is_there_to_guard():
    assert len(refusal_sites()) >= 30, "the walk found too few `Refused(` calls to be trusted"


@pytest.mark.parametrize("name, line, call", refusal_sites(),
                         ids=[f"{n}:{l}" for n, l, _ in refusal_sites()])
def test_every_refusal_site_names_a_kind_on_the_list(name, line, call):
    kinds = [k.value for k in call.keywords if k.arg == "kind"]
    assert kinds, f"{name}:{line} refuses without a kind"
    if isinstance(kinds[0], ast.Constant):
        assert kinds[0].value in _sender.KINDS, f"{name}:{line} says {kinds[0].value!r}"
    sent = [k.value for k in call.keywords if k.arg == "sent"]
    if sent and isinstance(sent[0], ast.Constant):
        assert sent[0].value in _sender.SENT


def test_a_refusal_without_a_word_cannot_be_constructed():
    with pytest.raises(TypeError):
        _sender.Refused("no word")                       # type: ignore[call-arg]
    with pytest.raises(ValueError):
        _sender.Refused("a word not on the list", kind="bad-address")
    with pytest.raises(TypeError):                       # keyword-only: it cannot slip in as the code
        _sender.Refused("a kind where the code goes", "usage")  # type: ignore[call-arg, arg-type]


def test_the_vocabulary_is_three_groups_and_the_senders_are_two_of_them():
    # 7 and 11 since SM-716: `message-id` is the seventh INPUT word (RULED OQ2,
    # recorded as an amendment to agents-send-door, whose list moved with it).
    assert len(_sender.KINDS_INPUT) == 7 and len(_sender.KINDS_WORLD) == 4
    assert len(set(_sender.KINDS)) == 11, "a word appears twice"


@pytest.mark.parametrize("field, kind", [("from_", "address"), ("to", "address"), ("subject", "subject"),
                                         ("project", "header"), ("task", "header")])
def test_a_line_break_takes_its_kind_from_the_field_it_is_in(field, kind):
    """The one site whose kind is not a constant, so the walk cannot read it."""
    req = _sender.Request(to="eve", from_="ivan", subject="s")
    setattr(req, field, "a\nb")
    with pytest.raises(_sender.Refused) as refused:
        _sender.validate(req)
    assert refused.value.kind == kind and refused.value.sent == "no"


def test_only_a_message_the_mta_was_handed_is_sent_unknown():
    unknown = [(n, l) for n, l, c in refusal_sites()
               for k in c.keywords if k.arg == "sent" and getattr(k.value, "value", None) == "unknown"]
    assert len(unknown) == 1, f"exactly one site may say sent=unknown (the MTA ran and failed): {unknown}"
