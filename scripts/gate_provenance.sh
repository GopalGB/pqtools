#!/usr/bin/env bash
# Print the provenance header for a release-gate log.
#
# Extracted from release_gate.sh so it can be EXECUTED by a test. The previous
# version lived inline, and the test written for it grepped the script's text
# for three substrings - which meant inverting the clean/dirty branches left
# the test green while the header reported every clean tree as DIRTY and every
# dirty tree as clean. A control that cannot see behaviour is not a control.
#
# Usage: bash scripts/gate_provenance.sh [pytest-python]
# Runs against the CURRENT directory, so a test can drive it in a temp repo.

set -uo pipefail
PY="${1:-}"

# The header is printed in full even when a field cannot be determined - a
# partial header is still evidence - but the script then EXITS NON-ZERO so the
# caller can refuse. Round 18 added a guard in release_gate.sh whose message
# said "refusing to gate an unidentifiable tree"; this script had no `exit` in
# it and its last command was a printf, so it always returned 0 and the
# unidentifiable-tree case was the one case the guard could NOT see. The guard
# fired only when bash could not execute the file at all.
gate_status=0

printf '# release gate\n'

# `git status --porcelain` alone: it already reports staged, unstaged AND
# untracked, so the `git diff --quiet HEAD` that used to accompany it added
# nothing - and errored (exit 128, hence "always dirty") in a repo with no
# commits yet.
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
  gate_dirty=''
else
  gate_dirty=' (working tree DIRTY)'
fi
# On an UNBORN HEAD `git rev-parse HEAD` prints the literal string "HEAD" to
# STDOUT and then fails. The pre-round-17 form was
#   "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
# with the `||` INSIDE the substitution, so "unknown" was APPENDED to "HEAD"
# and the commit field came out two lines long and unparseable.
#
# The load-bearing half of the fix is the `||` being OUTSIDE: it REPLACES the
# value instead of appending to it. Round 17 changed both halves at once and
# its commit message credited `--verify`; the round-18 control proved that
# wrong - dropping `--verify` alone leaves this correct and the test green,
# because the outside-`||` reassigns. `--verify` is kept for the narrower
# property that it prints nothing on failure, but it is not what fixed this.
gate_commit=$(git rev-parse --verify HEAD 2>/dev/null) || gate_commit='unknown'
printf '# commit:  %s%s\n' "$gate_commit" "$gate_dirty"

# A DIRTY log's SHA identifies neither the tree that ran nor the tree being
# shipped. That is not hypothetical: the round-16 gate log names 89367cd, an
# autocommit holding two evidence files, while every change it certifies sat
# uncommitted in the worktree. So when dirty, also emit a content identity of
# what ACTUALLY ran, which is checkable after the fact.
if [ -n "$gate_dirty" ]; then
  # `git stash create` was the obvious tool and it is the WRONG one: it stashes
  # TRACKED changes only, so it silently omits untracked files - the exact class
  # `git status --porcelain` counted as dirty eighteen lines up. This is not
  # hypothetical either. The round-17 gate log names 295b580c as "the exact tree
  # that ran", and `git ls-tree -r 295b580c` does not contain
  # scripts/gate_provenance.sh - the script that PRINTED that line - because the
  # file was still untracked when it ran. The fix for round 16 reproduced round
  # 16's defect. (With ONLY untracked changes, `git stash create` prints nothing
  # at all, so the old fallback fired - and it hashed `git status --porcelain`,
  # which lists untracked NAMES, not contents. Blind the same way.)
  #
  # Build the identity in a scratch index instead: read HEAD, then `add -A` over
  # the worktree. It covers tracked AND untracked; ignored files stay out,
  # which is what --porcelain counted, so the two agree.
  #
  # NOT "the same side effect as stash create", which is what this comment used
  # to claim: stash create never wrote untracked CONTENT to the object store,
  # and `add -A` does. Every dirty gate run therefore stores a blob of every
  # untracked non-ignored file. They are unreachable and gc-prunable and are
  # never pushed - but they are on disk, and the pre-push secret scan looks at
  # commits, so it would not see them. Worth naming given this repo's
  # no-secret-value invariant. `.samples/` and everything else gitignored stays
  # out, which is where such material is meant to live.
  gate_tmp=$(mktemp -d 2>/dev/null) || gate_tmp=''
  gate_tree=''
  gate_why=''
  if [ -n "$gate_tmp" ]; then
    GIT_INDEX_FILE="$gate_tmp/index" git read-tree HEAD 2>/dev/null
    # `git add -A` DROPS PATHS WHILE EXITING 0. An unreadable directory makes it
    # print "warning: could not open directory" to stderr and return success,
    # and the resulting tree is byte-identical to one where the directory was
    # never there (reproduced). An exit-status check does not catch that, so
    # capture stderr and treat any of it as fatal to the identity.
    gate_add_err=$(GIT_INDEX_FILE="$gate_tmp/index" git add -A 2>&1 >/dev/null)
    if [ -n "$gate_add_err" ]; then
      # Quote what git actually said rather than guessing which case it was -
      # an unreadable directory and an embedded repository both land here.
      gate_why="git add -A: $(printf '%s' "$gate_add_err" | head -1)"
    else
      gate_tree=$(GIT_INDEX_FILE="$gate_tmp/index" git write-tree 2>/dev/null) \
        || { gate_tree=''; gate_why='git write-tree failed'; }
    fi
    # A nested repository (a stray clone, or a `git worktree` - and gate runs
    # DO happen in mq-gate-wt-*) is recorded as a `160000 commit` gitlink whose
    # object lives in the OTHER repo's store, so it cannot be read back here
    # while `git status --porcelain` counted it dirty. That is the marker and
    # the identity disagreeing, which is the thing this line exists to prevent.
    if [ -n "$gate_tree" ] \
       && git ls-tree -r "$gate_tree" 2>/dev/null | grep -q '^160000'; then
      gate_tree=''
      gate_why='the worktree contains a nested repository (unreadable gitlink)'
    fi
    rm -rf "$gate_tmp"
  else
    gate_why='could not create a scratch index'
  fi
  if [ -n "$gate_tree" ]; then
    printf '# content: %s (tracked + untracked; read it with git ls-tree -r)\n' \
      "$gate_tree"
  else
    # An identity that omits part of what ran is worse than none: it reads as
    # checkable and is not. Refuse instead of approximating, and make the
    # refusal reach the caller as a non-zero status.
    printf '# content: unknown - COULD NOT IDENTIFY THE TREE THAT RAN (%s)\n' \
      "$gate_why"
    gate_status=3
  fi
fi

# `git branch --show-current` names the branch that WOULD be created on an
# unborn head, and prints nothing when detached. The detached fallback used to
# be `rev-parse --abbrev-ref`, which prints the literal string "HEAD" there -
# uninformative in precisely the case the fallback exists for, and gate runs
# DO happen in detached worktrees (mq-gate-wt-*). Use the short rev.
gate_branch=$(git branch --show-current 2>/dev/null)
if [ -z "$gate_branch" ]; then
  # Empty does not imply detached: on git < 2.22 `--show-current` does not
  # exist and errors to the suppressed stderr, which would have labelled every
  # ordinary branch "detached at <sha>". Ask git directly instead.
  if git symbolic-ref -q HEAD >/dev/null 2>&1; then
    gate_branch=$(git symbolic-ref --short -q HEAD 2>/dev/null) \
      || gate_branch='unknown'
  elif gate_branch=$(git rev-parse --short --verify HEAD 2>/dev/null); then
    gate_branch="detached at $gate_branch"
  else
    gate_branch='unknown'
  fi
fi
printf '# branch:  %s\n' "$gate_branch"

# Status-checked: a collection error otherwise printed an empty count and the
# run could still end GATE PASSED, blanking the line in the case it matters.
if [ -n "$PY" ]; then
  if gate_collect=$("$PY" -m pytest --collect-only -q 2>/dev/null | tail -1) \
     && [ -n "$gate_collect" ]; then
    printf '# collect: %s\n' "$gate_collect"
  else
    printf '# collect: unknown - COLLECTION FAILED\n'
    gate_status=3
  fi
fi

printf '# date:    %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

exit "$gate_status"
