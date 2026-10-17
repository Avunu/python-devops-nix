"""Settings resolution and shared exception types.

Resolution order, highest first, for every setting:

1. ``<P>_DEVGUARD_<GUARD>_<KEY>`` environment variables (``<P>`` is the
   product's ``ENV_PREFIX``), so a single command can be re-pointed or a guard
   turned off without a rebuild;
2. the runtime file, ``$<P>_DEVGUARD_RUNTIME`` or else
   ``$DEVENV_RUNTIME/devguard-runtime.json``, for values only known once the
   development shell's processes have started (the ports the catcher actually
   bound, which devenv's allocator may have moved);
3. the values baked in by Nix (``_baked.json``, written next to this file by
   ``lib.devguard.mkPackage``), so the guards still hold when the development
   shell is absent: an editor terminal, ``nix run``, a stray ``sudo -u``;
4. the defaults: the product's (``_product.DEFAULTS``) over the core's below.

The runtime file sits below the environment and above the baked values on
purpose. Outside the shell ``DEVENV_RUNTIME`` is unset, and the baked values,
which carry this project's own ports, are what remains. Falling back to the
stock 1025 instead would be another project's catcher on a machine running
several: a cross-project mail leak in one of the two guards that offer
transport-level containment.

Settings are read at *call* time rather than frozen at import, so a guard can
be turned off for a single command without a rebuild.

The schema: ``{"enabled": bool, "guards": {<guard>: {"enable": bool, ...}}}``.
The core reads ``enabled``, every guard's ``enable``, ``egress.allow_hosts``,
``mail.{host,port,http_port,pop3_enabled,pop3_port,pop3_user,pop3_password}``
and ``jobs.{blocked_jobs,extra_blocked}``; every other key belongs to the
product's own guards.
"""

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import _product

#: The package's import name, which is also its log prefix: ``<package>[egress]``.
PACKAGE = __package__ or __name__.rpartition(".")[0]

#: ``<P>``: the settings variables are ``<P>_DEVGUARD_*``.
ENV_PREFIX = _product.ENV_PREFIX

try:
    BAKED: dict[str, Any] = json.loads(
        (Path(__file__).parent / "_baked.json").read_text()
    )
except (OSError, ValueError):  # absent in a bare source tree
    BAKED = {}
if not isinstance(BAKED, dict):
    BAKED = {}

#: The core's defaults. The product's DEFAULTS are merged over them, key by key.
CORE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "guards": {
        # The catch-all: every non-loopback connect() from this process.
        "egress": {"enable": True, "allow_hosts": []},
        # The mail transport (guards/mail_stdlib.py) and the catcher it points
        # at. The product's own mail guard reads the same tree.
        "mail": {
            "enable": True,
            "host": "127.0.0.1",
            "port": 1025,
            "http_port": 8025,
            "pop3_enabled": False,
            "pop3_port": 1110,
            "pop3_user": "dev",
            "pop3_password": "dev",
        },
        # The job denylist, which the product's scheduler (or cron) guard
        # enforces. Matched exact-string by the product; blocked_jobs is the
        # product's built-in list, extra_blocked the consumer's additions.
        "jobs": {"enable": True, "blocked_jobs": [], "extra_blocked": []},
    },
}


def _merge(base, over):
    merged = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


DEFAULTS: dict[str, Any] = _merge(CORE_DEFAULTS, getattr(_product, "DEFAULTS", {}))

_TRUTHY = {"1", "true", "yes", "on", "t", "y"}
_FALSY = {"0", "false", "no", "off", "f", "n", ""}


class DevGuardBlocked(OSError):
    """Raised when a guard refuses an action that would reach production.

    Subclasses ``OSError`` so callers that already handle "the remote service
    is unreachable" degrade the way they would for a network failure.
    """


class DevGuardPatchError(RuntimeError):
    """A patch target went missing: the framework's internals moved.

    Raised from inside the post-import hook so the offending ``import`` fails
    loudly instead of leaving a silently inert guard behind.
    """


def _as_bool(value, fallback):
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return fallback


def warn(message):
    sys.stderr.write(f"WARNING {PACKAGE} {message}\n")


def _as_int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        warn(f"invalid integer {value!r}; falling back to {fallback!r}")
        return fallback


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return list(value)
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def env_name(*parts):
    """The environment variable for a setting: env_name("mail", "port")."""
    return "_".join([ENV_PREFIX, "DEVGUARD", *parts]).upper()


def _env(*parts):
    value = os.environ.get(env_name(*parts))
    return value if value is not None and value.strip() != "" else None


def _runtime_path():
    explicit = os.environ.get(env_name("runtime"))
    if explicit:
        return explicit
    runtime = os.environ.get("DEVENV_RUNTIME")
    return os.path.join(runtime, "devguard-runtime.json") if runtime else None


#: Keyed on the resolved path, so a `devenv up` that rewrites the file under a
#: long-lived worker still takes effect on the next process, while
#: Settings.get(), called on every guard invocation by design, does not stat
#: and parse JSON each time.
_RUNTIME_CACHE: dict[str, Any] = {}


def _runtime():
    path = _runtime_path()
    if path is None:
        return {}
    if path in _RUNTIME_CACHE:
        return _RUNTIME_CACHE[path]
    try:
        with open(path) as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            loaded = {}
    except (OSError, ValueError):
        # Absent outside devenv, and unreadable is not worth failing a guard
        # over: the baked values below are a safe fallback by construction.
        loaded = {}
    _RUNTIME_CACHE[path] = loaded
    return loaded


def _layered(guard, key):
    """The runtime file, the baked values and the defaults, in that order."""
    for layer in (_runtime(), BAKED, DEFAULTS):
        guards = layer.get("guards")
        entry = guards.get(guard) if isinstance(guards, dict) else None
        value = entry.get(key) if isinstance(entry, dict) else None
        if value is not None:
            return value
    return None


class Settings:
    """Lazily-resolved view of the guard configuration."""

    __slots__ = ()

    @property
    def enabled(self):
        value = _env("enabled")
        for layer in (_runtime(), BAKED):
            if value is None:
                value = layer.get("enabled")
        return _as_bool(value, DEFAULTS["enabled"])

    @property
    def disabled_guards(self):
        """Guards switched off for this process by ``<P>_DEVGUARD_DISABLE``."""
        return {name.lower() for name in _as_list(_env("disable"))}

    def guard_enabled(self, guard):
        if not self.enabled or guard in self.disabled_guards:
            return False
        return _as_bool(self.get(guard, "enable"), True)

    def get(self, guard, key, cast=None):
        """Resolve one setting: env, then runtime file, then baked, then default."""
        value = _env(guard, key)
        if value is None:
            value = _layered(guard, key)
        return cast(value) if cast is not None and value is not None else value

    def flag(self, guard, key, fallback=True):
        return _as_bool(self.get(guard, key), fallback)

    def number(self, guard, key, fallback=0):
        return _as_int(self.get(guard, key), fallback)

    def text(self, guard, key, fallback=""):
        value = self.get(guard, key)
        return fallback if value is None else str(value)

    def items(self, guard, key):
        return _as_list(self.get(guard, key))

    # -- mail, which has enough settings to deserve named accessors ---------

    @property
    def mail_host(self):
        return self.text("mail", "host", "127.0.0.1")

    @property
    def mail_port(self):
        return self.number("mail", "port", 1025)

    @property
    def mail_http_port(self):
        return self.number("mail", "http_port", 8025)

    @property
    def pop3_enabled(self):
        return self.flag("mail", "pop3_enabled", False)

    @property
    def pop3_port(self):
        return self.number("mail", "pop3_port", 1110)

    @property
    def pop3_user(self):
        return self.text("mail", "pop3_user", "dev")

    @property
    def pop3_password(self):
        return self.text("mail", "pop3_password", "dev")

    @property
    def block_incoming(self):
        """Incoming mail is blocked unless it has somewhere safe to point."""
        return not self.pop3_enabled

    # -- jobs ---------------------------------------------------------------

    @property
    def blocked_jobs(self):
        """The product's built-in denylist and the consumer's additions, in order."""
        jobs = self.items("jobs", "blocked_jobs") + self.items("jobs", "extra_blocked")
        return list(dict.fromkeys(jobs))


_SETTINGS = Settings()


def settings():
    return _SETTINGS
