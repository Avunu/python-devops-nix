"""Confirm a product's .github files follow the conventions.

    repository.py pins PRODUCT_DIR
    repository.py dependabot PRODUCT_DIR SPEC_JSON

`pins` (docs/conventions.md, CI): every `uses:` in .github/workflows and in
.github/actions is pinned by a full commit SHA, or a `docker://` image by its
sha256 digest, with a trailing comment, which by convention names the version.
A local `./` path is exempt. Where .github/workflows/actions.lock exists, an
action a workflow step uses may instead be pinned by an exact release tag
(`vX.Y.Z`), which gh-actions-lock locks. A reusable workflow call to an Avunu
repository may also be pinned by its exact release tag; any other reusable call
and a composite action's steps keep the SHA. A branch is never a pin.

`dependabot` (docs/conventions.md, Dependabot): every ecosystem in
.github/dependabot.yml has the commit prefix, the seven-day cooldown and one
group of every dependency (`patterns: ["*"]`). SPEC_JSON is {"prefix": str,
"developmentPrefix": str, "development": [{"ecosystem": str, "directory":
str}, ...]}: the entries listed in `development` are development-only tooling
and take `developmentPrefix` instead.

Each prints one line per finding and exits 1 if there is any.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

PIN = re.compile(r"[^@\s]+@[0-9a-f]{40}|docker://[^@\s]+@sha256:[0-9a-f]{64}")
# An exact release tag of an action: owner/repo[/path]@vX.Y.Z.
TAG = re.compile(r"[A-Za-z0-9_.-]+/[^@\s]+@v[0-9]+\.[0-9]+\.[0-9]+")
LOCK = Path(".github/workflows/actions.lock")
COOLDOWN_DAYS = 7


def mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {k.value: v for k, v in node.value if isinstance(k, yaml.ScalarNode)}


def sequence(node: yaml.Node | None) -> list[yaml.Node]:
    return node.value if isinstance(node, yaml.SequenceNode) else []


def uses_nodes(root: yaml.Node) -> Iterator[tuple[yaml.Node, str]]:
    # Each `uses:` with its kind. A workflow step's action is "step", the kind a
    # lock may pin by release tag. A job's jobs.<id>.uses is "reusable", a
    # reusable workflow call. A composite action's runs.steps[].uses is
    # "composite", which stays on the SHA.
    top = mapping(root)
    for job_node in mapping(top.get("jobs")).values():
        job = mapping(job_node)
        if "uses" in job:
            yield job["uses"], "reusable"
        for step in sequence(job.get("steps")):
            if "uses" in mapping(step):
                yield mapping(step)["uses"], "step"
    for step in sequence(mapping(top.get("runs")).get("steps")):
        if "uses" in mapping(step):
            yield mapping(step)["uses"], "composite"


def avunu_release_tag(ref: str) -> bool:
    """A reusable workflow of an Avunu repository, at an exact release tag."""
    return ref.startswith("Avunu/") and TAG.fullmatch(ref) is not None


def pin_findings(path: Path, rel: str, lockable: bool) -> Iterator[str]:
    text = path.read_text()
    root = yaml.compose(text)
    if root is None:
        return
    lines = text.splitlines()
    for node, kind in uses_nodes(root):
        where = f"{rel}:{node.start_mark.line + 1}"
        ref = node.value if isinstance(node, yaml.ScalarNode) else ""
        if ref.startswith("./"):
            continue
        if lockable and kind == "step" and TAG.fullmatch(ref):
            continue
        if kind == "reusable" and avunu_release_tag(ref):
            continue
        if not PIN.fullmatch(ref):
            if ref.startswith("docker://"):
                by = "sha256 digest"
            elif kind == "reusable" and ref.startswith("Avunu/"):
                by = "full commit SHA or exact release tag (vX.Y.Z)"
            else:
                by = "full commit SHA"
            yield f"{where}: {ref} is not pinned by a {by}"
            continue
        if not trailing_comment(lines, node):
            yield f"{where}: {ref} has no trailing comment"


def trailing_comment(lines: list[str], node: yaml.Node) -> bool:
    # A block scalar's comment can only follow its `|` or `>` indicator; any
    # other scalar's follows its end, maybe after the rest of a flow mapping.
    block = getattr(node, "style", None) in ("|", ">")
    mark = node.start_mark if block else node.end_mark
    rest = lines[mark.line][mark.column :]
    if block:
        rest = rest.lstrip("|>+-0123456789")
    comment = re.search(r"(?:^|\s)#(.*)", rest)
    return comment is not None and bool(comment.group(1).strip())


def pins(product_dir: Path) -> int:
    github = product_dir / ".github"
    files = sorted(
        p for ext in ("yml", "yaml") for p in (github / "workflows").glob(f"*.{ext}")
    )
    files += sorted(
        p
        for ext in ("yml", "yaml")
        for p in (github / "actions").rglob(f"action.{ext}")
    )
    if not files:
        print(".github/workflows: no workflow found")
        return 1
    lockable = (product_dir / LOCK).is_file()
    findings = [
        line
        for path in files
        for line in pin_findings(path, str(path.relative_to(product_dir)), lockable)
    ]
    for line in findings:
        print(line)
    if findings:
        print(
            f"{len(findings)} action(s) not pinned. Pin each by its full commit SHA,"
            " with its version in a trailing comment, or by its exact release tag where"
            " the convention allows one: a workflow step where .github/workflows/actions.lock"
            " exists, or a reusable workflow of Avunu (docs/conventions.md, CI)."
        )
        return 1
    print(f"every action in {len(files)} file(s) is pinned")
    return 0


def directories(update: dict) -> list[str]:
    if "directories" in update:
        return [str(d) for d in update["directories"] or []]
    return [str(update.get("directory", "/"))]


def update_findings(update: dict, prefix: str) -> Iterator[str]:
    got = (update.get("commit-message") or {}).get("prefix")
    if got != prefix:
        yield f"commit-message.prefix is {json.dumps(got)}, not {json.dumps(prefix)}"
    days = (update.get("cooldown") or {}).get("default-days")
    if days != COOLDOWN_DAYS:
        yield f"cooldown.default-days is {json.dumps(days)}, not {COOLDOWN_DAYS}"
    groups = (update.get("groups") or {}).values()
    if not any((group or {}).get("patterns") == ["*"] for group in groups):
        yield 'no group has patterns: ["*"]'


def dependabot(product_dir: Path, spec: dict) -> int:
    path = product_dir / ".github" / "dependabot.yml"
    if not path.is_file():
        print(".github/dependabot.yml: missing from the product")
        return 1
    updates = (yaml.safe_load(path.read_text()) or {}).get("updates") or []
    development = {(d["ecosystem"], d["directory"]) for d in spec["development"]}
    seen: set[tuple[str, str]] = set()
    findings = []
    for update in updates:
        ecosystem = str(update.get("package-ecosystem"))
        dirs = directories(update)
        keys = {(ecosystem, d) for d in dirs}
        seen |= keys
        dev = bool(keys & development)
        prefix = spec["developmentPrefix" if dev else "prefix"]
        name = f"{ecosystem} in {', '.join(dirs)}"
        findings += [f"{name}: {f}" for f in update_findings(update, prefix)]
    if not updates:
        findings.append("no updates")
    # A development entry that names no ecosystem in the file is stale.
    findings += [
        f"{e} in {d}: listed as development-only, but the file has no such entry"
        for e, d in sorted(development - seen)
    ]
    for line in findings:
        print(f".github/dependabot.yml: {line}")
    if findings:
        print(
            f"{len(findings)} difference(s) from the Dependabot shape"
            " (docs/conventions.md, Dependabot)."
        )
        return 1
    print(f".github/dependabot.yml: {len(updates)} ecosystem(s) in the shape")
    return 0


def main() -> int:
    command, product_dir, *rest = sys.argv[1:]
    if command == "pins":
        return pins(Path(product_dir))
    if command == "dependabot":
        return dependabot(Path(product_dir), json.loads(rest[0]))
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    sys.exit(main())
