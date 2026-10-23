#!/usr/bin/env bash
# Runs the prek hooks, and commits and pushes what they rewrote (action.yml).
#
# Environment: GH_TOKEN, GH_REPO, EVENT_NAME, BRANCH, HEAD_SHA, HEAD_REPO, PR,
# COMMIT_MESSAGE, FIXERS, DISPATCH, PASSES, RUN_URL; RUNNER_TEMP and GITHUB_OUTPUT from
# the runner.
# Writes `sha` (empty when nothing was pushed) to GITHUB_OUTPUT, and the comment's body
# to $RUNNER_TEMP/pre-commit-autofix.md.
#
# Exits 0 unless the setup is wrong: a hook that still fails is the lint job's to report.
set -euo pipefail

if [ "$EVENT_NAME" != pull_request ] && [ "$EVENT_NAME" != pull_request_target ]; then
  echo "::error title=pre-commit-autofix::runs on a pull request, not on $EVENT_NAME"
  exit 1
fi
if [ "$HEAD_REPO" != "$GH_REPO" ]; then
  echo "::notice title=pre-commit-autofix::$HEAD_REPO is a fork, which this token cannot push to; the lint job's diff shows the fixes"
  exit 0
fi
if [ "$(git rev-parse HEAD)" != "$HEAD_SHA" ]; then
  echo "::error title=pre-commit-autofix::the checkout is $(git rev-parse HEAD), not the pull request's head $HEAD_SHA: check out with ref: \${{ github.event.pull_request.head.sha }}"
  exit 1
fi

# The caller's own fixers first (`nix fmt`), so the hooks see their output. One that
# fails has fixed what it could; the check that runs it reports the rest.
while IFS= read -r fixer; do
  [ -n "${fixer// /}" ] || continue
  echo "::group::$fixer"
  bash -c "$fixer" || echo "::warning title=pre-commit-autofix::$fixer failed"
  echo "::endgroup::"
done <<<"${FIXERS:-}"

log="$RUNNER_TEMP/pre-commit-autofix.log"
status=1
for ((pass = 1; pass <= PASSES; pass++)); do
  before="$(git diff | sha256sum)"
  if prek run --all-files --color=never >"$log" 2>&1; then
    status=0
    break
  fi
  if [ "$(git diff | sha256sum)" = "$before" ]; then
    break
  fi
done
cat "$log"

if git diff --quiet; then
  if [ "$status" -eq 0 ]; then
    echo "The hooks pass on $HEAD_SHA; nothing to fix."
  else
    echo "::notice title=pre-commit-autofix::the hooks that fail on $HEAD_SHA cannot fix what they report; the lint job lists it"
  fi
  exit 0
fi

# Only the files that were rewritten: a stray file a hook leaves is not a fix.
git add --update
mapfile -t files < <(git diff --cached --name-only)
git -c user.name="github-actions[bot]" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit --quiet --no-verify -m "$COMMIT_MESSAGE" \
  -m "The hooks in .pre-commit-config.yaml, and the workflow's fixers, rewrote these files on the pull request's head. Pushed by the pre-commit-autofix action ($RUN_URL)."
sha="$(git rev-parse HEAD)"

# Fully qualified, from a detached head. A push that is not a fast-forward is refused,
# which is right: the branch moved while the hooks ran, and the run for its new head
# fixes that one.
if ! git push --quiet origin "HEAD:refs/heads/$BRANCH"; then
  echo "::notice title=pre-commit-autofix::$BRANCH moved while the hooks ran, or the push was refused (does the checkout keep its credentials, and does the job have contents: write?); nothing was pushed"
  exit 0
fi
echo "sha=$sha" >>"$GITHUB_OUTPUT"
echo "Pushed $sha to $BRANCH."

dispatched=()
failed=()
while IFS= read -r line; do
  [ -n "${line// /}" ] || continue
  read -ra args <<<"$line"
  if gh workflow run "${args[@]}" --repo "$GH_REPO" --ref "$BRANCH"; then
    dispatched+=("${args[0]}")
  else
    echo "::warning title=pre-commit-autofix::could not dispatch ${args[0]} on $BRANCH"
    failed+=("${args[0]}")
  fi
done <<<"$DISPATCH"

short="${sha:0:7}"
{
  echo "### pre-commit fixes pushed"
  echo
  echo "The pre-commit hooks and fixers rewrote ${#files[@]} file(s) on this branch, and $short commits what they changed:"
  echo
  for f in "${files[@]}"; do
    echo "- \`$f\`"
  done
  echo
  echo "**Pull before you push again** (\`git pull --rebase\`), or your next push will be refused."
  echo "To have your own commits run the hooks first, run \`prek install\` once in your clone."
  echo
  if [ "$status" -ne 0 ]; then
    echo "Some hooks still fail after their fixes, and what they report needs a person:"
    echo
    echo "<details><summary>The last hook run</summary>"
    echo
    echo '````text'
    tail -n 80 "$log"
    echo '````'
    echo
    echo "</details>"
    echo
  fi
  if [ "${#dispatched[@]}" -gt 0 ]; then
    list="$(printf '%s, ' "${dispatched[@]}")"
    echo "A push made with \`GITHUB_TOKEN\` starts no workflow, so this run dispatched ${list%, } on $short."
  fi
  if [ "${#failed[@]}" -gt 0 ]; then
    list="$(printf '%s, ' "${failed[@]}")"
    echo "It could not dispatch ${list%, }: run them from the Actions tab on this branch, or push an empty commit."
  fi
  echo
  echo "_From [this run]($RUN_URL)._"
} >"$RUNNER_TEMP/pre-commit-autofix.md"
