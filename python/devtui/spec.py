"""The TUI's spec: what a product tells the shared TUI, as JSON from Nix.

    {
      "product":  "odoo-nix",
      "cli":      "odoo",
      "root":     "@root@",
      "banner":   {… python/banner.py's spec …},
      "site":     {"label": "Odoo", "url": "http://127.0.0.1:@port@", "process": "odoo"},
      "stack":    {"socket": "", "actions": [{"label": "Migrate every database",
                                              "argv": ["odoo", "db", "migrate", "--all"]}]},
      "actions":  [{"label": "Provision the database", "argv": ["odoo", "db", "provision"]}],
      "commands": [{"file": "/nix/store/…-odoo-commands.json"},
                   {"probe": ["/nix/store/…/python", "…"], "cache": "@state@/tui"}],
      "plugin":   {"module": "odoo_nix_cli.tui", "config": {…}}
    }

Every key but "product", "cli" and "banner" is optional. An action is a
command line the TUI runs for the user, from `root`: shown before it runs,
streamed while it does. One marked "interactive" gets the terminal instead (a
REPL, a shell). "confirm" asks first. "after" names what the TUI offers once it
succeeds: "rebuild" (re-evaluate the shell and restart the stack), "restart"
(the stack's processes, as they are) or nothing.

An action whose argv starts with "devtui:" is the TUI's own: devtui:up,
devtui:down, devtui:restart, devtui:open; devtui:rebuild [COMMAND…], which
restarts the stack from a re-evaluated shell and then runs COMMAND there; and
devtui:fresh COMMAND…, which runs COMMAND in a re-evaluated shell
("freshShell", by default `nix develop --impure -c`), for a command that needs
what the shell will be rather than what the TUI started in (the Python
environment after an add).

"stack.socket" is process-compose's unix socket, $PC_SOCKET_PATH when empty;
"stack.up" and "stack.rebuild" are how to start it detached, as they are and
after re-evaluating the shell (devtui.stack has the defaults).

As in the banner, @name@ placeholders in any string are filled when the TUI
starts, and again whenever the stack starts, from `--var NAME=VALUE` and the
`--vars-command`'s output (lib.tui.script), so a value only known at run time
(a leased port) stays current. Parsing is standard library.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import banner
from banner import SpecError

# The banner's validators: one JSON dialect for both specs.
_object = banner._object
_str = banner._str
_bool = banner._bool
_list = banner._list

AFTER = ("", "rebuild", "restart")
FRESH_SHELL = ("nix", "develop", "--impure", "-c")


@dataclass(frozen=True)
class Action:
    label: str
    argv: tuple[str, ...]
    key: str = ""
    help: str = ""
    interactive: bool = False
    confirm: bool = False
    after: str = ""


@dataclass(frozen=True)
class Site:
    url: str
    label: str = "Site"
    process: str = ""


@dataclass(frozen=True)
class Stack:
    socket: str = ""
    up: tuple[str, ...] = ()
    rebuild: tuple[str, ...] = ()
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class CommandSource:
    """A command catalog (devtui.commands): a file built with the shell, or
    a probe run once in the application's environment and cached."""

    file: str = ""
    probe: tuple[str, ...] = ()
    cache: str = ""
    #: Where the probe runs (the spec's root when empty).
    cwd: str = ""


@dataclass(frozen=True)
class Plugin:
    module: str
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    product: str
    cli: str
    banner: banner.Spec
    root: str = ""
    site: Site | None = None
    stack: Stack | None = None
    actions: tuple[Action, ...] = ()
    commands: tuple[CommandSource, ...] = ()
    plugin: Plugin | None = None
    fresh_shell: tuple[str, ...] = FRESH_SHELL


def _argv(value: Any, where: str) -> tuple[str, ...]:
    argv = tuple(
        _str(word, f"{where}[{i}]") for i, word in enumerate(_list(value, where))
    )
    if not argv:
        raise SpecError(f"{where}: empty")
    return argv


def _action(value: Any, where: str) -> Action:
    row = _object(
        value,
        where,
        ["label", "argv"],
        ["key", "help", "interactive", "confirm", "after"],
    )
    after = _str(row.get("after", ""), f"{where}.after")
    if after not in AFTER:
        raise SpecError(f"{where}.after: expected one of {', '.join(map(repr, AFTER))}")
    key = _str(row.get("key", ""), f"{where}.key")
    if len(key) > 1:
        raise SpecError(f"{where}.key: one character, not {key!r}")
    return Action(
        label=_str(row["label"], f"{where}.label"),
        argv=_argv(row["argv"], f"{where}.argv"),
        key=key,
        help=_str(row.get("help", ""), f"{where}.help"),
        interactive=_bool(row.get("interactive", False), f"{where}.interactive"),
        confirm=_bool(row.get("confirm", False), f"{where}.confirm"),
        after=after,
    )


def _actions(value: Any, where: str) -> tuple[Action, ...]:
    return tuple(
        _action(item, f"{where}[{i}]") for i, item in enumerate(_list(value, where))
    )


def _site(value: Any) -> Site | None:
    if value is None:
        return None
    row = _object(value, "site", ["url"], ["label", "process"])
    return Site(
        url=_str(row["url"], "site.url"),
        label=_str(row.get("label", "Site"), "site.label"),
        process=_str(row.get("process", ""), "site.process"),
    )


def _stack(value: Any) -> Stack | None:
    if value is None:
        return None
    row = _object(value, "stack", [], ["socket", "up", "rebuild", "actions"])
    return Stack(
        socket=_str(row.get("socket", ""), "stack.socket"),
        up=_argv(row["up"], "stack.up") if "up" in row else (),
        rebuild=_argv(row["rebuild"], "stack.rebuild") if "rebuild" in row else (),
        actions=_actions(row.get("actions", []), "stack.actions"),
    )


def _source(value: Any, where: str) -> CommandSource:
    row = _object(value, where, [], ["file", "probe", "cache", "cwd"])
    if ("file" in row) == ("probe" in row):
        raise SpecError(f"{where}: one of file or probe")
    return CommandSource(
        file=_str(row.get("file", ""), f"{where}.file"),
        probe=_argv(row["probe"], f"{where}.probe") if "probe" in row else (),
        cache=_str(row.get("cache", ""), f"{where}.cache"),
        cwd=_str(row.get("cwd", ""), f"{where}.cwd"),
    )


def _plugin(value: Any) -> Plugin | None:
    if value is None:
        return None
    row = _object(value, "plugin", ["module"], ["config"])
    config = row.get("config", {})
    if not isinstance(config, dict):
        raise SpecError("plugin.config: expected an object")
    return Plugin(module=_str(row["module"], "plugin.module"), config=config)


def parse(data: Any) -> Spec:
    """A Spec from the decoded JSON, placeholders already filled."""
    top = _object(
        data,
        "spec",
        ["product", "cli", "banner"],
        ["root", "site", "stack", "actions", "commands", "plugin", "freshShell"],
    )
    try:
        parsed_banner = banner.parse(top["banner"])
    except SpecError as exc:
        raise SpecError(f"banner: {exc}") from None
    return Spec(
        product=_str(top["product"], "product"),
        cli=_str(top["cli"], "cli"),
        banner=parsed_banner,
        root=_str(top.get("root", ""), "root"),
        site=_site(top.get("site")),
        stack=_stack(top.get("stack")),
        actions=_actions(top.get("actions", []), "actions"),
        commands=tuple(
            _source(item, f"commands[{i}]")
            for i, item in enumerate(_list(top.get("commands", []), "commands"))
        ),
        plugin=_plugin(top.get("plugin")),
        fresh_shell=_argv(top["freshShell"], "freshShell")
        if "freshShell" in top
        else FRESH_SHELL,
    )


def load(data: Any, variables: Mapping[str, str]) -> Spec:
    """parse() after filling @name@ from `variables`."""
    return parse(banner.substitute(data, variables))


def parse_vars(text: str) -> dict[str, str]:
    """A `--vars-command`'s output: NAME=VALUE lines; others are ignored."""
    out = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and banner.PLACEHOLDER.fullmatch(f"@{name}@"):
            out[name] = value
    return out
