"""The dispatcher (DESIGN §6.1): commands from a list in sort-key order, the product's
extra commands, help text overrides, and the exit-code ladder."""

import argparse
import dataclasses
import os
import unittest
from types import MappingProxyType
from unittest import mock

from support import KIT, STUB, run_cli

from standards_core import cli
from standards_core.commands import CORE_COMMANDS, Command, help_text, paths
from standards_core.common.report import CLEAN, ConfigError


def extra(name, sort_key="", run=None):
    def register(subparsers, product):
        p = subparsers.add_parser(name, help=f"the {name} command")
        p.add_argument(
            "--flag",
            action="store_true",
            help=help_text(product, f"{name} --flag", None),
        )
        p.set_defaults(func=run or (lambda args: CLEAN))

    return Command(name, register, sort_key)


def commands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


class TestCli(unittest.TestCase):
    def test_version(self):
        code, out, _ = run_cli(STUB, "--version")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), STUB.identity.version)

    def test_prog_and_description(self):
        parser = cli.build_parser(STUB)
        self.assertEqual(parser.prog, "stub-nix")
        self.assertEqual(
            parser.description,
            "stub-nix standards tools. See https://example.org/stub-nix/docs.",
        )
        product = dataclasses.replace(
            STUB, identity=dataclasses.replace(STUB.identity, description="Own words.")
        )
        self.assertEqual(cli.build_parser(product).description, "Own words.")

    def test_core_commands_in_sort_key_order(self):
        self.assertEqual(
            [c.name for c in CORE_COMMANDS],
            ["ci", "config", "data-path", "pin-path", "policy", "profile", "sync"],
        )
        self.assertEqual(
            commands(cli.build_parser(STUB)),
            ["ci", "config", "data-path", "pin-path", "policy", "profile", "sync"],
        )

    def test_extra_commands_interleave_by_sort_key(self):
        parser = cli.build_parser(
            STUB, [extra("minibench"), extra("zz-last"), extra("aa", sort_key="pin")]
        )
        self.assertEqual(
            commands(parser),
            [
                "ci",
                "config",
                "minibench",
                "data-path",
                "pin-path",
                "aa",
                "policy",
                "profile",
                "sync",
                "zz-last",
            ],
        )

    def test_extra_command_runs_with_the_product(self):
        seen = []

        def run(args):
            seen.append((args.product, args.flag))
            return 4

        code, _, _ = run_cli(
            STUB,
            "gate",
            "--flag",
            main=lambda argv: cli.main(STUB, argv, [extra("gate", run=run)]),
        )
        self.assertEqual(code, 4)
        self.assertEqual(seen, [(STUB, True)])

    def test_duplicate_command_is_a_bug(self):
        with self.assertRaises(RuntimeError) as caught:
            cli.build_parser(STUB, [extra("config")])
        self.assertIn("config", str(caught.exception))
        code, _, err = run_cli(
            STUB, "config", main=lambda argv: cli.main(STUB, argv, [extra("config")])
        )
        self.assertEqual(code, 3)
        self.assertIn("internal error: RuntimeError", err)

    def test_help_overrides(self):
        product = dataclasses.replace(
            STUB,
            help=MappingProxyType(
                {
                    "config": "Configuration, in the product's words.",
                    "config --json": "JSON, please",
                    "profile show --explain": "where each value came from",
                    "gate --flag": "the extra command's flag",
                }
            ),
        )
        self.assertEqual(
            help_text(product, "config", "x"), "Configuration, in the product's words."
        )
        self.assertEqual(help_text(product, "nope", "x"), "x")
        parser = cli.build_parser(product, [extra("gate")])
        sub = parser._subparsers._group_actions[0].choices
        self.assertEqual(
            sub["config"].description, "Configuration, in the product's words."
        )
        self.assertIn("JSON, please", sub["config"].format_help())
        show = sub["profile"]._subparsers._group_actions[0].choices["show"]
        self.assertIn("where each value came from", show.format_help())
        self.assertIn("the extra command's flag", sub["gate"].format_help())

    def test_an_unknown_help_key_is_a_bug(self):
        for key in ("nope", "config --nope", "profile nope", "gate --flag"):
            product = dataclasses.replace(STUB, help=MappingProxyType({key: "x"}))
            with self.subTest(key=key), self.assertRaises(RuntimeError) as caught:
                cli.build_parser(product)
            self.assertIn(key, str(caught.exception))

    def test_every_handler_gets_the_product(self):
        args = cli.build_parser(KIT).parse_args(["data-path", "x"])
        self.assertIs(args.product, KIT)

    def test_unknown_command_exits_2_listing_commands(self):
        code, _, err = run_cli(STUB, "nonexistent")
        self.assertEqual(code, 2)
        # Python 3.11 quotes the choices, later versions do not.
        self.assertIn("invalid choice: 'nonexistent'", err)
        for name in ("config", "data-path", "pin-path", "profile"):
            self.assertRegex(err, rf"\b'?{name}'?[,)]")

    def test_no_command_exits_2_with_help(self):
        code, _, err = run_cli(STUB)
        self.assertEqual(code, 2)
        self.assertIn("data-path", err)
        self.assertIn("usage: stub-nix", err)

    def test_error_maps_to_its_code(self):
        code, _, err = run_cli(STUB, "data-path", "does/not/exist.json")
        self.assertEqual(code, ConfigError.code)
        self.assertIn("stub-nix data-path: no such data file", err)

    def test_unexpected_exception_exits_3_not_1(self):
        with mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")):
            code, _, err = run_cli(STUB, "data-path", "schema/core.schema.json")
        self.assertEqual(code, 3)
        self.assertEqual(
            err, "stub-nix data-path: internal error: RuntimeError: boom\n"
        )

    def test_unexpected_exception_traceback_on_request(self):
        with (
            mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")),
            mock.patch.dict(os.environ, {"STUB_NIX_DEBUG": "1"}),
        ):
            code, _, err = run_cli(STUB, "data-path", "schema/core.schema.json")
        self.assertEqual(code, 3)
        self.assertIn("Traceback", err)
        # The other product's variable is not this one's.
        with (
            mock.patch.object(paths, "data_path", side_effect=RuntimeError("boom")),
            mock.patch.dict(os.environ, {"KIT_NIX_DEBUG": "1"}),
        ):
            _, _, err = run_cli(STUB, "data-path", "schema/core.schema.json")
        self.assertNotIn("Traceback", err)

    def test_a_command_that_fails_to_build_exits_3(self):
        # A broken command module, or a dependency missing from the environment, fails
        # while the parser is built: an environment error, never 1 (drift).
        def broken(subparsers, product):
            raise ModuleNotFoundError("No module named 'packaging'")

        code, _, err = run_cli(
            STUB,
            "config",
            "modules.ci",
            main=lambda argv: cli.main(STUB, argv, [Command("broken", broken)]),
        )
        self.assertEqual(code, 3)
        self.assertEqual(
            err,
            "stub-nix: internal error: ModuleNotFoundError: No module named 'packaging'\n",
        )


if __name__ == "__main__":
    unittest.main()
