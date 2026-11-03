"""``<cli> ci <step>``: the decisions a product's workflows delegate (DESIGN §8).

The workflows only set up the job and call these steps; see ``standards_core.ci`` for
each. ``--repo`` defaults to ``$GITHUB_REPOSITORY``; the GitHub steps use the token
``gh`` holds (``GH_TOKEN``). Where a step's ``--gates`` or release configuration is
omitted, it is read from the checkout in the current directory, as sync renders it.

=====================  ==========================================  =====================
step                   prints                                      ``$GITHUB_OUTPUT``
=====================  ==========================================  =====================
``verdict``            ``auto`` | ``review`` | ``relock`` |        ``verdict``
                       ``close``
``automerge``          what it did                                 ``result``
``enable-automerge``   what it did                                 ``result``
``relock-inputs``      ``ok`` | ``rollout-input-moved``            ``state``, ``moved``
``relock-patch``       ``empty=true|false``                        ``empty``
``refuse-workflows``   the workflow paths (exit 1) or nothing      —
``dispatch-ci``        each dispatch                               ``dispatched``
``sweep``              each dispatch                               ``dispatched``
``update-branches``    what it did to each bot PR that was behind  ``updated``
``fast-forward``       ``<branch> = [<tag> = ]<sha>``              ``moved``
``bump``               each unit's new version                     ``bumped``, ``sha``
``drift``              one line per issue                          —
=====================  ==========================================  =====================

``--set KEY=JSON`` (repeatable) overrides a dotted configuration key for the step;
``--param KEY=JSON`` (repeatable) gives a decision-table parameter, a JSON list of
strings. A product's old flag names stay accepted as aliases (``ci.arg_aliases``).

Exit codes: 0, or 1 when a refusal or a guard fails; 2 for a bad argument; 3 when
GitHub or git cannot be reached.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from standards_core.commands import help_text
from standards_core.common import pyproject, repo, repoconfig
from standards_core.common.report import CLEAN, DRIFT, ConfigError, EnvError

if TYPE_CHECKING:
    from standards_core.common.repoconfig import RepoConfig
    from standards_core.product import Product

# What `git push` prints when the remote branch moved (DESIGN §15.CR6).
_MOVED = ("non-fast-forward", "fetch first", "[rejected]")


def _repo(args: argparse.Namespace) -> str:
    repo_name = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_name or "/" not in repo_name:
        raise ConfigError("give --repo <owner>/<repo> (or set GITHUB_REPOSITORY)")
    return repo_name


def _csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _checkout(product: Product) -> RepoConfig:
    """The checkout in the current directory, resolved as sync resolves it."""
    return repoconfig.load(product, pyproject.checkout_root(product))


def _gates(args: argparse.Namespace) -> list[str]:
    if args.gates is not None:
        return _csv(args.gates)
    return _checkout(args.product).gates


def _pairs(values: list[str] | None, flag: str) -> dict[str, Any]:
    """``KEY=JSON`` items as a mapping."""
    out: dict[str, Any] = {}
    for item in values or []:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"{flag} {item!r}: give <key>=<JSON>")
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{flag} {key.strip()}: not JSON: {e}") from e
    return out


def params(args: argparse.Namespace) -> dict[str, list[str]]:
    """The decision-table parameters: each a JSON list of strings."""
    out = {}
    for key, value in _pairs(getattr(args, "param", None), "--param").items():
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"--param {key} must be a JSON list of strings")
        out[key] = value
    return out


def with_sets(cfg: dict, args: argparse.Namespace) -> dict:
    """``cfg`` with the step's ``--set`` overrides (dotted keys) applied, as a copy."""
    out = copy.deepcopy(dict(cfg))
    for dotted, value in _pairs(getattr(args, "set", None), "--set").items():
        parts = dotted.split(".")
        table = out
        for part in parts[:-1]:
            nested = table.get(part)
            if not isinstance(nested, dict):
                nested = table[part] = {}
            table = nested
        table[parts[-1]] = value
    return out


def _decide(args: argparse.Namespace) -> str:
    from standards_core.ci import verdict

    return verdict.decide(
        args.product,
        args.ecosystem,
        update_type=args.update_type,
        group=args.dependency_group,
        dependency_names=args.dependency_names,
        directory=args.directory,
        params=params(args),
        engine_repo=args.engine_repo,
        policy=args.policy,
    )


def run_verdict(args: argparse.Namespace) -> int:
    from standards_core.ci import output

    v = _decide(args)
    print(v)
    output(verdict=v)
    return CLEAN


def run_automerge(args: argparse.Namespace) -> int:
    from standards_core.ci import automerge, output

    if args.verdict:
        decision = args.verdict
    elif args.ecosystem:
        decision = _decide(args)
    else:
        raise ConfigError(
            "give --verdict, or the fetch-metadata outputs (--ecosystem …)"
        )
    result = automerge.apply(
        args.product,
        decision,
        pr_url=args.pr_url,
        repo=_repo(args),
        integration=args.integration,
        gates=_gates(args),
        merge_method=args.merge_method,
        relock_state=args.relock,
        moved=_csv(args.moved),
    )
    output(result=result)
    return CLEAN


def run_enable(args: argparse.Namespace) -> int:
    from standards_core.ci import automerge, output

    result = automerge.enable(
        args.product,
        args.pr_url,
        _repo(args),
        args.integration,
        _gates(args),
        args.merge_method,
    )
    output(result=result)
    return CLEAN


def run_relock_inputs(args: argparse.Namespace) -> int:
    from standards_core.ci import output, relock

    product: Product = args.product
    root = Path.cwd()
    base = relock.merge_base(root, args.base)
    lock = root / "flake.lock"
    head_text = lock.read_text() if lock.is_file() else ""
    if not head_text:
        raise ConfigError("no flake.lock in the current directory")
    moved = relock.moved_inputs(product, relock.lock_at(root, base), head_text)
    state = "rollout-input-moved" if moved else "ok"
    print(state)
    if moved:
        print(
            f"moved: {', '.join(moved)} (these move through"
            f" {product.identity.cli} repo rollout)"
        )
    output(state=state, moved=",".join(moved))
    return CLEAN


def run_relock_patch(args: argparse.Namespace) -> int:
    from standards_core.ci import error, output, relock

    empty, refused = relock.make_patch(Path.cwd(), Path(args.out))
    if refused:
        error(
            f"relock would change a workflow ({', '.join(refused)}); run"
            f" {args.product.identity.cli} repo rollout"
        )
        return DRIFT
    print(f"empty={'true' if empty else 'false'}")
    output(empty="true" if empty else "false")
    return CLEAN


def run_refuse(args: argparse.Namespace) -> int:
    from standards_core.ci import error, relock

    if not args.cached and not args.base:
        raise ConfigError("give --base <ref>, or --cached")
    paths = relock.workflow_paths(relock.changed(Path.cwd(), args.base, args.cached))
    if paths:
        for p in paths:
            error(
                f"refusing to push a change to {p}: GITHUB_TOKEN cannot push workflow"
                " files"
            )
        return DRIFT
    return CLEAN


def _contexts(args: argparse.Namespace) -> tuple[tuple[str, str], ...]:
    if not args.context:
        return tuple(args.product.ci.contexts)
    out = []
    for pair in args.context:
        context, sep, workflow = pair.partition("=")
        if not sep or not context.strip() or not workflow.strip():
            raise ConfigError(f"--context {pair!r}: give <check name>=<workflow file>")
        out.append((context.strip(), workflow.strip()))
    return tuple(out)


def run_dispatch(args: argparse.Namespace) -> int:
    from standards_core.ci import output, release

    done = release.dispatch_ci(
        _repo(args), args.integration, args.package, _contexts(args)
    )
    output(dispatched=",".join(done))
    return CLEAN


def run_sweep(args: argparse.Namespace) -> int:
    from standards_core.ci import output, release

    done = release.sweep(_repo(args), _contexts(args))
    output(dispatched=",".join(done))
    return CLEAN


def run_update_branches(args: argparse.Namespace) -> int:
    from standards_core.ci import branches, output

    if args.limit < 1:
        raise ConfigError("--limit must be at least 1")
    done = branches.Updater(
        _repo(args),
        args.integration,
        dispatch_ci=args.dispatch_ci,
        contexts=_contexts(args),
        limit=args.limit,
    ).run()
    output(updated=",".join(f"{n}:{what}" for n, what in done.items()))
    return CLEAN


def run_fast_forward(args: argparse.Namespace) -> int:
    from standards_core.ci import conventional, output, release

    product: Product = args.product
    if not args.release_branch:
        raise ConfigError(
            "--release-branch is empty: this branching has no release branch"
        )
    # semver asks nothing of the checkout: with it named and the guard's inputs given,
    # the step runs outside a checkout (an engine repository's own release).
    loaded = None
    if args.version_scheme != "semver" or (not args.tag and not args.integration):
        loaded = _checkout(product)
    cfg = with_sets(loaded.cfg if loaded else {}, args)
    if args.tag_prefix is not None:
        cfg.setdefault("releases", {})["tag-prefix"] = args.tag_prefix
    facts = loaded.facts if loaded else {}
    name = args.version_scheme or cfg.get("releases", {}).get(
        "version-scheme", "semver"
    )
    scheme = conventional.scheme(product, name)
    integration = args.integration or cfg.get("integration-branch", "")
    if not args.tag and not integration:
        raise ConfigError("give --tag, or --integration for the required checks")
    ff = release.FastForward(
        repo=_repo(args),
        branch=args.release_branch,
        sha=args.sha,
        tag=args.tag,
        scheme=scheme,
        cfg=cfg,
        facts=facts,
        integration=integration,
        wait=args.wait_checks,
        dispatch_after=tuple(args.dispatch_after),
        dry_run=args.dry_run,
    )
    code = ff.run()
    output(moved=ff.moved)
    return code


def run_bump(args: argparse.Namespace) -> int:
    from standards_core.ci import bump, conventional, error, notice, output
    from standards_core.scaffold.context import NS, ns

    product: Product = args.product
    tool = product.release_tool
    if tool is None:
        raise ConfigError(
            f"{product.identity.name} has no release tool: ci bump has no units"
        )
    rc = _checkout(product)
    if not rc.modules.get("releases"):
        raise ConfigError("releases is off: ci bump has nothing to release")
    cfg = with_sets(rc.cfg, args)
    scheme = conventional.scheme(
        product, cfg.get("releases", {}).get("version-scheme", "semver")
    )
    integration = args.integration or cfg.get("integration-branch", "")
    ctx = NS(
        name=rc.name,
        cfg=ns(cfg),
        modules=ns(dict(rc.modules)),
        discover=ns(dict(rc.facts)),
        gates=list(rc.gates),
        branches=ns({"integration": integration, "release": rc.release_branch or None}),
    )
    head = repo.git(rc.root, "rev-parse", "HEAD").strip()
    bumps = bump.plan_bumps(rc.root, args.base, tool, scheme, ctx)
    for b in bumps:
        print(f"{b.unit.name}: {b.old} → {b.new} ({b.level}: {', '.join(b.commits)})")
    names = ",".join(b.unit.name for b in bumps)
    if not bumps:
        print("nothing to bump")
        output(bumped="", sha=head)
        return CLEAN
    if args.dry_run:
        print(f"dry run: would commit {bump.subject(tool, bumps)!r}")
        output(bumped=names, sha=head)
        return CLEAN
    sha = bump.apply_bumps(rc.root, bumps, tool)
    print(f"committed {sha}: {bump.subject(tool, bumps)}")
    if args.push:
        if not integration:
            raise ConfigError("give --integration: there is no branch to push to")
        try:
            repo.git(rc.root, "push", "origin", f"HEAD:refs/heads/{integration}")
        except EnvError as e:
            if any(word in str(e) for word in _MOVED):
                notice(
                    f"{integration} moved since {head[:12]}; the run for its new head"
                    " bumps"
                )
                output(bumped="", sha=head)
                return CLEAN
            error(f"could not push the bump to {integration}: {e}")
            output(bumped="", sha=head)
            return DRIFT
        print(f"pushed {sha[:12]} to {integration}")
    output(bumped=names, sha=sha)
    return CLEAN


def run_drift(args: argparse.Namespace) -> int:
    from standards_core.ci import drift

    summary = Path(args.summary_file).read_text() if args.summary_file else ""
    done = drift.run(
        args.product,
        Path.cwd(),
        _repo(args),
        args.latest,
        summary,
        check_profile=not args.no_profile,
    )
    for what, state in done.items():
        print(f"{what}: {state}")
    return CLEAN


# --- arguments ----------------------------------------------------------------------


class _Into(argparse.Action):
    """An alias that turns ``--old V`` into ``--param key=V`` or ``--set key=V``."""

    def __init__(self, option_strings, dest, *, into: str, key: str, **kwargs):
        super().__init__(option_strings, dest, **kwargs)
        self.into = into
        self.key = key

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, self.into, None) or [])
        items.append(f"{self.key}={values}")
        setattr(namespace, self.into, items)


class _Step:
    """Adds one step's flags, each with its ``ci <step> <flag>`` help key."""

    def __init__(self, product: Product, name: str, parser: argparse.ArgumentParser):
        self.product = product
        self.name = name
        self.parser = parser

    def add(self, flag: str, text: str, **kwargs: Any) -> None:
        kwargs["help"] = help_text(self.product, f"ci {self.name} {flag}", text)
        self.parser.add_argument(flag, **kwargs)

    def aliases(self) -> None:
        """``ci.arg_aliases`` on this step, for each target flag it has."""
        flags = self.parser._option_string_actions
        for old, new in self.product.ci.arg_aliases.items():
            target, _, key = new.partition(" ")
            if target not in flags or old in flags:
                continue
            text = help_text(self.product, f"ci {self.name} {old}", f"same as {new}")
            if key:
                self.parser.add_argument(
                    old,
                    action=_Into,
                    dest=f"alias{old.replace('-', '_')}",
                    into=flags[target].dest,
                    key=key,
                    metavar="VALUE",
                    default=argparse.SUPPRESS,
                    help=text,
                )
                continue
            action = flags[target]
            if action.nargs == 0:
                self.parser.add_argument(
                    old,
                    dest=action.dest,
                    action="store_const",
                    const=action.const,
                    default=argparse.SUPPRESS,
                    help=text,
                )
            else:
                self.parser.add_argument(
                    old,
                    dest=action.dest,
                    type=action.type,
                    choices=action.choices,
                    metavar=action.metavar,
                    default=argparse.SUPPRESS,
                    help=text,
                )


def _metadata_args(s: _Step) -> None:
    from standards_core.ci import verdict

    s.add("--ecosystem", "fetch-metadata package-ecosystem", default="")
    s.add("--update-type", "fetch-metadata update-type", default="")
    s.add("--dependency-group", "fetch-metadata dependency-group", default="")
    s.add("--dependency-names", "fetch-metadata dependency-names", default="")
    s.add("--directory", "fetch-metadata directory", default="/")
    s.add(
        "--engine-repo",
        "the <owner>/<repo> the checkout locks the engine from ($engine)",
        default="",
    )
    s.add(
        "--param",
        "a decision-table parameter, KEY=<JSON list> ($param:KEY; repeatable)",
        action="append",
        default=[],
        metavar="KEY=JSON",
    )
    s.add(
        "--policy",
        "dependabot.auto-merge-policy: the decision table, or every PR (all)",
        choices=verdict.POLICIES,
        default=verdict.DECISION_TABLE,
    )


def _merge_args(s: _Step) -> None:
    s.add("--pr-url", "the pull request", required=True)
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    s.add("--integration", "the integration branch", required=True)
    s.add(
        "--gates",
        "the enabled gates, comma-separated (default: the checkout's)",
        default=None,
    )
    s.add(
        "--merge-method",
        "how auto-merge merges",
        default="squash",
        choices=("squash", "merge", "rebase"),
    )


def _set_arg(s: _Step) -> None:
    s.add(
        "--set",
        "override a dotted configuration key for this step (repeatable)",
        action="append",
        default=[],
        metavar="KEY=JSON",
    )


def _context_arg(s: _Step, text: str) -> None:
    s.add(
        "--context",
        text,
        action="append",
        default=[],
        metavar="CHECK=WORKFLOW",
    )


def register(subparsers: argparse._SubParsersAction, product: Product) -> None:
    from standards_core.ci import verdict

    p = subparsers.add_parser(
        "ci",
        help="the steps the workflows run (auto-merge, relock, release, bump, drift)",
        description=help_text(product, "ci", __doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="step", metavar="<step>", required=True)
    contexts = ", ".join(f"'{c}={w}'" for c, w in product.ci.contexts) or "none"
    engine = product.engine.flake_input

    def step(name: str, text: str, func: Any) -> _Step:
        q = sub.add_parser(
            name, help=text, description=help_text(product, f"ci {name}", text)
        )
        q.set_defaults(func=func)
        return _Step(product, name, q)

    steps = []

    s = step("verdict", "the auto-merge verdict for a Dependabot PR", run_verdict)
    _metadata_args(s)
    steps.append(s)

    s = step(
        "automerge", "apply the auto-merge verdict under the safety rule", run_automerge
    )
    _merge_args(s)
    _metadata_args(s)
    s.add("--verdict", "a verdict already made", choices=verdict.VERDICTS, default="")
    s.add(
        "--relock",
        "relock-plan's state for nix: empty, patch or rollout-input-moved",
        default="",
    )
    s.add("--moved", "the rollout inputs a nix PR moves, comma-separated", default="")
    steps.append(s)

    s = step("enable-automerge", "gh pr merge --auto under the safety rule", run_enable)
    _merge_args(s)
    steps.append(s)

    s = step(
        "relock-inputs",
        f"whether a nix PR moves {engine} or standards-profile",
        run_relock_inputs,
    )
    s.add(
        "--base",
        "the PR's base SHA (the merge-base with HEAD is used)",
        required=True,
    )
    steps.append(s)

    s = step(
        "relock-patch",
        "stage everything and write the patch, refusing workflows",
        run_relock_patch,
    )
    s.add("--out", "where the patch goes", required=True)
    steps.append(s)

    s = step(
        "refuse-workflows",
        "exit 1 when a change touches .github/workflows/",
        run_refuse,
    )
    s.add("--base", "compare <base>..HEAD", default="")
    s.add("--cached", "compare the index with HEAD (or --base)", action="store_true")
    steps.append(s)

    s = step("dispatch-ci", "dispatch CI on the open release PR", run_dispatch)
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    s.add("--integration", "the integration branch", required=True)
    s.add("--package", "release-please's component name", default="")
    _context_arg(
        s,
        "a required check and the workflow that reports it (repeatable; default:"
        f" {contexts})",
    )
    steps.append(s)

    s = step("sweep", "dispatch CI on bot PRs whose head has no ci runs", run_sweep)
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    _context_arg(s, f"as dispatch-ci (default: {contexts})")
    steps.append(s)

    s = step(
        "update-branches",
        "bring bot PRs that are behind up to date",
        run_update_branches,
    )
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    s.add("--integration", "the integration branch", required=True)
    s.add(
        "--dispatch-ci",
        "the token is GITHUB_TOKEN, whose update raises no event: dispatch CI on the"
        " new head",
        action="store_true",
    )
    _context_arg(s, "with --dispatch-ci, as dispatch-ci")
    s.add("--limit", "the most PRs to act on in one run", type=int, default=10)
    steps.append(s)

    s = step(
        "fast-forward",
        "move the release branch to the release and guard it",
        run_fast_forward,
    )
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    s.add("--release-branch", "the branch to move", required=True)
    s.add("--sha", "the commit to move it to", required=True)
    s.add(
        "--tag",
        "the release tag the branch must equal (tags flow); without it the required"
        " checks of --sha must pass",
        default="",
    )
    s.add(
        "--version-scheme",
        "the version scheme whose tag rule applies (default: the checkout's)",
        choices=("semver", *product.version_schemes),
        default=None,
    )
    s.add("--tag-prefix", "releases.tag-prefix (default: the checkout's)", default=None)
    s.add(
        "--integration",
        "the branch whose required checks guard a tagless move (default: the"
        " checkout's)",
        default="",
    )
    s.add(
        "--wait-checks",
        "seconds to wait for pending checks (tagless; default: one read)",
        type=float,
        default=0.0,
        metavar="SECONDS",
    )
    s.add(
        "--dispatch-after",
        "a workflow to dispatch on the release branch once it moved (repeatable)",
        action="append",
        default=[],
        metavar="WORKFLOW",
    )
    _set_arg(s)
    s.add("--dry-run", "read, but make no write call", action="store_true")
    steps.append(s)

    s = step(
        "bump",
        "raise each changed unit's version and commit it (post-merge-bump flow)",
        run_bump,
    )
    s.add(
        "--base", "the release branch to compare with: origin/<series>", required=True
    )
    s.add(
        "--integration",
        "the branch --push pushes to (default: the checkout's)",
        default="",
    )
    s.add(
        "--push", "push the bump commit to the integration branch", action="store_true"
    )
    s.add("--dry-run", "print the bumps, write nothing", action="store_true")
    _set_arg(s)
    steps.append(s)

    s = step("drift", f"keep the {engine} and profile update issues", run_drift)
    s.add("--repo", "<owner>/<repo> (default: $GITHUB_REPOSITORY)", default="")
    s.add("--latest", f"the latest {engine} release, vX.Y.Z", required=True)
    s.add(
        "--summary-file",
        f"`{product.identity.cli} sync --check` output to quote",
        default="",
    )
    s.add("--no-profile", "skip the standards-profile comparison", action="store_true")
    steps.append(s)

    for s in steps:
        s.aliases()
