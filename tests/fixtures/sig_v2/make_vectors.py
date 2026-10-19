#!/usr/bin/env python3
"""Mint every signed test vector from the inputs below, under the committed TEST key.

    TEST KEY - NOT A REAL KEY. `sessionmail-sig-TEST-VECTORS-ONLY` (and `.pub`), beside
    this file, is an ed25519 key made for these vectors alone. Its private half is
    committed ON PURPOSE, so that anyone with the repository can re-mint the vectors. It
    belongs to no seat, host or person, and nothing trusts it. The release export leaves
    the private half out (`.gitattributes`); there this script says so in one line and
    exits 1.

Command, from the repository root:

    uv run --offline python tests/fixtures/sig_v2/make_vectors.py

It writes, in place:

- `tests/fixtures/sig_v2/NAME.wire`, `NAME.canon`, `NAME.eml` for the ten v2 vectors, and
  `vectors.json` (the manifest, with the key's public half);
- the 13 signed controls in `tests/fixtures/mime_honest/controls/`: seven v1 controls
  (`sm822-ctl-*`) and six more (`sm823-*`: four v2, two v1).

`README.md` beside this file is written by hand.

A re-run writes the same bytes: an ed25519 SSH signature is deterministic (the same key
over the same bytes gives the same signature), and every input is a constant here. After a
re-run, `git status --porcelain tests/fixtures` is empty.

Every signature is made by the package's own signing path, `_sender.sign_bytes` (v2 through
`_sender.sign_wire`), with the TEST key in a throwaway ssh-agent. The script reads no
environment but PATH, and no file outside this repository: HOME is a temp directory, so no
user key, mail tree or registry is ever read.

Before anything is written, every vector is checked: canonical(wire) == canonical(eml) ==
canon, every line of a folded v2 signature header is 78 characters or fewer (a v1 header is
one line, as v1 writes it), the package's verifier says GOOD (rc 0)
on every `.eml` and control, and no MIME, CTE or disposition defect is read in any of them.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sessionmail import _mhs, _sender as X, _sig as S  # noqa: E402

assert Path(X.__file__).resolve().is_relative_to(ROOT), f"not this repo's package: {X.__file__}"

KEY_NAME = "sessionmail-sig-TEST-VECTORS-ONLY"     # TEST KEY: see the docstring
KEY, PUB = HERE / KEY_NAME, HERE / f"{KEY_NAME}.pub"
#: The one line when the private half is absent, as in the release export (.gitattributes).
NOT_SHIPPED = "this TEST key is not shipped; generate your own ed25519 key to re-mint"
NS = X.SIG_NS_DEFAULT
CONTROLS = ROOT / "tests" / "fixtures" / "mime_honest" / "controls"
VERIFIER = Path(_mhs.MHS_DIR) / "session-mail-verify"

# ── sig_v2: the ten v2 vectors ──────────────────────────────────────────────

DOM, HOST, SESSION = "hq.example.net", "hq", "0a1b2c3d"
DATE = "Tue, 01 Jan 2030 12:00:00 +0000"
RECEIVED_DATE = "Tue,  1 Jan 2030 12:00:00 +0000 (UTC)"
LEAD, WORKER = "acme+lead", "acme+t-1.worker"
PAGE = (b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>t</title></head>\n"
        b"<body><p>The decision: <strong>go</strong>.</p></body></html>\n")
MD = "The decision: **go**.\n\nSigned by the whole mail — every part.\n".encode()
ATOM = json.dumps({"type": "decision", "authority": "judy", "id": "D2",
                   "title": "sign every part", "body": "x" * 1200},
                  ensure_ascii=False).encode() + b"\n"
ATOM_HDRS = [("Atom-Type", "decision"), ("Atom-Authority", "judy"), ("Atom-Id", "D2")]
ATOM_BOUNDARY = "=_sesh890_00112233445566778899aabbccddeeff"

#: The two postfix vectors carry REAL postfix 3.2.2 re-writes (probes P3 and P5): the
#: address lines as sent, and as postfix delivered them, with only the names and hosts
#: replaced. The SHAPES are postfix's own: `J. Smith` arrives `J.Smith`, `Judy<x>` gains a
#: space, `x (a comment)` and `x(c2)` move the comment after the domain, a quoted `"Q\"uote"`
#: and an empty `""` are kept, `Uma . Lee` arrives `Uma.Lee`.
POSTFIX = {
    "postfix-reformatted": ("P3", {
        "from": '"J. Smith" <probe+from3>',
        "to": "J. Smith <probe+e>",
        "cc": 'Judy<probe+f>, probe+g (a comment), "Q\\"uote" <probe+h>',
    }, {
        b"from": b'From: "J. Smith" <probe+from3@hq.example.net>',
        b"to": b"To: J.Smith <probe+e@hq.example.net>",
        b"cc": b"Cc: Judy <probe+f@hq.example.net>,\n\tprobe+g@hq.example.net (a comment),\n"
               b'\t"Q\\"uote" <probe+h@hq.example.net>',
    }),
    "postfix-reformatted-2": ("P5", {
        "from": "<probe+from5>",
        "to": "Judy Hopper (JH) <probe+l>",
        "cc": '"" <probe+m>, probe+n(c2), Uma . Lee <probe+o>',
    }, {
        b"from": b"From: <probe+from5@hq.example.net>",
        b"to": b"To: Judy Hopper (JH) <probe+l@hq.example.net>",
        b"cc": b'Cc: "" <probe+m@hq.example.net>,\n\tprobe+n@hq.example.net (c2),\n'
               b"\tUma.Lee <probe+o@hq.example.net>",
    }),
}

DELIVERED = ("Return-Path: <{env}>\nX-Original-To: {xot}\nDelivered-To: {dt}\n"
             "Received: by " + DOM + " (Postfix, from userid 501)\n"
             "\tid 0123456789ABC; " + RECEIVED_DATE + "\n")


def req(**kw):
    """A Request as the sender's command line makes it (RFC 2047 subjects and names)."""
    r = X.Request(to=kw.pop("to"), from_=kw.pop("from_"), subject=kw.pop("subject"))
    for k, v in kw.items():
        setattr(r, k, v)
    r.subject = X.rfc2047(r.subject)
    if r.original_subject:
        r.original_subject = X.rfc2047(r.original_subject)
    r.from_, r.to = X.encode_addr_display(r.from_), X.encode_addr_display(r.to)
    return r


def built(r, body, n, refs=None, page=None, boundary=""):
    return X.build(r, body, host=HOST, date=DATE, message_id=f"<1901000000.{n}.1@{HOST}.session-mail>",
                   references=refs or [], session=SESSION, signature=None, page=page,
                   boundary=boundary)


def atom_wire(r, n, cte):
    """markdown, application/json (8bit or base64), html: hand-built over build()'s head."""
    b = ATOM_BOUNDARY
    head = built(r, b"", n).split(b"\n\n", 1)[0].decode()
    head = head.replace("Content-Type: text/plain; charset=utf-8",
                        f'Content-Type: multipart/alternative; boundary="{b}"')
    js = base64.encodebytes(ATOM) if cte == "base64" else ATOM
    part = "Content-Type: {}\nContent-Transfer-Encoding: {}\n\n"
    return (head.encode() + b"\n\n"
            + f"--{b}\n{part.format('text/plain; charset=utf-8', '8bit')}".encode() + MD
            + f"\n--{b}\n{part.format('application/json', cte)}".encode() + js
            + f"\n--{b}\n{part.format('text/html; charset=utf-8', '8bit')}".encode() + PAGE
            + f"\n--{b}--\n".encode())


def addr_lines(raw: bytes) -> dict:
    """{b'from'|b'to'|b'cc': the whole header line, folds included} of a mail's head."""
    out, cur = {}, None
    for line in raw.split(b"\n\n", 1)[0].split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if cur:
                out[cur] += b"\n" + line
            continue
        name = line.split(b":", 1)[0].lower()
        cur = name if name in (b"from", b"to", b"cc") else None
        if cur:
            out[cur] = line
    return out


def with_addr_lines(raw: bytes, new: dict) -> bytes:
    head, body = raw.split(b"\n\n", 1)
    old = addr_lines(raw)
    for k, v in new.items():
        head = head.replace(old[k], v, 1)
    return head + b"\n\n" + body


def postfix_like(raw: bytes) -> bytes:
    """What postfix does to the simple shapes: each bare addr-spec qualified with DOM, the
    list folded at `,\\n\\t`. A short host (`eve@hq`) is left alone."""
    new = {}
    for k, line in addr_lines(raw).items():
        name, value = line.split(b":", 1)
        items = [i.strip() for i in S.collapse(value).split(b", ")]
        out = []
        for i in items:
            m = re.fullmatch(rb"(.*<)([^<>@]+)(>)", i)
            if m:
                i = m.group(1) + m.group(2) + b"@" + DOM.encode() + m.group(3)
            elif re.fullmatch(rb'[^\s<>@"]+', i):
                i += b"@" + DOM.encode()
            out.append(i)
        if out != items:
            new[k] = name + b": " + b",\n\t".join(out)
    return with_addr_lines(raw, new)


def sig_v2_wires() -> dict[str, bytes]:
    v = {}
    v["single"] = built(req(from_=WORKER, to=LEAD, subject="design done"),
                        b"The design is in design.md.\n", 1)
    v["empty-body"] = built(req(from_=WORKER, to=WORKER, subject="[BOOT]"), b"", 2)
    v["two-part"] = built(req(from_=f"Acme <{LEAD}>", to="Judy <judy>",
                              subject="Sign the whole mail: go?"), MD, 3, page=PAGE,
                          boundary="=_sesh890_ffeeddccbbaa99887766554433221100")
    v["cc"] = built(req(from_=LEAD, to="ivan", subject="cc vector",
                        cc=["acmecorp+ops", f"Uma <uma@{DOM}>", "Eve <eve@hq>"]),
                    b"Three on Cc: bare, qualified, short host.\n", 4)
    v["reply"] = built(req(from_=LEAD, to="ivan", subject="Re: cc vector",
                           in_reply_to="<1901000000.4.1@hq.session-mail>",
                           supersedes="<1901000000.4.0@hq.session-mail>"),
                       b"A reply, superseding a draft.\n", 5,
                       refs=["<1900000000.1.1@hq.session-mail>", "<1900500000.2.1@hq.session-mail>",
                             "<1901000000.4.1@hq.session-mail>"])
    v["atom-headers"] = built(req(from_="Jérôme <uma>", to="acme",
                                  subject="Décision: sign every part",
                                  original_subject="Décision: sign every part, the long way round",
                                  project="acme", task="T-1", headers=ATOM_HDRS),
                              b"Atom headers on a single part.\n", 6)
    ra = req(from_="Uma <uma>", to="Judy <judy>", subject="D2: sign every part", headers=ATOM_HDRS)
    v["atom-three-part"] = atom_wire(ra, 7, "8bit")
    v["atom-three-part-b64"] = atom_wire(ra, 8, "base64")
    for n, (name, (probe, sent, _real)) in enumerate(POSTFIX.items()):
        r = X.Request(to=sent["to"], from_=sent["from"], subject=f"postfix re-formatting, real ({probe})")
        r.cc = [sent["cc"]]
        v[name] = built(r, f"Address shapes postfix re-formats, from real probe {probe}.\n".encode(),
                        9 + n)
    return v


def mint_sig_v2(env) -> tuple[dict[str, bytes], dict]:
    """{relative path: bytes} for the 30 vector files, and the manifest's `vectors`."""
    files, manifest = {}, {}
    for name, wire in sig_v2_wires().items():
        err: list[str] = []
        header = X.sign_wire(wire, {**env, "SESSIONMAIL_ORIGIN": DOM}, err)
        assert header is not None and err == [], (name, err)
        h, p = S.h_for(wire), S.digests(wire)
        canon = S.canonical(wire, NS, h, p, DOM)
        signed = S.insert_header(wire, header)
        fs = dict(S.fields(wire))
        to = S.collapse(fs["to"]).decode()
        rewritten = (with_addr_lines(signed, POSTFIX[name][2]) if name in POSTFIX
                     else postfix_like(signed))
        env_from = X.addr_spec(S.collapse(fs["from"]).decode())
        eml = DELIVERED.format(env=env_from if "@" in env_from else f"{env_from}@{DOM}",
                               xot=X.addr_spec(to.split(",")[0]),
                               dt=X.lookup_addr(to.split(",")[0], DOM)).encode() + rewritten
        assert S.canonical(eml, NS, h, p, DOM) == canon, f"{name}: canonical(eml) != canon"
        assert S.digests(eml) == p, f"{name}: the delivered digests differ"
        changed = [k.decode() for k, v in addr_lines(eml.split(b"\n", 5)[5]).items()
                   if S.collapse(v) != S.collapse(addr_lines(wire)[k])]
        manifest[name] = {"ns": NS, "origin": DOM, "h": h, "p": p, "postfix_rewrote": changed,
                          "leaves": [leaf.get_content_type() for leaf in S.leaves(wire)]}
        base = f"tests/fixtures/sig_v2/{name}"
        files[f"{base}.wire"], files[f"{base}.canon"], files[f"{base}.eml"] = wire, canon, eml
    return files, manifest


# ── the signed controls (mime_honest/controls) ──────────────────────────────

V1_DATE = "Wed, 02 Jan 2030 08:00:00 +0000"
V2_DATE = "Wed, 02 Jan 2030 11:00:00 +0000"
ITEM_DATE = "Wed, 02 Jan 2030 11:30:00 +0000"
V1_BOUNDARY = "=_sesh890_" + "0123456789abcdef" * 2
V2_BOUNDARY = "=_sesh890_" + "0" * 32
CTL_SESSION = "1a2b3c4d"

A = b"Pay Alice 100 dollars.\nThanks, Bob.\n"
E = b"Pay Alice: https://pay.example/send?amt=100&ref=BEEF\nRule: a==b holds; caf\xc3\xa9 ok.\n"
T = b"UGF5IE1hbGxvcnkgOTk5OSBkb2xsYXJzIGluc3RlYWQuCg==\n"
PAGE_A = b"<p>Pay Alice 100 dollars.</p>\n<p>Thanks, Bob.</p>\n"
PAGE_E = b'<p>Pay Alice: <a href="https://pay.example/send?amt=100&amp;ref=BEEF">link</a></p>\n'
BODY3 = ("Pay Alïce 100 €.\n\n---\n\nOnly after the audit <ref 7> passes.\n"
         "Thanks, Bob.\n").encode()
PAGE3 = ("<p>Pay Alïce 100 €.</p>\n<hr>\n<p>Only after the audit &lt;ref 7&gt; passes.<br>\n"
         "Thanks, Bob.</p>\n").encode()
RUN_BODY = b"echo SIGNED-LINE-1\n\n---\n\necho SIGNED-LINE-2\n"
RUN_PAGE = b"<p>echo SIGNED-LINE-1</p>\n<hr>\n<p>echo SIGNED-LINE-2</p>\n"
TOP_CTE = b"Content-Transfer-Encoding: 8bit\n"


def v1_honest(env, name, body, page=None, subject=None, mid=None) -> bytes:
    """An honest v1 mail: the v1 canonical form, signed, then built with its header."""
    r = SimpleNamespace(from_="alice@t3.invalid", to="bob@t3.invalid", cc=[], bcc=[],
                        subject=subject or f"T-3 v1 {name}", in_reply_to="", project="", task="",
                        original_subject="", headers=[], supersedes="")
    mid = mid or f"<{name}@t3.repro>"
    canon = X.canonical(r.from_, r.to, r.subject, V1_DATE, mid, body)
    b64 = X.sign_bytes(canon, env, NS)
    assert b64, name
    return X.build(r, body, host="t3", date=V1_DATE, message_id=mid, references=[], session="",
                   signature=S.format_v1(NS, b64), page=page,
                   boundary=V1_BOUNDARY if page is not None else "")


def set_cte(wire: bytes, new: bytes) -> bytes:
    """The same mail with its top-level CTE line replaced or removed (v1 signs no CTE, so the
    signature still holds)."""
    head, sep, body = wire.partition(b"\n\n")
    head += b"\n"
    assert head.count(TOP_CTE) == 1
    return head.replace(TOP_CTE, new)[:-1] + sep + body


def v2_signed(env, r, body, page, date, mid, refs=()) -> bytes:
    w = X.build(r, body, host="t4.invalid", date=date, message_id=mid, references=list(refs),
                session=CTL_SESSION, signature=None, page=page,
                boundary=V2_BOUNDARY if page is not None else "")
    assert not S.has_bare_address(w, S.h_for(w))
    err: list[str] = []
    header = X.sign_wire(w, env, err)
    assert header is not None and err == [], err
    return S.insert_header(w, header)


def mint_controls(env) -> dict[str, bytes]:
    out = {}

    def put(name, raw):
        out[f"tests/fixtures/mime_honest/controls/{name}.eml"] = raw

    h_a = v1_honest(env, "v1-A", A)
    put("sm822-ctl-A-8bit", h_a)
    put("sm822-ctl-A-7bit", set_cte(h_a, b"Content-Transfer-Encoding: 7bit\n"))
    put("sm822-ctl-A-noCTE", set_cte(h_a, b""))
    put("sm822-ctl-E-8bit", v1_honest(env, "v1-E", E))
    put("sm822-ctl-T-8bit", v1_honest(env, "v1-T", T))
    put("sm822-ctl-altA-8bit", v1_honest(env, "v1-altA", A, PAGE_A))
    put("sm822-ctl-altE-8bit", v1_honest(env, "v1-altE", E, PAGE_E))
    for name, page in (("single", None), ("alt", PAGE3)):
        r = X.Request(to="bob@t4.invalid", from_="alice@t4.invalid", subject=f"T-4 relay {name}")
        put(f"sm823-relay-honest-{name}",
            v2_signed(env, r, BODY3, page, V2_DATE, f"<honest-{name}@t4.invalid>"))
    for name, page in (("single", None), ("alt", PAGE3)):
        put(f"sm823-relay-honest-v1-{name}",
            v1_honest(env, f"honest-v1-{name}", BODY3, page, subject=f"T-3 v1 honest-v1-{name}",
                      mid=f"<honest-v1-{name}@t3.repro>"))
    for tag, subject, body, page in (("consumers-honest-item", "1 pay alice", BODY3, PAGE3),
                                     ("runexec-honest-alt", "1 echo SUBJECT-LINE", RUN_BODY, RUN_PAGE)):
        r = X.Request(to="alice@t4.invalid", from_="alice@t4.invalid", subject=subject)
        r.in_reply_to = "<notes@t4.invalid>"
        put(f"sm823-{tag}", v2_signed(env, r, body, page, ITEM_DATE, "<item@t4.invalid>",
                                      refs=["<notes@t4.invalid>"]))
    return out


# ── checks, then write ──────────────────────────────────────────────────────

def check(files: dict[str, bytes], tmp: Path) -> None:
    child = {"PATH": os.environ["PATH"], "HOME": str(tmp), "SESSIONMAIL_ORIGIN": DOM}
    for rel, raw in files.items():
        if not rel.endswith(".eml"):
            continue
        inside = False
        for line in raw.split(b"\n\n", 1)[0].split(b"\n"):
            if line[:1] not in (b" ", b"\t"):
                inside = line.startswith(b"X-Session-Signature: v=2;")
            assert not inside or len(line) <= 78, f"{rel}: a v2 signature line over 78"
        assert S.mime_defects(raw) == [] and S.cte_defects(raw) == [], rel
        assert S.dispo_defects(raw) == [], rel
        f = tmp / "verify-me.eml"
        f.write_bytes(raw)
        done = subprocess.run([str(VERIFIER), str(f)], capture_output=True, text=True, env=child)
        assert done.returncode == 0, f"{rel}: the verifier says {done.returncode}\n{done.stdout}"


def main() -> int:
    if not KEY.is_file():                       # the release export leaves it out
        sys.exit(f"make_vectors: {NOT_SHIPPED}")
    for tool in ("ssh-keygen", "ssh-agent", "ssh-add"):
        if shutil.which(tool) is None:
            sys.exit(f"make_vectors: {tool} is not on PATH")
    if not PUB.is_file():
        sys.exit(f"make_vectors: the TEST key's .pub is missing ({PUB}); this script never makes one")
    tmp = Path(tempfile.mkdtemp(prefix="make-vectors-"))
    sock_dir = tempfile.mkdtemp(prefix="mv-agent-", dir="/tmp")   # a short socket path
    agent_pid = None
    try:
        key = tmp / KEY_NAME                    # ssh-add wants a 0600 copy, not the checkout's
        shutil.copyfile(KEY, key)
        key.chmod(0o600)
        agent = subprocess.run(["ssh-agent", "-s", "-a", os.path.join(sock_dir, "s")],
                               check=True, capture_output=True, text=True).stdout
        sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", agent).group(1)
        agent_pid = re.search(r"SSH_AGENT_PID=(\d+);", agent).group(1)
        subprocess.run(["ssh-add", "-q", str(key)], check=True, capture_output=True,
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp), "SSH_AUTH_SOCK": sock})
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "CLAUDE_SESSION_AGENT_SOCK": sock,
               "CLAUDE_SESSION_PUB": str(PUB)}
        files, manifest = mint_sig_v2(env)
        files |= mint_controls(env)
        pub = PUB.read_text().strip()
        files["tests/fixtures/sig_v2/vectors.json"] = (
            json.dumps({"pubkey": pub, "vectors": manifest}, indent=2, ensure_ascii=False)
            + "\n").encode()
        check(files, tmp)
    finally:
        if agent_pid:
            subprocess.run(["kill", agent_pid], capture_output=True)
        shutil.rmtree(sock_dir, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    for rel, raw in sorted(files.items()):
        (ROOT / rel).write_bytes(raw)
    print(f"make_vectors: wrote {len(files)} files under the TEST key {KEY_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
