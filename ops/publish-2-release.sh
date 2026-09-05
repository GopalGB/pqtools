#!/usr/bin/env bash
# Step D5 of DEPLOYMENT-PLAN.md - tag v0.1.0 and let the release workflow publish to PyPI.
#
# THIS IS THE IRREVERSIBLE STEP. PyPI never permits reusing a version number: if 0.1.0
# publishes with a defect, the only remedies are yanking it and shipping 0.1.1.
# Run from an interactive terminal (in Claude Code: prefix with `!`) - the push gate asks for y/N.
set -uo pipefail
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools" || exit 1
fail() { printf '\n  STOP: %s\n' "$1" >&2; exit 1; }

VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null) \
  || fail "could not read the version from pyproject.toml"
TAG="v${VERSION}"

printf '== pre-flight ==\n'
printf '  version in pyproject.toml : %s  -> tag %s\n' "$VERSION" "$TAG"

[ -z "$(git status --porcelain)" ] || fail "working tree is dirty - commit or stash first"
printf '  worktree clean at %s\n' "$(git rev-parse --short HEAD)"

git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null && fail "tag ${TAG} already exists locally"
git ls-remote --exit-code --tags origin "${TAG}" >/dev/null 2>&1 && fail "tag ${TAG} already exists on the remote"
printf '  tag %s is unused locally and on origin\n' "$TAG"

[ "$(git rev-parse HEAD)" = "$(git rev-parse @{u} 2>/dev/null)" ] \
  || fail "HEAD differs from origin/master - push your commits first, so the tag matches what CI tested"
printf '  HEAD matches origin/master\n'

CONC=$(gh run list --workflow=CI --branch master --limit 1 --json conclusion,headSha --jq '.[0] | "\(.conclusion) \(.headSha)"' 2>/dev/null)
CI_RESULT=${CONC%% *}; CI_SHA=${CONC##* }
[ "$CI_RESULT" = "success" ] || fail "the latest CI run on master is '${CI_RESULT}', not success"
[ "$CI_SHA" = "$(git rev-parse HEAD)" ] || fail "the green CI run was for ${CI_SHA:0:7}, not this HEAD - push and let CI run again"
printf '  CI green on this exact commit\n'

curl -s -o /dev/null -w '' https://pypi.org/pypi/mquery-toolkit/json
PYPI=$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/mquery-toolkit/json)
[ "$PYPI" = "404" ] || fail "pypi.org/project/mquery-toolkit returns ${PYPI} - the name is taken, or this version already shipped"
printf '  pypi name still free (404)\n'

printf '\n== tagging and pushing %s ==\n' "$TAG"
printf '   this triggers release.yml: it re-runs the full check suite, builds, then publishes to PyPI.\n'
printf '   the push gate will ask: Confirm push? [y/N]\n\n'

git tag -a "$TAG" -m "mquery-toolkit ${VERSION}" || fail "could not create the tag"
git push origin "$TAG" || {
  git tag -d "$TAG" >/dev/null 2>&1
  fail "push declined or failed - local tag removed so you can retry cleanly"
}

printf '\n== published (pending the workflow) ==\n'
printf '  watch:   gh run watch\n'
printf '  actions: https://github.com/GopalGB/mquery-toolkit/actions\n'
printf '  pypi:    https://pypi.org/project/mquery-toolkit/  (live once the workflow finishes)\n'
printf '\n  then verify from a clean venv:\n'
printf '    python3 -m venv /tmp/mq-live && /tmp/mq-live/bin/pip install mquery-toolkit && /tmp/mq-live/bin/mquery --help\n'
