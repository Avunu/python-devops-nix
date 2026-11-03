"""Path globs over ``git ls-files`` output.

One dialect everywhere sync matches a tracked path and turns a glob into a regular
expression for a tool (``glob_to_regex``): ``**`` is any run of characters including
``/``, ``*`` and ``?`` stay inside one path segment. A ``**/`` prefix or a ``/**/``
infix also matches no directory at all, so ``**/forms/**`` matches ``forms/x.js`` and
``a/**/b.js`` matches ``a/b.js``.

``match`` knows no braces. Product data may spell alternatives as ``a.{x,y}``;
``expand_braces`` turns such a pattern into its plain globs when the data is loaded.
"""

import itertools
import re
from functools import cache


class ManifestError(RuntimeError):
    """The product's packaged data is inconsistent: a product bug, never the checkout's."""


def _translate(glob: str) -> str:
    out = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@cache
def _compiled(glob: str) -> re.Pattern[str]:
    return re.compile(_translate(glob) + r"\Z")


def match(glob: str, path: str) -> bool:
    """Whether the tracked ``path`` (relative, ``/``-separated) matches ``glob``."""
    return _compiled(glob).match(path) is not None


def match_any(globs: list[str] | tuple[str, ...], path: str) -> bool:
    return any(match(g, path) for g in globs)


def select(globs: list[str] | tuple[str, ...], paths: list[str]) -> list[str]:
    """The paths matching any of ``globs``, in their order."""
    return [p for p in paths if match_any(globs, p)]


def expand_braces(pattern: str) -> list[str]:
    """``pattern`` with each ``{a,b,…}`` group expanded, in order: ``a.{x,y}`` → ``a.x``, ``a.y``.

    Several groups multiply (``{a,b}/{c,d}`` is four globs). A nested or unbalanced brace
    is a ``ManifestError``. A pattern without braces is returned alone.
    """
    pieces: list[list[str]] = []
    literal = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "}":
            raise ManifestError(f"glob {pattern!r}: '}}' at {i} closes no brace")
        if c != "{":
            literal.append(c)
            i += 1
            continue
        end = pattern.find("}", i + 1)
        if end < 0:
            raise ManifestError(f"glob {pattern!r}: the brace at {i} is never closed")
        body = pattern[i + 1 : end]
        if "{" in body:
            raise ManifestError(f"glob {pattern!r}: nested braces are not supported")
        pieces.append(["".join(literal)])
        pieces.append(body.split(","))
        literal = []
        i = end + 1
    pieces.append(["".join(literal)])
    return ["".join(parts) for parts in itertools.product(*pieces)]


def glob_to_regex(glob: str) -> str:
    """The fixed ``glob_to_regex`` Jinja filter: for prek's ``exclude`` regexes.

    ``**`` becomes ``.*``, ``*`` becomes ``[^/]*``, everything else is escaped, and the
    result is anchored at the end with ``$``. The start is the caller's: prek's global
    exclude puts every alternative after one ``^``.
    """
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "".join(out) + "$"
