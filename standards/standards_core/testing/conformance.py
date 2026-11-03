"""Whether a product meets the core's contract (DESIGN §11.4).

``check(product)`` returns a list of problems; empty means conformant. Products run it
in their own suites, and from the command line::

    python -m standards_core.testing.conformance kit_nix_tools.product:PRODUCT

which prints each problem and exits 1 when there is any.

This is the static part: the product builds; every core module is present;
``modules.schema.json`` defines exactly the product's modules, each with ``enable``;
the composed schemas load; every built-in profile validates and ``minimal`` turns on only
the required module; every manifest fragment parses, names known handlers, has its
templates and resolves its ``uses`` keys; and every variable the product's data names
with a core suffix starts with ``<env_prefix>_``.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from standards_core.common import config, schema
from standards_core.common.data import product_roots
from standards_core.common.report import StandardsError
from standards_core.product import CORE_HANDLERS, CORE_MODULES

if TYPE_CHECKING:
    from standards_core.product import Product

# The variables the core reads, each f"{env_prefix}_{suffix}".
ENV_SUFFIXES = (
    "ALLOW_SKEW",
    "DEBUG",
    "EXPECT_REV",
    "FETCH_TOKEN",
    "ISSUE_TOKEN",
    "LOCKED_REV",
    "OFFLINE",
    "PIN_URL",
    "SYNC_LOCK_CHANGED",
    "SYNC_REENTERED",
    "SYNC_REEXEC",
    "SYNC_RESULT",
    "URL_OVERRIDE",
)
_ENV = re.compile(r"\b([A-Z][A-Z0-9_]*?)_(" + "|".join(ENV_SUFFIXES) + r")\b")
# Data files the variable scan reads: text the product writes into checkouts or prints.
_TEXT_SUFFIXES = (
    ".j2",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".nix",
    ".sh",
    ".md",
    ".txt",
)


def _guard(problems: list[str], what: str, fn: Callable[[], Any]) -> Any:
    """``fn()``, or ``None`` with a problem naming ``what`` when it raises."""
    try:
        return fn()
    except (
        StandardsError,
        RuntimeError,
        TypeError,
        ValueError,
        OSError,
        KeyError,
    ) as e:
        problems.append(f"{what}: {type(e).__name__}: {e}")
        return None


def _builds(product: Product) -> list[str]:
    return [f"Product: {p}" for p in product._problems()]


def _modules(product: Product) -> list[str]:
    out = []
    missing = [m for m in CORE_MODULES if m not in product.modules.names]
    if missing:
        out.append(f"modules.names lacks the core module(s) {', '.join(missing)}")
    doc = _guard(
        out, "modules.schema.json", lambda: schema.raw(product, "modules.schema.json")
    )
    if doc is None:
        return out
    defs = doc.get("$defs", {})
    names = list(product.modules.names)
    declared = list(defs.get("modules", {}))
    if declared != names:
        out.append(
            "modules.schema.json $defs/modules must define exactly modules.names, in"
            f" order: {names}, not {declared}"
        )
    enum = defs.get("module-name", {}).get("enum")
    if enum != names:
        out.append("modules.schema.json $defs/module-name must enumerate modules.names")
    for name in declared:
        node = defs["modules"][name]
        found = _guard(
            out,
            f"modules.schema.json module {name}",
            lambda node=node: schema.deref(product, node, "modules.schema.json")[0],
        )
        if found is not None and "enable" not in found.get("properties", {}):
            out.append(f"modules.schema.json module {name} has no enable property")
    return out


def _schemas(product: Product) -> list[str]:
    out: list[str] = []
    for name in (schema.PROFILE, schema.APP, schema.FLEET):
        _guard(out, name, lambda name=name: schema.defaults(product, name))
    fleet = {"schema": 1, product.fleet_key: [{"repo": "owner/name"}]}
    _guard(
        out,
        schema.FLEET,
        lambda: schema.Validator(product, schema.FLEET, "fleet").errors(fleet),
    )
    entry = _guard(
        out,
        product.schemas.fleet_entry,
        lambda: schema.deref(
            product, schema.load(product, product.schemas.fleet_entry), "x"
        )[0],
    )
    if entry is not None and "repo" not in entry.get("required", []):
        out.append(f"{product.schemas.fleet_entry} must require repo")
    return out


def _profiles(product: Product) -> list[str]:
    out: list[str] = []
    if "minimal.toml" not in product_roots(product).listdir("profiles"):
        out.append("profiles/minimal.toml is missing")
        return out
    names = _guard(out, "profiles", lambda: config.builtin_names(product)) or []
    _guard(out, "profiles", lambda: config.builtin_name(product, "recommended"))
    for stem in names:
        doc = _guard(
            out, f"profile {stem}", lambda s=stem: config.builtin_profile(product, s)[1]
        )
        if doc is None:
            continue
        _guard(
            out,
            f"profile {stem}",
            lambda d=doc, s=stem: config.validate_profile(product, d, s, org=False),
        )
        if stem == "minimal":
            on = [
                m
                for m in product.modules.names
                if isinstance(doc.get(m), dict) and doc[m].get("enable") is True
            ]
            if on != [product.modules.required]:
                out.append(
                    f"profile minimal turns on {on}, not only"
                    f" {product.modules.required}"
                )
    return out


def _manifest(product: Product) -> list[str]:
    from standards_core.scaffold import manifest

    out: list[str] = []
    parsed = _guard(out, "manifest", lambda: manifest.load(product))
    if parsed is None:
        return out
    roots = product_roots(product)
    handlers = set(CORE_HANDLERS) | set(product.handlers)
    for entry in parsed.entries:
        where = f"manifest.d/{entry.fragment}: {entry.path}"
        if entry.handler is not None and entry.handler not in handlers:
            out.append(f"{where}: handler {entry.handler!r} is not registered")
        for template in (entry.template, entry.seed_template):
            if (
                template
                and _guard(out, where, lambda t=template: roots.find(f"templates/{t}"))
                is None
            ):
                out.append(f"{where}: templates/{template} is in no data root")
        for key in entry.uses:
            dotted = key.removesuffix("?")
            node = _guard(
                out, where, lambda k=dotted: schema.node_at(product, schema.APP, k)
            )
            if node is None:
                out.append(f"{where}: uses {key!r}, which no schema key resolves")
    return out


def _env_names(product: Product) -> list[str]:
    out: list[str] = []
    prefix = product.identity.env_prefix
    root = product.roots[0]
    texts: list[tuple[str, str]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name.endswith(_TEXT_SUFFIXES):
                try:
                    texts.append((path.relative_to(root).as_posix(), path.read_text()))
                except (OSError, UnicodeDecodeError):
                    continue
    texts += [(f"help[{k!r}]", v) for k, v in product.help.items()]
    for where, text in texts:
        for m in _ENV.finditer(text):
            if m[1] != prefix:
                out.append(
                    f"{where}: {m[0]} is a core variable; spell it {prefix}_{m[2]}"
                )
    return out


def check(product: Product) -> list[str]:
    """Every way ``product`` breaks the core's contract; empty when it conforms."""
    problems = _builds(product)
    for part in (_modules, _schemas, _profiles, _manifest, _env_names):
        problems += part(product)
    return problems


def _load(spec: str) -> Product:
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit(f"usage: python -m {__name__} <module:attr>")
    return getattr(importlib.import_module(module), attr)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(f"usage: python -m {__name__} <module:attr>", file=sys.stderr)
        return 2
    product = _load(args[0])
    problems = check(product)
    for problem in problems:
        print(problem)
    if not problems:
        print(f"{product.identity.name}: conforms")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
