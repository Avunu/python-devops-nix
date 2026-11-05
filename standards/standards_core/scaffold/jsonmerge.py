"""The ``json-merge`` strategy: managed keys in a JSON file the checkout owns (DESIGN §5.3).

The JSON counterpart of ``tomlmerge``. Only the managed keys are set; every other key,
and the order of all of them, is the checkout's. A key sync adds goes before the first
later key of its parent's ``key_order`` the object has, else at the end of the object,
so a group turned off and on again leaves the file as it was.

Each managed key belongs to one key group, managed while ``on(ctx, group)`` holds. When
a group is off and was on in any table the checkout committed since it opted in
(``ctx.history``), each of its keys whose value still equals what the group renders is
removed (an object left empty goes too); any other value is left as the checkout's,
with a warning on the run that turns it off. While a group is on, the value written is
``reconcile(ctx, path, have, rendered)``: a floor keeps a hand-raised pin.

The file is written in the product's ``json_style``. A file the checkout's formatter
laid out otherwise is drift only when its parsed values differ.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.common.report import ConfigError
from standards_core.product import mapping_field
from standards_core.scaffold import context, jsonfmt
from standards_core.scaffold.handlers import BaseHandler, item

if TYPE_CHECKING:
    from standards_core.scaffold.context import NS
    from standards_core.scaffold.engine import Item, Plan
    from standards_core.scaffold.manifest import Entry

KeyPath = tuple[str, ...]


@dataclass(frozen=True)
class JsonGroups:
    # group → [(key path, value)]
    groups: Callable[[NS], dict[str, list[tuple[KeyPath, Any]]]]
    on: Callable[[NS, str], bool]
    # parent → key order for inserts
    key_order: Mapping[KeyPath, tuple[str, ...]] = mapping_field()
    baseline: Callable[[NS, KeyPath, Any, Any], bool] = (
        lambda ctx, path, have, rendered: False
    )
    # floors
    reconcile: Callable[[NS, KeyPath, Any, Any], Any] = (
        lambda ctx, path, have, rendered: rendered
    )
    # rules on checkout-owned keys
    problems: Callable[[dict, NS], list[tuple[int, str]]] = lambda doc, ctx: []


@dataclass
class Merged:
    """The merged document, and what sync left as the checkout's although a group is
    off."""

    doc: dict
    warnings: list[str] = field(default_factory=list)


def was_on(groups: JsonGroups, ctx: Any, group: str) -> bool:
    return context.ever(ctx.get("history"), lambda h: groups.on(h, group))


def turns_off(groups: JsonGroups, ctx: Any, group: str) -> bool:
    prev = ctx.get("previous")
    return bool(prev) and groups.on(prev, group)


def get(doc: Any, path: KeyPath) -> Any:
    cur = doc
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def put(table: dict, key: str, value: Any, order: tuple[str, ...]) -> None:
    """Set ``table[key]``; a new key goes before the first later key of ``order`` the
    table has, else at the end."""
    if key in table or key not in order:
        table[key] = value
        return
    later = order[order.index(key) + 1 :]
    items = list(table.items())
    at = next((i for i, (k, _) in enumerate(items) if k in later), len(items))
    items.insert(at, (key, value))
    table.clear()
    table.update(items)


def _set(groups: JsonGroups, doc: dict, path: KeyPath, value: Any) -> None:
    cur = doc
    for n, part in enumerate(path[:-1]):
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            put(cur, part, nxt, groups.key_order.get(path[:n], ()))
        cur = nxt
    put(cur, path[-1], value, groups.key_order.get(path[:-1], ()))


def _drop(doc: dict, path: KeyPath) -> None:
    """Remove ``path`` and every object along it the removal leaves empty."""
    parents = [doc]
    for part in path[:-1]:
        nxt = parents[-1].get(part)
        if not isinstance(nxt, dict):
            return
        parents.append(nxt)
    parents[-1].pop(path[-1], None)
    for n in range(len(path) - 1, 0, -1):
        if parents[n]:
            return
        parents[n - 1].pop(path[n - 1], None)


def merge(groups: JsonGroups, current: dict | None, ctx: Any, path: str) -> Merged:
    """``current`` (or a new document) with every managed key set or retracted."""
    doc = copy.deepcopy(current) if current is not None else {}
    out = Merged(doc)
    for group, keys in groups.groups(ctx).items():
        on = groups.on(ctx, group)
        for key_path, value in keys:
            have = get(doc, key_path)
            if not on:
                if have is None or groups.baseline(ctx, key_path, have, value):
                    continue
                if have == value:
                    if was_on(groups, ctx, group):
                        _drop(doc, key_path)
                elif turns_off(groups, ctx, group):
                    out.warnings.append(
                        f"{path} {'.'.join(key_path)} is the app's now that {group} is"
                        " off (it differs from what sync wrote)"
                    )
                continue
            if have is not None:
                value = groups.reconcile(ctx, key_path, have, value)
            if have != value:
                _set(groups, doc, key_path, copy.deepcopy(value))
    return out


def _parse(text: str | None, path: str) -> dict | None:
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} is not a JSON object")
    return doc


class JsonGroupsHandler(BaseHandler):
    """A ``json-merge`` entry: ``groups`` own its managed keys. A missing file is created
    from the groups that are on."""

    strategies = ("json-merge",)
    merged = True

    def __init__(self, name: str, groups: JsonGroups) -> None:
        self.name = name
        self.groups = groups

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        ctx = plan.ctx
        doc = _parse(current, path)
        merged = self.merge(plan, doc, path)
        plan.notices += merged.warnings
        if merged.doc == (doc if doc is not None else {}):
            out = [item(entry, path, current, current)]
        else:
            wanted = jsonfmt.dumps(merged.doc, plan.product.json_style)
            problem = "missing" if current is None else "managed keys differ"
            out = [item(entry, path, current, wanted, problem)]
        for code, problem in self.groups.problems(merged.doc, ctx):
            out.append(item(entry, path, current, current, problem, code))
        return out

    def merge(self, plan: Plan, doc: dict | None, path: str) -> Merged:
        """The merge a subclass may extend (scripts, retired steps)."""
        return merge(self.groups, doc, plan.ctx, path)
