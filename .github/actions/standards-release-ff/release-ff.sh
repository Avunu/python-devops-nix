#!/usr/bin/env bash
# The release step (action.yml): with BUMP, `<cli> ci bump` and then `<cli> ci
# fast-forward` to the commit it leaves; without it, the fast-forward alone. Writes
# `moved`, `bumped` and `sha` to $GITHUB_OUTPUT.
#
# Environment: CLI, RELEASE_BRANCH, SHA, TAG, BUMP, BASE, WAIT_CHECKS, GH_TOKEN,
# GIT_USER, DRY_RUN, and the runner's GITHUB_OUTPUT.
set -euo pipefail

fail() {
  echo "::error title=standards-release-ff::$*"
  exit 1
}

bool() {
  case "$2" in
    true | false) ;;
    *) fail "$1 must be true or false, not '$2'" ;;
  esac
}

[[ "$CLI" =~ ^[a-z][a-z0-9-]*$ ]] || fail "cli must be a console script name, not '$CLI'"
[ -n "$RELEASE_BRANCH" ] || fail "release-branch is empty: this branching has no release branch"
git check-ref-format --branch "$RELEASE_BRANCH" >/dev/null 2>&1 || fail "release-branch '$RELEASE_BRANCH' is not a branch name"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || fail "sha must be a 40-hex commit, not '$SHA'"
[[ "$WAIT_CHECKS" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "wait-checks must be a number of seconds, not '$WAIT_CHECKS'"
bool bump "$BUMP"
bool dry-run "$DRY_RUN"

# `<cli> ci <step>`, with its $GITHUB_OUTPUT keys read back into `out_<key>`; returns
# the step's exit code.
step() {
  local file line code=0
  file="$(mktemp)"
  GITHUB_OUTPUT="$file" "$CLI" ci "$@" || code=$?
  while IFS= read -r line; do
    if [[ "$line" =~ ^([a-z_]+)=(.*)$ ]]; then
      printf -v "out_${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
    fi
  done <"$file"
  rm -f "$file"
  return "$code"
}

sha="$SHA"
bumped=""
if [ "$BUMP" = true ]; then
  # The bump commits and pushes with git, as the token: an extra header for
  # github.com replaces the one actions/checkout may have persisted (an empty value
  # resets the list), for every git command this step runs.
  basic="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
  echo "::add-mask::$basic"
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_0=""
  export GIT_CONFIG_KEY_1="http.https://github.com/.extraheader"
  export GIT_CONFIG_VALUE_1="AUTHORIZATION: basic $basic"

  if [ -n "$GIT_USER" ]; then
    [[ "$GIT_USER" =~ ^(.+)\ \<([^<>]+)\>$ ]] || fail "git-user must be 'Name <email>', not '$GIT_USER'"
    name="${BASH_REMATCH[1]}" email="${BASH_REMATCH[2]}"
  elif who="$(gh api user --jq '"\(.login) \(.id)"' 2>/dev/null)" && [ -n "$who" ]; then
    name="${who% *}" email="${who##* }+${who% *}@users.noreply.github.com"
  else
    name="github-actions[bot]" email="41898282+github-actions[bot]@users.noreply.github.com"
  fi
  export GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email"
  export GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email"

  [ "$(git rev-parse HEAD)" = "$SHA" ] || fail "the checkout is at $(git rev-parse HEAD), not sha $SHA: check out the integration head the bump starts from"
  if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
    git fetch --quiet --unshallow --no-tags origin
  fi
  base="${BASE:-origin/$RELEASE_BRANCH}"
  if [[ "$base" == origin/* ]]; then
    branch="${base#origin/}"
    if git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null; then
      git fetch --quiet --no-tags origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    else
      echo "::notice title=standards-release-ff::$branch does not exist yet: the first release has nothing to bump against, and the branch is created at $SHA"
      base=""
    fi
  fi
  if [ -n "$base" ]; then
    args=(bump --base "$base")
    if [ "$DRY_RUN" = true ]; then args+=(--dry-run); else args+=(--push); fi
    out_bumped="" out_sha=""
    step "${args[@]}"
    bumped="$out_bumped"
    if [ -n "$bumped" ] && [ "$DRY_RUN" = false ]; then
      sha="$out_sha"
    elif [ "$DRY_RUN" = false ] && [ "$(git rev-parse HEAD)" != "$out_sha" ]; then
      # A bump was committed but its push was refused because the integration branch
      # moved: the run for the new head bumps and moves the branch.
      {
        echo "moved=false"
        echo "bumped="
        echo "sha=$SHA"
      } >>"$GITHUB_OUTPUT"
      exit 0
    fi
  fi
fi

args=(fast-forward --release-branch "$RELEASE_BRANCH" --sha "$sha" --wait-checks "$WAIT_CHECKS")
[ -z "$TAG" ] || args+=(--tag "$TAG")
[ "$DRY_RUN" = false ] || args+=(--dry-run)
out_moved="false"
rc=0
step "${args[@]}" || rc=$?
{
  echo "moved=$out_moved"
  echo "bumped=$bumped"
  echo "sha=$sha"
} >>"$GITHUB_OUTPUT"
exit "$rc"
