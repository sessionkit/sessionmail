"""SM-668 optimize-002: mhs-thread verifies memory mail IN-PROCESS, fail-closed.

`_sig_ok` used to fork session-mail-verify once per candidate item (637 forks
for `memory tree /ASKS --depth 1` on a large root). It now checks the one shape
every memory mail has (ssh-ed25519, sha512) with `cryptography`, and hands
EVERY other case to the subprocess. The contract: it may say good ONLY where the reference
says good.

So this file is a DIFFERENTIAL: a corpus of real signatures (made here with
ssh-keygen) and of every way one can be wrong, and for EACH mail the verdict
of `_sig_ok` is compared with `session-mail-verify FILE` rc == 0, the
reference, run as a subprocess. The reader runs on the package's Python, with
$SESSIONMAIL_PYTHON naming it, so the reference execs that same interpreter:
that is how mhs-thread runs in production since SM-878 (before it, both were
PATH's python3).

Run: uv run pytest -q tests/mhs/test_inproc_verify.py
"""
import ast
import base64
import email
import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys

import pytest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "sessionmail", "mhs")
MHS = os.path.realpath(os.path.join(HERE, "mhs-thread"))
REF = os.path.realpath(os.path.join(HERE, "session-mail-verify"))
NS = "session-mail@example.com"   # MAIL_NS_DEFAULT, with SESSION_MAIL_SIG_NS unset
V2 = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "sig_v2"
SIG = os.path.realpath(os.path.join(HERE, "..", "_sig.py"))

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen"),
                                reason="needs ssh-keygen on the PATH")


def _env():
    # SESSIONMAIL_PYTHON too (SM-878): the gate reads it, so an inherited one
    # would move the port's on/off under every test here.
    env = {k: v for k, v in os.environ.items()
           if k not in ("SESSION_MAIL_VERIFY", "SESSION_MAIL_SIG_NS", "SESSIONMAIL_PYTHON")}
    return env


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _reference_canonical():
    """The reference's canonical(), lifted from its heredoc, to SIGN with."""
    text = read(REF)
    head = "<<'PYEOF'\n"
    src = text[text.index(head) + len(head):text.index("\nPYEOF\n")]
    keep = [n for n in ast.parse(src).body
            if (isinstance(n, ast.FunctionDef) and n.name in ("local_part", "canonical"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "HDRS" for t in n.targets))]
    ns = {"sys": sys}
    exec(compile(ast.Module(body=keep, type_ignores=[]), REF, "exec"), ns)
    return ns["canonical"]


CANON = _reference_canonical()


def keygen(d, name, *args):
    path = os.path.join(d, name)
    subprocess.run(["ssh-keygen", "-q", "-N", "", "-C", name, "-f", path, *args],
                   check=True, capture_output=True)
    return path


def sign(key, data, ns=NS, *opts):
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", ns, *opts],
                          input=data, capture_output=True, check=True)
    lines = done.stdout.decode().strip().splitlines()
    return "".join(lines[1:-1])


def header(b64, ns=NS):
    return f"v=1; a=sshsig; n={ns}; h=from:to:subject:date:message-id; b={b64}"


def mail(body="the body\n", subject="[ASKS] 10 an item", extra="", ctype=None):
    head = ("From: ivan@hq\nTo: ivan+asks@hq\nSubject: %s\n"
            "Date: Sun, 09 Jun 2030 12:00:00 +0000\nMessage-ID: <x1@hq>\n%s" % (subject, extra))
    if ctype:
        head += "MIME-Version: 1.0\nContent-Type: %s\n" % ctype
    return head + "\n" + body


def signed(key, raw, ns=NS, hdr_ns=None, *opts):
    data = CANON(email.message_from_string(raw))
    b64 = sign(key, data, ns, *opts)
    return add_sig(raw, header(b64, hdr_ns or ns))


def add_sig(raw, value):
    head, body = raw.split("\n\n", 1)
    return head + "\nX-Session-Signature: " + value + "\n\n" + body


def sig_b64(raw):
    v = email.message_from_string(raw)["X-Session-Signature"]
    return v.split("b=", 1)[1].strip()


def swap_b64(raw, new):
    return raw.replace(sig_b64(raw), new)


def blob_fields(b64):
    raw = base64.b64decode(b64)
    off, out = 10, []
    while off < len(raw):
        (n,) = struct.unpack(">I", raw[off:off + 4])
        out.append(raw[off + 4:off + 4 + n])
        off += 4 + n
    return raw[:10], out


def pack(preamble, fields, tail=b""):
    return base64.b64encode(preamble + b"".join(struct.pack(">I", len(f)) + f for f in fields) + tail).decode()


def inner(blob):
    out, o = [], 0
    while o < len(blob):
        (n,) = struct.unpack(">I", blob[o:o + 4])
        out.append(blob[o + 4:o + 4 + n])
        o += 4 + n
    return out


def s_plus_l(b64):
    """The same ed25519 signature with S + L. OpenSSL refuses S >= L, and so
    does an OpenSSH linked to libcrypto (Homebrew's); OpenSSH's built-in
    ed25519 (Apple's /usr/bin/ssh-keygen) accepts it. The port must hand it
    on, so the verdict is the reference's on either build."""
    pre, (pub, ns, res, ha, sig) = blob_fields(b64)
    kind, rs = inner(sig)
    L = 2 ** 252 + 27742317777372353535851937790883648493
    s = int.from_bytes(rs[32:], "little") + L
    if s >= 2 ** 253:
        return None
    sig2 = struct.pack(">I", len(kind)) + kind + struct.pack(">I", 64) + rs[:32] + s.to_bytes(32, "little")
    return pack(pre, [pub, ns, res, ha, sig2])


# ── SSHSIG signed without ssh-keygen (review F1, F2 of optimize-002) ─────────
# ssh-keygen will not sign the namespaces the port must refuse (empty, a NUL),
# and it never produces a weak-key or malleated Ed25519 signature. Both are
# built here: the namespace edges with cryptography's Ed25519PrivateKey, the
# weak keys with RFC 8032 arithmetic in python (they are forgeries: there is
# no private key). Ported from reviews/2030-12-08T1215-optimize-002/
# ed25519_adversarial.py in the SM-668 task store.

def _sstr(b):
    return struct.pack(">I", len(b)) + b


def sshsig_message(data, ns=NS):
    """What an SSHSIG signature signs (OpenSSH's PROTOCOL.sshsig)."""
    return (b"SSHSIG" + _sstr(ns.encode("utf-8")) + _sstr(b"") + _sstr(b"sha512")
            + _sstr(hashlib.sha512(data).digest()))


def sshsig_b64(pub32, sig64, ns=NS):
    blob = (b"SSHSIG" + struct.pack(">I", 1)
            + _sstr(_sstr(b"ssh-ed25519") + _sstr(pub32)) + _sstr(ns.encode("utf-8"))
            + _sstr(b"") + _sstr(b"sha512") + _sstr(_sstr(b"ssh-ed25519") + _sstr(sig64)))
    return base64.b64encode(blob).decode()


def crypto_signed(raw, ns=NS):
    """Sign with cryptography's Ed25519PrivateKey over the SSHSIG framing,
    the header naming the same namespace the signature carries."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    sig = key.sign(sshsig_message(CANON(email.message_from_string(raw)), ns))
    return add_sig(raw, header(sshsig_b64(pub, sig, ns), ns))


class Ed:
    """Ed25519 over extended coordinates (RFC 8032 section 5.1), enough to
    forge for weak keys. dec() decodes as ref10 and OpenSSL do: y taken mod
    p, no canonical check, no x == 0 sign check."""
    p = 2 ** 255 - 19
    L = 2 ** 252 + 27742317777372353535851937790883648493
    d = (-121665 * pow(121666, 2 ** 255 - 21, 2 ** 255 - 19)) % (2 ** 255 - 19)
    SQRTM1 = pow(2, (2 ** 255 - 20) // 4, 2 ** 255 - 19)
    O = (1).to_bytes(32, "little")

    @classmethod
    def add(cls, P, Q):
        p = cls.p
        A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
        B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
        C = 2 * P[3] * Q[3] * cls.d % p
        D = 2 * P[2] * Q[2] % p
        E, F, G, H = B - A, D - C, D + C, B + A
        return (E * F % p, G * H % p, F * G % p, E * H % p)

    @classmethod
    def mul(cls, s, P):
        Q = (0, 1, 1, 0)
        while s > 0:
            if s & 1:
                Q = cls.add(Q, P)
            P = cls.add(P, P)
            s >>= 1
        return Q

    @classmethod
    def neg(cls, P):
        return ((-P[0]) % cls.p, P[1], P[2], (-P[3]) % cls.p)

    @classmethod
    def enc(cls, P):
        zi = pow(P[2], cls.p - 2, cls.p)
        x, y = P[0] * zi % cls.p, P[1] * zi % cls.p
        return (y | ((x & 1) << 255)).to_bytes(32, "little")

    @classmethod
    def dec(cls, s):
        p, v = cls.p, int.from_bytes(s, "little")
        y = (v & ((1 << 255) - 1)) % p
        x2 = (y * y - 1) * pow(cls.d * y * y + 1, p - 2, p) % p
        x = 0
        if x2:
            x = pow(x2, (p + 3) // 8, p)
            if (x * x - x2) % p:
                x = x * cls.SQRTM1 % p
            assert (x * x - x2) % p == 0, "not a point"
        if (x & 1) != v >> 255:
            x = (-x) % p
        return (x, y, 1, x * y % p)

    @classmethod
    def h(cls, R, A, M):
        return int.from_bytes(hashlib.sha512(R + A + M).digest(), "little") % cls.L

    @classmethod
    def cofactorless_ok(cls, A, R, S, M):
        """[S]B - [h]A == R, compared as encodings: the equation OpenSSL and
        OpenSSH both check (S < L aside)."""
        return cls.enc(cls.add(cls.mul(S, cls.B), cls.neg(cls.mul(cls.h(R, A, M), cls.dec(A))))) == R


Ed.B = Ed.dec((4 * pow(5, Ed.p - 2, Ed.p) % Ed.p).to_bytes(32, "little"))
Ed.T8 = Ed.dec(bytes.fromhex("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"))
Ed.TORSION = [Ed.mul(i, Ed.T8) for i in range(8)]

# Small-order public keys, in every encoding the decoders let through:
# canonical, non-canonical (y >= p) and "negative zero" (x == 0, sign bit set).
WEAK_KEYS = {
    "identity": "0100000000000000000000000000000000000000000000000000000000000000",
    "identity-negzero": "0100000000000000000000000000000000000000000000000000000000000080",
    "identity-noncanon": "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "identity-noncanon-negzero": "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "order2": "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "order2-negzero": "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "order4": "0000000000000000000000000000000000000000000000000000000000000000",
    "order4-signed": "0000000000000000000000000000000000000000000000000000000000000080",
    "order4-noncanon": "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "order4-noncanon-signed": "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "order8": "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "order8-signed": "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
    "order8-b": "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "order8-b-signed": "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
}


def weak_vectors():
    """name -> (raw mail, want). The Ed25519 inputs on which two verifiers can
    disagree. Each forgery is checked against the cofactorless equation here,
    so it is well-formed; whether a verifier ACCEPTS it is the library's
    business, which is the point: the port's `cryptography` must accept a
    subset of what the reference's ssh-keygen accepts, on whatever build this
    host has (review F2). want None: the reference decides, the port may
    decide or defer, never disagree."""
    import random
    rng = random.Random(668)
    L, B = Ed.L, Ed.B

    def rnd():
        return rng.getrandbits(512) % L

    def M_of(raw):
        return sshsig_message(CANON(email.message_from_string(raw)))

    def put(out, name, raw, A, R, S, want=None):
        out[name] = (add_sig(raw, header(sshsig_b64(A, R + S.to_bytes(32, "little")))), want)

    out = {}
    a = rnd()
    A = Ed.enc(Ed.mul(a, B))

    def honest(name, A_bytes, R_fixed=None, h_mod8_zero=False, want=None, canonical_R=True):
        for i in range(4000):
            raw = mail(body="vector %s try %d\n" % (name, i))
            M, r = M_of(raw), rnd()
            R = R_fixed if R_fixed is not None else Ed.enc(Ed.mul(r, B))
            h = Ed.h(R, A_bytes, M)
            if h_mod8_zero and h % 8:
                continue
            S = (h * a) % L if R_fixed is not None else (r + h * a) % L
            assert Ed.cofactorless_ok(A_bytes, R, S, M) == canonical_R, name
            put(out, name, raw, A_bytes, R, S, want)
            return raw, M, R, S
        raise AssertionError("could not grind " + name)

    def forge_small(name, A_bytes, S_fixed=None):
        Apt = Ed.dec(A_bytes)
        for i in range(4000):
            raw = mail(body="vector %s try %d\n" % (name, i))
            M = M_of(raw)
            s = rnd() if S_fixed is None else S_fixed
            sB = Ed.mul(s, B)
            for T in Ed.TORSION:
                R = Ed.enc(Ed.add(sB, Ed.neg(T)))
                if Ed.enc(Ed.mul(Ed.h(R, A_bytes, M), Apt)) == Ed.enc(T):
                    assert Ed.cofactorless_ok(A_bytes, R, s, M), name
                    put(out, name, raw, A_bytes, R, s)
                    return
        raise AssertionError("could not forge " + name)

    # The python signer, honest on a prime-order key: the reference says
    # good, so the signer is sound and every refusal below is the input's.
    raw, M, R, S = honest("good-python-signer", A, want="decide")
    # S + 15L: the high bits set. ref10 refuses them and OpenSSL refuses S >= L.
    put(out, "ed25519-s-plus-15l", raw, A, R, (S + 15 * L) % 2 ** 256, want="defer")
    for name, hx in WEAK_KEYS.items():
        forge_small("weak-key-" + name, bytes.fromhex(hx))
    forge_small("weak-key-identity-s0", bytes.fromhex(WEAK_KEYS["identity"]), S_fixed=0)
    # A mixed-order key, [a]B + T8: honest, and verifies cofactorless only
    # when h = 0 mod 8, so the nonce is ground until it does.
    A_mixed = Ed.enc(Ed.add(Ed.mul(a, B), Ed.T8))
    honest("weak-key-mixed-order", A_mixed, h_mod8_zero=True)
    # A small-order R on a prime-order key: R = identity, S = h*a.
    honest("weak-r-identity", A, R_fixed=Ed.O)
    # The same R written non-canonically (y = p + 1): a verifier that
    # compares the re-encoded R' byte for byte refuses it.
    honest("weak-r-identity-noncanon", A, R_fixed=bytes.fromhex(WEAK_KEYS["identity-noncanon"]),
           canonical_R=False)
    # A small-order R other than the identity: the mixed-order key, S = h*a,
    # R = -[h]T8, ground until R is consistent with its own h.
    for i in range(4000):
        raw = mail(body="vector weak-r-small-order try %d\n" % i)
        M = M_of(raw)
        hit = next((Ed.enc(T) for T in Ed.TORSION[1:]
                    if Ed.enc(Ed.neg(Ed.mul(Ed.h(Ed.enc(T), A_mixed, M), Ed.T8))) == Ed.enc(T)), None)
        if hit:
            S = (Ed.h(hit, A_mixed, M) * a) % L
            assert Ed.cofactorless_ok(A_mixed, hit, S, M)
            put(out, "weak-r-small-order", raw, A_mixed, hit, S)
            break
    else:
        raise AssertionError("could not forge weak-r-small-order")
    return out


def build_corpus(d):
    """name -> (file, what the port must do: 'decide' or 'defer' or None)."""
    a = keygen(d, "a", "-t", "ed25519")
    b = keygen(d, "b", "-t", "ed25519")
    ec = keygen(d, "ec", "-t", "ecdsa", "-b", "256")
    rsa = keygen(d, "rsa", "-t", "rsa", "-b", "2048")
    good = signed(a, mail())
    other = signed(b, mail())
    gb = sig_b64(good)
    pre, (pub, ns, res, ha, sig) = blob_fields(gb)
    _, (pub_b, *_rest) = blob_fields(sig_b64(other))
    c = {}
    # ── what the port decides itself ──
    c["good"] = (good, "decide")
    c["good-empty-body"] = (signed(a, mail(body="")), "decide")
    c["good-crlf-body"] = (signed(a, mail(body="line one\r\nline two\r\n")), "decide")
    c["good-unicode"] = (signed(a, mail(body="café — \U0001F600\n", subject="[ASKS] 11 über")), "decide")
    c["good-named-from"] = (signed(a, mail().replace("From: ivan@hq", "From: Ivan <ivan@hq>")), "decide")
    c["good-multipart-alt"] = (signed(a, mail(
        body="--XX\nContent-Type: text/plain; charset=utf-8\n\nthe markdown\n--XX\n"
             "Content-Type: text/html\n\n<p>the markdown</p>\n--XX--\n",
        ctype='multipart/alternative; boundary="XX"')), "decide")
    c["good-unsigned-header-added"] = (add_sig(mail(extra="X-Note: added later\n"),
                                               email.message_from_string(good)["X-Session-Signature"]), "decide")
    c["good-x-session-from-names-another-sid"] = (good.replace("From: ivan@hq", "X-Session-From: 00000000-0000-0000-0000-000000000000\nFrom: ivan@hq"), "decide")
    c["good-other-key-other-agent"] = (signed(b, mail().replace("ivan@hq", "eve@hq")), "decide")
    c["good-other-namespace-consistent"] = (signed(a, mail(), "other@example.com"), "decide")
    c["unsigned"] = (mail(), "decide")
    c["unsigned-empty-header"] = (mail(extra="X-Session-Signature: \n"), "decide")
    # ── tampering: the reference says BAD, the port must not say good ──
    c["tampered-body"] = (good.replace("the body", "the bodY"), "defer")
    c["tampered-subject"] = (good.replace("10 an item", "10 an itEm"), "defer")
    c["tampered-date"] = (good.replace("12:00:00", "12:00:01"), "defer")
    c["tampered-message-id"] = (good.replace("<x1@hq>", "<x2@hq>"), "defer")
    c["tampered-from"] = (good.replace("From: ivan@hq", "From: eve@hq"), "defer")
    c["tampered-to"] = (good.replace("To: ivan+asks@hq", "To: ivan+rules@hq"), "defer")
    c["wrong-namespace-header"] = (signed(a, mail(), "other@example.com", NS), "defer")
    c["wrong-namespace-signed"] = (signed(a, mail(), NS, "other@example.com"), "defer")
    c["namespace-param-missing"] = (signed(a, mail(), "x@y").replace("n=x@y; ", ""), "defer")
    c["wrong-key-spliced"] = (swap_b64(good, pack(pre, [pub_b, ns, res, ha, sig])), "defer")
    # Plain verify derives the principal (<sid>@session) FROM the embedded key,
    # so a principal/sid mismatch is only expressible as a header claiming a
    # sid the key does not hash to. Plain mode ignores that header (the
    # binding is --trust's leg 2): the reference says good, and so must the port.
    sid_a = hashlib.sha256(pub).digest()[:16].hex()
    c["good-signed-by-b-claims-a-sid"] = (other.replace(
        "From: ivan@hq", "X-Session-From: %s-%s-%s-%s-%s\nFrom: ivan@hq"
        % (sid_a[:8], sid_a[8:12], sid_a[12:16], sid_a[16:20], sid_a[20:])), "decide")
    # A signature lifted whole from ANOTHER mail onto this one: bad.
    c["signature-from-another-mail"] = (swap_b64(good, sig_b64(signed(a, mail(body="other\n")))), "defer")
    # ── armor / format ──
    c["truncated-armor"] = (swap_b64(good, gb[:-20]), "defer")
    c["truncated-armor-padded"] = (swap_b64(good, gb[:len(gb) // 4 * 4 - 8]), "defer")
    c["garbled-armor"] = (swap_b64(good, gb[:30] + "!!!!" + gb[34:]), "defer")
    # A space inside b=: textwrap breaks there and ssh-keygen skips the
    # whitespace, so the REFERENCE says good. Not the canonical encoding: deferred.
    # SM-787: b= is now read by _sig's byte parse, which removes whitespace
    # inside a value, so the port sees the canonical encoding and decides it.
    c["good-armor-with-space"] = (swap_b64(good, gb[:40] + " " + gb[40:]), "decide")
    c["armor-not-base64"] = (swap_b64(good, "not-a-signature"), "defer")
    c["armor-empty"] = (good.replace("b=" + gb, "b="), "defer")
    c["armor-b-missing"] = (good.replace("b=" + gb, ""), "defer")
    c["bad-magic"] = (swap_b64(good, pack(b"SSHSIH" + pre[6:], [pub, ns, res, ha, sig])), "defer")
    c["version-2"] = (swap_b64(good, pack(pre[:6] + b"\x00\x00\x00\x02", [pub, ns, res, ha, sig])), "defer")
    c["trailing-data"] = (swap_b64(good, pack(pre, [pub, ns, res, ha, sig], b"\x00")), "defer")
    # ssh-keygen reads the reserved field and ignores it: the REFERENCE says
    # good. The port only takes it empty, so this one is deferred.
    c["good-reserved-nonempty"] = (swap_b64(good, pack(pre, [pub, ns, b"x", ha, sig])), "defer")
    c["hash-sha256-header-only"] = (swap_b64(good, pack(pre, [pub, ns, res, b"sha256", sig])), "defer")
    c["hash-md5"] = (swap_b64(good, pack(pre, [pub, ns, res, b"md5", sig])), "defer")
    c["sig-zeroed"] = (swap_b64(good, pack(pre, [pub, ns, res, ha, sig[:-64] + b"\x00" * 64])), "defer")
    c["sig-type-renamed"] = (swap_b64(good, pack(pre, [pub, ns, res, ha, sig.replace(b"ssh-ed25519", b"ssh-ed25518")])), "defer")
    c["key-trailing"] = (swap_b64(good, pack(pre, [pub + b"\x00", ns, res, ha, sig])), "defer")
    # Set an UNUSED low bit in the last data character before the padding:
    # python's b64decode yields the same bytes, OpenSSH's decoder may refuse.
    # Which way the reference goes, the port must not decide it.
    alph = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    if gb.endswith("="):
        k = len(gb.rstrip("=")) - 1
        nc = gb[:k] + alph[alph.index(gb[k]) | 1] + gb[k + 1:]
        assert nc != gb and base64.b64decode(nc) == base64.b64decode(gb)
        c["non-canonical-base64-bits"] = (swap_b64(good, nc), "defer")
    malleated = s_plus_l(gb)
    if malleated:
        c["ed25519-s-plus-l"] = (swap_b64(good, malleated), "defer")
    # ── shapes the port does not handle: the subprocess decides, good or bad ──
    c["good-sha256"] = (signed(a, mail(), NS, None, "-O", "hashalg=sha256"), "defer")
    c["good-unsupported-ecdsa"] = (signed(ec, mail()), "defer")
    c["good-unsupported-rsa"] = (signed(rsa, mail()), "defer")
    # canonical() refuses multipart/mixed (rc 3), so sign the text the sender
    # would have: the reference can never reach ssh-keygen with it.
    mixed = mail(body="--XX\nContent-Type: text/plain\n\nhi\n--XX--\n", ctype='multipart/mixed; boundary="XX"')
    c["unsupported-multipart-mixed"] = (add_sig(mixed, header(sign(a, CANON(email.message_from_string(mail(body="hi\n")))))), "defer")
    # ── the v= dispatch (SM-594, in the reference since 2bd8a2b) ──
    # The reference reads v= BEFORE anything else: v=2 is verify_v2 over a
    # different canonical form, any v= but 1 is rc 3. The port is v1 only.
    # A v1 signature relabelled v=2 is the case that matters: without the
    # gate the port would check it as v1 and say good where the reference
    # says rc 3.
    c["good-v-absent"] = (good.replace("v=1; ", ""), "decide")
    c["good-v-spaced"] = (good.replace("v=1;", "v= 1 ;"), "decide")
    # SM-787: this was "good" (the text parse kept the last v=). A v= twice is
    # now refused by the reference (rc 1, sig-header-ambiguous); the port defers.
    c["v-twice-last-1"] = (good.replace("v=1; ", "v=2; ").replace(gb, gb + "; v=1"), "defer")
    c["v2-relabelled-v1-signature"] = (good.replace("v=1; ", "v=2; "), "defer")
    c["v2-relabelled-with-p"] = (good.replace("v=1; ", "v=2; ").replace("; b=", "; p=" + "A" * 43 + "=; b="), "defer")
    c["v-twice-last-2"] = (good.replace(gb, gb + "; v=2"), "defer")
    c["v3"] = (good.replace("v=1; ", "v=3; "), "defer")
    c["v-empty"] = (good.replace("v=1; ", "v=; "), "defer")
    # ── SM-787: one parser for the header, _sig's byte parse (EARS-335) ──
    # Each is a captured good v1 whose header a relay rewrote without the key.
    # The text parse said v1 GOOD on every one; the reference now refuses them
    # all, and the port must never decide one of them.
    wide = ("from:to:cc:subject:date:message-id:in-reply-to:references:supersedes:"
            "mime-version:content-type:x-session-from:x-mouth-device")
    good_v1 = email.message_from_string(good)["X-Session-Signature"]
    c["sm787-ears-335-nbsp"] = (good.replace(good_v1, "v=2; a=sshsig; n=%s; h=%s; b=%s; \u00a0v=1"
                                             % (NS, wide, gb)), "defer")
    c["sm787-ears-335-with-p"] = (good.replace(good_v1, "v=2; a=sshsig; n=%s; h=%s; p=%s; b=%s; \u00a0v=1"
                                               % (NS, wide, "A" * 43 + "=", gb)), "defer")
    c["sm787-v-twice-agreeing"] = (good.replace(gb, gb + "; v=1"), "defer")
    c["sm787-nbsp-between-tags"] = (good.replace("v=1; a=", "v=1;\u00a0a="), "defer")
    c["sm787-nbsp-in-v"] = (good.replace("v=1;", "v=\u00a01;"), "defer")
    c["sm787-no-equals-segment"] = (good.replace("v=1; ", "v=1; junk; "), "defer")
    # A bare CR: all ASCII, no tag twice. The text parse (universal newlines)
    # found the real v1 field first; the bytes' first field is a forged v2.
    c["sm787-bare-cr-field-split"] = (good.replace(
        "X-Session-Signature: " + good_v1,
        "X-Note: x\rX-Session-Signature: " + good_v1
        + "\nX-Session-Signature: v=2; a=sshsig; n=%s; h=%s; p=%s; b=%s" % (NS, wide, "A" * 43 + "=", gb)),
        "defer")
    # F2: more than one X-Session-Signature field.
    # The reference read the FIRST and said v1 GOOD; now it refuses the mail.
    real = "X-Session-Signature: " + good_v1
    v2_field = "X-Session-Signature: v=2; a=sshsig; n=%s; h=%s; p=%s; b=%s" % (NS, wide, "A" * 43 + "=", gb)
    c["sm787-two-fields-v1-then-v2"] = (good.replace(real, real + "\n" + v2_field), "defer")
    c["sm787-two-fields-v2-then-v1"] = (good.replace(real, v2_field + "\n" + real), "defer")
    c["sm787-two-identical-fields"] = (good.replace(real, real + "\n" + real), "defer")
    # fix-002 review: a second field hidden behind a bare CR. _sig (LF only) counts ONE;
    # Python's email parser sees two, the forged v2 FIRST. The reference refuses the bare CR.
    c["sm787-bare-cr-hidden-v2-first"] = (good.replace(real, "X-Note: x\r" + v2_field + "\n" + real), "defer")
    # ── SM-822: v1 verifies only in the exact shape the v1 sender wrote ──
    # Each is a good v1 signature (the crypto passes) on a mail a relay with no
    # key reshaped. The reference refuses each (cte-defect / v1-shape, rc 1)
    # after its crypto; the port runs the same lifted v1_refusal and defers.
    plain = "text/plain; charset=utf-8"
    c["good-sm822-sender-shape"] = (signed(a, mail(ctype=plain, extra="Content-Transfer-Encoding: 8bit\n")), "decide")
    c["good-sm822-7bit"] = (signed(a, mail(ctype=plain, extra="Content-Transfer-Encoding: 7bit\n")), "decide")
    c["good-sm822-binary"] = (signed(a, mail(ctype=plain, extra="Content-Transfer-Encoding: binary\n")), "decide")
    sender = signed(a, mail(ctype=plain, extra="Content-Transfer-Encoding: 8bit\n"))
    cte8 = "Content-Transfer-Encoding: 8bit\n"
    for name, line in (("base64", "Content-Transfer-Encoding: base64\n"),
                       ("qp", "Content-Transfer-Encoding: quoted-printable\n"),
                       ("folded", "Content-Transfer-Encoding:\n base64\n"),
                       ("comment", "Content-Transfer-Encoding: base64 (c)\n"),
                       ("second-after", cte8 + "Content-Transfer-Encoding: base64\n"),
                       ("second-before", "Content-Transfer-Encoding: base64\n" + cte8)):
        c["sm822-cte-" + name] = (sender.replace(cte8, line, 1), "defer")
    alt = signed(a, mail(
        body="--XX\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n"
             "the markdown\n--XX\nContent-Type: text/html; charset=utf-8\n"
             "Content-Transfer-Encoding: 8bit\n\n<p>the markdown</p>\n--XX--\n",
        ctype='multipart/alternative; boundary="XX"', extra=cte8))
    c["good-sm822-alternative"] = (alt, "decide")
    c["sm822-alt-container-b64"] = (alt.replace(cte8, "Content-Transfer-Encoding: base64\n", 1), "defer")
    c["sm822-alt-leaf-qp"] = (alt.replace("charset=utf-8\n" + cte8 + "\nthe",
                                          "charset=utf-8\nContent-Transfer-Encoding: quoted-printable\n\nthe"), "defer")
    c["sm822-alt-html-space-colon"] = (alt.replace(cte8 + "\n<p>", cte8 + "Content-Transfer-Encoding : base64\n\n<p>"), "defer")
    c["sm822-alt-append"] = (alt.replace("</p>\n--XX--", "</p>\n--XX\nContent-Type: text/plain\n\nmore\n--XX--"), "defer")
    c["sm822-alt-rewrap"] = (alt.replace("text/html", "text/plain"), "defer")
    c["sm822-charset-latin1"] = (sender.replace("charset=utf-8", "charset=iso-8859-1"), "defer")
    c["sm822-single-text-html"] = (sender.replace("text/plain", "text/html"), "defer")
    c["sm822-bare-cr-body"] = (signed(a, mail(body="one\ntwo\n")).replace("one\ntwo", "one\rtwo"), "defer")
    # ── SM-831: a Content-Disposition no signature covers ──
    # A good v1 signature on a mail a relay with no key gave a CD: on the top
    # block of a single part, on the alternative's container, on its text leaf.
    # The reference refuses each (v1-shape, rc 1) through the lifted v1_refusal,
    # which calls _sig.v1_defects; the port defers, as it does for SM-822.
    cda = "Content-Disposition: attachment\n"
    c["sm831-v1-single-top-cd-attach"] = (sender.replace(cte8, cte8 + cda, 1), "defer")
    c["sm831-v1-alt-top-cd-attach"] = (alt.replace(cte8, cte8 + cda, 1), "defer")
    c["sm831-v1-alt-plain-cd-attach"] = (alt.replace("charset=utf-8\n" + cte8 + "\nthe",
                                                     "charset=utf-8\n" + cte8 + cda + "\nthe"), "defer")
    # ── SM-832: v1 signs the FIRST instance of each covered header ──
    # A good v1 signature on a mail a relay with no key gave a second Subject, To
    # or Date AFTER the signed one (notmuch shows the last Subject and Date and
    # joins every To). The reference refuses each (v1-shape, rc 1) through the
    # lifted v1_refusal, which calls _sig.v1_defects; the port defers.
    c["sm832-subject-after"] = (sender.replace("Subject: [ASKS] 10 an item\n",
                                               "Subject: [ASKS] 10 an item\nSubject: [ASKS] 99 EVIL\n", 1), "defer")
    c["sm832-to-flip-after"] = (sender.replace("To: ivan+asks@hq\n",
                                               "To: ivan+asks@hq\nTo: bob+asks@example.net\n", 1), "defer")
    c["sm832-date-after"] = (sender.replace("Date: Sun, 09 Jun 2030 12:00:00 +0000\n",
                                            "Date: Sun, 09 Jun 2030 12:00:00 +0000\n"
                                            "Date: Sun, 09 Jun 2030 12:34:56 +0000\n", 1), "defer")
    # ── namespaces ssh-keygen will not sign (review F1 of optimize-002) ──
    # Signed with cryptography, over the namespace the header names, so only
    # the namespace differs from a good mail. The reference refuses both:
    # `ssh-keygen -n ''` exits 1, and a NUL in argv raises in its
    # subprocess.run. The port's `not ns` and NUL guards are all that stop
    # it saying good; these two cases pin them.
    c["good-cryptography-signer"] = (crypto_signed(mail()), "decide")
    c["ns-empty"] = (crypto_signed(mail(), ""), "defer")
    c["ns-nul"] = (crypto_signed(mail(), "a\x00b"), "defer")
    # ── weak keys and malleated signatures (review F2 of optimize-002) ──
    c.update(weak_vectors())
    files = {}
    # Real v2 mail, as the sender signs it by default now (SM-594's golden
    # vectors, delivered form): the reference says good, the port defers.
    for vec in ("single", "two-part", "empty-body"):
        raw_v2 = (V2 / ("%s.eml" % vec)).read_bytes()
        for name, data in (("good-v2-" + vec, raw_v2),
                           ("v2-tampered-" + vec, raw_v2.replace(b"Subject: ", b"Subject: X", 1))):
            path = os.path.join(d, name + ".eml")
            with open(path, "wb") as fh:
                fh.write(data)
            files[name] = (path, "defer")
    for name, (raw, want) in c.items():
        path = os.path.join(d, name + ".eml")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(raw)
        files[name] = (path, want)
    bad = os.path.join(d, "not-utf8.eml")
    with open(bad, "wb") as fh:
        fh.write(good.replace("the body", "the b\xffody").encode("latin-1"))
    files["not-utf8"] = (bad, "defer")
    files["missing-file"] = (os.path.join(d, "nope.missing"), None)
    return files


HELPER = r'''
import builtins, importlib.machinery, importlib.util, json, sys
mhs, block, files = sys.argv[1], sys.argv[2] == "block", sys.argv[3:]
if block:   # cryptography unimportable, simulated
    real = builtins.__import__
    def guard(name, *a, **k):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("blocked by the test")
        return real(name, *a, **k)
    builtins.__import__ = guard
loader = importlib.machinery.SourceFileLoader("mhs_thread", mhs)
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
calls = []
real_run = mod.subprocess.run
def run(argv, *a, **k):
    calls.append(argv[-1])
    return real_run(argv, *a, **k)
mod.subprocess.run = run
out = {"port": mod._port() is not None, "v": {}}
for fn in files:
    inproc = mod._sig_inproc(fn)
    out["v"][fn] = [inproc, mod._sig_ok({"filename": [fn]}) if fn.endswith(".eml") else None]
out["forked"] = calls
print(json.dumps(out))
'''


def reader(d, files, block=False, env=None, mhs=MHS):
    helper = os.path.join(d, "helper.py")
    with open(helper, "w") as fh:
        fh.write(HELPER)
    # As production runs it (SM-878): on the package's Python, and naming it in
    # $SESSIONMAIL_PYTHON, as mhs-thread does for itself as a script.
    done = subprocess.run([sys.executable, helper, mhs, "block" if block else "-", *files],
                          capture_output=True, text=True,
                          env={**(env or _env()), "SESSIONMAIL_PYTHON": sys.executable})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def reference(fn):
    # The reference as mhs-thread starts it: $SESSIONMAIL_PYTHON is the reader's Python.
    return subprocess.run([REF, fn], capture_output=True,
                          env={**_env(), "SESSIONMAIL_PYTHON": sys.executable}).returncode


def has_crypto():
    return subprocess.run([sys.executable, "-c", "import cryptography"],
                          capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("sm668"))
    files = build_corpus(d)
    rcs = {name: reference(path) for name, (path, _) in files.items()}
    return d, files, rcs


def test_the_corpus_means_what_it_says(corpus):
    """The reference's own verdicts on the corpus: every good is rc 0, every
    tamper is not. If this fails the corpus is wrong, not the port."""
    _, files, rcs = corpus
    for name in files:
        if name.startswith("good"):
            assert rcs[name] == 0, name
        elif name.startswith("unsigned"):
            assert rcs[name] == 2, name
        elif name.startswith("weak-"):
            # forgeries on weak keys: whether the reference takes them is the
            # build's ssh-keygen's to say (both on this host did, 2030-12-08);
            # the differential only asks the port to agree or defer
            continue
        elif name not in ("ed25519-s-plus-l", "non-canonical-base64-bits"):
            # those two depend on the OpenSSH build (OpenSSH 10.5p1 refuses
            # both); the port defers them, so either answer is the reference's
            assert rcs[name] != 0, (name, rcs[name])


def test_differential_every_case_equals_the_reference(corpus):
    d, files, rcs = corpus
    got = reader(d, [p for p, _ in files.values()])
    if has_crypto():
        assert got["port"], "the in-process verify is OFF with the repo's own reference"
    for name, (path, want) in files.items():
        inproc, ok = got["v"][path]
        ref_ok = rcs[name] == 0
        # fail-closed: the port's own verdicts are a SUBSET of the reference's
        assert inproc is None or inproc == ref_ok, (name, inproc, rcs[name])
        if path.endswith(".eml"):
            assert ok == ref_ok, (name, ok, rcs[name])
        if not got["port"]:
            assert inproc is None
        elif want == "decide":
            assert inproc is not None, name     # the fast path really runs
        elif want == "defer":
            assert inproc is None, name         # and hands everything else on


def test_the_fast_path_forks_nothing_and_the_rest_fork_once_each(corpus):
    d, files, _ = corpus
    if not has_crypto():
        pytest.skip("cryptography not importable under the package's Python")
    paths = {n: p for n, (p, _) in files.items() if p.endswith(".eml")}
    got = reader(d, list(paths.values()))
    forked = set(got["forked"])
    for name, path in paths.items():
        want = files[name][1]
        if want is None:    # the reference's to say: forked once iff deferred
            want = "defer" if got["v"][path][0] is None else "decide"
        if want == "decide":
            assert path not in forked, name
        else:
            assert got["forked"].count(path) == 1, name


def test_the_corpus_carries_the_adversarial_vectors(corpus):
    """Review F1 and F2 of optimize-002: the cases that pin the namespace
    guards and the OpenSSL-within-OpenSSH subset. A builder that silently
    dropped one would leave its guard unpinned again."""
    _, files, _ = corpus
    want = {"ns-empty", "ns-nul", "good-cryptography-signer", "good-python-signer",
            "ed25519-s-plus-15l", "weak-key-identity-s0", "weak-key-mixed-order",
            "weak-r-identity", "weak-r-identity-noncanon", "weak-r-small-order"}
    want |= {"weak-key-" + k for k in WEAK_KEYS}
    assert want <= set(files), sorted(want - set(files))


def test_without_cryptography_every_verdict_comes_from_the_subprocess(corpus):
    d, files, rcs = corpus
    paths = [p for p, _ in files.values() if p.endswith(".eml")]
    got = reader(d, paths, block=True)
    assert got["port"] is False
    assert sorted(got["forked"]) == sorted(paths)
    for name, (path, _) in files.items():
        if path.endswith(".eml"):
            inproc, ok = got["v"][path]
            assert inproc is None and ok == (rcs[name] == 0), name


def test_a_named_verifier_is_never_second_guessed(corpus, tmp_path):
    """SESSION_MAIL_VERIFY (the tests' stub) is THE verifier: no port."""
    d, files, _ = corpus
    stub = tmp_path / "stub"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    env = {**_env(), "SESSION_MAIL_VERIFY": str(stub)}
    good = files["good"][0]
    got = reader(d, [good], env=env)
    assert got["port"] is False
    assert got["v"][good] == [None, False]      # the stub's answer, not the crypto's
    assert got["forked"] == [good]


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on this machine")
def test_the_gate_follows_the_wrappers_interpreter_rule(corpus, tmp_path):
    """SM-878: the reference runs on `${SESSIONMAIL_PYTHON:-python3}`, and the
    gate states the same rule. Here PATH's python3 is NOT this interpreter
    (a stub, as the system 3.9 is on a stock macOS):
      - unset, the reference would run PATH's: the port is off;
      - naming this interpreter, as the package sets it for every script
        it starts: the port is ON, and a good mail forks nothing;
      - naming a path that is not there: the reference cannot run, so off.
    Run on the PACKAGE's interpreter, which has `cryptography` (a dependency)."""
    d, files, _ = corpus
    good = files["good"][0]
    other = tmp_path / "other"
    other.mkdir()
    (other / "python3").write_text("#!/bin/sh\nexit 97\n")
    (other / "python3").chmod(0o755)
    base = {**_env(), "PATH": os.pathsep.join(
        [str(other), os.path.dirname(shutil.which("ssh-keygen")), "/usr/bin", "/bin"])}
    helper = os.path.join(str(tmp_path), "helper.py")
    with open(helper, "w") as fh:
        fh.write(HELPER)

    def gate(**extra):
        done = subprocess.run([sys.executable, helper, MHS, "-", good], capture_output=True,
                              text=True, env={**base, **extra})
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    assert gate()["port"] is False
    on = gate(SESSIONMAIL_PYTHON=sys.executable)
    assert on["port"] is True
    assert on["v"][good] == [True, True] and on["forked"] == []
    assert gate(SESSIONMAIL_PYTHON=str(tmp_path / "gone" / "python3"))["port"] is False


def test_the_pin_is_the_reference_in_this_tree():
    """VERIFY_PORT_PIN is the sha256 of the WHOLE session-mail-verify file:
    its python heredoc AND the bash wrapper that execs it.
    IF THIS FAILS, session-mail-verify changed and the in-process port in
    mhs-thread (SM-668) is OFF: every verify forks again. Re-read the port
    against the new reference (the wrapper, the v= dispatch, canonical, the
    header parse, what reaches ssh-keygen), re-run this differential, and
    only then re-pin."""
    with open(REF, "rb") as fh:
        want = hashlib.sha256(fh.read()).hexdigest()
    pin = [ln for ln in read(MHS).splitlines(True) if ln.startswith("VERIFY_PORT_PIN = ")]
    assert pin == ['VERIFY_PORT_PIN = "%s"\n' % want]


def test_a_changed_reference_turns_the_port_off(corpus, tmp_path):
    """Even a comment: a trust port runs only against the text it was proven on."""
    d, files, _ = corpus
    mhs_dir = tmp_path / "mhs"
    shutil.copytree(HERE, mhs_dir, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(SIG, tmp_path / "_sig.py")   # an install: the reference needs it (SM-787)
    ref = mhs_dir / "session-mail-verify"
    ref.write_text(ref.read_text().replace("\ndef canonical(msg)", "\n# a comment\ndef canonical(msg)", 1))
    good = files["good"][0]
    got = reader(d, [good], mhs=str(mhs_dir / "mhs-thread"))
    assert got["port"] is False and got["v"][good][0] is None
    assert got["v"][good][1] is True and got["forked"] == [good]


def test_a_changed_wrapper_turns_the_port_off(corpus, tmp_path):
    """The bash in front of the heredoc picks the interpreter and the
    environment the reference runs in: a change there is a change to the
    reference, and the port goes off with it."""
    d, files, _ = corpus
    mhs_dir = tmp_path / "mhs"
    shutil.copytree(HERE, mhs_dir, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(SIG, tmp_path / "_sig.py")   # an install: the reference needs it (SM-787)
    ref = mhs_dir / "session-mail-verify"
    text = ref.read_text()
    head = text.index("<<'PYEOF'\n")
    ref.write_text(text.replace("\n#", "\n# wrapper edit\n#", 1))
    assert ref.read_text()[ref.read_text().index("<<'PYEOF'\n"):] == text[head:]
    good = files["good"][0]
    got = reader(d, [good], mhs=str(mhs_dir / "mhs-thread"))
    assert got["port"] is False and got["v"][good][0] is None
    assert got["v"][good][1] is True and got["forked"] == [good]


def test_without_sig_py_the_port_is_off(corpus, tmp_path):
    """SM-787: the reference reads every header with _sig's byte parse, so an
    install with no _sig.py beside mhs/ refuses every mail. The port loads
    the same _sig.py; without it the port is off, and never says good."""
    d, files, _ = corpus
    mhs_dir = tmp_path / "mhs"
    shutil.copytree(HERE, mhs_dir, ignore=shutil.ignore_patterns("__pycache__"))
    good = files["good"][0]
    got = reader(d, [good], mhs=str(mhs_dir / "mhs-thread"))
    assert got["port"] is False and got["v"][good] == [None, False]
    assert got["forked"] == [good]


def test_the_sm787_vectors_are_in_the_corpus(corpus):
    """A builder that dropped one would leave the one-parser rule unpinned here."""
    _, files, _ = corpus
    want = {"sm787-ears-335-nbsp", "sm787-ears-335-with-p", "sm787-v-twice-agreeing",
            "sm787-nbsp-between-tags", "sm787-nbsp-in-v", "sm787-no-equals-segment",
            "sm787-bare-cr-field-split", "v-twice-last-1", "not-utf8",
            "sm787-two-fields-v1-then-v2", "sm787-two-fields-v2-then-v1",
            "sm787-two-identical-fields", "sm787-bare-cr-hidden-v2-first"}
    assert want <= set(files), sorted(want - set(files))


def test_the_sm822_vectors_are_in_the_corpus(corpus):
    """A builder that dropped one would leave v1_refusal's lift unpinned here."""
    _, files, _ = corpus
    want = {"good-sm822-sender-shape", "good-sm822-7bit", "good-sm822-binary",
            "good-sm822-alternative", "sm822-cte-base64", "sm822-cte-qp", "sm822-cte-folded",
            "sm822-cte-comment", "sm822-cte-second-after", "sm822-cte-second-before",
            "sm822-alt-container-b64", "sm822-alt-leaf-qp", "sm822-alt-html-space-colon",
            "sm822-alt-append", "sm822-alt-rewrap", "sm822-charset-latin1",
            "sm822-single-text-html", "sm822-bare-cr-body"}
    assert want <= set(files), sorted(want - set(files))


def test_the_sm822_reshapes_have_a_good_signature(corpus):
    """Each sm822- vector is refused for its SHAPE: its v1 crypto is good. The reference's
    --json names the reason, and it is never bad-signature."""
    _, files, _ = corpus
    for name, (path, _) in files.items():
        if name.startswith("sm822-"):
            done = subprocess.run([REF, path, "--json"], capture_output=True, env=_env())
            reason = json.loads(done.stdout)["reason"]
            assert (done.returncode, reason in ("cte-defect", "v1-shape")) == (1, True), (name, reason)


def test_the_sm831_vectors_are_refused_as_v1_shape(corpus):
    """Each sm831- vector has a good v1 signature and a Content-Disposition: the reference
    refuses it as v1-shape, naming the part, and the port defers to it (the differential)."""
    _, files, _ = corpus
    got = {n: p for n, (p, _) in files.items() if n.startswith("sm831-")}
    assert sorted(got) == ["sm831-v1-alt-plain-cd-attach", "sm831-v1-alt-top-cd-attach",
                           "sm831-v1-single-top-cd-attach"]
    for name, path in got.items():
        done = subprocess.run([REF, path, "--json"], capture_output=True, env=_env())
        assert (done.returncode, json.loads(done.stdout)["reason"]) == (1, "v1-shape"), name
        plain = subprocess.run([REF, path], capture_output=True, text=True, env=_env())
        assert "1 Content-Disposition field, which no signature covers" in plain.stdout, name


def test_the_sm832_vectors_are_in_the_corpus(corpus):
    """A builder that dropped one would leave the duplicate-header rule unpinned here."""
    _, files, _ = corpus
    want = {"sm832-subject-after", "sm832-to-flip-after", "sm832-date-after"}
    assert want <= set(files), sorted(want - set(files))


def test_the_sm832_vectors_are_refused_as_v1_shape(corpus):
    """Each sm832- vector has a good v1 signature and one covered header twice: the
    reference refuses it as v1-shape, naming the header, and the port defers to it (the
    differential). Each one's crypto is good: the second instance comes after the first."""
    _, files, _ = corpus
    got = {n: p for n, (p, _) in files.items() if n.startswith("sm832-")}
    assert sorted(got) == ["sm832-date-after", "sm832-subject-after", "sm832-to-flip-after"]
    for name, path in got.items():
        done = subprocess.run([REF, path, "--json"], capture_output=True, env=_env())
        assert (done.returncode, json.loads(done.stdout)["reason"]) == (1, "v1-shape"), name
        plain = subprocess.run([REF, path], capture_output=True, text=True, env=_env())
        assert "signature-crypto  : ✅ good" in plain.stdout, name
        header = name.split("-")[1]
        assert f"the mail: 2 {header} fields: v1 signs the first only" in plain.stdout, name


def test_scope_only_mhs_thread_reads_the_port():
    """session-mail-verify, message verify and the pager keep their own path."""
    src = os.path.join(HERE, "..")
    users = []
    for root, _, names in os.walk(src):
        for n in names:
            p = os.path.join(root, n)
            if "__pycache__" in p:
                continue
            try:
                t = read(p)
            except (UnicodeDecodeError, OSError):
                continue
            if "_sig_inproc" in t or "VERIFY_PORT_PIN" in t:
                users.append(os.path.relpath(p, src))
    assert users == [os.path.join("mhs", "mhs-thread")]
