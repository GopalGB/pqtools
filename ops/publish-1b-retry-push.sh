#!/usr/bin/env bash
# Retry of step D1 after the pre-push gate was repaired (Max HQ commit 8e500c69d).
# The GitHub repo already exists and is empty, so this only pushes.
# Run from an interactive terminal (in Claude Code: prefix with `!`) - the gate asks for y/N.
set -uo pipefail
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools" || exit 1
fail() { printf '\n  STOP: %s\n' "$1" >&2; exit 1; }

printf '== pre-flight ==\n'
[ -z "$(git status --porcelain)" ] || fail "working tree is dirty"
printf '  worktree clean at %s\n' "$(git rev-parse --short HEAD)"
git remote get-url origin >/dev/null 2>&1 || fail "no origin remote (re-run publish-1-github.sh instead)"
printf '  origin: %s\n' "$(git remote get-url origin)"
grep -q -- '--not --remotes' .git/hooks/pre-push || fail "this repo still has the OLD broken pre-push gate; run security/tools/install-hooks.sh"
printf '  pre-push gate: repaired version installed\n'

printf '\n== pushing ==\n'
printf '   the gate lists 10 commits / 37 files, scans them, then asks: Confirm push? [y/N]\n\n'
git push -u origin master || {
  printf '\n  push did not complete. Nothing was published.\n' >&2
  printf '  If the gate BLOCKED on a secret, read what it named and fix it - do NOT use GIT_PUSH_BYPASS.\n' >&2
  exit 1
}

printf '\n== after the push ==\n'
gh repo edit GopalGB/mquery-toolkit \
  --add-topic power-query --add-topic power-bi --add-topic m-language \
  --add-topic linter --add-topic formatter --add-topic python >/dev/null 2>&1 && printf '  topics set\n'
printf '  repo: https://github.com/GopalGB/mquery-toolkit\n'
printf '  CI:   https://github.com/GopalGB/mquery-toolkit/actions   (9 cells)\n'
printf '\n  watch it:  gh run watch\n'
printf '  next:      D3 in DEPLOYMENT-PLAN.md (PyPI trusted publisher, browser, one time)\n'
