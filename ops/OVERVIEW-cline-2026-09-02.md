# mquery-toolkit - Everything You Need To Know

> One-page master summary of what we built, where it lives, what it does, its quality state, and what's left. Updated: 2026-09-02 (evening).

## What it is

`mquery-toolkit` v0.1.0 - an unofficial, offline, credential-free Python library + CLI (`mquery`) for working with **Power Query M language source code** (the language inside Power BI / Excel / Fabric queries). It is NOT an M runtime and is NOT affiliated with or endorsed by Microsoft.

Syntax and formatting are delegated to pinned official Microsoft npm packages (`@microsoft/powerquery-parser` 2.0.0, `@microsoft/powerquery-formatter` 1.0.0) via a small Node bridge. The Python core never makes network requests and never touches credentials.

## Why it exists (the niche)

There was no good third-party tooling for programmatically working with M code: version-diffing queries, batch formatting, safe automated renames, CI checks on `.pq` files. The Phase 5 ORACLE research confirmed the gap and returned **GO** for an unofficial M source-tooling library (NO-GO for a standalone M runtime). G approved the local build.

## Where it lives

- Project repo: `/Users/gopalmacbook/Desktop/Max HQ/pqtools/` (isolated local git repo, MIT, branch `master`, commits `8326d3f` base + `9bc508c` build - nothing pushed)
- Planning + evidence: `.planning/max-log-recovery-powerquery-toolkit-2026-09-01/` (PLAN.md, STATE.md, POWERQUERY-SUPPORT-MATRIX.md, POWERQUERY-CORPUS-MANIFEST.json, TRACK-B-VALIDATION.md, evidence/)
- Research: cited ORACLE report is recorded in STATE.md Phase 5 (frozen pins: Python 3.11-3.13, Node 22.23.2, parser 2.0.0, formatter 1.0.0, 10 MiB limits, dry-run default)
- Built artifacts: `dist/mquery_toolkit-0.1.0-py3-none-any.whl` and `.tar.gz`

## What it does (features)

| Command | What it does |
|---|---|
| `mquery parse FILE` | Parse M code via Microsoft's official parser; deterministic JSON out |
| `mquery format FILE` | Pretty-print M with Microsoft's official formatter |
| `mquery check FILE` | Diagnostics; rule IDs: M_PARSE_ERROR, M001 duplicate binding, M002 dynamic Web.Contents, M003 credential-like literal, M004 unreachable binding, M005 unresolved reference, M006 source inventory (`--json` supported) |
| `mquery dependencies FILE` | Extract data-source/dependency inventory |
| `mquery rename FILE --old X --new Y` | Binding-aware rename, intentionally limited to one ordinary top-level `let` binding; refuses quoted ids, records, lambdas, non-ASCII, ambiguous shapes |
| `mquery replace-source FILE --source S` | Replace complete validated source only |

Safety model:
- All edits emit a **unified diff by default**; `--write` does an atomic replace preserving UTF-8, newline convention, final-newline, and file mode; rejects symlinks/hardlinks
- Cross-platform **advisory lock** serializes mquery writers + final snapshot check (documented limit: no portable mandatory lock exists against non-cooperating programs)
- Hostile-input limits: 10 MiB file cap, bounded bridge process (stdin timeout proven at 0.12s), oversized output terminated

Optional adapters (mocked/local-only in v1):
- **Fabric** - accepts only a caller-provided token via injected transport; rejects cross-origin/202/429 polling redirects (security fix from review)
- **PQTest** - only a user-installed Windows executable at pinned version 2.155.2; never downloads binaries

## Local runtime

- Node 22.23.2 required (Homebrew Node 22 breaks on missing `libsimdjson`; project pins a verified user-space Node at `.tools/node-v22.23.2-darwin-arm64/bin/node`). Override with `MQUERY_NODE`.
- The Microsoft bridge is vendored as `src/mquery_toolkit/_bridge.cjs` (esbuild bundle, 45k lines) so the wheel works after install.

## Quality state (verified)

- 38 pytest tests, 82.59% coverage - passed twice consecutively
- npm tests 1/1; mypy clean; Ruff clean; Semgrep (fallback rules) 0 findings
- wheel + sdist build green; Twine check green; dependency audit: no known vulnerabilities
- Corpus: 4 pinned MIT-licensed .pq fixtures, SHA-256 4/4 verified
- Independent planner-lane code review: **SHIP, zero CRITICAL/HIGH** (after 3 fix rounds: Fabric token-forwarding fix, real stdin timeout, narrowed advisory-lock contract, wheel notices)
- Known policy note: exact `pyproject.toml`/`.semgrep.yml` creation was blocked by the MAX protected-config guard; verified fallbacks `setup.py` + `semgrep-rules.yaml` used (guard was never bypassed)

## What's left (Phase 7 gates)

1. **OpenCodeReview full verdict** - current attempts are PARTIAL: Gemini free tier 429s, then Groq free tier 429/408 on large batches. Retry after quota reset (config now correctly points at `fb-groq` = gpt-oss-120b, verified live)
2. **Exact `claude-opus-5` review verdict** - blocked on `Credit balance is too low`. Owner: G (Anthropic credit top-up)
3. After both verdicts: release/publication (GitHub repo, PyPI upload) requires G's explicit outbound approval. Nothing has been pushed or published.

## How to try it locally

```sh
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools"
export MQUERY_NODE="$PWD/.tools/node-v22.23.2-darwin-arm64/bin/node"
export PYTHONPATH=src   # not pip-installed yet; or pip install dist/mquery_toolkit-0.1.0-py3-none-any.whl
python3 -m mquery_toolkit.cli format tests/fixtures/m-spec-let.pq   # diff by default
python3 -m mquery_toolkit.cli check tests/fixtures/DataConnectors/HelloWorld.query.pq
pytest --cov   # full suite
```
