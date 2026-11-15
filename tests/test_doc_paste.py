"""Every fenced shell block in the docs pastes into a Mac's zsh as written.

In zsh, the Mac's own shell, `interactivecomments` is off at the prompt, so a
pasted `#` is a WORD, not a comment:

- `session list   # your sessions` runs `session list '#' your sessions`;
- an apostrophe in a comment (`# one session's window`) opens a quote that
  eats the lines pasted after it;
- `(in ...)` in a comment is a glob, and zsh stops on it.

So a command's explanation goes in the prose, never after the command. A `#`
line belongs only in a block the reader saves and runs as a script: one that
starts with a `#!` line. And every quote a block opens, it closes.

A value the reader types is an UPPERCASE word (`message send TO`), the word the
command's own help uses, never a `<word>`. Unquoted, `<box>` is a redirection
in zsh and in bash: the paste stops with "no such file or directory: box", or
a parse error, and the line does not run.

The guard reads each block as zsh does at the prompt, with no comments. It
covers every Markdown file in the repo (git's list when there is one, else a
walk of the tree, as in a release archive), except CHANGELOG.md, whose landed
bullets are never rewritten, the test fixtures, and the specs under
`openspec/`, which are design records, not pages a reader pastes from.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SHELL_FENCES = {"bash", "sh", "shell", "zsh", "console"}
SKIPPED = {"CHANGELOG.md"}
SKIPPED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
}
FIXTURES = Path("tests") / "fixtures"
# Top-level folders whose Markdown is not a page a reader pastes from.
SKIPPED_TREES = {"openspec"}

FENCE = re.compile(r"^(?P<indent> *)(?P<tick>`{3,}|~{3,})\s*(?P<info>[^\s`]*)")
# A `<word>` placeholder: `<` then a letter, up to a `>` with no space before
# it. Not `<<`, `<(`, `<&`, and not `<in >out`, two real redirections.
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>]*(?<!\s)>")
# `<<WORD`, `<<-WORD`, `<<'WORD'`, `<<"WORD"`; not `<<<`, a here-string.
HEREDOC = re.compile(
    r"(?<!<)<<(?!<)(?P<dash>-?)\s*(?P<q>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=q)"
)


@dataclass(frozen=True)
class Block:
    path: str
    line: int  # 1-based line of the block's first content line
    info: str
    lines: tuple[str, ...]


def _doc_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.md"],
            capture_output=True,
            check=True,
        ).stdout.decode()
        names = [n for n in out.split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = []
    if not names:
        names = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*.md")
            if not SKIPPED_DIRS.intersection(p.relative_to(ROOT).parts)
        ]
    return sorted(
        n
        for n in names
        if Path(n).name not in SKIPPED
        and FIXTURES not in Path(n).parents
        and Path(n).parts[0] not in SKIPPED_TREES
        and (ROOT / n).is_file()
    )


def shell_blocks(text: str, path: str = "<text>") -> list[Block]:
    """Each fenced block whose info string names a shell, de-indented."""
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, tick, info = len(m["indent"]), m["tick"], m["info"].lower()
        j = i + 1
        while j < len(lines) and not lines[j].lstrip().startswith(tick):
            j += 1
        if info in SHELL_FENCES:
            body = tuple(
                ln[indent:] if ln[:indent].strip() == "" else ln
                for ln in lines[i + 1 : j]
            )
            blocks.append(Block(path, i + 2, info, body))
        i = j + 1
    return blocks


def paste_problems(block: Block) -> list[str]:
    """What breaks when this block is pasted at a zsh prompt.

    A `#` that starts a word is a problem anywhere in a pasted block: after a
    command it is a trailing comment zsh runs as words; on its own line it is
    a command named `#`. Only a script block, one whose first line is `#!`,
    may carry `#` lines. A quote still open at the end of the block is the
    other problem, and an unquoted `<word>` placeholder, a redirection, the
    third. Heredoc bodies are text, so they are not read for any of them.
    """
    script = bool(block.lines) and block.lines[0].startswith("#!")
    problems: list[str] = []
    # The open quoting contexts: "'" '"' "$'" "`" and "$(" (with its paren depth).
    stack: list[list] = []
    heredocs: list[tuple[str, bool]] = []
    in_heredoc: tuple[str, bool] | None = None
    opened_at = 0
    for n, line in enumerate(block.lines):
        where = f"{block.path}:{block.line + n}"
        if in_heredoc:
            word, dash = in_heredoc
            if (line.lstrip("\t") if dash else line) == word:
                in_heredoc = heredocs.pop(0) if heredocs else None
            continue
        if not stack:
            heredocs = [(m["word"], bool(m["dash"])) for m in HEREDOC.finditer(line)]
        words_before = hashed = False
        k = 0
        while k < len(line):
            c = line[k]
            top = stack[-1][0] if stack else None
            if top == "'":
                if c == "'":
                    stack.pop()
            elif top == "$'":
                if c == "\\":
                    k += 1
                elif c == "'":
                    stack.pop()
            elif top in ('"', "`"):
                if c == "\\":
                    k += 1
                elif c == top:
                    stack.pop()
                elif c == "$" and line[k + 1 : k + 2] == "(":
                    stack.append(["$(", 1])
                    k += 1
            else:
                # Unquoted: the top level, or inside a $( ... ).
                if c == "\\":
                    k += 1
                elif c == "'":
                    if not stack:
                        opened_at = n
                    stack.append(["'" if line[k - 1 : k] != "$" else "$'", 0])
                elif c in ('"', "`"):
                    if not stack:
                        opened_at = n
                    stack.append([c, 0])
                elif c == "$" and line[k + 1 : k + 2] == "(":
                    if not stack:
                        opened_at = n
                    stack.append(["$(", 1])
                    k += 1
                elif top == "$(" and c == "(":
                    stack[-1][1] += 1
                elif top == "$(" and c == ")":
                    stack[-1][1] -= 1
                    if stack[-1][1] == 0:
                        stack.pop()
                elif (
                    c == "<"
                    and line[k - 1 : k] != "<"
                    and (ph := PLACEHOLDER.match(line, k))
                ):
                    problems.append(
                        f"{where}: a <word> placeholder, a redirection: {ph.group()}"
                    )
                    words_before = True
                    k = ph.end() - 1
                elif c == "#" and (k == 0 or line[k - 1] in " \t;&|") and not hashed:
                    # zsh reads on past it, so the rest of the line still counts.
                    hashed = True
                    if words_before:
                        problems.append(
                            f"{where}: a trailing # comment: {line.strip()}"
                        )
                    elif not script:
                        problems.append(
                            f"{where}: a # line in a block that is pasted: {line.strip()}"
                        )
                elif not c.isspace():
                    words_before = True
            k += 1
        if not stack and heredocs:
            in_heredoc = heredocs.pop(0)
    if stack:
        problems.append(
            f"{block.path}:{block.line + opened_at}: a quote opened here is still open"
            f" at the end of the block ({stack[0][0]})"
        )
    return problems


DOCS = _doc_files()


def _all_blocks() -> list[Block]:
    return [
        b
        for name in DOCS
        for b in shell_blocks((ROOT / name).read_text(encoding="utf-8"), name)
    ]


def test_the_guard_reads_the_public_docs():
    names = set(DOCS)
    assert {"README.md", "AGENTS.md", "docs/message-setup.md", "docs/pull.md"} <= names
    assert "CHANGELOG.md" not in names
    assert not any(n.startswith("tests/fixtures/") for n in names)
    assert not any(n.startswith("openspec/") for n in names)
    blocks = _all_blocks()
    assert len(blocks) > 50, len(blocks)
    assert {b.path for b in blocks} >= {
        "README.md",
        "AGENTS.md",
        "docs/message-setup.md",
        "docs/postfix-configuration.md",
        "docs/pull.md",
    }


def test_no_pasted_shell_line_carries_a_comment_an_open_quote_or_a_placeholder():
    problems = [p for b in _all_blocks() for p in paste_problems(b)]
    assert problems == [], "\n".join(problems)


# The canaries: the guard bites on each way a paste breaks, and on the real
# docs, and lets through what zsh reads as written.
def _one(text: str) -> Block:
    [block] = shell_blocks(f"```bash\n{text}\n```\n")
    return block


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("session list   # your live sessions", "a trailing # comment"),
        ("make check;# lint, then test", "a trailing # comment"),
        ('rm -rf "$x"   # (in the cache)', "a trailing # comment"),
        ("# one session's window\nsession list", "a # line in a block that is pasted"),
        ("session context x   # one session's window\nsession list", "still open"),
        ('message send box -s "PR ready -f note.md', "still open"),
        ("echo 'it is\nsession list", "still open"),
        ('echo "$(session list "x)"', "still open"),
        ('message send <box> -s "PR ready" -f note.md', "a <word> placeholder"),
        ("cd <the directory you will work from>", "a <word> placeholder"),
        ("sessionkit setup --address <name@host>", "a <word> placeholder"),
        ('echo "$(session context <seat>)"', "a <word> placeholder"),
    ],
)
def test_the_canary_bites(text, says):
    problems = paste_problems(_one(text))
    assert any(says in p for p in problems), problems


@pytest.mark.parametrize(
    "text",
    [
        'echo "${#PATH}" $# \'a # b\' "c # d"',
        "git clone https://example.invalid/x#frag",
        "echo $'it\\'s'",
        'f="$(printf "%s" "$(date)")" && echo "$f"',
        "cat > note.md <<'EOF'\nan apostrophe's # fine in here\nEOF\necho done",
        "perl -pi -e '\n  s/\"a\"/b/\n' file",
        "#!/bin/sh\n# a script the reader saves and runs\necho ok",
        "message send TO -s 'PR ready' -f note.md",
        "sort < in.txt > out.txt",
        "sort <in >out",
        "diff <(ls a) <(ls b)",
        "exec 3<&0",
        "echo '<box>' \"<b>bold</b>\"",
        "cat <<EOF>note.md\n<box> is text in here\nEOF",
    ],
)
def test_the_canary_lets_through_what_zsh_reads_as_written(text):
    assert paste_problems(_one(text)) == []


def test_the_canary_bites_on_the_real_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\nuv tool uninstall sessionmail\n" in readme
    planted = readme.replace(
        "\nuv tool uninstall sessionmail\n",
        "\nuv tool uninstall sessionmail   # the commands and the program\n",
        1,
    )
    problems = [
        p for b in shell_blocks(planted, "README.md") for p in paste_problems(b)
    ]
    assert len(problems) == 1 and "README.md:" in problems[0], problems


def test_the_placeholder_canary_bites_on_the_real_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\nmessage send NAME -s" in readme
    planted = readme.replace("\nmessage send NAME -s", "\nmessage send <box> -s", 1)
    problems = [
        p for b in shell_blocks(planted, "README.md") for p in paste_problems(b)
    ]
    assert len(problems) == 1 and "<box>" in problems[0], problems


ZSH = shutil.which("zsh")


@pytest.mark.skipif(ZSH is None, reason="no zsh on this machine")
def test_zsh_at_the_prompt_reads_a_hash_as_a_word(tmp_path):
    # The premise, measured: an interactive zsh with no rc files.
    env = {
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path),
        "PATH": os.defpath,
        "TERM": "dumb",
    }
    done = subprocess.run(
        [ZSH, "-f", "-i"],
        input="PS1=''\necho x # only if\n",
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "x # only if" in done.stdout, (done.stdout, done.stderr)
