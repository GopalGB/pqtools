# mquery-toolkit — pre-release review remediation

## Context

The staged tree is the initial public release of `mquery-toolkit` (offline Power Query M
parse/format/check/rename, driven by a vendored Microsoft parser + formatter behind a Node
bridge). It is packaged for PyPI with a trusted-publishing release workflow and a 3-OS x
3-Python CI matrix.

A correctness/security review of the staged diff found four HIGH issues, several MEDIUM ones,
and a set of LOW cleanups. Two of the HIGH issues will break the `windows-latest` CI leg the
first time it runs; two are latent hangs / masked errors. Two MEDIUM issues make documented
safety guarantees (`README.md` "Refuses symlinks", `SECURITY.md` "prevents lost updates")
untrue as written.

Goal: make the Windows leg green, close the write-boundary gaps so the documented guarantees
hold on every supported OS, and harden the publish path before the first `v*` tag is pushed.

## HIGH — must fix before tagging

### 1. Symlink refusal is unenforced on Windows
`src/mquery_toolkit/core.py:406` — `getattr(os, "O_NOFOLLOW", 0)` degrades to `0` on Windows,
so `_snapshot` opens through a symlink and both `S_ISREG` and `st_nlink != 1` pass.

Fix: before `os.open`, call `os.stat(path, follow_symlinks=False)` and raise `SafeWriteError`
on `stat.S_ISLNK(...)` or a reparse-point `st_file_attributes`. Keep `O_NOFOLLOW` as the
POSIX fast path. Reuse the existing single message string so `tests/test_core.py:175`
(`match="non-symlink"`) keeps passing.

### 2. Windows lock teardown masks errors and leaks the fd
`src/mquery_toolkit/core.py:497-536` — `msvcrt.locking(fd, LK_LOCK, 1)` raises `OSError`
after ~10s of contention. That escapes into the `finally`, where `_unlock_file` calls
`LK_UNLCK` on a never-acquired region and raises again, replacing the original error and
skipping `os.close(lock_fd)` (line 532) and the lock unlink (line 536).

Fix: set `acquired = True` only after `_lock_file` returns; in the `finally`, unlock only if
`acquired`, wrap it in `contextlib.suppress(OSError)` (already imported at the top of
`core.py`), and move `os.close(lock_fd)` into its own nested `finally` so it always runs.

### 3. Subprocess timeout does not actually bound the call
`src/mquery_toolkit/core.py:174` — `thread.join()` takes no timeout and only the direct child
is killed, so a grandchild inheriting the stdout/stderr pipes blocks the reader thread
forever. Reachable through `pqtest`, which reuses `_run_process_bounded`.

Fix: join with a bounded deadline; on expiry, leave the daemon readers and raise the same
`subprocess.TimeoutExpired` the caller already handles at `core.py` `_bridge` and
`pqtest._run_bounded`. Add a regression test spawning a child that forks a sleeping
grandchild holding stdout.

### 4. Windows CRLF conversion breaks the checksum-verified corpus
No `.gitattributes` is staged. GitHub `windows-latest` runners ship Git for Windows with
`core.autocrlf=true`, so `tests/fixtures/DataConnectors/*.pq` check out with CRLF and
`hashlib.sha256(read_bytes())` at `tests/test_corpus.py:13` will not match `SHA256SUMS`.

Fix: add `.gitattributes`:
```
* text=auto eol=lf
tests/fixtures/** -text
src/mquery_toolkit/_bridge.cjs -text
```
This also keeps the `git diff --exit-code -- src/mquery_toolkit/_bridge.cjs` bundle-determinism
check at `.github/workflows/ci.yml:33` stable across the matrix.

## MEDIUM

- **Lock unlink breaks mutual exclusion** — `core.py:536`. Unlinking while another process is
  blocked on the same path lets a waiter and a fresh process lock two different inodes. Either
  stop unlinking the lock file, or amend `SECURITY.md` to drop "prevents lost updates between
  cooperating `mquery` processes"; the snapshot re-check alone does not provide it.
- **Rename span ordering** — `core.py:294`. `reversed(edits)` assumes ascending spans, but
  `js/bridge.js` `renameSpans` emits them in `Object.values()` traversal order. Use
  `sorted(edits, reverse=True)` and reject overlapping spans.
- **Node resolved from CWD on Windows** — `core.py:118`. Bare `"node"` goes through
  `CreateProcess`, which searches the current directory before `PATH`. Resolve with
  `shutil.which("node")` and pass the absolute path; raise the existing `NodeError` when unresolvable.
- **Dev-machine path in the wheel** — `core.py:112`. Remove the hardcoded
  `.tools/node-v22.23.2-darwin-arm64/bin/node` fallback; `MQUERY_NODE` already covers it.
- **PQTest reuses the Node timeout** — `pqtest.py:34,50`. Introduce
  `PQTEST_TIMEOUT_SECONDS` (caller-overridable, default well above 30s); real connector runs
  exceed the current bound and surface as a misleading `AdapterError`.
- **Fabric result URL breaks on query strings** — `fabric.py:156`.
  `request_url.rstrip("/") + "/result"` mangles `.../op?api-version=1`. Rebuild with
  `urlsplit`/`urlunsplit`, appending to `path` only, then re-run the existing
  `_require_https_same_origin` check.
- **Release workflow hardening** — `.github/workflows/release.yml`. Pin
  `pypa/gh-action-pypi-publish@release/v1` (line 45) to a commit SHA; add
  `permissions: contents: read` to the `build` job; add a step asserting the pushed `v*` tag
  matches `project.version` in `pyproject.toml` before the publish job runs.
- **Declared-but-unrun security tooling** — `semgrep-rules.yaml` and the `pip-audit` dev
  dependency have no CI step. Add both to `.github/workflows/ci.yml` (a single ubuntu leg is
  enough) or remove them; `tests/test_rules.py` only regex-greps the YAML, which is not coverage.
- **Missing `py.typed`** — add the marker file to `src/mquery_toolkit/` and to
  `[tool.setuptools.package-data]` in `pyproject.toml`, alongside `_bridge.cjs`.
- **Verify mypy on the CLI transform** — `cli.py:86-102` declares
  `transform: Callable[[str], str]`, assigns it at line 88, then redefines it with two `def`s
  whose parameter names differ. Run `python -m mypy src`; if it errors, replace the two inner
  `def`s with `functools.partial` / lambdas bound to the declared name.

## LOW

- `cli.py:103` — use the `_text` argument `update_file` read under the lock instead of the
  outer pre-lock `source`; or drop `replace_source`'s unused first parameter.
- `fabric.py:23` — `redact` is dead outside tests; wire it into an error path or remove it.
- `fabric.py:174` — `int(str(retry_raw))` rejects the RFC-legal HTTP-date `Retry-After`.
  Parse it or document the restriction in `README.md`.
- `core.py:196` — the Node version regex rejects v100+ and prerelease tags (`v22.0.0-nightly`).
- `js/bridge.test.js` — add direct tests for `renameSpans` scope refusals and `analysisView`
  scoping; today only pinned package versions are asserted.
- `pyproject.toml` — raise `twine>=5` to `twine>=6.1`; older twine cannot read the PEP 639
  metadata `setuptools>=77` emits.
- `CONTRIBUTING.md` — replace the "frozen Track B validation matrix" reference with the actual
  commands from `README.md`'s Development section.

## Verification

1. `python -m pytest -q --cov=mquery_toolkit --cov-fail-under=80` — all green locally.
2. `python -m mypy src && python -m ruff check . && python -m ruff format --check .` — confirms
   the `cli.py` transform item and the CI gate at `ci.yml:37-39`.
3. `npm ci --ignore-scripts && npm test && npm run bundle && git diff --exit-code -- src/mquery_toolkit/_bridge.cjs`.
4. Windows-specific, the leg that currently fails: push the branch and confirm
   `test_write_refuses_symlink_and_hardlink` and
   `test_vendor_fixture_checksums_and_parser_coverage` pass on `windows-latest`. Add
   `git config core.autocrlf` output as a debug step on the first run to confirm the
   `.gitattributes` fix took effect.
5. Timeout regression: new test spawning a grandchild that inherits stdout must raise
   `subprocess.TimeoutExpired` within the bound rather than hanging.
6. Publish path: dry-run against TestPyPI using the commented `publish-testpypi` job in
   `release.yml` before the first real `v*` tag.
