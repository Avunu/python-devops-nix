"""The ``jsonc-merge`` strategy: some leaves of a JSONC file the checkout owns (DESIGN §5.5).

A file the checkout and its editor own (``.vscode/settings.json``,
``.vscode/extensions.json``), of which sync owns only a few leaves. The entry's template
renders a merge spec, in JSON::

    {
      "set": {"a.b": 1, "[x]": {"y": true}},
      "union": {"recommendations": {"ensure": ["v.x"], "opposite": "unwantedRecommendations"}},
      "forbidden": [["k"], {"prefix": "p."}],
      "never": [["n"], {"prefix": "q.", "in": ["parent"]}]
    }

Paths are key lists, never split on dots; a top-level string is shorthand for
``[string]``. A ``set`` dict merges per sub-key (its leaves are managed), any other value
is one managed leaf. A ``union`` array gets every ``ensure`` item (appended when absent)
and loses them from its sibling ``opposite``. ``forbidden`` keys are deleted (a
``{"prefix": …, "in": parent}`` form deletes every key of the parent starting with
it). ``never`` documents keys sync must not touch: a ``set``, ``union`` or ``forbidden``
path that reaches one is a ``ManifestError``.

The editor rewrites only what changes, so comments, foreign keys, the indentation, the
trailing-comma style, the separators of an inline container and the end of the file are
the checkout's. ``--check`` compares parsed values: a comment or a reformat is never
drift.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from standards_core.common.report import DRIFT, ConfigError
from standards_core.scaffold import engine, jsonfmt
from standards_core.scaffold import render as rendering
from standards_core.scaffold.globs import ManifestError
from standards_core.scaffold.handlers import BaseHandler, item, template_dir

if TYPE_CHECKING:
    from standards_core.scaffold.engine import Item, Plan
    from standards_core.scaffold.manifest import Entry

KeyPath = tuple[str, ...]

# --- the parser ----------------------------------------------------------------------------


@dataclass
class Node:
    kind: Literal["object", "array", "value"]
    start: int
    end: int  # just after its last character
    members: list[Member]


@dataclass
class Member:
    key: str | None  # None in an array
    key_span: tuple[int, int] | None
    value: Node
    line_start: int  # the start of the line it starts on
    line_end: int  # just after the line it ends on (its comma's, when it has one)
    comma: int | None  # its comma's offset

    @property
    def start(self) -> int:
        return self.key_span[0] if self.key_span else self.value.start

    @property
    def stop(self) -> int:
        """Just after its comma, else after its value."""
        return self.comma + 1 if self.comma is not None else self.value.end


_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_LITERALS = ("true", "false", "null")


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


class _Parser:
    def __init__(self, text: str, path: str) -> None:
        self.text = text
        self.path = path

    def fail(self, offset: int, message: str) -> ConfigError:
        return ConfigError(f"{self.path}:{_line(self.text, offset)}: {message}")

    def skip(self, i: int) -> int:
        """Past whitespace and comments."""
        text = self.text
        while i < len(text):
            if text[i].isspace():
                i += 1
            elif text.startswith("//", i):
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
            elif text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end < 0:
                    raise self.fail(i, "a /* comment is never closed")
                i = end + 2
            else:
                break
        return i

    def string(self, i: int) -> int:
        text = self.text
        j = i + 1
        while j < len(text):
            if text[j] == "\\":
                j += 2
            elif text[j] == '"':
                return j + 1
            elif text[j] == "\n":
                break
            else:
                j += 1
        raise self.fail(i, "a string is never closed")

    def value(self, i: int) -> Node:
        text = self.text
        i = self.skip(i)
        if i >= len(text):
            raise self.fail(i, "a value is missing")
        c = text[i]
        if c == "{":
            return self.container(i, "object", "}")
        if c == "[":
            return self.container(i, "array", "]")
        if c == '"':
            return Node("value", i, self.string(i), [])
        if m := _NUMBER.match(text, i):
            return Node("value", i, m.end(), [])
        for literal in _LITERALS:
            if text.startswith(literal, i):
                return Node("value", i, i + len(literal), [])
        raise self.fail(i, f"unexpected {c!r}")

    def line_end(self, offset: int) -> int:
        end = self.text.find("\n", offset)
        return len(self.text) if end < 0 else end + 1

    def container(
        self, start: int, kind: Literal["object", "array"], closer: str
    ) -> Node:
        text = self.text
        node = Node(kind, start, start, [])
        i = self.skip(start + 1)
        while True:
            if i >= len(text):
                raise self.fail(start, f"{text[start]!r} is never closed")
            if text[i] == closer:
                node.end = i + 1
                return node
            key = None
            key_span = None
            if kind == "object":
                if text[i] != '"':
                    raise self.fail(i, "an object key must be a string")
                end = self.string(i)
                key = json.loads(text[i:end])
                key_span = (i, end)
                i = self.skip(end)
                if i >= len(text) or text[i] != ":":
                    raise self.fail(i, "':' is missing after a key")
                i += 1
            value = self.value(i)
            first = key_span[0] if key_span else value.start
            i = self.skip(value.end)
            comma = None
            if i < len(text) and text[i] == ",":
                comma = i
                i = self.skip(i + 1)
            elif i < len(text) and text[i] != closer:
                raise self.fail(i, f"',' or {closer!r} is missing")
            last = comma + 1 if comma is not None else value.end
            node.members.append(
                Member(
                    key,
                    key_span,
                    value,
                    text.rfind("\n", 0, first) + 1,
                    self.line_end(last),
                    comma,
                )
            )


def parse(text: str, path: str) -> Node:
    """``text``'s top-level value. A syntax error is exit 2 (``path:line``)."""
    parser = _Parser(text, path)
    node = parser.value(0)
    rest = parser.skip(node.end)
    if rest < len(text):
        raise parser.fail(rest, "text after the top-level value")
    return node


def value(node: Node, text: str) -> Any:
    """The semantic value of ``node`` (comments and trailing commas ignored)."""
    if node.kind == "object":
        return {m.key: value(m.value, text) for m in node.members}
    if node.kind == "array":
        return [value(m.value, text) for m in node.members]
    return json.loads(text[node.start : node.end])


def loads(text: str, path: str) -> Any:
    return value(parse(text, path), text)


# --- layout --------------------------------------------------------------------------------


def _leading(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    line = text[start:]
    return line[: len(line) - len(line.lstrip(" \t"))]


def indent_unit(text: str) -> str:
    """``"\\t"`` if the first indented line starts with a tab, else its run of spaces,
    else 4 spaces."""
    for line in text.splitlines():
        if line[:1] == "\t":
            return "\t"
        if line[:1] == " " and line.strip():
            return line[: len(line) - len(line.lstrip(" "))]
    return "    "


def _inline(text: str, node: Node) -> bool:
    """An object or array whose opener and closer are on one line."""
    return "\n" not in text[node.start : node.end]


def _containers(node: Node):
    if node.kind != "value":
        yield node
    for m in node.members:
        yield from _containers(m.value)


def _separators(text: str, node: Node, root: Node) -> tuple[str, str]:
    """The file's own ``(item, key)`` separators for an inline container: from its
    members first, then the file's; else ``json.dumps``'s. With no comma to go by, the
    item separator follows the key separator's spacing."""
    item_sep = key_sep = None
    for container in (node, *_containers(root)):
        for m in container.members:
            if key_sep is None and m.key_span is not None:
                between = text[m.key_span[1] : m.value.start]
                colon = between.find(":")
                key_sep = ": " if between[colon + 1 : colon + 2] == " " else ":"
            if item_sep is None and m.comma is not None and _inline(text, container):
                item_sep = ", " if text[m.comma + 1 : m.comma + 2] == " " else ","
    if item_sep is None:
        # No inline comma to go by: a file that writes `"a":1` writes `1,2` too.
        item_sep = "," if key_sep == ":" else ", "
    return item_sep, key_sep or ": "


def _dump_inline(v: Any, seps: tuple[str, str]) -> str:
    return json.dumps(v, ensure_ascii=False, separators=seps)


def _dump_broken(v: Any, unit: str, base: str) -> str:
    if not isinstance(v, dict | list) or not v:
        return json.dumps(v, ensure_ascii=False)
    lines = json.dumps(v, indent=unit, ensure_ascii=False).split("\n")
    return "\n".join([lines[0], *(base + line for line in lines[1:])])


# --- navigation ----------------------------------------------------------------------------


def _show(path: Sequence[str]) -> str:
    return "/".join(path)


def _member(node: Node, key: str) -> Member | None:
    found = None
    for m in node.members:
        if m.key == key:
            found = m  # the last one wins, as in JSON.parse
    return found


def _walk(
    text: str, path: Sequence[str], where: str
) -> tuple[Node, Member | None, int]:
    """``(parent, member, depth)``: the deepest object reached along ``path`` and the
    member for ``path[depth]`` in it (``None`` when it is missing). A path that crosses
    a non-object is exit 2."""
    root = parse(text, where)
    if root.kind != "object":
        raise ConfigError(f"{where}: the top level is not an object")
    node = root
    for depth, key in enumerate(path):
        m = _member(node, key)
        if m is None or depth == len(path) - 1:
            return node, m, depth
        if m.value.kind != "object":
            raise ConfigError(
                f"{where}: {_show(path[: depth + 1])} is not an object, so"
                f" {_show(path)} cannot be set"
            )
        node = m.value
    raise ConfigError(f"{where}: an empty key path")


# --- edits ---------------------------------------------------------------------------------


def _insert(text: str, node: Node, key: str | None, v: Any, unit: str) -> str:
    """``text`` with a member added at the end of ``node``."""
    root = parse(text, "<edit>")
    if _inline(text, node):
        seps = _separators(text, node, root)
        member = (
            json.dumps(key, ensure_ascii=False) + seps[1] if key is not None else ""
        )
        member += _dump_inline(v, seps)
        if not node.members:
            return text[: node.start + 1] + member + text[node.start + 1 :]
        last = node.members[-1]
        if last.comma is not None:
            at = last.comma + 1
            return text[:at] + seps[0][1:] + member + "," + text[at:]
        at = last.value.end
        return text[:at] + seps[0] + member + text[at:]
    _, key_sep = _separators(text, node, root)
    if not node.members:
        indent = _leading(text, node.start) + unit
        member = (
            json.dumps(key, ensure_ascii=False) + key_sep if key is not None else ""
        )
        member += _dump_broken(v, unit, indent)
        at = node.start + 1
        return text[:at] + "\n" + indent + member + text[at:]
    last = node.members[-1]
    indent = _leading(text, last.start)
    member = json.dumps(key, ensure_ascii=False) + key_sep if key is not None else ""
    member += _dump_broken(v, unit, indent)
    closer = node.end - 1
    newline = text.find("\n", last.stop)
    if newline < 0 or newline > closer:
        # The closer shares the last member's line: the new member gets a line, and so
        # does the closer.
        at = closer
        tail = "\n" + _leading(text, node.start)
        while at > last.stop and text[at - 1] in " \t":
            at -= 1
    else:
        at = newline
        tail = ""
    new = "\n" + indent + member + ("," if last.comma is not None else "") + tail
    if last.comma is None:
        cut = last.value.end
        return text[:cut] + "," + text[cut:at] + new + text[at:]
    return text[:at] + new + text[at:]


def _remove(text: str, node: Node, index: int) -> str:
    """``text`` without ``node.members[index]``."""
    m = node.members[index]
    members = node.members
    if _inline(text, node):
        if m.comma is not None:
            end = m.comma + 1
            while end < len(text) and text[end] in " \t":
                end += 1
            return text[: m.start] + text[end:]
        if index > 0:
            prev = members[index - 1]
            return text[: prev.comma] + text[m.value.end :]
        return text[: m.start] + text[m.value.end :]
    before = text[m.line_start : m.start]
    after = text[m.stop : m.line_end].strip()
    owns = not before.strip() and (not after or after.startswith("//"))
    start, end = (m.line_start, m.line_end) if owns else (m.start, m.stop)
    out = text[:start] + text[end:]
    if index == len(members) - 1 and m.comma is None and index > 0:
        prev = members[index - 1]
        if prev.comma is not None:
            out = out[: prev.comma] + out[prev.comma + 1 :]
    return out


def set_leaf(text: str, path: Sequence[str], v: Any, *, indent: str) -> str:
    """``text`` with ``path`` set to ``v``: only the value's span is rewritten, or the
    member (with any missing objects on the way) is added."""
    path = tuple(path)
    node, m, depth = _walk(text, path, "<edit>")
    if m is None:
        nested = v
        for key in reversed(path[depth + 1 :]):
            nested = {key: nested}
        return _insert(text, node, path[depth], nested, indent)
    if not isinstance(v, dict | list) or not v:
        new = json.dumps(v, ensure_ascii=False)
    elif _inline(text, node) or (m.value.kind != "value" and _inline(text, m.value)):
        new = _dump_inline(v, _separators(text, node, parse(text, "<edit>")))
    else:
        new = _dump_broken(v, indent, _leading(text, m.start))
    return text[: m.value.start] + new + text[m.value.end :]


def delete(text: str, path: Sequence[str]) -> str:
    """``text`` without the member at ``path`` (unchanged when there is none)."""
    path = tuple(path)
    node, m, depth = _walk(text, path, "<edit>")
    if m is None or depth != len(path) - 1:
        return text
    index = max(i for i, x in enumerate(node.members) if x is m)
    return _remove(text, node, index)


def _array(text: str, path: Sequence[str], where: str) -> Node | None:
    _, m, depth = _walk(text, path, where)
    if m is None or depth != len(path) - 1:
        return None
    if m.value.kind != "array":
        raise ConfigError(f"{where}: {_show(path)} is not an array")
    return m.value


def ensure_items(
    text: str, path: Sequence[str], ids: Sequence[Any], *, indent: str
) -> str:
    """``text`` whose array at ``path`` holds every one of ``ids`` (appended in order
    when absent; the array is created when missing)."""
    path = tuple(path)
    if _array(text, path, "<edit>") is None:
        return set_leaf(text, path, list(ids), indent=indent)
    for item_id in ids:
        array = _array(text, path, "<edit>")
        assert array is not None
        if item_id not in value(array, text):
            text = _insert(text, array, None, item_id, indent)
    return text


def remove_items(text: str, path: Sequence[str], ids: Sequence[Any]) -> str:
    """``text`` whose array at ``path`` holds none of ``ids``."""
    path = tuple(path)
    while True:
        array = _array(text, path, "<edit>")
        if array is None:
            return text
        index = next(
            (i for i, m in enumerate(array.members) if value(m.value, text) in ids),
            None,
        )
        if index is None:
            return text
        text = _remove(text, array, index)


# --- the merge spec ------------------------------------------------------------------------


@dataclass(frozen=True)
class Prefix:
    prefix: str
    parent: KeyPath = ()

    def matches(self, path: KeyPath) -> bool:
        n = len(self.parent)
        return (
            len(path) > n
            and path[:n] == self.parent
            and path[n].startswith(self.prefix)
        )


@dataclass(frozen=True)
class Union:
    path: KeyPath
    ensure: tuple[Any, ...]
    opposite: str | None


@dataclass(frozen=True)
class Spec:
    set: tuple[tuple[KeyPath, Any], ...]
    union: tuple[Union, ...]
    forbidden: tuple[KeyPath | Prefix, ...]
    never: tuple[KeyPath | Prefix, ...]


def _key_path(raw: Any, where: str) -> KeyPath | Prefix:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list) and raw and all(isinstance(k, str) for k in raw):
        return tuple(raw)
    if (
        isinstance(raw, dict)
        and isinstance(raw.get("prefix"), str)
        and raw["prefix"]
        and set(raw) <= {"prefix", "in"}
    ):
        parent = raw.get("in", [])
        if not isinstance(parent, list) or not all(isinstance(k, str) for k in parent):
            raise ManifestError(f'{where}: a prefix\'s "in" is a key list')
        return Prefix(raw["prefix"], tuple(parent))
    raise ManifestError(
        f"{where}: {raw!r} is not a key path (a string, a list of keys or a prefix form)"
    )


def _leaves(raw: dict, prefix: KeyPath = ()) -> list[tuple[KeyPath, Any]]:
    out: list[tuple[KeyPath, Any]] = []
    for key, v in raw.items():
        path = (*prefix, key)
        if isinstance(v, dict) and v:
            out += _leaves(v, path)
        else:
            out.append((path, v))
    return out


def _clash(path: KeyPath | Prefix, never: KeyPath | Prefix) -> bool:
    if isinstance(path, Prefix) and isinstance(never, Prefix):
        return path.parent == never.parent and (
            path.prefix.startswith(never.prefix) or never.prefix.startswith(path.prefix)
        )
    if isinstance(path, Prefix):
        return path.matches(never) if isinstance(never, tuple) else False
    if isinstance(never, Prefix):
        return never.matches(path)
    n = min(len(path), len(never))
    return path[:n] == never[:n]


def parse_spec(raw: Any, where: str) -> Spec:
    """A rendered merge spec; a malformed one, or one that touches a ``never`` path, is
    a ``ManifestError`` (a product bug)."""
    if not isinstance(raw, dict) or set(raw) - {"set", "union", "forbidden", "never"}:
        raise ManifestError(
            f"{where}: a jsonc-merge spec is an object of set, union, forbidden, never"
        )
    sets = raw.get("set", {})
    unions = raw.get("union", {})
    if not isinstance(sets, dict) or not isinstance(unions, dict):
        raise ManifestError(f"{where}: set and union are objects")
    union = []
    for key, spec in unions.items():
        if (
            not isinstance(spec, dict)
            or set(spec) - {"ensure", "opposite"}
            or not isinstance(spec.get("ensure", []), list)
            or not isinstance(spec.get("opposite", ""), str)
        ):
            raise ManifestError(
                f"{where}: union {key!r} is {{ensure: [ids], opposite?: key}}"
            )
        union.append(Union((key,), tuple(spec.get("ensure", [])), spec.get("opposite")))
    lists = {}
    for name in ("forbidden", "never"):
        found = raw.get(name, [])
        if not isinstance(found, list):
            raise ManifestError(f"{where}: {name} is a list of key paths")
        lists[name] = tuple(_key_path(p, f"{where}: {name}") for p in found)
    out = Spec(tuple(_leaves(sets)), tuple(union), lists["forbidden"], lists["never"])
    touched: list[KeyPath | Prefix] = [p for p, _ in out.set]
    touched += [u.path for u in out.union]
    touched += [(*u.path[:-1], u.opposite) for u in out.union if u.opposite]
    touched += list(out.forbidden)
    for path in touched:
        for never in out.never:
            if _clash(path, never):
                shown = path if isinstance(path, Prefix) else _show(path)
                raise ManifestError(f"{where}: {shown} is a key sync must never touch")
    return out


def _get(doc: Any, path: KeyPath) -> Any:
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _has(doc: Any, path: KeyPath) -> bool:
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    return True


def _forbidden_paths(doc: Any, spec: Spec) -> list[KeyPath]:
    out: list[KeyPath] = []
    for f in spec.forbidden:
        if isinstance(f, Prefix):
            parent = _get(doc, f.parent) if f.parent else doc
            if isinstance(parent, dict):
                out += [(*f.parent, k) for k in parent if k.startswith(f.prefix)]
        elif _has(doc, f):
            out.append(f)
    return out


def _model(doc: Any, spec: Spec, where: str) -> Any:
    """``doc`` with the spec applied, as data."""
    out = copy.deepcopy(doc)
    for path, v in spec.set:
        cur = out
        for depth, key in enumerate(path[:-1]):
            nxt = cur.get(key)
            if nxt is None:
                nxt = cur[key] = {}
            elif not isinstance(nxt, dict):
                raise ConfigError(
                    f"{where}: {_show(path[: depth + 1])} is not an object, so"
                    f" {_show(path)} cannot be set"
                )
            cur = nxt
        cur[path[-1]] = copy.deepcopy(v)
    for u in spec.union:
        have = out.get(u.path[0])
        if have is None:
            have = out[u.path[0]] = []
        elif not isinstance(have, list):
            raise ConfigError(f"{where}: {_show(u.path)} is not an array")
        have += [i for i in dict.fromkeys(u.ensure) if i not in have]
        if u.opposite and isinstance(out.get(u.opposite), list):
            out[u.opposite] = [i for i in out[u.opposite] if i not in u.ensure]
    for path in _forbidden_paths(out, spec):
        parent = _get(out, path[:-1]) if len(path) > 1 else out
        parent.pop(path[-1], None)
    return out


def _id(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def problems(doc: Any, spec: Spec) -> list[str]:
    """What ``doc`` lacks, as ``--check`` reports it (parsed values only)."""
    out: list[str] = []
    for path, v in spec.set:
        if not _has(doc, path):
            out.append(f"{_show(path)}: missing")
        elif _get(doc, path) != v:
            out.append(f"{_show(path)}: differs")
    for u in spec.union:
        have = _get(doc, u.path)
        opposite = _get(doc, (*u.path[:-1], u.opposite)) if u.opposite else None
        for i in dict.fromkeys(u.ensure):
            if not isinstance(have, list) or i not in have:
                out.append(f"{_show(u.path)}: {_id(i)} missing")
            if isinstance(opposite, list) and i in opposite:
                out.append(f"{_show(u.path)}: {_id(i)} listed in {u.opposite}")
    out += [f"forbidden key {_show(p)}" for p in _forbidden_paths(doc, spec)]
    return out


def apply(text: str, spec: Spec, where: str) -> str:
    """``text`` edited to the spec: ``set``, then ``union``, then ``forbidden``. The
    result re-parses to the intended values (asserted)."""
    root = parse(text, where)
    if root.kind != "object":
        raise ConfigError(f"{where}: the top level is not an object")
    intended = _model(value(root, text), spec, where)
    unit = indent_unit(text)
    out = text
    for path, v in spec.set:
        now = loads(out, where)
        if not _has(now, path) or _get(now, path) != v:
            out = set_leaf(out, path, v, indent=unit)
    for u in spec.union:
        out = ensure_items(out, u.path, list(dict.fromkeys(u.ensure)), indent=unit)
        if u.opposite:
            out = remove_items(out, (*u.path[:-1], u.opposite), u.ensure)
    for path in _forbidden_paths(loads(out, where), spec):
        out = delete(out, path)
    got = loads(out, where)
    if got != intended:
        raise RuntimeError(f"{where}: the JSONC edit does not give the intended values")
    return out


# --- the handler ---------------------------------------------------------------------------


def load_spec(plan: Plan, entry: Entry, templates: Path | None) -> Spec:
    """The entry's merge spec, rendered against the plan's context."""
    where = f"manifest.d/{entry.fragment}: {entry.path}"
    text = rendering.render(
        plan.product,
        entry.template or "",
        plan.ctx,
        None,
        directory=template_dir(entry, templates),
    )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{where}: the spec does not render JSON: {e}") from e
    return parse_spec(raw, where)


class JsoncMergeHandler(BaseHandler):
    """``jsonc-merge``: the leaves a spec names in a JSONC file the checkout owns."""

    name = "jsonc-merge"
    strategies = ("jsonc-merge",)

    def render(
        self,
        plan: Plan,
        entry: Entry,
        path: str,
        current: str | None,
        templates: Path | None,
    ) -> list[Item]:
        spec = load_spec(plan, entry, templates)
        if current is None:
            wanted = jsonfmt.dumps(
                _model({}, spec, path), plan.product.json_style, jsonc=False
            )
            return [item(entry, path, None, wanted, "missing")]
        doc = loads(current, path)
        if not isinstance(doc, dict):
            raise ConfigError(f"{path}: the top level is not an object")
        found = problems(doc, spec)
        if not found:
            return [item(entry, path, current, current)]
        wanted = apply(current, spec, path)
        out = [item(entry, path, current, wanted, found[0], DRIFT)]
        out += [item(entry, path, current, current, p, DRIFT) for p in found[1:]]
        return out

    def retract(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """Every module off: each ``set`` leaf still holding the rendered value (with an
        object that leaves empty) and each ``ensure`` item go; a differing leaf is the
        checkout's (with a warning on the run
        that turns the module off). ``forbidden`` keys are not restored; the file stays."""
        if not engine.was_on(plan, entry):
            return None
        return self._remove(
            plan, entry, path, current, warn=engine.on_at_head(plan, entry)
        )

    def absent(self, plan: Plan, entry: Entry, path: str, current: str) -> Item | None:
        """The module on, its ``when`` false: as ``retract``."""
        if not engine.was_on(plan, entry):
            return None
        return self._remove(plan, entry, path, current, warn=False)

    def _remove(
        self, plan: Plan, entry: Entry, path: str, current: str, *, warn: bool
    ) -> Item | None:
        spec = load_spec(plan, entry, plan.templates)
        doc = loads(current, path)
        if not isinstance(doc, dict):
            raise ConfigError(f"{path}: the top level is not an object")
        module = ",".join(entry.modules)
        gone: list[str] = []
        text = current
        for key_path, v in spec.set:
            if not _has(doc, key_path):
                continue
            if _get(doc, key_path) == v:
                text = delete(text, key_path)
                # An object the removal leaves empty goes too, as in json-merge.
                for depth in range(len(key_path) - 1, 0, -1):
                    if _get(loads(text, path), key_path[:depth]) != {}:
                        break
                    text = delete(text, key_path[:depth])
                gone.append(f"{_show(key_path)}: goes with {module}")
            elif warn:
                plan.notices.append(
                    f"{path} {_show(key_path)} is the checkout's now that {module} is"
                    " off (it differs from what sync wrote)"
                )
        for u in spec.union:
            have = _get(loads(text, path), u.path)
            ids = [
                i
                for i in dict.fromkeys(u.ensure)
                if isinstance(have, list) and i in have
            ]
            if ids:
                text = remove_items(text, u.path, ids)
                gone += [f"{_show(u.path)}: {_id(i)} goes with {module}" for i in ids]
        if not gone:
            return None
        return item(entry, path, current, text, "; ".join(gone), DRIFT)
