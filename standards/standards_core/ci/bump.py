"""The post-merge bump of the ``develop+series`` flow (DESIGN §8.2).

After a merge to the integration branch, ``ci bump --base origin/<series>`` raises the
version of every release unit that changed since the release branch, by the highest
level its conventional commits ask for, and commits the new versions in one commit
(``tool.commit_subject``); ``ci fast-forward`` then moves the release branch to it once
its checks pass.

Per unit (``tool.units``):

1. ``since`` is the newest commit in ``base..HEAD`` that ``ci bump`` wrote and that
   changed the unit's version file (its last bump, not yet fast-forwarded), else
   ``base``. A bump commit is recognised by its whole subject, ``tool.commit_subject``
   with ``{units}`` matching any text that names the unit; a feature commit that also
   edits the version file is never ``since``, and is counted in its own bump;
2. the non-merge commits in ``since..HEAD`` that touch the unit, bump commits excluded,
   and those whose files under the unit are all ``tool.exempt`` dropped, give the level
   (``conventional.level``); none is no bump;
3. ``old`` is the version at ``since``. When ``HEAD``'s differs, someone edited it by
   hand since the last bump: the unit is skipped with a notice, so a hand edit is never
   bumped a second time. Otherwise ``new = scheme.bump(old, level)``, which
   ``scheme.valid`` must accept.

The bump commit's type is ``chore``, so the run its push triggers computes no level and
goes straight to the fast-forward (loop safety).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.ci import conventional, notice
from standards_core.common import repo
from standards_core.common.report import ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Level, ReleaseTool, ReleaseUnit, VersionScheme

_SEP = "\x1f"
_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Bump:
    unit: ReleaseUnit
    old: str
    new: str
    level: Level
    commits: tuple[str, ...]  # short shas that set the level


@cache
def _subject(template: str) -> re.Pattern[str]:
    parts = template.split("{units}")
    return re.compile("(?P<units>.+)".join(re.escape(p) for p in parts))


def is_bump(tool: ReleaseTool, subject: str, unit: str | None = None) -> bool:
    """Whether ``subject`` is a bump commit's (of ``unit``, when given)."""
    m = _subject(tool.commit_subject).fullmatch(subject.strip())
    if m is None:
        return False
    if unit is None or "units" not in m.groupdict():
        return True
    return unit in [u.strip() for u in m["units"].split(",")]


def _log(root: Path, *args: str) -> list[tuple[str, str]]:
    out = repo.git(root, "log", f"--format=%H{_SEP}%s", *args)
    return [
        (sha, subject)
        for sha, _, subject in (line.partition(_SEP) for line in out.splitlines())
        if sha
    ]


def since(root: Path, base: str, unit: ReleaseUnit, tool: ReleaseTool) -> str:
    """The unit's last bump in ``base..HEAD``, else ``base``."""
    for sha, subject in _log(root, f"{base}..HEAD", "--", unit.version_file):
        if is_bump(tool, subject, unit.name):
            return sha
    return base


def _show(root: Path, rev: str, path: str) -> str | None:
    try:
        return repo.git(root, "show", f"{rev}:./{path}")
    except EnvError:
        return None


def _label(rev: str) -> str:
    return rev[:12] if _SHA.fullmatch(rev) else rev


def counted(
    root: Path, start: str, unit: ReleaseUnit, tool: ReleaseTool
) -> list[tuple[str, conventional.Commit | None]]:
    """The commits in ``start..HEAD`` that count for ``unit``, newest first."""
    out = []
    for sha, subject in _log(root, "--no-merges", f"{start}..HEAD", "--", unit.path):
        if is_bump(tool, subject):
            continue
        files = repo.git(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            sha,
            "--",
            unit.path,
        ).split()
        if files and all(tool.exempt(unit, f) for f in files):
            continue
        message = repo.git(root, "log", "-1", "--format=%B", sha)
        out.append((sha, conventional.parse(message)))
    return out


def plan_bumps(
    root: Path,
    base: str,
    tool: ReleaseTool,
    scheme: VersionScheme,
    ctx: Mapping[str, Any],
) -> list[Bump]:
    """The bump each unit needs; units with none, or edited by hand, are left out (the
    latter with a notice)."""
    cfg = ctx.get("cfg") or {}
    facts = ctx.get("discover") or {}
    out: list[Bump] = []
    for unit in tool.units(root, ctx):  # type: ignore[arg-type]
        start = since(root, base, unit, tool)
        commits = counted(root, start, unit, tool)
        found = conventional.level(c for _, c in commits)
        if found is None:
            continue
        old_text = _show(root, start, unit.version_file)
        old = tool.read_version(old_text, unit) if old_text is not None else None
        if old is None:
            notice(f"{unit.name}: no version at {_label(start)}; not bumped")
            continue
        head_text = _show(root, "HEAD", unit.version_file)
        head = tool.read_version(head_text, unit) if head_text is not None else None
        if head != old:
            notice(
                f"{unit.name}: version edited by hand since {_label(start)}"
                f" ({old} → {head}); not bumped"
            )
            continue
        new = scheme.bump(old, found, cfg, facts)
        problem = scheme.valid(new, cfg, facts)
        if problem is not None:
            raise ConfigError(f"{unit.name}: {old} raised to {new}: {problem}")
        shas = tuple(
            sha[:12] for sha, c in commits if conventional.commit_level(c) == found
        )
        out.append(Bump(unit, old, new, found, shas))
    return out


def subject(tool: ReleaseTool, bumps: list[Bump]) -> str:
    return tool.commit_subject.format(units=", ".join(b.unit.name for b in bumps))


def apply_bumps(root: Path, bumps: list[Bump], tool: ReleaseTool) -> str:
    """Write each new version, commit them in one commit, and return its sha."""
    for bump in bumps:
        path = root / bump.unit.version_file
        path.write_text(tool.write_version(path.read_text(), bump.unit, bump.new))
        repo.git(root, "add", "--", bump.unit.version_file)
    repo.git(root, "commit", "-q", "-m", subject(tool, bumps))
    return repo.git(root, "rev-parse", "HEAD").strip()
