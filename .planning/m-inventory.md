# Power Query M standard library inventory

> **HISTORICAL RECORD - a snapshot, not the current registry.**
>
> This inventory was built on 2026-09-05 against a working copy at the old
> `app-development/projects/mquery-toolkit` path, which no longer exists. The
> registry has changed since, and the command quoted below will not run as
> written.
>
> It is kept because the fetch provenance it records is useful. **For the
> current counts and what they do and do not mean, read
> [SUPPORT-MATRIX.md](../SUPPORT-MATRIX.md)**, which is enforced by
> `tests/test_support_matrix.py` rather than being prose.

Grounded inventory built by fetching each Microsoft Learn category page listed below with
WebFetch (fetched 2026-09-05) and cross-referencing against the live `BUILTINS` registry:

```
cd "/Users/gopalmacbook/Desktop/Max HQ/app-development/projects/mquery-toolkit" && .venv/bin/python -c "from pqtools.evaluate import BUILTINS; print('\n'.join(sorted(BUILTINS)))"
```

`table-functions` and `list-functions` are already covered elsewhere and were NOT re-fetched
here (per task instructions).

Every function name below came from a page actually fetched via WebFetch this session. No
name was filled in from memory or inference.

## accessing-data-functions
source: https://learn.microsoft.com/en-us/powerquery-m/accessing-data-functions
total documented: 85
internal-use-only (skip these): none (note: `Cdm.Contents` is flagged on the page as "unavailable because it requires .NET 4.5" — a different caveat than "intended for internal use only" — so it is still counted as documented/missing, not skipped)
ALREADY IMPLEMENTED (15): Csv.Document, Excel.Workbook, File.Contents, Folder.Contents, Folder.Files, Json.Document, Json.FromValue, MySQL.Database, OData.Feed, Odbc.DataSource, Odbc.Query, Oracle.Database, PostgreSQL.Database, Sql.Database, Web.Contents
MISSING (70): AccessControlEntry.ConditionToIdentities, Access.Database, ActiveDirectory.Domains, AdobeAnalytics.Cubes, AdoDotNet.DataSource, AdoDotNet.Query, AnalysisServices.Database, AnalysisServices.Databases, AzureStorage.BlobContents, AzureStorage.Blobs, AzureStorage.DataLake, AzureStorage.DataLakeContents, AzureStorage.Tables, Cdm.Contents, Cube.AddAndExpandDimensionColumn, Cube.AddMeasureColumn, Cube.ApplyParameter, Cube.AttributeMemberId, Cube.AttributeMemberProperty, Cube.CollapseAndRemoveColumns, Cube.Dimensions, Cube.DisplayFolders, Cube.MeasureProperties, Cube.MeasureProperty, Cube.Measures, Cube.Parameters, Cube.Properties, Cube.PropertyKey, Cube.ReplaceDimensions, Cube.Transform, DB2.Database, DeltaLake.Metadata, DeltaLake.Table, Essbase.Cubes, Excel.CurrentWorkbook, Exchange.Contents, FabricAI.Prompt, GoogleAnalytics.Accounts, Hdfs.Contents, Hdfs.Files, HdInsight.Containers, HdInsight.Contents, HdInsight.Files, Html.Table, Identity.From, Identity.IsMemberOf, IdentityProvider.Default, Informix.Database, Odbc.InferOptions, OleDb.DataSource, OleDb.Query, Pdf.Tables, RData.FromBinary, Salesforce.Data, Salesforce.Reports, SapBusinessWarehouse.Cubes, SapHana.Database, SharePoint.Contents, SharePoint.Files, SharePoint.Tables, Soda.Feed, Sql.Databases, Sybase.Database, Teradata.Database, WebAction.Request, Web.BrowserContents, Web.Headers, Web.Page, Xml.Document, Xml.Tables

## binary-functions
source: https://learn.microsoft.com/en-us/powerquery-m/binary-functions
total documented: 41
internal-use-only (skip these): none
ALREADY IMPLEMENTED (7): Binary.Buffer, Binary.Combine, Binary.Decompress, Binary.FromText, Binary.Length, Binary.ToText, #binary
MISSING (34): BinaryFormat.7BitEncodedSignedInteger, BinaryFormat.7BitEncodedUnsignedInteger, BinaryFormat.Binary, BinaryFormat.Byte, BinaryFormat.Choice, BinaryFormat.Decimal, BinaryFormat.Double, BinaryFormat.Group, BinaryFormat.Length, BinaryFormat.List, BinaryFormat.Null, BinaryFormat.Record, BinaryFormat.SignedInteger16, BinaryFormat.SignedInteger32, BinaryFormat.SignedInteger64, BinaryFormat.Single, BinaryFormat.Text, BinaryFormat.Transform, BinaryFormat.UnsignedInteger16, BinaryFormat.UnsignedInteger32, BinaryFormat.UnsignedInteger64, BinaryFormat.ByteOrder, Table.PartitionValues, Binary.ApproximateLength, Binary.Compress, Binary.From, Binary.FromList, Binary.InferContentType, Binary.Range, Binary.Split, Binary.ToList, Binary.View, Binary.ViewError, Binary.ViewFunction

(Note: `Table.PartitionValues` is listed on this page under "Controlling byte order" — recorded verbatim under this category per the task's instruction to record functions exactly as they appear on the fetched page, even when they belong to another namespace.)

## combiner-functions
source: https://learn.microsoft.com/en-us/powerquery-m/combiner-functions
total documented: 5
internal-use-only (skip these): none
ALREADY IMPLEMENTED (4): Combiner.CombineTextByDelimiter, Combiner.CombineTextByEachDelimiter, Combiner.CombineTextByLengths, Combiner.CombineTextByPositions
MISSING (1): Combiner.CombineTextByRanges

## comparer-functions
source: https://learn.microsoft.com/en-us/powerquery-m/comparer-functions
total documented: 4
internal-use-only (skip these): none
ALREADY IMPLEMENTED (3): Comparer.FromCulture, Comparer.Ordinal, Comparer.OrdinalIgnoreCase
MISSING (1): Comparer.Equals

## date-functions
source: https://learn.microsoft.com/en-us/powerquery-m/date-functions
total documented: 58
internal-use-only (skip these): none
ALREADY IMPLEMENTED (25): Date.AddDays, Date.AddMonths, Date.AddWeeks, Date.AddYears, Date.Day, Date.DayOfWeek, Date.DayOfWeekName, Date.DayOfYear, Date.EndOfMonth, Date.EndOfWeek, Date.EndOfYear, Date.From, Date.FromText, Date.IsInCurrentMonth, Date.IsInCurrentYear, Date.Month, Date.MonthName, Date.QuarterOfYear, Date.StartOfMonth, Date.StartOfWeek, Date.StartOfYear, Date.ToText, Date.WeekOfYear, Date.Year, #date
MISSING (33): Date.AddQuarters, Date.DaysInMonth, Date.EndOfDay, Date.EndOfQuarter, Date.IsInCurrentDay, Date.IsInCurrentQuarter, Date.IsInCurrentWeek, Date.IsInNextDay, Date.IsInNextMonth, Date.IsInNextNDays, Date.IsInNextNMonths, Date.IsInNextNQuarters, Date.IsInNextNWeeks, Date.IsInNextNYears, Date.IsInNextQuarter, Date.IsInNextWeek, Date.IsInNextYear, Date.IsInPreviousDay, Date.IsInPreviousMonth, Date.IsInPreviousNDays, Date.IsInPreviousNMonths, Date.IsInPreviousNQuarters, Date.IsInPreviousNWeeks, Date.IsInPreviousNYears, Date.IsInPreviousQuarter, Date.IsInPreviousWeek, Date.IsInPreviousYear, Date.IsInYearToDate, Date.IsLeapYear, Date.StartOfDay, Date.StartOfQuarter, Date.ToRecord, Date.WeekOfMonth

## datetime-functions
source: https://learn.microsoft.com/en-us/powerquery-m/datetime-functions
total documented: 26
internal-use-only (skip these): none
ALREADY IMPLEMENTED (9): DateTime.AddZone, DateTime.Date, DateTime.FixedLocalNow, DateTime.From, DateTime.FromText, DateTime.LocalNow, DateTime.Time, DateTime.ToText, #datetime
MISSING (17): DateTime.FromFileTime, DateTime.IsInCurrentHour, DateTime.IsInCurrentMinute, DateTime.IsInCurrentSecond, DateTime.IsInNextHour, DateTime.IsInNextMinute, DateTime.IsInNextNHours, DateTime.IsInNextNMinutes, DateTime.IsInNextNSeconds, DateTime.IsInNextSecond, DateTime.IsInPreviousHour, DateTime.IsInPreviousMinute, DateTime.IsInPreviousNHours, DateTime.IsInPreviousNMinutes, DateTime.IsInPreviousNSeconds, DateTime.IsInPreviousSecond, DateTime.ToRecord

## datetimezone-functions
source: https://learn.microsoft.com/en-us/powerquery-m/datetimezone-functions
total documented: 16
internal-use-only (skip these): none
ALREADY IMPLEMENTED (1): #datetimezone
MISSING (15): DateTimeZone.FixedLocalNow, DateTimeZone.FixedUtcNow, DateTimeZone.From, DateTimeZone.FromFileTime, DateTimeZone.FromText, DateTimeZone.LocalNow, DateTimeZone.RemoveZone, DateTimeZone.SwitchZone, DateTimeZone.ToLocal, DateTimeZone.ToRecord, DateTimeZone.ToText, DateTimeZone.ToUtc, DateTimeZone.UtcNow, DateTimeZone.ZoneHours, DateTimeZone.ZoneMinutes

## duration-functions
source: https://learn.microsoft.com/en-us/powerquery-m/duration-functions
total documented: 13
internal-use-only (skip these): none
ALREADY IMPLEMENTED (12): Duration.Days, Duration.From, Duration.FromText, Duration.Hours, Duration.Minutes, Duration.Seconds, Duration.ToText, Duration.TotalDays, Duration.TotalHours, Duration.TotalMinutes, Duration.TotalSeconds, #duration
MISSING (1): Duration.ToRecord

## error-handling
source: https://learn.microsoft.com/en-us/powerquery-m/error-handling
total documented: 4
internal-use-only (skip these): none
ALREADY IMPLEMENTED (0): none
MISSING (4): Diagnostics.ActivityId, Diagnostics.CorrelationId, Diagnostics.Trace, Error.Record

## expression-functions
source: https://learn.microsoft.com/en-us/powerquery-m/expression-functions
total documented: 3
internal-use-only (skip these): none
ALREADY IMPLEMENTED (0): none
MISSING (3): Expression.Constant, Expression.Evaluate, Expression.Identifier

## function-values
source: https://learn.microsoft.com/en-us/powerquery-m/function-values
total documented: 6
internal-use-only (skip these): Function.InvokeWithErrorContext
ALREADY IMPLEMENTED (0): none
MISSING (5): Function.From, Function.Invoke, Function.InvokeAfter, Function.IsDataSource, Function.ScalarVector

## lines-functions
source: https://learn.microsoft.com/en-us/powerquery-m/lines-functions
total documented: 4
internal-use-only (skip these): none
ALREADY IMPLEMENTED (4): Lines.FromBinary, Lines.FromText, Lines.ToBinary, Lines.ToText
MISSING (0): none

## logical-functions
source: https://learn.microsoft.com/en-us/powerquery-m/logical-functions
total documented: 3
internal-use-only (skip these): none
ALREADY IMPLEMENTED (2): Logical.From, Logical.FromText
MISSING (1): Logical.ToText

## number-functions
source: https://learn.microsoft.com/en-us/powerquery-m/number-functions
total documented: 52
internal-use-only (skip these): none
ALREADY IMPLEMENTED (27): Number.Abs, Number.BitwiseAnd, Number.BitwiseOr, Number.BitwiseXor, Number.Exp, Number.Factorial, Number.From, Number.FromText, Number.IntegerDivide, Number.IsEven, Number.IsNaN, Number.IsOdd, Number.Ln, Number.Log, Number.Log10, Number.Mod, Number.Power, Number.Random, Number.RandomBetween, Number.Round, Number.RoundAwayFromZero, Number.RoundDown, Number.RoundTowardZero, Number.RoundUp, Number.Sign, Number.Sqrt, Number.ToText
MISSING (25): Byte.From, Currency.From, Decimal.From, Double.From, Int8.From, Int16.From, Int32.From, Int64.From, Percentage.From, Single.From, Number.Combinations, Number.Permutations, Number.Acos, Number.Asin, Number.Atan, Number.Atan2, Number.Cos, Number.Cosh, Number.Sin, Number.Sinh, Number.Tan, Number.Tanh, Number.BitwiseNot, Number.BitwiseShiftLeft, Number.BitwiseShiftRight

## record-functions
source: https://learn.microsoft.com/en-us/powerquery-m/record-functions
total documented: 23
internal-use-only (skip these): none
ALREADY IMPLEMENTED (16): Record.AddField, Record.Combine, Record.Field, Record.FieldCount, Record.FieldNames, Record.FieldOrDefault, Record.FieldValues, Record.FromList, Record.HasFields, Record.RemoveFields, Record.RenameFields, Record.ReorderFields, Record.SelectFields, Record.ToList, Record.ToTable, Record.TransformFields
MISSING (7): Geography.FromWellKnownText, Geography.ToWellKnownText, GeographyPoint.From, Geometry.FromWellKnownText, Geometry.ToWellKnownText, GeometryPoint.From, Record.FromTable

## replacer-functions
source: https://learn.microsoft.com/en-us/powerquery-m/replacer-functions
total documented: 2
internal-use-only (skip these): none
ALREADY IMPLEMENTED (2): Replacer.ReplaceText, Replacer.ReplaceValue
MISSING (0): none

## splitter-functions
source: https://learn.microsoft.com/en-us/powerquery-m/splitter-functions
total documented: 10
internal-use-only (skip these): none
ALREADY IMPLEMENTED (5): Splitter.SplitTextByCharacterTransition, Splitter.SplitTextByDelimiter, Splitter.SplitTextByEachDelimiter, Splitter.SplitTextByLengths, Splitter.SplitTextByPositions
MISSING (5): Splitter.SplitByNothing, Splitter.SplitTextByAnyDelimiter, Splitter.SplitTextByRanges, Splitter.SplitTextByRepeatedLengths, Splitter.SplitTextByWhitespace

## text-functions
source: https://learn.microsoft.com/en-us/powerquery-m/text-functions
total documented: 46
note: the category page lists 45. Text.Format is the 46th - it has its own
reference page (powerquery-m/text-format, HTTP 200, with two worked
examples) but is absent from the category index, so a scrape of the index
alone drops it. It was found by the doc-example corpus, not by this
inventory, and is added here by hand so the catalog stops calling a real
Power Query function a typo.
internal-use-only (skip these): none
ALREADY IMPLEMENTED (40): Text.Format, Text.Length, Character.FromNumber, Character.ToNumber, Guid.From, Json.FromValue, Text.From, Text.FromBinary, Text.NewGuid, Text.ToList, Text.At, Text.Middle, Text.Start, Text.End, Text.Insert, Text.Remove, Text.Replace, Text.Select, Text.Contains, Text.EndsWith, Text.PositionOf, Text.PositionOfAny, Text.StartsWith, Text.AfterDelimiter, Text.BeforeDelimiter, Text.BetweenDelimiters, Text.Clean, Text.Combine, Text.Lower, Text.PadEnd, Text.PadStart, Text.Proper, Text.Repeat, Text.Reverse, Text.Split, Text.SplitAny, Text.Trim, Text.TrimEnd, Text.TrimStart, Text.Upper
MISSING (6): Text.InferNumberType, Text.ToBinary, Value.FromText, Text.Range, Text.RemoveRange, Text.ReplaceRange

## time-functions
source: https://learn.microsoft.com/en-us/powerquery-m/time-functions
total documented: 10
internal-use-only (skip these): none
ALREADY IMPLEMENTED (7): Time.From, Time.FromText, Time.Hour, Time.Minute, Time.Second, Time.ToText, #time
MISSING (3): Time.EndOfHour, Time.StartOfHour, Time.ToRecord

## type-functions
source: https://learn.microsoft.com/en-us/powerquery-m/type-functions
total documented: 24
internal-use-only (skip these): none
ALREADY IMPLEMENTED (1): Type.Is
MISSING (23): Type.AddTableKey, Type.ClosedRecord, Type.Facets, Type.ForFunction, Type.ForRecord, Type.FunctionParameters, Type.FunctionRequiredParameters, Type.FunctionReturn, Type.IsNullable, Type.IsOpenRecord, Type.ListItem, Type.NonNullable, Type.OpenRecord, Type.RecordFields, Type.ReplaceFacets, Type.ReplaceTableKeys, Type.ReplaceTablePartitionKey, Type.TableColumn, Type.TableKeys, Type.TablePartitionKey, Type.TableRow, Type.TableSchema, Type.Union

## uri-functions
source: https://learn.microsoft.com/en-us/powerquery-m/uri-functions
total documented: 4
internal-use-only (skip these): none
ALREADY IMPLEMENTED (4): Uri.BuildQueryString, Uri.Combine, Uri.EscapeDataString, Uri.Parts
MISSING (0): none

(Note: `Uri.UnescapeDataString` is present in the live BUILTINS registry but does NOT appear on this fetched page's tables — it may be documented elsewhere. Not counted here since it wasn't found on this page.)

## value-functions
source: https://learn.microsoft.com/en-us/powerquery-m/value-functions
total documented: 36 (24 excluding internal-use-only)
internal-use-only (skip these): Action.WithErrorContext, DirectQueryCapabilities.From, Excel.ShapeTable, Progress.DataSourceProgress, SqlExpression.SchemaFrom, SqlExpression.ToExpression, Value.Firewall, Value.ViewError, Value.ViewFunction, Graph.Nodes, Value.Lineage, Value.Traits
ALREADY IMPLEMENTED (4): Value.Compare, Value.Equals, Value.Is, Value.Type
MISSING (20): Value.Alternates, Value.Expression, Value.VersionIdentity, Value.Versions, Value.NativeQuery, Value.NullableEquals, Value.Optimize, Value.Add, Value.Divide, Value.Multiply, Value.Subtract, Value.As, Value.ReplaceType, Embedded.Value, Module.Versions, Variable.Value, Variable.ValueOrDefault, Value.Metadata, Value.RemoveMetadata, Value.ReplaceMetadata

## TOTALS
documented (excluding internal-use-only): 461
implemented: 187
missing: 274

(Scope: the 22 categories fetched in this pass only — accessing-data, binary, combiner,
comparer, date, datetime, datetimezone, duration, error-handling, expression,
function-values, lines, logical, number, record, replacer, splitter, text, time, type,
uri, value. table-functions and list-functions were already inventoried previously and
are excluded from this total.)


## table-functions
source: https://learn.microsoft.com/en-us/powerquery-m/table-functions
Added by the main session (fetched 2026-09-05), not the inventory agent, which
was told to skip this category. Microsoft's four "intended for internal use
only" entries (Table.WithErrorContext, Table.ConformToPageReader,
Table.FilterWithDataTable, Table.ReplaceRelationshipIdentity) are excluded.
total documented: 114
internal-use-only (skip these): Table.WithErrorContext, Table.ConformToPageReader, Table.FilterWithDataTable, Table.ReplaceRelationshipIdentity
MISSING (114): #table, ItemExpression.From, RowExpression.Column, RowExpression.From, Table.FromColumns, Table.FromList, Table.FromRecords, Table.FromRows, Table.FromValue, Table.View, Table.ViewError, Table.ViewFunction, Table.ToColumns, Table.ToList, Table.ToRecords, Table.ToRows, Table.ApproximateRowCount, Table.ColumnCount, Table.IsEmpty, Table.PartitionValues, Table.Profile, Table.RowCount, Table.Schema, Tables.GetRelationships, Table.AlternateRows, Table.Combine, Table.FindText, Table.First, Table.FirstN, Table.FirstValue, Table.FromPartitions, Table.InsertRows, Table.Last, Table.LastN, Table.MatchesAllRows, Table.MatchesAnyRows, Table.Partition, Table.Range, Table.RemoveFirstN, Table.RemoveLastN, Table.RemoveRows, Table.RemoveRowsWithErrors, Table.Repeat, Table.ReplaceRows, Table.ReverseRows, Table.SelectRows, Table.SelectRowsWithErrors, Table.SingleRow, Table.Skip, Table.SplitAt, Table.Column, Table.ColumnNames, Table.ColumnsOfType, Table.DemoteHeaders, Table.DuplicateColumn, Table.HasColumns, Table.Pivot, Table.PrefixColumns, Table.PromoteHeaders, Table.RemoveColumns, Table.ReorderColumns, Table.RenameColumns, Table.SelectColumns, Table.TransformColumnNames, Table.Unpivot, Table.UnpivotOtherColumns, Table.AddColumn, Table.AddFuzzyClusterColumn, Table.AddIndexColumn, Table.AddJoinColumn, Table.AddKey, Table.AggregateTableColumn, Table.CombineColumns, Table.CombineColumnsToRecord, Table.ExpandListColumn, Table.ExpandRecordColumn, Table.ExpandTableColumn, Table.FillDown, Table.FillUp, Table.FuzzyGroup, Table.FuzzyJoin, Table.FuzzyNestedJoin, Table.Group, Table.Join, Table.Keys, Table.NestedJoin, Table.PartitionKey, Table.ReplaceErrorValues, Table.ReplaceKeys, Table.ReplacePartitionKey, Table.ReplaceValue, Table.Split, Table.SplitColumn, Table.TransformColumns, Table.TransformColumnTypes, Table.TransformRows, Table.Transpose, Table.Contains, Table.ContainsAll, Table.ContainsAny, Table.Distinct, Table.IsDistinct, Table.PositionOf, Table.PositionOfAny, Table.RemoveMatchingRows, Table.ReplaceMatchingRows, Table.AddRankColumn, Table.Max, Table.MaxN, Table.Min, Table.MinN, Table.Sort, Table.Buffer, Table.StopFolding

## list-functions
source: https://learn.microsoft.com/en-us/powerquery-m/list-functions
Added by the main session (fetched 2026-09-05). List.ConformToPageReader is
excluded as internal-use-only.
total documented: 70
internal-use-only (skip these): List.ConformToPageReader
MISSING (70): List.Count, List.IsEmpty, List.NonNullCount, List.Alternate, List.Buffer, List.Distinct, List.FindText, List.First, List.FirstN, List.InsertRange, List.IsDistinct, List.Last, List.LastN, List.MatchesAll, List.MatchesAny, List.Positions, List.Range, List.Select, List.Single, List.SingleOrDefault, List.Skip, List.Accumulate, List.Combine, List.RemoveFirstN, List.RemoveItems, List.RemoveLastN, List.RemoveMatchingItems, List.RemoveNulls, List.RemoveRange, List.Repeat, List.ReplaceMatchingItems, List.ReplaceRange, List.ReplaceValue, List.Reverse, List.Split, List.Transform, List.TransformMany, List.Zip, List.AllTrue, List.AnyTrue, List.Contains, List.ContainsAll, List.ContainsAny, List.PositionOf, List.PositionOfAny, List.Difference, List.Intersect, List.Union, List.Max, List.MaxN, List.Median, List.Min, List.MinN, List.Sort, List.Percentile, List.Average, List.Mode, List.Modes, List.StandardDeviation, List.Sum, List.Covariance, List.Product, List.Dates, List.DateTimes, List.DateTimeZones, List.Durations, List.Generate, List.Numbers, List.Random, List.Times
