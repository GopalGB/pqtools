# pqtools - run, lint and format Power Query M without Power BI

**pqtools runs a Power Query `.pq` query end to end - fetching its own data
from a CSV, a web URL or a SQL database - in pure Python, with no Power BI and
no Excel.** It is also a linter, a formatter and a safe renamer for M source -
`ruff` and `black` for Power Query - and it reads, edits and writes the queries
stored inside `.pbix`, `.pbit` and `.xlsx` files.

```bash
pip install pqtools
pq list  report.pbix                    # what queries are in this file?
pq eval  report.pbix --member Sales     # run one of them
pq eval  report.pq                      # or run a .pq directly
pq eval  report.pq --allow-net          # ...including its Web.Contents source
pq format report.pq                     # format it
pq check  report.pq                     # lint it in CI
```

<!-- coverage:start -->
pqtools implements **476 of the 634** functions in Microsoft's Power Query M reference (75%). Every one of the remaining 158 is recognised by name and refuses with a typed error saying which outside system it would need - never a wrong answer, and never the bare "unknown identifier" that a typo produces.
<!-- coverage:end -->

What it is for, in one line each:

- **Run a real query, source and all.** Paste a query out of Power Query's
  Advanced Editor and `pq eval` runs the whole thing - `Csv.Document`,
  `Web.Contents`, `Excel.Workbook`, `Sql.Database`, `Table.PromoteHeaders`,
  type conversion, filters, `Table.Group`, joins, pivots - and prints JSON or
  CSV. Network and database sources are off until you pass `--allow-net` /
  `--allow-db`, because the query names the destination, not you.
- **Lint and format M in CI.** `pq check` and `pq format` give Power Query the
  code-review tooling every other language already has. Exit codes are CI-shaped.
- **Test a transformation without opening Power BI.** Swap the real data source
  for a fixture with `--bind` and assert on the result from `pytest`.
- **Refactor safely.** `pq rename` renames a `let` binding across a query without
  a find-and-replace touching a string literal that happens to match.
- **Work with the queries inside a .pbix or .xlsx.** `pq list` names them,
  `pq eval --member` runs one, `pq check` lints them, and `pq format` /
  `pq rename` print the edited M, and `--write` saves it back into the file.
  `pq add` puts a brand-new query in. See
  [Working inside .xlsx and .pbix](#working-inside-xlsx-and-pbix).

> **Unofficial.** Not affiliated with or endorsed by Microsoft, and not a
> reimplementation of the Mashup Engine. What that engine still owns is **query
> folding** - rewriting a chain of steps into one remote SQL statement so the
> work happens on the server. pqtools does not fold: it pulls, then transforms
> locally. Same rows, more bytes over the wire. pandas settles it the same way
> (`read_sql` then `groupby`), and nobody calls pandas an approximation of SQL.
> See [Connectors](#connectors) below.

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

pandas reads a CSV and transforms it. `pq eval` does the same for Power Query.
A real M query is a `Source = <connector>(...)` step followed by a chain of
`Table.*` transformations, and both halves now run locally:

```bash
pq eval report.pq          # the query reads its own CSV - nothing to supply
```

**Local-file sources run natively.** `Csv.Document`, `File.Contents` and
`Text.FromBinary` are implemented in `pqtools` itself, in pure Python with no
dependencies. A query copied verbatim out of Power Query's Advanced Editor -
`Source` step included - evaluates without any help, as long as its file path
exists on this machine.

**Engine-backed sources do not, and will not.** `Sql.Database`, `Web.Contents`,
`SharePoint.*`, `Odbc.*` and friends need credentials, a network identity,
driver-specific type mapping, or query folding into a remote engine. Those are
Microsoft's Mashup Engine, this project does not reimplement it, and each one
raises a typed error naming itself. For those - and for any query carrying the
authoring machine's `C:\Users\...` path - supply the source table yourself:

```bash
pq eval report.pq --bind Source=data.csv
```

`--bind NAME=PATH` reads `PATH` into whatever `pqtools` would get from that
file, then substitutes it wherever `NAME` is used as a `let` binding. The
binding's own right-hand side - the connector call - is never evaluated, which
is what makes it irrelevant whether `pqtools` could have run it:

| `PATH` | bound value |
|---|---|
| `.csv` | list of records via `csv.DictReader`; every value stays text |
| `.json` | whatever the document holds |
| `.xlsx`, `.xlsm`, `.xlsb` | the `Excel.Workbook` navigation table, with `useHeaders` false to match `Excel.Workbook`'s own default - the query calls `Table.PromoteHeaders` itself, exactly as Power Query generates it |
| anything else | raw bytes, which is what `File.Contents` returns |

So a workbook query authored on someone else's machine runs here unchanged:

```bash
pq eval report.xlsx --member BaseData --bind Source=./local-copy.xlsx
```

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

`--bind` wins over the query's own `Source` expression, so
`Csv.Document(File.Contents("ignored.csv"))` is never called and `ignored.csv`
is never opened. `Source` is the CSV you bound, `Kept` drops the `b = "y"` row,
and `Renamed` renames `a` to `id`.

Drop the `--bind` and the query reads its own file instead. Here that file does
not exist, so it fails with a typed, exit-`2` error naming the path and the way
forward - it does not invent data and does not create the file:

```bash
$ pq eval report.pq
error M_EVAL_ERROR: File.Contents: no such file: ignored.csv - if this path came
from the machine that authored the query, bind the step's result instead:
--bind Source=<local file>
```

**Supported:** number/text/logical/null literals; the arithmetic, relational,
equality, combination (`&`) and unary operators over **every operand pair
Microsoft's M specification defines for them** - so `null` propagates through
arithmetic instead of raising, `#date + #duration` and `#datetime - #datetime`
work, `8 / 0` is `#infinity` rather than an error, binaries and logicals order,
and `&` merges records, joins a date with a time, and concatenates lists as
well as text; `and or not`; `if/then/else`; `let/in` (lazy, memoised, correctly
shadowed - a binding's expression is only ever evaluated once, and only if
something actually references it); records (`[a = 1]`) and field access
(`r[a]`, `r[a]?`, and the `each`-scoped `[a]` shorthand for `_[a]`); lists
(`{1, 2}`) and index access (`l{0}`, `l{0}?`); `each` and `(x) => ...` lambdas
and calling them; `try ... otherwise ...`; and these 557 builtins.
The list below is generated from `pqtools.evaluate.BUILTINS` and
`tests/test_readme_builtins.py` fails if the two ever disagree - so it cannot
silently drift, which a hand-maintained list can and did:

```
Text.AfterDelimiter Text.At Text.BeforeDelimiter Text.BetweenDelimiters
Text.Clean Text.Combine Text.Contains Text.End Text.EndsWith Text.From
Text.FromBinary Text.InferNumberType Text.Insert Text.Length Text.Lower
Text.Middle Text.NewGuid Text.PadEnd Text.PadStart Text.PositionOf
Text.PositionOfAny Text.Proper Text.Range Text.Remove Text.RemoveRange
Text.Repeat Text.Replace Text.ReplaceRange Text.Reverse Text.Select
Text.Split Text.SplitAny Text.Start Text.StartsWith Text.ToBinary
Text.ToList Text.Trim Text.TrimEnd Text.TrimStart Text.Type Text.Upper
Number.Abs Number.Acos Number.Asin Number.Atan Number.Atan2
Number.BitwiseAnd Number.BitwiseNot Number.BitwiseOr Number.BitwiseShiftLeft
Number.BitwiseShiftRight Number.BitwiseXor Number.Combinations Number.Cos
Number.Cosh Number.Exp Number.Factorial Number.From Number.FromText
Number.IntegerDivide Number.IsEven Number.IsNaN Number.IsOdd Number.Ln
Number.Log Number.Log10 Number.Mod Number.PI Number.Permutations
Number.Power Number.Random Number.RandomBetween Number.Round
Number.RoundAwayFromZero Number.RoundDown Number.RoundTowardZero
Number.RoundUp Number.Sign Number.Sin Number.Sinh Number.Sqrt Number.Tan
Number.Tanh Number.ToText Number.Type
Logical.From Logical.FromText Logical.ToText Logical.Type
List.Accumulate List.AllTrue List.Alternate List.AnyTrue List.Average
List.Buffer List.Combine List.Contains List.ContainsAll List.ContainsAny
List.Count List.Covariance List.DateTimeZones List.DateTimes List.Dates
List.Difference List.Distinct List.Durations List.FindText List.First
List.FirstN List.Generate List.InsertRange List.Intersect List.IsDistinct
List.IsEmpty List.Last List.LastN List.MatchesAll List.MatchesAny List.Max
List.MaxN List.Median List.Min List.MinN List.Mode List.Modes
List.NonNullCount List.Numbers List.Percentile List.PositionOf
List.PositionOfAny List.Positions List.Product List.Random List.Range
List.RemoveFirstN List.RemoveItems List.RemoveLastN List.RemoveMatchingItems
List.RemoveNulls List.RemoveRange List.Repeat List.ReplaceMatchingItems
List.ReplaceRange List.ReplaceValue List.Reverse List.Select List.Single
List.SingleOrDefault List.Skip List.Sort List.Split List.StandardDeviation
List.Sum List.Times List.Transform List.TransformMany List.Union List.Zip
Record.AddField Record.Combine Record.Field Record.FieldCount
Record.FieldNames Record.FieldOrDefault Record.FieldValues Record.FromList
Record.FromTable Record.HasFields Record.RemoveFields Record.RenameFields
Record.ReorderFields Record.SelectFields Record.ToList Record.ToTable
Record.TransformFields
Table.AddColumn Table.AddIndexColumn Table.AddJoinColumn Table.AddKey
Table.AddRankColumn Table.AggregateTableColumn Table.AlternateRows
Table.ApproximateRowCount Table.Buffer Table.Column Table.ColumnCount
Table.ColumnNames Table.ColumnsOfType Table.Combine Table.CombineColumns
Table.CombineColumnsToRecord Table.Contains Table.ContainsAll
Table.ContainsAny Table.DemoteHeaders Table.Distinct Table.DuplicateColumn
Table.ExpandListColumn Table.ExpandRecordColumn Table.ExpandTableColumn
Table.FillDown Table.FillUp Table.FindText Table.First Table.FirstN
Table.FirstValue Table.FromColumns Table.FromList Table.FromPartitions
Table.FromRecords Table.FromRows Table.FromValue Table.Group
Table.HasColumns Table.InsertRows Table.IsDistinct Table.IsEmpty Table.Join
Table.Keys Table.Last Table.LastN Table.MatchesAllRows Table.MatchesAnyRows
Table.Max Table.MaxN Table.Min Table.MinN Table.NestedJoin Table.Partition
Table.PartitionKey Table.PartitionValues Table.Pivot Table.PositionOf
Table.PositionOfAny Table.PrefixColumns Table.Profile Table.PromoteHeaders
Table.Range Table.RemoveColumns Table.RemoveFirstN Table.RemoveLastN
Table.RemoveMatchingRows Table.RemoveRows Table.RemoveRowsWithErrors
Table.RenameColumns Table.ReorderColumns Table.Repeat
Table.ReplaceErrorValues Table.ReplaceKeys Table.ReplaceMatchingRows
Table.ReplacePartitionKey Table.ReplaceRows Table.ReplaceValue
Table.ReverseRows Table.RowCount Table.Schema Table.SelectColumns
Table.SelectRows Table.SelectRowsWithErrors Table.SingleRow Table.Skip
Table.Sort Table.Split Table.SplitAt Table.SplitColumn Table.StopFolding
Table.ToColumns Table.ToList Table.ToRecords Table.ToRows
Table.TransformColumnNames Table.TransformColumnTypes Table.TransformColumns
Table.TransformRows Table.Transpose Table.Unpivot Table.UnpivotOtherColumns
Type.Is Type.IsNullable Type.NonNullable
Value.Add Value.As Value.Compare Value.Divide Value.Equals Value.Is
Value.Metadata Value.Multiply Value.NativeQuery Value.NullableEquals
Value.Optimize Value.RemoveMetadata Value.ReplaceMetadata Value.Subtract
Value.Type
Date.AddDays Date.AddMonths Date.AddQuarters Date.AddWeeks Date.AddYears
Date.Day Date.DayOfWeek Date.DayOfWeekName Date.DayOfYear Date.DaysInMonth
Date.EndOfDay Date.EndOfMonth Date.EndOfQuarter Date.EndOfWeek
Date.EndOfYear Date.From Date.FromText Date.IsInCurrentDay
Date.IsInCurrentMonth Date.IsInCurrentQuarter Date.IsInCurrentWeek
Date.IsInCurrentYear Date.IsInNextDay Date.IsInNextMonth Date.IsInNextNDays
Date.IsInNextNMonths Date.IsInNextNQuarters Date.IsInNextNWeeks
Date.IsInNextNYears Date.IsInNextQuarter Date.IsInNextWeek Date.IsInNextYear
Date.IsInPreviousDay Date.IsInPreviousMonth Date.IsInPreviousNDays
Date.IsInPreviousNMonths Date.IsInPreviousNQuarters Date.IsInPreviousNWeeks
Date.IsInPreviousNYears Date.IsInPreviousQuarter Date.IsInPreviousWeek
Date.IsInPreviousYear Date.IsInYearToDate Date.IsLeapYear Date.Month
Date.MonthName Date.QuarterOfYear Date.StartOfDay Date.StartOfMonth
Date.StartOfQuarter Date.StartOfWeek Date.StartOfYear Date.ToRecord
Date.ToText Date.Type Date.WeekOfMonth Date.WeekOfYear Date.Year
DateTime.AddZone DateTime.Date DateTime.FixedLocalNow DateTime.From
DateTime.FromFileTime DateTime.FromText DateTime.IsInCurrentHour
DateTime.IsInCurrentMinute DateTime.IsInCurrentSecond DateTime.IsInNextHour
DateTime.IsInNextMinute DateTime.IsInNextNHours DateTime.IsInNextNMinutes
DateTime.IsInNextNSeconds DateTime.IsInNextSecond DateTime.IsInPreviousHour
DateTime.IsInPreviousMinute DateTime.IsInPreviousNHours
DateTime.IsInPreviousNMinutes DateTime.IsInPreviousNSeconds
DateTime.IsInPreviousSecond DateTime.LocalNow DateTime.Time
DateTime.ToRecord DateTime.ToText DateTime.Type
DateTimeZone.FixedLocalNow DateTimeZone.FixedUtcNow DateTimeZone.From
DateTimeZone.FromFileTime DateTimeZone.FromText DateTimeZone.LocalNow
DateTimeZone.RemoveZone DateTimeZone.SwitchZone DateTimeZone.ToLocal
DateTimeZone.ToRecord DateTimeZone.ToText DateTimeZone.ToUtc
DateTimeZone.UtcNow DateTimeZone.ZoneHours DateTimeZone.ZoneMinutes
Time.EndOfHour Time.From Time.FromText Time.Hour Time.Minute Time.Second
Time.StartOfHour Time.ToRecord Time.ToText
Duration.Days Duration.From Duration.FromText Duration.Hours
Duration.Minutes Duration.Seconds Duration.ToRecord Duration.ToText
Duration.TotalDays Duration.TotalHours Duration.TotalMinutes
Duration.TotalSeconds
Csv.Document
Json.Document Json.FromValue
Lines.FromBinary Lines.FromText Lines.ToBinary Lines.ToText
File.Contents
Folder.Contents Folder.Files
Excel.Workbook
Web.Contents
OData.Feed
Sql.Database
Odbc.DataSource Odbc.Query
PostgreSQL.Database
MySQL.Database
Oracle.Database
Uri.BuildQueryString Uri.Combine Uri.EscapeDataString Uri.Parts
Binary.ApproximateLength Binary.Buffer Binary.Combine Binary.Compress
Binary.Decompress Binary.From Binary.FromList Binary.FromText
Binary.InferContentType Binary.Length Binary.Range Binary.Split
Binary.ToList Binary.ToText Binary.View Binary.ViewError Binary.ViewFunction
BinaryEncoding.Base64 BinaryEncoding.Hex
Compression.Deflate Compression.GZip Compression.None
Character.FromNumber Character.ToNumber
Guid.From
Splitter.SplitByNothing Splitter.SplitTextByAnyDelimiter
Splitter.SplitTextByCharacterTransition Splitter.SplitTextByDelimiter
Splitter.SplitTextByEachDelimiter Splitter.SplitTextByLengths
Splitter.SplitTextByPositions Splitter.SplitTextByRanges
Splitter.SplitTextByRepeatedLengths Splitter.SplitTextByWhitespace
Combiner.CombineTextByDelimiter Combiner.CombineTextByEachDelimiter
Combiner.CombineTextByLengths Combiner.CombineTextByPositions
Combiner.CombineTextByRanges
Replacer.ReplaceText Replacer.ReplaceValue
Comparer.FromCulture Comparer.Ordinal Comparer.OrdinalIgnoreCase
Precision.Decimal Precision.Double
Order.Ascending Order.Descending
JoinKind.FullOuter JoinKind.Inner JoinKind.LeftAnti JoinKind.LeftOuter
JoinKind.LeftSemi JoinKind.RightAnti JoinKind.RightOuter JoinKind.RightSemi
MissingField.Error MissingField.Ignore MissingField.UseNull
Occurrence.All Occurrence.First Occurrence.Last
RoundingMode.AwayFromZero RoundingMode.Down RoundingMode.ToEven
RoundingMode.TowardZero RoundingMode.Up
ExtraValues.Error ExtraValues.Ignore ExtraValues.List
QuoteStyle.Csv QuoteStyle.None
TextEncoding.Ascii TextEncoding.BigEndianUnicode TextEncoding.Unicode
TextEncoding.Utf16 TextEncoding.Utf8 TextEncoding.Windows
GroupKind.Global GroupKind.Local
Int8.Type
Int16.Type
Int32.Type
Int64.Type
Single.Type
Double.Type
Decimal.Type
Currency.Type
Byte.Type
Any.Type
Expression.Constant Expression.Evaluate Expression.Identifier
Day.Friday Day.Monday Day.Saturday Day.Sunday Day.Thursday Day.Tuesday
Day.Wednesday Percentage.Type PercentileMode.ExcelExc
PercentileMode.ExcelInc PercentileMode.SqlCont PercentileMode.SqlDisc
RankKind.Competition RankKind.Dense RankKind.Ordinal
RelativePosition.FromEnd RelativePosition.FromStart
#binary #date #datetime #datetimezone #duration #table #time
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
includes: any engine-backed connector (`Web.Contents`, `Sql.Database`,
`Excel.Workbook`, `SharePoint.*`, `Odbc.*`, `Folder.*` - the error
names the construct and says it needs Fabric or PQTest, the two hosts that can
actually run it; `Csv.Document` and `File.Contents` are *not* in this list, they
run natively);
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

`pq eval` does not replace Power Query. It runs local-file sources and the
whole transformation chain; for anything that needs a live engine it stops and
says so, rather than guessing at what that engine would have returned.

## Connectors

A query's `Source` step names where the data comes from. pqtools runs those
steps rather than making you replace them.

| Source | Status | Needs |
|---|---|---|
| `Csv.Document`, `File.Contents`, `Json.Document`, `Lines.*` | works | nothing |
| `Folder.Files`, `Folder.Contents` | works | nothing |
| `Excel.Workbook` | works | `pip install 'pqtools[excel]'` |
| `Web.Contents`, `OData.Feed` | works | `--allow-net` |
| `Sql.Database`, `Odbc.Query`, `Odbc.DataSource` | works | `--allow-db` + `pqtools[sql]` |
| `PostgreSQL.Database` | works | `--allow-db` + `pqtools[postgres]` |
| `MySQL.Database` | works | `--allow-db` + `pqtools[mysql]` |
| `Oracle.Database` | works | `--allow-db` + `pqtools[oracle]` |
| `SharePoint.*` | refuses, by name | - |

Drivers are optional extras, the way pandas keeps psycopg and SQLAlchemy
optional. `pip install 'pqtools[all]'` gets the lot.

```bash
# the pandas equivalent of pd.read_csv(url).groupby(...)
pq eval titanic.pq --allow-net --format csv
```

```
let
    Source   = Csv.Document(Web.Contents("https://.../titanic.csv")),
    Promoted = Table.PromoteHeaders(Source),
    Adults   = Table.SelectRows(Promoted, each [Age] <> "" and
                                Number.From([Age]) >= 18),
    Grouped  = Table.Group(Adults, {"Pclass"},
                 {{"Adults", each Table.RowCount(_), Int64.Type}})
in
    Grouped
```

**Every function Power Query has, pqtools answers for.** Not by implementing
all of them - by never leaving you guessing which case you are in. `pqtools`
carries Microsoft's whole documented function list, so a name it does not
implement still gets a typed error saying *why*, and a name that is not M at
all still reads as a typo:

```
Salesforce.Data(...)      Salesforce.Data is a data-source connector. It needs
                          vendor credentials, an OAuth identity, or a
                          proprietary driver that pqtools does not ship.
                          Supply its result with --bind NAME=PATH and pqtools
                          will run every step after it
Table.FuzzyJoin(...)      does approximate matching. Microsoft does not
                          document the similarity algorithm precisely enough
                          to reproduce, and an approximate join returns the
                          WRONG ROWS rather than an error
Tabel.RowCount(...)       unknown identifier: Tabel.RowCount
```

Those are three different problems with three different fixes, and before
0.10.0 all three said "unknown identifier". The list is generated from
Microsoft's reference by `scripts/sync_m_catalog.py`, so it cannot drift the
way the previous hand-kept version did - it had five connector names on file
while Microsoft documented eighty-five.

The same check runs in reverse. `tests/test_catalog.py` fails if pqtools
registers a name Power Query does not have, because that is the one failure
worse than a missing function: your query passes here and then fails in Power
Query. It found two - `Uri.UnescapeDataString` and `Table.SelectDuplicates`,
both invented by this project, both removed in 0.10.0.

### Why network and database access are off by default

pandas never has to ask this question: you type `read_sql` yourself, so the
destination is always yours. Here **the query names the destination**, and the
query often came from a workbook somebody else wrote. Running it unasked would
make `pq eval report.pbix` an SSRF primitive and, with a URL built by string
concatenation, an exfiltration one.

So:

- `--allow-net` permits HTTP(S). `--allow-db` permits database connections.
- Even with `--allow-net`, **loopback, private and link-local addresses stay
  blocked**. `169.254.169.254` is the cloud instance-metadata endpoint on AWS,
  GCP and Azure; reading it hands over the host's credentials. `--allow-private-net`
  lifts that when you actually mean to reach an intranet.
- `--allow-host HOST` narrows a run to named hosts.
- Non-HTTP schemes never pass the network gate, so `file://` cannot turn a
  fetch into a local-file read.
- `try ... otherwise` and `try ... catch` **never swallow a blocked connector**.
  A permission decision must not arrive at the caller disguised as a value.

From Python the same policy is an argument:

```python
from pqtools import evaluate
from pqtools.io import IOPolicy

evaluate(source, io=IOPolicy(allow_net=True, hosts=frozenset({"api.example.com"})))
```

Credentials come from the environment (`PQTOOLS_SQL_USER`, `PQTOOLS_SQL_PASSWORD`,
`PQTOOLS_PG_*`, `PQTOOLS_MYSQL_*`, `PQTOOLS_ORACLE_*`) rather than from the M
file, because a password written into a query gets committed.

### What is still not folded

Power Query pushes `Table.SelectRows`/`Table.Group` down into the remote engine
as SQL. pqtools does not. A query that would fold in Power BI still returns the
same rows here; it just fetches more of them first. If a table is too large to
pull, put the filter in a `[Query="SELECT ..."]` option and let the server do it.

## Frequently asked

### Can I run Power Query without Power BI or Excel?

Yes. `pq eval report.pq` runs the whole query - the `Source` step included.
Local files work with no flags; `Web.Contents`, `Sql.Database`, `Odbc.*`,
`PostgreSQL`/`MySQL`/`Oracle` and `Excel.Workbook` work behind `--allow-net` /
`--allow-db` (see [Connectors](#connectors)). `SharePoint.*` still refuses,
with a typed error naming itself.

What does not happen is **query folding** - your filters run here, not on the
server. Same rows, more bytes fetched.

### Is there a linter or formatter for Power Query M?

That is what `pq check` and `pq format` are. Formatting is delegated to
Microsoft's own `@microsoft/powerquery-formatter`, and parsing to Microsoft's
`@microsoft/powerquery-parser`, both pinned - so the syntax pqtools accepts is
the syntax Power Query accepts, not a reimplementation that drifts.

### Is pqtools the pandas of Power Query?

That is the goal, and it now holds on both halves.

**Transformation:** M builtins covering `Table.*` aggregation and joins (all
six `JoinKind` values), pivot/unpivot, the type system, date/time handling.

**Getting the data:** `read_csv` has `Csv.Document`; `read_excel` has
`Excel.Workbook`; `read_sql` has `Sql.Database` / `Odbc.Query` /
`PostgreSQL.Database`; a URL has `Web.Contents`. Drivers are optional extras
exactly as pandas keeps psycopg optional.

Two honest differences. pandas does not fold queries into the database either,
so that is parity, not a gap - but Power Query *does*, so a query that folds in
Power BI moves more bytes here. And pqtools asks permission before reaching the
network, because in pandas you type the URL and here the query supplies it.

### How do I test a Power Query transformation?

Bind the source step to a fixture and assert on the output:

```python
from pqtools import evaluate

query = """
let
    Source = Csv.Document(File.Contents("live.csv")),
    Renamed = Table.RenameColumns(Source, {{"a", "id"}})
in
    Renamed
"""

# --bind's Python equivalent: Source is substituted, so Csv.Document and
# File.Contents are never called and live.csv is never opened.
assert evaluate(query, bindings={"Source": [{"a": 1}]}) == [{"id": 1}]
```

### Can it read the queries inside a .pbix or .xlsx file?

Yes - `pq check report.pbix` and `pq format book.xlsx` extract and analyse the M
already stored in the container, and `--write` saves changes back into it.
See [Working inside .xlsx and .pbix](#working-inside-xlsx-and-pbix).

### Does it send my data anywhere?

No telemetry, ever, and no network call you did not ask for. The runtime makes
an outbound request only when a query contains `Web.Contents`/`OData.Feed`
**and** you passed `--allow-net`, and it will still refuse loopback, private
and link-local addresses unless you also pass `--allow-private-net`. Default
behaviour with no flags is exactly what it was: nothing leaves the machine.
See [Safety model](#safety-model).

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
- **Outbound access is default-deny.** A query's connectors do not run until
  the caller passes `--allow-net` / `--allow-db`, because the M source names
  the destination and that source is often somebody else's file. Even then,
  loopback, private, link-local, reserved and multicast addresses are refused
  unless `--allow-private-net` is also given - `169.254.169.254` is the cloud
  instance-metadata endpoint, and a read of it hands over the host's
  credentials. Hostnames are resolved before the check, so one DNS record
  pointing inward does not slip past; `--allow-host` is the airtight version.
- **Only `http` and `https` pass the network gate**, so `file://` cannot turn
  a permitted fetch into an arbitrary local-file read.
- **A blocked connector is never swallowed by `try`.** Neither
  `try ... otherwise` nor `try ... catch` catches a policy refusal, so a
  permission decision cannot reach the caller disguised as a fallback value.
- **Credentials come from the environment**, not from the M file
  (`PQTOOLS_SQL_USER`, `PQTOOLS_PG_PASSWORD`, ...), because a password typed
  into a query gets committed.
- **`Expression.Evaluate` is deliberately not implemented.** It would be an
  eval sink reachable from file content.

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

**Listing and adding:**

```bash
pq list report.pbix                      # name every query inside
pq eval report.pbix --member Sales       # run one of them
pq add  book.xlsx --name "Top Colors" \
        --source 'let S = ... in S'      # preview a new query
pq add  book.xlsx --name "Top Colors" \
        --source 'let S = ... in S' --write   # ...and save it in
```

**Writing back:** `pq format`, `pq rename`, `pq replace-source` and `pq add`
print the result by default and save it with `--write`. A `--write` on a
container copies the original to `<file>.bak` first, because this is the one
command here that can destroy its input and the damage would surface in Excel
rather than in this process.

The rebuild changes only the M source: every other zip member and all three
opaque DataMashup segments are carried through byte-for-byte, and the result
is re-read from scratch and verified before anything reaches disk. It has been
validated against synthesized fixtures, a real Power BI `.pbix`, and real
Excel-authored `.xlsx` workbooks - on those, **openpyxl (an independent xlsx
parser) opens the rewritten file to the same sheets and cell values as the
original**, which is the check that matters, since it is not this code
agreeing with itself.

**Residual risk, stated plainly:** Excel itself has not opened a rewritten
workbook, because Excel was not installed where this was validated. The checks
above are the strongest available substitute, not a replacement - hence the
`.bak`.

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
