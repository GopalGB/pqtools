# Release handoff: 2026-09-14 plan

Local candidate: branch `feat/connectors-and-real-m-0.9.0`, checkpoint `f78766d`, package version `0.10.0`.

Completed local artifacts:

- Product and connector regression receipts under `/tmp/pqtools-closeout-20260914.z11kgu/`.
- Packaging distributions passed `twine check`; base and extras install smoke passed.
- Portfolio copy passed local lint and production build in the separate portfolio checkout.
- Security receipts are under `/tmp/pqtools-closeout-20260914.z11kgu/security/`.
- Exact Claude review is preserved in `review-final.txt`; it identified a provenance issue that is recorded in ACCEPTANCE.

Before any public release, run from a clean checkout of the candidate:

```bash
python -m venv /tmp/pqtools-floor-release
/tmp/pqtools-floor-release/bin/pip install -e . pytest pytest-cov pytest-xdist
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
bash scripts/floor_venv_run.sh /tmp/pqtools-floor-release/bin/python evidence/floor-venv-suite-2026-09-15.log .venv/bin/python
git rm evidence/floor-venv-suite-2026-09-10.log
git add evidence/floor-venv-suite-2026-09-15.log
git commit -m "evidence: floor run <candidate-sha>"
bash scripts/check_floor_freshness.sh
PQ_GATE_PYTEST_ARGS='-n auto --cov=pqtools --cov-report=term-missing --cov-fail-under=80' bash scripts/release_gate.sh
```

External actions remain pending and intentionally unperformed: external CI confirmation, website deployment, creating a public PR, creating tag `v0.10.0` (the release workflow publishes PyPI), and PyPI publication. Live Microsoft-service and native Mashup validation also remain deferred.
