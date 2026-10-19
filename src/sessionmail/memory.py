"""`memory` - the maildir as a filesystem. Scaffolded from its help pages.

Pages: `help/memory.txt` and `help/memory/*.txt`. Every flag below is
on one of those pages. Bodies are stubs: see `_stub`.
"""

from __future__ import annotations

import click

from . import __version__
from ._help import CONTEXT_SETTINGS, OrderedGroup, PageCommand, unwrapped
from ._init import EX_USAGE
from . import _mhs, _settings

CAPABILITY = "memory"

BOX_HELP = "read a shared box instead of your own"


@click.group(
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    options_metavar="",
    subcommand_metavar="<verb> PATH [args]",
    epilog=unwrapped(
        """PATH walks from the root by subject: /ASKS/12, /BOOT/latest,
/CONVO/today. Every name a listing prints is a valid segment. Root is
$<AGENT>_ROOT; the agent is the environment; --box NAME reads a shared
box. Writing is sending: `message send AGENT+memory -r LABEL-ID ...`.
A change is a new item that --supersedes the old; the old is hidden,
never deleted. --supersedes takes ONE id: one mail retires one item,
and a repeated flag keeps only the last. Two live items under one
number are a FORK, which `run` refuses: retire the unwanted copy with
an UNNUMBERED mail (a numbered one is one more copy).

Exit: 0 ok · 3 signature refused · 4 `run` is OFF (SESSIONMAIL_MEMORY_RUN
is not 1); each verb's page (-h) gives the rest of its own codes"""
    ),
)
@click.version_option(__version__, "--version", "-V")
def cli() -> None:
    """memory - a mailbox as a filesystem: labels, numbered items, never edited"""


def _go(verb: str, argv: list[str], env: dict[str, str] | None = None) -> None:
    """Hand VERB's argv to the oracle and exit as it exited. ENV, when
    given, is the oracle's whole environment."""
    try:
        code = _mhs.run([*_mhs.oracle(verb), *argv], env=env)
    except FileNotFoundError as exc:
        click.echo(f"memory {verb}: {exc}", err=True)
        raise SystemExit(2)
    raise SystemExit(code)


def _box(box: str | None) -> list[str]:
    """`--box NAME` onto the bash, which takes it on `ls` and `tree` (the
    read-only listings). The root hint is still `$<BOX>_ROOT` from the
    environment; without it the bash takes its announced subject search.
    It lets one seat read another seat's tree as a model."""
    return ["--box", box] if box else []


def _no_box(box: str | None, verb: str) -> None:
    if box:
        raise click.UsageError(f"--box is not carried by the transport yet: the bash "
                               f"`session mail {verb}` reads the root hint from the "
                               f"environment, one seat at a time")


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[PATH] [--all] [--box NAME]",
    short_help="[PATH]                       one level under PATH (default /)",
    epilog=unwrapped(
        """Every name it prints is a valid next segment of PATH.

Exit: 0 ok · 1 usage · 2 path not found or ambiguous"""
    ),
)
@click.argument("path", required=False, metavar="PATH")
@click.option("--all", "show_all", is_flag=True,
              help="keep superseded items (default: hidden)")
@click.option("--box", metavar="NAME", help=BOX_HELP)
def ls(path: str | None, show_all: bool, box: str | None) -> None:
    """memory ls - one level under an MHS PATH: its labels and items"""
    argv = [path] if path else []
    _go("ls", argv + (["--all"] if show_all else []) + _box(box))


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="PATH [--depth N] [--all] [--dates] [--box NAME]",
    short_help="PATH [--depth N] [--all]     the subtree, subjects only",
    epilog=unwrapped(
        """Ends with its own token cost. /today and /yesterday resolve to ISO
dates first; a segment matching two spellings is refused as ambiguous.

Exit: 0 ok · 1 usage · 2 path not found or ambiguous"""
    ),
)
@click.argument("path", metavar="PATH")
@click.option("--depth", type=int, metavar="N",
              help="cut N levels below PATH (0 = PATH's children only)")
@click.option("--all", "show_all", is_flag=True, help="keep superseded items")
@click.option("--dates", is_flag=True, help="show [sender date] on each item")
@click.option("--box", metavar="NAME", help="read a shared box")
def tree(path: str, depth: int | None, show_all: bool, dates: bool, box: str | None) -> None:
    """memory tree - the subtree under an MHS PATH, subjects only"""
    argv = [path]
    if depth is not None:
        argv += ["--depth", str(depth)]
    if show_all:
        argv.append("--all")
    if dates:
        argv.append("--dates")
    _go("tree", argv + _box(box))


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="PATH [--all] [--id] [--full] [--box NAME]",
    short_help="PATH [--all] [--id]          one item with its body",
    epilog=unwrapped(
        """PATH may end in `latest` for the newest signed item under a label
(/BOOT/latest). A PATH that names a NON-MEMBER (unsigned, a bad
signature, or From/To not the owner) is refused, its reason and
Message-ID on stderr, exit 3. The body shown is the text the signature
covers: one that cannot be shown as signed is withheld, nothing on
stdout and the reason on stderr, exit 3. A signed item with no
text/plain part says so. A shown item's footer says "signature verifies ·
signer NOT pinned": the key is the one the mail carries, and nothing yet
says whose it is.

Exit: 0 ok · 1 no such child, an ambiguous spelling, a verify timeout, or
the executor's usage · 2 a FORK, or click's usage (a missing PATH, an
unknown flag) · 3 signature refused"""
    ),
)
@click.argument("path", metavar="PATH")
@click.option("--all", "show_all", is_flag=True,
              help="print superseded versions too, oldest first")
@click.option("--id", "show_id", is_flag=True,
              help="print only the item's Message-ID (bare id@host)")
@click.option("--full", is_flag=True,
              help="lift the 40 lines / 400 tokens cut on the body")
@click.option("--box", metavar="NAME", help="read a shared box")
def cat(path: str, show_all: bool, show_id: bool, full: bool, box: str | None) -> None:
    """memory cat - one item under an MHS PATH, with its body"""
    _no_box(box, "cat")
    argv = [path]
    if show_all:
        argv.append("--all")
    if show_id:
        argv.append("--resolve")   # the bash spelling of "the Message-ID only"
    if full:
        argv.append("--bodies")
    _go("cat", argv)


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="PATH [--list] [--only N,...] [--json]",
    short_help="PATH [--list] [--only N,..]  OFF unless opted in; trusted signers only",
    epilog=unwrapped(
        f"""OFF by default: nothing runs unless SESSIONMAIL_MEMORY_RUN=1.
--list runs nothing and needs no opt-in. Not an MCP tool.

Trusted signers only: a mail item runs only if its signer passes
--trust: a registry cert for its key whose agent is the item's From
($SESSION_REGISTRY, else "registry" in {_settings.SHOWN};
with neither, all are refused). An UNBLESSED signer (cert, no blessing) runs,
so headed. A refused item is named with its TRUST: line; the rest
run. A non-member (unsigned, a bad signature, From/To not the owner) is
skipped.

An unreachable PATH FALLS BACK to ~/.<agent>/wake.toml, else to `ls /`
and the [BOOT] letter; neither is put to --trust.

The body is the program; the subject only documents it and never runs.
A placeholder body is skipped once its signer passes --trust. `# budget=N`
on the subject caps output tokens (default 600). A FORK (two live copies
of one number) runs neither.

Exit: 0 nothing refused (skips and fallbacks too) · 1 the executor's usage
· 2 a FORK, or click's usage · 3 an item's signer refused by --trust
(outranks 2) · 4 OFF"""
    ),
)
@click.argument("path", metavar="PATH")
@click.option("--list", "list_only", is_flag=True,
              help="print the script that would run; run nothing")
@click.option("--only", metavar="N,...", help="only these item numbers")
@click.option("--json", "as_json", is_flag=True, help="machine-readable results")
def run(path: str, list_only: bool, only: str | None, as_json: bool) -> None:
    """memory run - run the numbered items under an MHS PATH, in order; opt-in"""
    if not list_only and not _mhs.run_allowed():
        # The click door's half of the gate: refused before the
        # oracle is reached. The executor holds the same gate, by path.
        click.echo(_mhs.RUN_OFF, err=True)
        raise SystemExit(_mhs.RUN_OFF_EXIT)
    argv = [path]
    if list_only:
        argv.append("--list")
    if only:
        argv += ["--only", only]
    if as_json:
        argv.append("--json")
    # The executor asks the verifier --trust, which reads only
    # $SESSION_REGISTRY. Fill it from config as `message verify` does.
    _go("run", argv, env=_mhs.trust_env())


@cli.command(
    cls=PageCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="[BOX] [--dry-run]",
    short_help="[BOX] [--dry-run]            the boot readout, peek-only",
    epilog=unwrapped(
        """The boot readout, PEEK-ONLY: a wake is a notification, never a
completion, so this verb claims nothing - new/ is untouched, and
read-state, closed-state and truth-state (Supersedes) are only projected.
Three sections, each budget-capped and ending with its own token cost:
BOOT, the newest surviving letter to BOX+boot, body included only if its
signature verifies AND it is From BOX, under "signature verifies · signer
NOT pinned" (the key is the one the mail carries; nothing yet says whose
it is); else REFUSED, body withheld, nothing older shown in its place;
RECENT, threads touching BOX in the last two days, one line
each, closed loops dropped; COUNTS, what rests unread and the next hop.
BOX defaults to the seat, then the session, as `pager listen` resolves
it, so wake and listen always describe one agent. `memory run /BOOT/WAKE`
is the script-mail wake; this is the readout it was written to replace.

Exit: 0 ok · 1 box not found, or the executor's usage · 2 click's usage
(an unknown flag) · 3 the BOOT letter refused (after every section printed)"""
    ),
)
@click.argument("box", required=False, metavar="BOX")
@click.option("--dry-run", is_flag=True,
              help="BOOT prints; RECENT and COUNTS show their command instead of running it")
def wake(box: str | None, dry_run: bool) -> None:
    """memory wake - the boot readout: the letter, recent threads, counts; peek-only"""
    argv = [box] if box else []
    if dry_run:
        argv.append("--dry-run")
    _go("wake", argv)


#: `memory init` is specified in a PROPOSED change, not yet in the ratified
#: spec: the change's delta, and the requirement its page cites.
INIT_SPEC = "openspec/changes/memory-init/specs/memory/spec.md"
INIT_REQUIREMENT = "init prints the root a box has, or mints one with the standard labels"


class InitCommand(PageCommand):
    """`init`'s table is CLOSED and gives 2 to "not found or ambiguous" (spec
    delta, "init refuses loudly and never forks"), but click exits 2 on every
    usage error. So a usage error on `init` (a missing BOX, an unknown flag)
    exits 1, as the page promises. Only the code changes: click still prints
    its own usage message."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as exc:
            exc.exit_code = EX_USAGE
            raise


@cli.command(
    cls=InitCommand,
    context_settings=CONTEXT_SETTINGS,
    usage="BOX [--labels L,.. | --plus L,..] [--record FILE] [--supersede]",
    short_help="BOX [--record FILE]          BOX's root: print it, or mint it",
    epilog=unwrapped(
        """BOX is an argument, never the environment. A [ROOT] To BOX signed by
the box key is printed, never minted twice; only missing labels are added,
so a second run sends nothing. Every mint is a message To BOX, signed with
the box key (never the session's) and verified. Default labels: CHARTER
RULES HOW-TO BOOT CONVO ASKS; also IDEAS RULINGS LESSONS TOOLS.

Refuses, naming what it wanted: no such BOX; a root hint ($<BOX>_ROOT, or
mail_root: in FILE) that disagrees with the box; two of one label; the box
key absent or unusable (nothing minted); a root that does not verify.
Never mints a second live root.

--supersede mints a NEW root, a new tree; the old one stays as it was. The
pointer (mail_root:, the export line) moves only after it verifies: the
pointer is the blessing, Supersedes: a courtesy. Last line: export
<BOX>_ROOT='<id>'.

Exit: 0 ok · 1 usage · 2 not found or ambiguous · 3 signature refused"""
    ),
)
@click.argument("box", metavar="BOX")
@click.option("--labels", metavar="L,..",
              help="mint exactly these, in place of the default set")
@click.option("--plus", metavar="L,..",
              help="mint the default set and these too")
@click.option("--record", metavar="FILE",
              help="write mail_root: into FILE's front matter; commit FILE")
@click.option("--supersede", is_flag=True,
              help="mint a new root and its labels, then move the pointer")
def init(box: str, labels: str | None, plus: str | None, record: str | None,
         supersede: bool) -> None:
    """memory init - print BOX's root, or mint [ROOT] and the standard labels"""
    # A thin wrapper (seam.md §6): the body is `_init.run`, the same body as the
    # library's `init()`, with each line said as it happens. An InitError's
    # code IS the exit; nothing here decides one. A usage error raised in this
    # body is `InitError(1)`, never click's UsageError or `ctx.fail()`, which
    # exit 2: on init's table, 2 is "not found".
    from . import _init

    try:
        _init.run(box, labels=labels, plus=plus or (), supersede=supersede, record=record,
                  report=click.echo)
    except _init.InitError as exc:
        click.echo(f"memory init: {exc}", err=True)
        sent = exc.result
        if sent is not None:
            click.echo(f"memory init: already sent: {' '.join(sent.minted)}; "
                       f"root {sent.root_id or '(none)'}"
                       + (f"; supersedes {sent.superseded}" if sent.superseded else ""),
                       err=True)
        raise SystemExit(exc.code)


from ._mcp import attach  # noqa: E402  - the `mcp` verb, declared once for the three

attach(cli, CAPABILITY)


# `python -m sessionmail.memory` IS `memory`. Without this the module imports,
# does nothing and exits 0, and the MCP send
# door starts its child exactly this way (openspec/changes/agents-send-door,
# "Running the module sends, or says why not - it never does nothing").
# `prog_name`, or the page says `Usage: memory.py ...`.
if __name__ == "__main__":
    cli(prog_name="memory")
