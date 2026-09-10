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
    exit 3
fi
readonly extras="pandas, pyarrow, openpyxl, python_calamine all absent (find_spec -> None)"

export PYTHONDONTWRITEBYTECODE=1
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
PYCACHE_LEFT="$(find src tests -name __pycache__ -type d | wc -l | tr -d ' ')"
readonly PYCACHE_LEFT

# -z/-0 throughout: `-co` pulls in untracked paths, and a path with a space
# would otherwise be split into two filenames and hashed as neither.
# `scripts` is in scope because this script is one of the things the artifact
# claims produced it.
digest() {
    git ls-files -zco --exclude-standard src tests scripts pyproject.toml \
        | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1
}
DIGEST_BEFORE="$(digest)"; readonly DIGEST_BEFORE
DIGEST_FILES="$(git ls-files -co --exclude-standard src tests scripts pyproject.toml | wc -l | tr -d ' ')"
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
DIGEST_AFTER="$(digest)"; readonly DIGEST_AFTER

# Did `-rs` actually apply? The block alone proves nothing (it also prints for
# failures under the default -r fE), so compare the skips it accounts for
# against the skips the summary counts.
skips_reported="$(grep -cE '^SKIPPED ' "${body}" || true)"
skips_accounted="$(sed -nE 's/^SKIPPED \[([0-9]+)\].*/\1/p' "${body}" | paste -sd+ - | bc 2>/dev/null || echo 0)"
skips_total="$(sed -nE 's/.*[^0-9]([0-9]+) skipped.*/\1/p' "${body}" | tail -1)"
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
    exit 6
fi
if [[ "${skips_accounted}" != "${skips_total}" ]]; then
    echo "refusing to write: '-rs' did not account for every skip, so the" >&2
    echo "recorded argv would not describe the run that happened." >&2
    echo "  SKIPPED lines: ${skips_reported}  accounting for: ${skips_accounted}" >&2
    echo "  summary says skipped: ${skips_total}" >&2
    exit 4
fi
[[ "${DIGEST_BEFORE}" == "${DIGEST_AFTER}" ]] || {
    echo "refusing to write: the tree changed under the run" >&2
    echo "  before: ${DIGEST_BEFORE}" >&2
    echo "  after : ${DIGEST_AFTER}" >&2
    exit 5
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
    echo "#     git ls-files -zco --exclude-standard src tests scripts pyproject.toml \\"
    echo "#       | sort -z | xargs -0 shasum -a 256 | shasum -a 256"
    echo "#     over ${DIGEST_FILES} files, tracked AND untracked (pytest collects an untracked"
    echo "#     test; git ls-files alone cannot see one). Re-measured after the run:"
    echo "#     ${DIGEST_AFTER}"
    echo "#"
    echo "#   git tree sha            : ${TREE_SHA}"
    echo "#   pushed tip              : ${UPSTREAM}"
    echo "#   uncommitted             : $(git status --porcelain | wc -l | tr -d ' ') file(s)"
    echo "#   started                 : ${STARTED}"
    echo "#   finished                : ${FINISHED}"
    echo "#   collected (floor)       : ${FLOOR_COLLECT}"
    echo "#   collected (dev)         : ${DEV_COLLECT}"
    echo "#"
    cat "${body}"
    echo "FLOOR EXIT: ${CODE}"
} > "${OUT}"

echo "wrote ${OUT} (pytest exit ${CODE}, ${skips_accounted} skips accounted, digest stable)"
exit "${CODE}"
