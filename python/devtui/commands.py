"""Command catalogs: a product's command tree, as the TUI's Commands tab
browses it, and the command line a filled-in form makes.

A catalog is JSON, one object per command, nested by subcommand:

    {"name": "odoo", "path": [], "help": "…", "source": "odoo-nix",
     "commands": [{"name": "db", "path": ["db"], "help": "…", "commands": [
        {"name": "migrate", "path": ["db", "migrate"], "help": "…",
         "params": [{"name": "all_", "kind": "option", "opts": ["--all"],
                     "type": "bool", "flag": true, "help": "…"}]}]}]}

It is made from a click group with `dump` (click's own Context.to_info_dict()),
either when the shell is built (lib.tui.commands, for the product's command
line) or by a probe the TUI runs once in the application's environment and
caches, keyed by the probe's command line, which names the environment's store
path (for the framework's own commands, which take the framework to import).
A command that takes its arguments unparsed (an argument with click.UNPROCESSED
and nargs=-1) is "raw": the form is one line of arguments.

This module is standard library, and click only inside `dump`, so a probe can
run it as a script with the application's interpreter:

    python …/devtui/commands.py dump frappe.utils.bench_helper:get_app_groups \
        --name bench --source frappe
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # option | argument
    opts: tuple[str, ...] = ()
    secondary: tuple[str, ...] = ()
    type: str = "text"
    choices: tuple[str, ...] = ()
    required: bool = False
    multiple: bool = False
    flag: bool = False
    default: str = ""
    help: str = ""

    @property
    def label(self) -> str:
        if self.kind == "argument":
            return self.name.upper()
        return max(self.opts, key=len) if self.opts else self.name


@dataclass(frozen=True)
class Command:
    name: str
    path: tuple[str, ...] = ()
    help: str = ""
    short: str = ""
    params: tuple[Param, ...] = ()
    commands: tuple[Command, ...] = ()
    raw: bool = False
    interactive: bool = False
    source: str = ""

    @property
    def summary(self) -> str:
        return self.short or (self.help.strip().splitlines() or [""])[0]

    def walk(self) -> Iterator[Command]:
        yield self
        for child in self.commands:
            yield from child.walk()


def param_from_json(data: Mapping[str, Any]) -> Param:
    return Param(
        name=str(data["name"]),
        kind=str(data.get("kind", "option")),
        opts=tuple(data.get("opts", ())),
        secondary=tuple(data.get("secondary", ())),
        type=str(data.get("type", "text")),
        choices=tuple(data.get("choices", ())),
        required=bool(data.get("required", False)),
        multiple=bool(data.get("multiple", False)),
        flag=bool(data.get("flag", False)),
        default=str(data.get("default", "")),
        help=str(data.get("help", "")),
    )


def from_json(data: Mapping[str, Any], source: str = "") -> Command:
    source = str(data.get("source", source))
    return Command(
        name=str(data["name"]),
        path=tuple(data.get("path", ())),
        help=str(data.get("help", "")),
        short=str(data.get("short", "")),
        params=tuple(param_from_json(p) for p in data.get("params", ())),
        commands=tuple(from_json(c, source) for c in data.get("commands", ())),
        raw=bool(data.get("raw", False)),
        interactive=bool(data.get("interactive", False)),
        source=source,
    )


def to_json(command: Command) -> dict[str, Any]:
    out: dict[str, Any] = {"name": command.name, "path": list(command.path)}
    for key in ("help", "short", "source"):
        if getattr(command, key):
            out[key] = getattr(command, key)
    for key in ("raw", "interactive"):
        if getattr(command, key):
            out[key] = True
    if command.params:
        out["params"] = [
            {
                k: (list(v) if isinstance(v, tuple) else v)
                for k, v in vars(p).items()
                if v not in ("", (), False) or k in ("name", "kind")
            }
            for p in command.params
        ]
    if command.commands:
        out["commands"] = [to_json(c) for c in command.commands]
    return out


# -- argv ---------------------------------------------------------------------


def argv(cli: str, command: Command, values: Mapping[str, Any]) -> list[str]:
    """The command line for `command` with the form's `values`: options in
    declaration order, then the arguments. A value is a str, a bool (a flag),
    or, for a param that takes several, a str of shell words. A raw command's
    arguments are values["args"], split as the shell would."""
    out = [cli, *command.path]
    if command.raw:
        return out + shlex.split(str(values.get("args", "") or ""))
    args: list[str] = []
    for param in command.params:
        value = values.get(param.name)
        if param.flag:
            # Only a flag that differs from its default is spelled out.
            if not isinstance(value, bool) or value == _default_true(param):
                continue
            if value and param.opts:
                out.append(param.opts[0])
            elif not value and param.secondary:
                out.append(param.secondary[0])
            continue
        if value is None or value == "":
            continue
        words = shlex.split(str(value)) if param.multiple else [str(value)]
        if param.kind == "argument":
            args.extend(words)
        else:
            for word in words:
                out.extend([param.opts[0], word])
    return out + args


def _default_true(param: Param) -> bool:
    return param.default.lower() in ("true", "1")


def missing(command: Command, values: Mapping[str, Any]) -> list[str]:
    """The required params with no value."""
    if command.raw:
        return []
    return [
        p.label
        for p in command.params
        if p.required and not p.flag and values.get(p.name) in (None, "")
    ]


# -- dump: click -> catalog -----------------------------------------------------


def _type(param: Any) -> tuple[str, tuple[str, ...]]:
    import click

    t = param.type
    if isinstance(t, click.Choice):
        return "choice", tuple(str(c) for c in t.choices)
    if isinstance(t, click.types.BoolParamType):
        return "bool", ()
    if isinstance(t, (click.types.IntParamType, click.IntRange)):
        return "int", ()
    if isinstance(t, (click.types.FloatParamType, click.FloatRange)):
        return "float", ()
    if isinstance(t, (click.Path, click.File)):
        return "path", ()
    return "text", ()


def _default(value: Any) -> str:
    # click 8.3 marks "no default" with a sentinel, not None.
    if value is None or callable(value) or type(value).__name__ == "Sentinel":
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _raw(cmd: Any) -> bool:
    import click

    return any(
        isinstance(p, click.Argument) and p.nargs == -1 and p.type is click.UNPROCESSED
        for p in cmd.params
    )


def dump(
    cmd: Any,
    name: str,
    *,
    path: Sequence[str] = (),
    source: str = "",
    interactive: Sequence[str] = (),
) -> Command:
    """The catalog for a click command or group. `interactive` names the
    commands (space-joined paths) that need the terminal."""
    import click

    ctx = click.Context(cmd, info_name=name)
    params = []
    for p in cmd.params:
        if getattr(p, "hidden", False) or p.name in (None, "help"):
            continue
        kind, choices = _type(p)
        is_flag = bool(getattr(p, "is_flag", False))
        params.append(
            Param(
                name=p.name,
                kind="argument" if isinstance(p, click.Argument) else "option",
                opts=tuple(p.opts),
                secondary=tuple(p.secondary_opts),
                type="bool" if is_flag else kind,
                choices=choices,
                required=bool(p.required),
                multiple=bool(p.multiple or p.nargs == -1),
                flag=is_flag,
                default=_default(p.default),
                help=str(getattr(p, "help", "") or ""),
            )
        )
    children = []
    if isinstance(cmd, click.Group):
        for child_name in cmd.list_commands(ctx):
            child = cmd.get_command(ctx, child_name)
            if child is None or child.hidden:
                continue
            children.append(
                dump(
                    child,
                    child_name,
                    path=(*path, child_name),
                    source=source,
                    interactive=interactive,
                )
            )
    return Command(
        name=name,
        path=tuple(path),
        help=(cmd.help or "").strip(),
        short=(cmd.short_help or "").strip(),
        params=tuple(params),
        commands=tuple(children),
        raw=_raw(cmd),
        interactive=" ".join(path) in interactive,
        source=source,
    )


def _resolve(target: str) -> Any:
    """`module:attr` (an attribute, or a callable that returns the group, or
    a list of commands, or a {name: command} mapping, to gather under one). A
    mapping of one group is that group (Frappe's get_app_groups())."""
    import click

    module, _, attr = target.partition(":")
    obj = importlib.import_module(module)
    for part in attr.split(".") if attr else ():
        obj = getattr(obj, part)
    if callable(obj) and not isinstance(obj, click.Command):
        obj = obj()
    if isinstance(obj, dict):
        values = list(obj.values())
        obj = (
            values[0]
            if len(values) == 1 and isinstance(values[0], click.Group)
            else values
        )
    if isinstance(obj, (list, tuple)):
        group = click.Group()
        for command in obj:
            group.add_command(command)
        obj = group
    return obj


# -- loading a catalog, cached for a probe ------------------------------------

#: The line `dump --marked` writes before the JSON: a probe imports the
#: framework, which may print to stdout first.
MARKER = "-----devtui-commands-----"


def cache_file(cache_dir: str, probe: Sequence[str]) -> Path:
    key = hashlib.sha256("\0".join(probe).encode()).hexdigest()[:20]
    return Path(cache_dir) / f"commands-{key}.json"


def load_file(path: str | os.PathLike[str]) -> Command:
    return from_json(json.loads(Path(path).read_text()))


def run_probe(
    probe: Sequence[str], cache_dir: str, *, cwd: str | None = None
) -> Command:
    """The probe's catalog, from the cache when this probe (and so this
    environment) has run before."""
    cached = cache_file(cache_dir, probe) if cache_dir else None
    if cached is not None and cached.is_file():
        try:
            return load_file(cached)
        except (OSError, ValueError, KeyError):
            pass
    result = subprocess.run(
        list(probe),
        cwd=cwd or None,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise RuntimeError(f"{shlex.join(probe)} exited {result.returncode}: {tail}")
    out = result.stdout
    if MARKER in out:
        out = out.split(MARKER, 1)[1]
    data = json.loads(out)
    command = from_json(data)
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(cached)
        except OSError:
            pass
    return command


def main(argv_: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="devtui-commands", description="Write a click group's command catalog."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    d = sub.add_parser("dump", help="the catalog of module:attr, as JSON on stdout")
    d.add_argument("target", help="module:attr, a click group or command")
    d.add_argument("--name", required=True, help="the command line's name")
    d.add_argument("--source", default="", help="whose commands these are")
    d.add_argument("--path", default="", help="the group's own path, space-separated")
    d.add_argument(
        "--marked",
        action="store_true",
        help=f"write {MARKER} on a line before the JSON",
    )
    d.add_argument(
        "--interactive",
        action="append",
        default=[],
        metavar="PATH",
        help="a command that needs the terminal (space-separated path); repeatable",
    )
    args = parser.parse_args(argv_)
    command = dump(
        _resolve(args.target),
        args.name,
        path=args.path.split(),
        source=args.source,
        interactive=args.interactive,
    )
    if args.marked:
        sys.stdout.write(MARKER + "\n")
    json.dump(to_json(command), sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    # Run as a script, this directory is first on sys.path, and its app.py,
    # spec.py, run.py, … would shadow any module of those names the target
    # imports.
    here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != here]
    sys.exit(main())
