"""The ``toml-merge`` strategy: managed keys in a TOML file the checkout owns (DESIGN §5.3).

tomlkit edits keys in place, so comments, order and every key sync does not own stay
byte for byte. A managed table the file lacks is written as text in its place among the
managed tables the file has (``TomlGroups.canon_tables``), else after the last existing
``[tool.*]`` table; a managed key a table lacks goes before the first later one it has
(``key_order``, else the group's order). So turning a module off and on again restores
the file byte for byte.

Each managed key belongs to one key group, managed while ``on(ctx, group)`` holds. When
a group is off, each of its keys that still holds the rendered value is removed, when
the group was on in any table the checkout committed since it opted in
(``ctx.history``, ``context.ever``); any other value is left as the checkout's, with a
warning on the run that turns it off. A key ``baseline`` says the checkout had of its
own (what its framework's generator writes) is never retracted, so the history is never
asked about it. While a group is on, the value written is ``reconcile(ctx, path, have,
rendered)``: the rendered value by default, or the one on disk when it is at or above a
floor.

``on`` is also asked about each committed state of ``ctx.history`` and about
``ctx.previous``: those are namespaces with ``modules`` and ``cfg`` only.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomlkit
import tomlkit.items

from standards_core.common.report import ConfigError
from standards_core.product import mapping_field
from standards_core.scaffold import context
from standards_core.scaffold.handlers import BaseHandler, item

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import NS
    from standards_core.scaffold.engine import Item, Plan
    from standards_core.scaffold.manifest import Entry

KeyPath = tuple[str, ...]


@dataclass(frozen=True)
class TomlGroups:
    # group → [(key path, value)], file order
    groups: Callable[[NS], dict[str, list[tuple[KeyPath, Any]]]]
    on: Callable[[NS, str], bool]  # whether a group is managed now
    canon_tables: tuple[str, ...] = ()  # managed tables in layout order
    head_tables: tuple[str, ...] = ()  # tables that follow [project] in order
    key_order: Mapping[KeyPath, tuple[str, ...]] = mapping_field()
    # never retracted
    baseline: Callable[[NS, KeyPath, Any, Any], bool] = (
        lambda ctx, path, have, rendered: False
    )
    # value to keep
    reconcile: Callable[[NS, KeyPath, Any, Any], Any] = (
        lambda ctx, path, have, rendered: rendered
    )
    # written once when missing (fail_under)
    seeds: Callable[[NS, dict], list[tuple[KeyPath, Any]]] = lambda ctx, doc: []
    # in-place tomlkit edits beyond groups
    extra: Callable[[Any, NS], None] = lambda doc, ctx: None
    # added to managed_view
    view_extra: Callable[[dict, NS], dict[str, Any]] = lambda doc, ctx: {}
    # rules on app-owned keys
    problems: Callable[[dict, NS], list[tuple[int, str]]] = lambda doc, ctx: []


@dataclass
class Merged:
    """The merged text, and the keys sync left as the checkout's although their group
    is off."""

    text: str
    warnings: list[str] = field(default_factory=list)


def was_on(groups: TomlGroups, ctx: Any, group: str) -> bool:
    """Whether ``group`` was on in any table since the checkout opted in."""
    return context.ever(ctx.get("history"), lambda h: groups.on(h, group))


def turns_off(groups: TomlGroups, ctx: Any, group: str) -> bool:
    """Whether ``group`` was on at ``HEAD``: the run that turns it off says once what it
    leaves to the checkout."""
    prev = ctx.get("previous")
    return bool(prev) and groups.on(prev, group)


def managed(groups: TomlGroups, ctx: Any) -> list[tuple[KeyPath, Any]]:
    """The managed keys of the groups that are on, in file order."""
    return [
        kv
        for group, keys in groups.groups(ctx).items()
        if groups.on(ctx, group)
        for kv in keys
    ]


_HEADER = re.compile(r"^\s*\[\[?\s*(?P<name>[^\]\[]+?)\s*\]\]?\s*(#.*)?$")


def get(doc: Any, path: KeyPath) -> Any:
    cur = doc
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def _table_text(path: KeyPath, values: dict[str, Any]) -> str:
    lines = [f"[{'.'.join(path)}]"]
    for key, value in values.items():
        lines.append(
            f"{tomlkit.key(key).as_string()} = {tomlkit.item(value).as_string()}"
        )
    return "\n".join(lines) + "\n"


def _is_tool(name: str) -> bool:
    return name == "tool" or name.startswith("tool.")


def _after_tools(headers: list[tuple[int, str]], end: int) -> int:
    """The line after the last ``[tool.*]`` table: where a table goes that has no place."""
    tool = [i for i, name in headers if _is_tool(name)]
    if not tool:
        return end
    after = [i for i, _ in headers if i > tool[-1]]
    return after[0] if after else end


def _table_position(
    groups: TomlGroups, headers: list[tuple[int, str]], name: str, end: int
) -> int:
    """The line a new managed table ``name`` goes before.

    The ``head_tables`` after the first follow it (and each other); a managed
    ``[tool.*]`` table goes before the first managed table ranked after it that follows
    the checkout's own ``[tool.*]`` tables, else after the last ``[tool.*]`` table."""
    head = list(groups.head_tables)
    canon = list(groups.canon_tables)
    if name in head[1:]:
        for anchor in reversed(head[: head.index(name)]):
            found = [j for j, (_, n) in enumerate(headers) if n == anchor]
            if found:
                j = found[-1] + 1
                while j < len(headers) and headers[j][1].startswith(anchor + "."):
                    j += 1
                return headers[j][0] if j < len(headers) else end
        return _after_tools(headers, end)
    rank = canon.index(name) if name in canon else len(canon)
    own = [j for j, (_, n) in enumerate(headers) if _is_tool(n) and n not in canon]
    for i, n in headers[own[-1] + 1 if own else 0 :]:
        if n in canon and canon.index(n) > rank:
            return i
    return _after_tools(headers, end)


def _headers(lines: list[str]) -> list[tuple[int, str]]:
    return [
        (i, m["name"].strip())
        for i, line in enumerate(lines)
        if (m := _HEADER.match(line))
    ]


def _insert_at(text: str, at: int, blocks: list[str]) -> str:
    """``text`` with ``blocks`` (TOML tables) before line ``at``, a blank line around
    them."""
    lines = text.splitlines(keepends=True)
    before, rest = lines[:at], lines[at:]
    while before and not before[-1].strip():
        rest.insert(0, before.pop())
    head = "".join(before)
    if head and not head.endswith("\n"):
        head += "\n"
    middle = "\n".join(blocks)
    tail = "".join(rest).lstrip("\n")
    return head + ("\n" if head else "") + middle + ("\n" + tail if tail else "")


def _place_tables(
    groups: TomlGroups, text: str, missing: dict[KeyPath, dict[str, Any]]
) -> str:
    """``text`` with each missing managed table written in its place."""
    canon = list(groups.canon_tables)

    def rank(path: KeyPath) -> int:
        name = ".".join(path)
        return canon.index(name) if name in canon else len(canon)

    for path in sorted(missing, key=rank):
        lines = text.splitlines(keepends=True)
        at = _table_position(groups, _headers(lines), ".".join(path), len(lines))
        text = _insert_at(text, at, [_table_text(path, missing[path])])
    return text


def put_key(
    table: Any, key: str, value: Any, order: list[str] | tuple[str, ...]
) -> None:
    """Set a key the table lacks before the first later key of ``order`` it has, else at
    the end."""
    order = list(order)
    later = order[order.index(key) + 1 :] if key in order else []
    container = getattr(table, "value", None)
    body = getattr(container, "body", None)
    at = next(
        (
            i
            for i, (k, _) in enumerate(body or [])
            if k is not None and getattr(k, "key", None) in later
        ),
        None,
    )
    if at is None or not hasattr(container, "_insert_at"):
        table[key] = value
        return
    container._insert_at(at, key, value)  # tomlkit has no public insert-before


def _insert_tables(text: str, blocks: list[str]) -> str:
    """``text`` with ``blocks`` (TOML tables) placed after the last ``[tool.*]`` table."""
    if not blocks:
        return text
    lines = text.splitlines(keepends=True)
    return _insert_at(text, _after_tools(_headers(lines), len(lines)), blocks)


def merge(groups: TomlGroups, text: str, ctx: Any, path: str) -> Merged:
    """``text`` with every managed key of a group that is on set (reconciled), every
    managed key of a group that is off and still holding its rendered value removed
    (when the group was on), the seeds written where they are missing, and
    ``groups.extra``'s edits."""
    doc = tomlkit.parse(text)
    missing: dict[KeyPath, dict[str, Any]] = {}
    warnings: list[str] = []

    def table_at(where: KeyPath) -> Any:
        cur: Any = doc
        for part in where:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur if isinstance(cur, dict) else None

    def prune(where: KeyPath) -> None:
        """Remove the tables along ``where`` that the retraction left empty."""
        for n in range(len(where), 0, -1):
            table = table_at(where[:n])
            parent = table_at(where[: n - 1]) if n > 1 else doc
            if table is None or len(table) or parent is None:
                return
            del parent[where[n - 1]]

    def set_key(key_path: KeyPath, value: Any, order: list[str]) -> None:
        table_path, key = key_path[:-1], key_path[-1]
        if isinstance(value, dict):
            # An exact table: its keys are exactly these.
            table = table_at(key_path)
            if table is None:
                missing.setdefault(key_path, {}).update(value)
                return
            for k in [k for k in table if k not in value]:
                del table[k]
            for k, v in value.items():
                if plain(table.get(k)) != v:
                    table[k] = v
            return
        table = table_at(table_path)
        if table is None or isinstance(table, tomlkit.items.AoT):
            missing.setdefault(table_path, {})[key] = value
        elif key not in table:
            put_key(table, key, value, order)
        elif plain(table.get(key)) != value:
            table[key] = value

    for group, keys in groups.groups(ctx).items():
        on = groups.on(ctx, group)
        for key_path, value in keys:
            table_path = key_path[:-1]
            have = plain(get(doc, key_path))
            if not on:
                # The history is asked only about a key that holds the rendered value
                # and is not the checkout's baseline (a shallow clone may not know).
                if have is None or groups.baseline(ctx, key_path, have, value):
                    continue
                if have == value:
                    if was_on(groups, ctx, group):
                        del table_at(table_path)[key_path[-1]]
                        prune(table_path)
                elif turns_off(groups, ctx, group):
                    warnings.append(
                        f"{path} {'.'.join(key_path)} is the app's now that {group} is"
                        " off (it differs from what sync wrote)"
                    )
                continue
            if have is not None:
                value = groups.reconcile(ctx, key_path, have, value)
            order = list(groups.key_order.get(table_path, ())) or [
                p[-1] for p, _ in keys if p[:-1] == table_path
            ]
            set_key(key_path, value, order)
    for key_path, value in groups.seeds(ctx, doc.unwrap()):
        if get(doc, key_path) is None:
            set_key(key_path, value, list(groups.key_order.get(key_path[:-1], ())))
    groups.extra(doc, ctx)
    return Merged(_place_tables(groups, tomlkit.dumps(doc), missing), warnings)


def managed_view(groups: TomlGroups, doc: dict, ctx: Any) -> dict:
    """The managed keys' current values, for the semantic comparison ``--check`` makes:
    the keys of the groups that are on, and whether each off group's rendered keys are
    gone, plus ``view_extra``."""
    view = {".".join(p): get(doc, p) for p, _ in managed(groups, ctx)}
    for group, keys in groups.groups(ctx).items():
        if groups.on(ctx, group):
            continue
        left = {
            f"{'.'.join(p)} (off)": get(doc, p) == value
            for p, value in keys
            if not groups.baseline(ctx, p, plain(get(doc, p)), value)
        }
        # None left reads the same either way, so the history is asked only when one is.
        if any(left.values()) and was_on(groups, ctx, group):
            view.update(left)
    view.update(groups.view_extra(doc, ctx))
    return view


def value_text(value: Any) -> str:
    """A TOML value on one line: arrays and tables inline."""
    if isinstance(value, dict):
        inner = ", ".join(
            f"{tomlkit.key(k).as_string()} = {value_text(v)}" for k, v in value.items()
        )
        return "{ " + inner + " }" if inner else "{}"
    if isinstance(value, list):
        return "[" + ", ".join(value_text(v) for v in value) + "]"
    return tomlkit.item(value).as_string()


def add_table(product: Product, text: str, values: dict[str, Any]) -> str:
    """``text`` with a new ``[tool.<table>]`` table (``--standards`` creates it once)."""
    lines = [
        f"[tool.{product.identity.table}]",
        *(f"{tomlkit.key(k).as_string()} = {value_text(v)}" for k, v in values.items()),
    ]
    return _insert_tables(text, ["\n".join(lines) + "\n"])


def _parse(text: str, path: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


class TomlMergeHandler(BaseHandler):
    """A ``toml-merge`` entry: ``groups`` own its managed keys. ``creates_table`` marks
    the handler of the config file, where ``--standards`` adds ``[tool.<table>]``."""

    strategies = ("toml-merge",)
    merged = True

    def __init__(
        self, name: str, groups: TomlGroups, *, creates_table: bool = False
    ) -> None:
        self.name = name
        self.groups = groups
        self.creates_table = creates_table

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        ctx = plan.ctx
        text = current or ""
        doc = _parse(text, path)
        created = self.creates_table and plan.created_config is not None
        if created:
            text = add_table(plan.product, text, plan.created_config or {})
        merged = merge(self.groups, text, ctx, path)
        plan.notices += merged.warnings
        wanted = merged.text
        same = not created and managed_view(self.groups, doc, ctx) == managed_view(
            self.groups, _parse(wanted, path), ctx
        )
        if same:
            out = [item(entry, path, current, current)]
        else:
            problem = "missing" if current is None else "managed keys differ"
            out = [item(entry, path, current, wanted, problem)]
        for code, problem in self.groups.problems(doc, ctx):
            out.append(item(entry, path, current, current, problem, code))
        return out
