"""A stub product's journald graft (lib.journald.mkGraft), as a product ships
one: inert unless stderr is the journal, and patching its framework's
logging (here tests/fixtures/journald/stub_framework.py) as it is imported.
_hook.py and _journald.py are put beside this file by mkGraft."""

import os

from ._journald import JournaldFormatter, journal_stream

_INSTALLED = False


def _tenant(record):
    return os.environ.get("STUB_SITE")


def _patch(module):
    module.handler.setFormatter(JournaldFormatter(tenant=_tenant))


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    if not journal_stream():
        return

    from ._hook import on_import

    on_import("stub_framework", _patch)
