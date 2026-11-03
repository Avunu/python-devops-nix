"""``json-merge`` (DESIGN §5.3): ``JsonGroups`` set, retract when equal and was on, the
turn-off warning, ``reconcile`` keeping a hand-raised pin, unmanaged keys untouched, the
output in the product's ``json_style``."""

import dataclasses
import json
import unittest
from pathlib import Path
from types import MappingProxyType

from support import KIT, STUB, product_copy
from support.checkouts import CheckoutCase

from standards_core.common.report import INVALID, EnvError
from standards_core.scaffold import context, floors, jsonfmt, jsonmerge
from standards_core.scaffold.jsonmerge import JsonGroups, JsonGroupsHandler

PINS = {"lint": {"oxlint": "1.2.0"}, "format": {"oxfmt": "0.5.0"}}


def groups_of(ctx):
    return {
        group: [(("devDependencies", pkg), floors.caret(v)) for pkg, v in pins.items()]
        for group, pins in PINS.items()
    } | {"scripts": [(("scripts", "lint"), "oxlint .")]}


def on(ctx, group):
    return bool(ctx.modules.get("tests" if group == "format" else "python-lint"))


def reconcile(ctx, path, have, rendered):
    """A floor: a caret range whose minimum is at or above the rendered one stays."""
    if path[0] != "devDependencies" or not isinstance(have, str):
        return rendered
    return have if floors.range_ok(have, rendered.lstrip("^")) else rendered


def problems(doc, ctx):
    deps = doc.get("devDependencies") or {}
    return [(INVALID, "eslint is forbidden")] if "eslint" in deps else []


GROUPS = JsonGroups(
    groups=groups_of,
    on=on,
    key_order=MappingProxyType({(): ("name", "scripts", "devDependencies", "license")}),
    reconcile=reconcile,
    problems=problems,
)


def ctx(lint=True, tests=True, history=None, previous=None):
    hist = context.History(
        context.ns({"modules": m, "cfg": {}}) for m in (history or [])
    )
    return context.ns(
        {
            "modules": {"python-lint": lint, "tests": tests},
            "cfg": {},
            "history": hist,
            "previous": context.ns({"modules": previous, "cfg": {}})
            if previous is not None
            else None,
        }
    )


class TestMerge(unittest.TestCase):
    def test_sets_the_keys_of_the_groups_that_are_on(self):
        merged = jsonmerge.merge(GROUPS, {"name": "x"}, ctx(), "package.json")
        self.assertEqual(
            merged.doc,
            {
                "name": "x",
                "scripts": {"lint": "oxlint ."},
                "devDependencies": {"oxlint": "^1.2.0", "oxfmt": "^0.5.0"},
            },
        )
        self.assertEqual(merged.warnings, [])

    def test_unmanaged_keys_keep_their_order(self):
        current = {
            "devDependencies": {"zod": "^3", "oxlint": "^1.2.0"},
            "private": True,
            "name": "x",
            "scripts": {"build": "vite build"},
        }
        doc = jsonmerge.merge(GROUPS, current, ctx(), "package.json").doc
        self.assertEqual(list(doc), ["devDependencies", "private", "name", "scripts"])
        self.assertEqual(list(doc["devDependencies"]), ["zod", "oxlint", "oxfmt"])
        self.assertEqual(doc["scripts"], {"build": "vite build", "lint": "oxlint ."})
        self.assertEqual(current["devDependencies"], {"zod": "^3", "oxlint": "^1.2.0"})

    def test_a_new_parent_goes_by_key_order(self):
        doc = jsonmerge.merge(
            GROUPS, {"version": "1", "name": "x", "license": "MIT"}, ctx(), "p"
        ).doc
        self.assertEqual(
            list(doc), ["version", "name", "scripts", "devDependencies", "license"]
        )

    def test_reconcile_keeps_a_hand_raised_pin(self):
        current = {"devDependencies": {"oxlint": "^1.9.0", "oxfmt": "^0.1.0"}}
        doc = jsonmerge.merge(GROUPS, current, ctx(), "package.json").doc
        self.assertEqual(
            doc["devDependencies"], {"oxlint": "^1.9.0", "oxfmt": "^0.5.0"}
        )

    def test_off_and_was_on_retracts_what_still_equals_the_render(self):
        current = {
            "name": "x",
            "scripts": {"lint": "oxlint ."},
            "devDependencies": {"oxlint": "^1.2.0", "oxfmt": "^0.5.0"},
        }
        was = [{"python-lint": True, "tests": True}]
        doc = jsonmerge.merge(GROUPS, current, ctx(lint=False, history=was), "p").doc
        self.assertEqual(doc, {"name": "x", "devDependencies": {"oxfmt": "^0.5.0"}})

    def test_off_and_never_on_retracts_nothing(self):
        current = {"scripts": {"lint": "oxlint ."}}
        doc = jsonmerge.merge(
            GROUPS, current, ctx(lint=False, tests=False, history=[{}]), "p"
        ).doc
        self.assertEqual(doc, current)

    def test_an_edited_key_stays_with_a_warning_on_the_run_that_turns_it_off(self):
        current = {"scripts": {"lint": "oxlint --fix ."}}
        was = [{"python-lint": True}]
        merged = jsonmerge.merge(
            GROUPS,
            current,
            ctx(lint=False, tests=False, history=was, previous={"python-lint": True}),
            "package.json",
        )
        self.assertEqual(merged.doc, current)
        self.assertEqual(
            merged.warnings,
            [
                (
                    "package.json scripts.lint is the app's now that scripts is off (it"
                    " differs from what sync wrote)"
                )
            ],
        )
        later = jsonmerge.merge(
            GROUPS,
            current,
            ctx(lint=False, tests=False, history=was, previous={"python-lint": False}),
            "package.json",
        )
        self.assertEqual(later.warnings, [])

    def test_a_shallow_history_that_cannot_answer_fails(self):
        hist = context.History()
        hist.truncated = True
        c = ctx(lint=False)
        c["history"] = hist
        with self.assertRaises(EnvError):
            jsonmerge.merge(GROUPS, {"scripts": {"lint": "oxlint ."}}, c, "p")
        # Nothing equal to the render: the history is never asked.
        jsonmerge.merge(GROUPS, {"scripts": {"lint": "x"}}, c, "p")

    def test_baseline_is_never_retracted(self):
        groups = dataclasses.replace(
            GROUPS,
            baseline=lambda ctx, path, have, rendered: path == ("scripts", "lint"),
        )
        current = {"scripts": {"lint": "oxlint ."}}
        doc = jsonmerge.merge(
            groups,
            current,
            ctx(lint=False, tests=False, history=[{"python-lint": True}]),
            "p",
        ).doc
        self.assertEqual(doc, current)


class TestHandlerInTheEngine(CheckoutCase):
    """A product's ``package.json`` entry served by a ``JsonGroupsHandler``."""

    def setUp(self):
        self.tmp_product = self.enterContext(_TempDir())
        product = product_copy(
            STUB,
            self.tmp_product,
            handlers=MappingProxyType(
                {
                    **STUB.handlers,
                    "stub-package": JsonGroupsHandler("stub-package", GROUPS),
                }
            ),
        )
        (product.data_root / "manifest.d" / "zz.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "path": "package.json",
                            "strategy": "json-merge",
                            "handler": "stub-package",
                            "module": ["python-lint", "tests"],
                        }
                    ]
                }
            )
        )
        self.product = product
        super().setUp()

    def test_created_in_the_json_style_then_merged(self):
        self.synced()
        text = self.read("package.json")
        self.assertEqual(
            text,
            jsonfmt.dumps(
                {
                    "scripts": {"lint": "oxlint ."},
                    "devDependencies": {"oxlint": "^1.2.0", "oxfmt": "^0.5.0"},
                },
                STUB.json_style,
            ),
        )
        self.assertIn('\t"scripts"', text)
        # The checkout's formatter lays it out otherwise: only values count.
        doc = json.loads(text)
        doc["dependencies"] = {"left-pad": "1.3.0"}
        doc["devDependencies"]["oxlint"] = "^1.5.0"
        self.write("package.json", json.dumps(doc, indent=2) + "\n")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fn("sync", "--write")[0], 0)
        self.assertEqual(json.loads(self.read("package.json")), doc)

    def test_drift_problems_and_retraction(self):
        self.synced()
        doc = json.loads(self.read("package.json"))
        doc["devDependencies"]["oxlint"] = "^1.0.0"
        self.write("package.json", json.dumps(doc))
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("package.json (json-merge): managed keys differ", out)
        self.assertEqual(self.fn("sync", "--write")[0], 0)
        self.assertEqual(
            json.loads(self.read("package.json"))["devDependencies"]["oxlint"],
            "^1.2.0",
        )
        doc = json.loads(self.read("package.json"))
        doc["devDependencies"]["eslint"] = "^9"
        self.write("package.json", json.dumps(doc))
        self.assertEqual(self.check()[0], 2)
        del doc["devDependencies"]["eslint"]
        self.write("package.json", json.dumps(doc))
        self.commit()
        self.write(
            "pyproject.toml",
            self.read("pyproject.toml")
            + "\n[tool.stub-nix.python-lint]\nenable = false\n",
        )
        self.commit()
        self.assertEqual(self.fn("sync", "--write")[0], 0)
        self.assertEqual(
            json.loads(self.read("package.json")),
            {"devDependencies": {"oxfmt": "^0.5.0"}},
        )

    def test_the_kit_style_is_prettiers(self):
        text = jsonfmt.dumps({"a": {"b": [1, 2]}}, KIT.json_style)
        self.assertEqual(text, '{\n  "a": {\n    "b": [1, 2]\n  }\n}\n')


class _TempDir:
    def __enter__(self) -> Path:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
