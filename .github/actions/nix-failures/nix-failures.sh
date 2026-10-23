#!/usr/bin/env bash
# Writes Markdown naming what failed in a nix build log (action.yml).
#
# Environment: LOG (the build's output), TITLE, OUT (the Markdown to write), TAIL (lines
# of each derivation's log), MAX (derivations shown in full).
#
# A failed derivation is one nix reports as the build's own failure, in either of the
# forms nix has printed:
#   error: builder for '/nix/store/<hash>-<name>.drv' failed with exit code 1;   (nix 2.x)
#   error: Cannot build '/nix/store/<hash>-<name>.drv'.                          (newer)
# A dependency that failed is reported by its own line, so "dependencies of derivation
# ... failed to build" is not one. Each one's log comes from `nix log`, or, if the store
# kept none, from the `<name>> ` lines `-L` printed. With no failed derivation, the
# failure is the evaluation's, and the report is the log from its first `error:`.
set -euo pipefail

mkdir -p "$(dirname "$OUT")"
clean="$(mktemp)"
if [ -f "$LOG" ]; then
  # Colours, and the carriage returns of nix's progress bar.
  sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' -e 's/\r$//' "$LOG" >"$clean"
fi

# A fence one backtick longer than the longest run of backticks in $1, so a log that
# prints a fence of its own cannot close this one.
fence() {
  local n
  n="$({ grep -oE '`+' "$1" || true; } | awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }')"
  if [ "$n" -lt 3 ]; then
    n=2
  fi
  printf '%*s' $((n + 1)) '' | tr ' ' '`'
}

# Prints the file $1 fenced.
fenced() {
  local f
  f="$(fence "$1")"
  echo
  echo "${f}text"
  cat "$1"
  echo "$f"
  echo
}

mapfile -t drvs < <(
  { grep -oE "(Cannot build|builder for) '/nix/store/[a-z0-9]{32}-[^']+\.drv'" "$clean" || true; } |
    grep -oE "/nix/store/[^']+\.drv" |
    awk '!seen[$0]++'
)

name_of() {
  local base="${1#/nix/store/}"
  base="${base#*-}"
  echo "${base%.drv}"
}

excerpt="$(mktemp)"
{
  echo "### ❌ $TITLE"
  echo
  if [ "${#drvs[@]}" -eq 0 ]; then
    if [ ! -s "$clean" ]; then
      echo "The step failed before it wrote anything to its log; the run has the rest."
    elif grep -q '^error:' "$clean"; then
      echo "Nix failed before building anything; the error:"
      awk '/^error:/ { p = 1 } p' "$clean" | head -n "$TAIL" >"$excerpt"
    else
      echo "The step failed; the last $TAIL lines of its output:"
      tail -n "$TAIL" "$clean" >"$excerpt"
    fi
    if [ -s "$excerpt" ]; then
      fenced "$excerpt"
    fi
  else
    names=()
    for drv in "${drvs[@]}"; do
      names+=("\`$(name_of "$drv")\`")
    done
    joined="$(printf '%s, ' "${names[@]}")"
    echo "${#drvs[@]} derivation(s) failed: ${joined%, }."
    echo
    shown=0
    for drv in "${drvs[@]}"; do
      if [ "$shown" -ge "$MAX" ]; then
        echo "The other $((${#drvs[@]} - shown)) are in the run's log."
        echo
        break
      fi
      name="$(name_of "$drv")"
      if ! nix log "$drv" 2>/dev/null | tail -n "$TAIL" >"$excerpt" || [ ! -s "$excerpt" ]; then
        awk -v p="$name> " 'index($0, p) == 1 { print substr($0, length(p) + 1) }' "$clean" |
          tail -n "$TAIL" >"$excerpt"
      fi
      echo "<details><summary><code>$name</code>: the last lines of its log</summary>"
      fenced "$excerpt"
      echo "</details>"
      echo
      shown=$((shown + 1))
    done
  fi
} >"$OUT"
rm -f "$clean" "$excerpt"
