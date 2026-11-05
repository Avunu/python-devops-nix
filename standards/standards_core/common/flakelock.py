"""Reading ``flake.lock``: nodes, ``follows`` paths and the pins they lock (DESIGN §9.1).

A pin is a ``github``, ``gitlab`` or ``git`` lock node. A checkout reaches its engine's
own pinned inputs through its lock's engine node (``product.engine.flake_input``), so a
name is looked up first among the root's inputs and then among the engine's. The
engine's pinned inputs (``product.pins.engine_pinned``) are the exception: CI runs
checks and rules from them, so they are read only through the engine node (or the root
of the engine's own lock) and must name the repository the engine pins. A lock that
retargets one, or shadows it with a root input of the same name, is a ``ConfigError``
rather than a different set of rules. ``standards-profile`` is the other way round: only
the checkout's own root input counts.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from standards_core.common.report import ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.product import Product

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# A GitLab owner may be a group path, which a flake URL spells with %2F.
_GITLAB_OWNER = re.compile(r"[A-Za-z0-9_.-]+(?:(?:/|%2F)[A-Za-z0-9_.-]+)*")
_HOST = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]+)?")
_REV = re.compile(r"[0-9a-f]{40}")

# The org profile input: read only from the checkout's own root.
PROFILE_INPUT = "standards-profile"

PIN_TYPES = ("github", "gitlab", "git")


@dataclass(frozen=True)
class Pin:
    """A locked ``github``, ``gitlab`` or ``git`` input.

    ``owner``/``repo`` name it on GitHub or GitLab (``owner`` may be a GitLab group path);
    ``host`` is the GitHub or GitLab host (``github.com`` or ``gitlab.com`` by default; a
    GitHub Enterprise or self-hosted GitLab otherwise); ``url`` is a ``git`` input's clone
    URL. ``name`` is the directory stem under the pins directory.
    """

    input: str
    type: str
    rev: str
    nar_hash: str
    owner: str = ""
    repo: str = ""
    host: str = ""
    url: str = ""

    @property
    def name(self) -> str:
        if self.type == "git":
            return (
                self.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "source"
            )
        return self.repo

    @property
    def label(self) -> str:
        """How messages name the source: ``owner/repo`` or the clone URL."""
        if self.type == "git":
            return self.url
        return f"{self.owner.replace('%2F', '/')}/{self.repo}"

    @property
    def tarball_url(self) -> str:
        """Where a ``github`` or ``gitlab`` pin's tarball is, on the pin's own host.

        A GitHub Enterprise host serves it from its REST API (``/api/v3``), as Nix's own
        ``github`` fetcher does, so the token goes to the host it was issued for.
        """
        if self.type == "github" and self.host.lower() in ("", "github.com"):
            return f"https://codeload.github.com/{self.owner}/{self.repo}/tar.gz/{self.rev}"
        if self.type == "github":
            return (
                f"https://{self.host}/api/v3/repos/{self.owner}/{self.repo}"
                f"/tarball/{self.rev}"
            )
        if self.type == "gitlab":
            project = urllib.parse.quote(
                f"{self.owner.replace('%2F', '/')}/{self.repo}", safe=""
            )
            return (
                f"https://{self.host}/api/v4/projects/{project}"
                f"/repository/archive.tar.gz?sha={self.rev}"
            )
        raise ValueError(f"a {self.type} pin has no tarball")


def parse(text: str, label: str) -> dict:
    """A lock file's text parsed (``label`` names it in errors), as ``load`` checks it."""
    try:
        lock = json.loads(text)
    except json.JSONDecodeError as e:
        raise EnvError(f"{label} is unreadable: {e}") from e
    if (
        not isinstance(lock, dict)
        or not isinstance(lock.get("nodes"), dict)
        or not isinstance(lock.get("root"), str)
        or not all(isinstance(node, dict) for node in lock["nodes"].values())
    ):
        raise EnvError(f"{label} is not a flake lock file")
    return lock


def load(path: Path) -> dict:
    """The parsed lock file; unreadable or not a version-7 lock is an ``EnvError``."""
    try:
        text = path.read_text()
    except FileNotFoundError as e:
        raise EnvError(f"{path} does not exist") from e
    except OSError as e:
        raise EnvError(f"{path} is unreadable: {e}") from e
    return parse(text, str(path))


def _node(lock: dict, name: object) -> dict:
    node = lock["nodes"].get(name) if isinstance(name, str) else None
    if not isinstance(node, dict):
        raise EnvError(
            f"not a flake lock file: it refers to a node {name!r} it does not have"
        )
    return node


def node_at(lock: dict, path: list[str]) -> tuple[str, dict] | None:
    """Follow input names from the root node; ``None`` when the path does not exist.

    Each step's reference is either a node name or, for ``follows``, a path from the root.
    """
    name = lock["root"]
    for step in path:
        inputs = _node(lock, name).get("inputs", {})
        if not isinstance(inputs, dict):
            raise EnvError(
                f"not a flake lock file: node {name!r} has inputs that are not a table"
            )
        ref = inputs.get(step)
        if ref is None:
            return None
        if isinstance(ref, list):
            found = node_at(lock, ref)
            if found is None:
                return None
            name = found[0]
        else:
            name = ref
    return name, _node(lock, name)


def input_node(product: Product, lock: dict, name: str) -> tuple[str, dict] | None:
    """The node for input ``name``: the root's own, else the engine's.

    An engine-pinned input is the engine's only: in a checkout's lock (one with an engine
    input) the root's input of that name is never consulted. And ``standards-profile`` is
    the checkout's only: the engine's is never consulted.
    """
    engine = product.engine.flake_input
    if name in product.pins.engine_pinned and node_at(lock, [engine]) is not None:
        return node_at(lock, [engine, name])
    if name == PROFILE_INPUT:
        return node_at(lock, [name])
    return node_at(lock, [name]) or node_at(lock, [engine, name])


def _rev_at(lock: dict, path: list[str]) -> str | None:
    found = node_at(lock, path)
    locked = found[1].get("locked") if found else None
    rev = locked.get("rev") if isinstance(locked, dict) else None
    return rev if isinstance(rev, str) else None


def engine_rev(product: Product, lock: dict) -> str | None:
    """The engine revision a checkout locks, or ``None`` (the engine itself has no such input)."""
    return _rev_at(lock, [product.engine.flake_input])


def base_rev(product: Product, lock: dict) -> str | None:
    """The revision of the engine's ``base_input`` (the one that locks this core), or ``None``."""
    return _rev_at(lock, [product.engine.flake_input, product.engine.base_input])


def _field(name: str, locked: dict, key: str) -> str:
    value = locked.get(key)
    if not isinstance(value, str):
        raise EnvError(f"input {name!r} has no locked {key}")
    return value


def locked_pin(product: Product, lock: dict, name: str) -> Pin:
    """The locked coordinates of input ``name``: a ``github``, ``gitlab`` or ``git`` node."""
    engine = product.engine.flake_input
    found = input_node(product, lock, name)
    if found is None:
        raise ConfigError(
            f"flake.lock has no input {name!r}, neither the checkout's nor {engine}'s"
        )
    locked = found[1].get("locked", {})
    kind = locked.get("type") if isinstance(locked, dict) else None
    if not isinstance(locked, dict) or kind not in PIN_TYPES:
        raise ConfigError(
            f"input {name!r} is locked as {kind!r}, not a github, gitlab or git input"
        )
    rev = _field(name, locked, "rev")
    nar_hash = _field(name, locked, "narHash")
    if not _REV.fullmatch(rev):
        raise ConfigError(
            f"input {name!r} locks rev {rev!r}, which is not a commit SHA"
        )
    if kind == "git":
        url = _field(name, locked, "url")
        parts = urllib.parse.urlsplit(url.removeprefix("git+"))
        if parts.scheme not in ("https", "http", "ssh", "file") or not parts.path.strip(
            "/"
        ):
            raise ConfigError(
                f"input {name!r} locks url {url!r}, which is not a git URL"
            )
        pin = Pin(name, kind, rev, nar_hash, url=url.removeprefix("git+"))
        if not _NAME.fullmatch(pin.name) or pin.name.startswith("."):
            raise ConfigError(
                f"input {name!r} locks url {url!r}, whose last part is not a repository name"
            )
        return pin
    owner = _field(name, locked, "owner")
    repo = _field(name, locked, "repo")
    host = locked.get("host") or ("gitlab.com" if kind == "gitlab" else "github.com")
    # These become a URL and a directory name under the pins directory, so a crafted lock
    # must not be able to leave it.
    owner_ok = (_GITLAB_OWNER if kind == "gitlab" else _NAME).fullmatch(owner)
    if not owner_ok or owner.startswith("."):
        raise ConfigError(
            f"input {name!r} locks owner {owner!r}, which is not a {kind} name"
        )
    if not _NAME.fullmatch(repo) or repo.startswith("."):
        raise ConfigError(
            f"input {name!r} locks repo {repo!r}, which is not a {kind} name"
        )
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ConfigError(
            f"input {name!r} locks host {host!r}, which is not a host name"
        )
    expected = product.pins.engine_pinned.get(name)
    # GitHub names are case-insensitive, and a flake URL may spell them either way.
    if expected and (
        kind != "github"
        or host.lower() != "github.com"
        or (owner.lower(), repo.lower()) != tuple(p.lower() for p in expected)
    ):
        where = (
            f"{kind}:{owner}/{repo}"
            if host.lower() in ("github.com", "gitlab.com")
            else f"{kind}:{owner}/{repo} on {host}"
        )
        raise ConfigError(
            f"input {name!r} locks {where}, but {engine} pins it to"
            f" github:{'/'.join(expected)}"
        )
    return Pin(name, kind, rev, nar_hash, owner=owner, repo=repo, host=host)


def locked_rev(product: Product, lock: dict, name: str) -> str | None:
    """The locked revision of input ``name`` (the checkout's own, else the engine's), or ``None``."""
    found = input_node(product, lock, name)
    locked = found[1].get("locked") if found else None
    rev = locked.get("rev") if isinstance(locked, dict) else None
    return rev if isinstance(rev, str) else None
