#!/usr/bin/env bash
# PRD 6.2 evidence: run the suite in a venv with NO optional extras, and emit a
# log whose header a reader can check against the tree it claims.
#
# This exists because the header was hand-assembled for six rounds and the
# reviews kept finding it claiming things it could not show. Every claim below
# is now produced by this script, in the same invocation as the run:
#
#   * `-rs` is ASSERTED to have taken effect (the skip block must be present).
#     Round 56 found the recorded argv false: the flags were interpolated
#     unquoted from a variable, and zsh does not word-split, so pytest got one
#     giant argument and silently ignored it. Nothing in the artifact showed
#     that. Now argv is an array, and the run fails if `-rs` did not apply.
#   * the tree digest covers TRACKED AND UNTRACKED files (`-co`), because
#     pytest collects an untracked test and `git ls-files` alone cannot see it.
#     `--exclude-standard` keeps `.gitignore` honoured, so `.samples/` stays out.
#   * the digest is re-measured after the run and must be unchanged.
#   * timestamps bracket the run from outside pytest.
#
# Usage: scripts/floor_venv_run.sh <floor-venv-python> <output-log>
set -euo pipefail

readonly FLOOR_PYTHON="${1:?usage: floor_venv_run.sh <floor-venv-python> <output-log>}"
readonly OUT="${2:?usage: floor_venv_run.sh <floor-venv-python> <output-log>}"
ROOT="$(git rev-parse --show-toplevel)"
readonly ROOT
cd "${ROOT}"

[[ -x "${FLOOR_PYTHON}" ]] || { echo "not executable: ${FLOOR_PYTHON}" >&2; exit 2; }

# The extras MUST be absent, or this is not the floor.
# `find_spec` is the right probe, not `import`: it returns None for a genuinely
# absent module, which is what "the extras are not installed" means. Reported as
# names only - a bare find_spec dump is a screenful of ModuleSpec repr.
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

digest() {
    git ls-files -co --exclude-standard src tests pyproject.toml \
        | sort | xargs shasum -a 256 | shasum -a 256 | cut -d' ' -f1
}
DIGEST_BEFORE="$(digest)"
readonly DIGEST_BEFORE
DIGEST_FILES="$(git ls-files -co --exclude-standard src tests pyproject.toml | wc -l | tr -d ' ')"
readonly DIGEST_FILES

# An ARRAY, not a string: this is the defect round 56 raised.
readonly ARGS=(-p no:cacheprovider "--rootdir=${ROOT}" -rs)
FLOOR_COLLECT="$("${FLOOR_PYTHON}" -m pytest --collect-only -q "--rootdir=${ROOT}" 2>/dev/null | tail -1)"
readonly FLOOR_COLLECT
DEV_COLLECT="$(.venv/bin/python -m pytest --collect-only -q 2>/dev/null | tail -1)"
readonly DEV_COLLECT

body="$(mktemp)"
trap 'rm -f "${body}"' EXIT
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
readonly STARTED
set +e
"${FLOOR_PYTHON}" -m pytest "${ARGS[@]}" > "${body}" 2>&1
readonly CODE=$?
set -e
FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
readonly FINISHED
DIGEST_AFTER="$(digest)"
readonly DIGEST_AFTER

# The argv claim must be falsifiable FROM THE ARTIFACT. `-rs` with skips prints
# the summary block; if it is absent the flag did not apply and the header
# would be lying, exactly as it did in round 55.
if ! grep -q "short test summary info" "${body}"; then
    echo "refusing to write: '-rs' produced no skip block, so the recorded argv" >&2
    echo "would not describe the run that happened" >&2
    exit 4
fi
[[ "${DIGEST_BEFORE}" == "${DIGEST_AFTER}" ]] || {
    echo "refusing to write: the tree changed under the run" >&2; exit 5; }

{
    echo "# PRD 6.2 - the suite with pandas, pyarrow, openpyxl and python-calamine ABSENT."
    echo "#"
    echo "# Produced by scripts/floor_venv_run.sh, which is committed and is the only"
    echo "# thing that writes this file. Every line below is emitted by the same"
    echo "# invocation as the run; none of it is typed by hand. The script refuses to"
    echo "# write at all if '-rs' did not take effect or the tree moved under the run."
    echo "#"
    echo "#   pytest argv        : ${ARGS[*]}"
    echo "#   __pycache__ dirs   : ${PYCACHE_LEFT}"
    echo "#   PYTHONDONTWRITEBYTECODE : ${PYTHONDONTWRITEBYTECODE}"
    echo "#   extras absent      : ${extras}"
    echo "#"
    echo "#   tree digest        : ${DIGEST_BEFORE}"
    echo "#     git ls-files -co --exclude-standard src tests pyproject.toml \\"
    echo "#       | sort | xargs shasum -a 256 | shasum -a 256"
    echo "#     over ${DIGEST_FILES} files, tracked AND untracked (pytest collects an"
    echo "#     untracked test; 'git ls-files' alone cannot see one). Re-measured"
    echo "#     after the run: ${DIGEST_AFTER}"
    echo "#"
    echo "#   started            : ${STARTED}"
    echo "#   finished           : ${FINISHED}"
    echo "#   collected (floor)  : ${FLOOR_COLLECT}"
    echo "#   collected (dev)    : ${DEV_COLLECT}"
    # Pinned to the pushed tip, not to HEAD: this repo is swept by an estate
    # autocommit bot mid-run, so HEAD is often a `chore(auto)` commit that is
    # soft-reset away minutes later, leaving an unresolvable sha in an
    # evidence file. The upstream tip is always resolvable; the digest above
    # is what actually pins the tree.
    echo "#   pushed tip         : $(git rev-parse --short '@{upstream}') on $(git rev-parse --abbrev-ref '@{upstream}')"
    echo "#   local HEAD at run  : $(git rev-parse --short HEAD)$(git merge-base --is-ancestor '@{upstream}' HEAD 2>/dev/null && echo '' || echo ' (diverged)')"
    echo "#   uncommitted        : $(git status --porcelain | wc -l | tr -d ' ') file(s)"
    echo "#"
    cat "${body}"
    echo "FLOOR EXIT: ${CODE}"
} > "${OUT}"
echo "wrote ${OUT} (exit ${CODE}, skip block present, digest stable)"
