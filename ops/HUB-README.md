# powerquery-toolkit - one folder, everything about `pqtools` (formerly `mquery-toolkit`)

Root hub for the Power Query M toolkit work (Codex + Cline built it 2026-09-01/02; the Opus 5 session reviewed it, ran four fix rounds, passed both mandatory gates and prepared the release 2026-09-02/03). Start with `STATUS.md`.

| File | What it is |
|---|---|
| `STATUS.md` | Verified state as of the last session: every gate, every number, what is still open. **Read this first.** |
| `DEPLOYMENT-PLAN.md` | End-to-end release plan D0-D6: exact commands, which steps are G's click, rollback, open decisions. |
| `REVIEW-opus5-2026-09-02.md` | Every review finding and its disposition: the in-session Opus read (11), wrapper rounds 1-3 (W, R, T series), and OpenCodeReview (O series), plus what was verified correct and which models fired. |
| `OVERVIEW-cline-2026-09-02.md` | Cline's own one-page summary written before the Opus session (kept as-is; some "what's left" items are now closed - see STATUS). |
| `BACKLOG-0.1.1.md` | Every deferred review finding, traced to the round that raised it. Open as issues after the repo exists. |
| `evidence/` | Matrix logs and reviewer outputs, file names carry commit ids and timestamps. `run-opus-gate.sh` and `run-ocr-gate.sh` re-run either gate on any commit range. |
| `publish-1-github.sh` | Step D1: pre-flight + `gh repo create` + push. Run it with `!` so the security gate can ask for your `y`. |
| `../` (this repo) | The package itself. Isolated git repo, remote `GopalGB/pqtools`. |
| `planning/` -> `.planning/max-log-recovery-powerquery-toolkit-2026-09-01` | PRD, PLAN, STATE, support matrix, corpus manifest, Track A evidence. The dated record of how it was built. |
| `research-feasibility-report.md` -> `research/reports/...` | ORACLE feasibility report, recommendation GO. |

`code/`, `planning/` and the research report are symlinks so there is one copy of each and nothing drifts; open them from here.

## What the package is, in three lines

`pqtools` (published 2026-09-03 as `mquery-toolkit` 0.1.0, renamed the same day) is an unofficial, offline Python library + CLI (`pq`, formerly `mquery`) for Power Query M source: parse, format, check, list dependencies, rename one binding, replace source. Syntax and formatting are delegated to Microsoft's pinned MIT packages through a vendored Node bridge, so it needs Node.js 22+ on the machine. It is not an M runtime and is not affiliated with Microsoft.

## Try it locally

```sh
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools"
export MQUERY_NODE="$PWD/.tools/node-v22.23.2-darwin-arm64/bin/node"
.venv/bin/python -m mquery_toolkit.cli check tests/fixtures/DataConnectors/HelloWorld.query.pq
.venv/bin/python -m mquery_toolkit.cli format tests/fixtures/m-spec-let.pq     # dry-run diff
.venv/bin/python -m pytest -q --cov=mquery_toolkit
```
