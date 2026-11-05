"""The config file and its ``[tool.<table>]`` table: loading them, and whether a checkout opted in.

The file is ``config_path(product, root)``: ``hooks.config_file(root)`` when the product
keeps the table in one of several files (``identity.config_paths``), else
``identity.config_file`` (``pyproject.toml`` by default), and the table is
``[tool.<identity.table>]``. Resolving the table into the configuration the tools act on
(profiles, defaults, module switches) is ``standards_core.common.config``; this module
only reads.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.common import repo
from standards_core.common.report import ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Product


@cache
def _opt_in_re(product: Product) -> re.Pattern[str]:
    # The dev shell's opt-in test, the same pattern on the same lines: one that opens
    # [tool.<table>], a subtable of it or an array of tables under it. Nix's [[:space:]]
    # is these six characters.
    table = re.escape(product.identity.table)
    return re.compile(r"[ \t\n\v\f\r]*\[{1,2}tool\." + table + r"[].].*", re.DOTALL)


def read(path: Path) -> str:
    """The text of the file at ``path``; missing or unreadable is an ``EnvError``.

    Its line endings are kept as they are, as Nix's ``builtins.readFile`` keeps them: the
    dev shell's line match (``opt_in_line``) splits on ``\\n`` alone, so a lone ``\\r``
    must reach TOML as itself (which refuses it, exit 2) and not as a newline.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as e:
        raise EnvError(f"{path} does not exist") from e
    except (OSError, UnicodeDecodeError) as e:
        raise EnvError(f"{path} is unreadable: {e}") from e


def parse(text: str, path: Path) -> dict:
    """``text`` parsed as TOML; malformed is a ``ConfigError``."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def config_paths(product: Product) -> tuple[str, ...]:
    """Every file, relative to a checkout's root, the table may live in:
    ``identity.config_paths``, else ``(identity.config_file,)``."""
    identity = product.identity
    return identity.config_paths or (identity.config_file,)


def config_rel(product: Product, root: Path) -> str:
    """The checkout's config file relative to ``root``: ``hooks.config_file(root)``,
    else ``identity.config_file``. A hook's answer outside ``identity.config_paths``
    (when those are set) is a ``ConfigError``."""
    hook = product.hooks.config_file
    if hook is None:
        return product.identity.config_file
    rel = hook(root)
    paths = product.identity.config_paths
    if paths and rel not in paths:
        raise ConfigError(
            f"hooks.config_file gave {rel!r} for {root}, not one of"
            f" identity.config_paths ({', '.join(paths)})"
        )
    return rel


def config_path(product: Product, root: Path) -> Path:
    """The checkout's config file: ``<root>/<config_rel(product, root)>``. Every core
    read and write of the table goes through it (DESIGN §15.CR1)."""
    return root / config_rel(product, root)


def _owner(product: Product, path: Path, start: Path, top: Path) -> Path:
    """The outermost directory from ``start`` up to ``top`` whose config file is
    ``path``, else ``start``: a checkout that keeps its table in a subdirectory
    (``kit/pyproject.toml``) claims the file over that subdirectory, where the same file
    would read as a root ``pyproject.toml``."""
    owner = start
    for directory in (start, *start.parents):
        if config_path(product, directory) == path:
            owner = directory
        if directory == top:
            break
    return owner


def _toplevel(start: Path) -> Path | None:
    try:
        return repo.toplevel(start).resolve()
    except EnvError:
        return None


def per_checkout(product: Product) -> bool:
    """Whether the product's config file can differ between checkouts: a
    ``hooks.config_file``, or more than one ``identity.config_paths``."""
    return product.hooks.config_file is not None or len(config_paths(product)) > 1


def _opens_table(product: Product, path: Path) -> bool:
    try:
        return opt_in_line(product, read(path))
    except EnvError:
        return False


def checkout_root(product: Product, start: Path | None = None) -> Path:
    """The checkout at or above ``start`` (default: the current directory).

    It is the nearest directory ``d``, up to the git work tree's root, for which
    ``config_path(product, d)`` is a file, else the work tree's root (outside git,
    ``start``). For a product whose config file differs between checkouts
    (``per_checkout``), a directory whose file opens ``[tool.<table>]`` (``opt_in_line``)
    is preferred: from an addon or package directory with a ``pyproject.toml`` of its own
    that does not opt in, the root is the opted-in checkout above it, whose table may be
    in another file (``nix/pyproject.toml``); only when no file up to the work tree's root
    opens the table is the nearest existing one taken. When an outer directory up to the
    work tree's root has that same file as its config file, the outermost such directory
    is the root. The root is never derived from the config file's location, which may sit
    in a subdirectory of it (DESIGN §15.CR1).
    """
    here = (start or Path.cwd()).resolve()
    top = _toplevel(here)
    if top is None:
        return here
    wide = per_checkout(product)
    nearest: tuple[Path, Path] | None = None
    for directory in (here, *here.parents):
        path = config_path(product, directory)
        if path.is_file():
            if nearest is None:
                nearest = (path, directory)
                if not wide:
                    break
            if _opens_table(product, path):
                return _owner(product, path, directory, top)
        if directory == top:
            break
    if nearest is not None:
        return _owner(product, *nearest, top)
    return top


def claimed_by(product: Product, directory: Path) -> Path | None:
    """The outer checkout root whose config file is ``directory``'s own (``kit/`` of a
    checkout keeping its table in ``kit/pyproject.toml``), else ``None``. Never an outer
    root for a product with one root-level config file."""
    if not per_checkout(product):
        return None
    directory = directory.resolve()
    top = _toplevel(directory)
    if top is None:
        return None
    owner = _owner(product, config_path(product, directory), directory, top)
    return owner if owner != directory else None


def root_for(product: Product, path: Path) -> Path:
    """The checkout root whose config file is ``path`` (``--pyproject``): the outermost
    ancestor ``d`` of ``path``, up to the git work tree's root, with
    ``config_path(product, d) == path``, else ``path``'s directory. The directory is
    resolved, the file name is not: a symlinked config file belongs to the checkout the
    link is in, not the one its target is in."""
    path = path.absolute()
    parent = path.parent.resolve()
    top = _toplevel(parent) if parent.is_dir() else None
    if top is None:
        return parent
    return _owner(product, parent / path.name, parent, top)


def config_text(product: Product, root: Path) -> str:
    """The text of the checkout's config file.

    A missing file is an ``EnvError`` when ``identity.config_required``; otherwise it
    reads as empty, which is "not opted in" (and which ``--standards`` creates).
    """
    path = config_path(product, root)
    if not product.identity.config_required and not path.exists():
        return ""
    return read(path)


def load(product: Product, root: Path) -> dict:
    """The parsed config file of the checkout at ``root``.

    Missing is an ``EnvError`` when ``identity.config_required``, else ``{}``; malformed
    is a ``ConfigError``.
    """
    return parse(config_text(product, root), config_path(product, root))


def opt_in_line(product: Product, text: str) -> bool:
    """Whether the dev shell sees ``text`` as opted in: it has a ``[tool.<table>…]`` header line.

    The shell can't parse TOML, so this is what decides its tools and apps.
    """
    pattern = _opt_in_re(product)
    return any(pattern.fullmatch(line) for line in text.split("\n"))


def tool_table(product: Product, doc: dict) -> dict | None:
    """The raw ``[tool.<table>]`` table, or ``None`` when there is none (not opted in)."""
    name = product.identity.table
    tool = doc.get("tool", {})
    table = tool.get(name) if isinstance(tool, dict) else None
    if table is not None and not isinstance(table, dict):
        raise ConfigError(f"[tool.{name}] must be a table")
    return table


def check_opt_in_spelling(product: Product, text: str, doc: dict) -> None:
    """Refuse (exit 2) a config file the dev shell and the tools disagree about.

    The tools find the table by parsing TOML, the shell by a line match, and TOML has
    spellings only one of them sees: ``[tool."<table>"]``, ``[ tool.<table> ]`` and
    ``[tool]`` with ``<table>.<key> = …`` create the table without the line, and a
    ``[tool.<table>]`` line inside a multi-line string is the line without the table.
    """
    name = product.identity.table
    table = tool_table(product, doc) is not None
    line = opt_in_line(product, text)
    if table and not line:
        raise ConfigError(
            f"write the table as a [tool.{name}] header (or [tool.{name}.<name>] / "
            f"[[tool.{name}.<name>]]): the dev shell finds it by that line, not by parsing "
            "TOML, so a quoted key, spaces inside the brackets or dotted keys under [tool] "
            f"leave it without {product.identity.name}'s tools"
        )
    if line and not table:
        raise ConfigError(
            f"a line opens [tool.{name}] but TOML sees no such table (is it inside a "
            "multi-line string?): the dev shell takes that line as the opt-in, so reword it "
            "or add the table"
        )


def opted_in(product: Product, doc: dict) -> bool:
    """Whether the checkout opted in: its config file has a ``[tool.<table>]`` table."""
    return tool_table(product, doc) is not None


def opt_in_hint(product: Product) -> str:
    """The message a checkout that has not opted in gets (exit 2)."""
    return product.identity.opt_in_hint


def project_name(doc: dict) -> str | None:
    """``[project].name``, or ``None``."""
    project = doc.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    return name if isinstance(name, str) and name else None
