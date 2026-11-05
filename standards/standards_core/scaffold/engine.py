"""The plan: every managed file's current and wanted state, computed in memory (DESIGN §6.3).

``--check`` reports the plan; ``--write`` applies it. Both run the same code, so
``--check`` exits 0 right after a ``--write`` that exited 0.

An ``Item`` is one file: what is on disk, what sync wants there (``None`` to delete it),
and the problem when the two differ or a rule is broken. A problem sync can fix is
drift; one it can't (a rule on a checkout-owned key) carries its own exit code and no
new content.

An entry is live when one of its modules is on and its ``when`` holds. Each entry is
served by a handler (``scaffold.handlers``): a merged handler renders on every run (its
key groups retract themselves); otherwise a live entry renders, an entry whose module is
on but whose ``when`` is false is ``absent``, and one whose modules are all off is
retracted. A module "turned off" when it is off now and was on in a configuration
committed since the checkout opted in: a ``[tool.<table>]`` table with the profile
committed beside it (``history``).

Every path is checked once rendered (``inside``): relative, no ``..``, no control
character, not under a directory sync never writes, and no symlink on the way, since
``--check`` runs on untrusted pull requests.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from standards_core.common import config, flakelock, pins, pyproject, repo
from standards_core.common.config import NotOptedIn, Resolved
from standards_core.common.report import (
    CLEAN,
    DRIFT,
    ENVIRONMENT,
    INVALID,
    ConfigError,
    EnvError,
    Finding,
)
from standards_core.scaffold import context, globs, manifest, nixattrs
from standards_core.scaffold import render as rendering

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.handlers import Handler


@dataclass
class Item:
    path: str
    strategy: str
    current: str | None
    wanted: str | None
    problem: str = ""
    code: int = CLEAN
    command: str | None = None
    phase: str = "b"
    # The module that manages the file (several, comma-separated, for a file several
    # modules contribute to); empty for a whole-repo check. ``--check --format json``
    # reports it per file.
    module: str = ""
    # The manifest entry the item was rendered for; ``None`` for a whole-repo check, a
    # retired file or a lock. Phase B's lock steps read its handler and ``relock_when``.
    entry: manifest.Entry | None = field(default=None, repr=False, compare=False)

    @property
    def changes(self) -> bool:
        return self.code == DRIFT and (
            self.wanted != self.current or self.command is not None
        )

    def diff(self) -> str:
        if self.wanted is None and self.current is None:
            return ""
        a = (self.current or "").splitlines(keepends=True)
        b = (self.wanted or "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, "current", "rendered"))

    def finding(self) -> Finding:
        return Finding(
            self.path,
            self.strategy,
            self.problem,
            self.diff() if self.wanted != self.current else "",
            self.module,
        )


@dataclass
class Plan:
    product: Product = field(repr=False)
    root: Path
    checkout: context.Checkout
    resolved: Resolved
    ctx: context.NS
    items: list[Item] = field(default_factory=list)
    created_config: dict | None = None
    # What sync leaves to the checkout and says so: keys of a module that turned off but
    # were edited, seeds of a module that is off, the resolver's notices.
    notices: list[str] = field(default_factory=list)
    # The org profile's templates/ directory, when it has one.
    templates: Path | None = None

    @property
    def cfg(self) -> dict:
        return self.resolved.cfg

    def add(self, item: Item) -> None:
        self.items.append(item)

    @property
    def problems(self) -> list[Item]:
        return [i for i in self.items if i.code != CLEAN]

    @property
    def code(self) -> int:
        return max((i.code for i in self.items), default=CLEAN)


# --- paths ---------------------------------------------------------------------------


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def unmanaged_dirs(product: Product) -> frozenset[str]:
    """The directories sync never reads or writes: ``.git`` and ``paths.never_write``.

    A rendered path is data a pull request can set (an in-repo profile's
    ``[[extra-files]]``): one into ``.git/`` would print ``.git/config`` (a persisted
    checkout token) under ``--check``, and under ``--write`` replace it with config the
    following ``git add`` obeys. Compared case-folded and without trailing dots and
    spaces, as case-insensitive and Windows file systems resolve them."""
    return frozenset(
        d.casefold() for d in {".git", *product.paths.never_write} if d.strip()
    )


def unmanaged_dir(product: Product, path: str) -> str | None:
    """The component of ``path`` that names a directory sync never writes into, if any."""
    names = unmanaged_dirs(product)
    for part in PurePosixPath(path).parts:
        if part.rstrip(". ").casefold() in names:
            return part
    return None


def inside(product: Product, root: Path, path: str, *, link_ok: bool = False) -> Path:
    """``root / path``, once ``path`` is known to stay inside the checkout with no link on
    the way.

    ``path`` is a rendered managed path: it must be relative, with no ``..``, no control
    character, and no existing component of it may be a symlink (the last one may, with
    ``link_ok``, for a retired link sync deletes unread), and none may be a directory
    sync never writes (``.git`` above all). ``--check`` runs on untrusted pull requests:
    a committed link would otherwise print the file it reaches in the diff, and
    ``--write`` would write through it. Exit 2."""
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
        raise ConfigError(
            f"{path!r} is not a path inside the checkout: a managed path must be"
            " relative, without .."
        )
    if _CONTROL.search(path):
        # A newline in a path would start a line of its own in every message that names
        # it (a workflow command in a CI log, a forged finding).
        raise ConfigError(
            f"{path!r} is not a path sync may manage: it holds a control character"
        )
    if (part := unmanaged_dir(product, path)) is not None:
        raise ConfigError(
            f"{path!r} is not a path sync may manage: it is inside {part}/, which sync"
            " never reads or writes"
        )
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        last = i == len(parts) - 1
        if cur.is_symlink() and not (last and link_ok):
            if last:
                raise ConfigError(
                    f"{path} is a symlink: a managed file must be a regular file"
                    " (remove the link)"
                )
            raise ConfigError(
                f"{path}: {PurePosixPath(*parts[: i + 1])} is a symlink: sync never"
                " reads or writes through a link (remove it)"
            )
    return root / path


def read(product: Product, root: Path, path: str) -> str | None:
    """The text of ``path`` under ``root``; ``None`` when it is missing or a directory.

    A symlink anywhere on the path is refused, never followed (``inside``)."""
    inside(product, root, path)
    try:
        return (root / path).read_text()
    except (FileNotFoundError, IsADirectoryError):
        return None


# --- flake inputs ----------------------------------------------------------------------
#
# Read with the Nix lexer of ``scaffold/nixattrs.py`` (DESIGN §5.6.2, §5.6.4): the direct
# children of ``inputs = { … };`` at any indentation, ``self`` excluded.

flake_input_specs = nixattrs.flake_input_specs
input_spec = nixattrs.input_spec
locked_spec = nixattrs.locked_spec
strip_nix_comments = nixattrs.strip_nix_comments


# --- configuration ---------------------------------------------------------------------


def config_file(product: Product, root: Path) -> str:
    """The checkout's config file, relative to its root (``pyproject.config_rel``), so a
    product that keeps the table elsewhere per checkout is read where it is."""
    return pyproject.config_rel(product, root)


def with_table(product: Product, doc: dict, table: dict) -> dict:
    """``doc`` (the parsed config file) with ``[tool.<table>]`` set to ``table``."""
    out = copy.deepcopy(doc)
    out.setdefault("tool", {})[product.identity.table] = copy.deepcopy(table)
    return out


def origin_default_branch(root: Path) -> str | None:
    """The branch ``origin/HEAD`` names, when the clone knows it."""
    try:
        ref = repo.git(
            root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
        ).strip()
    except EnvError:
        return None
    return ref.split("/", 1)[1] if "/" in ref else None


def new_table(
    product: Product,
    root: Path,
    checkout: context.Checkout,
    args: Any,
    profile: str,
) -> dict:
    """``[tool.<table>]`` as ``--standards <profile>`` creates it: ``schema`` and
    ``profile``, then the product's keys (``hooks.new_target_table``).
    ``integration-branch`` is added by ``with_integration_branch`` once the profile's
    branching is known."""
    table: dict[str, Any] = {"schema": 1, "profile": profile}
    hook = product.hooks.new_target_table
    if hook is not None:
        for key, value in hook(root, checkout, args, profile).items():
            if key in ("schema", "profile"):
                continue
            table[key] = value
    return table


def with_integration_branch(root: Path, created: dict, resolved: Resolved) -> dict:
    """``created`` plus ``integration-branch`` when ``origin/HEAD`` names a branch other
    than the default the resolution gave (read once, at creation, so later syncs stay
    deterministic)."""
    branch = origin_default_branch(root)
    default = resolved.cfg.get("integration-branch")
    if branch and branch != default:
        out = dict(created)
        out["integration-branch"] = branch
        return out
    return created


def profile_locked(product: Product, root: Path, table: dict) -> bool:
    """Whether an org profile's ``standards-profile`` input is locked from the URL
    ``profile`` names. Built-in and in-repo profiles need no lock."""
    profile = table.get("profile", "minimal")
    if not isinstance(profile, str) or not profile.startswith(
        config.ORG_PROFILE_PREFIXES
    ):
        return True
    try:
        lock = flakelock.load(root / "flake.lock")
    except EnvError:
        return False
    ref = (
        lock["nodes"]
        .get(lock["root"], {})
        .get("inputs", {})
        .get(flakelock.PROFILE_INPUT)
    )
    if ref is None:
        return False
    have = locked_spec(lock, ref)
    return have is None or have == input_spec("url", profile)


def resolve(
    product: Product,
    checkout: context.Checkout,
    *,
    created: dict | None = None,
    profile_dir: Path | None = None,
    bootstrap: bool = False,
) -> Resolved:
    """The checkout's resolved configuration; ``created`` is the table ``--standards``
    makes.

    ``bootstrap`` is phase A's: an org profile whose input is not locked yet (or is
    locked from another URL) cannot be read, so the phase-A files are rendered from the
    checkout's own table on top of ``minimal``, locked, and then rendered again from the
    real resolution (``provisional``)."""
    table = (
        created
        if created is not None
        else pyproject.tool_table(product, checkout.config)
    )
    if table is None:
        raise NotOptedIn(product)
    doc = with_table(product, checkout.config, table)
    if (
        bootstrap
        and profile_dir is None
        and not profile_locked(product, checkout.root, table)
    ):
        doc = with_table(product, checkout.config, {**table, "profile": "minimal"})
        resolved = config.resolve_doc(product, doc, checkout=checkout, provisional=True)
        resolved.cfg["profile"] = table.get("profile", "minimal")
        resolved.profile["source"] = table.get("profile", "minimal")
        return resolved
    return config.resolve_doc(product, doc, checkout=checkout, profile_dir=profile_dir)


def _in_repo_dir(name: str) -> str | None:
    """The directory an in-repo profile ``./<dir>`` names, relative to the checkout;
    ``None`` for any other profile (or one that would leave the checkout)."""
    if not name.startswith("./"):
        return None
    rel = PurePosixPath(name)
    if not rel.parts or ".." in rel.parts or rel.is_absolute():
        return None
    return rel.as_posix()


def _committed_profile(
    product: Product,
    root: Path,
    sha: str,
    name: str,
    cache: dict[str, dict] | None = None,
) -> tuple[dict | None, str]:
    """``(profile.toml, key)`` of the org profile ``name`` as commit ``sha`` had it: an
    in-repo profile's committed file, a flake URL's tree as that commit's
    ``flake.lock`` locks it; ``(None, "")`` for a built-in. ``key`` tells two of them
    apart, and ``cache`` holds what each key read, so a tree is fetched and hashed once.

    A profile the commit doesn't hold (no such file, a link, no locked input) is a
    ``ConfigError``: that state tells nothing. A locked tree that can't be fetched is an
    ``EnvError``: what it held is unknown."""
    if config.is_builtin(name):
        return None, ""
    directory = _in_repo_dir(name)
    if directory is not None:
        path = f"{directory}/profile.toml"
        try:
            listing = repo.git(root, "ls-tree", sha, "--", path)
            fields = listing.split("\t", 1)[0].split()
            # A committed link is never followed, as resolving the work tree refuses one.
            if len(fields) != 3 or fields[1] != "blob" or fields[0] == "120000":
                raise ConfigError(f"{sha}: {path} is not a committed regular file")
            text = repo.git(root, "cat-file", "blob", fields[2])
            return tomllib.loads(text), "blob:" + fields[2]
        except (EnvError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(f"{sha}: {path}: {e}") from e
    if not name.startswith(config.ORG_PROFILE_PREFIXES):
        raise ConfigError(f"profile {name!r} is not a built-in, a flake URL or ./<dir>")
    try:
        lock = flakelock.parse(
            repo.git(root, "show", f"{sha}:./flake.lock"), f"{sha}:flake.lock"
        )
        pin = flakelock.locked_pin(product, lock, flakelock.PROFILE_INPUT)
    except EnvError as e:
        raise ConfigError(f"{sha}: {e}") from e
    key = "nar:" + pin.nar_hash
    if cache is not None and key in cache:
        return cache[key], key
    tree = pins.pin_path(
        product, flakelock.PROFILE_INPUT, root / "flake.lock", lock=lock
    )
    try:
        doc = config.read_profile_dir(tree, name)
    except ConfigError as e:
        raise ConfigError(f"{sha}: {e}") from e
    if cache is not None:
        cache[key] = doc
    return doc, key


def _profile_at(
    product: Product,
    root: Path,
    sha: str,
    doc: dict,
    profile_dir: Path | None,
    cache: dict[str, dict] | None = None,
) -> tuple[dict | None, str]:
    """The org profile commit ``sha``'s table (in ``doc``) names, as that commit had it,
    not today's: a profile edit or bump that turns a module off changes nothing in the
    config file, and the module was on all the same.

    With ``--profile-path``, a state whose committed profile can't be read is read from
    that directory instead, as before a profile author's first lock."""
    name = (pyproject.tool_table(product, doc) or {}).get("profile", "minimal")
    name = name if isinstance(name, str) else "minimal"
    try:
        return _committed_profile(product, root, sha, name, cache)
    except (ConfigError, EnvError):
        if profile_dir is None:
            raise
        return config.read_profile_dir(profile_dir, name), f"path:{profile_dir}"


def _has_github_dir(product: Product, root: Path, sha: str) -> bool:
    """Whether commit ``sha`` was set up for GitHub: it holds one of
    ``paths.github_marks`` (``.github/``, or a file only a GitHub-only module
    renders)."""
    marks = product.paths.github_marks
    if not marks:
        return False
    try:
        return bool(repo.git(root, "ls-tree", sha, "--", *marks).strip())
    except EnvError:
        return False


def history_paths(product: Product, root: Path) -> list[str]:
    """The config files a checkout's history is read from: every one of
    ``pyproject.config_paths``, then the current one when it is not among them."""
    paths = list(pyproject.config_paths(product))
    current = config_file(product, root)
    return paths if current in paths else [*paths, current]


def _committed_text(root: Path, sha: str, path: str) -> str | None:
    try:
        return repo.git(root, "show", f"{sha}:./{path}")
    except EnvError:
        return None


def _committed_doc(product: Product, root: Path, sha: str) -> dict | None:
    """Commit ``sha``'s config file, parsed; ``None`` when it has none it can parse.

    It is the first of ``history_paths`` whose text at ``sha`` has the opt-in line, else
    the current config file (DESIGN §15.CR1): a checkout that moved its table from one
    file to another reads each commit's table where that commit kept it."""
    paths = history_paths(product, root)
    texts = {path: _committed_text(root, sha, path) for path in paths}
    chosen = next(
        (p for p in paths if texts[p] and pyproject.opt_in_line(product, texts[p])),
        config_file(product, root),
    )
    text = texts.get(chosen)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _state(resolved: Resolved) -> context.NS:
    return context.ns({"modules": resolved.modules, "cfg": resolved.cfg})


def previous(
    product: Product,
    root: Path,
    checkout: context.Checkout | None = None,
    profile_dir: Path | None = None,
) -> context.NS | None:
    """The modules and configuration ``HEAD`` committed (its table and its profile):
    what a module that is off now was before. ``None`` when there is no ``HEAD``, no
    table there, or it doesn't resolve: then nothing counts as turned off, and only
    files with a managed header are retracted."""
    doc = _committed_doc(product, root, "HEAD")
    if doc is None or pyproject.tool_table(product, doc) is None:
        return None
    try:
        profile_doc, _ = _profile_at(product, root, "HEAD", doc, profile_dir)
        resolved = config.resolve_doc(
            product, doc, root=root, checkout=checkout, profile_doc=profile_doc
        )
    except Exception:  # noqa: BLE001 — a state that does not resolve tells nothing
        return None
    return _state(resolved)


# How far back ``history`` reads: commits that changed a config file (``history_paths``),
# flake.lock or an in-repo profile, newest first.
HISTORY_DEPTH = 300


def history(
    product: Product,
    root: Path,
    checkout: context.Checkout | None = None,
    profile_dir: Path | None = None,
) -> context.History:
    """The modules and configuration of every state the checkout has committed since it
    opted in, newest first (``HEAD``'s included, each distinct state once). A state is a
    ``[tool.<table>]`` table with the org profile committed beside it: an in-repo
    profile's ``profile.toml`` at that commit, a flake URL's tree as that commit's
    ``flake.lock`` locks it.

    A module that is off now but on in any of them was on once, so sync may have
    written its files and keys: they are retracted whichever commit turned it off. The
    walk stops at the newest commit without the table: the checkout's settings before it
    opted in are its own.

    Where the repository is hosted is not committed: ``origin`` is today's. When it is
    off GitHub and no ``repo`` names the host, each state is also read as hosted on
    GitHub when ``HEAD`` or its commit holds one of ``paths.github_marks``, so what a
    GitHub-only module left before a move is retracted.

    A shallow clone whose walk reaches its boundary with the table still there is
    ``truncated``, and a state whose locked profile can't be fetched makes the history
    ``unknown``: a question it can't answer fails (``context.ever``, exit 3)."""
    out = context.History()
    config_paths = history_paths(product, root)
    try:
        edits = repo.git(
            root, "log", f"-n{HISTORY_DEPTH}", "--format=%H", "--", *config_paths
        ).split()
    except EnvError:
        return out
    docs: dict[str, dict | None] = {}
    paths = [*config_paths, "flake.lock"]
    for sha in edits:
        docs[sha] = doc = _committed_doc(product, root, sha)
        table = pyproject.tool_table(product, doc) if doc is not None else None
        if doc is not None and table is None:
            break
        directory = _in_repo_dir(str((table or {}).get("profile", "")))
        if directory is not None and directory not in paths:
            paths.append(directory)
    try:
        shas = repo.git(
            root, "log", f"-n{HISTORY_DEPTH}", "--format=%H", "--", *paths
        ).split()
    except EnvError:
        return out
    # Each distinct state once: whether origin puts it off GitHub, and those also read
    # as on it.
    elsewhere: dict[tuple[str, str], bool] = {}
    hosted: set[tuple[str, str]] = set()
    profiles: dict[str, dict] = {}
    github_dir: bool | None = None
    stopped = len(shas) >= HISTORY_DEPTH
    for sha in shas:
        doc = docs[sha] if sha in docs else _committed_doc(product, root, sha)
        if doc is None:
            continue
        table = pyproject.tool_table(product, doc)
        if table is None:
            stopped = True
            break
        try:
            profile_doc, key = _profile_at(
                product, root, sha, doc, profile_dir, profiles
            )
        except ConfigError:
            # No profile there to read (an in-repo one not added yet, no lock yet).
            continue
        except EnvError as e:
            out.unknown = out.unknown or (
                "retracting what a module that is off now left behind needs the org"
                f" profile commit {sha[:12]} locked, and it could not be read: {e}"
            )
            continue
        state = (json.dumps(table, sort_keys=True, default=str), key)
        if state not in elsewhere:
            try:
                resolved = config.resolve_doc(
                    product, doc, root=root, checkout=checkout, profile_doc=profile_doc
                )
            except Exception:  # noqa: BLE001, S112 — a state that no longer resolves tells nothing
                continue
            out.append(_state(resolved))
            elsewhere[state] = (
                resolved.repo_host not in (None, "github.com")
                and "repo" not in resolved.sources
            )
        if not elsewhere[state] or state in hosted:
            continue
        if github_dir is None:
            github_dir = _has_github_dir(product, root, "HEAD")
        if not (github_dir or _has_github_dir(product, root, sha)):
            continue
        hosted.add(state)
        try:
            on_github = config.resolve_doc(
                product,
                doc,
                root=root,
                checkout=checkout,
                profile_doc=profile_doc,
                on_github=True,
            )
        except Exception:  # noqa: BLE001, S112 — as above
            continue
        out.append(_state(on_github))
    if shas and not stopped:
        try:
            shallow = repo.git(root, "rev-parse", "--is-shallow-repository").strip()
            out.truncated = shallow == "true"
        except EnvError:
            out.truncated = True
    return out


def profile_templates(
    product: Product, root: Path, cfg: dict, profile_dir: Path | None
) -> Path | None:
    """The org profile's ``templates/`` directory, when it has one."""
    name = cfg.get("profile", "minimal")
    if config.is_builtin(name):
        return None
    if profile_dir is not None:
        directory = profile_dir
    else:
        try:
            directory, _ = config.org_profile_dir(product, name, root)
        except (ConfigError, EnvError):
            return None
    templates = directory / "templates"
    if templates.is_symlink():
        raise ConfigError(
            "the profile's templates/ is a symlink: a profile's templates are read only"
            " as regular files, never through a link"
        )
    return templates if templates.is_dir() else None


def check_overrides(templates: Path | None, entries: list[manifest.Entry]) -> None:
    """Each file in the profile's ``templates/`` replaces an overridable template or is
    an ``[[extra-files]]`` template; anything else is exit 2 naming it, and so is a
    symlink anywhere in it (``render.template_files``). An overridable entry whose
    template ends in ``/`` renders several, and each one under it may be replaced."""
    if templates is None:
        return
    allowed = {
        e.template
        for e in entries
        if e.template and (e.overridable or e.profile_template)
    }
    folders = tuple(t for t in allowed if t.endswith("/"))
    for path in rendering.template_files(templates):
        rel = path.relative_to(templates).as_posix()
        if rel not in allowed and not (folders and rel.startswith(folders)):
            raise ConfigError(
                f"the profile's templates/{rel} overrides no overridable template and"
                " no [[extra-files]] entry names it (tool configs, merged keys and"
                " caller workflows change through parameters)"
            )


def check_uses(
    product: Product, ctx: context.NS, entry: manifest.Entry, path: str
) -> None:
    """An org value a live entry needs must be set; ``org.<key>?`` is optional."""
    for key in entry.uses:
        if not key.startswith("org.") or key.endswith("?"):
            continue
        value: Any = ctx.org
        for part in key.split(".")[1:]:
            value = value.get(part) if isinstance(value, dict) else None
        if not value:
            table = product.identity.table
            raise ConfigError(
                f"{path} needs {key}, which is empty: set it in the profile's [org] or"
                f" [tool.{table}.org]"
            )


_GITHUB_FLAKE_URL = re.compile(
    r"^(github:|(git\+)?https://github\.com/)", re.IGNORECASE
)


def check_engine_url(product: Product, cfg: dict, modules: dict) -> None:
    """An ``engine.url_key`` off GitHub works for the dev shell and sync, but a GitHub
    ``uses:`` can't name it: exit 2 while ``ci`` is on."""
    value: Any = cfg
    for part in product.engine.url_key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if (
        isinstance(value, str)
        and value
        and modules.get("ci")
        and not _GITHUB_FLAKE_URL.match(value)
    ):
        raise ConfigError(
            f"{product.engine.url_key} = {value!r} is not on GitHub, and ci is on: the"
            " caller workflows' `uses:` can name only a GitHub repository (use a"
            " github: URL, or turn ci off)"
        )


def entries_for(
    product: Product, cfg: dict, root: Path | None = None
) -> list[manifest.Entry]:
    """The product's entries and the org profile's ``[[extra-files]]``, which may not
    collide (nor name the config file)."""
    man = manifest.load(product)
    extra = manifest.extra_entries(cfg)
    configs = (
        {config_file(product, root)}
        if root is not None
        else set(pyproject.config_paths(product))
    )
    taken = {e.path for e in man.entries} | configs
    for e in extra:
        if _CONTROL.search(e.path):
            raise ConfigError(f"[[extra-files]] {e.path!r} holds a control character")
        if e.path in taken:
            raise ConfigError(
                f"[[extra-files]] {e.path} is a path {product.identity.name} manages"
            )
        if PurePosixPath(e.path).is_absolute() or ".." in PurePosixPath(e.path).parts:
            raise ConfigError(f"[[extra-files]] {e.path} must stay inside the checkout")
        if (part := unmanaged_dir(product, e.path)) is not None:
            raise ConfigError(
                f"[[extra-files]] {e.path} is inside {part}/, which sync never writes"
            )
    return [*man.entries, *extra]


# --- retire ------------------------------------------------------------------------------


def _requirement_names(text: str) -> list[str]:
    """The package names a requirements file lists (PEP 503-normalised), options
    skipped."""
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if m:
            names.append(re.sub(r"[-_.]+", "-", m[0]).lower())
    return names


def _project_doc(plan: Plan) -> dict:
    """The checkout's ``pyproject.toml``, parsed (the config file when it is that one)."""
    if config_file(plan.product, plan.root) == "pyproject.toml":
        return plan.checkout.config
    try:
        text = read(plan.product, plan.root, "pyproject.toml")
        return tomllib.loads(text) if text is not None else {}
    except (ConfigError, tomllib.TOMLDecodeError):
        return {}


def _missing_deps(plan: Plan, text: str) -> list[str]:
    """The packages ``text`` lists that ``[project].dependencies`` does not."""
    project = _project_doc(plan).get("project", {})
    deps = project.get("dependencies", []) if isinstance(project, dict) else []
    have = set(_requirement_names("\n".join(d for d in deps if isinstance(d, str))))
    return sorted({n for n in _requirement_names(text) if n not in have})


def _rule_applies(plan: Plan, rule: manifest.Retire) -> bool:
    modules = plan.ctx.modules
    if not modules.get(rule.module) or not all(modules.get(m) for m in rule.also):
        return False
    return rendering.evaluate(rule.when, plan.ctx)


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.MULTILINE)


def _retired(plan: Plan, managed_paths: set[str], only: set[str] | None) -> list[Item]:
    """Every tracked match of an applying retire rule: drift, deleted by ``--write``."""
    product = plan.product
    rules = [
        r
        for r in (
            *manifest.load(product).retire,
            *manifest.profile_retire(plan.cfg),
        )
        if _rule_applies(plan, r)
    ]
    keep = list(plan.cfg.get("retire-keep", []))
    out = []
    for path in plan.checkout.tracked:
        if only is not None and path not in only:
            continue
        if path in keep or globs.match_any(keep, path):
            continue
        if unmanaged_dir(product, path) is not None:
            # Sync never reads or writes there, so nothing there is a legacy file.
            continue
        link = (plan.root / path).is_symlink()
        if not link and not (plan.root / path).is_file():
            continue
        for rule in rules:
            if not globs.match_any(list(rule.paths), path) or globs.match_any(
                list(rule.unless), path
            ):
                continue
            if rule.unmanaged and path in managed_paths:
                continue
            if link:
                # Never read through a link: deleting the link is all retiring it takes,
                # and a rule that looks inside the file cannot apply to one.
                if not (rule.contains or rule.only_sections or rule.deps_in_project):
                    out.append(
                        Item(
                            path,
                            "retire",
                            "",
                            None,
                            f"legacy file ({rule.rule})",
                            DRIFT,
                            module=rule.module,
                        )
                    )
                    break
                continue
            text = read(product, plan.root, path) or ""
            if rule.contains and not any(
                re.search(pattern, text) for pattern in rule.contains
            ):
                continue
            if rule.only_sections:
                sections = set(_SECTION.findall(text))
                if not sections or not sections <= set(rule.only_sections):
                    continue
            missing = _missing_deps(plan, text) if rule.deps_in_project else []
            if missing:
                out.append(
                    Item(
                        path,
                        "retire",
                        text,
                        text,
                        f"legacy file ({rule.rule}), but [project].dependencies lacks"
                        f" {', '.join(missing)}: add them there, then sync deletes it",
                        INVALID,
                        module=rule.module,
                    )
                )
            else:
                out.append(
                    Item(
                        path,
                        "retire",
                        text,
                        None,
                        f"legacy file ({rule.rule})",
                        DRIFT,
                        module=rule.module,
                    )
                )
            break
    return out


# --- history questions the handlers ask --------------------------------------------------


def was_on(plan: Plan, entry: manifest.Entry) -> bool:
    """Whether one of the entry's modules was on in any table since the checkout opted
    in (``history``): only then is what sync would have written there sync's to
    retract."""
    return context.ever(
        plan.ctx.get("history"),
        lambda h: any(h.modules.get(m) for m in entry.modules),
    )


def on_at_head(plan: Plan, entry: manifest.Entry) -> bool:
    """Whether one of the entry's modules was on at ``HEAD``: this run turns it off, so
    what sync leaves behind is reported now, once."""
    prev = plan.ctx.get("previous")
    return bool(prev) and any(prev.modules.get(m) for m in entry.modules)


# --- whole-repo checks -------------------------------------------------------------------


def locked_engine(product: Product, root: Path) -> dict:
    """The ``flake.lock`` engine node's ``locked`` (``rev``, ``owner``, ``repo``), or
    ``{}``."""
    lock = root / "flake.lock"
    if not lock.is_file():
        return {}
    try:
        found = flakelock.node_at(flakelock.load(lock), [product.engine.flake_input])
    except EnvError:
        return {}
    locked = found[1].get("locked") if found else None
    return dict(locked) if isinstance(locked, dict) else {}


def locked_rev(product: Product, root: Path) -> str | None:
    return locked_engine(product, root).get("rev")


def stale_inputs(
    lock: dict, wanted: dict[str, str | None], skip: tuple[str, ...] = ()
) -> list[str]:
    """The inputs ``flake.nix`` declares that ``flake.lock`` has no node for, or locked
    from another URL or ``follows``."""
    root_inputs = lock["nodes"].get(lock["root"], {}).get("inputs", {})
    out = []
    for name, spec in wanted.items():
        if name in skip:
            continue
        if name not in root_inputs:
            out.append(f"{name} (no node)")
            continue
        have = locked_spec(lock, root_inputs[name])
        if spec is not None and have is not None and have != spec:
            out.append(
                f"{name} (locked from {have.removeprefix('follows:')}, flake.nix has"
                f" {spec.removeprefix('follows:')})"
            )
    return out


def allow_skew(product: Product) -> bool:
    return os.environ.get(f"{product.identity.env_prefix}_ALLOW_SKEW") == "1"


def lock_problems(plan: Plan) -> list[Item]:
    """``flake.lock`` must hold a node for every input ``flake.nix`` declares, locked
    from the URL (or ``follows``) ``flake.nix`` gives it; ``standards-profile``
    included."""
    product = plan.product
    sync = product.identity.sync_command
    module = product.modules.required
    text = read(product, plan.root, "flake.nix")
    if text is None:
        return []
    lock_path = plan.root / "flake.lock"
    if not lock_path.is_file():
        return [
            Item(
                "flake.lock",
                "lock",
                None,
                None,
                f"missing: run `{sync}`",
                DRIFT,
                module=module,
            )
        ]
    try:
        lock = flakelock.load(lock_path)
    except EnvError as e:
        return [
            Item("flake.lock", "lock", None, None, str(e), ENVIRONMENT, module=module)
        ]
    # An engine's self-tests lock the checkout under test in its place (URL_OVERRIDE).
    skip = (product.engine.flake_input,) if allow_skew(product) else ()
    stale = stale_inputs(lock, flake_input_specs(text), skip)
    if stale:
        return [
            Item(
                "flake.lock",
                "lock",
                None,
                None,
                f"does not follow flake.nix: {'; '.join(stale)}: run `{sync}`",
                DRIFT,
                module=module,
            )
        ]
    return []


def lock_paths(product: Product) -> tuple[str, ...]:
    """The files the lock steps make: a run that fails after one of them is written
    leaves it untracked, and the step that made it does not run again."""
    return ("flake.lock", *product.pins.lock_paths)


def untracked_locks(product: Product, root: Path) -> list[str]:
    """The lock files that exist but are neither tracked nor staged (git-ignored ones
    aside): the flake and CI see only tracked files."""
    out = repo.git(
        root,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        "--",
        *lock_paths(product),
    )
    return sorted(p for p in out.split("\0") if p)


def untracked_lock_problems(plan: Plan) -> list[Item]:
    sync = plan.product.identity.sync_command
    return [
        Item(
            path,
            "lock",
            None,
            None,
            "exists but is not tracked (the flake and CI see only tracked files): run"
            f" `{sync}`",
            DRIFT,
        )
        for path in untracked_locks(plan.product, plan.root)
    ]


# --- the plan ----------------------------------------------------------------------------


def handler_for(product: Product, entry: manifest.Entry) -> Handler:
    """The handler serving ``entry``: its ``handler``, else its strategy's base."""
    from standards_core.scaffold import handlers

    return handlers.for_entry(product, entry)


def build(
    product: Product,
    root: Path,
    *,
    only: list[str] | None = None,
    options: dict | None = None,
    phases: tuple[str, ...] = ("a", "b"),
    profile_dir: Path | None = None,
    standards: str | None = None,
    engine_lock: dict | None = None,
    args: Any = None,
) -> Plan:
    """The plan for the checkout at ``root``.

    ``standards`` is ``--standards <profile>``: it creates ``[tool.<table>]`` when there
    is none, and must name the table's profile when there is one. Without either, the
    checkout has not opted in (exit 2).

    ``engine_lock`` stands in for ``flake.lock``'s engine node: a dry run on a checkout
    whose lock phase A would (re)lock renders against the rev that lock would take.
    ``args`` are the sync flags, for ``hooks.new_target_table``.
    """
    root = Path(root)
    checkout = product.hooks.load_checkout(root)
    root = checkout.root
    table = pyproject.tool_table(product, checkout.config)
    created = None
    if table is None:
        if not standards:
            raise NotOptedIn(product)
        created = new_table(product, root, checkout, args, standards)
    elif standards and table.get("profile", "minimal") != standards:
        raise ConfigError(
            f"[tool.{product.identity.table}] already names profile"
            f" {table.get('profile', 'minimal')!r}: edit profile there instead of"
            f" passing --standards {standards}"
        )
    bootstrap = phases == ("a",)
    resolved = resolve(
        product, checkout, created=created, profile_dir=profile_dir, bootstrap=bootstrap
    )
    if created is not None:
        created = with_integration_branch(root, created, resolved)
        resolved = resolve(
            product,
            checkout,
            created=created,
            profile_dir=profile_dir,
            bootstrap=bootstrap,
        )
    cfg = resolved.cfg
    product.hooks.preflight(checkout, resolved)
    check_engine_url(product, cfg, resolved.modules)
    man = manifest.load(product)
    lock = engine_lock if engine_lock is not None else locked_engine(product, root)
    ctx = context.build(
        product, checkout, resolved, lock=lock, floors=man.floors, options=options
    )
    ctx["previous"] = previous(product, root, checkout, profile_dir)
    ctx["history"] = history(product, root, checkout, profile_dir)
    entries = entries_for(product, cfg, root)
    templates = profile_templates(product, root, cfg, profile_dir)
    check_overrides(templates, entries)
    plan = Plan(
        product,
        root,
        checkout,
        resolved,
        ctx,
        created_config=created,
        notices=list(resolved.notices),
        templates=templates,
    )
    if "b" in phases:
        plan.notices += product.hooks.notices(plan)
    only_set = set(only) if only else None
    managed_paths: set[str] = set()
    # Sentinel entries that share a path edit one text in entry order (DESIGN §5.6.3):
    # the path's one item so far, whose ``wanted`` the next entry starts from.
    chained: dict[str, Item] = {}
    for entry in entries:
        # The rendered path is what is read and written, so that is what must stay
        # inside the checkout: an [[extra-files]] path of "{{ '..' }}/x" passes any
        # check of the template.
        path = rendering.render_string(product, entry.path, ctx)
        inside(product, root, path, link_ok=True)
        managed_paths.add(path)
        if entry.phase not in phases or (only_set is not None and path not in only_set):
            continue
        handler = handler_for(product, entry)
        module_on = any(ctx.modules.get(m) for m in entry.modules)
        before = chained.get(path) if entry.strategy == "sentinel" else None
        current = before.wanted if before is not None else read(product, root, path)
        items: list[Item] = []
        if handler.merged:
            # Its key groups are set or retracted on every run.
            if module_on:
                check_uses(product, ctx, entry, path)
            items = handler.render(plan, entry, path, current, templates)
        elif module_on and rendering.evaluate(entry.when, ctx):
            check_uses(product, ctx, entry, path)
            items = handler.render(plan, entry, path, current, templates)
        elif current is not None:
            gone = (
                handler.absent(plan, entry, path, current)
                if module_on
                else handler.retract(plan, entry, path, current)
            )
            items = [gone] if gone is not None else []
        for it in items:
            it.module = it.module or ",".join(entry.modules)
            if it.entry is None:
                it.entry = entry
            if entry.strategy != "sentinel":
                plan.add(it)
            elif before is None:
                chained[path] = before = it
                plan.add(it)
            else:
                chain(before, it)
    if "b" in phases:
        for it in _retired(plan, managed_paths, only_set):
            plan.add(it)
        for it in product.hooks.checks(plan):
            if only_set is None or it.path in only_set:
                plan.add(it)
    return plan


def chain(first: Item, then: Item) -> None:
    """``first`` (a path's sentinel item so far) followed by ``then``, the next entry's
    edit of the text ``first`` wanted: one item, its problems joined with ``"; "``."""
    first.wanted = then.wanted
    first.problem = "; ".join(p for p in (first.problem, then.problem) if p)
    first.code = max(first.code, then.code)
    modules = [*first.module.split(","), *then.module.split(",")]
    first.module = ",".join(dict.fromkeys(m for m in modules if m))


def check_handlers(product: Product) -> None:
    """Every entry names a registered handler that serves its strategy (a product bug,
    ``ManifestError``, otherwise)."""
    for entry in manifest.load(product).entries:
        handler_for(product, entry)
