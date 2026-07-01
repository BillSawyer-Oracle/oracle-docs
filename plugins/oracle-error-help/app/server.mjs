import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const APP_DIR = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(APP_DIR);
const REFS = join(ROOT, "skills", "oracle-error-help", "references");
const PREFIX_FILE = join(REFS, "oracle-error-prefixes.txt");
const SOURCES_FILE = join(REFS, "oracle-trusted-sources.md");
const ORACLE_INDEX = "https://docs.oracle.com/en/error-help/db/acfs-index.html";
const PREFIX_RE = /^[A-Z][A-Z0-9*]{0,31}$/;
const STATIC = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/style.css", ["style.css", "text/css; charset=utf-8"]],
]);

function cleanPrefixes(values) {
  if (!Array.isArray(values)) throw new Error("Prefixes must be a list.");
  const prefixes = [...new Set(values.map((value) => String(value).trim().toUpperCase()).filter(Boolean))];
  if (!prefixes.length || prefixes.some((value) => !PREFIX_RE.test(value))) {
    throw new Error("Use one valid Oracle prefix per line (letters, digits, or *).");
  }
  return prefixes.sort((a, b) => a.localeCompare(b));
}

function cleanSources(values) {
  if (!Array.isArray(values)) throw new Error("Sources must be a list.");
  return values.map((source) => {
    const result = {
      name: String(source.name ?? "").trim(),
      pattern: String(source.pattern ?? "").trim(),
      notes: String(source.notes ?? "").trim(),
    };
    if (!result.name || !result.pattern || Object.values(result).some((value) => value.length > 300 || /[|\r\n]/.test(value))) {
      throw new Error("Each source needs a name and URL pattern; pipes and line breaks are not allowed.");
    }
    return result;
  });
}

function parseSources(markdown) {
  return markdown.split(/\r?\n/).flatMap((line) => {
    if (!/^\|.+\|$/.test(line) || /^\|\s*(Source|-)/i.test(line)) return [];
    const cells = line.slice(1, -1).split("|").map((cell) => cell.trim());
    return cells.length === 3 ? [{ name: cells[0], pattern: cells[1].replaceAll("`", ""), notes: cells[2] }] : [];
  });
}

function sourceMarkdown(sources) {
  const timestamp = new Date().toISOString();
  const rows = sources.map(({ name, pattern, notes }) => `| ${name} | ${pattern} | ${notes} |`).join("\n");
  return `---
type: reference
title: Oracle Trusted Sources for Error Lookup
description: Curated fallback sources for Oracle error research when official Error Help has no entry.
resource: oracle-error-help://references/trusted-sources
tags: [oracle, errors, sources]
version: 2026.07
timestamp: ${timestamp}
---

# Oracle Trusted Sources for Error Lookup

Use these sources in listed order when Oracle Error Help has no useful entry.

| Source | URL Pattern | Notes |
|---|---|---|
${rows}

Search for the exact error code plus relevant context. Prefer the sources above. If none is useful, broaden the search and clearly identify that the result is not from a curated source.
`;
}

function extractPrefixes(html) {
  const matches = html.matchAll(/<a\b[^>]*href=["'][^"']+-index\.html["'][^>]*>([^<]+)<\/a>/gi);
  return cleanPrefixes([...matches].map((match) => match[1].replace(/&amp;/g, "&").trim()).filter((value) => PREFIX_RE.test(value.toUpperCase())));
}

async function atomicWrite(path, content) {
  const temporary = `${path}.tmp`;
  await writeFile(temporary, content, "utf8");
  await rename(temporary, path);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 256_000) throw new Error("Request is too large.");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function send(res, status, body, type = "application/json; charset=utf-8") {
  res.writeHead(status, { "content-type": type, "cache-control": "no-store", "x-content-type-options": "nosniff" });
  res.end(type.startsWith("application/json") ? JSON.stringify(body) : body);
}

async function handle(req, res) {
  try {
    const url = new URL(req.url, "http://localhost");
    if (req.method === "GET" && STATIC.has(url.pathname)) {
      const [file, type] = STATIC.get(url.pathname);
      return send(res, 200, await readFile(join(APP_DIR, file), "utf8"), type);
    }
    if (req.method === "GET" && url.pathname === "/api/config") {
      const [prefixText, sourceText] = await Promise.all([readFile(PREFIX_FILE, "utf8"), readFile(SOURCES_FILE, "utf8")]);
      return send(res, 200, { prefixes: prefixText.split(/\r?\n/).filter(Boolean), sources: parseSources(sourceText) });
    }
    if (req.method === "PUT" && url.pathname === "/api/prefixes") {
      const prefixes = cleanPrefixes((await readJson(req)).prefixes);
      await atomicWrite(PREFIX_FILE, `${prefixes.join("\n")}\n`);
      return send(res, 200, { prefixes });
    }
    if (req.method === "PUT" && url.pathname === "/api/sources") {
      const sources = cleanSources((await readJson(req)).sources);
      await atomicWrite(SOURCES_FILE, sourceMarkdown(sources));
      return send(res, 200, { sources });
    }
    if (req.method === "POST" && url.pathname === "/api/refresh") {
      const response = await fetch(ORACLE_INDEX, { signal: AbortSignal.timeout(30_000), headers: { "user-agent": "oracle-error-help-plugin/1.0" } });
      if (!response.ok) throw new Error(`Oracle returned HTTP ${response.status}.`);
      return send(res, 200, { prefixes: extractPrefixes(await response.text()) });
    }
    send(res, 404, { error: "Not found." });
  } catch (error) {
    send(res, 400, { error: error.message || "Request failed." });
  }
}

function selfTest() {
  assert.deepEqual(cleanPrefixes(["ora", " TNS ", "ora"]), ["ORA", "TNS"]);
  const sources = [{ name: "Oracle Docs", pattern: "docs.oracle.com", notes: "Official" }];
  assert.deepEqual(parseSources(sourceMarkdown(sources)), sources);
  assert.deepEqual(extractPrefixes('<a href="ora-index.html">ORA</a><a href="pls-index.html">PLS</a>'), ["ORA", "PLS"]);
  console.log("Self-test passed.");
}

async function smokeTest() {
  const server = createServer(handle);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    const [page, config] = await Promise.all([
      fetch(`http://127.0.0.1:${port}/`),
      fetch(`http://127.0.0.1:${port}/api/config`),
    ]);
    const data = await config.json();
    assert.equal(page.status, 200);
    assert.ok(data.prefixes.includes("ORA"));
    assert.ok(data.sources.length > 0);
    console.log("Smoke test passed.");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

if (process.argv.includes("--self-test")) {
  selfTest();
} else if (process.argv.includes("--smoke-test")) {
  await smokeTest();
} else {
  const port = Number(process.env.PORT || 8787);
  createServer(handle).listen(port, "127.0.0.1", () => {
    console.log(`Oracle Error Help editor: http://127.0.0.1:${port}`);
  });
}
