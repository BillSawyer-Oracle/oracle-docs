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
const VERSIONS = new Set(["26ai", "21c", "19c"]);
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

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const canonicalPrefix = (value) => String(value).trim().toUpperCase();
const normalizedNumber = (value) => String(Number.parseInt(value, 10)).padStart(5, "0");
const displayCode = (prefix, number) => `${canonicalPrefix(prefix)}-${normalizedNumber(number)}`;
const codeUrl = (prefix, number, version) =>
  `https://docs.oracle.com/en/error-help/db/${canonicalPrefix(prefix).replaceAll("*", "").toLowerCase()}-${normalizedNumber(number)}/?r=${version}`;

function detectOracleCodes(text, prefixes) {
  const prefixSet = new Set(prefixes.map(canonicalPrefix));
  const prefixPattern = prefixes.slice().sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  const matches = [];
  const supported = new RegExp(`(^|[^A-Z0-9*])(${prefixPattern})\\s*[- ]\\s*(\\d{1,6})(?!\\d)`, "gi");
  const casual = /\b(?:ora\s*[- ]?\s*err|oraerr|oracle\s+error)\s*[-:]?\s*(\d{1,6})(?!\d)/gi;
  const unsupported = /(^|[^A-Z0-9*])([A-Z][A-Z0-9*]{1,20})-(\d{1,6})(?!\d)/gi;

  for (const match of text.matchAll(supported)) {
    matches.push({ prefix: canonicalPrefix(match[2]), number: match[3], original: `${match[2]}-${match[3]}`, index: match.index + match[1].length });
  }
  for (const match of text.matchAll(casual)) {
    matches.push({ prefix: "ORA", number: match[1], original: match[0], index: match.index });
  }
  matches.sort((a, b) => a.index - b.index);
  const seen = new Set();
  const detected = matches.flatMap((match) => {
    const code = displayCode(match.prefix, match.number);
    if (seen.has(code)) return [];
    seen.add(code);
    return [{ ...match, code }];
  });

  const ignoredSeen = new Set();
  const ignored = [...text.matchAll(unsupported)].flatMap((match) => {
    const prefix = canonicalPrefix(match[2]);
    const original = `${match[2]}-${match[3]}`;
    if (prefixSet.has(prefix) || ignoredSeen.has(original.toUpperCase())) return [];
    ignoredSeen.add(original.toUpperCase());
    return [{ original, prefix }];
  });
  return { detected, ignored };
}

function decodeHtml(value) {
  const named = { amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"' };
  return String(value).replace(/&(#x[\da-f]+|#\d+|[a-z]+);/gi, (entity, token) => {
    if (token[0] !== "#") return named[token.toLowerCase()] ?? entity;
    const number = Number.parseInt(token.slice(token[1]?.toLowerCase() === "x" ? 2 : 1), token[1]?.toLowerCase() === "x" ? 16 : 10);
    return Number.isFinite(number) ? String.fromCodePoint(number) : entity;
  });
}

function htmlToText(html) {
  return decodeHtml(String(html ?? "")
    .replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<!--[\s\S]*?-->/gi, " ")
    .replace(/<li\b[^>]*>/gi, "• ")
    .replace(/<\/(?:li|p|div|h\d)>|<br\s*\/?\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " "))
    .replace(/[^\S\r\n]+/g, " ")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function versionBlock(html, version) {
  const startMatch = new RegExp(`<div\\s+id=["']${escapeRegExp(version)}["'][^>]*>`, "i").exec(html);
  if (!startMatch) return "";
  const start = startMatch.index + startMatch[0].length;
  const rest = html.slice(start);
  const nextVersion = /<div\s+id=["'](?:26ai|21c|19c)["'][^>]*>/i.exec(rest);
  const mainEnd = rest.indexOf("</main>");
  const ends = [nextVersion?.index, mainEnd].filter((value) => Number.isInteger(value) && value >= 0);
  return rest.slice(0, ends.length ? Math.min(...ends) : rest.length);
}

function divWithClasses(block, required) {
  for (const match of block.matchAll(/<div\s+class=["']([^"']+)["'][^>]*>([\s\S]*?)<\/div>/gi)) {
    const classes = new Set(match[1].split(/\s+/));
    if (required.every((name) => classes.has(name))) return htmlToText(match[2]);
  }
  return "";
}

function officialSections(block) {
  const headings = [...block.matchAll(/<h3[^>]*>([\s\S]*?)<\/h3>/gi)];
  return headings.map((heading, index) => {
    const start = heading.index + heading[0].length;
    const end = index + 1 < headings.length ? headings[index + 1].index : block.length;
    return { title: htmlToText(heading[1]), text: divWithClasses(block.slice(start, end), ["ca"]) || "Not specified." };
  });
}

function parseOraclePage(html, version, code) {
  const block = versionBlock(html, version);
  const headings = [...block.matchAll(/<h2[^>]*>([\s\S]*?)<\/h2>/gi)];
  return headings.flatMap((heading, index) => {
    if (htmlToText(heading[1]).toUpperCase() !== code.toUpperCase()) return [];
    const start = heading.index + heading[0].length;
    const end = index + 1 < headings.length ? headings[index + 1].index : block.length;
    const variant = block.slice(start, end);
    const sections = officialSections(variant);
    const section = (name) => sections.find((item) => item.title.toLowerCase() === name)?.text || "Not specified.";
    return [{
      message: divWithClasses(variant, ["st"]) || "Not specified.",
      messageDetails: divWithClasses(variant, ["ca", "v"]),
      cause: section("cause"),
      action: section("action"),
      additionalSections: sections.filter((item) => !["cause", "action"].includes(item.title.toLowerCase())),
    }];
  });
}

function fallbackSearchUrl(code, sources) {
  const sites = sources.map(({ pattern }) => pattern.replace(/^\*\./, "")).filter(Boolean).map((domain) => `site:${domain}`);
  return `https://www.google.com/search?q=${encodeURIComponent(`${code} ${sites.join(" OR ")}`)}`;
}

async function fetchLookup(code, version, sources) {
  const url = codeUrl(code.prefix, code.number, version);
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(30_000), headers: { "user-agent": "oracle-error-help-plugin/1.0" } });
    if (!response.ok) return { status: "lookup_unavailable", code: code.code, url, message: `Oracle returned HTTP ${response.status}.` };
    const variants = parseOraclePage(await response.text(), version, code.code);
    return variants.length
      ? { status: "found", code: code.code, url, variants }
      : { status: "not_found", code: code.code, url, message: `No ${version} entry was found.`, fallbackSearchUrl: fallbackSearchUrl(code.code, sources) };
  } catch (error) {
    return { status: "lookup_unavailable", code: code.code, url, message: error.name === "TimeoutError" ? "Lookup timed out." : `Lookup failed: ${error.message}` };
  }
}

async function lookupErrors(text, version, prefixes, sources) {
  if (!VERSIONS.has(version)) throw new Error("Version must be 26ai, 21c, or 19c.");
  if (Buffer.byteLength(text, "utf8") > 2_000_000) throw new Error("Error text must be 2 MB or smaller.");
  const { detected, ignored } = detectOracleCodes(text, prefixes);
  const informative = detected.filter(({ code }) => code !== "ORA-06512");
  const selected = informative.slice(0, 5);
  const results = [];
  for (const code of selected) results.push(await fetchLookup(code, version, sources));
  if (detected.some(({ code }) => code === "ORA-06512")) {
    results.push({ status: "backtrace", code: "ORA-06512", message: "PL/SQL backtrace marker; use the other errors to find the underlying cause." });
  }
  return { version, detected, ignored, omitted: informative.slice(5), results };
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
    if (req.method === "POST" && url.pathname === "/api/lookup") {
      const input = await readJson(req);
      const [prefixText, sourceText] = await Promise.all([readFile(PREFIX_FILE, "utf8"), readFile(SOURCES_FILE, "utf8")]);
      const data = await lookupErrors(
        String(input.text ?? ""),
        String(input.version ?? "26ai"),
        prefixText.split(/\r?\n/).filter(Boolean),
        parseSources(sourceText),
      );
      return send(res, 200, data);
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
  const detected = detectOracleCodes("ORA 942\nORA-06512: at line 1\npls-201", ["ORA", "PLS"]);
  assert.deepEqual(detected.detected.map(({ code }) => code), ["ORA-00942", "ORA-06512", "PLS-00201"]);
  const page = '<div id="26ai"><h2>ORA-00942</h2><div class="st">table or view does not exist</div><h3>Cause</h3><div class="ca"><p>Missing.</p></div><h3>Action</h3><div class="ca"><ul><li>Check it.</li></ul></div></div><div id="21c">';
  assert.deepEqual(parseOraclePage(page, "26ai", "ORA-00942")[0], {
    message: "table or view does not exist",
    messageDetails: "",
    cause: "Missing.",
    action: "• Check it.",
    additionalSections: [],
  });
  console.log("Self-test passed.");
}

async function smokeTest() {
  const server = createServer(handle);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    const [page, config, lookup] = await Promise.all([
      fetch(`http://127.0.0.1:${port}/`),
      fetch(`http://127.0.0.1:${port}/api/config`),
      fetch(`http://127.0.0.1:${port}/api/lookup`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: "No errors here.", version: "26ai" }) }),
    ]);
    const data = await config.json();
    const lookupData = await lookup.json();
    assert.equal(page.status, 200);
    assert.ok(data.prefixes.includes("ORA"));
    assert.ok(data.sources.length > 0);
    assert.deepEqual(lookupData.detected, []);
    console.log("Smoke test passed.");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

async function liveTest() {
  const [prefixText, sourceText] = await Promise.all([readFile(PREFIX_FILE, "utf8"), readFile(SOURCES_FILE, "utf8")]);
  const data = await lookupErrors(
    "ORA 942",
    "26ai",
    prefixText.split(/\r?\n/).filter(Boolean),
    parseSources(sourceText),
  );
  assert.equal(data.results[0]?.status, "found");
  assert.equal(data.results[0]?.code, "ORA-00942");
  console.log(`Live test passed: ${data.results[0].code} — ${data.results[0].variants[0].message}`);
}

if (process.argv.includes("--self-test")) {
  selfTest();
} else if (process.argv.includes("--smoke-test")) {
  await smokeTest();
} else if (process.argv.includes("--live-test")) {
  await liveTest();
} else {
  const port = Number(process.env.PORT || 8787);
  createServer(handle).listen(port, "127.0.0.1", () => {
    console.log(`Oracle Error Help app: http://127.0.0.1:${port}`);
  });
}
