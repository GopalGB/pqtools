let
    Source = Csv.Document(File.Contents("C:\Users\Analyst\Documents\orders.csv"), [Delimiter=",", Columns=3, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"OrderID", Int64.Type}, {"CustomerID", Int64.Type}, {"Amount", type number}}),
    CustomersSource = Csv.Document(File.Contents("C:\Users\Analyst\Documents\customers.csv"), [Delimiter=",", Columns=3, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Customers Promoted Headers" = Table.PromoteHeaders(CustomersSource, [PromoteAllScalars=true]),
    #"Customers Changed Type" = Table.TransformColumnTypes(#"Customers Promoted Headers", {{"CustomerID", Int64.Type}, {"CustomerName", type text}, {"Tier", type text}}),
    #"Merged Queries" = Table.NestedJoin(#"Changed Type", {"CustomerID"}, #"Customers Changed Type", {"CustomerID"}, "CustomerData", JoinKind.LeftOuter),
    #"Expanded CustomerData" = Table.ExpandTableColumn(#"Merged Queries", "CustomerData", {"CustomerName", "Tier"}, {"CustomerName", "Tier"})
in
    #"Expanded CustomerData"
