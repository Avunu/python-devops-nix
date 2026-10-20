"""python/sources.py's pieces that tests/sources.sh reaches only indirectly:
.gitmodules parsing, scope, the settings and the summary's wording.

Run: python3 -I tests/test_sources.py <python/>

Standard library only, and run on every interpreter the products' presets name
(lib.sources.check), since a product's scaffolder may import the module
in-process.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
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


@contextlib.contextmanager
def scratch():
    """A temporary directory to work in, the working directory restored after."""
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            yield os.path.realpath(tmp)
        finally:
            os.chdir(cwd)


def touch(*parts: str) -> str:
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("x\n")
    return path


class GitDirOf(unittest.TestCase):
    def test_a_git_directory_is_itself(self) -> None:
        with scratch():
            os.makedirs("app/.git")
            self.assertEqual(sources.git_dir_of("app"), os.path.join("app", ".git"))

    def test_a_gitdir_file_is_followed_relative_to_the_checkout(self) -> None:
        with scratch():
            os.makedirs(".git/modules/apps/a")
            os.makedirs("apps/a")
            with open("apps/a/.git", "w") as handle:
                handle.write("gitdir: ../../.git/modules/apps/a\n")
            self.assertEqual(
                os.path.normpath(sources.git_dir_of("apps/a")),
                os.path.normpath(".git/modules/apps/a"),
            )

    def test_an_absolute_gitdir_is_kept(self) -> None:
        with scratch() as tmp:
            os.makedirs("apps/a")
            with open("apps/a/.git", "w") as handle:
                handle.write(f"gitdir: {tmp}/elsewhere\n")
            self.assertEqual(sources.git_dir_of("apps/a"), f"{tmp}/elsewhere")

    def test_nothing_or_nonsense_is_empty(self) -> None:
        with scratch():
            os.makedirs("empty")
            self.assertEqual(sources.git_dir_of("empty"), "")
            self.assertEqual(sources.git_dir_of("missing"), "")
            touch("junk", ".git")
            self.assertEqual(sources.git_dir_of("junk"), "")


class CheckoutUnfinished(unittest.TestCase):
    def make(self, *files: str) -> str:
        for name in files:
            touch("app", ".git", name)
        os.makedirs("app/.git", exist_ok=True)
        return "app"

    def test_a_clone_with_no_index_was_never_checked_out(self) -> None:
        with scratch():
            self.assertTrue(sources.checkout_unfinished(self.make("HEAD")))

    def test_an_index_means_it_was(self) -> None:
        with scratch():
            self.assertFalse(sources.checkout_unfinished(self.make("HEAD", "index")))

    def test_no_HEAD_is_not_a_clone_to_finish(self) -> None:
        with scratch():
            self.assertFalse(sources.checkout_unfinished(self.make("config")))

    def test_not_a_checkout_at_all(self) -> None:
        with scratch():
            os.makedirs("app")
            self.assertFalse(sources.checkout_unfinished("app"))


class EntryLock(unittest.TestCase):
    def test_a_second_entry_is_refused_while_the_first_holds_it(self) -> None:
        with scratch():
            subprocess.run(["git", "init", "-q"], check=True)
            with sources.entry_lock() as first, sources.entry_lock() as second:
                self.assertTrue(first)
                self.assertFalse(second)
            with sources.entry_lock() as again:
                self.assertTrue(again)

    def test_a_refused_entry_touches_nothing_and_says_why(self) -> None:
        with scratch() as tmp:
            subprocess.run(["git", "init", "-q"], check=True)
            err = io.StringIO()
            with sources.entry_lock(), contextlib.redirect_stderr(err):
                report = sources.Sources(sources.Config(label="p")).enter(tmp)
            self.assertEqual(report, sources.Report())
            self.assertIn(
                "p: another shell entry is setting up the sources", err.getvalue()
            )

    def test_outside_a_repository_there_is_nothing_to_lock(self) -> None:
        with scratch(), sources.entry_lock() as acquired:
            self.assertTrue(acquired)


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

    def test_an_unfinished_checkout_names_the_command_that_finishes_it(self) -> None:
        text = summary(
            sources.Config(label="p"), sources.Report(unfinished=["apps/x", "apps/y"])
        )
        self.assertIn("p: apps/x was cloned but its checkout never finished", text)
        self.assertIn("p: apps/y was cloned but its checkout never finished", text)
        self.assertIn("overwrites what is there", text)
        self.assertIn("git submodule update --init --force -- apps/x", text)

    def test_an_unfinished_checkout_recurses_when_the_config_does(self) -> None:
        text = summary(
            sources.Config(label="p", recursive=True),
            sources.Report(unfinished=["x"]),
        )
        self.assertIn("git submodule update --init --force --recursive -- x", text)

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
