"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const parser = require("@microsoft/powerquery-parser");
const { tokenView, renameSpans, analysisView } = require("./bridge.js");

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
