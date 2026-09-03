let
    Source = Csv.Document(File.Contents("C:\Users\Analyst\Documents\category_sales.csv"), [Delimiter=",", Columns=5, Encoding=1252, QuoteStyle=QuoteStyle.None]),
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"Region", type text}, {"OrderDate", type date}, {"Electronics", type number}, {"Clothing", type number}, {"Home", type number}}),
    #"Unpivoted Other Columns" = Table.UnpivotOtherColumns(#"Changed Type", {"Region", "OrderDate"}, "Category", "Revenue"),
    #"Filtered Rows" = Table.SelectRows(#"Unpivoted Other Columns", each [Revenue] <> 0),
    #"Inserted Year" = Table.AddColumn(#"Filtered Rows", "Year", each Date.Year([OrderDate]), Int64.Type),
    #"Inserted Month Name" = Table.AddColumn(#"Inserted Year", "Month Name", each Date.MonthName([OrderDate]), type text)
in
    #"Inserted Month Name"
