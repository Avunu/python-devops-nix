"""A development shell's welcome banner, printed once on enterShell.

One renderer for both products (PLAN.md, Phase 5), in place of frappe-nix's
flags and odoo-nix's nine positional arguments. Everything it shows comes from
one JSON file the product writes at evaluation:

    {
      "title":    "acme — Frappe Bench dev environment",
      "commands": [{"command": "devenv up", "description": "start all services"}],
      "urls":     [{"label": "URL", "url": "http://127.0.0.1:@port@"}],
      "facts":    [{"label": "Python", "value": "/nix/store/…/bin/python"},
                   {"label": "App", "value": ["apps/acme → this repository",
                                              "the bench is generated"]}],
      "guard":    {"enable": true,
                   "egress": {"enable": true, "allow": ["api.example.com"]},
                   "mail": {"enable": true, "url": "http://127.0.0.1:20223",
                            "incoming": "blocked"}},
      "notes":    [{"text": "frappe-runtime is not in this shell", "style": "yellow"}]
    }

Every key but "title" is optional. A fact's value is a line or a list of
lines, the rest aligned under the first. "guard" is the devguard state: off
prints the warning that nothing stops a restored database from reaching
production; on, the egress and mail lines for the guards that are on.

A fact the product only knows when the shell starts (a port read back from a
config file, $DEVENV_RUNTIME) is an @name@ placeholder in any string, filled
from `--var name=value`. Placeholders with no --var are left as they are.

Nothing in the JSON is rich markup: every string is printed literally, so a
"[db]" in a command is not taken for a tag. Rendering takes rich, from the
product's tool interpreter (lib.python.rich), never the application's
environment. Parsing is standard library, so a malformed spec fails before rich
is imported.
"""

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)@")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    command: str
    description: str


@dataclass(frozen=True)
class Url:
    label: str
    url: str


@dataclass(frozen=True)
class Fact:
    label: str
    lines: tuple[str, ...]
    style: str = ""


@dataclass(frozen=True)
class Note:
    text: str
    style: str = ""


@dataclass(frozen=True)
class Guard:
    enable: bool = True
    egress: bool = False
    allow: tuple[str, ...] = ()
    mail: bool = False
    mail_url: str = ""
    incoming: str = ""


@dataclass(frozen=True)
class Spec:
    title: str
    commands: tuple[Command, ...] = ()
    urls: tuple[Url, ...] = ()
    facts: tuple[Fact, ...] = ()
    guard: Guard | None = None
    notes: tuple[Note, ...] = field(default_factory=tuple)


def _object(
    value: Any, where: str, required: Sequence[str], optional: Sequence[str]
) -> dict:
    if not isinstance(value, dict):
        raise SpecError(f"{where}: expected an object, got {type(value).__name__}")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise SpecError(f"{where}: unknown key(s): {', '.join(unknown)}")
    missing = [key for key in required if key not in value]
    if missing:
        raise SpecError(f"{where}: missing key(s): {', '.join(missing)}")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{where}: expected a string, got {type(value).__name__}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{where}: expected true or false, got {type(value).__name__}")
    return value


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise SpecError(f"{where}: expected a list, got {type(value).__name__}")
    return value


def _guard(value: Any) -> Guard | None:
    if value is None:
        return None
    data = _object(value, "guard", ["enable"], ["egress", "mail"])
    egress = _object(data.get("egress") or {}, "guard.egress", [], ["enable", "allow"])
    mail = _object(
        data.get("mail") or {}, "guard.mail", [], ["enable", "url", "incoming"]
    )
    return Guard(
        enable=_bool(data["enable"], "guard.enable"),
        egress=_bool(egress.get("enable", False), "guard.egress.enable"),
        allow=tuple(
            _str(host, f"guard.egress.allow[{i}]")
            for i, host in enumerate(
                _list(egress.get("allow", []), "guard.egress.allow")
            )
        ),
        mail=_bool(mail.get("enable", False), "guard.mail.enable"),
        mail_url=_str(mail.get("url", ""), "guard.mail.url"),
        incoming=_str(mail.get("incoming") or "", "guard.mail.incoming"),
    )


def parse(data: Any) -> Spec:
    """A Spec from the decoded JSON, failing on an unknown or missing key."""
    top = _object(
        data, "spec", ["title"], ["commands", "urls", "facts", "guard", "notes"]
    )
    commands = []
    for i, item in enumerate(_list(top.get("commands", []), "commands")):
        row = _object(item, f"commands[{i}]", ["command", "description"], [])
        commands.append(
            Command(
                _str(row["command"], f"commands[{i}].command"),
                _str(row["description"], f"commands[{i}].description"),
            )
        )
    urls = []
    for i, item in enumerate(_list(top.get("urls", []), "urls")):
        row = _object(item, f"urls[{i}]", ["label", "url"], [])
        urls.append(
            Url(
                _str(row["label"], f"urls[{i}].label"),
                _str(row["url"], f"urls[{i}].url"),
            )
        )
    facts = []
    for i, item in enumerate(_list(top.get("facts", []), "facts")):
        row = _object(item, f"facts[{i}]", ["label", "value"], ["style"])
        value = row["value"]
        lines = (
            (value,)
            if isinstance(value, str)
            else tuple(
                _str(line, f"facts[{i}].value")
                for line in _list(value, f"facts[{i}].value")
            )
        )
        if not lines:
            raise SpecError(f"facts[{i}].value: empty")
        facts.append(
            Fact(
                _str(row["label"], f"facts[{i}].label"),
                lines,
                _str(row.get("style", ""), f"facts[{i}].style"),
            )
        )
    notes = []
    for i, item in enumerate(_list(top.get("notes", []), "notes")):
        row = _object(item, f"notes[{i}]", ["text"], ["style"])
        notes.append(
            Note(
                _str(row["text"], f"notes[{i}].text"),
                _str(row.get("style", ""), f"notes[{i}].style"),
            )
        )
    return Spec(
        title=_str(top["title"], "title"),
        commands=tuple(commands),
        urls=tuple(urls),
        facts=tuple(facts),
        guard=_guard(top.get("guard")),
        notes=tuple(notes),
    )


def substitute(data: Any, variables: Mapping[str, str]) -> Any:
    """`data` with every @name@ in its strings replaced from `variables`."""
    if isinstance(data, str):
        return PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), data)
    if isinstance(data, list):
        return [substitute(item, variables) for item in data]
    if isinstance(data, dict):
        return {key: substitute(value, variables) for key, value in data.items()}
    return data


def lines(spec: Spec) -> list[tuple[str, str]]:
    """The lines under the panel, as (text, style), unformatted: what `render`
    prints, and what a test reads."""
    out: list[tuple[str, str]] = []

    def fact(label: str, values: Sequence[str], style: str = "") -> None:
        prefix = f"  {label}: "
        out.append((prefix + values[0], style))
        out.extend((" " * len(prefix) + value, style) for value in values[1:])

    for url in spec.urls:
        fact(url.label, [url.url])
    for item in spec.facts:
        fact(item.label, item.lines, item.style)
    guard = spec.guard
    if guard is not None:
        if not guard.enable:
            # A persistent, project-level decision: while it stands, restores
            # refuse to carry production's secrets.
            out.append(
                (
                    "  devguard OFF — nothing stops a restored database from reaching production",
                    "bold red",
                )
            )
        else:
            if guard.egress:
                allowed = f" (allowed: {', '.join(guard.allow)})" if guard.allow else ""
                fact("Egress", [f"outbound connections blocked{allowed}"])
            if guard.mail:
                where = f" ({guard.mail_url})" if guard.mail_url else ""
                mail = [f"ALL outgoing email → Mailpit{where}"]
                if guard.incoming:
                    mail.append(f"incoming (IMAP/POP3) is {guard.incoming}")
                fact("Mail", mail)
    if spec.notes:
        out.append(("", ""))
        out.extend((f"  {note.text}", note.style) for note in spec.notes)
    return out


def render(spec: Spec, console: Any = None) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    console.print()
    if spec.commands:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan")
        table.add_column()
        for row in spec.commands:
            table.add_row(Text(row.command), Text(row.description))
        console.print(
            Panel(
                table, title=Text(spec.title), title_align="left", border_style="cyan"
            )
        )
    else:
        console.print(Text(spec.title, style="bold cyan"))
    for text, style in lines(spec):
        console.print(Text(text, style=style or ""))
    console.print()


def main(argv: Sequence[str], prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="Print the development shell's banner."
    )
    parser.add_argument("spec", type=Path, help="the banner's JSON")
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
    try:
        data = json.loads(args.spec.read_text())
        spec = parse(substitute(data, variables))
    except (OSError, ValueError) as exc:
        print(f"{parser.prog}: {args.spec}: {exc}", file=sys.stderr)
        return 1
    render(spec)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
