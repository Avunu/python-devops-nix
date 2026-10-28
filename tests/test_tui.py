"""python/devtui's standard-library core: the spec, the process-compose
client against a fake server, the command catalogs, the store's search, the
plugin's forms and the stack's environment.

Run: python -I tests/test_tui.py <python/>   (an interpreter with click)

Run on every interpreter the products' presets name, each with
lib.python.textual (lib.tui.check).
"""

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
    sys.exit("usage: test_tui.py <python dir>")
sys.path.insert(0, sys.argv.pop(1))

import click
from devtui import catalog, commands, pc, plugin, spec, stack

BANNER = {"title": "acme — dev environment"}


class SpecTest(unittest.TestCase):
    def test_minimal(self):
        s = spec.parse({"product": "p-nix", "cli": "p", "banner": BANNER})
        self.assertEqual(s.cli, "p")
        self.assertEqual(s.banner.title, "acme — dev environment")
        self.assertIsNone(s.stack)
        self.assertIsNone(s.plugin)

    def test_full(self):
        s = spec.parse(
            {
                "product": "p-nix",
                "cli": "p",
                "root": "/w",
                "banner": BANNER,
                "site": {"url": "http://127.0.0.1:1", "process": "web"},
                "stack": {
                    "actions": [
                        {
                            "label": "Migrate",
                            "argv": ["p", "migrate"],
                            "after": "restart",
                            "key": "m",
                        }
                    ]
                },
                "actions": [
                    {"label": "Shell", "argv": ["p", "shell"], "interactive": True}
                ],
                "commands": [
                    {"file": "/c.json"},
                    {"probe": ["py", "x"], "cache": "/tmp/c"},
                ],
                "plugin": {"module": "p_plugin", "config": {"series": "18.0"}},
            }
        )
        self.assertEqual(s.site.process, "web")
        self.assertEqual(s.stack.actions[0].after, "restart")
        self.assertTrue(s.actions[0].interactive)
        self.assertEqual(s.commands[1].probe, ("py", "x"))
        self.assertEqual(s.plugin.config["series"], "18.0")

    def test_errors(self):
        base = {"product": "p", "cli": "p", "banner": BANNER}
        cases = [
            ({**base, "colour": 1}, "unknown key(s): colour"),
            ({"product": "p", "cli": "p"}, "missing key(s): banner"),
            ({**base, "banner": {"title": 1}}, "banner: title: expected a string"),
            (
                {**base, "actions": [{"label": "x", "argv": []}]},
                "actions[0].argv: empty",
            ),
            (
                {**base, "actions": [{"label": "x", "argv": ["a"], "after": "reboot"}]},
                "after: expected one of",
            ),
            (
                {**base, "actions": [{"label": "x", "argv": ["a"], "key": "ab"}]},
                "key: one character",
            ),
            (
                {**base, "commands": [{"file": "a", "probe": ["b"]}]},
                "one of file or probe",
            ),
        ]
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(spec.SpecError) as ctx:
                    spec.parse(data)
                self.assertIn(message, str(ctx.exception))

    def test_vars(self):
        data = {
            "product": "p",
            "cli": "p",
            "banner": {"title": "on @port@"},
            "site": {"url": "http://h:@port@/@nope@"},
        }
        s = spec.load(data, {"port": "8223"})
        self.assertEqual(s.banner.title, "on 8223")
        self.assertEqual(s.site.url, "http://h:8223/@nope@")
        self.assertEqual(
            spec.parse_vars("port=1\nbad line\n9x=2\nurl=a=b\n"),
            {"port": "1", "url": "a=b"},
        )


# -- process-compose ---------------------------------------------------------------

PROCESSES = {
    "data": [
        {
            "name": "web",
            "status": "Running",
            "is_ready": "Ready",
            "has_ready_probe": True,
            "restarts": 1,
            "exit_code": 0,
            "pid": 42,
            "is_running": True,
            "namespace": "default",
        },
        {
            "name": "db",
            "status": "Running",
            "is_ready": "Not Ready",
            "has_ready_probe": True,
            "is_running": True,
            "pid": 7,
        },
        {"name": "init", "status": "Completed", "exit_code": 0, "is_running": False},
        {"name": "worker", "status": "Completed", "exit_code": 3, "is_running": False},
        {
            "name": "mail",
            "status": "Disabled",
            "is_running": False,
            "future_field": [1],
        },
    ]
}


class FakeProcessCompose(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        self.calls = []
        super().__init__(path, Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        self.server.calls.append((self.command, self.path))
        if self.path == "/live":
            return self._send(200, {"status": "alive"})
        if self.path == "/processes":
            return self._send(200, PROCESSES)
        if self.path.startswith("/process/logs/"):
            name = self.path.split("/")[3]
            return self._send(200, {"logs": [f"{name} line {i}" for i in range(3)]})
        if self.path == "/process/restart/nope":
            return self._send(400, {"error": "process nope does not exist"})
        if self.path.startswith(
            ("/process/restart/", "/process/start/", "/process/stop/", "/project/stop")
        ):
            return self._send(200, {"name": self.path.rsplit("/", 1)[-1]})
        return self._send(404, {"error": "no route"})

    do_GET = do_POST = do_PATCH = _handle


class ProcessComposeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "pc.sock")
        self.server = FakeProcessCompose(self.path)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = pc.Client(self.path)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir)

    def test_processes(self):
        procs = {p.name: p for p in self.client.processes()}
        self.assertEqual(sorted(procs), ["db", "init", "mail", "web", "worker"])
        self.assertEqual(procs["web"].state, "up")
        self.assertEqual(procs["web"].restarts, 1)
        self.assertEqual(procs["db"].state, "starting")
        self.assertEqual(procs["init"].state, "done")
        self.assertEqual(procs["worker"].state, "failed")
        self.assertEqual(procs["mail"].state, "down")

    def test_verbs(self):
        self.client.restart("web")
        self.client.start("db")
        self.client.stop("a/b")
        self.client.shutdown()
        self.assertEqual(
            self.client.logs("web", limit=3), ["web line 0", "web line 1", "web line 2"]
        )
        self.assertIn(("POST", "/process/restart/web"), self.server.calls)
        self.assertIn(("POST", "/process/start/db"), self.server.calls)
        self.assertIn(("PATCH", "/process/stop/a%2Fb"), self.server.calls)
        self.assertIn(("POST", "/project/stop"), self.server.calls)
        self.assertIn(("GET", "/process/logs/web/0/3"), self.server.calls)

    def test_errors(self):
        with self.assertRaises(pc.Error) as ctx:
            self.client.restart("nope")
        self.assertIn("process nope does not exist", str(ctx.exception))
        self.assertTrue(self.client.alive())
        down = pc.Client(os.path.join(self.dir, "absent.sock"))
        self.assertFalse(down.alive())
        with self.assertRaises(pc.Unavailable):
            down.processes()
        with (
            mock.patch.dict(os.environ, clear=True),
            self.assertRaises(pc.Unavailable) as ctx,
        ):
            pc.Client().processes()
        self.assertIn("$PC_SOCKET_PATH is not set", str(ctx.exception))


# -- commands ----------------------------------------------------------------------


@click.group()
@click.option("-c", "--config", help="odoo.conf")
def cli(config):
    """The command line."""


@cli.group("db")
def db():
    """Databases."""


@db.command("migrate")
@click.argument("database", required=False)
@click.option("--all", "all_", is_flag=True, help="every database")
@click.option("--backup/--no-backup", default=True, help="snapshot first")
@click.option("--format", "fmt", type=click.Choice(["zip", "dump"]), default="zip")
@click.option("--module", "modules", multiple=True)
@click.option("--jobs", type=int, required=True)
def migrate(database, all_, backup, fmt, modules, jobs):
    """Migrate a database."""


@cli.command("shell")
def shell():
    """A REPL."""


@cli.command("passthrough", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def passthrough(args):
    """Hands its arguments on."""


@cli.command("secret", hidden=True)
def secret():
    pass


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.root = commands.dump(cli, "odoo", source="odoo-nix", interactive=["shell"])

    def find(self, *path):
        return next(c for c in self.root.walk() if c.path == path)

    def test_dump(self):
        names = [c.name for c in self.root.commands]
        self.assertEqual(names, ["db", "passthrough", "shell"])
        migrate_ = self.find("db", "migrate")
        params = {p.name: p for p in migrate_.params}
        self.assertEqual(params["database"].kind, "argument")
        self.assertTrue(params["all_"].flag)
        self.assertEqual(params["backup"].secondary, ("--no-backup",))
        self.assertEqual(params["fmt"].choices, ("zip", "dump"))
        self.assertTrue(params["modules"].multiple)
        self.assertEqual(params["jobs"].type, "int")
        self.assertTrue(self.find("shell").interactive)
        self.assertTrue(self.find("passthrough").raw)
        # No default is "", whatever click marks it with (8.3: a sentinel).
        self.assertEqual(
            next(p for p in self.root.params if p.name == "config").default, ""
        )
        self.assertEqual(migrate_.summary, "Migrate a database.")

    def test_round_trip(self):
        data = json.loads(json.dumps(commands.to_json(self.root)))
        self.assertEqual(commands.from_json(data), self.root)

    def test_argv(self):
        m = self.find("db", "migrate")
        line = commands.argv(
            "odoo",
            m,
            {
                "database": "acme",
                "all_": True,
                "backup": False,
                "fmt": "dump",
                "modules": "a b",
                "jobs": "2",
            },
        )
        self.assertEqual(
            line,
            [
                "odoo",
                "db",
                "migrate",
                "--all",
                "--no-backup",
                "--format",
                "dump",
                "--module",
                "a",
                "--module",
                "b",
                "--jobs",
                "2",
                "acme",
            ],
        )
        self.assertEqual(
            commands.argv("odoo", m, {"backup": True, "all_": False}),
            ["odoo", "db", "migrate"],
        )
        self.assertEqual(commands.missing(m, {}), ["--jobs"])
        raw = self.find("passthrough")
        self.assertEqual(
            commands.argv("odoo", raw, {"args": "-x 'a b'"}),
            ["odoo", "passthrough", "-x", "a b"],
        )

    def test_probe_cached(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            counter = tmp / "runs"
            data = json.dumps(commands.to_json(self.root))
            code = f"import pathlib; p = pathlib.Path({str(counter)!r}); p.write_text(p.read_text() + 'x' if p.exists() else 'x'); print({data!r})"
            probe = [sys.executable, "-c", code]
            first = commands.run_probe(probe, str(tmp / "cache"))
            second = commands.run_probe(probe, str(tmp / "cache"))
            self.assertEqual(first, self.root)
            self.assertEqual(second, self.root)
            self.assertEqual(counter.read_text(), "x")
            with self.assertRaises(RuntimeError) as ctx:
                commands.run_probe(
                    [sys.executable, "-c", "import sys; sys.exit('boom')"], ""
                )
            self.assertIn("boom", str(ctx.exception))
        finally:
            shutil.rmtree(tmp)

    def test_script(self):
        # commands.py as a probe runs it: a script, with its own directory off
        # sys.path, and the JSON after a marker, past whatever the import printed.
        import subprocess

        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "probe_target.py").write_text(
                "import click\n"
                "import spec\n"
                "print('noise from the framework')\n"
                "@click.group()\n"
                "def g():\n"
                "    'G.'\n"
                "@g.command()\n"
                "@click.option('--x')\n"
                "def run(x):\n"
                "    'Run.'\n"
                "def groups():\n"
                "    return {'frappe': g}\n"
            )
            # A module the target imports, which devtui's own spec.py must not shadow.
            (tmp / "spec.py").write_text("SHADOW = False\n")
            script = Path(commands.__file__)
            probe = [
                sys.executable,
                str(script),
                "dump",
                "--marked",
                "probe_target:groups",
                "--name",
                "bench",
                "--source",
                "t",
            ]
            env = {**os.environ, "PYTHONPATH": str(tmp)}
            with mock.patch.dict(os.environ, env):
                root = commands.run_probe(probe, "", cwd=str(tmp))
            self.assertEqual([c.name for c in root.commands], ["run"])
            self.assertEqual((root.name, root.source), ("bench", "t"))
            out = subprocess.run(
                probe, env=env, capture_output=True, text=True, check=True
            )
            self.assertTrue(
                out.stdout.startswith("noise from the framework\n" + commands.MARKER)
            )
        finally:
            shutil.rmtree(tmp)


# -- the store's catalog --------------------------------------------------------------


ITEMS = (
    catalog.Item(
        "repair_order",
        title="Repair Orders",
        group="repair",
        summary="Fix things",
        tags=("application",),
        rank=1,
    ),
    catalog.Item("repair_type", title="Repair Type", group="repair", summary="Kinds"),
    catalog.Item(
        "web_responsive",
        title="Web Responsive",
        group="web",
        summary="Mobile-friendly backend",
    ),
    catalog.Item(
        "account_lock",
        title="Lock dates",
        group="account-financial-tools",
        categories=("Accounting",),
        available=False,
    ),
)


class CatalogTest(unittest.TestCase):
    def test_search(self):
        ids = lambda hits: [i.id for i in hits]
        self.assertEqual(
            ids(catalog.search(ITEMS, "repair")), ["repair_order", "repair_type"]
        )
        self.assertEqual(ids(catalog.search(ITEMS, "rep ord")), ["repair_order"])
        self.assertEqual(ids(catalog.search(ITEMS, "mobile")), ["web_responsive"])
        self.assertEqual(
            ids(catalog.search(ITEMS, "web_responsive")), ["web_responsive"]
        )
        self.assertEqual(ids(catalog.search(ITEMS, "pair")), [])
        self.assertEqual(
            ids(catalog.search(ITEMS, "", group="web")), ["web_responsive"]
        )
        self.assertEqual(
            ids(catalog.search(ITEMS, "", category="Accounting")), ["account_lock"]
        )
        self.assertEqual(
            ids(catalog.search(ITEMS, "", only=["repair_type"])), ["repair_type"]
        )
        self.assertNotIn(
            "account_lock", ids(catalog.search(ITEMS, "", available_only=True))
        )
        # Unavailable items sort last.
        self.assertEqual(ids(catalog.search(ITEMS, ""))[-1], "account_lock")

    def test_catalog(self):
        c = catalog.Catalog(items=ITEMS)
        self.assertEqual(c.by_id["repair_type"].group, "repair")
        self.assertEqual(
            c.groups(), [("account-financial-tools", 1), ("repair", 2), ("web", 1)]
        )
        self.assertEqual(c.categories(), [("Accounting", 1)])


class FormTest(unittest.TestCase):
    def test_build(self):
        form = plugin.Form(
            "Add from a Git URL",
            (
                plugin.Field("url", "URL", required=True),
                plugin.Field("branch", "Branch"),
            ),
            ("odoo", "module", "add", "{url}", "--branch={branch}"),
        )
        self.assertEqual(
            form.build({"url": " owner/repo "}), ["odoo", "module", "add", "owner/repo"]
        )
        self.assertEqual(
            form.build({"url": "o/r", "branch": "18.0"}),
            ["odoo", "module", "add", "o/r", "--branch=18.0"],
        )


class StackTest(unittest.TestCase):
    def test_environment(self):
        base = {"DIRENV_DIR": "-/w", "DEVENV_IN_DIRENV_SHELL": "true", "PATH": "/bin"}
        loaded = stack.environment(base, fresh=False)
        fresh = stack.environment(base, fresh=True)
        self.assertEqual(loaded["DIRENV_DIR"], "-/w")
        self.assertNotIn("DIRENV_DIR", fresh)
        self.assertNotIn("DEVENV_IN_DIRENV_SHELL", fresh)
        self.assertEqual(fresh["PC_TUI_ENABLED"], "0")
        self.assertEqual(fresh["PATH"], "/bin")
        self.assertEqual(stack.command(fresh=True), ["devenv", "up", "-D"])
        self.assertEqual(stack.command(("a",), ("b",), fresh=True), ["b"])
        self.assertEqual(stack.command(("a",), ("b",), fresh=False), ["a"])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
