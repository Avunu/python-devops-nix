#!/usr/bin/env bash
# Installs the product's standards tools (action.yml): from the engine revision
# flake.lock locks, or from OVERRIDE; with HARDENED=true from the engine's uv.lock by
# hash (hardened_install). Writes <P>_EXPECT_REV (and with OVERRIDE <P>_ALLOW_SKEW=1,
# with FETCH_TOKEN <P>_FETCH_TOKEN, with HARDENED UV_NO_CACHE=1 and UV_NO_CONFIG=1) to
# $GITHUB_ENV, and `rev` and `version` to $GITHUB_OUTPUT.
#
# Environment: CLI, ENGINE_INPUT, INSTALL_SPEC, ENV_PREFIX, PYTHON, OVERRIDE,
# FETCH_TOKEN, HARDENED, SOURCE, PROJECT, GIT_SOURCES, BUILD_REQUIREMENTS, with HARDENED
# UV (setup-uv's uv-path), UV_SHA256 (the sha256 of the uv binary itself) and API_TOKEN
# (a read token that is not the App's: the job's github.token), and the runner's
# GITHUB_ENV, GITHUB_OUTPUT, GITHUB_PATH, GITHUB_WORKSPACE and RUNNER_TEMP.
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
  [[ "$PYTHON" =~ ^3\.[0-9]+(\.[0-9]+)?$ ]] || fail "hardened needs python, a CPython version like 3.12, not '$PYTHON'"
  [[ "${UV:-}" == /* && -x "$UV" ]] || fail "hardened needs UV, the absolute path of the pinned uv, not '${UV:-}'"
  [[ "${UV_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || fail "hardened needs UV_SHA256, the sha256 of the uv binary"
  [ -n "${API_TOKEN:-}" ] || fail "hardened needs API_TOKEN, a token that reads the source repositories"
  # setup-uv checks its checksum only when it downloads: with uv already in the runner's
  # tool cache (an earlier step's setup-uv, or anything that wrote there) it checks
  # nothing. So the binary itself is copied out and checked here, and that copy is the
  # uv this step runs and the one later steps find first on PATH.
  uv_dir="$RUNNER_TEMP/standards-uv"
  rm -rf "$uv_dir"
  mkdir -p "$uv_dir"
  /usr/bin/cp -- "$UV" "$uv_dir/uv"
  uv_sum="$(/usr/bin/sha256sum "$uv_dir/uv")"
  [ "${uv_sum%% *}" = "$UV_SHA256" ] ||
    fail "$UV is not the pinned uv: sha256 ${uv_sum%% *}, not $UV_SHA256"
  UV="$uv_dir/uv"
  echo "$uv_dir" >>"$GITHUB_PATH"
  # The runner image's git and gh, which later steps must find on PATH too: a directory
  # an earlier step put before /usr/bin (GITHUB_PATH) could shadow them.
  GIT=/usr/bin/git GH=/usr/bin/gh
  [ -x "$GIT" ] && [ -x "$GH" ] || fail "no /usr/bin/git or /usr/bin/gh on this runner"
  [ "$(command -v git)" = "$GIT" ] || fail "git on PATH is $(command -v git), not $GIT"
  [ "$(command -v gh)" = "$GH" ] || fail "gh on PATH is $(command -v gh), not $GH"
  # uv, git and gh run here under an empty environment (`clean`), but the job's later
  # steps run the installed tools, git, gh and actions in the job's own: refuse what an
  # earlier step could have exported (GITHUB_ENV) to steer them, or to send gh's token
  # to another host. setup-uv exports its own paths; git's identity and gh's quiet
  # switches are harmless.
  for var in $(compgen -e); do
    case "$var" in
      UV_CACHE_DIR | UV_PYTHON_INSTALL_DIR | UV_TOOL_DIR | UV_TOOL_BIN_DIR | UV_NO_CACHE | UV_NO_CONFIG) ;;
      UV_*) fail "hardened refuses $var in the job's environment (setup-uv's paths, UV_NO_CACHE and UV_NO_CONFIG only)" ;;
      GH_TOKEN | GH_PROMPT_DISABLED | GH_NO_UPDATE_NOTIFIER | GH_SPINNER_DISABLED) ;;
      GH_* | GITHUB_ENTERPRISE_TOKEN) fail "hardened refuses $var in the job's environment (gh: GH_TOKEN and its quiet switches only)" ;;
      GIT_AUTHOR_NAME | GIT_AUTHOR_EMAIL | GIT_COMMITTER_NAME | GIT_COMMITTER_EMAIL | GIT_TERMINAL_PROMPT) ;;
      GIT_* | SSH_ASKPASS) fail "hardened refuses $var in the job's environment (git: its identity and GIT_TERMINAL_PROMPT only)" ;;
      PYTHONUNBUFFERED | PYTHONIOENCODING | PYTHONDONTWRITEBYTECODE | PYTHONUTF8) ;;
      PYTHON*) fail "hardened refuses $var in the job's environment" ;;
      NO_PROXY | no_proxy) ;;
      LD_* | BASH_ENV | ENV | *_PROXY | *_proxy | SSL_CERT_FILE | SSL_CERT_DIR | REQUESTS_CA_BUNDLE | \
        CURL_CA_BUNDLE | NODE_EXTRA_CA_CERTS | NODE_TLS_REJECT_UNAUTHORIZED)
        fail "hardened refuses $var in the job's environment" ;;
    esac
  done
  # No uv cache and no uv configuration for any later step in the job either.
  {
    echo "UV_NO_CACHE=1"
    echo "UV_NO_CONFIG=1"
  } >>"$GITHUB_ENV"
fi

if [ -n "$FETCH_TOKEN" ]; then
  echo "::add-mask::$FETCH_TOKEN"
  echo "${ENV_PREFIX}_FETCH_TOKEN=$FETCH_TOKEN" >>"$GITHUB_ENV"
fi

# HARDENED runs uv, git, gh and the venv's python only through `clean`: an empty
# environment but for an explicit PATH, a fresh HOME (no ~/.gitconfig, ~/.netrc,
# ~/.config/uv or gh login) and the variables set here, in an empty directory (no
# .python-version, .venv or uv.toml from the checkout). Nothing the job or an earlier
# step exported (UV_*, PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, GIT_CONFIG_*,
# GIT_CONFIG_PARAMETERS, VIRTUAL_ENV, proxies, an index URL) reaches them, so every path
# passed to them is absolute.
clean() {
  local tools="$RUNNER_TEMP/standards-tools"
  mkdir -p "$tools/home" "$tools/tmp"
  env -i -C "$tools/tmp" \
    PATH="$(dirname "$UV"):$(dirname "$GIT"):/usr/bin:/bin" \
    HOME="$tools/home" TMPDIR="$tools/tmp" LANG=C.UTF-8 \
    UV_NO_CONFIG=1 UV_NO_CACHE=1 UV_NO_PROGRESS=1 \
    UV_PYTHON_INSTALL_DIR="$tools/python" UV_PYTHON_BIN_DIR="$tools/python-bin" \
    GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
    "$@"
}

# HARDENED: fail unless <rev> of https://github.com/<repo> is on its default branch,
# which must be protected: `git fetch <repo> <rev>` alone takes any commit GitHub serves
# for the repository, a pull request's head (refs/pull/*/head) or a fork's included.
# Read with API_TOKEN, never the App's token.
on_default_branch() {
  local repo="$1" rev="$2" branch protected status
  branch="$(clean GH_TOKEN="$API_TOKEN" "$GH" api "repos/$repo" --jq .default_branch)" ||
    fail "could not read $repo's default branch"
  [[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "$repo's default branch is '$branch'"
  protected="$(clean GH_TOKEN="$API_TOKEN" "$GH" api "repos/$repo/branches/$branch" --jq .protected)" ||
    fail "could not read $repo's branch $branch"
  [ "$protected" = true ] || fail "$repo's default branch $branch is not protected: its history proves nothing"
  status="$(clean GH_TOKEN="$API_TOKEN" "$GH" api "repos/$repo/compare/$branch...$rev" --jq .status)" ||
    fail "could not compare $rev with $repo's $branch"
  case "$status" in
    behind | identical) ;;
    *) fail "$rev is not on $repo's $branch ($status): only a commit its protected default branch has is installed" ;;
  esac
}

# A git checkout of https://github.com/<repo> at the full revision <rev>, in <dir>.
fetch_rev() {
  local repo="$1" rev="$2" dir="$3"
  on_default_branch "$repo" "$rev"
  clean "$GIT" init -q "$dir"
  clean "$GIT" -C "$dir" fetch -q --depth 1 --no-tags "https://github.com/${repo}.git" "$rev"
  clean "$GIT" -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(clean "$GIT" -C "$dir" rev-parse HEAD)" = "$rev" ] || fail "fetched $repo at $rev, and HEAD is not $rev"
}

# HARDENED: nothing resolved at run time. Every dependency comes from <project>/uv.lock
# by hash and as a wheel; a git dependency only from a GIT_SOURCES repository at a full
# revision; and only the project and those git dependencies are built, offline and
# without build isolation, against the hashed BUILD_REQUIREMENTS. The interpreter is
# the uv-managed CPython that the pinned uv installs into a fresh directory, checked
# against the hash uv carries for it; nothing finds or downloads another one. The
# console script lands in $RUNNER_TEMP/standards-tools/bin, which goes on PATH.
hardened_install() {
  local project="$1" tools="$RUNNER_TEMP/standards-tools"
  [ -f "$project/uv.lock" ] || fail "no uv.lock in $project: hardened installs only what it locks"
  [ -f "$project/pyproject.toml" ] || fail "no pyproject.toml in $project"
  mkdir -p "$tools/bin" "$tools/git"
  clean "$UV" export -q --frozen --no-dev --no-emit-project --no-header --no-annotate \
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

  rm -rf "$tools/venv" "$tools/python"
  clean "$UV" python install -q --no-bin --install-dir "$tools/python" "$PYTHON"
  local interpreter
  interpreter="$(clean "$UV" python find --managed-python --no-python-downloads "$PYTHON")"
  [[ "$interpreter" == "$tools/python/"* ]] || fail "uv found $interpreter, not the CPython it installed in $tools/python"
  clean "$UV" venv -q --managed-python --no-python-downloads --python "$interpreter" --no-project "$tools/venv"
  clean "$UV" pip install -q --python "$tools/venv/bin/python" --require-hashes --no-build \
    -r "$tools/requirements.txt" -r "$tools/build.txt"
  clean "$UV" pip install -q --python "$tools/venv/bin/python" --offline --no-deps --no-build-isolation \
    "${built[@]}" "$project"
  # Each git dependency records where it came from as a git install would (PEP 610's
  # direct_url.json), so standards_core.revision() and the version-skew row still see it.
  if [ "${#pins[@]}" -gt 0 ]; then
    clean "$tools/venv/bin/python" -I - "${pins[@]}" <<'PY'
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
  version="$(clean "$RUNNER_TEMP/standards-tools/bin/$CLI" --version)"
else
  version="$("$CLI" --version 2>/dev/null || "$(uv tool dir --bin)/$CLI" --version)"
fi
echo "$CLI $version${rev:+ at $rev}"
{
  echo "rev=$rev"
  echo "version=$version"
} >>"$GITHUB_OUTPUT"
