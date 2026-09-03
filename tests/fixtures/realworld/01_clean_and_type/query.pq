let
    Source = Csv.Document(File.Contents("C:\Users\Analyst\Documents\sales.csv"), [Delimiter=",", Columns=5, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"OrderID", Int64.Type}, {"Customer", type text}, {"Region", type text}, {"Amount", type number}, {"OrderDate", type date}}),
    #"Filtered Rows" = Table.SelectRows(#"Changed Type", each [Amount] > 100),
    #"Renamed Columns" = Table.RenameColumns(#"Filtered Rows", {{"Customer", "CustomerName"}}),
    #"Added Custom" = Table.AddColumn(#"Renamed Columns", "AmountWithTax", each [Amount] * 1.08),
    #"Sorted Rows" = Table.Sort(#"Added Custom", {{"Amount", Order.Descending}})
in
    #"Sorted Rows"
