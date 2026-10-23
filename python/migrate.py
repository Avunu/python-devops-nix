"""Migrate on deploy, behind a verified snapshot (docs/library.md, Production).

The flow both products run before new code serves an old schema
(docs/conventions.md, Migrate on deploy):

    check     is there anything to do? (the product's command; optional)
    snapshot  dump the database into a private file, then verify the dump
              before anything relies on it
    migrate   the product's command
    success   the product's command (optional), then keep the newest N
              snapshots
    failure   restore the snapshot (rollbackOnFailure), then the product's
              failure command (optional), and exit with the migration's status

Each step but the flow itself is the product's: an executable the
configuration names. The product decides what a failed migrate leaves
running: frappe-nix keeps serving in Frappe's maintenance mode (its migrate
command turns it on, its failure command says so); odoo-nix's service
`Requires=` the migrate unit, so a failed migrate keeps Odoo down.

The configuration is a JSON object (``lib.nixos.migrate.script`` writes it):

    label      the message prefix: "<label>(<target>): ..."
    target     what is migrated: a site, a database
    check      exit 0 to migrate, SKIP (100) for nothing to do (it says why),
               anything else is an error; null to always migrate
    migrate    the migration
    onSuccess  after a migration that exited 0; a failure here is a warning
    onFailure  after a failed migration and its rollback
    rollbackOnFailure
    snapshot   null for none, or:
      dir        the snapshot directory, made (and kept) 0700
      prefix     snapshot file names: <prefix>-YYYYmmdd-HHMMSS<extension>
      extension  e.g. ".sql.gz", ".dump"
      retention  how many to keep, newest first, after a successful migration
      dump       writes the dump to its stdout
      verify     `verify <snapshot>`: exit 0 when the dump is whole
      restore    `restore <snapshot>`: put the database back as the dump has it

Every command runs with MIGRATE_TARGET and, once there is one,
MIGRATE_SNAPSHOT in its environment; onFailure also with MIGRATE_EXIT (the
migration's status) and MIGRATE_ROLLBACK (restored, failed or none).

Snapshots are whole databases, so they are private where they are written:
the directory 0700 and each file 0600 from its first byte. Not by a unit-wide
UMask=0077: a migration also writes files a web server reads through the
service group (Frappe's theme CSS, frappe-nix#83; Odoo's filestore,
odoo-nix#51).

Messages go to stderr with a ``<N>`` syslog prefix when stderr is the journal
(journald.py), so a failed migration is found at ``journalctl -p err``.

Standard library only, written to Python 3.11. It runs as its own process
(``lib.nixos.migrate.script``) or is imported in-process by a product's own
migrate tool, beside journald.py.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

try:  # beside it in a package, or on sys.path as a command's modules
    from . import journald  # type: ignore[attr-defined]
except ImportError:
    import journald

#: The check command's "nothing to do".
SKIP = 100


class MigrateError(Exception):
    """A failure before the migration ran: nothing was changed."""


@dataclass(frozen=True)
class Snapshot:
    dir: Path
    prefix: str
    extension: str
    retention: int
    dump: str
    verify: str
    restore: str

    @classmethod
    def from_json(cls, data: Mapping) -> "Snapshot":
        retention = int(data["retention"])
        if retention < 1:
            raise ValueError("snapshot.retention must be at least 1")
        return cls(
            dir=Path(data["dir"]),
            prefix=data["prefix"],
            extension=data["extension"],
            retention=retention,
            dump=data["dump"],
            verify=data["verify"],
            restore=data["restore"],
        )


@dataclass(frozen=True)
class Config:
    label: str
    target: str
    migrate: str
    check: str | None = None
    on_success: str | None = None
    on_failure: str | None = None
    rollback_on_failure: bool = True
    snapshot: Snapshot | None = None

    @classmethod
    def from_json(cls, data: Mapping) -> "Config":
        known = {
            "label",
            "target",
            "migrate",
            "check",
            "onSuccess",
            "onFailure",
            "rollbackOnFailure",
            "snapshot",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
        snapshot = data.get("snapshot")
        return cls(
            label=data["label"],
            target=data["target"],
            migrate=data["migrate"],
            check=data.get("check"),
            on_success=data.get("onSuccess"),
            on_failure=data.get("onFailure"),
            rollback_on_failure=bool(data.get("rollbackOnFailure", True)),
            snapshot=Snapshot.from_json(snapshot) if snapshot else None,
        )

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Config":
        with open(path, encoding="utf-8") as stream:
            return cls.from_json(json.load(stream))


class Runner:
    """One target's migration. ``run()`` is the whole flow; ``take()``,
    ``restore()`` and ``prune()`` are its steps, for a product whose own
    migrate tool decides when a snapshot is due."""

    def __init__(self, config: Config, stderr=None) -> None:
        self.config = config
        self.stderr = stderr if stderr is not None else sys.stderr
        try:
            self.journal = journald.journal_stream(self.stderr.fileno())
        except (AttributeError, OSError, ValueError):
            self.journal = False
        self.env = dict(os.environ, MIGRATE_TARGET=config.target)

    # ── messages ─────────────────────────────────────────────────────────

    def say(self, message: str, prio: int = 6) -> None:
        line = f"{self.config.label}({self.config.target}): {message}"
        if self.journal:
            line = journald.prefix_lines(line, prio)
        self.stderr.write(line + "\n")
        self.stderr.flush()

    def warn(self, message: str) -> None:
        self.say(message, 4)

    def error(self, message: str) -> None:
        self.say(message, 3)

    # ── commands ─────────────────────────────────────────────────────────

    def call(
        self, command: str, *args: str, env: Mapping[str, str] | None = None, **kwargs
    ) -> int:
        self.stderr.flush()
        return subprocess.run(
            [command, *args],
            env=dict(self.env, **(env or {})),
            check=False,
            **kwargs,
        ).returncode

    # ── snapshot steps ───────────────────────────────────────────────────

    def snapshots(self) -> list[Path]:
        """This target's snapshots, oldest first."""
        snap = self.require_snapshot()
        if not snap.dir.is_dir():
            return []
        found = [
            path
            for path in snap.dir.iterdir()
            if path.name.startswith(f"{snap.prefix}-")
            and path.name.endswith(snap.extension)
            and path.is_file()
        ]
        return sorted(found, key=lambda path: (path.stat().st_mtime, path.name))

    def require_snapshot(self) -> Snapshot:
        if self.config.snapshot is None:
            raise MigrateError("no snapshot is configured")
        return self.config.snapshot

    def private_dir(self) -> Path:
        """The snapshot directory, created and tightened to 0700. It holds
        whole databases, and the service group may include a web server."""
        directory = self.require_snapshot().dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        return directory

    def new_file(self) -> tuple[Path, int]:
        """A new snapshot file, 0600 from its first byte whatever the umask."""
        snap = self.require_snapshot()
        directory = self.private_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for n in range(100):
            suffix = f"-{n}" if n else ""
            path = directory / f"{snap.prefix}-{stamp}{suffix}{snap.extension}"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            os.fchmod(fd, 0o600)
            return path, fd
        raise MigrateError(f"could not name a new snapshot in {directory}")

    def take(self) -> Path:
        """Dump, then verify. A dump that fails or does not verify is removed
        and raises MigrateError: a truncated safety net is worse than none,
        because it is trusted."""
        snap = self.require_snapshot()
        path, fd = self.new_file()
        self.say(f"taking pre-migrate snapshot -> {path}")
        try:
            rc = self.call(snap.dump, stdout=fd)
            # The dump is the way back if the host goes down mid-migration:
            # on disk before anything relies on it, not only in the page cache.
            if rc == 0:
                os.fsync(fd)
        finally:
            os.close(fd)
        if rc != 0:
            path.unlink(missing_ok=True)
            raise MigrateError(f"pre-migrate snapshot failed (dump exit {rc})")
        if path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            raise MigrateError("pre-migrate snapshot is empty")
        rc = self.call(snap.verify, str(path), env={"MIGRATE_SNAPSHOT": str(path)})
        if rc != 0:
            path.unlink(missing_ok=True)
            raise MigrateError(
                f"pre-migrate snapshot did not verify (exit {rc}); removed it"
            )
        self.say("snapshot verified")
        return path

    def restore(self, path: Path) -> bool:
        snap = self.require_snapshot()
        self.warn(f"rolling back from {path}")
        rc = self.call(snap.restore, str(path), env={"MIGRATE_SNAPSHOT": str(path)})
        if rc == 0:
            self.warn("rollback complete; restored the pre-migrate snapshot")
            return True
        self.error(
            f"ERROR rollback FAILED (exit {rc}); the database may be inconsistent. Snapshot preserved at {path}"
        )
        return False

    def prune(self) -> list[Path]:
        """Keep the newest ``retention`` snapshots; return the ones removed."""
        snap = self.require_snapshot()
        stale = self.snapshots()[: -snap.retention]
        for path in stale:
            path.unlink(missing_ok=True)
        if stale:
            self.say(f"pruned {len(stale)} old snapshot(s), keeping {snap.retention}")
        return stale

    # ── the flow ─────────────────────────────────────────────────────────

    def run(self) -> int:
        config = self.config
        if config.check is not None:
            rc = self.call(config.check)
            if rc == SKIP:
                return 0
            if rc != 0:
                self.error(
                    f"ERROR the check before migrating failed (exit {rc}); nothing was changed"
                )
                return rc

        path = None
        if config.snapshot is not None:
            try:
                path = self.take()
            except (MigrateError, OSError) as exc:
                self.error(f"ERROR {exc}; aborting before migrate (no safety net)")
                return 1

        snap_env = {"MIGRATE_SNAPSHOT": str(path)} if path else {}
        rc = self.call(config.migrate, env=snap_env)
        if rc == 0:
            if config.on_success is not None:
                hook = self.call(config.on_success, env=snap_env)
                if hook != 0:
                    self.warn(f"warning: the success step exited {hook}")
            if config.snapshot is not None:
                self.prune()
            self.say("migrate OK")
            return 0

        self.error(f">>> MIGRATION FAILED (exit {rc}) <<<")
        rollback = "none"
        if config.rollback_on_failure and path is not None:
            rollback = "restored" if self.restore(path) else "failed"
        elif config.rollback_on_failure and config.snapshot is not None:
            self.error("no snapshot to roll back to")
        if config.on_failure is not None:
            self.call(
                config.on_failure,
                env=dict(snap_env, MIGRATE_EXIT=str(rc), MIGRATE_ROLLBACK=rollback),
            )
        return rc


def main(argv: Sequence[str], prog: str = "migrate") -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="Migrate behind a verified snapshot."
    )
    parser.add_argument("config", help="the JSON configuration")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="the whole flow (the default)")
    sub.add_parser("take", help="take and verify a snapshot; print its path")
    restore = sub.add_parser("restore", help="restore a snapshot")
    restore.add_argument("snapshot", type=Path)
    sub.add_parser("prune", help="keep the newest snapshots, by retention")
    sub.add_parser("list", help="this target's snapshots, oldest first")
    args = parser.parse_args(argv)

    runner = Runner(Config.load(args.config))
    try:
        if args.command in (None, "run"):
            return runner.run()
        if args.command == "take":
            print(runner.take())
        elif args.command == "restore":
            return 0 if runner.restore(args.snapshot) else 1
        elif args.command == "prune":
            runner.prune()
        elif args.command == "list":
            for path in runner.snapshots():
                print(path)
    except MigrateError as exc:
        runner.error(f"ERROR {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
