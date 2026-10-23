#!/usr/bin/env bash
# Creates or edits the comment on a pull request whose first line is `<!-- $MARKER -->`
# (action.yml). Other actions in this repository run it too, through
# $GITHUB_ACTION_PATH/../sticky-comment, so the one copy is pinned with whichever action
# the caller pinned.
#
# Environment: GH_TOKEN, GH_REPO, MARKER, BODY_FILE, MODE (upsert or update), and
# optionally PR, HEAD_REPO (the pull request's head repository, to skip a fork's),
# GITHUB_REF_NAME (to find the pull request of a dispatched run) and RUN_URL.
#
# Exits 0 whatever the API says: every refusal is a warning.
set -uo pipefail

warn() {
  echo "::warning title=sticky-comment::$*"
  exit 0
}

[[ "$MARKER" =~ ^[A-Za-z0-9._:-]+$ ]] || {
  echo "::error title=sticky-comment::marker must be letters, digits and . _ : -, not '$MARKER'"
  exit 1
}
case "$MODE" in
  upsert | update) ;;
  *)
    echo "::error title=sticky-comment::mode must be upsert or update, not '$MODE'"
    exit 1
    ;;
esac
[ -f "$BODY_FILE" ] || warn "no body at $BODY_FILE"

# A fork's pull request gets a read-only token: there is nothing to try.
if [ -n "${HEAD_REPO:-}" ] && [ "$HEAD_REPO" != "$GH_REPO" ]; then
  echo "::notice title=sticky-comment::$HEAD_REPO is a fork; its pull request gets no comment"
  exit 0
fi

pr="${PR:-}"
if [ -z "$pr" ] && [ -n "${GITHUB_REF_NAME:-}" ]; then
  pr="$(gh pr list --repo "$GH_REPO" --head "$GITHUB_REF_NAME" --state open \
    --json number --jq '.[0].number // empty')" || warn "could not look up the pull request of $GITHUB_REF_NAME"
fi
if [ -z "$pr" ]; then
  echo "sticky-comment: no open pull request for this run; nothing to comment on"
  exit 0
fi

# GitHub refuses a comment over 65536 characters. The reports bound themselves well
# under it; this cut, at a line, is the backstop.
body="${RUNNER_TEMP:-/tmp}/sticky-comment-$MARKER.md"
limit=60000
{
  echo "<!-- $MARKER -->"
  if [ "$(wc -c <"$BODY_FILE")" -gt "$limit" ]; then
    head -c "$limit" "$BODY_FILE" | sed '$d'
    echo
    echo "_Cut short: the whole report is in [the run](${RUN_URL:-})._"
  else
    cat "$BODY_FILE"
  fi
} >"$body"

existing="$(gh api --paginate "repos/$GH_REPO/issues/$pr/comments" \
  --jq ".[] | select(.user.type == \"Bot\" and (.body | startswith(\"<!-- $MARKER -->\"))) | .id")" ||
  warn "could not list the comments on #$pr"
existing="${existing%%$'\n'*}"

if [ -n "$existing" ]; then
  gh api --method PATCH "repos/$GH_REPO/issues/comments/$existing" --field body=@"$body" >/dev/null ||
    warn "could not edit comment $existing on #$pr"
  echo "sticky-comment: edited $MARKER on #$pr"
elif [ "$MODE" = upsert ]; then
  gh api --method POST "repos/$GH_REPO/issues/$pr/comments" --field body=@"$body" >/dev/null ||
    warn "could not comment on #$pr"
  echo "sticky-comment: commented $MARKER on #$pr"
else
  echo "sticky-comment: #$pr has no $MARKER comment, and mode is update; nothing to do"
fi
