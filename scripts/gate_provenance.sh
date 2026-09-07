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
# --verify, because plain `git rev-parse HEAD` on an UNBORN HEAD prints the
# literal string "HEAD" to STDOUT and then fails - so `|| echo unknown`
# appended to it and emitted a two-line, unparseable commit field. --verify
# prints nothing on failure. (Caught by the test for the unborn-HEAD case,
# which is the whole reason that test exists.)
gate_commit=$(git rev-parse --verify HEAD 2>/dev/null) || gate_commit='unknown'
printf '# commit:  %s%s\n' "$gate_commit" "$gate_dirty"

# A DIRTY log's SHA identifies neither the tree that ran nor the tree being
# shipped. That is not hypothetical: the round-16 gate log names 89367cd, an
# autocommit holding two evidence files, while every change it certifies sat
# uncommitted in the worktree. So when dirty, also emit a content identity of
# what ACTUALLY ran, which is checkable after the fact.
if [ -n "$gate_dirty" ]; then
  gate_tree=$(git stash create 2>/dev/null)
  if [ -n "$gate_tree" ]; then
    printf '# content: %s (git stash create - the exact tree that ran)\n' "$gate_tree"
  else
    printf '# content: %s (hash of the uncommitted diff)\n' \
      "$( (git diff HEAD 2>/dev/null; git status --porcelain 2>/dev/null) \
          | git hash-object --stdin 2>/dev/null || echo unknown)"
  fi
fi

# Same trap: --abbrev-ref prints "HEAD" on an unborn head. `git branch
# --show-current` names the branch that WOULD be created, and prints nothing
# in a detached head, so fall back to the abbreviated rev there.
gate_branch=$(git branch --show-current 2>/dev/null)
if [ -z "$gate_branch" ]; then
  gate_branch=$(git rev-parse --abbrev-ref --verify HEAD 2>/dev/null) \
    || gate_branch='unknown'
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
  fi
fi

printf '# date:    %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
