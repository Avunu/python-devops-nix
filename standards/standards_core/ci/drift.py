"""The nightly ``drift`` reminders (DESIGN §8.4).

The engine and an org profile never move by Dependabot: their bump can rewrite workflow
files, which ``GITHUB_TOKEN`` cannot push. Their reminders are two issues the nightly
job keeps current:

- "<engine> vX.Y.Z available" while the version the callers pin (their ``# vX.Y.Z``
  comment) is older than the latest release, with the drift summary and the command
  ``<cli> repo rollout --to vX.Y.Z --repo <repo>``; closed once it is current;
- "standards profile update available" while the locked ``standards-profile`` rev
  differs from the head of the ref ``profile`` names, with ``<cli> repo rollout
  --profile-to latest --repo <repo>``; closed once it is current.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

from standards_core.common import github
from standards_core.common.report import ConfigError
from standards_core.scaffold import defaults

if TYPE_CHECKING:
    from standards_core.product import Product

PROFILE_TITLE = "standards profile update available"


def engine_title(product: Product) -> re.Pattern[str]:
    """The engine reminder's title, whatever version it names."""
    return re.compile(rf"^{re.escape(product.engine.flake_input)} v\S+ available$")


def ls_remote(url: str, ref: str) -> str:
    """The commit ``ref`` names on ``url`` (empty when it can't be read)."""
    proc = subprocess.run(
        ["git", "ls-remote", url, ref], capture_output=True, text=True, check=False
    )
    for line in proc.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}", "HEAD"):
            return sha
    return proc.stdout.split("\t", 1)[0].strip() if proc.stdout.strip() else ""


def profile_remote(lock: dict) -> tuple[str, str, str] | None:
    """``(url, ref, locked rev)`` of the root input ``standards-profile``, when there is
    one."""
    nodes = lock.get("nodes", {})
    name = (
        nodes.get(lock.get("root", "root"), {})
        .get("inputs", {})
        .get("standards-profile")
    )
    if not isinstance(name, str) or name not in nodes:
        return None
    node = nodes[name]
    original, locked = node.get("original", {}), node.get("locked", {})
    kind = original.get("type")
    if kind == "github":
        host = original.get("host", "github.com")
        url = f"https://{host}/{original['owner']}/{original['repo']}"
    elif kind == "gitlab":
        host = original.get("host", "gitlab.com")
        url = f"https://{host}/{original['owner']}/{original['repo']}"
    elif kind == "git":
        url = original.get("url", "")
    else:
        return None
    return url, original.get("ref") or "HEAD", locked.get("rev", "")


def _issues(repo: str) -> list[dict]:
    items = (
        github.get(f"repos/{repo}/issues?state=open&per_page=100", paginate=True) or []
    )
    return [i for i in items if "pull_request" not in i]


def keep_issue(
    repo: str, match: Callable[[str], object], title: str, body: str | None
) -> str:
    """Open or update the issue ``match`` finds with ``title``/``body``; ``body=None``
    closes it."""
    found = [i for i in _issues(repo) if match(i.get("title", ""))]
    if body is None:
        for issue in found:
            github.request(
                "PATCH", f"repos/{repo}/issues/{issue['number']}", {"state": "closed"}
            )
        return "closed" if found else "none"
    if found:
        github.request(
            "PATCH",
            f"repos/{repo}/issues/{found[0]['number']}",
            {"title": title, "body": body},
        )
        return "updated"
    github.request("POST", f"repos/{repo}/issues", {"title": title, "body": body})
    return "opened"


def _version(text: str) -> Version | None:
    try:
        return Version(text.removeprefix("v"))
    except InvalidVersion:
        return None


def run(
    product: Product,
    root: Path,
    repo: str,
    latest: str,
    summary: str = "",
    check_profile: bool = True,
) -> dict[str, str]:
    out: dict[str, str] = {}
    engine = product.engine.flake_input
    cli = product.identity.cli
    want = _version(latest)
    if want is None:
        raise ConfigError(f"--latest {latest!r} is not a version")
    title = engine_title(product)
    pinned = defaults.head_version(product, root)
    if pinned is not None and pinned < want:
        tag = f"v{want}"
        quoted = (
            [
                "",
                "Drift against the latest release:",
                "",
                "```",
                summary.rstrip(),
                "```",
            ]
            if summary.strip()
            else []
        )
        body = "\n".join(
            [
                f"This checkout's workflows pin {engine} v{pinned}; v{want} is released.",
                "",
                (
                    "Roll it out (it rewrites the caller workflows, so it needs a"
                    " token with the `workflow` scope):"
                ),
                "",
                "```",
                f"{cli} repo rollout --to {tag} --repo {repo}",
                "```",
                *quoted,
            ]
        )
        out[engine] = keep_issue(repo, title.match, f"{engine} {tag} available", body)
    else:
        out[engine] = keep_issue(repo, title.match, "", None)
    if check_profile:
        try:
            lock = json.loads((root / "flake.lock").read_text())
        except (OSError, json.JSONDecodeError):
            lock = {}
        remote = profile_remote(lock)
        if remote is not None:
            url, ref, rev = remote
            head = ls_remote(url, ref)

            def match(t: str) -> bool:
                return t == PROFILE_TITLE

            if head and rev and head != rev:
                body = "\n".join(
                    [
                        (
                            f"The locked standards profile is {rev[:12]}; {ref} of"
                            f" {url} is at {head[:12]}."
                        ),
                        "",
                        "```",
                        f"{cli} repo rollout --profile-to latest --repo {repo}",
                        "```",
                    ]
                )
                out["profile"] = keep_issue(repo, match, PROFILE_TITLE, body)
            elif head:
                out["profile"] = keep_issue(repo, match, PROFILE_TITLE, None)
    return out
