#!/usr/bin/env bash
# OpenCodeReview gate for mquery-toolkit, reproducible.
# Usage: bash run-ocr-gate.sh <base-sha> <head-sha> <out-file> [gateway-model]
# Defaults to the backend-smart lane (gpt-oss-120b on Groq with Cerebras fallover) because the
# fb-groq lane returned 27x HTTP 429 in 25 minutes on 2026-09-02 and produced nothing.
# Hard-bounded with --kill-after so a hung run cannot outlive its budget (memory: exit 124 != bounded).
set -uo pipefail
BASE="$1"; HEAD="$2"; OUT="$3"; MODEL="${4:-backend-smart}"
REPO="/Users/gopalmacbook/Desktop/Max HQ/pqtools"
cd "$REPO" || exit 1
{
  echo "# OpenCodeReview ($(ocr --version 2>&1 | head -1)) - range $BASE..$HEAD, provider max-gateway model $MODEL"
  echo "# excludes: vendored bundle, npm lockfile, corpus fixtures | invoked $(date -u +%FT%TZ)"
  echo
  timeout --kill-after=30 1500 ocr review --from "$BASE" --to "$HEAD" \
    --provider max-gateway --model "$MODEL" \
    --exclude '**/_bridge.cjs,**/package-lock.json,**/fixtures/**' \
    --background "Unofficial offline Python CLI + library for Power Query M source. Parsing/formatting delegated to pinned Microsoft npm packages through a vendored Node bridge (excluded from this diff, committed and reproducible). Core must stay offline and credential-free; file writes are dry-run by default and atomic with --write; Fabric/PQTest adapters are optional and mocked. Focus on correctness bugs and security, not style."
  RC=$?
  echo
  echo "# ocr exit: $RC (124/137 = timed out)"
} > "$OUT" 2>&1
tail -5 "$OUT"
