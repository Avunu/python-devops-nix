"""``sync --write`` around the plan (DESIGN §6.3): the lock, the re-exec and the dev-shell
re-entry, with a fake ``nix`` on PATH that records each call and writes canned locks.

The rest of the suite syncs offline (``STUB_NIX_OFFLINE=1``), which takes the lock and
re-exec branches out of play, so these are where those branches run.
"""

import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path
from typing import ClassVar
from unittest import mock

from support import STUB, git, run_cli
from support.checkouts import CheckoutCase, write_profile

from standards_core.cli import build_parser
from standards_core.commands import sync
from standards_core.common import nar
from standards_core.common.report import EnvError
from standards_core.scaffold import bootstrap, engine
from standards_core.testing.fakes import flake_lock

PREFIX = STUB.identity.env_prefix
REEXEC = f"{PREFIX}_SYNC_REEXEC"
REENTERED = f"{PREFIX}_SYNC_REENTERED"
LOCK_CHANGED = f"{PREFIX}_SYNC_LOCK_CHANGED"
RESULT = f"{PREFIX}_SYNC_RESULT"
CI = f"{PREFIX}_CI"
LOCKED_REV = f"{PREFIX}_LOCKED_REV"

FAKE_NIX = """#!{python}
import json, os, sys
argv = sys.argv[1:]
keep = {keep!r}
with open(os.environ["FAKE_NIX_LOG"], "a") as log:
    env = {{k: os.environ[k] for k in keep if k in os.environ}}
    log.write(json.dumps({{"argv": argv, "env": env}}) + "\\n")
locks = json.loads(os.environ.get("FAKE_NIX_LOCKS", "{{}}"))
key = " ".join(argv[:2])
if key in locks:
    with open("flake.lock", "w") as f:
        json.dump(locks[key], f)
if argv[:1] == ["eval"]:
    sys.stdout.write(os.environ.get("FAKE_NIX_VERSION", ""))
child = argv[:1] in (["run"], ["develop"])
if "FAKE_CHILD_RESULT" in os.environ and child and "{result}" in os.environ:
    with open(os.environ["{result}"], "w") as f:
        f.write(os.environ["FAKE_CHILD_RESULT"])
sys.exit(0 if argv[:1] == ["eval"] else int(os.environ.get("FAKE_NIX_CODE", "0")))
"""

# The URL a rendered flake.nix gives the engine by default (release-<major>).
BRANCH = "release-1"
URL = f"github:example/stub-nix/{BRANCH}"
PINNED = "github:example/stub-nix/v1.0.0"

# The probe itself: FakeNix patches it out in every test but the probe's own.
RELEASE_BRANCH_EXISTS = bootstrap.release_branch_exists


def flake(*inputs: str, engine_url: str = URL, tool: str = "v1") -> str:
    """A flake.nix with the engine, nixpkgs following it, and ``inputs``
    (``github:example/<name>/<tool>`` each)."""
    lines = [f'    stub-nix.url = "{engine_url}";']
    lines.append('    nixpkgs.follows = "stub-nix/nixpkgs";')
    lines += [f'    {name}.url = "github:example/{name}/{tool}";' for name in inputs]
    body = "\n".join(lines)
    return f"{{\n  inputs = {{\n{body}\n  }};\n  outputs = inputs: {{ }};\n}}\n"


def specs(*inputs: str, engine_url: str = URL, tool: str = "v1") -> dict:
    """``engine.flake_input_specs`` of ``flake(...)``."""
    return engine.flake_input_specs(flake(*inputs, engine_url=engine_url, tool=tool))


def locked(*inputs: str, tool: str = "v1") -> dict:
    """The lock ``nix flake lock`` leaves for ``flake(*inputs)``."""
    return flake_lock(flake(*inputs, tool=tool))


def main_locked(*inputs: str) -> dict:
    """``locked(...)`` with the engine locked from its default branch."""
    lock = locked(*inputs)
    lock["nodes"]["stub-nix"]["original"].pop("ref")
    return lock


class FakeNix(CheckoutCase):
    """A stub checkout with a fake ``nix`` (and ``git``, nothing else) on PATH, online."""

    def setUp(self) -> None:
        super().setUp()
        for key in (
            f"{PREFIX}_OFFLINE",
            REEXEC,
            REENTERED,
            LOCK_CHANGED,
            RESULT,
            "FAKE_CHILD_RESULT",
            "FAKE_NIX_CODE",
            "FAKE_NIX_LOCKS",
        ):
            os.environ.pop(key, None)
        self.bin = Path(self._tmp.name) / "bin"
        self.bin.mkdir()
        nix = self.bin / "nix"
        keep = (REEXEC, REENTERED, LOCK_CHANGED, CI, LOCKED_REV)
        nix.write_text(FAKE_NIX.format(python=sys.executable, keep=keep, result=RESULT))
        nix.chmod(0o755)
        real_git = shutil.which("git")
        assert real_git
        (self.bin / "git").symlink_to(real_git)
        self.log = self.bin / "log.jsonl"
        env = mock.patch.dict(
            os.environ,
            {
                "FAKE_NIX_LOG": str(self.log),
                "PATH": str(self.bin),
                "FAKE_NIX_VERSION": STUB.identity.version,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        # The release-branch probe asks GitHub; these tests are offline and say it exists.
        probe = mock.patch.object(bootstrap, "release_branch_exists", return_value=True)
        probe.start()
        self.addCleanup(probe.stop)

    def calls(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def argvs(self) -> list[str]:
        return [" ".join(c["argv"]) for c in self.calls()]

    def runner(self, product=STUB, **kwargs) -> bootstrap.Runner:
        return bootstrap.Runner(self.root, product, **kwargs)

    def write_lock(self, lock: dict) -> None:
        self.write("flake.lock", json.dumps(lock))

    def tools(self, *names: str) -> None:
        """Put ``names`` on PATH (each the fake nix, which only logs)."""
        for name in names:
            (self.bin / name).symlink_to(self.bin / "nix")

    def synced_offline(self) -> None:
        with mock.patch.dict(os.environ, {f"{PREFIX}_OFFLINE": "1"}):
            self.synced()


class TestLockReasons(FakeNix):
    def test_missing_lock(self):
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs(), URL), (True, False)
        )

    def test_unreadable_lock_relocks(self):
        self.write("flake.lock", "{")
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs(), URL), (True, False)
        )

    def test_input_set_changed(self):
        self.write_lock(locked("tool"))
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs("tool"), URL),
            (False, False),
        )
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs("tool", "extra"), URL),
            (True, False),
        )

    def test_another_branch_relocks(self):
        """Same input names, another branch: the lock is stale."""
        self.write_lock(locked("tool"))
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs("tool", tool="v2"), URL),
            (True, False),
        )

    def test_follows_changed_relocks(self):
        self.write_lock(locked("tool"))
        wanted = specs("tool") | {"nixpkgs": "github:nixos/nixpkgs/nixos-unstable"}
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, wanted, URL), (True, False)
        )

    def test_main_locked(self):
        self.write_lock(main_locked("tool"))
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, specs("tool"), URL), (False, True)
        )

    def test_self_is_not_an_input(self):
        """``self.submodules = true;`` in ``inputs`` declares no input to lock."""
        text = flake("tool").replace(
            "  inputs = {\n", "  inputs = {\n    self.submodules = true;\n"
        )
        self.write_lock(locked("tool"))
        wanted = engine.flake_input_specs(text)
        self.assertNotIn("self", wanted)
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, wanted, URL), (False, False)
        )


class TestPinnedEngine(FakeNix):
    """``dev-shell.stub-nix-url`` pins the engine (a tag, a rev, a fork): phase A locks it
    once and never moves it again, and only the default release branch is probed."""

    def pinned_lock(self) -> dict:
        lock = locked("tool")
        lock["nodes"]["stub-nix"]["original"]["ref"] = "v1.0.0"
        return lock

    def wanted(self) -> dict:
        return specs("tool", engine_url=PINNED)

    def test_pinned_lock_is_kept(self):
        self.write_lock(self.pinned_lock())
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, self.wanted(), PINNED),
            (False, False),
        )
        self.assertFalse(bootstrap.phase_a_lock(self.runner(), self.wanted(), PINNED))
        self.assertEqual(self.argvs(), [])

    def test_a_new_pin_moves_once(self):
        self.write_lock(locked("tool"))
        self.assertEqual(
            bootstrap.lock_reasons(STUB, self.root, self.wanted(), PINNED),
            (False, True),
        )
        os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": self.pinned_lock()})
        self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.wanted(), PINNED))
        self.assertEqual(self.argvs(), ["flake update stub-nix"])
        self.assertFalse(bootstrap.phase_a_lock(self.runner(), self.wanted(), PINNED))
        self.assertEqual(
            self.argvs(), ["flake update stub-nix"], "a second sync moved it again"
        )

    def test_a_pin_is_never_probed(self):
        self.assertFalse(bootstrap.release_ref_needed(STUB, self.root, PINNED))
        self.assertTrue(bootstrap.release_ref_needed(STUB, self.root, URL))


class TestPhaseALock(FakeNix):
    inputs: ClassVar[dict] = specs("tool")

    def test_no_lock_locks_once(self):
        os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake lock": locked("tool")})
        self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
        self.assertEqual(self.argvs(), ["flake lock"])

    def test_main_locked_moves_to_release(self):
        self.write_lock(main_locked("tool"))
        os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": locked("tool")})
        self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
        self.assertEqual(self.argvs(), ["flake update stub-nix"])

    def test_new_lock_on_main_is_moved_too(self):
        os.environ["FAKE_NIX_LOCKS"] = json.dumps(
            {"flake lock": main_locked("tool"), "flake update": locked("tool")}
        )
        self.assertTrue(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
        self.assertEqual(self.argvs(), ["flake lock", "flake update stub-nix"])

    def test_another_branch_locks(self):
        self.write_lock(locked("tool"))
        os.environ["FAKE_NIX_LOCKS"] = json.dumps(
            {"flake lock": locked("tool", tool="v2")}
        )
        wanted = specs("tool", tool="v2")
        self.assertTrue(bootstrap.phase_a_lock(self.runner(), wanted, URL))
        self.assertEqual(self.argvs(), ["flake lock"])

    def test_current_lock_runs_nothing(self):
        self.write_lock(locked("tool"))
        self.assertFalse(bootstrap.phase_a_lock(self.runner(), self.inputs, URL))
        self.assertEqual(self.argvs(), [])

    def test_the_url_override_locks_the_checkout_under_test(self):
        self.write_lock(locked("tool"))
        with mock.patch.dict(
            os.environ,
            {f"{PREFIX}_URL_OVERRIDE": "path:/x", f"{PREFIX}_ALLOW_SKEW": "1"},
        ):
            bootstrap.phase_a_lock(self.runner(), self.inputs, URL)
        self.assertEqual(self.argvs(), ["flake lock --override-input stub-nix path:/x"])

    def test_dry_run_and_offline_run_nothing(self):
        for kwargs in ({"dry_run": True}, {"offline": True}):
            with self.subTest(**kwargs):
                self.assertFalse(
                    bootstrap.phase_a_lock(self.runner(**kwargs), self.inputs, URL)
                )
        self.assertEqual(self.argvs(), [])


class TestReexec(FakeNix):
    def setUp(self) -> None:
        super().setUp()
        self.write_lock(locked())

    def test_same_version_stays(self):
        bootstrap.maybe_reexec(self.runner(), ["--skip-lock"])
        self.assertEqual([c["argv"][0] for c in self.calls()], ["eval"])

    def test_other_version_reexecs_once_from_the_lock(self):
        os.environ["FAKE_NIX_VERSION"] = "9.9.9"
        with self.assertRaises(SystemExit) as caught:
            bootstrap.maybe_reexec(self.runner(), ["--skip-lock"], lock_changed=True)
        self.assertEqual(caught.exception.code, 0)
        calls = self.calls()
        self.assertEqual(
            calls[-1]["argv"],
            ["run", "--no-pure-eval", ".#stub-init", "--", "--sync", "--skip-lock"],
        )
        self.assertEqual(calls[-1]["env"].get(REEXEC), "1")
        self.assertEqual(calls[-1]["env"].get(LOCK_CHANGED), "1")
        os.environ[REEXEC] = "1"
        bootstrap.maybe_reexec(self.runner(), [])
        self.assertEqual(len(self.calls()), len(calls), "a second re-exec ran")

    def test_the_reexec_hook_names_the_command(self):
        os.environ["FAKE_NIX_VERSION"] = "9.9.9"
        product = dataclasses.replace(
            STUB,
            sync=dataclasses.replace(
                STUB.sync,
                reexec_argv=lambda argv: ["nix", "run", ".#other", "--", *argv],
            ),
        )
        with self.assertRaises(SystemExit):
            bootstrap.maybe_reexec(self.runner(product), ["--force"])
        self.assertEqual(self.calls()[-1]["argv"], ["run", ".#other", "--", "--force"])

    def test_no_reexec_under_the_url_override(self):
        os.environ["FAKE_NIX_VERSION"] = "9.9.9"
        with mock.patch.dict(
            os.environ,
            {f"{PREFIX}_URL_OVERRIDE": "path:/x", f"{PREFIX}_ALLOW_SKEW": "1"},
        ):
            bootstrap.maybe_reexec(self.runner(), [])
        self.assertEqual(self.calls(), [])

    def test_a_lock_not_on_github_is_not_read(self):
        lock = locked()
        lock["nodes"]["stub-nix"]["locked"]["type"] = "path"
        self.write_lock(lock)
        self.assertIsNone(bootstrap.locked_engine_version(self.runner()))
        self.assertEqual(self.calls(), [])

    def test_lock_reaches_nix_as_data(self):
        lock = locked()
        lock["nodes"]["stub-nix"]["locked"]["owner"] = (
            "example${builtins.currentSystem}"
        )
        self.write_lock(lock)
        product = dataclasses.replace(
            STUB, engine=dataclasses.replace(STUB.engine, version_file="VERSION")
        )
        self.assertEqual(
            bootstrap.locked_engine_version(self.runner(product)),
            STUB.identity.version,
        )
        call = self.calls()[0]
        expr = call["argv"][call["argv"].index("--expr") + 1]
        self.assertNotIn("example", expr)
        self.assertNotIn("VERSION", expr)
        self.assertIn(f'builtins.getEnv "{LOCKED_REV}"', expr)
        data = json.loads(call["env"][LOCKED_REV])
        self.assertEqual(data["node"]["owner"], "example${builtins.currentSystem}")
        self.assertEqual(data["file"], "VERSION")


class TestPassthrough(CheckoutCase):
    """What a sync hands over is a command line the receiving command reads: the
    product's flags through ``sync.passthrough``, spelled for its init app on a re-exec
    and for ``<cli> sync`` on a re-entry."""

    def setUp(self) -> None:
        super().setUp()

        def add_arguments(parser, after):
            if after == "--only":
                parser.add_argument("--unit-version", metavar="N")

        def passthrough(args, init):
            if not args.unit_version:
                return []
            return ["--version" if init else "--unit-version", args.unit_version]

        self.product = dataclasses.replace(
            STUB,
            sync=dataclasses.replace(
                STUB.sync, add_arguments=add_arguments, passthrough=passthrough
            ),
        )
        self.parser = build_parser(self.product)
        self.args = self.parser.parse_args(
            [
                *("sync", "--write", "--unit-version", "16"),
                *("--standards", "recommended", "--skip-lock", "--force"),
                *("--only", "a,b", "--profile-path", "prof"),
            ]
        )

    def test_the_reexec_reads_as_the_init_app(self):
        argv = sync.passthrough(self.product, self.args, init=True)
        self.assertEqual(
            argv,
            [
                *("--skip-lock", "--version", "16", "--force"),
                *("--standards", "recommended"),
                *("--profile-path", str(Path("prof").resolve())),
                *("--only", "a,b"),
            ],
        )

    def test_the_reentry_reads_as_cli_sync(self):
        again = self.parser.parse_args(
            ["sync", "--write", *sync.passthrough(self.product, self.args)]
        )
        self.assertEqual(
            (again.unit_version, again.standards, again.only, again.force),
            ("16", "recommended", ["a,b"], True),
        )
        self.assertTrue(again.skip_lock)
        self.assertEqual(again.profile_path, str(Path("prof").resolve()))


class TestReentry(FakeNix):
    def relocking(self, **changes):
        """The stub with a pre-re-entry step, as a product whose dev shell needs a lock
        first would have."""
        return dataclasses.replace(
            STUB,
            sync=dataclasses.replace(
                STUB.sync,
                before_reentry=lambda runner: runner.run(
                    ["nix", "run", "--no-pure-eval", ".#relock"]
                ),
                **changes,
            ),
        )

    def test_without_the_tools_runs_the_hook_then_reenters(self):
        with self.assertRaises(SystemExit):
            bootstrap.ensure_tools(
                self.runner(self.relocking()), ["--skip-lock"], lock_changed=True
            )
        calls = self.calls()
        self.assertEqual(calls[0]["argv"], ["run", "--no-pure-eval", ".#relock"])
        self.assertEqual(
            calls[1]["argv"],
            [
                *("develop", "--no-pure-eval", "-c"),
                *("stub-nix", "sync", "--write", "--phase", "b", "--skip-lock"),
            ],
        )
        self.assertEqual(calls[1]["env"], {CI: "1", REENTERED: "1", LOCK_CHANGED: "1"})

    def test_the_reentry_hook_names_the_command(self):
        product = self.relocking(
            reentry_argv=lambda argv: ["nix", "develop", ".#tools", "-c", *argv]
        )
        with self.assertRaises(SystemExit):
            bootstrap.ensure_tools(self.runner(product), ["--force"])
        self.assertEqual(
            self.calls()[-1]["argv"], ["develop", ".#tools", "-c", "--force"]
        )
        self.assertNotIn(LOCK_CHANGED, self.calls()[-1]["env"])

    def test_the_url_override_reaches_the_dev_shell(self):
        with (
            mock.patch.dict(
                os.environ,
                {f"{PREFIX}_URL_OVERRIDE": "path:/x", f"{PREFIX}_ALLOW_SKEW": "1"},
            ),
            self.assertRaises(SystemExit),
        ):
            bootstrap.ensure_tools(self.runner(), [])
        self.assertEqual(
            self.calls()[0]["argv"][:5],
            ["develop", "--no-pure-eval", "--override-input", "stub-nix", "path:/x"],
        )

    def test_second_entry_is_an_environment_error(self):
        os.environ[REENTERED] = "1"
        with self.assertRaises(EnvError):
            bootstrap.ensure_tools(self.runner(), [])
        self.assertEqual(self.calls(), [])

    def test_with_the_tools_nothing_runs(self):
        self.tools("uv")
        bootstrap.ensure_tools(self.runner(), [])
        self.assertEqual(self.calls(), [])

    def test_every_required_tool_is_needed(self):
        self.tools("uv")
        product = dataclasses.replace(
            STUB, sync=dataclasses.replace(STUB.sync, required_tools=("uv", "npm"))
        )
        with self.assertRaises(SystemExit):
            bootstrap.ensure_tools(self.runner(product), [])
        self.assertEqual([c["argv"][0] for c in self.calls()], ["develop"])

    def test_offline_and_dry_run_never_reenter(self):
        for kwargs in ({"dry_run": True}, {"offline": True}):
            bootstrap.ensure_tools(self.runner(**kwargs), [])
        self.assertEqual(self.calls(), [])


class TestHandoverResult(FakeNix):
    """A failed ``nix develop``/``nix run`` is exit 3, never 1 (drift); the child sync's
    own code comes back through ``<PREFIX>_SYNC_RESULT``."""

    def reenter(self) -> tuple[int, str]:
        self.synced_offline()
        code, _, err = self.fn("sync", "--write")
        return code, err

    def test_failed_nix_develop_is_an_environment_error(self):
        os.environ["FAKE_NIX_CODE"] = "1"
        code, err = self.reenter()
        self.assertEqual(code, 3, err)
        self.assertIn("nix develop", err)

    def test_child_code_is_passed_through(self):
        os.environ["FAKE_NIX_CODE"] = "1"
        os.environ["FAKE_CHILD_RESULT"] = "2"
        code, err = self.reenter()
        self.assertEqual(code, 2, err)

    def test_failed_reexec_is_an_environment_error(self):
        self.write_lock(locked())
        os.environ["FAKE_NIX_VERSION"] = "9.9.9"
        os.environ["FAKE_NIX_CODE"] = "1"
        with self.assertRaises(EnvError):
            bootstrap.maybe_reexec(self.runner(), [])
        os.environ["FAKE_CHILD_RESULT"] = "0"
        with self.assertRaises(SystemExit) as caught:
            bootstrap.maybe_reexec(self.runner(), [])
        self.assertEqual(caught.exception.code, 0)

    def test_child_records_its_result(self):
        result = self.bin / "result"
        with mock.patch.dict(
            os.environ, {f"{PREFIX}_OFFLINE": "1", RESULT: str(result)}
        ):
            code, _, err = self.fn("sync", "--write")
        self.assertEqual(result.read_text().strip(), str(code), err)

    def test_an_error_is_recorded_too(self):
        result = self.bin / "result"
        self.table("nope = 1\n")
        with mock.patch.dict(
            os.environ, {f"{PREFIX}_OFFLINE": "1", RESULT: str(result)}
        ):
            code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 2, err)
        self.assertEqual(result.read_text().strip(), "2")


class TestBareSyncBootstrap(FakeNix):
    """A bare ``stub-nix sync --write`` on a checkout locked to the engine's default
    branch, with no uv: phase A moves the engine, and the re-entered phase B is told the
    lock changed."""

    def test_main_locked_checkout(self):
        self.synced_offline()
        good = json.loads(self.read("flake.lock"))
        moved = json.loads(self.read("flake.lock"))
        moved["nodes"]["stub-nix"]["original"].pop("ref")
        self.write_lock(moved)
        self.commit()
        os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake update": good})
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            [" ".join(c["argv"][:2]) for c in self.calls()],
            ["flake update", "eval --raw", "develop --no-pure-eval"],
        )
        self.assertEqual(self.calls()[-1]["env"].get(LOCK_CHANGED), "1")
        self.assertIn("flake.lock", git(self.root, "diff", "--cached", "--name-only"))

    def test_a_current_checkout_locks_nothing(self):
        self.synced_offline()
        self.tools("uv")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["eval"])

    def test_reentered_phase_b_knows_the_lock_changed(self):
        seen = []
        product = dataclasses.replace(
            STUB,
            sync=dataclasses.replace(
                STUB.sync,
                phase_b_after=lambda plan, runner, args, changed, touched: (
                    seen.append(changed) or []
                ),
            ),
        )
        self.synced_offline()
        self.tools("uv")
        with mock.patch.dict(os.environ, {LOCK_CHANGED: "1", REENTERED: "1"}):
            code, _, err = run_cli(
                product, "sync", "--write", "--phase", "b", cwd=self.root
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(seen, [True])

    def test_skip_lock_runs_no_phase_a_step(self):
        self.synced_offline()
        self.tools("uv")
        self.write_lock(main_locked())
        self.commit()
        code, _, err = self.fn("sync", "--write", "--skip-lock")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), [])
        # The lock stays on another branch: drift --check reports.
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("stub-nix (locked from github:example/stub-nix, flake.nix", out)

    def test_only_another_file_runs_no_phase_a_step(self):
        self.synced_offline()
        self.tools("uv")
        self.write_lock(main_locked())
        self.commit()
        code, _, err = self.fn("sync", "--write", "--only", ".editorconfig")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls(), [])


class TestReleaseBranchPreflight(FakeNix):
    """Before the release branch exists, a sync that would lock it refuses before
    writing anything."""

    def absent(self) -> None:
        probe = mock.patch.object(
            bootstrap, "release_branch_exists", return_value=False
        )
        probe.start()
        self.addCleanup(probe.stop)

    def test_an_unbootstrapped_checkout_is_refused_with_nothing_written(self):
        self.absent()
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 3, err)
        self.assertIn(
            f"https://github.com/example/stub-nix has no {BRANCH} branch yet", err
        )
        self.assertIn("stub-nix v1.0.0 is tagged", err)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.calls(), [])

    def test_preflight_phase(self):
        self.absent()
        code, _, err = self.fn("sync", "--write", "--phase", "preflight")
        self.assertEqual(code, 3, err)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_preflight_refuses_what_phase_a_would(self):
        """A product's init app runs the preflight before it writes its own files: an
        invalid configuration is exit 2 there, with nothing written or probed."""
        for argv, needle in (
            (("--standards", "minimal"), "already names profile 'recommended'"),
            (("--standards", "./nope"), "already names profile"),
        ):
            with self.subTest(argv=argv):
                code, _, err = self.fn("sync", "--write", "--phase", "preflight", *argv)
                self.assertEqual(code, 2, err)
                self.assertIn(needle, err)
                self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.table("nope = 1\n")
        self.commit()
        code, _, err = self.fn("sync", "--write", "--phase", "preflight")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.calls(), [])

    def test_preflight_follows_the_checkouts_own_engine_url(self):
        """``dev-shell.stub-nix-url`` is the checkout's choice: the preflight checks the
        URL phase A would lock, not the release branch."""
        self.absent()
        self.table('dev-shell.stub-nix-url = "github:example/stub-nix"\n')
        self.commit()
        code, _, err = self.fn("sync", "--write", "--phase", "preflight")
        self.assertEqual(code, 0, err)

    def test_preflight_with_skip_lock_does_not_probe(self):
        self.absent()
        code, _, err = self.fn("sync", "--write", "--phase", "preflight", "--skip-lock")
        self.assertEqual(code, 0, err)

    def test_a_dry_run_renders_against_the_rev_the_lock_would_take(self):
        """A dry run on an unbootstrapped checkout renders phase B against the release
        branch's head, as ``git ls-remote`` reports it, not an empty rev."""
        revs = []
        real = engine.build

        def build(*a, **kw):
            plan = real(*a, **kw)
            revs.append((kw.get("phases"), plan.ctx.engine.rev))
            return plan

        with (
            mock.patch.object(bootstrap, "release_branch_rev", return_value="c" * 40),
            mock.patch.object(engine, "build", build),
        ):
            code, out, err = self.fn("sync", "--write", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn((("b",), "c" * 40), revs, revs)
        self.assertIn("would run: nix flake lock", out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_a_lock_already_on_the_branch_is_not_probed(self):
        self.write_lock(locked())
        self.assertFalse(bootstrap.release_ref_needed(STUB, self.root, URL))
        self.write_lock(main_locked())
        self.assertTrue(bootstrap.release_ref_needed(STUB, self.root, URL))

    def test_offline_dry_run_and_unknown_pass(self):
        self.absent()
        bootstrap.require_release_branch(self.runner(offline=True), URL)
        bootstrap.require_release_branch(self.runner(dry_run=True), URL)
        with mock.patch.object(bootstrap, "release_branch_exists", return_value=None):
            bootstrap.require_release_branch(self.runner(), URL)


class TestReleaseBranchProbe(FakeNix):
    """``git ls-remote`` against the engine repository, as a fake ``git`` answers it."""

    def fake_git(self, code: int, out: str = "") -> Path:
        args = self.bin / "git-args"
        script = self.bin / "git"
        script.unlink()
        script.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"open({str(args)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
            f"sys.stdout.write({out!r})\nsys.exit({code})\n"
        )
        script.chmod(0o755)
        return args

    def test_exists(self):
        exists = RELEASE_BRANCH_EXISTS
        args = self.fake_git(0)
        self.assertTrue(exists(STUB, self.root, BRANCH))
        self.assertEqual(
            args.read_text(),
            "ls-remote --exit-code --heads https://github.com/example/stub-nix "
            + BRANCH,
        )
        self.fake_git(2)
        self.assertFalse(exists(STUB, self.root, BRANCH))
        self.fake_git(128)
        self.assertIsNone(exists(STUB, self.root, BRANCH))

    def test_rev(self):
        sha = "d" * 40
        self.fake_git(0, f"{sha}\trefs/heads/{BRANCH}\n")
        self.assertEqual(bootstrap.release_branch_rev(STUB, self.root, BRANCH), sha)
        self.fake_git(0, f"{sha}\trefs/heads/other\n")
        self.assertIsNone(bootstrap.release_branch_rev(STUB, self.root, BRANCH))
        self.fake_git(2)
        self.assertIsNone(bootstrap.release_branch_rev(STUB, self.root, BRANCH))


class TestOrgProfileFirstLock(FakeNix):
    """The first sync of a checkout whose own table turns ``extras`` on, with an org
    profile that turns ``commits`` on (``extras`` needs it) and is not locked yet: phase
    A renders the flake over ``minimal``, where ``commits`` is off, so the need is left
    to the full resolution after the lock instead of refusing the sync."""

    profile = "github:example/profile"
    extra_pyproject = "\n[tool.stub-nix.extras]\nenable = true\n"

    def profile_lock(self) -> dict:
        rev = "c" * 40
        # The locked tree, where pin-path finds it without a fetch.
        tree = write_profile(self.root, rel=f".dev-dist/pins/profile-{rev}")
        lock = flake_lock(flake())
        lock["nodes"]["standards-profile"] = {
            "flake": False,
            "locked": {
                "type": "github",
                "owner": "example",
                "repo": "profile",
                "rev": rev,
                "narHash": nar.nar_hash(tree),
            },
            "original": {"type": "github", "owner": "example", "repo": "profile"},
        }
        lock["nodes"]["root"]["inputs"]["standards-profile"] = "standards-profile"
        return lock

    def test_the_need_waits_for_the_locked_profile(self):
        os.environ["FAKE_NIX_LOCKS"] = json.dumps({"flake lock": self.profile_lock()})
        code, _, err = self.fn("sync", "--write", "--phase", "a")
        self.assertEqual(code, 0, err)
        self.assertIn('url = "github:example/profile";', self.read("flake.nix"))
        # The flake rendered over minimal is the one the locked profile renders: one lock.
        self.assertEqual(
            [a for a in self.argvs() if a.startswith("flake")], ["flake lock"]
        )
        # The full resolution still checks the need: a checkout that turns commits off
        # itself is refused before anything is locked.
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml") + "\n[tool.stub-nix.commits]\nenable = false\n",
        )
        git(self.root, "rm", "-q", "--cached", "flake.lock")
        (self.root / "flake.lock").unlink()
        self.commit()
        self.log.unlink()
        code, _, err = self.fn("sync", "--write", "--phase", "a")
        self.assertEqual(code, 2, err)
        self.assertIn("module extras needs module commits", err)
        self.assertEqual(self.calls(), [])
