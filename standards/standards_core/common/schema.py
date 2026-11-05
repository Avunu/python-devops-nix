"""Validating a TOML document against a product's schemas (DESIGN §4.1, §4.6).

The package carries no JSON Schema library (its dependencies are jinja2, tomlkit,
packaging and pyyaml), so this implements the keywords the schemas use: ``$ref`` (within
a file, ``#/…``, and to another file, ``<file>.json#/…``), ``type`` (a name or a list),
``const``, ``enum``, ``minimum``, ``maximum``, ``pattern``, ``minLength``, ``minItems``,
``uniqueItems``, ``required``, ``properties``, ``additionalProperties`` (``false`` or a
schema), ``propertyNames``, ``items`` and ``anyOf``. Annotations (``title``,
``description``, ``default``, ``$id``, ``$schema``, ``$defs``, ``$comment``,
``examples``) are ignored. A keyword it does not know is a bug in the schema, so it
raises.

One keyword is the core's own: ``"x-app-only": true`` marks a module parameter only the
checkout's table may set. Validated as a profile (``app_only_allowed=False``), a
document that sets one gets an error naming it.

Schema files are read through the product's data roots, the product's first: a ``$ref``
to ``modules.schema.json`` reaches the product's file from the core's
``core.schema.json``, and the other way round. ``profile.schema.json``,
``app.schema.json`` and ``fleet.schema.json`` are composed (``common.compose``) unless the
product ships the file, and ``core.schema.json`` gets the product's enums filled in.

Defaults (``defaults``) are read from the same schemas: every ``default`` reached through
``properties`` and ``$ref``.
"""

from __future__ import annotations

import copy
import json
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.common.data import product_roots

if TYPE_CHECKING:
    from standards_core.product import Product

ANNOTATIONS = {
    "$schema",
    "$id",
    "$defs",
    "$comment",
    "title",
    "description",
    "default",
    "examples",
    "x-app-only",
}
KEYWORDS = {
    "$ref",
    "type",
    "const",
    "enum",
    "minimum",
    "maximum",
    "pattern",
    "minLength",
    "minItems",
    "uniqueItems",
    "required",
    "properties",
    "additionalProperties",
    "propertyNames",
    "items",
    "anyOf",
}

# The documents built in memory unless the product ships them (DESIGN §4.1).
PROFILE = "profile.schema.json"
APP = "app.schema.json"
FLEET = "fleet.schema.json"
CORE = "core.schema.json"
COMPOSED = (PROFILE, APP, FLEET)

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def ships(product: Product, name: str) -> bool:
    """Whether the product's own data root has ``schema/<name>``."""
    return (Path(product.data_root) / "schema" / name).is_file()


def file_name(product: Product, name: str) -> str:
    """The file a ``$ref`` file part names: the two product schema names map to the
    product's ``Schemas`` choices, so the core's references reach them whatever they are
    called."""
    if name == "modules.schema.json":
        return product.schemas.modules
    if name == "fleet-entry.schema.json":
        return product.schemas.fleet_entry
    return name


def _read(product: Product, name: str) -> dict:
    path = product_roots(product).path(f"schema/{name}")
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise RuntimeError(f"schema {name} is unreadable: {e}") from e
    if not isinstance(doc, dict):
        raise TypeError(f"schema {name} is not a JSON object")
    return doc


@cache
def load(product: Product, name: str) -> dict:
    """The schema document ``name`` for ``product`` (``profile.schema.json``).

    A composed name is built by ``common.compose`` unless the product ships the file;
    ``core.schema.json`` gets the product's enums. Cached per ``(product, name)``.
    """
    from standards_core.common import compose

    name = file_name(product, name)
    if ships(product, name):
        return _read(product, name)
    if name == CORE:
        return compose.core_schema(product, _read(product, name))
    if name == PROFILE:
        return compose.profile_schema(product)
    if name == APP:
        return compose.app_schema(product)
    if name == FLEET:
        return compose.fleet_schema(product)
    return _read(product, name)


def raw(product: Product, name: str) -> dict:
    """A deep copy of the file ``schema/<name>`` as shipped, never composed."""
    return copy.deepcopy(_read(product, file_name(product, name)))


def _resolve(product: Product, ref: str, base: str) -> tuple[dict, str]:
    """The schema a ``$ref`` names, and the file further refs inside it are relative to."""
    file, _, pointer = ref.partition("#")
    file = file_name(product, file or base)
    node: Any = load(product, file)
    for part in [p for p in pointer.split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise RuntimeError(f"schema {base}: $ref {ref!r} names nothing")
        node = node[part]
    if not isinstance(node, dict):
        raise TypeError(f"schema {base}: $ref {ref!r} is not a schema")
    return node, file


def deref(product: Product, node: dict, base: str) -> tuple[dict, str]:
    """Follow ``$ref`` until a schema without one."""
    seen = 0
    while "$ref" in node:
        node, base = _resolve(product, node["$ref"], base)
        seen += 1
        if seen > 32:
            raise RuntimeError(f"schema {base}: $ref cycle")
    return node, base


def _ecma(pattern: str) -> str:
    """A JSON Schema (ECMA-262) ``pattern`` for Python's ``re.search``: ``$`` is the end
    of the value, where Python's would also match before a final newline."""
    return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _show(value: Any) -> str:
    return json.dumps(value, default=str)


class Validator:
    """Validates against one schema document; ``where`` prefixes every message."""

    def __init__(
        self,
        product: Product,
        name: str,
        where: str,
        *,
        app_only_allowed: bool = True,
    ):
        self.product = product
        self.name = name
        self.where = where
        self.app_only_allowed = app_only_allowed

    def _at(self, path: str) -> str:
        return f"{self.where}{path}" if path else self.where

    def _object(self, value: dict, schema: dict, path: str, base: str) -> list[str]:
        out: list[str] = []
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{self._at(path)}: {key} is required")
        extra = schema.get("additionalProperties", True)
        names = schema.get("propertyNames")
        for key, item in value.items():
            sub = f"{path}.{key}"
            if names is not None:
                out += [
                    e.replace(self._at(sub), f"{self._at(sub)} (key)")
                    for e in self.errors(key, names, sub, base)
                ]
            if key in props:
                child, child_base = deref(self.product, props[key], base)
                if child.get("x-app-only") and not self.app_only_allowed:
                    table = self.product.identity.table
                    out.append(
                        f"{self._at(sub)}: an app-only key; set it in the checkout's"
                        f" [tool.{table}]"
                    )
                    continue
                out += self.errors(item, child, sub, child_base)
            elif extra is False:
                out.append(f"{self._at(sub)}: unknown key")
            elif isinstance(extra, dict):
                out += self.errors(item, extra, sub, base)
        return out

    def errors(
        self,
        value: Any,
        schema: dict | None = None,
        path: str = "",
        base: str | None = None,
    ) -> list[str]:
        """Every way ``value`` breaks the schema, as ``<where>: <what>`` lines (empty when valid)."""
        if schema is None:
            schema = load(self.product, self.name)
        base = base or self.name
        schema, base = deref(self.product, schema, base)
        unknown = set(schema) - KEYWORDS - ANNOTATIONS
        if unknown:
            raise RuntimeError(
                f"schema {base}: keyword(s) not supported: {sorted(unknown)}"
            )
        where = self._at(path)
        if "type" in schema:
            types = (
                schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            )
            if not any(_TYPES[t](value) for t in types):
                return [f"{where}: expected {' or '.join(types)}, got {_show(value)}"]
        if "anyOf" in schema:
            branches = [self.errors(value, sub, path, base) for sub in schema["anyOf"]]
            if all(branches):
                # The branch whose type matched explains best; else the first.
                return min(
                    branches,
                    key=lambda errs: (any("expected" in e for e in errs), len(errs)),
                )
        out: list[str] = []
        if "const" in schema and value != schema["const"]:
            out.append(f"{where}: must be {_show(schema['const'])}")
        if "enum" in schema and value not in schema["enum"]:
            out.append(f"{where}: must be one of {_show(schema['enum'])}")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                out.append(f"{where}: must be at least {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                out.append(f"{where}: must be at most {schema['maximum']}")
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                out.append(
                    f"{where}: must be at least {schema['minLength']} characters"
                )
            if "pattern" in schema and not re.search(_ecma(schema["pattern"]), value):
                out.append(
                    f"{where}: {_show(value)} does not match {schema['pattern']}"
                )
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                out.append(f"{where}: needs at least {schema['minItems']} item(s)")
            if schema.get("uniqueItems"):
                seen = [json.dumps(v, sort_keys=True, default=str) for v in value]
                if len(set(seen)) != len(seen):
                    out.append(f"{where}: items must be unique")
            if "items" in schema:
                for i, item in enumerate(value):
                    out += self.errors(item, schema["items"], f"{path}[{i}]", base)
        if isinstance(value, dict):
            out += self._object(value, schema, path, base)
        return out


def _defaults(product: Product, node: dict, base: str) -> Any:
    node, base = deref(product, node, base)
    if "properties" in node:
        out = {}
        for key, sub in node["properties"].items():
            value = _defaults(product, sub, base)
            if value is not None:
                out[key] = value
        if "default" in node and isinstance(node["default"], dict):
            out = {**copy.deepcopy(node["default"]), **out}
        return out if out or "default" in node else None
    return copy.deepcopy(node.get("default"))


def defaults(product: Product, name: str) -> dict:
    """Every key of schema ``name`` that has a ``default``, with it; nested tables included."""
    return _defaults(product, load(product, name), name) or {}


def node_at(product: Product, name: str, key: str) -> dict | None:
    """The schema of the dotted ``key`` in schema ``name``; ``None`` when it has none."""
    node, base = deref(product, load(product, name), name)
    for part in key.split("."):
        props = node.get("properties", {})
        if part in props:
            node, base = deref(product, props[part], base)
        elif isinstance(node.get("additionalProperties"), dict):
            node, base = deref(product, node["additionalProperties"], base)
        else:
            return None
    return node
