"""Confirm a product's interface follows the conventions: the environment
variables its tools read and set, and the flags its commands take.

    interface.py env PRODUCT_DIR SPEC_JSON
    interface.py flags SPEC_JSON

`env` (docs/conventions.md, Tool-owned variables): every variable in the
product's namespace that its source reads or sets is `<P>_NIX_*`, or one of
devguard's `<P>_DEVGUARD_*`. SPEC_JSON is {"prefix": "<P>", "allowed":
["<P>_NIX_", ...], "required": {name: section}, "exceptions": [name, ...],
"ignore": [dir, ...]}. A variable is a name `<P>_...` in upper case that the
source uses as one, in any file that is Python, shell or Nix:

-   expanded: `$NAME` or `${NAME...}`;
-   a whole string literal: `"NAME"` or `'NAME'`, as os.environ.get, a
    helper of the product's own or an env dict takes it;
-   set: `NAME=`, in shell, `env NAME=1 cmd` or an `Environment=` line;
-   after `export`, `unset`, `printenv` or a makeWrapper `--set`, `--set-default`,
    `--unset`, `--prefix` or `--suffix`;
-   in Nix, an attribute: `NAME = ...;` (`env.NAME`, `environment.NAME`).

A name on a comment line, inside a longer string or as a Python constant does
not count. `$NAME` counts anywhere, a docstring's too. No scan can tell what a
name is for, so two kinds of name count that are not the environment's: a
quoted template key (`{"<P>_VERSION": ...}`), and a shell script's own
variable (`<P>_SHA="$(git rev-parse HEAD)"`). A script writes its own in lower
case. `required` names variables the source must use, and `exceptions` names
that may break the rule (the framework's own, old names read until 2.0.0, and
names that are not variables, such as a template's keys). An exception that is
not found, or that follows the rule, fails the check.

`flags` (docs/conventions.md, Restore and Scaffolder): each command, run with
`--help`, lists the flags the conventions name for its role. SPEC_JSON is
{"commands": {role: [argv, ...]}, "required": {role: {flag: section}},
"exceptions": ["<role> <flag>", ...]}. A flag counts when it heads a line of
the help's option list, as argparse, click and a hand-written usage print it:
`  --files / --no-files  ...`, `  -y, --yes  ...`, `  --at <when>  ...`,
`  --commit[=<msg>]  ...`, `  --files FILES [FILES ...]  ...`,
`  --[no-]force  ...`. Two spaces or a tab end the flags, and a metavar is in
upper case or in brackets. One only named in a description does not count, even
at the start of a line. A help in another shape (`--at when`, one space before
the description) fails the check; it never passes it wrongly.

Each prints one line per finding and exits 1 if there is any.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SUFFIXES = {".py": "python", ".sh": "shell", ".bash": "shell", ".nix": "nix"}
# A comment line in each of them.
COMMENT = re.compile(r"\s*#")
# A word that sets or names the variable after it.
SETTERS = r"export|unset|printenv|--set|--set-default|--unset|--prefix|--suffix"
# The groups of `patterns` that hold the name.
GROUPS = ("expanded", "quoted", "set", "named", "attribute")
# The words of a help's option term. A bracketed group is one word, spaces and
# all (`[FILES ...]`).
TERM_WORDS = re.compile(r"(?:\[[^\]]*\]|\{[^}]*\}|<[^>]*>|[^\s\[{<])+")
# A word of a help's option term: a flag (`-y,`, `--commit[=<msg>]`,
# `--files/--no-files`), the `/` between a flag and its negation, or a metavar
# (`<when>`, `TEXT`, `[a|b]`, `{a,b}`, `[FILES ...]`).
TERM_WORD = re.compile(r"(-\S*|/|<[^>]*>|\[[^\]]*\]|\{[^}]*\}|[A-Z][A-Z0-9_.-]*),?")
FLAG = re.compile(r"--?[A-Za-z0-9][\w-]*")
# `--[no-]force`, which is `--force` and `--no-force`.
NEGATABLE = re.compile(r"--\[no-\]([A-Za-z0-9][\w-]*)")


def language(path: Path) -> str | None:
    if path.suffix in SUFFIXES:
        return SUFFIXES[path.suffix]
    if path.suffix or path.name.startswith("."):
        return None
    try:
        with path.open("rb") as f:
            first = f.readline(200)
    except OSError:
        return None
    if first.startswith(b"#!"):
        if b"python" in first:
            return "python"
        if re.search(rb"\b(ba|da|z)?sh\b", first):
            return "shell"
    return None


def patterns(prefix: str, lang: str) -> re.Pattern[str]:
    name = rf"{re.escape(prefix)}_[A-Z0-9_]*[A-Z0-9](?![A-Za-z0-9_])"
    forms = [
        rf"\$\{{?(?P<expanded>{name})",
        rf"(?P<q>[\"'])(?P<quoted>{name})(?P=q)",
        rf"(?<![\w.$-])(?P<set>{name})=(?!=)",
        rf"(?:{SETTERS})\s+(?P<named>{name})",
    ]
    if lang == "nix":
        forms.append(rf"(?<![\w$-])(?P<attribute>{name})\s*=(?!=)")
    return re.compile("|".join(forms))


def scan(product_dir: Path, prefix: str, ignore: list[str]) -> dict[str, list[str]]:
    """{name: ["file:line", ...]} for each variable the source uses."""
    skip = {product_dir / ".git", *(product_dir / d for d in ignore)}
    found: dict[str, list[str]] = {}
    compiled: dict[str, re.Pattern[str]] = {}
    for root, dirs, files in os.walk(product_dir):
        here = Path(root)
        dirs[:] = sorted(d for d in dirs if here / d not in skip)
        for file in sorted(files):
            path = here / file
            lang = language(path)
            if lang is None or path.is_symlink():
                continue
            if lang not in compiled:
                compiled[lang] = patterns(prefix, lang)
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(product_dir)
            for lineno, line in enumerate(text.splitlines(), 1):
                if COMMENT.match(line):
                    continue
                for match in compiled[lang].finditer(line):
                    var = next(match[g] for g in GROUPS if match.groupdict().get(g))
                    found.setdefault(var, []).append(f"{rel}:{lineno}")
    return found


def env(product_dir: Path, spec: dict) -> int:
    found = scan(product_dir, spec["prefix"], spec["ignore"])
    allowed = tuple(spec["allowed"])
    exceptions = set(spec["exceptions"])

    def where(places: list[str]) -> str:
        more = len(places) - 1
        return places[0] + (f", and {more} more" if more else "")

    findings = [
        f"{var}: not {' or '.join(a + '*' for a in allowed)} ({where(places)})"
        " (Tool-owned variables)"
        for var, places in sorted(found.items())
        if not var.startswith(allowed) and var not in exceptions
    ]
    findings += [
        f"{var}: never read or set ({section})"
        for var, section in sorted(spec["required"].items())
        if var not in found
    ]
    findings += [
        f"{var}: listed as an exception, but nothing is wrong with it"
        for var in sorted(exceptions)
        if var not in found or var.startswith(allowed)
    ]
    for line in findings:
        print(line)
    if findings:
        print(
            f"{len(findings)} variable(s) outside the conventions"
            " (docs/conventions.md, Environment variables)."
        )
        return 1
    print(f"{len(found)} variable(s) in {spec['prefix']}_*, each in the conventions")
    return 0


def help_flags(text: str) -> set[str]:
    """The flags that head the lines of a help's option list."""
    flags: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"\s+(-\S.*)", line)
        if not match:
            continue
        # The term is what comes before the description, two spaces or a tab
        # on. A line of prose that starts with a flag has words a term cannot.
        term = re.split(r"\s{2,}|\t", match.group(1).strip())[0]
        words = TERM_WORDS.findall(term)
        if not all(TERM_WORD.fullmatch(word) for word in words):
            continue
        for word in words:
            for part in word.split("/") if word.startswith("-") else []:
                if negatable := NEGATABLE.match(part):
                    flags |= {f"--{negatable[1]}", f"--no-{negatable[1]}"}
                elif flag := FLAG.match(part):
                    flags.add(flag[0])
    return flags


def run_help(argv: list[str]) -> tuple[str, str | None]:
    """The help's text, and why it could not be read, or None."""
    command = " ".join([*argv, "--help"])
    try:
        done = subprocess.run(
            [*argv, "--help"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"`{command}` did not run: {exc}"
    if done.returncode != 0:
        output = (done.stdout + done.stderr).strip()
        return "", f"`{command}` exited {done.returncode}: {output}"
    return done.stdout + done.stderr, None


def flags(spec: dict) -> int:
    exceptions = set(spec["exceptions"])
    problems: dict[str, str] = {}
    # The roles whose help could not be read, whose exceptions are not judged.
    unread: set[str] = set()
    findings = []
    for role, required in sorted(spec["required"].items()):
        text, error = run_help(spec["commands"][role])
        if error:
            findings.append(f"{role}: {error}")
            unread.add(role)
            continue
        accepted = help_flags(text)
        for flag, section in sorted(required.items()):
            if flag not in accepted:
                problems[f"{role} {flag}"] = f"not in its --help ({section})"
    findings += [
        f"{key}: {problem}"
        for key, problem in sorted(problems.items())
        if key not in exceptions
    ]
    findings += [
        f"{key}: listed as an exception, but nothing is wrong with it"
        for key in sorted(exceptions - problems.keys())
        if key.split()[0] not in unread
    ]
    for line in findings:
        print(line)
    if findings:
        print(
            f"{len(findings)} difference(s) from the shared flags"
            " (docs/conventions.md, Restore and Scaffolder)."
        )
        return 1
    print(f"{len(spec['required'])} command(s) take the shared flags")
    return 0


def main() -> int:
    command, *rest = sys.argv[1:]
    if command == "env":
        return env(Path(rest[0]), json.loads(rest[1]))
    if command == "flags":
        return flags(json.loads(rest[0]))
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    sys.exit(main())
