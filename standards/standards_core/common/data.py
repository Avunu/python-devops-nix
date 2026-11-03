"""Data files: the product's data root first, then the core's (DESIGN §4.6).

A product ships its schemas, profiles, templates and manifest fragments under its own
``data/`` directory; the core ships what every product shares under
``standards_core/data/``. A relative path is looked up in the product's root, then in
the core's, so a product can replace any core file by shipping one of the same name.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from standards_core.common.report import ConfigError

if TYPE_CHECKING:
    from standards_core.product import Product


def _parts(rel: str) -> tuple[str, ...]:
    """``rel``'s parts; a path that is absolute, empty or leaves a root is refused."""
    pure = PurePosixPath(rel)
    if not rel or pure.is_absolute() or ".." in pure.parts:
        raise ConfigError(
            f"data path must be relative to a data root and stay inside it: {rel!r}"
        )
    return pure.parts


def _on_disk(root: Path, rel: str) -> Path:
    if not isinstance(root, Path):
        # A zipped install: there is no file to hand to another program.
        raise ConfigError(
            f"{root} is not installed as files on disk, so {rel!r} has no path"
        )
    return root


@dataclass(frozen=True)
class DataRoots:
    """The data directories a product reads, the product's first."""

    roots: tuple[Path, ...]  # (product.data_root, core data root)

    def find(self, rel: str) -> Path | None:
        """The first root's ``rel``, file or directory; ``None`` when no root has it."""
        parts = _parts(rel)
        for root in self.roots:
            path = _on_disk(root, rel).joinpath(*parts)
            if path.exists():
                return path.resolve()
        return None

    def path(self, rel: str) -> Path:
        """``find(rel)``, or a ``ConfigError`` when no root has it."""
        found = self.find(rel)
        if found is None:
            raise ConfigError(f"no such data file: {rel}")
        return found

    def listdir(self, rel: str) -> dict[str, Path]:
        """The entries of directory ``rel`` across the roots, by file name, sorted.

        A product file shadows the core file of the same name.
        """
        parts = _parts(rel)
        out: dict[str, Path] = {}
        for root in reversed(self.roots):
            directory = _on_disk(root, rel).joinpath(*parts)
            if directory.is_dir():
                for entry in directory.iterdir():
                    out[entry.name] = entry.resolve()
        return dict(sorted(out.items()))


@cache
def product_roots(product: Product) -> DataRoots:
    """The product's ``DataRoots``, cached per product."""
    return DataRoots(product.roots)


def data_path(product: Product, rel: str) -> Path:
    """The absolute path of data file or directory ``rel``: the product's, else the core's."""
    return product_roots(product).path(rel)
