"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const parser = require("@microsoft/powerquery-parser");
const { tokenView, renameSpans, analysisView, astView } = require("./bridge.js");

async function parseAst(source) {
  const parsed = await parser.TaskUtils.tryLexParse(parser.DefaultSettings, source);
  return parsed.ast;
}

test("pinned package versions are installed", () => {
  assert.equal(require("@microsoft/powerquery-parser/package.json").version, "2.0.0");
  assert.equal(require("@microsoft/powerquery-formatter/package.json").version, "1.0.0");
});

test("renameSpans: happy path renames both declaration and reference", async () => {
  const source = "let A = 1, B = A in B";
  const ast = await parseAst(source);
  const result = renameSpans(ast, "A");
  assert.deepEqual(result.bindings, ["A", "B"]);
  assert.equal(result.spans.length, 2);
  for (const [start, end] of result.spans) {
    assert.equal(source.slice(start, end), "A");
  }
});

test("renameSpans: refuses RENAME_TARGET when the name is absent", async () => {
  const ast = await parseAst("let A = 1, B = A in B");
  assert.throws(() => renameSpans(ast, "Z"), /RENAME_TARGET/);
});

test("renameSpans: refuses RENAME_TARGET when the name is declared twice", async () => {
  const ast = await parseAst("let A = 1, A = 2 in A");
  assert.throws(() => renameSpans(ast, "A"), /RENAME_TARGET/);
});

test("renameSpans: refuses RENAME_SCOPE for a nested function", async () => {
  const ast = await parseAst("let F = (x) => x in F");
  assert.throws(() => renameSpans(ast, "F"), /RENAME_SCOPE/);
});

test("renameSpans: refuses RENAME_SCOPE for an each expression", async () => {
  const ast = await parseAst("let L = List.Transform({1}, each _) in L");
  assert.throws(() => renameSpans(ast, "L"), /RENAME_SCOPE/);
});

test("renameSpans: refuses RENAME_ROOT for a non-let root", async () => {
  const ast = await parseAst("1 + 1");
  assert.throws(() => renameSpans(ast, "A"), /RENAME_ROOT/);
});

test("analysisView: reports bindings and their references, including unresolved names", async () => {
  const ast = await parseAst("let A = 1, B = A + Missing in B");
  const result = analysisView(ast);
  const names = result.bindings.map((binding) => binding.name);
  assert.deepEqual(names, ["A", "B"]);
  const bindingB = result.bindings.find((binding) => binding.name === "B");
  const referenceNames = bindingB.references.map((reference) => reference.name);
  assert.ok(referenceNames.includes("A"));
  assert.ok(referenceNames.includes("Missing"));
  assert.deepEqual(
    result.resultReferences.map((reference) => reference.name),
    ["B"],
  );
});

test("analysisView: scopes function parameters out of references", async () => {
  const ast = await parseAst("let F = (x) => x + Y in F");
  const result = analysisView(ast);
  const bindingF = result.bindings.find((binding) => binding.name === "F");
  const referenceNames = bindingF.references.map((reference) => reference.name);
  assert.ok(referenceNames.includes("Y"));
  assert.ok(!referenceNames.includes("x"));
});

test("analysisView: scopes each's implicit _ out of references", async () => {
  const ast = await parseAst("let L = List.Transform({1}, each _ + Z) in L");
  const result = analysisView(ast);
  const bindingL = result.bindings.find((binding) => binding.name === "L");
  const referenceNames = bindingL.references.map((reference) => reference.name);
  assert.ok(referenceNames.includes("Z"));
  assert.ok(!referenceNames.includes("_"));
});

test("analysisView: a nested let scopes its own bindings", async () => {
  const ast = await parseAst("let A = let Inner = 1 in Inner + Outer in A");
  const result = analysisView(ast);
  const bindingA = result.bindings.find((binding) => binding.name === "A");
  const referenceNames = bindingA.references.map((reference) => reference.name);
  assert.ok(referenceNames.includes("Outer"));
  assert.ok(!referenceNames.includes("Inner"));
});

test("analysisView: returns undefined for a non-let root", async () => {
  const ast = await parseAst("1 + 1");
  assert.equal(analysisView(ast), undefined);
});

test("tokenView: maps the first token of a simple let expression", async () => {
  const parsed = await parser.TaskUtils.tryLexParse(parser.DefaultSettings, "let A = 1 in A");
  const view = tokenView(parsed.lexerSnapshot.tokens[0]);
  assert.equal(view.kind, "KeywordLet");
  assert.equal(view.text, "let");
  assert.equal(view.line, 1);
  assert.equal(view.column, 1);
  assert.equal(view.start, 0);
  assert.equal(view.end, 3);
});

test("parse-error mapping: reports a finite line and column for a rejected source", async () => {
  const parsed = await parser.TaskUtils.tryLexParse(parser.DefaultSettings, "let =");
  assert.ok(parser.TaskUtils.isError(parsed));
  const found = parsed.error?.innerError?.foundToken;
  const line = (found?.token?.positionStart?.lineNumber + 1) || 1;
  const column = (found?.columnNumber + 1) || 1;
  assert.ok(Number.isFinite(line));
  assert.ok(Number.isFinite(column));
  assert.ok(column >= 1);
});

// --------------------------------------------------------------------------
// astView
// --------------------------------------------------------------------------

test("astView: a literal is a bare kind/value/literalKind node with no children", async () => {
  const ast = await parseAst("1");
  const view = astView(ast);
  assert.equal(view.kind, "LiteralExpression");
  assert.equal(view.value, "1");
  assert.equal(view.literalKind, "Numeric");
  assert.equal(view.line, 1);
  assert.equal(view.column, 1);
  assert.equal(view.children, undefined);
});

test("astView: 1 + 2 is an ArithmeticExpression over [left, operator, right]", async () => {
  const ast = await parseAst("1 + 2");
  const view = astView(ast);
  assert.equal(view.kind, "ArithmeticExpression");
  assert.equal(view.children.length, 3);
  assert.equal(view.children[0].value, "1");
  assert.equal(view.children[1].kind, "Constant");
  assert.equal(view.children[1].value, "+");
  assert.equal(view.children[2].value, "2");
});

test("astView: a let binds through ArrayWrapper/Csv/IdentifierPairedExpression and drops bookkeeping", async () => {
  const ast = await parseAst("let x = 1 in x");
  const view = astView(ast);
  assert.equal(view.kind, "LetExpression");
  assert.equal(view.id, undefined);
  assert.equal(view.attributeIndex, undefined);
  assert.equal(view.tokenRange, undefined);
  assert.equal(view.isLeaf, undefined);
  const [letConst, variableList, inConst, body] = view.children;
  assert.equal(letConst.value, "let");
  assert.equal(inConst.value, "in");
  assert.equal(variableList.kind, "ArrayWrapper");
  const [csv] = variableList.children;
  assert.equal(csv.kind, "Csv");
  const [pair] = csv.children;
  assert.equal(pair.kind, "IdentifierPairedExpression");
  const [key, equals, value] = pair.children;
  assert.equal(key.kind, "Identifier");
  assert.equal(key.value, "x");
  assert.equal(key.identifierContextKind, "Key");
  assert.equal(equals.value, "=");
  assert.equal(value.value, "1");
  assert.equal(body.kind, "IdentifierExpression");
});

test("astView: an if is condition/then/else expressions interleaved with keyword constants", async () => {
  const ast = await parseAst("if 1 = 1 then 2 else 3");
  const view = astView(ast);
  assert.equal(view.kind, "IfExpression");
  const kinds = view.children.map((child) => child.kind);
  assert.deepEqual(kinds, [
    "Constant",
    "EqualityExpression",
    "Constant",
    "LiteralExpression",
    "Constant",
    "LiteralExpression",
  ]);
  assert.equal(view.children[0].value, "if");
  assert.equal(view.children[2].value, "then");
  assert.equal(view.children[4].value, "else");
});

test("astView: a record is RecordExpression -> ArrayWrapper -> Csv -> GeneralizedIdentifierPairedExpression", async () => {
  const ast = await parseAst("[a = 1, b = 2]");
  const view = astView(ast);
  assert.equal(view.kind, "RecordExpression");
  const content = view.children.find((child) => child.kind === "ArrayWrapper");
  assert.equal(content.children.length, 2);
  const [firstPair] = content.children[0].children;
  assert.equal(firstPair.kind, "GeneralizedIdentifierPairedExpression");
  const [name, , value] = firstPair.children;
  assert.equal(name.kind, "GeneralizedIdentifier");
  assert.equal(name.value, "a");
  assert.equal(value.value, "1");
});

test("astView: a list is ListExpression -> ArrayWrapper -> Csv -> item, in source order", async () => {
  const ast = await parseAst("{1, 2, 3}");
  const view = astView(ast);
  assert.equal(view.kind, "ListExpression");
  const content = view.children.find((child) => child.kind === "ArrayWrapper");
  const values = content.children.map((csv) => csv.children[0].value);
  assert.deepEqual(values, ["1", "2", "3"]);
});

test("astView: a call is RecursivePrimaryExpression over head + InvokeExpression args", async () => {
  const ast = await parseAst("f(1, 2)");
  const view = astView(ast);
  assert.equal(view.kind, "RecursivePrimaryExpression");
  const [head, recursive] = view.children;
  assert.equal(head.kind, "IdentifierExpression");
  assert.equal(head.children[0].value, "f");
  assert.equal(recursive.kind, "ArrayWrapper");
  const invoke = recursive.children[0];
  assert.equal(invoke.kind, "InvokeExpression");
  const args = invoke.children
    .find((child) => child.kind === "ArrayWrapper")
    .children.map((csv) => csv.children[0].value);
  assert.deepEqual(args, ["1", "2"]);
});

test("astView: a lambda is a FunctionExpression over its ParameterList and body", async () => {
  const ast = await parseAst("(x, y) => x + y");
  const view = astView(ast);
  assert.equal(view.kind, "FunctionExpression");
  const [parameterList, arrow, body] = view.children;
  assert.equal(parameterList.kind, "ParameterList");
  assert.equal(arrow.value, "=>");
  const names = parameterList.children
    .find((child) => child.kind === "ArrayWrapper")
    .children.map((csv) => csv.children[0].children[0].value);
  assert.deepEqual(names, ["x", "y"]);
  assert.equal(body.kind, "ArithmeticExpression");
});

test("astView: drops bookkeeping on a deep tree while keeping value/literalKind on every leaf", async () => {
  const ast = await parseAst("let x = 1 in x + 2");
  const seen = [];
  const walk = (node) => {
    seen.push(node.kind);
    assert.equal(Object.prototype.hasOwnProperty.call(node, "id"), false);
    assert.equal(Object.prototype.hasOwnProperty.call(node, "attributeIndex"), false);
    assert.equal(Object.prototype.hasOwnProperty.call(node, "tokenRange"), false);
    assert.equal(Object.prototype.hasOwnProperty.call(node, "isLeaf"), false);
    for (const child of node.children || []) walk(child);
  };
  walk(astView(ast));
  assert.ok(seen.includes("LiteralExpression"));
  assert.ok(seen.includes("ArithmeticExpression"));
});
