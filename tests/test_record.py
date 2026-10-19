"""`memory init --record FILE`, the record half (`_record`), SM-186 do-006.

Every test runs on a TEMPORARY git repo (`tmp_path`, `git init`) holding a
PROJECT.md-shaped file with a trailing comment on a neighbouring key: never
the real store, never a live mailbox. The repo's hooks path points at an empty
directory, so the machine's global hooks do not run here. The ref race is
SIMULATED by replacing `_record._git`; no test races real processes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import hostile_git
from sessionmail import _record
from sessionmail._init import InitError, InitResult

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

R1, R2 = "r1@example.invalid", "r2@example.invalid"
PROJECT = (
    "---\n"
    "name: alpha\n"
    "mail_grammar: scope   # a hand-written comment\n"
    "tags:\n"
    "  - one\n"
    "  - two\n"
    "---\n"
    "\n"
    "# alpha\n"
    "\n"
    "mail_root: \"not-front-matter@example.invalid\"\n"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "store"
    (r / "projects" / "alpha").mkdir(parents=True)
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    git(r, "init", "-q")
    for k, v in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", str(hooks))):
        git(r, "config", k, v)
    return r


def committed(repo: Path, rel: str, text: str) -> Path:
    p = repo / rel
    p.write_bytes(text.encode())
    git(repo, "add", "--", rel)
    git(repo, "commit", "-q", "-m", f"add {rel}", "--", rel)
    return p


@pytest.fixture
def project(repo) -> Path:
    return committed(repo, "projects/alpha/PROJECT.md", PROJECT)


def refused(fn, *args, **kwargs) -> InitError:
    with pytest.raises(InitError) as caught:
        fn(*args, **kwargs)
    return caught.value


def changed_lines(before: bytes, after: bytes) -> list[tuple[int, str, str]]:
    a, b = before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True)
    return [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]


MINTED = InitResult(root_id=R2, minted=("[ROOT]", "[ASKS]"), existing=(), recorded=None)


# ── the edit: one line, every other byte kept ────────────────────────────


def test_insert_adds_one_line_before_the_closing_fence_and_keeps_every_other_byte(repo, project):
    before = project.read_bytes()
    sha = _record.record(project, R1, box="alpha")
    after = project.read_bytes()
    a, b = before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True)
    assert len(b) == len(a) + 1
    assert b[6] == f'mail_root: "{R1}"\n'           # after the tags list, before the fence
    assert b[:6] + b[7:] == a                       # every other line byte-identical, in order
    assert "mail_grammar: scope   # a hand-written comment\n" in b
    assert sha and git(repo, "rev-parse", "HEAD").strip() == sha


def test_replace_changes_exactly_one_line_and_keeps_its_comment(repo):
    text = PROJECT.replace("tags:\n", f"mail_root: '<{R1}>'   # recorded by hand\ntags:\n")
    p = committed(repo, "projects/alpha/PROJECT.md", text)
    before = p.read_bytes()
    assert _record.record(p, f"<{R2}>")
    after = p.read_bytes()
    assert changed_lines(before, after) == [
        (3, f"mail_root: '<{R1}>'   # recorded by hand\n", f'mail_root: "{R2}"   # recorded by hand\n')]
    assert len(before.splitlines()) == len(after.splitlines())


def test_moving_the_pointer_from_r1_to_r2_is_the_same_edit(repo, project):
    """do-007's supersede needs exactly this: R1 recorded, then R2."""
    first = _record.record(project, R1)
    second = _record.record(project, R2)
    assert first != second
    assert _record.precheck(project).current == R2
    assert git(repo, "log", "--format=%s", "-2").split("\n")[:2] == [
        f"memory init: mail_root -> {R2}", f"memory init: mail_root -> {R1}"]


def test_a_body_line_that_looks_like_a_record_is_not_the_record(repo, project):
    """Only the front matter is read and written; the body's lookalike stays."""
    assert _record.precheck(project).current is None
    _record.record(project, R1)
    assert project.read_text().endswith('mail_root: "not-front-matter@example.invalid"\n')


def test_crlf_front_matter_stays_crlf(repo):
    p = committed(repo, "P.md", "---\r\nname: alpha\r\n---\r\nbody\r\n")
    _record.record(p, R1)
    assert p.read_bytes() == f'---\r\nname: alpha\r\nmail_root: "{R1}"\r\n---\r\nbody\r\n'.encode()


def test_empty_front_matter_gets_the_line(repo):
    p = committed(repo, "P.md", "---\n---\nbody\n")
    _record.record(p, R1)
    assert p.read_text() == f'---\nmail_root: "{R1}"\n---\nbody\n'


def test_mail_grammar_is_never_written(repo, project):
    """mail_root: only; mail_grammar is FROZEN and the wrapper's."""
    _record.record(project, R1)
    assert project.read_text().count("mail_grammar") == 1


# ── the no-op ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spelling", [f'"{R1}"', f"'<{R1}>'", R1])
def test_a_file_that_already_names_the_root_is_no_write_and_no_commit(repo, spelling):
    p = committed(repo, "P.md", f"---\nmail_root: {spelling}\n---\n")
    head, mtime, raw = git(repo, "rev-parse", "HEAD"), p.stat().st_mtime_ns, p.read_bytes()
    assert _record.record(p, R1) is None
    assert (git(repo, "rev-parse", "HEAD"), p.stat().st_mtime_ns, p.read_bytes()) == (head, mtime, raw)


# ── the commit: by path, exactly one path, the sha of THIS commit ─────────


def test_the_commit_holds_exactly_one_path_and_leaves_others_alone(repo, project):
    """Another seat's STAGED file must not be swept into init's commit."""
    other = committed(repo, "other.md", "one\n")
    other.write_text("two\n")
    git(repo, "add", "--", "other.md")
    sha = _record.record(project, R1, box="alpha")
    assert git(repo, "show", "--name-only", "--format=", sha).split() == ["projects/alpha/PROJECT.md"]
    assert git(repo, "show", "--format=%s", "-s", sha).strip() == f"memory init alpha: mail_root -> {R1}"
    assert git(repo, "diff", "--cached", "--name-only").split() == ["other.md"]   # still staged, not taken
    assert git(repo, "status", "--porcelain", "--", "projects").strip() == ""


def test_the_sha_is_read_from_the_commit_not_from_head_afterwards(repo, project, monkeypatch):
    """HEAD moves under you in a busy store: a seat commits right after us."""
    real = _record._git

    def busy(cwd, args):
        done = real(cwd, args)
        if args[0] == "commit":
            committed(repo, "late.md", "someone else\n")
        return done

    monkeypatch.setattr(_record, "_git", busy)
    sha = _record.record(project, R1)
    assert sha != git(repo, "rev-parse", "HEAD").strip()
    assert git(repo, "show", "--name-only", "--format=", sha).split() == ["projects/alpha/PROJECT.md"]


# ── refusals BEFORE a mint: exit 1, kind "record", FILE named, nothing written ──


def test_precheck_accepts_a_clean_tracked_file(repo, project):
    rec = _record.precheck(project)
    assert (rec.current, rec.at) == (None, None)


@pytest.mark.parametrize("text, words", [
    ("no front matter\n", "no YAML front matter"),
    ("", "no YAML front matter"),
    ("---\nname: alpha\nbody, never closed\n", "never closed"),
    (f"---\nmail_root: {R1}\nmail_root: {R2}\n---\n", "mail_root: 2 times"),
])
def test_an_unfit_front_matter_is_refused_as_usage(repo, text, words):
    p = committed(repo, "P.md", text) if text else (repo / "P.md")
    if not text:
        p.write_text("")
        git(repo, "add", "--", "P.md")
        git(repo, "commit", "-q", "-m", "empty", "--", "P.md")
    head = git(repo, "rev-parse", "HEAD")
    for call in (lambda: _record.precheck(p), lambda: _record.record(p, R1)):
        err = refused(call)
        assert str(p) in str(err) and words in str(err)
    assert refused(_record.precheck, p).code == 1
    assert refused(_record.precheck, p).kind == "record"
    assert git(repo, "rev-parse", "HEAD") == head and p.read_text() == text


def test_a_missing_file_is_usage(repo):
    err = refused(_record.precheck, repo / "nope.md")
    assert (err.code, err.kind) == (1, "record") and "nope.md" in str(err) and "no such file" in str(err)


def test_a_file_outside_any_repo_is_usage(tmp_path):
    p = tmp_path / "loose" / "P.md"
    p.parent.mkdir()
    p.write_text("---\nname: x\n---\n")
    err = refused(_record.precheck, p)
    assert (err.code, err.kind) == (1, "record") and "not in a git work tree" in str(err)


def test_an_untracked_file_is_usage(repo, project):
    p = repo / "new.md"
    p.write_text("---\nname: x\n---\n")
    err = refused(_record.precheck, p)
    assert (err.code, err.kind) == (1, "record") and "untracked" in str(err)
    assert p.read_text() == "---\nname: x\n---\n"


def test_a_file_in_a_repository_with_no_commit_yet_is_usage(repo):
    """Staged, so tracked, but there is no HEAD to compare it with: `git diff
    HEAD` exits 128, and precheck refuses as usage rather than guess
    (verify-001 round 2: the exit table did not name this cause)."""
    p = repo / "projects" / "alpha" / "PROJECT.md"
    p.write_text(PROJECT)
    git(repo, "add", "--", "projects/alpha/PROJECT.md")
    err = refused(_record.precheck, p)
    assert (err.code, err.kind) == (1, "record") and "cannot compare it with HEAD" in str(err)
    assert p.read_text() == PROJECT


@pytest.mark.parametrize("stage", [False, True])
def test_a_file_with_uncommitted_changes_is_usage(repo, project, stage):
    project.write_text(PROJECT.replace("name: alpha", "name: beta"))
    if stage:
        git(repo, "add", "--", "projects/alpha/PROJECT.md")
    err = refused(_record.precheck, project)
    assert (err.code, err.kind) == (1, "record") and "uncommitted changes" in str(err)


# ── failures AFTER a mint: exit 2, kind "record", .result carries the root ──


def fake_git(real, failures: list[str]):
    """A git whose next len(FAILURES) commits fail with those words."""
    calls = []

    def run(cwd, args):
        if args[0] == "commit":
            calls.append(args)
            if failures:
                return subprocess.CompletedProcess(["git", *args], 128, "", failures.pop(0))
        return real(cwd, args)

    return run, calls


RACE = "fatal: cannot lock ref 'HEAD': is at 1111111 but expected 2222222"
LOCK = "fatal: Unable to create '/x/.git/index.lock': File exists."


@pytest.mark.parametrize("words", [RACE, LOCK])
def test_a_concurrent_commit_is_retried_and_then_lands(repo, project, monkeypatch, words):
    run, calls = fake_git(_record._git, [words, words])
    monkeypatch.setattr(_record, "_git", run)
    slept = []
    monkeypatch.setattr(_record, "_sleep", slept.append)
    sha = _record.record(project, R1)
    assert len(calls) == 3 and slept == [0.25, 0.5]
    assert git(repo, "show", "--name-only", "--format=", sha).split() == ["projects/alpha/PROJECT.md"]


def test_a_race_that_outlasts_the_retries_fails_with_the_minted_root(repo, project, monkeypatch):
    run, calls = fake_git(_record._git, [RACE] * 3)
    monkeypatch.setattr(_record, "_git", run)
    monkeypatch.setattr(_record, "_sleep", lambda s: None)
    before, head = project.read_bytes(), git(repo, "rev-parse", "HEAD")
    err = refused(_record.record, project, R2, result=MINTED)
    assert len(calls) == _record.ATTEMPTS == 3
    assert (err.code, err.kind) == (2, "record")
    assert err.result is MINTED and err.result.root_id == R2
    assert R2 in str(err) and "no root" in str(err) and "after 3 tries" in str(err)
    assert project.read_bytes() == before                    # the pointer did not move
    assert git(repo, "rev-parse", "HEAD") == head


def test_any_other_commit_failure_is_not_retried(repo, project, monkeypatch):
    run, calls = fake_git(_record._git, ["fatal: something else entirely"])
    monkeypatch.setattr(_record, "_git", run)
    monkeypatch.setattr(_record, "_sleep", lambda s: pytest.fail("slept on a non-race failure"))
    err = refused(_record.record, project, R2, result=MINTED)
    assert len(calls) == 1 and (err.code, err.kind, err.result) == (2, "record", MINTED)
    assert "something else entirely" in str(err)


def test_a_failed_pointer_move_names_both_ids_and_restores_the_old_one(repo, project, monkeypatch):
    _record.record(project, R1)
    before = project.read_bytes()
    run, _ = fake_git(_record._git, ["fatal: disk full"])
    monkeypatch.setattr(_record, "_git", run)
    err = refused(_record.record, project, R2, result=MINTED)
    assert R1 in str(err) and R2 in str(err)
    assert project.read_bytes() == before


def test_a_file_that_changed_since_its_precheck_fails_as_record_not_usage(repo, project):
    """After a mint, even an 'unfit FILE' is exit 2 carrying the result."""
    _record.precheck(project)
    project.write_text("no front matter any more\n")
    err = refused(_record.record, project, R2, result=MINTED)
    assert (err.code, err.kind, err.result) == (2, "record", MINTED)


@pytest.mark.parametrize("bad", ["", "a b@x", 'a"b@x', "a'b@x"])
def test_an_id_that_cannot_be_written_back_is_refused(repo, project, bad):
    err = refused(_record.record, project, bad, result=MINTED)
    assert (err.code, err.kind, err.result) == (2, "record", MINTED)
    assert project.read_text() == PROJECT


def test_the_exit_code_stays_on_the_closed_table():
    assert _record.EX_RECORD_FAILED == 2


# ── a symlinked FILE is FOLLOWED: the target gets the record, the link stays a link ──
# (do-006 review B1: a rename through the link replaced it with a regular file,
# exit 0, and the target never got mail_root:. The store has this shape today:
# projects/spine/PROJECT.md -> README.md.)


@pytest.fixture
def linked(repo, project) -> Path:
    """A tracked symlink LINK.md -> PROJECT.md, beside it, committed as a link."""
    link = repo / "projects" / "alpha" / "LINK.md"
    link.symlink_to("PROJECT.md")
    git(repo, "add", "--", "projects/alpha/LINK.md")
    git(repo, "commit", "-q", "-m", "add link", "--", "projects/alpha/LINK.md")
    return link


def test_a_symlink_is_followed_and_the_link_stays_a_link(repo, project, linked):
    sha = _record.record(linked, R1, box="alpha")
    assert linked.is_symlink() and linked.readlink() == Path("PROJECT.md")
    assert f'mail_root: "{R1}"\n' in project.read_text()
    assert git(repo, "show", "--name-only", "--format=", sha).split() == ["projects/alpha/PROJECT.md"]
    assert git(repo, "ls-files", "-s", "--", "projects/alpha/LINK.md").split()[0] == "120000"
    assert git(repo, "status", "--porcelain").strip() == ""
    assert _record.record(linked, R1) is None          # read back through the link: already named


def test_precheck_through_a_link_addresses_the_target(repo, project, linked):
    rec = _record.precheck(linked)
    assert (rec.path, rec.link) == (project.resolve(), linked.absolute())


def test_a_dangling_link_is_usage_naming_both(repo, project):
    link = repo / "GONE.md"
    link.symlink_to("nowhere.md")
    err = refused(_record.precheck, link)
    assert (err.code, err.kind) == (1, "record")
    assert str(link) in str(err) and "nowhere.md" in str(err) and "does not exist" in str(err)


def test_a_link_into_another_repository_is_usage_naming_both(repo, project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    for k, v in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", str(tmp_path / "no-hooks"))):
        git(other, "config", k, v)
    target = committed(other, "P.md", "---\nname: other\n---\n")
    link = repo / "OTHER.md"
    link.symlink_to(target)
    head = git(other, "rev-parse", "HEAD")
    err = refused(_record.record, link, R1, result=MINTED)
    assert (err.code, err.kind) == (2, "record")         # inside record(): after a mint
    err = refused(_record.precheck, link)
    assert (err.code, err.kind) == (1, "record")
    assert str(link) in str(err) and "its target is in" in str(err)
    assert git(other, "rev-parse", "HEAD") == head and "mail_root" not in target.read_text()


def test_a_link_to_a_file_outside_any_repository_is_usage(repo, project, tmp_path):
    loose = tmp_path / "loose.md"
    loose.write_text("---\nname: x\n---\n")
    link = repo / "LOOSE.md"
    link.symlink_to(loose)
    err = refused(_record.precheck, link)
    assert (err.code, err.kind) == (1, "record") and "not in a git work tree" in str(err)


def test_a_path_through_a_symlinked_directory_commits_in_the_real_repo(repo, project, tmp_path):
    """A store may be reached through a directory link (two paths that name
    one repo). Resolving must not break that."""
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    sha = _record.record(alias / "projects" / "alpha" / "PROJECT.md", R1)
    assert git(repo, "show", "--name-only", "--format=", sha).split() == ["projects/alpha/PROJECT.md"]


# ── review N1: a failure inside record() names what FILE names NOW ──


def test_a_late_failure_names_the_id_the_file_still_holds(repo, project):
    _record.record(project, R1)
    _record.precheck(project)
    project.write_text(project.read_text().replace("name: alpha", "name: dirty"))
    err = refused(_record.record, project, R2, result=MINTED)
    assert (err.code, err.kind) == (2, "record")
    assert f"still names {R1}" in str(err) and R2 in str(err)


# ── SM-866: a stranger's git config cannot refuse or sign the record ─────


IDENTITY_VARS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


def stranger_store(tmp_path) -> Path:
    """A store repo with NO repo-local config: identity, signing and hooks
    all come from wherever the user's git config says, as a stranger's do."""
    r = tmp_path / "stranger-store"
    (r / "projects" / "alpha").mkdir(parents=True)
    git(r, "init", "-q")
    p = r / "projects" / "alpha" / "PROJECT.md"
    p.write_text(PROJECT)
    git(r, "add", "--", "projects/alpha/PROJECT.md")
    git(r, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return p


@pytest.mark.parametrize("shape", hostile_git.SHAPES)
def test_the_record_lands_unsigned_as_the_users_own_identity_under_a_hostile_config(
        tmp_path, monkeypatch, shape):
    """The GC's hostile global (a hooksPath to a pre-commit that exits 1,
    commit.gpgsign = true, a foreign signer), as a global file or as
    GIT_CONFIG_COUNT entries: the record still commits, UNSIGNED, and its
    author is the identity the user's config names, kept, not dropped."""
    project = stranger_store(tmp_path)
    for k in IDENTITY_VARS:              # a seat exports these: the config's identity is
        monkeypatch.delenv(k, raising=False)   # what this test reads
    for k, v in hostile_git.hostile_env(tmp_path, shape).items():
        monkeypatch.setenv(k, v)
    assert hostile_git.HOOK_SAYS in hostile_git.plain_commit_is_refused(tmp_path, dict(os.environ))
    repo = project.parents[2]
    sha = _record.record(project, R1, box="alpha")
    assert sha and git(repo, "rev-parse", "HEAD").strip() == sha
    assert f'mail_root: "{R1}"' in git(repo, "show", f"{sha}:projects/alpha/PROJECT.md")
    assert not hostile_git.signed(repo, sha)
    user = f"{hostile_git.USER_NAME} <{hostile_git.USER_EMAIL}>"
    assert hostile_git.people(repo, sha) == (user, user)


def test_the_record_keeps_a_repo_local_identity_under_git_config_count(repo, project,
                                                                       tmp_path, monkeypatch):
    """GIT_CONFIG_COUNT entries are command scope: they beat this repo's own
    `commit.gpgsign=false` and `core.hooksPath` (measured, triage-001 row 8),
    which is why this file went red under a seat. The record's own `-c`
    beats them in turn; the repo's local identity still authors it."""
    for k in IDENTITY_VARS:
        monkeypatch.delenv(k, raising=False)
    for k, v in hostile_git.hostile_env(tmp_path, "count").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "commit", "-q", "--allow-empty", "-m", "a plain commit")
    sha = _record.record(project, R1, box="alpha")
    assert sha and not hostile_git.signed(repo, sha)
    assert hostile_git.people(repo, sha) == ("Test <test@example.invalid>",) * 2


def test_the_record_keeps_an_env_identity_under_a_hostile_config(tmp_path, monkeypatch):
    """The user's identity, wherever it comes from, authors the record: an
    exported GIT_AUTHOR_*/GIT_COMMITTER_* (as a seat sets them) beats the
    config's, exactly as in a plain `git commit`. Only signing and hooks
    are pinned."""
    project = stranger_store(tmp_path)
    for k, v in hostile_git.hostile_env(tmp_path, "global").items():
        monkeypatch.setenv(k, v)
    for k in IDENTITY_VARS:
        monkeypatch.setenv(k, "Env User" if k.endswith("_NAME") else "env@example.invalid")
    sha = _record.record(project, R1, box="alpha")
    assert sha and not hostile_git.signed(project.parents[2], sha)
    assert hostile_git.people(project.parents[2], sha) == ("Env User <env@example.invalid>",) * 2


# ── SM-866 ruling: the store repo's OWN hooks run; a global hooksPath never does ─


def identity_only(tmp_path) -> dict[str, str]:
    """A plain user config: an identity and nothing hostile."""
    cfg = tmp_path / "identity-only.gitconfig"
    cfg.write_text(f"[user]\n\tname = {hostile_git.USER_NAME}\n\temail = {hostile_git.USER_EMAIL}\n")
    return {"GIT_CONFIG_GLOBAL": str(cfg)}


def lay(monkeypatch, env: dict[str, str]) -> None:
    for k in IDENTITY_VARS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("shape", ["plain", *hostile_git.SHAPES])
def test_the_store_repos_own_failing_hook_blocks_the_record(tmp_path, monkeypatch, shape):
    """The store repo's own pre-commit runs on the record, and its refusal
    stands: a record error carrying the hook's words, HEAD unmoved, the file
    as it was. Under a hostile config too, and it is the REPO's hook that
    refuses, never the global one."""
    project = stranger_store(tmp_path)
    repo = project.parents[2]
    hostile_git.hook(repo / ".git" / "hooks", refuse="the store says no")
    lay(monkeypatch, identity_only(tmp_path) if shape == "plain"
        else hostile_git.hostile_env(tmp_path, shape))
    head, before = git(repo, "rev-parse", "HEAD"), project.read_bytes()
    err = refused(_record.record, project, R1, result=MINTED)
    assert (err.code, err.kind) == (2, "record")
    assert "the store says no" in str(err) and hostile_git.HOOK_SAYS not in str(err)
    assert git(repo, "rev-parse", "HEAD") == head
    assert project.read_bytes() == before


@pytest.mark.parametrize("shape", hostile_git.SHAPES)
def test_a_hostile_hooks_path_never_leaks_into_the_record_through_the_fallback(
        tmp_path, monkeypatch, shape):
    """With no repo-local core.hooksPath the record runs the repo's
    <git-common-dir>/hooks, never the global or GIT_CONFIG_COUNT hooksPath,
    which `rev-parse --git-path hooks` would have named (shown first)."""
    project = stranger_store(tmp_path)
    repo = project.parents[2]
    lay(monkeypatch, hostile_git.hostile_env(tmp_path, shape))
    assert Path(git(repo, "rev-parse", "--git-path", "hooks").strip()) == \
        hostile_git.hooks_dir(tmp_path), "the rig must make --git-path leak"
    mark = tmp_path / "the-stores-own-hook-ran"
    hostile_git.hook(repo / ".git" / "hooks", mark=mark)
    sha = _record.record(project, R1, box="alpha")
    assert sha and git(repo, "rev-parse", "HEAD").strip() == sha
    assert mark.exists(), "the repo's own hook ran"


@pytest.mark.parametrize("form", ["relative", "absolute"])
def test_a_repo_local_hooks_path_is_honoured_for_the_record(tmp_path, monkeypatch, form):
    """A repo-local core.hooksPath names the store's own hooks, under the
    hostile global too. A RELATIVE value resolves against the repo's TOP, as
    git resolves it: the process cwd holds a hooks dir of that name that
    would let the commit through, and the record `-C`s into projects/alpha/,
    which holds none. Resolved against either, the record would land."""
    project = stranger_store(tmp_path)
    repo = project.parents[2]
    hostile_git.hook(repo / "owner-hooks", refuse="the owner's hooksPath says no")
    git(repo, "config", "--local", "core.hooksPath",
        "owner-hooks" if form == "relative" else str(repo / "owner-hooks"))
    elsewhere = tmp_path / "elsewhere"
    hostile_git.hook(elsewhere / "owner-hooks", mark=tmp_path / "the-cwd-hook-ran")
    monkeypatch.chdir(elsewhere)
    lay(monkeypatch, hostile_git.hostile_env(tmp_path, "global"))
    head = git(repo, "rev-parse", "HEAD")
    err = refused(_record.record, project, R1, result=MINTED)
    assert "the owner's hooksPath says no" in str(err), str(err)
    assert not (tmp_path / "the-cwd-hook-ran").exists()
    assert git(repo, "rev-parse", "HEAD") == head


def test_a_store_whose_hooks_cannot_be_read_fails_as_record(repo, project, monkeypatch):
    """own_hooks raising (git cannot read the repo) is a failed commit with
    git's words, never an unpinned commit that would let a global hook in."""
    def broken(_cwd):
        raise RuntimeError("fatal: the hooks dir is unreadable")
    monkeypatch.setattr(_record, "own_hooks", broken)
    head = git(repo, "rev-parse", "HEAD")
    err = refused(_record.record, project, R1, result=MINTED)
    assert (err.code, err.kind) == (2, "record") and "the hooks dir is unreadable" in str(err)
    assert git(repo, "rev-parse", "HEAD") == head
