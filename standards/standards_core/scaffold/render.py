"""Rendering: the one fixed Jinja configuration, the headers, and the template helpers.

The environment is fixed: sandboxed, ``StrictUndefined``, ``trim_blocks``,
``lstrip_blocks`` and ``keep_trailing_newline``. Templates are read from the
``templates/`` directory of each data root, the product's first, or from an org
profile's ``templates/`` for an override or an ``[[extra-files]]`` template, with the
same configuration. Templates get:

- filters ``json`` / ``jsonc`` (in the product's ``json_style``), ``json_string`` (one
  quoted string), ``yaml_json`` (compact JSON in a YAML scalar), ``nix_string`` (a Nix
  string literal), ``nix_attr`` (a Nix attribute name), ``glob_to_regex`` and
  ``org_url`` (an org URL template filled in for the checkout, ``url_template``), plus
  the product's ``jinja_filters``;
- functions ``rev(url, floor)`` and ``pin(action, sha, version)`` (floors), bound to the
  file being rendered, plus the product's ``hooks.render_helpers(ctx, current)``.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
from jinja2.sandbox import ImmutableSandboxedEnvironment, SecurityError

from standards_core.common.report import ConfigError
from standards_core.product import header_kind
from standards_core.scaffold import floors, globs, jsonfmt
from standards_core.scaffold.globs import ManifestError

if TYPE_CHECKING:
    from standards_core.product import Product

# The filters every product's templates have; a product's own may not shadow them.
CORE_FILTERS = (
    "json",
    "jsonc",
    "json_string",
    "yaml_json",
    "nix_string",
    "nix_attr",
    "glob_to_regex",
    "org_url",
)
# The helpers every render has; a product's render_helpers may not shadow them.
CORE_HELPERS = ("rev", "pin")


def header(product: Product, kind: str, local: bool, note: str = "") -> str:
    """The managed header line for a ``kind`` of file (DESIGN §5.1); empty for ``none``."""
    found = header_kind(product, kind)
    if found is None:
        raise ManifestError(f"unknown header kind {kind!r}")
    if not found.leader:
        return ""
    markers = product.markers
    text = f"{markers.header} {markers.header_local if local else markers.header_whole}"
    if note:
        text += f" {note}"
    if found.closer:
        text += f" {found.closer}"
    return f"{found.leader} {text}\n"


def nix_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


_NIX_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*")
_NIX_KEYWORDS = {
    "assert",
    "else",
    "if",
    "in",
    "inherit",
    "let",
    "or",
    "rec",
    "then",
    "with",
}


def nix_attr(name: str) -> str:
    """``name`` as a Nix attribute name: bare when it is an identifier, else quoted (an
    input may be named ``my.unit`` or ``1st-unit``)."""
    if _NIX_IDENT.fullmatch(name) and name not in _NIX_KEYWORDS:
        return name
    return nix_string(name)


def yaml_json(value: Any) -> str:
    """``value`` as compact JSON in a single-quoted YAML scalar: a workflow input that a
    reusable workflow reads with ``fromJSON``."""
    return "'" + json.dumps(value, separators=(",", ":")).replace("'", "''") + "'"


def url_template(product: Product, template: str, ctx: Any) -> str:
    """An org URL template with the product's placeholders filled in from the context.

    ``hooks.url_placeholders(ctx)`` is an ordered sequence of ``(key, value)`` pairs,
    each ``{key}`` replaced in that order (so ``{name_hyphen}`` can go before
    ``{name}``).
    """
    out = template
    for key, value in product.hooks.url_placeholders(ctx):
        out = out.replace("{" + key + "}", value)
    return out


def _configure(product: Product, env: jinja2.Environment) -> jinja2.Environment:
    style = product.json_style

    @jinja2.pass_context
    def org_url(context: Any, template: str) -> str:
        return url_template(product, template, context)

    env.filters["json"] = lambda v: jsonfmt.dumps(v, style)
    env.filters["jsonc"] = lambda v: jsonfmt.dumps(v, style, jsonc=True)
    env.filters["json_string"] = lambda v: json.dumps(v, ensure_ascii=False)
    env.filters["yaml_json"] = yaml_json
    env.filters["nix_string"] = nix_string
    env.filters["nix_attr"] = nix_attr
    env.filters["glob_to_regex"] = globs.glob_to_regex
    env.filters["org_url"] = org_url
    shadowed = sorted(set(product.jinja_filters) & set(CORE_FILTERS))
    if shadowed:
        raise RuntimeError(
            f"jinja_filters shadows the core filter(s) {', '.join(shadowed)}"
        )
    env.filters.update(product.jinja_filters)
    return env


def _link_error(rel: str) -> ConfigError:
    return ConfigError(
        f"the profile's templates/{rel} is a symlink: a profile's templates are read only"
        " as regular files, never through a link (an in-repo profile is part of the pull"
        " request --check runs on)"
    )


def template_files(directory: Path) -> list[Path]:
    """Every file under a profile's ``templates/``, sorted; a symlink anywhere in it (a
    file or a directory, dangling or not) is exit 2, unread: ``--check`` renders an
    in-repo profile's templates on untrusted pull requests, and a link would print what
    it reaches."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        here = Path(dirpath)
        for name in [*dirnames, *filenames]:
            if (here / name).is_symlink():
                raise _link_error((here / name).relative_to(directory).as_posix())
        out += [here / name for name in filenames]
    return sorted(out)


class _NoLinkLoader(jinja2.FileSystemLoader):
    """A ``FileSystemLoader`` that refuses a template reached through a symlink (exit 2),
    so an ``include`` or an override can never read outside the profile's
    ``templates/``."""

    def get_source(
        self, environment: jinja2.Environment, template: str
    ) -> tuple[str, str, Callable[[], bool]]:
        pieces = jinja2.loaders.split_template_path(template)
        for searchpath in self.searchpath:
            base = Path(searchpath)
            if base.is_symlink():
                raise _link_error("")
            cur = base
            for i, piece in enumerate(pieces):
                cur = cur / piece
                if cur.is_symlink():
                    raise _link_error("/".join(pieces[: i + 1]))
        return super().get_source(environment, template)


def _environment(product: Product, loader: jinja2.BaseLoader) -> jinja2.Environment:
    # Sandboxed: an org profile's templates and [[extra-files]] paths are data a pull
    # request can change (an in-repo profile), and --check renders them on untrusted
    # PRs. The sandbox refuses the attribute walks (``cycler.__init__.__globals__``) that
    # reach Python, and the immutable one also keeps a template from changing the
    # context the next file renders with.
    return _configure(
        product,
        ImmutableSandboxedEnvironment(
            loader=loader,
            undefined=jinja2.StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
        ),
    )


@cache
def environment(product: Product) -> jinja2.Environment:
    """The product's templates' environment: each root's ``templates/``, product first."""
    dirs = [Path(root) / "templates" for root in product.roots]
    loaders: list[jinja2.BaseLoader] = [
        jinja2.FileSystemLoader(str(d)) for d in dirs if d.is_dir()
    ]
    return _environment(product, jinja2.ChoiceLoader(loaders))


@cache
def profile_environment(product: Product, directory: str) -> jinja2.Environment:
    """An org profile's ``templates/``, rendered with the same configuration; a template
    reached through a symlink is refused."""
    return _environment(product, _NoLinkLoader(directory))


def _refused(what: str, e: SecurityError) -> ConfigError:
    return ConfigError(
        f"{what}: the template reaches outside its data, which the sandbox refuses: {e}"
    )


def _broken(what: str, e: jinja2.TemplateError) -> ConfigError:
    return ConfigError(f"{what} does not render: {type(e).__name__}: {e}")


def render_string(product: Product, source: str, ctx: dict[str, Any]) -> str:
    """A one-line template, such as an entry's ``path`` (an org profile's
    ``[[extra-files]]`` path is profile data, so an error in it is a configuration
    error, exit 2)."""
    try:
        return environment(product).from_string(source).render(**ctx)
    except SecurityError as e:
        raise _refused(repr(source), e) from e
    except jinja2.TemplateError as e:
        raise _broken(repr(source), e) from e


def helpers(product: Product, ctx: dict[str, Any], current: str | None) -> dict:
    """The functions a render gets: ``rev`` and ``pin`` bound to ``current`` (the file
    on disk), then the product's ``render_helpers``, which may shadow neither them nor a
    context key."""
    out: dict[str, Any] = {
        "rev": lambda url, floor: floors.rev(current, url, floor),
        "pin": lambda action, sha, ver: floors.pin(current, action, sha, ver),
    }
    extra = dict(product.hooks.render_helpers(ctx, current))
    shadowed = sorted(set(extra) & (set(out) | set(ctx)))
    if shadowed:
        raise RuntimeError(
            f"render_helpers shadows {', '.join(shadowed)} (a core helper or a context"
            " key)"
        )
    out.update(extra)
    return out


def render(
    product: Product,
    template: str,
    ctx: dict[str, Any],
    current: str | None,
    *,
    directory: Path | None = None,
) -> str:
    """``template`` rendered for ``ctx``; ``current`` is the file on disk, which the
    floors read.

    ``directory`` is an org profile's ``templates/`` to render it from instead of the
    product's.
    """
    funcs = helpers(product, ctx, current)
    env = (
        profile_environment(product, str(directory))
        if directory is not None
        else environment(product)
    )
    try:
        return env.get_template(template).render(**ctx, **funcs)
    except SecurityError as e:
        raise _refused(f"templates/{template}", e) from e
    except jinja2.TemplateError as e:
        if directory is None:
            raise  # a product template is the product's own: a bug, exit 3
        # An org profile's template: an undefined name or a syntax error is the
        # profile's, exit 2 like any other profile error.
        raise _broken(f"the org profile's templates/{template}", e) from e


# What a ``when`` may call, and how each operator of one compares.
_CALLS = {"len": len, "any": any, "all": all}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


def _evaluate(node: ast.expr, ctx: dict[str, Any], expr: str) -> Any:
    def ev(n: ast.expr) -> Any:
        return _evaluate(n, ctx, expr)

    def fail(why: str) -> ConfigError:
        return ConfigError(f"when {expr!r}: {why}")

    if isinstance(node, ast.Constant) and isinstance(
        node.value, str | int | float | bool | type(None)
    ):
        return node.value
    if isinstance(node, ast.Name):
        if node.id.startswith("_") or node.id not in ctx:
            raise fail(f"{node.id!r} is not in the context")
        return ctx[node.id]
    if isinstance(node, ast.Attribute | ast.Subscript):
        base = ev(node.value)
        key = node.attr if isinstance(node, ast.Attribute) else ev(node.slice)
        if isinstance(key, str) and key.startswith("_"):
            raise fail(f"{key!r}: names starting with _ are not readable")
        if isinstance(node, ast.Attribute) and not isinstance(base, dict):
            raise fail(f".{key} of a value that is not a table")
        if not isinstance(base, dict | list | tuple | str) or isinstance(key, bool):
            raise fail(f"[{key!r}] of a value that has no items")
        try:
            return base[key]
        except (KeyError, IndexError, TypeError) as e:
            raise fail(f"no {key!r} there") from e
    if isinstance(node, ast.BoolOp):
        value: Any = None
        for operand in node.values:
            value = ev(operand)
            if isinstance(node.op, ast.And) and not value:
                return value
            if isinstance(node.op, ast.Or) and value:
                return value
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not ev(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = ev(node.operand)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise fail("unary minus needs a number")
        return -value
    if isinstance(node, ast.Compare):
        left = ev(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = ev(comparator)
            try:
                if not _COMPARE[type(op)](left, right):
                    return False
            except TypeError as e:
                raise fail(str(e)) from e
            left = right
        return True
    if isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _CALLS
            or node.keywords
        ):
            raise fail(f"only {', '.join(_CALLS)} may be called")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise fail("no * arguments")
        try:
            return _CALLS[node.func.id](*(ev(a) for a in node.args))
        except TypeError as e:
            raise fail(str(e)) from e
    if isinstance(node, ast.List | ast.Tuple):
        return [ev(e) for e in node.elts]
    raise fail(f"{type(node).__name__} is not allowed (see DESIGN §4.3 `when`)")


def evaluate(expr: str, ctx: dict[str, Any]) -> bool:
    """A ``when`` (a manifest entry's, a retire rule's, an ``[[extra-files]]`` one): a
    Python expression over the context, read by a small evaluator rather than ``eval``.

    An org profile's ``when`` is data a pull request can change (an in-repo profile),
    and ``--check`` evaluates it on untrusted PRs; restricting ``__builtins__`` is no
    sandbox. So only these are read: names of the context, ``.key`` and ``[key]`` on its
    values (never a name starting with ``_``), constants, lists and tuples,
    ``and``/``or``/``not``, unary minus, comparisons (``==``, ``!=``, ``<``, ``<=``,
    ``>``, ``>=``, ``in``, ``not in``, ``is``, ``is not``) and calls of ``len``, ``any``
    and ``all``. Anything else is exit 2."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ConfigError(f"when {expr!r} is not an expression: {e.msg}") from e
    return bool(_evaluate(tree.body, ctx, expr))
