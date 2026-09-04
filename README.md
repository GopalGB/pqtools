# pqtools

Offline command-line and Python tooling for Power Query M source: parse, format,
lint (`check`), safely rename a `let` binding, and run (`eval`) the
transformation chain of a query against data you supply.

> **Unofficial.** Not affiliated with or endorsed by Microsoft. Not a Power Query
> runtime - `pq eval` runs the transformation chain of a query locally; it never
> runs a connector (`Web.Contents`, `Sql.Database`, `Csv.Document`, ...). See
> [Running M](#running-m) below.

> **Renamed.** Published as `mquery-toolkit` 0.1.0 on 2026-09-03 and renamed the
> same day to `pqtools` to avoid a CLI name collision with the existing `mquery`
> package on PyPI (a Yara malware-query tool). `mquery-toolkit` 0.1.0 is yanked.

## Install

```bash
pip install pqtools
```

Requires **Node.js 22 or newer** on `PATH`, or point `MQUERY_NODE` at a Node
binary. The Microsoft parser and formatter packages are bundled inside the
wheel (`_bridge.cjs`) - no `npm install` needed.

## Quick start

```bash
# Parse to deterministic JSON (tokens, root kind, bindings/references)
pq parse query.pq

# Format - dry run prints a unified diff, nothing is written
pq format query.pq

# Format and write in place (atomic replace, preserves mode/newline/encoding)
pq format query.pq --write

# Lint, machine-readable output; exit code 2 if any diagnostic is severity=error
pq check query.pq --json

# Rename one top-level let binding - dry run first
pq rename query.pq --old OldName --new NewName

# Run a query's transformation chain locally, against your own data
pq eval report.pq --bind Source=data.csv
```

## Python API

```python
from pqtools import check, format_source, parse, rename, update_file

parsed = parse(source_text)  # dict: tokens, rootKind, analysis
formatted = format_source(source_text)  # formatted M source, same encoding
diagnostics = check(source_text, "query.pq")  # list[Diagnostic]
renamed = rename(source_text, "OldName", "NewName")

from pqtools.evaluate import evaluate

result = evaluate(source_text, bindings={"Source": [{"a": "1"}, {"a": "2"}]})

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

`M002` and `M003` are token-based checks over the parsed source, so they no
longer fire inside comments or strings. Every matching occurrence is
reported, one diagnostic per call site or literal.

`check --json` emits stable objects; `check` without `--json` prints
`file:line:column: severity code: message` per diagnostic. The CLI exits `2`
when any diagnostic has severity `error`, `0` otherwise.

## Running M

pandas does not run Excel's formulas; it replaces Excel's data connections with
your data, in Python. `pq eval` does the same for Power Query. A real M query is
a `Source = <connector>(...)` step followed by a chain of `Table.*`
transformations. `pqtools` cannot run the connector step - that is Microsoft's
proprietary Mashup Engine, and this project does not reimplement it. But if
*you* supply the source table, the rest of the transformation chain runs
locally, offline, in Python:

```bash
pq eval report.pq --bind Source=data.csv
```

`--bind NAME=PATH` loads `PATH` (a `.csv`, read as a list of records with
`csv.DictReader` - every value stays text, or a `.json` file, loaded as
whatever it holds) and, wherever `NAME` is used as a `let` binding in the
query, substitutes it directly - the binding's own right-hand-side expression
(the connector call) is never evaluated, which is exactly what makes it
irrelevant that `pqtools` cannot run it.

A **table** is simply `list[dict[str, Any]]` - a list of records. A record is
`dict[str, Any]`. A list is `list[Any]`. That is the whole data model.

**Worked example.** Given `report.pq`:

```m
let
  Source = Csv.Document(File.Contents("ignored.csv")),
  Kept = Table.SelectRows(Source, each [b] <> "y"),
  Renamed = Table.RenameColumns(Kept, {{"a", "id"}})
in
  Renamed
```

and `data.csv`:

```csv
a,b
1,x
2,y
3,z
```

```bash
$ pq eval report.pq --bind Source=data.csv
[{"b": "x", "id": "1"}, {"b": "z", "id": "3"}]
```

`Csv.Document(File.Contents("ignored.csv"))` is never called - `ignored.csv` is
never opened. `Source` is the CSV you bound, `Kept` drops the `b = "y"` row, and
`Renamed` renames `a` to `id`. Without `--bind`, the same query fails with a
typed, exit-`2` error naming the connector:

```bash
$ pq eval report.pq
error M_EVAL_UNSUPPORTED: Csv.Document is a connector - Power Query's Mashup
Engine runs it (Fabric or PQTest is the host that can); pqtools evaluates only
the transformation chain after you supply its result table with --bind
```

**Supported:** number/text/logical/null literals; `+ - * /`; `= <> < <= > >=`;
`and or not`; text `&`; `if/then/else`; `let/in` (lazy, memoised, correctly
shadowed - a binding's expression is only ever evaluated once, and only if
something actually references it); records (`[a = 1]`) and field access
(`r[a]`, `r[a]?`, and the `each`-scoped `[a]` shorthand for `_[a]`); lists
(`{1, 2}`) and index access (`l{0}`, `l{0}?`); `each` and `(x) => ...` lambdas
and calling them; `try ... otherwise ...`; and these 280 builtins.
The list below is generated from `pqtools.evaluate.BUILTINS` and
`tests/test_readme_builtins.py` fails if the two ever disagree - so it cannot
silently drift, which a hand-maintained list can and did:

```
Text.AfterDelimiter Text.At Text.BeforeDelimiter Text.BetweenDelimiters
Text.Clean Text.Combine Text.Contains Text.End Text.EndsWith Text.From
Text.Insert Text.Length Text.Lower Text.Middle Text.NewGuid Text.PadEnd
Text.PadStart Text.PositionOf Text.PositionOfAny Text.Proper Text.Remove
Text.Repeat Text.Replace Text.Reverse Text.Select Text.Split Text.SplitAny
Text.Start Text.StartsWith Text.ToList Text.Trim Text.TrimEnd Text.TrimStart
Text.Type Text.Upper
Number.Abs Number.BitwiseAnd Number.BitwiseOr Number.BitwiseXor Number.Exp
Number.Factorial Number.From Number.IntegerDivide Number.IsEven Number.IsNaN
Number.IsOdd Number.Ln Number.Log Number.Log10 Number.Mod Number.Power
Number.Random Number.RandomBetween Number.Round Number.RoundAwayFromZero
Number.RoundDown Number.RoundTowardZero Number.RoundUp Number.Sign Number.Sqrt
Number.ToText Number.Type
List.Accumulate List.AllTrue List.AnyTrue List.Average List.Buffer
List.Combine List.Contains List.ContainsAll List.ContainsAny List.Count
List.Difference List.Distinct List.First List.FirstN List.Generate
List.InsertRange List.Intersect List.IsEmpty List.Last List.LastN List.Max
List.Median List.Min List.Mode List.NonNullCount List.Numbers List.Percentile
List.PositionOf List.Positions List.Range List.RemoveItems List.RemoveNulls
List.Repeat List.ReplaceValue List.Reverse List.Select List.Skip List.Sort
List.Split List.StandardDeviation List.Sum List.Transform List.Union List.Zip
Record.AddField Record.Combine Record.Field Record.FieldCount
Record.FieldNames Record.FieldOrDefault Record.FromList Record.HasFields
Record.RemoveFields Record.RenameFields Record.ReorderFields
Record.SelectFields Record.ToList Record.ToTable Record.TransformFields
Table.AddColumn Table.AddIndexColumn Table.Buffer Table.ColumnCount
Table.ColumnNames Table.Combine Table.DemoteHeaders Table.Distinct
Table.DuplicateColumn Table.ExpandRecordColumn Table.ExpandTableColumn
Table.FillDown Table.FillUp Table.FirstN Table.FromColumns Table.FromList
Table.FromRecords Table.FromRows Table.FromValue Table.Group Table.HasColumns
Table.IsEmpty Table.Join Table.LastN Table.Max Table.Min Table.NestedJoin
Table.Pivot Table.PromoteHeaders Table.Range Table.RemoveColumns
Table.RemoveRowsWithErrors Table.RenameColumns Table.ReorderColumns
Table.ReplaceValue Table.RowCount Table.SelectColumns Table.SelectDuplicates
Table.SelectRows Table.Skip Table.Sort Table.SplitColumn Table.ToColumns
Table.ToList Table.ToRecords Table.ToRows Table.TransformColumnNames
Table.TransformColumnTypes Table.TransformColumns Table.Transpose
Table.Unpivot Table.UnpivotOtherColumns
Date.AddDays Date.AddMonths Date.AddWeeks Date.AddYears Date.Day
Date.DayOfWeek Date.DayOfWeekName Date.DayOfYear Date.EndOfMonth
Date.EndOfWeek Date.EndOfYear Date.From Date.IsInCurrentMonth
Date.IsInCurrentYear Date.Month Date.MonthName Date.QuarterOfYear
Date.StartOfMonth Date.StartOfWeek Date.StartOfYear Date.ToText Date.Type
Date.WeekOfYear Date.Year
DateTime.AddZone DateTime.Date DateTime.FixedLocalNow DateTime.From
DateTime.LocalNow DateTime.Time DateTime.ToText DateTime.Type
Duration.Days Duration.From Duration.Hours Duration.Minutes Duration.Seconds
Duration.ToText Duration.TotalDays Duration.TotalHours Duration.TotalMinutes
Duration.TotalSeconds
Time.From Time.Hour Time.Minute Time.Second Time.ToText
Splitter.SplitTextByCharacterTransition Splitter.SplitTextByDelimiter
Splitter.SplitTextByEachDelimiter Splitter.SplitTextByPositions
Replacer.ReplaceText Replacer.ReplaceValue
Value.Compare Value.Equals Value.Is Value.Type
Type.Is
Json.Document
Logical.From Logical.Type
Order.Ascending Order.Descending
Occurrence.All Occurrence.First Occurrence.Last
MissingField.Error MissingField.Ignore MissingField.UseNull
RelativePosition.FromEnd RelativePosition.FromStart
JoinKind.FullOuter JoinKind.Inner JoinKind.LeftAnti JoinKind.LeftOuter
JoinKind.RightAnti JoinKind.RightOuter
GroupKind.Global GroupKind.Local
Day.Friday Day.Monday Day.Saturday Day.Sunday Day.Thursday Day.Tuesday
Day.Wednesday
Any.Type
Byte.Type
Currency.Type
Decimal.Type
Double.Type
ExtraValues.Error ExtraValues.Ignore ExtraValues.List
Int16.Type
Int32.Type
Int64.Type
Int8.Type
Percentage.Type
QuoteStyle.Csv QuoteStyle.None
Single.Type
#date #datetime #datetimezone #duration #time
```

Also supported: the M **type system** (`type text`, `type date`, `Int64.Type`
and the other nominal number subtypes) as real values, which is what makes
`Table.TransformColumnTypes` - step two of every query Power Query's UI writes -
actually run; **column projection** (`[Amount]` on a table yields that column's
values, so `each List.Sum([Amount])` works as a `Table.Group` aggregation); and
**temporal values** (`#date`, `#datetime`, `#time`, `#duration`) which compare
and order by value, so date filters and date ranges behave.

**Everything else raises a typed `UnsupportedError` (`M_EVAL_UNSUPPORTED`)
naming the exact construct** - never approximated, never guessed at. That
includes: any connector (`Web.Contents`, `Sql.Database`, `File.Contents`,
`Excel.Workbook`, `Csv.Document`, `Binary.*` - the error names the construct
and says it needs Fabric or PQTest, the two hosts that can actually run it);
`#shared`; `meta`; `??`; field projection (`r[[a],[b]]`); culture-aware date and
number parsing (a supplied culture is refused by name rather than silently
parsed as en-US); `RoundingMode.*`, `TextEncoding.*` and `BinaryEncoding.*`
(deliberately unregistered - their numeric values could not be verified, and a
wrong enum number would silently do the wrong thing rather than fail); any
identifier this evaluator does not know; and any builtin call with an argument
shape not listed above. A wrong number would be worse than a refusal, so `pqtools` never
approximates a connector's result or a builtin's documented behaviour - it
either runs the real, documented semantics or it stops and tells you exactly
where. `max_steps` (default 1,000,000, an `evaluate()` keyword argument) bounds
the total number of AST nodes visited, so a runaway query cannot hang the
caller either.

`pq eval` does not replace Power Query - it replaces the connector's *data*,
the same trade pandas makes when it replaces a spreadsheet's data connections.

## Safety model

- **Dry-run by default.** Every edit command (`format`, `rename`,
  `replace-source`) prints a unified diff and touches nothing unless `--write`
  is passed.
- **`--write` is an atomic replace**: the file is written to a sibling temp
  file, `fsync`'d, `chmod`'d to match the original, then moved into place with
  `os.replace`, after which the parent directory is `fsync`'d so the rename
  itself is durable.
- **Layout is preserved**: UTF-8 encoding, a leading BOM (present in every
  Power Query SDK connector file), newline convention (`\n` vs `\r\n`),
  final-newline state, and file mode all round-trip unchanged.
- **Refuses symlinks and hardlinks** - writes require a regular, single-link
  file.
- **Detects concurrent change**: the source is snapshotted before the
  transform and re-checked immediately before the atomic replace - this final
  snapshot check, not the lock, is the guarantee against lost updates; a
  change in that microsecond window raises `SafeWriteError`.
- **Advisory lock while writing only** - a `--write` call takes a
  cross-process advisory lock (`fcntl`/`msvcrt`) for the duration of the
  write and removes the lock file afterward, best-effort. It only serialises
  cooperating `pq` processes and is not a correctness guarantee: because
  the lock file is removed after use, a waiting process and a freshly
  started one can end up locking different inodes. Dry-run calls take no
  lock and create no lock file.
- This is **not mandatory locking** - no OS provides a portable mandatory
  lock, and the advisory lock is not itself the correctness guard. Use
  source control or external exclusive ownership for concurrent editors.

## Limits

**Each evaluation spawns Node.** `parse`, `check`, `format`, `rename` and `eval`
each start a Node subprocess to reach Microsoft's parser - measured at roughly
**0.75 s per call**, almost entirely process startup rather than parsing. That is
fine for a CLI invocation and for linting a file in CI, but it means evaluating
hundreds of queries in a loop from Python is dominated by process spawn, not by
your data. The test suite hits this hard enough that it runs with `pytest -n auto`
(945 s serial, 126 s parallel). Making the bridge a persistent worker process
would remove the per-call cost; that is a real change to the most
safety-critical code in the package, so it is not being rushed into a release.


- Input and output are capped at **10 MiB**.
- The Node subprocess is bounded to a **30 second** timeout.
- Supported extensions: `.pq`, `.m`, `.pqm`, and any `*.query.pq` file.
- `rename` scope: exactly **one unquoted top-level `let` binding**. It refuses
  quoted identifiers (`#"..."`), record literals, lambda expressions, and
  non-ASCII source.
- `Retry-After` on the Fabric adapter must be whole seconds; HTTP-date values
  are rejected.
- **Windows:** two guarantees are weaker there and the code says so rather than pretending.
  A directory `fsync` after the atomic replace is impossible on Windows, so the rename is durable
  only as far as the filesystem makes it; and if the Node subprocess spawns a grandchild that
  inherits its stdout, a reader already blocked in `ReadFile` is not released by closing the pipe,
  so a timed-out call can run until that grandchild exits. Neither affects the bundled bridge,
  which spawns nothing.
- The parse response is roughly 40x the size of the source, and it is capped at 10 MiB, so `parse`, `check`, `dependencies` and `rename` fail with a typed `NodeError` on sources above roughly 240 KiB. `format` returns only text and is not affected.
- `eval` walks at most `max_steps` AST nodes (default 1,000,000, an
  `evaluate()` keyword argument, not yet exposed as a CLI flag) before raising
  a typed `EvalError` - a runaway or hostile query cannot hang the caller. A
  `--bind` file goes through the same `--bind`-only read path as everything
  else: 10 MiB cap, no symlinks, no non-regular files.

## Working inside .xlsx and .pbix

`pqtools` can read the Power Query M source out of the real files it lives
in - no need to open Excel or Power BI to see or lint a query.

**Supported:** `pq check`, `pq parse`, `pq dependencies` and `pq eval` accept
an `.xlsx`, `.pbix`, `.pbit`, or a `.pbip` project (or its directory) directly.
Each finds the Power Query section(s) inside the container and runs
normally; `check` diagnostics and JSON output are labelled
`container!part` (e.g. `report.pbix!Formulas/Section1.m`) so the output
stays greppable across a batch of files. `pq eval` needs `--member NAME` to
pick one `shared` query out of a container that holds more than one.

```bash
pq check report.pbix
pq check "Sales.pbip" --json
pq dependencies workbook.xlsx
```

**Not supported (yet):** writing back into a container. `pq format`,
`pq rename` and `pq replace-source` refuse with a clear error on a
container path. The underlying logic exists
(`pqtools.containers.write_sections`) and is exercised in this repo's test
suite against synthesized fixtures and a real Power BI Desktop sample - it
rebuilds the container with only the M source changed, then re-reads its
own output and verifies nothing else moved before ever touching disk - but
it has not been validated against the wide range of real-world files this
format can take, so it is deliberately kept out of the CLI.

`pqtools` is not a Power BI or Excel client: `pq eval` runs a query's own
transformation chain against data you supply (see [Running M](#running-m)) -
it never opens a workbook, runs a connector, or writes anything back through
the CLI.

## Optional adapters

- **`fabric` extra** (`pip install "pqtools[fabric]"`) - a Fabric
  Execute Query client that takes a caller-provided bearer token and an
  injected HTTP transport. It never manages credentials itself and is fully
  mocked in tests (no network access in the test suite).
- **`pqtest`** - a bounded wrapper around a user-installed Microsoft PQTest
  executable, Windows-only, pinned to version `2.155.2`. It never downloads a
  binary; it only validates and runs one already on disk.

## What it is not

- Not the Power Query Mashup Engine. `pq eval` runs a query's transformation
  chain against data you supply (see [Running M](#running-m)); it never runs a
  connector, and anything it does not implement raises a typed error instead
  of approximating one.
- Not a Power BI or Fabric client, and it does not manage credentials.
- Not a general-purpose file editor - it only touches files with a supported
  extension and only through the safety model above.
- Not a replacement for Microsoft's own parser/formatter - it vendors and
  calls them directly rather than reimplementing M syntax.

## Development

```bash
git clone https://github.com/GopalGB/pqtools
cd pqtools
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,fabric]"
npm ci --ignore-scripts

pytest -q --cov=pqtools --cov-fail-under=80
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
