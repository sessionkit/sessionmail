"""A09 imap-new-mail-detector (SM-70): the A08 contract over IMAP IDLE.

Hermetic: a fake IMAP server on a loopback port, speaking exactly the
nine commands the detector uses, recording every command it sees so the
read-only gate is measured (EXAMINE seen, SELECT never, BODY.PEEK not
BODY[]). Live proof against Dovecot is F17; this is the regression net.
"""

from __future__ import annotations

import re
import socket
import threading
import time

import pytest

from sessionmail import _detect_imap

LONG_POLL = 5.0


class FakeImap:
    """Just enough IMAP4rev1 for the detector, one client at a time."""

    def __init__(self) -> None:
        self.messages: list[str] = []          # Message-IDs, UID = index + 1
        self.commands: list[str] = []
        self.lock = threading.Lock()
        self.conn: socket.socket | None = None
        self.idling = False
        self.exists_in_continuation = False     # the same-segment race
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _send(self, line: str) -> None:
        with self.lock:
            if self.conn:
                self.conn.sendall(line.encode() + b"\r\n")

    def deliver(self, mid: str) -> None:
        self.messages.append(mid)
        if self.idling:
            self._send(f"* {len(self.messages)} EXISTS")

    def _serve(self) -> None:
        self.conn, _ = self.srv.accept()
        f = self.conn.makefile("rb")
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
                    self._send(f"{self.idle_tag} OK IDLE terminated")
                continue
            tag, _, rest = line.partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            if verb == "CAPABILITY":
                self._send("* CAPABILITY IMAP4rev1 IDLE")
                self._send(f"{tag} OK done")
            elif verb == "LOGIN":
                self._send(f"{tag} OK logged in")
            elif verb == "EXAMINE":
                self._send(f"* {len(self.messages)} EXISTS")
                self._send("* 0 RECENT")
                self._send("* OK [UIDVALIDITY 1] ok")
                self._send(f"* OK [UIDNEXT {len(self.messages) + 1}] ok")
                self._send(f"{tag} OK [READ-ONLY] examine done")
            elif verb == "SELECT":
                self._send(f"{tag} NO the test forbids SELECT")
            elif verb == "IDLE":
                self.idle_tag = tag
                self.idling = True
                if self.exists_in_continuation:
                    # One write: continuation AND a push in the same segment.
                    self.exists_in_continuation = False
                    self._send(f"+ idling\r\n* {len(self.messages)} EXISTS")
                else:
                    self._send("+ idling")
            elif verb == "NOOP":
                self._send(f"{tag} OK noop")
            elif verb == "UID":
                sub, _, a = args.partition(" ")
                if sub.upper() == "SEARCH":
                    m = re.search(r"UID (\d+):\*", a)
                    lo = int(m.group(1))
                    n = len(self.messages)
                    uids = [u for u in range(1, n + 1) if u >= lo] or ([n] if n else [])
                    self._send("* SEARCH " + " ".join(map(str, uids)))
                    self._send(f"{tag} OK search done")
                elif sub.upper() == "FETCH":
                    ids, _, what = a.partition(" ")
                    for u in (int(x) for x in ids.split(",")):
                        hdr = f"Message-ID: <{self.messages[u - 1]}>\r\n\r\n"
                        with self.lock:
                            self.conn.sendall(
                                f"* {u} FETCH (UID {u} FLAGS (\\Recent) "
                                f"BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(hdr)}}}\r\n"
                                .encode() + hdr.encode() + b")\r\n")
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


def connect(server: FakeImap):
    return _detect_imap.connect("127.0.0.1", server.port, False, "alpha", "secret")


def later(seconds: float, fn, *args) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args)))
    t.start()
    return t


def ids(arrivals) -> list[str]:
    return [a.message_id for a in arrivals]


def test_blocks_while_quiet_and_times_out(server):
    imap = connect(server)
    with pytest.raises(_detect_imap.Timeout):
        _detect_imap.detect(imap, "INBOX", poll=0.05, timeout=0.3)


def test_one_arrival_wakes_from_the_push_not_the_cycle(server):
    server.messages = ["old-1@example", "old-2@example"]
    imap = connect(server)
    t = later(0.2, server.deliver, "new@example")
    t0 = time.monotonic()
    got = _detect_imap.detect(imap, "INBOX", poll=LONG_POLL, timeout=3.0)
    took = time.monotonic() - t0
    t.join()
    assert ids(got) == ["new@example"]
    assert took < 1.0, f"woke after {took:.2f}s: that was the cycle, not the push"


def test_residents_are_not_arrivals(server):
    """Different from A08 on purpose: UID < UIDNEXT at arm is depth."""
    server.messages = ["old@example"]
    imap = connect(server)
    with pytest.raises(_detect_imap.Timeout):
        _detect_imap.detect(imap, "INBOX", poll=0.05, timeout=0.3)


def test_a_burst_during_idle_is_one_list(server):
    imap = connect(server)

    def burst() -> None:
        for i in range(3):
            server.deliver(f"b-{i}@example")
            time.sleep(0.02)

    t = later(0.2, burst)
    got = _detect_imap.detect(imap, "INBOX", poll=LONG_POLL, timeout=3.0, settle=0.3)
    t.join()
    assert ids(got) == [f"b-{i}@example" for i in range(3)]


def test_read_only_by_construction(server):
    imap = connect(server)
    later(0.1, server.deliver, "x@example")
    _detect_imap.detect(imap, "INBOX", poll=LONG_POLL, timeout=3.0)
    verbs = [c.split(" ", 1)[1].upper() for c in server.commands if " " in c]
    assert any(v.startswith("EXAMINE") for v in verbs)
    assert not any(v.startswith("SELECT") for v in verbs)
    fetches = [v for v in verbs if v.startswith("UID FETCH")]
    assert fetches and all("BODY.PEEK[" in f for f in fetches)


def test_a_push_in_the_continuation_segment_still_wakes(server):
    """The race the module comment names: the server writes `+ idling`
    and `* N EXISTS` in ONE segment, so the push is already in imaplib's
    buffer where select() cannot see it. The drain after DONE reads it;
    it costs one poll (0.3 s here), never the mail."""
    server.messages = ["old@example"]
    server.exists_in_continuation = True
    imap = connect(server)
    later(0.05, server.deliver, "raced@example")   # lands while IDLE is being issued
    t0 = time.monotonic()
    got = _detect_imap.detect(imap, "INBOX", poll=0.3, timeout=3.0)
    assert ids(got) == ["raced@example"]
    assert time.monotonic() - t0 < 1.0


def test_password_on_argv_is_refused(capsys):
    code = _detect_imap.main(["--user", "alpha", "--password-file", "/dev/null",
                              "--password", "leak"])
    assert code == 2
    assert "never travels on argv" in capsys.readouterr().err
