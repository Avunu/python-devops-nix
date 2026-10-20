"""Source repositories as git submodules: the first clone, and what shell entry
does about them.

Product-independent and standard library only (written to 3.11). A product
runs it as a command (`lib.sources.command` builds one), or copies it into its
own package and imports it (`lib.sources.module`). Two entry points:

    enter [ROOT]   what the development shell runs on every entry: check out,
                   once, the submodules a fresh clone of the project has never
                   had, keep the partial clones it made in shape, and only
                   report on the rest
    clone URL PATH the scaffolder's first clone of a source repository, in the
                   same shape shell entry makes

Ported from frappe-nix's lib/apps-report.nix and odoo-nix's
lib/submodules-init.nix, which had been ported into each other twice; the
reasoning below is theirs, and so are the messages, near enough verbatim.

Once per clone, not on every entry. Entry runs on every `nix develop` and every
direnv reload. Checking out any registered submodule it found without a
checkout (what both products once did) also brought back one removed by hand
or deinitialized, freshly cloned from its remote, on the next reload. So the
checkout is limited to a submodule this clone has never set up (`ever_set_up`);
one that has been set up and is now missing was taken out by someone, and
entry leaves it out and says how to bring it back. Past that, submodules move
only when someone moves them, or when the product's update command does.

Except one the first checkout did not finish. The first checkout is several
steps (clone, hand the clone to git, fetch the pin, check it out) and git
writes the index last, so a ^C or a killed shell in the middle leaves a `.git`
in the directory and no index. That is not a submodule someone took out; it is
one that was never put in, and until `classify` told the two apart it looked
checked out for good: the app was empty, `git status` showed every file staged
for deletion, and nothing ever retried (`checkout_unfinished`). Such a clone is
finished from the clone it has (`finish_checkout`), if its directory holds
nothing but the `.git`. With files in it they are somebody's (a checkout cut
short while writing them leaves some), and `update --force` would overwrite
them, so entry names the command and leaves them be.

Which makes two entries at once a hazard: the later finds the earlier one's
first checkout half done, which looks the same. So an entry holds a lock in the
project's git directory (`entry_lock`) and a second one does not wait for it:
it says so and leaves the sources to the first, and the next entry looks again.

One path at a time: a single `git submodule update` over several paths gives
up on all of them when one clone fails. .gitmodules, not `git submodule
status`, names the submodules: the latter dies on the first gitlink with no
.gitmodules entry, which is one of the shapes this exists to report.

The clone is a partial one, not the shallow one `shallow = true` in .gitmodules
asks for. A shallow `git submodule update` clones the remote's *default* branch
at depth 1 and then fetches the pinned commit, usually on another branch, with
no depth at all: slow, and it leaves the clone following a branch that is not
even the project's. Instead, the branch .gitmodules pairs the submodule with,
and only that branch, is cloned with its whole history of commits and folders
but no file contents (--filter=blob:none): `git log`, `git log -- <file>` and
merge-bases work offline, and a checkout downloads only the files it needs. A
path the product names as treeless (odoo-nix's OCB, whose folder history alone
is enormous) gets commits only (--filter=tree:0).

Only that branch, because the rest is as much again as the checkout and more:
frappe has 337 branches and erpnext 694; an OCA repository's other branches are
a dozen series of history. One who wants another branch fetches it first:

    git remote set-branches --add origin <branch> && git fetch origin && git switch <branch>

Not a sparse checkout, though translations are most of a Frappe app's files
and three quarters of OCB's: Nix copies a submodule from the working tree and
leaves out, without a word, a file sparse-checkout left out, so a build from
such a clone would ship without its translations.

Not treeless for an ordinary branch: a treeless clone has no folders for any
commit, so everything that walks history (`git log -p`, `git blame`, an
editor's history view) fetches them one commit at a time, each as a pack of its
own. frappe-nix measured 345 packs holding 1.9x the objects there were.

Two more things keep a partial clone from growing without bound, because git
does neither:

  - the pinned commit is fetched, the ordinary way, before anything can ask
    git for a commit the clone lacks (`prefetch_pin`). `git submodule update`
    to a pin the clone has not got first asks the clone for it, and a partial
    clone answers with a lazy fetch that negotiates nothing, so the remote
    sends the commit's whole history again: for erpnext, 1.5 GB per pin bump;
  - the clone's packs are merged where they are alike in size
    (`tidy_packs`). Every lazy fetch is a pack, and the `git maintenance run
    --auto` a fetch starts does not merge them in a partial clone. Not `git gc
    --auto`, which counts packs, not bytes.

A submodule already checked out shallow (what `shallow = true` and earlier
scaffolders made) is given its paired branch's history in place, once: the
checkout does not move, only history is fetched (`add_history`). A clone an
earlier version made treeless fetches folders from now on (`stop_treeless`).
Every one of these clones is set never to recurse into submodules on fetch
(`no_submodule_fetch`): otherwise git diffs each fetched commit looking for a
moved gitlink, which in a treeless clone is a lazy fetch per commit.

The same is done for a submodule's own submodules that someone initialised
shallow (hrms's frappe-ui), though entry never initialises them itself
(`deepen_nested`). Nix needs it: a flake that includes submodules, evaluated
from a tree with uncommitted changes, fetches each one as a git input of its
own and fails on a shallow one ("so 'revCount' is not available"). From a clean
tree it reads them out of the commit, so the flake works right up to the first
edit and then stops, with a message that names no cause.

With a scope, a directory that is the checkout of a submodule the project has
stopped registering is told apart from a stray repository (`is_retired`): its
`.git` file points into the project's .git/modules, where git put the clone. A
`git pull` of a rename or a removal leaves exactly that, because git drops the
gitlink and the .gitmodules entry but never a directory that holds a
repository. It is not something to vendor or push, as a stray repository is;
it is the old copy of an app. Entry says whether anything in it would be lost
(`unsaved_work`) and, when nothing would, how to clear it away, the clone and
its config section with it: left behind, they make a later submodule at the
same path look as if this clone had set it up before, and so not worth checking
out. Entry deletes nothing itself.

The product supplies (`Config`):

    label           the message prefix ("frappe-nix")
    noun            what the superproject is called in messages ("bench",
                    "project")
    scope           directories whose direct children are the source
                    submodules (("apps",)), or () for every submodule in
                    .gitmodules. With a scope, a git repository in one of them
                    that is not a registered submodule is reported too
    recursive       whether a checkout takes the submodule's own submodules
    treeless        paths cloned commits-only (tree:0)
    update_command  the command that moves submodules to their branches' tips
    remove_command  the command that finishes a half-done removal, with
                    {name} (the .gitmodules name), {path} and {base} (the last
                    path component) filled in
    nested_hint     the lines after "<path> is a git repository but not a
                    registered submodule"
"""

import argparse
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # not Unix: entry runs unguarded, as it always did
    fcntl = None  # type: ignore[assignment]

#: How long a prefetch of a pinned commit may take before it is given up on.
PREFETCH_TIMEOUT = 300

#: How many levels of a submodule's own submodules entry looks through.
MAX_NESTING = 4

BLOBLESS = "blob:none"
TREELESS = "tree:0"


@dataclass(frozen=True)
class Config:
    label: str
    noun: str = "project"
    scope: tuple[str, ...] = ()
    recursive: bool = False
    treeless: tuple[str, ...] = ()
    update_command: str = ""
    remove_command: str = ""
    nested_hint: tuple[str, ...] = (
        "  'nix build' will not see it. Register it (git submodule add) or commit its",
        "  files in place.",
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Config":
        """A Config from JSON-shaped data, failing on an unknown key."""
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown sources setting(s): {', '.join(unknown)}")
        if "label" not in data:
            raise ValueError("sources settings need a label")
        values = dict(data)
        for key in ("scope", "treeless", "nested_hint"):
            if key in values:
                values[key] = tuple(values[key])  # type: ignore[arg-type]
        return cls(**values)  # type: ignore[arg-type]

    def filter_for(self, path: str) -> str:
        return TREELESS if path in self.treeless else BLOBLESS


@dataclass(frozen=True)
class Submodule:
    name: str
    path: str
    branch: str = ""
    url: str = ""


@dataclass
class Report:
    """What `enter` found, for the summary and for tests."""

    first: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    stale: list[Submodule] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    #: cloned, never checked out, and finished by this entry
    resumed: list[str] = field(default_factory=list)
    #: cloned, never checked out, with files in the way: left for a person
    unfinished: list[str] = field(default_factory=list)
    #: the directory of a submodule the project no longer registers
    retired: list[str] = field(default_factory=list)


# ── git ────────────────────────────────────────────────────────────────────


def git(
    *args: str,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    """git, never reading stdin (a prompt would hang shell entry)."""
    full_env = None
    if env:
        full_env = {**os.environ, **env}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=full_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        text=True,
        timeout=timeout,
        check=False,
    )


def ok(*args: str, cwd: str | os.PathLike[str] | None = None) -> bool:
    return git(*args, cwd=cwd).returncode == 0


def out(*args: str, cwd: str | os.PathLike[str] | None = None) -> str:
    """git's stdout, stripped, or "" when it fails."""
    result = git(*args, cwd=cwd)
    return result.stdout.strip() if result.returncode == 0 else ""


def say(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()


def parse_gitmodules(text: str) -> list[Submodule]:
    """Submodules from `git config -f .gitmodules --list -z` output, in file
    order. A section without a path is not a submodule git can use, and is
    left out."""
    entries: dict[str, dict[str, str]] = {}
    for record in text.split("\0"):
        if not record:
            continue
        key, _, value = record.partition("\n")
        if not key.startswith("submodule."):
            continue
        # The name may itself contain dots; the variable is the last part.
        name, _, var = key[len("submodule.") :].rpartition(".")
        if name:
            entries.setdefault(name, {})[var] = value
    subs = []
    for name, values in entries.items():
        if "path" not in values:
            continue
        branch = values.get("branch", "")
        # git's "." is the superproject's branch, which means nothing for a
        # source repository: the remote's default.
        subs.append(
            Submodule(
                name=name,
                path=values["path"],
                branch="" if branch == "." else branch,
                url=values.get("url", ""),
            )
        )
    return subs


def read_gitmodules(root: str = ".") -> list[Submodule]:
    """The submodules `root`'s .gitmodules registers: the project's own by
    default, a checked-out submodule's with its path."""
    gitmodules = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return []
    return parse_gitmodules(out("config", "-f", gitmodules, "--list", "-z"))


def gitlinks() -> dict[str, str]:
    """{path: commit} for every submodule commit the index records."""
    result = git("ls-files", "-s", "-z")
    links = {}
    if result.returncode != 0:
        return links
    for record in result.stdout.split("\0"):
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[0] == "160000":
            links[path] = fields[1]
    return links


def in_scope(path: str, scope: Sequence[str]) -> bool:
    if not scope:
        return True
    parent, _, base = path.rpartition("/")
    return bool(base) and parent in scope


def ever_set_up(name: str) -> bool:
    """Whether this clone has ever set the submodule up: a clone of it under
    the git dir (what `git submodule update --init` makes), or a URL in the
    local config (what `git submodule init` writes, and all that `git submodule
    add` over an existing checkout leaves). A fresh clone has neither. One
    removed by hand keeps both, and a deinitialized one keeps its clone."""
    gitdir = out("rev-parse", "--git-path", f"modules/{name}")
    if gitdir and os.path.exists(gitdir):
        return True
    return ok("config", "--get", f"submodule.{name}.url")


def git_dir_of(path: str) -> str:
    """The git directory behind a checkout: its `.git` when that is a
    directory, else where its `.git` file ("gitdir: <path>") points. "" for a
    directory with neither. Read, not asked of git, because entry asks it of
    every submodule every time."""
    dotgit = os.path.join(path, ".git")
    if os.path.isdir(dotgit):
        return dotgit
    try:
        with open(dotgit) as handle:
            first = handle.readline()
    except OSError:
        return ""
    prefix = "gitdir:"
    if not first.startswith(prefix):
        return ""
    return os.path.normpath(os.path.join(path, first[len(prefix) :].strip()))


def checkout_unfinished(path: str) -> bool:
    """Whether a clone was made and never checked out: it has a HEAD and no
    index. git writes the index after the files, so a checkout cut short (a ^C,
    a killed shell) leaves exactly this, and so does a clone made with
    --no-checkout that nothing has checked out yet. To git it is a repository
    with every file staged for deletion."""
    gitdir = git_dir_of(path)
    return (
        bool(gitdir)
        and os.path.exists(os.path.join(gitdir, "HEAD"))
        and not os.path.exists(os.path.join(gitdir, "index"))
    )


def shallow_on_disk(path: str) -> bool:
    """Whether a clone is shallow, read from its git directory."""
    gitdir = git_dir_of(path)
    return bool(gitdir) and os.path.exists(os.path.join(gitdir, "shallow"))


def nested_checkouts(root: str, depth: int = 0) -> Iterator[str]:
    """The submodules below `root` that are checked out, at any depth up to
    MAX_NESTING, by the path they have from the project's root. They come from
    `root`'s .gitmodules, not from `git submodule status`, which stops at the
    first nested submodule with a broken ref (Frappe apps have those)."""
    if depth >= MAX_NESTING:
        return
    for sub in read_gitmodules(root):
        parts = sub.path.split("/")
        if os.path.isabs(sub.path) or ".." in parts:
            continue
        nested = os.path.join(root, sub.path)
        if os.path.exists(os.path.join(nested, ".git")):
            yield nested
            yield from nested_checkouts(nested, depth + 1)


@contextlib.contextmanager
def entry_lock() -> Iterator[bool]:
    """Holds, for the length of one entry, a lock in the project's git
    directory, and yields whether it got it. Entry runs on every direnv reload
    and every `nix develop`, and two of them overlap (a reload while `devenv
    up` starts). Without the lock the later one finds the earlier one's first
    checkout half done, a `.git` and no index, which is also what a cut-short
    checkout looks like, and would finish it underneath the first. Not
    waiting: entry never fails or hangs on this, and the next one tries again.
    Where it cannot be had at all (no repository, no fcntl, a read-only git
    directory) entry goes ahead as it always did."""
    path = out("rev-parse", "--git-path", "sources.lock") if fcntl else ""
    try:
        fd = (
            os.open(os.path.abspath(path), os.O_RDWR | os.O_CREAT, 0o644)
            if path
            else -1
        )
    except OSError:
        fd = -1
    if fd < 0:
        yield True
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def is_shallow(path: str) -> bool:
    return out("rev-parse", "--is-shallow-repository", cwd=path) == "true"


def is_partial(path: str) -> bool:
    return out("config", "--get", "remote.origin.promisor", cwd=path) == "true"


def no_submodule_fetch(path: str) -> None:
    """The clone's own fetches skip the on-demand submodule check. In the
    clone's config, and only where it says nothing of its own, so a value
    someone set there by hand stands."""
    if not ok("config", "--local", "--get", "fetch.recurseSubmodules", cwd=path):
        git("config", "fetch.recurseSubmodules", "false", cwd=path)


def have_commit(path: str, commit: str) -> bool:
    """Whether a commit is on disk, without the lazy fetch a partial clone
    would make to answer the question, which is the whole reason to ask."""
    return (
        git(
            "cat-file",
            "-e",
            f"{commit}^{{commit}}",
            cwd=path,
            env={"GIT_NO_LAZY_FETCH": "1"},
        ).returncode
        == 0
    )


def tidy_packs(path: str) -> None:
    """Merge a partial clone's packs where they are alike in size. A geometric
    repack keeps each pack at least twice the size of the next smaller, so
    packs of one size (a repeated download) are merged and the objects they
    share stored once; when they already are in that shape it does nothing."""
    pack_dir = out("rev-parse", "--git-path", "objects/pack", cwd=path)
    if not pack_dir:
        return
    pack_dir = os.path.join(path, pack_dir) if not os.path.isabs(pack_dir) else pack_dir
    try:
        packs = [n for n in os.listdir(pack_dir) if n.endswith(".pack")]
    except OSError:
        return
    if len(packs) > 1:
        git("maintenance", "run", "--quiet", "--task=geometric-repack", cwd=path)


def directory_entries(path: str) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def unsaved_work(path: str) -> list[str]:
    """What deleting the checkout at `path` would lose, in words: changes not
    committed, commits no remote branch has (on a branch or on a detached
    HEAD, where a submodule's checkout sits), stashed changes. A question git
    cannot answer is an answer too: it is not safe to call the checkout
    clean."""
    status = git("status", "--porcelain", cwd=path)
    unpushed = git(
        "log", "--oneline", "-1", "--branches", "HEAD", "--not", "--remotes", cwd=path
    )
    stash = git("stash", "list", cwd=path)
    if status.returncode or unpushed.returncode or stash.returncode:
        return ["state git could not read"]
    found = []
    if status.stdout.strip():
        found.append("uncommitted changes")
    if unpushed.stdout.strip():
        found.append("commits no remote branch has")
    if stash.stdout.strip():
        found.append("stashed changes")
    return found


# ── shell entry ────────────────────────────────────────────────────────────


class Sources:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.links: dict[str, str] = {}
        self._modules: str | None = None

    @property
    def label(self) -> str:
        return self.config.label

    def stop_treeless(self, path: str) -> None:
        """A clone an earlier version made treeless fetches folders along with
        commits from now on. Config only: nothing is downloaded here. A
        treeless path stays treeless, and a filter set by hand to anything but
        tree:0 stands."""
        if self.config.filter_for(path) != BLOBLESS:
            return
        if (
            out("config", "--get", "remote.origin.partialclonefilter", cwd=path)
            == TREELESS
        ):
            git("config", "remote.origin.partialclonefilter", BLOBLESS, cwd=path)

    def prefetch_pin(self, path: str) -> None:
        """Fetch the commit the superproject records, the ordinary way, when the
        clone lacks it. Asked on every entry, because a pin moves under a clone
        with a `git pull` of the superproject. A fetch that fails (offline, a
        pin the remote no longer has) is reported and leaves the clone as it
        was."""
        pin = self.links.get(path, "")
        if not pin or have_commit(path, pin):
            return
        try:
            result = git(
                "fetch",
                "-q",
                "--no-recurse-submodules",
                "origin",
                pin,
                cwd=path,
                timeout=PREFETCH_TIMEOUT,
            )
            fetched = result.returncode == 0
        except subprocess.TimeoutExpired:
            fetched = False
        if not fetched:
            say(
                f"{self.label}: could not fetch {path}'s pinned commit {pin[:12]} — "
                "'git submodule update' on it will download its whole history again"
            )

    def clone_paired(self, url: str, path: str, branch: str) -> bool:
        """A partial clone of the paired branch alone, falling back to the
        remote's default branch when the paired one cannot be cloned."""
        filt = self.config.filter_for(path)
        base = ["clone", "-q", "--no-checkout", f"--filter={filt}", "--single-branch"]
        branch_args = ["--branch", branch] if branch else []
        if (
            git(
                *base, *branch_args, "--", url, path, stdout=None, stderr=None
            ).returncode
            == 0
        ):
            return True
        if not branch:
            return False
        say(
            f"{self.label}: could not clone {path} at '{branch}' (.gitmodules' branch) — "
            "trying its remote's default branch"
        )
        return git(*base, "--", url, path, stdout=None, stderr=None).returncode == 0

    def first_checkout(self, sub: Submodule) -> bool:
        """The clone the first entry makes, at the pinned commit. git's own
        submodule clone cannot pick the branch, so this clones by hand and
        hands the result to git: absorbgitdirs moves it under
        .git/modules/<name> as `git submodule update` would have put it, and
        `update --force` checks out the pin (it otherwise skips a submodule
        whose HEAD is already the pinned commit, checked out or not)."""
        if git(
            "submodule", "init", "-q", "--", sub.path, stdout=None, stderr=None
        ).returncode:
            return False
        url = out("config", "--get", f"submodule.{sub.name}.url")
        if not url or not self.clone_paired(url, sub.path, sub.branch):
            return False
        # Its "Migrating git directory" note is noise on every first entry; its
        # error is not.
        absorb = git("submodule", "absorbgitdirs", "--", sub.path)
        if absorb.returncode:
            say(absorb.stderr.rstrip())
            return False
        return self.finish_checkout(sub)

    def finish_checkout(self, sub: Submodule) -> bool:
        """Check the pin out of a clone that has none: the second half of
        first_checkout, and all that is left to do for a clone it did not get
        to finish. Leaves the clone as it is when git fails, so that the next
        entry can try again."""
        # The pin is usually on another branch than the one just cloned, and
        # `submodule update` asks the clone for it before it fetches anything.
        self.prefetch_pin(sub.path)
        # --init with --recursive: the nested submodules are not initialised
        # yet, and --recursive alone updates only initialised ones.
        recursive = ["--init", "--recursive"] if self.config.recursive else []
        update = git(
            "submodule",
            "update",
            "--force",
            *recursive,
            "--",
            sub.path,
            stdout=sys.stderr,
            stderr=None,
        )
        if update.returncode:
            return False
        no_submodule_fetch(sub.path)
        return True

    def undo_first_checkout(self, sub: Submodule, active_before: str) -> None:
        """Back to the state before first_checkout: no clone under the git dir,
        no URL in the config and an empty directory, so the next entry still
        counts the submodule as new and tries again, rather than as one someone
        took out."""
        gitdir = out("rev-parse", "--git-path", f"modules/{sub.name}")
        if gitdir and ".." not in sub.name:
            shutil.rmtree(gitdir, ignore_errors=True)
        if os.path.isdir(sub.path):
            for entry in os.listdir(sub.path):
                target = os.path.join(sub.path, entry)
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    try:
                        os.unlink(target)
                    except OSError:
                        pass
        git("config", "--unset", f"submodule.{sub.name}.url")
        if not active_before:
            git("config", "--unset", f"submodule.{sub.name}.active")

    def add_history(self, sub: Submodule) -> bool:
        """A shallow clone, in place, to first_checkout's shape: the paired
        branch's history, followed from here on alongside whatever branch the
        clone already followed. HEAD, the checkout and any local branch stay
        where they are; this only fetches. A fetch that fails leaves the clone
        shallow, so the next entry tries again."""
        filt = self.config.filter_for(sub.path)
        refspec = (
            f"+refs/heads/{sub.branch}:refs/remotes/origin/{sub.branch}"
            if sub.branch
            else ""
        )
        fetch = git(
            "fetch",
            "-q",
            "--unshallow",
            f"--filter={filt}",
            "--no-recurse-submodules",
            "origin",
            *([refspec] if refspec else []),
            cwd=sub.path,
            stdout=None,
            stderr=None,
        )
        if fetch.returncode:
            return False
        if refspec:
            specs = out(
                "config", "--get-all", "remote.origin.fetch", cwd=sub.path
            ).splitlines()
            if refspec not in specs:
                git("config", "--add", "remote.origin.fetch", refspec, cwd=sub.path)
        git("config", "remote.origin.partialclonefilter", filt, cwd=sub.path)
        no_submodule_fetch(sub.path)
        return True

    def deepen_nested(self, path: str) -> None:
        """Give the shallow clones below a submodule their history, once. They
        are the submodule's own submodules (hrms has frappe-ui), cloned with
        --depth by whoever initialised them, and entry never initialises them
        (a fresh checkout is not recursive: Frappe apps ship nested submodules
        with broken refs). The history matters to Nix: evaluating a flake that
        includes submodules, from a tree with uncommitted changes, fetches each
        one as a git input of its own and fails on a shallow one ("so
        'revCount' is not available"). From a clean tree Nix reads them out of
        the commit and never notices, so the flake works until the first edit."""
        for nested in nested_checkouts(path):
            if not shallow_on_disk(nested):
                continue
            what = (
                "commits only"
                if self.config.filter_for(nested) == TREELESS
                else "commits and folders, no file contents"
            )
            say(
                f"{self.label}: {nested} (a submodule of a submodule) is a shallow clone — fetching its history",
                f"  ({what}), once: Nix cannot fetch a flake's submodules from a shallow one.",
                "  This can take a while.",
            )
            if not self.add_history(Submodule(name=nested, path=nested)):
                say(
                    f"{self.label}: could not fetch {nested}'s history — the next shell entry tries again"
                )

    def modules_dir(self) -> str:
        """The project's .git/modules, as a real path ("" outside a
        repository). Asked of git once, and only when something needs it."""
        if self._modules is None:
            relative = out("rev-parse", "--git-path", "modules")
            self._modules = os.path.realpath(relative) if relative else ""
        return self._modules

    def is_retired(self, path: str) -> bool:
        """Whether a directory is the checkout of a submodule the project has
        since stopped registering: its `.git` file points into the project's
        .git/modules, where git put the clone. A repository that keeps its own
        `.git` directory is one somebody `git add`ed as it was, and is not."""
        modules = self.modules_dir()
        gitdir = git_dir_of(path)
        if not modules or not gitdir or os.path.isdir(os.path.join(path, ".git")):
            return False
        return os.path.realpath(gitdir).startswith(modules + os.sep)

    def explain_retired(self, path: str) -> None:
        """What a `git pull` leaves of a submodule upstream renamed or dropped:
        git removes the gitlink and the .gitmodules entry but not a directory
        that holds a repository, nor the clone under .git/modules. Whether it
        is safe to delete is decided here, since that is the only question
        anyone has about it. A pin on a branch the clone does not follow shows
        as commits no remote branch has: it errs toward asking to look."""
        real = os.path.realpath(git_dir_of(path))
        name = os.path.relpath(real, self.modules_dir())
        clone = os.path.relpath(real, os.path.realpath(os.getcwd()))
        work = unsaved_work(path)
        lines = [
            f"{self.label}: {path} is the old checkout of a submodule this {self.config.noun} no longer registers —"
        ]
        if work:
            lines.append(
                f"  one that was renamed or removed. It holds {', '.join(work)}, so look before you delete it."
            )
        else:
            lines += [
                "  one that was renamed or removed. Nothing in it is uncommitted or unpushed; to clear it away:",
                f"    rm -rf {shlex.quote(path)} {shlex.quote(clone)} && git config --remove-section {shlex.quote('submodule.' + name)}",
            ]
        say(*lines)

    def classify(self) -> tuple[Report, list[Submodule], dict[str, Submodule]]:
        """Sort every in-scope submodule into the report's lists. Returns the
        report, the submodules to check out for the first time, and the
        checked-out ones by path."""
        report = Report()
        subs = sorted(
            (s for s in read_gitmodules() if in_scope(s.path, self.config.scope)),
            key=lambda s: s.path,
        )
        registered = {s.path for s in subs}
        first: list[Submodule] = []
        checked: dict[str, Submodule] = {}
        for sub in subs:
            if os.path.exists(os.path.join(sub.path, ".git")):
                checked[sub.path] = sub
                report.checked.append(sub.path)
            elif sub.path in self.links:
                # Registered with a gitlink and no checkout: never checked
                # out (a fresh clone), or taken out since.
                if ever_set_up(sub.name):
                    report.unchecked.append(sub.path)
                else:
                    first.append(sub)
            else:
                # Only the .gitmodules entry is left: a removal that stopped
                # halfway.
                report.stale.append(sub)
        for directory in self.config.scope:
            for entry in directory_entries(directory):
                path = f"{directory}/{entry}"
                if path in registered or not os.path.isdir(path):
                    continue
                if not os.path.exists(os.path.join(path, ".git")):
                    continue
                if self.is_retired(path):
                    report.retired.append(path)
                else:
                    report.nested.append(path)
        return report, first, checked

    def enter(self, root: str = ".") -> Report:
        """Shell entry in `root`; the working directory is restored after."""
        cwd = os.getcwd()
        os.chdir(root)
        try:
            with entry_lock() as acquired:
                if not acquired:
                    say(
                        f"{self.label}: another shell entry is setting up the sources — leaving them to it"
                    )
                    return Report()
                return self._enter()
        finally:
            os.chdir(cwd)

    def _enter(self) -> Report:
        if out("rev-parse", "--is-inside-work-tree") != "true":
            return Report()
        self.links = gitlinks()
        report, first, checked = self.classify()

        for path in report.nested:
            say(
                f"{self.label}: {path} is a git repository but not a registered submodule —"
            )
            say(*self.config.nested_hint)

        for path in report.retired:
            self.explain_retired(path)

        for sub in first:
            # Git refuses to clone into a directory with anything in it. Say
            # what is in the way rather than set the submodule up for a clone
            # that fails.
            in_the_way = directory_entries(sub.path)
            if in_the_way:
                say(
                    f"{self.label}: {sub.path} is not checked out, but its directory is not empty, so git",
                    "  will not clone into it. Move what is there aside and re-enter the shell:",
                    *(f"    {entry}" for entry in in_the_way),
                )
                continue
            active_before = out("config", "--get", f"submodule.{sub.name}.active")
            say(
                f"{self.label}: checking out {sub.path} (first shell entry in this clone)"
            )
            report.first.append(sub.path)
            if self.first_checkout(sub):
                continue
            report.failed.append(sub.path)
            self.undo_first_checkout(sub, active_before)

        for path, sub in checked.items():
            # A clone with a `.git` and no index is not checked out, whatever
            # `.git` suggests: an earlier entry was cut short. Finish it, but
            # only into an empty directory; files there are somebody's, and
            # `update --force` would overwrite them.
            if checkout_unfinished(path) and path in self.links:
                if directory_entries(path) != [".git"]:
                    report.unfinished.append(path)
                else:
                    say(
                        f"{self.label}: {path} was cloned but its checkout never finished — checking out its pinned commit"
                    )
                    report.resumed.append(path)
                    if not self.finish_checkout(sub):
                        report.failed.append(path)
                        continue
            if is_partial(path):
                no_submodule_fetch(path)
                self.stop_treeless(path)
                self.prefetch_pin(path)
                tidy_packs(path)
            if is_shallow(path):
                what = (
                    "commits only"
                    if self.config.filter_for(path) == TREELESS
                    else "commits and folders, no file contents"
                )
                say(
                    f"{self.label}: {path} is a shallow clone — fetching its branch's history",
                    f"  ({what}), once. This can take a while.",
                )
                if not self.add_history(sub):
                    say(
                        f"{self.label}: could not fetch {path}'s history — the next shell entry tries again"
                    )
            self.deepen_nested(path)

        self.summarise(report)
        return report

    def summarise(self, report: Report) -> None:
        recursive = " --recursive" if self.config.recursive else ""
        if report.failed:
            paths = " ".join(report.failed)
            say(
                f"{self.label}: could not check out: {paths} — see git's error above.",
                f"  The next shell entry tries again, or: git submodule update --init{recursive} -- {paths}",
            )
        for path in report.unfinished:
            say(
                f"{self.label}: {path} was cloned but its checkout never finished, and its directory has files in it,",
                "  so the shell leaves it alone. This checks out what the "
                f"{self.config.noun} records and overwrites what is there:",
                f"    git submodule update --init --force{recursive} -- {path}",
            )
        if report.unchecked:
            paths = " ".join(report.unchecked)
            at_pins = f"  at the commits the {self.config.noun} records:"
            at_tips = "  or at their branches' tips:"
            width = max(len(at_pins), len(at_tips)) + 2
            lines = [
                f"{self.label}: registered but not checked out: {paths}",
                "  (this clone has set them up before, so the shell does not bring them back)",
                f"{at_pins:<{width}}git submodule update --init{recursive} -- {paths}",
            ]
            if self.config.update_command:
                lines.append(f"{at_tips:<{width}}{self.config.update_command}")
            say(*lines)
        for sub in report.stale:
            finish = (
                self.config.remove_command
                or "git config -f .gitmodules --remove-section submodule.{name}"
            ).format(name=sub.name, path=sub.path, base=sub.path.rpartition("/")[2])
            say(
                f"{self.label}: .gitmodules still registers {sub.path}, but the {self.config.noun} records no commit",
                f"  for it — a removal that stopped halfway. Finish it: {finish}",
            )


# ── the first clone ────────────────────────────────────────────────────────


class BranchMissing(Exception):
    pass


def clone(
    url: str,
    path: str,
    *,
    branch: str = "",
    treeless: bool = False,
    require_branch: bool = False,
    label: str = "sources",
) -> str:
    """The scaffolder's first clone of a source repository, in the shape shell
    entry makes: a partial clone of `branch` alone (its commits and folders,
    the files of the checkout; with `treeless`, commits only), set not to
    recurse into submodules on fetch. Returns the branch cloned ("" for the
    remote's default).

    A `branch` the remote does not have is the default branch instead, or, with
    `require_branch`, BranchMissing and no clone. Raises
    subprocess.CalledProcessError when git fails."""
    if branch:
        heads = git("ls-remote", "--heads", "--", url, branch)
        if not (heads.returncode == 0 and heads.stdout.strip()):
            if require_branch:
                raise BranchMissing(f"{url} has no '{branch}' branch")
            say(f"{label}: {url} has no '{branch}' branch — cloning its default branch")
            branch = ""
    args = [
        "clone",
        "-q",
        f"--filter={TREELESS if treeless else BLOBLESS}",
        "--single-branch",
        *(["--branch", branch] if branch else []),
        "--",
        url,
        path,
    ]
    result = git(*args, stdout=None, stderr=None)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args])
    no_submodule_fetch(path)
    return branch


# ── command line ───────────────────────────────────────────────────────────


def main(
    argv: Sequence[str], *, config: Mapping[str, object], prog: str | None = None
) -> int:
    settings = Config.from_mapping(config)
    parser = argparse.ArgumentParser(
        prog=prog, description="Source submodules: the first clone, and shell entry."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    enter_p = sub.add_parser(
        "enter", help="check out a fresh clone's submodules once; report on the rest"
    )
    enter_p.add_argument("root", nargs="?", default=".")
    enter_p.add_argument(
        "--treeless",
        action="append",
        default=[],
        metavar="PATH",
        help="also clone this path commits-only (tree:0); repeatable",
    )
    clone_p = sub.add_parser(
        "clone", help="a partial clone of one branch, as shell entry makes"
    )
    clone_p.add_argument("url")
    clone_p.add_argument("path")
    clone_p.add_argument("--branch", default="")
    clone_p.add_argument(
        "--treeless", action="store_true", help="commits only (tree:0)"
    )
    clone_p.add_argument(
        "--require-branch",
        action="store_true",
        help="fail (exit 3) rather than clone the default branch when --branch is missing",
    )
    args = parser.parse_args(argv)

    if args.command == "enter":
        if args.treeless:
            settings = Config(
                **{
                    **settings.__dict__,
                    "treeless": settings.treeless + tuple(args.treeless),
                }
            )
        if not os.path.isdir(args.root):
            return 0
        Sources(settings).enter(args.root)
        # Shell entry never fails on this: every problem is reported, and the
        # next entry tries again.
        return 0

    try:
        clone(
            args.url,
            args.path,
            branch=args.branch,
            treeless=args.treeless,
            require_branch=args.require_branch,
            label=settings.label,
        )
    except BranchMissing as exc:
        say(f"{settings.label}: {exc} — skipped")
        return 3
    except subprocess.CalledProcessError:
        say(f"{settings.label}: could not clone {args.url} into {args.path}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], config={"label": Path(sys.argv[0]).stem}))
