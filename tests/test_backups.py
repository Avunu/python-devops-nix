"""python/backups.py without a network: discovery, selection, caching and the
fail-safe download, for both layouts in use (tests/fixtures/backup-layouts/), against a directory
laid out like the bucket and against a stub mc serving the same tree over the
S3 code path.

Run: python3 -I tests/test_backups.py <python/> <tests/fixtures/backup-layouts/>

Standard library only, and run on every interpreter the products' presets name
(lib.backups.check), since a product's command line imports the module
in-process. Ported in part from odoo-nix's tests/test_remote.py.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

if len(sys.argv) < 3:
    sys.exit("usage: test_backups.py <python dir> <layouts dir>")
LAYOUTS = Path(sys.argv.pop(2)).resolve()
sys.path.insert(0, str(Path(sys.argv.pop(1)).resolve()))

import backups

dumps = backups.load_layout(LAYOUTS / "database_dumps.py", "database_dumps")
folders = backups.load_layout(LAYOUTS / "timestamp_folders.py", "timestamp_folders")

NAMES = [
    "2026_10_01_03_00_00.dump",
    "2026_10_02_03_00_00.dump",
    "2026_10_03_03_00_00.dump",
    "2026_10_04_03_00_00.dump.zip",
]

CREDENTIALS = {
    "BACKUPS_URL": "https://s3.example.invalid",
    "BACKUPS_ACCESS_KEY": "AK",
    "BACKUPS_SECRET_KEY": "s/e+c'ret",
    "BACKUPS_BUCKET": "b",
    "BACKUPS_PREFIX": "/odoo/",
}

# A stand-in for minio-client: `ls --json [--recursive]`, `cp` and `mirror`,
# over a directory tree in which <bucket>/<key> is the object <key>. It accepts
# only the alias in $MC_CONFIG_DIR/config.json with the expected credentials,
# and logs its argv, so a test can assert the secret never reached it.
STUB_MC = r"""
import json, os, shutil, sys
from pathlib import Path

root = Path(os.environ["STUB_MC_ROOT"])
with open(os.environ["STUB_MC_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
config = json.loads(Path(os.environ["MC_CONFIG_DIR"], "config.json").read_text())
want = json.loads(os.environ["STUB_MC_WANT"])
args = [a for a in sys.argv[1:] if a not in ("--json", "--quiet", "--overwrite")]
mode, args = args[0], args[1:]

def local(target):
    alias, _, rest = target.partition("/")
    entry = config["aliases"].get(alias)
    if entry is None or entry["accessKey"] != want["accessKey"] or entry["secretKey"] != want["secretKey"]:
        print(json.dumps({"status": "error", "error": {"message": "Signature validation failed"}}))
        sys.exit(1)
    return root / rest.rstrip("/")

if mode == "ls":
    recursive = "--recursive" in args
    base = local([a for a in args if a != "--recursive"][0])
    if base.is_dir():
        found = base.rglob("*") if recursive else base.iterdir()
        for p in sorted(found):
            if recursive and p.is_dir():
                continue
            key = p.relative_to(base).as_posix() + ("/" if p.is_dir() else "")
            kind = "folder" if p.is_dir() else "file"
            size = p.stat().st_size if p.is_file() else 0
            print(json.dumps({"status": "success", "type": kind, "key": key, "size": size}))
elif mode == "cp":
    shutil.copyfile(local(args[0]), args[1])
elif mode == "mirror":
    shutil.copytree(local(args[0]), args[1], dirs_exist_ok=True)
else:
    sys.exit(f"stub mc: unsupported {mode}")
"""


def make(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class Case(unittest.TestCase):
    """A clean environment: no BACKUPS_*, no STUB_*."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache = self.tmp / "cache"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("BACKUPS_", "STUB"))
        }
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fixture_dumps(self, root: Path) -> None:
        for name in NAMES:
            make(root / "prod" / name, name)
        (root / "other").mkdir(parents=True)
        # Not backups: must never be selected.
        for junk in ("notes.txt", "2026_10_05_03_00_00.dump.partial", "latest.dump"):
            make(root / "prod" / junk, "x")

    def fixture_folders(self, root: Path) -> None:
        ts = "20260101_010101"
        make(root / ts / f"{ts}-prod_example_com-database.sql.gz", "jan-db")
        make(root / ts / f"{ts}-prod_example_com-site_config_backup.json", "{}")
        make(root / ts / f"{ts}-prod_example_com-files.tar", "public")
        make(root / ts / f"{ts}-prod_example_com-private-files.tar", "private")
        ts = "20260303_030303"
        make(root / ts / f"{ts}-prod_example_com-partial-database.sql.gz", "partial")
        make(root / "README.txt", "hello")


class DirectoryStore(Case):
    """The odoo-nix layout, read from a directory (ported from test_remote.py)."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "bucket"
        self.fixture_dumps(self.root)
        os.environ["STUB_NIX_BACKUP_SOURCE"] = str(self.root)
        self.b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)

    def test_lists_databases_and_only_real_backups(self):
        with self.b.store() as store:
            self.assertTrue(store.local)
            self.assertEqual(self.b.folders(store), ["other", "prod"])
            self.assertEqual([s.name for s in self.b.sets(store, "prod")], NAMES)

    def test_pick_newest_and_at(self):
        with self.b.store() as store:
            sets = self.b.sets(store, "prod")
            self.assertEqual(backups.pick(sets, None).name, NAMES[-1])
            self.assertEqual(backups.pick(sets, "2026-10-02").name, NAMES[1])
            self.assertEqual(backups.pick(sets, "2026_10_03_03").name, NAMES[2])
            self.assertEqual(backups.pick(sets, NAMES[0]).name, NAMES[0])
            with self.assertRaises(backups.BackupError):
                backups.pick(sets, "2025")
            with self.assertRaises(backups.BackupError):
                backups.pick([], None)

    def test_fetch_caches_and_keeps_newest_two(self):
        with self.b.store() as store:
            first = self.b.fetch(store, "prod", dest=self.cache, at="2026_10_01")
            self.assertEqual(first.paths["dump"].read_text(), NAMES[0])
            for at in ("2026_10_02", "2026_10_03"):
                self.b.fetch(store, "prod", dest=self.cache, at=at)
            kept = sorted(p.name for p in first.paths["dump"].parent.parent.iterdir())
            self.assertEqual(kept, NAMES[1:3])

    def test_retention_never_drops_what_it_just_fetched(self):
        # odoo-nix's own fetch kept the newest two by name, so fetching an
        # older backup while two newer ones were cached deleted the download
        # it had just made. The base keeps frappe-nix's rule.
        with self.b.store() as store:
            for at in ("2026_10_03", "2026_10_02"):
                self.b.fetch(store, "prod", dest=self.cache, at=at)
            old = self.b.fetch(store, "prod", dest=self.cache, at="2026_10_01")
            self.assertTrue(old.paths["dump"].is_file())

    def test_fetch_uses_cache_unless_told_not_to(self):
        with self.b.store() as store:
            path = self.b.fetch(store, "prod", dest=self.cache).paths["dump"]
            path.write_bytes(b"x" * len(NAMES[-1]))  # same size, different content
            self.assertEqual(
                self.b.fetch(store, "prod", dest=self.cache).paths["dump"], path
            )
            self.assertEqual(path.read_bytes(), b"x" * len(NAMES[-1]))
            self.b.fetch(store, "prod", dest=self.cache, use_cache=False)
            self.assertEqual(path.read_text(), NAMES[-1])

    def test_unknown_database_has_no_backups(self):
        with self.b.store() as store:
            with self.assertRaises(backups.BackupError) as ctx:
                self.b.fetch(store, "other", dest=self.cache)
            self.assertIn("YYYY_MM_DD_HH_MM_SS", "\n".join(ctx.exception.details))

    def test_manifest(self):
        with self.b.store() as store:
            fetched = self.b.fetch(store, "prod", dest=self.cache, at="2026-10-02T03")
        manifest = fetched.manifest()
        self.assertEqual(manifest["name"], NAMES[1])
        self.assertEqual(manifest["at"], "2026-10-02T03:00:00")
        self.assertEqual(manifest["scope"], "prod")
        self.assertEqual(manifest["available"], {"dump": True})
        self.assertEqual(Path(manifest["dump"]).read_text(), NAMES[1])

    def test_cache_from_the_environment(self):
        os.environ["STUB_NIX_BACKUP_CACHE"] = str(self.cache)
        with self.b.store() as store:
            fetched = self.b.fetch(store, "prod")
        self.assertTrue(fetched.paths["dump"].is_relative_to(self.cache))

    def test_no_download_directory(self):
        with self.b.store() as store, self.assertRaises(backups.BackupError) as ctx:
            self.b.fetch(store, "prod")
        self.assertIn("STUB_NIX_BACKUP_CACHE", ctx.exception.message)

    def test_mirror_needs_an_object_store(self):
        self.assertTrue(self.b.is_local())
        with self.b.store() as store, self.assertRaises(backups.BackupError):
            store.mirror("b/attachments", self.tmp / "out")

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.b.main(["list", "prod"]), 0)
            self.assertEqual(self.b.main(["folders"]), 0)
        self.assertEqual(out.getvalue().split(), [*NAMES, "other", "prod"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.b.main(["list", "other"]), 1)
        self.assertTrue(err.getvalue().startswith("stub-nix: no backups under "))


class Credentials(Case):
    def setUp(self):
        super().setUp()
        self.b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)

    def test_missing_credentials_are_named(self):
        self.assertFalse(self.b.has_credentials())
        with self.assertRaises(backups.BackupError) as ctx, self.b.store():
            pass
        text = ctx.exception.render("stub-nix")
        for word in (
            "BACKUPS_BUCKET",
            "setup-backup-access",
            "backup-access",
            "STUB_NIX_BACKUP_SOURCE",
        ):
            self.assertIn(word, text)

    def test_mc_config_is_private_and_removed(self):
        os.environ.update(CREDENTIALS)
        self.assertTrue(self.b.has_credentials())
        with self.b.store() as store:
            self.assertEqual(store.location, "stubnix/b/odoo")
            self.assertFalse(store.local)
            config = Path(store.env["MC_CONFIG_DIR"], "config.json")
            self.assertEqual(oct(config.stat().st_mode & 0o777), "0o600")
            alias = json.loads(config.read_text())["aliases"]["stubnix"]
            self.assertEqual(alias["secretKey"], CREDENTIALS["BACKUPS_SECRET_KEY"])
            directory = config.parent
        self.assertFalse(directory.exists())
        # The process environment is left alone: the config reaches mc only.
        self.assertNotIn("MC_CONFIG_DIR", os.environ)

    def test_an_mc_target_as_the_source(self):
        os.environ["STUB_NIX_BACKUP_SOURCE"] = "myalias/bucket/prefix/"
        with self.b.store() as store:
            self.assertFalse(store.local)
            self.assertEqual(store.location, "myalias/bucket/prefix")
            self.assertIsNone(store.env)

    def test_a_missing_directory_is_still_a_directory(self):
        os.environ["STUB_NIX_BACKUP_SOURCE"] = str(self.tmp / "nowhere")
        with self.b.store() as store:
            self.assertTrue(store.local)
            self.assertEqual(store.ls("", recursive=True), [])

    def test_legacy_variable_is_read_with_a_warning(self):
        b = backups.Backups(
            label="stub-nix",
            env_prefix="STUB",
            layout=dumps,
            legacy_env={"BACKUP_SOURCE": "STUB_BACKUP_SOURCE"},
        )
        os.environ["STUB_BACKUP_SOURCE"] = str(self.tmp)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(b.env("BACKUP_SOURCE"), str(self.tmp))
            self.assertEqual(b.env("BACKUP_SOURCE"), str(self.tmp))
        self.assertEqual(err.getvalue().count("STUB_BACKUP_SOURCE is deprecated"), 1)
        os.environ["STUB_NIX_BACKUP_SOURCE"] = "/new"
        self.assertEqual(b.env("BACKUP_SOURCE"), "/new")


class ObjectStore(Case):
    """Both layouts over the S3 path, through a stub mc."""

    def setUp(self):
        super().setUp()
        self.bucket = self.tmp / "s3"
        stub = self.tmp / "mc"
        stub.write_text(f"#!{sys.executable}\n{STUB_MC}")
        stub.chmod(0o755)
        self.log = self.tmp / "mc.log"
        os.environ.update(
            {
                **CREDENTIALS,
                "STUB_NIX_MC": str(stub),
                "STUB_MC_ROOT": str(self.bucket),
                "STUB_MC_LOG": str(self.log),
                "STUB_MC_WANT": json.dumps(
                    {
                        "accessKey": CREDENTIALS["BACKUPS_ACCESS_KEY"],
                        "secretKey": CREDENTIALS["BACKUPS_SECRET_KEY"],
                    }
                ),
            }
        )

    def argv_log(self) -> str:
        return self.log.read_text()

    def test_dumps(self):
        self.fixture_dumps(self.bucket / "b" / "odoo")
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)
        with b.store() as store:
            self.assertEqual(b.folders(store), ["other", "prod"])
            self.assertEqual([s.name for s in b.sets(store, "prod")], NAMES)
            fetched = b.fetch(store, "prod", dest=self.cache, at="2026-10-03")
        self.assertEqual(fetched.paths["dump"].read_text(), NAMES[2])
        self.assertEqual(fetched.source, "stubnix/b/odoo/prod")
        self.assertNotIn("s/e+c", self.argv_log())

    def test_folders(self):
        os.environ["BACKUPS_PREFIX"] = "Backups"
        self.fixture_folders(self.bucket / "b" / "Backups")
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=folders)
        with b.store() as store:
            sets = b.sets(store)
            self.assertEqual(
                [s.name for s in sets], ["20260101_010101", "20260303_030303"]
            )
            fetched = b.fetch(
                store, dest=self.cache, at="20260101_010101", roles=["private_files"]
            )
            self.assertEqual(fetched.paths["private_files"].read_text(), "private")
            self.assertIsNone(fetched.paths["files"])
            self.assertEqual(fetched.manifest()["slug"], "prod_example_com")
            self.assertEqual(fetched.manifest()["available"]["files"], True)
            with self.assertRaises(backups.BackupError) as ctx:
                b.fetch(store, dest=self.cache)
            self.assertIn("PARTIAL", ctx.exception.message)
        # The partial set was refused before anything of it was downloaded.
        self.assertFalse(list(self.cache.rglob("*partial-database*")))
        self.assertNotIn("s/e+c", self.argv_log())

    def test_wrong_credentials_fail_with_mc_message(self):
        self.fixture_dumps(self.bucket / "b" / "odoo")
        os.environ["BACKUPS_SECRET_KEY"] = "wrong"
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)
        with b.store() as store, self.assertRaises(backups.BackupError) as ctx:
            b.folders(store)
        self.assertIn("Signature validation failed", ctx.exception.render("stub-nix"))

    def test_mirror(self):
        make(self.bucket / "b" / "attachments" / "ab" / "abcdef", "blob")
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)
        self.assertFalse(b.is_local())
        with b.store() as store:
            store.mirror("b/attachments", self.tmp / "filestore")
        self.assertEqual((self.tmp / "filestore" / "ab" / "abcdef").read_text(), "blob")

    def test_no_mc(self):
        os.environ["STUB_NIX_MC"] = str(self.tmp / "absent")
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=dumps)
        with b.store() as store, self.assertRaises(backups.BackupError) as ctx:
            b.folders(store)
        self.assertIn("minio-client", ctx.exception.message)


class At(unittest.TestCase):
    def test_forms(self):
        for at, digits in {
            "2026-10-02": "20261002",
            "2026-10-02T03:04:05": "20261002030405",
            "2026-10-02 03:04": "202610020304",
            "2026-10-02T03:04:05Z": "20261002030405",
            "20261002_030405": "20261002030405",
            "2026_10_02_03_04_05": "20261002030405",
            "2026": "2026",
        }.items():
            self.assertEqual(backups.parse_at(at), digits, at)

    def test_refusals(self):
        for at in (
            "yesterday",
            "26",
            "2026-10-02T03:04:05+02:00",
            "2026-10-02T03:04:05.123",
            "-2026",
        ):
            with self.assertRaises(backups.BackupError, msg=at):
                backups.parse_at(at)

    def test_exact_name_wins_over_time(self):
        sets = [
            backups.BackupSet("20260101_000000", datetime(2026, 1, 1), {}),
            backups.BackupSet("20260102_000000", datetime(2026, 1, 2), {}),
        ]
        self.assertEqual(backups.pick(sets, "20260101_000000").name, "20260101_000000")
        self.assertEqual(backups.pick(sets, "2026-01").name, "20260102_000000")


class LayoutContract(Case):
    """What the base refuses from a layout, so a broken one fails loudly."""

    def layout(self, *sets, roles=("x",), optional=()):
        return type(
            "L",
            (),
            {
                "ROLES": roles,
                "OPTIONAL_ROLES": optional,
                "parse": staticmethod(lambda _l: sets),
            },
        )

    def sets_of(self, layout):
        os.environ["STUB_NIX_BACKUP_SOURCE"] = str(self.tmp)
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=layout)
        with b.store() as store:
            return b.sets(store)

    def test_refusals(self):
        when = datetime(2026, 1, 1)
        x = backups.Entry("x", 1)
        for bad in (
            [{"name": "a/b", "at": when, "objects": {}}],
            [
                {"name": "a", "at": when, "objects": {}},
                {"name": "a", "at": when, "objects": {}},
            ],
            [{"name": "a", "at": "2026-01-01", "objects": {}}],
            [{"name": "a", "at": when, "objects": {"y": x}}],
            [{"name": "a", "at": when, "objects": {}, "meta": {"available": 1}}],
            [{"name": "a", "at": when, "objects": {}, "meta": {"x": 1}}],
        ):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.sets_of(self.layout(*bad))
        with self.assertRaises(ValueError):
            backups.Backups(
                label="l", env_prefix="P", layout=self.layout(optional=("y",))
            )

    def test_sets_sort_by_time(self):
        sets = self.sets_of(
            self.layout(
                {"name": "b", "at": datetime(2026, 1, 1), "objects": {}},
                backups.BackupSet("a", datetime(2026, 1, 2), {}),
            )
        )
        self.assertEqual([s.name for s in sets], ["b", "a"])

    def test_unknown_optional_role(self):
        os.environ["STUB_NIX_BACKUP_SOURCE"] = str(self.tmp / "bucket")
        self.fixture_folders(self.tmp / "bucket")
        b = backups.Backups(label="stub-nix", env_prefix="STUB", layout=folders)
        with b.store() as store, self.assertRaises(backups.BackupError):
            b.fetch(store, dest=self.cache, roles=["database"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
