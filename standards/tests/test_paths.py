"""``data-path`` (the product's data first, then the core's) and ``pin-path``."""

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import STUB, TREE_NAR_HASH, make_tree, product_copy, run_cli

from standards_core.common.data import data_path
from standards_core.product import core_data_root

REV = "c" * 40


class TestDataPathCommand(unittest.TestCase):
    def test_prints_existing_files(self):
        for rel in (
            "schema/modules.schema.json",
            "schema/core.schema.json",
            "profiles/minimal.toml",
        ):
            code, out, _ = run_cli(STUB, "data-path", rel)
            self.assertEqual(code, 0)
            self.assertEqual(Path(out.strip()), data_path(STUB, rel))
            self.assertTrue(Path(out.strip()).is_file())

    def test_product_first_then_the_core(self):
        code, out, _ = run_cli(STUB, "data-path", "schema/core.schema.json")
        self.assertEqual(
            Path(out.strip()),
            (core_data_root() / "schema" / "core.schema.json").resolve(),
        )
        code, out, _ = run_cli(STUB, "data-path", "profiles/minimal.toml")
        self.assertTrue(
            Path(out.strip()).is_relative_to(Path(STUB.data_root).resolve())
        )
        with tempfile.TemporaryDirectory() as tmp:
            product = product_copy(STUB, Path(tmp))
            own = product.data_root / "schema" / "core.schema.json"
            own.write_text("{}")
            code, out, _ = run_cli(product, "data-path", "schema/core.schema.json")
            self.assertEqual((code, Path(out.strip())), (0, own.resolve()))

    def test_missing(self):
        code, out, err = run_cli(STUB, "data-path", "nope.json")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no such data file", err)
        code, _, err = run_cli(STUB, "data-path", "../stub_nix_tools/product.py")
        self.assertEqual(code, 2)
        self.assertIn("stay inside it", err)


class TestPinPathCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        src = make_tree(root / "src" / f"lint-rules-{REV}")
        with tarfile.open(root / "lint-rules.tar.gz", "w:gz") as tar:
            tar.add(src, arcname=src.name)
        self.unit = root / "unit"
        (self.unit / "sub").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.unit), "init", "-q"], check=True)
        lock = {
            "version": 7,
            "root": "root",
            "nodes": {
                "root": {"inputs": {"shared-rules": "rules"}},
                "rules": {
                    "locked": {
                        "type": "github",
                        "owner": "example",
                        "repo": "lint-rules",
                        "rev": REV,
                        "narHash": TREE_NAR_HASH,
                    }
                },
            },
        }
        (self.unit / "flake.lock").write_text(json.dumps(lock))
        self.patch = mock.patch.dict(
            os.environ, {"STUB_NIX_PIN_URL": f"file://{root}/{{repo}}.tar.gz"}
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_from_a_subdirectory_reads_the_git_roots_lock(self):
        code, out, err = run_cli(
            STUB, "pin-path", "shared-rules", cwd=self.unit / "sub"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            Path(out.strip()),
            (self.unit / ".dev-dist" / "pins" / f"lint-rules-{REV}").resolve(),
        )

    def test_a_checkout_in_a_subdirectory_reads_its_own_lock(self):
        # A fixture checkout inside a larger repository whose root has a flake.lock of
        # its own without the input.
        outer = Path(self.tmp.name) / "outer"
        unit = outer / "tests" / "fixtures" / "unit"
        (unit / "pkg").mkdir(parents=True)
        subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
        (outer / "flake.lock").write_text(
            json.dumps({"version": 7, "root": "root", "nodes": {"root": {}}})
        )
        (unit / "flake.lock").write_text((self.unit / "flake.lock").read_text())
        pin = (unit / ".dev-dist" / "pins" / f"lint-rules-{REV}").resolve()
        for cwd in (unit, unit / "pkg"):
            code, out, err = run_cli(STUB, "pin-path", "shared-rules", cwd=cwd)
            self.assertEqual(code, 0, err)
            self.assertEqual(Path(out.strip()), pin)
        self.assertFalse((outer / ".dev-dist").exists())
        # Above the checkout, the repository's own lock answers.
        code, _, err = run_cli(STUB, "pin-path", "shared-rules", cwd=outer / "tests")
        self.assertEqual(code, 2, err)

    def test_unknown_input(self):
        code, _, err = run_cli(
            STUB, "pin-path", "pilot", "--lock", str(self.unit / "flake.lock")
        )
        self.assertEqual(code, 2)
        self.assertIn("no input 'pilot'", err)

    def test_no_lock(self):
        code, _, _ = run_cli(
            STUB, "pin-path", "pilot", "--lock", str(self.unit / "missing.lock")
        )
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
