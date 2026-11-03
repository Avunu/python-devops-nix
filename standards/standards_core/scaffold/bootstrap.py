"""What ``sync --write`` runs around the plan: the two phases' external steps (DESIGN §6.3).

Phase A writes the phase-``a`` files (the flake, ``.envrc``) and locks the flake; phase
B renders everything else, then brings the derived files up (the lock seeds, the
product's steps). Every external command goes through ``Runner``, which prints it, and
which ``--dry-run`` and ``--offline`` turn into a report of what would run.

A sync that hands over to another (a dev-shell re-entry when phase B's tools are
missing) gets the child's exit code back through ``<PREFIX>_SYNC_RESULT``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from standards_core.common.report import EnvError

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
