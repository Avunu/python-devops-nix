"""The product plugin contract. See DESIGN.md §3."""

from __future__ import annotations

import argparse
import re
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from standards_core.ci.verdict import DecisionRule
    from standards_core.commands.policy import PrContext
    from standards_core.commands.repo.audit import Audit, RowSpec
    from standards_core.commands.repo.doctor import Row as DoctorRow
    from standards_core.commands.repo.targets import Target
    from standards_core.common.config import Resolved
    from standards_core.scaffold.bootstrap import Runner
    from standards_core.scaffold.context import NS, Checkout
    from standards_core.scaffold.engine import Item, Plan
    from standards_core.scaffold.handlers import Handler

Level = Literal["major", "minor", "patch"]
# discovery facts of one checkout: core (§4.7) plus hooks.discover
Facts = Mapping[str, Any]
# function defaults only, never a dataclass field default
EMPTY: Mapping[str, Any] = MappingProxyType({})


def mapping_field() -> Any:
    """An empty read-only mapping as a dataclass field default.

    A `MappingProxyType` is unhashable on Python 3.11, and `@dataclass` refuses an
    unhashable default ("mutable default … use default_factory"), so it is built per instance.
    """
    return field(default_factory=lambda: MappingProxyType({}))


def default_committed_argv(root: Path) -> list[str]:
    """`committed` from the tools project when `tools/pyproject.toml` exists, else from PATH."""
    if (root / "tools" / "pyproject.toml").is_file():
        return ["uv", "run", "--frozen", "--project", str(root / "tools"), "committed"]
    return ["committed"]


FIRST_OPT_IN_MESSAGE = (
    "{path} is the checkout's own and differs from what {name} writes: sync would replace it"
    " with the managed file. {hint} then pass --force to replace it. The replacement:\n{diff}"
)

# The modules whose parameters the core reads (§4.2); every product lists them all.
CORE_MODULES: tuple[str, ...] = (
    "dev-shell",
    "commits",
    "releases",
    "dependabot",
    "ci",
    "repo-policy",
)
# Names the core registers itself, which a product's mappings may not shadow (§3.2).
CORE_VERSION_SCHEMES: tuple[str, ...] = ("semver",)
CORE_BRANCHING: tuple[str, ...] = ()
CORE_HANDLERS: tuple[str, ...] = (
    "whole",
    "seed",
    "uv-lock",
    "npm-lock",
    "blame-ignore-revs",
    "toml-merge",
    "json-merge",
    "jsonc-merge",
    "blocks",
    "sentinel",
)

_SLUG = re.compile(r"[a-z][a-z0-9-]*")
_KEBAB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_ENV_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*")
_REPORT_KEY = re.compile(r"[a-z][a-z0-9_]*")
_ALIAS_TARGET = re.compile(r"--[a-z0-9][a-z0-9-]*|--(?:param|set) [a-z0-9][a-z0-9._-]*")
_OPT_IN_FIELDS = frozenset({"path", "name", "hint", "diff"})


def core_data_root() -> Path:
    """The core's own data directory, which every product's roots end with (§4.6)."""
    return files("standards_core").joinpath("data")  # type: ignore[return-value]


def _format_fields(template: str) -> set[str] | None:
    """The field names ``template`` formats, or ``None`` when it is not a format string."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        return None
    return {
        re.split(r"[.\[]", f, maxsplit=1)[0] for _, f, _, _ in parsed if f is not None
    }


# --- identity, markers, engine, pins, paths ------------------------------------------


@dataclass(frozen=True)
class Identity:
    name: str  # "kit-nix": message prefix ("kit-nix sync: …"), clone prefixes
    cli: str  # "kit-nix": console script; argparse prog; `<cli> repo doctor` in hints
    distribution: str  # "kit-nix-tools": what `uv tool install` names
    # the product tools' __version__ (requires-<name> is checked against it)
    version: str
    table: str  # "kit-nix": [tool.<table>], also the dev shell's opt-in line match
    env_prefix: str  # "KIT_NIX": every variable the core reads is f"{env_prefix}_<X>"
    report_key: str  # "kit_nix": the JSON report key and the context alias of `engine`
    sync_command: str  # "kit-init --sync": the fix named in drift output
    opt_in_hint: str  # the NotOptedIn message (exit 2)
    docs_url: str  # printed by --help
    # the default file holding [tool.<table>], relative to the root
    config_file: str = "pyproject.toml"
    # every file the table may live in, relative to the root; () → (config_file,)
    config_paths: tuple[str, ...] = ()
    # False: a missing config_file reads as {} (not opted in; --standards creates it)
    config_required: bool = True
    # argparse description; "" → f"{name} standards tools. See {docs_url}."
    description: str = ""

    @property
    def requires_key(self) -> str:
        """``requires-<name>`` ("requires-kit-nix"): the profile-only version range key."""
        return f"requires-{self.name}"


@dataclass(frozen=True)
class Markers:
    # "kit-nix:managed": first-line marker; its presence makes a file sync's
    managed: str
    header: str  # "kit-nix:managed — generated by kit-nix (`kit-init --sync`);"
    header_local: str  # "edit only inside the kit-nix:local region."
    local: str  # "kit-nix:local": regions are "<leader> kit-nix:local-begin <name>"
    block_begin: str  # ">>> kit-nix: {name} (managed) >>>"  (leader prepended, §5.6)
    block_end: str  # "<<< kit-nix: {name} (managed) <<<"
    html_begin: str  # "<!-- kit-nix:begin {name} -->"
    html_end: str  # "<!-- kit-nix:end {name} -->"
    header_whole: str = "do not edit."


@dataclass(frozen=True)
class HeaderKind:
    leader: str  # "#", "//" or "<!--"
    closer: str = ""  # "-->" for html


# The header kinds every product has (§5.1); `product.header_kinds` adds to them.
CORE_HEADER_KINDS: Mapping[str, HeaderKind] = MappingProxyType(
    {
        "yaml": HeaderKind("#"),
        "toml": HeaderKind("#"),
        "shell": HeaderKind("#"),
        "ini": HeaderKind("#"),
        "nix": HeaderKind("#"),
        "jsonc": HeaderKind("//"),
        "ts": HeaderKind("//"),
        "none": HeaderKind(""),
    }
)


def header_kind(product: Product, kind: str) -> HeaderKind | None:
    """The header kind named ``kind``: the core's, else the product's; ``None`` when unknown."""
    return CORE_HEADER_KINDS.get(kind) or product.header_kinds.get(kind)


@dataclass(frozen=True)
class Engine:
    flake_input: str  # "kit-nix": the root input every checkout locks
    repo: str  # "example/kit-nix": owner/name when the lock names none
    release_branch: str  # "release-{major}": the moving branch the default URL follows
    url_key: str  # "dev-shell.kit-nix-url": cfg key that replaces the default flake URL
    # "kit-nix-tools @
    # git+https://github.com/{owner}/{repo}@{rev}#subdirectory=py/kit_nix_tools"
    install_spec: str
    # "kit-*": reusable workflow file stem glob, matched as <stem>.ya?ml
    caller_stem: str
    # "kit-init": flake app run as `nix run --no-pure-eval .#<init_app> -- --sync`
    init_app: str
    # read from the locked engine tree to decide a re-exec
    version_file: str = "version.txt"
    tool_python: str = "3.12"  # `uv tool run --python` for audit's pinned tool runs
    # the engine's input that locks this core (§9.4)
    base_input: str = "python-devops-nix"


@dataclass(frozen=True)
class Pins:
    dir: str  # ".dev-dist/pins": where pin-path fetches, relative to the lock
    # input → (owner, repo) read only via the engine node
    engine_pinned: Mapping[str, tuple[str, str]] = mapping_field()
    # lock files sync's steps make, besides flake.lock (untracked check)
    lock_paths: tuple[str, ...] = ()
    # inputs a local region may not define
    managed_inputs: Callable[[NS], tuple[str, ...]] = lambda ctx: ()


@dataclass(frozen=True)
class Paths:
    # dirs refused in any managed path; ".git" is always added
    never_write: frozenset[str] = frozenset()
    # ctx.unmanaged_dirs: dirs a checkout-walking hook must skip
    not_source: tuple[str, ...] = ()
    # paths whose presence means "set up for GitHub" (history)
    github_marks: tuple[str, ...] = (".github",)


# --- modules, data, schemas -------------------------------------------------------------


@dataclass(frozen=True)
class Modules:
    # every module, in documentation order; must include CORE_MODULES (§4.2)
    names: tuple[str, ...]
    # module → modules it needs on (exit 2 otherwise)
    needs: Mapping[str, tuple[str, ...]] = mapping_field()
    # off (with a notice) on a checkout hosted elsewhere
    github_only: tuple[str, ...] = ()
    # top-level keys only a profile sets, beyond the core's
    profile_only: tuple[str, ...] = ()
    # top-level keys only the checkout's table sets, beyond the core's
    app_only: tuple[str, ...] = ()
    # org-only lists copied into cfg, beyond retire/extra-files
    resolved_from_profile: tuple[str, ...] = ()
    # the module every opted-in checkout has on (enable = false is exit 2)
    required: str = "dev-shell"


@dataclass(frozen=True)
class Schemas:
    # $defs/modules/<m>, org-extra, profile-extra, app-extra (§4.1)
    modules: str = "modules.schema.json"
    fleet_entry: str = "fleet-entry.schema.json"  # one fleet entry; must declare "repo"


@dataclass(frozen=True)
class Fixture:
    table: str  # extra [tool.<table>] lines
    # path → text written into the throwaway checkout
    files: Mapping[str, str] = mapping_field()
    # skip unless these modules are on under the profile alone
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class FirstOptIn:
    path: str  # "flake.nix": an unmanaged file sync would replace
    hint: str  # where the checkout's own content goes instead (printed with the diff)
    # texts replaced silently
    legacy: Callable[[str, Checkout], list[str]] = lambda current, checkout: []
    # format fields: {diff}, and any of {path}, {name} (identity.name), {hint}
    message: str = FIRST_OPT_IN_MESSAGE


@dataclass(frozen=True)
class JsonStyle:
    """How jsonfmt writes JSON: the default is oxfmt's form (tabs, width 110)."""

    indent: str = "\t"  # one level; "  " for prettier
    tab_width: int = 2  # a tab's width when measuring a line
    width: int = 110  # print width
    # "jsonc": after the last member of broken JSONC containers
    trailing_commas: Literal["jsonc", "none"] = "jsonc"


@dataclass(frozen=True)
class Branching:
    name: str  # "develop+version" | "main+tags" | "develop+series"
    flow: Literal["tags", "post-merge-bump", "none"]  # what moves the release branch
    # integration-branch default (cfg → name)
    integration: Callable[[Mapping[str, Any]], str]
    # (cfg, facts) → "" when there is none
    release_branch: Callable[[Mapping[str, Any], Facts], str]
    # ruleset ref include, "refs/heads/*.0"; "" → no release ruleset
    release_pattern: str = ""
    release_label: str = ""  # ruleset name stem, "*.0"
    tags: bool = False  # the tags ruleset and audit rows A2/A11 apply


# --- protocols ----------------------------------------------------------------------


class VersionScheme(Protocol):
    """Every method gets the resolved cfg and the checkout's facts (a series file, the lock, …)."""

    name: str  # "semver", "kit-series"

    def valid(self, version: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        """The problem with `version` under this scheme, or None."""

    def bump(
        self, version: str, level: Level, cfg: Mapping[str, Any], facts: Facts
    ) -> str:
        """`version` raised by `level` (a bump of x resets y and z)."""

    def message_problems(
        self, text: str, cfg: Mapping[str, Any], facts: Facts
    ) -> list[str]:
        """Commit-message and title rules this scheme adds; [] for none."""

    def tag_problem(self, tag: str, cfg: Mapping[str, Any], facts: Facts) -> str | None:
        """Why the fast-forward must not move to `tag` (checked before any write), or None."""


@dataclass(frozen=True)
class ReleaseUnit:
    name: str  # "kit_alpha": named in commit subjects
    path: str  # "kit_alpha/": commits touching it count
    version_file: str  # "kit_alpha/__unit__.toml": where its version lives


class ReleaseTool(Protocol):
    name: str  # "unit-manifest"
    commit_subject: str  # "chore(release): bump {units}"

    def units(self, root: Path, ctx: NS) -> list[ReleaseUnit]:
        """The units sync and bump see, from tracked files only."""

    def read_version(self, text: str, unit: ReleaseUnit) -> str | None:
        """The version in `text` (the version file at some revision)."""

    def write_version(self, text: str, unit: ReleaseUnit, version: str) -> str:
        """`text` with the version replaced, every other byte kept."""

    def exempt(self, unit: ReleaseUnit, path: str) -> bool:
        """A change to `path` alone never bumps (tests, README, i18n, …)."""


# --- hook bundles -------------------------------------------------------------------


@dataclass(frozen=True)
class Hooks:
    """Checkout, configuration and context hooks. Defaults are the core's neutral behaviour."""

    # root → Checkout; EnvError (exit 3) when it is not one
    load_checkout: Callable[[Path], Checkout]
    # facts (§4.7)
    discover: Callable[
        [Checkout, Mapping[str, Any], Mapping[str, bool]], dict[str, Any]
    ]
    # enabled gates, in product.ci.gates order
    gates: Callable[
        [Mapping[str, bool], Mapping[str, Any], Mapping[str, Any]], list[str]
    ]
    # product keys added to the context
    template_globals: Callable[[Checkout, Resolved, NS], dict[str, Any]]
    # per render, final ctx (gen)
    render_helpers: Callable[[NS, str | None], Mapping[str, Any]] = (
        lambda ctx, current: {}
    )
    # derived defaults (mutates)
    derive: Callable[[dict[str, Any], Checkout | None], None] = lambda cfg, checkout: (
        None
    )
    # None → Branching.integration
    integration_default: Callable[[Mapping[str, Any]], str] | None = None
    # None → Branching.release_branch when releases is on
    release_branch: (
        Callable[[Mapping[str, Any], Mapping[str, bool], Facts], str] | None
    ) = None
    # extra off-GitHub rules
    host_rules: Callable[[dict, Mapping, bool, list[str]], None] = (
        lambda cfg, app, gh, notices: None
    )
    # applied in order
    url_placeholders: Callable[[NS], Sequence[tuple[str, str]]] = lambda ctx: (
        ("repo", ctx.get("repo") or ""),
    )
    # raise ConfigError (exit 2)
    preflight: Callable[[Checkout, Resolved], None] = lambda checkout, resolved: None
    notices: Callable[[Plan], list[str]] = lambda plan: []  # phase-B notices
    # whole-repo problem items engine.build adds (--check and phase B)
    checks: Callable[[Plan], list[Item]] = lambda plan: []
    # how policy runs `committed`
    committed_argv: Callable[[Path], list[str]] = default_committed_argv
    # table --standards creates
    new_target_table: (
        Callable[[Path, Checkout, argparse.Namespace, str], dict[str, Any]] | None
    ) = None
    # root → the config file relative to the root, from tracked files and their text
    # only (§3.4); None → identity.config_file. One of identity.config_paths when set.
    config_file: Callable[[Path], str] | None = None
    # whole data files compared as data
    semantic_compare: Callable[[NS], bool] = lambda ctx: False
    # extra pr-policy problems
    pr_checks: Callable[[PrContext], list[str]] = lambda pr: []
    # profile validate's throwaway checkout
    write_fixture: Callable[[Path, str, Fixture, str], None] | None = None
    # app layer for a not-opted-in target
    fleet_table: Callable[[str, Mapping[str, Any], str], dict[str, Any]] = (
        lambda repo, entry, ref: {}
    )
    # {documentation}
    documentation_url: Callable[[Target], str] = lambda t: t.entry.get(
        "documentation", ""
    )
    # None → engine.install_spec at the locked rev
    tool_spec: Callable[[Audit], str | None] | None = None
    repo_policy_values: Callable[
        [Mapping, Mapping, Sequence[str]], Mapping[str, Any]
    ] = lambda cfg, modules, gates: {}
    # None → core selection (§7.3)
    rulesets: Callable[[Mapping, Mapping, str, str], list[str]] | None = None
    doctor_rows: Callable[[str, Any], tuple[list[DoctorRow], list[str]]] = (
        lambda repo, rc: ([], [])
    )


@dataclass(frozen=True)
class SyncHooks:
    """The sync phases' product steps (§6.3)."""

    # once per core flag slot (§6.2)
    add_arguments: Callable[[argparse.ArgumentParser, str], None] = (
        lambda parser, after: None
    )
    # merged into ctx.options
    options: Callable[[argparse.Namespace], dict[str, Any]] = lambda args: {}
    # product flags a handover repeats
    passthrough: Callable[[argparse.Namespace, bool], list[str]] = lambda args, init: []
    # None → nix run --no-pure-eval .#<init_app> -- --sync <argv>
    reexec_argv: Callable[[list[str]], list[str]] | None = None
    # None → nix develop --no-pure-eval -c <cli> sync --write --phase b <argv>
    reentry_argv: Callable[[list[str]], list[str]] | None = None
    # phase B re-enters the dev shell when one is missing
    required_tools: tuple[str, ...] = ("uv",)
    before_reentry: Callable[[Runner], None] = lambda runner: None
    phase_a_before: Callable[[Plan, Runner, argparse.Namespace], None] = (
        lambda plan, runner, args: None
    )
    phase_a_after: Callable[[Plan, Runner, argparse.Namespace, bool], None] = (
        lambda plan, runner, args, changed: None
    )
    # formatters; returns more touched
    post_write: Callable[[Plan, Runner, list[str]], list[str]] = (
        lambda plan, runner, touched: []
    )
    phase_b_after: Callable[
        [Plan, Runner, argparse.Namespace, bool, list[str]], list[str]
    ] = lambda plan, runner, args, changed, touched: []
    # argv run after `nix flake update` in repo rollout
    rollout_steps: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class CiConfig:
    # every gate name, in required-check order
    gates: tuple[str, ...] = ("pr-policy", "lint", "test")
    check_context: str = "ci / {gate}"  # the required context of a gate
    # dispatch-ci defaults
    contexts: tuple[tuple[str, str], ...] = (
        ("ci / lint", "ci.yml"),
        ("ci / pr-policy", "pr-policy.yml"),
    )
    # name → (color, description, module); "" module = always
    labels: Mapping[str, tuple[str, str, str]] = mapping_field()
    decision_table: tuple[DecisionRule, ...] = ()  # §8.3
    # audit A5's workflow; under post-merge-bump it must run on push (§11.4)
    ci_workflow: str = "ci.yml"
    nightly_workflow: str = ""  # audit A5's nightly; "" → none
    # old `ci` flag → "--new-flag" or "--param <k>"/"--set <k>" (§8.1)
    arg_aliases: Mapping[str, str] = mapping_field()


# --- the product --------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Product:
    identity: Identity
    markers: Markers
    engine: Engine
    pins: Pins
    modules: Modules
    # the product's data directory (importlib.resources files(pkg)/"data")
    data_root: Path
    hooks: Hooks
    sync: SyncHooks = field(default_factory=SyncHooks)
    ci: CiConfig = field(default_factory=CiConfig)
    paths: Paths = field(default_factory=Paths)
    schemas: Schemas = field(default_factory=Schemas)
    # added to the core kinds (§5.1)
    header_kinds: Mapping[str, HeaderKind] = mapping_field()
    jsonc_globs: tuple[str, ...] = ()  # full-path globs semantic.kind reads as JSONC
    # how managed JSON is written (§2.3 jsonfmt)
    json_style: JsonStyle = field(default_factory=JsonStyle)
    # added to the core filters; no shadowing
    jinja_filters: Mapping[str, Callable[..., Any]] = mapping_field()
    # handler name → instance; no shadowing of core names
    handlers: Mapping[str, Handler] = mapping_field()
    version_schemes: Mapping[str, VersionScheme] = mapping_field()  # added to "semver"
    # releases.branching values (§8.1)
    branching: Mapping[str, Branching] = mapping_field()
    # units and versions for the post-merge bump
    release_tool: ReleaseTool | None = None
    first_opt_in: tuple[FirstOptIn, ...] = ()  # §5.9
    # profile validate renders every template for each
    fixtures: Mapping[str, Fixture] = mapping_field()
    fleet_key: str = "repos"  # the fleet file's list key
    self_repo: str = ""  # `repo apply --self` target; "" → --self is exit 2
    # id → spec; adds to or replaces core rows (§7.4)
    audit_rows: Mapping[str, RowSpec] = mapping_field()
    # argparse text overrides: "<command>" or "<command> <flag>" (§6.1)
    help: Mapping[str, str] = mapping_field()

    def __post_init__(self) -> None:
        """Fail at import (a product bug, exit 3 through cli.main), never at sync."""
        problems = self._problems()
        if problems:
            raise ValueError("Product: " + "; ".join(problems))

    def _problems(self) -> list[str]:
        """Every §3.2 rule this product breaks, each naming the field."""
        out: list[str] = []
        ident = self.identity
        for name in ("name", "cli", "table"):
            value = getattr(ident, name)
            if not _SLUG.fullmatch(value):
                out.append(f"identity.{name} {value!r} is not ^[a-z][a-z0-9-]*$")
        if not _ENV_PREFIX.fullmatch(ident.env_prefix):
            out.append(
                f"identity.env_prefix {ident.env_prefix!r} is not ^[A-Z][A-Z0-9_]*$"
            )
        if not _REPORT_KEY.fullmatch(ident.report_key):
            out.append(
                f"identity.report_key {ident.report_key!r} is not ^[a-z][a-z0-9_]*$"
            )

        paths = ident.config_paths
        for rel in (ident.config_file, *paths):
            pure = PurePosixPath(rel)
            if not rel or pure.is_absolute() or ".." in pure.parts or "\\" in rel:
                out.append(f"identity config file {rel!r} is not a relative POSIX path")
        if len(set(paths)) != len(paths):
            out.append("identity.config_paths lists a path twice")
        if paths and ident.config_file not in paths:
            out.append(
                f"identity.config_file {ident.config_file!r} is not one of"
                " identity.config_paths"
            )

        mods = self.modules
        names = mods.names
        seen: set[str] = set()
        for name in names:
            if name in seen:
                out.append(f"modules.names lists {name!r} twice")
            seen.add(name)
            if not _KEBAB.fullmatch(name):
                out.append(f"modules.names: {name!r} is not kebab-case")
        missing = [m for m in CORE_MODULES if m not in seen]
        if missing:
            out.append(f"modules.names lacks the core modules {', '.join(missing)}")
        for module, needed in mods.needs.items():
            for name in (module, *needed):
                if name not in seen:
                    out.append(f"modules.needs names {name!r}, not a module")
        for name in mods.github_only:
            if name not in seen:
                out.append(f"modules.github_only names {name!r}, not a module")
        if mods.required not in seen:
            out.append(f"modules.required {mods.required!r} is not a module")

        for attr, core in (
            ("version_schemes", CORE_VERSION_SCHEMES),
            ("branching", CORE_BRANCHING),
            ("handlers", CORE_HANDLERS),
        ):
            for key in getattr(self, attr):
                if key in core:
                    out.append(f"{attr} key {key!r} shadows the core's")

        marks = self.markers
        for attr in ("block_begin", "block_end", "html_begin", "html_end"):
            if getattr(marks, attr).count("{name}") != 1:
                out.append(f"markers.{attr} must contain {{name}} exactly once")
        if marks.block_begin == marks.block_end:
            out.append("markers.block_begin and markers.block_end are equal")

        gates = self.ci.gates
        if len(set(gates)) != len(gates):
            out.append("ci.gates lists a gate twice")
        if "{gate}" not in self.ci.check_context:
            out.append("ci.check_context does not contain {gate}")
        for key, value in self.ci.arg_aliases.items():
            if not key.startswith("--"):
                out.append(f"ci.arg_aliases key {key!r} does not start with --")
            if not _ALIAS_TARGET.fullmatch(value):
                out.append(
                    f"ci.arg_aliases[{key!r}] = {value!r} is not --<flag>,"
                    " --param <key> or --set <key>"
                )

        if bool(self.fixtures) != (self.hooks.write_fixture is not None):
            out.append("fixtures and hooks.write_fixture must be set together")

        if not Path(self.data_root).is_dir():
            out.append(f"data_root {str(self.data_root)!r} is not a directory")

        style = self.json_style
        if not style.indent or (style.indent != "\t" and style.indent.strip(" ")):
            out.append(f"json_style.indent {style.indent!r} is not a tab or spaces")
        if style.width <= 0 or style.tab_width <= 0:
            out.append("json_style.width and json_style.tab_width must be positive")

        paths = [f.path for f in self.first_opt_in]
        if len(set(paths)) != len(paths):
            out.append("first_opt_in lists a path twice")
        for item in self.first_opt_in:
            # A product may write its hint or name inline (DESIGN §5.9), but never
            # leave the replacement out.
            fields = _format_fields(item.message)
            if fields is None or not fields <= _OPT_IN_FIELDS or "diff" not in fields:
                out.append(
                    f"first_opt_in {item.path!r}: message must format only"
                    " {path}, {name}, {hint} and {diff}, and {diff} at least"
                )
        return out

    @property
    def roots(self) -> tuple[Path, ...]:
        """``(data_root, core data root)``: the product's first (§4.6)."""
        return (Path(self.data_root), core_data_root())

    @property
    def rollout_inputs(self) -> tuple[str, str]:
        return (self.engine.flake_input, "standards-profile")
