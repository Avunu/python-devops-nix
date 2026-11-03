"""Helpers the core's tests share: the stub products, a NAR-hashed tree, fakes."""

from kit_nix_tools.product import PRODUCT as KIT
from stub_nix_tools.product import PRODUCT as STUB

from standards_core.testing.fakes import FakeGitHub, git, run_cli
from support.helpers import TREE_NAR_HASH, make_tree, product_copy

__all__ = [
    "KIT",
    "STUB",
    "TREE_NAR_HASH",
    "FakeGitHub",
    "git",
    "make_tree",
    "product_copy",
    "run_cli",
]
