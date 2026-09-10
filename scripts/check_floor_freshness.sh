#!/usr/bin/env bash
# Step 9 of release_gate.sh, extracted so that it can be TESTED.
#
# Round 58's HIGH: the missing-log branch printed `SKIP  no floor log present`
# and never touched the gate's failure counter, so a renamed, deleted, or
# dated-out log produced `GATE PASSED`, exit 0, with the evidence check never
# having run. The log's filename carries the date it was produced and the gate
# hardcoded one such name, so the next floor run on any other day would have
# made that silent pass permanent.
#
# That is the same shape release_gate.sh already memorialises in the comment
# 130 lines above its own step 9 - "a rename or a missing file sent the error
# to stderr and the gate ran on to GATE PASSED". Writing the check as its own
# script is what makes the branch reachable from a test instead of only from a
# 12-minute gate run.
#
# Prints the resolved log path on success; diagnosis on stderr otherwise.
# Exit: 0 fresh · 65 no log · 66 more than one log · 67 stale, unreadable, or
# the digest helper refused (its own 64 is re-reported as 67 rather than
# escaping undocumented - `v="$(helper)"` under `set -e` propagates the
# helper's status verbatim, which round 59 caught).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE

# Resolved from TRACKED files, never by a hardcoded date and never by a bare
# filesystem glob. The name changes every time the floor is re-run, so a
# hardcoded name silently stops matching - but a glob over the working
# directory is equally wrong in the other direction: any stray copy of an old
# log breaks the gate. Round 59 hit exactly that, and from a source no
# developer would think to look at - the review harness left a renamed-away
# BASE log on disk, so the tree under review had two.
#
# The floor log is committed evidence. What the gate certifies is the tracked
# one; untracked debris beside it is not evidence and does not get a vote.
# This also matches floor_digest.sh, which refuses untracked files in scope.
logs=()
while IFS= read -r line; do
    [[ -n "${line}" ]] && logs+=("${line}")
done < <(git ls-files -- 'evidence/floor-venv-suite-*.log')

if (( ${#logs[@]} == 0 )); then
    echo "no TRACKED floor log matching evidence/floor-venv-suite-*.log" >&2
    echo "produce one: scripts/floor_venv_run.sh <floor-venv-python> <log-path>" >&2
    echo "(an untracked log is not evidence - commit it)" >&2
    exit 65
fi
if (( ${#logs[@]} > 1 )); then
    echo "more than one tracked floor log; cannot tell which one to trust:" >&2
    printf '  %s\n' "${logs[@]}" >&2
    exit 66
fi

log="${logs[0]}"
if [[ ! -f "${log}" ]]; then
    echo "tracked floor log ${log} is missing from the working tree" >&2
    exit 67
fi
recorded="$(sed -n 's/^#   tree digest *: *//p' "${log}" | head -1)"
# Guarded, not bare: under `set -e` a bare `actual="$(helper)"` exits with the
# helper's own status, so floor_digest.sh's 64 (untracked file in scope) would
# leave this script with an exit code its header does not document and its
# caller cannot interpret.
if ! actual="$("${HERE}/floor_digest.sh")"; then
    echo "cannot compute the digest to compare against ${log}" >&2
    exit 67
fi

if [[ -z "${recorded}" ]]; then
    echo "floor log ${log} records no tree digest" >&2
    exit 67
fi
if [[ "${recorded}" != "${actual}" ]]; then
    echo "floor log ${log} is stale" >&2
    echo "  recorded: ${recorded}" >&2
    echo "  actual  : ${actual}" >&2
    echo "  regenerate: scripts/floor_venv_run.sh <floor-venv-python> ${log}" >&2
    exit 67
fi

echo "${log}"
