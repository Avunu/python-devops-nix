#!/usr/bin/env bash
# Checks for the sources command (lib.sources.command): what the development
# shell does about a project's source submodules on entry, and the first clone
# a scaffolder makes. On a fresh clone, entry checks out every submodule, once.
# After that it touches none: checking out whatever it found without a
# checkout is how a submodule removed by hand came back, re-cloned, on the next
# direnv reload.
#
# Ported from frappe-nix's tests/apps-report.sh and odoo-nix's
# tests/submodules-init.sh, which were one test in two shapes. So is this:
#
#   apps     frappe-nix's: the sources are apps/<x> (scope [ "apps" ]), a
#            stray repository and a half-finished removal are reported, and a
#            checkout does not recurse
#   modules  odoo-nix's: every submodule (scope [ ]), under modules/ and one at
#            core/ cloned commits-only (treeless), and a checkout recurses
#
# The remotes are file:// repositories with uploadpack.allowFilter, as GitHub
# has it: without it a partial clone quietly falls back to a full one and
# nothing below would be tested.
#
# Usage: sources.sh <path-to-the-sources-command> <apps|modules>
set -euo pipefail

TOOL="$1"
SHAPE="$2"
case "$SHAPE" in
  apps)
    P=apps
    NOUN=bench
    RECURSIVE=""
    ;;
  modules)
    P=modules
    NOUN=project
    RECURSIVE=" --recursive"
    ;;
  *)
    echo "unknown shape: $SHAPE" >&2
    exit 2
    ;;
esac

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
# Submodule operations on file:// URLs are refused by default. Every fetch keeps
# its pack (git unpacks one under 100 objects into loose ones, where a repeated
# download would overwrite itself and leave nothing to count), and a gc runs in
# the foreground, where there is something to look at when it is done.
export GIT_CONFIG_COUNT=4
export GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always
export GIT_CONFIG_KEY_1=transfer.unpackLimit GIT_CONFIG_VALUE_1=1
export GIT_CONFIG_KEY_2=gc.autoDetach GIT_CONFIG_VALUE_2=false
export GIT_CONFIG_KEY_3=init.defaultBranch GIT_CONFIG_VALUE_3=main

fails=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  fails=$((fails + 1))
}
check() { # <description> <command...>
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then ok "$desc"; else no "$desc"; fi
}
check_not() { # <description> <command...> — passes when the command fails
  local desc=$1
  shift
  if "$@" > /dev/null 2>&1; then no "$desc"; else ok "$desc"; fi
}
check_eq() { # <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (expected '$2', got '$3')"; fi
}
says() { grep -qF -- "$1" <<< "$OUT"; }
silent_on() { ! grep -qF -- "$1" <<< "$OUT"; }
run() { OUT="$("$TOOL" enter "$PWD" 2>&1)" && RC=0 || RC=$?; }

# Whether an object is on disk, without the lazy fetch a partial clone would
# otherwise make to answer the question.
has_obj() { # <repo> <object>
  GIT_NO_LAZY_FETCH=1 git -C "$1" cat-file -e "$2" 2> /dev/null
}

# The shape the first entry gives a clone: partial, not shallow, the paired
# branch (develop) of origin followed and no other, folders but no file
# contents from here on (core: commits only), and no recursing into nested
# submodules on fetch.
partial_one_branch() { # <path>
  local filter=blob:none
  [ "$1" != core ] || filter=tree:0
  [ "$(git -C "$1" config remote.origin.promisor)" = true ] \
    && [ "$(git -C "$1" rev-parse --is-shallow-repository)" = false ] \
    && [ "$(git -C "$1" config --get-all remote.origin.fetch)" = '+refs/heads/develop:refs/remotes/origin/develop' ] \
    && [ "$(git -C "$1" config remote.origin.partialclonefilter)" = "$filter" ] \
    && [ "$(git -C "$1" config fetch.recurseSubmodules)" = false ] \
    && git -C "$1" rev-parse -q --verify refs/remotes/origin/develop > /dev/null \
    && ! git -C "$1" rev-parse -q --verify refs/remotes/origin/version-x > /dev/null
}

# Everything git knows about the project and its submodules, and the file tree.
snapshot() {
  git status --porcelain --ignore-submodules=none
  git ls-files -s
  cat .gitmodules .git/config
  find .git/modules -maxdepth 3 -name HEAD -exec sh -c 'echo "$1: $(cat "$1")"' _ {} \; 2> /dev/null | sort
  {
    find "$P" -maxdepth 2
    [ ! -e core ] || find core -maxdepth 2
  } | sort
}

# Checked out, at the commit the project records for it.
at_pin() { # <path>
  [ -f "$1/$(basename "$1")/hooks.py" ] \
    && [ "$(git -C "$1" rev-parse HEAD)" = "$(git ls-files -s -- "$1" | awk '{ print $2 }')" ]
}
empty() { [ -z "$(ls -A "$1" 2> /dev/null)" ]; }

NAMES=(present fresh gone taken inplace)

commit_to() { # <name> <branch> <message> — a commit on the seed, pushed
  local seed="$ROOT/seed/$1"
  git -C "$seed" checkout -q "$2"
  mkdir -p "$seed/$1/$2"
  printf '%s\n' "$3" > "$seed/$1/$2/log.txt"
  git -C "$seed" add -A
  git -C "$seed" commit -q -m "$3"
  git -C "$seed" push -q "$ROOT/remotes/$1.git" "$2"
}
# develop, which the project pins, and version-x, which it never checks out.
seed_remote() { # <name>
  local seed="$ROOT/seed/$1"
  mkdir -p "$seed/$1"
  printf 'name = "%s"\n' "$1" > "$seed/$1/hooks.py"
  git -C "$seed" init -q -b develop
  git -C "$seed" add -A
  git -C "$seed" commit -q -m init
  git init -q --bare "$ROOT/remotes/$1.git"
  git -C "$ROOT/remotes/$1.git" symbolic-ref HEAD refs/heads/develop
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowFilter true
  git -C "$ROOT/remotes/$1.git" config uploadpack.allowAnySHA1InWant true
  git -C "$seed" push -q "$ROOT/remotes/$1.git" develop
  git -C "$seed" branch -q version-x
  commit_to "$1" develop "develop 1"
  commit_to "$1" version-x "version-x 1"
}
for a in "${NAMES[@]}" core; do seed_remote "$a"; done

BENCH="$ROOT/bench"
mkdir -p "$BENCH/$P"
cd "$BENCH"
git init -q -b main
for a in present fresh gone taken; do
  git submodule add -q -b develop "file://$ROOT/remotes/$a.git" "$P/$a"
done
if [ "$SHAPE" = modules ]; then
  git submodule add -q -b develop "file://$ROOT/remotes/core.git" core
fi
# inplace: registered over a checkout that was already there (frappe-init
# --migrate, odoo-nix's scaffolder): its .git stays in the working tree, and
# the project's git dir holds no clone of it.
git clone -q -b develop "file://$ROOT/remotes/inplace.git" "$P/inplace"
git submodule add -q -b develop "file://$ROOT/remotes/inplace.git" "$P/inplace" 2> /dev/null
mkdir -p "$P/localapp/localapp"
printf 'name = "localapp"\n' > "$P/localapp/localapp/hooks.py"
# stray: a nested repo `git add`ed as-is, a gitlink with no .gitmodules entry,
# which `git submodule status` dies on.
mkdir -p "$P/stray" && git -C "$P/stray" init -q -b main
printf 'x\n' > "$P/stray/f" && git -C "$P/stray" add -A && git -C "$P/stray" commit -q -m i
git -c advice.addEmbeddedRepo=false add -A 2> /dev/null
git commit -q -m bench
# Upstream moves on past what the project pins, so a checkout at the branch
# tip is not a checkout at the pin.
for a in "${NAMES[@]}" core; do commit_to "$a" develop "develop 2"; done

# Fresh clones of the project as committed, for the sections further down.
git clone -q "$BENCH" "$ROOT/clone"
git clone -q "$BENCH" "$ROOT/clone2"
git clone -q "$BENCH" "$ROOT/clone3"
git clone -q "$BENCH" "$ROOT/clone4"
git clone -q "$BENCH" "$ROOT/clone5"

# fresh: what `git submodule deinit` leaves, its clone kept in the git dir.
git submodule deinit -q -f -- "$P/fresh"
# gone: what frappe's upstream `bench remove-app` leaves: the directory moved
# to archived/, the gitlink dropped, the .gitmodules entry and clone left.
mkdir -p "archived/$P"
mv "$P/gone" "archived/$P/gone-2026-09-25"
git update-index --force-remove -- "$P/gone"
# taken, inplace: removed by hand.
rm -rf "$P/taken" "$P/inplace"

echo "── a $NOUN with one of each ($SHAPE) ──────────────────────────"
before="$(snapshot)"
run
after="$(snapshot)"
check_eq "exits 0" 0 "$RC"
check_eq "changes nothing — no checkout, no clone, no index or config edit" "$before" "$after"
check "…so $P/fresh is still empty" empty "$P/fresh"
check "…$P/gone is still gone" test ! -e "$P/gone"
check "…and so are the ones removed by hand" test ! -e "$P/taken" -a ! -e "$P/inplace"
check "…even the one whose .git lived in the working tree" test ! -e ".git/modules/$P/inplace"
check "says it is checking nothing out" silent_on "checking out"
check "names the submodules taken out since, in path order" \
  says "stub-nix: registered but not checked out: $P/fresh $P/inplace $P/taken"
check "…with the command that checks out their pinned commits" \
  says "git submodule update --init$RECURSIVE -- $P/fresh $P/inplace $P/taken"
check "…naming the $NOUN" says "at the commits the $NOUN records:"
check "names the half-finished removal" says ".gitmodules still registers $P/gone, but the $NOUN records no commit"
if [ "$SHAPE" = apps ]; then
  check "…and how to finish it" says "Finish it: stub remove-app gone"
  check "…and the update command" says "stub update --pull"
  check "names the stray repo" says "stub-nix: $P/stray is a git repository but not a registered submodule"
else
  check "…and how to finish it, by default" says "Finish it: git config -f .gitmodules --remove-section submodule.$P/gone"
  check "…and the update command" says "stub project update"
  check "says nothing of a gitlink with no .gitmodules entry outside a scope" silent_on "$P/stray"
fi
check "says nothing of a checked-out submodule" silent_on "$P/present"
check "…or of a local directory" silent_on "localapp"

echo "── a $NOUN with nothing to say ────────────────────────────────"
git submodule update -q --init -- "$P/fresh" "$P/taken" "$P/inplace"
git config -f .gitmodules --remove-section "submodule.$P/gone"
rm -rf "$P/stray"
git update-index --force-remove -- "$P/stray"
run
check_eq "exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"

echo "── a fresh clone ────────────────────────────────────────────────"
cd "$ROOT/clone"
# What the first entry once left in a submodule it never checked out:
# node_modules from a `yarn install` in the empty directory.
mkdir -p "$P/gone/node_modules"
touch "$P/gone/node_modules/.yarn-integrity"
# inplace: .gitmodules names a branch its remote no longer has.
git config -f .gitmodules "submodule.$P/inplace.branch" retired
run
check_eq "exits 0" 0 "$RC"
for a in present fresh taken inplace; do
  check "checks out $P/$a at its pinned commit" at_pin "$P/$a"
  check "…as a partial clone of its branch alone" partial_one_branch "$P/$a"
done
check "says so" says "stub-nix: checking out $P/present (first shell entry in this clone)"
check "the git dir is under .git/modules, as git's own submodule clone puts it" \
  test "$(cat "$P/present/.git")" = "gitdir: ../../.git/modules/$P/present"
# origin/develop is a commit past the pin, so nothing of it was checked out.
check "the paired branch has its folders past the checkout…" has_obj "$P/present" 'origin/develop^{tree}'
check_not "…but not their file contents" has_obj "$P/present" 'origin/develop:present/develop/log.txt'
if [ "$SHAPE" = modules ]; then
  check "checks out core at its pinned commit" at_pin core
  check "…as a partial clone of its branch, commits only" partial_one_branch core
  check_not "…so without the folders past the checkout" has_obj core 'origin/develop^{tree}'
fi
check_not "another branch is not fetched" has_obj "$P/present" "$(git -C "$ROOT/remotes/present.git" rev-parse version-x)"
check "…until asked for" sh -c "git -C $P/present remote set-branches --add origin version-x && git -C $P/present fetch -q origin"
check "…with its folders…" has_obj "$P/present" 'origin/version-x^{tree}'
check_not "…but not their file contents" has_obj "$P/present" 'origin/version-x:present/version-x/log.txt'
check "and switching to it downloads what it needs" git -C "$P/present" switch -q version-x
check "…files and all" test -f "$P/present/present/version-x/log.txt"
git -C "$P/present" switch -q --detach "$(git ls-files -s -- "$P/present" | awk '{ print $2 }')"
git -C "$P/present" config --replace-all remote.origin.fetch '+refs/heads/develop:refs/remotes/origin/develop'
git -C "$P/present" branch -q -D version-x
git -C "$P/present" update-ref -d refs/remotes/origin/version-x
check "a branch the remote no longer has falls back to its default" \
  says "stub-nix: could not clone $P/inplace at 'retired' (.gitmodules' branch) — trying its remote's default branch"
git checkout -q -- .gitmodules
check "names a submodule whose directory is in the way" says "$P/gone is not checked out, but its directory is not empty"
check "…and what is in it" says "    node_modules"
check "…leaves that directory as it was" test -f "$P/gone/node_modules/.yarn-integrity" -a ! -e "$P/gone/.git"
check_not "…and does not set it up, so the next entry still counts it as new" git config --get "submodule.$P/gone.url"
check "reports nothing as taken out" silent_on "registered but not checked out"

before="$(snapshot)"
run
after="$(snapshot)"
check_eq "a second entry changes nothing" "$before" "$after"
check "…and checks nothing out" silent_on "checking out"

rm -rf "$P/taken"
git submodule deinit -q -f -- "$P/fresh"
run
check "a submodule removed by hand after the first entry stays removed" test ! -e "$P/taken"
check "…as does a deinitialized one" empty "$P/fresh"
check "…and both are reported" says "registered but not checked out: $P/fresh $P/taken"

rm -rf "$P/gone/node_modules"
run
check "once its directory is cleared, the one in the way is checked out" at_pin "$P/gone"

echo "── shallow clones from before ───────────────────────────────────"
cd "$ROOT/clone3"
# What `shallow = true` made of every submodule: the remote's default branch
# at depth 1, and only that branch followed.
SHALLOW=("$P/present" "$P/fresh" "$P/taken" "$P/inplace")
[ "$SHAPE" != modules ] || SHALLOW+=(core)
git submodule update -q --init --depth 1 -- "${SHALLOW[@]}"
printf 'mine\n' > "$P/present/local.txt"
git -C "$P/present" add local.txt
git -C "$P/present" commit -q -m "a local commit"
declare -A head_before
for p in "${SHALLOW[@]}"; do head_before[$p]="$(git -C "$p" rev-parse HEAD)"; done
check "(they start out shallow)" test "$(git -C "$P/fresh" rev-parse --is-shallow-repository)" = true
run
check_eq "exits 0" 0 "$RC"
for p in "${SHALLOW[@]}"; do
  check "$p becomes a partial clone of its branch" partial_one_branch "$p"
  check_eq "…its checkout where it was" "${head_before[$p]}" "$(git -C "$p" rev-parse HEAD)"
done
check_eq "a local commit is kept, and the worktree left clean" "" "$(git -C "$P/present" status --porcelain)"
check "the paired branch gains its folders" has_obj "$P/fresh" 'origin/develop~2^{tree}'
check "says what it is fetching" says "stub-nix: $P/fresh is a shallow clone — fetching its branch's history"
check "…commits and folders" says "(commits and folders, no file contents), once."
if [ "$SHAPE" = modules ]; then
  check "…and for core, commits only" says "(commits only), once."
fi
before="$(snapshot)"
run
after="$(snapshot)"
check_eq "the next entry changes nothing" "$before" "$after"
check_eq "…and says nothing" "" "$OUT"

echo "── a first checkout that fails ──────────────────────────────────"
cd "$ROOT/clone2"
mv "$ROOT/remotes/fresh.git" "$ROOT/remotes/fresh.git.away"
run
check_eq "exits 0" 0 "$RC"
check "names the submodule it could not check out" says "stub-nix: could not check out: $P/fresh — see git's error above."
check "…and how to retry it" says "git submodule update --init$RECURSIVE -- $P/fresh"
check "…still checks out the rest" at_pin "$P/present"
check "…leaves the failed one's directory empty" empty "$P/fresh"
check_not "…with no URL left in the config" git config --get "submodule.$P/fresh.url"
check_not "…nor an activation" git config --get "submodule.$P/fresh.active"
mv "$ROOT/remotes/fresh.git.away" "$ROOT/remotes/fresh.git"
run
check "and the next entry tries again" at_pin "$P/fresh"

echo "── a first checkout that was cut short ──────────────────────────"
# A first checkout is a clone, then the pin's files, and git writes the index
# last. A ^C, or a shell killed, in between leaves a clone in the git dir, a
# `.git` in the directory and no index: to git a repository whose every file is
# staged for deletion, and to the next entry one that has a `.git` and so is
# checked out. It stayed that way until someone noticed the app was empty.
strip_worktree() { find "$1" -mindepth 1 -maxdepth 1 -not -name .git -exec rm -rf {} +; }
cd "$ROOT/clone4"
run
check_eq "(the first entry checks everything out)" 0 "$RC"
# present: killed while the files were being fetched, so the directory is empty.
PGD=".git/modules/$P/present"
touch "$PGD/marker"
rm "$PGD/index"
strip_worktree "$P/present"
# fresh: killed while the files were being written, so some of them are there.
rm ".git/modules/$P/fresh/index"
printf 'edited\n' > "$P/fresh/fresh/hooks.py"
# taken: cut short before git was handed the clone, so its git dir is still in
# the directory.
rm "$P/taken/.git"
mv ".git/modules/$P/taken" "$P/taken/.git"
git config --file "$P/taken/.git/config" --unset core.worktree
rm "$P/taken/.git/index"
strip_worktree "$P/taken"
run
check_eq "exits 0" 0 "$RC"
check "says it is finishing the one with nothing in it" \
  says "stub-nix: $P/present was cloned but its checkout never finished — checking out its pinned commit"
check "…and the one cloned in place" \
  says "stub-nix: $P/taken was cloned but its checkout never finished — checking out its pinned commit"
check "checks the pin out" at_pin "$P/present"
check_eq "…leaving the worktree clean" "" "$(git -C "$P/present" status --porcelain)"
check "…from the clone it had, not a new one" test -e "$PGD/marker"
check "the one cloned in place too" at_pin "$P/taken"
check_eq "…clean" "" "$(git -C "$P/taken" status --porcelain)"
check_not "one with files in its directory is left alone" test -e ".git/modules/$P/fresh/index"
check_eq "…its files as they were" edited "$(cat "$P/fresh/fresh/hooks.py")"
check "…and named, with the command that finishes it" \
  says "git submodule update --init --force$RECURSIVE -- $P/fresh"
check "…which says what that overwrites" says "overwrites what is there"
run
check "a second entry does not mention the finished ones" silent_on "$P/present"
check "…nor the one cloned in place" silent_on "$P/taken"
check "…but still names the one that needs a hand" says "$P/fresh was cloned but its checkout never finished"
# shellcheck disable=SC2086
git submodule update -q --init --force $RECURSIVE -- "$P/fresh"
check "the command it gave finishes it" at_pin "$P/fresh"
run
check_eq "and then there is nothing to say" "" "$OUT"

echo "── a repository with submodules of its own ──────────────────────"
# As hrms has frappe-ui. A fetch's on-demand submodule check diffs every commit
# it brings in for a moved gitlink, and in a treeless clone each of those trees
# is a lazy fetch from the remote: one round trip per commit on another branch.
seed_remote ui
seed_remote nested
git -C "$ROOT/seed/nested" checkout -q develop
git -C "$ROOT/seed/nested" submodule add -q "file://$ROOT/remotes/ui.git" frontend/ui
git -C "$ROOT/seed/nested" commit -q -m "add ui"
git -C "$ROOT/seed/nested" push -q "$ROOT/remotes/nested.git" develop
commit_to nested version-x "version-x 2" 2> /dev/null
mkdir -p "$ROOT/nestbench/$P"
cd "$ROOT/nestbench"
git init -q -b main
git submodule add -q -b develop "file://$ROOT/remotes/nested.git" "$P/nested"
git commit -q -m bench
git clone -q "$ROOT/nestbench" "$ROOT/nestclone"
cd "$ROOT/nestclone"
run
check_eq "exits 0" 0 "$RC"
check "checks it out as a partial clone of its branch" partial_one_branch "$P/nested"
check_eq "…set not to recurse into submodules on fetch" \
  false "$(git -C "$P/nested" config --get fetch.recurseSubmodules)"
if [ "$SHAPE" = modules ]; then
  check "…and, recursing, checks out its own submodule" test -f "$P/nested/frontend/ui/ui/hooks.py"
else
  check "…and does not check out its own submodule" empty "$P/nested/frontend/ui"
fi
# A clone an earlier version made: commits without their folders, which is
# where the on-demand check is a lazy fetch per commit.
git -C "$P/nested" config remote.origin.partialclonefilter tree:0
commit_to nested develop "develop 3"
git -C "$P/nested" fetch -q
check_not "…so a plain git fetch in a clone made treeless does not fetch folders to look for submodule changes" \
  has_obj "$P/nested" 'origin/develop^{tree}'
# A clone converted before the setting existed.
git -C "$P/nested" config --unset fetch.recurseSubmodules || true
run
check_eq "an existing treeless clone gets the setting on the next entry" \
  false "$(git -C "$P/nested" config --get fetch.recurseSubmodules)"
check_eq "…and folders along with the commits it fetches from now on" \
  blob:none "$(git -C "$P/nested" config --get remote.origin.partialclonefilter)"
check_eq "…silently" "" "$OUT"
git -C "$P/nested" config fetch.recurseSubmodules on-demand
run
check_eq "but one set by hand is left as it is" \
  on-demand "$(git -C "$P/nested" config --get fetch.recurseSubmodules)"
if [ "$SHAPE" = modules ]; then
  cd "$ROOT/clone"
  run
  check_eq "core stays commits only" tree:0 "$(git -C core config --get remote.origin.partialclonefilter)"
fi

echo "── a submodule's own submodule that is shallow ──────────────────"
# As hrms's frappe-ui is, when it was cloned with --depth. Nix, evaluating a
# flake that includes submodules from a tree with uncommitted changes, fetches
# each one as a git input of its own and cannot fetch a shallow one ("so
# 'revCount' is not available"); from a clean tree it reads them out of the
# commit, which is why the flake worked until the day the tree was dirty.
cd "$ROOT/nestclone"
UI="$P/nested/frontend/ui"
git -C "$P/nested" submodule update -q --init -- frontend/ui
git -C "$UI" fetch -q --depth 1 origin develop
ui_head="$(git -C "$UI" rev-parse HEAD)"
check "(it starts out shallow)" test "$(git -C "$UI" rev-parse --is-shallow-repository)" = true
run
check_eq "exits 0" 0 "$RC"
check_eq "its history is fetched" false "$(git -C "$UI" rev-parse --is-shallow-repository)"
check_eq "…its checkout where it was" "$ui_head" "$(git -C "$UI" rev-parse HEAD)"
check "…the commits before it are there" git -C "$UI" cat-file -e "$ui_head~1^{commit}"
check "says what it is fetching" \
  says "stub-nix: $UI (a submodule of a submodule) is a shallow clone — fetching its history"
check "…and why" says "Nix cannot fetch a flake's submodules from a shallow one"
check "…and that it is the nested one, not the app" silent_on "stub-nix: $P/nested is a shallow clone"
before="$(snapshot)"
run
after="$(snapshot)"
check_eq "the next entry changes nothing" "$before" "$after"
check_eq "…and says nothing" "" "$OUT"

if [ "$SHAPE" = apps ]; then
  echo "── the old checkout of a submodule that is no longer registered ─"
  # What a `git pull` of a rename (or a removal) leaves behind: the gitlink and
  # the .gitmodules entry go, but git will not delete a directory that holds a
  # repository, and the clone in the git dir stays too. The entry used to call
  # it "a git repository that is not a registered submodule" and advise
  # vendoring it or pushing it somewhere, which is not what it is.
  cd "$ROOT/clone5"
  run
  for a in taken inplace; do
    git update-index --force-remove -- "$P/$a"
    git config -f .gitmodules --remove-section "submodule.$P/$a"
  done
  printf 'mine\n' > "$P/inplace/local.txt"
  git -C "$P/inplace" add local.txt
  git -C "$P/inplace" commit -q -m "a local commit"
  run
  check_eq "exits 0" 0 "$RC"
  check "names it for what it is" \
    says "stub-nix: $P/taken is the old checkout of a submodule this bench no longer registers"
  check "…says nothing in it would be lost" says "Nothing in it is uncommitted or unpushed"
  check "…and how to clear it away, its clone and its config with it" \
    says "rm -rf $P/taken .git/modules/$P/taken && git config --remove-section submodule.$P/taken"
  check "…not as a repository that is not a registered submodule" \
    silent_on "$P/taken is a git repository but not a registered submodule"
  check "one with a commit of its own is named the same way" \
    says "stub-nix: $P/inplace is the old checkout of a submodule this bench no longer registers"
  check "…and says what it holds" says "It holds commits no remote branch has, so look before you delete it"
  check "…and does not offer to delete it" silent_on "rm -rf $P/inplace"
  check "nothing is removed" test -d "$P/taken" -a -d "$P/inplace" -a -d ".git/modules/$P/taken"
  printf 'mine\n' > "$P/taken/untracked.txt"
  run
  check "a file of its own is said too" says "It holds uncommitted changes, so look before you delete it"
  rm "$P/taken/untracked.txt"
  rm -rf "$P/taken" ".git/modules/$P/taken"
  git config --remove-section "submodule.$P/taken"
  run
  check "once it is cleared away it is not mentioned" silent_on "$P/taken"
  check "…and the other still is" says "$P/inplace is the old checkout"
fi

echo "── a pin the clone does not have ────────────────────────────────"
# `git submodule update` to a pin a partial clone lacks first asks the clone for
# it. The answer is a lazy fetch that negotiates nothing, so the remote sends
# the commit's whole history again: a clone's worth of data per pin bump.
# Entry fetches the pin the ordinary way first.
pack_entries() { # <git dir> — object entries across all of its packs
  local n=0 i
  for i in "$1"/objects/pack/*.idx; do n=$((n + $(git show-index < "$i" | wc -l))); done
  echo "$n"
}
bump_pin() { # <name> — point the project at the remote's develop tip
  git update-index --cacheinfo "160000,$(git -C "$ROOT/remotes/$1.git" rev-parse develop),$P/$1"
}
seed_remote pins
for i in $(seq 40); do commit_to pins develop "develop 1$i"; done
mkdir -p "$ROOT/pinbench/$P"
cd "$ROOT/pinbench"
git init -q -b main
git submodule add -q -b develop "file://$ROOT/remotes/pins.git" "$P/pins"
git commit -q -m bench
git clone -q "$ROOT/pinbench" "$ROOT/pinclone"
git clone -q "$ROOT/pinbench" "$ROOT/pincontrol"
for c in pinclone pincontrol; do
  cd "$ROOT/$c"
  run
done
GD=".git/modules/$P/pins"

# The control: what git does by itself, with no entry in between.
cd "$ROOT/pincontrol"
for i in 1 2 3 4 5; do commit_to pins develop "develop 2$i"; done
bump_pin pins
before="$(pack_entries "$GD")"
git submodule update -q -- "$P/pins"
raw=$(($(pack_entries "$GD") - before))
echo "  · (git alone: a submodule update to a new pin adds $raw object entries to a clone of $before)"

cd "$ROOT/pinclone"
bump_pin pins
before="$(pack_entries "$GD")"
run
check_eq "exits 0" 0 "$RC"
check "entry fetches the pin the clone lacked" has_obj "$P/pins" "$(git ls-files -s -- "$P/pins" | awk '{ print $2 }')"
check "…and says nothing about it" silent_on "pinned commit"
git submodule update -q -- "$P/pins"
added=$(($(pack_entries "$GD") - before))
check "a submodule update to it then lands on the pin" at_pin "$P/pins"
# The history is 45 commits and, with their folders, over 180 objects: what a
# lazy fetch of the pin sends. What is new is 5 commits and their folders.
check "…and adds only what is new ($added object entries; git alone added $raw)" \
  test "$added" -lt 60
if [ "$raw" -lt 60 ]; then
  echo "  · note: git alone no longer re-downloads the history for a new pin; prefetch_pin may be unneeded"
fi
# Offline, or a remote that no longer has the pin: said, and the clone left alone.
for i in 1 2 3; do commit_to pins develop "develop 3$i"; done
bump_pin pins
mv "$ROOT/remotes/pins.git" "$ROOT/remotes/pins.git.away"
before="$(pack_entries "$GD")"
run
check_eq "exits 0 when the remote cannot be reached" 0 "$RC"
check "…says the pin could not be fetched" says "stub-nix: could not fetch $P/pins's pinned commit"
check "…and the history it would cost" says "'git submodule update' on it will download its whole history again"
check_eq "…leaving the clone's packs as they were" "$before" "$(pack_entries "$GD")"
mv "$ROOT/remotes/pins.git.away" "$ROOT/remotes/pins.git"
run
check "the next entry fetches it" has_obj "$P/pins" "$(git ls-files -s -- "$P/pins" | awk '{ print $2 }')"

echo "── a clone that took the repeated download ──────────────────────"
# pincontrol ran `git submodule update` on its own, above: the history again,
# as a pack beside the one it was already in.
cd "$ROOT/pincontrol"
unique() { GIT_NO_LAZY_FETCH=1 git -C "$P/pins" cat-file --batch-all-objects --batch-check='%(objectname)' 2> /dev/null | wc -l | tr -d ' '; }
u="$(unique)"
entries="$(pack_entries "$GD")"
check "(it holds more object entries than objects: $entries for $u)" test "$entries" -gt $((u + u / 5))
run
check_eq "exits 0" 0 "$RC"
check "an entry merges the repeat away: $(pack_entries "$GD") entries" test "$(pack_entries "$GD")" -le $((u + u / 10))
check_eq "…keeping every object" "$u" "$(unique)"
check_eq "…silently" "" "$OUT"

echo "── a partial clone with many packs ──────────────────────────────"
# Every lazy fetch is a pack of its own. The `git maintenance run --auto` that
# a fetch starts does not merge them in a partial clone.
cd "$ROOT/pinclone"
for i in 1 2 3 4 5 6 7; do
  commit_to pins develop "develop 4$i"
  git -C "$P/pins" fetch -q
done
packs() { find "$GD/objects/pack" -name '*.pack' | wc -l | tr -d ' '; }
objects() { git -C "$P/pins" rev-list --all --objects --missing=allow-promisor | wc -l | tr -d ' '; }
check "(a pack per fetch piles up)" test "$(packs)" -gt 4
objs_before="$(objects)"
run
check_eq "exits 0" 0 "$RC"
check "an entry consolidates them" test "$(packs)" -le 4
check_eq "…without losing an object" "$objs_before" "$(objects)"
check_eq "…silently" "" "$OUT"
git -C "$P/pins" config remote.origin.partialclonefilter 'blob:limit=1k'
run
check_eq "a filter set by hand is left as it is" 'blob:limit=1k' "$(git -C "$P/pins" config --get remote.origin.partialclonefilter)"

echo "── not a project ────────────────────────────────────────────────"
mkdir -p "$ROOT/empty"
OUT="$("$TOOL" enter "$ROOT/empty" 2>&1)" && RC=0 || RC=$?
check_eq "not a repository: exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"
mkdir -p "$ROOT/plain"
git -C "$ROOT/plain" init -q
OUT="$("$TOOL" enter "$ROOT/plain" 2>&1)" && RC=0 || RC=$?
check_eq "no submodules: exits 0" 0 "$RC"
check_eq "and prints nothing" "" "$OUT"
OUT="$("$TOOL" enter "$ROOT/nowhere" 2>&1)" && RC=0 || RC=$?
check_eq "no such directory: exits 0" 0 "$RC"

echo "── the first clone (a scaffolder's) ─────────────────────────────"
cd "$ROOT"
OUT="$("$TOOL" clone --branch develop "file://$ROOT/remotes/present.git" first/present 2>&1)" && RC=0 || RC=$?
check_eq "exits 0" 0 "$RC"
check_eq "…silently" "" "$OUT"
check "a partial clone of the branch alone, checked out" partial_one_branch first/present
check "…files and all" test -f first/present/present/hooks.py
OUT="$("$TOOL" clone --branch develop --treeless "file://$ROOT/remotes/core.git" first/core 2>&1)" && RC=0 || RC=$?
check_eq "--treeless: commits only" tree:0 "$(git -C first/core config remote.origin.partialclonefilter)"
OUT="$("$TOOL" clone --branch retired "file://$ROOT/remotes/fresh.git" first/fresh 2>&1)" && RC=0 || RC=$?
check_eq "a branch the remote lacks: exits 0" 0 "$RC"
check "…says so" says "stub-nix: file://$ROOT/remotes/fresh.git has no 'retired' branch — cloning its default branch"
check_eq "…and clones the default" develop "$(git -C first/fresh rev-parse --abbrev-ref HEAD)"
OUT="$("$TOOL" clone --require-branch --branch retired "file://$ROOT/remotes/taken.git" first/taken 2>&1)" && RC=0 || RC=$?
check_eq "with --require-branch: exits 3" 3 "$RC"
check "…says it is skipped" says "has no 'retired' branch — skipped"
check "…and clones nothing" test ! -e first/taken
OUT="$("$TOOL" clone "file://$ROOT/remotes/nope.git" first/nope 2>&1)" && RC=0 || RC=$?
check_eq "a remote that is not there: exits 1" 1 "$RC"
check "…naming it" says "stub-nix: could not clone file://$ROOT/remotes/nope.git into first/nope"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
