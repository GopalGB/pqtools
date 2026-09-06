#!/usr/bin/env bash
# Exact claude-opus-5 wrapper gate for pqtools (formerly mquery-toolkit), reproducible.
# Reviews BASE..HEAD with the 2.6 MB vendored bundle and the npm lockfile replaced by
# 1-line stub blobs (so the diff fits the model and the reviewer still sees the files exist).
# Usage: bash run-opus-gate.sh <base-sha> <head-sha> <out-file>
set -uo pipefail
BASE="$1"; HEAD="$2"; OUT="$3"
REPO="/Users/gopalmacbook/Desktop/Max HQ/pqtools"
WT="/tmp/mq-gate-wt-$$"
cd "$REPO" || exit 1
# A relative OUT was resolved inside the temporary worktree below and then
# deleted with it - a 15-minute review with nothing to show for it
# (round 9). Anchor it to the repo before the first cd away.
[[ "$OUT" = /* ]] || OUT="$REPO/$OUT"
git worktree add -q "$WT" "$BASE" || exit 1
cd "$WT" || exit 1
git read-tree "$HEAD"
STUB_BRIDGE=$(printf '// vendored esbuild bundle of @microsoft/powerquery-parser 2.0.0 + powerquery-formatter 1.0.0 (2.6 MB, committed; excluded from review diff, reproducible via `npm run bundle`)\n' | git hash-object -w --stdin)
STUB_LOCK=$(printf '{ "_note": "package-lock.json is committed (npm lockfile v3, pins parser 2.0.0 / formatter 1.0.0 / esbuild 0.28.2); excluded from review diff" }\n' | git hash-object -w --stdin)
# The package was renamed mquery_toolkit -> pqtools in 0.2.0. This line kept the
# old path, so the stub landed on a file that does not exist and the real 2.6 MB
# bundle went into the review diff unstubbed - a silent degradation, not an error.
git ls-files --error-unmatch src/pqtools/_bridge.cjs >/dev/null || {
  echo "BLOCKED: src/pqtools/_bridge.cjs is not tracked; the stub path is stale again." >&2
  exit 2
}
# Stub a file ONLY when it changed in BASE..HEAD. The index holds HEAD and the
# worktree's commit is BASE, so stubbing an unchanged file replaces HEAD's blob
# while BASE keeps the real one - and the diff shows the 2.6 MB bundle being
# DELETED: 46,288 lines, "Prompt is too long", BLOCKED. Fixing the stale path
# above is what exposed this; while the path was stale nothing was stubbed and
# an unchanged bundle produced no diff at all, which was the right result by
# accident.
NOTES=""
stub() {
  if git diff --quiet "$BASE" "$HEAD" -- "$1"; then
    NOTES="$NOTES# $1: unchanged in $BASE..$HEAD, not stubbed, not in the diff\n"
  else
    git update-index --cacheinfo "100644,$2,$1"
    NOTES="$NOTES# $1: changed in $BASE..$HEAD, shown as a stub blob\n"
  fi
}
stub src/pqtools/_bridge.cjs "$STUB_BRIDGE"
stub package-lock.json "$STUB_LOCK"
STAT=$(git diff --cached --stat | tail -1)
if [ -n "${DRY_RUN:-}" ]; then
  printf '%b' "$NOTES"; echo "DRY_RUN staged: $STAT"
  cd "$REPO" && git worktree remove --force "$WT"; exit 0
fi
{
  echo "# Exact claude-opus-5 wrapper review - range $BASE..$HEAD (bundle + lockfile shown as stub blobs)"
  echo "# staged: $STAT"
  printf '%b' "$NOTES"
  echo "# invoked $(date -u +%FT%TZ) via ~/.codex/skills/claude-review/bin/review.sh (ANTHROPIC_API_KEY unset inside the wrapper)"
  echo
  timeout 900 bash "$HOME/.codex/skills/claude-review/bin/review.sh" --staged
  RC=$?
  echo
  echo "# wrapper exit: $RC (0=SHIP, 2=FIX-FIRST, 3=BLOCKED/no standalone verdict line)"
} > "$OUT" 2>&1
cd "$REPO" && git worktree remove --force "$WT"
tail -3 "$OUT"
