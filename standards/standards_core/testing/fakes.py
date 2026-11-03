"""Fakes and helpers for product test suites: a fake GitHub, an in-process CLI run, git.

``FakeGitHub`` replaces ``standards_core.common.github``'s transport
(``github.use(fake)``), so no test needs the network or ``gh``. ``run_cli`` runs a
product's command line in-process and captures its output. ``git`` runs git in a
throwaway repository as a fixed user, with no detached maintenance.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.common import github
from standards_core.common.repo import NO_AUTO_MAINTENANCE

if TYPE_CHECKING:
    from standards_core.product import Product


class FakeGitHub(github.Transport):
    """Answers from ``routes`` (``(method, endpoint)`` → a value, an int status to fail
    with, or a function of the request body returning either); an unknown GET is a 404,
    an unknown write succeeds with ``{}``. ``calls`` records every call."""

    def __init__(self, routes: dict | None = None, cli_results: dict | None = None):
        self.routes: dict[tuple[str, str], Any] = dict(routes or {})
        self.cli_results: dict[tuple[str, ...], github.Result] = dict(cli_results or {})
        self.calls: list[tuple[str, str, Any]] = []
        self.cli_calls: list[tuple[str, ...]] = []

    def request(self, method, endpoint, body=None, paginate=False):
        self.calls.append((method, endpoint, body))
        key = (method, endpoint)
        if key in self.routes:
            value = self.routes[key]
            if callable(value):
                value = value(body)
            if isinstance(value, int) and not isinstance(value, bool):
                raise github.GitHubError(
                    f"gh api {method} {endpoint}: HTTP {value}", value
                )
            return 200, value
        if method == "GET":
            raise github.GitHubError(
                f"gh api GET {endpoint}: Not Found (HTTP 404)", 404
            )
        return 201, {}

    def cli(self, *args):
        self.cli_calls.append(args)
        return self.cli_results.get(args[:2], github.Result(0, ""))

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


def run_cli(
    product: Product,
    *argv: str,
    cwd: Path | None = None,
    main: Callable[..., int] | None = None,
) -> tuple[int, str, str]:
    """Run ``<cli> <argv>`` in-process; returns (exit code, stdout, stderr).

    ``main`` defaults to ``standards_core.cli.main``; a product passes its own entry point
    when it adds commands.
    """

    def entry(args: list[str]) -> int:
        if main is not None:
            return main(args)
        from standards_core import cli

        return cli.main(product, args)

    out, err = io.StringIO(), io.StringIO()
    old = Path.cwd()
    try:
        if cwd:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = entry(list(argv))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


def git(root: Path, *args: str) -> str:
    """``git -C root <args>`` as user t, returning stdout. The repositories are temporary:
    no detached auto maintenance still writing in one when its directory is removed."""
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            *NO_AUTO_MAINTENANCE,
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
