"""Nix attribute sets, read with a lexer rather than by indentation (DESIGN §5.6.2).

``scan(text)`` lexes a Nix file (comments, both string kinds with their escapes and
interpolations, brackets) and records the outermost attribute set: its span and its
direct children (``name = …;``, ``name.url = …;``, ``"quoted".x = …;``), each with its
span and, when the value is itself an attribute set written ``{ … }``, that set's
children in turn. Nothing is evaluated: a child is the text from its attribute path to
its ``;``.

Strings follow Nix's lexer:

- ``"…"``: ``\\`` escapes the next character; ``$${`` is a literal ``${``; ``${`` opens an
  interpolation, which nests brackets and strings until its matching ``}``;
- ``''…''``: ``'''`` is a literal ``''``; ``''$`` a literal ``$`` (so ``''${X}`` is text);
  ``''\\`` escapes the next character; ``$${`` is a literal ``${``; ``${`` opens an
  interpolation; any other ``''`` ends the string.

The sentinel ``nix-attrset`` anchor (``scaffold.blocks``) follows a path of children
from the outermost set, and the flake helpers read a flake's inputs with it:
``flake_input_specs`` applies the input rule (DESIGN §5.6.4) to the direct children of
``inputs = { … };``, whatever the indentation. ``input_spec``, ``locked_spec`` and
``strip_nix_comments`` compare what a flake declares with what its lock holds.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from standards_core.common.report import ConfigError

# --- the lexer ---------------------------------------------------------------------------

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*")
_NUMBER = re.compile(r"[0-9][0-9.eE+-]*")
_OPENERS = {"{": "}", "${": "}", "[": "]", "(": ")"}
_CLOSERS = {"}", "]", ")"}
# Two-character operators, so `==` is never a binding's `=`.
_OPS2 = ("==", "!=", "<=", ">=", "&&", "||", "->", "//", "++")
# A keyword whose `;` is its own (`with pkgs; …`, `assert c; …`), not the binding's.
_SEMI_KEYWORDS = ("with", "assert")


@dataclass(frozen=True)
class Token:
    kind: str  # "id", "str" ("…"), "istr" (''…''), "num", or the punctuation itself
    start: int
    end: int
    # An identifier's text; a "…" string's raw content, between its quotes.
    text: str = ""
    # Whether a string holds an interpolation (its value is not static).
    dynamic: bool = False


def line_of(text: str, offset: int) -> int:
    """The 1-based line ``offset`` is on."""
    return text.count("\n", 0, offset) + 1


def _fail(path: str, text: str, offset: int, message: str) -> ConfigError:
    return ConfigError(f"{path}:{line_of(text, offset)}: {message}")


class _Lexer:
    def __init__(self, text: str, path: str) -> None:
        self.text = text
        self.path = path

    def fail(self, offset: int, message: str) -> ConfigError:
        return _fail(self.path, self.text, offset, message)

    def comment(self, i: int) -> int | None:
        """The offset after the comment at ``i``, or ``None`` when there is none."""
        text = self.text
        if text.startswith("#", i):
            end = text.find("\n", i)
            return len(text) if end < 0 else end
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise self.fail(i, "a /* comment is never closed")
            return end + 2
        return None

    def interpolation(self, i: int) -> int:
        """``i`` is just after ``${``: the offset after its matching ``}``."""
        depth = 1
        text = self.text
        while i < len(text):
            skipped = self.comment(i)
            if skipped is not None:
                i = skipped
                continue
            c = text[i]
            if c == '"':
                i, _ = self.string(i)
            elif text.startswith("''", i):
                i, _ = self.indented(i)
            elif text.startswith("${", i):
                depth += 1
                i += 2
            elif c == "{":
                depth += 1
                i += 1
            elif c == "}":
                depth -= 1
                i += 1
                if depth == 0:
                    return i
            else:
                i += 1
        raise self.fail(len(text), "a ${ interpolation is never closed")

    def string(self, start: int) -> tuple[int, bool]:
        """A ``"…"`` string at ``start``: the offset after it, and whether it
        interpolates."""
        text = self.text
        i = start + 1
        dynamic = False
        while i < len(text):
            c = text[i]
            if c == "\\" or text.startswith("$$", i):
                i += 2
            elif text.startswith("${", i):
                dynamic = True
                i = self.interpolation(i + 2)
            elif c == '"':
                return i + 1, dynamic
            else:
                i += 1
        raise self.fail(start, "a string is never closed")

    def indented(self, start: int) -> tuple[int, bool]:
        """An indented ``''…''`` string at ``start``: the offset after it, and whether
        it interpolates."""
        text = self.text
        i = start + 2
        dynamic = False
        while i < len(text):
            if text.startswith("'''", i) or text.startswith("''$", i):
                i += 3
            elif text.startswith("''\\", i):
                i += 4
            elif text.startswith("''", i):
                return i + 2, dynamic
            elif text.startswith("$$", i):
                i += 2
            elif text.startswith("${", i):
                dynamic = True
                i = self.interpolation(i + 2)
            else:
                i += 1
        raise self.fail(start, "an indented string is never closed")

    def tokens(self) -> list[Token]:
        text = self.text
        out: list[Token] = []
        i = 0
        while i < len(text):
            c = text[i]
            if c.isspace():
                i += 1
                continue
            skipped = self.comment(i)
            if skipped is not None:
                i = skipped
                continue
            if c == '"':
                end, dynamic = self.string(i)
                out.append(Token("str", i, end, text[i + 1 : end - 1], dynamic))
                i = end
            elif text.startswith("''", i):
                end, dynamic = self.indented(i)
                out.append(Token("istr", i, end, "", dynamic))
                i = end
            elif text.startswith("${", i):
                out.append(Token("${", i, i + 2))
                i += 2
            elif m := _IDENT.match(text, i):
                out.append(Token("id", i, m.end(), m[0]))
                i = m.end()
            elif m := _NUMBER.match(text, i):
                out.append(Token("num", i, m.end(), m[0]))
                i = m.end()
            elif text[i : i + 2] in _OPS2:
                out.append(Token(text[i : i + 2], i, i + 2))
                i += 2
            else:
                out.append(Token(c, i, i + 1))
                i += 1
        return out


def tokens(text: str, path: str = "flake.nix") -> list[Token]:
    """``text``'s tokens, comments and whitespace left out. An unclosed string or
    comment is exit 2 (``path:line``)."""
    return _Lexer(text, path).tokens()


# --- attribute sets ----------------------------------------------------------------------


@dataclass
class Child:
    """One binding of an attribute set: ``path = value;``."""

    path: tuple[str | None, ...]  # each component; None for a ${…} one
    start: int  # the first character of its attribute path
    end: int  # just after its `;`
    value_start: int
    value_end: int  # just after its value's last token
    # The value, when it is `{ … }` (or `rec { … }`).
    attrset: AttrSet | None = None
    # The value, when it is one static "…" string (its raw content).
    string: str | None = None

    @property
    def name(self) -> str | None:
        return self.path[0] if self.path else None


@dataclass
class AttrSet:
    start: int  # its `{`
    end: int  # its `}`
    children: list[Child] = field(default_factory=list)

    def child(self, name: str) -> AttrSet | None:
        """The attribute set bound to ``name`` (a one-component path) in this set."""
        for c in self.children:
            if c.path == (name,) and c.attrset is not None:
                return c.attrset
        return None


@dataclass
class Scan:
    text: str
    path: str
    root: AttrSet | None

    def find(self, steps: Sequence[str]) -> AttrSet | None:
        """The attribute set at ``steps`` from the outermost one."""
        cur = self.root
        for step in steps:
            if cur is None:
                return None
            cur = cur.child(step)
        return cur

    def line(self, offset: int) -> int:
        return line_of(self.text, offset)


def _matches(toks: list[Token], text: str, path: str) -> dict[int, int]:
    """Each opener's index → its closer's index; a mismatch is exit 2."""
    out: dict[int, int] = {}
    stack: list[int] = []
    for n, tok in enumerate(toks):
        if tok.kind in _OPENERS:
            stack.append(n)
        elif tok.kind in _CLOSERS:
            if not stack or _OPENERS[toks[stack[-1]].kind] != tok.kind:
                raise _fail(path, text, tok.start, f"an unbalanced {tok.kind!r}")
            out[stack.pop()] = n
    if stack:
        opener = toks[stack[-1]]
        raise _fail(path, text, opener.start, f"{opener.kind!r} is never closed")
    return out


class _Parser:
    def __init__(self, text: str, path: str) -> None:
        self.text = text
        self.toks = tokens(text, path)
        self.match = _matches(self.toks, text, path)

    def value_end(self, i: int, close: int) -> int:
        """The index of the `;` ending the value that starts at token ``i`` (or
        ``close`` when there is none)."""
        toks = self.toks
        lets = 0
        pending = 0
        while i < close:
            tok = toks[i]
            if tok.kind in _OPENERS:
                i = self.match[i] + 1
                continue
            if tok.kind == "id":
                if tok.text == "let":
                    lets += 1
                elif tok.text == "in" and lets:
                    lets -= 1
                elif tok.text in _SEMI_KEYWORDS:
                    pending += 1
            elif tok.kind == ";":
                if lets:
                    pass
                elif pending:
                    pending -= 1
                else:
                    return i
            i += 1
        return close

    def attrset(self, open_: int) -> AttrSet:
        toks = self.toks
        close = self.match[open_]
        out = AttrSet(toks[open_].start, toks[close].start)
        i = open_ + 1
        while i < close:
            tok = toks[i]
            if tok.kind == "id" and tok.text == "inherit":
                i = self.value_end(i + 1, close) + 1
                continue
            path: list[str | None] = []
            j = i
            ok = True
            while j < close and toks[j].kind != "=":
                t = toks[j]
                if t.kind == "id" or (t.kind == "str" and not t.dynamic):
                    path.append(t.text)
                    j += 1
                elif t.kind in ("${", "str", "istr"):
                    path.append(None)
                    j = self.match[j] + 1 if t.kind == "${" else j + 1
                else:
                    ok = False
                    break
                if j < close and toks[j].kind == ".":
                    j += 1
                elif j < close and toks[j].kind != "=":
                    ok = False
                    break
            if not ok or j >= close or not path:
                # Not a binding: a lambda's formals (`{ self, ... }`), or text this
                # reader does not follow. The set's children stop here.
                return out
            start = j + 1
            end = self.value_end(start, close)
            last = toks[end - 1] if end - 1 >= start else toks[j]
            child = Child(
                tuple(path),
                tok.start,
                toks[end].end if end < close else last.end,
                toks[start].start if start < close else toks[j].end,
                last.end,
            )
            first = start
            if first < end and toks[first].kind == "id" and toks[first].text == "rec":
                first += 1
            if first < end and toks[first].kind == "{" and self.match[first] == end - 1:
                child.attrset = self.attrset(first)
            elif (
                end - start == 1
                and toks[start].kind == "str"
                and not toks[start].dynamic
            ):
                child.string = toks[start].text
            out.children.append(child)
            i = end + 1
        return out

    def root(self) -> AttrSet | None:
        """The outermost attribute set: the first top-level ``{ … }`` that is not a
        function's formals (``{ pkgs }: …``)."""
        toks = self.toks
        i = 0
        while i < len(toks):
            if toks[i].kind == "{":
                close = self.match[i]
                after = toks[close + 1].kind if close + 1 < len(toks) else ""
                if after not in (":", "@"):
                    return self.attrset(i)
                i = close + 1
                continue
            if toks[i].kind in _OPENERS:
                i = self.match[i] + 1
                continue
            i += 1
        return None


def scan(text: str, path: str = "flake.nix") -> Scan:
    """``text``'s outermost attribute set and its children. A lexing error or an
    unbalanced bracket is exit 2 (``path:line``)."""
    parser = _Parser(text, path)
    return Scan(text, path, parser.root())


def bindings(body: str, path: str = "flake.nix") -> list[Child]:
    """The top-level bindings of ``body`` (a sequence of ``name = …;`` lines, such as a
    sentinel block's rendered body), with offsets into ``body``."""
    wrapped = "{\n" + body + "\n}"
    found = scan(wrapped, path).root
    if found is None:
        return []
    for child in _walk(found):
        child.start -= 2
        child.end -= 2
        child.value_start -= 2
        child.value_end -= 2
    return found.children


def _walk(attrset: AttrSet):
    for child in attrset.children:
        yield child
        if child.attrset is not None:
            child.attrset.start -= 2
            child.attrset.end -= 2
            yield from _walk(child.attrset)


# --- layout ------------------------------------------------------------------------------


def line_start(text: str, offset: int) -> int:
    return text.rfind("\n", 0, offset) + 1


def leading(text: str, offset: int) -> str:
    """The indentation of the line ``offset`` is on."""
    start = line_start(text, offset)
    line = text[start:]
    return line[: len(line) - len(line.lstrip(" \t"))]


def indent_unit(text: str) -> str:
    """One indentation level of ``text``: a tab when its first indented line starts
    with one, else the smallest positive step between successive non-blank lines'
    indentation, else 2 spaces."""
    previous = 0
    step = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        width = len(line) - len(line.lstrip(" \t"))
        if width and line[0] == "\t":
            return "\t"
        if width > previous and (not step or width - previous < step):
            step = width - previous
        previous = width
    return " " * step if step else "  "


def child_indent(text: str, attrset: AttrSet) -> str:
    """Where a new child of ``attrset`` is indented: as its first child, else one level
    deeper than its opener's line."""
    if attrset.children:
        return leading(text, attrset.children[0].start)
    return leading(text, attrset.start) + indent_unit(text)


def inline(text: str, attrset: AttrSet) -> bool:
    """Whether ``attrset`` is written on one line (``inputs = { a.url = "…"; };``)."""
    return "\n" not in text[attrset.start : attrset.end]


# --- flake inputs ------------------------------------------------------------------------


def strip_nix_comments(text: str) -> str:
    """``text`` without its ``#`` comments (string literals kept)."""
    return _NIX_COMMENT.sub(lambda m: m[0] if m[0].startswith('"') else "", text)


_NIX_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|#[^\n]*')


def input_spec(attr: str, value: str) -> str:
    """A flake input reference in one comparable form: ``follows:<a>/<b>``, or the URL
    with a forge's owner and repository lower-cased (Nix matches them
    case-insensitively)."""
    if attr == "follows":
        return f"follows:{value}"
    forge = re.fullmatch(
        r"(github|gitlab|sourcehut):([^/?]+)/([^/?]+)(?:/([^?]+))?", value
    )
    if not forge:
        return value
    kind, owner, repo_name, ref = forge.groups()
    return f"{kind}:{owner.lower()}/{repo_name.lower()}" + (f"/{ref}" if ref else "")


def locked_spec(lock: dict, ref: object) -> str | None:
    """What a root input of ``flake.lock`` was locked from, in ``input_spec``'s form;
    ``None`` when the lock records it in a form this can't compare."""
    if isinstance(ref, list):
        return "follows:" + "/".join(str(step) for step in ref)
    node = lock["nodes"].get(ref) if isinstance(ref, str) else None
    original = node.get("original") if isinstance(node, dict) else None
    if not isinstance(original, dict):
        return None
    kind = original.get("type")
    if kind == "git" and isinstance(original.get("url"), str):
        url = original["url"]
        if original.get("ref"):
            url += ("&" if "?" in url else "?") + f"ref={original['ref']}"
        return "git+" + url if not url.startswith("git+") else url
    if kind not in ("github", "gitlab", "sourcehut"):
        return None
    tail = original.get("ref") or original.get("rev")
    return input_spec(
        "url",
        f"{kind}:{original.get('owner')}/{original.get('repo')}"
        + (f"/{tail}" if tail else ""),
    )


_REFS = ("url", "follows")


def child_spec(child: Child) -> tuple[str | None, bool]:
    """``(spec, is_input)`` of one direct child of ``inputs``, by the input rule:
    ``name.url``/``name.follows`` with a string give their spec; ``name = { … }`` gives
    its ``url``/``follows`` (the last one), else ``None``; anything else adds nothing."""
    if len(child.path) == 2 and child.path[1] in _REFS:
        if child.string is None:
            return None, False
        return input_spec(str(child.path[1]), child.string), True
    if len(child.path) == 1 and child.attrset is not None:
        spec = None
        for inner in child.attrset.children:
            if (
                len(inner.path) == 1
                and inner.path[0] in _REFS
                and inner.string is not None
            ):
                spec = input_spec(str(inner.path[0]), inner.string)
        return spec, True
    return None, False


def inputs(found: Scan) -> AttrSet | None:
    """The flake's ``inputs = { … };`` set."""
    return found.find(["inputs"])


def flake_input_specs(text: str) -> dict[str, str | None]:
    """Each input a ``flake.nix`` declares (direct children of ``inputs = { … };``),
    with its ``url`` or ``follows:<path>`` normalised by ``input_spec``; ``None`` when
    it shows neither. ``self`` is never an input (``self.submodules = true;``). A file
    the lexer cannot read declares none (the lock check has nothing to compare)."""
    try:
        found = scan(text)
    except ConfigError:
        return {}
    attrset = inputs(found)
    specs: dict[str, str | None] = {}
    for child in attrset.children if attrset is not None else []:
        name = child.name
        if name is None or name == "self":
            continue
        spec, is_input = child_spec(child)
        if not is_input:
            continue
        if spec is not None or name not in specs:
            specs[name] = spec
    return dict(sorted(specs.items()))
