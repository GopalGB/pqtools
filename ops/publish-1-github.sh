#!/usr/bin/env bash
# Step D1 of DEPLOYMENT-PLAN.md - create the public GitHub repo and push.
# Run this from an interactive terminal (in Claude Code: prefix the line with `!`),
# because the MAX pre-push security gate reads its y/N confirmation from the TTY.
# It re-runs the pre-flight checks first and refuses to continue if any of them moved.
set -uo pipefail

REPO_DIR="/Users/gopalmacbook/Desktop/Max HQ/pqtools"
OWNER_REPO="GopalGB/mquery-toolkit"
DESC="Unofficial offline tooling for Power Query M source (parse, format, check, rename). Not affiliated with Microsoft."

cd "$REPO_DIR" || exit 1
fail() { printf '\n  STOP: %s\n' "$1" >&2; exit 1; }

printf '== pre-flight ==\n'

gh auth status >/dev/null 2>&1 || fail "gh is not authenticated (run: gh auth login)"
printf '  gh authenticated as %s\n' "$(gh api user --jq .login)"

gh repo view "$OWNER_REPO" >/dev/null 2>&1 && fail "$OWNER_REPO already exists - this script only creates a new repo"
printf '  github %s is free\n' "$OWNER_REPO"

PYPI=$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/mquery-toolkit/json)
[ "$PYPI" = "404" ] || fail "pypi.org/project/mquery-toolkit now returns $PYPI - the name was taken; pick another before publishing"
printf '  pypi name mquery-toolkit is free (404)\n'

[ -z "$(git status --porcelain)" ] || fail "working tree is dirty - commit or stash first"
printf '  worktree clean at %s (%s commits over base)\n' "$(git rev-parse --short HEAD)" "$(git rev-list --count 8326d3f..HEAD)"

[ -x .git/hooks/pre-push ] || fail "the MAX pre-push security gate is missing (run security/tools/install-hooks.sh)"
printf '  pre-push security gate installed\n'

git remote get-url origin >/dev/null 2>&1 && fail "a remote named origin already exists - remove it or push manually"

printf '\n== creating %s and pushing ==\n' "$OWNER_REPO"
printf '   the security gate will list every file and ask: Confirm push? [y/N]\n\n'

gh repo create "$OWNER_REPO" --public --source=. --remote=origin --push --description "$DESC" || {
  printf '\n  push did not complete. Nothing is public unless the repo was created above.\n'
  printf '  If the repo exists but the push was declined, re-run:  git push -u origin master\n' >&2
  exit 1
}

printf '\n== after the push ==\n'
gh repo edit "$OWNER_REPO" \
  --add-topic power-query --add-topic power-bi --add-topic m-language \
  --add-topic linter --add-topic formatter --add-topic python >/dev/null 2>&1 \
  && printf '  topics set\n'

printf '  repo:  https://github.com/%s\n' "$OWNER_REPO"
printf '  CI:    https://github.com/%s/actions  (9 cells: 3.11-3.13 x ubuntu/macos/windows)\n' "$OWNER_REPO"
printf '\n  watch it:  gh run watch\n'
printf '  next:      D3 in DEPLOYMENT-PLAN.md (PyPI trusted publisher, browser, one time)\n'
