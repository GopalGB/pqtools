#!/usr/bin/env bash
# The one definition of "which files the floor evidence is about".
#
# Two callers need this and they MUST agree: floor_venv_run.sh records the
# digest in the log header, release_gate.sh step 9 recomputes it to decide
# whether that log is stale. Round 58 found the pipeline duplicated verbatim
# in both, one round after the scope had changed. The next divergence would
# have had the gate certifying a different scope than the log's own printed
# command, with nothing to notice.
#
# Scope is the suite plus its producer: what the tests are, what they run
# against, and the scripts that claim to have run them. release_gate.sh is
# deliberately NOT here. It consumes this evidence rather than producing it,
# and including all of scripts/ made the evidence self-invalidating - editing
# the gate, or any unrelated helper, reddened step 9 until a ~12-minute floor
# re-run on a machine whose suite needs to be left alone to finish.
#
# Tracked files only. `-co` put the developer's private untracked files into
# the digest, producing a value that no clone, CI run, or review worktree
# could reproduce - and step 9's printed remedy (re-run the floor) cannot fix
# that, because the untracked file is still there afterwards. An untracked
# file in scope is therefore a refusal, not an input.
#
# Exit: 0 ok · 2 bad usage · 64 untracked file in scope · 65 empty scope.
#
# Usage gets its own code and is checked FIRST. Both used to be 64 with the
# scan running first, so `floor_digest.sh --bogus` reported "untracked file(s)
# in scope" whenever the scope happened to be dirty, and no caller could tell
# a typo from a real refusal by exit code alone.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

readonly SCOPE=(
    src
    tests
    pyproject.toml
    scripts/floor_venv_run.sh
    scripts/floor_digest.sh
)

# One flag means one flag. Only $1 used to be inspected, so a second word
# was silently accepted: `floor_digest.sh --files junk` printed the 125-file
# scope count and exited 0 (measured, round 60). Refused with 2, before the
# untracked scan, so a typo in a dirty tree still reads as a typo.
case "${1-}" in
"" | --files)
    if (( $# > 1 )); then
        echo "usage: floor_digest.sh [--files]" >&2
        exit 2
    fi
    ;;
*)
    echo "usage: floor_digest.sh [--files]" >&2
    exit 2
    ;;
esac

untracked="$(git ls-files -o --exclude-standard -- "${SCOPE[@]}")"
if [[ -n "${untracked}" ]]; then
    echo "floor_digest: untracked file(s) in scope - commit or remove them first:" >&2
    echo "${untracked}" >&2
    exit 64
fi

# A scope that matches nothing must not hash to a confident-looking value:
# `shasum` of empty input is a perfectly good digest of nothing, and it would
# compare equal to another empty run forever.
tracked_count="$(git ls-files -z -- "${SCOPE[@]}" | tr -dc '\0' | wc -c | tr -d ' ')"
if [[ "${tracked_count}" -eq 0 ]]; then
    echo "floor_digest: the scope matches no tracked files - refusing to" >&2
    echo "hash nothing. Is this a real checkout of the repo?" >&2
    exit 65
fi

case "${1-}" in
"")
    # -z, then sort -z: a newline in a path would otherwise split one name
    # into two and change the digest without changing the tree.
    git ls-files -z -- "${SCOPE[@]}" \
        | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1
    ;;
--files)
    # Already counted above, the NUL-separator way. `wc -l` disagreed with the
    # digest on exactly the newline-in-path case -z exists for.
    #
    # NOT `awk 'BEGIN { RS = "\0" }'`, which is the obvious replacement and is
    # wrong here: macOS awk reads "\0" as the empty string and switches to
    # paragraph mode, making the whole NUL-separated blob ONE record. Measured
    # - `printf 'a\0b\0c\0'` through that awk counts 1, and it reported 1 file
    # for a 125-file scope while the digest beside it was computed over all
    # 125. A count is evidence, so it gets its own control.
    echo "${tracked_count}"
    ;;
esac
