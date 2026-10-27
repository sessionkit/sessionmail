"""The suite is hermetic by construction, not by good manners.

Four things could make a test read the machine it runs on: the real notmuch
index, the real `~/.sessionmail`, the real mailstore root, and the runner's
own seat (its identity and its `$<AGENT>_ROOT` hint). All four are
redirected or removed for EVERY test here, including the scaffold gate's
"invoke every verb once" sweep, which now reaches verbs that have bodies.

A test that wants a working engine (`tests/test_pager_status.py`) points
`SESSIONMAIL_NOTMUCH` at its own fake; the default below answers "no
results" so that a verb invoked by the burndown finds nothing and touches
nothing, on any machine, with or without notmuch installed.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_call
from fake_launchctl import FakeLaunchctl
from fakes import fake_scutil_only, write_fake  # fake_scutil_only: re-exported (test_host_rule)
from sessionmail import _launchd, _magicdns, _maildir, _mhs, _notmuch, _operator, _seat, _sig, _voice, pager, rules
from sessionmail import _host, _settings

#: The operator block hq and nas carry after SM-727 deploys (design §f): the
#: suite's default host. `stranger` removes it.
SC6_OPERATOR = '{"operator": {"mailbox": "judy", "privileged": ["ivan", "judy"]}}\n'
#: The rules Settings that line fills (`rules.operator_globs`, design d.2):
#: `$operator` is `judy@*`, exactly the pre-SM-727 code default. A test that
#: judges plain layers (no `load_layers`, so nothing reads the config) and
#: means the SC6 host hands this in; `NO_SETTINGS` is a stranger's host.
SC6_SETTINGS = rules.Settings(configured=rules.operator_globs(
    _operator._from_block(json.loads(SC6_OPERATOR)["operator"])))


def KEEPER_SIGNED(_msg) -> bool:
    """SM-782: the voice verdict seam's stand-in for a GOOD keeper signature
    (`_voice.recognise(msg, env, KEEPER_SIGNED)`). A test that means TRUSTED
    voice says so with this; with no verdict a keeper mail is UNVERIFIED.
    The signature itself is proven against a fixture registry in
    tests/test_voice_signed.py."""
    return True


@pytest.fixture
def keeper_signs(monkeypatch):
    """SM-782: every keeper-From voice mail the pager reads OFF DISK is
    signed by the keeper (`_voice.keeper_signed` stood in), so a scoop or a
    listener test that means TRUSTED voice says so. A mail that is not From
    the keeper's address is never asked, so it stays plain."""
    monkeypatch.setattr(_voice, "keeper_signed", lambda path, msg, env=None: True)


#: The fake installing account the conftest hands `_operator.account_name`
#: (SM-806): the default operator's name on a stranger's host.
ACCOUNT_DEFAULT = "acct-default"


def write_operator(home, text: str | None):
    """Write TEXT as HOME's `~/.sessionkit/tools/settings/session.json` (None: no file)."""
    path = home / _operator.DIR / "session.json"
    if text is None:
        if path.exists():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


#: The suite's alert runner (SM-798): a NEUTRAL local part, never a persona's
#: name. The suite's default host configures NO runner (the shipped template
#: sets none); a test that means a host with one asks for `alert_runner`, or
#: hands `RUNNER_SETTINGS` (or `Settings(runner=RUNNER)`) to a pure judgement.
RUNNER = "alerts"
#: `SC6_SETTINGS` on a host whose mail settings name `RUNNER`.
RUNNER_SETTINGS = dataclasses.replace(SC6_SETTINGS, runner=RUNNER)


def write_mail_settings(home, text: str | None):
    """Write TEXT as HOME's `~/.sessionkit/mail/settings/mail.json`, the mail tool's HOST
    layer (None: no file). HOME is the settings home, `_operator.settings_home()`."""
    path = home / _settings.RELATIVE
    if text is None:
        if path.exists():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def alert_runner():
    """A host whose mail settings name the alert runner `RUNNER` (SM-798).
    Returns a function that writes TEXT as the host's mail file instead
    (None: none)."""
    home = _operator.settings_home()
    write_mail_settings(home, json.dumps({rules.ALERT_RUNNER_KEY: RUNNER}) + "\n")
    return lambda text: write_mail_settings(home, text)


@pytest.fixture
def stranger():
    """A stranger's install: no operator block at all. Returns a function
    that writes TEXT as the host's file instead (None: none)."""
    home = _operator.settings_home()
    write_operator(home, None)
    return lambda text: write_operator(home, text)

EMPTY_ENGINE = """#!{python}
import sys
sys.stdout.write("[]")
"""

#: The bundled oracle's own seat chain (`mhs/`), beyond `_seat.SEAT_VARS`:
#: mhs-thread and session-mail-run read SESSION_AGENT, session-mail-wake
#: reads SESSION_SEAT.
ORACLE_SEAT_VARS = ("SESSION_AGENT", "SESSION_SEAT")
#: The session's signing key (`_sender`): a test that signs brings its own.
SIGNER_VARS = ("CLAUDE_SESSION_AGENT_SOCK", "CLAUDE_SESSION_PUB")
#: The mailstore root's other spellings. `SESSIONMAIL_ROOT` outranks the
#: `SESSION_MAIL_ROOT` set below on every side (`_maildir.env_root`, SM-910);
#: `MAIL_ROOT` is vmailbox-gen's.
OTHER_ROOT_VARS = ("SESSIONMAIL_ROOT", "MAIL_ROOT")
NOTMUCH_VARS = ("NOTMUCH_DATABASE", "NOTMUCH_PROFILE")
#: The mail origin a v2 signature qualifies a bare address with (SM-594 design 4.2).
#: Never the host's: a test that wants another, or none, sets its own.
TEST_ORIGIN = "origin.test.invalid"
#: The suffix every test runs under unless it sets its own (SM-674): RFC 2606's
#: `.invalid`, so no tailnet answers to it and no live row can match it.
HERMETIC_SUFFIX = "hermetic.invalid"
#: The test-time anchors (SM-869): a lone typed time in the
#: suite is one of these plus a delta, never a real date. Both are Sundays in
#: 2030: ANCHOR in Sydney's standard time (+10:00), ANCHOR_SUMMER in its
#: daylight time (+11:00). The shell side sources tests/mhs/anchor.sh, and
#: tests/test_anchor.py holds the two definitions equal.
ANCHOR = datetime.datetime(2030, 6, 9, 12, 34, 56, tzinfo=datetime.timezone.utc)
ANCHOR_SUMMER = datetime.datetime(2030, 12, 8, 12, 34, 56, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def hermetic(tmp_path_factory, monkeypatch):
    sandbox = tmp_path_factory.mktemp("hermetic")
    # EVERY `SESSIONMAIL_*` the runner carries goes first (SM-150 rca-001
    # §3); what the fixture wants back it sets again below. A seat's knobs
    # are not the suite's: `tm kick` gives every owner seat (super, GC,
    # engineer) a SESSIONMAIL_PAGE_RULES layer ending `wake from:ivan@*`,
    # and a test whose mail is From ivan (test_pull_imap's ride-queue test)
    # paged at once from such a seat and rode from a worker's. The pager
    # also reads _BURST_SECONDS, _REMIND_MINUTES, _GROUP_AFTER, _DETECT_*,
    # _QUERY and _PAGER from here. A test that wants one sets it.
    for var in [v for v in os.environ if v.startswith("SESSIONMAIL_")]:
        monkeypatch.delenv(var, raising=False)
    engine = write_fake(sandbox / "empty-notmuch", EMPTY_ENGINE)
    monkeypatch.setenv(_notmuch.BINARY_VAR, str(engine))
    monkeypatch.setenv("SESSIONMAIL_HOME", str(sandbox / "home"))
    # And HOME itself (SM-450, design.md D9): the pager hooks read and create
    # `$HOME/.sessionkit/mail/settings/mail.json` (session-settings 3.1; it was
    # `~/.session/mail/settings.json`), so a suite on the real HOME would
    # read the developer's file and the first hook test would CREATE it, with
    # the hooks OFF - silencing a live fleet. `sandbox / "user"` is not
    # `sandbox / "home"` (SESSIONMAIL_HOME): config is not state, and a test
    # that confused the two should fail. A test that needs its own HOME sets
    # it, as several already do; never relax this line to fix one.
    (sandbox / "user").mkdir()   # a HOME that exists, as a real one does
    monkeypatch.setenv("HOME", str(sandbox / "user"))
    # A session's mail state (ride, stamps, marks, key) is under the SESSION
    # DIR sessiontools keeps (session-settings 3.3a): `$HOME/.session/sessions/
    # <sid>/mail/`, or `$SESSIONS_DIR/<sid>/mail/` - a launcher may set
    # that for its sessions, so a suite run from one would write into its tree.
    monkeypatch.delenv("SESSIONS_DIR", raising=False)
    # The writing model (SM-918) is read from Claude Code's transcript under
    # `$CLAUDE_CONFIG_DIR/projects/`, else `$HOME/.claude/projects/`. HOME is the
    # sandbox's above; a runner's CLAUDE_CONFIG_DIR would point every send at the
    # seat's REAL transcript and stamp its model on a test's mail.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # And the runner's git config (SM-866, SK-126's form). A seat exports
    # GIT_CONFIG_COUNT with GIT_CONFIG_KEY_n/VALUE_n (Claude Code sets
    # commit.gpgsign=true through the session signer): those are COMMAND
    # scope, so they beat a temp repo's own `commit.gpgsign=false`, and every
    # test that commits went red. A global GIT_CONFIG_GLOBAL (a hooksPath to
    # a pre-commit that refuses, gpgsign) did the same. So no git a test runs
    # reads the runner's config: no env entries, no global, no system file.
    # A test that means a hostile config sets one itself.
    for var in [v for v in os.environ
                if v in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
                or v.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # The LOCKED rules file (pager-filters D3 layer 0) is never taken from
    # the environment, so SESSIONMAIL_HOME does not move it and no variable
    # can: its one seam is `rules.locked_path`, pointed into the sandbox here
    # so a loader called with no `locked=` never reads the host's file.
    monkeypatch.setattr(rules, "locked_path", lambda: sandbox / "locked" / rules.LOCKED_FILE)
    # The OPERATOR block (SM-727): who is privileged is read from
    # `~/.sessionkit/tools/settings/session.json` under the PASSWORD DATABASE's home,
    # never `$HOME`, so the HOME above does not move it: its one seam is
    # `_operator.settings_home`, pointed into the sandbox here, so no test
    # reads the developer's real file. The sandbox holds the line hq and nas
    # carry after deploy (design §f, SC6): operator judy, privileged ivan and
    # judy. The whole suite then runs as those hosts do, and is SC6's
    # regression net. A test that wants a stranger's host uses `stranger`.
    operator_home = sandbox / "operator"
    write_operator(operator_home, SC6_OPERATOR)
    monkeypatch.setattr(_operator, "settings_home", lambda: operator_home)
    # The DEFAULT operator's name (SM-806): with no operator block, the
    # operator defaults to the installing ACCOUNT, read from the password
    # database. Its one seam is `_operator.account_name`, patched here to a
    # fake name, so no test reads the real account: on a real host that can
    # be a live seat's name. `stranger(None)` is the host it shows on.
    monkeypatch.setattr(_operator, "account_name", lambda: ACCOUNT_DEFAULT)
    # The macOS NAMES (SM-941): the drop's IS-ME set asks `scutil --get`
    # LocalHostName and ComputerName. Its one seam is `_host.scutil_binary`,
    # patched here so a test is answered by a fake `scutil` on the PATH it
    # hands over, and NEVER by the host's own (/usr/sbin/scutil, which the
    # runner's PATH names on a Mac): no test reads this machine's names.
    monkeypatch.setattr(_host, "scutil_binary", fake_scutil_only)
    # The pager's CONFIRMATION MAILS (pager-filters D9, tasks 4.2; SM-474
    # do-007): every `pager listen` arm now sends one "listening" mail
    # through the MTA. Its one seam is `pager._mta_send`, pointed here at a
    # recorder that sends nothing, so no test ever reaches a real
    # /usr/sbin/sendmail. A seam and not a variable, because a test that
    # blanks SESSIONMAIL_* (the pull test does) would otherwise fall back to
    # the real MTA. A test that wants the sends patches its own recorder.
    monkeypatch.setattr(pager, "_mta_send", _no_send, raising=False)
    # ...and the MTA ITSELF, for what the seam cannot reach: a test that runs
    # `pager listen` as a real CHILD PROCESS (`_spawn_listener`, the caller-
    # going test) imports a fresh `pager`, unpatched, and its arm calls the
    # real `_sender.send`. Measured in the do-007 review (46cadd4d): nothing
    # stopped that child but `preflight`'s LIVE postfix map lookup refusing
    # `sess-a@hq` - a refusal by luck, after reading the host. The child
    # inherits this variable, and `_deliver` checks the MTA BEFORE preflight,
    # so it now refuses `no MTA` and touches nothing, like GEN_VAR below.
    # A test that wants a sendmail points this at its own fake.
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", str(sandbox / "no-mta-here"))
    # The MAIL ROOT, for the same reason. Plain `pager status` reads the
    # session's box under it since message-subscriptions 4.2, and a test that
    # set a session id and forgot this read `~/.mail/agents/<id>` (measured,
    # SM-374 do-009). An empty root: a test that wants mail builds its own.
    monkeypatch.setenv("SESSION_MAIL_ROOT", str(sandbox / "mail"))
    # The RUNNER's own session must not leak into the tests. It always did -
    # nothing here ever deleted it - and it was harmless only by luck: every
    # reader that could see a session id also saw a SEAT var, and seat vars
    # are tried first. `message list` with no BOX now prefers the SESSION box
    # (specs/message, "A bare listing is the doorbell's box"), which made the
    # leak bite: 24 tests failed against the developer's real session id and
    # would have passed in CI, or the reverse. A fixture called `hermetic`
    # should not be selective about it. A test that wants a session sets one.
    for var in _seat.SESSION_VARS:
        monkeypatch.delenv(var, raising=False)
    # The same for a DECLARED address (task-address-doorbell): `EMAIL` is a
    # variable plenty of shells already carry, and a plus address in it moves
    # a seat's folder. A test that wants a declaration sets one.
    # (The HEARS variable it also cleared retired with message-subscriptions
    # 4.8: nothing reads it, so an inherited one is inert.)
    monkeypatch.delenv("EMAIL", raising=False)
    # And `MAIL`: with no session id, `pager listen` watches the maildir it
    # names (message-subscriptions D8). A login shell sets it to an mbox
    # (`/var/mail/<user>`), so a no-session test would read the runner's
    # shell. A test that wants a MAIL sets one.
    monkeypatch.delenv("MAIL", raising=False)
    # And SESSIONMAIL_SUBSCRIBE: a session born under test (every listen of a
    # fresh session id) applies it (message-subscriptions D4, tasks 4.1), so a
    # runner launched from an agent MD that sets it would subscribe test
    # sessions. A test that wants one sets it.
    monkeypatch.delenv("SESSIONMAIL_SUBSCRIBE", raising=False)
    # And the MAIL ORIGIN (SM-594 design 4.2): a v2 signature qualifies a bare
    # address with $SESSIONMAIL_ORIGIN, else `postconf -xh myorigin` - on PATH,
    # then at the system paths in `_sig.POSTCONF_FALLBACK`, which would read the
    # host's postfix. Fixed here for the sender and every verifier child alike;
    # the fallback is emptied so a test that unsets the variable gets none.
    monkeypatch.setenv("SESSIONMAIL_ORIGIN", TEST_ORIGIN)
    monkeypatch.setattr(_sig, "POSTCONF_FALLBACK", ())
    # And the SM-538 seat switch: a runner seat that turned its shortener off
    # would change what every send test sees. A test that wants it sets it.
    monkeypatch.delenv("SESSIONMAIL_SUBJECT_SHORTEN", raising=False)
    # And the SM-538 counter's directory (design 5.2): named, and MISSING, so
    # a shortened send under test writes nothing and can never fall through
    # to the live node_exporter collector. A test that wants it makes it.
    monkeypatch.setenv("SESSIONMAIL_TEXTFILE_DIR", str(sandbox / "textfile"))
    # The GENERATOR. A verb that rebuilds the map (`message subscription
    # add|remove`, the pager's doorbell) falls back to `vmailbox-gen` on PATH:
    # the LIVE one, which writes the live postfix maps. A test that wants a
    # generator points GEN_VAR at a wrapped one under tmp; the default names
    # one that does not exist, so a forgotten fixture fails CLOSED
    # (SM-374 do-006: "tests must never rebuild the live map").
    monkeypatch.setenv(_seat.GEN_VAR, str(sandbox / "no-generator-here"))
    # And the generator's POSTCONF (SM-310's live-tree guard). Set, it is the
    # ONLY postconf the generator asks - no PATH, no system fallback - so a
    # path that does not exist means "no postconf": a run with --out into the
    # sandbox still runs, and one without --out is refused. No test reads the
    # host's postfix through the generator; one that wants an answer points
    # this at a fake.
    monkeypatch.setenv("SESSIONMAIL_POSTCONF", str(sandbox / "no-postconf-here"))
    # The TAILNET SUFFIX (SM-674). With MAGICDNS_SUFFIX unset, the FQDN's
    # suffix is asked of the runner's real tailscale (`_magicdns`, the helper
    # beside vmailbox-gen), so a suite on hq would pass on hq's tailnet and
    # fail anywhere else. Every test gets a suffix no tailnet has, so a test
    # that leaned on the old literal default fails here, loudly; one that
    # wants a real answer sets its own, and one that wants NO answer unsets
    # it and points the seams (PATH, MAGICDNS_TAILSCALE_BUNDLE) at fakes. The
    # bundle seam is emptied too, so no test can reach an installed app's CLI;
    # and the in-process memo starts empty for each test.
    monkeypatch.setenv("MAGICDNS_SUFFIX", HERMETIC_SUFFIX)
    monkeypatch.setenv("MAGICDNS_TAILSCALE_BUNDLE", "")
    _magicdns.forget()
    # And the runner's SEAT, its mail roots and its index (review 2030-12-08,
    # H3). With the runner's `CLAUDE_CODE_AGENT` and `$<AGENT>_ROOT` left in
    # place, the scaffold sweep (`tests/invocations.py`) ran the developer's
    # real `memory run /BOOT/WAKE` through the bundled oracle, and `message
    # list` walked the real seat box: 36,004 opens under ~/.mail/agents/<seat>
    # in one run of that one test, measured. A test that wants a seat sets
    # one. A pinned $SESSIONMAIL_MHS_ORACLE goes too: the bundle is what
    # ships, so the bundle is what the sweep runs. And the run opt-in
    # (SM-780): a developer who set it must not have the sweep run anything;
    # a test that means to run sets it itself. And the --trust registry
    # (SM-785): a developer's own registry must never decide a test.
    for var in (*_seat.SEAT_VARS, *ORACLE_SEAT_VARS, *SIGNER_VARS, *OTHER_ROOT_VARS,
                *NOTMUCH_VARS, _mhs.ORACLE_VAR, _mhs.RUN_GATE_VAR, _mhs.REGISTRY_VAR):
        monkeypatch.delenv(var, raising=False)
    for var, value in list(os.environ.items()):
        if var.endswith("_ROOT") and not os.path.isabs(value.strip()):
            monkeypatch.delenv(var)   # a `$<AGENT>_ROOT` hint: a Message-ID
    monkeypatch.setenv(_maildir.LEGACY_ROOT_VAR, str(sandbox / "mail"))
    # Host mode reads the host's sessionkit profile and no other notmuch
    # config (`_index`): one is written here, under a sandbox
    # XDG_CONFIG_HOME, naming a database that does not exist. So a real
    # notmuch (the oracle calls `notmuch` by name, past SESSIONMAIL_NOTMUCH)
    # fails to open it instead of opening the real index. A test that builds
    # an index writes its own profile, or points XDG_CONFIG_HOME at one.
    xdg = sandbox / "xdg"
    profile = xdg / "notmuch" / "sessionkit" / "config"
    profile.parent.mkdir(parents=True)
    profile.write_text(f"[database]\npath={sandbox / 'no-notmuch-database'}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    # And any notmuch the suite runs outside sessionmail's own calls reads
    # a config that does not exist ("cannot load config file").
    monkeypatch.setenv("NOTMUCH_CONFIG", str(sandbox / "no-notmuch-config"))
    # CLAUDE IS UP, by default (SM-706). `pager status` asks
    # `_seat.claude_for` before it says "not listening"; with no claude for
    # the session it says the not-yet line instead. Every suite written
    # before SM-706 assumes the in-claude world - and whether the runner
    # itself sits under a claude must not decide a test - so the answer is
    # pinned to a live pid here. A pre-claude test overrides it (the
    # `pre_claude` fixture below); `tests/test_seat_claude_for.py` tests the
    # real function, held in REAL_CLAUDE_FOR. And the sidecar source reads
    # SESSION_SIDECAR_DIR: pointed at an empty sandbox dir, so no test can
    # ever read the runner's real `~/.claude/sessions`.
    monkeypatch.setattr(_seat, "claude_for", _claude_up)
    monkeypatch.setenv("SESSION_SIDECAR_DIR", str(sandbox / "no-sidecars-here"))


#: What the conftest's `pager._mta_send` answers: a receipt for nothing sent.
NO_SEND_ID = "<no-send@test.invalid>"


def _no_send(req, env, mint=None) -> str:
    """The conftest's `pager._mta_send`: records nothing, sends nothing."""
    return NO_SEND_ID


@pytest.fixture(autouse=True)
def tool_child(hermetic, monkeypatch):
    """SM-926: a tool's verb runs in a CHILD, where the test process's patches
    never land. Every `_mcp.run_child` this test makes hands the child the
    seams this test patched (`tests/tool_call.py`), and refuses one it cannot.
    A test whose OWN child program sets a seam names it in `.covered`
    (`"sessionmail.pager.confirm_focus"`). A refusal fails the test here, at
    teardown, even if a server turned the raise into a tool error."""
    state = SimpleNamespace(covered=set(), refused=[])
    tool_call.install(monkeypatch, state.covered, state.refused)
    yield state
    if state.refused:
        pytest.fail("\n".join(dict.fromkeys(state.refused)), pytrace=False)


@pytest.fixture(autouse=True)
def launchctl_fake(hermetic, tmp_path_factory, monkeypatch):
    """No test ever reaches the real launchd. `message seat agent
    --install` runs `$SESSIONMAIL_LAUNCHCTL`, else /bin/launchctl: every test
    gets the recording fake in tests/fake_launchctl.py through the variable,
    and the in-process fallback is pointed at a path that does not exist, so
    an env that lost the variable fails CLOSED instead of loading a job. A
    test that builds an env from nothing adds `launchctl_fake.env` to it.
    Teardown stops every agent the fake started, by pid."""
    fake = FakeLaunchctl(tmp_path_factory.mktemp("launchctl"))
    monkeypatch.setenv(_launchd.LAUNCHCTL_VAR, str(fake.path))
    monkeypatch.setattr(_launchd, "DEFAULT_LAUNCHCTL",
                        str(fake.path.parent / "no-launchctl-here"))
    yield fake
    fake.stop_all()


#: The real `_seat.claude_for`, captured before any test patches it.
REAL_CLAUDE_FOR = _seat.claude_for


def _claude_up(session, ps=None, me=None, live=None):
    """The conftest pin: a claude is running for every session (our pid)."""
    return os.getpid()


#: How a tool's child gets the conftest's own function seams (SM-926,
#: `tests/tool_call.py`): the claude pin is "this process's pid" there too; the
#: no-send MTA answers the same receipt, and in a `pager` child, which runs
#: `pager` as `__main__` where no patch lands, SESSIONMAIL_SENDMAIL (set above,
#: naming no MTA) is what stops a send instead.
tool_call.CARRIED_FNS[_claude_up] = ("pid", None, None)
tool_call.CARRIED_FNS[_no_send] = ("returns", NO_SEND_ID, "SESSIONMAIL_SENDMAIL names no MTA")
# The macOS-names seam (SM-941) takes an ENV, so it is no constant: the child
# imports the same function by reference (SM-964), from tests/fakes.py.
tool_call.CARRIED_FNS[fake_scutil_only] = ("ref", "fakes:fake_scutil_only", None)


@pytest.fixture
def pre_claude(monkeypatch):
    """SM-706: claude has NOT started for this session - a `session start`
    context generator's world. Records each call, so a test can pin that the
    question was (or was never) asked."""
    calls: list[str] = []

    def nobody(session, ps=None, me=None, live=None):
        calls.append(session)
        return None

    monkeypatch.setattr(_seat, "claude_for", nobody)
    return calls


@pytest.fixture
def fresh_session() -> str:
    """THIS test's own session id, made up and never seen before (SM-558,
    GC criterion 1). Every test that runs `pager listen`, `pager off` or a
    hook as a real CHILD PROCESS takes one, so no two tests share an id and
    none is an id a live seat carries. Visibly fake (`sess-` + 12 hex), and
    not uuid-shaped, so `_migrate` never takes it for a real session.

    It sets nothing: a rig that sets CLAUDE_CODE_SESSION_ID binds it in a
    fixture that REQUESTS the rig (`sid` in the listener test files), so the
    rig's own `sess-a` is always overwritten, never the other way round.

    The id alone is not what keeps a child off the seat running the suite:
    the ancestry walk, which a child runs on the REAL `ps`, ends at the
    nearest claude, and that claude carries the seat's id, not this one
    (`_seat.listening_by_ancestry`, SM-558). State dirs are per test
    already (`hermetic`'s sandbox HOME, each rig's SESSIONMAIL_HOME)."""
    import uuid
    return f"sess-{uuid.uuid4().hex[:12]}"


# ── the latency lane (SM-863) ───────────────────────────────────────────────
#
# A test marked `latency` bounds how SOON something happens. Machine load
# reddens such a bound with no product change, so a run leaves the lane out
# unless it asks for it: with a -m (`make test-latency` is `-m latency`), or
# by naming the test's id. `make test`, and any gate that runs the suite as
# it stands, cannot go red on load alone. Kept here rather than in
# pyproject.toml so the suite carries its own selection.

LATENCY = "latency"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{LATENCY}: a wall-clock bound on how soon something happens; left out "
        "of a run unless asked for (-m latency, `make test-latency`, or by id), "
        "and run alone on a quiet host")


def _named_by_id(config) -> set[tuple[Path, str]]:
    """The tests the command line names by id, as (absolute file, the id
    after the file). A parametrized id names its bare function too."""
    named = set()
    for arg in config.args:
        path, sep, rest = arg.partition("::")
        if sep:
            named.add(((config.invocation_params.dir / path).resolve(), rest))
    return named


def pytest_collection_modifyitems(config, items):
    if config.option.markexpr:            # a -m chooses for itself
        return
    named = _named_by_id(config)
    keep, drop = [], []
    for item in items:
        if item.get_closest_marker(LATENCY) is None:
            keep.append(item)
            continue
        rest = item.nodeid.partition("::")[2]
        asked = any(item.path.resolve() == p and (rest == r or rest.split("[", 1)[0] == r)
                    for p, r in named)
        (keep if asked else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


# ── the shell rail on BOTH bashes (SM-963) ──────────────────────────────────
#
# A shell-rail test that pins `/bin/bash` runs on bash 3.2 on a Mac, and only
# there. The fleet does not: `session-mail-wake` runs the FIRST bash on PATH
# (`system_bash`), which is Homebrew's 5.x on a Mac that has it and 5.x on any
# Linux. Bash 4.4+ reads a declared-but-unassigned `local x` as UNSET under
# `set -u`; 3.2 reads it as empty. So a rail bug of that class is green on the
# pinned bash and red on the one that runs it (SM-963: `_mbl_two_boxes`'s bare
# `first`, `memory wake` exiting 1 exactly when two boxes answer to one name).
#
# `rail_bash` runs a test once per DISTINCT bash: `/bin/bash` and the bash on
# the runner's PATH. A param is skipped only when that bash is absent, or is
# the same binary as `/bin/bash` (a Linux /usr/bin/bash beside a merged /bin).

SYSTEM_BASH = "/bin/bash"


def _rail_bashes() -> list:
    import shutil
    system = SYSTEM_BASH if Path(SYSTEM_BASH).is_file() else None
    on_path = shutil.which("bash")
    same = bool(system and on_path and os.path.samefile(system, on_path))
    return [
        pytest.param(system, id="bin-bash", marks=pytest.mark.skipif(
            system is None, reason=f"no {SYSTEM_BASH} on this host")),
        pytest.param(on_path, id="path-bash", marks=pytest.mark.skipif(
            on_path is None or same,
            reason="no bash on PATH" if on_path is None
            else f"the PATH bash ({on_path}) is {SYSTEM_BASH}")),
    ]


@pytest.fixture(params=_rail_bashes())
def rail_bash(request) -> str:
    """An ABSOLUTE bash for a shell-rail test: `/bin/bash`, then the PATH bash."""
    return request.param
