"""The secrets toolkit's commands (docs/library.md, Secrets and backups):
edit-secret, rekey-secrets, setup-backup-access, and the agenix rules they
share.

    rules ROOT                      print the agenix rules file for ROOT
    edit-secret <name> [identity]   decrypt into $EDITOR, re-encrypt, stage
    rekey-secrets [agenix -r args]  re-encrypt everything to the declared list
    setup-backup-access             prompt for the backup credentials, check
                                    them, and encrypt them

Python rather than bash by design rule 5: these prompt, parse an env-file,
render a Nix file from JSON and keep an exit-code table. `check-secrets`
only runs agecheck, so it stays a bash `exec` shim.

The configuration is a JSON object (``lib.secrets.tools`` writes it):

    label          the product's name: messages and the rules file's header
    option         the option path, "<product>.secrets", for messages
    relDir         the secrets directory, relative to the git worktree
    rules          a JSON file of the declared recipients,
                   { "<relDir>/<x>.age": [key...] }, which agecheck reads too
    identityPaths  the identities agenix-shell decrypts with, as shell
                   strings: "$HOME/.ssh/id_ed25519" ($VAR is expanded here)
    secretNames    the declared secrets by the name edit-secret takes
    agenix         ragenix's `agenix`
    agecheck       the recipient-drift check
    rage           rage, to read a secret back (ragenix has no --decrypt)
    git            git
    backupAccess   setup-backup-access only, else null:
      restoreCommand  the command that restores from a backup
      bucketExample   the bucket name shown as an example
      fetch           the backup fetch command, $FETCH to verifyListing
      verifyListing   runs the product's check that the credentials reach
                      its backups, with BACKUPS_* and FETCH in its
                      environment; prints one line, exits non-zero on failure

Every command works in the git worktree, $REPO_ROOT, which is not always
where the application runs: that is where the .age files are tracked and
where `git add` has to run.

Standard library only, except setup-backup-access, which imports rich for its
prompts (lib.python.rich). Written to Python 3.11; it runs as its own process
on the interpreter the product picks (docs/library.md, Python versions).
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: The variables setup-backup-access writes, in order, and what it calls them.
FIELDS = (
    "BACKUPS_URL",
    "BACKUPS_ACCESS_KEY",
    "BACKUPS_SECRET_KEY",
    "BACKUPS_BUCKET",
    "BACKUPS_PREFIX",
)


class Exit(Exception):
    """Stop with an exit status; the message, if any, is already printed."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


# ── configuration ────────────────────────────────────────────────────────


def _keys(
    data: Mapping,
    where: str,
    required: set[str],
    optional: frozenset[str] = frozenset(),
):
    if not isinstance(data, Mapping):
        raise TypeError(f"{where}: expected an object")
    unknown = sorted(set(data) - required - optional)
    if unknown:
        raise ValueError(f"{where}: unknown key(s): {', '.join(unknown)}")
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"{where}: missing key(s): {', '.join(missing)}")


@dataclass(frozen=True)
class BackupAccess:
    restore_command: str
    bucket_example: str
    fetch: str
    verify_listing: str

    @classmethod
    def from_json(cls, data: Mapping) -> "BackupAccess":
        _keys(
            data,
            "backupAccess",
            {"restoreCommand", "bucketExample", "fetch", "verifyListing"},
        )
        return cls(
            restore_command=str(data["restoreCommand"]),
            bucket_example=str(data["bucketExample"]),
            fetch=str(data["fetch"]),
            verify_listing=str(data["verifyListing"]),
        )


@dataclass(frozen=True)
class Config:
    label: str
    option: str
    rel_dir: str
    rules_path: str
    rules: dict[str, list[str]]
    identity_paths: list[str]
    secret_names: list[str]
    agenix: str
    agecheck: str
    rage: str
    git: str
    backup_access: BackupAccess | None = None

    @classmethod
    def from_json(cls, data: Mapping) -> "Config":
        _keys(
            data,
            "configuration",
            {
                "label",
                "option",
                "relDir",
                "rules",
                "identityPaths",
                "secretNames",
                "agenix",
                "agecheck",
                "rage",
                "git",
            },
            {"backupAccess"},
        )
        rules_path = str(data["rules"])
        with open(rules_path, encoding="utf-8") as stream:
            rules = json.load(stream)
        if not isinstance(rules, Mapping) or not all(
            isinstance(keys, list) and all(isinstance(k, str) for k in keys)
            for keys in rules.values()
        ):
            raise ValueError("rules: expected { <path>: [<public key>...] }")
        backup = data.get("backupAccess")
        return cls(
            label=str(data["label"]),
            option=str(data["option"]),
            rel_dir=str(data["relDir"]),
            rules_path=rules_path,
            rules={str(k): list(v) for k, v in rules.items()},
            identity_paths=[str(p) for p in data["identityPaths"]],
            secret_names=[str(n) for n in data["secretNames"]],
            agenix=str(data["agenix"]),
            agecheck=str(data["agecheck"]),
            rage=str(data["rage"]),
            git=str(data["git"]),
            backup_access=BackupAccess.from_json(backup) if backup else None,
        )

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Config":
        with open(path, encoding="utf-8") as stream:
            return cls.from_json(json.load(stream))


# ── the rules agenix reads ───────────────────────────────────────────────
#
# Rendered at runtime with **absolute** paths, never committed or put in the
# store. ragenix resolves a rule key relative to the rules file's own
# directory, so a store-path rules file with repo-relative keys turns
# "secrets/backup-access.age" into "/nix/store/secrets/backup-access.age":
# `agenix -e` then reports no rule for the file, and `agenix -r` skips it as
# "does not exist, ignored" and exits 0, so a rekey silently does nothing.
# (ryantm/agenix resolves the same key against $PWD instead. Absolute paths
# are correct for both.) The worktree's path is not knowable at evaluation
# time, so this happens here.


def nix_string(value: str) -> str:
    """A Nix string literal holding ``value`` exactly."""
    out = []
    for i, ch in enumerate(value):
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "$" and value[i + 1 : i + 2] == "{":
            out.append("\\$")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def render_rules(rules: Mapping[str, Sequence[str]], root: str, label: str) -> str:
    lines = [f"# Generated by {label}. Do not edit; do not commit.", "{"]
    for rel, keys in rules.items():
        path = nix_string(f"{root}/{rel}")
        lines.append(
            f"  {path}.publicKeys = [{' '.join(nix_string(k) for k in keys)}];"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


@contextmanager
def rules_file(cfg: Config, root: str) -> Iterator[str]:
    """A private rules file for ``root``, removed afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "secrets.nix")
        Path(path).write_text(render_rules(cfg.rules, root, cfg.label))
        yield path


# ── shared steps ─────────────────────────────────────────────────────────


def repo_root() -> str:
    """cd to the git worktree, $REPO_ROOT, and return it."""
    root = os.environ.get("REPO_ROOT", "")
    if not root:
        print("REPO_ROOT: run this from the dev shell", file=sys.stderr)
        raise Exit(1)
    os.chdir(root)
    return root


def run(argv: Sequence[str], **kwargs) -> int:
    sys.stdout.flush()
    sys.stderr.flush()
    return subprocess.run(list(argv), check=False, **kwargs).returncode


def must(argv: Sequence[str], **kwargs) -> None:
    """Run a command; stop with its status when it fails (`set -e`)."""
    code = run(argv, **kwargs)
    if code != 0:
        raise Exit(code)


def stage(cfg: Config, rel: str) -> bool:
    """`git add` an untracked .age file; True when it was untracked.

    A flake's source tree is exactly its git-tracked files, so an untracked
    secret is invisible to the build, and the next `direnv reload` reports it
    missing (or, for setup-backup-access, the failure reads as "my key is
    wrong")."""
    tracked = run(
        [cfg.git, "ls-files", "--error-unmatch", "--", rel],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if tracked == 0:
        return False
    must([cfg.git, "add", "--", rel])
    return True


def agecheck(cfg: Config) -> int:
    return run([cfg.agecheck, "check", cfg.rules_path, "--root", "."])


# ── rules ────────────────────────────────────────────────────────────────


def cmd_rules(cfg: Config, argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("usage: rules <worktree>", file=sys.stderr)
        return 2
    sys.stdout.write(render_rules(cfg.rules, argv[0], cfg.label))
    return 0


# ── edit-secret ──────────────────────────────────────────────────────────


def edit_usage(cfg: Config) -> str:
    names = "\n".join(f"  {name}" for name in cfg.secret_names)
    return f"""\
Usage: edit-secret <name> [identity-file]

Decrypts a secret into $EDITOR and re-encrypts it to the recipients
declared in flake.nix. Creates it if it does not exist.

Declared secrets:
{names}

The recipient list is generated from `{cfg.option}.recipients`, so
there is no secrets.nix to keep in step. After changing that list, run
`rekey-secrets`.
"""


def cmd_edit_secret(cfg: Config, argv: Sequence[str]) -> int:
    if not argv or argv[0] in ("", "--help", "-h"):
        sys.stdout.write(edit_usage(cfg))
        return 0
    root = repo_root()
    rel = f"{cfg.rel_dir}/{argv[0]}.age"
    identity = argv[1] if len(argv) > 1 else ""

    env = dict(os.environ)
    # ryantm/agenix substitutes `cp -- /dev/stdin` for $EDITOR when stdin is
    # not a terminal, so `edit-secret foo <<EOF … EOF` just works there.
    # ragenix does not: it refuses with "Standard output is not a terminal".
    # Restore the documented behaviour, unconditionally: EDITOR is nano on
    # most machines, and an interactive editor cannot work without a terminal
    # anyway.
    if not sys.stdin.isatty():
        env["EDITOR"] = "cp /dev/stdin"

    with rules_file(cfg, root) as rules:
        env["RULES"] = rules
        command = [cfg.agenix, "-e", f"{root}/{rel}"]
        if identity:
            command += ["-i", identity]
        must(command, env=env)

    if stage(cfg, rel):
        print(f"staged new secret {rel} (age ciphertext is meant to be committed)")

    return agecheck(cfg)


# ── rekey-secrets ────────────────────────────────────────────────────────


def cmd_rekey_secrets(cfg: Config, argv: Sequence[str]) -> int:
    root = repo_root()
    print("Re-encrypting every declared secret to the current recipient list…")
    print("(you need to be able to decrypt them, so this cannot run in CI)")
    print()

    with rules_file(cfg, root) as rules:
        must([cfg.agenix, "-r", *argv], env=dict(os.environ, RULES=rules))

    print()
    code = agecheck(cfg)
    if code != 0:
        return code
    print()
    print("Commit the changed .age files: until you do, the recipient list in")
    print("flake.nix and the ciphertext on the branch disagree.")
    return 0


# ── setup-backup-access ──────────────────────────────────────────────────


def parse_env(text: str) -> dict[str, str]:
    """The BACKUPS_* values of an env-file setup-backup-access wrote.

    Parsed, not sourced: this is ciphertext someone else may have written,
    and sourcing it would execute whatever is in it. A value is taken as the
    shell would read it when it parses as one word (the single-quoted form
    this tool writes, with ' written as '\\''), and as it stands otherwise.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in FIELDS:
            continue
        try:
            words = shlex.split(value, comments=False, posix=True)
        except ValueError:
            words = []
        if len(words) == 1:
            values[key] = words[0]
        elif not words and value in ("", "''", '""'):
            values[key] = ""
        else:
            values[key] = value
    return values


def shell_quote(value: str) -> str:
    """Single-quoted: the file is sourced, and an S3 secret routinely
    contains characters the shell would otherwise expand. Embedded single
    quotes are closed, escaped and reopened the usual way."""
    return "'" + value.replace("'", "'\\''") + "'"


def render_env(values: Mapping[str, str], restore_command: str) -> str:
    lines = [f"# Written by setup-backup-access. Sourced by {restore_command}."]
    for key in FIELDS:
        value = values.get(key, "")
        if key == "BACKUPS_PREFIX" and not value:
            continue
        lines.append(f"{key}={shell_quote(value)}")
    return "\n".join(lines) + "\n"


def identities(cfg: Config) -> tuple[list[str], list[str]]:
    """(the configured identity paths, expanded; the readable ones)."""
    candidates: list[str] = []
    for raw in cfg.identity_paths:
        # Shell strings ("$HOME/.ssh/id_ed25519"), as agenix-shell expands
        # them. One path each: unlike the shell, never split on spaces.
        candidates.append(os.path.expandvars(raw))
    readable = [p for p in candidates if os.path.isfile(p) and os.access(p, os.R_OK)]
    return candidates, readable


def agenix_identity_args(paths: Sequence[str]) -> list[str]:
    """ragenix's -i takes several values (-i a b); repeating the flag is
    rejected with "cannot be used multiple times"."""
    return ["-i", *paths] if paths else []


def rage_identity_args(paths: Sequence[str]) -> list[str]:
    """rage's -i takes one value (-i a -i b). ryantm/agenix matches rage
    here, which is why the repeated form looks right for agenix too."""
    return [arg for path in paths for arg in ("-i", path)]


NOT_A_TERMINAL = """
To script it, pipe the env-file into edit-secret instead:

    edit-secret backup-access <<'ENV'
    BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
    BACKUPS_ACCESS_KEY=...
    BACKUPS_SECRET_KEY=...
    BACKUPS_BUCKET=...
    BACKUPS_PREFIX=
    ENV
"""


class Terminal:
    """The prompts and styled messages, on rich. Replaceable in tests."""

    def __init__(self) -> None:
        from rich.console import Console

        self.console = Console(highlight=False)
        self.errors = Console(stderr=True, highlight=False)

    def say(self, message: str = "", style: str | None = None) -> None:
        self.console.print(message, style=style, markup=False)

    def note(self, message: str) -> None:
        self.say(message, "yellow")

    def error(self, message: str) -> None:
        self.say(message, "red")

    def detail(self, message: str) -> None:
        """Indented, on stderr: what a failing command said."""
        for line in message.splitlines():
            self.errors.print(f"    {line}", markup=False, highlight=False)

    def panel(self, lines: Sequence[str], colour: str) -> None:
        from rich.panel import Panel
        from rich.text import Text

        self.console.print(
            Panel.fit(Text("\n".join(lines)), border_style=colour, padding=(0, 1))
        )

    def ask(
        self,
        header: str,
        example: str = "",
        current: str = "",
        *,
        password: bool = False,
        hint: str = "",
    ) -> str:
        from rich.prompt import Prompt
        from rich.text import Text

        prompt = Text(header, style="bold")
        extra = hint or (f"e.g. {example}" if example and not current else "")
        if extra:
            prompt.append(f" ({extra})", style="dim")
        return Prompt.ask(
            prompt,
            console=self.console,
            password=password,
            default=current,
            show_default=bool(current) and not password,
        )

    def confirm(self, question: str) -> bool:
        from rich.prompt import Confirm

        return Confirm.ask(question, console=self.console, default=True)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_setup_backup_access(
    cfg: Config,
    argv: Sequence[str],
    ui: "Terminal | None" = None,
    is_interactive: Callable[[], bool] = interactive,
) -> int:
    access = cfg.backup_access
    if access is None:
        print(
            f"{cfg.label}: setup-backup-access was built without a fetch command.",
            file=sys.stderr,
        )
        return 2

    # Never a prompt without a terminal: a script gets a refusal and the way
    # to do it non-interactively, not a read that blocks.
    if not is_interactive():
        print(
            f"{cfg.label}: setup-backup-access needs an interactive terminal.",
            file=sys.stderr,
        )
        sys.stderr.write(NOT_A_TERMINAL)
        return 1

    ui = ui or Terminal()
    root = repo_root()
    rel = f"{cfg.rel_dir}/backup-access.age"

    # ── identities ──
    # Same list agenix-shell decrypts with, so "it worked in the shell" and
    # "I can write the secret" cannot disagree.
    candidates, readable = identities(cfg)
    if not readable:
        ui.error(f"No readable SSH key among: {' '.join(candidates)}")
        ui.say()
        ui.say("agenix needs one of those to encrypt to itself, so that you can read")
        ui.say("back what you are about to write. Point identityPaths at your key:")
        ui.say()
        ui.say(f'    {cfg.option}.identityPaths = [ "$HOME/.ssh/id_something" ];')
        return 1

    ui.panel(
        [
            f"Backup access for {Path.cwd().name}",
            "",
            f"These are the object-store credentials `{access.restore_command}` uses to find",
            "and download production backups. They are encrypted to this project's",
            f"declared recipients and committed as {rel}.",
        ],
        "blue",
    )

    # ── existing values, if any ──
    # Editing is the common case after the first run (a rotated key changes
    # two of five fields), so pre-fill from whatever is already there.
    current: dict[str, str] = {}
    if os.path.isfile(rel):
        # rage, not agenix: ragenix implements only --edit/--rekey/--schema,
        # so there is no `agenix -d` to read the current values back with.
        read = subprocess.run(
            [cfg.rage, "--decrypt", *rage_identity_args(readable), rel],
            check=False,
            capture_output=True,
            text=True,
        )
        if read.returncode == 0:
            current = parse_env(read.stdout)
            ui.note("Editing the existing secret; current values are pre-filled.")
        else:
            ui.note(f"{rel} exists but none of your keys can read it.")
            ui.note("Continuing will overwrite it with what you enter.")
            if not ui.confirm("Overwrite it?"):
                return 1

    ui.say()

    url = ui.ask(
        "Endpoint URL",
        "https://s3.us-east-005.backblazeb2.com",
        current.get("BACKUPS_URL", ""),
    )
    if not url:
        ui.error("An endpoint is required.")
        return 1
    if not url.startswith(("http://", "https://")):
        ui.note("That has no scheme; assuming https://")
        url = f"https://{url}"

    access_key = ui.ask(
        "Access key ID",
        "005ecc20398e6520000000004",
        current.get("BACKUPS_ACCESS_KEY", ""),
    )
    if not access_key:
        ui.error("An access key is required.")
        return 1

    # Hidden: the only one of the five worth keeping off the scrollback, which
    # also means it cannot be pre-filled visibly like the others. Blank keeps
    # the one already stored, so rotating an access key does not mean
    # re-typing a secret that has not changed.
    stored_secret = current.get("BACKUPS_SECRET_KEY", "")
    secret = ui.ask(
        "Secret access key",
        password=True,
        hint="leave blank to keep the stored one" if stored_secret else "",
    )
    if not secret:
        if stored_secret:
            secret = stored_secret
            ui.note("Keeping the stored secret key.")
        else:
            ui.error("A secret key is required.")
            return 1

    bucket = ui.ask("Bucket", access.bucket_example, current.get("BACKUPS_BUCKET", ""))
    if not bucket:
        ui.error("A bucket is required.")
        return 1

    stored_prefix = current.get("BACKUPS_PREFIX", "")
    prefix = ui.ask(
        "Path inside the bucket (optional)",
        "Backups",
        stored_prefix,
        hint="- for none" if stored_prefix else "",
    )
    if prefix == "-":
        prefix = ""

    values = {
        "BACKUPS_URL": url,
        "BACKUPS_ACCESS_KEY": access_key,
        "BACKUPS_SECRET_KEY": secret,
        "BACKUPS_BUCKET": bucket,
        "BACKUPS_PREFIX": prefix,
    }

    # ── does it work? ──
    # Checked before encrypting, because a typo in a key is otherwise a
    # mystery several minutes into the first restore. The credentials go to
    # the product's check and nowhere else.
    ui.say()
    if ui.confirm("Test these against the bucket now?"):
        sys.stdout.flush()
        check = subprocess.run(
            [access.verify_listing],
            check=False,
            env=dict(os.environ, FETCH=access.fetch, **values),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        listing = check.stdout.rstrip("\n")
        if check.returncode == 0:
            ui.say(f"✓ {listing}", "green")
        else:
            ui.error("Could not list the bucket:")
            ui.detail(listing)
            ui.say()
            if not ui.confirm("Save anyway?"):
                return 1

    # ── write it ──
    # EDITOR is how agenix takes content without opening an editor. Both it
    # and ragenix accept an absolute path, which is what the rules name.
    payload = render_env(values, access.restore_command)
    with rules_file(cfg, root) as rules:
        must(
            [cfg.agenix, "-e", f"{root}/{rel}", *agenix_identity_args(readable)],
            env=dict(os.environ, EDITOR="cp /dev/stdin", RULES=rules),
            input=payload,
            text=True,
        )

    stage(cfg, rel)

    ui.say()
    agecheck(cfg)

    ui.panel(
        [f"Wrote {rel}", "", f"git commit {rel}", access.restore_command],
        "green",
    )
    return 0


# ── entry point ──────────────────────────────────────────────────────────

TOOLS = {
    "rules": cmd_rules,
    "edit-secret": cmd_edit_secret,
    "rekey-secrets": cmd_rekey_secrets,
    "setup-backup-access": cmd_setup_backup_access,
}


def main(argv: Sequence[str], *, config: str | os.PathLike) -> int:
    """``argv`` is the tool's name, then its arguments."""
    if not argv or argv[0] not in TOOLS:
        print(f"usage: {{{','.join(TOOLS)}}} [args...]", file=sys.stderr)
        return 2
    try:
        cfg = Config.load(config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"{argv[0]}: bad configuration {config}: {exc}", file=sys.stderr)
        return 2
    try:
        return TOOLS[argv[0]](cfg, argv[1:])
    except Exit as exc:
        return exc.code
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except EOFError:
        print(file=sys.stderr)
        return 1
