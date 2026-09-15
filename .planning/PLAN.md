# pqtools Claude Closeout Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` if available in the executor environment; otherwise follow this checklist sequentially with the same evidence and approval gates. The selected executor is **soark llm**, as requested by G. Do not substitute a model or launch execution from this planning session. Steps use checkboxes; mark one complete only after recording its evidence.

**Goal:** Finish Claude's outstanding pqtools work, establish reproducible local release evidence, and prepare the package and related website for separately approved publication.

**Architecture:** Preserve the existing evaluator, export API, CLI and typed refusals. Make the evidence producer and consumer agree on deterministic content identity and successful execution; materialize review trees with Git's native machinery. Reuse the product tests and acceptance contract, then freeze code before producing final evidence.

**Tech stack:** Python >=3.11, Bash, Node >=22, Microsoft's pinned parser/formatter bridge, pytest, Ruff, strict mypy, optional pandas/Arrow/Excel extras, GitHub Actions. The separate portfolio is Next.js with its own package scripts and repository instructions.

**Spec:** [RECOVERY-2026-09-14.md](RECOVERY-2026-09-14.md), plus [.planning/PRD-pandas-for-powerquery-2026-09-06.md](PRD-pandas-for-powerquery-2026-09-06.md). The recovery contract explicitly records differences between historical proposals and the currently documented API.

**Planning review:** `gpt-5.6-sol` at xhigh reviewed the draft. Five corrections were incorporated: eliminate the cross-phase dependency, put mutation-capable preparation before freeze, keep the consumer contract minimal, allow a skill-free executor to follow the same checklist, and review the portfolio candidate separately. This was a plan review, not an Opus code review or executed test pass.

**Status:** PLAN ONLY. No implementation, tests, dependency installation, release, or deployment has been performed for this plan. Read-only Git, artifact, byte-comparison and digest probes informed it.

## Global constraints

- Preserve starting HEAD `6e1c90d0e1df00b0dfdc74247e0a8872df71dda8`, branch `feat/connectors-and-real-m-0.9.0`, and the pre-existing modified floor log. Recheck all three before execution; stop and reconcile if they changed.
- Work sequentially. Keep one heavy job at a time. Never use `pytest -n auto` on this Mac, chunk a full suite into a claimed full pass, kill someone else's processes, or launch estate indexing/model fights.
- Implementation is authorized only when G explicitly hands this plan to the executor to run. Once authorized, follow the phases continuously if G requests full execution; do not repeatedly ask for routine reversible work.
- Push, tag, PyPI publication, website deployment, account changes and messages remain explicit G actions. Preparing their artifacts is in scope; performing them is not implied.
- Keep `.samples/` ignored and uncommitted. Exercise writes only on copies. Never read credential files or print secret values. Do not edit global rules, skills, hooks, or Git internals directly.
- `SUPPORT-MATRIX.md` is the support authority. Names registered, signatures verified, and semantic compatibility are distinct. No full-Mashup, folding or universal-equivalence claims.
- Preserve `--format json|csv`, `--to parquet --out`, M-expression parameter binding under I/O policy, stable JSON shapes, exit codes, and typed refusals. No interface redesign.
- No new required runtime dependency. No widening evaluator semantics or implementing the explicitly deferred services/formats. Dependency floor changes require evidence and a separate recorded decision.
- Reviews request exactly `claude-opus-5`; unavailable/limited/malformed output means `REVIEW_BLOCKED`, never a fallback or fabricated SHIP. Planner verification uses `gpt-5.6-sol` where the configured routing requires it.
- Run positive controls on isolated temporary fixtures/copies. Read the actual failing assertion, restore the implementation, then rerun. Do not mutate a shared checkout while a floor run or review reads it.
- Store full transient outputs under a task-specific directory outside the repo; copy only finished, inspected, nonsensitive receipts into the closeout. Never hand-edit a produced floor log or turn a killed/header-only review into clean evidence.

## Execution state and file ownership

At execution start, create `.planning/CLOSEOUT-STATE-2026-09-14.md` with: phase/task, candidate SHA, dirty paths, focused test results, floor digest/log SHA-256, review base/head/verdict, release-gate result, security receipts, public-release state, and one exact next command. Update after each completed task or interruption. Do not rewrite the 5,000+ line historical closeout to make it look current.

| Files | Responsibility |
|---|---|
| `scripts/floor_digest.sh`, `tests/test_end_to_end.py` | Locale-independent content identity and behavior regressions. |
| `scripts/check_floor_freshness.sh`, `scripts/floor_venv_run.sh` only if necessary, same test module | Reject incomplete/failed/malformed floor evidence; preserve producer contract. |
| `evidence/run-opus-gate.sh`, new `scripts/materialize_review_tree.sh`, new `tests/test_review_materialization.py` | Native isolated review-tree construction, tested before contacting a reviewer. |
| `README.md`, `llms.txt`, `SUPPORT-MATRIX.md`, `pyproject.toml` comments only | Accurate usage, dependency evidence and claims; no manifest dependency changes by default. |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | Enforce successful no-extras verification in CI/release, rather than only the dev environment. |
| Existing `tests/test_export.py`, `tests/test_cli_verbs.py`, `tests/test_container_workflow.py`, safety suites | Reuse acceptance coverage; patch only reproduced defects with focused regressions. |
| `evidence/floor-venv-suite-2026-09-10.log` | Replace through the producer after code is frozen; retain one tracked floor log. Keep the existing filename to avoid a pointless rename. Header timestamps identify the new run. |
| `.planning/CLOSEOUT-STATE-2026-09-14.md`, `.planning/ACCEPTANCE-2026-09-14.md`, `.planning/RELEASE-HANDOFF-2026-09-14.md` | Small current checkpoint, acceptance table, and publication handoff. |
| Separate portfolio `components/sections/work.tsx`; `components/project-tile.tsx` only if a link defect reproduces | Correct only the pqtools card; preserve unrelated site work. |

Any new product defect discovered during validation must name the failing behavior, exact existing module, regression and scope before editing. Cosmetic rewrites and speculative cleanup are not part of this plan.

## Phase 1 - Preserve Claude's checkpoint and establish the baseline

**Success:** The executor can distinguish original work from its changes and completed historical runs from present validation. No unfinished work is discarded.

- [ ] Read this plan and its recovery contract. Read the applicable AGENTS.md and execution skills. Classify this as EXECUTE of an approved plan; use the model G selected.
- [ ] Record the baseline with `git status --short`, `git rev-parse HEAD`, `git diff --stat`, `git diff -- evidence/floor-venv-suite-2026-09-10.log`, and `git diff --cached --stat`. If changes differ from the recovery snapshot, inspect and reconcile before continuing.
- [ ] Create one scratch directory with `mktemp -d /tmp/pqtools-closeout-20260914.XXXXXX`. Save its absolute path in state. Save the existing floor log and both unstaged/staged diffs there, without copying ignored or credential files. Use targeted paths, never `git add -A` in the shared checkout.
- [ ] Read the exact round-60 review receipt named in RECOVERY.md and the `abb92ca..6e1c90d` diff. Disposition its six findings against the current code; do not apply old recommendations blindly. If the scratch receipt disappeared, extract only that receipt from the named JSONL.
- [ ] Check `sysctl vm.swapusage`, `df -h .`, and process names for existing pytest/floor/review runs. Do not print full arbitrary process environments. If this repo is actively being modified or the host cannot sustain one suite, record `RESOURCE_OR_CONCURRENT_WRITER_BLOCKED`; use a user-approved idle window/isolated checkout without disabling global automation.
- [ ] Run the cheap baseline probes below with exit codes captured separately. Save full outputs. A stale-digest result is an expected finding, not permission to overwrite evidence.

```bash
git rev-parse 'HEAD^{tree}'
LC_ALL=C bash scripts/floor_digest.sh
LC_ALL=en_US.UTF-8 bash scripts/floor_digest.sh
bash scripts/check_floor_freshness.sh
```

**Expected baseline:** C and en_US digests differ on unchanged files; current no-extras log is complete but uncommitted. The old `/tmp/pq-floor-venv` may be absent. No full baseline suite is needed before the small focused fixes.

## Phase 2 - Make floor evidence deterministic and fail closed

**Files:** patch `scripts/floor_digest.sh`, `scripts/check_floor_freshness.sh`, `tests/test_end_to_end.py`. Patch the producer only if a new test establishes a producer defect. Reuse `_freshness_repo`, `_freshness`, `_fixture_digest`, `_write_log`, `_git`, `_git_env`.

**Interfaces:** `floor_digest.sh [--files]` retains 0/success, 2/usage, 64/untracked scope, 65/empty scope. `check_floor_freshness.sh` retains 0/valid, 65/missing tracked log, 66/ambiguous, 67/stale or invalid evidence. Success stdout is exactly the selected path plus newline; diagnostics use stderr.

### 2A. Canonical digest

- [ ] Add `test_the_digest_is_independent_of_locale`. Put representative mixed-case, punctuation and non-ASCII paths into a miniature repo, all inside `src/`; commit the fixture. Run the **real copied helper** in C and an available installed UTF-8 locale. Assert both runs succeed and their digests match. Also assert content and path changes alter the digest, while caller locale changes do not. Choose names whose order actually differs under the two locale sorts; record this precondition so a vacuous fixture cannot pass.
- [ ] Run `.venv/bin/python -m pytest tests/test_end_to_end.py -k 'digest_is_independent_of_locale' -q`. Require the intended digest-inequality failure on the baseline. If the host has only C collation, record the portability test as blocked there and execute the differing-locale case on macOS; do not count a skip as proof.
- [ ] Make the smallest fix inside the helper, directly after its shell options:

```bash
# Content identity must not depend on the caller's collation rules.
export LC_ALL=C
```

- [ ] Rerun the regression plus existing digest tests. Run the helper from the real checkout under C and en_US; they must now match. Do not hand-replace the old log's digest. This producer change intentionally requires a fresh floor run later.

### 2B. A current digest must not certify a failed run

The current consumer accepts a one-line digest-only fixture. The producer can write a failed pytest run and return its failure, but a later freshness check ignores that recorded outcome. Make this a behavioral contract, not another prose check.

- [ ] Upgrade `_write_log` to write a **minimal valid producer-format log**, not just a digest. Include the exact producer header, the exact four-absent-extras marker, a current canonical digest, and terminal `FLOOR EXIT: 0`. Keep the fixture narrowly about the consumer contract; no test pins the real suite's count.
- [ ] Parameterize `test_current_digest_does_not_certify_invalid_floor_evidence` with: digest only; missing/truncated footer; exit 1 through 5; duplicated/conflicting footer; missing/wrong absent-extras marker; duplicate digest or marker fields. Each case uses a correct current digest and must return 67. Also retain missing/ambiguous/stale/stray-log behavior.
- [ ] Use a concrete regression shaped like this (replace the helper's fixture body as described above):

```python
@pytest.mark.parametrize("exit_code", [1, 2, 3, 4, 5])
def test_a_failed_floor_run_is_not_fresh(tmp_path: Path, exit_code: int) -> None:
    repo = _freshness_repo(tmp_path / "repo")
    log = _write_log(repo, "floor-venv-suite-2026-01-01.log", _fixture_digest(repo))
    text = log.read_text(encoding="utf-8")
    assert text.count("FLOOR EXIT: 0") == 1
    log.write_text(text.replace("FLOOR EXIT: 0", f"FLOOR EXIT: {exit_code}"), encoding="utf-8")
    result = _freshness(repo)
    assert result.returncode == 67, result.stdout + result.stderr
```

- [ ] Run the new cases red before changing the consumer. Then add a small validation function to the existing consumer: require exactly one canonical digest field, exactly one known absent-extras marker, and exactly one anchored terminal `FLOOR EXIT: 0`. Reject missing, duplicate, malformed or nonzero fields. Do not duplicate the producer's pytest-summary and skip-accounting parser in the consumer. Use fixed-string/full-line checks for markers and anchored parsing for numbers. Keep all malformed paths mapped to 67; no unchecked grep failure may escape with a different status under `set -e`.
- [ ] Rerun `.venv/bin/python -m pytest tests/test_end_to_end.py -k 'freshness or digest or floor' -q`; positive-control the locale pin and failed-log guard individually in fixture copies. Do not claim this artifact parser proves a test actually ran: the producing command's receipt is still required.

**Success:** Identical bytes have one digest under both collations. A header-only or failed current-digest log cannot pass. Valid consumer-contract fixtures pass. Actual writer/reader integration is independently completed in Phase 5A. Existing tracked-log resolution and refusal codes remain intact.

## Phase 3 - Verify and repair the review tree itself

**Files:** `evidence/run-opus-gate.sh`, create `scripts/materialize_review_tree.sh` and `tests/test_review_materialization.py`. Create a helper because the existing wrapper hardcodes the live repo and its cleanup sweep; unit tests must not invoke that sweep against real worktrees.

**Interfaces:** helper runs only inside a disposable review checkout, takes one target commit argument, leaves the checkout's HEAD at BASE and index/worktree at target, returns 0 only after parity checks, and uses exit 3 for failed construction. It does not contact Claude, stub files, remove unrelated worktrees, or change the main checkout.

- [ ] Reproduce the round-60 recommendation's remaining issue in a miniature repo: BASE tracks `Foo.md`, target tracks `foo.md`. Git object paths are case-sensitive even on a case-insensitive filesystem, so `git cat-file -e "$target:Foo.md"` does not protect the new file. Mark this as confirmed only when the fixture reproduces it.
- [ ] Build fixtures for ordinary delete, ordinary rename, case-only rename, nested path, non-ASCII name, and replacement content. On a case-sensitive machine, run all applicable cases and explicitly leave the case-insensitive filesystem case pending for macOS. The materialization tests inspect actual files and staged diff; no grep-only test.
- [ ] Prove this native sequence in the disposable fixture before using it in production:

```bash
# cwd is the new disposable checkout at BASE; TARGET was resolved to a commit.
git read-tree --reset -u "$TARGET"
git diff --exit-code
# Compare `git diff --cached` here to the expected BASE..TARGET diff.
```

- [ ] Put the successful native materialization in the helper with checked statuses. Verify HEAD still equals BASE, the index tree equals TARGET's tree, every tracked target path has its expected bytes, and no BASE-only/untracked path remains. Fail closed if any measurement fails. Do not use case-folding heuristics to decide which files to delete.
- [ ] Replace only the wrapper's `read-tree` / `checkout-index` / bespoke removal sequence with a checked invocation of this helper. Call the helper from the main script directory, since BASE may predate the helper. Keep stubbing after parity checks and preserve current review exit propagation and output provenance.
- [ ] Run `.venv/bin/python -m pytest tests/test_review_materialization.py -q` and `bash -n scripts/materialize_review_tree.sh evidence/run-opus-gate.sh`. Positive-control by restoring the defective order in a fixture copy; the case-only rename test must fail on the relevant filesystem.

**Success:** The reviewer sees target bytes/paths while the staged diff is still BASE..TARGET. A broken materialization aborts before a model call. The test harness never invokes the live stale-worktree cleanup sweep. If native Git behavior differs on an installed platform, stop this task for planner diagnosis rather than inventing a second deletion heuristic.

## Phase 4 - Close the actual product requirements and public-copy gaps

**Files:** existing product tests and docs listed in the ownership table; `.planning/ACCEPTANCE-2026-09-14.md`; separate portfolio card. Product source is changed only for a reproduced acceptance failure.

- [ ] Create an acceptance table with columns requirement, current test/workflow, command, expected observable, actual artifact, status. Start with the rows in RECOVERY.md and all seven original PRD acceptance criteria. Use `VERIFIED`, `FAILED`, `BLOCKED`, or `DEFERRED`; never a bare checkmark for unexecuted work.
- [ ] Reuse these regression groups; run once after focused changes, then rely on the final full suite instead of repeating entire groups without cause:

```bash
.venv/bin/python -m pytest tests/test_export.py tests/test_cli_verbs.py tests/test_container_workflow.py tests/test_container_backup_safety.py tests/test_odata_pagination.py tests/test_sql_navigation.py tests/test_sql_options.py tests/test_sql_credentials.py tests/test_no_silent_handlers.py -q
```

- [ ] Exercise the real workbook loop using copies in scratch. Preserve and compare original SHA-256 values before/after. These are the actual CLI forms:

```bash
.venv/bin/pq list .samples/Chapter06Sample1.xlsx
.venv/bin/pq show .samples/Chapter06Sample1.xlsx --member BaseData
.venv/bin/pq explain Table.FuzzyNestedJoin
.venv/bin/pq eval .samples/Chapter06Sample1.xlsx --member BaseData --bind Source=.samples/BrilliantBritishCars.xlsx --format csv
.venv/bin/pq eval .samples/Chapter06Sample1.xlsx --member BaseData --bind Source=.samples/BrilliantBritishCars.xlsx --to parquet --out "$PQ_SCRATCH/workbook.parquet"
```

Define `PQ_SCRATCH` from Phase 1's recorded directory first. Save source and CSV to scratch files. Compare exported table row count, ordered columns and representative values against the same evaluation result; do not assert an old 457-row number without inspecting the current input. Read parquet back with pyarrow and assert values/dtypes. Use `pqtools.open(...).source("BaseData")` to assert the handle and CLI show the same expression. For `.eval`, supply `bindings` using the existing Excel source loader behavior from `_load_binding`; do not assume a path string is already table rows.

- [ ] Reuse `test_round_trip_pandas_preserves_every_mapped_type`, `test_round_trip_arrow_and_parquet_preserve_every_mapped_type`, and `test_a_non_utc_offset_column_keeps_its_dtype_and_its_instants` for the full type contract. Cover `null` versus NaN, nullable integers, timezone offsets, mixed/nested/DeferredTable refusals. Real-workbook success alone does not cover every M primitive.
- [ ] On a copied workbook, run format preview, safe format write, add preview/write, reopen and evaluate the added query, and diff extracted source before/after. Reuse `tests/test_container_workflow.py:124-275` and its exact `add` arguments. Assert backups preserve original bytes; refused operations leave the copy unchanged. Never write to `.samples/` originals.
- [ ] Exercise parameter overrides, clean single/batch check output, mixed good/bad batch results, bad argument counts, unsupported options, parser errors, missing files, and `pq explain` for each live diagnostic/failure code. Existing tests in `tests/test_cli_verbs.py` and `tests/test_end_to_end.py` already cover these. Inspect at least one real human-output transcript: file/location, plain-language reason, actionable next step, no traceback; separately assert JSON remains machine-readable. Do not claim human usability testing happened unless a person tried it.
- [ ] Reconcile documentation instead of reopening delivered APIs: append a current-implementation clarification for the PRD's broader `--to` proposal and scalar-only parameter wording. Replace README's unqualified "You get the same answers here" with a scope-qualified sentence, e.g. "Supported queries run locally; unsupported features are refused, and semantic equivalence with Microsoft's engine is not claimed."
- [ ] Fix the obsolete `2026-09-06` dependency-evidence link in `pyproject.toml` comments and replace "both ends of the declared range" in docs with the exact tested-version statement. An unbounded `>=` range has no verified upper end. Actual minimum-version validation is Phase 5; do not silently raise package floors to make it green.
- [ ] Read the portfolio's own instructions and dirty status before touching it. Patch only its pqtools card: preserve both links, replace stale function/test counts with durable capability wording (e.g. "Typed refusals" / "clear errors for unsupported features"), and remove the misleading "never a live SQL connector" statement. Run its existing `npm run lint` and `npm run build` sequentially. Inspect the rendered card in both copy modes, keyboard link focus, and narrow/wide layouts. Record the portfolio's own starting SHA, exact pqtools-card diff and dirty paths. Obtain its own exact `claude-opus-5` review for the changed customer-facing copy, resolve findings, and create a separate local checkpoint after the relevant security check. Record its candidate SHA, reviewed diff and build/visual artifacts in the state file. A pqtools SHA/review does not cover this repository. If website review or visualization is blocked, preserve the patch and label that workstream BLOCKED. No deploy in this phase.

**Success:** Every requested user workflow has a concrete result or named boundary, the original API changes are not rebuilt, documentation matches the support matrix, and local website copy/links are reviewable. No claims of live native refresh, live SQL service validation, public SEO indexing or agent adoption without their own evidence.

## Phase 5 - Test the environments, freeze the code, and generate fresh evidence

This phase intentionally orders work to prevent another stale-artifact loop. Finish source/test/producer/CI changes before the final floor run. Short focused tests precede expensive checks; final full verification follows review corrections.

### 5A. Environment and CI coverage

- [ ] Verify `.venv/bin/python`, Node, npm, pytest, Ruff and mypy versions using their version commands. Confirm extras are installed in dev. Recreate a dedicated floor venv in scratch using Python >=3.11:

```bash
python3 -m venv "$PQ_SCRATCH/floor-venv"
"$PQ_SCRATCH/floor-venv/bin/python" -m pip install -e . pytest
"$PQ_SCRATCH/floor-venv/bin/python" -c 'import importlib.util as u; names=("pandas","pyarrow","openpyxl","python_calamine"); assert all(u.find_spec(n) is None for n in names)'
```

Use a resolved interpreter path. Do not use `--system-site-packages` or an import hook that pretends installed modules are absent. Missing network/wheels is `FLOOR_ENV_BLOCKED`, with the exact failed install command. Do not uninstall extras from the dev venv.

- [ ] Add and run actual producer/consumer integration tests using this floor interpreter. Invoke the real producer in a miniature tracked repo with one passing test and one extras-dependent skipped test; its actual output must pass the consumer. In a second case, introduce one failing test: the producer must return nonzero and the consumer must reject its output. These are new Phase 5 tests, not an unfinished Phase 2 checkbox. Keep collection and source import paths tied to each fixture; an editable install pointing at the wrong checkout cannot validate the fixture. The consumer does not authenticate logs: retain the actual producer receipt and never accept a fabricated zero footer as proof of execution.
- [ ] In a second scratch venv created with a verified Python 3.11 interpreter, validate the declared minimum versions, starting from `pandas==2.0.0`, `pyarrow==14.0.0` and `numpy<2`, plus the test dependencies needed by the export group. This is a validation target derived from `pyproject.toml`, not a claim those versions were tested today. Record resolved versions. Run export/round-trip tests and the non-UTC regression. A genuine failure is a dependency-contract decision for planner review: fix compatibility or explicitly propose a justified floor change, never hide it by testing only 2.3.x. Test current dev versions separately and report them as a second measured point, not all future versions.
- [ ] Patch the Linux CI path and the release build job to exercise a real no-extras run. Keep existing OS/Python matrix, dev-suite, coverage, bundle, lint/type/security and publishing guards. After dev dependencies are installed, create a separate floor venv, install `-e . pytest`, run the producer with explicit floor AND dev interpreter paths, then invoke the strengthened consumer. The workflow may regenerate the existing tracked evidence path in its ephemeral checkout; do not commit or upload source changes from CI.
- [ ] Upload completed run logs as CI artifacts using the already-pinned upload-artifact action from the release workflow. Preserve failure outputs too, labeled as failures. Ensure the producer failure reaches the job exit; do not pipe through an unchecked `tee`, add `continue-on-error`, or skip missing evidence. Linux jobs enforce the floor; macOS must exercise the case-insensitive and differing-locale regressions. Validate workflow syntax with an available YAML/workflow checker, and inspect remote job results later only after an approved push.

### 5B. Pre-freeze validation, candidate review and freeze

- [ ] Before freezing, run `.venv/bin/python scripts/sync_builtin_list.py` and inspect all generated documentation changes. Include any legitimate generated changes in the candidate. Run one intact sequential dev suite with `.venv/bin/python -m pytest -q --cov=pqtools --cov-report=term-missing --cov-fail-under=80`, recording full output. This is the pre-review full test result; the final aggregate release gate later verifies the evidence-bearing candidate.
- [ ] Run JavaScript and package checks sequentially:

```bash
npm test
npm run bundle
git diff --exit-code -- src/pqtools/_bridge.cjs
.venv/bin/python -m build --outdir "$PQ_SCRATCH/dist"
.venv/bin/python -m twine check "$PQ_SCRATCH"/dist/*
```

A bundle mismatch is a real candidate change; inspect it, do not overwrite/revert to silence the check. Build into an empty task-specific dist directory so old wheels cannot be mistaken for the candidate.
- [ ] Install the exact newly built wheel into a fresh scratch venv. Run import, `pq --help`, evaluation of `1 + 1`, typed refusal for an unsupported name, and base-install absence of pandas/Arrow. Run outside the repo directory and print `pqtools.__file__` so the editable source tree cannot satisfy the wheel test. Install the same wheel with export extras in another venv and repeat a parquet round trip. Verify vendored bridge, notices and `py.typed` are in the wheel; inspect sdist contents for accidental `.samples` or private files.
- [ ] Run the security-auditor skill plus explicit missing coverage: secret scan over relevant tracked code/history scope, SAST, Python dependency audit and npm dependency audit. Use `bash "$HOME/.codex/skills/security-auditor/bin/audit.sh"`, `.venv/bin/python -m pip_audit --progress-spinner off`, and `npm audit`; use the repository Semgrep rules with `semgrep scan --metrics=off --disable-version-check --error --config semgrep-rules.yaml src tests js`. Report exact rules and scope, including that a tiny custom ruleset is bounded coverage. No required scanner may be called clean if skipped/unavailable; name the missing scanner and user-level remedy. No secrets or unresolved HIGH/CRITICAL vulnerabilities may remain.

- [ ] Resolve every failure or generated source/bundle/documentation diff from the checks above before the code-review/freeze steps below. Finish all new tests from 5A here. No mutation-capable preparation remains after freeze; later confirmation must produce zero source/manifest/test diff.

- [ ] Run focused regressions, Ruff, format checks, strict mypy, and the applicable security scan before local code checkpoints. Stage named paths only. Keep commits focused; do not commit a stale floor log beside code and call it final evidence.
- [ ] If the original modified floor log is still uncommitted, first preserve it in a separate local evidence-only checkpoint labeled historical, after inspecting it and running the relevant staged secret check. This preserves Claude's work and makes a clean candidate possible; it is not release evidence for the new code. Then commit/freeze the code, tests, producer/consumer, review helper and CI changes. A locally committed candidate is allowed to have stale floor evidence while explicitly marked NOT RELEASE READY. Preserve the original dirty floor log snapshot in scratch. Set a recorded `PQ_REVIEW_BASE=abb92ca` and resolve `PQ_CODE_HEAD` from Git. This range covers the previously unreviewed round-60 fixes plus the new closeout work. If splitting a large range for review, record exhaustive file/range coverage; never drop a difficult file to fit a prompt.
- [ ] Run the exact Claude skill on that frozen range, preferably directly to avoid the old wrapper's historical hazards:

```bash
CLAUDE_REVIEW_MODEL=claude-opus-5 bash "$HOME/.codex/skills/claude-review/bin/review.sh" "$PQ_REVIEW_BASE..$PQ_CODE_HEAD" > "$PQ_SCRATCH/code-review.txt" 2>&1
```

Capture the command's actual exit status and inspect the whole receipt. `0` without a substantive nonempty review/standalone SHIP is not enough. Review CRITICAL/HIGH findings, reproduce before fixing, resolve and re-review; disposition every lower-severity finding on evidence. Use the repaired wrapper only if its exact-tree behavior has passed and its full scope is required. Never change the reviewer model. Keep credentials private; the reviewed skill already unsets the API-key variable for its invocation.

- [ ] When code is reviewed and clean, include planned checkpoint documents in the local checkpoint and stop modifying files while the floor run is active, including state notes. Record progress after the run; changing the uncommitted-file count during it can correctly trigger a refusal. Stop modifying files in the floor digest scope. Record SHA and source/producer digest. Ensure no autocommit or other writer can change the tree during the run; if it changes, preserve the failed receipt and rerun after stabilization.

### 5C. Floor run and final full verification

- [ ] Run the producer once on the frozen candidate, writing first to scratch so an interrupted run cannot overwrite the current receipt:

```bash
bash scripts/floor_venv_run.sh "$PQ_SCRATCH/floor-venv/bin/python" "$PQ_SCRATCH/floor-final.log" "$PWD/.venv/bin/python" > "$PQ_SCRATCH/floor-producer.txt" 2>&1
```

Wait for completion with tools that retain the job ID; do not infer completion from a stopped-task notification. Inspect producer exit, body summary, exact absent-extras check, skip reasons/counts, start/end tree identity, digest, and `FLOOR EXIT: 0`. Compare import paths to this checkout. Any failure keeps the old artifact untouched.

- [ ] Copy the completed log to the single tracked `evidence/floor-venv-suite-2026-09-10.log`. Run the strengthened checker under both C and an installed differing-collation locale. Commit only the completed evidence and necessary final state/docs; no source/test/producer changes in this checkpoint. Record this as `PQ_FINAL_HEAD`.
- [ ] Run one intact full dev suite through the existing release gate, forcing sequential execution and retaining coverage:

```bash
PQ_GATE_PYTEST_ARGS='--cov=pqtools --cov-report=term-missing --cov-fail-under=80' bash scripts/release_gate.sh > "$PQ_SCRATCH/release-gate.txt" 2>&1
```

The environment variable is deliberately supplied to the Bash script; no `-n` flag is present. This gate has **9 numbered steps**, not the historical PRD's eight; count PASS/FAIL/SKIP separately. Require exit 0, `GATE PASSED`, no failures, and actual execution of the real-workbook step. Read and preserve `/tmp/pq-gate-tests.log`, catalog/docs logs and gate output before another run overwrites them. The gate syncs README/llms.txt, already prepared in 5B, so final confirmation must produce zero diff. If it rewrites anything, stop 5C and return to 5B: reconcile the candidate, review it, freeze again and regenerate floor evidence before another final gate. Do not continue to Phase 6 with the changed candidate. Do not run a bundle rebuild or any source-mutating preparation after freeze.

**Success:** Current code has a complete dev suite, real no-extras suite with reconciled skips, current canonical evidence, minimum-version result, JavaScript/build/install checks and security receipts. Test counts are measured dynamically; old 4187/4146 numbers are not assertions about changed code. Public CI remains unverified until it actually runs.

## Phase 6 - Final independent review and closeout

**Files:** `.planning/ACCEPTANCE-2026-09-14.md`, `.planning/CLOSEOUT-STATE-2026-09-14.md`, completed review/evidence receipts as narrowly needed. Keep raw in-progress logs outside `evidence/`.

- [ ] Obtain exact `claude-opus-5` review of the final changes not already covered, including the new floor artifact and its relationship to the frozen code. Record base/head SHAs, diff scope, requested model, receipt path, exit status and standalone verdict. Previous code review plus a precisely scoped final delta can form a complete coverage chain; a historical round number cannot.
- [ ] Resolve genuine CRITICAL/HIGH issues and obtain a clean re-review. If a source, test, manifest or producer file changes, return to the relevant task and regenerate floor evidence after freezing again. If only non-scope prose changes, keep valid source evidence, review the actual delta, and rerun only checks affected by it. Do not manufacture an endless full-suite loop for unchanged code.
- [ ] Have the planner lane check the acceptance table against the original PRD and RECOVERY.md. Verify all requirements have evidence or an explicit supported boundary. Verify reviews cover the latest implementation, security scope is stated, all phases are accounted for, and no old artifact is quoted as current.
- [ ] Record final `git status --short`, HEAD, scoped digest, floor-log hash and completed receipt hashes. Re-run the cheap freshness/identity checks. If HEAD differs from the full-gate receipt only because of reviewed evidence/documentation commits outside runtime scope, state this explicitly and verify scope equality; do not falsely call the old log a run on the new SHA. If runtime scope differs, rerun the full gate.
- [ ] Write a short closeout with VERIFIED/PARTIAL/BLOCKED/DEFERRED per workstream. Include an exact next command for any blocker. Preserve the old closeout and its corrections rather than erasing the history. Do not update global memory as part of this task.

**Success:** Local completion is review-backed and the evidence identifies the bytes that were tested. `REVIEW_BLOCKED`, `RESOURCE_OR_CONCURRENT_WRITER_BLOCKED`, `FLOOR_ENV_BLOCKED`, `MIN_VERSION_BLOCKED`, `SECURITY_BLOCKED`, or `NATIVE_VALIDATION_UNVERIFIED` remain explicit where applicable. A blocker does not justify substituting a model, weakening a test, or inventing a pass.

## Phase 7 - Prepare the publication handoff; G controls publication

**File:** `.planning/RELEASE-HANDOFF-2026-09-14.md`; separate website build/preview receipt. This phase prepares reviewable actions, not automatic release commands.

- [ ] Query remote Git/CI and PyPI state read-only at handoff time; record observed SHA/version/date. Local tracking refs are not proof of current remote state. Compare intended package version with published versions and tags before proposing the next version. Never overwrite/reuse an existing immutable release.
- [ ] Inspect `ops/publish-*.sh` before citing any command: these are historical and may assume master, old names or a different branch. Do not blindly execute them. Prefer a release instruction that identifies the exact approved candidate, version, target branch, tag and workflow.
- [ ] Prepare the branch/PR summary with problem, behavior, tests, review receipts and known limits. Push/PR creation happens only after G's explicit approval; run the existing pre-push security gate without bypass when that approval arrives. Preserve changes from the starting dirty checkout.
- [ ] Require external CI on the exact release candidate, including the existing OS/Python matrix and the new no-extras path. A feature-branch push alone does not trigger this repo's branch-only CI; the current workflow uses main/master pushes and pull requests. Report absent CI rather than assuming it ran.
- [ ] Explain that pushing a `v*` tag triggers `.github/workflows/release.yml` and can publish to PyPI. Obtain G's explicit approval for that concrete version/tag after CI/review/security are green. If a version bump changes the manifest, treat it as a new candidate: focused validation/review, fresh floor digest/run and final gate precede tagging.
- [ ] After G publishes, verify the release workflow's successful publish job and install that exact PyPI version into a clean venv. Confirm import location, version, CLI smoke and rendered README. Package upload success alone is not installed-package verification. Do not perform this until publication is authorized.
- [ ] For the website, preserve a separate local patch/build/visual receipt. Verify deployment tooling/provider from the site's actual configuration before proposing a command; do not invent a Cloudflare project name. G separately approves the concrete website deployment. After approval, inspect the visible public pqtools card and both destinations, not just an HTTP status or URL. Do not promise indexing, SEO ranking or agent adoption.
- [ ] If either public action remains unapproved, finish with `LOCAL_READY / PUBLICATION_PENDING` and list exactly which artifact/action is ready. Do not claim everything is deployed.

**Success:** G can approve a specific candidate and publication action without another investigation. Package, website, external CI and live native integrations retain their own truthful statuses.

## Handoff and interruption rules

Start at the first unchecked task, not at the September 5 audit or round-15 handoff. Recheck the recorded candidate identity before resuming. A failure gets one hypothesis, the smallest fix and the exact failing command; repeated failures on the same mechanism return to planner diagnosis. Never trade away the contract to get green.

Before ending an execution session, update state with the next command and report:

```text
STATUS:   DONE | DONE_WITH_CONCERNS | BLOCKED | AWAITING_APPROVAL
CHANGED:  named files / commits
TESTS:    actual commands, outcomes, artifact paths
GATE:     approved plan; exact Claude review status; security status
LESSON:   none unless explicitly recorded under authorized project policy
NEXT:     one command or one concrete G action
```

This plan itself is complete when its steps are coherent and reviewed. Its unchecked boxes deliberately remain unchecked until soark llm executes them.
