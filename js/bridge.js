"use strict";

const fs = require("fs");
const MAX_BYTES = 10 * 1024 * 1024;

function emit(value) {
  const output = JSON.stringify(value);
  process.stdout.write(
    Buffer.byteLength(output, "utf8") > MAX_BYTES
      ? JSON.stringify({ error: "OUTPUT_LIMIT" })
      : output,
  );
}

function tokenView(token) {
  return {
    kind: token.kind,
    text: token.data,
    line: token.positionStart.lineNumber + 1,
    column: token.positionStart.lineCodeUnit + 1,
    endLine: token.positionEnd.lineNumber + 1,
    endColumn: token.positionEnd.lineCodeUnit + 1,
    start: token.positionStart.codeUnit,
    end: token.positionEnd.codeUnit,
  };
}

function renameSpans(ast, oldName) {
  if (ast.kind !== "LetExpression") throw new Error("RENAME_ROOT");
  const bindings = ast.variableList.elements.map((element) => element.node.key);
  const matches = bindings.filter((node) => node.literal === oldName);
  if (matches.length !== 1) throw new Error("RENAME_TARGET");

  const spans = [];
  const visit = (node, isRoot = false) => {
    if (!node || typeof node !== "object") return;
    if (!isRoot && ["LetExpression", "FunctionExpression", "EachExpression"].includes(node.kind)) {
      throw new Error("RENAME_SCOPE");
    }
    if (
      node.kind === "Identifier" &&
      node.literal === oldName &&
      ["Key", "Value"].includes(node.identifierContextKind)
    ) {
      spans.push([node.tokenRange.positionStart.codeUnit, node.tokenRange.positionEnd.codeUnit]);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach((item) => visit(item));
      else if (value && typeof value === "object") visit(value);
    }
  };
  visit(ast, true);
  return { bindings: bindings.map((node) => node.literal), spans };
}

function analysisView(ast) {
  if (ast.kind !== "LetExpression") return undefined;
  const position = (node) => ({
    name: node.literal,
    line: node.tokenRange.positionStart.lineNumber + 1,
    column: node.tokenRange.positionStart.lineCodeUnit + 1,
  });
  const references = (root) => {
    const found = [];
    const visit = (node, scope = new Set()) => {
      if (!node || typeof node !== "object") return;
      if (node.kind === "FunctionExpression") {
        const parameters = node.parameters.content?.elements || [];
        const nested = new Set(scope);
        parameters.forEach((item) => nested.add(item.node.name.literal));
        visit(node.expression, nested);
        return;
      }
      if (node.kind === "EachExpression") {
        visit(node.paired, new Set([...scope, "_"]));
        return;
      }
      if (node.kind === "LetExpression") {
        const nested = new Set(scope);
        node.variableList.elements.forEach((item) => nested.add(item.node.key.literal));
        node.variableList.elements.forEach((item) => visit(item.node.value, nested));
        visit(node.expression, nested);
        return;
      }
      if (node.kind === "Identifier" && node.identifierContextKind === "Value") {
        if (!scope.has(node.literal)) found.push(position(node));
      }
      for (const value of Object.values(node)) {
        if (Array.isArray(value)) value.forEach((item) => visit(item, scope));
        else if (value && typeof value === "object") visit(value, scope);
      }
    };
    visit(root);
    return found;
  };
  return {
    bindings: ast.variableList.elements.map((element) => ({
      ...position(element.node.key),
      references: references(element.node.value),
    })),
    resultReferences: references(ast.expression),
  };
}

async function main() {
  const raw = fs.readFileSync(0, "utf8");
  if (Buffer.byteLength(raw, "utf8") > MAX_BYTES) return emit({ error: "INPUT_LIMIT" });
  const request = JSON.parse(raw);
  if (typeof request.source !== "string") return emit({ error: "BAD_REQUEST" });
  if (Buffer.byteLength(request.source, "utf8") > MAX_BYTES) return emit({ error: "INPUT_LIMIT" });
  const parser = require("@microsoft/powerquery-parser");
  const formatter = require("@microsoft/powerquery-formatter");
  const parsed = await parser.TaskUtils.tryLexParse(parser.DefaultSettings, request.source);
  if (parser.TaskUtils.isError(parsed)) {
    const found = parsed.error?.innerError?.foundToken;
    return emit({
      error: "PARSE_ERROR",
      line: found?.token?.positionStart?.lineNumber + 1 || 1,
      column: found?.columnNumber + 1 || 1,
      message: "Power Query parser rejected the source",
    });
  }
  if (request.kind === "format") {
    const formatted = await formatter.tryFormat(
      { ...formatter.DefaultSettings, newlineLiteral: request.newline === "\r\n" ? "\r\n" : "\n" },
      request.source,
    );
    return formatted.kind === "Ok"
      ? emit({ formatted: formatted.value })
      : emit({ error: "FORMAT_ERROR", message: "Power Query formatter rejected the source" });
  }
  if (request.kind === "rename") {
    try {
      return emit(renameSpans(parsed.ast, request.old));
    } catch (error) {
      return emit({ error: String(error.message || "RENAME_UNSAFE") });
    }
  }
  return emit({
    rootKind: parsed.ast.kind,
    tokens: parsed.lexerSnapshot.tokens.map(tokenView),
    analysis: analysisView(parsed.ast),
  });
}

main().catch(() => emit({ error: "BRIDGE_FAILURE" }));
