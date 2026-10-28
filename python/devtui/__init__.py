"""A development shell's TUI, shared by both products (docs/library.md,
Development shell): the shell's banner, devenv's processes, every command of
the product's command line, and a store for what the product can add.

    devtui SPEC.json [--vars-command PROGRAM] [--var NAME=VALUE]...

The spec is devtui.spec's, written by the product at evaluation
(lib.tui.script). The TUI is a front end: every change it makes is a command
line of the product's own, shown before it runs. It needs a terminal on
stdin and stdout, and says so otherwise.

Runs on lib.python.textual, isolated (-I): never in, or with, the
application's environment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str], prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="The development shell's TUI."
    )
    parser.add_argument("spec", type=Path, help="the TUI's JSON (devtui.spec)")
    parser.add_argument(
        "--vars-command",
        default="",
        metavar="PROGRAM",
        help="prints NAME=VALUE lines that fill @NAME@; run at start and whenever the stack starts",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="fill @NAME@ in the spec's strings; repeatable",
    )
    args = parser.parse_args(argv)
    variables = {}
    for item in args.var:
        name, sep, value = item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            parser.error(f"--var takes NAME=VALUE, not {item!r}")
        variables[name] = value

    from devtui import spec

    try:
        data = json.loads(args.spec.read_text())
        parsed = spec.load(data, variables)
    except (OSError, ValueError) as exc:
        print(f"{parser.prog}: {args.spec}: {exc}", file=sys.stderr)
        return 1
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            f"{parser.prog}: the menu needs a terminal. Run `{parsed.cli} --help` for the commands.",
            file=sys.stderr,
        )
        return 2

    from devtui.app import DevTui

    app = DevTui(data, variables, vars_command=args.vars_command)
    return app.run() or 0
