"""``<cli> sync --write|--check``: the managed files of the checkout in the current directory.

Only a checkout that opted in is synced: one whose config file has a
``[tool.<table>]`` table. ``--standards <profile>`` creates the table; without either,
both modes exit 2 with the opt-in hint and write nothing. A directory whose config
file belongs to the checkout above it (a table kept in a subdirectory's file) is
refused the same way: run sync at that checkout's root.

``--write`` runs both phases: phase A writes the files the flake needs (the flake,
``.envrc``), locks the flake, and re-executes sync from the engine the lock pins when
that is another release; phase B renders every other managed file, retracts what a
module that turned off left, deletes what is retired, and brings the locks up. It
stages what it wrote with ``git add`` and commits nothing.

``--check`` computes the same plan in memory and runs nothing, so it works in a CI job
without Nix. Exit codes: 0 clean, 1 drift, 2 invalid configuration that sync can't fix,
3 environment (not a checkout, unreadable lock, version skew).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.commands import help_text
from standards_core.common import pyproject, repo, skew
from standards_core.common.report import (
    CLEAN,
    DRIFT,
    ENVIRONMENT,
    INVALID,
    ConfigError,
    EnvError,
    Finding,
    StandardsError,
    render,
    worst,
)
from standards_core.scaffold import bootstrap, defaults, engine, handlers, tomlmerge
from standards_core.scaffold.engine import Item

if TYPE_CHECKING:
    from standards_core.product import Product


def _only(args: argparse.Namespace) -> list[str] | None:
    paths = [
        p.strip() for chunk in (args.only or []) for p in chunk.split(",") if p.strip()
    ]
    return paths or None


def _options(product: Product, args: argparse.Namespace) -> dict:
    return {
        "force": bool(getattr(args, "force", False)),
        "throwaway": bool(getattr(args, "throwaway", False)),
        **product.sync.options(args),
    }


def _profile_dir(product: Product, args: argparse.Namespace) -> Path | None:
    if not getattr(args, "profile_path", None):
        return None
    path = Path(args.profile_path).resolve()
    print(
        f"{product.identity.name} sync: warning: reading the org profile from {path}"
        " (--profile-path), not its locked input; nothing records this, so commit only"
        " what the locked profile renders",
        file=sys.stderr,
    )
    return path


def passthrough(
    product: Product, args: argparse.Namespace, *, init: bool = False
) -> list[str]:
    """The flags a handed-over sync gets again; ``init`` spells them for the product's
    init app (``sync.reexec_argv``) rather than for ``<cli> sync``."""
    out = []
    if args.dry_run:
        out.append("--dry-run")
    if args.skip_lock:
        out.append("--skip-lock")
    if args.offline:
        out.append("--offline")
    out += product.sync.passthrough(args, init)
    if args.force:
        out.append("--force")
    if getattr(args, "throwaway", False):
        out.append("--throwaway")
    if args.standards:
        out += ["--standards", args.standards]
    if args.profile_path:
        out += ["--profile-path", str(Path(args.profile_path).resolve())]
    for chunk in args.only or []:
        out += ["--only", chunk]
    return out


def _build(
    product: Product,
    root: Path,
    args: argparse.Namespace,
    phases: tuple[str, ...] = ("a", "b"),
) -> engine.Plan:
    return engine.build(
        product,
        root,
        only=_only(args),
        options=_options(product, args),
        profile_dir=args.profile_dir,
        standards=getattr(args, "standards", None),
        phases=phases,
        engine_lock=getattr(args, "dry_run_lock", None),
        args=args,
    )


def _stderr(lines: list[str], fmt: str = "text") -> None:
    """Diagnostics on stderr. Under ``--format github`` they are pull-request data the
    runner would parse for workflow commands as well, so they go inside a
    ``::stop-commands::`` block of their own, closed before the report reaches
    stdout."""
    if not lines:
        return
    if fmt == "github":
        token = secrets.token_hex(16)
        lines = [f"::stop-commands::{token}", *lines, f"::{token}::"]
    for line in lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()


def _notices(product: Product, plan: engine.Plan, fmt: str = "text") -> None:
    name = product.identity.name
    _stderr([f"{name} sync: notice: {notice}" for notice in plan.notices], fmt)


def _report(
    product: Product,
    findings: list[Finding],
    fmt: str,
    code: int,
    plan: engine.Plan | None,
) -> str:
    rev = plan.ctx.engine.rev if plan else ""
    text = render(
        findings,
        fmt,
        code,
        {"rev": rev, "version": product.identity.version},
        product=product,
    )
    if fmt == "json" and plan is not None:
        doc = json.loads(text)
        doc["profile"] = dict(plan.resolved.profile)
        doc["modules"] = dict(plan.resolved.modules)
        text = json.dumps(doc, indent=2) + "\n"
    return text


# --- --check -----------------------------------------------------------------------


def check(product: Product, root: Path, args: argparse.Namespace) -> int:
    if args.profile_dir is not None and os.environ.get("CI"):
        raise EnvError(
            "--profile-path is for profile authors: --check in CI reads the locked"
            " profile"
        )
    plan = _build(product, root, args)
    _notices(product, plan, args.format)
    items = list(plan.problems)
    if plan.created_config is not None:
        items.insert(
            0,
            Item(
                engine.config_file(product, root),
                "toml-merge",
                None,
                None,
                f"[tool.{product.identity.table}] is missing: --standards creates it",
                DRIFT,
            ),
        )
    if not _only(args):
        items += engine.lock_problems(plan)
        items += engine.untracked_lock_problems(plan)
    items += skew.problems(product, plan, getattr(args, "expect_rev", ""))
    code = worst(*(i.code for i in items))
    findings = [i.finding() for i in items]
    sys.stdout.write(_report(product, findings, args.format, code, plan))
    return code


# An error message that starts with the file it is about: "x.json: …", ".editorconfig:12: …".
_ERROR_PATH = re.compile(r"^(?P<path>[^\s:;]+)(?::\d+)?: ")


def _error_path(product: Product, root: Path, e: Exception) -> str:
    """The file a raised error is about: the path its message starts with, when that
    names one in the checkout; else the config file for a configuration error and ``.``
    otherwise."""
    m = _ERROR_PATH.match(str(e))
    if (
        m
        and not Path(m["path"]).is_absolute()
        and ".." not in Path(m["path"]).parts
        and os.path.lexists(root / m["path"])
    ):
        return m["path"]
    if isinstance(e, ConfigError):
        return engine.config_file(product, root)
    return "."


def refuse_claimed(product: Product, root: Path) -> None:
    """Refuse (exit 2) a directory whose config file belongs to the checkout above it:
    from ``kit/`` of a checkout keeping its table in ``kit/pyproject.toml``, sync would
    otherwise take ``kit/`` for a checkout of its own and render a second tree there
    (DESIGN §6.2, §15.CR1)."""
    owner = pyproject.claimed_by(product, root)
    if owner is None:
        return
    rel = pyproject.config_rel(product, owner)
    raise ConfigError(
        f"{rel} belongs to the checkout at {owner}: run"
        f" {product.identity.sync_command} there"
    )


def check_reported(product: Product, root: Path, args: argparse.Namespace) -> int:
    """``--check``, where an error that stops the plan (not opted in, not a repository, a
    schema error, a malformed local region, a crash) is still a report in ``json`` and
    ``github``: the fleet audit and the CI annotations always get a document."""
    if args.format == "text":
        repo.toplevel(root)
        refuse_claimed(product, root)
        return check(product, root, args)
    name = product.identity.name
    try:
        repo.toplevel(root)
        refuse_claimed(product, root)
        return check(product, root, args)
    except StandardsError as e:
        code, problem, path = e.code, str(e), _error_path(product, root, e)
    except Exception as e:  # noqa: BLE001 — the CLI's internal-error path, with a report
        if os.environ.get(bootstrap.env_name(product, "DEBUG")):
            traceback.print_exc()
        code, problem, path = (
            ENVIRONMENT,
            f"internal error: {type(e).__name__}: {e}",
            ".",
        )
    _stderr([f"{name} sync: {problem}"], args.format)
    rev = ""
    with contextlib.suppress(Exception):
        rev = engine.locked_rev(product, root) or ""
    sys.stdout.write(
        render(
            [Finding(path, "", problem)],
            args.format,
            code,
            {"rev": rev, "version": product.identity.version},
            product=product,
        )
    )
    return code


# --- --write -----------------------------------------------------------------------


def _apply(
    product: Product, root: Path, items: list[Item], runner: bootstrap.Runner
) -> list[str]:
    """Write or delete every item whose content changes; returns the paths touched."""
    name = product.identity.name
    touched = []
    for item in items:
        if item.code != DRIFT or item.command or item.wanted == item.current:
            continue
        # No link on the way (a retired link itself is deleted, never followed).
        target = engine.inside(product, root, item.path, link_ok=True)
        if runner.dry_run:
            print(item.finding().path + f" ({item.strategy}): {item.problem}")
            print(item.diff(), end="")
        elif item.wanted is None:
            target.unlink(missing_ok=True)
            print(f"{name} sync: - {item.path} ({item.problem})", file=sys.stderr)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()  # write a regular file, never through a link
            target.write_text(item.wanted)
            print(f"{name} sync: + {item.path}", file=sys.stderr)
        touched.append(item.path)
    return touched


def _unstaged(root: Path) -> set[str]:
    """The tracked paths with changes not staged when the run starts: the user's own
    edits, which sync writes over but never stages for them."""
    out = repo.git(root, "diff", "--name-only", "-z")
    return {p for p in out.split("\0") if p}


def _stage(root: Path, paths: list[str], runner: bootstrap.Runner) -> None:
    if runner.dry_run or not paths:
        return
    kept = {p for p in paths if p in runner.unstaged}
    runner.left_unstaged |= kept
    paths = sorted(set(paths) - kept)
    if paths:
        repo.git(root, "add", "-A", "--", *paths)


def _report_unstaged(product: Product, runner: bootstrap.Runner) -> None:
    for path in sorted(runner.left_unstaged):
        print(
            f"{product.identity.name} sync: {path}: left unstaged: it had changes of"
            " yours",
            file=sys.stderr,
        )


def _invalid(product: Product, items: list[Item]) -> int:
    bad = [i for i in items if i.code >= INVALID]
    for i in bad:
        print(f"{product.identity.name} sync: {i.path}: {i.problem}", file=sys.stderr)
    return worst(*(i.code for i in bad))


def _create_table(
    product: Product, root: Path, plan: engine.Plan, runner: bootstrap.Runner
) -> None:
    """Write the ``[tool.<table>]`` table ``--standards`` created (and the config file
    itself when it is missing and optional)."""
    if plan.created_config is None:
        return
    identity = product.identity
    path = engine.config_file(product, root)
    text = engine.read(product, root, path) or ""
    new = tomlmerge.add_table(product, text, plan.created_config)
    if runner.dry_run:
        print(f"{path} (toml-merge): [tool.{identity.table}] is created")
        return
    target = engine.inside(product, root, path)
    if not target.parent.is_dir():
        # The file is created only where its directory exists: a product whose table
        # lives in a generated directory makes that directory first.
        raise ConfigError(
            f"{path} cannot be created: {target.parent.relative_to(root)}/ does not"
            " exist"
        )
    target.write_text(new)
    print(f"{identity.name} sync: + {path} [tool.{identity.table}]", file=sys.stderr)
    _stage(root, [path], runner)


def _flake_step(plan: engine.Plan, args: argparse.Namespace) -> bool:
    """Whether phase A locks the flake (and checks the release branch, and hands over):
    always, unless ``--only`` names none of the phase-``a`` files. ``[tool.<table>]`` is
    created either way, since every other file renders from it."""
    only = _only(args)
    if only is None:
        return True
    return bool({"flake.nix", *(i.path for i in plan.items)} & set(only))


def _lock(product: Product, root: Path, runner: bootstrap.Runner, url: str) -> bool:
    """Lock the flake the phase-``a`` files make and stage the lock; returns whether
    ``flake.lock`` changed."""
    text = engine.read(product, root, "flake.nix")
    if text is None:
        return False
    changed = bootstrap.phase_a_lock(runner, engine.flake_input_specs(text), url)
    if (root / "flake.lock").is_file():
        _stage(root, ["flake.lock"], runner)
    return changed


def phase_a(
    product: Product, root: Path, args: argparse.Namespace, runner: bootstrap.Runner
) -> bool:
    """Phase A (DESIGN §6.3 steps 1 to 8). Returns whether ``flake.lock`` changed.

    Under ``--offline`` or ``--skip-lock`` it runs steps 1, 2, 4 and 7 only: no
    release-branch check, no lock, no second build against a newly locked profile and
    no re-exec. ``--only`` limits it too: the flake is locked and handed over only when
    ``--only`` is not given or names a phase-``a`` file."""
    plan = _build(product, root, args, phases=("a",))
    code = _invalid(product, plan.items)
    if code:
        raise SystemExit(code)
    flake_step = _flake_step(plan, args)
    locking = flake_step and not args.offline and not args.skip_lock
    product.sync.phase_a_before(plan, runner, args)
    if locking:
        bootstrap.require_release_branch(runner, plan.ctx.engine.url)
    _create_table(product, root, plan, runner)
    touched = _apply(product, root, plan.items, runner)
    # A flake sees only tracked files: stage them before `nix flake lock` reads the flake.
    _stage(root, touched, runner)
    changed = False
    if locking and not runner.dry_run:
        changed = _lock(product, root, runner, plan.ctx.engine.url)
        # An org profile read only now that it is locked: render the phase-a files again
        # from it (its modules can change the flake), then lock once more.
        again = _build(product, root, args, phases=("a",))
        code = _invalid(product, again.items)
        if code:
            raise SystemExit(code)
        redone = _apply(product, root, again.items, runner)
        if redone:
            _stage(root, redone, runner)
            changed = _lock(product, root, runner, again.ctx.engine.url) or changed
        plan = again
    elif locking:
        print(
            "would run: nix flake lock (when flake.lock is missing or stale, or"
            f" {product.engine.flake_input} is not locked from flake.nix's URL)"
        )
        # Phase B of a dry run renders against the rev that lock would take.
        node = bootstrap.dry_run_lock(product, root, plan.ctx.engine.url)
        if node is not None:
            args.dry_run_lock = node
    product.sync.phase_a_after(plan, runner, args, changed)
    if locking and not runner.dry_run:
        bootstrap.maybe_reexec(
            runner,
            passthrough(product, args, init=True),
            changed or bootstrap.inherited_lock_change(product),
        )
    return changed


def _repo_settings(ns: dict | None) -> list[str]:
    """The modules of ``ns`` (a plan's context or ``previous``) on that need a
    repository setting: ``releases``, and ``dependabot`` with ``auto-merge``."""
    if ns is None:
        return []
    modules, cfg = ns["modules"], ns["cfg"]
    out = ["releases"] if modules.get("releases") else []
    if modules.get("dependabot") and (cfg.get("dependabot") or {}).get(
        "auto-merge", True
    ):
        out.append("dependabot (auto-merge)")
    return out


def needs_repo_settings(plan: engine.Plan) -> list[str]:
    """What this sync turns on that needs a repository setting: on now, and off at
    ``HEAD`` (or no table there yet)."""
    before = set(_repo_settings(plan.ctx.get("previous")))
    return [m for m in _repo_settings(plan.ctx) if m not in before]


def lock_steps(
    product: Product,
    plan: engine.Plan,
    runner: bootstrap.Runner,
    touched: list[str],
) -> list[str]:
    """Run each lock command a handler names (``lock_argv``) for an item whose lock is
    missing or short (``item.command``) or whose ``relock_when`` path was written; stage
    each lock. Returns the locks run."""
    out: list[str] = []
    for item in plan.items:
        entry = item.entry
        if entry is None or item.path in out:
            continue
        if item.command is None and not any(p in touched for p in entry.relock_when):
            continue
        argv = handlers.for_entry(product, entry).lock_argv(plan, item, touched)
        if argv is None:
            continue
        runner.run(argv)
        out.append(item.path)
        if (plan.root / item.path).is_file():
            _stage(plan.root, [item.path], runner)  # now, so a later failure keeps it
    return out


def phase_b(
    product: Product,
    root: Path,
    args: argparse.Namespace,
    runner: bootstrap.Runner,
    lock_changed: bool,
) -> int:
    """Phase B (DESIGN §6.3 steps 1 to 9)."""
    identity = product.identity
    lock_changed = lock_changed or bootstrap.inherited_lock_change(product)
    bootstrap.ensure_tools(runner, passthrough(product, args), lock_changed)
    only = _only(args)
    plan = _build(product, root, args, phases=("b",))
    _notices(product, plan)
    defaults.warn_changed(product, root, plan)
    code = _invalid(product, plan.items)
    if code:
        return code
    for module in needs_repo_settings(plan):
        print(
            f"{identity.name} sync: notice: {module} is on now and needs a repository"
            f" setting: run `{identity.cli} repo doctor`",
            file=sys.stderr,
        )
    touched = _apply(product, root, plan.items, runner)
    # Staged now as well as at the end, so a lock or formatter step that fails below
    # leaves what sync wrote staged rather than half-applied in the work tree.
    _stage(root, touched, runner)
    touched += lock_steps(product, plan, runner, touched)
    touched += product.sync.post_write(plan, runner, list(touched))
    touched += product.sync.phase_b_after(
        plan, runner, args, lock_changed, list(touched)
    )
    # A lock an earlier, failed run wrote is staged too: its step does not run again.
    untracked = [] if runner.dry_run or only else engine.untracked_locks(product, root)
    _stage(root, [p for p in touched if (root / p).exists()] + untracked, runner)

    _report_unstaged(product, runner)
    if runner.dry_run:
        return CLEAN
    # What sync could not fix: rules on checkout-owned keys, the product's whole-repo
    # checks, and (offline) the locks it did not run.
    after = _build(product, root, args)
    left = [i for i in after.problems if not (args.offline and i.command)]
    for i in left:
        print(f"{identity.name} sync: {i.path}: {i.problem}", file=sys.stderr)
    if args.offline:
        for i in after.problems:
            if i.command:
                print(
                    f"{identity.name} sync: {i.path}: {i.problem} (offline: run"
                    f" `{i.command}`)",
                    file=sys.stderr,
                )
    return worst(*(i.code for i in left))


def write(product: Product, root: Path, args: argparse.Namespace) -> int:
    runner = bootstrap.Runner(root, product, dry_run=args.dry_run, offline=args.offline)
    runner.unstaged = _unstaged(root)
    bootstrap.override_url(product)
    lock_changed = False
    if args.phase == "preflight":
        # Before a product's init app writes its own files: what phase A would refuse
        # (an invalid table, a --standards it contradicts, an unknown profile, a missing
        # release branch) is refused now, with nothing written.
        plan = _build(product, root, args, phases=("a",))
        code = _invalid(product, plan.items)
        if code:
            return code
        if _flake_step(plan, args) and not args.skip_lock:
            bootstrap.require_release_branch(runner, plan.ctx.engine.url)
        return CLEAN
    if args.phase in ("all", "a"):
        lock_changed = phase_a(product, root, args, runner)
    if args.phase == "a":
        _report_unstaged(product, runner)
        return CLEAN
    return phase_b(product, root, args, runner, lock_changed)


def _run(args: argparse.Namespace) -> int:
    product = args.product
    root = Path.cwd()
    args.profile_dir = _profile_dir(product, args)
    if args.check:
        return check_reported(product, root, args)
    repo.toplevel(root)
    refuse_claimed(product, root)
    if os.environ.get(bootstrap.env_name(product, "OFFLINE")) == "1":
        args.offline = True
    try:
        return write(product, root, args)
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code
        return CLEAN if e.code is None else ENVIRONMENT


def run(args: argparse.Namespace) -> int:
    product = args.product
    try:
        code = _run(args)
    except StandardsError as e:
        bootstrap.report_result(product, e.code)
        raise
    bootstrap.report_result(product, code)
    return code


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    identity = product.identity
    table = f"[tool.{identity.table}]"
    p = subparsers.add_parser(
        "sync",
        help=f"render, write or check the {identity.name} managed files of the"
        " checkout in the current directory",
        description=help_text(product, "sync", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    extra = product.sync.add_arguments

    def flag(*names: str, **kwargs) -> None:
        kwargs["help"] = help_text(product, f"sync {names[0]}", kwargs.get("help"))
        p.add_argument(*names, **kwargs)
        extra(p, names[0])

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write",
        action="store_true",
        help=help_text(
            product,
            "sync --write",
            f"write the managed files ({identity.sync_command})",
        ),
    )
    extra(p, "--write")
    mode.add_argument(
        "--check",
        action="store_true",
        help=help_text(product, "sync --check", "report drift and write nothing"),
    )
    extra(p, "--check")
    flag(
        "--standards",
        metavar="PROFILE",
        help=f"--write: opt in: create {table} with this profile (minimal, recommended,"
        " recommended@<minor>, github:<owner>/<repo>[/<ref>], ./<dir>)",
    )
    flag(
        "--profile-path",
        metavar="DIR",
        help="read the org profile from this directory instead of its locked input"
        " (profile authors only)",
    )
    flag(
        "--force",
        action="store_true",
        help="--write: replace an unmanaged file sync would replace on first opt-in"
        " (review the diff first)",
    )
    flag(
        "--dry-run",
        action="store_true",
        help="--write: print the diff and the commands, write nothing",
    )
    flag(
        "--skip-lock",
        action="store_true",
        help="--write: run none of the steps that lock the flake",
    )
    flag(
        "--offline",
        action="store_true",
        help="--write: run no nix, uv or npm command (also"
        f" {identity.env_prefix}_OFFLINE=1); the locks they make are reported",
    )
    flag(
        "--only",
        action="append",
        metavar="PATH[,PATH…]",
        help="limit to these managed paths",
    )
    flag(
        "--format",
        choices=("text", "json", "github"),
        default="text",
        help="--check: report format",
    )
    flag(
        "--expect-rev",
        default="",
        help=f"--check: the {product.engine.flake_input} revision this"
        f" {identity.distribution} was installed from",
    )
    p.add_argument(
        "--phase",
        choices=("all", "a", "b", "preflight"),
        default="all",
        help=argparse.SUPPRESS,
    )
    # A fixture checkout's sync (the conformance check): options.throwaway.
    p.add_argument("--throwaway", action="store_true", help=argparse.SUPPRESS)
    extra(p, "")
    p.set_defaults(func=run)
