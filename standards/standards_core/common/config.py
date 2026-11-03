"""The resolved configuration of a checkout (DESIGN §4.4): profiles, the merge, defaults and modules.

Every consumer resolves the same way: sync and ``--check``, ``<cli> config``, the CI
steps, ``repo apply`` and ``repo audit``.

1. Read ``[tool.<table>]``. No table means not opted in: ``resolve`` raises
   ``NotOptedIn`` and the caller decides what that means.
2. Load the layers, lowest first: the built-in profile (``profile`` itself when it
   names one, else the org profile's ``extends``; plain ``recommended`` is the newest
   snapshot), the org profile (from the locked ``standards-profile`` input, or
   ``./<dir>`` in the checkout's own tree), and the checkout's table.
3. Validate each layer on its own: the profile schema for the first two (app-only keys
   refused), the app schema for the third (profile-only keys refused).
4. Merge: tables key by key, recursively; scalars and arrays replaced whole. The
   ``resolved_from_profile`` lists (``[[retire]]``, ``[[extra-files]]`` and the
   product's) come only from the org profile.
5. Fill the schema defaults; set ``profile``, the ``copyright-holder`` fallback, the
   integration branch, the product's derived defaults and ``repo``; then apply the
   module rules (the required module, ``tool = "none"``, GitHub-only modules off on
   other hosts, needs), computing ``modules``.
6. Check ``requires-<product>`` against the running version (exit 3).
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from standards_core.common import flakelock, pyproject, repo, schema
from standards_core.common.data import product_roots
from standards_core.common.pins import pin_path
from standards_core.common.report import ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.context import Checkout

PROFILE_SCHEMA = schema.PROFILE
APP_SCHEMA = schema.APP

# The top-level keys only a profile sets, and only the checkout's table sets (DESIGN
# §4.2); the product adds to both. `schema` is in neither: both have their own.
CORE_PROFILE_ONLY = ("name", "description", "extends", "retire", "extra-files")
CORE_APP_ONLY = ("profile", "repo", "integration-branch", "retire-keep")
# The resolved lists only the org profile sets: in ``cfg``, never in the checkout's table.
CORE_RESOLVED_FROM_PROFILE = ("retire", "extra-files")
ORG_PROFILE_PREFIXES = ("github:", "gitlab:", "git+https://")


def profile_only(product: Product) -> tuple[str, ...]:
    """The top-level keys only a profile may set: the core's, ``requires-<name>``, the product's."""
    core = (*CORE_PROFILE_ONLY, product.identity.requires_key)
    return tuple(dict.fromkeys((*core, *product.modules.profile_only)))


def app_only(product: Product) -> tuple[str, ...]:
    """The top-level keys only the checkout's table may set."""
    return tuple(dict.fromkeys((*CORE_APP_ONLY, *product.modules.app_only)))


def resolved_from_profile(product: Product) -> tuple[str, ...]:
    """The lists only the org profile sets, copied into ``cfg``."""
    extra = product.modules.resolved_from_profile
    return tuple(dict.fromkeys((*CORE_RESOLVED_FROM_PROFILE, *extra)))


class NotOptedIn(ConfigError):
    """Exit 2: the checkout has no ``[tool.<table>]`` table."""

    def __init__(self, product: Product) -> None:
        super().__init__(pyproject.opt_in_hint(product))


@dataclass
class Resolved:
    """The ``cfg``, ``modules`` and ``profile`` of the template context (DESIGN §3.5).

    ``sources`` maps each dotted key that a layer set to that layer's label
    (``builtin:recommended@1.0``, ``org:<source>@<rev>``, ``app``); every other key came
    from the schema's ``default``. ``repo_host`` is the host of ``cfg["repo"]``
    (``github.com`` unless it names another), or ``None`` when the repository is unknown.
    """

    cfg: dict
    modules: dict[str, bool]
    profile: dict
    sources: dict[str, str] = field(default_factory=dict)
    repo_host: str | None = None
    notices: list[str] = field(default_factory=list)
    product: Product | None = field(default=None, repr=False, compare=False)

    def source(self, key: str) -> str:
        """The layer that set ``key`` (or one of its parents), else ``default``."""
        parts = key.split(".")
        for n in range(len(parts), 0, -1):
            found = self.sources.get(".".join(parts[:n]))
            if found:
                return found
        return "default"

    def get(self, key: str) -> Any:
        """The resolved value of a dotted key: a ``cfg`` key, ``modules[.<m>]`` or ``profile[.<k>]``.

        A ``cfg`` key is one of the app schema's, or a ``resolved_from_profile`` list,
        which only the org profile sets.
        """
        if self.product is None:
            raise RuntimeError("Resolved.get needs the product it was resolved for")
        head, _, rest = key.partition(".")
        roots = {"modules": self.modules, "profile": self.profile}
        if head in roots:
            value: Any = roots[head]
            if rest and (not isinstance(value, dict) or rest not in value):
                raise ConfigError(f"no key {key!r}")
            return value[rest] if rest else value
        if head in resolved_from_profile(self.product):
            # Lists the org profile alone sets; not in the checkout's schema.
            if rest:
                raise ConfigError(f"no key {key!r}: {head} is a list")
            return self.cfg.get(head, [])
        if schema.node_at(self.product, APP_SCHEMA, key) is None:
            table = self.product.identity.table
            raise ConfigError(f"[tool.{table}] has no key {key!r}")
        value = self.cfg
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value


# --- built-in profiles -------------------------------------------------------------------


@cache
def _snapshots(product: Product) -> dict[Version, str]:
    """``recommended@<minor>`` files, by their version."""
    out = {}
    for name in product_roots(product).listdir("profiles"):
        stem = name.removesuffix(".toml")
        if name.endswith(".toml") and stem.startswith("recommended@"):
            try:
                out[Version(stem.removeprefix("recommended@"))] = stem
            except InvalidVersion:
                continue
    return out


def builtin_names(product: Product) -> list[str]:
    """``minimal`` and every ``recommended@<minor>`` snapshot, oldest first."""
    snapshots = _snapshots(product)
    return ["minimal", *(snapshots[v] for v in sorted(snapshots))]


def builtin_name(product: Product, name: str) -> str:
    """The file stem a built-in profile name means: plain ``recommended`` is the newest snapshot."""
    snapshots = _snapshots(product)
    if name == "recommended":
        if not snapshots:
            raise ConfigError(f"{product.identity.name} ships no recommended snapshot")
        return snapshots[max(snapshots)]
    if name in builtin_names(product):
        return name
    names = ", ".join(["recommended", *builtin_names(product)])
    raise ConfigError(f"no built-in profile {name!r} (built-ins: {names})")


@cache
def _builtin_doc(product: Product, stem: str) -> dict:
    path = product_roots(product).path(f"profiles/{stem}.toml")
    return tomllib.loads(path.read_text())


def builtin_profile(product: Product, name: str) -> tuple[str, dict]:
    """``(stem, document)`` of a built-in profile; an unknown name is a ``ConfigError``."""
    stem = builtin_name(product, name)
    return stem, copy.deepcopy(_builtin_doc(product, stem))


def is_builtin(profile: str) -> bool:
    return profile in ("minimal", "recommended") or profile.startswith("recommended@")


# --- layers ------------------------------------------------------------------------------


def validate_profile(product: Product, doc: dict, label: str, *, org: bool) -> None:
    """A profile document against the profile schema, with the key rules of §4.4."""
    table = product.identity.table
    only = app_only(product)
    errors = []
    for key in doc:
        if key in only:
            errors.append(
                f"{label}.{key}: an app-only key; set it in the checkout's [tool.{table}]"
            )
    if org and "extends" not in doc:
        errors.append(f"{label}: extends is required in an org profile")
    if not org and "extends" in doc:
        errors.append(f"{label}: a built-in profile has no extends")
    errors += schema.Validator(
        product, PROFILE_SCHEMA, label, app_only_allowed=False
    ).errors({k: v for k, v in doc.items() if k not in only})
    if errors:
        raise ConfigError("invalid profile:\n  " + "\n  ".join(errors))


def validate_app(product: Product, table: dict) -> None:
    """``[tool.<table>]`` against the app schema, with profile-only keys named as such."""
    where = f"[tool.{product.identity.table}]"
    only = profile_only(product)
    errors = [
        f"{where}.{key}: a profile-only key; a single checkout that needs it uses an"
        ' in-repo profile (profile = "./<dir>")'
        for key in table
        if key in only
    ]
    errors += schema.Validator(product, APP_SCHEMA, where).errors(
        {k: v for k, v in table.items() if k not in only}
    )
    if errors:
        raise ConfigError("invalid configuration:\n  " + "\n  ".join(errors))


def read_profile_dir(directory: Path, label: str) -> dict:
    path = directory / "profile.toml"
    if path.is_symlink():
        raise ConfigError(
            f"profile {label}: profile.toml is a symlink: a profile is read only from"
            " regular files"
        )
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"profile {label}: {path} does not exist") from e
    except (OSError, UnicodeDecodeError) as e:
        raise EnvError(f"profile {label}: {path} is unreadable: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"profile {label}: {path}: {e}") from e


def org_profile_dir(
    product: Product, profile: str, root: Path, lock_path: Path | None = None
) -> tuple[Path, str]:
    """``(directory, rev)`` of the org profile ``profile`` names.

    ``./<dir>`` is the checkout's own tree (no rev). A flake URL is read from the locked
    ``standards-profile`` input of the checkout's ``flake.lock``: the Nix store path when
    present, else fetched and narHash-verified (``pin-path``).
    """
    if profile.startswith("./"):
        rel = PurePosixPath(profile)
        if ".." in rel.parts or rel.is_absolute():
            raise ConfigError(
                f"profile {profile!r}: an in-repo profile must stay inside the checkout"
            )
        # Part of the pull request --check runs on: a committed link (``.lnk ->
        # ../elsewhere``) would read a profile, and print templates, from outside it.
        cur = root
        for i, part in enumerate(rel.parts):
            cur = cur / part
            if cur.is_symlink():
                raise ConfigError(
                    f"profile {profile!r}: {PurePosixPath(*rel.parts[: i + 1])} is a"
                    " symlink: an in-repo profile is never read through a link (remove"
                    " it)"
                )
        return root / rel, ""
    if not profile.startswith(ORG_PROFILE_PREFIXES):
        raise ConfigError(
            f"profile {profile!r} is not a built-in, a github:/gitlab:/git+https:// URL"
            " or ./<dir>"
        )
    lock_path = lock_path or root / "flake.lock"
    lock = flakelock.load(lock_path)
    name = flakelock.PROFILE_INPUT
    if flakelock.input_node(product, lock, name) is None:
        raise ConfigError(
            f"profile {profile!r} is not locked: {lock_path} has no {name} input; run"
            f" {product.identity.sync_command}"
        )
    rev = flakelock.locked_rev(product, lock, name) or ""
    return pin_path(product, name, lock_path), rev


def _record(table: dict, label: str, sources: dict[str, str], prefix: str) -> None:
    for key, value in table.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            _record(value, label, sources, path)
        else:
            sources[path] = label


# --- merge -------------------------------------------------------------------------------


def _merge(
    base: Any, over: Any, label: str, sources: dict[str, str], prefix: str = ""
) -> Any:
    """``over`` merged onto ``base``: tables key by key, everything else replaced whole."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            path = f"{prefix}.{key}" if prefix else key
            out[key] = _merge(base.get(key), value, label, sources, path)
        return out
    if isinstance(over, dict):
        _record(over, label, sources, prefix)
    else:
        sources[prefix] = label
    return copy.deepcopy(over)


def _fill_defaults(cfg: Any, defaults: Any) -> Any:
    if isinstance(cfg, dict) and isinstance(defaults, dict):
        out = dict(cfg)
        for key, value in defaults.items():
            out[key] = (
                _fill_defaults(out[key], value) if key in out else copy.deepcopy(value)
            )
        return out
    return cfg


# --- modules -----------------------------------------------------------------------------


def _switch(cfg: dict, module: str) -> bool:
    table = cfg.get(module, {})
    return bool(table.get("enable")) and table.get("tool") != "none"


def _app_turns_off(app: dict, module: str) -> bool:
    """Whether the checkout's own table turns ``module`` off, which no profile can undo."""
    table = app.get(module, {})
    return table.get("enable") is False or table.get("tool") == "none"


def _modules(
    product: Product,
    cfg: dict,
    app: dict,
    on_github: bool,
    notices: list[str],
    *,
    provisional: bool = False,
) -> dict[str, bool]:
    """``modules``: each module's effective switch under the module rules.

    ``provisional`` is phase A's resolution of an org profile it cannot read yet:
    ``cfg`` stands on ``minimal``, not on the profile, so a need it leaves off may be
    one the profile turns on. Such a module stays as the checkout set it, and the full
    resolution after the lock checks the need; only a need the checkout's own table
    turns off is refused now.
    """
    mods = product.modules
    required = mods.required
    if not _switch(cfg, required):
        raise ConfigError(
            f"module {required} is required: every opted-in checkout has it"
            " (enable = false is invalid)"
        )
    on = {m: _switch(cfg, m) for m in mods.names}
    host_off: set[str] = set()
    if not on_github:
        for m in mods.github_only:
            if app.get(m, {}).get("enable") is True:
                raise ConfigError(
                    f"module {m} is GitHub-only and this repository is hosted"
                    " elsewhere; remove its enable = true"
                )
            if on[m]:
                host_off.add(m)
                on[m] = False
    product.hooks.host_rules(cfg, app, on_github, notices)
    if host_off:
        notices.append(
            "repository is not on GitHub: GitHub-only modules off:"
            f" {', '.join(sorted(host_off))}"
        )
    changed = True
    while changed:
        changed = False
        for m in mods.names:
            if not on[m]:
                continue
            for need in mods.needs.get(m, ()):
                if on[need]:
                    continue
                if need in host_off and app.get(m, {}).get("enable") is not True:
                    on[m] = False
                    host_off.add(m)
                    changed = True
                    break
                if provisional and not _app_turns_off(app, need):
                    continue
                raise ConfigError(
                    f"module {m} needs module {need}, which is off: turn {need} on or"
                    f" {m} off"
                )
    return on


# --- resolve -----------------------------------------------------------------------------


def _repo(
    cfg: dict, root: Path | None, name: str | None
) -> tuple[str | None, str | None]:
    """``(repo, host)``: the table's, else origin's, else ``<org.github-owner>/<name>``."""
    explicit = cfg.get("repo")
    if explicit:
        parts = explicit.split("/")
        host = parts[0].lower() if len(parts) > 2 and "." in parts[0] else "github.com"
        return (explicit if host == "github.com" else "/".join(parts[1:])), host
    origin = repo.origin_repo(root) if root is not None else None
    if origin:
        host, path = origin
        return path, host
    owner = cfg.get("org", {}).get("github-owner")
    if owner and name:
        return f"{owner}/{name}", "github.com"
    return None, None


def check_requires(product: Product, spec: str | None, label: str) -> None:
    """``requires-<name>`` must admit the running product version (exit 3 otherwise)."""
    if not spec:
        return
    identity = product.identity
    try:
        admits = SpecifierSet(spec).contains(
            Version(identity.version), prereleases=True
        )
    except (InvalidSpecifier, InvalidVersion) as e:
        raise ConfigError(
            f"{label}: {identity.requires_key} {spec!r} is not a version specifier: {e}"
        ) from e
    if not admits:
        raise EnvError(
            f"{label} requires {identity.name} {spec}, but this is"
            f" {identity.distribution} {identity.version}"
        )


def integration_default(product: Product, cfg: dict) -> str:
    """The integration branch when the table names none: the product's rule, else the
    ``releases.branching``'s, else ``main`` (a product with no branching)."""
    if product.hooks.integration_default is not None:
        return product.hooks.integration_default(cfg)
    branching = product.branching.get(cfg.get("releases", {}).get("branching", ""))
    return branching.integration(cfg) if branching is not None else "main"


def resolve_doc(
    product: Product,
    doc: dict,
    *,
    root: Path | None = None,
    checkout: Checkout | None = None,
    lock_path: Path | None = None,
    profile_dir: Path | None = None,
    profile_doc: dict | None = None,
    on_github: bool | None = None,
    provisional: bool = False,
) -> Resolved:
    """Resolve a parsed config file (DESIGN §4.4).

    ``root`` is the checkout's directory (in-repo profiles, the lock, origin); it
    defaults to ``checkout.root``. ``checkout`` feeds ``hooks.derive`` and names the
    checkout for the ``repo`` default (else ``[project].name``). ``profile_dir`` replaces
    the org profile's locked tree with a local checkout (``--profile-path``).
    ``profile_doc`` is the org profile's ``profile.toml`` read by the caller, and
    ``on_github`` the host rule's answer when the caller knows it: both for a committed
    state, whose profile and host may not be today's.

    ``provisional`` resolves the table over ``minimal`` in place of an org profile not
    locked yet (phase A): a module need that profile may meet is left to the full
    resolution (``_modules``).
    """
    identity = product.identity
    if root is None and checkout is not None:
        root = checkout.root
    app = pyproject.tool_table(product, doc)
    if app is None:
        raise NotOptedIn(product)
    validate_app(product, app)
    app = copy.deepcopy(app)
    name = app.get("profile", "minimal")

    layers: list[tuple[str, dict]] = []
    org_doc: dict | None = None
    profile_info = {"name": "", "source": "builtin", "rev": ""}
    if is_builtin(name):
        stem, base = builtin_profile(product, name)
        profile_info["name"] = stem
    else:
        if profile_doc is not None:
            org_doc, rev = profile_doc, ""
        else:
            if profile_dir is not None:
                directory, rev = profile_dir, ""
            elif root is None:
                raise ConfigError(
                    f"profile {name!r} needs the checkout's directory to be read"
                )
            else:
                directory, rev = org_profile_dir(product, name, root, lock_path)
            org_doc = read_profile_dir(directory, name)
        label = f"org:{name}@{rev}" if rev else f"org:{name}"
        validate_profile(product, org_doc, f"profile {name}", org=True)
        check_requires(product, org_doc.get(identity.requires_key), f"profile {name}")
        stem, base = builtin_profile(product, org_doc["extends"])
        profile_info = {"name": org_doc["name"], "source": name, "rev": rev}
        layers.append((label, org_doc))
    validate_profile(product, base, f"built-in profile {stem}", org=False)
    layers.insert(0, (f"builtin:{stem}", base))
    layers.append(("app", app))

    sources: dict[str, str] = {}
    cfg: dict = {}
    meta = ("schema", "name", "description", "extends", identity.requires_key)
    for label, layer in layers:
        body = {k: v for k, v in layer.items() if k not in meta}
        cfg = _merge(cfg, body, label, sources)
    cfg["schema"] = app["schema"]
    for key in resolved_from_profile(product):
        cfg[key] = copy.deepcopy(org_doc.get(key, [])) if org_doc else []
    cfg = _fill_defaults(cfg, schema.defaults(product, PROFILE_SCHEMA))
    cfg = _fill_defaults(cfg, schema.defaults(product, APP_SCHEMA))
    cfg["profile"] = name

    org = cfg["org"]
    if not org.get("copyright-holder"):
        org["copyright-holder"] = org.get("publisher", "")
    if "integration-branch" not in cfg:
        cfg["integration-branch"] = integration_default(product, cfg)
    product.hooks.derive(cfg, checkout)
    unit = checkout.name if checkout is not None else pyproject.project_name(doc)
    repo_path, host = _repo(cfg, root, unit)
    if repo_path:
        cfg["repo"] = repo_path

    notices: list[str] = []
    github = host in (None, "github.com") if on_github is None else on_github
    modules = _modules(product, cfg, app, github, notices, provisional=provisional)
    return Resolved(
        cfg=cfg,
        modules=modules,
        profile=profile_info,
        sources=sources,
        repo_host=host,
        notices=notices,
        product=product,
    )


def resolve(
    product: Product,
    config_path: Path,
    *,
    root: Path | None = None,
    lock_path: Path | None = None,
    profile_dir: Path | None = None,
    checkout: Checkout | None = None,
) -> Resolved:
    """Resolve the checkout whose config file is ``config_path``.

    Its text must spell the table the way the dev shell's line match finds it
    (``pyproject.check_opt_in_spelling``), so the shell and the tools never disagree on
    whether the checkout opted in. A missing file reads as empty (not opted in) when the
    product's config file is optional. ``root`` is the checkout's directory (in-repo
    profiles, the default lock ``root / "flake.lock"``, origin); it defaults to the
    config file's directory, which is the root when the file sits at it (§15.CR1).
    """
    root = root or config_path.resolve().parent
    if not product.identity.config_required and not config_path.exists():
        text = ""
    else:
        text = pyproject.read(config_path)
    doc = pyproject.parse(text, config_path)
    pyproject.check_opt_in_spelling(product, text, doc)
    return resolve_doc(
        product,
        doc,
        root=root,
        checkout=checkout,
        lock_path=lock_path,
        profile_dir=profile_dir,
    )
