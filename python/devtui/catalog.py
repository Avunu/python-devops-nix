"""The store's catalog: what a product offers to add (OCA modules and their
bundles, Frappe apps), and the search over it.

A product's plugin (devtui.plugin) builds the Catalog from its own data; the
Store tab only reads it. Search is word-prefix, as gum's `--no-fuzzy` filter
was: every word of the query must start a word of the item's name, title,
group, categories or summary, so typing a repository's name lists all of its
modules. The ranking puts a match on the name first, then the title, then the
rest, and breaks ties by `rank` (applications, stars) and name.

Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Item:
    id: str
    title: str = ""
    summary: str = ""
    group: str = ""
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    version: str = ""
    license: str = ""
    url: str = ""
    depends: tuple[str, ...] = ()
    available: bool = True
    note: str = ""
    rank: int = 0
    facts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Bundle:
    id: str
    title: str = ""
    summary: str = ""
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalog:
    #: What one item is called: "module", "app".
    noun: str = "item"
    #: What `Item.group` is: "Repository", "Publisher".
    group_label: str = "Group"
    items: tuple[Item, ...] = ()
    bundles: tuple[Bundle, ...] = ()
    #: What a bundle is called: "bundle", "stack".
    bundle_noun: str = "bundle"
    by_id: Mapping[str, Item] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.by_id:
            object.__setattr__(self, "by_id", {item.id: item for item in self.items})

    def groups(self) -> list[tuple[str, int]]:
        """Every group, with how many items it holds, by name."""
        counts: dict[str, int] = {}
        for item in self.items:
            if item.group:
                counts[item.group] = counts.get(item.group, 0) + 1
        return sorted(counts.items())

    def categories(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for item in self.items:
            for category in item.categories:
                counts[category] = counts.get(category, 0) + 1
        return sorted(counts.items())


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _fields(item: Item) -> tuple[list[str], ...]:
    return (
        words(item.id.replace("_", " ").replace("-", " ")) + [item.id.lower()],
        words(item.title),
        words(item.group)
        + words(" ".join(item.categories))
        + words(" ".join(item.tags)),
        words(item.summary),
    )


def score(item: Item, query: str) -> int:
    """0 for no match; higher is better."""
    terms = words(query)
    if not terms:
        return 1
    fields = _fields(item)
    total = 0
    for term in terms:
        best = 0
        for weight, field_words in zip((8, 4, 2, 1), fields, strict=True):
            for word in field_words:
                if word == term:
                    best = max(best, weight * 2)
                elif word.startswith(term):
                    best = max(best, weight)
        if not best:
            return 0
        total += best
    if item.id.lower() == query.strip().lower():
        total += 100
    return total


def search(
    items: Iterable[Item],
    query: str = "",
    *,
    group: str = "",
    category: str = "",
    only: Sequence[str] | None = None,
    available_only: bool = False,
) -> list[Item]:
    """The items matching every filter, best first. `only` restricts to ids."""
    allowed = set(only) if only is not None else None
    hits = []
    for item in items:
        if group and item.group != group:
            continue
        if category and category not in item.categories:
            continue
        if allowed is not None and item.id not in allowed:
            continue
        if available_only and not item.available:
            continue
        s = score(item, query)
        if s:
            hits.append((s, item))
    hits.sort(key=lambda hit: (-hit[0], not hit[1].available, -hit[1].rank, hit[1].id))
    return [item for _, item in hits]
