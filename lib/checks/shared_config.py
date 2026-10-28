"""Confirm a product's repository config files carry the base's settings.

    shared_config.py BASE_DIR PRODUCT_DIR SPEC_JSON

SPEC_JSON is {"files": [name, ...], "exceptions": {name: [[key, ...], ...]}}.
Each file is read from both directories and parsed into nested mappings (see
`load`). The rule (docs/conventions.md, Repository files): every setting the
base's copy makes, the product's copy makes too, with the same value. The product may
add settings of its own. A list is a value, compared whole, except the lists in
`SETS`: the product's carries every item the base's has, and may add its own. An
exception names the path of one base setting the product may set differently or
leave out, and the product documents why next to it.

Prints one line per difference and exits 1 if there is any.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import yaml


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_editorconfig(path: Path) -> dict:
    # {"<glob>": {key: value}}, with the preamble's keys (root = true) at the
    # top level. The format is INI without a header for the preamble, so
    # configparser does not read it. Keys are case-insensitive in the spec;
    # values are compared as written.
    top: dict = {}
    current = top
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = top.setdefault(line[1:-1], {})
        elif "=" in line:
            key, value = line.split("=", 1)
            current[key.strip().lower()] = value.strip()
        else:
            raise ValueError(f"{path.name}:{lineno}: not a section or a key: {raw}")
    return top


def load_prek(path: Path) -> dict:
    # {"<repo url>": {"<hook id>" or "<hook id> (<name>)": {hook settings}}}.
    # Only the hooks are shared. Each `rev` is moved by the repository's own
    # Dependabot, so revisions differ between repositories for days at a time;
    # the top-level `exclude` lists the repository's own paths.
    data = yaml.safe_load(path.read_text()) or {}
    repos: dict[str, dict] = {}
    for repo in data.get("repos", []):
        hooks = repos.setdefault(repo["repo"], {})
        for hook in repo.get("hooks", []):
            key = hook["id"] if "name" not in hook else f"{hook['id']} ({hook['name']})"
            hooks[key] = hook
    return repos


LOADERS = {
    ".editorconfig": load_editorconfig,
    ".pre-commit-config.yaml": load_prek,
}

# Per file, the paths of lists compared as sets the product may extend. The
# scope list is one for both products, and a product adds the scopes only it
# uses (docs/conventions.md, Commits).
SETS = {
    "committed.toml": {("allowed_scopes",)},
}


def load(path: Path) -> dict:
    if path.name in LOADERS:
        return LOADERS[path.name](path)
    if path.suffix == ".toml":
        return load_toml(path)
    raise ValueError(f"{path.name}: no comparison rule for this file")


def compare(
    base: dict,
    product: dict,
    path: tuple[str, ...],
    skip: set[tuple[str, ...]],
    sets: set[tuple[str, ...]],
):
    for key, want in base.items():
        here = (*path, key)
        if here in skip:
            continue
        shown = " > ".join(here)
        if key not in product:
            yield f"{shown}: missing (base: {json.dumps(want)})"
        elif isinstance(want, dict) and isinstance(product[key], dict):
            yield from compare(want, product[key], here, skip, sets)
        elif here in sets and isinstance(product[key], list):
            for item in want:
                if item not in product[key]:
                    yield f"{shown}: missing {json.dumps(item)}"
        elif product[key] != want:
            yield f"{shown}: {json.dumps(product[key])} (base: {json.dumps(want)})"


def has_path(tree: dict, path: tuple[str, ...]) -> bool:
    for key in path:
        if not isinstance(tree, dict) or key not in tree:
            return False
        tree = tree[key]
    return True


def main() -> int:
    base_dir, product_dir, spec_json = sys.argv[1:]
    spec = json.loads(spec_json)
    failures = 0
    for name in spec["files"]:
        product_file = Path(product_dir, name)
        if not product_file.is_file():
            print(f"{name}: missing from the product")
            failures += 1
            continue
        skip = {tuple(p) for p in spec["exceptions"].get(name, [])}
        base = load(Path(base_dir, name))
        sets = SETS.get(name, set())
        diffs = list(compare(base, load(product_file), (), skip, sets))
        # An exception for a setting the base no longer makes is stale.
        diffs += [
            f"{' > '.join(p)}: listed as an exception, but the base sets no such key"
            for p in sorted(skip)
            if not has_path(base, p)
        ]
        for diff in diffs:
            print(f"{name}: {diff}")
        failures += len(diffs)
        if not diffs:
            print(f"{name}: carries the base's settings")
    if failures:
        print(
            f"{failures} difference(s) from the base's copies. Copy the base's"
            " settings in, or list a documented exception (docs/conventions.md,"
            " Repository files)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
