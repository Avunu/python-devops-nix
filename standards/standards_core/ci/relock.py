"""relock-plan's input check, the relock patch, and the workflow-path refusal (DESIGN §8).

- ``moved_inputs``: a Dependabot ``nix`` PR that moves a rollout input (the engine's
  lock node or ``standards-profile``) is not relocked; it moves through ``<cli> repo
  rollout``, and the auto-merge job closes it.
- ``make_patch``: ``git add -A`` then ``git diff --cached --binary``. Sync stages its
  writes and new files are untracked, so a plain ``git diff`` would miss both. A patch
  that names any path under ``.github/workflows/`` is refused: ``GITHUB_TOKEN`` cannot
  push it, and no workflow ever pushes one.
- ``workflow_paths``: the paths of a commit range or of the index under
  ``.github/workflows/``.
- ``apply_patch``: relock-push's check of relock-plan's patch, which ran the PR's code
  and so is data: no rename, copy or mode change, each path an added, modified or
  deleted regular file (mode 100644) that ``product.ci.relock_patch_paths`` names, none
  under ``.github/``, no symlink, and something changed. It applies the patch to the
  index (``git apply --index``) only once the patch text passes.
- ``verify``: what the relocked files say, through the product's
  ``ci.verify_relock`` (``ok``, ``needs-review`` or ``reject``), before a job that can
  hold a write token pushes them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.common import repo
from standards_core.common.report import ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Product

# `.github/workflows/` at the repository root or, for a checkout in a subdirectory (a
# fixture inside a larger repository), at its root: either way a workflow file.
WORKFLOW = re.compile(r"(^|/)\.github/workflows/")

OK, REVIEW, REJECT = "ok", "needs-review", "reject"
REGULAR = "100644"
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Patch headers that rename, copy or change a mode: the patch text is refused first.
_MOVES = re.compile(
    r"^(?:(?:rename|copy) (?:from|to)|(?:dis)?similarity index|(?:old|new) mode) ",
    re.MULTILINE,
)
_FILE_MODE = re.compile(r"^(?:new file|deleted file) mode (\S+)$", re.MULTILINE)
_INDEX_MODE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+ (\S+)$", re.MULTILINE)


@dataclass
class Verification:
    """What a product's ``ci.verify_relock`` found: ``rejects`` fail the step and
    nothing is pushed; ``reviews`` are pushed with auto-merge off."""

    rejects: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)

    def decision(self) -> str:
        if self.rejects:
            return REJECT
        return REVIEW if self.reviews else OK

    def reasons(self) -> list[str]:
        lines = [f"reject: {r}" for r in self.rejects]
        lines += [f"needs-review: {r}" for r in self.reviews]
        return list(dict.fromkeys(lines))


def _lock_doc(text: str, where: str) -> dict:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{where}: not JSON: {e}") from e
    if not isinstance(doc, dict) or "nodes" not in doc:
        raise ConfigError(f"{where}: not a flake.lock")
    return doc


def input_node(doc: dict, name: str) -> object:
    """The lock data of the root input ``name`` (its ``locked`` table), ``None`` when
    absent."""
    nodes = doc.get("nodes", {})
    root = nodes.get(doc.get("root", "root"), {})
    ref = root.get("inputs", {}).get(name)
    if ref is None:
        return None
    if isinstance(ref, list):
        return ["follows", *ref]
    return nodes.get(ref, {}).get("locked")


def moved_inputs(product: Product, base_lock: str, head_lock: str) -> list[str]:
    """The rollout inputs whose lock node differs between two ``flake.lock`` texts."""
    base = (
        _lock_doc(base_lock, "flake.lock at the merge-base")
        if base_lock.strip()
        else {"nodes": {}}
    )
    head = _lock_doc(head_lock, "flake.lock")
    return [
        n for n in product.rollout_inputs if input_node(base, n) != input_node(head, n)
    ]


def merge_base(root: Path, base: str) -> str:
    try:
        return repo.git(root, "merge-base", base, "HEAD").strip()
    except EnvError:
        return base


def lock_at(root: Path, rev: str) -> str:
    """``flake.lock`` (beside the checkout's root) at ``rev``; empty when it did not
    exist."""
    try:
        return repo.git(root, "show", f"{rev}:./flake.lock")
    except EnvError:
        return ""


def workflow_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if WORKFLOW.search(p)]


def make_patch(root: Path, out: Path) -> tuple[bool, list[str]]:
    """Stage everything and write the binary patch to ``out``.

    Returns ``(empty, refused)``: ``refused`` lists the workflow paths the patch names
    (and then nothing should be pushed)."""
    repo.git(root, "add", "-A")
    patch = repo.git(root, "diff", "--cached", "--binary")
    out.write_text(patch)
    names = repo.git(root, "diff", "--cached", "--name-only").split("\n")
    return not patch.strip(), workflow_paths([n for n in names if n])


def changed(root: Path, base: str | None, cached: bool) -> list[str]:
    """Paths changed in ``base..HEAD``, or staged in the index with ``cached``."""
    if cached:
        out = repo.git(
            root, "diff", "--cached", "--name-only", *([base] if base else [])
        )
    else:
        out = repo.git(root, "diff", "--name-only", f"{base}..HEAD")
    return [p for p in out.split("\n") if p]


def _patch_text_problems(text: str) -> list[str]:
    out = []
    if _MOVES.search(text):
        out.append("the patch renames or copies a file, or changes a file's mode")
    modes = _FILE_MODE.findall(text) + _INDEX_MODE.findall(text)
    if any(mode != REGULAR for mode in modes):
        out.append(f"the patch has a file mode other than {REGULAR}")
    return out


def _path_problem(path: str) -> str | None:
    wrapped = f"/{path}/"
    if path.startswith("/") or any(
        part in wrapped for part in ("//", "/./", "/../", "/.github/")
    ):
        return "path"
    return None


def apply_patch(product: Product, root: Path, patch: Path) -> list[str]:
    """Check relock-plan's ``patch``, apply it to the index of ``root`` and check what
    the index then holds against ``HEAD``. Returns the problems; empty when the patch
    is one relock-push may commit.

    The patch text is checked before ``git apply`` runs, and nothing is applied when it
    fails. A product that names no ``ci.relock_patch_paths`` pushes no relock: that is a
    ``ConfigError`` (exit 2), not a refusal."""
    from standards_core.scaffold import globs

    allowed = product.ci.relock_patch_paths
    if not allowed:
        raise ConfigError(
            f"{product.identity.name} names no ci.relock_patch_paths: no relock patch"
            " may be pushed"
        )
    try:
        text = patch.read_bytes().decode("utf-8", errors="replace")
    except OSError as e:
        raise ConfigError(f"{patch}: {e.strerror or e}") from e
    if not text.strip():
        return ["the patch changes nothing"]
    problems = _patch_text_problems(text)
    if problems:
        return problems
    try:
        repo.git(root, "apply", "--index", str(patch.resolve()))
    except EnvError as e:
        return [f"the patch does not apply: {e}"]
    raw = repo.git(root, "diff", "--cached", "--no-renames", "--raw", "-z", "HEAD")
    fields = raw.split("\0")
    out: list[str] = []
    entries = 0
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta:
            continue
        entries += 1
        old, new, _old_id, _new_id, status = meta.lstrip(":").split()[:5]
        why = []
        if status == "A" and new != REGULAR:
            why.append(f"mode {new}")
        elif status == "M" and (old, new) != (REGULAR, REGULAR):
            why.append(f"mode {old} to {new}")
        elif status == "D" and old != REGULAR:
            why.append(f"mode {old}")
        elif status not in ("A", "M", "D"):
            why.append(f"change type {status}")
        if _path_problem(path):
            why.append("path")
        elif not globs.match_any(allowed, path):
            why.append("not a path this job pushes")
        if status != "D" and (root / path).is_symlink():
            why.append("symlink")
        if why:
            out.append(f"{path!r}: {'; '.join(why)}")
    if not entries:
        out.append("the patch changes nothing")
    return out


def verify(product: Product, root: Path, base: str, head: str) -> Verification:
    """The product's ``ci.verify_relock`` over ``base`` (a commit the integration branch
    holds) and ``head`` (the commit or tree to push), both full object ids."""
    for flag, value in (("--base", base), ("--head", head)):
        if not _OBJECT_ID.fullmatch(value):
            raise ConfigError(f"{flag} {value!r} is not a full object id")
    check = product.ci.verify_relock
    if check is None:
        raise ConfigError(
            f"{product.identity.name} has no ci.verify_relock: there is nothing to"
            " verify a relock with"
        )
    return check(root, base, head)
