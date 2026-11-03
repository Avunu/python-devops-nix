"""Version skew (DESIGN §9.4): ``--expect-rev``, the caller workflows and the core's own
revision against the lock; ``standards_core.revision()``'s two readers; and
``<PREFIX>_ALLOW_SKEW``. The version-skew and ``--expect-rev`` cases of the ported
``test_sync.py`` are here."""

import json
import os
import tempfile
from importlib import metadata
from pathlib import Path
from unittest import mock

from support import STUB
from support.checkouts import CheckoutCase

import standards_core
from standards_core.common import skew
from standards_core.scaffold import engine
from standards_core.testing.fakes import REV

PREFIX = STUB.identity.env_prefix
ALLOW = {f"{PREFIX}_ALLOW_SKEW": "1"}
VERSION = STUB.identity.version


def caller(sha: str, comment: str = f" # v{VERSION}", owner="example") -> str:
    return (
        "jobs:\n  release:\n    uses:"
        f" {owner}/stub-nix/.github/workflows/stub-release.yml@{sha}{comment}\n"
    )


class TestExpectRev(CheckoutCase):
    def test_expect_rev(self):
        self.synced()
        code, out, _ = self.fn("sync", "--check", "--expect-rev", "f" * 40)
        self.assertEqual(code, 3, out)
        self.assertIn(
            f"flake.lock (skew): version skew: this stub-nix-tools was installed from"
            f" {'f' * 40}, flake.lock pins stub-nix {REV}",
            out,
        )
        self.assertEqual(self.fn("sync", "--check", "--expect-rev", REV)[0], 0)
        with mock.patch.dict(os.environ, ALLOW):
            self.assertEqual(self.fn("sync", "--check", "--expect-rev", "f" * 40)[0], 0)

    def test_nothing_locked_is_not_compared(self):
        self.assertEqual(skew.expect_rev_problems(STUB, "", "f" * 40), [])
        self.assertEqual(skew.expect_rev_problems(STUB, REV, ""), [])

    def test_the_json_report(self):
        self.synced()
        code, out, _ = self.fn(
            "sync", "--check", "--format", "json", "--expect-rev", "f" * 40
        )
        self.assertEqual(code, 3, out)
        doc = json.loads(out)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(
            [(f["path"], f["strategy"]) for f in doc["files"]],
            [("flake.lock", "skew")],
        )


class TestCallers(CheckoutCase):
    def test_caller_workflow_pin(self):
        self.synced()
        self.write(".github/workflows/release.yml", caller("e" * 40, " # v0.0.0"))
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn(
            ".github/workflows/release.yml (skew): version skew: calls stub-nix"
            f" @{'e' * 12} # v0.0.0, but flake.lock pins {REV[:12]} and this"
            f" stub-nix-tools is v{VERSION}",
            out,
        )
        with mock.patch.dict(os.environ, ALLOW):
            self.assertEqual(self.check()[0], 0)

    def test_the_locked_rev_and_version_are_clean(self):
        self.synced()
        self.write(".github/workflows/release.yml", caller(REV))
        self.commit()
        self.assertEqual(self.check()[0], 0)

    def test_a_missing_version_comment_is_skew(self):
        self.synced()
        self.write(".github/workflows/release.yml", caller(REV, ""))
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn(f"calls stub-nix @{REV[:12]}", out)

    def test_one_item_per_file(self):
        self.synced()
        bad = caller("e" * 40) + caller("d" * 40).removeprefix("jobs:\n")
        self.write(".github/workflows/release.yml", bad)
        self.write(".github/workflows/nightly.yaml", caller("d" * 40))
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertEqual(out.count("(skew): version skew"), 2, out)

    def test_what_is_not_an_engine_caller_is_ignored(self):
        self.synced()
        # Another stem, another repository, an untracked file, a file outside workflows.
        self.write(
            ".github/workflows/other.yml",
            caller("e" * 40).replace("stub-release.yml", "lint.yml"),
        )
        self.write(".github/workflows/fork.yml", caller("e" * 40, owner="someone"))
        self.write("docs/example.yml", caller("e" * 40))
        self.commit()
        self.write(".github/workflows/untracked.yml", caller("e" * 40))
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_a_fork_is_named_by_the_lock(self):
        """The callers name the repository the checkout locks the engine from."""
        self.synced()
        lock = json.loads(self.read("flake.lock"))
        lock["nodes"]["stub-nix"]["locked"]["owner"] = "someone"
        self.write("flake.lock", json.dumps(lock))
        self.write(".github/workflows/fork.yml", caller("e" * 40, owner="someone"))
        self.write(".github/workflows/upstream.yml", caller("e" * 40))
        self.commit()
        code, out = self.check()
        self.assertEqual(code, 3, out)
        self.assertIn(".github/workflows/fork.yml (skew)", out)
        self.assertNotIn("upstream.yml", out)

    def test_the_stem_is_the_products(self):
        pattern = skew.caller_pattern(STUB, "example", "stub-nix")
        good = f"example/stub-nix/.github/workflows/stub-ci.yaml@{REV} # v1.2.3"
        m = pattern.search(good)
        self.assertEqual((m["sha"], m["ver"]), (REV, "1.2.3"))
        self.assertIsNone(pattern.search(good.replace("stub-ci", "kit-ci")))
        self.assertIsNotNone(pattern.search(good.upper().replace(REV.upper(), REV)))


class TestCoreRevision(CheckoutCase):
    """Row 3: the core's own revision against the engine's ``base_input`` in the lock."""

    def lock_base(self, rev: str | None) -> None:
        lock = json.loads(self.read("flake.lock"))
        if rev is not None:
            lock["nodes"]["stub-nix"]["inputs"] = {"python-devops-nix": "pdn"}
            lock["nodes"]["pdn"] = {
                "locked": {
                    "type": "github",
                    "owner": "example",
                    "repo": "python-devops-nix",
                    "rev": rev,
                    "narHash": "sha256-x",
                },
                "original": {
                    "type": "github",
                    "owner": "example",
                    "repo": "python-devops-nix",
                },
            }
        self.write("flake.lock", json.dumps(lock))
        self.commit()

    def check_with(self, core_rev: str | None) -> tuple[int, str]:
        with mock.patch.object(standards_core, "revision", return_value=core_rev):
            return self.check()

    def test_another_base_is_skew(self):
        self.synced()
        self.lock_base("c" * 40)
        code, out = self.check_with("b" * 40)
        self.assertEqual(code, 3, out)
        self.assertIn(
            f"flake.lock (skew): version skew: standards-core {'b' * 12} is not the"
            f" python-devops-nix {'c' * 12} that stub-nix locks",
            out,
        )
        with mock.patch.dict(os.environ, ALLOW):
            self.assertEqual(self.check_with("b" * 40)[0], 0)

    def test_the_same_base_is_clean(self):
        self.synced()
        self.lock_base("c" * 40)
        self.assertEqual(self.check_with("c" * 40)[0], 0)

    def test_an_unknown_revision_is_skipped(self):
        """A path or editable install has no revision: the row is skipped."""
        self.synced()
        self.lock_base("c" * 40)
        self.assertEqual(self.check_with(None)[0], 0)

    def test_an_engine_without_the_base_is_skipped(self):
        self.synced()
        self.lock_base(None)
        self.assertEqual(self.check_with("b" * 40)[0], 0)

    def test_the_base_input_is_the_products(self):
        import dataclasses

        product = dataclasses.replace(
            STUB, engine=dataclasses.replace(STUB.engine, base_input="core-src")
        )
        self.synced()
        lock = json.loads(self.read("flake.lock"))
        lock["nodes"]["stub-nix"]["inputs"] = {"core-src": "src"}
        lock["nodes"]["src"] = {"locked": {"type": "github", "rev": "c" * 40}}
        self.write("flake.lock", json.dumps(lock))
        self.commit()
        plan = engine.build(product, self.root, options={})
        with mock.patch.object(standards_core, "revision", return_value="b" * 40):
            items = skew.core_problems(product, plan)
        self.assertEqual(len(items), 1)
        self.assertIn("is not the core-src", items[0].problem)

    def test_no_lock_and_an_unreadable_lock_are_left_to_the_lock_rows(self):
        self.synced()
        plan = engine.build(STUB, self.root, options={})
        (self.root / "flake.lock").write_text("{")
        with mock.patch.object(standards_core, "revision", return_value="b" * 40):
            self.assertEqual(skew.core_problems(STUB, plan), [])
            (self.root / "flake.lock").unlink()
            self.assertEqual(skew.core_problems(STUB, plan), [])


class FakeDistribution:
    def __init__(self, direct_url: str | None):
        self.direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self.direct_url


class TestRevision(CheckoutCase):
    """``standards_core.revision()``: ``direct_url.json``'s ``vcs_info``, else
    ``_source.json``, else ``None``."""

    def setUp(self) -> None:
        super().setUp()
        self.package = Path(tempfile.mkdtemp(dir=self._tmp.name))
        files = mock.patch.object(standards_core, "files", lambda name: self.package)
        files.start()
        self.addCleanup(files.stop)

    def revision(self, direct_url: dict | str | None) -> str | None:
        text = (
            direct_url if not isinstance(direct_url, dict) else json.dumps(direct_url)
        )
        with mock.patch.object(
            standards_core.metadata,
            "distribution",
            return_value=FakeDistribution(text),
        ):
            return standards_core.revision()

    def source(self, value: object) -> None:
        (self.package / "_source.json").write_text(json.dumps(value))

    def test_a_git_install(self):
        sha = "1234567890" * 4
        direct = {
            "url": "https://github.com/example/python-devops-nix",
            "vcs_info": {"vcs": "git", "commit_id": sha},
            "subdirectory": "standards",
        }
        self.source({"rev": "f" * 40})
        self.assertEqual(self.revision(direct), sha)

    def test_a_path_install_has_none(self):
        direct = {"url": "file:///src/standards", "dir_info": {"editable": True}}
        self.assertIsNone(self.revision(direct))
        self.assertIsNone(self.revision(None))
        self.assertIsNone(self.revision("not json"))

    def test_the_nix_build(self):
        direct = {"url": "file:///src/standards", "dir_info": {}}
        self.source({"rev": "c" * 40})
        self.assertEqual(self.revision(direct), "c" * 40)

    def test_not_a_commit_is_none(self):
        direct = {"url": "x", "vcs_info": {"vcs": "git", "commit_id": "main"}}
        self.assertIsNone(self.revision(direct))
        self.source({"rev": "C" * 40})
        self.assertIsNone(self.revision(direct))
        self.source(["c" * 40])
        self.assertIsNone(self.revision(direct))

    def test_not_installed(self):
        self.source({"rev": "c" * 40})
        with mock.patch.object(
            standards_core.metadata,
            "distribution",
            side_effect=metadata.PackageNotFoundError("standards-core"),
        ):
            self.assertEqual(standards_core.revision(), "c" * 40)


class TestAllowSkew(CheckoutCase):
    def test_every_row_is_suppressed(self):
        self.synced()
        self.write(".github/workflows/release.yml", caller("e" * 40))
        lock = json.loads(self.read("flake.lock"))
        lock["nodes"]["stub-nix"]["inputs"] = {"python-devops-nix": "pdn"}
        lock["nodes"]["pdn"] = {"locked": {"type": "github", "rev": "c" * 40}}
        self.write("flake.lock", json.dumps(lock))
        self.commit()
        plan = engine.build(STUB, self.root, options={})
        with mock.patch.object(standards_core, "revision", return_value="b" * 40):
            self.assertEqual(len(skew.problems(STUB, plan, "f" * 40)), 3)
            with mock.patch.dict(os.environ, ALLOW):
                self.assertEqual(skew.problems(STUB, plan, "f" * 40), [])

    def test_the_url_override_needs_allow_skew(self):
        with mock.patch.dict(os.environ, {f"{PREFIX}_URL_OVERRIDE": "path:/x"}):
            code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 3, err)
        self.assertIn(f"needs {PREFIX}_ALLOW_SKEW=1", err)
