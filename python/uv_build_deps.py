"""Keep pyproject.toml's [tool.uv.extra-build-dependencies] in step with uv.lock.

A package the lock has no wheel for builds from its sdist, and an old sdist
often predates PEP 517 and never declares setuptools, so uv2nix cannot build it
in the Nix sandbox. Rather than keep that list by hand, every wheel-less
package in uv.lock is granted setuptools as a build dependency. That is
harmless for a package that does declare a backend: extra-build-dependencies
only adds.

The entries live in a block between two sentinel comments, so an update is a
text edit that keeps the rest of the file as it is:

    [tool.uv.extra-build-dependencies]
    # >>> <label>: sdist build deps (managed) >>>
    "docopt" = ["setuptools"]
    # <<< <label>: sdist build deps (managed) <<<

`always` names packages the block lists whatever the lock says, each with its
own build dependencies: one that is sdist-only on some platform or version and
not others, or one whose backend is not setuptools. An entry in `always`
replaces the derived one.

odoo-nix's lib/uv_build_deps.py, with the product's label and its `always`
passed in. Standard library only, written to 3.11.

Usage:
    <command> update [pyproject.toml] [uv.lock]   rewrite the managed block
    <command> check  [pyproject.toml] [uv.lock]   exit 1 if it is out of date
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

SETUPTOOLS = ("setuptools",)


def markers(label: str) -> tuple[str, str]:
    """The sentinel lines around the managed block."""
    return (
        f"# >>> {label}: sdist build deps (managed) >>>",
        f"# <<< {label}: sdist build deps (managed) <<<",
    )


def sdist_only(lock: Mapping, exclude: str | None = None) -> list[str]:
    """Names of the packages in `lock` (a parsed uv.lock) with no wheel.

    The workspace's virtual root and its editable members are left out: uv
    builds neither from an sdist. So is `exclude`, the root project's name.
    """
    names = []
    for package in lock.get("package", []):
        name = package.get("name")
        if not name or name == exclude or package.get("wheels"):
            continue
        source = package.get("source", {})
        if isinstance(source, dict) and (
            source.get("virtual") or source.get("editable")
        ):
            continue
        names.append(name)
    return names


def derive(
    lock: Mapping,
    exclude: str | None = None,
    always: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """The managed entries, `{ package: [build dependency, ...] }`, by name."""
    entries = {name: list(SETUPTOOLS) for name in sdist_only(lock, exclude)}
    entries.update({name: list(deps) for name, deps in (always or {}).items()})
    return dict(sorted(entries.items()))


def render(entries: Mapping[str, Sequence[str]], label: str, pad: str = "") -> str:
    """The managed block, sentinels included, each line indented by `pad`."""
    begin, end = markers(label)
    lines = [pad + begin]
    for name, deps in entries.items():
        quoted = ", ".join(f'"{dep}"' for dep in deps)
        lines.append(f'{pad}"{name}" = [{quoted}]')
    lines.append(pad + end)
    return "\n".join(lines)


def sync(text: str, entries: Mapping[str, Sequence[str]], label: str) -> str | None:
    """`text` with its managed block replaced by `entries`, or None if it has none."""
    begin, end = markers(label)
    found = re.search(
        r"^([ \t]*)" + re.escape(begin) + r".*?^[ \t]*" + re.escape(end) + r"[ \t]*$",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if found is None:
        return None
    block = render(entries, label, found.group(1))
    return text[: found.start()] + block + text[found.end() :]


def plan(
    pyproject: Path,
    lockfile: Path,
    label: str,
    always: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, str | None, int]:
    """The file's text, its synced text (None without a block), and the entry count."""
    text = pyproject.read_text(encoding="utf-8")
    root = tomllib.loads(text).get("project", {}).get("name")
    with lockfile.open("rb") as handle:
        entries = derive(tomllib.load(handle), root, always)
    return text, sync(text, entries, label), len(entries)


def main(
    argv: Sequence[str],
    label: str,
    always: Mapping[str, Sequence[str]] | None = None,
    prog: str = "uv_build_deps",
) -> int:
    usage = f"usage: {prog} {{update,check}} [pyproject.toml] [uv.lock]\n"
    if not argv or argv[0] not in ("update", "check") or len(argv) > 3:
        sys.stderr.write(usage)
        return 2
    mode = argv[0]
    pyproject = Path(argv[1] if len(argv) > 1 else "pyproject.toml")
    lockfile = Path(argv[2] if len(argv) > 2 else "uv.lock")
    text, synced, count = plan(pyproject, lockfile, label, always)
    if synced is None:
        begin, _ = markers(label)
        sys.stderr.write(
            f"{label}: no managed block ({begin}) in {pyproject}; "
            "build dependencies not synced\n"
        )
        return 1
    if mode == "check":
        if synced != text:
            sys.stderr.write(
                f"{label}: [tool.uv.extra-build-dependencies] in {pyproject} is out of date "
                f"with {lockfile}; run `{prog} update`\n"
            )
            return 1
        return 0
    if synced != text:
        pyproject.write_text(synced, encoding="utf-8")
    sys.stderr.write(f"{label}: synced {count} sdist build-dep entr(ies)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[2:], label=sys.argv[1]) if len(sys.argv) > 1 else 2)
