"""Fixture checkouts: the shapes of checkout every template must render for (DESIGN §3.4).

``Product.fixtures`` names them (``Fixture``: extra ``[tool.<table>]`` lines, files, and
the modules it requires). ``<cli> profile validate`` renders an org profile against each,
and the conformance check syncs each under every built-in profile. ``throwaway`` makes
one: a git repository of its own, written by ``hooks.write_fixture``, committed once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.common.repo import NO_AUTO_MAINTENANCE
from standards_core.common.report import EnvError

if TYPE_CHECKING:
    from standards_core.product import Fixture, Product

# Where a throwaway checkout carries the profile under test, as an in-repo profile.
PROFILE_DIR = ".standards-profile"


def git_env() -> dict[str, str]:
    """An environment in which none of the user's git configuration (commit signing, a
    global ``core.hooksPath``, init templates) applies, and no hook runs."""
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "profile-validate",
        "GIT_AUTHOR_EMAIL": "profile-validate@example.org",
        "GIT_COMMITTER_NAME": "profile-validate",
        "GIT_COMMITTER_EMAIL": "profile-validate@example.org",
    }
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        env.pop(key, None)
    return env


def git(root: Path, *argv: str) -> None:
    """``git -C root <argv>`` in the isolated environment."""
    try:
        subprocess.run(
            ["git", "-C", str(root), *NO_AUTO_MAINTENANCE, *argv],
            check=True,
            capture_output=True,
            env=git_env(),
        )
    except FileNotFoundError as e:
        raise EnvError("git is not on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace").strip()
        raise EnvError(f"git {' '.join(argv)} failed: {stderr}") from e


def commit(root: Path, message: str) -> None:
    """Stage everything and commit it, unsigned and with no hook."""
    git(root, "add", "-A")
    git(
        root,
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "commit",
        "-q",
        "--no-verify",
        "--allow-empty",
        "-m",
        message,
    )


def throwaway(
    product: Product,
    root: Path,
    profile_dir: Path | None,
    name: str,
    fixture: Fixture,
    *,
    profile: str | None = None,
) -> Path:
    """A checkout under ``root`` shaped as fixture ``name``, in a git repository with one
    commit. ``profile_dir`` is an org profile directory copied in as the in-repo profile
    ``./.standards-profile``; without one, ``profile`` (a built-in name) is the table's
    profile. ``hooks.write_fixture`` writes the config file and the files."""
    write = product.hooks.write_fixture
    if write is None:
        raise RuntimeError(f"{product.identity.name} has no hooks.write_fixture")
    checkout = root / f"{name}-{len(list(root.iterdir()))}"
    checkout.mkdir(parents=True)
    git(checkout, "init", "-q", "--template=")
    if profile_dir is not None:
        shutil.copytree(profile_dir, checkout / PROFILE_DIR)
        profile_rel = f"./{PROFILE_DIR}"
    else:
        profile_rel = profile or "minimal"
    write(checkout, name, fixture, profile_rel)
    commit(checkout, name)
    return checkout
