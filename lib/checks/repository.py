"""Confirm a product's .github files follow the conventions.

    repository.py pins PRODUCT_DIR
    repository.py dependabot PRODUCT_DIR SPEC_JSON

`pins` (docs/conventions.md, CI): every `uses:` in .github/workflows and in
.github/actions is pinned by a full commit SHA, with the version in a trailing
comment. A local `./` path is exempt.

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

PIN = re.compile(r"[^@\s]+@[0-9a-f]{40}")
COOLDOWN_DAYS = 7


def mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {k.value: v for k, v in node.value if isinstance(k, yaml.ScalarNode)}


def sequence(node: yaml.Node | None) -> list[yaml.Node]:
    return node.value if isinstance(node, yaml.SequenceNode) else []


def uses_nodes(root: yaml.Node) -> Iterator[yaml.Node]:
    # A workflow's jobs.<id>.uses (a reusable workflow) and
    # jobs.<id>.steps[].uses; a composite action's runs.steps[].uses.
    top = mapping(root)
    jobs = [mapping(job) for job in mapping(top.get("jobs")).values()]
    steps = [step for job in jobs for step in sequence(job.get("steps"))]
    steps += sequence(mapping(top.get("runs")).get("steps"))
    for holder in jobs + [mapping(step) for step in steps]:
        if "uses" in holder:
            yield holder["uses"]


def pin_findings(path: Path, rel: str) -> Iterator[str]:
    text = path.read_text()
    root = yaml.compose(text)
    if root is None:
        return
    lines = text.splitlines()
    for node in uses_nodes(root):
        where = f"{rel}:{node.start_mark.line + 1}"
        ref = node.value if isinstance(node, yaml.ScalarNode) else ""
        if ref.startswith("./"):
            continue
        if not PIN.fullmatch(ref):
            yield f"{where}: {ref} is not pinned by a full commit SHA"
            continue
        rest = lines[node.end_mark.line][node.end_mark.column :].strip()
        if not rest.startswith("#") or not rest.lstrip("#").strip():
            yield f"{where}: {ref} has no version in a trailing comment"


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
    findings = [
        line
        for path in files
        for line in pin_findings(path, str(path.relative_to(product_dir)))
    ]
    for line in findings:
        print(line)
    if findings:
        print(
            f"{len(findings)} action(s) not pinned. Pin each by its full commit SHA,"
            " with the version in a trailing comment (docs/conventions.md, CI)."
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
