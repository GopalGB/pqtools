let
    Source = Csv.Document(File.Contents("C:\Users\Analyst\Documents\transactions.csv"), [Delimiter=",", Columns=4, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"TransactionID", Int64.Type}, {"Region", type text}, {"Category", type text}, {"Amount", type number}}),
    #"Grouped Rows" = Table.Group(#"Changed Type", {"Region"}, {{"TotalSales", each List.Sum([Amount]), type number}, {"AverageSale", each List.Average([Amount]), type number}, {"TransactionCount", each Table.RowCount(_), Int64.Type}, {"MaxSale", each List.Max([Amount]), type number}})
in
    #"Grouped Rows"
