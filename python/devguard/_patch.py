"""Shared patching helpers used by every guard.

Nothing here imports the framework at module scope: guards run from a
post-import hook, and ``install()`` runs from a ``.pth`` file before the
framework is importable. Helpers that need the framework (a desk-facing error,
an extra log sink) reach it through the product's hooks in ``_product.py``.
"""

import contextlib
import sys

from . import _product
from ._settings import PACKAGE, DevGuardBlocked, DevGuardPatchError, env_name

#: target -> "patched". Lets a test, or a developer, see which targets a
#: process actually reached; a module that is never imported never reaches its
#: post-import callback, which is fail-safe but otherwise invisible.
STATUS: dict[str, str] = {}

_ANNOUNCED: set[str] = set()


def warn(guard, message):
    """Announce on stderr, and to the product's log sink if it has one.

    Raising alone is not enough: scheduler and mail paths catch Exception and
    turn it into a log line or a "failed" record nobody ties back to the
    guard. stderr rather than ``logging``, because guards run before the
    framework configures its handlers.
    """
    sys.stderr.write(f"WARNING {PACKAGE}[{guard}] {message}\n")
    sink = getattr(_product, "log", None)
    if sink is not None:
        # Logging must never be the thing that fails.
        with contextlib.suppress(Exception):
            sink(guard, message)


def announce(guard, detail):
    """Emit a guard's activation banner once per process, on first use.

    Deferred rather than printed at install, so the many Python processes a
    development shell starts which never reach a guarded code path stay quiet.
    """
    if guard in _ANNOUNCED:
        return
    _ANNOUNCED.add(guard)
    warn(guard, f"ACTIVE -- {detail}")


def blocked(guard, message):
    """The exception a refusal raises, without announcing it."""
    return DevGuardBlocked(f"{PACKAGE}[{guard}]: {message}")


def block(guard, message):
    """Announce and raise. For code paths where failing is the right answer."""
    warn(guard, message)
    raise blocked(guard, message)


def user_error(guard, message):
    """Announce and surface the message to the person who asked.

    For deliberate user actions (a button, an API call) where a silent no-op
    would leave someone believing the action happened. The product's
    ``user_error`` hook raises the framework's own error, which its UI shows;
    without one, or if it returns, this raises ``DevGuardBlocked``.
    """
    warn(guard, message)
    hook = getattr(_product, "user_error", None)
    if hook is not None:
        hook(guard, message)
    raise blocked(guard, message)


def mark(fn, target):
    """Tag a replacement so it is identifiable after ``functools.wraps``."""
    fn.__devguard__ = target
    return fn


def is_guarded(fn):
    return getattr(fn, "__devguard__", None) is not None


def no_op(guard, what, banner=None):
    """Replacement that logs and returns None.

    For scheduler entry points, where manufacturing a failure would only fill a
    job log with noise nobody reads.
    """

    def replacement(*_args, **_kwargs):
        if banner:
            announce(guard, banner)
        warn(guard, f"skipping {what}")

    return mark(replacement, what)


def blocking(guard, what, message, banner=None):
    """Replacement that logs and raises. For the funnels that must not proceed."""

    def replacement(*_args, **_kwargs):
        if banner:
            announce(guard, banner)
        block(guard, f"{what}: {message}")

    return mark(replacement, what)


def _moved(label, what):
    return DevGuardPatchError(
        f"{PACKAGE}: {label} {what} -- {_product.FRAMEWORK}'s internals have moved "
        "and this guard can no longer be relied on. Update "
        f"{_product.PRODUCT}, or set {env_name('enabled')}=0 to run without it."
    )


def _label(owner):
    return getattr(owner, "__qualname__", None) or getattr(owner, "__name__", owner)


def require(owner, name):
    """Fetch a patch target, or fail the import loudly."""
    value = getattr(owner, name, None)
    if value is None:
        raise _moved(f"{_label(owner)}.{name}", "is missing")
    STATUS[f"{getattr(owner, '__name__', owner)}.{name}"] = "patched"
    return value


def require_any(owner, *names):
    """Fetch the first surviving spelling of a renamed target, or fail loudly.

    ``require`` is right for an internal with one name. This is for one the
    framework renamed across the series the product's presets span: any known
    spelling will do, but none of them surviving still has to be loud. Returns
    the name as well as the value, so the caller patches back the spelling this
    framework actually uses.
    """
    for name in names:
        value = getattr(owner, name, None)
        if value is not None:
            STATUS[f"{getattr(owner, '__name__', owner)}.{name}"] = "patched"
            return name, value
    raise _moved(_label(owner), f"has none of {', '.join(names)}")
