"""python/devtui's app, driven headless (textual's App.run_test): the tabs a
spec gives, the stack against a fake process-compose, a command's form and
the line it makes, the store's pick → confirm → run → follow-up, the spec's
run-time values, and the TUI's own actions.

Run: python -I tests/test_tui_app.py <python/>   (lib.python.textual, with click)

Run on every interpreter the products' presets name (lib.tui.check).
"""

import asyncio
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

if len(sys.argv) < 2:
    sys.exit("usage: test_tui_app.py <python dir>")
sys.path.insert(0, sys.argv.pop(1))

import click
from devtui import commands
from devtui.app import DevTui
from devtui.catalog import Bundle, Catalog, Item
from devtui.commandsview import CommandsView
from devtui.plugin import Closure, Field, Form, Step
from devtui.run import ConfirmScreen, RunScreen
from devtui.spec import Action
from devtui.stackview import StackView
from devtui.store import StoreView
from textual.widgets import DataTable, Input, RichLog, Static

PY = sys.executable


def py(code):
    return (PY, "-c", code)


# -- a fake process-compose ------------------------------------------------------------

PROCESSES = [
    {
        "name": "web",
        "status": "Running",
        "is_ready": "Ready",
        "has_ready_probe": True,
        "is_running": True,
        "pid": 4,
    },
    {"name": "db", "status": "Running", "is_running": True, "pid": 3},
]


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        self.calls = []
        super().__init__(path, Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass

    def _handle(self):
        self.server.calls.append((self.command, self.path))
        if self.path == "/processes":
            body = {"data": PROCESSES}
        elif self.path.startswith("/process/logs/"):
            body = {"logs": [f"{self.path.split('/')[3]} says hello"]}
        else:
            body = {"name": "ok"}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = _handle


# -- a stub product ------------------------------------------------------------------------

ITEMS = (
    Item(
        "repair_order",
        title="Repair Orders",
        group="repair",
        summary="Fix things",
        tags=("application",),
    ),
    Item("repair_type", title="Repair Type", group="repair", summary="Kinds of repair"),
    Item(
        "web_responsive",
        title="Web Responsive",
        group="web",
        summary="Mobile-friendly backend",
    ),
)


class Product:
    def __init__(self):
        self.added = set()

    def catalog(self):
        return Catalog(
            noun="module",
            group_label="Repository",
            items=ITEMS,
            bundles=(
                Bundle("base", "Base", members=("web_responsive", "repair_type")),
            ),
        )

    def installed(self):
        return {"repair_type"} | self.added

    def closure(self, ids, bundles):
        return Closure(brings=("base_dep",), notes=("1 new repository: repair",))

    def add(self, ids, bundles):
        self.added |= set(ids)
        return [
            Step(py(f"print('adding', {sorted(ids)!r}, {sorted(bundles)!r})"), "add")
        ]

    def remove(self, ids):
        return [Step(py(f"print('removing', {sorted(ids)!r})"), "remove")]

    def detail(self, item):
        return f"More about **{item.id}**."

    def after_add(self):
        return [Action(label="Provision", argv=py("print('provisioned')"))]

    def forms(self):
        return [
            Form(
                "Add from a Git URL",
                (Field("url", "URL", required=True),),
                (*py("print('git {url}')"),),
            )
        ]


@click.group()
def cli():
    """The command line."""


@cli.command("migrate")
@click.option("--jobs", type=int, required=True, help="how many")
@click.option("--all", "all_", is_flag=True)
def migrate(jobs, all_):
    """Migrate."""


class AppTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # unittest runs the loop in debug mode, which logs every callback over
        # 0.1s: a textual frame often is one.
        asyncio.get_running_loop().slow_callback_duration = 60

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.sock = str(self.dir / "pc.sock")
        self.server = Server(self.sock)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        catalog_file = self.dir / "commands.json"
        catalog_file.write_text(
            json.dumps(commands.to_json(commands.dump(cli, "stub", source="stub-nix")))
        )
        vars_command = self.dir / "vars"
        vars_command.write_text("#!/bin/sh\necho port=8223\n")
        vars_command.chmod(0o755)
        self.data = {
            "product": "stub-nix",
            "cli": "stub",
            "root": str(self.dir),
            "banner": {
                "title": "stub on @port@",
                "urls": [{"label": "URL", "url": "http://127.0.0.1:@port@"}],
            },
            "site": {"url": "http://127.0.0.1:@port@", "process": "web"},
            "stack": {
                "socket": self.sock,
                "actions": [{"label": "Say hi", "argv": list(py("print('hi')"))}],
            },
            "actions": [
                {"label": "Open", "argv": ["devtui:open"]},
                {
                    "label": "Echo",
                    "argv": list(py("print('echo')")),
                    "after": "rebuild",
                },
            ],
            "commands": [{"file": str(catalog_file)}],
        }
        self.vars_command = str(vars_command)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir)

    def app(self, **kwargs):
        return DevTui(
            self.data,
            vars_command=self.vars_command,
            product=kwargs.pop("product", Product()),
            **kwargs,
        )

    async def until(self, pilot, check, timeout=10.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("timed out")
            await pilot.pause(0.05)

    async def test_home_and_vars(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            self.assertEqual(app.title, "stub on 8223")
            self.assertEqual(app.spec.site.url, "http://127.0.0.1:8223")
            tabs = [pane.id for pane in app.query("TabPane")]
            self.assertEqual(tabs, ["home", "stack", "commands", "store"])
            await self.until(pilot, lambda: app.processes is not None)
            stack_line = str(app.query_one("#home-stack", Static).content)
            self.assertIn("2/2 processes up", stack_line)
            self.assertIn("web is up", stack_line)
            with mock.patch.object(app, "open_url") as open_url:
                await pilot.press("o")
                open_url.assert_called_once_with("http://127.0.0.1:8223")
                app.run_spec_action(app.spec.actions[0])
                self.assertEqual(open_url.call_count, 2)

    async def test_no_store_without_plugin(self):
        data = {k: v for k, v in self.data.items() if k not in ("stack", "commands")}
        app = DevTui(data, {"port": "1"}, product=None)
        async with app.run_test(size=(120, 40)):
            self.assertEqual([pane.id for pane in app.query("TabPane")], ["home"])

    async def test_stack(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("2")
            view = app.query_one(StackView)
            table = view.query_one("#stack-table", DataTable)
            await self.until(pilot, lambda: table.row_count == 2)
            table.focus()
            self.assertEqual(view.selected, "db")
            await pilot.press("r")
            await self.until(
                pilot, lambda: ("POST", "/process/restart/db") in self.server.calls
            )
            log = view.query_one("#stack-log", RichLog)
            await self.until(
                pilot, lambda: any("db says hello" in line.text for line in log.lines)
            )

    async def test_rebuild_restarts_and_reevaluates(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            await self.until(pilot, lambda: app.processes is not None)
            with mock.patch.object(app, "_down_then") as down:
                app.action_stack_rebuild()
                down.assert_called_once()
            self.server.calls.clear()
            app.processes = None
            app.data["stack"]["up"] = list(py("print('up as loaded')"))
            app.data["stack"]["rebuild"] = list(
                py("import os; print('fresh', 'DIRENV_DIR' in os.environ)")
            )
            app.refresh_spec()
            with mock.patch.dict(os.environ, {"DIRENV_DIR": "-/w"}):
                app.action_stack_rebuild()
                await self.until(
                    pilot,
                    lambda: (
                        isinstance(app.screen, RunScreen) and app.screen.ok is not None
                    ),
                )
            log = app.screen.query_one("#run-log", RichLog)
            self.assertTrue(app.screen.ok)
            self.assertTrue(any("fresh False" in line.text for line in log.lines))

    async def test_fresh_shell(self):
        self.data["freshShell"] = list(py("import sys; print('fresh', sys.argv[1:])"))
        self.data["stack"]["up"] = list(py("print('up')"))
        self.data["stack"]["rebuild"] = list(py("print('rebuilt')"))
        self.data["actions"].append(
            {"label": "Provision", "argv": ["devtui:fresh", "odoo", "db", "provision"]}
        )
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            app.run_spec_action(app.spec.actions[-1])
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            lines = [
                line.text for line in app.screen.query_one("#run-log", RichLog).lines
            ]
            self.assertTrue(
                any("fresh ['odoo', 'db', 'provision']" in line for line in lines),
                lines,
            )
            await pilot.press("escape")
            # devtui:rebuild COMMAND: the stack from a re-evaluated shell, then COMMAND there.
            app.processes = None
            app.run_spec_action(
                Action(label="x", argv=("devtui:rebuild", "odoo", "db", "provision"))
            )
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            lines = [
                line.text for line in app.screen.query_one("#run-log", RichLog).lines
            ]
            self.assertEqual(len(app.screen.steps), 2)
            self.assertTrue(any("rebuilt" in line for line in lines), lines)
            self.assertTrue(
                any("fresh ['odoo', 'db', 'provision']" in line for line in lines),
                lines,
            )

    async def test_commands(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.press("3")
            view = app.query_one(CommandsView)
            root = view.roots[0]
            migrate_ = next(c for c in root.walk() if c.path == ("migrate",))
            view.select(root, migrate_)
            await pilot.pause()
            preview = view.query_one("#cmd-preview", Static)
            self.assertIn("needs: --jobs", str(preview.content))
            jobs = view.query("#cmd-form Input").first(Input)
            jobs.value = "2"
            await pilot.pause()
            self.assertIn("$ stub migrate --jobs 2", str(preview.content))
            with mock.patch.object(app, "run_steps") as run_steps:
                view.run()
                self.assertEqual(
                    run_steps.call_args.args[1],
                    [Step(("stub", "migrate", "--jobs", "2"))],
                )
            hits = [
                cmd
                for cmd in app.get_system_commands(app.screen)
                if cmd.title == "stub migrate"
            ]
            self.assertEqual(len(hits), 1)

    async def test_store(self):
        product = Product()
        app = self.app(product=product)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.press("4")
            view = app.query_one(StoreView)
            table = view.query_one("#store-table", DataTable)
            self.assertEqual(table.row_count, 3)
            view.query_one("#store-search", Input).value = "repair"
            await self.until(pilot, lambda: table.row_count == 2)
            table.focus()
            await pilot.press("space")  # repair_order: an add
            await pilot.press("down", "space")  # repair_type: installed, so a removal
            self.assertEqual(view.adds, ["repair_order"])
            self.assertEqual(view.removes, ["repair_type"])
            self.assertIn(
                "brings 1 more: base_dep",
                str(view.query_one("#store-cart-text", Static).content),
            )
            await pilot.press("a")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            self.assertEqual([s.label for s in app.screen.steps], ["remove", "add"])
            await pilot.click("#confirm-run")
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            run = app.screen
            lines = [line.text for line in run.query_one("#run-log", RichLog).lines]
            self.assertTrue(run.ok)
            self.assertTrue(
                any("removing ['repair_type']" in line for line in lines), lines
            )
            self.assertTrue(
                any("adding ['repair_order'] []" in line for line in lines), lines
            )
            await pilot.click("#run-after-0")
            await self.until(
                pilot,
                lambda: (
                    isinstance(app.screen, RunScreen)
                    and app.screen is not run
                    and app.screen.ok
                ),
            )
            lines = [
                line.text for line in app.screen.query_one("#run-log", RichLog).lines
            ]
            self.assertTrue(any("provisioned" in line for line in lines), lines)
            await pilot.press("escape")
            self.assertEqual(view.adds, [])
            self.assertIn("repair_order", view.installed)

    async def test_store_alone(self):
        # `--only store`: the picker a product's own command opens.
        product = Product()
        app = self.app(product=product, only="store")
        async with app.run_test(size=(160, 50)) as pilot:
            self.assertEqual(list(app.query("TabPane")), [])
            self.assertEqual(app.sub_title, "stub-nix: add modules")
            view = app.query_one(StoreView)
            table = view.query_one("#store-table", DataTable)
            await self.until(pilot, lambda: app.focused is table)
            await pilot.press("4", "1")  # no tabs to switch to
            self.assertIs(app.focused, table)
            await pilot.press("space")
            self.assertEqual(view.adds, [view.rows[0].id])
            await pilot.press("a")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            await pilot.click("#confirm-run")
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            self.assertTrue(app.screen.ok)
            run = app.screen
            await pilot.click("#run-after-0")
            await self.until(
                pilot,
                lambda: (
                    isinstance(app.screen, RunScreen)
                    and app.screen is not run
                    and app.screen.ok
                ),
            )
            await pilot.press("escape")
            self.assertIs(app.screen.query_one(StoreView), view)
            self.assertEqual(view.adds, [])

    def test_store_alone_needs_a_store(self):
        with self.assertRaisesRegex(ValueError, "stub-nix has no store here"):
            DevTui(
                self.data, vars_command=self.vars_command, product=None, only="store"
            )
        with self.assertRaisesRegex(ValueError, "only: 'stack'"):
            self.app(only="stack")

    async def test_bundle(self):
        product = Product()
        app = self.app(product=product)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.press("4")
            view = app.query_one(StoreView)
            view.filter = "bundle:base"
            view.refresh_rows()
            view.query_one("#store-table", DataTable).focus()
            await pilot.press("b")
            self.assertEqual(view.bundles, ["base"])
            steps = []
            with mock.patch.object(
                app,
                "push_screen",
                side_effect=lambda screen, cb=None: steps.extend(screen.steps),
            ):
                view.action_apply()
            self.assertEqual(steps[0].argv[-1], "print('adding', [], ['base'])")

    async def test_action_follow_up(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            app.run_spec_action(app.spec.actions[1])
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            button = app.screen.query_one("#run-after-0")
            self.assertEqual(str(button.label), "Rebuild & restart the stack")
            with mock.patch.object(app, "action_stack_rebuild") as rebuild:
                await pilot.click("#run-after-0")
                rebuild.assert_called_once()

    async def test_failure_stops(self):
        app = self.app()
        async with app.run_test(size=(140, 45)) as pilot:
            app.run_steps(
                "t", [Step(py("import sys; sys.exit(3)")), Step(py("print('never')"))]
            )
            await self.until(
                pilot,
                lambda: isinstance(app.screen, RunScreen) and app.screen.ok is not None,
            )
            lines = [
                line.text for line in app.screen.query_one("#run-log", RichLog).lines
            ]
            self.assertFalse(app.screen.ok)
            self.assertFalse(any("never" in line for line in lines))
            self.assertIn(
                "exited 3", str(app.screen.query_one("#run-status", Static).content)
            )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
