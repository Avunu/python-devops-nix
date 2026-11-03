"""GitHub through the ``gh`` CLI, with HTTP status codes and one seam tests replace.

The CI steps and the ``repo`` group need to tell a 403 from a 404 from a 422 (rulesets
on a private repository, a missing ref, a refused bypass actor), and to run the ``gh``
subcommands that are not ``gh api`` (``gh pr merge --auto``, ``gh workflow run``,
``gh label create``).

Everything goes through two module-level functions, ``request`` and ``cli``. Tests swap
``Transport`` for a fake (``use(fake)``), so no test needs the network or ``gh``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from standards_core.common.report import EnvError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from standards_core.product import Product

# gh prints "gh: Not Found (HTTP 404)" on stderr for a failed API call.
_STATUS = re.compile(r"\(HTTP (\d{3})\)")

# GitHub Actions' App id: the `integration_id` of every required gate context and the
# default bypass actor of the release-branch ruleset.
ACTIONS_APP_ID = 15368


class GitHubError(EnvError):
    """A GitHub call that failed; ``status`` is the HTTP status when gh reported one."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class Result:
    """A finished ``gh`` subcommand."""

    code: int
    stdout: str = ""
    stderr: str = ""


class Transport:
    """The real transport: ``gh`` on PATH, with the token it already holds (``GH_TOKEN``)."""

    def request(
        self, method: str, endpoint: str, body: Any = None, paginate: bool = False
    ) -> tuple[int, Any]:
        cmd = ["gh", "api", "--method", method, endpoint]
        if paginate:
            cmd += ["--paginate", "--slurp"]
        stdin = None
        if body is not None:
            cmd += ["--input", "-"]
            stdin = json.dumps(body)
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, check=False
            )
        except FileNotFoundError as e:
            raise EnvError("gh is not on PATH") from e
        if proc.returncode != 0:
            m = _STATUS.search(proc.stderr)
            status = int(m[1]) if m else None
            raise GitHubError(
                f"gh api {method} {endpoint}: {proc.stderr.strip() or proc.returncode}",
                status,
                proc.stdout,
            )
        if not proc.stdout.strip():
            return 204, None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise GitHubError(
                f"gh api {method} {endpoint}: the response is not JSON: {e}"
            ) from e
        if (
            paginate
            and isinstance(data, list)
            and all(isinstance(page, list) for page in data)
        ):
            data = [item for page in data for item in page]
        return 200, data

    def cli(self, *args: str) -> Result:
        try:
            proc = subprocess.run(
                ["gh", *args], capture_output=True, text=True, check=False
            )
        except FileNotFoundError as e:
            raise EnvError("gh is not on PATH") from e
        return Result(proc.returncode, proc.stdout, proc.stderr)


_transport: Transport = Transport()


def use(transport: Transport) -> Transport:
    """Replace the transport (tests); returns the previous one, to restore."""
    global _transport
    previous, _transport = _transport, transport
    return previous


def request(
    method: str, endpoint: str, body: Any = None, *, paginate: bool = False
) -> Any:
    """The decoded JSON of a REST call (``None`` when empty); a failure is a ``GitHubError``."""
    return _transport.request(method, endpoint, body, paginate)[1]


def get(endpoint: str, *, paginate: bool = False) -> Any:
    return request("GET", endpoint, paginate=paginate)


def get_or_none(endpoint: str) -> Any:
    """``GET endpoint``, or ``None`` on a 404."""
    try:
        return get(endpoint)
    except GitHubError as e:
        if e.status == 404:
            return None
        raise


def cli(*args: str) -> Result:
    """A ``gh`` subcommand (``pr merge``, ``workflow run``, ``label create``); never raises on its exit."""
    return _transport.cli(*args)


def required_contexts(repo: str, branch: str) -> tuple[set[str], bool]:
    """The status-check contexts required on ``branch``, from its rulesets and its classic protection.

    Returns ``(contexts, known)``. ``GET /repos/{r}/rules/branches/{b}`` (the rules that apply
    to the branch, every ruleset merged) is readable with ``contents: read``; so is the
    ``protection`` summary of ``GET /repos/{r}/branches/{b}``. ``known`` is false when neither
    could be read, which the callers treat as "not protected"."""
    contexts: set[str] = set()
    known = False
    try:
        rules = get(f"repos/{repo}/rules/branches/{branch}", paginate=True) or []
        known = True
        for rule in rules:
            if rule.get("type") == "required_status_checks":
                parameters = rule.get("parameters") or {}
                for check in parameters.get("required_status_checks", []):
                    if check.get("context"):
                        contexts.add(check["context"])
    except GitHubError:
        pass
    try:
        info = get(f"repos/{repo}/branches/{branch}") or {}
        known = True
        checks = (info.get("protection") or {}).get("required_status_checks") or {}
        contexts.update(c for c in checks.get("contexts", []) if c)
        contexts.update(
            c.get("context") for c in checks.get("checks", []) if c.get("context")
        )
    except GitHubError:
        pass
    return contexts, known


def gate_contexts(product: Product, gates: Sequence[str]) -> list[str]:
    """The required context of each gate: ``product.ci.check_context`` with ``{gate}`` filled."""
    return [product.ci.check_context.format(gate=g) for g in gates]


def gates_protected(
    product: Product, repo: str, branch: str, gates: Sequence[str]
) -> bool:
    """Whether every enabled gate's context is required on ``branch`` (the auto-merge safety rule)."""
    contexts, known = required_contexts(repo, branch)
    wanted = set(gate_contexts(product, gates))
    return known and bool(wanted) and wanted <= contexts


# A check run counts as passed with these conclusions, the ones GitHub's required checks
# accept; it failed with these. Anything else (queued, in progress, no conclusion yet)
# is pending.
GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
FAILED_CONCLUSIONS = frozenset(
    {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
)


def _check_runs(repo: str, sha: str) -> list[dict]:
    data = get(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", paginate=True)
    pages = data if isinstance(data, list) else [data]
    runs: list[dict] = []
    for page in pages:
        if isinstance(page, dict):
            runs += [r for r in page.get("check_runs") or [] if isinstance(r, dict)]
        elif isinstance(page, list):
            runs += [r for r in page if isinstance(r, dict)]
    return runs


def _run_order(run: dict) -> tuple[bool, str, int]:
    """A check run's place among the attempts of one name: a run with no
    ``started_at`` (a queued re-run) after every started one, then by start, then id."""
    started = run.get("started_at") or ""
    run_id = run.get("id")
    return (not started, started, run_id if isinstance(run_id, int) else 0)


def _statuses(repo: str, sha: str) -> dict[str, dict]:
    """The commit statuses on ``sha`` by context, from every page of the combined
    status (none on a 404)."""
    try:
        data = get(f"repos/{repo}/commits/{sha}/status?per_page=100", paginate=True)
    except GitHubError as e:
        if e.status == 404:
            return {}
        raise
    pages = data if isinstance(data, list) else [data]
    out: dict[str, dict] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        for status in page.get("statuses") or []:
            if isinstance(status, dict) and status.get("context"):
                # The combined status lists the newest status per context; keep the
                # first page's should a context repeat.
                out.setdefault(status["context"], status)
    return out


def _run_state(run: dict) -> str:
    if run.get("status") != "completed":
        return "pending"
    conclusion = run.get("conclusion") or ""
    if conclusion in GREEN_CONCLUSIONS:
        return "green"
    if conclusion in FAILED_CONCLUSIONS:
        return "failed"
    return "pending"


def _status_state(status: dict) -> str:
    state = status.get("state") or ""
    if state == "success":
        return "green"
    if state in ("failure", "error"):
        return "failed"
    return "pending"


def check_state(
    repo: str, sha: str, contexts: Iterable[str]
) -> Literal["green", "pending", "failed"]:
    """Whether the required ``contexts`` passed on ``sha`` (DESIGN §8.2).

    Each context is read from its newest check run (highest ``started_at``, then ``id``;
    a run not started yet is newer than any started one), so a re-run replaces a failed
    attempt; a commit status counts for a context no check run names. Every context
    green is ``green``; any failed is ``failed``; anything else (missing, queued, in
    progress, a pending status) is ``pending``.
    """
    newest: dict[str, dict] = {}
    for run in _check_runs(repo, sha):
        name = run.get("name") or ""
        have = newest.get(name)
        if have is None or _run_order(run) > _run_order(have):
            newest[name] = run
    statuses = _statuses(repo, sha)
    states = []
    for context in contexts:
        if context in newest:
            states.append(_run_state(newest[context]))
        elif context in statuses:
            states.append(_status_state(statuses[context]))
        else:
            states.append("pending")
    if "failed" in states:
        return "failed"
    if all(s == "green" for s in states):
        return "green"
    return "pending"
