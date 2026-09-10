#!/usr/bin/env bash
# PRD 6.2 evidence: run the suite in a venv with NO optional extras, and emit a
# log whose header a reader can check against the tree it claims.
#
# This script exists because the header was hand-assembled for six rounds and
# six consecutive reviews found it claiming things it could not show. That was
# one defect, not six: the artifact and the claims about it had different
# authors. Everything below is emitted by the same invocation as the run.
#
# The script REFUSES to write when a claim would be unfalsifiable. Each refusal
# is a defect a review actually found:
#
#   r56  the recorded argv was false - the flags were interpolated unquoted and
#        zsh does not word-split, so pytest got one giant argument and ignored
#        it. argv is now an array, printed one %q-quoted word per line: joining
#        it with spaces reproduces the exact string of the defect, so the
#        recorded line could not distinguish the two.
#   r57  the `-rs` guard was confounded: pytest's -r default is 'fE', so the
#        `short test summary info` block prints on any run with a failure. The
#        guard now sums the per-group SKIPPED counts and requires that total to
#        equal the summary line's skip count. (Not the LINE count - pytest
#        groups skips by file/line/reason, so 37 lines carried 41 skips.)
#   r57  `$(git rev-parse '@{upstream}')` inlined in `echo` cannot fail under
#        `set -e`; in a detached worktree it printed an empty provenance line
#        and reported success. Every measurement is now its own statement, and
#        the tree is pinned by `HEAD^{tree}`, which always resolves.
#
# Usage: scripts/floor_venv_run.sh <floor-venv-python> <output-log> [dev-python]
set -euo pipefail

readonly FLOOR_PYTHON="${1:?usage: floor_venv_run.sh <floor-venv-python> <output-log> [dev-python]}"
readonly OUT="${2:?usage: floor_venv_run.sh <floor-venv-python> <output-log> [dev-python]}"
ROOT="$(git rev-parse --show-toplevel)"
readonly ROOT
# Script-relative, matching check_floor_freshness.sh. Two resolution
# strategies for the one dependency created to END duplication is how the
# duplication comes back: they diverge the moment `scripts/` is symlinked or
# relocated, and then the producer and the gate hash different scopes again.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE
cd "${ROOT}"
readonly DEV_PYTHON="${3:-${ROOT}/.venv/bin/python}"

for interpreter in "${FLOOR_PYTHON}" "${DEV_PYTHON}"; do
    [[ -x "${interpreter}" ]] || {
        echo "not an executable interpreter: ${interpreter}" >&2
        echo "(pass the dev interpreter as \$3 when running outside the main checkout," >&2
        echo " e.g. from a review worktree, which has no .venv)" >&2
        exit 2
    }
done

# `find_spec` is the right probe, not `import`: it returns None for a genuinely
# absent module, which is what "the extras are not installed" means.
present="$("${FLOOR_PYTHON}" - <<'PYPROBE'
import importlib.util as u
mods = ("pandas", "pyarrow", "openpyxl", "python_calamine")
print(" ".join(m for m in mods if u.find_spec(m) is not None))
PYPROBE
)"
if [[ -n "${present}" ]]; then
    echo "refusing: not a floor venv -- these extras are importable in" >&2
    echo "${FLOOR_PYTHON}: ${present}" >&2
    exit 68
fi
readonly extras="pandas, pyarrow, openpyxl, python_calamine all absent (find_spec -> None)"

export PYTHONDONTWRITEBYTECODE=1
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
PYCACHE_LEFT="$(find src tests -name __pycache__ -type d | wc -l | tr -d ' ')"
readonly PYCACHE_LEFT

# The scope and the hashing both live in floor_digest.sh now. They used to be
# written out here AND again in release_gate.sh step 9; round 58 caught the
# duplication one round after the scope changed, which is exactly when two
# copies diverge. The gate must certify the same set of files this header
# names, so there is one definition and both read it.
digest() {
    "${HERE}/floor_digest.sh"
}
DIGEST_BEFORE="$(digest)"; readonly DIGEST_BEFORE
DIGEST_FILES="$("${HERE}/floor_digest.sh" --files)"
readonly DIGEST_FILES
TREE_SHA="$(git rev-parse 'HEAD^{tree}')"; readonly TREE_SHA
UPSTREAM="$(git rev-parse --short '@{upstream}' 2>/dev/null || echo '(no upstream)')"
readonly UPSTREAM

readonly ARGS=(-p no:cacheprovider "--rootdir=${ROOT}" -rs)
FLOOR_COLLECT="$("${FLOOR_PYTHON}" -m pytest --collect-only -q "--rootdir=${ROOT}" 2>/dev/null | tail -1)"
readonly FLOOR_COLLECT
DEV_COLLECT="$("${DEV_PYTHON}" -m pytest --collect-only -q 2>/dev/null | tail -1)"
readonly DEV_COLLECT

body="$(mktemp)"
trap 'rm -f "${body}"' EXIT
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; readonly STARTED
set +e
"${FLOOR_PYTHON}" -m pytest "${ARGS[@]}" > "${body}" 2>&1
CODE=$?
set -e
readonly CODE
FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; readonly FINISHED
# Measured HERE, next to `finished`, because that is where it is PRINTED.
# Round 57 hoisted this out of an `echo` (a substitution inlined there cannot
# fail under `set -e`) but hoisted it to t=0, so the header reported the dirt
# from before a ~10-minute run. The stated reason for the field is an
# autocommit bot sweeping concurrently - which is exactly the thing a t=0
# reading cannot see.
UNCOMMITTED="$(git status --porcelain | wc -l | tr -d ' ')"
readonly UNCOMMITTED
DIGEST_AFTER="$(digest)"; readonly DIGEST_AFTER

# Did `-rs` actually apply? The block alone proves nothing (it also prints for
# failures under the default -r fE), so compare the skips it accounts for
# against the skips the summary counts.
skips_reported="$(grep -cE '^SKIPPED ' "${body}" || true)"
# awk, not `paste | bc`: bc is absent from slim images, and the old
# `2>/dev/null || echo 0` turned that absence into the number 0, which fails
# the equality below and aborts with the WRONG diagnosis - "'-rs' did not
# account for every skip" on a run where -rs worked perfectly.
skips_accounted="$(sed -nE 's/^SKIPPED \[([0-9]+)\].*/\1/p' "${body}" \
    | awk '{ s += $1 } END { print s + 0 }')"
# Anchored on the OUTCOME words, not merely on pytest's `=...=` banner shape.
# Round 59 measured the first attempt: `^=+ .* =+$` matches three lines in a
# real log - `test session starts`, `short test summary info`, and the actual
# result - so it still leaned on `tail -1` to pick the right one, which is the
# reliance the comment claimed to have removed. Only the result line carries
# passed/failed/error. Verified on this repo's own log: 3 matches -> 1.
skips_total="$(grep -E '^=+ .*(passed|failed|error).* =+$' "${body}" \
    | sed -nE 's/.*[^0-9]([0-9]+) skipped.*/\1/p' | tail -1)"
: "${skips_accounted:=0}" "${skips_total:=0}"
# A floor run MUST skip: the extras-dependent tests are exactly what the absent
# extras make unrunnable. Without this, a run with zero skips satisfies the
# check below trivially (0 == 0) while proving nothing about `-rs` - measured,
# a red probe file with no skips was written by an earlier version of this
# guard.
if [[ "${skips_total}" -eq 0 ]]; then
    echo "refusing to write: a floor run with ZERO skips is not a floor run -" >&2
    echo "the extras-dependent tests should be skipping. Nothing here could" >&2
    echo "show whether '-rs' applied." >&2
    exit 65
fi
if [[ "${skips_accounted}" != "${skips_total}" ]]; then
    echo "refusing to write: '-rs' did not account for every skip, so the" >&2
    echo "recorded argv would not describe the run that happened." >&2
    echo "  SKIPPED lines: ${skips_reported}  accounting for: ${skips_accounted}" >&2
    echo "  summary says skipped: ${skips_total}" >&2
    exit 66
fi
[[ "${DIGEST_BEFORE}" == "${DIGEST_AFTER}" ]] || {
    echo "refusing to write: the tree changed under the run" >&2
    echo "  before: ${DIGEST_BEFORE}" >&2
    echo "  after : ${DIGEST_AFTER}" >&2
    exit 67
}

{
    echo "# PRD 6.2 - the suite with pandas, pyarrow, openpyxl and python-calamine ABSENT."
    echo "#"
    echo "# Produced by scripts/floor_venv_run.sh, which is committed, is in the"
    echo "# digest below, and is the only thing that writes this file. Every line"
    echo "# here is emitted by the same invocation as the run; none is typed by"
    echo "# hand. The script refuses to write when a claim would be unfalsifiable."
    echo "#"
    echo "#   pytest argv, one %q-quoted word per line:"
    printf '#     %q\n' "${ARGS[@]}"
    echo "#   (printed per word because joining an array with spaces reproduces"
    echo "#    the exact one-giant-argument string of the round-56 defect)"
    echo "#"
    echo "#   __pycache__ dirs        : ${PYCACHE_LEFT}"
    echo "#   PYTHONDONTWRITEBYTECODE : ${PYTHONDONTWRITEBYTECODE}"
    echo "#   extras absent           : ${extras}"
    echo "#   skips: ${skips_reported} SKIPPED lines accounting for ${skips_accounted}, summary says ${skips_total}"
    echo "#"
    echo "#   tree digest             : ${DIGEST_BEFORE}"
    echo "#     scripts/floor_digest.sh - the single definition, read by this"
    echo "#     script and by release_gate.sh step 9 so the gate cannot certify"
    echo "#     a different scope than this header names."
    echo "#     over ${DIGEST_FILES} TRACKED files. Untracked files are refused rather"
    echo "#     than hashed: including them produced a digest no clone, CI run or"
    echo "#     review worktree could reproduce. Re-measured after the run:"
    echo "#     ${DIGEST_AFTER}"
    echo "#"
    echo "#   git tree sha            : ${TREE_SHA}"
    echo "#   pushed tip              : ${UPSTREAM}"
    echo "#   uncommitted             : ${UNCOMMITTED} file(s)"
    echo "#   started                 : ${STARTED}"
    echo "#   finished                : ${FINISHED}"
    echo "#   collected (floor)       : ${FLOOR_COLLECT}"
    echo "#   collected (dev)         : ${DEV_COLLECT}"
    echo "#"
    cat "${body}"
    echo "FLOOR EXIT: ${CODE}"
} > "${OUT}"

echo "wrote ${OUT} (pytest exit ${CODE}, ${skips_accounted} skips accounted, digest stable)"
# pytest's own code, passed through. This is safe ONLY because every refusal
# above uses the 64+ sysexits range: pytest exits 1-5 (5 = no tests collected,
# 4 = usage error, 3 = internal error), so while the refusals sat at 3/4/5 a
# caller could not tell "the tree moved under the run" from "pytest collected
# nothing". Do not move a refusal back below 64.
exit "${CODE}"
