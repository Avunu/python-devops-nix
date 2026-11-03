"""Data files through the roots: the product's first, then the core's (DESIGN §4.6)."""

import tempfile
import unittest
from pathlib import Path

from support import KIT, STUB

from standards_core.common import data
from standards_core.common.data import DataRoots, data_path
from standards_core.common.report import ConfigError
from standards_core.product import core_data_root


class TestDataPath(unittest.TestCase):
    def test_roots_are_product_first(self):
        self.assertEqual(STUB.roots[0], STUB.data_root)
        self.assertEqual(STUB.roots[1], core_data_root())
        self.assertTrue((core_data_root() / "README.md").is_file())

    def test_product_file_shadows_the_core_file(self):
        path = data_path(STUB, "README.md")
        self.assertTrue(path.is_absolute())
        self.assertEqual(path, (STUB.data_root / "README.md").resolve())
        self.assertIn("stub_nix_tools/data", path.read_text())

    def test_core_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = DataRoots((Path(tmp), core_data_root()))
            self.assertEqual(
                roots.path("README.md"), (core_data_root() / "README.md").resolve()
            )
            (Path(tmp) / "README.md").write_text("mine\n")
            self.assertEqual(roots.path("README.md").read_text(), "mine\n")

    def test_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "schema").mkdir()
            roots = DataRoots((root, core_data_root()))
            self.assertTrue(roots.path("schema").is_dir())

    def test_listdir_unites_and_shadows(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            product, core = Path(a), Path(b)
            for root, names in (
                (product, ("x.toml", "both.toml")),
                (core, ("y.toml", "both.toml")),
            ):
                (root / "profiles").mkdir()
                for name in names:
                    (root / "profiles" / name).write_text(str(root))
            listed = DataRoots((product, core)).listdir("profiles")
            self.assertEqual(list(listed), ["both.toml", "x.toml", "y.toml"])
            self.assertEqual(listed["both.toml"].read_text(), str(product))
            self.assertEqual(listed["y.toml"].read_text(), str(core))
            self.assertEqual(DataRoots((product, core)).listdir("nothing"), {})

    def test_outside_or_missing(self):
        for rel in ("", "../pyproject.toml", "/etc/passwd", "nope.json", "a/../../b"):
            with self.subTest(rel=rel), self.assertRaises(ConfigError):
                data_path(STUB, rel)
        self.assertIsNone(DataRoots(STUB.roots).find("nope.json"))

    def test_roots_are_cached_per_product(self):
        self.assertIs(data.product_roots(STUB), data.product_roots(STUB))
        self.assertIsNot(data.product_roots(STUB), data.product_roots(KIT))
        self.assertEqual(data.product_roots(KIT).roots, KIT.roots)


if __name__ == "__main__":
    unittest.main()
