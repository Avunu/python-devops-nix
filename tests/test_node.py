"""python/node.py: detection agreeing with lib.node.detect over the same
fixtures, and the command's install, build and watch driven against stub
package managers on PATH.

Run: python3 -I tests/test_node.py <python/> <nix-detect.json> <fixtures/node>

<nix-detect.json> is lib.node.detect's answer for each fixture, as tests/node.nix
writes it: the summary fields, or "error" where it refuses. Standard library
only, and run on every interpreter the products' presets name (lib.node.check),
since a product may import the module in-process.
"""

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

if len(sys.argv) < 4:
    sys.exit("usage: test_node.py <python dir> <nix-detect.json> <fixtures dir>")
sys.path.insert(0, sys.argv.pop(1))
NIX = json.loads(Path(sys.argv.pop(1)).read_text())
FIXTURES = Path(sys.argv.pop(1))

import node

# A stub manager: logs `<name> <args>` with the directory it ran in, makes
# node_modules on an install, and on `run watch` prints two lines and exits.
STUB = """#!/bin/sh
echo "$(basename "$0") $* @ $(basename "$PWD")" >> "$STUB_LOG"
case "$1" in
  ci|install) mkdir -p node_modules ;;
  run) if [ "$2" = watch ]; then echo "one"; echo "two"; fi
       if [ -n "$STUB_FAIL" ] && [ "$2" = "$STUB_FAIL" ]; then exit 3; fi ;;
esac
"""


class Detect(unittest.TestCase):
    def test_agrees_with_lib_node_on_every_fixture(self) -> None:
        for name in sorted(NIX):
            with self.subTest(fixture=name):
                try:
                    t = node.detect(FIXTURES / name, name)
                    got: object = t.summary() if t else None
                except node.Refused:
                    got = "error"
                self.assertEqual(got, NIX[name])

    def test_no_package_json_is_no_target(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(node.detect(d))

    def test_refusals_name_the_fix(self) -> None:
        for name, words in {
            "ambiguous": "delete the lockfile",
            "berry": "pnpm import",
            "bun": "bun lock",
            "mismatch": "regenerate the lock with pnpm",
        }.items():
            with self.subTest(fixture=name), self.assertRaises(node.Refused) as cm:
                node.detect(FIXTURES / name, name)
            self.assertIn(words, str(cm.exception))

    def test_devengines_list_form_takes_the_first(self) -> None:
        d = node.declared_manager(
            {"devEngines": {"packageManager": [{"name": "pnpm", "version": "^11"}]}}
        )
        self.assertEqual(
            (d.name, d.major, d.via), ("pnpm", "11", "devEngines.packageManager")
        )

    def test_package_manager_wins_over_devengines(self) -> None:
        d = node.declared_manager(
            {
                "packageManager": "yarn@1.22.22",
                "devEngines": {"packageManager": {"name": "pnpm"}},
            }
        )
        self.assertEqual((d.name, d.version), ("yarn", "1.22.22"))

    def test_pnpm_importers_skip_the_env_document(self) -> None:
        lock = FIXTURES / "pnpm-workspace" / "pnpm-lock.yaml"
        self.assertIn("---", lock.read_text())
        self.assertEqual(node.pnpm_importers(lock), [".", "types"])


class Command(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("npm", "pnpm", "yarn"):
            p = bin_dir / name
            p.write_text(STUB)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "log"
        self.log.touch()
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "STUB_LOG": str(self.log),
        }
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.targets: list[dict[str, str]] = []

    def add(self, fixture: str, key: str | None = None) -> Path:
        key = key or fixture
        shutil.copytree(FIXTURES / fixture, self.root / key)
        # The fixtures come from the store, read-only.
        for d, _dirs, files in os.walk(self.root / key):
            os.chmod(d, 0o755)
            for f in files:
                os.chmod(os.path.join(d, f), 0o644)
        self.targets.append({"key": key, "path": str(self.root / key)})
        return self.root / key

    def run_cli(self, *argv: str, ci: str = "") -> tuple[int, str, str]:
        spec = self.tmp / "targets.json"
        spec.write_text(json.dumps(self.targets))
        out, err = io.StringIO(), io.StringIO()
        env = {**self.env, "STUB_CI": ci}
        with (
            unittest.mock.patch.dict(os.environ, env),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = node.main(
                list(argv), label="stub-nix", targets=str(spec), ci_var="STUB_CI"
            )
        return code, out.getvalue(), err.getvalue()

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def test_each_manager_installs_frozen(self) -> None:
        for f in ("npm", "pnpm", "yarn"):
            self.add(f)
        code, _, _ = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(self.calls()),
            [
                "npm ci @ npm",
                "pnpm install --frozen-lockfile @ pnpm",
                "yarn install --frozen-lockfile @ yarn",
            ],
        )

    def test_an_unchanged_target_is_not_installed_again(self) -> None:
        path = self.add("pnpm-workspace")
        self.run_cli("install")
        self.run_cli("install")
        self.assertEqual(len(self.calls()), 1)
        # A member's package.json is part of what the install read.
        (path / "types" / "package.json").write_text(
            '{"name": "@fixture/types", "version": "2.0.0"}'
        )
        self.run_cli("install")
        self.assertEqual(len(self.calls()), 2)
        self.run_cli("install", "--force")
        self.assertEqual(len(self.calls()), 3)

    def test_ci_mode_installs_nothing(self) -> None:
        self.add("npm")
        code, _, _ = self.run_cli("install", ci="1")
        self.assertEqual((code, self.calls()), (0, []))

    def test_lockless_with_dependencies_fails_and_installs_nothing(self) -> None:
        self.add("lockless")
        self.add("no-deps")
        code, _, err = self.run_cli("install")
        self.assertEqual((code, self.calls()), (1, []))
        self.assertIn("lockless has dependencies and no lockfile", err)

    def test_a_refused_target_does_not_stop_the_others(self) -> None:
        self.add("ambiguous")
        self.add("yarn")
        code, _, err = self.run_cli("install")
        self.assertEqual(code, 1)
        self.assertEqual(self.calls(), ["yarn install --frozen-lockfile @ yarn"])
        self.assertIn("ambiguous has package-lock.json and yarn.lock", err)

    def test_build_runs_only_build_scripts(self) -> None:
        self.add("npm")
        self.add("no-build")
        code, _, _ = self.run_cli("build")
        self.assertEqual((code, self.calls()), (0, ["npm run build @ npm"]))

    def test_a_root_without_build_builds_its_members(self) -> None:
        self.add("yarn-workspace", "ws")
        code, _, _ = self.run_cli("build")
        self.assertEqual((code, self.calls()), (0, ["yarn run build @ frontend"]))

    def test_build_fails_when_a_script_does(self) -> None:
        self.add("pnpm")
        self.env["STUB_FAIL"] = "build"
        code, _, err = self.run_cli("build")
        self.assertEqual(code, 1)
        self.assertIn("pnpm: build failed (exit 3)", err)

    def test_named_targets_only_and_unknown_names_refused(self) -> None:
        self.add("npm")
        self.add("yarn")
        self.run_cli("build", "yarn")
        self.assertEqual(self.calls(), ["yarn run build @ yarn"])
        code, _, err = self.run_cli("build", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no such target: nope", err)

    def test_watch_prefixes_each_line_with_its_key(self) -> None:
        self.add("pnpm-workspace", "ws")
        self.add("npm")
        code, out, _ = self.run_cli("watch")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["[ws] one", "[ws] two"])

    def test_list(self) -> None:
        self.add("pnpm-workspace", "ws")
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["ws"]["members"], ["types"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
