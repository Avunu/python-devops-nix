#!/usr/bin/env bash
# Installs the product's standards tools (action.yml): from the engine revision
# flake.lock locks, or from OVERRIDE. Writes <P>_EXPECT_REV (and with OVERRIDE
# <P>_ALLOW_SKEW=1, with FETCH_TOKEN <P>_FETCH_TOKEN) to $GITHUB_ENV, and `rev` and
# `version` to $GITHUB_OUTPUT.
#
# Environment: CLI, ENGINE_INPUT, INSTALL_SPEC, ENV_PREFIX, PYTHON, OVERRIDE,
# FETCH_TOKEN, and the runner's GITHUB_ENV, GITHUB_OUTPUT and GITHUB_WORKSPACE.
set -euo pipefail

fail() {
  echo "::error title=standards-install::$*"
  exit 1
}

# Each value lands in a variable name or a command line: refuse anything else.
[[ "$ENV_PREFIX" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "env-prefix must be like KIT_NIX, not '$ENV_PREFIX'"
[[ "$CLI" =~ ^[a-z][a-z0-9-]*$ ]] || fail "cli must be a console script name, not '$CLI'"
[[ "$ENGINE_INPUT" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "engine-input must be a flake input name, not '$ENGINE_INPUT'"

if [ -n "$FETCH_TOKEN" ]; then
  echo "::add-mask::$FETCH_TOKEN"
  echo "${ENV_PREFIX}_FETCH_TOKEN=$FETCH_TOKEN" >>"$GITHUB_ENV"
fi

rev=""
if [ -n "$OVERRIDE" ]; then
  case "$OVERRIDE" in
    /*) path="$OVERRIDE" ;;
    *) path="$GITHUB_WORKSPACE/$OVERRIDE" ;;
  esac
  [ -d "$path" ] || fail "override $OVERRIDE is not a directory"
  uv tool install --force --python "$PYTHON" "$path"
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
  [[ "$owner/$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "flake.lock's $ENGINE_INPUT names no owner/repo"
  [[ "$rev" =~ ^[0-9a-f]{40}$ ]] || fail "flake.lock's $ENGINE_INPUT has no 40-hex rev"
  spec="${INSTALL_SPEC//\{owner\}/$owner}"
  spec="${spec//\{repo\}/$repo}"
  spec="${spec//\{rev\}/$rev}"
  echo "installing $spec"
  uv tool install --force --python "$PYTHON" "$spec"
  echo "${ENV_PREFIX}_EXPECT_REV=$rev" >>"$GITHUB_ENV"
fi

# setup-uv puts its tool-bin-dir on PATH for later steps; this one reads it directly.
version="$("$CLI" --version 2>/dev/null || "$(uv tool dir --bin)/$CLI" --version)"
echo "$CLI $version${rev:+ at $rev}"
{
  echo "rev=$rev"
  echo "version=$version"
} >>"$GITHUB_OUTPUT"
