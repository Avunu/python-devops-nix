"""python/banner.py: the spec's parser, the placeholders, the lines under the
panel in both products' shapes, and the rendering.

Run: python -I tests/test_banner.py <python/>   (an interpreter with rich)

Run on every interpreter the products' presets name, each with
lib.python.rich (lib.banner.check).
"""

import io
import re
import sys
import unittest

if len(sys.argv) < 2:
    sys.exit("usage: test_banner.py <python dir>")
sys.path.insert(0, sys.argv.pop(1))

import banner
from rich.console import Console

FRAPPE = {
    "title": "acme — Frappe Bench dev environment",
    "commands": [
        {"command": "devenv up", "description": "start all services"},
        {"command": "bench-update --pull", "description": "pull app submodules only"},
    ],
    "urls": [{"label": "URL", "url": "http://127.0.0.1:@port@"}],
    "facts": [
        {"label": "Python", "value": "/nix/store/x/bin/python"},
        {"label": "Bench root", "value": "/w/acme"},
        {
            "label": "App",
            "value": [
                "apps/acme → this repository (edits are live)",
                "the bench is generated; deleting it costs a re-copy, not the database",
            ],
        },
        {"label": "Sockets", "value": "@runtime@/{mysql,redis,socketio,web}.sock"},
    ],
    "guard": {
        "enable": True,
        "egress": {"enable": True, "allow": ["api.example.com", "10.0.0.0/8"]},
        "mail": {
            "enable": True,
            "url": "http://127.0.0.1:20223",
            "incoming": "blocked",
        },
    },
    "notes": [{"text": "⚠  frappe-runtime is not in this shell", "style": "yellow"}],
}

ODOO = {
    "title": "acme — Odoo 18.0 (OCB + OCA) dev environment",
    "commands": [
        {"command": "odoo test <mod> [db]", "description": "run a module's tests"}
    ],
    "urls": [{"label": "Odoo server", "url": "http://127.0.0.1:10590"}],
    "facts": [{"label": "addons_path entries", "value": "12"}],
    "guard": {"enable": False},
}


class Parse(unittest.TestCase):
    def test_both_shapes(self) -> None:
        self.assertEqual(
            banner.parse(FRAPPE).guard.allow, ("api.example.com", "10.0.0.0/8")
        )
        self.assertFalse(banner.parse(ODOO).guard.enable)

    def test_title_only(self) -> None:
        spec = banner.parse({"title": "t"})
        self.assertEqual((spec.commands, spec.guard), ((), None))

    def test_unknown_keys(self) -> None:
        for data, where in [
            ({"title": "t", "colour": 1}, "spec"),
            (
                {
                    "title": "t",
                    "commands": [{"command": "a", "description": "b", "x": 1}],
                },
                "commands[0]",
            ),
            (
                {"title": "t", "guard": {"enable": True, "mail": {"port": 1}}},
                "guard.mail",
            ),
        ]:
            with self.assertRaisesRegex(
                banner.SpecError, rf"^{re.escape(where)}: unknown key"
            ):
                banner.parse(data)

    def test_missing_and_mistyped(self) -> None:
        with self.assertRaisesRegex(banner.SpecError, "missing key.*title"):
            banner.parse({})
        with self.assertRaisesRegex(
            banner.SpecError, "guard.enable: expected true or false"
        ):
            banner.parse({"title": "t", "guard": {"enable": "yes"}})
        with self.assertRaisesRegex(banner.SpecError, r"facts\[0\].value: empty"):
            banner.parse({"title": "t", "facts": [{"label": "x", "value": []}]})


class Substitute(unittest.TestCase):
    def test_fills_given_names_only(self) -> None:
        got = banner.substitute(
            {"a": ["@port@ and @other@", 1, True], "b": "user@example.com"},
            {"port": "8223"},
        )
        self.assertEqual(
            got, {"a": ["8223 and @other@", 1, True], "b": "user@example.com"}
        )


class Lines(unittest.TestCase):
    def text(self, data: dict, **variables: str) -> list[str]:
        return [
            text
            for text, _ in banner.lines(
                banner.parse(banner.substitute(data, variables))
            )
        ]

    def test_frappe_shape(self) -> None:
        self.assertEqual(
            self.text(FRAPPE, port="8223", runtime="/run/x"),
            [
                "  URL: http://127.0.0.1:8223",
                "  Python: /nix/store/x/bin/python",
                "  Bench root: /w/acme",
                "  App: apps/acme → this repository (edits are live)",
                "       the bench is generated; deleting it costs a re-copy, not the database",
                "  Sockets: /run/x/{mysql,redis,socketio,web}.sock",
                "  Egress: outbound connections blocked (allowed: api.example.com, 10.0.0.0/8)",
                "  Mail: ALL outgoing email → Mailpit (http://127.0.0.1:20223)",
                "        incoming (IMAP/POP3) is blocked",
                "",
                "  ⚠  frappe-runtime is not in this shell",
            ],
        )

    def test_odoo_shape(self) -> None:
        self.assertEqual(
            self.text(ODOO),
            [
                "  Odoo server: http://127.0.0.1:10590",
                "  addons_path entries: 12",
                "  devguard OFF — nothing stops a restored database from reaching production",
            ],
        )

    def test_guards_off_one_by_one(self) -> None:
        data = {"title": "t", "guard": {"enable": True, "egress": {"enable": False}}}
        self.assertEqual(self.text(data), [])
        data["guard"]["mail"] = {"enable": True}
        self.assertEqual(self.text(data), ["  Mail: ALL outgoing email → Mailpit"])

    def test_off_is_bold_red(self) -> None:
        self.assertEqual(banner.lines(banner.parse(ODOO))[-1][1], "bold red")


class Render(unittest.TestCase):
    def render(self, data: dict) -> str:
        console = Console(file=io.StringIO(), width=100, color_system=None, record=True)
        banner.render(banner.parse(data), console)
        return console.export_text()

    def test_markup_is_literal(self) -> None:
        text = self.render(ODOO)
        self.assertIn("odoo test <mod> [db]", text)
        self.assertIn("acme — Odoo 18.0 (OCB + OCA) dev environment", text)

    def test_brackets_in_facts(self) -> None:
        text = self.render(
            {"title": "[b]t[/b]", "facts": [{"label": "x", "value": "[red]y"}]}
        )
        self.assertIn("[b]t[/b]", text)
        self.assertIn("x: [red]y", text)

    def test_no_commands_is_a_title_line(self) -> None:
        self.assertIn("plain", self.render({"title": "plain"}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
