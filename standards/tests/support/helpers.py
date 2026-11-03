"""A small tree whose NAR hash Nix computed."""

from pathlib import Path

# `nix hash path --type sha256 --sri` of the tree make_tree() builds.
TREE_NAR_HASH = "sha256-WnQ6pRq2vaNuq7WoQLwf1j4T6Ggr+DoCIXhN1ChYXmI="


def make_tree(root: Path) -> Path:
    (root / "sub").mkdir(parents=True)
    (root / "a").write_text("hello\n")
    (root / "sub" / "x").write_text("#!/bin/sh\n")
    (root / "sub" / "x").chmod(0o755)
    (root / "a").chmod(0o644)
    (root / "sub" / "link").symlink_to("../a")
    (root / "empty").write_text("")
    (root / "emptydir").mkdir()
    return root


def product_copy(product, root: Path, **changes):
    """``product`` with its data root copied under ``root`` (and ``changes``): a test may
    edit the copy's data, and the copy has caches of its own."""
    import dataclasses
    import shutil

    data = root / "data"
    shutil.copytree(product.data_root, data)
    return dataclasses.replace(product, data_root=data, **changes)
