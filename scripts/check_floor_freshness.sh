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
# Exit: 0 fresh · 65 no log · 66 more than one log · 67 stale or unreadable.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HERE

# Resolved by glob, never by a hardcoded date: the name changes every time
# the floor is re-run, and a name that no longer matches must be an error
# rather than an absence.
shopt -s nullglob
logs=(evidence/floor-venv-suite-*.log)
shopt -u nullglob

if (( ${#logs[@]} == 0 )); then
    echo "no floor log matching evidence/floor-venv-suite-*.log" >&2
    echo "produce one: scripts/floor_venv_run.sh <floor-venv-python> <log-path>" >&2
    exit 65
fi
if (( ${#logs[@]} > 1 )); then
    echo "more than one floor log; cannot tell which one the gate should trust:" >&2
    printf '  %s\n' "${logs[@]}" >&2
    exit 66
fi

log="${logs[0]}"
recorded="$(sed -n 's/^#   tree digest *: *//p' "${log}" | head -1)"
actual="$("${HERE}/floor_digest.sh")"

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
