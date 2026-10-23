#!/usr/bin/env bash
# Writes a workflow's report for its pull request (action.yml).
#
# Environment: NEEDS (`toJSON(needs)`), TITLE, HEAD_SHA, FAILURES (the directory the
# `failures-*` artifacts were merged into), OUT, RUN_URL; GITHUB_OUTPUT from the runner.
# Writes `marker` and `mode` (upsert when a job failed, else update) to GITHUB_OUTPUT.
set -euo pipefail

marker="$(tr -c 'A-Za-z0-9._:-' '-' <<<"$TITLE" | sed 's/-*$//')-report"
mkdir -p "$(dirname "$OUT")"

mapfile -t rows < <(jq -r 'to_entries[] | "\(.key)\t\(.value.result)"' <<<"$NEEDS")
failed=0
for row in "${rows[@]}"; do
  case "${row#*$'\t'}" in
    failure | cancelled) failed=$((failed + 1)) ;;
  esac
done

short="${HEAD_SHA:0:7}"
{
  if [ "$failed" -gt 0 ]; then
    echo "### ❌ $TITLE: $failed job(s) failed on $short"
  else
    echo "### ✅ $TITLE passed on $short"
  fi
  echo
  echo "| Job | Result |"
  echo "|---|---|"
  for row in "${rows[@]}"; do
    job="${row%%$'\t'*}"
    result="${row#*$'\t'}"
    case "$result" in
      success) icon="✅" ;;
      failure) icon="❌" ;;
      cancelled) icon="⛔" ;;
      *) icon="⏭️" ;;
    esac
    echo "| \`$job\` | $icon $result |"
  done
  echo
  if [ "$failed" -gt 0 ]; then
    shopt -s nullglob
    reports=("$FAILURES"/*.md)
    for f in "${reports[@]}"; do
      cat "$f"
      echo
    done
    if [ "${#reports[@]}" -eq 0 ]; then
      echo "No job left details of its failure; they are in the run's log."
      echo
    fi
  fi
  echo "_From [this run]($RUN_URL)._"
} >"$OUT"

{
  echo "marker=$marker"
  echo "mode=$([ "$failed" -gt 0 ] && echo upsert || echo update)"
} >>"$GITHUB_OUTPUT"
