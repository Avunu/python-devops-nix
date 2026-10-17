"""The stub product's job guard: skips a denylisted job by name.

It patches ``stub_framework.scheduler.run``, the stand-in for a framework's
scheduler entry point, through the core's hook and helpers, as a real product's
scheduler or cron guard does.
"""

from .._hook import on_import
from .._patch import is_guarded, mark, no_op, require
from .._settings import settings

NAME = "jobs"

_INSTALLED = False


def _patch(module):
    original = require(module, "run")
    if is_guarded(original):
        return

    def run(job):
        if settings().guard_enabled(NAME) and job in settings().blocked_jobs:
            return no_op(NAME, job, banner="denylisted jobs are skipped")()
        return original(job)

    module.run = mark(run, "stub_framework.scheduler.run")


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("stub_framework.scheduler", _patch)
