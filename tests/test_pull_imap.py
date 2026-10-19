"""spike/pager-imap-idle: the IMAP puller (openspec/changes/pager-over-imap).

Hermetic: a fake IMAP server on a loopback port that takes a connection
per run, serves whole messages, and can change its UIDVALIDITY. What it
proves: an arrival lands in the local maildir's new/, whole; pulling never
marks the remote copy; a restart resumes from the cursor; nothing is
delivered twice; and the local pager hears the mirror unchanged.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sessionmail import _pull_imap, pager

QUICK = 0.2


class FakeImap:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []   # (Message-ID, From, Subject)
        self.commands: list[str] = []
        self.validity = 7
        self.lock = threading.Lock()
        self.conn: socket.socket | None = None
        self.idling = False
        # SM-150 rca-001 §3: the EVENT "the puller is in IDLE", for
        # `deliver_when_idle`. Set only after EXAMINE has answered, so an
        # arrival delivered on it is past the cursor and pushed as EXISTS.
        self.idle = threading.Event()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _send(self, line: str) -> None:
        with self.lock:
            if self.conn:
                self.conn.sendall(line.encode() + b"\r\n")

    def deliver(self, mid: str, sender: str = "ivan <ivan@alpha>",
                subject: str = "hello") -> None:
        self.messages.append((mid, sender, subject))
        if self.idling:
            self._send(f"* {len(self.messages)} EXISTS")

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.conn, self.idling = conn, False
            self.idle.clear()
            try:
                self._serve(conn)
            except OSError:
                pass
            finally:
                with self.lock:
                    self.conn = None
                conn.close()

    def _serve(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        self._send("* OK fake ready")
        while True:
            raw = f.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            self.commands.append(line)
            if self.idling:
                if line == "DONE":
                    self.idling = False
                    self.idle.clear()
                    self._send(f"{self.idle_tag} OK IDLE terminated")
                continue
            tag, _, rest = line.partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            n = len(self.messages)
            if verb == "CAPABILITY":
                self._send("* CAPABILITY IMAP4rev1 IDLE")
                self._send(f"{tag} OK done")
            elif verb == "LOGIN":
                self._send(f"{tag} OK logged in")
            elif verb == "EXAMINE":
                self._send(f"* {n} EXISTS")
                self._send(f"* OK [UIDVALIDITY {self.validity}] ok")
                self._send(f"* OK [UIDNEXT {n + 1}] ok")
                self._send(f"{tag} OK [READ-ONLY] examine done")
            elif verb == "IDLE":
                self.idle_tag, self.idling = tag, True
                self._send("+ idling")
                self.idle.set()
            elif verb == "NOOP":
                self._send(f"{tag} OK noop")
            elif verb == "UID":
                sub, _, a = args.partition(" ")
                if sub.upper() == "SEARCH":
                    lo = int(re.search(r"UID (\d+):\*", a).group(1))
                    uids = [u for u in range(1, n + 1) if u >= lo] or ([n] if n else [])
                    self._send("* SEARCH " + " ".join(map(str, uids)))
                    self._send(f"{tag} OK search done")
                elif sub.upper() == "FETCH":
                    ids, _, what = a.partition(" ")
                    for u in (int(x) for x in ids.split(",")):
                        mid, sender, subject = self.messages[u - 1]
                        hdr = f"Message-ID: <{mid}>\r\n"
                        if "FROM" in what.upper() or "BODY.PEEK[]" in what.upper():
                            hdr += f"From: {sender}\r\nSubject: {subject}\r\n"
                        hdr += "\r\n"
                        if "BODY.PEEK[]" in what.upper():
                            hdr += f"body of {mid}\r\n"
                        with self.lock:
                            conn.sendall(f"* {u} FETCH (UID {u} FLAGS () "
                                         f"BODY[HEADER] {{{len(hdr)}}}\r\n".encode()
                                         + hdr.encode() + b")\r\n")
                    self._send(f"{tag} OK fetch done")
            elif verb == "LOGOUT":
                self._send("* BYE")
                self._send(f"{tag} OK bye")
                return
            else:
                self._send(f"{tag} BAD unknown {verb}")


@pytest.fixture
def server():
    s = FakeImap()
    yield s
    s.srv.close()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    pw = tmp_path / "imap" / "alpha@127.0.0.1.pass"
    pw.parent.mkdir(parents=True)
    pw.write_text("secret\n")
    pw.chmod(0o600)
    return tmp_path


def url(server: FakeImap) -> str:
    return f"imap://alpha@127.0.0.1:{server.port}/INBOX"


def pull(server, home, timeout=3.0):
    out, err = [], []
    code = _pull_imap.run(url(server), home / "mirror", home=home, once=True,
                          poll=QUICK, reissue=60, timeout=timeout, env={},
                          out=out.append, err=err.append)
    return code, out, err


def mail(home: Path) -> list[str]:
    return sorted(p.read_text() for p in (home / "mirror" / "new").iterdir())


IDLE_WAIT = 10.0   # bound on the wait for the puller's IDLE; past every pull's timeout


def deliver_when_idle(server: FakeImap, *args) -> threading.Thread:
    """An ARRIVAL: delivered once the puller is idling, never on a clock
    (SM-150 rca-001 §3). `later(0.4, server.deliver)` raced the puller to
    its EXAMINE: delivered first, the mail is a resident (EXAMINE's
    UIDNEXT is past it) and is never pulled; delivered outside IDLE, no
    EXISTS is pushed. Bounded: a puller that never idles gets no arrival,
    and the test's own assertion fails on that."""
    def go():
        if server.idle.wait(IDLE_WAIT):
            server.deliver(*args)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


# -- the target -----------------------------------------------------------------

def test_url_defaults():
    t = _pull_imap.parse("imaps://ivan@mail.alpha")
    assert (t.user, t.host, t.port, t.tls, t.mailbox) == ("ivan", "mail.alpha", 993, True, "INBOX")
    t = _pull_imap.parse("imap://ivan@127.0.0.1:1143/Agents/ivan")
    assert (t.port, t.tls, t.mailbox) == (1143, False, "Agents/ivan")


@pytest.mark.parametrize("bad", ["imaps://ivan:hunter2@mail.alpha", "http://ivan@mail.alpha",
                                 "imaps://mail.alpha"])
def test_url_refusals(bad):
    with pytest.raises(_pull_imap.BadTarget):
        _pull_imap.parse(bad)


def test_password_file_readable_by_others_is_refused(tmp_path):
    pw = tmp_path / "p"
    pw.write_text("secret")
    pw.chmod(0o640)
    with pytest.raises(_pull_imap.BadTarget, match="only its owner"):
        _pull_imap.read_password(pw)
    pw.chmod(0o600)
    assert _pull_imap.read_password(pw) == "secret"


# -- the pull --------------------------------------------------------------------

def test_an_arrival_lands_whole_in_new_and_residents_do_not(server, home):
    server.deliver("old@alpha", subject="resident, not an arrival")
    deliver_when_idle(server, "new@alpha", "ivan <ivan@alpha>", "the mouth is clear")
    code, out, _ = pull(server, home)
    assert code == 0
    assert out == [out[0]] and out[0].startswith("pulled <new@alpha> -> new/")
    [text] = mail(home)
    assert "Subject: the mouth is clear" in text and "body of new@alpha" in text
    assert list((home / "mirror" / "tmp").iterdir()) == []


def test_a_restart_resumes_from_the_cursor_oldest_first(server, home):
    deliver_when_idle(server, "one@alpha", "ivan <ivan@alpha>", "first")
    pull(server, home)
    server.deliver("two@alpha", subject="landed while the puller was down")
    server.deliver("three@alpha", subject="and this one")
    started = time.monotonic()
    code, out, _ = pull(server, home)
    assert code == 0 and time.monotonic() - started < 1.5
    assert [o.split()[1] for o in out] == ["<two@alpha>", "<three@alpha>"]
    assert len(mail(home)) == 3


def test_nothing_is_delivered_twice_after_a_crash_before_the_cursor(server, home):
    deliver_when_idle(server, "one@alpha")
    pull(server, home)
    cpath = _pull_imap.cursor_path(_pull_imap.parse(url(server)), home)
    validity, uid = _pull_imap.read_cursor(cpath)
    _pull_imap.write_cursor(cpath, validity, uid - 1)      # as if we died after the rename
    code, out, err = pull(server, home, timeout=0.6)
    assert out == [] and len(mail(home)) == 1
    assert any("already here" in e for e in err)


def test_pulling_never_marks_the_remote_copy(server, home):
    deliver_when_idle(server, "one@alpha")
    pull(server, home)
    said = " ".join(server.commands).upper()
    assert "EXAMINE" in said and "SELECT" not in said and "STORE" not in said
    fetches = [c for c in server.commands if " FETCH " in c.upper()]
    assert fetches and all("BODY.PEEK[" in c.upper() for c in fetches)


def test_a_new_uidvalidity_drops_the_cursor_and_says_so(server, home):
    deliver_when_idle(server, "one@alpha")
    pull(server, home)
    server.validity = 8
    server.deliver("renumbered@alpha")
    code, out, err = pull(server, home, timeout=0.6)
    assert out == [] and any("cursor dropped" in e for e in err)


def test_a_quiet_once_is_not_an_error(server, home):
    assert pull(server, home, timeout=0.5)[0] == 0
    assert (home / "mirror" / "new").is_dir()


def test_cannot_connect_is_could_not_look(home):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    code = _pull_imap.run(f"imap://alpha@127.0.0.1:{port}", home / "mirror", home=home,
                          once=True, env={}, out=lambda s: None, err=lambda s: None)
    assert code == 2


# -- the point: the local pager hears the mirror unchanged ---------------------------

def test_the_local_pager_pages_on_what_was_pulled(server, home):
    deliver_when_idle(server, "far@alpha", "ivan <ivan@alpha>", "heard from afar")
    assert pull(server, home)[0] == 0
    env = {k: "" for k in os.environ if k.startswith(("CLAUDE", "SESSIONMAIL", "SESSION_"))}
    # NO rules override (SM-474 do-014, F1 (b), ruled): a mirror's mail
    # is not "to me", so the rules say ride - and a listener with no session
    # and no seat has no ride-along to carry it, so the ride is PROMOTED to
    # a page. Before (b), this test needed `SESSIONMAIL_PAGE_RULES="wake *"`
    # to pass (the do-006 reviewer's reproduction: without it, exit 124).
    env.update(SESSIONMAIL_HOME=str(home / "pagerhome"), SESSIONMAIL_PAGE_AGE_SECONDS="0",
               SESSIONMAIL_PAGE_LIST="on")
    r = CliRunner().invoke(pager.cli, ["listen", str(home / "mirror"), "--timeout", "5"], env=env)
    assert r.exit_code == 0, r.output
    assert "heard from afar" in r.output
    assert "no ride-along" in r.output, r.output


def test_a_listener_with_a_ride_queue_lets_the_same_pulled_mail_ride(server, home,
                                                                     monkeypatch):
    """Beside it: (b) cannot leak onto the seat path. The SAME pulled mail, in
    the SAME mirror, heard by a session listener (a seat, so a live ride
    queue) is NOT paged: ride means ride, and the next tool call carries it."""
    from sessionmail import _seat
    deliver_when_idle(server, "near@alpha", "ivan <ivan@alpha>", "heard up close")
    assert pull(server, home)[0] == 0
    monkeypatch.setenv("SESSIONMAIL_HOME", str(home / "pagerhome"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-q")
    monkeypatch.setenv("TM_AGENT_ADDRESS", "alpha")
    monkeypatch.setenv("SESSIONMAIL_PAGE_AGE_SECONDS", "0")
    monkeypatch.setattr(_seat, "_ps_table", lambda: ({}, {}))
    monkeypatch.setattr(_seat, "idle_seconds", lambda session, now=None: 3600.0)
    r = CliRunner().invoke(pager.cli, ["listen", str(home / "mirror"), "--timeout", "1.5"])
    assert r.exit_code == 124, (r.stdout, r.stderr)
    queue = _seat.ride_dir(_seat.resolve(), "sess-q") / _seat.QUEUE_NEW
    assert len(os.listdir(queue)) == 1


# -- SM-838: a name collision never clobbers, and retries under a fresh name -----
#
# The puller's clock is pinned so the FIRST name of a delivery is known; a
# retry name adds `R<hex>` before the `_imapV<v>U<uid>` tag. A plant in new/
# at the first name carries our own tag, so `already()` would skip the UID
# before `deliver` ran: through `pull_once` only a tmp/ collision is reachable.

from types import SimpleNamespace  # noqa: E402

from sessionmail import _maildir  # noqa: E402

T = 1_923_171_200.25
PLANT = b"PLANTED - not ours, never to be touched\n"
STUCK = "deadbeef"


def first_name(uid: int, validity: int = 7) -> str:
    host = re.sub(r"[/:]", "_", socket.gethostname()) or "localhost"
    return f"{int(T)}.M250000P{os.getpid()}_imapV{validity}U{uid}.{host}"


def retry_name(uid: int, validity: int = 7) -> str:
    return first_name(uid, validity).replace("_imapV", f"R{STUCK}_imapV")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(_pull_imap, "time", SimpleNamespace(time=lambda: T, sleep=time.sleep))


@pytest.fixture
def stuck(monkeypatch):
    monkeypatch.setattr(_maildir, "secrets", SimpleNamespace(token_hex=lambda n=None: STUCK))


@pytest.fixture
def box(tmp_path: Path) -> Path:
    root = tmp_path / "mirror"
    _pull_imap.ensure_maildir(root)
    return root


def plant(box: Path, sub: str, name: str) -> Path:
    p = box / sub / name
    p.write_bytes(PLANT)
    return p


def test_a_planted_new_file_survives_deliver_and_already_finds_the_copy(box, clock):
    """HEADLINE: before SM-838 `os.replace` overwrote the planted file."""
    planted = plant(box, "new", first_name(42))
    dest = _pull_imap.deliver(box, b"Subject: pulled\r\n\r\nbody\r\n", 7, 42)
    assert planted.read_bytes() == PLANT
    assert dest != planted and dest.parent == box / "new"
    assert dest.read_bytes() == b"Subject: pulled\r\n\r\nbody\r\n"
    assert re.fullmatch(rf"{int(T)}\.M250000P{os.getpid()}R[0-9a-f]{{8}}_imapV7U42\..+", dest.name)
    assert dest in set((box / "new").glob("*_imapV7U42.*"))   # already()'s own glob
    assert _pull_imap.already(box, 7, 42) is not None
    assert list((box / "tmp").iterdir()) == []
    assert dest.stat().st_mode & 0o777 == 0o600 and dest.stat().st_nlink == 1


def test_a_planted_tmp_file_is_retried_past_without_an_exception(box, clock):
    planted = plant(box, "tmp", first_name(42))
    dest = _pull_imap.deliver(box, b"body\r\n", 7, 42)
    assert planted.read_bytes() == PLANT
    assert list((box / "tmp").iterdir()) == [planted]
    assert list((box / "new").iterdir()) == [dest]


def test_a_first_attempt_collision_delivers_once_and_the_cursor_moves_on(server, home, clock):
    _pull_imap.ensure_maildir(home / "mirror")
    planted = plant(home / "mirror", "tmp", first_name(1))
    deliver_when_idle(server, "one@alpha")
    code, out, _ = pull(server, home)
    assert code == 0 and len(out) == 1 and len(mail(home)) == 1
    assert planted.read_bytes() == PLANT
    cpath = _pull_imap.cursor_path(_pull_imap.parse(url(server)), home)
    assert _pull_imap.read_cursor(cpath) == (7, 2)
    _pull_imap.write_cursor(cpath, 7, 1)                   # the same UID again: no double
    code, out, err = pull(server, home, timeout=0.6)
    assert out == [] and len(mail(home)) == 1
    assert any("already here" in e for e in err)


def test_a_persistent_collision_raises_as_before_and_writes_nothing(server, home, clock, stuck):
    """Three names failing is a broken box, not a collision (the ruling on
    rca-001 item 5): the error escapes, nothing is written and the cursor
    stays on the UID, so a restart delivers it once."""
    _pull_imap.ensure_maildir(home / "mirror")
    plants = [plant(home / "mirror", "tmp", first_name(1)),
              plant(home / "mirror", "tmp", retry_name(1))]
    deliver_when_idle(server, "one@alpha")
    with pytest.raises(FileExistsError):
        pull(server, home)
    assert list((home / "mirror" / "new").iterdir()) == []
    assert sorted((home / "mirror" / "tmp").iterdir()) == sorted(plants)
    assert all(p.read_bytes() == PLANT for p in plants)
    cpath = _pull_imap.cursor_path(_pull_imap.parse(url(server)), home)
    assert _pull_imap.read_cursor(cpath) == (7, 1)
