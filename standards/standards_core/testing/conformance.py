"""Whether a product meets the core's contract (DESIGN §11.4).

``check(product)`` returns a list of problems; empty means conformant. Products run it
in their own suites, and from the command line::

    python -m standards_core.testing.conformance kit_nix_tools.product:PRODUCT

which prints each problem and exits 1 when there is any.

The static part: the product builds; every core module is present;
``modules.schema.json`` defines exactly the product's modules, each with ``enable``;
the composed schemas load; every built-in profile validates and ``minimal`` turns on only
the required module; every manifest fragment parses, names known handlers, has its
templates and resolves its ``uses`` keys; and every variable the product's data names
with a core suffix starts with ``<env_prefix>_``.

The render part: every fixture checkout (``Product.fixtures``), under ``minimal`` and
every ``recommended@*``, syncs with ``sync --write --offline``, then (with the locks an
offline sync leaves to Nix and the lock commands faked) checks clean with ``sync
--check``, and a second write changes no byte; every ``seed_template`` holds its entry's
anchor.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
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


# --- the render part -------------------------------------------------------------------


@contextmanager
def _isolated(product: Product) -> Iterator[None]:
    """No user git configuration, no skew or URL override, no CI flag: the fixture runs
    see what a fresh machine sees. Restored afterwards."""
    prefix = product.identity.env_prefix
    unset = [
        f"{prefix}_{suffix}"
        for suffix in ("ALLOW_SKEW", "URL_OVERRIDE", "SYNC_REEXEC", "SYNC_REENTERED")
    ]
    unset += ["CI", "GITHUB_HEAD_REF", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"]
    saved = dict(os.environ)
    try:
        for key in unset:
            os.environ.pop(key, None)
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        for key, value in (
            ("GIT_AUTHOR_NAME", "conformance"),
            ("GIT_AUTHOR_EMAIL", "conformance@example.org"),
            ("GIT_COMMITTER_NAME", "conformance"),
            ("GIT_COMMITTER_EMAIL", "conformance@example.org"),
        ):
            os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _fake_locks(product: Product, root: Path) -> None:
    """The locks an offline sync leaves undone: ``flake.lock`` for the rendered flake,
    and every lock seed whose command did not run (a ``uv-lock`` one at its floors)."""
    from standards_core.scaffold import engine, handlers
    from standards_core.testing import fakes

    flake = root / "flake.nix"
    if flake.is_file():
        (root / "flake.lock").write_text(
            json.dumps(fakes.flake_lock(flake.read_text()), indent=2) + "\n"
        )
    plan = engine.build(product, root, options={"throwaway": True})
    for item in plan.items:
        if item.command is None or item.entry is None:
            continue
        handler = handlers.for_entry(product, item.entry)
        target = root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(handler, handlers.UvLockHandler):
            target.write_text(fakes.uv_lock(handler.floors(plan, item.entry)))
        elif not target.exists():
            target.write_text("")


def _sync(product: Product, root: Path, *argv: str) -> tuple[int, str]:
    from standards_core.testing import fakes

    code, out, err = fakes.run_cli(product, "sync", *argv, cwd=root)
    return code, (out + err).strip()


def _one(product: Product, root: Path, label: str) -> list[str]:
    from standards_core.scaffold import fixtures

    code, text = _sync(product, root, "--write", "--offline", "--throwaway")
    if code != 0:
        return [f"{label}: sync --write --offline exited {code}: {text}"]
    _fake_locks(product, root)
    fixtures.commit(root, "synced")
    out = []
    before = _snapshot(root)
    code, text = _sync(product, root, "--write", "--offline", "--throwaway")
    if code != 0:
        out.append(f"{label}: a second sync --write --offline exited {code}: {text}")
    else:
        after = _snapshot(root)
        changed = sorted(p for p in {*before, *after} if before.get(p) != after.get(p))
        if changed:
            out.append(f"{label}: a second sync --write changed {', '.join(changed)}")
    code, text = _sync(product, root, "--check", "--throwaway")
    if code != 0:
        out.append(f"{label}: sync --check after --write exited {code}: {text}")
    return out


def anchor_found(anchor: str, text: str) -> bool:
    """Whether ``text`` holds a sentinel ``anchor``: ``eof`` always; a ``nix-attrset``
    path's attribute sets, as the Nix scanner follows them; a ``toml-array``'s
    ``key = [`` in its table (or dotted at the root)."""
    from standards_core.scaffold import blocks

    return blocks.anchor_found(anchor, text)


def _seed_anchors(product: Product, root: Path, label: str) -> list[str]:
    from standards_core.scaffold import engine, manifest
    from standards_core.scaffold import render as rendering

    entries = [e for e in manifest.load(product).entries if e.seed_template]
    if not entries:
        return []
    plan = engine.build(product, root, options={"throwaway": True})
    out = []
    for entry in entries:
        text = rendering.render(product, entry.seed_template or "", plan.ctx, None)
        if not anchor_found(entry.anchor or "", text):
            out.append(
                f"{label}: {entry.path}: seed_template {entry.seed_template} lacks the"
                f" anchor {entry.anchor}"
            )
    return out


def _render(product: Product) -> list[str]:
    """The render part: each fixture under each built-in profile."""
    from standards_core.scaffold import fixtures

    if not product.fixtures or product.hooks.write_fixture is None:
        return []
    out: list[str] = []
    names = _guard(out, "profiles", lambda: config.builtin_names(product)) or []
    with tempfile.TemporaryDirectory() as tmp, _isolated(product):
        work = Path(tmp)
        for profile in names:
            plain: dict[str, bool] | None = None
            for name, fixture in product.fixtures.items():
                label = f"fixture {name} x {profile}"
                if (
                    fixture.requires
                    and plain is not None
                    and not all(plain.get(m) for m in fixture.requires)
                ):
                    continue
                try:
                    root = fixtures.throwaway(
                        product, work, None, name, fixture, profile=profile
                    )
                    out += _one(product, root, label)
                    if plain is None:
                        from standards_core.scaffold import engine

                        plain = dict(
                            engine.build(
                                product, root, options={"throwaway": True}
                            ).resolved.modules
                        )
                    out += _seed_anchors(product, root, label)
                except (StandardsError, RuntimeError, OSError) as e:
                    out.append(f"{label}: {type(e).__name__}: {e}")
    return out


def check(product: Product, *, render: bool = True) -> list[str]:
    """Every way ``product`` breaks the core's contract; empty when it conforms.
    ``render=False`` runs the static part only."""
    problems = _builds(product)
    for part in (_modules, _schemas, _profiles, _manifest, _env_names):
        problems += part(product)
    if render and not problems:
        problems += _render(product)
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
