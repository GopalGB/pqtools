# Handoff, 2026-09-07 - the review chain is closed again

## State

| | |
|---|---|
| Tip | `c79b676`, pushed and verified against `git ls-remote` |
| Gate | 8/8 PASSED, 4126 tests, 953 worked examples exact - `evidence/release-gate-2026-09-07-round16-fixes.log` |
| Reviews | rounds 9-14 and 16 obtained and dispositioned in `AUDIT-2026-09-05-CLOSEOUT.md` |

## The gap that existed, and how it closed

For a stretch, `b44f459..fcce6c3` was gate-verified but NOT review-verified:
`evidence/run-opus-gate.sh` was killed by the harness for system memory on
every attempt (`vm.swapusage used = 4045-4562M / 5120M`, ~63 Chrome processes
holding ~1.65 GB), and the documented light fallback
`.max/tools/duet.py --glm-only --free-only` was down the same day
(`clinepass#1:500, clinepass#2:403, clinepass#3:500, cf:403`).

It closed once memory freed: the round-16 review covered the WHOLE span in one
pass and returned FIX-FIRST with five findings, all reproduced and fixed in
`c79b676`. So the chain has no hole in it now - it was recorded as open while
it was open, and closed by a real review rather than by declaring it fine.

**The lesson worth keeping:** the review is not optional assurance. The
round-16 review caught a regression the round-15 RESTRUCTURING introduced -
the ELOOP re-check degrading the exact TOCTOU refusal it was meant to protect.
Gate + positive controls did not catch it, because the test written alongside
the change modelled only the case the author had in mind.

## Outstanding

Round 17 - the review of `fcce6c3..c79b676` (the round-16 fixes). Same
discipline: reproduce every finding against the committed tree first, dispose
of each in the closeout, positive-control each behavioural fix, and record any
control that cannot be made red rather than counting it.

## Standing

Tag and PyPI publish remain G's explicit call and were not touched.
Run the gate on an idle machine; check `sysctl vm.swapusage` first. Do NOT
chunk the suite to get under the limit - this repo has a cross-file failure
(the corpus BRIDGE_FAILURE that appears only after `test_core.py`), so a
chunked "N passed" asserts strictly less than one run while printing an
identical number.
