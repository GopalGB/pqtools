# mquery-toolkit

Offline command-line and Python tooling for Power Query M source: parse, format,
lint (`check`), and safely rename a `let` binding.

> **Unofficial.** Not affiliated with or endorsed by Microsoft. Not an M runtime -
> it parses and formats M source text; it does not evaluate queries.

## Install

```bash
pip install mquery-toolkit
```

Requires **Node.js 22 or newer** on `PATH`, or point `MQUERY_NODE` at a Node
binary. The Microsoft parser and formatter packages are bundled inside the
wheel (`_bridge.cjs`) - no `npm install` needed.

## Quick start

```bash
# Parse to deterministic JSON (tokens, root kind, bindings/references)
mquery parse query.pq

# Format - dry run prints a unified diff, nothing is written
mquery format query.pq

# Format and write in place (atomic replace, preserves mode/newline/encoding)
mquery format query.pq --write

# Lint, machine-readable output; exit code 2 if any diagnostic is severity=error
mquery check query.pq --json

# Rename one top-level let binding - dry run first
mquery rename query.pq --old OldName --new NewName
```

## Python API

```python
from mquery_toolkit import check, format_source, parse, rename, update_file

parsed = parse(source_text)  # dict: tokens, rootKind, analysis
formatted = format_source(source_text)  # formatted M source, same encoding
diagnostics = check(source_text, "query.pq")  # list[Diagnostic]
renamed = rename(source_text, "OldName", "NewName")

# File-level edit with the same dry-run/--write safety model as the CLI
diff = update_file(path, format_source)  # dry run: unified diff
diff = update_file(path, format_source, write=True)  # atomic write
```

## Diagnostics

| Code | Severity | Meaning |
|---|---|---|
| `M_PARSE_ERROR` | error | source does not parse |
| `M001` | error | duplicate `let` binding name |
| `M002` | warning | `Web.Contents` called with a non-literal (dynamic) URL |
| `M003` | warning | credential-like literal (`password`/`token`/`secret` = `"..."`) |
| `M004` | warning | `let` binding unreachable from the result |
| `M005` | warning | unresolved unqualified reference |
| `M006` | info | source-function inventory (`*.Contents` dependency) |

`check --json` emits stable objects; `check` without `--json` prints
`file:line:column: severity code: message` per diagnostic. The CLI exits `2`
when any diagnostic has severity `error`, `0` otherwise.

## Safety model

- **Dry-run by default.** Every edit command (`format`, `rename`,
  `replace-source`) prints a unified diff and touches nothing unless `--write`
  is passed.
- **`--write` is an atomic replace**: the file is written to a sibling temp
  file, `fsync`'d, `chmod`'d to match the original, then moved into place with
  `os.replace`.
- **Layout is preserved**: UTF-8 encoding, newline convention (`\n` vs
  `\r\n`), final-newline state, and file mode all round-trip unchanged.
- **Refuses symlinks and hardlinks** - writes require a regular, single-link
  file.
- **Detects concurrent change**: the source is snapshotted before the
  transform and re-checked immediately before the atomic replace; a change in
  between raises `SafeWriteError`.
- **Advisory lock while writing only** - a `--write` call takes a
  cross-process advisory lock (`fcntl`/`msvcrt`) for the duration of the
  write and removes the lock file afterward, best-effort. Dry-run calls take
  no lock and create no lock file.
- This is **not mandatory locking** - no OS provides a portable mandatory
  lock, so a non-cooperating program can still race past the final snapshot
  check. The snapshot re-checks are the correctness guard, not the lock.

## Limits

- Input and output are capped at **10 MiB**.
- The Node subprocess is bounded to a **30 second** timeout.
- Supported extensions: `.pq`, `.m`, `.pqm`, and any `*.query.pq` file.
- `rename` scope: exactly **one unquoted top-level `let` binding**. It refuses
  quoted identifiers (`#"..."`), record literals, lambda expressions, and
  non-ASCII source.

## Optional adapters

- **`fabric` extra** (`pip install "mquery-toolkit[fabric]"`) - a Fabric
  Execute Query client that takes a caller-provided bearer token and an
  injected HTTP transport. It never manages credentials itself and is fully
  mocked in tests (no network access in the test suite).
- **`pqtest`** - a bounded wrapper around a user-installed Microsoft PQTest
  executable, Windows-only, pinned to version `2.155.2`. It never downloads a
  binary; it only validates and runs one already on disk.

## What it is not

- Not an M language runtime or evaluator.
- Not a Power BI or Fabric client, and it does not manage credentials.
- Not a general-purpose file editor - it only touches files with a supported
  extension and only through the safety model above.
- Not a replacement for Microsoft's own parser/formatter - it vendors and
  calls them directly rather than reimplementing M syntax.

## Development

```bash
git clone https://github.com/GopalGB/mquery-toolkit
cd mquery-toolkit
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,fabric]"
npm ci --ignore-scripts

pytest -q --cov=mquery_toolkit --cov-fail-under=80
mypy src
ruff check .
ruff format --check .
npm test
python -m build
```

## License

MIT - see `LICENSE`. Bundled Microsoft packages
(`@microsoft/powerquery-parser`, `@microsoft/powerquery-formatter`) and their
dependencies are also MIT; see `THIRD_PARTY_NOTICES.txt` and `NOTICE`.
