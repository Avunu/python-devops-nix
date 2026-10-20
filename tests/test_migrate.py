"""python/migrate.py: the migrate-on-deploy flow against stub commands. The
check, the private snapshot, its verification, the migration, the rollback,
the product's success and failure steps, retention, and the messages' syslog
prefixes. tests/nixos-migrate.nix runs the same flow against MariaDB and
PostgreSQL in a VM.

Run: python3 -I tests/test_migrate.py <dir with migrate.py and journald.py>

Standard library only, and run on every interpreter the products' presets name
(lib.nixos.migrate.check): a product's migrate tool may import the module
in-process.
"""

import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("usage: test_migrate.py <python dir>")
sys.path.insert(0, str(Path(sys.argv.pop(1)).resolve()))

import migrate


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = self.tmp / "calls"
        self.calls.touch()
        self.snapdir = self.tmp / "state" / "snapshots"
        self.saved_stream = os.environ.pop("JOURNAL_STREAM", None)

    def tearDown(self):
        os.environ.pop("JOURNAL_STREAM", None)
        if self.saved_stream is not None:
            os.environ["JOURNAL_STREAM"] = self.saved_stream
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
                path.rmdir()
            else:
                path.unlink()
        self.tmp.rmdir()

    # A stub command: records its name, arguments and the MIGRATE_* variables,
    # runs `body` (Python), and exits with `rc`.
    def stub(self, name, rc=0, body=""):
        path = self.tmp / name
        path.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json, os, sys
                with open({str(self.calls)!r}, "a") as calls:
                    env = {{k: v for k, v in os.environ.items() if k.startswith("MIGRATE_")}}
                    calls.write(json.dumps([{name!r}, sys.argv[1:], env]) + "\\n")
                """
            )
            + textwrap.dedent(body)
            + f"\nsys.exit({rc})\n"
        )
        path.chmod(0o755)
        return str(path)

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def names(self):
        return [call[0] for call in self.recorded()]

    def config(self, **overrides):
        # Defaults are written only where the test did not pass its own stub,
        # which would otherwise be overwritten under the same name.
        snapshot = overrides.pop("snapshot", {})
        data = {
            "label": "stub-migrate",
            "target": "erp.example.com",
            "check": None,
            "rollbackOnFailure": True,
        }
        for key, name in (
            ("migrate", "migrate"),
            ("onSuccess", "success"),
            ("onFailure", "failure"),
        ):
            if key not in overrides:
                data[key] = self.stub(name)
        data.update(overrides)
        if snapshot is None:
            data["snapshot"] = None
            return migrate.Config.from_json(data)
        defaults = {
            "dump": lambda: self.stub(
                "dump", body="sys.stdout.write('-- dump\\n-- Dump completed\\n')"
            ),
            "verify": lambda: self.stub("verify"),
            "restore": lambda: self.stub("restore"),
        }
        data["snapshot"] = {
            "dir": str(self.snapdir),
            "prefix": "premigrate-erp.example.com",
            "extension": ".sql",
            "retention": 2,
        }
        for key, make in defaults.items():
            if key not in snapshot:
                data["snapshot"][key] = make()
        data["snapshot"].update(snapshot)
        return migrate.Config.from_json(data)

    def run_flow(self, config, stderr=None):
        err = stderr if stderr is not None else io.StringIO()
        rc = migrate.Runner(config, stderr=err).run()
        return rc, err.getvalue() if isinstance(err, io.StringIO) else None

    def snapshots(self):
        return (
            sorted(self.snapdir.glob("premigrate-*")) if self.snapdir.exists() else []
        )

    # ── success ──────────────────────────────────────────────────────────

    def test_success(self):
        rc, err = self.run_flow(self.config())
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.names(), ["dump", "verify", "migrate", "success"])
        [snap] = self.snapshots()
        self.assertEqual(snap.read_text(), "-- dump\n-- Dump completed\n")
        self.assertRegex(snap.name, r"^premigrate-erp\.example\.com-\d{8}-\d{6}\.sql$")
        calls = self.recorded()
        self.assertEqual(calls[1][1], [str(snap)])
        self.assertEqual(calls[2][2]["MIGRATE_SNAPSHOT"], str(snap))
        self.assertEqual(calls[2][2]["MIGRATE_TARGET"], "erp.example.com")
        self.assertIn("stub-migrate(erp.example.com): migrate OK", err)
        self.assertNotIn("<", err)

    def test_snapshot_is_private(self):
        old = os.umask(0)
        try:
            self.snapdir.mkdir(parents=True, mode=0o755)
            self.snapdir.chmod(0o755)
            rc, err = self.run_flow(self.config())
        finally:
            os.umask(old)
        self.assertEqual(rc, 0, err)
        self.assertEqual(stat.S_IMODE(self.snapdir.stat().st_mode), 0o700)
        [snap] = self.snapshots()
        self.assertEqual(stat.S_IMODE(snap.stat().st_mode), 0o600)

    def test_retention(self):
        taken = []
        for _ in range(4):
            before = set(self.snapshots())
            rc, err = self.run_flow(self.config())
            self.assertEqual(rc, 0, err)
            taken.extend(set(self.snapshots()) - before)
        # Four in the same second or not, each has its own name, and the two
        # newest are the ones kept.
        self.assertEqual(len(taken), 4)
        self.assertEqual(set(self.snapshots()), set(taken[-2:]))

    def test_retention_leaves_other_files(self):
        self.snapdir.mkdir(parents=True)
        other = self.snapdir / "premigrate-other.example.com-20260101-000000.sql"
        other.write_text("x")
        for _ in range(3):
            self.run_flow(self.config())
        self.assertTrue(other.exists())

    def test_success_step_failing_is_a_warning(self):
        rc, err = self.run_flow(self.config(onSuccess=self.stub("success", rc=1)))
        self.assertEqual(rc, 0)
        self.assertIn("warning: the success step exited 1", err)

    def test_no_snapshot(self):
        rc, err = self.run_flow(self.config(snapshot=None))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.names(), ["migrate", "success"])
        self.assertNotIn("MIGRATE_SNAPSHOT", self.recorded()[0][2])
        self.assertFalse(self.snapdir.exists())

    # ── check ────────────────────────────────────────────────────────────

    def test_check_skips(self):
        rc, err = self.run_flow(self.config(check=self.stub("check", rc=migrate.SKIP)))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.names(), ["check"])
        self.assertEqual(self.snapshots(), [])

    def test_check_proceeds(self):
        rc, _ = self.run_flow(self.config(check=self.stub("check")))
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.names(), ["check", "dump", "verify", "migrate", "success"]
        )

    def test_check_error(self):
        rc, err = self.run_flow(self.config(check=self.stub("check", rc=3)))
        self.assertEqual(rc, 3)
        self.assertEqual(self.names(), ["check"])
        self.assertIn("the check before migrating failed (exit 3)", err)

    # ── a snapshot that cannot be trusted ────────────────────────────────

    def test_dump_fails(self):
        rc, err = self.run_flow(
            self.config(
                snapshot={"dump": self.stub("dump", rc=2, body="print('partial')")}
            )
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["dump"])
        self.assertEqual(self.snapshots(), [])
        self.assertIn(
            "pre-migrate snapshot failed (dump exit 2); aborting before migrate (no safety net)",
            err,
        )

    def test_dump_empty(self):
        rc, err = self.run_flow(self.config(snapshot={"dump": self.stub("dump")}))
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["dump"])
        self.assertEqual(self.snapshots(), [])
        self.assertIn("is empty", err)

    def test_verify_fails(self):
        rc, err = self.run_flow(
            self.config(snapshot={"verify": self.stub("verify", rc=1)})
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["dump", "verify"])
        self.assertEqual(self.snapshots(), [])
        self.assertIn("did not verify (exit 1); removed it", err)

    # ── failure ──────────────────────────────────────────────────────────

    def test_failure_rolls_back(self):
        rc, err = self.run_flow(self.config(migrate=self.stub("migrate", rc=7)))
        self.assertEqual(rc, 7)
        self.assertEqual(
            self.names(), ["dump", "verify", "migrate", "restore", "failure"]
        )
        [snap] = self.snapshots()
        calls = self.recorded()
        self.assertEqual(calls[3][1], [str(snap)])
        self.assertEqual(calls[4][2]["MIGRATE_EXIT"], "7")
        self.assertEqual(calls[4][2]["MIGRATE_ROLLBACK"], "restored")
        self.assertEqual(calls[4][2]["MIGRATE_SNAPSHOT"], str(snap))
        self.assertIn(">>> MIGRATION FAILED (exit 7) <<<", err)
        self.assertIn("rollback complete", err)

    def test_failed_migrations_keep_their_snapshots(self):
        for _ in range(3):
            self.run_flow(self.config(migrate=self.stub("migrate", rc=1)))
        self.assertEqual(len(self.snapshots()), 3)

    def test_rollback_fails(self):
        rc, err = self.run_flow(
            self.config(
                migrate=self.stub("migrate", rc=1),
                snapshot={"restore": self.stub("restore", rc=5)},
            )
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.recorded()[-1][2]["MIGRATE_ROLLBACK"], "failed")
        self.assertIn("ERROR rollback FAILED (exit 5)", err)
        self.assertEqual(len(self.snapshots()), 1)

    def test_no_rollback(self):
        rc, _ = self.run_flow(
            self.config(migrate=self.stub("migrate", rc=1), rollbackOnFailure=False)
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["dump", "verify", "migrate", "failure"])
        self.assertEqual(self.recorded()[-1][2]["MIGRATE_ROLLBACK"], "none")

    def test_failure_without_snapshot(self):
        rc, err = self.run_flow(
            self.config(migrate=self.stub("migrate", rc=1), snapshot=None)
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.names(), ["migrate", "failure"])
        self.assertNotIn("no snapshot to roll back to", err)

    # ── messages ─────────────────────────────────────────────────────────

    def test_priorities_on_the_journal(self):
        with tempfile.TemporaryFile("w+") as journal:
            st = os.fstat(journal.fileno())
            os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"
            rc, _ = self.run_flow(
                self.config(migrate=self.stub("migrate", rc=1)), stderr=journal
            )
            journal.seek(0)
            lines = journal.read().splitlines()
        self.assertEqual(rc, 1)
        self.assertIn("<6>stub-migrate(erp.example.com): snapshot verified", lines)
        self.assertIn(
            "<3>stub-migrate(erp.example.com): >>> MIGRATION FAILED (exit 1) <<<", lines
        )
        self.assertTrue(
            any(
                line.startswith("<4>stub-migrate(erp.example.com): rolling back")
                for line in lines
            )
        )

    # ── configuration and the command line ───────────────────────────────

    def test_unknown_key(self):
        with self.assertRaisesRegex(
            ValueError, "unknown configuration keys: snapshots"
        ):
            migrate.Config.from_json(
                {"label": "x", "target": "y", "migrate": "z", "snapshots": None}
            )

    def test_retention_floor(self):
        with self.assertRaises(ValueError):
            self.config(snapshot={"retention": 0})

    def test_main(self):
        config = self.config()
        path = self.tmp / "config.json"
        path.write_text(
            json.dumps(
                {
                    "label": config.label,
                    "target": config.target,
                    "migrate": config.migrate,
                    "snapshot": {
                        "dir": str(self.snapdir),
                        "prefix": "premigrate-erp.example.com",
                        "extension": ".sql",
                        "retention": 1,
                        "dump": config.snapshot.dump,
                        "verify": config.snapshot.verify,
                        "restore": config.snapshot.restore,
                    },
                }
            )
        )
        out = io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, io.StringIO()
        try:
            self.assertEqual(migrate.main([str(path), "take"]), 0)
            self.assertEqual(migrate.main([str(path), "take"]), 0)
            taken = out.getvalue().split()
            self.assertEqual(len(taken), 2)
            self.assertEqual(migrate.main([str(path), "prune"]), 0)
            self.assertEqual(migrate.main([str(path), "restore", taken[1]]), 0)
            out.truncate(0)
            out.seek(0)
            self.assertEqual(migrate.main([str(path), "list"]), 0)
            listed = out.getvalue().split()
            self.assertEqual(migrate.main([str(path)]), 0)
        finally:
            sys.stdout, sys.stderr = saved
        self.assertEqual(listed, [taken[1]])
        self.assertEqual(self.names()[-3:], ["dump", "verify", "migrate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
