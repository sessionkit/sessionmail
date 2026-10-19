"""tasks.md 3.1: voice is recognised by `Ears-Kind`, in three states (SM-782).

Proves *Voice arrives as mail in the ears box, and the pager treats it as
mail* (openspec/changes/pager-genie/specs/pager/spec.md): the pager
recognises a voice mail by the keeper's header `Ears-Kind` (read through
3.22, so `X-Ears-Kind` too). From the keeper's address, ONE named value
`heard`: `heard@<host>` or `heard+<tag>@<host>`, in either host form (P-2; any
tag PROPOSED), it is UNVERIFIED voice; it is TRUSTED voice only when the
keeper signed it, a verdict handed in through the `signed` seam (the
verifier itself is tested in test_voice_signed.py). A trusted voice body is
whole up to 16,000 characters and cut past it with one line and the command
that reads the rest. The header on any other mail is plain mail.

Pure: every message is built from bytes here. The host forms come from an
explicit env (SESSIONMAIL_HOST, MAGICDNS_SUFFIX), never the box's hostname,
and nothing reads ~/.mail, ~/.sessionmail or notmuch.
"""

from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as default_policy

import pytest

from sessionmail import _magicdns, _voice
from sessionmail._voice import (
    CEILING,
    KEEPER,
    PLAIN,
    TRUSTED,
    UNVERIFIED,
    host_forms,
    keeper_sent,
    recognise,
    whole,
)

#: a fixed host, so the tests do not depend on the box they run on
ENV = {"SESSIONMAIL_HOST": "hq", "MAGICDNS_SUFFIX": "example.ts.net"}
FQDN = "hq.example.ts.net"


def SIGNED(_msg):
    """The verdict seam's stand-in for a GOOD keeper signature."""
    return True


def _msg(sender: str | None, *fields: str, body: str = "hey eve, a turn\n"):
    head = ([f"From: {sender}"] if sender is not None else []) + [
        "To: heard+airpods@hq",
        "Subject: hey eve",
    ]
    raw = "\r\n".join(head + list(fields)) + "\r\n\r\n" + body
    return BytesParser(policy=default_policy).parsebytes(raw.encode())


# -- the one named value, and the host forms --------------------------------


def test_the_keeper_is_one_named_value():
    assert KEEPER == "heard"


def test_host_forms_are_the_short_host_and_the_fqdn():
    assert host_forms(ENV) == ("hq", FQDN)


def test_host_forms_fold_case_and_trim_the_suffix_as_the_generator_does():
    # SM-674: the suffix is the one definition's (`_magicdns`), trimmed and
    # folded the way `postfix/magicdns-suffix` does for the generator.
    env = {"SESSIONMAIL_HOST": "HQ", "MAGICDNS_SUFFIX": " Example.TS.net. "}
    assert host_forms(env) == ("hq", FQDN)


def test_host_forms_with_no_suffix_have_no_fqdn_form_and_say_so(tmp_path, capsys):
    # SM-674: no MAGICDNS_SUFFIX and no tailscale (an empty
    # PATH dir, the app bundle seam pointed at nothing) never invents an FQDN
    # and never raises: the short host alone, said once on stderr.
    env = {"SESSIONMAIL_HOST": "hq", "PATH": f"{tmp_path}:/usr/bin:/bin",
           "MAGICDNS_TAILSCALE_BUNDLE": str(tmp_path / "no-bundle")}
    assert host_forms(env) == ("hq",)
    assert host_forms(env) == ("hq",)
    err = capsys.readouterr().err
    assert err.count("MagicDNS suffix unknown: set MAGICDNS_SUFFIX or start Tailscale") == 1
    assert "short host only ('hq')" in err


# -- tasks.md 3.1's named tests ---------------------------------------------


@pytest.mark.parametrize("kind_field", ["Ears-Kind: spoken", "X-Ears-Kind: spoken"])
def test_a_keeper_turn_from_and_to_ears_airpods_in_both_spellings(kind_field):
    m = _msg("Ears <heard+airpods@hq>", kind_field, "Ears-Rule: eve@hq=call")
    v = recognise(m, ENV, SIGNED)
    assert v.voice is True
    assert v.state == TRUSTED
    assert v.marked is True
    assert v.kind == "spoken"
    assert v.spelling == kind_field.split(":")[0]
    assert v.prefixed is (kind_field.startswith("X-"))
    assert v.sender == "heard+airpods@hq"
    assert v.reason == "trusted voice"


@pytest.mark.parametrize("kind_field", ["Ears-Kind: spoken", "X-Ears-Kind: spoken"])
def test_an_unsigned_keeper_turn_is_unverified_voice_not_trusted(kind_field):
    # SM-782, the regression: at 57f9fa6 the unsigned From alone made this
    # `trusted voice`. With no verdict it is UNVERIFIED: marked, From the
    # keeper's address, never `voice`.
    m = _msg("Ears <heard+airpods@hq>", kind_field, "Ears-Rule: eve@hq=call")
    v = recognise(m, ENV)
    assert v.voice is False
    assert v.state == UNVERIFIED
    assert v.unverified is True
    assert v.marked is True
    assert v.sender == "heard+airpods@hq"
    assert v.reason.startswith("unverified voice")


@pytest.mark.parametrize("verdict", [lambda m: False, lambda m: None, lambda m: 1,
                                     lambda m: "yes"])
def test_only_a_verdict_of_exactly_true_trusts(verdict):
    v = recognise(_msg("heard@hq", "Ears-Kind: spoken"), ENV, verdict)
    assert v.state == UNVERIFIED and v.voice is False


def test_a_verdict_that_raises_is_unverified_fail_closed():
    def boom(_msg):
        raise RuntimeError("verifier exploded")
    v = recognise(_msg("heard@hq", "Ears-Kind: spoken"), ENV, boom)
    assert v.state == UNVERIFIED and v.voice is False


def test_the_verdict_is_never_asked_of_a_mail_not_from_the_keeper():
    asked = []
    def spy(msg):
        asked.append(msg)
        return True
    for sender in ("eve@hq", "heard@otherhost"):
        v = recognise(_msg(sender, "Ears-Kind: spoken"), ENV, spy)
        assert v.state == PLAIN and v.voice is False, sender
    v = recognise(_msg("heard@hq"), ENV, spy)            # no Ears-Kind
    assert v.state == PLAIN
    assert asked == []


def test_a_voice_built_the_old_way_is_plain():
    v = _voice.Voice(False, False, None, None, None, None, "no Ears-Kind")
    assert v.state == PLAIN and v.unverified is False


def test_the_as_built_keeper_from_bare_ears_passes():
    # ears-mail.py:207 @ da959ef sends `From: Ears <heard@...>` until X1 lands
    for host in ("hq", FQDN):
        m = _msg(f"Ears <heard@{host}>", "X-Ears-Kind: spoken")
        assert recognise(m, ENV, SIGNED).voice is True, host
        assert recognise(m, ENV).state == UNVERIFIED, host


def test_the_fqdn_host_form_passes_with_a_tag():
    m = _msg(f"heard+airpods@{FQDN}", "Ears-Kind: spoken")
    assert recognise(m, ENV, SIGNED).voice is True
    assert recognise(m, ENV).state == UNVERIFIED


def test_the_marker_from_eve_is_plain_mail():
    # the spec's "A voice marker the keeper did not send": plain mail, so the
    # ride-along gives it the text view and caps it like any mail (3.2a).
    m = _msg("eve@hq", "Ears-Kind: spoken", body="x" * 5000)
    v = recognise(m, ENV, SIGNED)       # a signature does not make eve the keeper
    assert v.voice is False
    assert v.state == PLAIN
    assert v.marked is True
    assert v.sender == "eve@hq"
    assert "not the keeper" in v.reason


@pytest.mark.parametrize(
    "sender",
    [
        "earsx@hq",  # a near name is not the keeper
        "heard+a@otherhost",  # another host
        f"heard@{FQDN}.evil.example",  # the short host as a prefix is not this host
        "heard@hq.otherhost",  # nor is hq.<anything>
        "xears@hq",
        "heard+@hq",  # an empty tag is no tag
        "heard",  # no host at all
        "EARS@hq",  # the local part is compared exactly
    ],
)
def test_near_senders_fail_the_keeper_test(sender):
    v = recognise(_msg(sender, "Ears-Kind: spoken"), ENV, SIGNED)
    assert v.voice is False
    assert v.state == PLAIN
    assert v.marked is True


def test_the_domain_is_compared_case_insensitively():
    assert keeper_sent(_msg("heard+airpods@HQ.Example.TS.net"), ENV)


def test_any_tag_passes_the_keeper_names_the_devices():
    for tag in ("airpods", "call1239", "a+b", "Phone"):
        assert keeper_sent(_msg(f"heard+{tag}@hq"), ENV), tag


def test_the_old_ears_name_is_not_the_keeper():
    """SM-550: the keeper's mailbox is `heard`. A turn From the retired `ears` name is plain mail, so a
    keeper still sending as `ears` is untrusted and the genie stays deaf to
    it (fails safe), never half-trusted."""
    for sender in ("ears@hq", "ears+airpods@hq", f"ears+airpods@{FQDN}"):
        assert not keeper_sent(_msg(sender), ENV), sender


def test_two_senders_is_not_the_keeper():
    assert not keeper_sent(_msg("heard@hq, eve@hq"), ENV)


@pytest.mark.parametrize(
    "sender",
    [
        "heard@hq <eve@hq>",  # two addresses, malformed: the default policy repairs it to heard@hq
        "heard@hq)<eve@evil.example>",  # CVE-2023-27043's shape
    ],
)
def test_a_from_that_does_not_parse_whole_is_not_the_keeper(sender):
    # read from the raw field, strictly: a trust test fails closed
    v = recognise(_msg(sender, "Ears-Kind: spoken"), ENV, SIGNED)
    assert v.voice is False
    assert v.state == PLAIN
    assert v.sender is None
    assert keeper_sent(_msg(sender), ENV) is False


def test_a_folded_keeper_from_still_passes():
    m = _msg("Ears\r\n <heard+airpods@hq>", "Ears-Kind: spoken")
    assert recognise(m, ENV, SIGNED).voice is True
    assert recognise(m, ENV).state == UNVERIFIED


def test_no_from_is_not_the_keeper():
    v = recognise(_msg(None, "Ears-Kind: spoken"), ENV, SIGNED)
    assert v.voice is False
    assert v.state == PLAIN
    assert v.sender is None


def test_the_keeper_without_the_header_is_plain_mail():
    v = recognise(_msg("heard+airpods@hq"), ENV, SIGNED)
    assert v.voice is False
    assert v.state == PLAIN
    assert v.marked is False
    assert v.kind is None
    assert v.reason == "no Ears-Kind"


def test_an_empty_ears_kind_still_marks_it():
    # O1: the header's PRESENCE marks voice (the reader's rule: an empty
    # value is present); the keeper writes `spoken` or a cut notice.
    v = recognise(_msg("heard@hq", "Ears-Kind:"), ENV, SIGNED)
    assert v.voice is True
    assert v.kind == ""
    assert recognise(_msg("heard@hq", "Ears-Kind:"), ENV).state == UNVERIFIED


def test_both_spellings_the_unprefixed_kind_wins_and_is_recorded():
    m = _msg("heard@hq", "X-Ears-Kind: cut", "Ears-Kind: spoken")
    v = recognise(m, ENV)
    assert v.kind == "spoken"
    assert v.spelling == "Ears-Kind"
    assert v.prefixed is False


# -- the 16,000-character ceiling (O22 (F)) ---------------------------------


def test_the_ceiling_is_sixteen_thousand():
    assert CEILING == 16_000


def test_a_forty_thousand_character_body_is_cut_with_one_line():
    # the spec's "Past the unsigned ceiling"
    body = ("0123456789" * 4000)
    assert len(body) == 40_000
    w = whole(body, 812)
    assert w.cut == 24_000
    assert w.text.startswith(body[:CEILING])
    lines = w.text[CEILING:].split("\n")
    assert lines[0] == ""  # the cut line starts on a line of its own
    assert lines[1:] == ["… 24,000 more - message read 812"]
    assert w.more_line == "… 24,000 more - message read 812"


def test_a_body_at_the_ceiling_is_whole():
    body = "y" * CEILING
    w = whole(body, 7)
    assert w.text == body
    assert w.cut == 0
    assert w.more_line is None


def test_a_body_one_past_the_ceiling_is_cut():
    w = whole("y" * (CEILING + 1), 7)
    assert w.cut == 1
    assert w.text.endswith("\n… 1 more - message read 7")


def test_a_short_turn_is_whole():
    w = whole("hey eve, the build is green", "812")
    assert w.text == "hey eve, the build is green"
    assert w.cut == 0


def test_the_module_writes_no_wake_logic():
    # the seam with SM-474: parse and recognise here, judge there
    import inspect

    src = inspect.getsource(_voice)
    assert "wakeable" not in src
