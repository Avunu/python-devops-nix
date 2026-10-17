"""python/uv_build_deps.py against both products' lock shapes.

    python -I tests/test_uv_build_deps.py <python/> <tests/fixtures/build-deps/>

Each fixture directory holds a uv.lock trimmed from a product's real one, a
pyproject.toml whose managed block is stale, and expected.toml, the same file
after `update`.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

MODULES = Path(sys.argv[1])
FIXTURES = Path(sys.argv[2])
sys.path.insert(0, str(MODULES))

import uv_build_deps

# What each product passes as `always`: odoo-nix's ALWAYS list, and the
# entries of frappe-nix's template whose backend is not setuptools.
ODOO = {
    "label": "odoo-nix",
    "always": {
        name: ["setuptools"]
        for name in ["docopt", "ofxparse", "python-ldap", "rjsmin", "vobject"]
    },
}
FRAPPE = {
    "label": "frappe-nix",
    "always": {
        "frappe-runtime": ["hatchling"],
        "cairocffi": ["setuptools", "cffi", "pycparser"],
    },
}


def lock(shape: str) -> dict:
    with (FIXTURES / shape / "uv.lock").open("rb") as handle:
        return tomllib.load(handle)


class Derive(unittest.TestCase):
    def test_odoo_shape_sdist_only(self):
        # The virtual root is left out; OCB, a path source with no wheel, is in.
        self.assertEqual(
            sorted(uv_build_deps.sdist_only(lock("odoo-shape"))),
            ["docopt", "odoo", "ofxparse"],
        )

    def test_frappe_shape_sdist_only(self):
        # The editable member and the virtual root are left out; git and URL
        # sources have no wheels and are in.
        self.assertEqual(
            sorted(uv_build_deps.sdist_only(lock("frappe-shape"))),
            [
                "docopt",
                "frappe",
                "gunicorn",
                "mysqlclient",
                "pypika",
                "pyqrcode",
                "rauth",
            ],
        )

    def test_exclude_names_the_root(self):
        self.assertNotIn(
            "odoo", uv_build_deps.sdist_only(lock("odoo-shape"), exclude="odoo")
        )

    def test_always_replaces_the_derived_entry(self):
        entries = uv_build_deps.derive(
            lock("frappe-shape"), always={"gunicorn": ["flit-core"]}
        )
        self.assertEqual(entries["gunicorn"], ["flit-core"])

    def test_always_adds_what_the_lock_lacks(self):
        entries = uv_build_deps.derive(lock("odoo-shape"), always=ODOO["always"])
        self.assertEqual(entries["vobject"], ["setuptools"])

    def test_entries_are_sorted(self):
        entries = uv_build_deps.derive(lock("frappe-shape"), always=FRAPPE["always"])
        self.assertEqual(list(entries), sorted(entries))

    def test_an_empty_lock_derives_nothing(self):
        self.assertEqual(uv_build_deps.derive({}), {})


class Sync(unittest.TestCase):
    def plan(self, shape: str, product: dict) -> tuple[str, str | None]:
        _, synced, _ = uv_build_deps.plan(
            FIXTURES / shape / "pyproject.toml",
            FIXTURES / shape / "uv.lock",
            product["label"],
            product["always"],
        )
        return synced, (FIXTURES / shape / "expected.toml").read_text(encoding="utf-8")

    def test_odoo_shape(self):
        synced, expected = self.plan("odoo-shape", ODOO)
        self.assertEqual(synced, expected)

    def test_frappe_shape_keeps_the_indent_and_the_rest(self):
        synced, expected = self.plan("frappe-shape", FRAPPE)
        self.assertEqual(synced, expected)

    def test_the_result_is_valid_toml(self):
        synced, _ = self.plan("frappe-shape", FRAPPE)
        table = tomllib.loads(synced)["tool"]["uv"]["extra-build-dependencies"]
        self.assertEqual(table["frappe-runtime"], ["hatchling"])
        self.assertEqual(table["hand-kept"], ["flit-core"])

    def test_sync_is_idempotent(self):
        expected = (FIXTURES / "odoo-shape" / "expected.toml").read_text(
            encoding="utf-8"
        )
        entries = uv_build_deps.derive(
            lock("odoo-shape"), "odoo-nix-fixture-18", ODOO["always"]
        )
        self.assertEqual(uv_build_deps.sync(expected, entries, "odoo-nix"), expected)

    def test_another_label_finds_no_block(self):
        text = (FIXTURES / "odoo-shape" / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIsNone(uv_build_deps.sync(text, {}, "frappe-nix"))


class Main(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copyfile(FIXTURES / "odoo-shape" / name, self.dir / name)
        self.pyproject = self.dir / "pyproject.toml"
        self.lock = self.dir / "uv.lock"

    def run_main(self, *argv: str, label: str = "odoo-nix") -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = uv_build_deps.main(
                list(argv), label=label, always=ODOO["always"], prog="stub-build-deps"
            )
        return status, err.getvalue()

    def test_check_fails_on_a_stale_block_and_leaves_it(self):
        before = self.pyproject.read_text(encoding="utf-8")
        status, err = self.run_main("check", str(self.pyproject), str(self.lock))
        self.assertEqual(status, 1)
        self.assertIn("run `stub-build-deps update`", err)
        self.assertEqual(self.pyproject.read_text(encoding="utf-8"), before)

    def test_update_then_check(self):
        status, err = self.run_main("update", str(self.pyproject), str(self.lock))
        self.assertEqual(
            (status, err), (0, "odoo-nix: synced 6 sdist build-dep entr(ies)\n")
        )
        self.assertEqual(
            self.pyproject.read_text(encoding="utf-8"),
            (FIXTURES / "odoo-shape" / "expected.toml").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            self.run_main("check", str(self.pyproject), str(self.lock))[0], 0
        )

    def test_no_block_is_reported_and_nothing_written(self):
        before = self.pyproject.read_text(encoding="utf-8")
        status, err = self.run_main(
            "update", str(self.pyproject), str(self.lock), label="other-nix"
        )
        self.assertEqual(status, 1)
        self.assertIn(
            "no managed block (# >>> other-nix: sdist build deps (managed) >>>)", err
        )
        self.assertEqual(self.pyproject.read_text(encoding="utf-8"), before)

    def test_usage(self):
        for argv in ([], ["sync"], ["update", "a", "b", "c"]):
            with self.subTest(argv=argv):
                status, err = self.run_main(*argv)
                self.assertEqual(status, 2)
                self.assertTrue(err.startswith("usage: stub-build-deps {update,check}"))


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], verbosity=2)
