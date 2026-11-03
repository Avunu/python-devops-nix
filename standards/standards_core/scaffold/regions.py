"""Local regions: the part of a ``whole`` file the checkout may edit (DESIGN §5.2).

::

    # <markers.local>-begin <name>
    …kept verbatim…
    # <markers.local>-end <name>

The leader is the header kind's (``#``, or ``//`` for JSONC and TypeScript). The
template fixes the names and their order. Sync renders the template, then puts back what
the current file has between each pair of markers. A region missing from the current
file is empty; an unknown name, a duplicate or an unbalanced marker is exit 2.

``check_redefinitions`` keeps a region additive: it may add, never redefine what sync
manages outside it.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from functools import cache
from typing import TYPE_CHECKING

from standards_core.common.report import ConfigError
from standards_core.product import header_kind

if TYPE_CHECKING:
    from standards_core.product import Product


@cache
def marker(product: Product, leader: str = "#") -> re.Pattern[str]:
    """The begin/end marker line of a local region under ``leader``."""
    local = re.escape(product.markers.local)
    return re.compile(
        rf"^[ \t]*{re.escape(leader)} {local}-(?P<kind>begin|end) "
        r"(?P<name>[A-Za-z0-9_-]+)[ \t]*$"
    )


def leader_of(product: Product, header: str | None) -> str:
    """The comment leader of header kind ``header`` (``#`` when unknown or ``None``)."""
    kind = header_kind(product, header) if header else None
    return kind.leader if kind and kind.leader in ("#", "//") else "#"


def parse(
    product: Product,
    text: str,
    path: str,
    allowed: tuple[str, ...],
    *,
    leader: str = "#",
) -> dict[str, list[str]]:
    """The lines inside each region of ``text`` (without the markers), by name."""
    pattern = marker(product, leader)
    out: dict[str, list[str]] = {}
    current: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        m = pattern.match(line)
        if not m:
            if current is not None:
                out[current].append(line)
            continue
        kind, name = m["kind"], m["name"]
        if kind == "begin":
            if current is not None:
                raise ConfigError(
                    f"{path}:{number}: local region {name!r} begins inside {current!r}"
                )
            if name not in allowed:
                raise ConfigError(
                    f"{path}:{number}: unknown local region {name!r} "
                    f"(this file has: {', '.join(allowed) or 'none'})"
                )
            if name in out:
                raise ConfigError(
                    f"{path}:{number}: local region {name!r} appears twice"
                )
            out[name] = []
            current = name
        else:
            if current != name:
                raise ConfigError(
                    f"{path}:{number}: local-end {name!r} does not close an open region"
                )
            current = None
    if current is not None:
        raise ConfigError(f"{path}: local region {current!r} is never closed")
    return out


def splice(
    product: Product,
    rendered: str,
    current: str | None,
    path: str,
    allowed: tuple[str, ...],
    *,
    leader: str = "#",
) -> str:
    """``rendered`` with each region's body taken from ``current`` (the file on disk)."""
    if not allowed:
        return rendered
    pattern = marker(product, leader)
    bodies = (
        parse(product, current, path, allowed, leader=leader)
        if current is not None
        else {}
    )
    lines = rendered.splitlines(keepends=True)
    out: list[str] = []
    inside: str | None = None
    for line in lines:
        m = pattern.match(line.rstrip("\n"))
        if inside is not None:
            if m and m["kind"] == "end":
                out += [b + "\n" for b in bodies.get(inside, [])]
                out.append(line)
                inside = None
            continue
        out.append(line)
        if m and m["kind"] == "begin":
            inside = m["name"]
    return "".join(out)


def bodies(
    product: Product,
    text: str,
    path: str,
    allowed: tuple[str, ...],
    *,
    leader: str = "#",
) -> str:
    """The text of every region of ``text`` joined: what the checkout contributed."""
    regions = parse(product, text, path, allowed, leader=leader)
    return "\n".join("\n".join(lines) for lines in regions.values())


def filled(
    product: Product,
    text: str,
    path: str,
    allowed: tuple[str, ...],
    *,
    leader: str = "#",
) -> list[str]:
    """The names of the regions of ``text`` that hold anything but blank lines."""
    return [
        name
        for name, lines in parse(product, text, path, allowed, leader=leader).items()
        if any(line.strip() for line in lines)
    ]


# --- redefinitions ------------------------------------------------------------------

_HOOK_ID = re.compile(r"^\s*-\s+id:\s*(\S+)")
_ECOSYSTEM = re.compile(r"^(?P<indent>\s*)-\s+package-ecosystem:\s*(?P<eco>\S+)")
_DIRECTORY = re.compile(r"^\s+directory:\s*(\S+)")
_TARGET_BRANCH = re.compile(r"^\s+target-branch:\s*(\S+)")
_INI_SECTION = re.compile(r"^\s*(\[[^\]]+\])\s*$")
_NIX_DEFINED = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_'-]*)(?:\.[A-Za-z]+)?\s*=")
_NIX_INPUT = re.compile(r"^\s{4}([A-Za-z_][A-Za-z0-9_'-]*) = \{", re.MULTILINE)


def _updates_entries(lines: list[str]) -> set[str]:
    """Dependabot ``updates`` entries, keyed on (ecosystem, directory, target-branch).

    An entry runs from its ``- package-ecosystem:`` line to the next line indented no
    deeper than its dash, so ``target-branch:`` may come before or after ``directory:``.
    """
    found: set[str] = set()
    entry: dict | None = None

    def close() -> None:
        if entry is not None and entry["directory"] is not None:
            key = f"updates entry {entry['eco']} {entry['directory']}"
            if entry["branch"]:
                key += f" → {entry['branch']}"
            found.add(key)

    for line in lines:
        stripped = line.strip()
        dedented = len(line) - len(line.lstrip()) <= (entry or {}).get("indent", -1)
        if entry is not None and stripped and not stripped.startswith("#") and dedented:
            close()
            entry = None
        if m := _ECOSYSTEM.match(line):
            entry = {
                "indent": len(m["indent"]),
                "eco": m["eco"],
                "directory": None,
                "branch": "",
            }
            continue
        if entry is None:
            continue
        if (m := _DIRECTORY.match(line)) and entry["directory"] is None:
            entry["directory"] = m[1]
        elif m := _TARGET_BRANCH.match(line):
            entry["branch"] = m[1].strip("'\"")
    close()
    return found


def _keys(lines: list[str], header: str | None) -> set[str]:
    found = {f"hook id {m[1]}" for line in lines if (m := _HOOK_ID.match(line))}
    found |= _updates_entries(lines)
    if header == "ini":
        found |= {
            f"section {m[1]}" for line in lines if (m := _INI_SECTION.match(line))
        }
    return found


def check_redefinitions(
    product: Product,
    path: str,
    text: str,
    header: str | None,
    managed_inputs: Collection[str] = (),
) -> None:
    """Refuse (exit 2) a local region that redefines what sync manages outside it.

    Inside a region: a managed hook ``id``; a Dependabot ``updates`` entry with the same
    ``(package-ecosystem, directory, target-branch)``; an ini section; under the ``nix``
    header, a flake input in ``managed_inputs`` or defined outside the regions.
    """
    pattern = marker(product, leader_of(product, header))
    inside: list[str] = []
    outside: list[str] = []
    in_region = False
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            in_region = m["kind"] == "begin"
            continue
        (inside if in_region else outside).append(line)

    clash = sorted(_keys(inside, header) & _keys(outside, header))
    if header == "nix":
        defined = {m["name"] for line in inside if (m := _NIX_DEFINED.match(line))}
        managed = {*managed_inputs, *_NIX_INPUT.findall("\n".join(outside))}
        clash += [f"flake input {name}" for name in sorted(defined & managed)]
    if clash:
        raise ConfigError(
            f"{path}: the local region redefines what sync manages: {', '.join(clash)}"
        )
