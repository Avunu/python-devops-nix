"""``<cli> profile show|validate|list``: profiles, resolved, checked and listed.

``show`` prints the checkout's resolved configuration and its module switches;
``--explain`` adds, per value, the layer that set it (``builtin:recommended@1.0``,
``org:<source>@<rev>``, ``app`` or ``default``), which answers "why is this gate on".

``validate <profile.toml>`` checks an org profile on its own, before it is published:
its schema, its ``extends``, its version range, and that the product's managed files and
every template in its ``templates/`` render for each of the product's fixture
checkouts (``Product.fixtures``), each a throwaway git repository using the profile
in-repo.

``list`` prints ``minimal`` and every ``recommended@<minor>`` snapshot with its
description, marking the one plain ``recommended`` means.

Exit codes as sync's: 0, 2 (invalid) or 3 (environment).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
import tomlkit

from standards_core.commands import help_text
from standards_core.commands.config import add_source_arguments, resolve_args
from standards_core.common import config
from standards_core.common.report import CLEAN, ConfigError, EnvError
from standards_core.scaffold import engine, fixtures
from standards_core.scaffold import render as rendering

if TYPE_CHECKING:
    from standards_core.product import Product


def _plain(value: Any) -> Any:
    """``value`` without ``None`` (TOML has no null)."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_plain(v) for v in value if v is not None]
    return value


def _flat(value: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(value, dict) and value:
        out: dict[tuple[str, ...], Any] = {}
        for key, item in value.items():
            out.update(_flat(item, (*prefix, str(key))))
        return out
    return {prefix: value}


def _toml_key(path: tuple[str, ...]) -> str:
    return ".".join(tomlkit.key(part).as_string() for part in path)


def _toml_inline(value: Any) -> str:
    """``value`` on one line: tables inline, as TOML spells them."""
    if isinstance(value, dict):
        inner = ", ".join(
            f"{tomlkit.key(k).as_string()} = {_toml_inline(v)}"
            for k, v in value.items()
        )
        return "{ " + inner + " }" if inner else "{}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline(v) for v in value) + "]"
    return tomlkit.item(value).as_string()


def _explained(resolved: config.Resolved, fmt: str) -> str:
    """``show --explain``: what ``show`` prints, with the layer that set each value, in
    ``fmt``. TOML gives each ``[config]`` value as a dotted key with the layer as its
    comment (no ``None``: TOML has no null); JSON adds a ``sources`` table, keyed by the
    dotted name."""
    flat = _flat(resolved.cfg)
    if fmt == "json":
        doc = {
            "profile": resolved.profile,
            "modules": resolved.modules,
            "config": resolved.cfg,
            "sources": {".".join(k): resolved.source(".".join(k)) for k in flat},
        }
        return json.dumps(doc, indent=2) + "\n"
    lines = ["[profile]"]
    for key, value in _flat(dict(resolved.profile)).items():
        if value is not None:
            lines.append(f"{_toml_key(key)} = {_toml_inline(_plain(value))}")
    lines += ["", "[modules]"]
    for module, on in resolved.modules.items():
        lines.append(f"{tomlkit.key(module).as_string()} = {'true' if on else 'false'}")
    lines += ["", "[config]"]
    for key, value in flat.items():
        if value is not None:
            line = f"{_toml_key(key)} = {_toml_inline(_plain(value))}"
            lines.append(f"{line}  # {resolved.source('.'.join(key))}")
    return "\n".join(lines) + "\n"


def show(args: argparse.Namespace) -> int:
    resolved = resolve_args(args.product, args)
    if args.explain:
        sys.stdout.write(_explained(resolved, args.format))
        return CLEAN
    doc = {
        "profile": resolved.profile,
        "modules": resolved.modules,
        "config": resolved.cfg,
    }
    if args.format == "json":
        print(json.dumps(doc, indent=2, sort_keys=False))
    else:
        sys.stdout.write(tomlkit.dumps(_plain(doc)))
    return CLEAN


def validate(args: argparse.Namespace) -> int:
    product = args.product
    identity = product.identity
    path = Path(args.file)
    try:
        doc = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise EnvError(f"{path} does not exist") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    label = f"profile {path}"
    config.validate_profile(product, doc, label, org=True)
    config.builtin_profile(product, doc["extends"])
    config.check_requires(product, doc.get(identity.requires_key), label)
    templates = Path(args.templates) if args.templates else path.parent / "templates"
    options = {
        "force": False,
        "throwaway": True,
        **product.sync.options(argparse.Namespace()),
    }
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        profile = work / "profile"
        profile.mkdir()
        (profile / "profile.toml").write_text(path.read_text())
        if templates.is_symlink():
            raise ConfigError(
                f"{templates} is a symlink: a profile's templates are read only as"
                " regular files"
            )
        if templates.is_dir():
            # A link in it is refused here, as sync refuses it, rather than copied.
            rendering.template_files(templates)
            shutil.copytree(templates, profile / "templates", symlinks=True)
        checkouts = work / "checkouts"
        checkouts.mkdir()
        plain: engine.Plan | None = None
        for name, fixture in product.fixtures.items():
            if fixture.requires and plain is not None:
                # A fixture that needs modules the profile leaves off would be exit 2
                # by design.
                modules = plain.resolved.modules
                if not all(modules.get(m) for m in fixture.requires):
                    continue
            root = fixtures.throwaway(product, checkouts, profile, name, fixture)
            # Resolution checks the needs under the profile's own switches, and the
            # plan checks [[extra-files]], every override's place and every [[retire]].
            try:
                plan = engine.build(product, root, options=options)
            except ConfigError as e:
                raise ConfigError(f"the {name} checkout: {e}") from e
            plain = plain or plan
            directory = profile / "templates"
            files = rendering.template_files(directory) if directory.is_dir() else []
            for file in files:
                rel = file.relative_to(directory).as_posix()
                try:
                    rendering.render(product, rel, plan.ctx, None, directory=directory)
                except (jinja2.TemplateError, ConfigError) as e:
                    raise ConfigError(
                        f"templates/{rel} does not render for the {name} checkout: {e}"
                    ) from e
    print(f"{path}: valid (extends {doc['extends']})")
    return CLEAN


def list_profiles(args: argparse.Namespace) -> int:
    """Each built-in with its description, marking the one plain ``recommended`` means."""
    product = args.product
    names = config.builtin_names(product)
    try:
        newest = config.builtin_name(product, "recommended")
    except ConfigError:
        newest = None
    labels = {n: n + ("  (recommended)" if n == newest else "") for n in names}
    width = max(len(label) for label in labels.values())
    for name in names:
        description = config.builtin_profile(product, name)[1].get("description", "")
        print(f"{labels[name]:<{width}}  {description}".rstrip())
    return CLEAN


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    p = subparsers.add_parser(
        "profile",
        help="show the resolved profile, validate an org profile, list the built-ins",
        description=help_text(product, "profile", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(
        dest="profile_command", metavar="<show|validate|list>", required=True
    )
    s = sub.add_parser(
        "show",
        help=help_text(
            product, "profile show", "the checkout's resolved configuration and modules"
        ),
    )
    s.add_argument(
        "--explain",
        action="store_true",
        help=help_text(
            product, "profile show --explain", "name the layer that set each value"
        ),
    )
    s.add_argument(
        "--format",
        choices=("toml", "json"),
        default="toml",
        help=help_text(product, "profile show --format", None),
    )
    add_source_arguments(product, s, "profile show")
    s.set_defaults(func=show)
    v = sub.add_parser(
        "validate",
        help=help_text(
            product,
            "profile validate",
            "check an org profile before it is published",
        ),
    )
    v.add_argument(
        "file",
        metavar="profile.toml",
        help=help_text(product, "profile validate file", None),
    )
    v.add_argument(
        "--templates",
        help=help_text(
            product,
            "profile validate --templates",
            "its template directory (default: templates/ beside the file)",
        ),
    )
    v.set_defaults(func=validate)
    ls = sub.add_parser(
        "list", help=help_text(product, "profile list", "the built-in profiles")
    )
    ls.set_defaults(func=list_profiles)
