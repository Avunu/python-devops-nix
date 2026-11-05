"""Anchored sentinel blocks: managed lines inside a file the checkout owns (DESIGN §5.6).

::

    <indent><L> <markers.block_begin with the block's name>
    <indent>…body…
    <indent><L> <markers.block_end with the block's name>

``L`` is the entry's ``comment`` (``#`` or ``//``); under ``html`` the lines are
``markers.html_begin``/``html_end``. Sync owns the lines between the markers and nothing
else. A block that is present is rewritten where it is; a missing one is inserted at the
entry's anchor: the end of the file (``eof``), the end of a Nix attribute set
(``nix-attrset a.b``), or the end of a multi-line TOML array (``toml-array t.k``). When
the entry is not live, the block and its markers go: the markers prove sync wrote them.

Other generators' blocks are *foreign*: any comment ``>>> … >>>`` that is not an owned
marker opens one, the next ``<<< … <<<`` closes it, and nothing inside one is read,
moved or absorbed. An owned block may not open inside one.

``absorb`` removes the lines outside every block that repeat a body line (a hand-written
``/.venv`` the ``.gitignore`` block now holds), and a comment paragraph that loses all
its other lines with them. ``claims: nix-inputs`` moves the flake inputs the block
defines out of the rest of the anchor attribute set (DESIGN §5.6.4).
"""

from __future__ import annotations

import difflib
import re
import tomllib
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.common import repo
from standards_core.common.report import CLEAN, DRIFT, INVALID, ConfigError, EnvError
from standards_core.scaffold import manifest, nixattrs
from standards_core.scaffold import render as rendering
from standards_core.scaffold.engine import Item
from standards_core.scaffold.handlers import BaseHandler, template_dir

if TYPE_CHECKING:
    from standards_core.product import Product
    from standards_core.scaffold.engine import Plan
    from standards_core.scaffold.manifest import Entry

_NAME = r"[a-z0-9][a-z0-9-]*"
_FOREIGN_BEGIN = re.compile(r">>> .* >>>")
_FOREIGN_END = re.compile(r"<<< .* <<<")
_HTML = re.compile(r"<!--(?P<inner>.*)-->")


@dataclass(frozen=True)
class Found:
    """An owned block (``name``) or a foreign region (``name`` is ``None``): the
    0-based lines of its begin and end markers (an unclosed foreign region ends at the
    last line)."""

    name: str | None
    begin: int
    end: int
    indent: str = ""
    closed: bool = True

    @property
    def foreign(self) -> bool:
        return self.name is None

    def holds(self, line: int) -> bool:
        return self.begin <= line <= self.end


# --- markers -------------------------------------------------------------------------------


def marker_lines(product: Product, comment: str, name: str) -> tuple[str, str]:
    """The begin and end marker lines of block ``name`` (without indentation)."""
    markers = product.markers
    if comment == "html":
        return markers.html_begin.format(name=name), markers.html_end.format(name=name)
    return (
        f"{comment} {markers.block_begin.format(name=name)}",
        f"{comment} {markers.block_end.format(name=name)}",
    )


def _pattern(template: str) -> re.Pattern[str]:
    before, _, after = template.partition("{name}")
    return re.compile(re.escape(before) + f"(?P<name>{_NAME})" + re.escape(after))


def _owned_marker(
    product: Product, comment: str, stripped: str
) -> tuple[str, str] | None:
    """``("begin" | "end", name)`` when ``stripped`` is a marker of the owned shape."""
    begin, end = marker_lines(product, comment, "{name}")
    for kind, template in (("begin", begin), ("end", end)):
        m = _pattern(template).fullmatch(stripped)
        if m:
            return kind, m["name"]
    return None


def is_comment(comment: str, stripped: str) -> bool:
    if comment == "html":
        return stripped.startswith("<!--")
    return stripped.startswith(comment)


def _foreign(comment: str, stripped: str) -> str | None:
    """``"begin"`` or ``"end"`` for a foreign marker line, else ``None``."""
    if comment == "html":
        m = _HTML.fullmatch(stripped)
        if m is None:
            return None
        inner = m["inner"].strip()
    elif stripped.startswith(comment):
        inner = stripped[len(comment) :].strip()
    else:
        return None
    if _FOREIGN_BEGIN.fullmatch(inner):
        return "begin"
    if _FOREIGN_END.fullmatch(inner):
        return "end"
    return None


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def scan(
    text: str,
    product: Product,
    comment: str = "#",
    *,
    path: str = "",
    owned: Collection[str] = (),
) -> list[Found]:
    """The owned blocks (names in ``owned``) and foreign regions of ``text``, in order.

    An owned marker is a line that, stripped, is a marker with a name from ``owned``;
    any other ``>>> … >>>`` / ``<<< … <<<`` comment is foreign. An owned begin without
    its end, an end without its begin, an owned block inside another or inside a
    foreign region, and an owned name twice are exit 2 (``path:line``)."""
    found: list[Found] = []
    lines = text.split("\n")
    opened: tuple[str, int, str] | None = None
    foreign_at: int | None = None
    seen: dict[str, int] = {}
    for n, line in enumerate(lines):
        stripped = line.strip()
        marker = _owned_marker(product, comment, stripped)
        if marker is not None and marker[1] not in owned:
            marker = None
        if opened is not None:
            name, at, indent = opened
            if marker == ("end", name):
                found.append(Found(name, at, n, indent))
                opened = None
            elif marker is not None and marker[0] == "begin":
                raise ConfigError(
                    f"{path}:{n + 1}: the {marker[1]} block opens inside the {name}"
                    f" block (line {at + 1})"
                )
            elif marker is not None:
                raise ConfigError(
                    f"{path}:{n + 1}: the {marker[1]} block ends inside the {name}"
                    f" block (line {at + 1})"
                )
            continue
        if foreign_at is not None:
            if marker is not None:
                raise ConfigError(
                    f"{path}:{n + 1}: the {marker[1]} block marker is inside the"
                    f" foreign block that starts at line {foreign_at + 1}"
                )
            if _foreign(comment, stripped) == "end":
                found.append(Found(None, foreign_at, n, _indent(lines[foreign_at])))
                foreign_at = None
            continue
        if marker is not None and marker[0] == "begin":
            name = marker[1]
            if name in seen:
                raise ConfigError(
                    f"{path}:{n + 1}: the {name} block is there twice (also at line"
                    f" {seen[name] + 1})"
                )
            seen[name] = n
            opened = (name, n, _indent(line))
        elif marker is not None:
            raise ConfigError(
                f"{path}:{n + 1}: the {marker[1]} block ends, never begun"
            )
        elif _foreign(comment, stripped) == "begin":
            foreign_at = n
    if opened is not None:
        raise ConfigError(f"{path}:{opened[1] + 1}: the {opened[0]} block never ends")
    if foreign_at is not None:
        found.append(
            Found(None, foreign_at, len(lines) - 1, _indent(lines[foreign_at]), False)
        )
    return found


def owned_names(product: Product, entry: Entry) -> tuple[str, ...]:
    """The blocks of every sentinel entry for ``entry``'s path, live or not."""
    return tuple(
        e.block
        for e in manifest.load(product).entries
        if e.strategy == "sentinel" and e.path == entry.path and e.block
    ) or ((entry.block,) if entry.block else ())


def block_lines(
    product: Product, comment: str, name: str, body: str, indent: str
) -> list[str]:
    """The block's lines: its markers around ``body``, each non-empty line indented."""
    begin, end = marker_lines(product, comment, name)
    lines = [indent + line if line else "" for line in body.split("\n")] if body else []
    return [indent + begin, *lines, indent + end]


def _body_lines(body: str, indent: str) -> list[str]:
    return [indent + line if line else "" for line in body.split("\n")] if body else []


# --- anchors -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spot:
    """Where a missing block goes: before 0-based line ``line`` of ``text`` (which the
    anchor may have rewritten, an inline empty TOML array opened up), indented by
    ``indent``."""

    text: str
    line: int
    indent: str


def _split_before(text: str, offset: int, indent: str) -> str:
    """``text`` with the closer at ``offset`` moved to a line of its own."""
    start = offset
    while start > 0 and text[start - 1] in " \t":
        start -= 1
    return text[:start] + "\n" + indent + text[offset:]


def _nix_spot(
    text: str, path: str, steps: Sequence[str], name: str, anchor: str
) -> Spot:
    found = nixattrs.scan(text, path)
    attrset = found.find(steps)
    if attrset is None:
        raise ConfigError(
            f"{path}: the {name} block's anchor {anchor} is missing: no"
            f" `{'.'.join(steps)} = {{` attribute set"
        )
    if nixattrs.inline(text, attrset):
        raise ConfigError(
            f"{path}: write `{steps[-1]} = {{` with its attributes on lines of their"
            " own; the managed block needs a line to go in"
        )
    indent = nixattrs.child_indent(text, attrset)
    if text[nixattrs.line_start(text, attrset.end) : attrset.end].strip():
        text = _split_before(text, attrset.end, nixattrs.leading(text, attrset.start))
        return _nix_spot(text, path, steps, name, anchor)
    return Spot(text, nixattrs.line_of(text, attrset.end) - 1, indent)


_TOML_HEADER = re.compile(
    r"^[ \t]*\[(?!\[)[ \t]*(?P<name>[^\[\]]+?)[ \t]*\][ \t]*(#.*)?$"
)


def _toml_key(key: str) -> str:
    """A pattern for ``key`` bare or quoted."""
    return rf"""(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')"""


def _toml_close(text: str, open_: int, path: str) -> int:
    """The offset of the ``]`` matching the ``[`` at ``open_``."""
    depth = 0
    i = open_
    while i < len(text):
        for quote in ('"""', "'''"):
            if text.startswith(quote, i):
                end = text.find(quote, i + 3)
                if end < 0:
                    raise ConfigError(f"{path}: a {quote} string is never closed")
                i = end + 3
                break
        else:
            c = text[i]
            if c == '"':
                j = i + 1
                while j < len(text) and text[j] not in '"\n':
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
            elif c == "'":
                end = text.find("'", i + 1)
                i = (end if end >= 0 else len(text)) + 1
            elif c == "#":
                end = text.find("\n", i)
                i = end if end >= 0 else len(text)
            elif c == "[":
                depth += 1
                i += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return i
                i += 1
            else:
                i += 1
    raise ConfigError(f"{path}: an array is never closed")


def _toml_last(text: str, open_: int, close: int) -> tuple[int, str]:
    """Just after the last character between ``[`` and ``]`` that is neither a comment
    nor whitespace, and that character."""
    last = (open_ + 1, "[")
    i = open_ + 1
    while i < close:
        c = text[i]
        if text.startswith(('"""', "'''"), i):
            end = text.find(text[i : i + 3], i + 3) + 3
            last, i = (end, c), end
        elif c == '"':
            j = i + 1
            while j < close and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            last, i = (j + 1, c), j + 1
        elif c == "'":
            j = text.find("'", i + 1) + 1
            last, i = (j, c), j
        elif c == "#":
            end = text.find("\n", i)
            i = close if end < 0 else end
        elif c.isspace():
            i += 1
        else:
            last, i = (i + 1, c), i + 1
    return last


def toml_array(text: str, where: str) -> tuple[int, int] | None:
    """``(key line start, offset of its [)`` of the array ``where`` (``table.key``, or
    ``.key`` at the root) names: ``key = [`` in ``[table]``, else the dotted
    ``table.key = [`` before the first table; ``None`` when there is none."""
    table, _, key = where.rpartition(".")
    lines = text.split("\n")
    offsets = []
    at = 0
    for line in lines:
        offsets.append(at)
        at += len(line) + 1
    sections: list[tuple[str, int, int]] = []  # (table, first line, end line)
    current, first = "", 0
    for n, line in enumerate(lines):
        m = _TOML_HEADER.match(line)
        if m or line.lstrip().startswith("[["):
            sections.append((current, first, n))
            current, first = (m["name"].strip() if m else "\0"), n + 1
    sections.append((current, first, len(lines)))
    wanted = [(table, _toml_key(key))]
    if table:
        dotted = r"\s*\.\s*".join(_toml_key(k) for k in table.split("."))
        wanted.append(("", dotted + r"\s*\.\s*" + _toml_key(key)))
    for section, pattern in wanted:
        for name, start, end in sections:
            if name != section:
                continue
            for n in range(start, end):
                m = re.match(rf"[ \t]*{pattern}[ \t]*=[ \t]*\[", lines[n])
                if m:
                    return offsets[n], offsets[n] + m.end() - 1
    return None


def _toml_spot(text: str, path: str, where: str, name: str, anchor: str) -> Spot:
    found = toml_array(text, where)
    if found is None:
        raise ConfigError(
            f"{path}: the {name} block's anchor {anchor} is missing: no `{where} = [`"
        )
    key_start, open_ = found
    close = _toml_close(text, open_, path)
    key_indent = nixattrs.leading(text, key_start)
    if "\n" not in text[open_:close]:
        if text[open_ + 1 : close].strip():
            raise ConfigError(
                f"{path}: write `{where.rpartition('.')[2]} = [` with its items on lines"
                " of their own; the managed block needs a line to go in"
            )
        text = text[: open_ + 1] + "\n" + key_indent + text[close:]
        return _toml_spot(text, path, where, name, anchor)
    if text[nixattrs.line_start(text, close) : close].strip():
        text = _split_before(text, close, key_indent)
        return _toml_spot(text, path, where, name, anchor)
    # The first item's indentation, else the key's plus four spaces.
    indent = key_indent + "    "
    i = open_ + 1
    while i < close:
        if text[i].isspace():
            i += 1
        elif text[i] == "#":
            i = text.find("\n", i)
        else:
            indent = nixattrs.leading(text, i)
            break
    # The last item before the block needs its comma.
    last, char = _toml_last(text, open_, close)
    if char not in ",[":
        text = text[:last] + "," + text[last:]
        close += 1
    return Spot(text, nixattrs.line_of(text, close) - 1, indent)


def spot(text: str, path: str, anchor: str, name: str) -> Spot:
    """Where the missing block ``name`` goes under ``anchor``; a missing or unusable
    anchor is exit 2."""
    kind, _, where = anchor.partition(" ")
    if kind == "eof":
        if text and not text.endswith("\n"):
            text += "\n"
        lines = text.split("\n")
        return Spot(text, len(lines) - 1, "")
    if kind == "nix-attrset":
        return _nix_spot(text, path, where.split("."), name, anchor)
    if kind == "toml-array":
        return _toml_spot(text, path, where, name, anchor)
    raise ConfigError(f"{path}: unknown anchor {anchor!r}")


def anchor_found(anchor: str, text: str) -> bool:
    """Whether ``text`` holds ``anchor`` (what a ``seed_template`` must hold)."""
    kind, _, where = anchor.partition(" ")
    if kind == "eof":
        return True
    try:
        if kind == "nix-attrset":
            return nixattrs.scan(text).find(where.split(".")) is not None
        if kind == "toml-array":
            return toml_array(text, where) is not None
    except ConfigError:
        return False
    return False


# --- edits ---------------------------------------------------------------------------------


def _replace_lines(text: str, start: int, end: int, new: list[str]) -> str:
    """``text`` with 0-based lines ``start..end`` (inclusive) replaced by ``new``."""
    lines = text.split("\n")
    return "\n".join([*lines[:start], *new, *lines[end + 1 :]])


def insert(text: str, at: Spot, lines: list[str]) -> str:
    """``at.text`` with ``lines`` before its line ``at.line``."""
    rows = at.text.split("\n")
    if at.line >= len(rows) - 1 and rows[-1] == "":
        # The end of the file: the block, then the final newline.
        return "\n".join([*rows[:-1], *lines, ""])
    return "\n".join([*rows[: at.line], *lines, *rows[at.line :]])


def absorb(
    text: str, found: list[Found], body: str, comment: str, name: str
) -> tuple[str, list[str]]:
    """``text`` without the lines outside every block and foreign region that repeat a
    non-comment body line, and without a comment paragraph that loses every other line
    (CR4); the problems ``--check`` reports for them."""
    wanted = {
        line.strip()
        for line in body.split("\n")
        if line.strip() and not is_comment(comment, line.strip())
    }
    lines = text.split("\n")
    excluded = {n for f in found for n in range(f.begin, f.end + 1)}
    gone: set[int] = set()
    problems: list[str] = []
    paragraph: list[int] = []

    def close() -> None:
        absorbed = [n for n in paragraph if lines[n].strip() in wanted]
        if not absorbed:
            return
        gone.update(absorbed)
        others = [n for n in paragraph if n not in gone]
        if all(is_comment(comment, lines[n].strip()) for n in others):
            gone.update(others)
            # The blank line the paragraph leaves behind goes with it.
            after = paragraph[-1] + 1
            before = paragraph[0] - 1
            if after < len(lines) - 1 and not lines[after].strip():
                gone.add(after)
            elif before >= 0 and not lines[before].strip() and after >= len(lines) - 1:
                gone.add(before)

    for n, line in enumerate(lines):
        if n in excluded or not line.strip():
            close()
            paragraph = []
            continue
        paragraph.append(n)
    close()
    for n in sorted(gone):
        stripped = lines[n].strip()
        if not stripped:
            continue
        if stripped in wanted:
            problems.append(f"line {n + 1} is in the {name} block")
        else:
            problems.append(f"line {n + 1} goes with the lines the {name} block holds")
    if not gone:
        return text, []
    return "\n".join(line for n, line in enumerate(lines) if n not in gone), problems


# --- claims ---------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split())


def _signature(
    text: str, children: Sequence[nixattrs.Child]
) -> tuple[str | None, tuple[str, ...]]:
    """An input's definition, comparable: its ``input_spec`` and the rest of its
    attributes as whitespace-normalised text."""
    spec = None
    rest: list[str] = []
    for child in children:
        tail = child.path[1:]
        if (
            len(tail) == 1
            and tail[0] in ("url", "follows")
            and child.string is not None
        ):
            spec = nixattrs.input_spec(str(tail[0]), child.string)
        elif not tail and child.attrset is not None:
            for inner in child.attrset.children:
                key = inner.path
                if len(key) == 1 and key[0] in ("url", "follows") and inner.string:
                    spec = nixattrs.input_spec(str(key[0]), inner.string)
                else:
                    value = text[inner.value_start : inner.value_end]
                    rest.append(_norm(f"{'.'.join(map(str, key))} = {value}"))
        else:
            value = text[child.value_start : child.value_end]
            rest.append(_norm(f"{'.'.join(map(str, tail))} = {value}"))
    return spec, tuple(sorted(rest))


def _remove_children(text: str, children: Sequence[nixattrs.Child]) -> str:
    """``text`` without ``children``: their whole lines when they own them."""
    for child in sorted(children, key=lambda c: c.start, reverse=True):
        start = nixattrs.line_start(text, child.start)
        newline = text.find("\n", child.end)
        end = len(text) if newline < 0 else newline + 1
        after = text[child.end : end].strip()
        if text[start : child.start].strip() or (after and not after.startswith("#")):
            start, end = child.start, child.end
        text = text[:start] + text[end:]
    return text


@dataclass
class Claims:
    """The claimed inputs defined outside the block: removed from ``text``; those whose
    definition differs from the block's are in ``differ``, the equal ones in ``moved``."""

    text: str
    moved: list[str]
    differ: list[str]


def claims(text: str, path: str, anchor: str, body: str, found: list[Found]) -> Claims:
    """``claims: nix-inputs``: the anchor set's direct children outside every block and
    foreign region whose name the block's body defines."""
    steps = anchor.partition(" ")[2].split(".")
    attrset = nixattrs.scan(text, path).find(steps)
    if attrset is None:
        return Claims(text, [], [])
    ours: dict[str, list[nixattrs.Child]] = {}
    for child in nixattrs.bindings(body, path):
        if child.name is not None:
            ours.setdefault(child.name, []).append(child)
    theirs: dict[str, list[nixattrs.Child]] = {}
    for child in attrset.children:
        line = nixattrs.line_of(text, child.start) - 1
        if child.name in ours and not any(f.holds(line) for f in found):
            theirs.setdefault(child.name, []).append(child)
    moved, differ = [], []
    for name in sorted(theirs):
        same = _signature(text, theirs[name]) == _signature(body, ours[name])
        (moved if same else differ).append(name)
    text = _remove_children(text, [c for cs in theirs.values() for c in cs])
    return Claims(text, moved, differ)


# --- the handler -----------------------------------------------------------------------------


def _at_head(plan: Plan, path: str) -> str | None:
    try:
        return repo.git(plan.root, "show", f"HEAD:./{path}")
    except EnvError:
        return None


def _has_block(
    plan: Plan, entry: Entry, text: str | None, owned: Sequence[str]
) -> bool:
    if text is None:
        return False
    try:
        found = scan(text, plan.product, entry.comment, owned=owned)
    except ConfigError:
        return False
    return any(f.name == entry.block for f in found)


class SentinelHandler(BaseHandler):
    """``sentinel``: one managed block in a file the checkout owns."""

    name = "sentinel"
    strategies = ("sentinel",)

    def owned(self, plan: Plan, entry: Entry) -> tuple[str, ...]:
        """The block names this file's markers may carry (``owned_names``)."""
        return owned_names(plan.product, entry)

    def seed(self, plan: Plan, entry: Entry, templates: Path | None) -> str | None:
        """The text a missing file starts from: the ``seed_template`` rendered."""
        if not entry.seed_template:
            return None
        return rendering.render(plan.product, entry.seed_template, plan.ctx, None)

    def body(self, plan: Plan, entry: Entry, templates: Path | None) -> str:
        """The block's body: the template rendered, its trailing newline stripped."""
        text = rendering.render(
            plan.product,
            entry.template or "",
            plan.ctx,
            None,
            directory=template_dir(entry, templates),
        )
        return text.rstrip("\n")

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        product = plan.product
        name = entry.block or ""
        anchor = entry.anchor or "eof"
        owned = self.owned(plan, entry)
        body = self.body(plan, entry, templates)
        problems: list[str] = []
        text = current
        if text is None:
            text = self.seed(plan, entry, templates)
            if text is None and anchor == "eof":
                text = ""
            elif text is None:
                raise ConfigError(
                    f"{path} does not exist: the {name} block needs {anchor}"
                )
            problems.append("missing")
        found = scan(text, product, entry.comment, path=path, owned=owned)
        if entry.absorb:
            text, absorbed = absorb(text, found, body, entry.comment, name)
            problems += absorbed
            found = scan(text, product, entry.comment, path=path, owned=owned)
        differ: list[str] = []
        if entry.claims:
            claimed = claims(text, path, anchor, body, found)
            text = claimed.text
            differ = claimed.differ
            problems += [
                f"{n} moves into the managed {name} block" for n in claimed.moved
            ]
            problems += [
                f"{n} is replaced by the managed {name} block" for n in claimed.differ
            ]
            found = scan(text, product, entry.comment, path=path, owned=owned)
        block = next((f for f in found if f.name == name), None)
        if block is not None:
            have = text.split("\n")[block.begin + 1 : block.end]
            want = _body_lines(body, block.indent)
            if have != want:
                text = _replace_lines(text, block.begin + 1, block.end - 1, want)
                problems.append(f"the {name} block differs")
        else:
            at = spot(text, path, anchor, name)
            for f in found:
                if (
                    f.foreign
                    and f.begin < at.line
                    and (not f.closed or at.line <= f.end)
                ):
                    raise ConfigError(
                        f"{path}:{at.line + 1}: the {name} block would go inside the"
                        f" foreign block that starts at line {f.begin + 1}; move one of"
                        " them"
                    )
            text = insert(
                text, at, block_lines(product, entry.comment, name, body, at.indent)
            )
            if current is not None:
                problems.append(f"missing the {name} block")
            if anchor.startswith("toml-array "):
                try:
                    tomllib.loads(text)
                except tomllib.TOMLDecodeError as e:
                    raise ConfigError(
                        f"{path}: the {name} block does not leave valid TOML: {e}"
                    ) from e
        if differ and not plan.ctx.options.get("force"):
            return [self.refused(plan, entry, path, current, text, differ, owned)]
        code = DRIFT if text != current else CLEAN
        return [
            Item(
                path,
                entry.strategy,
                current,
                text,
                "; ".join(problems) if code else "",
                code,
                phase=entry.phase,
                entry=entry,
            )
        ]

    def refused(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        wanted: str,
        differ: list[str],
        owned: Sequence[str],
    ) -> Item:
        """A claimed input defined otherwise outside the block: exit 2. Before the first
        opt-in (the block is neither at ``HEAD`` nor in the file) the message shows the
        replacement ``--force`` writes."""
        name = entry.block
        first = not _has_block(plan, entry, current, owned) and not _has_block(
            plan, entry, _at_head(plan, path), owned
        )
        inputs = ", ".join(differ)
        if first:
            diff = "".join(
                difflib.unified_diff(
                    (current or "").splitlines(True),
                    wanted.splitlines(True),
                    "current",
                    "rendered",
                )
            )
            problem = (
                f"{path} defines {inputs} otherwise than the managed {name} block: sync"
                " would replace the definition with the block's. Pass --force to"
                f" replace it. The replacement:\n{diff}"
            )
        else:
            problem = (
                f"{path} defines {inputs} outside the managed {name} block (--force"
                " replaces the definition with the block's)"
            )
        return Item(
            path,
            entry.strategy,
            current,
            current,
            problem,
            INVALID,
            phase=entry.phase,
            entry=entry,
        )

    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """The block and its markers go when its entry is not live (its module off, or
        its ``when`` false): the markers prove sync wrote it."""
        found = scan(
            current,
            plan.product,
            entry.comment,
            path=path,
            owned=self.owned(plan, entry),
        )
        block = next((f for f in found if f.name == entry.block), None)
        if block is None:
            return None
        text = _replace_lines(current, block.begin, block.end, [])
        return Item(
            path,
            entry.strategy,
            current,
            text,
            f"the {entry.block} block goes with {','.join(entry.modules)}",
            DRIFT,
            phase=entry.phase,
            entry=entry,
        )

    def absent(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        return self.retract(plan, entry, path, current)
