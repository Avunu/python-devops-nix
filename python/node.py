"""JavaScript targets in the development shell: the frozen install shell entry
runs, and the targets' build and watch scripts.

Product-independent and standard library only (written to 3.11). A product
runs it as a command (`lib.node.command` builds one, over a JSON file of its
targets), or copies it into its own package and imports it (`lib.node.module`)
for `detect` alone. Four subcommands:

    list [--json]        the targets, the manager each installs with, and its
                         lockfile and scripts
    install [KEY]...     the frozen install of each target, online, skipped
                         when its manifests are the ones the last install saw
    build [KEY]...       `<manager> run build` in each target that has one
    watch [KEY]...       `<manager> run watch` in each target that has one, at
                         once, each line prefixed with its key

The rules are lib/node's (docs/conventions.md, JavaScript), and `detect` is
the same function as lib.node.detect, tested against it over the same
fixtures: what the Nix build installs with is what the shell installs with.
Package.json's `packageManager` (Corepack's "pnpm@10.4.1+sha512…"), else its
`devEngines.packageManager`, else the lockfile names the manager. Two
lockfiles, a declared manager the lockfile disagrees with, a yarn berry lock
and a bun lock are refused with the fix, never guessed past.

Why the shell needs this at all, when the Nix build installs offline: editors,
linters and a bundler's watch mode read node_modules in the working tree, and
the tree is not a store path. And why frozen: yarn 1's `install
--frozen-lockfile` in a folder whose yarn.lock is gone exits 0 having resolved
every range afresh — an app that moved to pnpm gets unpinned packages and no
error (frappe/suite#980). Here the lockfile picks the manager, so pnpm's lock
is installed by pnpm, and a folder with dependencies and no lock is an error.

A pnpm 11+ target whose devEngines pins another pnpm than the shell's would
download that one and switch to it; the shell keeps its own (the one Nix
builds with) unless the environment says otherwise (pnpm_config_pm_on_fail).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

LOCKFILES: Mapping[str, str] = {
    "bun.lock": "bun",
    "bun.lockb": "bun",
    "npm-shrinkwrap.json": "npm",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}
MANAGERS = ("npm", "pnpm", "yarn")

#: What an install reads besides package.json and the lock, and so what its
#: fingerprint covers.
SETTINGS = (".npmrc", ".yarnrc", ".pnpmfile.cjs", "pnpm-workspace.yaml")

#: The frozen install, per manager.
INSTALL: Mapping[str, Sequence[str]] = {
    "npm": ("npm", "ci"),
    "pnpm": ("pnpm", "install", "--frozen-lockfile"),
    "yarn": ("yarn", "install", "--frozen-lockfile"),
}


class Refused(Exception):
    """A target this cannot install: the message says what to do."""


@dataclass(frozen=True)
class Declared:
    name: str
    version: str | None
    via: str

    @property
    def major(self) -> str | None:
        m = re.match(r"[^0-9]*([0-9]+)", self.version or "")
        return m.group(1) if m else None


@dataclass(frozen=True)
class Target:
    key: str
    path: Path
    manager: str
    lock_name: str | None
    declared: Declared | None
    scripts: Mapping[str, str]
    has_deps: bool
    members: tuple[str, ...] = field(default=())

    @property
    def has_build(self) -> bool:
        return "build" in self.scripts

    @property
    def has_watch(self) -> bool:
        return "watch" in self.scripts

    @property
    def member_builds(self) -> list[str]:
        """The members with a build script: what builds when the root has none."""
        out = []
        for m in self.members:
            pj = json.loads(
                (self.path / m / "package.json").read_text(encoding="utf-8")
            )
            if "build" in (pj.get("scripts") or {}):
                out.append(m)
        return out

    @property
    def major(self) -> str | None:
        return self.declared.major if self.declared else None

    def summary(self) -> dict[str, object]:
        """The fields lib.node.detect's tests compare."""
        return {
            "manager": self.manager,
            "lockName": self.lock_name,
            "hasBuild": self.has_build,
            "hasDeps": self.has_deps,
            "members": list(self.members),
            "memberBuilds": self.member_builds,
            "major": self.major,
        }


# ── detection ──────────────────────────────────────────────────────────────


def declared_manager(pj: Mapping[str, object]) -> Declared | None:
    pm = pj.get("packageManager")
    if isinstance(pm, str):
        m = re.fullmatch(r"([^@]+)@([^+]+).*", pm)
        if m:
            return Declared(m.group(1), m.group(2), "packageManager")
        return Declared(pm, None, "packageManager")
    engines = pj.get("devEngines")
    if isinstance(engines, Mapping):
        dpm = engines.get("packageManager")
        if isinstance(dpm, list) and dpm:
            dpm = dpm[0]
        if isinstance(dpm, Mapping) and isinstance(dpm.get("name"), str):
            version = dpm.get("version")
            return Declared(
                str(dpm["name"]),
                version if isinstance(version, str) else None,
                "devEngines.packageManager",
            )
    return None


def is_yarn_v1(path: Path) -> bool:
    with path.open(encoding="utf-8", errors="replace") as f:
        return any(
            line.rstrip("\n") == "# yarn lockfile v1" for _, line in zip(range(8), f)
        )


def pnpm_project_lines(path: Path) -> list[str]:
    """The lock's last YAML document: pnpm 11+ puts an env document first."""
    lines = path.read_text(encoding="utf-8").split("\n")
    marks = [i for i, line in enumerate(lines) if line == "---"]
    return lines[marks[-1] + 1 :] if marks else lines


def pnpm_importers(path: Path) -> list[str]:
    keys: list[str] = []
    inside = False
    for line in pnpm_project_lines(path):
        if line == "importers:":
            inside = True
        elif not inside:
            continue
        elif re.match(r"[^ \t]", line):
            break
        else:
            m = re.fullmatch(r"  ([^ \t].*):[ \t]*", line)
            if m:
                keys.append(m.group(1).removeprefix("'").removesuffix("'"))
    return keys


def expand_workspaces(src: Path, key: str, patterns: Sequence[str]) -> list[str]:
    def dirs_of(rel: str) -> list[str]:
        p = src / rel if rel else src
        if not p.is_dir():
            return []
        return sorted(
            e.name for e in p.iterdir() if e.is_dir() and e.name != "node_modules"
        )

    def join(a: str, b: str) -> str:
        return f"{a}/{b}" if a else b

    found: list[str] = []
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        rels = [""]
        for seg in pattern.removeprefix("./").split("/"):
            if seg in ("", "."):
                continue
            if seg == "**":
                raise Refused(
                    f"{key}'s package.json workspaces use `**`; list the member "
                    "folders, or one `*` per level"
                )
            if "*" in seg:
                rx = re.compile(re.escape(seg).replace(r"\*", "[^/]*"))
                rels = [join(r, n) for r in rels for n in dirs_of(r) if rx.fullmatch(n)]
            else:
                rels = [join(r, seg) for r in rels]
        found += [r for r in rels if r and (src / r / "package.json").is_file()]
    return list(dict.fromkeys(found))


def detect(src: str | os.PathLike[str], key: str | None = None) -> Target | None:
    """lib.node.detect: the directory as a target, or None without a
    package.json. Raises Refused for what lib.node refuses."""
    path = Path(src)
    key = key or str(path)
    if not (path / "package.json").is_file():
        return None
    pj = json.loads((path / "package.json").read_text(encoding="utf-8"))
    present = sorted(name for name in LOCKFILES if (path / name).is_file())
    if len(present) > 1:
        raise Refused(
            f"{key} has {' and '.join(present)}; one folder installs with one "
            "package manager — delete the lockfile it does not use"
        )
    lock_name = present[0] if present else None
    from_lock: str | None = None
    if lock_name is not None:
        from_lock = LOCKFILES[lock_name]
        if from_lock == "bun":
            raise Refused(
                f"{key}/{lock_name} is a bun lock, which cannot be installed offline "
                "here; use pnpm (`pnpm import` reads the package.json), npm or yarn 1"
            )
        if lock_name == "yarn.lock" and not is_yarn_v1(path / lock_name):
            raise Refused(
                f"{key}/yarn.lock is a yarn berry (2+) lock; yarn 1, npm and pnpm "
                "install offline here — `pnpm import` converts it"
            )
    declared = declared_manager(pj)
    if declared is None:
        manager = from_lock or "npm"
    elif declared.name not in MANAGERS:
        raise Refused(
            f"{key}'s package.json names {declared.name} ({declared.via}); one of "
            f"{', '.join(MANAGERS)} installs here"
        )
    elif declared.name == "yarn" and declared.major not in (None, "1"):
        raise Refused(
            f"{key}'s package.json names yarn {declared.version} ({declared.via}), "
            "which is yarn berry; yarn 1, npm and pnpm install offline here"
        )
    elif from_lock is not None and from_lock != declared.name:
        raise Refused(
            f"{key}'s package.json names {declared.name} ({declared.via}), but its "
            f"lockfile is {lock_name}; regenerate the lock with {declared.name}, or "
            f"change {declared.via}"
        )
    else:
        manager = declared.name

    members: list[str] = []
    if lock_name is not None:
        if manager == "pnpm":
            members = [
                m
                for m in pnpm_importers(path / lock_name)
                if m != "." and (path / m / "package.json").is_file()
            ]
        else:
            ws = pj.get("workspaces", [])
            patterns = ws if isinstance(ws, list) else ws.get("packages", [])
            members = expand_workspaces(path, key, patterns)

    scripts = pj.get("scripts") or {}
    return Target(
        key=key,
        path=path,
        manager=manager,
        lock_name=lock_name,
        declared=declared,
        scripts=scripts,
        has_deps=any(
            pj.get(f)
            for f in ("dependencies", "devDependencies", "optionalDependencies")
        ),
        members=tuple(members),
    )


# ── install ────────────────────────────────────────────────────────────────


def fingerprint(target: Target) -> str:
    """Of everything the install reads, so a changed one installs again."""
    h = hashlib.sha256()
    names = ["package.json", target.lock_name or "", *SETTINGS]
    names += [f"{m}/package.json" for m in target.members]
    for name in names:
        p = target.path / name
        if name and p.is_file():
            h.update(name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def sentinel(target: Target, label: str) -> Path:
    return target.path / "node_modules" / f".{label}-installed"


def install_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("pnpm_config_pm_on_fail", "ignore")
    return env


def install(target: Target, label: str, *, force: bool = False) -> bool:
    if target.lock_name is None:
        if target.has_deps:
            raise Refused(
                f"{target.key} has dependencies and no lockfile, so nothing says "
                f"which versions to install; run `{target.manager} install` there "
                "and commit the lockfile"
            )
        return True
    mark = sentinel(target, label)
    want = fingerprint(target)
    if not force and mark.is_file() and mark.read_text().strip() == want:
        return True
    argv = INSTALL[target.manager]
    say(label, f"{target.key}: {' '.join(argv)}")
    r = subprocess.run(argv, cwd=target.path, env=install_env(), check=False)
    if r.returncode != 0:
        say(label, f"{target.key}: {argv[0]} failed (exit {r.returncode})")
        return False
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(want + "\n")
    return True


# ── build and watch ────────────────────────────────────────────────────────


def run_script(target: Target, script: str, label: str, member: str = "") -> bool:
    where = f"{target.key}/{member}" if member else target.key
    say(label, f"{where}: {target.manager} run {script}")
    r = subprocess.run(
        (target.manager, "run", script),
        cwd=target.path / member if member else target.path,
        env=install_env(),
        check=False,
    )
    if r.returncode != 0:
        say(label, f"{where}: {script} failed (exit {r.returncode})")
    return r.returncode == 0


def build(target: Target, label: str) -> bool:
    """lib.node.build's order: the target's build script, or each member's."""
    if target.has_build:
        return run_script(target, "build", label)
    ok = True
    for m in target.member_builds:
        ok = run_script(target, "build", label, m) and ok
    return ok


def watch(targets: Sequence[Target], label: str) -> int:
    """Every watch script at once, each line prefixed; ends when one does."""
    if not targets:
        say(label, "no target has a watch script")
        return 0
    width = max(len(t.key) for t in targets)
    procs: dict[int, tuple[subprocess.Popen[bytes], str]] = {}
    sel = selectors.DefaultSelector()
    for t in targets:
        say(label, f"{t.key}: {t.manager} run watch")
        p = subprocess.Popen(
            (t.manager, "run", "watch"),
            cwd=t.path,
            env=install_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert p.stdout is not None
        sel.register(p.stdout, selectors.EVENT_READ, t.key)
        procs[p.pid] = (p, t.key)

    def stop(*_: object) -> None:
        for p, _key in procs.values():
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    status = 0
    try:
        while sel.get_map():
            for k, _ in sel.select():
                line = k.fileobj.readline()  # type: ignore[union-attr]
                if not line:
                    sel.unregister(k.fileobj)
                    continue
                sys.stdout.write(f"[{k.data:<{width}}] {line.decode(errors='replace')}")
                sys.stdout.flush()
            for p, key in procs.values():
                if p.poll() not in (None, 0) and status == 0:
                    say(label, f"{key}: watch exited {p.returncode}; stopping the rest")
                    status = 1
                    stop()
    finally:
        stop()
        for p, _key in procs.values():
            p.wait()
    return status


# ── command line ───────────────────────────────────────────────────────────


def say(label: str, *lines: str) -> None:
    for line in lines:
        print(f"{label}: {line}", file=sys.stderr, flush=True)


def load_targets(spec: str | os.PathLike[str]) -> list[tuple[str, Path]]:
    data = json.loads(Path(spec).read_text(encoding="utf-8"))
    return [(t["key"], Path(t["path"])) for t in data]


def main(
    argv: Sequence[str],
    *,
    label: str,
    targets: str,
    ci_var: str | None = None,
    prog: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="JavaScript targets: install, build and watch."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    list_p = sub.add_parser("list", help="the targets and how each installs")
    list_p.add_argument("--json", action="store_true")
    install_p = sub.add_parser(
        "install", help="the frozen install, when manifests moved"
    )
    install_p.add_argument("keys", nargs="*", metavar="KEY")
    install_p.add_argument(
        "--force", action="store_true", help="install even when nothing moved"
    )
    build_p = sub.add_parser("build", help="run each target's build script")
    build_p.add_argument("keys", nargs="*", metavar="KEY")
    watch_p = sub.add_parser("watch", help="run every target's watch script at once")
    watch_p.add_argument("keys", nargs="*", metavar="KEY")
    args = parser.parse_args(argv)

    known = load_targets(targets)
    keys = getattr(args, "keys", [])
    unknown = sorted(set(keys) - {k for k, _ in known})
    if unknown:
        say(
            label,
            f"no such target: {', '.join(unknown)} "
            f"(the targets are {', '.join(k for k, _ in known) or 'none'})",
        )
        return 2
    chosen = [(k, p) for k, p in known if not keys or k in keys]

    found: list[Target] = []
    failed = False
    for key, path in chosen:
        try:
            t = detect(path, key)
        except Refused as exc:
            say(label, str(exc))
            failed = True
            continue
        if t is not None:
            found.append(t)

    if args.command == "list":
        if args.json:
            print(json.dumps({t.key: t.summary() for t in found}, indent=2))
        else:
            for t in found:
                scripts = ", ".join(s for s in ("build", "watch") if s in t.scripts)
                print(
                    f"{t.key}\t{t.manager}\t{t.lock_name or '(no lockfile)'}"
                    f"\t{scripts or '-'}"
                )
        return 1 if failed else 0

    if args.command == "install":
        if ci_var and os.environ.get(ci_var, "") not in ("", "0"):
            return 1 if failed else 0
        for t in found:
            try:
                ok = install(t, label, force=args.force)
            except Refused as exc:
                say(label, str(exc))
                ok = False
            failed = failed or not ok
        return 1 if failed else 0

    if args.command == "build":
        for t in found:
            failed = not build(t, label) or failed
        return 1 if failed else 0

    return watch([t for t in found if t.has_watch], label) or (1 if failed else 0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: node.py TARGETS.json {list,install,build,watch} ...")
    sys.exit(main(sys.argv[2:], label=Path(sys.argv[0]).stem, targets=sys.argv[1]))
