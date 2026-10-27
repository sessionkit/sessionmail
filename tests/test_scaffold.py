"""The scaffold's own gates.

Help-driven development (slipbox `help-driven-development`): the help page
is written first, the command is scaffolded to match it, and the body is a
stub until the requirement is built. That makes three things measurable
before anything works, and this module measures them:

- **Budget** - every rendered page is within the reader's 40 lines / 400
  tokens (`openspec/specs/help/spec.md`, "A help page fits the reader's
  budget").
- **Link** - every stub names a requirement that really exists in the spec
  it cites, so the link-back cannot rot in silence.
- **Progress** - `make progress` counts the verbs still exiting 69. The
  gate here only insists that a verb which claims to be a stub is telling
  the truth; building one never breaks this suite.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "openspec" / "specs"

#: `openspec/specs/package/spec.md`, "One package, three commands".
COMMANDS = ("message", "pager", "memory")

#: `openspec/specs/help/spec.md`, "A help page fits the reader's budget",
#: AMENDED to the fold (`openspec/changes/above-the-fold`). The concept, and why 40/400:
#: `slipbox/above-the-fold.md` - the first 40 lines of anything an agent
#: reads are all it may get, so what is NEEDED goes there and the rest may
#: follow below. A threshold, not a cutoff: fewer tokens, and the right
#: ones in the first 40 lines.
FOLD_LINES = 40
FOLD_TOKENS = 400
#: The old names, for the modules that import them.
LINE_BUDGET, TOKEN_BUDGET = FOLD_LINES, FOLD_TOKENS

#: A page MAY overrun the fold if someone SIGNS for it: a date, a name and
#: a reason, in the SOURCE and never in the rendered page. Click drops everything after
#: a form feed in a docstring, so the note sits at the bottom of the
#: docstring and no reader pays a token for it.
FOLD_OVERRIDE = re.compile(
    r"fold-override:\s*(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<signer>[^\n-]+?)\s+-\s+(?P<reason>\S[^\n]*)"
)


def fold_override(doc: str | None) -> str | None:
    """The signed reason this page may overrun, or None. Read from the
    docstring BELOW the form feed: an override that renders is not one."""
    if not doc or "\f" not in doc:
        return None
    m = FOLD_OVERRIDE.search(doc.split("\f", 1)[1])
    return m.group(0).strip() if m else None

#: `sessionmail._stub.EX_UNIMPLEMENTED` - sysexits.h EX_UNAVAILABLE.
EX_UNIMPLEMENTED = 69

from _export import export_ignored  # noqa: E402  (tests/ is on the path)
from invocations import INVOCATIONS, RESERVED_TLD  # noqa: E402  (tests/ is on the path)


def group(name: str):
    """The click group behind console script NAME."""
    return importlib.import_module(f"sessionmail.{name}").cli


def pages() -> list[tuple[str, list[str], str]]:
    """(label, argv, rendered help) for every group page and every verb page."""
    runner = CliRunner()
    out = []
    for name in COMMANDS:
        cli = group(name)
        out.append((name, [], runner.invoke(cli, ["-h"], prog_name=name).output))
        for argv, _ in _walk(cli, []):
            label = " ".join([name, *argv])
            text = runner.invoke(cli, [*argv, "-h"], prog_name=name).output
            out.append((label, argv, text))
    return out


def _walk(cli, argv: list[str]):
    """(argv, command) for every verb under CLI, a noun's own verbs after it:
    `message box` is a page, and so are `message box list` and `message box
    path` (message-subscriptions 3.1). One level was enough until a verb
    grew verbs; a page the walk never reaches is a page the fold never
    checks."""
    for verb in cli.list_commands(None):
        cmd = cli.get_command(None, verb)
        yield [*argv, verb], cmd
        if isinstance(cmd, click.Group):
            yield from _walk(cmd, [*argv, verb])


def docstrings() -> dict[str, str | None]:
    """label -> the SOURCE docstring behind that page. The rendered help is
    not enough: a fold override lives below the form feed, which click
    never renders."""
    out: dict[str, str | None] = {}
    for name in COMMANDS:
        cli = group(name)
        out[name] = cli.help or (cli.callback.__doc__ if cli.callback else None)
        for argv, cmd in _walk(cli, []):
            out[" ".join([name, *argv])] = cmd.callback.__doc__ if cmd.callback else None
    return out


PAGES = pages()
DOCS = docstrings()


#: How long one `skill tokens` call may take before it counts as no count
#: (SM-773). The real counter is `uv run --script` over tiktoken: a COLD but
#: healthy run (uv building the script's env, tiktoken loading its
#: vocabulary) takes seconds, so 30s is roomy for it. A WEDGED one, a stalled
#: vocabulary fetch under the hermetic HOME, sleeps for ever (measured asleep
#: 25-37 min at ~0.15s CPU) and used to hang the whole run.
TOKENS_TIMEOUT = 30.0
#: The line `skill tokens` prints: `tokens: N`. The count is read from that
#: line, never from every digit in stdout ("v3 2030" is not a count).
TOKENS_LINE = re.compile(r"^tokens:[ \t]*(\d+)[ \t]*$", re.M)


def count_tokens(text: str) -> tuple[int, str] | tuple[None, str]:
    """(tokens, "skill tokens") from the fleet's counter, or (None, why)
    when it gives no count: not on PATH, timed out, silent, a non-zero
    exit, or no `tokens: N` line. Never an ESTIMATE (SM-799): len/3 read
    25-30% high on --help prose, so it failed pages that fit, and a guess
    the gate cannot tell from a count is not a measurement. The caller
    decides what no count means (check_fold skips the token cap).

    Every call asks afresh. There is no latch: one slow call under load
    must not change the verdict on any later page (SM-799). The counter is
    bounded by TOKENS_TIMEOUT, and a timeout kills its whole process group,
    so a `uv` or `python` grandchild is not left asleep (SM-773)."""
    exe = shutil.which("skill")
    if not exe:
        return None, "`skill` is not on PATH"
    try:
        p = subprocess.Popen(
            [exe, "tokens", "-"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,  # its own group, so killpg reaps uv too
        )
    except OSError as e:
        return None, f"`skill tokens` did not start: {e}"
    try:
        out, _ = p.communicate(text, timeout=TOKENS_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass  # the group is already gone
        p.wait()
        for pipe in (p.stdin, p.stdout, p.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        if isinstance(e, subprocess.TimeoutExpired):
            return None, f"`skill tokens` timed out after {TOKENS_TIMEOUT:g}s"
        return None, f"`skill tokens` failed: {e!r}"
    if p.returncode != 0:
        return None, f"`skill tokens` exited {p.returncode}"
    found = TOKENS_LINE.search(out)
    if found:
        return int(found.group(1)), "skill tokens"
    if not out.strip():
        return None, "`skill tokens` printed no count"
    return None, f"`skill tokens` printed no `tokens: N` line: {out.strip()[:60]!r}"


def requirement_titles(capability: str, spec: Path | None = None) -> set[str]:
    """The requirement titles in SPEC - by default the ratified spec of
    CAPABILITY, or a proposed change's delta when a stub names one."""
    spec = spec or SPECS / capability / "spec.md"
    assert spec.is_file(), f"no spec at {spec}"
    titles = set(re.findall(r"^### Requirement: (.+)$", spec.read_text(), re.M))
    assert titles, f"{spec} parsed to zero requirements - the regex is wrong"
    return titles


# ── the pages exist and are the ones the help spec counted ───────────────

def test_three_groups_and_twenty_one_verbs():
    """`help/spec.md`: "Applied [...]: three group pages and twelve verb pages"
    - a statement of what was applied, not a count requirement. Verbs 13-15
    are `message|pager|memory mcp` (a stateless MCP server from the same
    model as the CLI; THREE servers, one per command), each page authored to 40/400 first. Verb 16
    is `pager off` (Rulings 1360). `pager on` is a refusal in the resolver, not a verb.
    Verbs 17-18 (Sun 2030-06-09, SM-30): `pager hook` - the plugin's two
    hooks as verbs of the pager, so seat identity has one resolver - and
    `pager stop`, `off` by the name the standard line uses ("run `pager
    stop` to stop"). Verb 19 (Sun
    2030-06-09, changes/message-path): `message path` - the maildir a box
    name resolves to, for SessionView's Messages tab. Verb 20 (Sun 2030-06-09, changes/memory-wake):
    `memory wake` - the boot readout comes home to memory (Q11). Pages 21-23 (Sun 2030-12-08,
    message-subscriptions 3.1): the noun `message box` and its two verbs,
    `list` and `path` - `message path` stays as the old name (specs/message,
    "The old verb still answers"), so it is still counted. Pages 24-25 are
    not new: `pager hook stop` and `pager hook batch` existed, but the walk
    went one level deep until the same day and never fold-checked them.
    Pages 26-28 (Sun 2030-12-08, message-subscriptions 3.2): the noun
    `message subscription` and its verbs `add` and `remove`. Page 29 (the
    same day, 3.3): `message subscription list`, what reaches me and who
    hears a box. Minus one (the same day, 4.2): `pager subscriptions`
    RETIRED with the saved-query files it listed, so 28. Plus one (Sun
    2030-12-08, on master as "Verb 21" before the SM-374 merge):
    `message verify` - the retired `session mail verify` at the message
    door, so 29.
    Plus five (Sun 2030-12-08, pager-filters 5.1 and 5.3, SM-474 do-008):
    the noun `pager rule` and its verbs `list`, `add` and `remove`, and
    `pager why` (RULED P10), so 34. Plus one (the
    same day, pager-filters 5.2, SM-474 do-009): `pager focus sleep|off`
    (RULED P7), so 35. Plus two (Sun 2030-12-08, sessiontools session-
    settings 3.2, call (a)): the HIDDEN noun `pager settings`
    and its verb `migrate`, a host's once-only move. Walked and fold-checked
    here, but on no group page and no MCP tool, so 37. Plus three (Sun
    2030-12-08, SM-715 do-002; ruling (b): three
    commands kept, the verbs named `message seat init` and `message seat
    agent`): the HIDDEN noun `message seat` and its two verbs (hidden on the
    Q-F ruling, after `pager settings`), so 40. Plus two
    (Sun 2030-12-08, SM-726 do-003, design.md (l3)): `pager mute` and `pager unmute`, so 42. Plus one (Sun
    2030-12-08, SM-498 build-002, merged up at its land-prep): `pager hook
    start`, the SessionStart hook that births the session's box, so 43. Plus
    one (Sun 2030-12-08, SM-748 do-002, openspec/changes/message-receipt D3):
    the HIDDEN verb `message receipt`, what another session recorded about
    one message - walked and fold-checked here, on no group page and no MCP
    tool, so `mcp` keeps line 20 of `message --help`; so 44 (SM-748
    merge-up-001 took both lines when SM-498 landed first). Plus two (Sun
    2030-12-08, SM-790 do-001, its design as passed): `message box archive`
    and `message box unarchive`, dead session boxes out of notmuch's scan and
    back - on the `box` page, never MCP tools (`_mcp.NOT_A_TOOL`), so 46.
    Plus one (Sun 2030-12-08, SM-721 fix-001, ruling (i)): `pager settings install`, the pre-launch verb a launcher runs
    before its birth writer - under the HIDDEN noun `pager settings`, so on
    no group page and no MCP tool; so 47 (SM-721 deliver-001 took both when
    SM-790 landed first). Plus one (Sun 2030-06-09, SM-186 do-003,
    changes/memory-init; merged up at SM-186 do-011, do-013 and do-014):
    `memory init` - the fifth memory verb, RATIFIED, so 48. Plus one (Sun 2030-12-08, SM-660
    fix-003, design-001 C.1): the HIDDEN verb `message setup`, its name
    PROVISIONAL (`_setup.VERB`) - on no group page and no MCP tool, so 49. Plus
    one (Sun 2030-12-08, SM-983 do-001, its design as passed): `pager settings
    hooks [on|off]`, the one verb that writes the host's `"hooks"` - under the
    HIDDEN noun `pager settings`, so on no group page and no MCP tool; so 50."""
    groups = [label for label, argv, _ in PAGES if not argv]
    verbs = [label for label, argv, _ in PAGES if argv]
    assert len(groups) == 3, groups
    assert len(verbs) == 50, verbs
    assert "pager settings hooks" in verbs, verbs
    assert "message setup" in verbs, verbs
    assert "pager settings install" in verbs, verbs
    assert "memory init" in verbs, verbs
    assert "message receipt" in verbs, verbs
    assert "pager hook start" in verbs, verbs
    assert {"pager mute", "pager unmute"} <= set(verbs), verbs
    assert {"message seat", "message seat init", "message seat agent"} <= set(verbs), verbs
    assert {"pager rule", "pager rule list", "pager rule add", "pager rule remove",
            "pager why", "pager focus"} <= set(verbs), verbs
    assert "pager subscriptions" not in verbs, verbs
    assert {"message box", "message box list", "message box path",
            "message box archive", "message box unarchive"} <= set(verbs), verbs
    assert {"message subscription", "message subscription add",
            "message subscription remove", "message subscription list"} <= set(verbs), verbs


@pytest.mark.parametrize("label", COMMANDS)
def test_a_command_answers_dash_h_as_well_as_dash_dash_help(label):
    """click omits `-h` unless told (slipbox `help-in-python`)."""
    runner = CliRunner()
    short = runner.invoke(group(label), ["-h"], prog_name=label)
    long = runner.invoke(group(label), ["--help"], prog_name=label)
    assert short.exit_code == 0
    assert short.output == long.output


# ── budget ───────────────────────────────────────────────────────────────

def check_fold(label: str, text: str, doc: str | None) -> None:
    """The gate itself, callable: a page is held to the fold unless its
    source signs for the overrun. Separate from the parametrized test so
    the signed branch can be watched working on a specimen - none of the
    real pages is signed, and a branch no test enters is a decoration."""
    signed = fold_override(doc)
    if signed:
        assert "fold-override" not in text, (
            f"{label}: the override is rendering. It belongs below the form "
            "feed, where click drops it - the reader should never see it."
        )
        return
    lines = len(text.splitlines())
    assert lines <= FOLD_LINES, (
        f"{label} --help is {lines} lines, over the fold of {FOLD_LINES}. "
        "Cut what the reader does not need, move the rest below the fold, "
        "or sign for it: `fold-override: <date> <name> - <reason>` after a "
        "\\f in the docstring. Concept: slipbox/above-the-fold.md."
    )
    tokens, how = count_tokens(text)
    if tokens is None:
        # No count is not a count: skip the token cap, naming the cause, and
        # never estimate (SM-799). The line cap above held on every host.
        pytest.skip(f"{label}: the {FOLD_TOKENS}-token cap was not measured "
                    f"({how}); the {FOLD_LINES}-line cap holds. SM-799.")
    assert tokens <= FOLD_TOKENS, (
        f"{label} --help is {tokens} tokens by {how}, over the fold of "
        f"{FOLD_TOKENS} ({tokens / max(lines, 1):.0f} tokens per line; ~12 "
        "means written for scanning). Cut what the reader does not need, "
        "move the rest below the fold, or sign for it: `fold-override: "
        "<date> <name> - <reason>` after a \\f in the docstring. Concept: "
        "slipbox/above-the-fold.md."
    )


@pytest.mark.parametrize("label,text", [(lbl, t) for lbl, _, t in PAGES])
def test_a_page_puts_what_is_needed_above_the_fold(label, text):
    """The fold, not a cutoff: `slipbox/above-the-fold.md`. Everything the
    reader needs - and nothing they do not - inside 40 lines and 400
    tokens; examples and detail may follow below. No test can judge what is
    NEEDED, so a page that overruns is red until someone signs for it:
    `fold-override: <date> <name> - <reason>` in the source, below the form
    feed. (`openspec/changes/above-the-fold`.)"""
    check_fold(label, text, DOCS.get(label))


# ── the nouns: the critical lines in the first 20 (message-subscriptions 3.4) ─
#
# Spine 10.15: agents run `--help | head -20`, so the
# lines that matter must be inside the first 20, not merely inside the fold.
# What is critical on each noun page is named here, page by page: the usage,
# every verb, the default and who may, and the exit codes.

HEAD = 20

#: label -> what `<label> --help | head -20` must show.
ABOVE_HEAD = {
    "message box": ["Usage: message box list|path", "Exit:"],
    "message box list": ["Usage: message box list", "--all", "Exit:"],
    "message box path": ["Usage: message box path", "BOX defaults to your seat", "Exit:"],
    "message subscription": ["Usage: message subscription", "your SESSION (the default",
                             "a privileged seat", "Exit:"],
    "message subscription add": ["Usage: message subscription add BOX", "your session",
                                 "--seat", "Ask one by mail", "Exit:"],
    "message subscription remove": ["Usage: message subscription remove BOX", "--seat",
                                    "Exit:"],
    "message subscription list": ["Usage: message subscription list", "seat is the first branch",
                                  "loop", "--depth", "LISTENER", "Exit:"],
    # SM-715 do-002: the hidden noun's own page. Not its two verbs: the exit-line
    # gate below runs each page's command IN-PROCESS with a flag it lacks, and
    # `seat agent` hands that flag to an exec (the test run would become bash),
    # `seat init` to the minter. Their pages are fold-checked by the walk, and
    # tests/test_message_seat.py reads them from a child.
    "message seat": ["Usage: message seat init|agent", "launchctl", "neither blesses", "Exit:"],
    # SM-748 do-002 (openspec/changes/message-receipt, "The page says it"): the
    # hidden verb's usage, its RECORDED rule, the one line on seat files the
    # verb never reads (ruling H), and its exits. Never shortened
    # to let a page pass: cut the page.
    "message receipt": ["Usage: message receipt ID --session SID", "RECORDED",
                        "seats/*/marks", "Exit:"],
    # SM-660 fix-003 (design-001 C): the hidden setup verb's usage, its
    # print-first rule, the dry run, and its exits.
    "message setup": ["Usage: message setup [--box NAME]... [--dry-run]", "WHOLE plan first",
                      "--dry-run", "Exit:"],
    # pager-filters 5.4 (SM-474 do-009): every verb pager-filters added.
    # The usage, who may (the gate and the route), the default, the exits.
    "pager rule": ["Usage: pager rule list|add|remove", "Your SESSION's rules",
                   "a privileged seat's", "ask one by mail", "next arrival", "Exit:"],
    "pager rule list": ["Usage: pager rule list", "Your session's rules by default", "--all",
                        "Exit:"],
    "pager rule add": ["Usage: pager rule add RULE", "--seat", "--locked",
                       "for a privileged seat only", "asks one", "Exit:"],
    "pager rule remove": ["Usage: pager rule remove RULE", "--seat", "--locked", "Exit:"],
    "pager why": ["Usage: pager why ID", "never re-judges", "Exit:"],
    "pager focus": ["Usage: pager focus sleep|off", "--until TIME", "--for SESSION", "--fleet",
                    "Your session by default", "wake!", "does not end", "asks one by mail",
                    "never wakes", "Exit:"],
}

#: label -> the verbs whose Commands ROW must be in the first 20. A row is
#: the name, then two or more spaces: a bare substring ("add ", "list ") is
#: also met by the usage line and the prose ("add and remove rebuild"), so
#: it stayed green with every row deleted (do-008 review).
VERB_ROWS = {
    "message box": ["list", "path"],
    "message subscription": ["add", "remove", "list"],
    "message seat": ["init", "agent"],
    "pager rule": ["list", "add", "remove"],
}

def as_an_agent_reads_it(label: str) -> str:
    """LABEL's page as `<label> --help | head -20` gets it: the command run
    for real, its output a pipe, no $COLUMNS. CliRunner renders 80 wide;
    a pipe renders `max_content_width` less click's 2, so 76 - and at 76
    rows that fit under CliRunner wrap (measured on `message --help`)."""
    import os
    import sys
    name, *argv = label.split()
    env = {k: v for k, v in os.environ.items() if k != "COLUMNS"}
    done = subprocess.run([sys.executable, "-P", "-m", f"sessionmail.{name}", *argv, "--help"],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture(scope="module")
def agent_pages() -> dict[str, str]:
    return {label: as_an_agent_reads_it(label) for label in [*ABOVE_HEAD, *COMMAND_PAGES]}


NOUN_PAGES = {label: text for label, _, text in PAGES if label in ABOVE_HEAD}

#: The command pages whose Commands rows must all be in the first 20.
COMMAND_PAGES = ("message", "pager")


def test_every_noun_page_is_walked():
    assert set(NOUN_PAGES) == set(ABOVE_HEAD)


@pytest.mark.parametrize("label", list(ABOVE_HEAD))
def test_a_noun_page_puts_its_critical_lines_in_the_first_20(label, agent_pages):
    head = "\n".join(agent_pages[label].splitlines()[:HEAD])
    missing = [want for want in ABOVE_HEAD[label] if want not in head]
    missing += [f"row {verb}" for verb in VERB_ROWS.get(label, [])
                if not re.search(rf"^  {re.escape(verb)} {{2,}}\S", head, re.M)]
    assert not missing, f"`{label} --help | head -{HEAD}` lacks {missing}:\n{head}"


@pytest.mark.parametrize("name", COMMAND_PAGES)
def test_the_command_page_names_every_verb_in_the_first_20(agent_pages, name):
    """The nouns are found from `message --help`: a verb listed below line
    20 is a verb an agent never learns exists. The noun `subscription` is
    12 characters, and when it set the name column every row wrapped, which
    pushed `box` and `subscription` to lines 22-25 (measured at 7d2d162).
    `pager --help` too (pager-filters 5.4): `rule`, `why` and `focus` are
    found there, or not at all."""
    head = agent_pages[name].splitlines()[:HEAD]
    rows = {line.split()[0] for line in head if line.startswith("  ") and line.strip()}
    verbs = [v for v in group(name).list_commands(None)
             if not group(name).commands[v].hidden]     # a hidden verb is on no page
    assert set(verbs) <= rows, f"not in the first {HEAD}: {set(verbs) - rows}\n" + "\n".join(head)


def test_no_message_verb_row_wraps(agent_pages):
    """A name of 4 or fewer takes one line; a longer one takes two, its name
    and then its text. Never a third: master's `verify`, hand-padded for
    master's wider layout, wrapped to three and pushed `mcp` to line 22
    (SM-374 do-015, measured on the merged tree)."""
    lines = agent_pages["message"].splitlines()
    start = lines.index("Commands:") + 1
    block = lines[start:start + next(i for i, line in enumerate(lines[start:]) if not line.strip())]
    verbs = [v for v in group("message").list_commands(None)
             if not group("message").commands[v].hidden]     # a hidden verb has no row
    want = sum(1 if len(v) <= 4 else 2 for v in verbs)
    assert len(block) == want, "a verb row wraps:\n" + "\n".join(block)


@pytest.mark.parametrize("label", list(ABOVE_HEAD))
def test_a_noun_page_says_the_usage_exit_the_code_gives(label):
    """Say what the code does (do-007's F1): click exits 2 on bad usage,
    and every noun page said `1 usage`. Measured by running the page's own
    command with a flag it does not have."""
    name, *argv = label.split()
    done = CliRunner().invoke(group(name), [*argv, "--no-such-flag"], prog_name=name)
    exit_line = next(line for line in NOUN_PAGES[label].splitlines() if "Exit:" in line)
    assert done.exit_code == 2, done.output
    assert f"{done.exit_code} usage" in exit_line, f"{label}: {exit_line.strip()}"
    assert "1 usage" not in exit_line, f"{label}: {exit_line.strip()}"


def test_the_gate_honours_a_signature_and_refuses_an_unsigned_overrun():
    """Both branches of the gate, watched on one specimen."""
    over = "a page that overruns " * 120
    signed = "help\n\f\nfold-override: 2030-06-09 Ivan 7 - what matters is above"
    check_fold("demo", over, signed)          # signed: allowed through
    with pytest.raises(AssertionError) as caught:
        check_fold("demo", over, None)        # unsigned: refused, and it says why
    said = str(caught.value)
    assert "above-the-fold" in said and "fold-override" in said


def test_a_signature_is_a_date_a_name_and_a_reason_or_it_is_not_one():
    """An unsigned mute is not a judgement (`changes/above-the-fold`). Each
    of these is missing exactly one of the three, and each is refused."""
    good = "\f\nfold-override: 2030-06-09 Ivan 7 - the T rules are above the fold"
    assert fold_override(good) is not None
    assert fold_override("\f\nfold-override: Ivan 7 - no date here") is None
    assert fold_override("\f\nfold-override: 2030-06-09 - no signer") is None
    assert fold_override("\f\nfold-override: 2030-06-09 Ivan 7") is None
    assert fold_override(None) is None


def test_an_override_above_the_form_feed_does_not_count():
    """It would render, and an override the reader pays for is what the rule
    forbids: in the source, never in the rendered page."""
    doc = "Help they read.\n\nfold-override: 2030-06-09 Ivan 7 - visible, so void"
    assert fold_override(doc) is None


def test_a_signed_page_passes_the_fold_and_the_reader_never_sees_why():
    """The whole mechanism end to end on a real click command: a page far
    over the fold, signed below the form feed, passes - and nothing of the
    signature reaches the rendered page."""
    import click

    doc = (
        "A page that overruns on purpose.\n\n"
        + "Filler prose that a reader does not need. " * 90
        + "\n\f\nfold-override: 2030-06-09 Ivan 7 - usage and options are "
          "above the fold; the filler is detail."
    )

    @click.command("verbose")
    def verbose():
        pass

    verbose.callback.__doc__ = doc
    verbose.help = doc

    text = CliRunner().invoke(verbose, ["--help"], prog_name="verbose").output
    tokens, how = count_tokens(text)
    if tokens is None:
        pytest.skip(f"cannot show the specimen overruns: {how} (SM-799)")
    assert tokens > FOLD_TOKENS, "the specimen must actually overrun"
    assert fold_override(verbose.callback.__doc__) is not None
    assert "fold-override" not in text


def test_the_token_counter_can_fail():
    """Charter 2: prove the instrument can say no before trusting its yes."""
    tokens, how = count_tokens("word " * 2000)
    if tokens is None:
        pytest.skip(f"no counter to prove: {how} (SM-799)")
    assert tokens > TOKEN_BUDGET


# ── the counter: a count, or None and why; never an estimate (SM-799) ────
#
# Each case drives count_tokens with a stub `skill` on PATH. The page is a
# 1476-character specimen whose real count (the stub's) is 384: len/3 would
# say 492, over the fold, which is the overshoot that failed pages that fit
# (the card's [memory run] sighting).

PAGE = ("A help line a reader scans, kept short and plain for the agent.\n"
        * 24)[:1476]


def _stub_skill(tmp_path: Path, monkeypatch, body: str) -> Path:
    """A `skill` running `body` under /bin/sh, first on PATH, so it shadows
    the real counter. The rest of PATH stays, for the stub's `sleep`."""
    exe = tmp_path / "stub" / "skill"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text(f"#!/bin/sh\ncat >/dev/null\n{body}\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")
    return exe


def _no_skill(tmp_path: Path, monkeypatch) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()[:1] != "Z"


def test_the_specimen_page_is_the_shape_that_flaked():
    """The fixture is what the card saw: inside 40 lines, and len/3 over the
    fold although the real count is under it."""
    assert len(PAGE) == 1476 and len(PAGE.splitlines()) <= FOLD_LINES
    assert round(len(PAGE) / 3.0) == 492 > FOLD_TOKENS


def test_the_counter_returns_the_count_skill_tokens_prints(tmp_path, monkeypatch):
    _stub_skill(tmp_path, monkeypatch, "echo 'tokens: 384'")
    assert count_tokens(PAGE) == (384, "skill tokens")


def test_the_counter_reads_the_tokens_line_not_every_digit(tmp_path, monkeypatch):
    _stub_skill(tmp_path, monkeypatch, "echo 'v3 2030'; echo 'tokens: 384'")
    assert count_tokens(PAGE) == (384, "skill tokens")


def test_no_skill_on_path_is_no_count_and_says_so(tmp_path, monkeypatch):
    _no_skill(tmp_path, monkeypatch)
    tokens, why = count_tokens(PAGE)
    assert tokens is None, (tokens, why)
    assert "not on PATH" in why, why


@pytest.mark.parametrize("body,said", [
    ("exit 0", "printed no count"),                            # silent
    ("echo 'tokens: 12'; exit 3", "exited 3"),                 # non-zero
    ("echo 'no count here'", "no `tokens: N` line"),            # garbage
    ("echo 'v3 2030'", "no `tokens: N` line"),                  # garbage, digits
    ("echo 'tokens:'", "no `tokens: N` line"),                  # garbage, no N
], ids=["silent", "non-zero", "garbage", "garbage-digits", "garbage-no-n"])
def test_a_counter_that_gives_no_count_is_none_and_says_why(tmp_path, monkeypatch,
                                                            body, said):
    _stub_skill(tmp_path, monkeypatch, body)
    tokens, why = count_tokens(PAGE)
    assert tokens is None, (tokens, why)
    assert said in why, why


def test_a_timeout_is_bounded_kills_the_group_and_is_no_count(tmp_path, monkeypatch):
    """SM-773 kept: a `skill tokens` that never returns, and forks a
    grandchild as `uv run` does, costs the bound and no more, and its whole
    process group dies. SM-799: what it returns is None and why, not len/3."""
    module = sys.modules[count_tokens.__module__]
    monkeypatch.setattr(module, "TOKENS_TIMEOUT", 1.0)
    pidfile = tmp_path / "grandchild.pid"
    _stub_skill(tmp_path, monkeypatch,
                f"sleep 100000 &\necho $! > {pidfile}\nexec sleep 100000")
    grandchild = None
    try:
        start = time.monotonic()
        tokens, why = count_tokens(PAGE)
        elapsed = time.monotonic() - start
        assert elapsed < 10, f"count_tokens took {elapsed:.1f}s against a 1s bound"
        assert tokens is None, (tokens, why)
        assert "timed out after 1s" in why, why
        grandchild = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(grandchild), f"grandchild {grandchild} survived the timeout"
    finally:
        if grandchild and _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)  # by pid: ours alone


def test_one_timeout_does_not_change_the_next_pages_count(tmp_path, monkeypatch):
    """The latch is gone: the bug was one slow call poisoning every later
    page in the run. After a timeout, a healthy counter is asked again."""
    module = sys.modules[count_tokens.__module__]
    monkeypatch.setattr(module, "TOKENS_TIMEOUT", 1.0)
    _stub_skill(tmp_path, monkeypatch, "exec sleep 100000")
    count_tokens(PAGE)  # times out; what it returns is the test above's
    _stub_skill(tmp_path, monkeypatch, "echo 'tokens: 384'")
    assert count_tokens(PAGE) == (384, "skill tokens")


def test_the_fold_skips_the_token_cap_with_the_reason_when_there_is_no_count(
        tmp_path, monkeypatch):
    _no_skill(tmp_path, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as caught:
        check_fold("demo", PAGE, None)
    said = str(caught.value)
    assert "demo" in said and "not on PATH" in said, said


def test_the_fold_still_fails_401_real_tokens_and_passes_400(tmp_path, monkeypatch):
    _stub_skill(tmp_path, monkeypatch, f"echo 'tokens: {FOLD_TOKENS + 1}'")
    with pytest.raises(AssertionError) as caught:
        check_fold("demo", PAGE, None)
    assert f"{FOLD_TOKENS + 1} tokens by skill tokens" in str(caught.value)
    _stub_skill(tmp_path, monkeypatch, f"echo 'tokens: {FOLD_TOKENS}'")
    check_fold("demo", PAGE, None)


def test_the_line_cap_holds_on_a_host_with_no_counter(tmp_path, monkeypatch):
    _no_skill(tmp_path, monkeypatch)
    with pytest.raises(AssertionError, match="over the fold of 40"):
        check_fold("demo", "x\n" * (FOLD_LINES + 1), None)


# ── the OpenSpec link-back ───────────────────────────────────────────────

@pytest.mark.parametrize("label", sorted(INVOCATIONS))
def test_a_stub_names_a_requirement_that_really_exists(label):
    """A stub that cites a requirement must cite a real one. A verb that has
    been BUILT is not a stub and is not checked here, so filling one in
    never breaks this test."""
    name = label.split()[0]
    result = CliRunner().invoke(group(name), INVOCATIONS[label], prog_name=name)
    if result.exit_code != EX_UNIMPLEMENTED:
        return  # built, or refused for its own reasons; not this gate's business
    text = result.stderr
    match = STUB_SPEC.search(text)
    assert match, f"{label} exits {EX_UNIMPLEMENTED} without naming a spec:\n{text}"
    path, capability = match.group("path"), match.group("capability")
    requirement = match.group("requirement").strip()
    # openspec/ is export-ignored: in the release export this skips (SM-849).
    assert requirement in requirement_titles(capability, export_ignored(path)), (
        f"{label} cites '{requirement}' in {path}, "
        f"which has no such requirement"
    )


#: What a stub prints: the ratified spec, or - for a verb whose requirement is
#: still a PROPOSED change (`memory init`, SM-186) - that change's delta.
STUB_SPEC = re.compile(
    r"spec:\s+(?P<path>openspec/(?:specs|changes/[^/\s]+/specs)/"
    r"(?P<capability>[^/\s]+)/spec\.md)\s+requirement:\s+(?P<requirement>.+)"
)


def test_the_link_reads_a_ratified_spec_and_a_proposed_change():
    """Both spellings a stub may print parse, and nothing else does: the
    widened pattern must not start accepting a path outside openspec/."""
    ratified = "spec:        openspec/specs/memory/spec.md\n  requirement: R"
    proposed = ("spec:        openspec/changes/memory-init/specs/memory/spec.md\n"
                "  requirement: R")
    assert STUB_SPEC.search(ratified).group("capability") == "memory"
    m = STUB_SPEC.search(proposed)
    assert (m.group("path"), m.group("capability")) == (
        "openspec/changes/memory-init/specs/memory/spec.md", "memory")
    assert STUB_SPEC.search("spec:        docs/memory/spec.md\n  requirement: R") is None


# ── the table is RUN, so it must not name anyone ─────────────────────────

#: Flags of `message send` that take a value, so the value is not a recipient.
SEND_FLAGS_WITH_VALUES = ("-s", "--subject", "-f", "--body-file", "--in-reply-to",
                          "--supersedes", "--cc", "--bcc", "--header")


def positionals(argv: list[str]) -> list[str]:
    """ARGV's positional words - the flags, and the values they eat, removed."""
    out, skip = [], False
    for word in argv:
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            skip = word in SEND_FLAGS_WITH_VALUES
            continue
        out.append(word)
    return out


def test_the_recipient_is_reserved_not_merely_wrong():
    """`message send` in the table is not a string that gets read - it is a
    call that gets MADE, by this gate and by `make progress`, through the
    real MTA. A recipient anyone can receive on is mail that arrives.

    It arrived: `send judy -s subject --no-body` put one bare
    `Subject: subject` into that box per run - 189 of them. Fixed by naming
    a box no map resolves; this is the guard that keeps it fixed.

    The property asserted is RESERVED, not merely unresolvable: RFC 2606
    sets `.invalid` aside so that it can never be delegated to anyone. A
    name that is merely absent from today's postfix map is one
    `vmailbox-gen` away from being someone's real box again."""
    recipients = positionals(INVOCATIONS["message send"])[1:]   # [0] is the verb
    assert recipients, "the send invocation names no recipient at all"
    for who in recipients:
        assert who.endswith(RESERVED_TLD), (
            f"the scaffold's `message send` would go to {who!r}, which is not in "
            f"the reserved {RESERVED_TLD} TLD - this table is RUN, so that is real "
            f"mail to a real person on every gate run and every `make progress`"
        )


def test_message_send_still_has_a_row_to_be_reserved():
    """The other way to stop the scaffold mailing anyone is to delete the row
    - which stops the gate and the burndown exercising `message send` at all,
    silently. That is a green check bought by making its claim untrue, and it
    is the shortcut with a live motive, so the row's existence is asserted
    separately from what is in it.

    Deliberately NOT generalised to every verb: `message path`, `pager off`,
    `pager stop` and `pager hook` have no row today, and three of those four
    change state when run - giving them one would put the same hazard this
    slice just closed back into a table that is RUN. Reported rather than
    fixed here."""
    assert "message send" in INVOCATIONS
    assert positionals(INVOCATIONS["message send"])[0] == "send"


# ── the group page ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", COMMANDS)
def test_a_group_page_lists_every_verb(name):
    text = next(t for lbl, argv, t in PAGES if lbl == name and not argv)
    for verb in group(name).list_commands(None):
        if group(name).commands[verb].hidden:
            assert f"\n  {verb} " not in text, f"{name} --help lists hidden {verb}"
            continue
        assert verb in text, f"{name} --help does not list {verb}"


#: RATIFIED but only two-thirds true. `openspec/specs/help/spec.md` says a
#: verb's flags "SHALL live on the verb's own page and nowhere else", and its
#: scenario says "no flag of any verb appears on it". pager's authored group
#: page obeys. message's and memory's do NOT: they carry a usage sketch per
#: verb (`send TO... -s SUBJECT (-f FILE|--no-body)`), and they read better
#: for it. So the gate is REAL for pager - it must not regress - and marked
#: as the known conflict for the other two until it is ruled which side moves.
FLAGS_ON_GROUP_PAGE = (
    "UNRESOLVED, needs a ruling: the authored group page carries a usage sketch "
    "per verb, which help/spec.md's 'nowhere else' forbids. Either the "
    "requirement means 'no flag TABLE', or the page wants cutting."
)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("message", marks=pytest.mark.xfail(reason=FLAGS_ON_GROUP_PAGE, strict=True)),
        pytest.param("memory", marks=pytest.mark.xfail(reason=FLAGS_ON_GROUP_PAGE, strict=True)),
        "pager",
    ],
)
def test_a_group_page_carries_no_verb_flags(name):
    text = next(t for lbl, argv, t in PAGES if lbl == name and not argv)
    cli = group(name)
    for verb in cli.list_commands(None):
        for param in cli.commands[verb].params:
            for opt in getattr(param, "opts", []):
                if opt.startswith("--") and opt not in ("--help", "--version"):
                    assert opt not in text, (
                        f"{name} --help mentions {verb}'s flag {opt}"
                    )
