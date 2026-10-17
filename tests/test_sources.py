"""python/sources.py's pieces that tests/sources.sh reaches only indirectly:
.gitmodules parsing, scope, the settings and the summary's wording.

Run: python3 -I tests/test_sources.py <python/>

Standard library only, and run on every interpreter the products' presets name
(lib.sources.check), since a product's scaffolder may import the module
in-process.
"""

import contextlib
import io
import sys
import unittest

if len(sys.argv) < 2:
    sys.exit("usage: test_sources.py <python dir>")
sys.path.insert(0, sys.argv.pop(1))

import sources


def gitmodules(*records: tuple[str, str]) -> str:
    """`git config --list -z` output."""
    return "".join(f"{key}\n{value}\0" for key, value in records)


class ParseGitmodules(unittest.TestCase):
    def test_names_with_dots_and_file_order(self) -> None:
        text = gitmodules(
            ("submodule.modules/web.ext.path", "modules/web.ext"),
            ("submodule.modules/web.ext.url", "https://example.com/web.ext.git"),
            ("submodule.modules/web.ext.branch", "18.0"),
            ("submodule.apps/a.path", "apps/a"),
            ("submodule.apps/a.url", "https://example.com/a.git"),
        )
        self.assertEqual(
            sources.parse_gitmodules(text),
            [
                sources.Submodule(
                    name="modules/web.ext",
                    path="modules/web.ext",
                    branch="18.0",
                    url="https://example.com/web.ext.git",
                ),
                sources.Submodule(
                    name="apps/a", path="apps/a", url="https://example.com/a.git"
                ),
            ],
        )

    def test_dot_branch_is_the_default(self) -> None:
        text = gitmodules(("submodule.x.path", "x"), ("submodule.x.branch", "."))
        self.assertEqual(sources.parse_gitmodules(text)[0].branch, "")

    def test_a_section_without_a_path_is_left_out(self) -> None:
        text = gitmodules(("submodule.x.url", "u"), ("core.bare", "false"))
        self.assertEqual(sources.parse_gitmodules(text), [])

    def test_name_and_path_may_differ(self) -> None:
        text = gitmodules(("submodule.frappe.path", "apps/frappe"))
        self.assertEqual(sources.parse_gitmodules(text)[0].name, "frappe")


class Scope(unittest.TestCase):
    def test_no_scope_is_everything(self) -> None:
        self.assertTrue(sources.in_scope("odoo", ()))
        self.assertTrue(sources.in_scope("modules/a/b", ()))

    def test_direct_children_only(self) -> None:
        self.assertTrue(sources.in_scope("apps/erpnext", ("apps",)))
        self.assertFalse(sources.in_scope("apps/erpnext/frontend", ("apps",)))
        self.assertFalse(sources.in_scope("apps", ("apps",)))
        self.assertFalse(sources.in_scope("vendor/x", ("apps",)))
        self.assertTrue(sources.in_scope("vendor/x", ("apps", "vendor")))


class Settings(unittest.TestCase):
    def test_from_mapping(self) -> None:
        config = sources.Config.from_mapping(
            {
                "label": "p-nix",
                "scope": ["apps"],
                "treeless": ["odoo"],
                "recursive": True,
            }
        )
        self.assertEqual(config.scope, ("apps",))
        self.assertEqual(config.filter_for("odoo"), "tree:0")
        self.assertEqual(config.filter_for("modules/x"), "blob:none")

    def test_unknown_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown sources setting.*colour"):
            sources.Config.from_mapping({"label": "p", "colour": "red"})

    def test_label_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "label"):
            sources.Config.from_mapping({"noun": "bench"})


def summary(config: sources.Config, report: sources.Report) -> str:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        sources.Sources(config).summarise(report)
    return err.getvalue()


class Summary(unittest.TestCase):
    def test_columns_line_up_for_any_noun(self) -> None:
        for noun in ("bench", "project"):
            text = summary(
                sources.Config(label="p", noun=noun, update_command="p update"),
                sources.Report(unchecked=["a/x"]),
            )
            lines = text.splitlines()
            self.assertEqual(
                lines[2].index("git submodule"), lines[3].index("p update"), text
            )

    def test_no_update_command(self) -> None:
        text = summary(sources.Config(label="p"), sources.Report(unchecked=["a/x"]))
        self.assertNotIn("branches' tips", text)

    def test_recursive_hints(self) -> None:
        text = summary(
            sources.Config(label="p", recursive=True),
            sources.Report(unchecked=["x"], failed=["y"]),
        )
        self.assertIn("git submodule update --init --recursive -- x", text)
        self.assertIn("git submodule update --init --recursive -- y", text)

    def test_remove_command_fields(self) -> None:
        stale = sources.Submodule(name="erp", path="apps/erpnext")
        text = summary(
            sources.Config(
                label="p",
                noun="bench",
                remove_command="bench remove-app {base} # {name} {path}",
            ),
            sources.Report(stale=[stale]),
        )
        self.assertIn("Finish it: bench remove-app erpnext # erp apps/erpnext", text)
        self.assertIn("but the bench records no commit", text)

    def test_default_remove_command(self) -> None:
        stale = sources.Submodule(name="modules/x", path="modules/x")
        text = summary(sources.Config(label="p"), sources.Report(stale=[stale]))
        self.assertIn(
            "git config -f .gitmodules --remove-section submodule.modules/x", text
        )

    def test_nothing_to_say(self) -> None:
        self.assertEqual(
            summary(sources.Config(label="p"), sources.Report(checked=["x"])), ""
        )


class CommandLine(unittest.TestCase):
    def test_bad_settings_fail_before_anything_runs(self) -> None:
        with self.assertRaises(ValueError):
            sources.main(["enter"], config={"label": "p", "scope": "apps", "oops": 1})

    def test_usage(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            sources.main([], config={"label": "p"})
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
