"""The frappe parity harness (standards/DESIGN.md §11.3): standards-core, given the
string values frappe-nix will pass, against frappe-nix-tools' own output.

Usage, from the repository root (frappe-nix-tools needs Python 3.12 or later)::

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.12 \\
        --with-editable standards --with click --with rich \\
        python parity/frappe/check.py /path/to/frappe-nix

The frappe-nix checkout is read only: its tools are imported from
``py/frappe_nix_tools``, and no byte code is written there.

It builds the core's stub product with frappe-nix's identity and markers (the
``Product`` frappe-nix will build when it moves onto the core) and compares, for what
the core ships so far (DESIGN §12: stages 3, 4 and 8):

- **header lines**: ``render.header`` for every header kind, with and without a local
  region, with and without a note, byte for byte;
- **report formats**: ``report.render`` of the same findings in ``text`` and ``json``
  (and ``github`` up to its random stop-commands token), byte for byte;
- **sync --check --format json**: on a directory that is no checkout, the whole error
  report; on an opted-in fixture, the key order of the document and of each file;
- **profile show --format json**: the key order of the document, the ``profile``
  table, and the order of the ``config`` keys both products have;
- **first-opt-in refusal**: an opted-in fixture with its own ``flake.nix`` and
  ``.envrc``; for each, ``sync --check --format json``'s file entry (path, strategy,
  module, exit) and its problem text up to the replacement diff, byte for byte, and the
  diff's header lines (the diffs themselves differ: the two products render different
  files).

- **repo-policy** (stage 8): ``repo_policy.for_target`` with the frappe product
  against frappe-nix-tools' ``repo_policy.for_app``, on the same resolved
  configuration, for both phases and a range of ``repo-policy``/``releases`` values,
  gate sets and documentation URLs: the settings, the labels and every ruleset, equal
  as data; and ``bypass_actors`` for frappe's two forms.

It prints one line per comparison and exits 1 when any differs. The later stage adds
``--help`` (9).
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
STANDARDS = HERE.parents[1] / "standards"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "parity",
    "GIT_AUTHOR_EMAIL": "parity@example.org",
    "GIT_COMMITTER_NAME": "parity",
    "GIT_COMMITTER_EMAIL": "parity@example.org",
}


def _imports(frappe_nix: Path) -> None:
    for path in (
        frappe_nix / "py" / "frappe_nix_tools",
        STANDARDS / "tests" / "stubs" / "stub",
    ):
        sys.path.insert(0, str(path))


# frappe-nix's first-opt-in text (its engine's ``_first_opt_in``) as the core's message
# template: the command it names is not ``identity.name``, so it is written inline.
FRAPPE_OPT_IN_MESSAGE = (
    "{path} is the app's own and differs from what frappe-init --app writes: sync would"
    " replace it with the managed file. {hint} then pass --force to replace it. The"
    " replacement:\n{diff}"
)
FRAPPE_OPT_IN_HINT = (
    "Keep custom inputs in the flake.nix `inputs` local region, outputs in"
    " nix/local.nix, systems and caches in [tool.frappe-nix.dev-shell] (direnv lines in"
    " the .envrc `envrc` region),"
)


# frappe-nix's labels (its LABEL_MODULES and repo-settings.json): the core ships the
# first two in its settings file, and the product adds the third.
FRAPPE_LABELS = {
    "dependencies": ("0366d6", "Dependency updates", "dependabot"),
    "needs-review": (
        "d93f0b",
        "Not merged automatically; a person reviews it",
        "dependabot",
    ),
    "screenshots": ("c5def5", "Screenshot refresh from nightly", "screenshots"),
}


def frappe_product():
    """The stub product with frappe-nix's string values."""
    from types import MappingProxyType

    import frappe_nix_tools
    from frappe_nix_tools import cli as frappe_cli
    from frappe_nix_tools.common import pyproject as frappe_pyproject
    from frappe_nix_tools.common import repo_policy as frappe_repo_policy
    from standards_core.product import FirstOptIn, Identity, Markers
    from stub_nix_tools.product import PRODUCT as STUB

    docs_url = frappe_cli.DOCS
    identity = Identity(
        name="frappe-nix",
        cli="frappe-nix",
        distribution="frappe-nix-tools",
        version=frappe_nix_tools.__version__,
        table="frappe-nix",
        env_prefix="FRAPPE_NIX",
        report_key="frappe_nix",
        sync_command="frappe-init --sync",
        opt_in_hint=frappe_pyproject.OPT_IN_HINT,
        docs_url=docs_url,
        description=f"frappe-nix's app standards tools. See {docs_url}.",
    )
    markers = Markers(
        managed="frappe-nix:managed",
        header="frappe-nix:managed — generated by frappe-nix (`frappe-init --sync`);",
        header_local="edit only inside the frappe-nix:local region.",
        local="frappe-nix:local",
        block_begin=">>> frappe-nix: {name} (managed) >>>",
        block_end="<<< frappe-nix: {name} (managed) <<<",
        html_begin="<!-- frappe-nix:begin {name} -->",
        html_end="<!-- frappe-nix:end {name} -->",
    )
    first_opt_in = tuple(
        FirstOptIn(path=path, hint=FRAPPE_OPT_IN_HINT, message=FRAPPE_OPT_IN_MESSAGE)
        for path in ("flake.nix", ".envrc")
    )
    ci = dataclasses.replace(
        STUB.ci,
        gates=tuple(frappe_repo_policy.GATE_ORDER),
        labels=MappingProxyType(FRAPPE_LABELS),
    )
    return dataclasses.replace(
        STUB, identity=identity, markers=markers, first_opt_in=first_opt_in, ci=ci
    )


class Results:
    def __init__(self) -> None:
        self.failed = 0

    def same(self, what: str, core: object, frappe: object) -> None:
        if core == frappe:
            print(f"ok   {what}")
            return
        self.failed += 1
        print(f"FAIL {what}\n  core:   {core!r}\n  frappe: {frappe!r}")


def headers(results: Results, product) -> None:
    from frappe_nix_tools.scaffold import manifest as frappe_manifest
    from frappe_nix_tools.scaffold import render as frappe_render
    from standards_core.scaffold import render

    for kind in frappe_manifest.HEADERS:
        for local in (False, True):
            for note in ("", "(a note)"):
                results.same(
                    f"header {kind} local={local} note={note!r}",
                    render.header(product, kind, local, note),
                    frappe_render.header(kind, local, note),
                )


def reports(results: Results, product) -> None:
    from frappe_nix_tools.common import report as frappe_report
    from standards_core.common import report

    findings = [
        ("a.toml", "whole", "differs from the rendered file", "-x\n+y\n", "lint"),
        ("b,c:d.json", "json-merge", "line one\nline %two", "", ""),
    ]
    engine = {"rev": "a" * 40, "version": product.identity.version}
    for code in (0, 1, 2, 3):
        for fmt in ("text", "json", "github"):
            chosen = findings if code else []
            core = report.render(
                [report.Finding(*f) for f in chosen], fmt, code, engine, product=product
            )
            frappe = frappe_report.render(
                [frappe_report.Finding(*f) for f in chosen], fmt, code, engine
            )
            if fmt == "github":
                token = re.compile(r"[0-9a-f]{32}")
                core, frappe = token.sub("T", core), token.sub("T", frappe)
            results.same(f"report {fmt} exit {code}", core, frappe)


def _run(main, argv: list[str], cwd: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    old = Path.cwd()
    try:
        os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


def _git(root: Path, *argv: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *argv],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


FRAPPE_APP = """[project]
name = "demo_app"
description = "A demo app"
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = []

[tool.frappe-nix]
schema = 1
profile = "{profile}"
frappe-major = 16
"""

CORE_UNIT = """[project]
name = "demo_app"
description = "A demo app"
requires-python = ">=3.14"
dynamic = ["version"]
dependencies = []

[tool.frappe-nix]
schema = 1
profile = "{profile}"
major = 16
"""


def _fixture(
    root: Path, text: str, *, frappe: bool, files: dict[str, str] | None = None
) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "develop")
    (root / "pyproject.toml").write_text(text)
    for rel, body in (files or {}).items():
        (root / rel).write_text(body)
    if frappe:
        (root / "demo_app").mkdir()
        (root / "demo_app" / "__init__.py").write_text('__version__ = "16.0.0"\n')
        (root / "demo_app" / "hooks.py").write_text('app_name = "demo_app"\n')
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def commands(results: Results, product, work: Path) -> None:
    from frappe_nix_tools import cli as frappe_cli
    from standards_core import cli

    def core_main(argv: list[str]) -> int:
        return cli.main(product, argv)

    bare = work / "bare"
    bare.mkdir()
    argv = ["sync", "--check", "--format", "json"]
    core = _run(core_main, argv, bare)
    frappe = _run(frappe_cli.main, argv, bare)
    results.same("sync --check --format json outside a checkout", core[:2], frappe[:2])

    for profile in ("minimal", "recommended"):
        app = _fixture(
            work / f"frappe-{profile}",
            FRAPPE_APP.format(profile=profile),
            frappe=True,
        )
        unit = _fixture(
            work / f"core-{profile}", CORE_UNIT.format(profile=profile), frappe=False
        )
        code_c, out_c, err_c = _run(core_main, argv, unit)
        code_f, out_f, err_f = _run(frappe_cli.main, argv, app)
        try:
            doc_c, doc_f = json.loads(out_c), json.loads(out_f)
        except ValueError:
            results.same(f"sync --check json ({profile})", out_c + err_c, out_f + err_f)
            continue
        results.same(f"sync --check json keys ({profile})", list(doc_c), list(doc_f))
        results.same(
            f"sync --check json file keys ({profile})",
            sorted({tuple(f) for f in doc_c["files"]}),
            sorted({tuple(f) for f in doc_f["files"]}),
        )
        results.same(
            f"sync --check json report key ({profile})",
            sorted(doc_c["frappe_nix"]),
            sorted(doc_f["frappe_nix"]),
        )

        show = ["profile", "show", "--format", "json"]
        code_c, out_c, err_c = _run(core_main, show, unit)
        code_f, out_f, err_f = _run(frappe_cli.main, show, app)
        if code_c or code_f:
            results.same(f"profile show ({profile})", (code_c, err_c), (code_f, err_f))
            continue
        doc_c, doc_f = json.loads(out_c), json.loads(out_f)
        results.same(f"profile show keys ({profile})", list(doc_c), list(doc_f))
        results.same(
            f"profile show profile table ({profile})",
            doc_c["profile"],
            doc_f["profile"],
        )
        shared = set(doc_c["config"]) & set(doc_f["config"])
        results.same(
            f"profile show config key order ({profile})",
            [k for k in doc_c["config"] if k in shared],
            [k for k in doc_f["config"] if k in shared],
        )


OWN_FILES = {
    "flake.nix": '{\n  inputs.x.url = "github:example/x";\n  outputs = _: { };\n}\n',
    ".envrc": "dotenv\nuse flake\n",
}


def first_opt_in(results: Results, product, work: Path) -> None:
    from frappe_nix_tools import cli as frappe_cli
    from standards_core import cli

    def core_main(argv: list[str]) -> int:
        return cli.main(product, argv)

    profile = "recommended"
    app = _fixture(
        work / "frappe-own",
        FRAPPE_APP.format(profile=profile),
        frappe=True,
        files=OWN_FILES,
    )
    unit = _fixture(
        work / "core-own",
        CORE_UNIT.format(profile=profile),
        frappe=False,
        files=OWN_FILES,
    )
    argv = ["sync", "--check", "--format", "json"]
    code_c, out_c, err_c = _run(core_main, argv, unit)
    code_f, out_f, err_f = _run(frappe_cli.main, argv, app)
    results.same("first opt-in: sync --check exit", code_c, code_f)
    try:
        files_c = {f["path"]: f for f in json.loads(out_c)["files"]}
        files_f = {f["path"]: f for f in json.loads(out_f)["files"]}
    except (ValueError, KeyError):
        results.same("first opt-in: sync --check json", out_c + err_c, out_f + err_f)
        return
    for path in OWN_FILES:
        core, frappe = files_c.get(path), files_f.get(path)
        if core is None or frappe is None:
            results.same(f"first opt-in {path}: reported", core, frappe)
            continue
        results.same(
            f"first opt-in {path}: entry",
            (core["strategy"], core["module"]),
            (frappe["strategy"], frappe["module"]),
        )
        text_c, _, diff_c = core["problem"].partition("The replacement:\n")
        text_f, _, diff_f = frappe["problem"].partition("The replacement:\n")
        results.same(f"first opt-in {path}: text", text_c, text_f)
        results.same(
            f"first opt-in {path}: diff header",
            diff_c.splitlines()[:2],
            diff_f.splitlines()[:2],
        )


# Tables over the recommended profile: each repo-policy/releases shape frappe renders.
POLICY_TABLES = {
    "recommended": {},
    "repo-policy values": {
        "repo-policy": {
            "enable": True,
            "required-approvals": 0,
            "merge-methods": ["rebase", "squash"],
            "require-code-owner-review": True,
            "require-thread-resolution": False,
            "delete-branch-on-merge": False,
        }
    },
    "managed parts off": {
        "repo-policy": {
            "manage-default-branch": False,
            "manage-features": False,
            "secret-scanning": False,
        }
    },
    "required checks": {"repo-policy": {"required-checks": ["lint"]}},
    "main+tags": {"releases": {"branching": "main+tags"}},
    "empty tag prefix, user bypass": {
        "releases": {"tag-prefix": ""},
        "repo-policy": {"release-bypass": "user:42"},
    },
    "dependabot off": {"dependabot": {"enable": False}},
    "releases off": {"releases": {"enable": False}},
}
POLICY_GATES = (
    ["pr-policy", "lint", "typecheck", "test", "marketplace"],
    ["test", "lint"],
    [],
)


def repo_policy(results: Results, product) -> None:
    from frappe_nix_tools.common import config as frappe_config
    from frappe_nix_tools.common import repo_policy as frappe_repo_policy
    from standards_core.common import repo_policy as core_repo_policy

    for label, extra in POLICY_TABLES.items():
        table = {
            "schema": 1,
            "profile": "recommended",
            "frappe-major": 16,
            "repo": "example/demo_app",
            **extra,
        }
        doc = {"tool": {"frappe-nix": table}}
        resolved = frappe_config.resolve_doc(doc, on_github=True)
        cfg, modules = resolved.cfg, resolved.modules
        for gates in POLICY_GATES:
            if (
                extra.get("repo-policy", {}).get("required-checks")
                and "lint" not in gates
            ):
                continue
            # Only the release branches the branching makes: main+tags has none (the
            # core also needs the branching's release_pattern for the ruleset).
            branching = cfg.get("releases", {}).get("branching")
            branches = ("version-16", "") if branching == "develop+version" else ("",)
            for phase in ("provisional", "full"):
                for release_branch in branches:
                    for documentation in ("", "https://d.example.org/"):
                        kwargs = {
                            "phase": phase,
                            "release_branch": release_branch,
                            "documentation": documentation,
                        }
                        core = core_repo_policy.for_target(
                            product, cfg, modules, gates, **kwargs
                        )
                        frappe = frappe_repo_policy.for_app(
                            cfg, modules, gates, **kwargs
                        )
                        results.same(
                            f"repo-policy {label}, gates {','.join(gates) or '-'},"
                            f" {phase}, release {release_branch or '-'}, docs"
                            f" {'yes' if documentation else 'no'}",
                            dataclasses.asdict(core),
                            dataclasses.asdict(frappe),
                        )
    for bypass in ("github-actions", "user:42"):
        results.same(
            f"repo-policy bypass_actors {bypass}",
            core_repo_policy.bypass_actors(bypass),
            frappe_repo_policy.bypass_actors(bypass),
        )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    frappe_nix = Path(argv[0]).resolve()
    if not (frappe_nix / "py" / "frappe_nix_tools" / "frappe_nix_tools").is_dir():
        print(f"{frappe_nix} is not a frappe-nix checkout", file=sys.stderr)
        return 2
    _imports(frappe_nix)
    for key in ("FRAPPE_NIX_ALLOW_SKEW", "FRAPPE_NIX_URL_OVERRIDE", "CI"):
        os.environ.pop(key, None)
    os.environ.update(GIT_ENV)
    product = frappe_product()
    results = Results()
    headers(results, product)
    reports(results, product)
    repo_policy(results, product)
    with tempfile.TemporaryDirectory() as tmp:
        commands(results, product, Path(tmp))
        first_opt_in(results, product, Path(tmp))
    print(f"{'FAIL' if results.failed else 'ok'}: {results.failed} difference(s)")
    return 1 if results.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
