# pqtools closeout acceptance — 2026-09-14 handoff

Candidate branch: `feat/connectors-and-real-m-0.9.0`  
Latest local checkpoint: `2082e19`

| Requirement | Evidence | Status |
|---|---|---|
| Locale-independent floor digest | `pytest tests/test_end_to_end.py -k digest_is_independent_of_locale`; C/en_US checker receipts | VERIFIED |
| Strict floor evidence parser | `pytest tests/test_end_to_end.py -k 'freshness or digest or floor'`; both locale checker runs | VERIFIED |
| Review-tree materialization | `pytest tests/test_review_materialization.py -q` (6 passed, 1 skipped) | VERIFIED |
| Product regression surface | grouped connector/export/CLI suite (207 passed) | VERIFIED |
| Real workbook acceptance | list/show/explain/eval, CSV and parquet export; source/copy SHA-256 matched | VERIFIED |
| Full dev suite | `full-dev-suite-01.txt`: 4207 passed, 1 skipped, coverage 92.78% | VERIFIED (receipt) |
| No-extras floor | prior fresh run: 4166 passed, 42 skipped; strict checker passed in C and en_US.UTF-8 | VERIFIED WITH PROVENANCE CONCERN |
| Clean committed floor rerun | `floor-final-clean.log`: 3260 passed, 33 skipped, interrupted (`FLOOR EXIT: 2`) | BLOCKED |
| Type/lint/format | mypy strict passed; ruff check and format check pass at `2082e19` | VERIFIED |
| Minimum versions | `min-venv-tests.txt`: 3 passed on pandas 2.0.0 / pyarrow 14.0.0 | VERIFIED |
| Packaging | `python -m build`, `twine check`, base install and extras install smoke passed | VERIFIED |
| Security | pip-audit/npm audit/explicit semgrep clean; gitleaks and broad security-auditor report historical findings | SECURITY_BLOCKED |
| Exact Claude review | `review-final.txt`, model `claude-opus-5`, `FIX-FIRST`; critical provenance finding reproduced | BLOCKED UNTIL CLEAN FLOOR |
| Release gate | `final-release-gate.txt`: interrupted full suite, then stale floor evidence; no `GATE PASSED` | BLOCKED |
| Portfolio copy | portfolio `npm run lint` and `npm run build` passed locally | VERIFIED; deployment deferred |

The committed plan deliberately leaves dependency-graph/Fabric/PQTest/TMDL/folding/Mashup behavior, live Microsoft-service validation, external CI, website deployment, PyPI publication, and public PR/tag actions outside local acceptance.

