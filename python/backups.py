"""Backup store: list, fetch and cache backup sets, from an S3-protocol object
store (through rclone) or a plain directory.

Product-independent and standard library only. A product either imports this
module in-process, on whatever interpreter its own command line runs on (3.11
and up), or runs it as a command (`lib.backups.fetch` builds one). It supplies:

    label       its name: the prefix of every message ("<label>: ...") and of
                the throwaway rclone remote
    env_prefix  its environment prefix <P>; the overrides below are <P>_NIX_*
    layout      the parser from an object listing to backup sets (see Layout)

The connection comes from the backup-access secret, a shell env-file:

    BACKUPS_URL  BACKUPS_ACCESS_KEY  BACKUPS_SECRET_KEY  BACKUPS_BUCKET
    BACKUPS_PREFIX (optional)
    BACKUPS_REGION (optional; by default read from BACKUPS_URL's host name,
                   s3.<region>.<provider>, as AWS, Backblaze and Wasabi name
                   theirs, and us-east-1 otherwise, which MinIO and R2 accept)

Overrides:

    <P>_NIX_BACKUP_SOURCE  a directory laid out like the bucket, or any rclone
                           target (<remote>:<bucket>[/prefix], a remote of the
                           user's rclone.conf), used instead of BACKUPS_*: a
                           restic mount, a fixture
    <P>_NIX_RCLONE         the rclone binary (default: rclone on PATH)
    <P>_NIX_BACKUP_CACHE   the download cache, when the caller names none

minio-client was the client until nixpkgs marked it insecure (abandoned
upstream). Its two names are refused with what replaced them:
<P>_NIX_MC (and a product's legacy name for it), and an mc target
(<alias>/<bucket>[/prefix]) as <P>_NIX_BACKUP_SOURCE.

The cache key is the backup set's name. A backup is immutable once written, so
the name is the version: there is no staleness window and nothing to hash.
Downloads land on a .partial file and are renamed only once the size matches
the listing, so an interrupted transfer is never served from cache as whole.
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

CREDENTIALS = (
    "BACKUPS_URL",
    "BACKUPS_ACCESS_KEY",
    "BACKUPS_SECRET_KEY",
    "BACKUPS_BUCKET",
)

#: Manifest keys the base writes; a layout's `meta` may not reuse them.
RESERVED = frozenset({"name", "at", "scope", "source", "available"})


class BackupError(Exception):
    """A user-facing failure: missing credentials, nothing found, a refusal.

    `details` are further lines, printed indented under the message.
    """

    def __init__(self, message: str, *details: str) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def render(self, label: str) -> str:
        return "\n".join(
            [
                f"{label}: {self.message}",
                *(f"  {line}" if line else "" for line in self.details),
            ]
        )


@dataclass(frozen=True, order=True)
class Entry:
    """One object (or, for a directory store, one directory) in a listing.

    `key` is relative to the location that was listed, '/'-separated, with no
    trailing '/'. A listing of an object store carries no directories; a
    listing of a directory does, so an empty folder is visible there.
    """

    key: str
    size: int
    is_dir: bool = False


@dataclass(frozen=True)
class BackupSet:
    """One restorable backup: what `list` prints and `fetch` downloads.

    name     what `list` prints, `--at` matches exactly, and the cache directory
             is called. Names must sort in time order, as both timestamp
             layouts in use do.
    at       the time the backup was taken, as its name records it
    objects  role -> the object holding it (an Entry from the listing)
    meta     anything else the layout read off the names; copied into the
             manifest (an archive's site slug, whether it is encrypted)
    problem  set when the backup exists but must not be restored (a partial
             dump): picking it fails with this message, before any download
    """

    name: str
    at: datetime
    objects: Mapping[str, Entry]
    meta: Mapping[str, Any] = field(default_factory=dict)
    problem: str | None = None


class Layout(Protocol):
    """How a product names its backups. Usually a module, which satisfies this
    structurally:

        ROLES: tuple[str, ...]           every role a set may carry, in
                                         manifest order
        OPTIONAL_ROLES: tuple[str, ...]  (optional) roles fetched only on
                                         request: `fetch --<role>` with '_'
                                         spelled '-', or fetch(roles=...)
        HINT: str                        (optional) what a source with no
                                         backups should have looked like
        parse(listing) -> sets           the parser

    `parse` receives the recursive listing of the store (or of one scope in
    it, such as a database's folder) as a sequence of Entry, and returns an
    iterable of BackupSet, or of mappings with BackupSet's fields. It needs no
    import from this module, so the product's layout file stands on its own.
    Anything in the listing that is not a backup is ignored, not reported.
    """

    ROLES: tuple[str, ...]

    def parse(
        self, listing: Sequence[Entry]
    ) -> Iterable[BackupSet | Mapping[str, Any]]: ...


@dataclass(frozen=True)
class Fetched:
    """What `fetch` downloaded: the chosen set and a local path per role."""

    backup: BackupSet
    scope: str
    source: str
    roles: tuple[str, ...]
    paths: Mapping[str, Path | None]

    def manifest(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.backup.name,
            "at": self.backup.at.isoformat(),
            "scope": self.scope,
            "source": self.source,
        }
        out.update(self.backup.meta)
        for role in self.roles:
            path = self.paths.get(role)
            out[role] = str(path) if path is not None else None
        out["available"] = {role: role in self.backup.objects for role in self.roles}
        return out


def parse_at(at: str) -> str:
    """The digits of a point in time, for a prefix match against a set's
    YYYYMMDDHHMMSS.

    Accepts ISO 8601 (2026-10-02, 2026-10-02T03:00, 2026-10-02 03:00:00Z) and
    the products' own namings (20261002_030000, 2026_10_02_03_00_00), each
    truncated at any field: "2026-10" is the newest backup of October 2026.
    Times are the backup's own clock; a trailing Z is accepted and ignored.
    """
    text = at.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1]
    digits = re.sub(r"[-_:T ]", "", text)
    if not re.fullmatch(r"\d{4,14}", digits) or not re.fullmatch(
        r"\d[\d\-_:T ]*", text
    ):
        raise BackupError(
            f"--at {at!r} is not a point in time.",
            "",
            "Give an ISO 8601 date or time (2026-10-02, 2026-10-02T03:00:00), or a",
            "backup's own name as `list` prints it, or any leading part of either.",
        )
    return digits


def pick(sets: Sequence[BackupSet], at: str | None) -> BackupSet:
    """The newest set, or the newest one matching AT: a set's exact name, or a
    point in time (see parse_at)."""
    if not sets:
        raise BackupError("no backups found")
    if not at:
        return sets[-1]
    exact = [s for s in sets if s.name == at]
    if exact:
        return exact[-1]
    digits = parse_at(at)
    matching = [s for s in sets if s.at.strftime("%Y%m%d%H%M%S").startswith(digits)]
    if not matching:
        raise BackupError(
            f"no backup matches --at {at}",
            "",
            f"Available ({len(sets)}, oldest first):",
            *(s.name for s in sets[-20:]),
        )
    return matching[-1]


def _private_dir(path: Path) -> None:
    """Create PATH if missing, and make it the owner's alone either way."""
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _sanitize(location: str) -> str:
    """A cache directory name for a source: one path component, no separators."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", location.rstrip("/") + "/")


def _as_set(item: BackupSet | Mapping[str, Any]) -> BackupSet:
    if isinstance(item, BackupSet):
        return item
    return BackupSet(
        name=item["name"],
        at=item["at"],
        objects=dict(item.get("objects", {})),
        meta=dict(item.get("meta", {})),
        problem=item.get("problem"),
    )


class Store:
    """One backup location: a directory, or an rclone target."""

    def __init__(
        self,
        location: str,
        *,
        local: bool,
        rclone: str,
        label: str,
        env: Mapping[str, str] | None = None,
    ):
        self.location = location.rstrip("/") if location != "/" else location
        self.local = local
        self.rclone = rclone
        self.label = label
        self.env = dict(env) if env is not None else None

    def path(self, *parts: str) -> str:
        return "/".join(
            p.strip("/") if i else p for i, p in enumerate((self.location, *parts)) if p
        )

    def _run(
        self, *args: str, missing_ok: bool = False
    ) -> subprocess.CompletedProcess[str] | None:
        """rclone ARGS. With `missing_ok`, None for exit status 3, rclone's
        "directory not found": an S3 prefix with nothing under it."""
        try:
            proc = subprocess.run(
                [self.rclone, *args],
                capture_output=True,
                text=True,
                check=False,
                env=self.env,
            )
        except FileNotFoundError as exc:
            raise BackupError(
                f"rclone ({self.rclone!r}) not found; run this from the {self.label} dev shell."
            ) from exc
        if missing_ok and proc.returncode == 3:
            return None
        if proc.returncode != 0:
            raise BackupError(
                f"rclone {args[0]} failed",
                *(proc.stderr or proc.stdout).strip().splitlines(),
            )
        return proc

    def ls(self, sub: str = "", *, recursive: bool = False) -> list[Entry]:
        """Entries under SUB (relative to the store), keys relative to SUB."""
        where = self.path(sub)
        if self.local:
            root = Path(where)
            if not root.is_dir():
                return []
            found = root.rglob("*") if recursive else root.iterdir()
            return sorted(
                Entry(
                    p.relative_to(root).as_posix(),
                    p.stat().st_size if p.is_file() else 0,
                    p.is_dir(),
                )
                for p in found
                if p.is_dir() or p.is_file()
            )
        # --files-only when recursive: an object store has no directories,
        # only the prefixes of the objects, which the keys already carry.
        # No modification times or MIME types: neither is used, and reading
        # them can cost a request per object.
        args = [
            "lsjson",
            "--no-modtime",
            "--no-mimetype",
            *(["--recursive", "--files-only", "--fast-list"] if recursive else []),
            where,
        ]
        proc = self._run(*args, missing_ok=True)
        if proc is None:
            return []
        return sorted(
            Entry(
                item["Path"].rstrip("/"),
                max(int(item.get("Size", 0)), 0),
                bool(item.get("IsDir")),
            )
            for item in json.loads(proc.stdout or "[]")
        )

    def copy(self, sub: str, dest: Path) -> None:
        """Download the object SUB (relative to the store) to DEST."""
        src = self.path(sub)
        if self.local:
            shutil.copyfile(src, dest)
        else:
            self._run("copyto", "--quiet", src, str(dest))

    def mirror(self, source: str, dest: Path) -> None:
        """Mirror a bucket path (`bucket/prefix`, reached through this store's
        credentials) into DEST; a repeat run copies only what changed, and
        nothing in DEST is deleted."""
        if self.local:
            raise BackupError(
                "nothing to mirror from: the backup source is a directory"
            )
        remote = self.location.split(":", 1)[0]
        dest.mkdir(parents=True, exist_ok=True)
        self._run(
            "copy",
            "--quiet",
            f"{remote}:{source.strip('/')}",
            str(dest),
        )


def region(url: str) -> str:
    """The signing region for an S3 endpoint: BACKUPS_REGION, else the one
    the host name carries (s3.<region>.amazonaws.com, s3-<region>..., as AWS,
    Backblaze and Wasabi name their endpoints), else us-east-1, which MinIO
    and Cloudflare R2 accept. minio-client asked the bucket; rclone needs it
    up front."""
    explicit = os.environ.get("BACKUPS_REGION", "").strip()
    if explicit:
        return explicit
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/", 1)[0]
    found = re.search(
        r"(?:^|\.)s3[.-]([a-z]{2}(?:-[a-z0-9]+)+)\.", host.split(":", 1)[0]
    )
    return found.group(1) if found else "us-east-1"


class Backups:
    """The fetch layer for one product: its names, its layout, its overrides."""

    def __init__(
        self,
        *,
        label: str,
        env_prefix: str,
        layout: Layout | ModuleType,
        legacy_env: Mapping[str, str] | None = None,
    ) -> None:
        self.label = label
        self.env_prefix = env_prefix
        self.layout = layout
        self.roles: tuple[str, ...] = tuple(layout.ROLES)
        self.optional: tuple[str, ...] = tuple(getattr(layout, "OPTIONAL_ROLES", ()))
        self.hint: str = getattr(layout, "HINT", "")
        # A suffix such as BACKUP_SOURCE -> the name the product used before
        # <P>_NIX_BACKUP_SOURCE, read with a warning while it is an alias.
        self.legacy_env = dict(legacy_env or {})
        self.remote = re.sub(r"[^a-z0-9]", "", label.lower()) or "backups"
        self._warned: set[str] = set()
        unknown = set(self.optional) - set(self.roles)
        if unknown:
            raise ValueError(f"layout OPTIONAL_ROLES not in ROLES: {sorted(unknown)}")

    @property
    def alias(self) -> str:
        """`remote`'s name from when the client was minio-client and this was
        its alias, kept for a product that still reads it."""
        return self.remote

    # ── environment ──────────────────────────────────────────────────────
    def var(self, suffix: str) -> str:
        return f"{self.env_prefix}_NIX_{suffix}"

    def env(self, suffix: str) -> str | None:
        value = os.environ.get(self.var(suffix))
        if value:
            return value
        old = self.legacy_env.get(suffix)
        if old and os.environ.get(old):
            if old not in self._warned:
                self._warned.add(old)
                print(
                    f"{self.label}: {old} is deprecated; set {self.var(suffix)} instead.",
                    file=sys.stderr,
                )
            return os.environ[old]
        return None

    def missing_credentials(self) -> list[str]:
        return [name for name in CREDENTIALS if not os.environ.get(name)]

    def has_credentials(self) -> bool:
        return bool(self.env("BACKUP_SOURCE")) or not self.missing_credentials()

    def is_local(self) -> bool:
        """True when the source is a directory, so there is nothing to mirror."""
        source = self.env("BACKUP_SOURCE")
        return bool(source) and self._looks_local(source)

    @staticmethod
    def _looks_local(source: str) -> bool:
        # An rclone target is <remote>:<path>, and a remote's name never
        # starts with a path character, so a path that does not exist yet is
        # still a (missing, so empty) directory, not a remote.
        return source.startswith(("/", "./", "../", "~")) or os.path.isdir(source)

    def _refuse_mc(self, source: str | None) -> None:
        """minio-client's names, gone with it, refused with what replaced them."""
        for name in (self.var("MC"), self.legacy_env.get("MC")):
            if name and os.environ.get(name):
                raise BackupError(
                    f"{name} names a minio-client binary, which is no longer used.",
                    "",
                    "Backups are read with rclone now (nixpkgs dropped minio-client as",
                    "abandoned and insecure). Unset it; to name another rclone binary, set",
                    f"{self.var('RCLONE')}.",
                )
        remote = (source or "").split("/", 1)[0]
        if source and not self._looks_local(source) and ":" not in remote:
            raise BackupError(
                f"{self.var('BACKUP_SOURCE')}={source} looks like a minio-client target"
                " (<alias>/<bucket>/...).",
                "",
                "Backups are read with rclone now: name a remote from your rclone.conf",
                f"as <remote>:<bucket>[/prefix], e.g. {self.var('BACKUP_SOURCE')}=myremote:{source.split('/', 1)[-1]}",
                "(`rclone config` creates one), or point it at a directory.",
            )

    @contextlib.contextmanager
    def store(self) -> Iterator[Store]:
        """The store backups are read from, with a throwaway rclone remote for
        the duration when the credentials come from BACKUPS_*."""
        source = self.env("BACKUP_SOURCE")
        self._refuse_mc(source)
        rclone = self.env("RCLONE") or "rclone"
        if source:
            local = self._looks_local(source)
            yield Store(
                os.path.expanduser(source) if local else source,
                local=local,
                rclone=rclone,
                label=self.label,
            )
            return
        missing = self.missing_credentials()
        if missing:
            raise BackupError(
                f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set.",
                "",
                "These come from the 'backup-access' agenix secret, a shell env-file defining",
                "BACKUPS_URL, BACKUPS_ACCESS_KEY, BACKUPS_SECRET_KEY, BACKUPS_BUCKET and",
                "optionally BACKUPS_PREFIX and BACKUPS_REGION.",
                "",
                "  setup-backup-access          write it",
                "  check-secrets                is your key a recipient?",
                "",
                f"Or point at a directory instead:  {self.var('BACKUP_SOURCE')}=/path/to/backups",
            )
        # The remote is defined in rclone's own environment, which only rclone
        # sees: not in argv (readable in /proc), not in a URL (no percent-
        # encoding, so a secret holding '/' or '+', routine on S3 and
        # universal on Backblaze, signs as written), and not in the user's
        # rclone.conf. RCLONE_CONFIG names a file in a private directory that
        # is never created, so no remote of the user's, even one of the same
        # name, adds settings to this one.
        name = f"RCLONE_CONFIG_{self.remote.upper()}_"
        with tempfile.TemporaryDirectory(prefix=f"{self.remote}-rclone-") as tmp:
            env = {
                **os.environ,
                "RCLONE_CONFIG": str(Path(tmp, "rclone.conf")),
                name + "TYPE": "s3",
                name + "PROVIDER": "Other",
                name + "ENV_AUTH": "false",
                name + "ENDPOINT": os.environ["BACKUPS_URL"],
                name + "REGION": region(os.environ["BACKUPS_URL"]),
                name + "ACCESS_KEY_ID": os.environ["BACKUPS_ACCESS_KEY"],
                name + "SECRET_ACCESS_KEY": os.environ["BACKUPS_SECRET_KEY"],
                # Read-only here: never try to create the bucket.
                name + "NO_CHECK_BUCKET": "true",
            }
            prefix = os.environ.get("BACKUPS_PREFIX", "").strip("/")
            location = f"{self.remote}:" + "/".join(
                p for p in (os.environ["BACKUPS_BUCKET"], prefix) if p
            )
            yield Store(
                location,
                local=False,
                rclone=rclone,
                label=self.label,
                env=env,
            )

    # ── discovery ────────────────────────────────────────────────────────
    def folders(self, store: Store, scope: str = "") -> list[str]:
        """Folder names directly under SCOPE: the databases, in a layout that
        keeps one folder per database."""
        return sorted(e.key for e in store.ls(scope) if e.is_dir)

    def sets(self, store: Store, scope: str = "") -> list[BackupSet]:
        """Every backup set under SCOPE, oldest first."""
        found = [
            _as_set(item) for item in self.layout.parse(store.ls(scope, recursive=True))
        ]
        names: set[str] = set()
        for s in found:
            if not s.name or "/" in s.name or s.name in (".", ".."):
                raise ValueError(f"layout returned an unusable set name: {s.name!r}")
            if s.name in names:
                raise ValueError(f"layout returned the set name {s.name!r} twice")
            names.add(s.name)
            if not isinstance(s.at, datetime):
                raise TypeError(f"layout returned a non-datetime `at` for {s.name!r}")
            if set(s.objects) - set(self.roles):
                raise ValueError(
                    f"layout returned roles outside ROLES for {s.name!r}: {sorted(s.objects)}"
                )
            clash = (RESERVED | set(self.roles)) & set(s.meta)
            if clash:
                raise ValueError(
                    f"layout meta for {s.name!r} reuses manifest keys: {sorted(clash)}"
                )
        return sorted(found, key=lambda s: (s.at, s.name))

    def require_sets(self, store: Store, scope: str = "") -> list[BackupSet]:
        """sets(), failing with what the source does hold when it holds none."""
        found = self.sets(store, scope)
        if found:
            return found
        where = store.path(scope)
        try:
            entries = store.ls(scope)
        except BackupError:
            entries = []
        shown = [f"  {e.key}/" if e.is_dir else f"  {e.key}" for e in entries[:20]]
        raise BackupError(
            f"no backups under {where}/",
            *([""] + self.hint.strip().splitlines() if self.hint.strip() else []),
            "",
            "If they sit under a prefix, set BACKUPS_PREFIX in the backup-access secret.",
            *(["", "What is there:", *shown] if shown else ["", "It is empty."]),
        )

    # ── download ─────────────────────────────────────────────────────────
    def fetch(
        self,
        store: Store,
        scope: str = "",
        *,
        dest: Path | None = None,
        at: str | None = None,
        roles: Iterable[str] = (),
        use_cache: bool = True,
        keep: int = 2,
        progress: Callable[[str], None] | None = None,
    ) -> Fetched:
        """Download one backup set into DEST/<source>/<set name>/.

        Every role the set has is fetched, except OPTIONAL_ROLES, which are
        fetched only when named in ROLES. KEEP is how many sets stay cached
        per source, the one just fetched always among them (0 keeps all).
        """
        say = progress or (lambda _msg: None)
        extra = set(roles)
        unknown = extra - set(self.optional)
        if unknown:
            raise BackupError(
                f"not an optional part of a backup: {', '.join(sorted(unknown))}"
            )
        if dest is None:
            cache = self.env("BACKUP_CACHE")
            if not cache:
                raise BackupError(
                    f"no download directory: pass one, or set {self.var('BACKUP_CACHE')}"
                )
            dest = Path(cache)

        chosen = pick(self.require_sets(store, scope), at)
        if chosen.problem:
            raise BackupError(*chosen.problem.splitlines())
        say(f"backup: {chosen.name}")

        # A cached backup is a copy of production's data: the directories this
        # creates are the owner's alone, and so is every file in them. DEST
        # itself is the caller's and keeps its mode.
        source_dir = Path(dest) / _sanitize(store.path(scope))
        set_dir = source_dir / chosen.name
        Path(dest).mkdir(parents=True, exist_ok=True)
        _private_dir(source_dir)
        _private_dir(set_dir)

        paths: dict[str, Path | None] = {}
        for role in self.roles:
            obj = chosen.objects.get(role)
            if obj is None or (role in self.optional and role not in extra):
                paths[role] = None
                continue
            paths[role] = self._fetch_one(store, scope, obj, set_dir, use_cache, say)

        if keep > 0:
            others = sorted(
                p for p in source_dir.iterdir() if p.is_dir() and p != set_dir
            )
            for old in others[: max(len(others) - keep + 1, 0)]:
                shutil.rmtree(old, ignore_errors=True)

        return Fetched(chosen, scope, store.path(scope), self.roles, paths)

    def _fetch_one(
        self,
        store: Store,
        scope: str,
        obj: Entry,
        set_dir: Path,
        use_cache: bool,
        say: Callable[[str], None],
    ) -> Path:
        local = set_dir / obj.key.rsplit("/", 1)[-1]
        if use_cache and local.is_file() and local.stat().st_size == obj.size:
            say(f"cached  {obj.key}")
            local.chmod(0o600)
            return local
        say(f"fetch   {obj.key}")
        partial = local.with_name(local.name + ".partial")
        partial.unlink(missing_ok=True)
        try:
            store.copy("/".join(p for p in (scope, obj.key) if p), partial)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        have = partial.stat().st_size if partial.exists() else 0
        if have != obj.size:
            partial.unlink(missing_ok=True)
            raise BackupError(
                f"short download: {obj.key} got {have} of {obj.size} bytes"
            )
        partial.chmod(0o600)
        partial.replace(local)
        return local

    # ── command line ─────────────────────────────────────────────────────
    def parser(self, prog: str | None = None) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(
            prog=prog,
            description="List and download backups from the backup store.",
            epilog=(
                f"Source: BACKUPS_URL, BACKUPS_ACCESS_KEY, BACKUPS_SECRET_KEY, BACKUPS_BUCKET and optionally "
                f"BACKUPS_PREFIX and BACKUPS_REGION (the backup-access secret), or {self.var('BACKUP_SOURCE')}: "
                f"a directory or an rclone target. {self.var('RCLONE')} names the rclone binary."
            ),
        )
        sub = p.add_subparsers(
            dest="mode", required=True, metavar="{list,folders,fetch}"
        )
        scope_help = "a folder under the store to look in, such as a database's (default: the store itself)"
        ls = sub.add_parser("list", help="print the backups, oldest first")
        ls.add_argument("scope", nargs="?", default="", help=scope_help)
        fo = sub.add_parser(
            "folders", help="print the folders under the store (or under SCOPE)"
        )
        fo.add_argument("scope", nargs="?", default="", help=scope_help)
        fe = sub.add_parser("fetch", help="download a backup and print a JSON manifest")
        fe.add_argument("scope", nargs="?", default="", help=scope_help)
        fe.add_argument(
            "--at",
            help="ISO 8601 date or time, or a backup's name, or a leading part of either",
        )
        for role in self.optional:
            fe.add_argument(
                f"--{role.replace('_', '-')}",
                dest="roles",
                action="append_const",
                const=role,
                help=f"also fetch {role.replace('_', ' ')}",
            )
        fe.add_argument(
            "--no-cache",
            action="store_true",
            help="download again even when the cached copy is whole",
        )
        fe.add_argument(
            "--dest",
            "--cache",
            type=Path,
            help=f"download directory (default: ${self.var('BACKUP_CACHE')})",
        )
        fe.add_argument(
            "--keep",
            type=int,
            default=2,
            help="backups to keep cached per source (default: 2)",
        )
        return p

    def main(self, argv: Sequence[str], prog: str | None = None) -> int:
        args = self.parser(prog).parse_args(list(argv))
        try:
            with self.store() as store:
                if args.mode == "list":
                    print(
                        "\n".join(s.name for s in self.require_sets(store, args.scope))
                    )
                elif args.mode == "folders":
                    names = self.folders(store, args.scope)
                    if names:
                        print("\n".join(names))
                else:
                    fetched = self.fetch(
                        store,
                        args.scope,
                        dest=args.dest,
                        at=args.at,
                        roles=args.roles or (),
                        use_cache=not args.no_cache,
                        keep=args.keep,
                        progress=lambda msg: print(f"  {msg}", file=sys.stderr),
                    )
                    print(json.dumps(fetched.manifest(), indent=2))
        except BackupError as exc:
            print(exc.render(self.label), file=sys.stderr)
            return 1
        return 0


def load_layout(
    path: str | os.PathLike[str], name: str = "backup_layout"
) -> ModuleType:
    """Import a layout from its file, for a command that is handed one."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load a layout from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(
    argv: Sequence[str],
    *,
    label: str,
    env_prefix: str,
    layout: Layout | ModuleType,
    legacy_env: Mapping[str, str] | None = None,
    prog: str | None = None,
) -> int:
    return Backups(
        label=label, env_prefix=env_prefix, layout=layout, legacy_env=legacy_env
    ).main(argv, prog)
