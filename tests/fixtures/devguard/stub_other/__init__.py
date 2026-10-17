"""A second graft (lib.python.mkGraft), with its own copy of the core's _hook.py.

It hooks the same module as the stub product's devguard, so a test can assert
that two registries at ``sys.meta_path[0]`` both fire.
"""

from ._hook import on_import

SEEN = []

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("stub_framework.scheduler", lambda module: SEEN.append(module.__name__))
