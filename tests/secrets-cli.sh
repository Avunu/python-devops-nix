#!/usr/bin/env bash
# End-to-end test for edit-secret / rekey-secrets against a real ragenix.
#
# This exists because of a bug that shipped: the generated agenix rules were a
# store file with repo-relative keys, and ragenix resolves a rule key relative
# to the *rules file's own directory* — so every key became
# /nix/store/secrets/<name>.age. `agenix -e` reported no rule for the file, and
# `agenix -r` skipped it as "does not exist, ignored" and exited 0. A rekey
# silently did nothing, which is precisely the failure this whole feature was
# built to make impossible.
#
# Ported from frappe-nix's and odoo-nix's copies, which differed only in the
# product's name and directory.
#
# Usage: secrets-cli.sh <edit-secret.sh> <rekey-secrets.sh> <check-secrets.sh> <key-dir>
#
# <key-dir> holds the committed fixture keypairs alice/alice.pub, bob/bob.pub.
set -euo pipefail

EDIT="$1"
REKEY="$2"
CHECK="$3"
KEYS="$4"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL %s\n' "$1"; printf '       %s\n' "${2:-}"; fail=$((fail + 1)); }

export HOME="$WORK/home"
mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$WORK/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

ROOT="$WORK/project"
mkdir -p "$ROOT/secrets"
cd "$ROOT"
git init -q .

# Deliberately different directories. The secret scripts work in the git
# worktree, because that is where the .age files are tracked and where `git add`
# has to run; a product may run its application from a generated directory
# *under* it. Pointing the scripts there instead is not a loud failure: agenix
# reports "no rule for this file" on an edit and skips it as "does not exist,
# ignored" on a rekey, exiting 0 having done nothing. So the two are split here
# on purpose, and the scripts are run from the application directory.
export REPO_ROOT="$ROOT"
APP_ROOT="$ROOT/.stub-nix/app"
mkdir -p "$APP_ROOT"
cd "$APP_ROOT"

install -m 0600 "$KEYS/alice" "$WORK/alice"
install -m 0600 "$KEYS/bob" "$WORK/bob"
cp "$KEYS/alice.pub" "$KEYS/bob.pub" "$WORK/"

# The rules the scripts were rendered against name alice only. A second set,
# naming alice and bob, stands in for "someone edited recipients in flake.nix".
# ragenix writes ASCII-armored age files, so the recipient stanzas sit inside a
# base64 blob rather than readable at the head of the file. De-armor first, the
# same way lib/agecheck.py has to.
tags() {
  python3 "$WORK/tags.py" "$1"
}
tag_of() { python3 -c "
import base64,hashlib,sys
b=base64.b64decode(open(sys.argv[1]).read().split()[1])
print(base64.b64encode(hashlib.sha256(b).digest()[:4]).decode().rstrip('='))
" "$1"; }


cat > "$WORK/tags.py" <<'TAGS_PY'
import base64, re, sys

data = open(sys.argv[1], "rb").read(1 << 16)
if data.startswith(b"-----BEGIN AGE"):
    body = "".join(
        line.strip()
        for line in data.decode("ascii", "replace").splitlines()
        if not line.startswith("-----")
    )
    data = base64.b64decode(body + "=" * (-len(body) % 4))

found = []
for raw in data.split(b"\n"):
    line = raw.decode("ascii", "replace")
    if line.startswith("---"):
        break
    m = re.match(r"^-> ssh-ed25519 (\S+)", line)
    if m:
        found.append(m.group(1))
sys.stdout.write(" ".join(sorted(found)) + (" " if found else ""))
TAGS_PY

A="$(tag_of "$WORK/alice.pub")"
B="$(tag_of "$WORK/bob.pub")"

# ── 1. edit-secret creates the file, where it belongs ────────────────────
# EDITOR is deliberately a real interactive editor, as it is on most machines.
# edit-secret has to override it when stdin is not a terminal: ryantm/agenix
# substitutes `cp -- /dev/stdin` itself, ragenix does not and fails with
# "Editor 'nano' exited with non-zero status code", which would make every
# secret hand-typed only.
export EDITOR="nano"
if printf 'BACKUPS_URL=https://example.invalid\n' | bash "$EDIT" backup-access >"$WORK/edit.log" 2>&1; then
  if [ -f "$ROOT/secrets/backup-access.age" ]; then
    ok "edit-secret writes secrets/backup-access.age in the repository, not the store"
  else
    no "edit-secret writes the file in the repository" "$(find / -name 'backup-access.age' 2>/dev/null | head -2)"
  fi
else
  no "edit-secret succeeds" "$(tail -4 "$WORK/edit.log")"
fi

# ── 2. …encrypted to the declared recipient ──────────────────────────────
if [ -f "$ROOT/secrets/backup-access.age" ] && [ "$(tags "$ROOT/secrets/backup-access.age")" = "$A " ]; then
  ok "the new secret is encrypted to the declared recipient"
else
  no "the new secret is encrypted to the declared recipient" \
    "expected '$A', got '$(tags "$ROOT/secrets/backup-access.age" 2>/dev/null)'"
fi

# ── 3. …and staged, because an untracked .age is invisible to the flake ──
if git -C "$ROOT" ls-files --error-unmatch -- secrets/backup-access.age >/dev/null 2>&1; then
  ok "edit-secret stages the new secret"
else
  no "edit-secret stages the new secret" "still untracked"
fi

# ── 4. the round trip ────────────────────────────────────────────────────
if [ "$(rage -d -i "$WORK/alice" "$ROOT/secrets/backup-access.age" 2>/dev/null)" = "BACKUPS_URL=https://example.invalid" ]; then
  ok "the secret decrypts back to what was written"
else
  no "the secret decrypts back" ""
fi

# ── 5. rekey actually re-encrypts ────────────────────────────────────────
# The regression. With the rules pointing into the store this exited 0, printed
# "does not exist, ignored", and changed nothing.
before="$(tags "$ROOT/secrets/backup-access.age")"
bash "$REKEY" -i "$WORK/alice" >"$WORK/rekey.log" 2>&1 || true
# The rules rekey-secrets generated name both keys and use absolute paths; keep
# a copy so the multi-identity cases below have something to encrypt against.
printf '{ "%s/secrets/backup-access.age".publicKeys = [ %s %s ]; }\n' \
  "$ROOT" "\"$(cat "$KEYS/alice.pub")\"" "\"$(cat "$KEYS/bob.pub")\"" >"$WORK/rules-abs.nix"
after="$(tags "$ROOT/secrets/backup-access.age")"

if grep -qi "does not exist, ignored" "$WORK/rekey.log"; then
  no "rekey-secrets finds the secret" "agenix skipped it: $(grep -i 'ignored' "$WORK/rekey.log" | head -1)"
else
  # tags() sorts, so compare against a sorted expectation rather than the order
  # the recipients were declared in.
  want="$(printf '%s\n%s\n' "$A" "$B" | sort | tr '\n' ' ')"
  if [ "$after" = "$want" ]; then
    ok "rekey-secrets re-encrypts to the new recipient list"
  else
    no "rekey-secrets re-encrypts to the new recipient list" \
      "before='$before' after='$after' expected '$want'"
  fi
fi

# ── 6. and the result is readable by the key that was just added ─────────
if [ "$(rage -d -i "$WORK/bob" "$ROOT/secrets/backup-access.age" 2>/dev/null)" = "BACKUPS_URL=https://example.invalid" ]; then
  ok "the newly added recipient can decrypt"
else
  no "the newly added recipient can decrypt" "this is the whole point of a rekey"
fi

# ── 7. check-secrets agrees ──────────────────────────────────────────────
if bash "$CHECK" >"$WORK/check.log" 2>&1; then
  ok "check-secrets passes after a successful rekey"
else
  no "check-secrets passes after a rekey" "$(tail -4 "$WORK/check.log")"
fi

# ── 8. several identities at once ────────────────────────────────────────
# setup-backup-access offers every readable key in identityPaths, because the
# one that can decrypt is not knowable in advance. The two tools spell that
# differently and the wrong spelling is a hard error, not a fallback:
#
#   ragenix  -i is variadic       (-i a b)      — repeating it is rejected with
#                                                 "cannot be used multiple times"
#   rage     -i takes one value   (-i a -i b)
#
# ryantm/agenix agrees with rage, which is why the repeated form looks correct.
readable=("$WORK/bob" "$WORK/alice")   # bob first, and bob cannot decrypt
identity_args=(-i "${readable[@]}")
rage_args=()
for p in "${readable[@]}"; do rage_args+=(-i "$p"); done

if printf 'BACKUPS_URL=https://multi.invalid\n' |
  EDITOR="cp /dev/stdin" RULES="$WORK/rules-abs.nix" \
    agenix -e "$ROOT/secrets/backup-access.age" "${identity_args[@]}" 2>"$WORK/multi.log"; then
  ok "agenix accepts every readable identity in one go"
else
  no "agenix accepts every readable identity in one go" "$(tail -2 "$WORK/multi.log")"
fi

# And reading one back uses rage, because ragenix implements only
# --edit/--rekey/--schema — there is no `agenix -d` to pre-fill an edit with.
if [ "$(rage --decrypt "${rage_args[@]}" "$ROOT/secrets/backup-access.age" 2>/dev/null)" \
  = "BACKUPS_URL=https://multi.invalid" ]; then
  ok "rage reads it back with the repeated-flag form"
else
  no "rage reads it back with the repeated-flag form" "$(tail -2 "$WORK/multi.log")"
fi

# ── 9. usage, and where the commands refuse to run ──────────────────────
# Help needs no worktree; everything else does, and says so.
if (unset REPO_ROOT; bash "$EDIT" --help) >"$WORK/help.log" 2>&1 &&
  grep -qx 'Usage: edit-secret <name> \[identity-file\]' "$WORK/help.log" &&
  grep -qx '  backup-access' "$WORK/help.log" &&
  grep -qF '`stub-nix.secrets.recipients`' "$WORK/help.log"; then
  ok "edit-secret --help lists the declared secrets, outside the dev shell too"
else
  no "edit-secret --help lists the declared secrets" "$(head -4 "$WORK/help.log")"
fi
if bash "$EDIT" >"$WORK/bare.log" 2>&1 && cmp -s "$WORK/bare.log" "$WORK/help.log"; then
  ok "edit-secret with no name prints the usage and exits 0"
else
  no "edit-secret with no name prints the usage" "$(head -2 "$WORK/bare.log")"
fi
for name in edit-secret rekey-secrets; do
  rc=0
  case "$name" in
    edit-secret) (unset REPO_ROOT; bash "$EDIT" backup-access) ;;
    *) (unset REPO_ROOT; bash "$REKEY") ;;
  esac </dev/null >"$WORK/noroot.log" 2>&1 || rc=$?
  if [ "$rc" -eq 1 ] && grep -qF 'REPO_ROOT: run this from the dev shell' "$WORK/noroot.log"; then
    ok "$name outside the dev shell exits 1 and says why"
  else
    no "$name outside the dev shell exits 1" "rc=$rc $(head -2 "$WORK/noroot.log")"
  fi
done

# ── 10. a failing step's status is the command's ─────────────────────────
# agenix -r with an identity that does not exist: rekey-secrets stops there,
# with agenix's status, before claiming anything was re-encrypted.
rc=0
bash "$REKEY" -i "$WORK/nonexistent" >"$WORK/rekey-fail.log" 2>&1 || rc=$?
if [ "$rc" -ne 0 ] && ! grep -q 'Commit the changed .age files' "$WORK/rekey-fail.log"; then
  ok "rekey-secrets stops with agenix's status when agenix fails"
else
  no "rekey-secrets stops when agenix fails" "rc=$rc $(tail -3 "$WORK/rekey-fail.log")"
fi
if grep -q 'Commit the changed .age files' "$WORK/rekey.log"; then
  ok "a successful rekey-secrets ends by asking for a commit"
else
  no "a successful rekey-secrets ends by asking for a commit" "$(tail -3 "$WORK/rekey.log")"
fi

# ── 11. the writeRules snippet, for a product's own agenix call ─────────
if [ -n "${WRITE_RULES:-}" ]; then
  if bash "$WRITE_RULES" >"$WORK/rules.nix" 2>"$WORK/rules.log" &&
    grep -qF "\"$ROOT/secrets/backup-access.age\".publicKeys = [\"$(cat "$KEYS/alice.pub")\"];" "$WORK/rules.nix" &&
    head -1 "$WORK/rules.nix" | grep -qx '# Generated by stub-nix. Do not edit; do not commit.'; then
    ok "writeRules renders absolute rules for \$REPO_ROOT"
  else
    no "writeRules renders absolute rules" "$(cat "$WORK/rules.nix" "$WORK/rules.log")"
  fi
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
