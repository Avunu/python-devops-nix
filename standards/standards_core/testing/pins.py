"""The three pins of this core in a product repository agree (DESIGN §10.2, §10.3).

A product reaches the core three ways, and each names one commit of the repository
that ships it:

- its ``flake.lock`` locks the root input (``--input``, default ``python-devops-nix``);
- its tools' ``pyproject.toml`` (``py/**/pyproject.toml``, or each ``--pyproject``
  glob) declares ``standards-core @ git+https://github.com/<owner>/<repo>@<sha>
  #subdirectory=standards``, the dependency ``uv tool install`` resolves with no Nix;
- its ``.github/**`` workflows and actions use ``<owner>/<repo>/…@<sha>``: the
  composite actions and the reusable workflows.

All three must be the same 40-hex commit of ``<owner>/<repo>`` on github.com::

    python -m standards_core.testing.pins --root DIR --owner OWNER [--reachable URL]

``--reachable URL`` also fetches ``URL`` (a blobless bare clone) and requires the
commit to be reachable from its ``main`` branch (``--branch``) or a ``v*`` tag, so a
product never ships a commit that only lived on a pull request's branch. That needs the
network, so the sandboxed check (``lib.standards.pins``) runs without it and the
product's CI runs it with it.

It prints one problem per line and exits 1 when there is any, 0 when the pins agree;
2 for a bad argument; 3 for an unreadable lock or a fetch that fails.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from standards_core.common import flakelock
from standards_core.common.report import (
    CLEAN,
    DRIFT,
    INVALID,
    EnvError,
    StandardsError,
)

DEFAULT_INPUT = "python-devops-nix"
DEFAULT_PYPROJECTS = ("py/**/pyproject.toml",)
DISTRIBUTION = "standards-core"
SUBDIRECTORY = "standards"
_REV = re.compile(r"[0-9a-f]{40}")
_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_REQUIREMENT = re.compile(
    r"^\s*" + re.escape(DISTRIBUTION) + r"\s*(?:\[[^\]]*\])?\s*@\s*(?P<url>\S+)",
    re.IGNORECASE,
)
_URL = re.compile(
    r"^git\+https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?@(?P<ref>[^#\s]+)(?:#(?P<fragment>\S*))?$"
)
# Directories a glob may reach that hold no file of the product's own.
_SKIP_PARTS = frozenset({"node_modules", "__pycache__"})


@dataclass
class Report:
    """What the scan found: the locked commit, each pin, and every problem."""

    locked: str | None = None
    urls: list[tuple[str, str]] = field(default_factory=list)  # (file, ref)
    uses: list[tuple[str, str]] = field(default_factory=list)  # (file:line, ref)
    problems: list[str] = field(default_factory=list)


def lock_rev(root: Path, owner: str, name: str, repo_name: str, report: Report) -> None:
    """The commit ``flake.lock``'s root input ``name`` locks, checked against
    ``<owner>/<repo_name>`` on github.com."""
    lock = flakelock.load(root / "flake.lock")
    _, root_node = flakelock.node_at(lock, []) or ("", {})
    root_inputs = root_node.get("inputs", {})
    if not isinstance(root_inputs, dict) or name not in root_inputs:
        report.problems.append(f"flake.lock: the root has no {name} input")
        return
    found = flakelock.node_at(lock, [name])
    if found is None:
        report.problems.append(f"flake.lock: the root's {name} input has no node")
        return
    locked = found[1].get("locked")
    if not isinstance(locked, dict):
        report.problems.append(f"flake.lock: {name} is not locked")
        return
    where = f"flake.lock: {name}"
    if locked.get("type") != "github":
        report.problems.append(
            f"{where} is locked as {locked.get('type')!r}, not from github"
        )
        return
    got = f"{locked.get('owner')}/{locked.get('repo')}"
    if str(locked.get("owner", "")).lower() != owner.lower() or (
        locked.get("repo") != repo_name
    ):
        report.problems.append(f"{where} locks {got}, not {owner}/{repo_name}")
    rev = locked.get("rev")
    if not isinstance(rev, str) or not _REV.fullmatch(rev):
        report.problems.append(f"{where} has no 40-hex rev")
        return
    report.locked = rev


def _requirements(doc: dict) -> Iterator[str]:
    project = doc.get("project")
    if not isinstance(project, dict):
        return
    for item in project.get("dependencies") or []:
        if isinstance(item, str):
            yield item
    extras = project.get("optional-dependencies") or {}
    if isinstance(extras, dict):
        for items in extras.values():
            for item in items or []:
                if isinstance(item, str):
                    yield item


def pyproject_files(root: Path, patterns: Sequence[str]) -> list[Path]:
    """The files the globs match under ``root``, none in a hidden or tool directory."""
    seen: dict[Path, None] = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root)
            hidden = any(p.startswith(".") or p in _SKIP_PARTS for p in rel.parts[:-1])
            if path.is_file() and not hidden:
                seen.setdefault(path, None)
    return list(seen)


def url_refs(
    root: Path,
    patterns: Sequence[str],
    owner: str,
    repo_name: str,
    report: Report,
) -> None:
    """Every ``standards-core @ <url>`` requirement, checked for its shape."""
    for path in pyproject_files(root, patterns):
        rel = path.relative_to(root).as_posix()
        try:
            doc = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            report.problems.append(f"{rel}: unreadable: {e}")
            continue
        for requirement in _requirements(doc):
            m = _REQUIREMENT.match(requirement)
            if not m:
                continue
            url = m.group("url").rstrip(";,")
            shape = _URL.match(url)
            if not shape:
                report.problems.append(
                    f"{rel}: {DISTRIBUTION} @ {url} is not"
                    f" git+https://github.com/{owner}/{repo_name}@<sha>"
                    f"#subdirectory={SUBDIRECTORY}"
                )
                continue
            got = f"{shape.group('owner')}/{shape.group('repo')}"
            if shape.group("owner").lower() != owner.lower() or (
                shape.group("repo") != repo_name
            ):
                report.problems.append(
                    f"{rel}: {DISTRIBUTION} comes from {got}, not {owner}/{repo_name}"
                )
            fragment = dict(
                part.partition("=")[::2]
                for part in (shape.group("fragment") or "").split("&")
                if part
            )
            if fragment.get("subdirectory") != SUBDIRECTORY:
                report.problems.append(
                    f"{rel}: {DISTRIBUTION}'s URL lacks #subdirectory={SUBDIRECTORY}"
                )
            ref = shape.group("ref")
            if not _REV.fullmatch(ref):
                report.problems.append(
                    f"{rel}: {DISTRIBUTION} is pinned to {ref!r}, not a 40-hex commit"
                )
            report.urls.append((rel, ref))


def uses_refs(root: Path, owner: str, repo_name: str, report: Report) -> None:
    """Every ``<owner>/<repo>/…@<ref>`` in ``.github/**``."""
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.-])"
        + re.escape(owner)
        + "/"
        + re.escape(repo_name)
        + r"/[^\s@'\"]+@(?P<ref>[^\s'\"#]+)",
        re.IGNORECASE,
    )
    github = root / ".github"
    if not github.is_dir():
        return
    for path in sorted(github.rglob("*")):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in pattern.finditer(line):
                ref = m.group("ref")
                report.uses.append((f"{rel}:{number}", ref))
                if not _REV.fullmatch(ref):
                    report.problems.append(
                        f"{rel}:{number}: {owner}/{repo_name} is used at {ref!r}, not"
                        " a 40-hex commit"
                    )


def check(
    root: Path,
    owner: str,
    *,
    input_name: str = DEFAULT_INPUT,
    repo_name: str | None = None,
    pyprojects: Sequence[str] = DEFAULT_PYPROJECTS,
) -> Report:
    """The scan of ``root``: the three pins, and whether they are one commit."""
    repo_name = repo_name or input_name
    report = Report()
    lock_rev(root, owner, input_name, repo_name, report)
    url_refs(root, pyprojects, owner, repo_name, report)
    uses_refs(root, owner, repo_name, report)
    if not report.urls:
        report.problems.append(
            f"no {' or '.join(pyprojects)} declares {DISTRIBUTION} @"
            f" git+https://github.com/{owner}/{repo_name}@<sha>"
            f"#subdirectory={SUBDIRECTORY}"
        )
    if report.locked:
        for where, ref in report.urls + report.uses:
            if _REV.fullmatch(ref) and ref != report.locked:
                report.problems.append(
                    f"{where}: pins {ref[:12]}, but flake.lock locks"
                    f" {input_name} {report.locked[:12]}"
                )
    return report


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise EnvError("git is not on PATH") from e


def reachable(url: str, rev: str, branch: str = "main") -> str | None:
    """Why ``rev`` is not on ``url``'s ``branch`` or a ``v*`` tag, or ``None``.

    A fetch that fails is an ``EnvError``."""
    with tempfile.TemporaryDirectory(prefix="standards-pins-") as tmp:
        bare = Path(tmp) / "repo.git"
        proc = _git("clone", "--quiet", "--bare", "--filter=blob:none", url, str(bare))
        if proc.returncode != 0:
            raise EnvError(f"git clone {url}: {proc.stderr.strip()}")
        if _git("cat-file", "-e", f"{rev}^{{commit}}", cwd=bare).returncode != 0:
            return f"{rev[:12]} is not a commit of {url}"
        proc = _git(
            "for-each-ref",
            "--contains",
            rev,
            "--format=%(refname)",
            f"refs/heads/{branch}",
            "refs/tags/v*",
            cwd=bare,
        )
        if proc.returncode != 0:
            raise EnvError(f"git for-each-ref: {proc.stderr.strip()}")
        if proc.stdout.strip():
            return None
        return f"{rev[:12]} is on neither {branch} nor a v* tag of {url}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m standards_core.testing.pins",
        description=(__doc__ or "").split("\n\n")[0],
    )
    parser.add_argument("--root", required=True, help="the product repository")
    parser.add_argument(
        "--owner", required=True, help="the GitHub owner the core comes from"
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"the root flake input that locks the core (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="the repository name on GitHub (default: the input's name)",
    )
    parser.add_argument(
        "--pyproject",
        action="append",
        default=None,
        metavar="GLOB",
        help="where the tools' pyproject.toml files are, relative to --root"
        f" (repeatable; default: {', '.join(DEFAULT_PYPROJECTS)})",
    )
    parser.add_argument(
        "--reachable",
        default="",
        metavar="URL",
        help="also require the commit on URL's --branch or a v* tag (network)",
    )
    parser.add_argument(
        "--branch", default="main", help="the branch --reachable reads (main)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as e:
        return INVALID if e.code else CLEAN
    root = Path(args.root)
    for value, what in ((args.owner, "--owner"), (args.input, "--input")):
        if not _NAME.fullmatch(value):
            print(f"{what} {value!r} is not a GitHub name", file=sys.stderr)
            return INVALID
    if not root.is_dir():
        print(f"--root {root} is not a directory", file=sys.stderr)
        return INVALID
    try:
        report = check(
            root,
            args.owner,
            input_name=args.input,
            repo_name=args.repo,
            pyprojects=tuple(args.pyproject or DEFAULT_PYPROJECTS),
        )
        if args.reachable and report.locked and not report.problems:
            problem = reachable(args.reachable, report.locked, args.branch)
            if problem:
                report.problems.append(problem)
    except StandardsError as e:
        print(f"pins: {e}", file=sys.stderr)
        return e.code
    for problem in report.problems:
        print(problem)
    if report.problems:
        return DRIFT
    print(
        f"{args.input} {report.locked}: flake.lock, {len(report.urls)}"
        f" {DISTRIBUTION} URL(s) and {len(report.uses)} .github reference(s) agree"
        + (" (reachable)" if args.reachable else "")
    )
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
