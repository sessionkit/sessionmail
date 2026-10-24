"""SM-668: one `notmuch show` of the root thread per reader invocation, with
bodies only when something prints a body.

mhs-thread used to fetch the whole root thread TWICE for a path read (the
walk, then the render), both times with `--body=true`. On a large root that is
14.8 MB of JSON and 1.1-1.9 s per fetch, for a `tree` that prints subjects
only (measured).

Hermetic: a `notmuch` stub on PATH serves a small fixture thread, includes a
"body" key only under --body=true (as notmuch does), and logs every call. The
verifier is `true` (SESSION_MAIL_VERIFY), so every owner mail is a member.

Run: uv run pytest -q tests/mhs/test_single_fetch.py
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "sessionmail", "mhs")
MHS = os.path.join(HERE, "mhs-thread")

ROOT_ID = "root@fixture"
BODY = "the body of ten, printed only on request"
LETTER = "the letter to self, printed by --boot"

STUB = textwrap.dedent('''\
    #!%(python)s
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["NM_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\\n")
    if not args or args[0] != "show":
        sys.exit(0)
    with open(os.environ["NM_FIXTURE"]) as fh:
        thread = json.load(fh)
    bodies = "--body=true" in args
    def strip(node):
        msg, kids = node
        msg = dict(msg)
        if not bodies:
            msg.pop("body", None)
        return [msg, [strip(k) for k in kids]]
    if os.environ.get("NM_EMPTY") and "--entire-thread=false" not in args:
        sys.exit(0)
    if "--entire-thread=false" in args:
        want = [a for a in args if a.startswith("id:")][0][3:]
        def find(node):
            if node[0]["id"] == want:
                return node
            for k in node[1]:
                hit = find(k)
                if hit:
                    return hit
        hit = find(thread[0])
        print(json.dumps([[strip([hit[0], []])]] if hit else []))
    else:
        print(json.dumps([[strip(n) for n in thread]]))
''')


def _msg(tmp, mid, subject, ts, body=None):
    fn = tmp / ("%s.eml" % mid.split("@")[0])
    fn.write_text("From: tst@host\nTo: tst@host\nSubject: %s\nMessage-ID: <%s>\n\n%s\n"
                  % (subject, mid, body or ""))
    m = {"id": mid, "match": True, "excluded": False, "filename": [str(fn)],
         "timestamp": ts, "date_relative": "", "tags": [], "crypto": {},
         "headers": {"Subject": subject, "From": "tst@host", "To": "tst@host",
                     "Date": "Sun, 09 Jun 2030 12:00:00 +0000"}}
    if body is not None:
        m["body"] = [{"id": 1, "content-type": "text/plain", "content": body}]
    return m


@pytest.fixture
def env(tmp_path):
    thread = [[_msg(tmp_path, ROOT_ID, "[ROOT]", 1), [
        [_msg(tmp_path, "asks@fixture", "[ASKS]", 2), [
            [_msg(tmp_path, "ten@fixture", "10 first ask", 3, BODY), []],
            [_msg(tmp_path, "twenty@fixture", "20 second ask", 4, "twenty"), []],
        ]],
        [_msg(tmp_path, "boot@fixture", "[BOOT]", 5), [
            [_msg(tmp_path, "letter@fixture", "10 letter", 6, LETTER), []],
        ]],
    ]]]
    (tmp_path / "thread.json").write_text(json.dumps(thread))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "notmuch"
    stub.write_text(STUB % {"python": sys.executable})
    stub.chmod(0o755)
    e = {k: v for k, v in os.environ.items() if k not in ("SESSION_AGENT", "CLAUDE_SESSION_ID")}
    e.update(PATH="%s:%s" % (bindir, e.get("PATH", "")), HOME=str(tmp_path),
             NM_LOG=str(tmp_path / "nm.log"), NM_FIXTURE=str(tmp_path / "thread.json"),
             CLAUDE_CODE_AGENT="tst", TST_ROOT="<%s>" % ROOT_ID, SESSION_MAIL_VERIFY="true")
    return tmp_path, e


def run(env, *args):
    tmp, e = env
    log = tmp / "nm.log"
    if log.exists():
        log.unlink()
    p = subprocess.run([sys.executable, MHS, *args], env=e, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    # The root-thread fetches: every `show` but the priming one (--entire-thread=false).
    shows = [c for c in calls if c[0] == "show" and "--entire-thread=false" not in c]
    return p.stdout, shows


def body_flags(shows):
    return [[a for a in c if a.startswith("--body=")][0] for c in shows]


@pytest.mark.parametrize("args", [("tree", "/ASKS", "--depth", "1"), ("ls", "/ASKS"), ("ls", "/"),
                                  ("--path", "/ASKS", "--resolve")])
def test_subject_readers_fetch_the_thread_once_without_bodies(env, args):
    out, shows = run(env, *args)
    assert body_flags(shows) == ["--body=false"], shows
    assert BODY not in out


def test_tree_still_lists_every_item(env):
    out, _ = run(env, "tree", "/ASKS", "--depth", "1")
    assert "10 first ask" in out and "20 second ask" in out


def test_cat_fetches_once_with_bodies_and_prints_the_body(env):
    out, shows = run(env, "cat", "/ASKS/10")
    assert body_flags(shows) == ["--body=true"], shows
    assert BODY in out


def test_tree_with_bodies_fetches_once_with_bodies(env):
    out, shows = run(env, "tree", "/ASKS", "--depth", "1", "-b")
    assert body_flags(shows) == ["--body=true"], shows
    assert BODY in out


def test_boot_fetches_once_with_bodies_and_prints_the_letter(env):
    out, shows = run(env, "--boot")
    assert body_flags(shows) == ["--body=true"], shows
    assert LETTER in out and "[ASKS]" in out


def dry_shows(env, *args):
    """The root-thread fetches a -n dry run says it would run (S01: every
    printed line is the invocation the real run makes)."""
    out, _ = run(env, *args, "-n")
    return [l for l in out.splitlines()
            if "notmuch show" in l and "--entire-thread=false" not in l]


@pytest.mark.parametrize("args, flag", [(("tree", "/ASKS", "--depth", "1"), "--body=false"),
                                        (("ls", "/"), "--body=false"),
                                        (("cat", "/ASKS/10"), "--body=true"),
                                        (("--boot",), "--body=true")])
def test_dry_run_prints_the_one_fetch_the_real_run_makes(env, args, flag):
    out, real = run(env, *args)
    dry = dry_shows(env, *args)
    assert len(dry) == len(real) == 1, (dry, real)
    assert flag in dry[0] and body_flags(real) == [flag]


@pytest.mark.parametrize("args", [("tree", "/ASKS", "--depth", "1"), ("--boot",)])
def test_an_empty_root_thread_fetch_is_a_named_refusal_not_a_traceback(env, args):
    """The root primes (its own message resolves) but the thread fetch prints
    nothing: the reader exits non-zero, naming the root, instead of the
    JSONDecodeError traceback it raised before SM-668."""
    tmp, e = env
    p = subprocess.run([sys.executable, MHS, *args], env=dict(e, NM_EMPTY="1"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "notmuch show printed nothing for the root thread id:%s" % ROOT_ID in p.stderr, p.stderr
    assert "Traceback" not in p.stderr, p.stderr
