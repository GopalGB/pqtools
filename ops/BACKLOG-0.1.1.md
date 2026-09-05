# mquery-toolkit 0.1.1 backlog

Everything the six review rounds surfaced that was deliberately not fixed for 0.1.0. Each line names where it came from, so nothing here is a vague "improve X". Open these as GitHub issues after the repo exists (`gh issue create -F -`), or work them in one branch.

The 0.1.0 stopping rule: the round-6 gate returned **SHIP** with no CRITICAL or HIGH, and every remaining finding was MEDIUM or LOW. Shipping beat another round.

## Correctness and robustness

| From | Item |
|---|---|
| round-6 gate MEDIUM | `os.replace` in `update_file` is not followed by an fsync of the parent directory, so a crash immediately after `--write` can leave the old content on disk while the docs advertise a durable atomic replace. Fix: fsync the parent dir fd, suppressed on Windows. |
| round-6 gate MEDIUM | `fabric.py` parses `Retry-After` before the status dispatch, so a 503 carrying an HTTP-date `Retry-After` reports "must be whole seconds" instead of "returned HTTP 503", hiding the real failure. Move the parse into the 202/429 branches. |
| round-6 gate LOW | `fabric.py` `result_requested` is never reset, so if the result endpoint answers 202 and polling returns to the operations endpoint, a later legitimate `Succeeded` is misreported. |
| round-6 gate LOW | `fabric.py` `_same_origin` compares `netloc` case-sensitively, so a valid `Location` differing only in host case is rejected. Compare `(scheme.lower(), hostname, port)`. |
| round-6 gate LOW | The `Running`/`NotStarted` poll sleeps a hardcoded 1 second and ignores `Retry-After`; a server asking for 30 s gets polled 30 times. |
| round-6 gate LOW | `cli._source` gates only on `st_size`, so `mquery check <fifo>` blocks forever. Reuse `_snapshot`, which already refuses non-regular files. |
| round-6 gate LOW | CLI errors print to stdout, so `mquery parse q.pq \| jq` feeds the error object into the consumer. Send the error branch to stderr. |
| round-6 gate LOW | On the abandoned-reader-thread timeout path the pipe fds are detached and never closed. Bounded in practice (the child is killed) but a long-lived host calling `format_source` against a hung Node leaks. Close the raw fds or document the cost in the comment. |
| round-6 gate LOW | `M002`/`M003` offsets are computed over the BOM-bearing source while parser diagnostics use BOM-stripped offsets, so those two codes are one column off on line 1 of a BOM file. |
| round-6 gate LOW | `update_file`'s `not path.name.endswith(".query.pq")` clause is dead - `*.query.pq` already has suffix `.pq`. |

## Windows platform gaps (found by CI on 2026-09-03, documented not hidden)

| From | Item |
|---|---|
| CI, windows-latest x 3 | `_run_process_bounded` cannot unblock a reader thread already inside `ReadFile`, so closing the pipe fds does not cut a timed-out call short when the child spawned a grandchild holding stdout - measured 30 s against a 2 s timeout. Redesign for 0.4.0: give the child a temp FILE for stdout/stderr instead of a PIPE, which removes reader threads entirely and makes the timeout exact on every OS; the 10 MiB cap becomes a size check on that file. Non-trivial rewrite of the most safety-critical function, so it is not being rushed. |
| CI, windows-latest x 3 | A directory `fsync` after `os.replace` is not possible on Windows; the `OSError` is suppressed by design and the rename's durability falls back to the filesystem's own ordering. No portable fix exists; documented in README Limits. |

## Performance

| From | Item |
|---|---|
| round-5 gate MEDIUM (V5) - **re-measured 2026-09-03** | The claimed O(bindings x nodes) *time* blow-up does not reproduce: 2,000 simple bindings parse in 0.14 s and 3,000 reference-heavy bindings (188 KiB) in 0.45 s, flat. The real limit is **response size**: the bridge's parse JSON is ~42x the source (8 fields per token), so the 10 MiB output cap fails `parse`/`check`/`dependencies`/`rename` with `NodeError: OUTPUT_LIMIT` at about **240 KiB of M source** (317 KiB fails, 188 KiB passes). `format` returns only text and is unaffected. Fix for 0.1.2: a lean bridge mode for `check` (analysis + kind/text/line/column only, which is all Python reads - `endLine`/`endColumn`/`start`/`end` are unused), roughly doubling the ceiling; keep the full `parse` JSON shape for the CLI. Document the ceiling in README Limits now. |
| round-6 gate MEDIUM | `rename` spawns Node three times (rename plan, collision-check parse, validation parse). `check` was reduced to one call and has a regression test; `rename` has no equivalent. Fix: have the bridge's rename response also return tokens, then add a call-count test. |

## Test coverage

| From | Item |
|---|---|
| round-2 gate R16, round-6 gate MEDIUM | `js/bridge.test.js` only asserts pinned package versions. `renameSpans`'s scope refusals, `analysisView`'s function/each/nested-let scoping, and the parse-error line/column mapping have no direct tests, and JS is outside the coverage gate. A scope regression would surface only as a silently missing `M005`. Add `node --test` cases over known ASTs. |
| round-2 gate R16 | `fabric.redact` is unused outside tests - wire it into an error path or remove it. |

## Diagnostics quality

| From | Item |
|---|---|
| first review INFO | `M002` (dynamic `Web.Contents`) and `M003` (credential-like literal) are text-pattern heuristics, not AST checks; expect false positives inside comments and strings. Move them onto the parsed tokens. |
| first review INFO | `dependencies()` is token-adjacency based (`Identifier` followed by `(`, minus same-`let` bindings), so it lists any invoked name including user functions from an outer scope. Document precisely or resolve through the analysis data. |

## Support matrix

| From | Item |
|---|---|
| first review #9 | **DONE 2026-09-03**: all 9 CI cells green on `ec1a58a` (https://github.com/GopalGB/mquery-toolkit/actions/runs/33713321632); `POWERQUERY-SUPPORT-MATRIX.md` now says measured, not claimed. |
