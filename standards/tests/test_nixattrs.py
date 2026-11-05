"""The Nix attribute-set scanner (DESIGN §5.6.2, §5.6.4): ``flake_input_specs`` gives the
first engine's regex results on its own flake shapes; strings, indented strings,
interpolations and comments; ``self.submodules`` is no input; neutral copies of the two
target projects' flakes; the inline-attrset refusal; ``claims`` moving, refusing and
replacing inputs; an unclaimed input that survives."""

import re
import unittest

from support import KIT
from support.checkouts import CheckoutCase

from standards_core.common.report import ConfigError
from standards_core.scaffold import engine, nixattrs
from standards_core.testing.fakes import flake_lock, git

# --- the first engine's reader, kept as the parity oracle ---------------------------------

_NAME = r"""(?:(?P<name>[A-Za-z_][A-Za-z0-9_'-]*)|"(?P<quoted>[^"\\$]*)")"""
_INPUT = re.compile(rf"^\s{{4}}{_NAME}\s*=\s*\{{")
_ATTR = re.compile(rf'^\s{{4}}{_NAME}\.(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
_INNER = re.compile(r'^\s{6}(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";')
_INLINE = re.compile(rf"^\s{{4}}{_NAME}\s*=\s*\{{(?P<body>.*)\}};\s*$")
_INLINE_ATTR = re.compile(
    r'(?:^|[;{\s])(?P<attr>url|follows)\s*=\s*"(?P<value>[^"]*)";'
)


def _name(m):
    return m["name"] if m["name"] is not None else m["quoted"]


def regex_specs(text: str) -> dict:
    """The regular-expression reader the scanner replaces (four spaces deep)."""
    m = re.search(r"\n  inputs = \{\n(?P<body>.*?)\n  \};\n", text, re.DOTALL)
    specs: dict = {}
    block = None
    for line in m["body"].splitlines() if m else []:
        if (one := _ATTR.match(line)) and not block:
            specs[_name(one)] = nixattrs.input_spec(one["attr"], one["value"])
        elif (inline := _INLINE.match(line)) and not block:
            specs.setdefault(_name(inline), None)
            for attr in _INLINE_ATTR.finditer(inline["body"]):
                specs[_name(inline)] = nixattrs.input_spec(attr["attr"], attr["value"])
        elif (opened := _INPUT.match(line)) and not block:
            block = _name(opened)
            specs.setdefault(block, None)
        elif block and re.match(r"^\s{4}\};", line):
            block = None
        elif block and (inner := _INNER.match(line)):
            specs[block] = nixattrs.input_spec(inner["attr"], inner["value"])
    specs.pop("self", None)
    return dict(sorted(specs.items()))


# A flake as the first engine's template renders it: 2/4/6-space levels.
RENDERED = """# Unit-specific Nix goes in nix/local.nix.
{
  description = "demo_unit: A demo unit";

  inputs = {
    stub-nix.url = "github:example/stub-nix/release-1";
    nixpkgs.follows = "stub-nix/nixpkgs";
    framework = {
      url = "github:example/framework/version-16";
      flake = false;
    };
    standards-profile = {
      url = "github:Example/Profile";
      flake = false;
    };
    "my.unit" = {
      url = "git+https://git.example.org/u.git?ref=main";
      flake = false;
    };
    # stub-nix:local-begin inputs
    extra.url = "path:/x";
    other = { url = "github:o/other/v2"; flake = false; };
    # stub-nix:local-end inputs
  };

  nixConfig = {
    extra-substituters = [ "https://cache.example.org" ];
  };

  outputs =
    { stub-nix, ... }@inputs:
    stub-nix.lib.mkFlake { inherit inputs; } (
      { lib, ... }:
      {
        imports = [ stub-nix.flakeModules.default ];
        systems = [ "x86_64-linux" ];
      }
    );
}
"""

# The template a checkout started from before opting in: comments, a commented input.
TEMPLATE = """{
  description = "@UNIT@ — a unit, developed with stub-nix";

  inputs = {
    stub-nix.url = "github:example/stub-nix";
    # The dev shell resolves `pkgs` from an input literally named `nixpkgs`.
    nixpkgs.follows = "stub-nix/nixpkgs";

    # The framework, pinned by this repo's flake.lock. `flake = false`: a source tree.
    framework = {
      url = "github:example/framework/@BRANCH@";
      flake = false;
    };

    # sibling = { url = "github:example/sibling/@BRANCH@"; flake = false; };
  };

  outputs = { stub-nix, ... }@inputs: stub-nix.lib.mkFlake { inherit inputs; } { };
}
"""

# A neutral copy of the first target project's flake: self.submodules, comments, an
# indented string with ''${…}.
PROJECT = """{
  description = "kit-project — a kit workspace (kit-nix)";

  inputs = {
    # units/* are git submodules; expose their contents to the flake source tree.
    self.submodules = true;
    kit-nix.url = "github:Example/kit-nix";
    # The dev shell resolves perSystem `pkgs` from an input literally named `nixpkgs`.
    nixpkgs.follows = "kit-nix/nixpkgs";
  };

  nixConfig = {
    extra-substituters = [ "https://cache.example.org" ];
    extra-trusted-public-keys = [
      "cache.example.org-1:aGVsbG8gd29ybGQ="
    ];
  };

  outputs =
    { self, kit-nix, ... }@inputs:
    kit-nix.lib.mkFlake { inherit inputs; } (
      { ... }:
      {
        imports = [ kit-nix.flakeModules.default ];
        # kit-nix.secrets = {
        #   dir = ./secrets;
        # };
        perSystem =
          { pkgs, ... }:
          {
            kit-nix = {
              enable = true;
              name = "kit-project";
              workspaceRoot = ./.;
              series = "18.0";
              extraScripts.serve-demo = {
                description = "Serve without --dev (for demos)";
                exec = ''
                  port="''${KIT_NIX_HTTP_PORT:-8069}"
                  if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
                    echo "Port $port is in use." >&2
                    exit 1
                  fi
                  exec python "$REPO_ROOT/core/serve" -c "$REPO_ROOT/kit.conf" "$@"
                '';
              };
            };
          };
      }
    );
}
"""

# The second target project's shape: nested sets and lists, a password-like string with
# sigils, four systems.
SHOP = """{
  description = "Shop management for an example business";

  inputs = {
    # src/core + src/external/* are git submodules.
    self.submodules = true;
    kit-nix.url = "github:Example/kit-nix";
    nixpkgs.follows = "kit-nix/nixpkgs";
  };

  outputs =
    { self, kit-nix, ... }@inputs:
    kit-nix.lib.mkFlake { inherit inputs; } (
      { ... }:
      {
        systems = [
          "aarch64-darwin"
          "x86_64-linux"
        ];
        perSystem =
          { pkgs, ... }:
          {
            kit-nix = {
              enable = true;
              pythonLibraries = {
                cups = [
                  pkgs.pkg-config
                ];
              };
              settings = {
                options = {
                  banner = "x&y^z*!{brace}$";
                  log_level = "info";
                };
              };
            };
          };
      }
    );
}
"""


class TestParity(unittest.TestCase):
    def test_the_first_engines_shapes(self):
        for text in (RENDERED, TEMPLATE, PROJECT, SHOP):
            with self.subTest(text=text[:40]):
                self.assertEqual(nixattrs.flake_input_specs(text), regex_specs(text))

    def test_rendered(self):
        self.assertEqual(
            nixattrs.flake_input_specs(RENDERED),
            {
                "extra": "path:/x",
                "framework": "github:example/framework/version-16",
                "my.unit": "git+https://git.example.org/u.git?ref=main",
                "nixpkgs": "follows:stub-nix/nixpkgs",
                "other": "github:o/other/v2",
                "standards-profile": "github:example/profile",
                "stub-nix": "github:example/stub-nix/release-1",
            },
        )

    def test_any_indentation(self):
        two = '{\ninputs = {\n  a.url = "github:o/a";\n  b = {\n    follows = "a/b";\n  };\n};\n}\n'
        tabs = two.replace("  ", "\t")
        want = {"a": "github:o/a", "b": "follows:a/b"}
        self.assertEqual(nixattrs.flake_input_specs(two), want)
        self.assertEqual(nixattrs.flake_input_specs(tabs), want)

    def test_the_input_rule(self):
        text = """{
  inputs = {
    a.url = "github:o/a";
    a.inputs.nixpkgs.follows = "nixpkgs";
    b.flake = false;
    c.inputs.x.follows = "a";
    d = { flake = false; };
    self.submodules = true;
  };
}
"""
        # A dotted child other than url/follows adds nothing; a set with neither is
        # an input without a spec; self is never one.
        self.assertEqual(
            nixattrs.flake_input_specs(text), {"a": "github:o/a", "d": None}
        )

    def test_engine_reexports(self):
        self.assertIs(engine.flake_input_specs, nixattrs.flake_input_specs)
        self.assertIs(engine.input_spec, nixattrs.input_spec)
        self.assertIs(engine.strip_nix_comments, nixattrs.strip_nix_comments)


class TestLexer(unittest.TestCase):
    def specs(self, attr: str) -> dict:
        return nixattrs.flake_input_specs(
            "{\n  x = " + attr + ';\n  inputs = {\n    a.url = "github:o/a";\n  };\n}\n'
        )

    def test_strings_with_braces_and_sigils(self):
        for value in (
            '"{ not a set }"',
            '"a } b"',
            r'"esc \" } \\"',
            '"$${not.interpolated} }"',
            '"${ { a = "}"; }.a }"',
            "''{ a }''",
            "''a ''${X:-1} } b''",
            "''a ''' } b''",
            "''a ''\\n } b''",
            "''a $${literal} } b''",
            "''a ${ { b = '' } ''; }.b } c''",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.specs(value), {"a": "github:o/a"})

    def test_comments(self):
        text = (
            '{\n  # inputs = { fake.url = "x"; };\n  /* } { */\n'
            '  inputs = {\n    a.url = "github:o/a"; # b.url = "y";\n'
            '    /* c.url = "z"; */\n  };\n}\n'
        )
        self.assertEqual(nixattrs.flake_input_specs(text), {"a": "github:o/a"})

    def test_lexing_errors_are_exit_2(self):
        for text in (
            '{\n  a = "x;\n}\n',
            "{\n  a = ''x;\n}\n",
            "{\n  /* x\n}\n",
            "{ a = 1;\n",
        ):
            with self.subTest(text=text), self.assertRaises(ConfigError) as caught:
                nixattrs.scan(text, "flake.nix")
            self.assertRegex(str(caught.exception), r"^flake\.nix:\d+: ")
        # The lock check reads nothing from a file it cannot lex.
        self.assertEqual(nixattrs.flake_input_specs('{ a = "x; }'), {})

    def test_a_lambda_is_not_the_outermost_set(self):
        text = '{ pkgs }:\n{\n  inputs = {\n    a.url = "github:o/a";\n  };\n}\n'
        self.assertEqual(nixattrs.flake_input_specs(text), {"a": "github:o/a"})

    def test_let_with_and_assert_keep_their_semicolons(self):
        text = """{
  x = let a = 1; b = 2; in a + b;
  y = with builtins; [ toString ];
  z = assert true; 1;
  inputs = {
    a.url = "github:o/a";
  };
}
"""
        found = nixattrs.scan(text)
        self.assertEqual(
            [c.path for c in found.root.children],
            [("x",), ("y",), ("z",), ("inputs",)],
        )

    def test_children_and_layout(self):
        found = nixattrs.scan(PROJECT)
        inputs = found.find(["inputs"])
        self.assertEqual(
            [c.path for c in inputs.children],
            [("self", "submodules"), ("kit-nix", "url"), ("nixpkgs", "follows")],
        )
        self.assertEqual(nixattrs.child_indent(PROJECT, inputs), "    ")
        self.assertEqual(nixattrs.indent_unit(PROJECT), "  ")
        self.assertIsNotNone(found.find(["nixConfig"]))
        self.assertIsNone(found.find(["outputs"]))  # a function, not a set
        self.assertFalse(nixattrs.inline(PROJECT, inputs))
        empty = nixattrs.scan("{\n  inputs = {\n  };\n}\n")
        self.assertEqual(
            nixattrs.child_indent(empty.text, empty.find(["inputs"])), "    "
        )
        tabs = nixattrs.scan("{\n\tinputs = {\n\t};\n}\n")
        self.assertEqual(
            nixattrs.child_indent(tabs.text, tabs.find(["inputs"])), "\t\t"
        )

    def test_bindings_of_a_body(self):
        body = 'kit-nix.url = "github:o/k";\nstandards-profile = {\n  url = "x";\n  flake = false;\n};'
        children = nixattrs.bindings(body)
        self.assertEqual([c.name for c in children], ["kit-nix", "standards-profile"])
        self.assertEqual(
            body[children[0].start : children[0].end], 'kit-nix.url = "github:o/k";'
        )
        self.assertTrue(body[children[1].start :].startswith("standards-profile = {"))
        self.assertEqual(children[1].attrset.children[0].string, "x")


class TestSelfIsNoInput(unittest.TestCase):
    def test_no_self_node_is_wanted(self):
        for text in (PROJECT, SHOP):
            specs = nixattrs.flake_input_specs(text)
            self.assertNotIn("self", specs)
            lock = flake_lock(text)
            self.assertNotIn("self", lock["nodes"]["root"]["inputs"])
            # The lock the specs make follows the flake: nothing stale, no relock.
            self.assertEqual(engine.stale_inputs(lock, specs), [])


class TestRedefinitions(unittest.TestCase):
    """``regions.check_redefinitions`` reads the inputs with the scanner."""

    def test_an_input_defined_outside_at_any_depth(self):
        from support import STUB

        from standards_core.scaffold import regions

        text = (
            '{\n  inputs = {\n    nixpkgs.url = "x";\n'
            '    # stub-nix:local-begin inputs\n    nixpkgs.follows = "y";\n'
            "    # stub-nix:local-end inputs\n  };\n}\n"
        )
        with self.assertRaisesRegex(ConfigError, "flake input nixpkgs"):
            regions.check_redefinitions(STUB, "flake.nix", text, "nix")
        ok = text.replace("nixpkgs.follows", "mine.follows")
        regions.check_redefinitions(STUB, "flake.nix", ok, "nix")
        # An attribute of that name elsewhere in the file is not an input.
        elsewhere = ok.replace("  };\n}\n", "  };\n  outputs = { nixpkgs = 1; };\n}\n")
        regions.check_redefinitions(STUB, "flake.nix", elsewhere, "nix")


# --- the kit's flake.nix inputs block: inline refusal and claims ---------------------------


class KitCase(CheckoutCase):
    product = KIT
    profile = "minimal"

    def setUp(self) -> None:
        super().setUp()
        # A workspace: its own pyproject.toml has the project's dependency array.
        self.write(
            "pyproject.toml",
            '[project]\nname = "kit-ws"\ndependencies = [\n]\n\n'
            f'[tool.kit-nix]\nschema = 1\nprofile = "{self.profile}"\n',
        )
        self.write("units.txt", "alpha\n")
        git(self.root, "rm", "-q", "--cached", "-r", "demo_unit")
        (self.root / "demo_unit" / "__init__.py").unlink()
        self.commit("workspace")


FLAKE = """{
  description = "ws";

  inputs = {
    self.submodules = true;
%s    nixpkgs.follows = "kit-nix/nixpkgs";
    core = {
      url = "github:example/core/18.0";
      flake = false;
    };
  };

  outputs = { kit-nix, ... }@inputs: kit-nix.lib.mkFlake { inherit inputs; } { };
}
"""
BLOCK_URL = "github:example/kit-nix/release-1"


class TestClaims(KitCase):
    def flake(self, own: str) -> None:
        self.write("flake.nix", FLAKE % own)
        self.commit("own flake")

    def test_an_equal_definition_moves_into_the_block(self):
        self.flake(f'    kit-nix.url = "{BLOCK_URL}";\n')
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("kit-nix moves into the managed inputs block", out)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        text = self.read("flake.nix")
        self.assertEqual(text.count("kit-nix.url"), 1)
        self.assertIn(
            f'    # >>> kit-nix: inputs (managed) >>>\n    kit-nix.url = "{BLOCK_URL}";\n',
            text,
        )
        # The unclaimed core input stays as it was written.
        self.assertIn('    core = {\n      url = "github:example/core/18.0";\n', text)

    def test_case_differences_in_a_forge_url_are_equal(self):
        self.flake('    kit-nix.url = "github:Example/Kit-Nix/release-1";\n')
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)

    def test_a_differing_definition_on_first_opt_in(self):
        self.flake('    kit-nix.url = "github:example/kit-nix/feature";\n')
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("flake.nix defines kit-nix otherwise than the managed", out)
        self.assertIn('-    kit-nix.url = "github:example/kit-nix/feature";', out)
        self.assertIn(f'+    kit-nix.url = "{BLOCK_URL}";', out)
        before = self.read("flake.nix")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 2, err)
        self.assertEqual(self.read("flake.nix"), before)
        code, _, err = self.fn("sync", "--write", "--force")
        self.assertEqual(code, 0, err)
        self.assertNotIn("feature", self.read("flake.nix"))
        self.assertIn("core = {", self.read("flake.nix"))

    def test_a_differing_definition_after_opt_in(self):
        self.flake("")
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.commit("synced")
        text = self.read("flake.nix").replace(
            "    nixpkgs.follows",
            '    kit-nix.url = "github:o/fork";\n    nixpkgs.follows',
        )
        self.write("flake.nix", text)
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("flake.nix defines kit-nix outside the managed inputs block", out)
        self.assertNotIn("The replacement", out)
        code, _, err = self.fn("sync", "--write", "--force")
        self.assertEqual(code, 0, err)
        self.assertNotIn("github:o/fork", self.read("flake.nix"))

    def test_an_unclaimed_input_survives_write_check_and_force(self):
        self.flake("")
        for argv in (("--write",), ("--write", "--force")):
            code, _, err = self.fn("sync", *argv)
            self.assertEqual(code, 0, err)
            self.assertIn(
                '    core = {\n      url = "github:example/core/18.0";\n      flake = false;\n    };\n',
                self.read("flake.nix"),
            )
        self.fake_locks()
        self.commit("synced")
        self.assertEqual(self.check()[0], 0)

    def test_a_definition_in_a_foreign_region_is_never_claimed(self):
        own = (
            "    # >>> generator: pinned (managed by gen) >>>\n"
            '    kit-nix.url = "github:o/fork";\n'
            "    # <<< generator: pinned <<<\n"
        )
        self.flake(own)
        code, _, err = self.fn("sync", "--write")
        self.assertEqual(code, 0, err)
        self.assertIn(own, self.read("flake.nix"))

    def test_an_inline_inputs_set_is_refused(self):
        self.write(
            "flake.nix",
            '{\n  inputs = { nixpkgs.url = "github:o/n"; };\n  outputs = _: { };\n}\n',
        )
        self.commit("inline")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn(
            "write `inputs = {` with its attributes on lines of their own", out
        )

    def test_a_missing_anchor_is_exit_2(self):
        self.write("flake.nix", "{\n  outputs = _: { };\n}\n")
        self.commit("no inputs")
        code, out = self.check()
        self.assertEqual(code, 2, out)
        self.assertIn("anchor nix-attrset inputs is missing", out)


if __name__ == "__main__":
    unittest.main()
