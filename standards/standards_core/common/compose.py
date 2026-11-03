"""The product's effective schemas, built in memory (DESIGN §4.1).

The core ships ``core.schema.json`` (org values, retire rules, extra files, header kinds,
the core modules' parameters, the profile reference and the core fleet-entry keys). The
product ships ``modules.schema.json`` (its module names, one schema per module, and the
org, profile and checkout keys it adds) and ``fleet-entry.schema.json``. From these this
module builds three documents, which ``common.schema.load`` serves under their names, so
every ``$ref`` and ``node_at`` works on them:

- ``profile.schema.json``: what a built-in or org profile may hold;
- ``app.schema.json``: what the checkout's ``[tool.<table>]`` may hold;
- ``fleet.schema.json``: a fleet file of ``repo`` commands.

For a core module the product's schema may add properties: the core's are merged under
the product's, which win per key. A product that ships one of the three documents itself
replaces composition for it.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

from standards_core.product import CORE_HEADER_KINDS, CORE_MODULES

if TYPE_CHECKING:
    from standards_core.product import Product

DRAFT = "https://json-schema.org/draft/2020-12/schema"
CORE = "core.schema.json"
MODULES = "modules.schema.json"
FLEET_ENTRY = "fleet-entry.schema.json"

NAME_PATTERN = "^[a-z0-9][a-z0-9-]*$"


def rebase(node: Any, file: str) -> Any:
    """``node`` copied with every document-local ``$ref`` (``#/…``) pointed at ``file``.

    A definition moved out of its file keeps naming what it named there.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#"):
                out[key] = f"{file}{value}"
            else:
                out[key] = rebase(value, file)
        return out
    if isinstance(node, list):
        return [rebase(item, file) for item in node]
    return copy.deepcopy(node)


def _modules_doc(product: Product) -> dict:
    from standards_core.common import schema

    return schema.load(product, MODULES)


def _defs(doc: dict) -> dict:
    defs = doc.get("$defs", {})
    return defs if isinstance(defs, dict) else {}


def _extra(product: Product, key: str) -> dict:
    """The product's ``org-extra``, ``profile-extra`` or ``app-extra`` definition."""
    node = _defs(_modules_doc(product)).get(key, {})
    return rebase(node, MODULES) if isinstance(node, dict) else {}


def core_schema(product: Product, doc: dict) -> dict:
    """``core.schema.json`` with the product's enums filled in."""
    doc = copy.deepcopy(doc)
    defs = doc["$defs"]
    kinds = list(dict.fromkeys([*CORE_HEADER_KINDS, *product.header_kinds]))
    defs["header-kind"] = {"enum": kinds}
    releases = defs["core-modules"]["releases"]["properties"]
    if product.branching:
        names = list(product.branching)
        releases["branching"] = {"enum": names, "default": names[0]}
    releases["version-scheme"] = {
        "enum": ["semver", *product.version_schemes],
        "default": "semver",
    }
    checks = defs["core-modules"]["repo-policy"]["properties"]["required-checks"]
    checks["anyOf"][1]["items"] = {"enum": list(product.ci.gates)}
    return doc


def module_defs(product: Product) -> dict[str, dict]:
    """One schema per module, in ``modules.names`` order.

    A core module's schema is the core's merged under the product's (the product's
    properties win per key); any other module's is a reference to the product's.
    """
    from standards_core.common import schema

    core = schema.load(product, CORE)["$defs"]["core-modules"]
    own = _defs(_modules_doc(product)).get("modules", {})
    out: dict[str, dict] = {}
    for name in product.modules.names:
        if name not in CORE_MODULES:
            out[name] = {"$ref": f"{MODULES}#/$defs/modules/{name}"}
            continue
        base = rebase(core[name], CORE)
        mine = rebase(own.get(name, {}), MODULES)
        if "$ref" in mine:
            mine, _ = schema.deref(product, mine, MODULES)
            mine = rebase(mine, MODULES)
        merged = {**base, **{k: v for k, v in mine.items() if k != "properties"}}
        merged["properties"] = {
            **base.get("properties", {}),
            **mine.get("properties", {}),
        }
        out[name] = merged
    return out


def _org(product: Product) -> dict:
    from standards_core.common import schema

    core = rebase(schema.load(product, CORE)["$defs"]["org"], CORE)
    extra = _extra(product, "org-extra").get("properties", {})
    core["properties"] = {**core.get("properties", {}), **extra}
    return core


def _extends(product: Product) -> dict:
    from standards_core.common import config

    names = ["minimal", "recommended", *config.builtin_names(product)]
    alternatives = "|".join(re.escape(n) for n in dict.fromkeys(names))
    return {
        "type": "string",
        "pattern": f"^({alternatives})$",
        "description": "Org profiles only, required there: a built-in profile.",
    }


def _module_properties(product: Product) -> dict[str, dict]:
    return {m: {"$ref": f"#/$defs/modules/{m}"} for m in product.modules.names}


def profile_schema(product: Product) -> dict:
    """``profile.schema.json``: a built-in or org profile."""
    identity = product.identity
    properties: dict[str, Any] = {
        "schema": {"const": 1, "description": "The profile schema version."},
        "name": {"type": "string", "pattern": NAME_PATTERN},
        "description": {"type": "string"},
        "extends": _extends(product),
        identity.requires_key: {
            "type": "string",
            "minLength": 1,
            "description": f"A PEP 440 specifier against {identity.distribution}'s"
            " version.",
        },
        "org": {"$ref": "#/$defs/org"},
        "retire": {
            "type": "array",
            "items": {"$ref": f"{CORE}#/$defs/retire-item"},
            "default": [],
        },
        "extra-files": {
            "type": "array",
            "items": {"$ref": f"{CORE}#/$defs/extra-file"},
            "default": [],
        },
        **_extra(product, "profile-extra").get("properties", {}),
        **_module_properties(product),
    }
    return {
        "$schema": DRAFT,
        "$id": "profile.schema.json",
        "title": f"A {identity.name} profile",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "name"],
        "properties": properties,
        "$defs": {"org": _org(product), "modules": module_defs(product)},
    }


def app_schema(product: Product) -> dict:
    """``app.schema.json``: the checkout's ``[tool.<table>]``."""
    identity = product.identity
    extra = _extra(product, "app-extra")
    properties: dict[str, Any] = {
        "schema": {"const": 1, "description": f"The [tool.{identity.table}] version."},
        "profile": {"$ref": f"{CORE}#/$defs/profile-ref"},
        "repo": {
            "type": "string",
            "pattern": "^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$",
            "description": "<owner>/<repo>, or <host>/<group>/…/<repo> off GitHub."
            " Default: from origin.",
        },
        "integration-branch": {
            "type": "string",
            "pattern": "^[A-Za-z0-9._/-]+$",
            "description": "Any branch name. Default: the branching's.",
        },
        "retire-keep": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "default": [],
            "description": "Paths exempt from every retire rule.",
        },
        **extra.get("properties", {}),
        "org": {"$ref": "#/$defs/org"},
        **_module_properties(product),
    }
    return {
        "$schema": DRAFT,
        "$id": "app.schema.json",
        "title": f"[tool.{identity.table}]",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", *extra.get("required", [])],
        "properties": properties,
        "$defs": {"org": _org(product), "modules": module_defs(product)},
    }


def fleet_schema(product: Product) -> dict:
    """``fleet.schema.json``: the fleet file of the ``repo`` commands."""
    return {
        "$schema": DRAFT,
        "$id": "fleet.schema.json",
        "title": f"A fleet list for {product.identity.cli} repo commands",
        "type": "object",
        "required": ["schema", product.fleet_key],
        "additionalProperties": False,
        "properties": {
            "$comment": {"type": "string"},
            "schema": {"const": 1},
            "profile": {
                "type": "string",
                "minLength": 1,
                "description": "The profile for a repository that has not opted in.",
            },
            product.fleet_key: {
                "type": "array",
                "items": {"$ref": FLEET_ENTRY},
            },
        },
    }
