"""A scaffolded command that is not built yet.

A stub makes running an unbuilt verb do nothing, gives the tests an
automated check of how much is done, and points at the spec it will meet.

So a stub does three things and no more: it names the requirement it will
satisfy, it touches nothing, and it exits a code no real verb uses. The
count of commands still exiting that code IS the burndown, and nobody
maintains it by hand.

`tests/test_scaffold.py` asserts every requirement named here exists
verbatim in its spec file, so the link cannot rot in silence.
"""

from __future__ import annotations

import click

#: sysexits.h EX_UNAVAILABLE. No verb in the authored help pages uses it:
#: they document 0, 1, 2, 3 and 124 only, so this code is unambiguous.
EX_UNIMPLEMENTED = 69


def stub(capability: str, requirement: str, spec: str | None = None) -> None:
    """Report that this command is scaffolded but unbuilt, then exit 69.

    SPEC defaults to the ratified `openspec/specs/<capability>/spec.md`. A verb
    whose requirement is still a proposed change names that change's delta
    instead (`openspec/changes/<change>/specs/<capability>/spec.md`), so the
    link points at text that exists rather than at a file that lacks it.
    """
    ctx = click.get_current_context()
    spec = spec or f"openspec/specs/{capability}/spec.md"
    click.echo(f"{ctx.command_path}: NOT IMPLEMENTED", err=True)
    click.echo(f"  spec:        {spec}", err=True)
    click.echo(f"  requirement: {requirement}", err=True)
    ctx.exit(EX_UNIMPLEMENTED)
