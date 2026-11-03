import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from support import KIT, STUB

from standards_core.common import flakelock
from standards_core.common.report import ConfigError, EnvError


def github(owner, repo, rev, nar_hash="sha256-AAAA"):
    return {
        "locked": {
            "type": "github",
            "owner": owner,
            "repo": repo,
            "rev": rev,
            "narHash": nar_hash,
        }
    }


# A checkout's lock: core-lib is its own input, registry is the engine's (one of the
# stub's engine-pinned inputs), and nixpkgs follows the engine's.
LOCK: dict[str, Any] = {
    "version": 7,
    "root": "root",
    "nodes": {
        "root": {
            "inputs": {
                "core-lib": "core-lib",
                "stub-nix": "stub-nix",
                "nixpkgs": ["stub-nix", "nixpkgs"],
            }
        },
        "core-lib": github("example", "core-lib", "f" * 40),
        "stub-nix": {
            **github("example", "stub-nix", "a" * 40),
            "inputs": {
                "registry": "registry",
                "nixpkgs": "nixpkgs",
                "flake-parts": "flake-parts",
                "python-devops-nix": "base",
            },
        },
        "registry": github("example", "registry", "2" * 40),
        "nixpkgs": github("NixOS", "nixpkgs", "n" * 40),
        "flake-parts": {"locked": {"type": "path", "path": "/x"}},
        "base": github("example", "python-devops-nix", "b" * 40),
    },
}


def copy(lock):
    return json.loads(json.dumps(lock))


class TestFlakeLock(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flake.lock"
            with self.assertRaises(EnvError):
                flakelock.load(path)
            path.write_text("{")
            with self.assertRaises(EnvError):
                flakelock.load(path)
            path.write_text(json.dumps(LOCK))
            self.assertEqual(flakelock.load(path), LOCK)

    def test_malformed_locks_are_env_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flake.lock"
            for doc in (
                [],
                {"root": "root", "nodes": []},
                {"root": 1, "nodes": {}},
                {"root": "r", "nodes": {"r": 1}},
            ):
                path.write_text(json.dumps(doc))
                with self.assertRaises(EnvError, msg=doc):
                    flakelock.load(path)
        dangling = {"root": "root", "nodes": {"root": {"inputs": {"pilot": "missing"}}}}
        with self.assertRaises(EnvError):
            flakelock.input_node(STUB, dangling, "pilot")
        bad_inputs = {"root": "root", "nodes": {"root": {"inputs": ["pilot"]}}}
        with self.assertRaises(EnvError):
            flakelock.input_node(STUB, bad_inputs, "pilot")
        not_a_table = {
            "root": "root",
            "nodes": {"root": {"inputs": {"p": "p"}}, "p": {"locked": "x"}},
        }
        with self.assertRaises(ConfigError):
            flakelock.locked_pin(STUB, not_a_table, "p")
        missing = {
            "root": "root",
            "nodes": {"root": {"inputs": {"p": "p"}}, "p": github("o", "r", "a" * 40)},
        }
        del missing["nodes"]["p"]["locked"]["narHash"]
        with self.assertRaisesRegex(EnvError, "no locked narHash"):
            flakelock.locked_pin(STUB, missing, "p")

    def test_follows(self):
        self.assertEqual(
            flakelock.node_at(LOCK, ["nixpkgs"]), ("nixpkgs", LOCK["nodes"]["nixpkgs"])
        )
        self.assertIsNone(flakelock.node_at(LOCK, ["nope"]))

    def test_input_node_falls_back_to_the_engine(self):
        self.assertEqual(
            flakelock.input_node(STUB, LOCK, "core-lib"),
            ("core-lib", LOCK["nodes"]["core-lib"]),
        )
        self.assertEqual(
            flakelock.input_node(STUB, LOCK, "registry"),
            ("registry", LOCK["nodes"]["registry"]),
        )
        self.assertIsNone(flakelock.input_node(STUB, LOCK, "pilot"))
        # Another product's engine input is not this one's.
        self.assertIsNone(flakelock.input_node(KIT, LOCK, "registry"))

    def test_engine_and_base_rev(self):
        self.assertEqual(flakelock.engine_rev(STUB, LOCK), "a" * 40)
        self.assertEqual(flakelock.base_rev(STUB, LOCK), "b" * 40)
        empty = {"root": "root", "nodes": {"root": {}}}
        self.assertIsNone(flakelock.engine_rev(STUB, empty))
        self.assertIsNone(flakelock.base_rev(STUB, empty))
        self.assertIsNone(flakelock.engine_rev(KIT, LOCK))

    def test_github_pin(self):
        pin = flakelock.locked_pin(STUB, LOCK, "registry")
        self.assertEqual(
            (pin.owner, pin.repo, pin.rev), ("example", "registry", "2" * 40)
        )
        self.assertEqual(
            pin.tarball_url,
            f"https://codeload.github.com/example/registry/tar.gz/{'2' * 40}",
        )
        with self.assertRaises(ConfigError):
            flakelock.locked_pin(STUB, LOCK, "pilot")
        with self.assertRaises(ConfigError):
            flakelock.locked_pin(STUB, LOCK, "flake-parts")

    def test_github_enterprise_pin_is_fetched_from_its_host(self):
        # Never codeload.github.com: the token is the GHE host's, and a same-named public
        # repository on github.com is someone else's.
        lock = copy(LOCK)
        lock["nodes"]["root"]["inputs"]["ghe"] = "ghe"
        lock["nodes"]["ghe"] = github("o", "r", "7" * 40)
        lock["nodes"]["ghe"]["locked"]["host"] = "ghe.example.com"
        pin = flakelock.locked_pin(STUB, lock, "ghe")
        self.assertEqual(pin.host, "ghe.example.com")
        self.assertEqual(
            pin.tarball_url,
            f"https://ghe.example.com/api/v3/repos/o/r/tarball/{'7' * 40}",
        )
        # The engine's own pins are github.com's: a lock moving one to another host is
        # refused.
        lock["nodes"]["registry"]["locked"]["host"] = "ghe.example.com"
        with self.assertRaisesRegex(
            ConfigError, "on ghe.example.com, but stub-nix pins it"
        ):
            flakelock.locked_pin(STUB, lock, "registry")

    def test_engine_pins_are_the_engines_only(self):
        # A root input of the same name never shadows the engine's pin in a checkout's lock.
        shadowed = copy(LOCK)
        shadowed["nodes"]["root"]["inputs"]["registry"] = "evil"
        shadowed["nodes"]["evil"] = github("example", "registry", "3" * 40)
        self.assertEqual(flakelock.locked_pin(STUB, shadowed, "registry").rev, "2" * 40)
        del shadowed["nodes"]["stub-nix"]["inputs"]["registry"]
        with self.assertRaisesRegex(ConfigError, "no input 'registry'"):
            flakelock.locked_pin(STUB, shadowed, "registry")
        # The checkout's own inputs still come from its root.
        self.assertEqual(flakelock.locked_pin(STUB, shadowed, "core-lib").rev, "f" * 40)

    def test_standards_profile_is_the_checkouts_own(self):
        lock = copy(LOCK)
        lock["nodes"]["stub-nix"]["inputs"]["standards-profile"] = "p"
        lock["nodes"]["p"] = github("example", "profile", "4" * 40)
        with self.assertRaisesRegex(ConfigError, "no input 'standards-profile'"):
            flakelock.locked_pin(STUB, lock, "standards-profile")
        lock["nodes"]["root"]["inputs"]["standards-profile"] = "p"
        self.assertEqual(
            flakelock.locked_pin(STUB, lock, "standards-profile").repo, "profile"
        )
        self.assertEqual(
            flakelock.locked_rev(STUB, lock, "standards-profile"), "4" * 40
        )

    def test_gitlab_and_git_pins(self):
        lock = copy(LOCK)
        lock["nodes"]["root"]["inputs"].update({"gl": "gl", "g": "g"})
        lock["nodes"]["gl"] = {
            "locked": {
                "type": "gitlab",
                "owner": "group%2Fsub",
                "repo": "profile",
                "rev": "5" * 40,
                "narHash": "sha256-B",
                "host": "git.example.org",
            }
        }
        lock["nodes"]["g"] = {
            "locked": {
                "type": "git",
                "url": "https://git.example.org/libs/shared_lib.git",
                "rev": "6" * 40,
                "narHash": "sha256-C",
            }
        }
        gl = flakelock.locked_pin(STUB, lock, "gl")
        self.assertEqual(
            gl.tarball_url,
            "https://git.example.org/api/v4/projects/group%2Fsub%2Fprofile"
            f"/repository/archive.tar.gz?sha={'5' * 40}",
        )
        self.assertEqual((gl.name, gl.label), ("profile", "group/sub/profile"))
        del lock["nodes"]["gl"]["locked"]["host"]
        self.assertIn(
            "https://gitlab.com/api/v4/",
            flakelock.locked_pin(STUB, lock, "gl").tarball_url,
        )
        g = flakelock.locked_pin(STUB, lock, "g")
        self.assertEqual(
            (g.type, g.name, g.url),
            ("git", "shared_lib", "https://git.example.org/libs/shared_lib.git"),
        )
        with self.assertRaises(ValueError):
            _ = g.tarball_url
        for bad in (
            "https://git.example.org/..",
            "ftp://x/y",
            "https://git.example.org/",
        ):
            lock["nodes"]["g"]["locked"]["url"] = bad
            with self.assertRaises(ConfigError, msg=bad):
                flakelock.locked_pin(STUB, lock, "g")
        # The engine's own pins stay on GitHub.
        lock["nodes"]["registry"] = lock["nodes"]["gl"]
        with self.assertRaisesRegex(ConfigError, "github:example/registry"):
            flakelock.locked_pin(STUB, lock, "registry")

    def test_engine_pins_must_name_their_repository(self):
        for owner, repo in (
            ("example", "pilot"),
            ("attacker", "registry"),
            ("example", "lint-rules"),
        ):
            retargeted = copy(LOCK)
            retargeted["nodes"]["registry"] = github(owner, repo, "2" * 40)
            with self.assertRaisesRegex(
                ConfigError, "stub-nix pins it to github:example/registry", msg=repo
            ):
                flakelock.locked_pin(STUB, retargeted, "registry")
        # The engine's own lock has the pins at its root; the check applies there too.
        own = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"pilot": "p"}},
                "p": github("example", "pilot", "1" * 40),
            },
        }
        self.assertEqual(flakelock.locked_pin(STUB, own, "pilot").repo, "pilot")
        own["nodes"]["p"] = github("Example", "Pilot", "1" * 40)
        self.assertEqual(flakelock.locked_pin(STUB, own, "pilot").repo, "Pilot")
        own["nodes"]["p"] = github("example", "pilot-fork", "1" * 40)
        with self.assertRaises(ConfigError):
            flakelock.locked_pin(STUB, own, "pilot")
        # A product with no engine-pinned inputs reads the same name from the root.
        self.assertEqual(flakelock.locked_pin(KIT, own, "pilot").repo, "pilot-fork")


if __name__ == "__main__":
    unittest.main()
