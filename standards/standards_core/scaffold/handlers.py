"""Handlers: what each strategy does with one manifest entry (DESIGN §5.1).

A handler renders a live entry into plan items, says what goes when the entry's modules
are all off (``retract``) or its ``when`` is false (``absent``), validates a seed on
disk, renders a seed's first text, and names the command a lock seed runs. The engine
picks an entry's handler by ``entry.handler``, else by its strategy, from the core's
registry plus ``Product.handlers``.

The base class of each strategy implements the behaviour every product gets; a product
handler subclasses one and overrides methods (a precheck before a whole file renders, a
seed computed rather than rendered, a lock's own problem). ``toml-merge`` and
``json-merge`` need the product's key groups, so a product registers a
``tomlmerge.TomlMergeHandler`` or ``jsonmerge.JsonGroupsHandler`` under a name of its
own and its entries name it.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from standards_core.common import repo
from standards_core.common.report import CLEAN, DRIFT, INVALID, ConfigError, EnvError
from standards_core.scaffold import engine, floors, regions, semantic
from standards_core.scaffold import render as rendering
from standards_core.scaffold.engine import Item
from standards_core.scaffold.globs import ManifestError

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import NS
    from standards_core.scaffold.engine import Plan
    from standards_core.scaffold.manifest import Entry


class Handler(Protocol):
    name: str
    strategies: tuple[str, ...]  # the strategies it may serve
    # rendered on every run, module on or off (key-group retraction happens in render)
    merged: bool

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]: ...

    def retract(
        self, plan: Plan, entry: Entry, path: str, current: str
    ) -> Item | None: ...  # every module off

    def absent(
        self, plan: Plan, entry: Entry, path: str, current: str
    ) -> Item | None: ...  # module on, when false

    def whole_precheck(
        self, plan: Plan, entry: Entry, path: str, current: str | None
    ) -> Item | None: ...

    def validate(
        self, plan: Plan, entry: Entry, path: str, current: str
    ) -> list[Item]: ...  # seeds on disk

    def seed(
        self, plan: Plan, entry: Entry, templates: Path | None
    ) -> str | None: ...  # None → render template

    def lock_argv(
        self, plan: Plan, item: Item, touched: Sequence[str]
    ) -> list[str] | None: ...


def item(
    entry: Entry,
    path: str,
    current: str | None,
    wanted: str | None,
    problem: str = "",
    code: int | None = None,
    command: str | None = None,
) -> Item:
    """The item of a live entry: drift when ``wanted`` differs (or a command must run),
    with the default problem ``missing`` or ``differs from the rendered file``."""
    if code is None:
        code = DRIFT if (wanted != current or command) else CLEAN
    if code == DRIFT and not problem:
        problem = "missing" if current is None else "differs from the rendered file"
    return Item(
        path,
        entry.strategy,
        current,
        wanted,
        problem,
        code,
        command,
        entry.phase,
        entry=entry,
    )


def template_dir(entry: Entry, templates: Path | None) -> Path | None:
    """Where ``entry``'s template comes from: the org profile's ``templates/`` for an
    ``[[extra-files]]`` template or an override, else the product's (``None``)."""
    if entry.profile_template:
        if templates is None or not (templates / (entry.template or "")).is_file():
            raise ConfigError(
                f"[[extra-files]] {entry.path}: the profile has no"
                f" templates/{entry.template}"
            )
        return templates
    if (
        entry.overridable
        and templates is not None
        and (templates / (entry.template or "")).is_file()
    ):
        return templates
    return None


def entry_header(plan: Plan, entry: Entry) -> str:
    """The managed header line ``entry``'s files open with (``""`` for ``none``)."""
    return rendering.header(
        plan.product, entry.header, bool(entry.local_regions), entry.header_note
    )


def whole_text(
    plan: Plan, entry: Entry, path: str, current: str | None, templates: Path | None
) -> str:
    """The whole file ``entry`` renders: the template, the regions of ``current``
    spliced back in (refused when they redefine what sync manages), the header first."""
    product = plan.product
    text = rendering.render(
        product,
        entry.template or "",
        plan.ctx,
        current,
        directory=template_dir(entry, templates),
    )
    if entry.local_regions:
        leader = regions.leader_of(product, entry.header)
        text = regions.splice(
            product, text, current, path, entry.local_regions, leader=leader
        )
        regions.check_redefinitions(
            product, path, text, entry.header, product.pins.managed_inputs(plan.ctx)
        )
    return entry_header(plan, entry) + text


def is_managed(product: Product, text: str) -> bool:
    """Whether ``text`` opens with a line carrying ``markers.managed``."""
    return product.markers.managed in text.split("\n", 1)[0]


def managed_at_head(plan: Plan, path: str) -> bool:
    """Whether ``HEAD``'s ``path`` opens with the managed marker: sync wrote it before."""
    try:
        text = repo.git(plan.root, "show", f"HEAD:./{path}")
    except EnvError:
        return False
    return is_managed(plan.product, text)


def first_opt_in(
    plan: Plan, entry: Entry, path: str, current: str | None, wanted: str
) -> Item | None:
    """Replacing an unmanaged file ``Product.first_opt_in`` lists needs ``--force``
    unless it is one the product wrote before (``legacy``): the checkout's own content
    would be lost (DESIGN §5.9). Once sync has written the file (it is managed at
    ``HEAD``), an edit is ordinary drift."""
    product = plan.product
    spec = next((f for f in product.first_opt_in if f.path == path), None)
    if spec is None or current is None or current == wanted:
        return None
    if is_managed(product, current) or plan.ctx.options.get("force"):
        return None
    if managed_at_head(plan, path):
        return None
    if current in spec.legacy(current, plan.checkout):
        return None
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(True), wanted.splitlines(True), "current", "rendered"
        )
    )
    message = spec.message.format(
        path=path, name=product.identity.name, hint=spec.hint, diff=diff
    )
    return Item(
        path,
        entry.strategy,
        current,
        current,
        message,
        INVALID,
        phase=entry.phase,
        entry=entry,
    )


BLAME_LINE = re.compile(r"^[0-9a-f]{40}  # \S.*$")


def blame_problems(root: Path, text: str) -> list[str]:
    """``.git-blame-ignore-revs`` rules: ``<40-hex sha>  # <subject>`` lines, each
    reachable from ``HEAD`` (checked only when the history is all there)."""
    out = []
    shas = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if not BLAME_LINE.match(line):
            out.append(f"line {number} is not `<40-hex sha>  # <subject>`: {line}")
        else:
            shas.append(line[:40])
    try:
        shallow = repo.git(root, "rev-parse", "--is-shallow-repository").strip()
    except EnvError:
        shallow = "true"
    if shallow != "true":
        for sha in shas:
            try:
                repo.git(root, "merge-base", "--is-ancestor", sha, "HEAD")
            except EnvError:
                out.append(f"{sha} is not reachable from HEAD")
    return out


# --- base classes ------------------------------------------------------------------------


class BaseHandler:
    """The defaults every handler has: nothing to retract, validate, seed or lock."""

    name = ""
    strategies: tuple[str, ...] = ()
    merged = False

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        raise ManifestError(
            f"{entry.fragment}: {path}: handler {self.name!r} does not render"
        )

    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        return None

    def absent(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        return None

    def whole_precheck(
        self, plan: Plan, entry: Entry, path: str, current: str | None
    ) -> Item | None:
        return None

    def validate(self, plan: Plan, entry: Entry, path: str, current: str) -> list[Item]:
        return []

    def seed(self, plan: Plan, entry: Entry, templates: Path | None) -> str | None:
        return None

    def lock_argv(
        self, plan: Plan, item: Item, touched: Sequence[str]
    ) -> list[str] | None:
        return None


class WholeHandler(BaseHandler):
    """``whole``: the file is the template's, apart from its local regions."""

    name = "whole"
    strategies = ("whole",)

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        pre = self.whole_precheck(plan, entry, path, current)
        if pre is not None:
            return [pre]
        wanted = whole_text(plan, entry, path, current, templates)
        refused = first_opt_in(plan, entry, path, current, wanted)
        if refused is not None:
            return [refused]
        product = plan.product
        if product.hooks.semantic_compare(plan.ctx) and semantic.equal(
            product, path, current, wanted
        ):
            # The checkout formats its data files with its own tool.
            return [item(entry, path, current, current)]
        return [item(entry, path, current, wanted)]

    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """Delete the file when it still opens with the managed header and its regions
        are empty (content there is exit 2); a headerless file only when it is what the
        module renders and the module was on."""
        product = plan.product
        head = entry_header(plan, entry)
        if head:
            if not current.startswith(head):
                return None  # the checkout's own file of that name
            filled = (
                regions.filled(
                    product,
                    current,
                    path,
                    entry.local_regions,
                    leader=regions.leader_of(product, entry.header),
                )
                if entry.local_regions
                else []
            )
            if filled:
                return Item(
                    path,
                    entry.strategy,
                    current,
                    current,
                    f"its module is off, but the local region {', '.join(filled)} holds"
                    f" content: move it out (or delete {path}) before turning the module"
                    " off",
                    INVALID,
                    phase=entry.phase,
                )
            return Item(
                path,
                entry.strategy,
                current,
                None,
                "file should not exist (its module is off)",
                DRIFT,
                phase=entry.phase,
            )
        # A headerless (data) file: sync's when it is what the module renders, and the
        # module was on.
        if not engine.was_on(plan, entry):
            return None
        try:
            would = whole_text(plan, entry, path, current, plan.templates)
        except Exception:  # noqa: BLE001 — a file that no longer renders is not sync's
            would = None
        if would is not None and (
            would == current or semantic.equal(product, path, current, would)
        ):
            return Item(
                path,
                entry.strategy,
                current,
                None,
                "file should not exist (its module is off)",
                DRIFT,
                phase=entry.phase,
            )
        if engine.on_at_head(plan, entry):
            plan.notices.append(
                f"{path} is the checkout's now that its module is off (it differs from"
                " what sync wrote)"
            )
        return None

    def absent(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """Delete the file only when sync wrote it as it is."""
        pre = self.whole_precheck(plan, entry, path, current)
        if pre is not None:
            return pre
        product = plan.product
        try:
            would: str | None = whole_text(plan, entry, path, current, plan.templates)
        except Exception:  # noqa: BLE001 — as in retract
            would = None
        # What a render under the previous context produced is not knowable here, but a
        # file that still opens with the managed header and holds nothing in its local
        # regions is one nobody edited (the header says not to): it is sync's to delete.
        head = entry_header(plan, entry)
        if head and current.startswith(head):
            try:
                untouched = not regions.bodies(
                    product,
                    current,
                    path,
                    entry.local_regions,
                    leader=regions.leader_of(product, entry.header),
                ).strip()
            except ConfigError:
                untouched = False
            if untouched:
                would = current
        if would is not None and would == current:
            return Item(
                path,
                entry.strategy,
                current,
                None,
                "should not exist (sync wrote it; its condition no longer holds)",
                DRIFT,
                phase=entry.phase,
            )
        return Item(
            path,
            entry.strategy,
            current,
            current,
            "file should not exist, and it is not what sync wrote (no managed header,"
            f" or edited), so sync leaves it: delete {path} if the checkout no longer"
            " needs it",
            DRIFT,
            phase=entry.phase,
        )


class SeedHandler(BaseHandler):
    """``seed``: written when missing, the checkout's from then on (``validate``
    applies). With ``command``, the file is made by that command: missing, or short of
    what ``lock_problem`` asks, is drift that runs it."""

    name = "seed"
    strategies = ("seed",)

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        if entry.command:
            problem = "" if current is None else self.lock_problem(plan, entry, path)
            if current is None or problem:
                problem = problem or "missing"
                return [
                    item(entry, path, current, current, problem, DRIFT, entry.command)
                ]
            return [item(entry, path, current, current)]
        if current is None:
            return [item(entry, path, current, self.seed_text(plan, entry, templates))]
        return [
            item(entry, path, current, current),
            *self.validate(plan, entry, path, current),
        ]

    def lock_problem(self, plan: Plan, entry: Entry, path: str) -> str:
        """What a command seed on disk lacks (``""`` for nothing): drift that reruns it."""
        return ""

    def seed_text(self, plan: Plan, entry: Entry, templates: Path | None) -> str:
        """``seed``'s text, else the template rendered (with the header an
        ``[[extra-files]]`` seed names)."""
        text = self.seed(plan, entry, templates)
        if text is not None:
            return text
        text = rendering.render(
            plan.product,
            entry.template or "",
            plan.ctx,
            None,
            directory=template_dir(entry, templates),
        )
        if entry.profile_template and entry.header != "none":
            return rendering.header(plan.product, entry.header, False) + text
        return text

    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """A seed is the checkout's from the moment it exists: left, and said so on the
        run that turns its module off."""
        if engine.on_at_head(plan, entry) and not entry.command:
            plan.notices.append(
                f"{path} stays: its module is off now, and the file is the checkout's"
            )
        return None


def _lock_dir(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return parent or "."


class UvLockHandler(SeedHandler):
    """``uv-lock``: a ``uv.lock`` made by ``uv lock``; every floor of the entry's
    ``floors`` kind must be met (``packages`` keeps the floors of the packages the
    project declares; ``None`` keeps them all)."""

    name = "uv-lock"

    def __init__(self, packages: Callable[[NS], Collection[str]] | None = None) -> None:
        self.packages = packages

    def floors(self, plan: Plan, entry: Entry) -> dict[str, str]:
        table = dict(plan.ctx.floors.get(entry.floors, {})) if entry.floors else {}
        if self.packages is not None:
            wanted = set(self.packages(plan.ctx))
            table = {pkg: v for pkg, v in table.items() if pkg in wanted}
        return table

    def lock_problem(self, plan: Plan, entry: Entry, path: str) -> str:
        short = floors.lock_shortfalls(plan.root / path, self.floors(plan, entry))
        return (
            f"below its floor or missing from the lock: {', '.join(short)}"
            if short
            else ""
        )

    def lock_argv(
        self, plan: Plan, item: Item, touched: Sequence[str]
    ) -> list[str] | None:
        argv = ["uv", "lock", "--project", _lock_dir(item.path)]
        lock = plan.root / item.path
        if lock.is_file() and item.entry is not None:
            for pkg in floors.lock_shortfalls(lock, self.floors(plan, item.entry)):
                argv += ["--upgrade-package", pkg]
        return argv


class NpmLockHandler(SeedHandler):
    """``npm-lock``: a ``package-lock.json`` made by ``npm install --package-lock-only``
    in the lock's directory."""

    name = "npm-lock"

    def lock_argv(
        self, plan: Plan, item: Item, touched: Sequence[str]
    ) -> list[str] | None:
        argv = [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ]
        directory = _lock_dir(item.path)
        return argv if directory == "." else [*argv, "--prefix", directory]


class BlameIgnoreRevsHandler(SeedHandler):
    """``blame-ignore-revs``: a seed whose lines must each name a reachable commit."""

    name = "blame-ignore-revs"

    def validate(self, plan: Plan, entry: Entry, path: str, current: str) -> list[Item]:
        return [
            Item(path, "seed", current, current, problem, DRIFT, phase=entry.phase)
            for problem in blame_problems(plan.root, current)
        ]


class BlocksHandler(BaseHandler):
    """``blocks``: a product's marker-delimited blocks in a file the checkout owns.
    ``render`` must be overridden; ``absent`` leaves the file (the blocks wait for the
    condition to hold again)."""

    name = "blocks"
    strategies = ("blocks",)


class _Unserved(BaseHandler):
    """A strategy name no core handler serves on its own: a product bug to use it."""

    def __init__(self, name: str, why: str) -> None:
        self.name = name
        self.strategies = (name,)
        self.why = why


def core_handlers() -> dict[str, Handler]:
    """The core's registry: a base handler per strategy name (``CORE_HANDLERS``)."""
    groups = "a {s} entry names the product's handler (registered in Product.handlers)"
    return {
        "whole": WholeHandler(),
        "seed": SeedHandler(),
        "uv-lock": UvLockHandler(),
        "npm-lock": NpmLockHandler(),
        "blame-ignore-revs": BlameIgnoreRevsHandler(),
        "toml-merge": _Unserved("toml-merge", groups.format(s="toml-merge")),
        "json-merge": _Unserved("json-merge", groups.format(s="json-merge")),
        "jsonc-merge": _Unserved("jsonc-merge", "no jsonc-merge handler is available"),
        "blocks": BlocksHandler(),
        "sentinel": _Unserved("sentinel", "no sentinel handler is available"),
    }


def registry(product: Product) -> Mapping[str, Handler]:
    """The core's handlers and the product's (which may not shadow a core name)."""
    out: dict[str, Any] = core_handlers()
    out.update(product.handlers)
    return out


def for_entry(product: Product, entry: Entry) -> Handler:
    """``entry``'s handler: its ``handler``, else its strategy's base. An unknown name, a
    handler that does not serve the entry's strategy or a strategy only a product
    handler serves is a ``ManifestError`` (a product bug, exit 3)."""
    name = entry.handler or entry.strategy
    found = registry(product).get(name)
    where = f"manifest.d/{entry.fragment}: {entry.path}"
    if found is None:
        raise ManifestError(f"{where}: no handler {name!r} is registered")
    if isinstance(found, _Unserved):
        raise ManifestError(f"{where}: {found.why}")
    if entry.strategy not in found.strategies:
        raise ManifestError(
            f"{where}: handler {name!r} serves {', '.join(found.strategies)}, not"
            f" {entry.strategy}"
        )
    return found
