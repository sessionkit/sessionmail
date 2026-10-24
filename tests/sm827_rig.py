"""SM-827's rig: a scratch memory home read by the REAL readers, notmuch and verifier.

Shared by tests/test_memory_signed_body.py (the attack rows) and
tests/test_memory_honest_unchanged.py (the honest fixtures). Built from the task's measure
(SM-827 findings/repro-001/measure.py and rca-001/prove.py), not re-derived:

- a personal memory tree for agent `alice`: [ROOT] -> [NOTES] -> item 1, [ROOT] -> [BOOT]
  -> "1 letter to self" (mhs-thread --boot), and a shelf letter To alice+boot (memory
  wake's BOOT section). Each mail is the sender's own `_sender.build`, signed v2 by a key
  made for the test (`Key`), and its registry (agent alice) is a scratch directory too;
- a REAL notmuch database under the home, asserted before `notmuch new`: the parse
  differential this card closes is GMime's, so a stub would prove nothing;
- the readers run as `memory` runs them, from a COPY of the package (`Tree`). Since
  SM-878 that is on the package's own Python: a Python reader (`READERS`) is started as
  `[sys.executable, script]`, never by its `#!/usr/bin/env python3` shebang. The PATH
  shim makes `python3` PATH's python3 or /usr/bin/python3 (macOS 3.9): the interpreters
  the verifier meets when it is run by hand (`Tree.verify`), which it is built to
  survive. A reader row needs only one of them (`reader_pythons`).

Hermetic: every HOME, maildir, notmuch DB, key and registry is under pytest's tmp_path.
Nothing reads ~/.mail and nothing is sent (the wire is written straight into the box).
`memory run` EXECUTES only echo lines, under `env -i`, in an empty scratch cwd.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from sessionmail import _sender, _sig

PKG = Path(_sig.__file__).resolve().parent
NOTMUCH = shutil.which("notmuch")
SYS_PY = "/usr/bin/python3"
NS = "session-mail@example.com"
DATE = "Sun, 08 Dec 2030 14:00:00 +0000"
SESSION = "5e827a11"
BOUNDARY = "=_sesh890_" + "0" * 32
A = "alice@sm827.invalid"


def pythons() -> list[tuple[str, str]]:
    """(id, interpreter): PATH's python3, and /usr/bin/python3 where it exists."""
    out = [("path-python3", shutil.which("python3"))]
    if os.access(SYS_PY, os.X_OK):
        out.append(("usr-bin-python3", SYS_PY))
    return out


#: The bundled Python readers. `memory` starts each on the package's Python since SM-878
#: (`_mhs.oracle`), and `Tree.run` does the same.
READERS = frozenset({"mhs-thread", "session-mail-run", "session-mail-wake"})


def reader_pythons() -> list[tuple[str, str]]:
    """`pythons()` for a row that only runs READERS: PATH's python3 alone.

    The readers run on the package's Python since SM-878, so the shim's python3 never
    runs a reader, and mhs-thread hands its own verifier that Python too
    ($SESSIONMAIL_PYTHON). A /usr/bin/python3 param would be an exact duplicate. A
    reader on 3.9 is refused by design (the 3.11 guard), which
    test_bundled_interpreter's test_a_by_hand_run_on_an_old_python_refuses_in_one_line
    and test_the_guard_parses_and_refuses_on_a_real_old_python assert. A row that also
    runs the verifier BY HAND (`Tree.verify`) keeps `pythons()`: the verifier is built to
    run on 3.9, and those rows are its proof."""
    return pythons()[:1]


# ── a key and its registry ───────────────────────────────────────────────────

class Key:
    """A throwaway ed25519 key and a registry whose one cert binds it to agent alice
    (as tests/test_verify_mime_ambiguous.py's Rig). The verifier binds a v2 key by its
    derived session id, so plain mode says GOOD; --trust says UNBLESSED (rc 4), which
    `memory run` runs, headed UNBLESSED."""

    def __init__(self, d: Path):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)
        self.key = d / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "sm827-test",
                        "-f", str(self.key)], check=True)
        kt, kb = (d / "key.pub").read_text().split()[:2]
        h = hashlib.sha256(base64.b64decode(kb)).digest()[:16].hex()
        sid = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        self.registry = d / "registry"
        (self.registry / "certs").mkdir(parents=True)
        (self.registry / "certs" / f"{sid}.cert").write_text(
            f"session-birth-v1\nsession-id: {sid}\npubkey: {kt} {kb}\nagent: alice\n")

    def sign(self, raw: bytes) -> bytes:
        h, p = _sig.h_for(raw), _sig.digests(raw)
        done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(self.key), "-n", NS],
                              input=_sig.canonical(raw, NS, h, p, ""), capture_output=True,
                              check=True)
        b = "".join(x for x in done.stdout.decode().splitlines()
                    if x and "SSH SIGNATURE" not in x)
        return _sig.insert_header(raw, _sig.format_header(NS, h, p, b))


# ── the mails ────────────────────────────────────────────────────────────────

def v2_mail(key: Key, subject: str, mid: str, parent: str | None, body: bytes,
            page: bytes | None = None, to: str = A) -> bytes:
    """An honest v2 mail exactly as `_sender.build` writes it, signed by KEY."""
    req = _sender.Request(to=to, from_=A, subject=subject, in_reply_to=parent)
    w = _sender.build(req, body, host="sm827.invalid", date=DATE, message_id=mid,
                      references=[parent] if parent else [], session=SESSION,
                      signature=None, page=page, boundary=BOUNDARY if page is not None else "")
    return key.sign(w)


def v2_unsigned(raw: bytes) -> bytes:
    """RAW with its X-Session-Signature field (and its folded lines) removed: the wire
    again, to edit and sign afresh."""
    head, sep, body = raw.partition(b"\n\n")
    out, skip = [], False
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if not skip:
                out.append(line)
            continue
        skip = line.lower().startswith(b"x-session-signature:")
        if not skip:
            out.append(line)
    return b"\n".join(out) + sep + body


def labels(key: Key) -> dict[str, bytes]:
    return {
        "root": v2_mail(key, "[ROOT]", "<root@sm827.invalid>", None, b"alice's memory\n"),
        "notes": v2_mail(key, "[NOTES]", "<notes@sm827.invalid>", "<root@sm827.invalid>",
                         b"notes\n"),
        "bootlabel": v2_mail(key, "[BOOT]", "<bootlabel@sm827.invalid>", "<root@sm827.invalid>",
                             b"boot\n"),
    }


def memory_set(key: Key, body: bytes, page: bytes | None, item_subject: str) -> dict[str, bytes]:
    """The labels, the item under [NOTES], the letter under [BOOT] and the shelf letter, the
    last three with BODY (and PAGE as a multipart/alternative's html)."""
    return {
        **labels(key),
        "item": v2_mail(key, item_subject, "<item@sm827.invalid>", "<notes@sm827.invalid>",
                        body, page),
        "boot": v2_mail(key, "1 letter to self", "<boot@sm827.invalid>",
                        "<bootlabel@sm827.invalid>", body, page),
        # postfix PREPENDS X-Original-To on delivery; the shelf matches on it
        "shelf": b"X-Original-To: alice+boot@sm827.invalid\n"
                 + v2_mail(key, "shelf letter", "<shelf@sm827.invalid>", None, body, page,
                           to="alice+boot@sm827.invalid"),
    }


EDITED = ("item", "boot", "shelf")   # the mails whose BODY a reader shows


def edited(mails: dict[str, bytes], edit) -> dict[str, bytes]:
    """EDIT (bytes -> bytes, no key) applied to every mail a reader shows a body from."""
    out = dict(mails)
    for k in EDITED:
        if k not in mails:
            continue
        if k == "shelf":
            xo, rest = mails[k].split(b"\n", 1)
            out[k] = xo + b"\n" + edit(rest)
        else:
            out[k] = edit(mails[k])
    return out


# ── a scratch home ───────────────────────────────────────────────────────────

def shim(root: Path, python: str) -> Path:
    d = root / ("shim-" + hashlib.sha256(python.encode()).hexdigest()[:8])
    if not d.exists():
        d.mkdir(parents=True)
        (d / "python3").symlink_to(python)
    return d


def env_for(home: Path, python: str, **extra: str) -> dict[str, str]:
    """The ONLY environment the readers see: a scratch HOME, notmuch config and root hint."""
    path = [str(shim(home.parent, python)), os.path.dirname(NOTMUCH), "/usr/bin", "/bin"]
    if shutil.which("ssh-keygen"):
        path.insert(2, os.path.dirname(shutil.which("ssh-keygen")))
    env = {"PATH": os.pathsep.join(dict.fromkeys(path)), "HOME": str(home), "TERM": "dumb",
           "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
           "SESSION_MAIL_ROOT": str(home / ".mail" / "agents"),
           "SESSIONMAIL_ROOT": str(home / ".mail" / "agents"),
           "MAIL_ROOT": str(home / ".mail"), "MAILDIR": str(home / ".mail"),
           "NOTMUCH_CONFIG": str(home / "notmuch-config"),
           "CLAUDE_CODE_SESSION_ID": "00000000-0000-4000-8000-000000000827",
           "CLAUDE_CODE_AGENT": "alice", "ALICE_ROOT": "root@sm827.invalid",
           "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_DATA_HOME": str(home / ".local" / "share")}
    env.update(extra)
    return env


def make_home(home: Path, mails: dict[str, bytes], extra: dict[str, bytes] | None = None
              ) -> dict[str, Path]:
    """MAILS into a scratch maildir (EXTRA: more files, by name), and a notmuch DB over it."""
    box = home / ".mail" / "agents" / "alice"
    for sub in ("new", "cur", "tmp"):
        (box / sub).mkdir(parents=True)
    files = {}
    for k, raw in {**mails, **(extra or {})}.items():
        f = box / "cur" / f"{k}:2,"
        f.write_bytes(raw)
        files[k] = f
    (home / "notmuch-config").write_text(
        "# SM-827 test: a SCRATCH notmuch config. Never the live database.\n"
        f"[database]\npath={box}\n[user]\nname=alice\nprimary_email={A}\n"
        "[new]\ntags=\nignore=\n[maildir]\nsynchronize_flags=false\n")
    env = env_for(home, shutil.which("python3"))
    got = subprocess.run([NOTMUCH, "config", "get", "database.path"], env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert Path(got).resolve() == box.resolve(), f"REFUSED: database.path {got}"
    subprocess.run([NOTMUCH, "new", "--quiet"], env=env, check=True, capture_output=True)
    return files


# ── a copy of the package: the readers under test ────────────────────────────

#: Appended to a Tree's _sig.py when the rows must still VERIFY: SM-819's, SM-823's and
#: SM-831's refusals switched off, so a test proves the READERS (with those refusals in,
#: every attack row reads BAD and a reader test would pass for the wrong reason). SM-831's
#: `dispo_defects` (merged up at 00401ce) is what makes the two Content-Disposition rows
#: BAD; v1_defects calls it too, and no row here is v1. signed_text, leaves(), leaf_canon
#: and digests() are untouched: the readers run the real code.
BYPASS = '''

# ── SM-827 TEST ONLY: the verifier's SM-819 / SM-823 / SM-831 refusals switched off ──
def cte_defects(raw):
    return []


def mime_defects(raw):
    return []


def dispo_defects(raw):
    return []
'''

#: A verifier that says GOOD to every mail and is NOT named by SESSION_MAIL_VERIFY: so
#: the readers keep their own p= re-check (check=True). It stands in for a verifier whose
#: reading differs from the reader's (another interpreter, or another file).
YES_VERIFIER = "#!/usr/bin/env bash\n# SM-827 TEST ONLY: GOOD to everything.\nexit 0\n"


class Tree:
    """A copy of the installed package's `sessionmail/` (the oracle scripts find their
    siblings, _sig.py and the verifier, beside themselves)."""

    def __init__(self, root: Path, bypass: bool = False, yes_verifier: bool = False,
                 src: Path = PKG):
        """SRC: the `sessionmail/` package dir to copy (the installed one; the golden
        recorder hands in another commit's)."""
        self.pkg = root / "sessionmail"
        shutil.copytree(src, self.pkg, ignore=shutil.ignore_patterns("__pycache__"))
        if bypass:
            sig = self.pkg / "_sig.py"
            sig.write_text(sig.read_text() + BYPASS)
        if yes_verifier:
            v = self.pkg / "mhs" / "session-mail-verify"
            v.write_text(YES_VERIFIER)
            v.chmod(0o755)
        self.mhs = self.pkg / "mhs"

    def run(self, env: dict, name: str, *args: str, cwd: Path | None = None,
            envi: bool = False) -> dict:
        argv = [str(self.mhs / name), *args]
        if name in READERS:   # as `memory` starts it (SM-878): the package's Python
            argv = [sys.executable, *argv]
        if name != "session-mail-verify":
            # `memory` hands every script it starts its own Python (`_mhs.child_env`): the
            # bash shims read it. The verifier run BY HAND (`verify`) keeps the shim's.
            env = {**env, "SESSIONMAIL_PYTHON": sys.executable}
        if envi:      # literally `env -i K=V ... argv`: the child sees ONLY these variables
            argv = ["/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()], *argv]
            env = {}
        p = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=300)
        return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}

    def verify(self, env: dict, path: Path) -> int:
        return self.run(env, "session-mail-verify", str(path))["rc"]


#: (name, script, args): what each consumer runs, as `memory` runs it (memory.py hands
#: these argvs to the oracle and exits as it exited). `memory cat --full` is `--bodies`.
def consumers(item_path: str = "/NOTES/1") -> dict[str, tuple]:
    return {
        "cat": ("mhs-thread", "cat", item_path),
        "cat_full": ("mhs-thread", "cat", item_path, "--bodies"),
        "tree_b": ("mhs-thread", "tree", "/NOTES", "-b"),
        "boot": ("mhs-thread", "--boot"),
        "run_list": ("session-mail-run", "/NOTES", "--list"),
        "wake": ("session-mail-wake", "alice"),
    }


# ── what each consumer shows ─────────────────────────────────────────────────

def norm(text: str | None) -> str:
    """Whitespace-normalised: each line stripped, blank lines dropped."""
    if not text:
        return ""
    return "\n".join(l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip())


def body_cat(out: str) -> str:
    """`cat`: the item line, its body indented, then the `— /NOTES/1 = ...` footer."""
    lines = out.split("\n")
    return "\n".join(l for l in lines[1:] if not l.startswith("— "))


def body_tree_b(out: str, item_line: str) -> str:
    keep, on = [], False
    for l in out.split("\n"):
        if item_line in l and not on:
            on = True
            continue
        if on:
            if l.startswith("— ") or re.match(r"^\s*└ \S", l):
                break
            keep.append(l)
    return "\n".join(keep)


def body_boot(out: str) -> str:
    """`--boot`: after the `[BOOT] — the letter to self` banner, the subject line and the
    `id:` line, the body, up to the `— N other boot item(s)` footer."""
    parts = out.split("═══ [BOOT] — the letter to self", 1)
    if len(parts) < 2:
        return ""
    keep = []
    for l in parts[1].split("\n")[1:]:
        if l.startswith("— "):
            break
        keep.append(l)
    return "\n".join(keep[2:])


def shelf(out: str) -> str:
    """memory wake's BOOT section, verbatim: from its banner to the RECENT banner."""
    i, j = out.find("═══ BOOT"), out.find("═══ RECENT")
    return out[i:j] if i != -1 else ""


def body_shelf(out: str) -> str:
    keep = []
    for l in shelf(out).split("\n")[1:]:
        if l.startswith("— ") or l.startswith("═══"):
            break
        keep.append(l)
    # line 0 the subject, line 1 "  from ... · [<_sig.UNPINNED>]"; then the body; maybe "(N older ...)"
    return "\n".join(l for l in keep[2:] if not l.strip().startswith("("))


def shelf_mark(out: str) -> str:
    # SM-762: a shown letter's badge is _sig.UNPINNED, never "sig OK"; a refused one
    # has no badge (its "⊘ REFUSED" line names why). "sig OK" stays in the pattern
    # so a regression to it reads as itself, not as "?".
    m = re.search(r"· \[(sig OK|" + re.escape(_sig.UNPINNED) + r"|⚠ UNVERIFIED[^\]]*)\]", shelf(out))
    return m.group(1) if m else "?"


def run_list_item(out: str, num: str = "1") -> tuple[str, str]:
    """(flag line, command) for item NUM in `run --list`."""
    lines = out.split("\n")
    for n, l in enumerate(lines):
        if re.match(rf"^# {re.escape(num)}  budget=", l):
            body = []
            for m in lines[n + 1:]:
                if m.startswith("# ") and re.match(r"^# \d", m):
                    break
                body.append(m)
            return l, "\n".join(body)
    return "", ""


# ── SM-762: the fence, applied to a golden recorded before it ────────────────
#: What `sig OK` became. The shelf's cost is len(text) // 4 over text that holds the
#: badge, so the longer badge adds exactly (len(UNPINNED) - len("sig OK")) / 4 tokens.
FENCE_GROWTH = len(_sig.UNPINNED) - len("sig OK")
assert FENCE_GROWTH % 4 == 0, "the shelf token shift would not be exact"
_CAT_FOOT = re.compile(r"^(— /[^\n]* = <[^>\n]*> · \d+ shown, \d+ filtered)( · ≈\d+ tok)$", re.M)
_SHELF_COST = re.compile(r"^(— shelf newest shown · ≈)(\d+)( tok)$", re.M)


def fenced(case: dict, member_cat: bool = True) -> dict:
    """A pre-SM-762 golden case (consumer -> {rc, out, err}) as SM-762's F prints it, and
    NOTHING else changed: `sig OK` reads `<UNPINNED>` in the shelf's and --boot's badge (the
    shelf's cost moves by FENCE_GROWTH // 4), and a checked cat footer gains
    ` · <UNPINNED>` when the item shown is a member (member_cat). Every other byte stays."""
    out = {}
    for k, v in case.items():
        text = v.get("out", "")
        if k in ("shelf", "boot"):
            text = (text.replace("[sig OK]", f"[{_sig.UNPINNED}]")         # the shelf's badge
                        .replace("· sig OK]", f"· {_sig.UNPINNED}]"))       # --boot's
        if k == "shelf" and _sig.UNPINNED in text:
            text = _SHELF_COST.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + FENCE_GROWTH // 4}"
                                   f"{m.group(3)}", text)
        if k in ("cat", "cat_full") and member_cat:
            text = _CAT_FOOT.sub(lambda m: f"{m.group(1)} · {_sig.UNPINNED}{m.group(2)}", text)
        out[k] = {**v, "out": text}
    return out
