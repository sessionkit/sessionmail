"""`store-index`, the per-store notmuch tool (SM-731 design §1, §5, §13).

do-007: the profile template and `store-index profile`. It renders; it
refuses a root with no store marker, an overwrite of a differing file, and an
existing XDG profile; the rendered `[query]` has `paper = tag:unread`; and it
keeps `maildir.synchronize_flags=true` (OQ4, as ruled).

Every run of the tool gets an env built from nothing (HOME, PATH, and
XDG_CONFIG_HOME where a test sets it), so no real config, profile or mail is
reachable; every notmuch call is on a scratch database under tmp_path
(design §13 hygiene).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTMUCH_DIR = ROOT / "src" / "sessionmail" / "notmuch"
TOOL = NOTMUCH_DIR / "store-index"
TEMPLATE = NOTMUCH_DIR / "notmuch-config.store.template"
#: The hook's fallback list, read from the hook (as test_notmuch_post_new does).
NOTMUCH_PATHS = re.search(r'^NOTMUCH_PATHS="([^"]+)"$', (NOTMUCH_DIR / "post-new").read_text(),
                          re.M).group(1).split()
#: The binary the tool finds here (SM-796): notmuch on PATH, else the list's
#: first runnable. SESSIONMAIL_NOTMUCH is dropped for every test (below).
NOTMUCH = shutil.which("notmuch") or next(
    (p for p in NOTMUCH_PATHS if os.access(p, os.X_OK)), NOTMUCH_PATHS[0])
#: `~/.notmuch-config` on a live host (design §1, R1: "today's settings"), key by key.
TODAYS = {
    "new.tags": "unread;inbox;",
    "new.ignore": ".DS_Store;/^sent$/",
    "search.exclude_tags": "deleted;spam;",
    "maildir.synchronize_flags": "true",
    "query.memory": "tag:memory and not tag:superseded",
    "query.atoms": ("Type:requirement or Type:decision or Type:idea or Type:finding "
                    "or Type:question or Type:note"),
}
TODAYS_HEADERS = {
    "Task": "Task", "Project": "Project", "Supersedes": "Supersedes",
    "XSupersedes": "X-Supersedes", "XProject": "X-Project", "Type": "Type",
    "Store": "Store", "Status": "Status", "Session": "X-Session-From",
    "DeliveredTo": "Delivered-To", "XTask": "X-Task", "Refs": "References",
    "Verb": "Verb", "Noun": "Noun", "Asker": "Asker", "Askee": "Askee",
    "Channel": "Channel", "Name": "Name", "Parent": "Parent",
}


class Home:
    """A scratch HOME holding a store `acme` (its marker, a nested project
    box), and the env the tool runs under: built from nothing."""

    def __init__(self, tmp: Path, store: str = "acme"):
        self.home = tmp / "home"
        self.store = store
        self.root = self.home / ".mail" / "agents" / store
        for box in (self.root, self.root / "proj"):
            for sub in ("cur", "new", "tmp"):
                (box / sub).mkdir(parents=True)
        (self.root / ".tm-store").write_text("")
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        self.profile = self.home / f".notmuch-config.{store}"
        self.stores = self.home / ".mail" / ".notmuch" / "stores"

    def run(self, *argv: str, python: str = sys.executable) -> subprocess.CompletedProcess:
        return subprocess.run([python, str(TOOL), *argv], capture_output=True, text=True,
                              env=self.env, timeout=30)

    def listing(self) -> set[str]:
        return {str(p.relative_to(self.home)) for p in self.home.rglob("*")}


def keyfile(path: Path) -> dict[str, str]:
    """`section.key` -> value, as notmuch's GLib keyfile reads it: `#` lines
    are comments, `key=value` splits at the first `=`, nothing is unquoted."""
    out: dict[str, str] = {}
    section = ""
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        key, _, value = line.partition("=")
        out[f"{section}.{key}"] = value
    return out


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path)


# --- renders ----------------------------------------------------------------


def test_profile_renders_the_template_into_the_dot_profile_and_nothing_else(home):
    before = home.listing()
    r = home.run("profile", "acme")
    assert r.returncode == 0, r.stderr
    assert str(home.profile) in r.stdout
    assert home.listing() - before == {".notmuch-config.acme"}, \
        "profile writes ~/.notmuch-config.STORE only: no database, no hook dir"
    assert oct(home.profile.stat().st_mode & 0o777) == "0o644"
    cfg = keyfile(home.profile)
    assert cfg["database.path"] == str(home.stores / "acme")
    assert cfg["database.mail_root"] == str(home.root)
    assert cfg["database.hook_dir"] == str(home.stores / "acme" / "hooks")
    assert "${" not in home.profile.read_text(), "a placeholder was left unfilled"


def test_the_profile_keeps_todays_settings(home):
    """R1: today's [new], [search], [maildir], [index] and the [query] block."""
    assert home.run("profile", "acme").returncode == 0
    cfg = keyfile(home.profile)
    for key, value in TODAYS.items():
        assert cfg[key] == value, key
    headers = {k.split(".", 2)[2]: v for k, v in cfg.items() if k.startswith("index.header.")}
    assert headers == TODAYS_HEADERS


def test_paper_is_tag_unread_and_sync_stays_true(home):
    """The two criteria by name: `paper` loses `path:agents/**`, which can
    never match under a store root (E6); synchronize_flags stays true (OQ4)."""
    assert home.run("profile", "acme").returncode == 0
    cfg = keyfile(home.profile)
    assert cfg["query.paper"] == "tag:unread"
    assert cfg["maildir.synchronize_flags"] == "true"


def test_the_hook_keys_are_root_relative_and_never_star(home):
    """design §4: the store's form of the hook's two path queries."""
    assert home.run("profile", "acme").returncode == 0
    cfg = keyfile(home.profile)
    assert cfg["sessionmail.agent_query"] == "path:**"
    assert cfg["sessionmail.memory_query"] == "path:memory/**"
    assert cfg["sessionmail.agent_query"] != "*"   # E16: a parse error


def test_the_rendered_profile_is_one_notmuch_reads_and_indexes_by(home):
    """notmuch itself reads the rendered file: every key above as it parses
    it, then a `new --no-hooks` on the scratch store, whose paths are
    store-relative (E6) and whose `query:paper` counts the unread one."""
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}")
    assert home.run("profile", "acme").returncode == 0
    (home.root / "proj" / "new" / "1.M1.t").write_text(
        "From: a@example.invalid\nSubject: s\nMessage-ID: <m1@example.invalid>\n\nb\n")
    (home.root / "cur" / "2.M1.t:2,S").write_text(
        "From: a@example.invalid\nSubject: s\nMessage-ID: <m2@example.invalid>\n\nb\n")
    env = {**home.env, "XDG_CONFIG_HOME": str(home.home / ".xdg")}

    def nm(*args: str) -> str:
        run = subprocess.run([NOTMUCH, f"--config={home.profile}", *args],
                             capture_output=True, text=True, env=env, timeout=30)
        assert run.returncode == 0, run.stderr
        return run.stdout.strip()

    nm("new", "--no-hooks")
    assert (home.stores / "acme").is_dir()
    assert nm("config", "get", "maildir.synchronize_flags") == "true"
    assert nm("config", "get", "query.paper") == "tag:unread"
    assert nm("config", "get", "sessionmail.agent_query") == "path:**"
    assert nm("config", "get", "database.mail_root") == str(home.root)
    assert nm("count", "*") == "2"
    assert nm("count", "path:proj/**") == "1", "path: is relative to the store root (E6)"
    assert nm("count", "query:paper") == "1"
    assert not (home.stores / "acme" / "hooks").exists(), "the hook dir is never created"


def test_mail_root_is_one_parameter(home, tmp_path):
    """Q2: RULINGS 430's move is a one-line change, so mail_root is a flag."""
    other = tmp_path / "elsewhere" / "acme"
    (other / "cur").mkdir(parents=True)
    (other / ".tm-store").write_text("")
    r = home.run("profile", "acme", "--mail-root", str(other))
    assert r.returncode == 0, r.stderr
    assert keyfile(home.profile)["database.mail_root"] == str(other)


def test_the_system_python_runs_it_through_its_shebang(home):
    """An operator, or a launchd job with PATH=/usr/bin:/bin, runs the file
    itself: its `env python3` is the system one (3.9 on macOS)."""
    if not os.access("/usr/bin/python3", os.X_OK):
        pytest.skip("no /usr/bin/python3")
    assert os.access(TOOL, os.X_OK), "store-index lost its exec bit"
    r = subprocess.run([str(TOOL), "profile", "acme"], capture_output=True, text=True,
                       env=home.env, timeout=30)
    assert r.returncode == 0, r.stderr
    assert keyfile(home.profile)["query.paper"] == "tag:unread"


# --- refuses ----------------------------------------------------------------


def test_a_root_with_no_store_marker_is_refused_and_nothing_written(home):
    (home.root / ".tm-store").unlink()
    before = home.listing()
    r = home.run("profile", "acme")
    assert r.returncode == 1
    assert ".tm-store" in r.stderr and "nothing written" in r.stderr
    assert home.listing() == before


def test_a_missing_mail_root_is_refused(home, tmp_path):
    r = home.run("profile", "acme", "--mail-root", str(tmp_path / "typo"))
    assert r.returncode == 1
    assert "no store marker" in r.stderr
    assert not home.profile.exists()


def test_an_identical_profile_is_left_as_it_is(home):
    assert home.run("profile", "acme").returncode == 0
    stat = home.profile.stat()
    r = home.run("profile", "acme")
    assert r.returncode == 0 and "unchanged" in r.stdout
    assert home.profile.stat().st_mtime_ns == stat.st_mtime_ns


def test_a_differing_profile_is_not_overwritten_without_force(home):
    home.profile.write_text("[database]\npath=/somewhere/else\n")
    r = home.run("profile", "acme")
    assert r.returncode == 1
    assert "differs" in r.stderr and "--force" in r.stderr
    assert home.profile.read_text() == "[database]\npath=/somewhere/else\n"

    r = home.run("profile", "acme", "--force")
    assert r.returncode == 0, r.stderr
    assert keyfile(home.profile)["query.paper"] == "tag:unread"
    assert [p.name for p in home.home.glob(".notmuch-config.acme.*")] == [], \
        "the atomic write left its temp file"


@pytest.mark.parametrize("xdg", ["default", "set"])
@pytest.mark.parametrize("force", [False, True], ids=["plain", "force"])
def test_an_existing_xdg_profile_is_refused_even_with_force(home, tmp_path, xdg, force):
    """E19a: notmuch reads $XDG_CONFIG_HOME/notmuch/STORE/config first, so a
    dot profile written beside it would be shadowed. --force is about the
    dot file, not this."""
    if xdg == "set":
        base = tmp_path / "xdg"
        home.env["XDG_CONFIG_HOME"] = str(base)
    else:
        base = home.home / ".config"
    shadow = base / "notmuch" / "acme" / "config"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("[database]\npath=/x\n")
    r = home.run("profile", "acme", *(["--force"] if force else []))
    assert r.returncode == 1
    assert str(shadow) in r.stderr and "shadowed" in r.stderr
    assert not home.profile.exists()


@pytest.mark.parametrize("name", ["", ".", "..", ".acme", "a/b", "../acme", "a b"])
def test_a_bad_store_name_is_refused(home, name):
    r = home.run("profile", name)
    assert r.returncode == 1
    assert "is not a store name" in r.stderr
    assert not list(home.home.glob(".notmuch-config*"))


def test_no_verb_is_a_usage_error(home):
    assert home.run().returncode == 2


# --- one definition: the tool's copies agree with sessionmail's -------------


def test_the_tool_agrees_with_the_resolver(home):
    """The tool imports nothing from sessionmail (it must run from a pinned
    copy), so its two copied rules are pinned here: the store marker is
    `_boxes.STORE_MARKER`, and the XDG-first lookup is `_index.candidates`.
    And the file it writes is the one the resolver then resolves."""
    from sessionmail import _boxes, _index

    text = TOOL.read_text()
    assert f'STORE_MARKER = "{_boxes.STORE_MARKER}"' in text
    assert home.run("profile", "acme").returncode == 0
    for env in (home.env, {**home.env, "XDG_CONFIG_HOME": str(home.home / "x")}):
        xdg, dot = _index.candidates("acme", env)
        assert dot == home.profile
        found, _tried = _index.profile_file("acme", env)
        assert found == home.profile
        xdg.parent.mkdir(parents=True)
        xdg.write_text("")
        r = subprocess.run([sys.executable, str(TOOL), "profile", "acme"],
                           capture_output=True, text=True, env=env, timeout=30)
        assert r.returncode == 1 and str(xdg) in r.stderr, "the tool's XDG path is not the resolver's"
        shutil.rmtree(xdg.parent)


def test_the_template_ships_beside_the_tool():
    """The tool finds the template beside itself, so both live in
    src/sessionmail/notmuch/ (package data; do-006's wheel test lists them)."""
    assert TEMPLATE.is_file() and TOOL.is_file()
    assert "TEMPLATE = Path(__file__).resolve().parent / \"notmuch-config.store.template\"" \
        in TOOL.read_text()


# --- build and update (do-008: design §4.3, §5, §13) -------------------------
#
# SC4 on two fixture stores, SC5's listing under build, update and a rebuild
# with E20's rename shapes under a profile whose synchronize_flags is true,
# the tag copy's one changed key, the store's lock, a dangling and a removed
# link, and a planted NOTMUCH_DATABASE. Every notmuch call is on a scratch
# database under tmp_path, with an env built from nothing.

needs_notmuch = pytest.mark.skipif(not os.access(NOTMUCH, os.X_OK), reason=f"no {NOTMUCH}")
HOOK = NOTMUCH_DIR / "post-new"


@pytest.fixture(autouse=True)
def _real_notmuch(monkeypatch):
    """conftest points SESSIONMAIL_NOTMUCH at a fake engine for every test;
    the tool reads it first (SM-796) when it is loaded in process, and these
    tests run the real notmuch over scratch databases."""
    monkeypatch.delenv("SESSIONMAIL_NOTMUCH", raising=False)


def load_tool():
    """The tool as a module (it has no .py, and imports nothing from
    sessionmail), for the pure functions and an in-process failure."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("store_index_tool", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def mail(path: Path, msgid: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"From: a@example.invalid\nTo: b@example.invalid\nSubject: {msgid}\n"
                    f"Message-ID: <{msgid}@x>\nDate: Sun, 09 Jun 2030 12:34:56 +0000\n"
                    f"{extra}\nbody {msgid}\n")


def rename_shapes(root: Path, tag: str = "") -> None:
    """E20's shapes (design §13 do-008), in two boxes of one store:
    a cur/ file with no info, a new/ file with info, an id with a seen copy
    in one box and an unseen one in another, and Supersedes:/X-Supersedes:
    carriers in cur/ with no info (their targets too, one in new/)."""
    for box in ("p1", "p2"):
        for sub in ("cur", "new", "tmp"):
            (root / box / sub).mkdir(parents=True, exist_ok=True)
    t = tag
    mail(root / "p1" / "new" / f"a{t}", f"a{t}")
    mail(root / "p1" / "cur" / f"b{t}:2,", f"b{t}")
    mail(root / "p1" / "cur" / f"c{t}", f"c{t}")                      # cur/, no info
    mail(root / "p1" / "new" / f"g{t}:2,", f"g{t}")                   # new/, with info
    mail(root / "p1" / "cur" / f"e1{t}:2,S", f"e{t}")                 # seen copy
    mail(root / "p2" / "new" / f"e2{t}", f"e{t}")                     # unseen copy, other box
    mail(root / "p1" / "new" / f"t{t}", f"t{t}")                      # a target, in new/
    mail(root / "p1" / "cur" / f"s{t}", f"s{t}", f"Supersedes: <t{t}@x>\n")       # carrier
    mail(root / "p2" / "cur" / f"o{t}:2,", f"o{t}")                   # a target
    mail(root / "p2" / "cur" / f"x{t}", f"x{t}", f"X-Supersedes: <o{t}@x>\n")     # old spelling


class Stores(Home):
    """A scratch HOME with a built-ready store (a profile rendered by the
    tool) holding E20's shapes, and helpers over its index."""

    def __init__(self, tmp: Path, store: str = "acme"):
        super().__init__(tmp, store)
        rename_shapes(self.root)
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".xdg")
        r = self.run("profile", store)
        assert r.returncode == 0, r.stderr

    def add_store(self, store: str) -> tuple[Path, Path]:
        root = self.home / ".mail" / "agents" / store
        root.mkdir(parents=True)
        (root / ".tm-store").write_text("")
        rename_shapes(root, tag=store)
        r = self.run("profile", store)
        assert r.returncode == 0, r.stderr
        return root, self.home / f".notmuch-config.{store}"

    @property
    def link(self) -> Path:
        return self.stores / self.store

    def gens(self, store: str | None = None) -> list[str]:
        prefix = f".{store or self.store}."
        return sorted(p.name for p in self.stores.iterdir()
                      if p.name.startswith(prefix) and p.is_dir() and not p.is_symlink()
                      and re.fullmatch(r"\d{8}T\d{6}\.\d{6}", p.name[len(prefix):]))

    def tree(self, root: Path | None = None) -> list[str]:
        """SC5's listing: every path under the store root, files and dirs."""
        root = root or self.root
        return sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    def nm(self, *args: str, profile: Path | None = None) -> str:
        run = subprocess.run([NOTMUCH, f"--config={profile or self.profile}", *args],
                             capture_output=True, text=True, env=self.env, timeout=30)
        assert run.returncode == 0, run.stderr
        return run.stdout.strip()

    def state(self, profile: Path | None = None) -> tuple[str, list[str]]:
        """SC4's pair: the count and the +superseded set."""
        return (self.nm("count", "--exclude=false", "*", profile=profile),
                sorted(self.nm("search", "--output=messages", "tag:superseded",
                               profile=profile).split()))

    def ok(self, *argv: str) -> str:
        r = self.run(*argv)
        assert r.returncode == 0, f"store-index {' '.join(argv)}: rc {r.returncode}: {r.stderr}"
        return r.stdout


@pytest.fixture
def stores(tmp_path):
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}")
    return Stores(tmp_path)


SUPERSEDED = ["id:o@x", "id:t@x"]


@needs_notmuch
def test_sc4_rebuild_after_removing_the_generation_restores_count_and_superseded(stores):
    """SC4 on two fixture stores: remove one store's index (its generation,
    which leaves stores/acme dangling), build again, and the count and the
    +superseded set come back; the other store is not touched."""
    beta_root, beta_profile = stores.add_store("beta")
    stores.ok("build", "acme")
    stores.ok("build", "beta")
    before = stores.state()
    assert before == ("9", SUPERSEDED), "both carrier spellings re-derive superseded"
    assert stores.nm("count", "tag:agent") == "9", "agent_query=path:** tags every message"
    beta_before, beta_gen = stores.state(beta_profile), stores.gens("beta")

    [gen] = stores.gens()
    shutil.rmtree(stores.stores / gen)
    assert stores.link.is_symlink() and not stores.link.exists(), "the link now dangles"
    stores.ok("build", "acme")

    assert stores.state() == before
    [new_gen] = stores.gens()
    assert new_gen != gen and os.readlink(stores.link) == new_gen
    assert stores.gens("beta") == beta_gen and stores.state(beta_profile) == beta_before


@needs_notmuch
def test_a_removed_link_rebuilds_and_leaves_no_orphan_generation(stores):
    """SC4 done by removing the link itself: the old generation would be an
    orphan, so build removes every other generation of the store (design §5)."""
    stores.ok("build", "acme")
    before, [old] = stores.state(), stores.gens()
    stores.link.unlink()
    out = stores.ok("build", "acme")
    [new] = stores.gens()
    assert new != old and old in out, "the orphan is named as removed"
    assert stores.state() == before
    assert sorted(p.name for p in stores.stores.iterdir()) == sorted(
        [".acme.lock", "acme", new]), "no copy, temp link or generation is left behind"


@needs_notmuch
def test_a_rebuild_swaps_the_link_and_removes_the_old_generation(stores):
    stores.ok("build", "acme")
    [old] = stores.gens()
    out = stores.ok("build", "acme")
    [new] = stores.gens()
    assert new != old and os.readlink(stores.link) == new
    assert f"removed: {old}" in out and "count 9  superseded 2" in out


@needs_notmuch
def test_sc5_no_maildir_path_changes_under_build_update_and_rebuild(stores):
    """SC5 with E20's shapes under synchronize_flags=true (design §5, §13):
    the listing is the same after build, after update (with new mail in each
    rename shape) and after a rebuild. P still says true, byte for byte."""
    profile_text = stores.profile.read_text()
    tree = stores.tree()
    stores.ok("build", "acme")
    assert stores.tree() == tree, "build renamed a maildir file"

    mail(stores.root / "p1" / "cur" / "h", "h")                    # cur/, no info
    mail(stores.root / "p2" / "new" / "i:2,", "i")                 # new/, with info
    mail(stores.root / "p2" / "new" / "b2", "b")                   # unseen copy of a cur/ id
    mail(stores.root / "p2" / "cur" / "s2", "s2", "Supersedes: <a@x>\n")   # carrier, target in new/
    tree = stores.tree()
    stores.ok("update", "acme")
    assert stores.tree() == tree, "update renamed a maildir file"
    assert stores.state() == ("12", ["id:a@x", "id:o@x", "id:t@x"])

    stores.ok("build", "acme")
    assert stores.tree() == tree, "the rebuild renamed a maildir file"
    assert stores.state() == ("12", ["id:a@x", "id:o@x", "id:t@x"])
    assert stores.profile.read_text() == profile_text
    assert stores.nm("config", "get", "maildir.synchronize_flags") == "true"


@needs_notmuch
def test_the_fixture_is_not_inert_the_same_pass_under_p_renames(stores):
    """The control (E20, E22 part C): `new` and the hook both under P's
    synchronize_flags=true rename files in this fixture. Without it, SC5's
    0-renames would also pass on a tree with none of the shapes."""
    tree = stores.tree()
    env = {k: v for k, v in stores.env.items()}
    run = subprocess.run([NOTMUCH, f"--config={stores.profile}", "new", "--no-hooks", "--quiet"],
                         env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert stores.tree() == tree, "notmuch new under true renames nothing (E20)"
    hook = subprocess.run([str(HOOK)], capture_output=True, text=True, timeout=60,
                          env={**env, "NOTMUCH_CONFIG": str(stores.profile),
                               "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    assert hook.returncode == 0, hook.stderr
    renamed = set(tree) ^ set(stores.tree())
    assert {"p1/cur/c", "p1/cur/c:2,", "p1/new/g:2,", "p1/cur/g:2,",
            "p2/new/e2", "p2/cur/e2:2,S"} <= renamed, sorted(renamed)


@needs_notmuch
def test_notmuch_new_runs_under_p_so_maildir_flags_become_tags(stores):
    """The hook spec: `notmuch new` SHALL run under P (synchronize_flags=true);
    only the tag pass runs under the copy. E22 part D names what tells them
    apart: under false, `F` and `R` never become `flagged` and `replied`. So
    this pins `new` to P in build, in update and in a rebuild (review
    bef71c21: a mutant running `new` under the tag copy passed every other
    test). Nothing is renamed: each file's info already matches its flags."""
    mail(stores.root / "p1" / "cur" / "f:2,F", "f")
    tree = stores.tree()
    stores.ok("build", "acme")
    assert stores.nm("search", "--output=messages", "tag:flagged") == "id:f@x"
    assert stores.tree() == tree
    mail(stores.root / "p2" / "cur" / "r:2,RS", "r")
    tree = stores.tree()
    stores.ok("update", "acme")
    assert stores.nm("search", "--output=messages", "tag:replied") == "id:r@x"
    assert stores.tree() == tree
    stores.ok("build", "acme")
    assert sorted(stores.nm("search", "--output=messages", "tag:flagged or tag:replied")
                  .split()) == ["id:f@x", "id:r@x"]
    assert stores.tree() == tree


@needs_notmuch
def test_the_tag_copy_differs_from_p_in_exactly_one_key(stores):
    """design §13: the tag copy is P with maildir.synchronize_flags=false and
    nothing else; P keeps true. Measured by notmuch's own `config list`, so a
    0-renames pass cannot come from a template that turned sync off."""
    stores.ok("build", "acme")
    stores.ok("update", "acme")
    copy = stores.stores / ".acme.tag.cfg"

    def listed(config: Path) -> dict[str, str]:
        return dict(line.split("=", 1) for line in stores.nm("config", "list", profile=config)
                    .splitlines())

    p, c = listed(stores.profile), listed(copy)
    assert {k for k in p.keys() | c.keys() if p.get(k) != c.get(k)} == {"maildir.synchronize_flags"}
    assert (p["maildir.synchronize_flags"], c["maildir.synchronize_flags"]) == ("true", "false")

    # build's copies are transient (removed after the run), so they are
    # derived here by the tool's own functions, at the live generation:
    # the scratch copy moves database.path only; its tag copy, sync only.
    tool = load_tool()
    gen = stores.stores / stores.gens()[0]
    base = stores.home / "base.cfg"
    base.write_text(tool.set_key(stores.profile.read_text(), "database", "path", str(gen)))
    tagc = stores.home / "tag.cfg"
    tagc.write_text(tool.tag_copy(base.read_text(), stores.profile))
    b, t = listed(base), listed(tagc)
    # The FILES differ in database.path alone (notmuch's list also moves
    # database.backup_dir, a default it derives from the path).
    pf, bf = keyfile(stores.profile), keyfile(base)
    assert {k for k in pf.keys() | bf.keys() if pf.get(k) != bf.get(k)} == {"database.path"}
    assert b["database.path"] == str(gen) and b["database.backup_dir"].startswith(str(gen))
    assert {k for k in b.keys() | t.keys() if b.get(k) != t.get(k)} == {"maildir.synchronize_flags"}
    assert t["maildir.synchronize_flags"] == "false"


def test_set_key_replaces_in_place_adds_to_a_section_or_appends_one():
    tool = load_tool()
    text = "[maildir]\n# synchronize_flags=true\nsynchronize_flags = true\n[x]\nsynchronize_flags=1\n"
    out = tool.set_key(text, "maildir", "synchronize_flags", "false")
    assert out == "[maildir]\n# synchronize_flags=true\nsynchronize_flags=false\n[x]\nsynchronize_flags=1\n"
    assert tool.set_key("[maildir]\n[y]\n", "maildir", "k", "v") == "[maildir]\nk=v\n[y]\n"
    assert tool.set_key("[y]\na=b\n", "maildir", "k", "v") == "[y]\na=b\n\n[maildir]\nk=v\n"


@needs_notmuch
def test_a_second_concurrent_run_is_refused_by_the_stores_lock(stores):
    """One run at a time per store (design §5): while stores/.acme.lock is
    held, build and update are refused and write nothing. Another store's
    lock is its own (R3)."""
    import fcntl
    stores.ok("build", "acme")
    beta_root, _ = stores.add_store("beta")
    listing = sorted(p.name for p in stores.stores.iterdir())
    with open(stores.stores / ".acme.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for verb in ("build", "update"):
            r = stores.run(verb, "acme")
            assert r.returncode == 1, (verb, r.stderr)
            assert ".acme.lock is held" in r.stderr and "nothing written" in r.stderr
            assert sorted(p.name for p in stores.stores.iterdir()) == listing
        stores.ok("build", "beta")   # only acme's lock is held
    stores.ok("update", "acme")      # released


@needs_notmuch
def test_a_planted_notmuch_database_never_reaches_notmuch(stores, tmp_path):
    """E14: an inherited NOTMUCH_DATABASE beats --config, and `new` would
    rewrite the database it names. Planted (with NOTMUCH_CONFIG and
    NOTMUCH_PROFILE) on a decoy, it changes nothing there through build or
    update, the `new` children or the hook's, and the store is indexed."""
    decoy = tmp_path / "decoy"
    mail(decoy / "mail" / "box" / "new" / "d", "decoy")
    for sub in ("cur", "tmp"):
        (decoy / "mail" / "box" / sub).mkdir()
    cfg = decoy / "config"
    cfg.write_text(f"[database]\npath={decoy / 'db'}\nmail_root={decoy / 'mail'}\n"
                   "[new]\ntags=unread\n")
    (stores.home / ".notmuch-config.decoy").write_text(cfg.read_text())
    clean = dict(stores.env)
    stores.nm("new", "--no-hooks", profile=cfg)
    probe = stores.nm("count", "--lastmod", "*", profile=cfg)
    stores.env.update({"NOTMUCH_DATABASE": str(decoy / "db"), "NOTMUCH_CONFIG": str(cfg),
                       "NOTMUCH_PROFILE": "decoy"})
    stores.ok("build", "acme")
    mail(stores.root / "p1" / "cur" / "h", "h")
    stores.ok("update", "acme")
    stores.env = clean
    assert stores.nm("count", "--lastmod", "*", profile=cfg) == probe, "the decoy was written"
    assert stores.nm("count", "tag:agent", profile=cfg) == "0", "the hook tagged the decoy"
    assert stores.state() == ("10", SUPERSEDED)
    assert stores.nm("count", "tag:agent") == "10"


def test_every_child_env_drops_the_three_and_the_hook_gets_the_copy():
    tool = load_tool()
    env = {"HOME": "/h", "PATH": "/p", "NOTMUCH_CONFIG": "c", "NOTMUCH_DATABASE": "d",
           "NOTMUCH_PROFILE": "p", "KEEP": "k"}
    assert tool.child_env(env) == {"HOME": "/h", "PATH": "/p", "KEEP": "k"}
    assert tool.hook_env(env, Path("/s/.acme.tag.cfg")) == {
        "HOME": "/h", "KEEP": "k", "NOTMUCH_CONFIG": "/s/.acme.tag.cfg",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "SESSIONMAIL_NOTMUCH": tool.NOTMUCH}


@needs_notmuch
def test_update_with_no_built_index_is_refused(stores):
    for state in ("missing", "dangling"):
        if state == "dangling":
            stores.ok("build", "acme")
            shutil.rmtree(stores.stores / stores.gens()[0])
        r = stores.run("update", "acme")
        assert r.returncode == 1 and "build acme` first" in r.stderr, (state, r.stderr)
        assert not stores.link.exists(), "a bare new would have made stores/acme a directory"


def test_build_and_update_refuse_a_missing_profile_and_write_nothing(home):
    before = home.listing()
    for verb in ("build", "update"):
        r = home.run(verb, "acme")
        assert r.returncode == 1 and "no profile for acme" in r.stderr, r.stderr
    assert home.listing() == before, "no stores/ dir, no lock file"


@pytest.mark.parametrize("path", ["{home}/.mail/.notmuch/xapian", "{home}/.mail/.notmuch/stores/other", ""])
def test_build_refuses_a_profile_whose_database_is_not_its_own(home, path):
    """The tool writes only stores/STORE: a profile edited to name the host
    index (or another store's) is refused before anything is written."""
    assert home.run("profile", "acme").returncode == 0
    text = home.profile.read_text()
    home.profile.write_text(re.sub(r"(?m)^path=.*$", f"path={path.format(home=home.home)}", text))
    before = home.listing()
    for verb in ("build", "update"):
        r = home.run(verb, "acme")
        assert r.returncode == 1 and "database.path" in r.stderr, r.stderr
    assert home.listing() == before


def test_build_refuses_a_mail_root_with_no_marker(home):
    assert home.run("profile", "acme").returncode == 0
    (home.root / ".tm-store").unlink()
    r = home.run("build", "acme")
    assert r.returncode == 1 and ".tm-store" in r.stderr
    assert not home.stores.exists()


@needs_notmuch
def test_build_refuses_a_stores_entry_that_is_not_its_link(stores):
    """A bare `notmuch new` under the profile makes stores/acme a directory.
    The tool did not make it, so it neither replaces nor removes it."""
    stores.nm("new", "--no-hooks")
    assert stores.link.is_dir() and not stores.link.is_symlink()
    r = stores.run("build", "acme")
    assert r.returncode == 1 and "is not a link" in r.stderr
    assert stores.gens() == [] and stores.link.is_dir()


@needs_notmuch
def test_a_failed_build_removes_its_generation_and_keeps_the_live_link(stores, capsys):
    stores.ok("build", "acme")
    [live] = stores.gens()
    tool = load_tool()
    tool.NOTMUCH = "/usr/bin/false"
    assert tool.main(["build", "acme"], env=dict(stores.env)) == tool.EX_FAILED
    assert "failed: notmuch new" in capsys.readouterr().err
    assert stores.gens() == [live] and os.readlink(stores.link) == live
    assert sorted(p.name for p in stores.stores.iterdir()) == sorted([".acme.lock", "acme", live])
    assert stores.state() == ("9", SUPERSEDED)


def test_generations_match_only_this_stores_stamped_dirs(tmp_path):
    """build removes `.STORE.<stamp>` directories only: never the lock, the
    copies, a link, or another store's generation (`.acme.x.<stamp>`)."""
    tool = load_tool()
    env = {"HOME": str(tmp_path)}
    s = tool.stores_dir(env)
    s.mkdir(parents=True)
    keep = [".acme.lock", ".acme.tag.cfg", ".acme.x.20301208T233456.000001", "acme",
            ".acme.20301208T233456", ".acme.old"]
    for name in keep:
        (s / name).mkdir()
    (s / ".acme.20301208T233456.000009").symlink_to("acme")
    (s / ".acme.20301208T233456.000002").mkdir()
    assert [p.name for p in tool.generations("acme", env)] == [".acme.20301208T233456.000002"]


def test_the_tool_and_the_hook_run_one_notmuch_and_scrub_what_the_resolver_scrubs():
    from sessionmail import _index
    tool = load_tool()
    assert tool.NOTMUCH == NOTMUCH, "the tool did not find this host's notmuch"
    assert list(tool.NOTMUCH_PATHS) == NOTMUCH_PATHS, "the tool's list is not the hook's"
    assert tool.hook_env({}, Path("/c"))["SESSIONMAIL_NOTMUCH"] == tool.NOTMUCH, \
        "the hook would not run the tool's notmuch"
    assert tuple(tool.SCRUBBED) == tuple(_index.SCRUBBED)
    assert tool.HOOK == HOOK.resolve() and os.access(HOOK, os.X_OK)



def test_the_tool_finds_notmuch_the_way_the_hook_does(tmp_path, monkeypatch):
    """SM-796: no hardcoded binary. On a scratch PATH it finds THAT notmuch,
    at load as well as by find_notmuch; the setting wins (a bare name looked
    up on PATH); and a PATH with none falls to the hook's list."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("notmuch", "nm-by-name"):
        (bindir / name).write_text("#!/bin/sh\nexit 0\n")
        (bindir / name).chmod(0o755)
    tool = load_tool()
    scratch = {"PATH": str(bindir)}
    assert tool.find_notmuch(scratch) == str(bindir / "notmuch")
    assert tool.find_notmuch({**scratch, "SESSIONMAIL_NOTMUCH": "nm-by-name"}) == \
        str(bindir / "nm-by-name")
    assert tool.find_notmuch({**scratch, "SESSIONMAIL_NOTMUCH": "/abs/nm"}) == "/abs/nm"
    listed = next((p for p in NOTMUCH_PATHS if os.access(p, os.X_OK)), "notmuch")
    assert tool.find_notmuch({"PATH": str(tmp_path / "empty")}) == listed
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    assert load_tool().NOTMUCH == str(bindir / "notmuch"), "not found at load"

# --- count: SC1's scan (do-009: design §6, §13; E17, E19) --------------------
#
# Every case §13 names, in one store, built by the tool: the scan's id SET
# must equal `notmuch search --output=messages` and its size `count
# --exclude=false`, and each case is pinned by name, so a rule that drifts
# from notmuch's walk fails here and not in a live SC1.

HDR = "From: a@example.invalid\nTo: b@example.invalid\nSubject: s\n"


def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Counted(Home):
    """A store holding every §13 do-009 case, its profile rendered and its
    index built by the tool, with an env built from nothing."""

    def __init__(self, tmp: Path):
        super().__init__(tmp)
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".xdg")
        r, p = self.root, self.root / "proj"
        put(p / "new" / "plain", HDR + "Message-ID: <plain@x>\n\nb\n")
        put(r / "cur" / "dup1:2,S", HDR + "Message-ID: <dup@x>\n\nb\n")         # a duplicate
        put(p / "new" / "dup2", HDR + "Message-ID: <dup@x>\n\nb\n")             # across boxes
        put(p / "cur" / "folded:2,", HDR + "Message-ID:\n <folded@x>\n\nb\n")   # folded
        put(p / "new" / "comment", HDR + "Message-ID: <cmt@x> (comment)\n\nb\n")
        put(p / "new" / "bare", HDR + "Message-ID: bare-id@x\n\nb\n")           # no brackets
        # unbracketed and folded: notmuch drops the line break, keeps the blanks
        put(p / "new" / "fold1", HDR + "Message-ID: foo\n bar@x\n\nb\n")
        put(p / "new" / "fold2", HDR + "Message-ID: one\n\ttwo@x\n\nb\n")
        put(p / "new" / "fold3", HDR + "Message-ID:  spaced  id@x  \n\nb\n")
        self.sha1 = put(p / "new" / "noid", "From: a@example.invalid\nSubject: no id\n\nb\n")
        # non-mail: no From, To or Subject header (E19c)
        put(p / "new" / "idonly", "Message-ID: <idonly@x>\n\nb\n")
        put(p / "new" / "cconly", "Cc: c@example.invalid\nMessage-ID: <cconly@x>\n\nb\n")
        put(p / "new" / "custom", "X-Custom: 1\nDate: Sun, 09 Jun 2030 12:34:56 +0000\n\nb\n")
        put(p / "new" / "noheaders", "just a body line\nand another\n")
        put(p / "new" / "empty", "")
        # the walk (E17, E19e, E1)
        put(p / "tmp" / "intmp", HDR + "Message-ID: <intmp@x>\n\nb\n")         # maildir tmp/: skipped
        put(r / ".Sent" / "cur" / "dot", HDR + "Message-ID: <dotfolder@x>\n\nb\n")
        put(p / "loose", HDR + "Message-ID: <loose@x>\n\nb\n")                  # outside cur/new
        put(r / "odd" / "tmp" / "m", HDR + "Message-ID: <oddtmp@x>\n\nb\n")    # tmp/ of a non-maildir
        put(r / "sent" / "cur" / "m", HDR + "Message-ID: <topsent@x>\n\nb\n")  # /^sent$/: ignored
        put(r / "deep" / "sent" / "m", HDR + "Message-ID: <deepsent@x>\n\nb\n")
        put(p / "cur" / ".DS_Store", HDR + "Message-ID: <dsstore@x>\n\nb\n")   # plain ignore entry
        put(r / ".notmuch" / "m", HDR + "Message-ID: <topnotmuch@x>\n\nb\n")  # root's: skipped
        put(p / ".notmuch" / "m", HDR + "Message-ID: <dotnotmuch@x>\n\nb\n")   # nested: indexed
        outside = self.home / ".mail" / "agents" / "sess1"
        put(outside / "new" / "m", HDR + "Message-ID: <linked@x>\n\nb\n")
        for sub in ("cur", "tmp"):
            (outside / sub).mkdir()
        (r / "sess1").symlink_to("../sess1")                                   # followed (E1)
        for argv in (("profile", "acme"), ("build", "acme")):
            done = self.run(*argv)
            assert done.returncode == 0, done.stderr

    def nm(self, *args: str, config: Path | None = None) -> str:
        run = subprocess.run([NOTMUCH, f"--config={config or self.profile}", *args],
                             capture_output=True, text=True, env=self.env, timeout=30)
        assert run.returncode == 0, run.stderr
        return run.stdout.strip()


@pytest.fixture
def counted(tmp_path):
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}")
    return Counted(tmp_path)


def expected_ids(counted: Counted) -> set[str]:
    import hashlib
    return {"plain@x", "dup@x", "folded@x", "cmt@x", "bare-id@x",
            "notmuch-sha1-" + hashlib.sha1(counted.sha1.read_bytes()).hexdigest(),
            "dotfolder@x", "loose@x", "oddtmp@x", "deepsent@x", "linked@x", "dotnotmuch@x",
            "foo bar@x", "one\ttwo@x", "spaced  id@x"}


@needs_notmuch
def test_the_scan_id_set_is_notmuchs_id_set_case_by_case(counted):
    """§13 do-009: the scan's id SET equals `notmuch search --output=messages`
    (not just the count), and both are exactly the cases' expected ids."""
    import json
    tool = load_tool()
    scanned = tool.scan(counted.root, ".DS_Store;/^sent$/")
    indexed = set(json.loads(counted.nm("search", "--format=json", "--output=messages",
                                        "--exclude=false", "*")))
    assert scanned.ids == indexed == expected_ids(counted)
    assert counted.nm("count", "--exclude=false", "*") == str(len(scanned.ids)) == "15"
    assert sorted(scanned.nonmail) == sorted([
        ".tm-store", "proj/new/idonly", "proj/new/cconly", "proj/new/custom",
        "proj/new/noheaders", "proj/new/empty"])


@needs_notmuch
def test_count_prints_the_bracket_and_a_quiet_window_pass(counted):
    r = counted.run("count", "acme")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    iso = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"
    assert re.fullmatch(rf"scan-before  {iso}  files=\d+  ids=15", lines[0]), lines[0]
    assert re.fullmatch(rf"index        {iso}  15", lines[1]), lines[1]
    assert re.fullmatch(rf"scan-after   {iso}  files=\d+  ids=15", lines[2]), lines[2]
    assert lines[3] == "set difference: scan-only 0, index-only 0"
    assert "non-mail (not counted): 6" in lines and "  proj/new/idonly" in lines
    assert lines[-1].startswith("acme: PASS (quiet window)")
    assert str(counted.root) in lines[-1]


@needs_notmuch
def test_count_ignores_exclude_tags(counted):
    """search.exclude_tags (deleted;spam;) applies to count, so a tagged
    message would make a correct index look short without --exclude=false."""
    tool = load_tool()
    copy = put(counted.home / "nosync.cfg", tool.tag_copy(counted.profile.read_text(),
                                                         counted.profile))
    counted.nm("tag", "+deleted", "--", "id:plain@x", config=copy)   # sync off: renames nothing
    assert counted.nm("count", "*") == "14", "the exclude applies without the flag"
    r = counted.run("count", "acme")
    assert r.returncode == 0 and "acme: PASS (quiet window)" in r.stdout, r.stdout


@needs_notmuch
def test_count_fails_and_names_the_ids_when_scan_and_index_disagree(counted):
    put(counted.root / "proj" / "new" / "late", HDR + "Message-ID: <late@x>\n\nb\n")
    (counted.root / "proj" / "loose").unlink()
    r = counted.run("count", "acme")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "set difference: scan-only 1, index-only 1" in r.stdout
    assert "  scan-only   late@x" in r.stdout and "  index-only  loose@x" in r.stdout
    assert r.stdout.splitlines()[-1].startswith("acme: FAIL")


@needs_notmuch
def test_mail_landing_inside_the_bracket_is_a_pass_not_a_quiet_one(counted, capsys):
    """design §6: PASS when ids(before) <= index <= ids(after). A message that
    lands between the first scan and the count (or the count and the second
    scan) is the live tree's bracket, not a failure (E9's one arrival)."""
    tool = load_tool()
    real = tool.index_ids

    def arrives_after(*a, **k):
        got = real(*a, **k)
        put(counted.root / "proj" / "new" / "arrival", HDR + "Message-ID: <arrival@x>\n\nb\n")
        return got

    tool.index_ids = arrives_after
    assert tool.main(["count", "acme"], env=dict(counted.env)) == 0
    out = capsys.readouterr().out
    assert "ids=15" in out.splitlines()[0] and "ids=16" in out.splitlines()[2]
    assert out.splitlines()[-1].startswith("acme: PASS  ")


def test_mail_id_follows_notmuchs_rules(tmp_path):
    """E19c-d at the function: mail needs From, To or Subject; the id is the
    first <...> of the first Message-ID, else its unfolded value, else the
    file's sha1."""
    import hashlib
    tool = load_tool()
    cases = {
        HDR + "Message-ID: <a@b> (comment)\nMessage-ID: <second@b>\n\nx\n": "a@b",
        "To: t@x\nMessage-ID:\n  <folded@b>\n\nx\n": "folded@b",
        "To: t@x\nMessage-ID: one\n\ttwo@b\n\nx\n": "one\ttwo@b",
        "Subject: s\nMessage-ID: bare@x\n\nx\n": "bare@x",
        "Subject: s\nMessage-ID:   \n\nx\n": None,     # blank: the sha1 (below)
        "Message-ID: <a@b>\n\nx\n": None,
        "": None,
    }
    for i, (text, want) in enumerate(cases.items()):
        f = put(tmp_path / f"m{i}", text)
        got = tool.mail_id(str(f))
        if "Subject: s\nMessage-ID:   " in text:
            assert got == "notmuch-sha1-" + hashlib.sha1(f.read_bytes()).hexdigest()
        else:
            assert got == want, text


#: Message-ID values (after `Message-ID: `) whose ids notmuch 0.40 derives by
#: _parse_message_id's quirks (review bef71c21, each measured on a scratch
#: index before it was pinned here).
PARSER_EDGES = {
    "emptybr": b"<>", "blankbr": b"<  >", "one": b"<x>", "two": b"<ab>",
    "unclosed": b"<unclosed@x", "unclosed1": b"<y", "innersp": b"<a b@x>",
    "dbl": b"<c  d@x>", "mix": b"<e \tf@x>", "tabs": b"<\tt@x\t>",
    "foldin": b"<fold\n in@x>", "textfirst": b"junk <tf@x>", "closefirst": b">x< <cf@x>",
    "cmtinside": b"<(c)ci@x>", "nested": b"((a)b) <n@x>", "esc": b"(a\\)b) <e@x>",
    "cmtonly": b"(just a comment)", "barecmt": b"(c) bare2@x", "twoids": b"<one@x> <two@x>",
    "utf8": "<café@x>".encode(), "latin1": b"<cafe\xe9@x>",
    "utf8bare": "cafébare@x".encode(), "crlf": b"<crlf@x>",
    # C's isspace(), not Python's (do-009 rework, measured): kept in the id
    "fs": b"<\x1cfs@x>", "nbsp": "<\u00a0nb@x>".encode(), "vt": b"<\x0bvt@x>",
}


@needs_notmuch
def test_the_scan_matches_notmuch_file_by_file_on_message_id_parser_edges(tmp_path):
    """Beyond §13's cases: notmuch's id parser (comments, text before `<`, an
    unclosed or one-character id, blanks inside, Latin-1 and UTF-8) and its
    mail test (a blank From/To/Subject is not one). The scan must give each
    FILE the id notmuch gives it, and call non-mail exactly what notmuch
    skips (review bef71c21: before the port, 7 of these disagreed)."""
    tool = load_tool()
    root, db = tmp_path / "root", tmp_path / "db"
    box = root / "box"
    for sub in ("cur", "new", "tmp"):
        (box / sub).mkdir(parents=True)
    db.mkdir()
    hdr = b"From: a@x\nTo: b@x\nSubject: s\n"
    for name, value in PARSER_EDGES.items():
        text = hdr + b"Message-ID: " + value + b"\n\nbody " + name.encode() + b"\n"
        (box / "new" / name).write_bytes(text.replace(b"\n", b"\r\n") if name == "crlf" else text)
    for name, head in {"blankfrom": b"From:   \n", "blanksubj": b"Subject:\n",
                       "blankto_okfrom": b"To:\nFrom: a@x\n"}.items():
        (box / "new" / name).write_bytes(head + b"Message-ID: <" + name.encode() + b"@x>\n\nb\n")
    cfg = tmp_path / "cfg"
    cfg.write_text(f"[database]\npath={db}\nmail_root={root}\n[new]\ntags=unread\n"
                   "[maildir]\nsynchronize_flags=false\n")
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def nm(*args: str) -> bytes:
        done = subprocess.run([NOTMUCH, f"--config={cfg}", *args], env=env,
                              capture_output=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return done.stdout

    nm("new", "--quiet")

    def walk(x):
        if isinstance(x, dict) and "id" in x and "filename" in x:
            yield x
        elif isinstance(x, (list, dict)):
            for y in (x.values() if isinstance(x, dict) else x):
                yield from walk(y)

    import json
    by_notmuch = {}
    for m in walk(json.loads(nm("show", "--format=json", "--body=false", "*"))):
        for f in m["filename"]:
            by_notmuch[Path(f).name] = m["id"]
    by_scan = {p.name: tool.mail_id(str(p)) for p in sorted((box / "new").iterdir())}
    assert {n: i for n, i in by_scan.items() if i is not None} == by_notmuch
    assert {n for n, i in by_scan.items() if i is None} == {"blankfrom", "blanksubj"}
    # A few spelled out, so the quirks are readable here:
    assert by_notmuch["one"] == "<x>" and by_notmuch["emptybr"] == "<>"
    assert by_notmuch["unclosed"] == "unclosed@x" and by_notmuch["closefirst"] == "<cf@x"
    assert by_notmuch["innersp"] == "ab@x" and by_notmuch["dbl"] == "c d@x"
    assert by_notmuch["latin1"] == "cafeé@x" and by_notmuch["blankto_okfrom"] == "blankto_okfrom@x"
    assert (by_notmuch["fs"], by_notmuch["nbsp"], by_notmuch["vt"]) == ("\x1cfs@x", "\u00a0nb@x", "vt@x")
    assert tool.scan(root, "").ids == set(json.loads(nm(
        "search", "--format=json", "--output=messages", "--exclude=false", "*")))


def test_count_refuses_a_missing_profile_or_an_unbuilt_index(home):
    r = home.run("count", "acme")
    assert r.returncode == 1 and "no profile for acme" in r.stderr
    assert home.run("profile", "acme").returncode == 0
    before = home.listing()
    r = home.run("count", "acme")
    assert r.returncode == 1 and "build acme` first" in r.stderr
    assert home.listing() == before, "count writes nothing"


# --- do-010: search, the join over NAMED stores (R4, SC2; design §8) ---------


def dated(path: Path, msgid: str, date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"From: a@example.invalid\nTo: b@example.invalid\nSubject: {msgid}\n"
                    f"Message-ID: <{msgid}@x>\nDate: {date}\n\nbody {msgid}\n")


class Joined(Stores):
    """Two built stores, acme and other (each with E20's shapes, other's ids
    suffixed `other`), plus three dated messages interleaved across them."""

    def __init__(self, tmp: Path):
        super().__init__(tmp)
        self.other, _ = self.add_store("other")
        dated(self.root / "p1" / "new" / "n1", "n1", "Sun, 09 Jun 2030 10:00:00 +0000")
        dated(self.other / "p1" / "new" / "n2", "n2", "Sun, 09 Jun 2030 11:00:00 +0000")
        dated(self.root / "p2" / "new" / "n3", "n3", "Sun, 08 Dec 2030 10:00:00 +0000")
        self.ok("build", "acme")
        self.ok("build", "other")

    def search(self, *argv: str) -> list[dict]:
        import json
        return json.loads(self.ok("search", "--format", "json", *argv))


@pytest.fixture
def joined(tmp_path):
    if not os.access(NOTMUCH, os.X_OK):
        pytest.skip(f"no {NOTMUCH}")
    return Joined(tmp_path)


@needs_notmuch
def test_sc2_a_stores_search_cannot_see_another_stores_mail(joined):
    """SC2: a message only in other is not found by acme's search, and is
    found by other's, labelled other (and the same the other way round)."""
    assert joined.ok("search", "--store", "acme", "id:aother@x") == ""
    assert joined.search("--store", "acme", "id:aother@x") == []
    [row] = joined.search("--store", "other", "id:aother@x")
    assert row["store"] == "other" and row["subject"] == "aother"
    assert joined.search("--store", "other", "id:a@x") == []
    assert [r["store"] for r in joined.search("--store", "acme", "id:a@x")] == ["acme"]


@needs_notmuch
def test_sc2_store_a_b_finds_both_and_labels_each_row_with_its_store(joined):
    rows = joined.search("--store", "acme,other", "id:a@x", "or", "id:aother@x")
    assert sorted((r["store"], r["subject"]) for r in rows) == [
        ("acme", "a"), ("other", "aother")]
    text = joined.ok("search", "--store", "acme,other", "id:a@x or id:aother@x").splitlines()
    row = r"{}:thread:[0-9a-f]{{16}}  \d{{4}}-\d\d-\d\d \d\d:\d\d  \[1/1\]  "
    tail = r"a@example\.invalid; {} \(agent inbox unread\)"
    assert sorted(text) == text and len(text) == 2   # one timestamp: --store's order
    assert re.fullmatch(row.format("acme") + tail.format("a"), text[0]), text
    assert re.fullmatch(row.format("other") + tail.format("aother"), text[1]), text


@needs_notmuch
def test_threads_are_keyed_by_store_and_thread_id(joined):
    """Thread ids are per database: the two stores reuse the same ids, and
    the join keeps both as two threads, keyed (store, thread)."""
    rows = joined.search("--store", "acme,other", "*")
    keys = [(r["store"], r["thread"]) for r in rows]
    assert len(keys) == len(set(keys))
    by_store = {s: {t for st, t in keys if st == s} for s in ("acme", "other")}
    assert by_store["acme"] & by_store["other"], "the fixture should reuse a thread id"
    for s in ("acme", "other"):
        n = joined.nm("count", "--output=threads", "*",
                      profile=joined.home / f".notmuch-config.{s}")
        assert len(by_store[s]) == int(n), f"{s}: every thread, once"
    text = joined.ok("search", "--store", "acme,other", "*").splitlines()
    assert len({line.split()[0] for line in text}) == len(rows), "each key printed once"


@needs_notmuch
def test_the_join_is_newest_first_across_stores_and_limit_cuts_the_merged_list(joined):
    q = ("id:n1@x", "or", "id:n2@x", "or", "id:n3@x")
    rows = joined.search("--store", "acme,other", *q)
    assert [(r["store"], r["subject"]) for r in rows] == [
        ("acme", "n3"), ("other", "n2"), ("acme", "n1")]
    rows = joined.search("--store", "acme,other", "--limit", "2", *q)
    assert [r["subject"] for r in rows] == ["n3", "n2"]
    assert joined.search("--store", "acme,other", "--limit", "0", *q) == []
    r = joined.run("search", "--store", "acme", "--limit", "-1", "*")
    assert r.returncode == 2


@needs_notmuch
def test_a_missing_profile_is_refused_by_name_and_nothing_is_searched(joined):
    for stores in ("acme,ghost", "ghost,acme", "ghost"):
        r = joined.run("search", "--store", stores, "*")
        assert r.returncode == 1, (stores, r.stderr)
        assert r.stdout == ""
        assert "no profile for ghost" in r.stderr
        assert str(joined.home / ".notmuch-config.ghost") in r.stderr


@needs_notmuch
def test_every_store_is_resolved_before_any_is_searched(joined, capsys):
    """In process, with notmuch unrunnable: a search would fail (exit 3), so
    a refusal (exit 1) for a later store shows the earlier one never ran."""
    tool = load_tool()
    tool.NOTMUCH = str(joined.home / "no-such-notmuch")
    assert tool.main(["search", "--store", "acme,ghost", "*"], env=dict(joined.env)) == 1
    assert "no profile for ghost" in capsys.readouterr().err
    assert tool.main(["search", "--store", "acme", "*"], env=dict(joined.env)) == 3


@needs_notmuch
def test_a_store_with_a_profile_but_no_built_index_is_refused_by_name(joined):
    joined.add_store("unbuilt")
    r = joined.run("search", "--store", "acme,unbuilt", "*")
    assert r.returncode == 1 and r.stdout == ""
    assert "no built index for unbuilt" in r.stderr and "build unbuilt" in r.stderr


@needs_notmuch
def test_a_profile_whose_database_is_not_its_own_is_refused(joined, tmp_path):
    """A profile that names the host's (or any other) database is not a
    store index: search refuses it rather than read that database."""
    host = joined.home / ".notmuch-config.hostlike"
    root = joined.home / ".mail" / "agents" / "hostlike"
    root.mkdir(parents=True)
    (root / ".tm-store").write_text("")
    joined.stores.joinpath("hostlike").mkdir()
    host.write_text(f"[database]\npath={joined.home / '.mail'}\nmail_root={root}\n")
    r = joined.run("search", "--store", "hostlike", "*")
    assert r.returncode == 1 and "database.path" in r.stderr and r.stdout == ""


@needs_notmuch
def test_no_store_searches_notmuch_profiles_store_only_and_never_the_host(joined):
    joined.env["NOTMUCH_PROFILE"] = "other"
    rows = joined.search("id:a@x or id:aother@x")
    assert [(r["store"], r["subject"]) for r in rows] == [("other", "aother")]
    del joined.env["NOTMUCH_PROFILE"]
    r = joined.run("search", "*")
    assert r.returncode == 1 and r.stdout == ""
    assert "no --store, and no NOTMUCH_PROFILE" in r.stderr
    joined.env["NOTMUCH_PROFILE"] = ""
    r = joined.run("search", "*")
    assert r.returncode == 1 and "NOTMUCH_PROFILE is empty" in r.stderr
    joined.env["NOTMUCH_PROFILE"] = "ghost"
    r = joined.run("search", "*")
    assert r.returncode == 1 and "no profile for ghost" in r.stderr


@needs_notmuch
def test_a_planted_host_config_or_database_never_answers_a_search(joined, tmp_path):
    """E3, E14: NOTMUCH_CONFIG and NOTMUCH_DATABASE planted on a decoy (a
    stand-in for the host index, also at ~/.notmuch-config) are dropped from
    the child: the search answers from the named store's index alone."""
    decoy = tmp_path / "decoy"
    mail(decoy / "mail" / "box" / "new" / "d", "decoy")
    mail(decoy / "mail" / "box" / "new" / "a", "a")      # acme's id, in the decoy too
    for sub in ("cur", "tmp"):
        (decoy / "mail" / "box" / sub).mkdir()
    cfg = joined.home / ".notmuch-config"
    cfg.write_text(f"[database]\npath={decoy / 'db'}\nmail_root={decoy / 'mail'}\n")
    joined.nm("new", "--no-hooks", profile=cfg)
    assert joined.nm("count", "id:decoy@x", profile=cfg) == "1"
    joined.env.update({"NOTMUCH_CONFIG": str(cfg), "NOTMUCH_DATABASE": str(decoy / "db")})
    assert joined.search("--store", "acme,other", "id:decoy@x") == []
    rows = joined.search("--store", "acme", "id:a@x")
    assert [r["store"] for r in rows] == ["acme"] and "agent" in rows[0]["tags"]


@needs_notmuch
def test_search_writes_nothing_and_takes_no_lock(joined):
    before = joined.listing()
    stamps = {p: p.stat().st_mtime_ns for p in joined.stores.rglob("*") if p.is_file()}
    with open(joined.stores / ".acme.lock") as fd:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)      # a build of acme is running
        assert joined.search("--store", "acme", "id:a@x")
    assert joined.listing() == before
    assert {p: p.stat().st_mtime_ns for p in joined.stores.rglob("*") if p.is_file()} == stamps


def test_the_query_goes_to_each_store_as_given_after_its_profile(home, tmp_path):
    """No notmuch needed: a fake records each call's argv. One call per
    store, each with that store's --config, summary output, newest first,
    the per-store limit, and the query words unchanged after `--`; ties in
    time keep --store's order; a store named twice is searched once."""
    tool = load_tool()
    log = tmp_path / "argv.log"
    fake = tmp_path / "fake-notmuch"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        "case \"$1\" in\n"
        "  *acme) echo '[{\"thread\":\"01\",\"timestamp\":5,\"subject\":\"x\"}]' ;;\n"
        "  *) echo '[{\"thread\":\"01\",\"timestamp\":5,\"subject\":\"y\"},"
        "{\"thread\":\"02\",\"timestamp\":9,\"subject\":\"z\"}]' ;;\n"
        "esac\n")
    fake.chmod(0o755)
    tool.NOTMUCH = str(fake)
    env = dict(home.env)
    for store in ("acme", "zed"):
        root = home.home / ".mail" / "agents" / store
        root.mkdir(parents=True, exist_ok=True)
        (root / ".tm-store").write_text("")
        assert tool.main(["profile", store], env=env) == 0
        (home.stores / store).mkdir(parents=True)
    rows = tool.search("acme,zed,acme", ["tag:x", "and", "-foo", "(a b)"], 7, env)
    assert [(r["store"], r["thread"], r["subject"]) for r in rows] == [
        ("zed", "02", "z"), ("acme", "01", "x"), ("zed", "01", "y")]
    calls = log.read_text().splitlines()
    assert calls == [
        f"--config={home.home / f'.notmuch-config.{s}'} search --format=json "
        "--output=summary --sort=newest-first --limit=7 -- tag:x and -foo (a b)"
        for s in ("acme", "zed")]
    assert [r["subject"] for r in tool.search("zed,acme", ["*"], 2, env)] == ["z", "y"]
    assert tool.search_line({"store": "acme", "thread": "01", "timestamp": 0, "matched": 1,
                             "total": 2, "authors": "a", "subject": "s",
                             "tags": ["t", "u"]}).startswith("acme:thread:01  ")


def test_search_is_documented_and_count_is_not_the_last_verb():
    text = TOOL.read_text()
    assert "arrives with SM-731's later slices" not in text
    assert "search [--store A[,B...]]" in text
