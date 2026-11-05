"""JSON and JSONC text in exactly the form a formatter leaves it (DESIGN §2.3, jsonfmt).

A managed JSON file is compared byte for byte, and sync runs the product's formatter over
what it writes, so the emitter has to produce that formatter's own output. The form is a
``JsonStyle`` (``Product.json_style``); the default is oxfmt's with ``useTabs``,
``tabWidth`` 2 and ``printWidth`` 110. oxfmt and prettier agree on the rules:

- an object stays expanded when its source has a line break after ``{``: this emitter
  always expands a non-empty object, so the formatter keeps it as written;
- an array is one group: on one line when that line (indent counted at the style's tab
  width, plus the comma after it) fits in the print width, otherwise one element per
  line. An array holding an expanded object always breaks;
- with ``trailing_commas = "jsonc"``, JSONC gets a trailing comma after the last member
  of every broken object and array; JSON never does, nor does JSONC under ``"none"``.
"""

import json
from typing import Any

from standards_core.product import JsonStyle

DEFAULT_STYLE = JsonStyle()


def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _breaks(value: Any) -> bool:
    """Whether ``value`` is printed over several lines whatever the width."""
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        if any(_breaks(v) for v in value):
            return True
        # prettier's forced break: several elements, every one an array of several.
        return len(value) > 1 and all(isinstance(v, list) and len(v) > 1 for v in value)
    return False


def _inline(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_inline(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{}"
    return _scalar(value)


def _level_width(style: JsonStyle) -> int:
    """The columns one indent level takes when a line is measured."""
    return len(style.indent.replace("\t", " " * style.tab_width))


def _emit(
    value: Any, level: int, used: int, comma: int, trailing: bool, style: JsonStyle
) -> str:
    """``value`` printed at indent ``level``, starting ``used`` columns into its line, with
    ``comma`` characters following it on that line."""
    pad = style.indent * (level + 1)
    step = _level_width(style)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = list(value.items())
        lines = ["{"]
        for i, (key, child) in enumerate(items):
            last = i == len(items) - 1
            sep = "" if last and not trailing else ","
            head = f"{_scalar(key)}: "
            body = _emit(
                child,
                level + 1,
                (level + 1) * step + len(head),
                len(sep),
                trailing,
                style,
            )
            lines.append(f"{pad}{head}{body}{sep}")
        lines.append(style.indent * level + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        if not _breaks(value):
            inline = _inline(value)
            if used + len(inline) + comma <= style.width:
                return inline
        lines = ["["]
        for i, child in enumerate(value):
            last = i == len(value) - 1
            sep = "" if last and not trailing else ","
            body = _emit(
                child, level + 1, (level + 1) * step, len(sep), trailing, style
            )
            lines.append(f"{pad}{body}{sep}")
        lines.append(style.indent * level + "]")
        return "\n".join(lines)
    return _scalar(value)


def dumps(value: Any, style: JsonStyle = DEFAULT_STYLE, *, jsonc: bool = False) -> str:
    """``value`` as formatted JSON (or JSONC) in ``style``, ending in a newline."""
    trailing = jsonc and style.trailing_commas == "jsonc"
    return _emit(value, 0, 0, 0, trailing, style) + "\n"


def stringify(value: Any, style: JsonStyle = DEFAULT_STYLE) -> str:
    """``JSON.stringify(value, null, <indent>)`` plus a newline: how sync writes ``package.json``.

    prettier formats ``package.json`` with its ``json-stringify`` parser, which prints every
    non-empty object and array expanded, so this is also the formatter's form of it.
    """
    return json.dumps(value, indent=style.indent, ensure_ascii=False) + "\n"
