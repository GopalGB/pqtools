#!/usr/bin/env bash
# Materialize a target commit into a disposable BASE checkout without moving HEAD.
#
# The worktree HEAD and index are left as:
# - HEAD: still BASE (where the worktree started)
# - index: target
# - worktree: target
#
# This is a helper for review-tree review prep where we need staged diff
# fidelity and no accidental worktree mutations.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: materialize_review_tree.sh <target-commit>" >&2
  exit 2
fi

TARGET="$1"
BASE=$(git rev-parse --verify HEAD)
if [[ -z "$BASE" ]]; then
  echo "base HEAD could not be resolved" >&2
  exit 3
fi

if ! git rev-parse --verify --quiet "${TARGET}^{commit}" >/dev/null; then
  echo "target commit not found: ${TARGET}" >&2
  exit 3
fi

# The `--reset -u` form is the anchor for this helper's contract: it updates
# index + worktree to TARGET while leaving HEAD at BASE.
# shellcheck disable=SC2086
if ! git read-tree --reset -u "$TARGET"; then
  echo "failed to materialize target commit: ${TARGET}" >&2
  exit 3
fi

if [[ "$(git rev-parse --verify HEAD)" != "$BASE" ]]; then
  echo "materialization changed HEAD (expected ${BASE}, got $(git rev-parse --verify HEAD))" >&2
  exit 3
fi

if ! git diff --exit-code >/dev/null; then
  echo "materialized worktree and index diverged after read-tree --reset -u" >&2
  exit 3
fi

if [[ "$(git write-tree)" != "$(git rev-parse --verify "${TARGET}^{tree}")" ]]; then
  echo "materialized index does not match target commit tree: ${TARGET}" >&2
  exit 3
fi

# Verify base index diff is exactly BASE..TARGET.
BASE_DIFF=$(git diff --name-status --no-renames "$BASE" "$TARGET")
WORKTREE_DIFF=$(git diff --cached --name-status --no-renames)
if [[ "$BASE_DIFF" != "$WORKTREE_DIFF" ]]; then
  echo "cached diff does not match BASE..TARGET; aborting review materialization" >&2
  exit 3
fi

echo "materialized ${BASE} -> ${TARGET}"
