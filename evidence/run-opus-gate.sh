#!/usr/bin/env bash
# Exact claude-opus-5 wrapper gate for pqtools (formerly mquery-toolkit), reproducible.
# Reviews BASE..HEAD with the 2.6 MB vendored bundle and the npm lockfile replaced by
# 1-line stub blobs (so the diff fits the model and the reviewer still sees the files exist).
# Usage: bash run-opus-gate.sh <base-sha> <head-sha> <out-file>
set -uo pipefail
BASE="$1"; HEAD="$2"; OUT="$3"
REPO="/Users/gopalmacbook/Desktop/Max HQ/pqtools"
WT="/tmp/mq-gate-wt-$$"
cd "$REPO" || exit 1
# A relative OUT was resolved inside the temporary worktree below and then
# deleted with it - a 15-minute review with nothing to show for it
# (round 9). Anchor it to the repo before the first cd away.
[[ "$OUT" = /* ]] || OUT="$REPO/$OUT"
# A killed run leaves its worktree registered and on disk. Two such - both at
# b44f459, from the OOM kills around round 13 - were still there at round 22,
# and a reviewer inspecting one reported an empty index and BASE-era files,
# then withdrew findings it could not verify.
#
# The trap below is NOT what fixes that case, and round 22's write-up was wrong
# to imply it was. An OOM kill is SIGKILL, which is untrappable - verified: a
# handler on EXIT INT TERM does not run on `kill -9`, and does run on
# `kill -TERM`. So the leak those two worktrees came from would happen
# identically today with the trap in place. The startup sweep is what covers
# it; the trap only covers the signals a process can see.
#
# The sweep is also not optional hygiene. `$$` wraps (kern.maxproc is 2000
# here), so a leaked /tmp/mq-gate-wt-<pid> is eventually the name the next run
# wants, and `git worktree add` aborts with "fatal: ... already exists"
# (verified). `git worktree prune` alone does not help: it only drops
# registrations whose directory is GONE, and these are on disk (verified - a
# stale worktree stays registered across a prune).
sweep() {
  local dir pid
  for dir in /tmp/mq-gate-wt-* /private/tmp/mq-gate-wt-*; do
    [ -d "$dir" ] || continue
    pid="${dir##*-}"
    case "$pid" in (*[!0-9]*|'') continue;; esac
    [ "$pid" = "$$" ] && continue
    # A live run owns its directory - never touch a concurrent review.
    kill -0 "$pid" 2>/dev/null && continue
    git worktree remove --force "$dir" 2>/dev/null
    rm -rf -- "$dir"
  done
  git worktree prune 2>/dev/null
}
cleanup() { cd "$REPO" 2>/dev/null && git worktree remove --force "$WT" 2>/dev/null; }
# Bash RESUMES after a signal handler returns (verified), so a bare `cleanup`
# on INT/TERM would delete the worktree and then let the script write its
# footer and `tail` the file - producing a truncated $OUT in evidence/ that
# reads like a completed review. Exit from the handler.
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
sweep
git worktree add -q "$WT" "$BASE" || exit 1
cd "$WT" || exit 1
git read-tree "$HEAD"

# The index now holds HEAD while the FILES on disk still hold BASE, which is
# what `git diff --cached` needs (the worktree's HEAD commit is BASE, so the
# staged diff is exactly BASE..HEAD). But a reviewer that opens a file reads
# the BASE version, and round 22 said so plainly: "I cannot verify the diff's
# own edits against a real tree from this session."
#
# Materialise HEAD's files too. This does not disturb the staged diff - that
# is computed from the worktree's HEAD COMMIT (still BASE) against the INDEX
# (still HEAD), neither of which the working files participate in.
git checkout-index -a -f
# checkout-index only writes; paths deleted between BASE and HEAD would linger.
# -z, because `git diff --name-only` C-QUOTES a non-ASCII path - verified:
# `src/café.py` comes back as `"src/caf\303\251.py"`, which names no file, and
# `rm -f` swallows the ENOENT silently. And -rf, because `rm -f` refuses a
# directory ("is a directory", exit 0 under the -f) so a deleted submodule or
# directory would survive. Either way a BASE-only path lingers in the tree the
# reviewer reads, which is the staleness this block exists to prevent.
git diff -z --name-only --diff-filter=D "$BASE" "$HEAD" \
  | while IFS= read -r -d '' gone; do
      [ -n "$gone" ] && rm -rf -- "$gone"
    done
STUB_BRIDGE=$(printf '// vendored esbuild bundle of @microsoft/powerquery-parser 2.0.0 + powerquery-formatter 1.0.0 (2.6 MB, committed; excluded from review diff, reproducible via `npm run bundle`)\n' | git hash-object -w --stdin)
STUB_LOCK=$(printf '{ "_note": "package-lock.json is committed (npm lockfile v3, pins parser 2.0.0 / formatter 1.0.0 / esbuild 0.28.2); excluded from review diff" }\n' | git hash-object -w --stdin)
# The package was renamed mquery_toolkit -> pqtools in 0.2.0. This line kept the
# old path, so the stub landed on a file that does not exist and the real 2.6 MB
# bundle went into the review diff unstubbed - a silent degradation, not an error.
git ls-files --error-unmatch src/pqtools/_bridge.cjs >/dev/null || {
  echo "BLOCKED: src/pqtools/_bridge.cjs is not tracked; the stub path is stale again." >&2
  exit 2
}
# Stub a file ONLY when it changed in BASE..HEAD. The index holds HEAD and the
# worktree's commit is BASE, so stubbing an unchanged file replaces HEAD's blob
# while BASE keeps the real one - and the diff shows the 2.6 MB bundle being
# DELETED: 46,288 lines, "Prompt is too long", BLOCKED. Fixing the stale path
# above is what exposed this; while the path was stale nothing was stubbed and
# an unchanged bundle produced no diff at all, which was the right result by
# accident.
NOTES=""
stub() {
  if git diff --quiet "$BASE" "$HEAD" -- "$1"; then
    NOTES="$NOTES# $1: unchanged in $BASE..$HEAD, not stubbed, not in the diff\n"
  else
    git update-index --cacheinfo "100644,$2,$1"
    NOTES="$NOTES# $1: changed in $BASE..$HEAD, shown as a stub blob\n"
  fi
}
stub src/pqtools/_bridge.cjs "$STUB_BRIDGE"
stub package-lock.json "$STUB_LOCK"
STAT=$(git diff --cached --stat | tail -1)
if [ -n "${DRY_RUN:-}" ]; then
  printf '%b' "$NOTES"; echo "DRY_RUN staged: $STAT"
  exit 0
fi
{
  echo "# Exact claude-opus-5 wrapper review - range $BASE..$HEAD (bundle + lockfile shown as stub blobs)"
  echo "# staged: $STAT"
  printf '%b' "$NOTES"
  echo "# invoked $(date -u +%FT%TZ) via ~/.codex/skills/claude-review/bin/review.sh (ANTHROPIC_API_KEY unset inside the wrapper)"
  # Round 45: the file said "Exact claude-opus-5" in its title and recorded
  # nothing about the model, while four captured reviews carried the marker
  # `ox-alpha` in their body. That marker is the MODEL'S SELF-LABEL, written
  # under this machine's model-indicator rule - it is not provenance, and the
  # difference was not written down anywhere. What IS provenance: review.sh
  # calls `claude -p --model "$MODEL"` and refuses to run if MODEL is anything
  # but claude-opus-5. Record the requested model and say which is which.
  echo "# model requested: ${CLAUDE_REVIEW_MODEL:-claude-opus-5} (review.sh refuses any substitution)"
  echo "# NOTE: any model marker inside the review body below is the model's own"
  echo "#       self-label, not evidence of provenance. The line above is."
  echo
  timeout 900 bash "$HOME/.codex/skills/claude-review/bin/review.sh" --staged
  RC=$?
  echo
  echo "# wrapper exit: $RC (0=SHIP, 2=FIX-FIRST, 3=BLOCKED/no standalone verdict line)"
} > "$OUT" 2>&1
# Teardown has ONE owner, the EXIT trap. The explicit removals that used to sit
# here and in the DRY_RUN branch just ran it twice.
cd "$REPO" || exit 1
tail -3 "$OUT"
# Round 46: line 5 has always said "exit 0=SHIP, 2=FIX-FIRST, 3=BLOCKED", and
# the script has never done it. `tail` was the last command, so the wrapper
# exited 0 whatever the review said - a caller checking `$?` reads SHIP for a
# BLOCKED run. Found when a session-limit block wrote `# wrapper exit: 3` into
# the file while the calling shell printed 0. Nothing was mis-recorded, because
# every round read the file; but the promise in the usage line was false, and
# an exit code that always says success is the same class of artifact as the
# 473-byte review stub. `RC` survives the brace group above - `{ ... } > file`
# is not a subshell (verified).
exit "$RC"
