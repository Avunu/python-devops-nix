"""The stub product's devguard grafted into a virtualenv, as products ship it.

Run: python3 -I tests/devguard_graft.py <site-packages>

<site-packages> is a virtualenv's, after lib.python.withGrafts put the stub's
devguard (lib.devguard.mkPackage) and a second graft (lib.python.mkGraft) in
it. ``site.addsitedir`` processes its .pth files the way interpreter start-up
does, so this checks what a product relies on: the .pth file names and lines,
both grafts installed from them with their hooks coexisting, the baked
settings reaching the guard, and the log line a refusal writes.
"""

import contextlib
import io
import os
import site
import socket
import sys
import tempfile

SITE = os.path.abspath(sys.argv[1])
FAILURES = []


def check(label, condition, detail=""):
    print(
        f"{'ok  ' if condition else 'FAIL'} {label}"
        + ("" if condition else f": {detail}")
    )
    if not condition:
        FAILURES.append(label)


for key in [k for k in os.environ if k.startswith("STUB_DEVGUARD_")]:
    del os.environ[key]
os.environ.pop("DEVENV_RUNTIME", None)

pths = sorted(name for name in os.listdir(SITE) if name.endswith(".pth"))
check(
    ".pth files are named zzz-<module>",
    pths == ["zzz-stub-devguard.pth", "zzz-stub-other.pth"],
    pths,
)
with open(os.path.join(SITE, "zzz-stub-devguard.pth")) as handle:
    line = handle.read()
check(
    "and import then install their module",
    line == "import stub_devguard; stub_devguard.install()\n",
    repr(line),
)

# A framework module on the path before start-up: the hooks fire when it is
# imported, after the .pth files ran.
framework = tempfile.mkdtemp()
os.makedirs(os.path.join(framework, "stub_framework"))
open(os.path.join(framework, "stub_framework", "__init__.py"), "w").close()
with open(os.path.join(framework, "stub_framework", "scheduler.py"), "w") as handle:
    handle.write("def run(job):\n    return 'ran'\n")
sys.path.insert(0, framework)

site.addsitedir(SITE)

check(
    "both grafts were installed at start-up",
    {"stub_devguard", "stub_other"} <= set(sys.modules),
)

import stub_devguard
import stub_other

registries = [
    sys.modules["stub_devguard._hook"]._REGISTRY,
    sys.modules["stub_other._hook"]._REGISTRY,
]
check(
    "each graft's own hook is at the front of sys.meta_path",
    all(registry in sys.meta_path[:2] for registry in registries)
    and registries[0] is not registries[1],
    sys.meta_path[:3],
)

from stub_framework import scheduler

check(
    "the second graft's hook fired",
    stub_other.SEEN == ["stub_framework.scheduler"],
    stub_other.SEEN,
)
check(
    "and so did devguard's",
    stub_devguard.status().get("stub_framework.scheduler.run") == "patched",
)
with contextlib.redirect_stderr(io.StringIO()):
    check("a baked denylist entry is skipped", scheduler.run("stub.sync.push") is None)
check("an unlisted job runs", scheduler.run("stub.report") == "ran")

st = stub_devguard.settings()
check("baked settings reach the guard", st.mail_port == 2525, st.mail_port)
check(
    "and untouched keys keep their defaults", st.mail_host == "127.0.0.1", st.mail_host
)

err = io.StringIO()
with contextlib.redirect_stderr(err):
    sock = socket.socket()
    try:
        sock.connect(("192.0.2.1", 9))
    except stub_devguard.DevGuardBlocked as exc:
        refusal = str(exc)
    else:
        refusal = ""
    finally:
        sock.close()
lines = err.getvalue().splitlines()
check(
    "a refusal raises <package>[egress]",
    refusal == "stub_devguard[egress]: blocked connection to 192.0.2.1:9",
    refusal,
)
check(
    "and logs the banner and the refusal under <package>[egress]",
    len(lines) == 2
    and lines[0].startswith(
        "WARNING stub_devguard[egress] ACTIVE -- outbound connections"
    )
    and lines[1] == "WARNING stub_devguard[egress] blocked connection to 192.0.2.1:9",
    lines,
)

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("the graft checks passed")
