# Handoff, 2026-09-07 - one outstanding gap

## Resolved since the first version of this file

Everything this file originally described as blocked is done. The
`core._atomic_write` lock fix is committed, and the errno decision it was
patching has been restructured into one table rather than patched a fifth
time (see `AUDIT-2026-09-05-CLOSEOUT.md`, "Round 15 - the errno taxonomy").

Gate on the restructured tree: **8/8 PASSED**, 4124 tests, 953 worked
examples exact - `evidence/release-gate-2026-09-07-round15-errno-taxonomy.log`.

## The one thing still open

**No independent review was obtained for `b44f459..1f6682f`, nor for the
errno restructuring on top of it.** Every round from 9 to 14 closed the
review-the-fix loop; this stretch did not.

Why, measured at the time:

- `evidence/run-opus-gate.sh` was killed by the harness for system memory on
  every attempt. `vm.swapusage` read `used = 4045-4562M / 5120M` with ~63
  Chrome processes holding ~1.65 GB. The wrapper spawns a full `claude -p`.
- The documented light fallback, `.max/tools/duet.py --glm-only --free-only`,
  was down the same day: `clinepass#1:500, clinepass#2:403, clinepass#3:500,
  cf:403`.

So the round-14 fixes and the round-15 restructuring are **gate-verified and
control-verified, but not review-verified**. That is a weaker chain than
rounds 9-14 and is stated here rather than left implicit.

## To close it

1. Free memory first - this is the blocker, not the code. Closing browser
   windows is G's call; do not kill them unasked.
2. `bash evidence/run-opus-gate.sh 1f6682f <tip> "$(pwd)/evidence/opus5-wrapper-round16-1f6682f..<tip>.txt"`
3. Reproduce every finding against the committed tree before fixing anything,
   dispose of each in the closeout, positive-control each behavioural fix.

## Standing

Tag and PyPI publish remain G's explicit call and were not touched.
Do NOT chunk the test suite to get under the memory limit: this repo has a
cross-file failure (the corpus BRIDGE_FAILURE that appears only after
`test_core.py`), so a chunked "N passed" asserts strictly less than one run
while printing an identical number.
