"""The stub product's side of the devguard contract (tests/test_devguard.py).

A real product's ``_product.py`` has the same shape: the names the core puts in
its messages, its defaults, its guards, and the two hooks into its framework.
"""

#: Every value of this list is recorded by the ``log`` hook below.
LOGGED = []

#: ``<P>``: the settings variables are STUB_DEVGUARD_*.
ENV_PREFIX = "STUB"

#: The framework and the product, as the core's messages name them.
FRAMEWORK = "Stub"
PRODUCT = "stub-nix"

#: Merged over the core's defaults. ``integrations`` is a product guard's tree;
#: ``pop3_user`` overrides a core default; ``blocked_jobs`` is the product's
#: built-in denylist.
DEFAULTS = {
    "guards": {
        "mail": {"pop3_user": "stub", "sender": "noreply@stub.test"},
        "integrations": {"enable": True, "allow_hosts": []},
        "jobs": {"blocked_jobs": ["stub.backups.offsite", "stub.mail.fetch"]},
    },
}

#: The product's own guards, under guards/, installed after the core's.
GUARDS = ("jobs",)

#: Settings whose entries egress allows too, so one list opens a host to both.
EGRESS_ALLOW = (("integrations", "allow_hosts"),)


class StubUserError(Exception):
    """Stands in for the framework's user-facing error."""


def log(guard, message):
    """The extra log sink: the framework's logger."""
    LOGGED.append((guard, message))


def user_error(guard, message):
    """Raise the framework's user-facing error."""
    raise StubUserError(f"Blocked by stub-devguard: {message}")
