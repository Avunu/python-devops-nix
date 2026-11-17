#!/usr/bin/env bash
# Installs the product's standards tools (action.yml): from the engine revision
# flake.lock locks, or from OVERRIDE; with HARDENED=true from the engine's uv.lock by
# hash (hardened_install). Writes <P>_EXPECT_REV (and with OVERRIDE <P>_ALLOW_SKEW=1,
# with FETCH_TOKEN <P>_FETCH_TOKEN, with HARDENED UV_NO_CACHE=1) to $GITHUB_ENV, and
# `rev` and `version` to $GITHUB_OUTPUT.
#
# Environment: CLI, ENGINE_INPUT, INSTALL_SPEC, ENV_PREFIX, PYTHON, OVERRIDE,
# FETCH_TOKEN, HARDENED, SOURCE, PROJECT, GIT_SOURCES, BUILD_REQUIREMENTS, and the
# runner's GITHUB_ENV, GITHUB_OUTPUT, GITHUB_PATH, GITHUB_WORKSPACE and RUNNER_TEMP.
set -euo pipefail

fail() {
  echo "::error title=standards-install::$*"
  exit 1
}

HARDENED="${HARDENED:-false}"
SOURCE="${SOURCE:-}"
PROJECT="${PROJECT:-}"
GIT_SOURCES="${GIT_SOURCES:-}"
BUILD_REQUIREMENTS="${BUILD_REQUIREMENTS:-}"
REPO_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
# A relative path of plain names: no `..`, `.`, empty or absolute component.
SUBDIR_RE='^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$'

# Each value lands in a variable name or a command line: refuse anything else.
[[ "$ENV_PREFIX" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "env-prefix must be like KIT_NIX, not '$ENV_PREFIX'"
[[ "$CLI" =~ ^[a-z][a-z0-9-]*$ ]] || fail "cli must be a console script name, not '$CLI'"
[[ "$ENGINE_INPUT" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "engine-input must be a flake input name, not '$ENGINE_INPUT'"
case "$HARDENED" in
  true | false) ;;
  *) fail "hardened must be true or false, not '$HARDENED'" ;;
esac
if [ "$HARDENED" = true ]; then
  [[ "$PROJECT" =~ $SUBDIR_RE ]] || fail "hardened needs project, a relative directory, not '$PROJECT'"
  [ -n "$OVERRIDE" ] || [[ "$SOURCE" =~ $REPO_RE ]] || fail "hardened needs source, an owner/repo, not '$SOURCE'"
  for allowed in $GIT_SOURCES; do
    [[ "$allowed" =~ $REPO_RE ]] || fail "git-sources must list owner/repo names, not '$allowed'"
  done
  [ -n "$BUILD_REQUIREMENTS" ] || fail "hardened needs build-requirements"
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    [[ "$line" =~ ^[A-Za-z0-9._-]+==[A-Za-z0-9.+!-]+\ --hash=sha256:[0-9a-f]{64}$ ]] ||
      fail "build-requirements must be 'name==version --hash=sha256:<hex>' lines, not '$line'"
  done <<<"$BUILD_REQUIREMENTS"
  # No uv cache for this step or any later one in the job, and no uv configuration.
  export UV_NO_CACHE=1 UV_NO_CONFIG=1
  echo "UV_NO_CACHE=1" >>"$GITHUB_ENV"
fi

if [ -n "$FETCH_TOKEN" ]; then
  echo "::add-mask::$FETCH_TOKEN"
  echo "${ENV_PREFIX}_FETCH_TOKEN=$FETCH_TOKEN" >>"$GITHUB_ENV"
fi

# A git checkout of https://github.com/<repo> at the full revision <rev>, in <dir>.
fetch_rev() {
  local repo="$1" rev="$2" dir="$3"
  git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 --no-tags "https://github.com/${repo}.git" "$rev"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(git -C "$dir" rev-parse HEAD)" = "$rev" ] || fail "fetched $repo at $rev, and HEAD is not $rev"
}

# HARDENED: nothing resolved at run time. Every dependency comes from <project>/uv.lock
# by hash and as a wheel; a git dependency only from a GIT_SOURCES repository at a full
# revision; and only the project and those git dependencies are built, offline and
# without build isolation, against the hashed BUILD_REQUIREMENTS. The console script
# lands in $RUNNER_TEMP/standards-tools/bin, which goes on PATH.
hardened_install() {
  local project="$1" tools="$RUNNER_TEMP/standards-tools"
  [ -f "$project/uv.lock" ] || fail "no uv.lock in $project: hardened installs only what it locks"
  [ -f "$project/pyproject.toml" ] || fail "no pyproject.toml in $project"
  mkdir -p "$tools/bin" "$tools/git"
  uv export -q --frozen --no-dev --no-emit-project --no-header --no-annotate \
    --project "$project" -o "$tools/export.txt"
  printf '%s\n' "$BUILD_REQUIREMENTS" >"$tools/build.txt"

  # The git dependencies leave the hashed requirements; each is checked and fetched.
  local -a built=() pins=()
  local line name repo rev sub allowed ok n=0
  : >"$tools/requirements.txt"
  while IFS= read -r line; do
    if [[ "$line" == *" @ git+"* ]]; then
      [[ "$line" =~ ^([A-Za-z0-9._-]+)\ @\ git\+https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})(#subdirectory=([^#]*))?$ ]] ||
        fail "uv.lock has a git dependency that is not https://github.com/<owner>/<repo>@<full revision>: $line"
      name="${BASH_REMATCH[1]}" repo="${BASH_REMATCH[2]%.git}" rev="${BASH_REMATCH[3]}" sub="${BASH_REMATCH[5]}"
      ok=""
      for allowed in $GIT_SOURCES; do
        [ "${allowed,,}" = "${repo,,}" ] && ok=1
      done
      [ -n "$ok" ] || fail "uv.lock takes $name from $repo, which git-sources does not list"
      [ -z "$sub" ] || [[ "$sub" =~ $SUBDIR_RE ]] || fail "uv.lock takes $name from $repo's '$sub', not a plain relative directory"
      n=$((n + 1))
      fetch_rev "$repo" "$rev" "$tools/git/$n"
      built+=("$tools/git/$n${sub:+/$sub}")
      pins+=("$name" "$repo" "$rev" "$sub")
      echo "$name from $repo at $rev${sub:+ ($sub)}"
    else
      printf '%s\n' "$line" >>"$tools/requirements.txt"
    fi
  done <"$tools/export.txt"

  rm -rf "$tools/venv"
  uv venv -q --python "$PYTHON" --no-project "$tools/venv"
  uv pip install -q --python "$tools/venv/bin/python" --require-hashes --no-build \
    -r "$tools/requirements.txt" -r "$tools/build.txt"
  uv pip install -q --python "$tools/venv/bin/python" --offline --no-deps --no-build-isolation \
    "${built[@]}" "$project"
  # Each git dependency records where it came from as a git install would (PEP 610's
  # direct_url.json), so standards_core.revision() and the version-skew row still see it.
  if [ "${#pins[@]}" -gt 0 ]; then
    "$tools/venv/bin/python" -I - "${pins[@]}" <<'PY'
import json
import sys
from importlib import metadata
from pathlib import Path

args = sys.argv[1:]
for i in range(0, len(args), 4):
    name, repo, rev, sub = args[i : i + 4]
    url = {"url": f"https://github.com/{repo}.git", "vcs_info": {"vcs": "git", "commit_id": rev}}
    if sub:
        url["subdirectory"] = sub
    dist = metadata.distribution(name)
    (Path(dist._path) / "direct_url.json").write_text(json.dumps(url))
PY
  fi
  [ -x "$tools/venv/bin/$CLI" ] || fail "$project installs no console script $CLI"
  ln -sf "$tools/venv/bin/$CLI" "$tools/bin/$CLI"
  echo "$tools/bin" >>"$GITHUB_PATH"
}

rev=""
if [ -n "$OVERRIDE" ]; then
  case "$OVERRIDE" in
    /*) path="$OVERRIDE" ;;
    *) path="$GITHUB_WORKSPACE/$OVERRIDE" ;;
  esac
  [ -d "$path" ] || fail "override $OVERRIDE is not a directory"
  if [ "$HARDENED" = true ]; then
    rm -rf "$RUNNER_TEMP/standards-tools"
    hardened_install "$path/$PROJECT"
  else
    uv tool install --force --python "$PYTHON" "$path"
  fi
  # A self-test runs tools that are not the locked engine's: no skew rows.
  echo "${ENV_PREFIX}_ALLOW_SKEW=1" >>"$GITHUB_ENV"
  echo "${ENV_PREFIX}_EXPECT_REV=" >>"$GITHUB_ENV"
else
  [ -f flake.lock ] || fail "no flake.lock in $(pwd): the engine revision comes from it"
  node="$(jq -c --arg input "$ENGINE_INPUT" '.nodes[.nodes[.root].inputs[$input] // ""].locked // empty' flake.lock)"
  [ -n "$node" ] || fail "flake.lock has no root input $ENGINE_INPUT"
  [ "$(jq -r .type <<<"$node")" = github ] || fail "flake.lock locks $ENGINE_INPUT from $(jq -r .type <<<"$node"), not github"
  owner="$(jq -r .owner <<<"$node")"
  repo="$(jq -r .repo <<<"$node")"
  rev="$(jq -r .rev <<<"$node")"
  [[ "$owner/$repo" =~ $REPO_RE ]] || fail "flake.lock's $ENGINE_INPUT names no owner/repo"
  [[ "$rev" =~ ^[0-9a-f]{40}$ ]] || fail "flake.lock's $ENGINE_INPUT has no 40-hex rev"
  if [ "$HARDENED" = true ]; then
    # Only `source`, whatever fork the lock names: the URL is built from `source`.
    [ "${owner,,}/${repo,,}" = "${SOURCE,,}" ] ||
      fail "flake.lock locks $ENGINE_INPUT from $owner/$repo; hardened installs only from $SOURCE"
    rm -rf "$RUNNER_TEMP/standards-tools"
    echo "installing $SOURCE at $rev ($PROJECT), hash-locked"
    fetch_rev "$SOURCE" "$rev" "$RUNNER_TEMP/standards-tools/src"
    hardened_install "$RUNNER_TEMP/standards-tools/src/$PROJECT"
  else
    spec="${INSTALL_SPEC//\{owner\}/$owner}"
    spec="${spec//\{repo\}/$repo}"
    spec="${spec//\{rev\}/$rev}"
    echo "installing $spec"
    uv tool install --force --python "$PYTHON" "$spec"
  fi
  echo "${ENV_PREFIX}_EXPECT_REV=$rev" >>"$GITHUB_ENV"
fi

# setup-uv puts its tool-bin-dir on PATH for later steps, and hardened_install its own
# bin dir; this step reads them directly.
if [ "$HARDENED" = true ]; then
  version="$("$RUNNER_TEMP/standards-tools/bin/$CLI" --version)"
else
  version="$("$CLI" --version 2>/dev/null || "$(uv tool dir --bin)/$CLI" --version)"
fi
echo "$CLI $version${rev:+ at $rev}"
{
  echo "rev=$rev"
  echo "version=$version"
} >>"$GITHUB_OUTPUT"
