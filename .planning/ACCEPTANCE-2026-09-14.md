# pqtools closeout acceptance: 2026-09-14 handoff

Candidate branch: `feat/connectors-and-real-m-0.9.0`  
Latest local checkpoint: `3aff833` (evidence commit: `f78766d`)

| Requirement | Evidence | Status |
|---|---|---|
| Locale-independent floor digest | `pytest tests/test_end_to_end.py -k digest_is_independent_of_locale`; C/en_US checker receipts | VERIFIED |
| Strict floor evidence parser | `pytest tests/test_end_to_end.py -k 'freshness or digest or floor'`; both locale checker runs | VERIFIED |
| Review-tree materialization | `pytest tests/test_review_materialization.py -q` (6 passed, 1 skipped) | VERIFIED |
| Product regression surface | grouped connector/export/CLI suite (207 passed) | VERIFIED |
| Real workbook acceptance | list/show/explain/eval, CSV and parquet export; source/copy SHA-256 matched | VERIFIED |
| Full dev suite | `full-dev-suite-01.txt`: 4207 passed, 1 skipped, coverage 92.78% | VERIFIED (receipt) |
| No-extras floor | `f78766d`: 4166 passed, 42 skipped; freshness passed at final HEAD under default, C, and en_US.UTF-8 | VERIFIED |
| Clean committed floor rerun | `floor-final-20260915.log`: 4166 passed, 42 skipped, `FLOOR EXIT: 0`; committed as `f78766d` | VERIFIED |
| Type/lint/format | final release gate at `f78766d`: mypy strict, Ruff check and format all passed | VERIFIED |
| Minimum versions | `min-venv-tests.txt`: 3 passed on pandas 2.0.0 / pyarrow 14.0.0 | VERIFIED |
| Packaging | `python -m build`, `twine check`, base install and extras install smoke passed | VERIFIED |
| Security | pip-audit/npm audit/explicit semgrep clean; gitleaks and broad security-auditor report historical findings | SECURITY_BLOCKED |
| Exact Claude review | final `claude-opus-5` review returned `FIX-FIRST`; critical/ high findings closed, paperwork/security concerns remain recorded | VERIFIED WITH CONCERNS |
| Release gate | `final-release-gate-success.txt`: all 9 steps passed; 4207 passed/1 skipped; `GATE PASSED` | VERIFIED |
| Portfolio copy | portfolio `npm run lint` and `npm run build` passed locally | VERIFIED; deployment deferred |

The committed plan deliberately leaves dependency-graph/Fabric/PQTest/TMDL/folding/Mashup behavior, live Microsoft-service validation, external CI, website deployment, PyPI publication, and public PR/tag actions outside local acceptance.
