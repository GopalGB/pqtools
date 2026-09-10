#!/usr/bin/env bash
# The checks that actually caught things, in the order they caught them.
#
# Every bug found in 0.9.0 and 0.10.0 development passed the test suite. The
# suite is necessary and it is not sufficient, because it was written by the
# people who wrote the code, using the idioms those people reach for. `#table`,
# `#(lf)`, and `Source{[Item=...]}` all shipped broken behind a thousand green
# tests, and every one of them was found by running real Power Query M.
#
# So this gate runs the suite AND the things the suite structurally cannot see:
# the documented workflow against a real artifact, and the registry against
# Microsoft's own reference.
#
# Usage:  bash scripts/release_gate.sh
# Exit:   0 = shippable, non-zero = do not ship.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

PY=.venv/bin/python
FAILED=0

# Provenance header, in its own script so a test can EXECUTE it rather than
# grep it. See scripts/gate_provenance.sh for why that distinction mattered.
#
# Status-checked. Unchecked, a rename or a missing file sent the error to
# stderr and the gate ran on to "GATE PASSED", exit 0, with a header-less log -
# an unprovenanced pass that reads exactly like a provenanced one. The header
# is the part of this log that says WHICH tree passed; without it the rest is
# not evidence.
bash scripts/gate_provenance.sh "$PY" || {
  printf 'FATAL: provenance header failed (%s) - refusing to gate an\n' "$?"
  printf '       unidentifiable tree.\n'
  exit 2
}

step() { printf '\n=== %s ===\n' "$1"; }
check() {
  if [ "$1" -eq 0 ]; then printf '  PASS  %s\n' "$2"
  else printf '  FAIL  %s\n' "$2"; FAILED=1; fi
}

step "1. Import integrity"
# Catches a module registering one M name twice - the failure that silently
# replaced Table.Range with a worse copy that had no bounds check.
$PY -c "from pqtools.evaluate import BUILTINS; print(f'  {len(BUILTINS)} builtins registered')"
check $? "registry imports and merges cleanly"

step "2. Test suite"
# -n auto by default. On a memory-constrained machine it spawns one worker per
# core, each loading the full registry, and the run dies in swap rather than in
# a test - so PQ_GATE_PYTEST_ARGS="" runs it sequentially instead.
# shellcheck disable=SC2086
# Unquoted default on purpose: "-n auto" inside the braces expands as ONE
# word and pytest rejects it ("invalid parse_numprocesses value: ' auto'").
$PY -m pytest -q ${PQ_GATE_PYTEST_ARGS--n auto} >/tmp/pq-gate-tests.log 2>&1
check $? "full suite (detail: /tmp/pq-gate-tests.log)"
tail -1 /tmp/pq-gate-tests.log

step "3. Lint, format, types"
.venv/bin/ruff check src tests scripts >/dev/null 2>&1; check $? "ruff check"
.venv/bin/ruff format --check src tests scripts >/dev/null 2>&1; check $? "ruff format"
.venv/bin/mypy >/dev/null 2>&1; check $? "mypy strict"

step "4. Documented coverage is computed, not remembered"
# llms.txt spent two releases telling AI assistants that connectors shipped in
# 0.8.0 were unsupported, because a human updated the prose and forgot.
# Compare the files against themselves across the sync, NOT against HEAD.
# `git diff` also reports every unrelated uncommitted edit, so a hand-written
# paragraph in the same file failed this step and named the wrong cause.
cp README.md /tmp/pq-gate-readme.before
cp llms.txt /tmp/pq-gate-llms.before
$PY scripts/sync_builtin_list.py >/tmp/pq-gate-sync.log 2>&1
check $? "sync_builtin_list.py runs"
if cmp -s README.md /tmp/pq-gate-readme.before && cmp -s llms.txt /tmp/pq-gate-llms.before; then
  check 0 "README/llms.txt coverage already current"
else
  check 1 "README/llms.txt were STALE - the sync just rewrote them, commit it"
fi
grep -E "^coverage:" /tmp/pq-gate-sync.log || true

step "5. No invented function names"
# A function that exists here but not in Power Query means the query passes
# here and fails there. Worse than a missing one, and invisible to any test
# of the function's own behaviour.
$PY -m pytest tests/test_catalog.py -q >/tmp/pq-gate-catalog.log 2>&1
check $? "registry matches Microsoft's reference"
tail -1 /tmp/pq-gate-catalog.log

step "6. Positive control on the refusal machinery"
# A gate that cannot fail proves nothing. Salesforce.Data must produce a real
# explanation, and a typo must still read as a typo.
$PY - <<'EOF'
import sys
from pqtools import evaluate
from pqtools.core import MQueryError

def message(source):
    try:
        evaluate(source)
    except MQueryError as error:
        return str(error)
    return ""

ok = True
if "connector" not in message("Salesforce.Data()"):
    print("  documented function did not explain itself"); ok = False
if "unknown identifier" not in message("Tabel.RowCount(1)"):
    print("  a typo stopped reading as a typo"); ok = False
sys.exit(0 if ok else 1)
EOF
check $? "documented-vs-typo distinction holds"

step "7. Real Power Query M, end to end"
# The check that found every bug the suite missed. .samples/ is gitignored, so
# skip rather than fail when it is absent - but say so, because a skipped
# check reporting green is how this class of bug survives.
if [ -f .samples/Chapter06Sample1.xlsx ] && [ -f .samples/BrilliantBritishCars.xlsx ]; then
  # Written to a file, not piped into `head`. Under `set -o pipefail` the
  # early close made pq exit 120 and the whole pipeline report failure while
  # grep had already matched - this step failed on healthy code, which is the
  # fastest way to teach someone to ignore the one check that has actually
  # caught things.
  .venv/bin/pq eval .samples/Chapter06Sample1.xlsx --member BaseData \
    --bind "Source=.samples/BrilliantBritishCars.xlsx" --format csv \
    >/tmp/pq-gate-workbook.csv 2>&1
  eval_status=$?
  head -1 /tmp/pq-gate-workbook.csv | grep -q "InvoiceDate,Make"
  header_status=$?
  check $(( eval_status | header_status )) \
    "unmodified workbook-authored query runs and types correctly"
  [ "$eval_status" -eq 0 ] || head -3 /tmp/pq-gate-workbook.csv
else
  printf '  SKIP  no .samples/ workbooks present - THIS CHECK DID NOT RUN\n'
fi

step "8. Microsoft's own worked examples"
# The corpus nobody here authored. 784 examples harvested from the M
# reference: none may hit an unknown identifier or a parse error, and every
# printed Output must reproduce exactly. This is the check that finally has
# teeth against the failure the other seven cannot see - a suite written by
# the authors of the code, in the idioms those authors reach for.
$PY -m pytest tests/test_doc_examples.py -q >/tmp/pq-gate-docs.log 2>&1
check $? "documented examples run and reproduce their output"
tail -1 /tmp/pq-gate-docs.log

step "9. The PRD 6.2 floor evidence describes THIS tree"
# The floor log records a digest of the tree it ran on. If the tree has moved
# since, the log certifies something that is no longer being shipped - which
# is the defect rounds 52-57 kept finding by hand, in six different disguises.
#
# This lives in the GATE and not in the test suite on purpose. The suite runs
# INSIDE the floor run, where the log on disk is still the previous one, so a
# pytest assertion here could never go green: the log can only be regenerated
# by a run that the assertion itself would fail. The gate is where "is this
# evidence current" belongs anyway - a stale artifact should block shipping,
# not block editing.
FLOOR_LOG=evidence/floor-venv-suite-2026-09-09.log
if [ -f "$FLOOR_LOG" ]; then
  recorded=$(sed -n 's/^#   tree digest *: *//p' "$FLOOR_LOG" | head -1)
  actual=$(git ls-files -zco --exclude-standard src tests scripts pyproject.toml \
    | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1)
  if [ -n "$recorded" ] && [ "$recorded" = "$actual" ]; then
    check 0 "floor log's digest matches the working tree"
  else
    check 1 "floor log's digest matches the working tree"
    printf '    recorded: %s\n    actual  : %s\n' "${recorded:-<none found>}" "$actual"
    printf '    regenerate: scripts/floor_venv_run.sh <floor-venv-python> %s\n' "$FLOOR_LOG"
  fi
else
  printf '  SKIP  no floor log present - THIS CHECK DID NOT RUN\n'
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'GATE PASSED\n'; else printf 'GATE FAILED\n'; fi
exit "$FAILED"
