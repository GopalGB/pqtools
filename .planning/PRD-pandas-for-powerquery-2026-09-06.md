# PRD - "pqtools is to Power Query what pandas is to CSV"

Written 2026-09-06, after the round-9 review fixes landed (`6b764a7`, gate
8/8, 4003 tests) and the branch was pushed. Scope decided by a 15-model
`orchestra --all` fight over the read-only audit's eight gaps, plus a ninth
the fight itself found. This file is the acceptance contract; "done" is
measured against §6, not against "I wrote some code".

## 1. The claim we are trying to earn

A person who works in Power Query should be able to do their whole loop
without Power BI open: read the queries out of a file, see one query's
source, run it, get the rows into the tool they already use, diff two
versions, do it across a folder, and be told - by name - when something
cannot be done. Today six of those seven work; the loop breaks at
"get the rows into the tool they already use" and at "across a folder".

**What this PRD does not do.** It does not move the compatibility number
(547/635) and does not touch the M evaluator. Every gap below is an
interface gap. Nothing here invents a function, an arity, a format or a
semantic. That constraint is what makes the whole set safe to build at once.

## 2. Where the eight gaps stand

| # | Gap | Verdict | Why |
|---|---|---|---|
| 1 | No `to_pandas()` / `to_arrow()` / parquet export | **SHIP** | This is the slogan. Optional extras, never a required dependency. |
| 3 | Cannot print one member's raw M without evaluating it | **SHIP** | `split_shared` already has the slice. Trivial and constantly wanted. |
| 4 | No `pq diff A.pq B.pq` | **SHIP** | Completes the "safely edits" half of the claim; `difflib` is already used for dry-run previews. |
| 5 | One file per invocation, no glob/batch | **SHIP** | The gap between a demo and a tool. `pq check *.pq` is what a repo owner runs in CI. |
| 8 | `pq explain NAME` exists as a library call, not a verb | **SHIP** | The refusal surface *is* this project's identity. It should be one command away. |
| 9 | **No way to set a query parameter** (found by the fight, not the audit) | **SHIP** | Real queries open with `#"StartDate" = ...`. `--bind` takes a file path only, so a parameterised query cannot be run at all without editing it. Every model that spotted it called it closer to the practitioner than anything on the audit's list. |
| 2 | `dependencies()` lists builtins, not query -> query | **DEFER** | Highest silent-wrong-data risk in the set: it returns a *claim about code* rather than the value of code, and every failure mode (shadowed identifiers, quoted names, names inside strings) is invisible in the output. It needs the same binding-aware analysis as the safe-rename guard, which is hard-won and must not be approximated. |
| 6 | Fabric/PQTest not reachable from `pq eval`; Fabric Arrow bytes never decoded | **DEFER** | An opaque, versioned, third-party binary format from a service we cannot test against here. A wrong decode is silently wrong data, which is exactly the failure class the product promises not to have. Stays a typed refusal. |
| 7 | `.pbip`/TMDL write-back | **DEFER** | TMDL round-trip fidelity is its own project. Stays `SafeWriteError`. |

The three deferrals are recorded in `SUPPORT-MATRIX.md` as named refusals,
not left as silence.

## 3. The object API question

**A thin facade, not a DataFrame clone.** `pqtools.open(path)` returns a
handle with `.queries`, `.source(name)`, `.eval(name)` - discoverability, so
the first thing a person types in a REPL works. It does **not** grow
`.filter()`, `.groupby()` or any transform verb. Transforms belong in M,
where they fold and where the user already knows the semantics; a
Python-side transform layer would be a second, silently different dialect.
Near-unanimous across the fight, and the one dissent (defer the handle
entirely) argued against a DataFrame mimic nobody proposed.

## 4. The single highest risk, and its guard

**`to_pandas()` type mapping.** M has types pandas has no lossless slot for:
`duration`, `time`, `binary`, `type`/`function`/`record`/`table` cells,
nested tables, and `null` inside an integer column. The failure mode is not
a crash, it is a column that silently reads as `object` or `NaN` and a user
who trusts it.

Guard, three parts, all mandatory before the feature is called done:
1. **An explicit, tested map** from every M primitive to its pandas and
   Arrow type. One test per row.
2. **A typed refusal, never a coercion.** A value that has no lossless slot
   raises `ExportRefusal` naming the column, the M type and the reason.
   `DeferredTable` (an unread lazy read) refuses the same way `pq eval`
   already refuses it, rather than silently exporting an empty frame.
3. **A round-trip test on real output**: evaluate a query whose result
   covers every mapped type, export, read back, assert dtypes and values.

The gate must stay green in an environment with neither pandas nor pyarrow
installed. Every export test is `pytest.importorskip`-guarded, and one test
asserts that importing `pqtools` with the extras absent still works and that
`to_pandas()` raises a named `ExportRefusal` telling the user which extra to
install. (Flagged in the fight: nobody else specified how the base env is
protected, and an `ImportError` at collection time would take the whole
4003-test gate down.)

## 5. What ships

**Library**

- `pqtools.export`: `to_pandas(rows)`, `to_arrow(rows)`, `to_parquet(rows, path)`,
  `ExportRefusal`. Rows are what `evaluate()` already returns.
- `pqtools.open(path)` -> handle with `.queries`, `.source(name)`, `.eval(name)`.
- Both re-exported from `pqtools.__all__`.

**CLI**

- `pq eval FILE --to parquet|csv|json|pandas` (`--to` supersedes nothing;
  `--format` keeps working).
- `pq show FILE [--member NAME]` - raw M source, no evaluation.
- `pq explain NAME` - why a name is refused, or that it is supported.
- `pq diff A.pq B.pq` - unified diff of normalised source.
- `pq check 'src/**/*.pq'` - glob/batch on every read-only verb, one exit
  code for the batch, one line per file.
- `pq eval FILE --set-param NAME=VALUE` - bind a query parameter to a scalar
  M literal, parsed by the package's own parser, never by `eval()`.

**Docs**: `SUPPORT-MATRIX.md` rows for each new surface and for the three
deferrals; `README.md` quick-start updated to the real end-to-end loop;
`llms.txt` error rows for `ExportRefusal`.

## 6. Acceptance - "done" means all of these

1. `bash scripts/release_gate.sh` PASSES 8/8 sequentially, test count up from
   4003, on a machine with pandas and pyarrow installed.
2. The suite passes with **both extras uninstalled** (importorskip path).
3. Every new behaviour has a **positive control**: reintroduce the defect,
   watch the test go red, restore, watch it go green. Recorded per control.
4. The end-to-end loop runs against a **real workbook-authored file**, not a
   fixture written for the test: open, show, explain, eval, export, diff.
5. No new required dependency in `pyproject.toml`.
6. Every refusal is typed and names the thing refused. No new silent path -
   the round-9 AST sweep over exception handlers that return without raising
   must still find nothing new.
7. `README.md` no longer claims anything the gate does not check.

## 7. Not in scope, said plainly

Gaps 2, 6 and 7 above. No Python-side transform verbs. No change to the
M evaluator, the builtin count or the refusal set. No tag, no PyPI publish -
those stay G's explicit call.
