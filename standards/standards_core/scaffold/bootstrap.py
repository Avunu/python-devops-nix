"""What ``sync --write`` runs around the plan: the two phases' external steps (DESIGN §6.3).

Phase A writes the phase-``a`` files (the flake, ``.envrc``), locks the flake, and
re-executes sync from the engine the lock pins when that is another release. Phase B
renders everything else, then brings the derived files up (the lock seeds, the
product's steps). Every external command goes through ``Runner``, which prints it, and
which ``--dry-run`` and ``--offline`` turn into a report of what would run.

A sync that hands over to another (the re-exec, or a dev-shell re-entry when phase B's
tools are missing) gets the child's exit code back through ``<PREFIX>_SYNC_RESULT``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from standards_core.common import flakelock
from standards_core.common.report import EnvError
from standards_core.scaffold import context, engine

if TYPE_CHECKING:
    from standards_core.product import Product


def env_name(product: Product, suffix: str) -> str:
    """``<env_prefix>_<suffix>``: every variable the core reads is spelled so."""
    return f"{product.identity.env_prefix}_{suffix}"


def report_result(product: Product, code: int) -> None:
    """In a handed-over sync: record its exit code for the sync that started it."""
    path = os.environ.get(env_name(product, "SYNC_RESULT"))
    if path:
        with contextlib.suppress(OSError):
            Path(path).write_text(f"{code}\n")


def lock_changed_env(product: Product, changed: bool) -> dict[str, str]:
    """Set on a handover when phase A changed ``flake.lock``, so the phase B that runs
    there still runs the steps that follow a lock change."""
    return {env_name(product, "SYNC_LOCK_CHANGED"): "1"} if changed else {}


def inherited_lock_change(product: Product) -> bool:
    """Whether the sync that handed over to this one changed ``flake.lock``."""
    return os.environ.get(env_name(product, "SYNC_LOCK_CHANGED")) == "1"


@dataclass
class Runner:
    root: Path
    product: Product = field(repr=False)
    dry_run: bool = False
    offline: bool = False
    ran: list[str] = field(default_factory=list)
    # Tracked paths with unstaged changes when the run started, and the ones of them
    # sync wrote and left unstaged.
    unstaged: set[str] = field(default_factory=set)
    left_unstaged: set[str] = field(default_factory=set)

    def say(self, text: str) -> None:
        print(text, file=sys.stderr)

    def run(
        self,
        argv: list[str],
        *,
        env: dict | None = None,
        check: bool = True,
        network: bool = True,
    ) -> int:
        name = self.product.identity.name
        shown = " ".join(argv)
        if self.dry_run or (self.offline and network):
            self.say(
                f"{name} sync: would run: {shown}"
                + (" (skipped: --offline)" if self.offline else "")
            )
            return 0
        self.say(f"{name} sync: $ {shown}")
        self.ran.append(shown)
        try:
            proc = subprocess.run(
                argv, cwd=self.root, env={**os.environ, **(env or {})}, check=False
            )
        except FileNotFoundError as e:
            raise EnvError(f"{argv[0]} is not on PATH (needed for: {shown})") from e
        if check and proc.returncode != 0:
            raise EnvError(f"`{shown}` exited {proc.returncode}")
        return proc.returncode


def override_url(product: Product) -> str | None:
    """``<PREFIX>_URL_OVERRIDE``: an engine's self-tests only, and only with skew
    allowed."""
    name = env_name(product, "URL_OVERRIDE")
    url = os.environ.get(name) or None
    skew = env_name(product, "ALLOW_SKEW")
    if url and os.environ.get(skew) != "1":
        raise EnvError(f"{name} is for the engine's self-tests and needs {skew}=1")
    return url


def nix_args(product: Product) -> list[str]:
    url = override_url(product)
    return ["--override-input", product.engine.flake_input, url] if url else []


# --- phase A: the lock -------------------------------------------------------------


def _engine_original(product: Product, lock: dict) -> str | None:
    """What the lock's engine node was locked from, in ``engine.input_spec``'s form."""
    ref = (
        lock["nodes"]
        .get(lock["root"], {})
        .get("inputs", {})
        .get(product.engine.flake_input)
    )
    return engine.locked_spec(lock, ref) if ref is not None else None


def lock_reasons(
    product: Product, root: Path, inputs: dict[str, str | None], url: str
) -> tuple[bool, bool]:
    """Whether ``flake.lock`` must be (re)locked, and whether the engine must move to
    ``url``.

    ``inputs`` is ``engine.flake_input_specs`` of the rendered ``flake.nix``. The lock is
    stale when its input set differs, or when an input is locked from another URL or
    ``follows`` than ``flake.nix`` now gives it (a changed ``profile`` re-points
    ``standards-profile``); ``nix flake lock`` relocks those. The engine moves (``nix
    flake update <engine>``) only when its node's ``original`` is not ``url`` (the
    release branch, or the ``engine.url_key`` value), so a pinned tag, rev, fork or
    mirror is never undone."""
    path = root / "flake.lock"
    if not path.is_file():
        return True, False
    try:
        lock = flakelock.load(path)
    except EnvError:
        return True, False
    flake_input = product.engine.flake_input
    have = set(lock["nodes"].get(lock["root"], {}).get("inputs", {}))
    relock = have != set(inputs) or bool(
        engine.stale_inputs(lock, inputs, skip=(flake_input,))
    )
    original = _engine_original(product, lock)
    return relock, original is not None and original != engine.input_spec("url", url)


def phase_a_lock(runner: Runner, inputs: dict[str, str | None], url: str) -> bool:
    """Lock the flake when needed (DESIGN §6.3 step 5). Returns whether ``flake.lock``
    changed."""
    product = runner.product
    path = runner.root / "flake.lock"
    before = path.read_bytes() if path.is_file() else None
    relock, move = lock_reasons(product, runner.root, inputs, url)
    if override_url(product):
        # An engine's self-tests lock the checkout under test in place of its release.
        runner.run(["nix", "flake", "lock", *nix_args(product)])
    else:
        if relock:
            runner.run(["nix", "flake", "lock"])
        if move or (relock and lock_reasons(product, runner.root, inputs, url)[1]):
            runner.run(["nix", "flake", "update", product.engine.flake_input])
    after = path.read_bytes() if path.is_file() else None
    return before != after


# --- phase A: the engine's release branch ---------------------------------------------


def engine_repo_url(product: Product) -> str:
    """The engine repository, which ``git ls-remote`` asks for its release branch."""
    return f"https://github.com/{product.engine.repo}"


def release_branch(product: Product) -> str:
    """The engine branch the default flake URL follows: ``release-<major>``."""
    major = context.engine_major(product.identity.version)
    return product.engine.release_branch.format(major=major)


def release_ref_needed(product: Product, root: Path, url: str) -> bool:
    """Whether phase A's lock would fetch the engine's release branch: the flake follows
    the default URL, and there is no ``flake.lock`` or its engine node was locked from
    something else."""
    if url != context.default_engine_url(product):
        return False
    path = root / "flake.lock"
    if not path.is_file():
        return True
    try:
        lock = flakelock.load(path)
    except EnvError:
        return True
    return _engine_original(product, lock) != engine.input_spec("url", url)


def _ls_remote(
    product: Product, root: Path, branch: str, *flags: str
) -> subprocess.CompletedProcess:
    """``git ls-remote <flags> --heads <engine repository> <branch>``."""
    return subprocess.run(
        ["git", "ls-remote", *flags, "--heads", engine_repo_url(product), branch],
        cwd=root,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def release_branch_exists(product: Product, root: Path, branch: str) -> bool | None:
    """Whether the engine repository has ``branch``: ``None`` when ``git ls-remote``
    can't tell (no git, no network); only its "no matching ref" exit (2) is a no."""
    try:
        proc = _ls_remote(product, root, branch, "--exit-code")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return {0: True, 2: False}.get(proc.returncode)


def release_branch_rev(product: Product, root: Path, branch: str) -> str | None:
    """The commit the engine's ``branch`` points at (``git ls-remote``); ``None`` when
    there is no such branch or ``git`` can't tell."""
    try:
        proc = _ls_remote(product, root, branch)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        sha, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}" and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return None


def require_release_branch(runner: Runner, url: str) -> None:
    """Refuse, before anything is written, when the release branch the rendered flake
    follows does not exist: it is created when the engine's ``v<major>.0.0`` is tagged,
    and ``nix flake lock`` would otherwise fail halfway through. A network failure is
    left to the lock step to report. An ``engine.url_key`` value is the checkout's own
    choice and is not checked, nor is a lock already on the branch."""
    product = runner.product
    if (
        runner.dry_run
        or runner.offline
        or override_url(product)
        or not release_ref_needed(product, runner.root, url)
    ):
        return
    branch = release_branch(product)
    if release_branch_exists(product, runner.root, branch) is False:
        major = context.engine_major(product.identity.version)
        raise EnvError(
            f"{engine_repo_url(product)} has no {branch} branch yet: the checkout's"
            f" flake follows it, and it is created when {product.identity.name}"
            f" v{major}.0.0 is tagged. Nothing was written; sync works from that"
            " release on."
        )


def dry_run_lock(product: Product, root: Path, url: str) -> dict | None:
    """The engine node a dry run renders phase B against: the release branch's head,
    when phase A's lock would take it (``None`` otherwise, or when it can't tell)."""
    if override_url(product) or not release_ref_needed(product, root, url):
        return None
    rev = release_branch_rev(product, root, release_branch(product))
    if rev is None:
        return None
    owner, _, name = product.engine.repo.partition("/")
    return {"rev": rev, "owner": owner, "repo": name}


# --- handovers -------------------------------------------------------------------------


def handover(runner: Runner, argv: list[str], env: dict[str, str]) -> NoReturn:
    """Run a sync somewhere else (a re-exec, the phase-B re-entry) and exit with its code.

    Nix exits 1 on any evaluation, fetch or build failure, and 1 means drift, so the
    child's own code comes back through ``<PREFIX>_SYNC_RESULT``; when the child never
    got to write it, a non-zero exit is ``nix``'s and is an environment error (3)."""
    product = runner.product
    fd, path = tempfile.mkstemp(prefix=f"{product.identity.name}-sync-result-")
    os.close(fd)
    try:
        code = runner.run(
            argv, env={**env, env_name(product, "SYNC_RESULT"): path}, check=False
        )
        recorded = Path(path).read_text().strip()
    finally:
        Path(path).unlink(missing_ok=True)
    if recorded.isdigit():
        raise SystemExit(int(recorded))
    if code == 0:
        raise SystemExit(0)
    raise EnvError(
        f"`{' '.join(argv)}` exited {code} before the sync it runs reported a result"
    )


def reentry_argv(product: Product, argv: list[str]) -> list[str]:
    """The command that runs phase B inside the checkout's dev shell."""
    if product.sync.reentry_argv is not None:
        return product.sync.reentry_argv(argv)
    return [
        "nix",
        "develop",
        "--no-pure-eval",
        *nix_args(product),
        "-c",
        product.identity.cli,
        "sync",
        "--write",
        "--phase",
        "b",
        *argv,
    ]


def ensure_tools(runner: Runner, argv: list[str], lock_changed: bool = False) -> None:
    """Phase B needs ``sync.required_tools``; without one, re-enter through the
    checkout's dev shell (once). ``sync.before_reentry`` runs first."""
    product = runner.product
    if runner.offline or runner.dry_run:
        return
    missing = [t for t in product.sync.required_tools if not shutil.which(t)]
    if not missing:
        return
    if os.environ.get(env_name(product, "SYNC_REENTERED")) == "1":
        raise EnvError(f"{', '.join(missing)} still not on PATH inside the dev shell")
    product.sync.before_reentry(runner)
    handover(
        runner,
        reentry_argv(product, argv),
        {
            env_name(product, "CI"): "1",
            env_name(product, "SYNC_REENTERED"): "1",
            **lock_changed_env(product, lock_changed),
        },
    )


# --- phase A: the re-exec --------------------------------------------------------------


def locked_engine_version(runner: Runner) -> str | None:
    """The ``engine.version_file`` of the engine ``flake.lock`` pins, read from the Nix
    store."""
    product = runner.product
    if runner.dry_run or runner.offline:
        return None
    try:
        lock = flakelock.load(runner.root / "flake.lock")
    except EnvError:
        return None
    found = flakelock.node_at(lock, [product.engine.flake_input])
    locked = found[1].get("locked") if found else None
    if not isinstance(locked, dict) or locked.get("type") != "github":
        return None
    # The lock's attributes reach Nix as data (an environment variable the expression
    # reads), never spliced into the expression: a `${…}` in a crafted lock would be
    # evaluated. The version file's name is the product's, passed the same way.
    variable = env_name(product, "LOCKED_REV")
    expr = (
        "let locked = builtins.fromJSON (builtins.getEnv"
        f' "{variable}"); in builtins.readFile ((builtins.fetchTree locked.node)'
        ' + "/${locked.file}")'
    )
    data = {"node": locked, "file": product.engine.version_file}
    try:
        out = subprocess.run(
            ["nix", "eval", "--raw", "--impure", "--expr", expr],
            cwd=runner.root,
            env={**os.environ, variable: json.dumps(data)},
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def reexec_argv(product: Product, argv: list[str]) -> list[str]:
    """The command that runs sync again from the engine the lock pins."""
    if product.sync.reexec_argv is not None:
        return product.sync.reexec_argv(argv)
    init = product.engine.init_app
    return ["nix", "run", "--no-pure-eval", f".#{init}", "--", "--sync", *argv]


def maybe_reexec(runner: Runner, argv: list[str], lock_changed: bool = False) -> None:
    """Phase B renders with the product tools the lock pins, so a bootstrap from another
    release hands over to them (once, DESIGN §6.3 step 8)."""
    product = runner.product
    if override_url(product) or os.environ.get(env_name(product, "SYNC_REEXEC")) == "1":
        return
    version = product.identity.version
    pinned = locked_engine_version(runner)
    if pinned is None or pinned == version:
        return
    name = product.identity.name
    runner.say(
        f"{name} sync: flake.lock pins {product.engine.flake_input} {pinned}; this is"
        f" {version}: re-running from the lock"
    )
    handover(
        runner,
        reexec_argv(product, argv),
        {
            env_name(product, "SYNC_REEXEC"): "1",
            **lock_changed_env(product, lock_changed),
        },
    )
