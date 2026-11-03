"""The config file and its ``[tool.<table>]`` table: loading them, and whether a checkout opted in.

The file is ``product.identity.config_file`` (``pyproject.toml`` by default) and the table
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


def config_path(product: Product, root: Path) -> Path:
    """The checkout's config file: ``<root>/<identity.config_file>``."""
    return root / product.identity.config_file


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
