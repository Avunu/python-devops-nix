"""What a product's plugin gives the TUI: its catalog, and the command lines
that add or remove what was picked from it.

A plugin is a module named by the spec ("plugin.module") with a
`load(config, spec)` that returns a Product. Everything it returns is data:
the TUI runs every Step from the spec's root, through the product's own
command line, and shows it before it does. So the plugin never changes the
workspace itself, and the logic that does stays in one place, the command line
(`odoo module add`, `frappe get-app`), where the scripts and tests reach it too.

`detail` may block (a README fetched over the network): the TUI calls it from a
worker thread. Everything else is called on the UI thread and should be quick.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from devtui.catalog import Catalog, Item
from devtui.spec import Action, Spec


@dataclass(frozen=True)
class Step:
    """One command line, and what it is for."""

    argv: tuple[str, ...]
    label: str = ""


@dataclass(frozen=True)
class Closure:
    """What adding a selection brings with it, for the confirmation."""

    #: Items not picked that come along (an OCA module's dependencies, a
    #: Frappe app's required apps).
    brings: tuple[str, ...] = ()
    #: Lines for the confirmation: "2 new repositories: …".
    notes: tuple[str, ...] = ()
    #: Picked or brought, but not in the catalog for this version.
    unavailable: tuple[str, ...] = ()


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    placeholder: str = ""
    required: bool = False


@dataclass(frozen=True)
class Form:
    """A way to add something that is not in the catalog (a git URL). `argv`
    is a template: "{name}" is the field's value, and a word whose fields are
    all empty is left out, so "--branch={branch}" disappears with no branch."""

    label: str
    fields: tuple[Field, ...]
    argv: tuple[str, ...]
    help: str = ""

    def build(self, values: Mapping[str, str]) -> list[str]:
        out = []
        for word in self.argv:
            names = [f.name for f in self.fields if "{" + f.name + "}" in word]
            if names and not any(values.get(n, "").strip() for n in names):
                continue
            for n in names:
                word = word.replace("{" + n + "}", values.get(n, "").strip())
            out.append(word)
        return out


class Product(Protocol):
    def catalog(self) -> Catalog: ...

    def installed(self) -> set[str]:
        """The ids of the catalog's items the workspace already has."""
        ...

    def closure(self, ids: Sequence[str], bundles: Sequence[str]) -> Closure: ...

    def add(self, ids: Sequence[str], bundles: Sequence[str]) -> list[Step]: ...

    def remove(self, ids: Sequence[str]) -> list[Step]: ...

    def detail(self, item: Item) -> str:
        """Markdown for the detail pane, beyond the item's own facts."""
        ...

    def after_add(self) -> list[Action]:
        """What to offer once an add has succeeded (install, provision)."""
        ...

    def forms(self) -> list[Form]: ...


def load(spec: Spec) -> Product | None:
    if spec.plugin is None:
        return None
    module = importlib.import_module(spec.plugin.module)
    config: Mapping[str, Any] = spec.plugin.config
    return module.load(config, spec)
