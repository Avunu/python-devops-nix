"""The managed-file manifest: the union of the product's ``manifest.d/*.json`` (DESIGN §4.3).

A fragment is a JSON object ``{"$comment"?, "entries"?, "retire"?, "floors"?}``:

``entries``
    managed files, each ``{path, strategy, module, when, template, uses, overridable,
    header, header_note, local_regions, handler, command, relock_when, floors, phase}``
    and, for ``sentinel`` entries, ``{block, seed_template, anchor, comment, absorb,
    claims}`` (below);
``retire``
    files sync deletes and ``--check`` reports as ``legacy file``, each ``{paths, rule,
    module, with?, when?, contains?, except?, unmanaged?, only_section?, only_sections?,
    deps_in_project?}``. A rule applies only while ``module`` and every module in
    ``with`` are on and ``when`` holds; ``contains`` holds regular expressions, any of
    which the file's text must match; ``unmanaged`` skips the paths a manifest entry
    manages; ``only_sections`` applies when the file's ``[section]`` set is non-empty and
    a subset of the list (``only_section: x`` is ``only_sections: [x]``);
    ``deps_in_project`` (a requirements file) retires it only once
    ``[project].dependencies`` names every package it lists. ``paths`` and ``except``
    accept ``{a,b}`` braces, expanded here;
``floors``
    version floors by kind (the product names the kinds), merged into the template
    context as ``floors``.

An entry is live when one of its ``module`` (a name, or a list meaning any of them) is
on and its ``when`` holds. ``path`` is itself a Jinja template and ``when`` an
expression over the template context. ``uses`` names the profile values the entry
reads; an ``org.*`` value suffixed ``?`` is optional, any other ``org.*`` value must be
set while the entry is live. ``overridable`` lets an org profile replace the template.
``phase`` is ``a`` for the files sync writes before locking the flake, else ``b``.

``(path, block)`` is unique across fragments: two ``sentinel`` entries may share a path
with different blocks, and any other path appears once. A duplicate is a
``ManifestError``: a product bug, never the checkout's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from standards_core.common.data import product_roots
from standards_core.common.report import ConfigError
from standards_core.product import CORE_HEADER_KINDS, header_kind
from standards_core.scaffold.globs import ManifestError, expand_braces

if TYPE_CHECKING:
    from standards_core.product import Product

STRATEGIES = (
    "whole",
    "toml-merge",
    "json-merge",
    "jsonc-merge",
    "seed",
    "blocks",
    "sentinel",
)
ENTRY_KEYS = {
    "path",
    "template",
    "strategy",
    "module",
    "when",
    "uses",
    "overridable",
    "header",
    "header_note",
    "local_regions",
    "handler",
    "command",
    "relock_when",
    "floors",
    "phase",
    "block",
    "seed_template",
    "anchor",
    "comment",
    "absorb",
    "claims",
}
# The keys only a sentinel entry may have.
SENTINEL_KEYS = {"block", "seed_template", "anchor", "comment", "absorb", "claims"}
RETIRE_KEYS = {
    "paths",
    "rule",
    "module",
    "with",
    "when",
    "contains",
    "except",
    "unmanaged",
    "only_section",
    "only_sections",
    "deps_in_project",
}
FRAGMENT_KEYS = {"entries", "retire", "floors", "$comment"}

_BLOCK = re.compile(r"[a-z0-9][a-z0-9-]*")
_ANCHOR = re.compile(
    r"eof|nix-attrset [A-Za-z0-9_'-]+(?:\.[A-Za-z0-9_'-]+)*|toml-array \S+"
)
COMMENTS = ("#", "//", "html")


@dataclass(frozen=True)
class Entry:
    path: str
    strategy: str
    fragment: str
    modules: tuple[str, ...]
    template: str | None = None
    when: str = "True"
    uses: tuple[str, ...] = ()
    overridable: bool = False
    header: str = "none"
    header_note: str = ""
    local_regions: tuple[str, ...] = ()
    handler: str | None = None
    command: str | None = None
    relock_when: tuple[str, ...] = ()
    floors: str | None = None
    phase: str = "b"
    block: str | None = None
    seed_template: str | None = None
    anchor: str | None = None
    comment: str = "#"
    absorb: bool = False
    claims: str | None = None
    # Set for an org profile's [[extra-files]] entry: the template is the profile's.
    profile_template: bool = False

    @property
    def module(self) -> str:
        """The first (for a single-module entry, the only) module."""
        return self.modules[0]


@dataclass(frozen=True)
class Retire:
    paths: tuple[str, ...]
    rule: str
    fragment: str
    module: str
    also: tuple[str, ...] = ()
    when: str = "True"
    contains: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()
    unmanaged: bool = False
    # The file's [section] set must be non-empty and a subset of these.
    only_sections: tuple[str, ...] = ()
    deps_in_project: bool = False


@dataclass(frozen=True)
class Manifest:
    entries: tuple[Entry, ...]
    retire: tuple[Retire, ...]
    floors: dict[str, dict[str, str]]

    def templates(self) -> dict[str, Entry]:
        """Every entry with a template, by template path."""
        return {e.template: e for e in self.entries if e.template}


def headers(product: Product) -> tuple[str, ...]:
    """The header kinds an entry may name: the core's, then the product's."""
    return tuple(dict.fromkeys([*CORE_HEADER_KINDS, *product.header_kinds]))


def _modules(product: Product, raw: Any, where: str) -> tuple[str, ...]:
    names = raw if isinstance(raw, list) else [raw]
    if not names or not all(isinstance(n, str) for n in names):
        raise ManifestError(f"{where}: module must be a module name or a list of them")
    unknown = [n for n in names if n not in product.modules.names]
    if unknown:
        raise ManifestError(f"{where}: unknown module(s) {unknown}")
    return tuple(names)


def _strings(raw: Any, where: str, key: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ManifestError(f"{where}: {key} must be a list of strings")
    return tuple(raw)


def _expanded(raw: Any, where: str, key: str) -> tuple[str, ...]:
    out: list[str] = []
    for pattern in _strings(raw, where, key):
        out += expand_braces(pattern)
    return tuple(out)


def _sentinel(raw: dict, where: str) -> None:
    block = raw.get("block")
    if not isinstance(block, str) or not _BLOCK.fullmatch(block):
        raise ManifestError(
            f"{where}: a sentinel needs a block name [a-z0-9][a-z0-9-]*"
        )
    anchor = raw.get("anchor")
    if not isinstance(anchor, str) or not _ANCHOR.fullmatch(anchor):
        raise ManifestError(
            f"{where}: a sentinel needs an anchor: eof, nix-attrset <path> or"
            " toml-array <table.key>"
        )
    if raw.get("comment", "#") not in COMMENTS:
        raise ManifestError(f"{where}: comment must be one of {list(COMMENTS)}")
    claims = raw.get("claims")
    if claims is not None and (
        claims != "nix-inputs" or not anchor.startswith("nix-attrset ")
    ):
        raise ManifestError(
            f"{where}: claims is nix-inputs, with a nix-attrset anchor only"
        )


def _entry(product: Product, raw: Any, fragment: str) -> Entry:
    if not isinstance(raw, dict) or "path" not in raw or "strategy" not in raw:
        raise ManifestError(
            f"manifest.d/{fragment}: an entry needs path and strategy: {raw!r}"
        )
    where = f"manifest.d/{fragment}: {raw['path']}"
    unknown = set(raw) - ENTRY_KEYS
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {sorted(unknown)}")
    if "module" not in raw:
        raise ManifestError(f"{where}: an entry needs its module")
    strategy = raw["strategy"]
    if strategy not in STRATEGIES:
        raise ManifestError(f"{where}: unknown strategy {strategy!r}")
    header = raw.get("header", "none")
    if header not in headers(product):
        raise ManifestError(f"{where}: unknown header {header!r}")
    if strategy in ("whole", "sentinel") and not raw.get("template"):
        raise ManifestError(f"{where}: a {strategy} entry needs a template")
    if raw.get("local_regions"):
        kind = header_kind(product, header)
        if strategy != "whole" or kind is None or kind.leader not in ("#", "//"):
            raise ManifestError(
                f"{where}: local regions need a whole file with a # or // header"
            )
    if strategy == "sentinel":
        _sentinel(raw, where)
    else:
        misplaced = sorted(set(raw) & SENTINEL_KEYS)
        if misplaced:
            raise ManifestError(f"{where}: {misplaced} are sentinel keys")
    phase = raw.get("phase", "b")
    if phase not in ("a", "b"):
        raise ManifestError(f"{where}: phase must be a or b")
    floors = raw.get("floors")
    if floors is not None and not isinstance(floors, str):
        raise ManifestError(f"{where}: floors must name a floors kind")
    return Entry(
        path=raw["path"],
        strategy=strategy,
        fragment=fragment,
        modules=_modules(product, raw["module"], where),
        template=raw.get("template"),
        when=raw.get("when", "True"),
        uses=_strings(raw.get("uses", []), where, "uses"),
        overridable=bool(raw.get("overridable", False)),
        header=header,
        header_note=raw.get("header_note", ""),
        local_regions=_strings(raw.get("local_regions", []), where, "local_regions"),
        handler=raw.get("handler"),
        command=raw.get("command"),
        relock_when=_strings(raw.get("relock_when", []), where, "relock_when"),
        floors=floors,
        phase=phase,
        block=raw.get("block"),
        seed_template=raw.get("seed_template"),
        anchor=raw.get("anchor"),
        comment=raw.get("comment", "#"),
        absorb=bool(raw.get("absorb", False)),
        claims=raw.get("claims"),
    )


def _retire(product: Product, raw: Any, fragment: str) -> Retire:
    if (
        not isinstance(raw, dict)
        or "paths" not in raw
        or "rule" not in raw
        or "module" not in raw
    ):
        raise ManifestError(
            f"manifest.d/{fragment}: a retire rule needs paths, rule and module: {raw!r}"
        )
    where = f"manifest.d/{fragment}: retire {raw['paths']}"
    unknown = set(raw) - RETIRE_KEYS
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {sorted(unknown)}")
    (module,) = _modules(product, raw["module"], where)
    if "only_section" in raw and "only_sections" in raw:
        raise ManifestError(f"{where}: only_section and only_sections are exclusive")
    if "only_section" in raw:
        sections: tuple[str, ...] = (raw["only_section"],)
        if not isinstance(raw["only_section"], str):
            raise ManifestError(f"{where}: only_section must be a section name")
    else:
        sections = _strings(raw.get("only_sections", []), where, "only_sections")
    return Retire(
        paths=_expanded(raw["paths"], where, "paths"),
        rule=raw["rule"],
        fragment=fragment,
        module=module,
        also=_modules(product, raw["with"], where) if raw.get("with") else (),
        when=raw.get("when", "True"),
        contains=_strings(raw.get("contains", []), where, "contains"),
        unless=_expanded(raw.get("except", []), where, "except"),
        unmanaged=bool(raw.get("unmanaged", False)),
        only_sections=sections,
        deps_in_project=bool(raw.get("deps_in_project", False)),
    )


def parse(product: Product, fragments: dict[str, Any]) -> Manifest:
    """The union of ``fragments`` (file name → parsed JSON), in file name order."""
    entries: list[Entry] = []
    retire: list[Retire] = []
    floors: dict[str, dict[str, str]] = {}
    seen: dict[tuple[str, str | None], str] = {}
    by_path: dict[str, list[Entry]] = {}
    for name in sorted(fragments):
        doc = fragments[name]
        if not isinstance(doc, dict) or set(doc) - FRAGMENT_KEYS:
            raise ManifestError(
                f"manifest.d/{name}: expected an object with entries, retire and floors"
            )
        for raw in doc.get("entries", []):
            entry = _entry(product, raw, name)
            key = (entry.path, entry.block)
            others = by_path.get(entry.path, [])
            if key in seen or (
                others
                and (entry.strategy != "sentinel" or others[0].strategy != "sentinel")
            ):
                first = seen.get(key) or others[0].fragment
                raise ManifestError(
                    f"{entry.path} is listed in manifest.d/{first} and manifest.d/{name}"
                )
            seen[key] = name
            by_path.setdefault(entry.path, []).append(entry)
            entries.append(entry)
        retire += [_retire(product, raw, name) for raw in doc.get("retire", [])]
        for kind, table in doc.get("floors", {}).items():
            for pkg, floor in table.items():
                if pkg in floors.get(kind, {}):
                    raise ManifestError(
                        f"floor {kind}/{pkg} is set by two fragments (one is"
                        f" manifest.d/{name})"
                    )
                floors.setdefault(kind, {})[pkg] = floor
    return Manifest(tuple(entries), tuple(retire), floors)


@cache
def load(product: Product) -> Manifest:
    """The product's manifest: every ``manifest.d/*.json`` in its data roots."""
    fragments = {}
    for name, path in product_roots(product).listdir("manifest.d").items():
        if not name.endswith(".json") or not path.is_file():
            continue
        try:
            fragments[name] = json.loads(path.read_text())
        except ValueError as e:
            raise ManifestError(f"manifest.d/{name} is not JSON: {e}") from e
    return parse(product, fragments)


def ships(product: Product, path: str) -> bool:
    """Whether the product's manifest renders ``path``."""
    return any(e.path == path for e in load(product).entries)


def extra_entries(cfg: dict) -> list[Entry]:
    """The org profile's ``[[extra-files]]`` as entries: rendered from the profile's
    ``templates/``, live while their module is on and their ``when`` holds."""
    out = []
    for i, raw in enumerate(cfg.get("extra-files", [])):
        out.append(
            Entry(
                path=raw["path"],
                strategy=raw["strategy"],
                fragment=f"profile [[extra-files]] {i}",
                modules=(raw["module"],),
                template=raw["template"],
                when=raw.get("when", "True"),
                header=raw.get("header", "none")
                if raw["strategy"] == "whole"
                else "none",
                profile_template=True,
            )
        )
    return out


def profile_retire(cfg: dict) -> list[Retire]:
    """The org profile's ``[[retire]]`` rules, applied like the product's. A ``contains``
    that is not a regular expression, or a path with unbalanced braces, is the
    profile's error (exit 2)."""
    out = []
    for i, raw in enumerate(cfg.get("retire", [])):
        contains = tuple(raw.get("contains", []))
        for pattern in contains:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ConfigError(
                    f"the org profile's [[retire]] {i} contains {pattern!r}, which is"
                    f" not a regular expression: {e}"
                ) from e
        paths: list[str] = []
        for pattern in raw["paths"]:
            try:
                paths += expand_braces(pattern)
            except ManifestError as e:
                raise ConfigError(f"the org profile's [[retire]] {i}: {e}") from e
        out.append(
            Retire(
                paths=tuple(paths),
                rule="the profile's [[retire]] rule",
                fragment="profile",
                module=raw["module"],
                contains=contains,
            )
        )
    return out
