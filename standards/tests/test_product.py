"""The Product API (DESIGN §3): every dataclass builds with its defaults, every
``__post_init__`` rule refuses, and products hash by identity.

``lib-standards`` runs this on python311, where a ``MappingProxyType`` field default
fails at class creation ("mutable default"); building each class here is that
regression test.
"""

import dataclasses
import functools
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from support import KIT, STUB

from standards_core import product as api
from standards_core.product import (
    Branching,
    CiConfig,
    Engine,
    FirstOptIn,
    Fixture,
    HeaderKind,
    Hooks,
    Identity,
    JsonStyle,
    Markers,
    Modules,
    Paths,
    Pins,
    Product,
    ReleaseUnit,
    Schemas,
    SyncHooks,
)


def replace(obj, **changes):
    return dataclasses.replace(obj, **changes)


def product(**changes) -> Product:
    """A copy of the stub product with ``changes``: ``__post_init__`` runs again."""
    return replace(STUB, **changes)


def plain() -> Product:
    """A product that sets only the required fields: every other one is its default."""
    return Product(
        identity=KIT.identity,
        markers=KIT.markers,
        engine=KIT.engine,
        pins=Pins(".dev-dist/pins"),
        modules=KIT.modules,
        data_root=KIT.data_root,
        hooks=Hooks(lambda r: r, lambda *a: {}, lambda *a: [], lambda *a: {}),
    )


class TestDefaults(unittest.TestCase):
    def test_every_dataclass_builds_with_its_defaults(self):
        built = [
            Identity(
                "x",
                "x",
                "x-tools",
                "1.0.0",
                "x",
                "X",
                "x",
                "x-init --sync",
                "hint",
                "u",
            ),
            Markers(
                "m", "h", "hl", "l", "b {name}", "e {name}", "<{name}>", "</{name}>"
            ),
            HeaderKind("#"),
            Engine("x", "o/x", "release-{major}", "k", "spec", "x-*", "x-init"),
            Pins(".dev-dist/pins"),
            Paths(),
            Modules(api.CORE_MODULES),
            Schemas(),
            Fixture(""),
            FirstOptIn("flake.nix", "hint"),
            JsonStyle(),
            Branching("main+tags", "tags", lambda cfg: "main", lambda cfg, facts: ""),
            ReleaseUnit("u", "u/", "u/__unit__.toml"),
            Hooks(lambda r: r, lambda *a: {}, lambda *a: [], lambda *a: {}),
            SyncHooks(),
            CiConfig(),
        ]
        self.assertEqual(len(built), 16)

    def test_mapping_defaults_are_read_only_and_per_instance(self):
        a, b = Pins("d"), Pins("d")
        self.assertIsInstance(a.engine_pinned, MappingProxyType)
        self.assertIsNot(a.engine_pinned, b.engine_pinned)
        for mapping in (
            Modules(api.CORE_MODULES).needs,
            Fixture("").files,
            CiConfig().labels,
            CiConfig().arg_aliases,
            STUB.header_kinds,
            plain().handlers,
            plain().fixtures,
            STUB.help,
        ):
            self.assertIsInstance(mapping, MappingProxyType)
            self.assertEqual(dict(mapping), {})
            with self.assertRaises(TypeError):
                mapping["x"] = 1  # type: ignore[index]

    def test_default_values(self):
        self.assertEqual(STUB.identity.requires_key, "requires-stub-nix")
        self.assertEqual(STUB.identity.config_file, "pyproject.toml")
        self.assertTrue(STUB.identity.config_required)
        self.assertFalse(KIT.identity.config_required)
        self.assertEqual(STUB.engine.version_file, "version.txt")
        self.assertEqual(STUB.engine.base_input, "python-devops-nix")
        self.assertEqual(STUB.rollout_inputs, ("stub-nix", "standards-profile"))
        self.assertEqual(STUB.ci.gates, ("pr-policy", "lint", "test"))
        self.assertEqual(STUB.ci.check_context.format(gate="lint"), "ci / lint")
        self.assertEqual(STUB.paths.github_marks, (".github",))
        self.assertEqual(STUB.modules.required, "dev-shell")
        self.assertEqual(STUB.json_style, JsonStyle("\t", 2, 110, "jsonc"))
        self.assertEqual(STUB.fleet_key, "repos")
        self.assertEqual(STUB.sync.required_tools, ("uv",))
        self.assertEqual(STUB.sync.rollout_steps, ())

    def test_default_hooks(self):
        hooks = STUB.hooks
        self.assertEqual(
            list(hooks.url_placeholders({"repo": "o/r"})), [("repo", "o/r")]
        )
        self.assertEqual(list(hooks.url_placeholders({})), [("repo", "")])
        self.assertEqual(hooks.pr_checks(None), [])
        self.assertEqual(hooks.render_helpers({}, None), {})
        self.assertEqual(STUB.sync.passthrough(None, True), [])
        self.assertEqual(plain().pins.managed_inputs({}), ())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(hooks.committed_argv(root), ["committed"])
            (root / "tools").mkdir()
            (root / "tools" / "pyproject.toml").write_text("")
            self.assertEqual(
                hooks.committed_argv(root),
                [
                    "uv",
                    "run",
                    "--frozen",
                    "--project",
                    str(root / "tools"),
                    "committed",
                ],
            )

    def test_header_kinds(self):
        self.assertEqual(api.header_kind(STUB, "nix"), HeaderKind("#"))
        self.assertEqual(api.header_kind(STUB, "jsonc"), HeaderKind("//"))
        self.assertIsNone(api.header_kind(STUB, "md"))
        md = product(header_kinds=MappingProxyType({"md": HeaderKind("<!--", "-->")}))
        self.assertEqual(api.header_kind(md, "md"), HeaderKind("<!--", "-->"))

    def test_first_opt_in_message_formats(self):
        text = api.FIRST_OPT_IN_MESSAGE.format(path="p", name="n", hint="h", diff="d")
        self.assertTrue(text.startswith("p is the checkout's own"))
        self.assertTrue(text.endswith("The replacement:\nd"))


class TestIdentityHashing(unittest.TestCase):
    def test_equal_products_are_distinct(self):
        a, b = product(), product()
        self.assertIsNot(a, b)
        self.assertNotEqual(a, b)
        self.assertEqual(a, a)
        self.assertEqual(len({a, b, a}), 2)

    def test_caches_never_share_an_entry(self):
        calls = []

        @functools.cache
        def cached(p: Product) -> str:
            calls.append(p)
            return p.identity.name

        a, b = product(), product()
        for _ in range(2):
            cached(a)
            cached(b)
        self.assertEqual(calls, [a, b])


class TestPostInitRules(unittest.TestCase):
    def refused(self, field: str, **changes):
        with self.assertRaisesRegex(ValueError, field) as ctx:
            product(**changes)
        return str(ctx.exception)

    def test_stubs_conform(self):
        self.assertEqual(STUB._problems(), [])
        self.assertEqual(KIT._problems(), [])

    def test_identity(self):
        ident = STUB.identity
        for field, value in (("name", "Stub"), ("cli", "1stub"), ("table", "stub_nix")):
            self.refused(f"identity.{field}", identity=replace(ident, **{field: value}))
        self.refused(
            "identity.env_prefix", identity=replace(ident, env_prefix="stub_nix")
        )
        self.refused(
            "identity.report_key", identity=replace(ident, report_key="stub-nix")
        )

    def test_modules(self):
        names = api.CORE_MODULES
        self.refused("lists 'ci' twice", modules=Modules((*names, "ci")))
        self.refused("not kebab-case", modules=Modules((*names, "Lint_X")))
        self.refused(
            "lacks the core modules ci", modules=Modules(names[:4] + names[5:])
        )
        self.refused(
            "modules.needs names 'nope'",
            modules=Modules(names, needs=MappingProxyType({"ci": ("nope",)})),
        )
        self.refused(
            "modules.needs names 'nope'",
            modules=Modules(names, needs=MappingProxyType({"nope": ("ci",)})),
        )
        self.refused(
            "modules.github_only", modules=Modules(names, github_only=("nope",))
        )
        self.refused("modules.required", modules=Modules(names, required="nope"))
        product(modules=Modules((*names, "extras"), needs={"extras": ("commits",)}))

    def test_core_names_are_not_shadowed(self):
        self.refused(
            "version_schemes key 'semver'", version_schemes={"semver": object()}
        )
        self.refused("handlers key 'whole'", handlers={"whole": object()})
        product(version_schemes={"stub-major": object()}, handlers={"readme": object()})

    def test_markers(self):
        marks = STUB.markers
        self.refused(
            "markers.block_begin", markers=replace(marks, block_begin=">>> x >>>")
        )
        self.refused(
            "markers.block_end", markers=replace(marks, block_end="{name} {name}")
        )
        self.refused(
            "markers.html_begin", markers=replace(marks, html_begin="<!-- x -->")
        )
        self.refused("markers.html_end", markers=replace(marks, html_end="<!-- x -->"))
        self.refused(
            "are equal",
            markers=replace(marks, block_end=marks.block_begin),
        )

    def test_ci(self):
        self.refused("ci.gates", ci=CiConfig(gates=("lint", "lint")))
        self.refused("ci.check_context", ci=CiConfig(check_context="ci / lint"))
        self.refused(
            "does not start with --", ci=CiConfig(arg_aliases={"old": "--new"})
        )
        for value in ("new", "--param  x", "--set a b", "--Bad", "-x"):
            with self.subTest(value=value):
                self.refused(
                    "ci.arg_aliases", ci=CiConfig(arg_aliases={"--old": value})
                )
        product(
            ci=CiConfig(
                arg_aliases={
                    "--a": "--b",
                    "--c": "--param docs-patterns",
                    "--d": "--set x",
                }
            )
        )

    def test_fixtures_and_write_fixture_go_together(self):
        bare = replace(STUB.hooks, write_fixture=None)
        self.refused(
            "fixtures and hooks.write_fixture",
            hooks=bare,
            fixtures={"plain": Fixture("")},
        )
        hooks = replace(STUB.hooks, write_fixture=lambda *a: None)
        self.refused("fixtures and hooks.write_fixture", hooks=hooks, fixtures={})
        product(hooks=hooks, fixtures={"plain": Fixture("")})
        product(hooks=bare, fixtures={})

    def test_data_root(self):
        self.refused("data_root", data_root=Path("/nonexistent/stub-data"))

    def test_json_style(self):
        for indent in ("", "x", " \t", "\t\t"):
            with self.subTest(indent=indent):
                self.refused("json_style.indent", json_style=JsonStyle(indent=indent))
        self.refused("json_style.width", json_style=JsonStyle(width=0))
        self.refused("json_style.width", json_style=JsonStyle(tab_width=-1))
        product(json_style=JsonStyle(indent="    "))

    def test_first_opt_in(self):
        one = FirstOptIn(".envrc", "put it in .envrc.local")
        self.refused("lists a path twice", first_opt_in=(one, one))
        for message in (
            "{path} {name} {hint}",
            "{path} {name} {hint} {diff} {other}",
            "{path} {name} {hint} {diff",
        ):
            with self.subTest(message=message):
                self.refused(
                    "message must format only",
                    first_opt_in=(replace(one, message=message),),
                )
        for message in (
            "{diff}{hint}{name}{path}",
            # A hint and a name written inline, as a product keeping its old text does.
            "{path} is the unit's own: keep yours elsewhere. The replacement:\n{diff}",
        ):
            with self.subTest(message=message):
                product(first_opt_in=(replace(one, message=message),))

    def test_all_problems_are_named(self):
        message = self.refused(
            "identity.name",
            identity=replace(STUB.identity, name="X"),
            ci=CiConfig(check_context="x"),
        )
        self.assertIn("ci.check_context", message)


if __name__ == "__main__":
    unittest.main()
