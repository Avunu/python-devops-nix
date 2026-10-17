"""The devguard core (python/devguard) against the stub product, without a
framework or a network.

Run: python3 -I tests/test_devguard.py <tree>

<tree> holds the stub product's package as lib.devguard.mkPackage builds it
(stub_devguard/, with the settings lib.devguard.stub bakes) and a second graft
from lib.python.mkGraft (stub_other/). lib.devguard.check runs this on every
interpreter the products' presets name.

The transport guards run against real sockets on loopback: stub SMTP, POP3 and
IMAP servers written on socketserver (smtpd and asyncore are gone from 3.12),
whose logs say what reached them. Remote destinations are in TEST-NET-1
(192.0.2.0/24), where nothing answers, so a connection the guard lets through
fails at the network instead, which is how "let through" is told apart from
"refused". Names under .test are stood in by a fake resolver, and names under
.invalid never resolve.

Everything here patches process-global state (socket, smtplib, ...), so the
guards are installed once, at import, exactly as the .pth bootstrap does, and
each test switches them with STUB_DEVGUARD_* variables, which is also how a
developer bypasses them for a single command.

Ported from frappe-nix's lib/devguard/tests/test_devguard.py and odoo-nix's
tests/test_devguard.py: the parts that test the core.
"""

import asyncio
import contextlib
import importlib
import importlib.util
import io
import json
import os
import smtplib
import socket
import socketserver
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock
import urllib.error
import urllib.request

if len(sys.argv) < 2:
    sys.exit("usage: test_devguard.py <tree with stub_devguard/ and stub_other/>")
TREE = os.path.abspath(sys.argv.pop(1))
sys.path.insert(0, TREE)

# Keep a developer's own settings, and their shell's runtime file, out of it.
for _key in [k for k in os.environ if k.startswith("STUB_DEVGUARD_")]:
    del os.environ[_key]
os.environ.pop("DEVENV_RUNTIME", None)

import imaplib
import poplib

import stub_devguard
import stub_other
from stub_devguard import (
    DevGuardBlocked,
    DevGuardPatchError,
    _hook,
    _patch,
    _product,
    _settings,
    settings,
)

# A stand-in for the framework's scheduler, imported before install() so the
# stub's jobs guard patches it at once (the same code path as a real import).
_framework = types.ModuleType("stub_framework")
_scheduler = types.ModuleType("stub_framework.scheduler")
RAN = []
_scheduler.run = lambda job: RAN.append(job) or "ran"
sys.modules["stub_framework"] = _framework
sys.modules["stub_framework.scheduler"] = _scheduler

stub_devguard.install()  # exactly what the .pth bootstrap does

from stub_devguard.guards import egress, jobs

BLOCKED = ("192.0.2.1", 9)

# An unresolvable host is the point: if any of these reach DNS, the redirect
# did not happen and the test fails with gaierror rather than passing quietly.
ELSEWHERE = "smtp.devguard-must-not-resolve.invalid"

#: What lib.devguard.stub bakes into _baked.json.
BAKED_MAIL_PORT = 2525


def env(**kw):
    """Patch STUB_DEVGUARD_* for one block: env(EGRESS_ALLOW_HOSTS="a,b")."""
    return unittest.mock.patch.dict(
        os.environ, {f"STUB_DEVGUARD_{k}": str(v) for k, v in kw.items()}
    )


def stderr():
    return contextlib.redirect_stderr(io.StringIO())


def let_through(fn):
    """Whether the guard let FN's connection through, whatever the network said."""
    try:
        fn()
    except DevGuardBlocked:
        return False
    except OSError as exc:
        # urllib wraps the socket error; the guard's refusal is its reason.
        return not isinstance(getattr(exc, "reason", None), DevGuardBlocked)
    return True


def connect(address, method="connect", family=socket.AF_INET):
    sock = socket.socket(family)
    sock.settimeout(0.3)
    try:
        return getattr(sock, method)(address)
    finally:
        sock.close()


def blocked_message(fn):
    try:
        fn()
    except DevGuardBlocked as exc:
        return str(exc)
    return ""


_DNS = {
    "api.vendor.test": "192.0.2.10",
    "api.allowed.test": "192.0.2.11",
    "cdn.allowed.test": "192.0.2.12",
    "baked.allowed.test": "192.0.2.13",
    "sandbox.integration.test": "192.0.2.14",
    "alias.test": "127.0.0.1",
}


def _stand_in_dns(host, port, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if isinstance(name, str) and name.endswith((".test", ".invalid")):
        if name not in _DNS:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_DNS[name], port or 0))]
    return _REAL_RESOLVER(host, port, *args, **kwargs)


_REAL_RESOLVER = egress._getaddrinfo
egress._getaddrinfo = _stand_in_dns


# --------------------------------------------------------------------------
# stub servers
# --------------------------------------------------------------------------


class StubServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)
        self.log = []
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


class _Lines(socketserver.StreamRequestHandler):
    def send(self, *lines):
        for line in lines:
            self.wfile.write(line + b"\r\n")
        self.wfile.flush()

    def lines(self):
        while True:
            line = self.rfile.readline()
            if not line:
                return
            line = line.rstrip(b"\r\n")
            self.server.log.append(line)
            yield line


class SMTPHandler(_Lines):
    def handle(self):
        self.send(b"220 stub ESMTP")
        data = False
        for line in self.lines():
            command = (line.split() or [b""])[0].upper()
            if data:
                if line == b".":
                    data = False
                    self.send(b"250 queued")
            elif command in (b"EHLO", b"HELO"):
                self.send(b"250 stub")
            elif command == b"DATA":
                data = True
                self.send(b"354 go ahead")
            elif command == b"QUIT":
                self.send(b"221 bye")
                return
            else:
                self.send(b"250 ok")


class POP3Handler(_Lines):
    def handle(self):
        self.send(b"+OK stub POP3")
        for line in self.lines():
            if line.upper().startswith(b"QUIT"):
                self.send(b"+OK bye")
                return
            self.send(b"+OK")


class IMAPHandler(_Lines):
    def handle(self):
        self.send(b"* OK stub IMAP4rev1 ready")
        for line in self.lines():
            tag, _, rest = line.partition(b" ")
            command = rest.split(b" ", 1)[0].upper()
            if command == b"CAPABILITY":
                self.send(b"* CAPABILITY IMAP4rev1", tag + b" OK CAPABILITY completed")
            elif command == b"LOGOUT":
                self.send(b"* BYE stub", tag + b" OK LOGOUT completed")
                return
            else:
                self.send(tag + b" OK done")


def setUpModule():
    global SMTP, POP3, IMAP
    SMTP, POP3, IMAP = (
        StubServer(SMTPHandler),
        StubServer(POP3Handler),
        StubServer(IMAPHandler),
    )


def tearDownModule():
    for server in (SMTP, POP3, IMAP):
        server.stop()


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


class SettingsTest(unittest.TestCase):
    def setUp(self):
        _settings._RUNTIME_CACHE.clear()
        self.addCleanup(_settings._RUNTIME_CACHE.clear)
        self.dir = tempfile.mkdtemp()

    def runtime(self, content):
        path = os.path.join(self.dir, "devguard-runtime.json")
        with open(path, "w") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        _settings._RUNTIME_CACHE.clear()
        return path

    def test_the_four_layers_in_order(self):
        st = settings()
        path = self.runtime({"guards": {"mail": {"port": 3535}}})
        with env(RUNTIME=path, MAIL_PORT=4545):
            self.assertEqual(st.mail_port, 4545, "the environment wins")
        with env(RUNTIME=path):
            self.assertEqual(st.mail_port, 3535, "then the runtime file")
        self.assertEqual(st.mail_port, BAKED_MAIL_PORT, "then the baked values")
        with unittest.mock.patch.object(_settings, "BAKED", {}):
            self.assertEqual(st.mail_port, 1025, "then the defaults")

    def test_a_layer_without_the_key_falls_through(self):
        path = self.runtime({"guards": {"mail": {"http_port": 18025}}})
        with env(RUNTIME=path):
            self.assertEqual(settings().mail_http_port, 18025)
            self.assertEqual(settings().mail_port, BAKED_MAIL_PORT)

    def test_the_runtime_file_is_found_under_devenv_runtime(self):
        self.runtime({"guards": {"mail": {"port": 3636}}})
        with unittest.mock.patch.dict(os.environ, {"DEVENV_RUNTIME": self.dir}):
            self.assertEqual(settings().mail_port, 3636)
            other = os.path.join(tempfile.mkdtemp(), "elsewhere.json")
            with open(other, "w") as handle:
                json.dump({"guards": {"mail": {"port": 3737}}}, handle)
            with env(RUNTIME=other):
                self.assertEqual(settings().mail_port, 3737, "an explicit path wins")

    def test_a_missing_or_broken_runtime_file_falls_back(self):
        for content in ("{not json", "[1, 2]", '{"guards": []}'):
            with env(RUNTIME=self.runtime(content)):
                self.assertEqual(settings().mail_port, BAKED_MAIL_PORT, content)
        with env(RUNTIME=os.path.join(self.dir, "absent.json")):
            self.assertEqual(settings().mail_port, BAKED_MAIL_PORT)

    def test_the_runtime_file_is_read_once_per_path(self):
        path = self.runtime({"guards": {"mail": {"port": 3838}}})
        with env(RUNTIME=path):
            self.assertEqual(settings().mail_port, 3838)
            with open(path, "w") as handle:
                json.dump({"guards": {"mail": {"port": 3939}}}, handle)
            self.assertEqual(settings().mail_port, 3838)

    def test_product_defaults_over_core_defaults(self):
        st = settings()
        self.assertEqual(st.pop3_user, "stub")  # the product's
        self.assertEqual(st.pop3_password, "dev")  # the core's
        self.assertEqual(st.text("mail", "sender"), "noreply@stub.test")  # product-only
        self.assertIsNone(st.get("mail", "no_such_key"))
        self.assertTrue(st.guard_enabled("integrations"))

    def test_master_switch_through_every_layer(self):
        st = settings()
        self.assertTrue(st.enabled)
        with env(ENABLED=0):
            self.assertFalse(st.guard_enabled("egress"))
        with env(RUNTIME=self.runtime({"enabled": False})):
            self.assertFalse(st.enabled)
            with env(ENABLED=1):
                self.assertTrue(st.enabled)
        with unittest.mock.patch.dict(_settings.BAKED, {"enabled": False}):
            self.assertFalse(st.enabled)

    def test_named_disable_and_per_guard_enable(self):
        st = settings()
        with env(DISABLE="egress, JOBS"):
            self.assertFalse(st.guard_enabled("egress"))
            self.assertFalse(st.guard_enabled("jobs"))
            self.assertTrue(st.guard_enabled("mail"))
        with env(JOBS_ENABLE=0):
            self.assertFalse(st.guard_enabled("jobs"))
        with unittest.mock.patch.dict(
            _settings.BAKED, {"guards": {"jobs": {"enable": False}}}
        ):
            self.assertFalse(st.guard_enabled("jobs"))

    def test_lists_split_on_commas(self):
        with env(EGRESS_ALLOW_HOSTS="a.example, 10.0.0.0/8 ,"):
            self.assertEqual(
                settings().items("egress", "allow_hosts"), ["a.example", "10.0.0.0/8"]
            )

    def test_blocked_jobs_unions_built_in_and_extra(self):
        self.assertEqual(
            settings().blocked_jobs,
            ["stub.backups.offsite", "stub.mail.fetch", "stub.sync.push"],
        )
        with env(JOBS_EXTRA_BLOCKED="my.job, stub.mail.fetch"):
            self.assertEqual(
                settings().blocked_jobs,
                ["stub.backups.offsite", "stub.mail.fetch", "my.job"],
            )

    def test_an_invalid_number_warns_and_falls_back(self):
        with env(MAIL_PORT="twenty-five"), stderr() as err:
            self.assertEqual(settings().mail_port, 1025)
        self.assertIn(
            "WARNING stub_devguard invalid integer 'twenty-five'", err.getvalue()
        )

    def test_names(self):
        self.assertEqual(_settings.PACKAGE, "stub_devguard")
        self.assertEqual(
            _settings.env_name("egress", "allow_hosts"),
            "STUB_DEVGUARD_EGRESS_ALLOW_HOSTS",
        )


# --------------------------------------------------------------------------
# the post-import hook
# --------------------------------------------------------------------------


class HookTest(unittest.TestCase):
    def module(self, name, body="VALUE = 1\n"):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, f"{name}.py"), "w") as handle:
            handle.write(body)
        sys.path.insert(0, tmp)
        self.addCleanup(sys.path.remove, tmp)
        self.addCleanup(sys.modules.pop, name, None)

    def test_fires_for_a_module_imported_later(self):
        self.module("devguard_probe_later")
        seen = []
        _hook.on_import("devguard_probe_later", lambda m: seen.append(m.VALUE))
        self.assertEqual(seen, [])
        module = importlib.import_module("devguard_probe_later")
        self.assertEqual(seen, [1])
        self.assertEqual(module.VALUE, 1, "the hooked module still works")
        self.assertTrue(module.__file__.endswith("devguard_probe_later.py"))

    def test_fires_at_once_for_a_module_already_imported(self):
        seen = []
        _hook.on_import("json", lambda m: seen.append(m.__name__))
        self.assertEqual(seen, ["json"])

    def test_fires_for_a_stdlib_submodule(self):
        fired = []
        saved = {
            name: sys.modules.pop(name)
            for name in ("json", "json.decoder")
            if name in sys.modules
        }
        self.addCleanup(sys.modules.update, saved)
        _hook.on_import("json.decoder", lambda m: fired.append(m.__name__))
        import json as fresh

        self.assertEqual(fired, ["json.decoder"])
        self.assertEqual(fresh.loads('{"a": 1}'), {"a": 1})

    def test_a_failing_callback_fails_the_import(self):
        self.module("devguard_probe_moved")

        def callback(module):
            _patch.require(module, "gone")

        _hook.on_import("devguard_probe_moved", callback)
        with self.assertRaises(DevGuardPatchError):
            importlib.import_module("devguard_probe_moved")
        self.assertNotIn("devguard_probe_moved", sys.modules)

    def test_only_registered_names_are_intercepted(self):
        self.assertIsNone(_hook._REGISTRY.find_spec("devguard_probe_unregistered"))

    def test_resolving_does_not_find_itself(self):
        # The re-entrancy guard: while the registry resolves a name, a nested
        # lookup of the same name (importlib.util.find_spec walks
        # sys.meta_path, so it reaches this finder again) is passed over.
        self.module("devguard_probe_reentrant")
        _hook.on_import("devguard_probe_reentrant", lambda m: None)
        nested = []
        real = importlib.util.find_spec

        def find_spec(name, package=None):
            nested.append(_hook._REGISTRY.find_spec(name))
            return real(name, package)

        with unittest.mock.patch.object(importlib.util, "find_spec", find_spec):
            spec = _hook._REGISTRY.find_spec("devguard_probe_reentrant")
        self.assertEqual(nested, [None])
        self.assertIsInstance(spec.loader, _hook._Loader)
        self.assertNotIsInstance(spec.loader._inner, _hook._Loader)
        self.assertIn("VALUE", spec.loader.get_source("devguard_probe_reentrant"))
        self.assertEqual(_hook._REGISTRY._resolving, set())

    def test_two_grafts_both_fire(self):
        # stub_other is a second graft with its own copy of _hook.py: its own
        # registry, also at the front of sys.meta_path.
        other = sys.modules["stub_other._hook"]
        self.assertIsNot(other._REGISTRY, _hook._REGISTRY)
        self.module("devguard_probe_shared")
        seen = []
        _hook.on_import("devguard_probe_shared", lambda m: seen.append("devguard"))
        other.on_import("devguard_probe_shared", lambda m: seen.append("other"))
        self.assertEqual(sys.meta_path[:2], [other._REGISTRY, _hook._REGISTRY])
        importlib.import_module("devguard_probe_shared")
        self.assertEqual(sorted(seen), ["devguard", "other"])
        self.assertEqual(sys.meta_path.count(_hook._REGISTRY), 1)
        self.assertEqual(sys.meta_path.count(other._REGISTRY), 1)


stub_other.install()


# --------------------------------------------------------------------------
# the patch helpers and the product's hooks
# --------------------------------------------------------------------------


class PatchTest(unittest.TestCase):
    def setUp(self):
        _product.LOGGED.clear()

    def test_warn_goes_to_stderr_and_the_product_sink(self):
        with stderr() as err:
            _patch.warn("probe", "hello")
        self.assertEqual(err.getvalue(), "WARNING stub_devguard[probe] hello\n")
        self.assertEqual(_product.LOGGED, [("probe", "hello")])

    def test_a_failing_sink_does_not_fail_warn(self):
        def sink(guard, message):
            raise RuntimeError("logging is down")

        with unittest.mock.patch.object(_product, "log", sink), stderr() as err:
            _patch.warn("probe", "still here")
        self.assertIn("still here", err.getvalue())

    def test_announce_once(self):
        with stderr() as err:
            _patch.announce("announce-probe", "first")
            _patch.announce("announce-probe", "second")
        self.assertEqual(
            err.getvalue(), "WARNING stub_devguard[announce-probe] ACTIVE -- first\n"
        )

    def test_block(self):
        with stderr() as err, self.assertRaises(DevGuardBlocked) as ctx:
            _patch.block("probe", "no")
        self.assertEqual(str(ctx.exception), "stub_devguard[probe]: no")
        self.assertIsInstance(ctx.exception, OSError)
        self.assertIn("WARNING stub_devguard[probe] no", err.getvalue())

    def test_user_error_raises_the_frameworks_error(self):
        with stderr(), self.assertRaises(_product.StubUserError) as ctx:
            _patch.user_error("probe", "press elsewhere")
        self.assertEqual(
            str(ctx.exception), "Blocked by stub-devguard: press elsewhere"
        )
        self.assertEqual(_product.LOGGED, [("probe", "press elsewhere")])

    def test_user_error_without_a_hook_still_refuses(self):
        for hook in (None, lambda guard, message: None):
            with (
                unittest.mock.patch.object(_product, "user_error", hook),
                stderr(),
                self.assertRaises(DevGuardBlocked),
            ):
                _patch.user_error("probe", "no")

    def test_replacements(self):
        with stderr() as err:
            skip = _patch.no_op("probe", "pkg.job", banner="jobs are skipped")
            self.assertIsNone(skip(1, a=2))
            refuse = _patch.blocking("probe", "pkg.push", "not here")
            with self.assertRaises(DevGuardBlocked) as ctx:
                refuse()
        self.assertTrue(_patch.is_guarded(skip) and _patch.is_guarded(refuse))
        self.assertFalse(_patch.is_guarded(len))
        self.assertEqual(str(ctx.exception), "stub_devguard[probe]: pkg.push: not here")
        self.assertIn("[probe] skipping pkg.job", err.getvalue())
        self.assertEqual(_patch.mark(lambda: None, "t").__devguard__, "t")

    def test_require_fails_closed_naming_the_product(self):
        owner = types.SimpleNamespace(__name__="pkg.mod", here=1, b=2)
        self.assertEqual(_patch.require(owner, "here"), 1)
        self.assertEqual(_patch.STATUS["pkg.mod.here"], "patched")
        with self.assertRaises(DevGuardPatchError) as ctx:
            _patch.require(owner, "gone")
        message = str(ctx.exception)
        for part in (
            "stub_devguard: pkg.mod.gone is missing",
            "Stub's internals",
            "Update stub-nix",
            "STUB_DEVGUARD_ENABLED=0",
        ):
            self.assertIn(part, message)
        self.assertEqual(_patch.require_any(owner, "a", "b"), ("b", 2))
        with self.assertRaises(DevGuardPatchError) as ctx:
            _patch.require_any(owner, "x", "y")
        self.assertIn("pkg.mod has none of x, y", str(ctx.exception))


# --------------------------------------------------------------------------
# install, and the product's guards
# --------------------------------------------------------------------------


class InstallTest(unittest.TestCase):
    def test_core_guards_first_then_the_products(self):
        self.assertEqual(
            [g.NAME for g in stub_devguard.all_guards()], ["egress", "mail", "jobs"]
        )

    def test_status_reports_what_was_patched(self):
        status = stub_devguard.status()
        for target in (
            "socket.socket.connect",
            "socket.getaddrinfo",
            "stub_framework.scheduler.run",
        ):
            self.assertEqual(status.get(target), "patched", target)

    def test_install_is_idempotent(self):
        run = _scheduler.run
        stub_devguard.install()
        self.assertIs(_scheduler.run, run)

    def test_the_product_guard_skips_denylisted_jobs(self):
        RAN.clear()
        with stderr():
            self.assertIsNone(_scheduler.run("stub.backups.offsite"))  # built in
            self.assertIsNone(_scheduler.run("stub.sync.push"))  # baked extra
            with env(JOBS_EXTRA_BLOCKED="stub.report"):
                self.assertIsNone(_scheduler.run("stub.report"))
        self.assertEqual(_scheduler.run("stub.report"), "ran")
        self.assertEqual(
            _scheduler.run("stub.backups.offsite_local"), "ran"
        )  # exact match
        with env(DISABLE="jobs"):
            self.assertEqual(_scheduler.run("stub.backups.offsite"), "ran")
        self.assertEqual(
            RAN, ["stub.report", "stub.backups.offsite_local", "stub.backups.offsite"]
        )
        self.assertEqual(jobs.NAME, "jobs")


# --------------------------------------------------------------------------
# egress
# --------------------------------------------------------------------------


class EgressTest(unittest.TestCase):
    def test_loopback_and_unix_sockets_connect(self):
        session = socket.create_connection(("127.0.0.1", SMTP.port), timeout=2)
        self.assertEqual(session.getpeername()[1], SMTP.port)
        session.close()
        self.assertEqual(connect(("localhost", SMTP.port), "connect_ex"), 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s")
            server = socket.socket(socket.AF_UNIX)
            server.bind(path)
            server.listen(1)
            client = socket.socket(socket.AF_UNIX)
            client.connect(path)
            client.close()
            server.close()

    def test_ipv4_mapped_loopback_connects(self):
        if not socket.has_ipv6:
            self.skipTest("no IPv6")
        try:
            result = connect(
                ("::ffff:127.0.0.1", SMTP.port), "connect_ex", socket.AF_INET6
            )
        except DevGuardBlocked as exc:
            self.fail(f"refused by the guard: {exc}")
        except OSError as exc:
            self.skipTest(f"no dual-stack sockets here: {exc}")
        self.assertIsInstance(result, int)  # the network's answer, not the guard's

    def test_everything_else_is_refused_before_any_packet(self):
        with stderr():
            self.assertFalse(let_through(lambda: connect(BLOCKED)))
            self.assertFalse(let_through(lambda: connect(BLOCKED, "connect_ex")))
            self.assertFalse(
                let_through(lambda: socket.create_connection(BLOCKED, timeout=0.3))
            )
            self.assertIn(
                "stub_devguard[egress]: blocked connection to 192.0.2.1:9",
                blocked_message(lambda: connect(BLOCKED)),
            )
            with self.assertRaises(urllib.error.URLError) as ctx:
                urllib.request.urlopen("http://192.0.2.1:9/", timeout=0.3)
            self.assertIsInstance(ctx.exception.reason, DevGuardBlocked)
            with self.assertRaises(DevGuardBlocked):
                asyncio.run(asyncio.open_connection(*BLOCKED))

    def test_a_refusal_is_logged_once_per_destination(self):
        with stderr() as err:
            for _ in range(3):
                self.assertFalse(let_through(lambda: connect(("192.0.2.2", 7))))
        self.assertEqual(err.getvalue().count("blocked connection to 192.0.2.2:7"), 1)

    def test_the_banner_names_the_settings(self):
        with unittest.mock.patch.object(_patch, "_ANNOUNCED", set()), stderr() as err:
            let_through(lambda: connect(("192.0.2.3", 9)))
        line = err.getvalue().splitlines()[0]
        self.assertTrue(
            line.startswith(
                "WARNING stub_devguard[egress] ACTIVE -- outbound connections"
            ),
            line,
        )
        self.assertIn("STUB_DEVGUARD_EGRESS_ALLOW_HOSTS=host[,host...]", line)
        self.assertIn("STUB_DEVGUARD_DISABLE=egress", line)

    def test_the_allowlist_matrix(self):
        with env(
            EGRESS_ALLOW_HOSTS="203.0.113.0/24, api.example.test, *.corp.test, 198.51.100.7, 2001:db8::/32"
        ):
            for host in (
                "127.0.0.1",
                "127.8.9.10",
                "::1",
                "::ffff:127.0.0.1",
                "0.0.0.0",
                "::",
                "localhost",
                "LOCALHOST.",
                "site.localhost",
                "alias.test",  # a name that resolves only to loopback
                "203.0.113.50",
                "::ffff:203.0.113.50",
                "2001:db8::1",
                "api.example.test",
                "API.example.test.",
                "a.corp.test",
                "corp.test",
                "198.51.100.7",
            ):
                self.assertTrue(egress._allowed(host), host)
            for host in (
                "192.0.2.1",
                "::ffff:192.0.2.1",
                "203.0.114.1",
                "2001:db9::1",
                "example.test",
                "evilapi.example.test",
                "corp.test.evil.test",
                "localhost.evil.test",
                "notlocalhost.invalid",
                "198.51.100.8",
                "nxdomain.invalid",
            ):
                self.assertFalse(egress._allowed(host), host)

    def test_the_baked_allowlist_holds_until_the_environment_replaces_it(self):
        self.assertTrue(egress._allowed("baked.allowed.test"))
        self.assertTrue(
            let_through(
                lambda: socket.create_connection(("baked.allowed.test", 9), timeout=0.3)
            )
        )
        with env(EGRESS_ALLOW_HOSTS="api.allowed.test"):
            self.assertFalse(egress._allowed("baked.allowed.test"))

    def test_a_name_resolving_only_to_loopback_is_allowed(self):
        loopback, remote = egress._ip("127.0.0.1"), egress._ip("192.0.2.1")
        with unittest.mock.patch.object(egress, "_resolve", return_value=(loopback,)):
            self.assertTrue(egress._allowed("alias.internal"))
        with unittest.mock.patch.object(
            egress, "_resolve", return_value=(loopback, remote)
        ):
            self.assertFalse(egress._allowed("alias.internal"))
        with unittest.mock.patch.object(egress, "_resolve", return_value=()):
            self.assertFalse(egress._allowed("alias.internal"))

    def test_an_allowlisted_name_passes_when_its_client_resolves_first(self):
        # connect() is handed the address, never the name: create_connection,
        # urllib3 (requests) and asyncio all resolve first.
        with env(EGRESS_ALLOW_HOSTS="api.allowed.test"):
            self.assertTrue(
                let_through(
                    lambda: socket.create_connection(
                        ("api.allowed.test", 9), timeout=0.3
                    )
                )
            )
            self.assertTrue(
                let_through(
                    lambda: urllib.request.urlopen(
                        "http://api.allowed.test:9/", timeout=0.3
                    )
                )
            )
            with stderr():
                self.assertFalse(
                    let_through(
                        lambda: socket.create_connection(
                            ("api.vendor.test", 443), timeout=0.3
                        )
                    )
                )
        with env(EGRESS_ALLOW_HOSTS="*.allowed.test"):
            self.assertTrue(
                let_through(
                    lambda: socket.create_connection(
                        ("cdn.allowed.test", 9), timeout=0.3
                    )
                )
            )
        with stderr():
            self.assertFalse(
                let_through(
                    lambda: socket.create_connection(
                        ("cdn.allowed.test", 9), timeout=0.3
                    )
                ),
                "dropping the entry refuses a name resolved earlier",
            )

    def test_the_refusal_names_the_host_it_resolved(self):
        with stderr():
            message = blocked_message(
                lambda: socket.create_connection(("api.vendor.test", 443), timeout=0.3)
            )
        self.assertIn("blocked connection to 192.0.2.10:443 (api.vendor.test)", message)

    def test_the_products_lists_open_egress_too(self):
        with env(INTEGRATIONS_ALLOW_HOSTS="sandbox.integration.test"):
            self.assertTrue(egress._allowed("sandbox.integration.test"))
            self.assertTrue(egress.listed("sandbox.integration.test"))
            self.assertTrue(
                let_through(
                    lambda: socket.create_connection(
                        ("sandbox.integration.test", 9), timeout=0.3
                    )
                )
            )
        self.assertFalse(egress._allowed("sandbox.integration.test"))

    def test_listed_needs_no_dns(self):
        no_dns = unittest.mock.patch.object(
            egress, "_getaddrinfo", side_effect=AssertionError("DNS")
        )
        with env(EGRESS_ALLOW_HOSTS="*.stripe.test, 10.0.0.0/8"), no_dns:
            for host in (
                "api.stripe.test",
                "10.1.2.3",
                "127.0.0.1",
                "::ffff:127.0.0.1",
                "site.localhost",
            ):
                self.assertTrue(egress.listed(host), host)
            for host in ("api.other.test", "alias.test", "192.0.2.1"):
                self.assertFalse(egress.listed(host), host)

    def test_the_mail_catcher_is_allowed_while_the_mail_guard_is_on(self):
        with env(MAIL_HOST="catcher.devguard.invalid"):
            self.assertTrue(egress._allowed("catcher.devguard.invalid"))
            with env(DISABLE="mail"):
                self.assertFalse(egress._allowed("catcher.devguard.invalid"))

    def test_passage(self):
        with egress.passage("objectstore"):
            self.assertTrue(egress.in_passage())
            self.assertTrue(let_through(lambda: connect(BLOCKED)))
            other = []
            thread = threading.Thread(
                target=lambda: other.append(let_through(lambda: connect(BLOCKED)))
            )
            with stderr():
                thread.start()
                thread.join()
            self.assertEqual(
                other, [False], "another thread's connection is still checked"
            )
        self.assertFalse(egress.in_passage())
        with stderr():
            self.assertFalse(let_through(lambda: connect(BLOCKED)))
        with self.assertRaises(RuntimeError), egress.passage("objectstore"):
            raise RuntimeError("boom")
        self.assertFalse(egress.in_passage(), "a passage ends when its block raises")

    def test_an_unexpected_resolver_answer_is_ignored(self):
        egress._remember(
            "odd.test", [(socket.AF_UNIX, socket.SOCK_STREAM, 0, "", "/not/inet")]
        )
        egress._remember(b"\xff", [])
        egress._remember("", [])

    def test_it_can_be_turned_off_for_one_command(self):
        for var in ({"DISABLE": "egress"}, {"ENABLED": "0"}, {"EGRESS_ENABLE": "0"}):
            with env(**var):
                self.assertTrue(let_through(lambda: connect(BLOCKED)), var)
        with stderr():
            self.assertFalse(let_through(lambda: connect(BLOCKED)))


# --------------------------------------------------------------------------
# the mail transport
# --------------------------------------------------------------------------


class MailTransportTest(unittest.TestCase):
    def setUp(self):
        for server in (SMTP, POP3, IMAP):
            server.log.clear()
        patch = env(MAIL_PORT=SMTP.port, MAIL_POP3_PORT=POP3.port)
        patch.start()
        self.addCleanup(patch.stop)
        quiet = stderr()
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_any_smtp_target_lands_on_the_catcher(self):
        session = smtplib.SMTP(ELSEWHERE, 25, timeout=2)
        self.assertEqual(session.sock.getpeername()[1], SMTP.port)
        self.assertEqual(session.starttls()[0], 220)
        self.assertEqual(session.login("someone", "hunter2")[0], 235)
        session.sendmail(
            "a@prod.test", ["customer@prod.test"], "Subject: hi\r\n\r\nbody"
        )
        session.quit()
        commands = [line.split()[0].lower() for line in SMTP.log if line]
        self.assertNotIn(b"starttls", commands)
        self.assertNotIn(b"auth", commands)
        self.assertTrue(
            any(
                line.lower().startswith(b"mail from:<a@prod.test>") for line in SMTP.log
            ),
            SMTP.log,
        )
        self.assertIn(b"body", SMTP.log)

    def test_an_explicit_connect_lands_on_the_catcher(self):
        session = smtplib.SMTP(timeout=2)
        session.connect(ELSEWHERE, 587)
        self.assertEqual(session.sock.getpeername()[1], SMTP.port)
        session.quit()

    def test_smtp_ssl_lands_on_the_catcher_in_plaintext(self):
        session = smtplib.SMTP_SSL(ELSEWHERE, 465, timeout=2)
        self.assertEqual(session.sock.getpeername()[1], SMTP.port)
        self.assertFalse(hasattr(session.sock, "cipher"))
        session.quit()
        self.assertEqual(smtplib.SMTP_SSL.__module__, "smtplib")

    def test_incoming_is_blocked_without_a_catcher_mailbox(self):
        for fn in (
            lambda: imaplib.IMAP4(ELSEWHERE, 143),
            lambda: imaplib.IMAP4_SSL(ELSEWHERE, 993),
            lambda: poplib.POP3(ELSEWHERE, 110),
            lambda: poplib.POP3_SSL(ELSEWHERE, 995),
        ):
            with self.assertRaises(DevGuardBlocked) as ctx:
                fn()
            self.assertIn("stub_devguard[mail]:", str(ctx.exception))
            self.assertIn("STUB_DEVGUARD_DISABLE=mail", str(ctx.exception))
        self.assertEqual(POP3.log + IMAP.log, [])

    def test_pop3_is_redirected_with_the_catchers_credentials(self):
        with env(MAIL_POP3_ENABLED=1):
            mailbox = poplib.POP3(ELSEWHERE, 110, timeout=2)
            self.assertEqual(mailbox.sock.getpeername()[1], POP3.port)
            mailbox.user("real@prod.test")
            mailbox.pass_("real-password")
            mailbox.quit()
            self.assertEqual(POP3.log[:2], [b"USER stub", b"PASS dev"])
            mailbox = poplib.POP3_SSL(ELSEWHERE, 995, timeout=2)
            self.assertEqual(mailbox.sock.getpeername()[1], POP3.port)
            mailbox.quit()
            with self.assertRaises(DevGuardBlocked):
                imaplib.IMAP4(ELSEWHERE, 143)

    def test_disabling_restores_stock_behaviour(self):
        for var in ({"ENABLED": "0"}, {"DISABLE": "jobs,mail"}):
            with env(**var):
                self.assertFalse(settings().guard_enabled("mail"))
                with self.assertRaises(socket.gaierror):
                    smtplib.SMTP(ELSEWHERE, 25, timeout=2)
                with self.assertRaises(socket.gaierror):
                    imaplib.IMAP4(ELSEWHERE, 143)
                session = smtplib.SMTP("127.0.0.1", SMTP.port, timeout=2)
                self.assertEqual(session.sock.getpeername()[1], SMTP.port)
                with self.assertRaises(smtplib.SMTPNotSupportedError):  # not the fake
                    session.login("real", "secret")
                session.quit()
                imap = imaplib.IMAP4("127.0.0.1", IMAP.port)
                imap.logout()
                mailbox = poplib.POP3("127.0.0.1", POP3.port, timeout=2)
                mailbox.user("real@prod.test")
                mailbox.quit()
        self.assertIn(b"USER real@prod.test", POP3.log)
        self.assertTrue(any(b"CAPABILITY" in line for line in IMAP.log))

    def test_with_the_mail_guard_off_egress_still_refuses_a_real_server(self):
        with env(DISABLE="mail"), self.assertRaises(DevGuardBlocked) as ctx:
            smtplib.SMTP("192.0.2.25", 25, timeout=1)
        self.assertIn("192.0.2.25:25", str(ctx.exception))

    def test_the_banner_says_where_mail_goes(self):
        with unittest.mock.patch.object(_patch, "_ANNOUNCED", set()), stderr() as err:
            smtplib.SMTP(ELSEWHERE, 25, timeout=2).quit()
        self.assertIn(
            f"WARNING stub_devguard[mail] ACTIVE -- ALL outgoing email is redirected to 127.0.0.1:{SMTP.port}, incoming (IMAP/POP3) is blocked",
            err.getvalue(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
