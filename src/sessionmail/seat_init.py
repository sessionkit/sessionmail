#!/usr/bin/env python3
"""message seat init: mint a key and a birth cert for a seat that has no Claude session.

    message seat init --registry DIR ADDRESS [--machine NAME]

The adapter signs as its seat (say desk.alice, or a plus address such as
heard+airpods), and its cert matches that name exactly, +tag and all. It:

1. generates an ed25519 key at $SESSIONMAIL_HOME/seat-keys/<ADDRESS>/id_ed25519
   (dir 0700, key 0600), and publishes its pub as seat.pub. No private key sits
   beside that pub's stem, so with the seat agent dead `ssh-keygen -Y sign -f
   seat.pub` FAILS instead of quietly signing from the file. A send that must sign is then refused.
2. writes a session-birth-v1 cert, field for field as claude-agents
   lamp-check:61-69 writes one, with `agent: ADDRESS` exactly and `kind: seat`,
   and self-signs it under the registry's session-birth@ namespace: the one
   session-birth@ entry in the newest @session line's namespaces= list, or
   session-birth@example.com when that list has none (mail and bless fall back
   to @example.com the same way). Two or more such entries are refused.
3. registers it the way lamp-check:113-115 does (an allowed_signers line, plus
   certs/<sid>.cert and .cert.sig), with namespaces= copied from the
   registry's NEWEST @session line, in ONE pathspec commit signed by the new
   key (its possession proof).
4. prints the sid, the fingerprint and the operator's bless command. It NEVER
   blesses: a blessing is the operator's hardware touch.

--registry has no default, so it can never touch the live registry by
accident. It has no command of its own on PATH: the wheel ships it as
`message seat init` (the package keeps three commands).
Exit: 0 minted; 2 refused, with nothing written; 1 a tool failed, rolled back.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: The birth namespace when the registry's namespaces= list names none: the
#: same neutral fallback as the sender's and session-mail-verify's namespaces.
BIRTH_NS_FALLBACK = "session-birth@example.com"
BIRTH_PREFIX = "session-birth@"
#: The blessing's principal: the SAME variable and the same neutral fallback
#: as mhs/session-mail-verify's BLESS_PRINCIPAL, so the bless command this
#: prints names the principal verify will check. No person's name in code
#: (the package's rule); a host sets SESSION_BLESS_PRINCIPAL.
BLESS_PRINCIPAL = os.environ.get("SESSION_BLESS_PRINCIPAL", "alice@bless")
#: A seat address: one name, or a plus address `<base>+<tag>` (heard+airpods).
#: Each half is the dot-grammar name (lowercase letters, digits, '.', '_',
#: '-', starting with a letter or digit), and there is at most ONE '+'. The
#: address is used verbatim, '+' and all: the seat-keys directory name, the
#: cert's `agent:` line (the verifier's leg 2 keeps the +tag and matches it
#: exactly), the registry commit's author and subject, and `message seat
#: agent`'s launchd label and socket path (_launchd.ADDRESS_RE, the wrapper's
#: SEAT_ADDRESS: the same pattern, three copies).
ADDRESS = re.compile(r"[a-z0-9][a-z0-9._-]*(?:\+[a-z0-9][a-z0-9._-]*)?\Z")
SESSION_LINE = re.compile(r'^(\S+)@session\s+namespaces="([^"]+)"\s+ssh-\S+\s+\S+')
EX_REFUSED, EX_FAILED = 2, 1
#: Every file a mint writes in the key home. All must be absent before it
#: starts (check_key_home), so a failed mint removes exactly these.
WRITES = ("id_ed25519", "id_ed25519.pub", "seat.pub", "birth-cert.txt", "birth-cert.txt.sig")


class Refused(Exception):
    """Nothing was written: say why, exit 2."""


def sid_of(publine: str) -> str:
    """sha256(the key's wire blob)[:16] as a UUID: session-mail-verify:159-161
    and claude-agents sid_derive.py, the same three lines."""
    h = hashlib.sha256(base64.b64decode(publine.split()[1])).digest()[:16].hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


#: The env identity git prefers over `-c user.*`: the birth commit
#: names its own author, so a seat's exported GIT_AUTHOR_*/GIT_COMMITTER_*
#: must not replace it.
GIT_IDENTITY_VARS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


def own_hooks(repo: Path) -> str:
    """The hooks directory REPO itself names, absolute, for a machine commit's
    `-c core.hooksPath=`: the repo's own hooks run, a user's global or system
    hooksPath (or one exported through GIT_CONFIG_COUNT) never does.

    It is the repo-local core.hooksPath when one is set (a relative one is
    resolved against the work tree's top, as git resolves it, and against
    the git dir in a bare repo), else `<git-common-dir>/hooks`. Never
    `rev-parse --git-path hooks`: that follows any hooksPath in effect, the
    global one included. Raises RuntimeError with git's words when REPO
    cannot be read."""
    git = ["git", "-C", str(repo)]
    local = subprocess.run([*git, "config", "--local", "--includes", "--type=path", "--get",
                            "core.hooksPath"], capture_output=True, text=True)
    if local.returncode not in (0, 1):   # 1 is "not set"
        raise RuntimeError(local.stderr.strip() or f"git config exit {local.returncode}")
    named = local.stdout.rstrip("\n") if local.returncode == 0 else ""
    if named and Path(named).is_absolute():
        return named
    where = ["--show-toplevel"] if named else ["--path-format=absolute", "--git-common-dir"]
    base = subprocess.run([*git, "rev-parse", *where], capture_output=True, text=True)
    if base.returncode != 0 and named:   # a bare repo: hooks run in the git dir
        base = subprocess.run([*git, "rev-parse", "--path-format=absolute", "--git-dir"],
                              capture_output=True, text=True)
    if base.returncode != 0 or not base.stdout.strip():
        raise RuntimeError(base.stderr.strip() or f"git rev-parse exit {base.returncode}")
    return str(Path(base.stdout.strip()) / (named or "hooks"))


def run(argv: list[str], *, drop: tuple[str, ...] = (), **kw) -> subprocess.CompletedProcess:
    """A tool, never through anyone's agent: the seat key signs from its file
    here, and $SSH_AUTH_SOCK (the user's launchd agent under launchd) is dropped,
    with any variable named in DROP."""
    env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK" and k not in drop}
    return subprocess.run(argv, capture_output=True, text=True, env=env, **kw)


def loose(path: Path) -> bool:
    return bool(stat.S_IMODE(path.stat().st_mode) & 0o077)


# ── checks: all of them before anything is written ───────────────────────────

def check_address(address: str) -> None:
    if not ADDRESS.match(address):
        raise Refused(f"'{address}' is not a seat address: lowercase letters, digits, '.', '_' "
                      f"and '-', starting with a letter or digit, and at most one '+' between "
                      f"two such names (heard+airpods). It is also a directory name")


def check_registry(registry: Path) -> tuple[Path, str]:
    """The registry's git top level, and the newest @session line's namespaces."""
    if not registry.is_dir():
        raise Refused(f"--registry {registry}: not a directory")
    top = run(["git", "-C", str(registry), "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        raise Refused(f"--registry {registry}: not inside a git work tree (the registry's "
                      f"history is its witnessed timeline)")
    signers = registry / "allowed_signers"
    if not signers.is_file():
        raise Refused(f"--registry {registry}: no allowed_signers file")
    found = [m.group(2) for m in map(SESSION_LINE.match, signers.read_text().splitlines()) if m]
    if not found:
        raise Refused(f"{signers}: no @session line with namespaces= to copy (the "
                      f"list comes from the registry, never a copy kept here)")
    dirty = run(["git", "-C", str(registry), "status", "--porcelain", "--", "allowed_signers"])
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise Refused(f"{signers} has uncommitted changes: the one commit this makes would "
                      f"carry them. Commit or drop them first")
    return Path(top.stdout.strip()), found[-1]


def birth_namespace(signers: Path, namespaces: str) -> tuple[str, bool]:
    """The ONE session-birth@ entry in the namespaces= list, and whether it fell
    back (the list names none). More than one is refused: never guess which."""
    births = [n for n in (x.strip() for x in namespaces.split(",")) if n.startswith(BIRTH_PREFIX)]
    if len(births) > 1:
        raise Refused(f"{signers}: the newest @session line's namespaces= names "
                      f"{len(births)} {BIRTH_PREFIX} namespaces ({', '.join(births)}): "
                      f"which one a birth is signed under is not guessed. Leave one")
    return (births[0], False) if births else (BIRTH_NS_FALLBACK, True)


def check_key_home(keys: Path, home: Path) -> None:
    for d in (keys, home):
        if d.exists() and loose(d):
            raise Refused(f"{d} has group or world bits "
                          f"({oct(stat.S_IMODE(d.stat().st_mode))}): a seat key lives in 0700")
    for name in WRITES:
        f = home / name
        if f.exists():
            note = f" (and it is {oct(stat.S_IMODE(f.stat().st_mode))}: loose)" if loose(f) else ""
            raise Refused(f"{f} exists{note}: seat-init never overwrites a key. Rotation mints "
                          f"a NEW seat key and leaves the old cert in place")


# ── mint ─────────────────────────────────────────────────────────────────────

def generate(home: Path, address: str) -> tuple[Path, Path, str]:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home.parent, 0o700)
    os.chmod(home, 0o700)
    key = home / "id_ed25519"
    made = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{address} seat",
                "-f", str(key)])
    if made.returncode != 0:
        raise RuntimeError(f"ssh-keygen could not generate the key: {made.stderr.strip()}")
    pub = home / "seat.pub"
    os.replace(home / "id_ed25519.pub", pub)     # no `seat` beside it: probe 8c
    os.chmod(key, 0o600)
    os.chmod(pub, 0o600)
    return key, pub, " ".join(pub.read_text().split()[:2])


def birth_cert(home: Path, key: Path, sid: str, publine: str, address: str,
               machine: str, birth_ns: str) -> Path:
    """lamp-check:61-73, field for field, plus `kind: seat` (ignored by the
    verifier, session-mail-verify:340-352; there for an auditor)."""
    cert = home / "birth-cert.txt"
    cert.write_text(f"session-birth-v1\nsession-id: {sid}\npubkey: {publine}\n"
                    f"born: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nagent: {address}\n"
                    f"machine: {machine}\nkind: seat\n")
    signed = run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", birth_ns, str(cert)])
    if signed.returncode != 0:
        raise RuntimeError(f"the birth cert could not be signed: {signed.stderr.strip()}")
    with tempfile.NamedTemporaryFile("w", suffix=".allowed", delete=False) as f:
        f.write(f"{sid}@session {publine}\n")    # lamp-check:79-81, the possession proof
    try:
        with open(cert, "rb") as data:
            ok = run(["ssh-keygen", "-Y", "verify", "-f", f.name, "-I", f"{sid}@session",
                      "-n", birth_ns, "-s", f"{cert}.sig"], stdin=data)
    finally:
        os.unlink(f.name)
    if ok.returncode != 0:
        raise RuntimeError(f"the birth cert's own signature does not verify: {ok.stderr.strip()}")
    return cert


def register(registry: Path, cert: Path, key: Path, sid: str, publine: str,
             namespaces: str, address: str) -> str:
    """lamp-check:105-137: the line, the two cert files, ONE pathspec commit.
    Any failure puts the registry back as it was and re-raises."""
    signers = registry / "allowed_signers"
    before = signers.read_bytes()
    certs = registry / "certs"
    certs.mkdir(exist_ok=True)
    paths = ["allowed_signers", f"certs/{sid}.cert", f"certs/{sid}.cert.sig"]
    try:
        with open(signers, "ab") as f:
            if before and not before.endswith(b"\n"):
                f.write(b"\n")
            f.write(f'{sid}@session namespaces="{namespaces}" {publine}\n'.encode())
        shutil.copyfile(cert, certs / f"{sid}.cert")
        shutil.copyfile(f"{cert}.sig", certs / f"{sid}.cert.sig")
        git = ["git", "-C", str(registry)]
        added = run([*git, "add", "--", *paths])
        # The user's global config must not refuse or re-sign this
        # commit. The registry's OWN hooks run, and may refuse it (an owner's
        # hook can gate what enters trust); a global or system hooksPath
        # never does. The seat key signs through ssh-keygen and never a
        # configured signer program, and the env identity is dropped so the
        # author is the seat the -c names. `-c` beats GIT_CONFIG_COUNT too.
        done = added if added.returncode != 0 else run(
            [*git, "-c", f"user.name={address} (seat)", "-c", f"user.email={sid}@session",
             "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "-c", "commit.gpgsign=true",
             "-c", "gpg.ssh.program=ssh-keygen", "-c", f"core.hooksPath={own_hooks(registry)}",
             "commit", "-q", "-m", f"registry: birth of {address} seat {sid}", "--", *paths],
            drop=GIT_IDENTITY_VARS)
        if done.returncode != 0:
            said = done.stderr.strip() or done.stdout.strip() or f"git exit {done.returncode}"
            raise RuntimeError(f"the registry commit failed: {said}")
    except BaseException:
        run(["git", "-C", str(registry), "reset", "-q", "--", *paths])
        signers.write_bytes(before)
        for p in paths[1:]:
            (registry / p).unlink(missing_ok=True)
        raise
    return run(["git", "-C", str(registry), "rev-parse", "--short", "HEAD"]).stdout.strip()


def bless_command(top: Path, registry: Path, home: Path, sid: str, address: str) -> str:
    """The operator's, never run here: session-bless (claude-agents
    lib/session-keys/session-bless) signs a copy into SESSION_DIR/bless-input.sig;
    its caller copies that beside the cert and commits it."""
    script = top / "lib" / "session-keys" / "session-bless"
    script_s = shlex.quote(str(script)) if script.is_file() else \
        "<claude-agents>/lib/session-keys/session-bless"
    q = shlex.quote
    cert = registry / "certs" / f"{sid}.cert"
    blessed = f"certs/{sid}.cert.blessed-by.{BLESS_PRINCIPAL}.sig"
    return "\n".join([
        f"  LAMP_BLESS=1 BLESS_PUBKEY=\"$BLESS_PUBKEY\" BLESS_KEY_HANDLE=\"$BLESS_KEY_HANDLE\" "
        f"BLESS_ALLOWED_SIGNERS={q(str(registry / 'allowed_signers'))} \\",
        f"    {script_s} {q(str(home))} {q(str(cert))} {sid}",
        f"  cp {q(str(home / 'bless-input.sig'))} {q(str(registry / blessed))}",
        f"  git -C {q(str(registry))} add -- {q(blessed)} && git -C {q(str(registry))} commit "
        f"-m {q(f'registry: bless {address} seat {sid} ({BLESS_PRINCIPAL})')} -- {q(blessed)}",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="message seat init", description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", required=True, type=Path,
                    help="the registry dir (allowed_signers, certs/) in its git repo; no default")
    ap.add_argument("--machine", default=socket.gethostname().split(".")[0],
                    help="the cert's machine: line (default: hostname -s)")
    ap.add_argument("address", help="the seat's address, e.g. bot.alice or heard+airpods")
    args = ap.parse_args(argv)
    os.umask(0o077)
    sm_home = Path(os.environ.get("SESSIONMAIL_HOME") or "~/.sessionmail").expanduser()
    keys = sm_home / "seat-keys"
    home = keys / args.address
    registry = args.registry.resolve() if args.registry.exists() else args.registry
    try:
        check_address(args.address)
        top, namespaces = check_registry(registry)
        birth_ns, fell_back = birth_namespace(registry / "allowed_signers", namespaces)
        check_key_home(keys, home)
    except Refused as r:
        print(f"seat-init: REFUSED - {r}. Nothing was written.", file=sys.stderr)
        return EX_REFUSED
    created = [d for d in (keys, home) if not d.exists()]
    try:
        key, pub, publine = generate(home, args.address)
        sid = sid_of(publine)
        cert = birth_cert(home, key, sid, publine, args.address, args.machine,
                          birth_ns)
        commit = register(registry, cert, key, sid, publine, namespaces, args.address)
    except Exception as exc:
        for name in WRITES:                      # exactly what THIS run wrote
            (home / name).unlink(missing_ok=True)
        for d in reversed(created):              # and the dirs it made, if still empty
            try:
                d.rmdir()
            except OSError:
                pass
        print(f"seat-init: FAILED - {exc}. The key and the registry are back as they were.",
              file=sys.stderr)
        return EX_FAILED
    fingerprint = run(["ssh-keygen", "-l", "-f", str(pub)]).stdout.strip()
    print(f"seat-init: {args.address} minted, and NOT blessed (--trust says UNBLESSED, rc 4, "
          f"until it is blessed)\n"
          f"  sid         : {sid}\n"
          f"  fingerprint : {fingerprint}\n"
          f"  private key : {key}  (0600: the seat agent loads it; it never leaves this machine)\n"
          f"  public key  : {pub}  (no private key beside its stem: a dead agent refuses)\n"
          f"  cert        : {registry / 'certs' / (sid + '.cert')}  (+ .cert.sig)\n"
          f"  birth ns    : {birth_ns}"
          f"{'  (the fallback: the registry lists no ' + BIRTH_PREFIX + ')' if fell_back else ''}\n"
          f"  registry    : commit {commit} in {top}, signed by the new key\n"
          f"  the seam    : CLAUDE_SESSION_PUB={pub} and CLAUDE_SESSION_AGENT_SOCK=<the seat "
          f"agent's socket>\n"
          f"Bless (the {BLESS_PRINCIPAL} key's holder only: a hardware touch; BLESS_PUBKEY "
          f"and BLESS_KEY_HANDLE are that key's):\n"
          + bless_command(top, registry, home, sid, args.address))
    return 0


if __name__ == "__main__":
    sys.exit(main())
