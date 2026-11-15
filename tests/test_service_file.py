"""The kit's service files: `message seat agent --install/--uninstall` declares
its job in `~/.sessionkit/mail/services/seat-agents/<ADDRESS>.json`.

`sessionkit service` finds the kit's jobs by reading one JSON file per service
under each package's `services/` folder, which sits BESIDE `settings/`. The
install writes the file with `enabled: true` once its own answer check passes;
the uninstall keeps it and writes `enabled: false`. Every write is whole and
atomic (a temp file in the same folder, then a rename), and a failed write
never undoes the launchd act it goes with.

Every test runs in process with HOME at tmp_path and the conftest's fake
launchctl (its `no-start` knob: a job is loaded, nothing is started). The
answer check is stubbed: whether the real agent answers is
`test_seat_install`'s business. The files are validated against a vendored
copy of the kit's own schema, `tests/fixtures/service/service.schema.json`.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
from pathlib import Path

import jsonschema
import pytest

from sessionmail import _launchd, _service_file, _settings

ADDRESS = "bot.alice"
PLUS = "heard+airpods"
LABEL = f"dev.sessionkit.mail.seat-agent.{ADDRESS}"
SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "service" /
                     "service.schema.json").read_text())

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS's")


def expected(address: str, enabled: bool) -> dict:
    """The ask's record for one seat, key for key."""
    return {"service": 1, "name": f"seat-agent.{address}",
            "label": f"dev.sessionkit.mail.seat-agent.{address}", "agent": address,
            "enabled": enabled, "install": f"message seat agent --install {address}",
            "uninstall": f"message seat agent --uninstall {address}", "hazard": ["call"]}


@pytest.fixture
def host(tmp_path, launchctl_fake, monkeypatch):
    """HOME at tmp_path, seats minted as files (a key and a pub, never used:
    the answer check is stubbed), `message` on PATH, the fake launchctl
    loading jobs without starting them. The socket cap is lifted: tmp_path
    is longer than macOS's 103 bytes, and the cap is not what is tested."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    message = bin_dir / "message"
    message.write_text("#!/bin/sh\nexit 0\n")
    message.chmod(0o755)
    keys = tmp_path / "state" / "seat-keys"
    for address in (ADDRESS, PLUS):
        (keys / address).mkdir(parents=True)
        (keys / address / "id_ed25519").write_text("not a key\n")
        (keys / address / "seat.pub").write_text("ssh-ed25519 AAAA not-a-key\n")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home),
           "SESSIONMAIL_HOME": str(tmp_path / "state"), **launchctl_fake.env}
    launchctl_fake.knob("no-start")
    monkeypatch.setattr(_launchd, "SOCK_MAX", 4096)
    monkeypatch.setattr(_launchd, "ANSWER_SECONDS", 0.3)
    monkeypatch.setattr(_launchd, "GONE_SECONDS", 0.3)
    monkeypatch.setattr(_launchd, "POLL_SECONDS", 0.01)
    answering = {"yes": True}
    monkeypatch.setattr(_launchd, "answers", lambda job, env: answering["yes"])
    services = home / ".sessionkit" / "mail" / "services"
    return dict(env=env, home=home, services=services, answering=answering,
                file=services / "seat-agents" / f"{ADDRESS}.json")


def install(env, *args):
    out, err = io.StringIO(), io.StringIO()
    rc = _launchd.install(*args, env=env, out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


def uninstall(env, address):
    err = io.StringIO()
    rc = _launchd.uninstall(address, env=env, err=err)
    return rc, err.getvalue()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


# ── where the file lives ───────────────────────────────────────────────────

def test_services_sits_beside_settings_never_inside_it(tmp_path):
    env = {"HOME": str(tmp_path)}
    services = _settings.services_dir(env)
    assert services == tmp_path / ".sessionkit" / "mail" / "services"
    assert services.parent == _settings.path(env).parent.parent
    assert _settings.path(env).parent not in services.parents
    assert _service_file.path("puller", env=env) == services / "puller.json"
    assert (_service_file.path(ADDRESS, group="seat-agents", env=env)
            == services / "seat-agents" / f"{ADDRESS}.json")


@pytest.mark.parametrize("stem", ["", ".", "..", "a/b", "a\0b"])
def test_a_stem_or_group_that_is_not_one_name_is_a_bug(tmp_path, stem):
    with pytest.raises(ValueError):
        _service_file.path(stem, env={"HOME": str(tmp_path)})
    with pytest.raises(ValueError):
        _service_file.path("x", group=stem or "..", env={"HOME": str(tmp_path)})


# ── the record ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("address", [ADDRESS, PLUS])
def test_the_seat_record_is_the_asks_and_validates_against_the_kit_schema(address, enabled):
    rec = _launchd.service_record(address, enabled=enabled)
    assert rec == expected(address, enabled)
    jsonschema.validate(rec, SCHEMA)
    assert rec["label"] == _launchd.label_for(address)


#: Faults the kit's schema lets through and this package's own check refuses:
#: an unknown key (ignored by the kit), another package's label, and a name
#: that is not the label's tail.
OURS_ONLY = ("unknown key", "does not start", "label's tail")


@pytest.mark.parametrize("rec, problem", [
    ({"name": "x", "label": "dev.sessionkit.mail.x", "enabled": True, "install": "i"},
     "missing required key 'service'"),
    ({"service": 1, "name": "x", "enabled": True, "install": "i"}, "'label' (or 'run')"),
    ({"service": 1, "name": "x", "label": "dev.sessionkit.mail.x", "run": ["x"],
      "enabled": True, "install": "i"}, "both 'label' and 'run'"),
    ({"service": 2, "name": "x", "label": "dev.sessionkit.mail.x", "enabled": True,
      "install": "i"}, "'service' is 2"),
    ({"service": True, "name": "x", "label": "dev.sessionkit.mail.x", "enabled": True,
      "install": "i"}, "'service' is True"),
    ({"service": 1, "name": "x", "label": "dev.sessionkit.mail.x", "enabled": "yes",
      "install": "i"}, "'enabled' is not true or false"),
    ({"service": 1, "name": "x", "label": "dev.sessionkit.voice.x", "enabled": True,
      "install": "i"}, "does not start 'dev.sessionkit.mail.'"),
    ({"service": 1, "name": "y", "label": "dev.sessionkit.mail.x", "enabled": True,
      "install": "i"}, "is not the label's tail"),
    ({"service": 1, "name": "x", "label": "dev.sessionkit.mail.x", "enabled": True,
      "install": "i", "hazard": "call"}, "'hazard'"),
    ({"service": 1, "name": "x", "label": "dev.sessionkit.mail.x", "enabled": True,
      "install": "i", "bogus": 1}, "unknown key 'bogus'"),
])
def test_check_names_each_fault(rec, problem):
    assert any(problem in p for p in _service_file.check(rec)), _service_file.check(rec)
    if any(word in problem for word in OURS_ONLY):
        jsonschema.validate(rec, SCHEMA)        # the kit's schema lets it through; we do not
    else:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(rec, SCHEMA)


@pytest.mark.parametrize("kw", [
    dict(label="dev.sessionkit.voice.x", enabled=True, install="i"),
    dict(label="dev.sessionkit.mail.x", name="y", enabled=True, install="i"),
    dict(label="dev.sessionkit.mail.x", enabled=True, install=""),
    dict(label="dev.sessionkit.mail.x", enabled=True, install="i", bogus=1),
    dict(enabled=True, install="i"),
])
def test_record_refuses_a_bad_record_as_this_packages_own_bug(kw):
    with pytest.raises(ValueError):
        _service_file.record(**kw)


def test_render_is_two_space_json_with_one_newline_in_the_records_order():
    rec = _launchd.service_record(ADDRESS, enabled=True)
    text = _service_file.render(rec)
    assert text == json.dumps(rec, indent=2) + "\n"
    assert list(json.loads(text)) == ["service", "name", "label", "enabled", "install",
                                      "uninstall", "hazard", "agent"]


# ── install ────────────────────────────────────────────────────────────────

@darwin
@pytest.mark.parametrize("address", [ADDRESS, PLUS])
def test_install_writes_a_schema_valid_file_after_the_answer_check(host, address):
    rc, out, err = install(host["env"], address)
    assert rc == 0, err
    target = host["services"] / "seat-agents" / f"{address}.json"
    assert load(target) == expected(address, True)
    jsonschema.validate(load(target), SCHEMA)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert err.splitlines()[-1] == f"message seat agent: service {target} (enabled: true)"
    assert out.startswith("SEAT_LABEL="), "the stdout contract is unchanged"
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name], "no temp left"
    # nothing under settings/, and nothing else under the kit's folder but log/ and services/
    assert not (host["home"] / ".sessionkit" / "mail" / "settings").exists()
    assert sorted(p.name for p in (host["home"] / ".sessionkit" / "mail").iterdir()) \
        == ["log", "services"]


@darwin
def test_a_reinstall_rewrites_the_file_whole_and_is_idempotent(host):
    assert install(host["env"], ADDRESS)[0] == 0
    first = host["file"].read_bytes()
    rc, _, err = install(host["env"], ADDRESS)          # the already-installed path
    assert rc == 0 and "already installed" in err
    assert host["file"].read_bytes() == first
    # a hand-edited file, or one that is not JSON, is replaced whole: nothing is merged
    host["file"].write_text('{"service": 1, "enabled": false, "note": "mine"}\n')
    assert install(host["env"], ADDRESS)[0] == 0
    assert host["file"].read_bytes() == first
    host["file"].write_text("not json")
    assert install(host["env"], ADDRESS)[0] == 0
    assert host["file"].read_bytes() == first


@darwin
def test_the_already_installed_path_writes_a_missing_file(host, launchctl_fake):
    """A job installed before this release has no file: a re-run that finds it
    loaded and answering writes it, and loads nothing."""
    assert install(host["env"], ADDRESS)[0] == 0
    host["file"].unlink()
    rc, _, err = install(host["env"], ADDRESS)
    assert rc == 0 and "already installed" in err
    assert load(host["file"]) == expected(ADDRESS, True)
    assert launchctl_fake.verbs().count("bootstrap") == 1


@darwin
def test_a_failed_answer_check_writes_no_file(host, launchctl_fake):
    host["answering"]["yes"] = False
    rc, out, err = install(host["env"], ADDRESS)
    assert rc == 1 and out == "" and "did not answer" in err
    assert not host["services"].exists(), "no file, and not even the folder"
    assert launchctl_fake.jobs() == {}, "rolled back as before"


@darwin
def test_a_loaded_job_that_does_not_answer_writes_no_file(host):
    assert install(host["env"], ADDRESS)[0] == 0
    host["file"].unlink()
    host["answering"]["yes"] = False
    rc, out, err = install(host["env"], ADDRESS)
    assert rc == 1 and out == "" and "does not answer" in err
    assert not host["file"].exists()


@darwin
def test_a_failed_bootstrap_writes_no_file(host, launchctl_fake):
    launchctl_fake.knob("fail-bootstrap")
    rc, _, err = install(host["env"], ADDRESS)
    assert rc == 1 and "bootstrap" in err
    assert not host["services"].exists()


@darwin
def test_a_refused_install_writes_no_file(host):
    rc, _, err = install(host["env"], "bot.nobody")
    assert rc == 2 and "mint the seat first" in err
    assert not host["services"].exists()


# ── the write is atomic, and a failed one never undoes the launchd act ─────

@darwin
def test_a_write_that_fails_before_the_rename_leaves_the_old_file_intact(host, monkeypatch,
                                                                          launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    host["file"].write_text("the previous file\n")

    real = os.replace

    def no_rename(src, dst):            # os is one module: fail only the service file's rename
        if str(dst).endswith(".json"):
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(_service_file.os, "replace", no_rename)
    rc, out, err = install(host["env"], ADDRESS)
    assert rc == _launchd.EX_UNDECLARED == 3, err
    assert host["file"].read_text() == "the previous file\n"
    assert sorted(p.name for p in host["file"].parent.iterdir()) == [host["file"].name], \
        "the temp file is removed"
    assert err.splitlines()[-1] == (
        f"message seat agent: FAILED - service could not write {host['file']}: No space left "
        "on device; the previous file is unchanged. The job is installed and its agent "
        "answers; re-run --install to write the file")
    assert out.startswith("SEAT_LABEL="), "the job is installed: its report still prints"
    assert LABEL in launchctl_fake.jobs(), "the launchd act is never undone"


@darwin
def test_the_write_goes_through_a_temp_file_in_the_same_folder_then_one_rename(host,
                                                                              monkeypatch):
    seen = []
    real = os.replace

    def spy(src, dst):
        if str(dst).endswith(".json"):  # os is one module: the plist's rename is not ours
            seen.append((Path(src), Path(dst), Path(src).read_bytes()))
        return real(src, dst)

    monkeypatch.setattr(_service_file.os, "replace", spy)
    assert install(host["env"], ADDRESS)[0] == 0
    [(src, dst, data)] = seen
    assert dst == host["file"] and src.parent == dst.parent and src != dst
    assert data == host["file"].read_bytes()


@darwin
def test_an_unwritable_folder_exits_3_and_keeps_the_job(host, launchctl_fake):
    host["services"].mkdir(parents=True)
    host["services"].chmod(0o555)
    try:
        rc, out, err = install(host["env"], ADDRESS)
    finally:
        host["services"].chmod(0o755)
    assert rc == _launchd.EX_UNDECLARED == 3, err
    assert "FAILED - service could not write" in err and "Permission denied" in err
    assert err.splitlines()[-1].endswith(
        "The job is installed and its agent answers; re-run --install to write the file")
    assert LABEL in launchctl_fake.jobs()
    assert not host["file"].exists()


@darwin
def test_exit_3_prints_the_report_whole_exactly_as_exit_0(host, launchctl_fake):
    """The stdout contract on 3 is the contract on 0: the five KEY=VALUE
    lines, nothing else, byte for byte what a run that wrote the file prints."""
    host["services"].mkdir(parents=True)
    host["services"].chmod(0o555)
    try:
        rc3, out3, _ = install(host["env"], ADDRESS)
    finally:
        host["services"].chmod(0o755)
    assert rc3 == 3
    rc0, out0, err0 = install(host["env"], ADDRESS)     # the re-run writes the file
    assert rc0 == 0 and "already installed" in err0, err0
    assert load(host["file"]) == expected(ADDRESS, True)
    assert out3 == out0
    keys = [line.split("=", 1)[0] for line in out3.splitlines()]
    assert keys == ["SEAT_LABEL", "SEAT_PLIST", "SEAT_SOCKET", "SEAT_PUB", "SEAT_LOG"]
    assert "FAILED" not in out3, "the failure line is stderr's, never stdout's"


@darwin
def test_a_real_launchctl_failure_is_1_even_with_services_unwritable(host, launchctl_fake):
    """1 keeps its meaning: a failed bootstrap is a failed install, whatever
    the services folder is like, and the file is never tried."""
    launchctl_fake.knob("fail-bootstrap")
    host["services"].mkdir(parents=True)
    host["services"].chmod(0o555)
    try:
        rc, out, err = install(host["env"], ADDRESS)
    finally:
        host["services"].chmod(0o755)
    assert rc == _launchd.EX_FAILED == 1 and out == "" and "bootstrap" in err
    assert "service" not in err
    assert launchctl_fake.jobs() == {}


def test_the_help_page_names_exit_3():
    from click.testing import CliRunner
    from sessionmail.message import cli
    page = CliRunner().invoke(cli, ["seat", "agent", "--help"], prog_name="message").stdout
    flat = " ".join(page.split())
    assert ("· 3 done (--install's report printed) but its service file was not written: "
            "re-run") in flat, page
    assert "· 1 the agent or launchctl failed (--install rolls back) ·" in flat


def test_the_exit_codes_are_four_distinct_values():
    assert (_launchd.EX_OK, _launchd.EX_FAILED, _launchd.EX_REFUSED,
            _launchd.EX_UNDECLARED) == (0, 1, 2, 3)


@darwin
def test_a_symlink_at_the_target_is_replaced_never_followed(host, tmp_path):
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("not ours\n")
    host["file"].parent.mkdir(parents=True)
    host["file"].symlink_to(elsewhere)
    assert install(host["env"], ADDRESS)[0] == 0
    assert not host["file"].is_symlink()
    assert load(host["file"]) == expected(ADDRESS, True)
    assert elsewhere.read_text() == "not ours\n"


# ── uninstall ──────────────────────────────────────────────────────────────

@darwin
def test_uninstall_keeps_the_file_and_flips_enabled_false(host, launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == 0, err
    assert load(host["file"]) == expected(ADDRESS, False)
    jsonschema.validate(load(host["file"]), SCHEMA)
    assert err.splitlines()[-1] == f"message seat agent: service {host['file']} (enabled: false)"
    assert launchctl_fake.jobs() == {}
    # a second uninstall finds nothing installed, and the file stays false
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == 0 and f"nothing installed for {ADDRESS}" in err
    assert load(host["file"]) == expected(ADDRESS, False)
    # and a fresh install flips it back
    assert install(host["env"], ADDRESS)[0] == 0
    assert load(host["file"]) == expected(ADDRESS, True)


@darwin
def test_uninstall_of_a_job_with_no_file_yet_writes_one_false(host):
    """A job installed before this release: its uninstall declares it, off."""
    assert install(host["env"], ADDRESS)[0] == 0
    host["file"].unlink()
    assert uninstall(host["env"], ADDRESS)[0] == 0
    assert load(host["file"]) == expected(ADDRESS, False)


@darwin
def test_uninstall_with_nothing_installed_and_no_file_writes_nothing(host):
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == 0 and err == f"message seat agent: nothing installed for {ADDRESS}\n"
    assert not host["services"].exists()


@darwin
def test_a_failed_bootout_keeps_the_file_enabled(host, launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    launchctl_fake.knob("fail-bootout")
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == 1 and "bootout" in err
    assert load(host["file"]) == expected(ADDRESS, True)


@darwin
def test_an_uninstall_into_an_unwritable_folder_exits_3_with_the_job_gone(host,
                                                                          launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    host["services"].joinpath("seat-agents").chmod(0o555)
    try:
        rc, err = uninstall(host["env"], ADDRESS)
    finally:
        host["services"].joinpath("seat-agents").chmod(0o755)
    assert rc == _launchd.EX_UNDECLARED == 3, err
    assert f"booted out {LABEL}" in err
    assert "FAILED - service could not write" in err and "Permission denied" in err
    assert launchctl_fake.jobs() == {}, "the job is gone all the same"
    assert load(host["file"]) == expected(ADDRESS, True), "the previous file is unchanged"
    # the re-run the line asks for writes it: nothing is installed, the file is there
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == 0, err
    assert load(host["file"]) == expected(ADDRESS, False)


@darwin
def test_an_uninstall_whose_bootout_fails_is_1_even_with_services_unwritable(host,
                                                                             launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    launchctl_fake.knob("fail-bootout")
    host["services"].joinpath("seat-agents").chmod(0o555)
    try:
        rc, err = uninstall(host["env"], ADDRESS)
    finally:
        host["services"].joinpath("seat-agents").chmod(0o755)
    assert rc == _launchd.EX_FAILED == 1 and "bootout" in err, err
    assert LABEL in launchctl_fake.jobs()


@darwin
def test_an_uninstall_whose_write_fails_still_boots_the_job_out(host, monkeypatch,
                                                                launchctl_fake):
    assert install(host["env"], ADDRESS)[0] == 0
    def no_rename(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(_service_file.os, "replace", no_rename)   # uninstall renames nothing else
    rc, err = uninstall(host["env"], ADDRESS)
    assert rc == _launchd.EX_UNDECLARED == 3, err
    assert launchctl_fake.jobs() == {}
    assert load(host["file"]) == expected(ADDRESS, True), "the previous file is unchanged"
    assert err.splitlines()[-1].endswith(
        "the previous file is unchanged. The job is uninstalled; re-run --uninstall to "
        "write the file")


def test_the_docs_example_is_what_install_writes():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "message-seat.md").read_text()
    section = doc.split("### The service file", 1)[1]
    example = section.split("```json\n", 1)[1].split("```", 1)[0]
    assert example == _service_file.render(_launchd.service_record(ADDRESS, enabled=True))
