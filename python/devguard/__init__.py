"""Guard rails for a development copy of production.

Loaded at interpreter start from ``zzz-<package>.pth`` inside the development
virtualenv (``lib.python.withGrafts``), that is *below* the framework. Every
interpreter started from that environment is covered, without installing
anything into the application and without editing its configuration.

A development copy restored from production carries working production
credentials. Left alone it will mail real customers, push a mutated database
over the production backup rotation, delete production files and call every
integration it has a key for. Each guard closes one of those routes.

Two layers, deliberately:

transport level, here
    ``egress`` refuses every non-loopback ``connect()`` this process makes,
    whatever library asked, and ``mail_stdlib`` points ``smtplib`` at the mail
    catcher and refuses ``imaplib`` and ``poplib``. Neither knows anything
    about the framework, so both hold across upgrades, third-party code and
    code nobody has read.
the product's guards
    Framework APIs, patched so the failure is readable (a job skipped by name,
    a backup button that says why) rather than a socket error three frames
    down. These are one refactor away from being bypassed; the fail-closed
    :func:`_patch.require` makes such drift loud, which is not the same
    guarantee, and is why ``egress`` exists.

Guards are installed unconditionally and consult :func:`settings` at call time,
so ``<P>_DEVGUARD_ENABLED=0`` (everything) or ``<P>_DEVGUARD_DISABLE=egress,mail``
(named guards) gives stock behaviour for a single command.

This tree is the core, shared by every product (python-devops-nix,
``python/devguard``). ``lib.devguard.mkPackage`` merges it with the product's
own tree, which supplies ``_product.py`` and the guards that patch its
framework, and writes ``_baked.json``. See ``_product.py`` in the product for
what it provides.
"""

import importlib

from . import _product
from ._settings import DevGuardBlocked, DevGuardPatchError, settings

__all__ = [
    "DevGuardBlocked",
    "DevGuardPatchError",
    "install",
    "settings",
    "status",
]

#: The core's guards, installed first and in this order: ``egress`` is the one
#: that holds when the others have drifted.
CORE_GUARDS = ("egress", "mail_stdlib")

_INSTALLED = False


def all_guards():
    """Every guard module, the core's then the product's (``_product.GUARDS``)."""
    names = CORE_GUARDS + tuple(getattr(_product, "GUARDS", ()))
    return [importlib.import_module(f".guards.{name}", __name__) for name in names]


def install():
    """Install every enabled guard. Idempotent; safe to call from anywhere."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    for guard in all_guards():
        if settings().guard_enabled(guard.NAME):
            guard.install()


def status():
    """Per-target report of what has been patched so far."""
    from ._patch import STATUS

    return dict(STATUS)


# NB: install() is called by the .pth bootstrap, not here. Importing the guard
# modules from this module's body would make `import <package>.guards.X`
# re-enter a partially initialised package.
