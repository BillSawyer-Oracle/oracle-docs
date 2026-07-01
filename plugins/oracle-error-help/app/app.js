const $ = (selector) => document.querySelector(selector);
const prefixes = $("#prefixes");
const sources = $("#sources");
const status = $("#status");
let fileText = "";

function message(text, error = false) {
  status.textContent = text;
  status.className = error ? "error" : "success";
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed.");
  return body;
}

async function run(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", () => {
    for (const item of document.querySelectorAll('[role="tab"]')) item.setAttribute("aria-selected", String(item === tab));
    for (const panel of document.querySelectorAll('[role="tabpanel"]')) panel.hidden = panel.id !== tab.dataset.panel;
    const lookup = tab.dataset.panel === "lookup-panel";
    $("h1").textContent = lookup ? "Error lookup" : "Reference editor";
    $("#view-description").textContent = lookup
      ? "Find official Oracle messages, causes, and recommended actions from pasted diagnostics or a trace file."
      : "Maintain recognized Oracle error prefixes and trusted fallback sources.";
  });
}

function byteCount(text) {
  return new TextEncoder().encode(text).length;
}

function updatePayloadSize() {
  const bytes = byteCount([$("#lookup-text").value, fileText].filter(Boolean).join("\n"));
  $("#payload-size").textContent = `Current payload: ${bytes.toLocaleString()} bytes.`;
}

$("#lookup-text").addEventListener("input", updatePayloadSize);
$("#lookup-file").addEventListener("change", () => run(async () => {
  const file = $("#lookup-file").files[0];
  if (!file) { fileText = ""; updatePayloadSize(); return; }
  if (!/\.(?:trc|log|txt)$/i.test(file.name)) throw new Error("Choose a .trc, .log, or .txt file.");
  if (file.size > 2_000_000) throw new Error("The selected file is larger than 2 MB.");
  fileText = await file.text();
  updatePayloadSize();
  message(`${file.name} loaded.`);
}));

function cell(row, value, className = "") {
  const item = document.createElement("td");
  item.className = className;
  item.textContent = value || "Not specified.";
  row.append(item);
  return item;
}

function statusLabel(value) {
  return { found: "Found", not_found: "Not found", lookup_unavailable: "Unavailable", backtrace: "Backtrace" }[value] || value;
}

function renderResults(data) {
  const rows = $("#result-rows");
  rows.replaceChildren();
  $("#result-badges").replaceChildren(
    Object.assign(document.createElement("span"), { className: "badge success-badge", textContent: `Version ${data.version}` }),
    Object.assign(document.createElement("span"), { className: "badge", textContent: `${data.detected.length} detected` }),
  );
  if (data.omitted.length) $("#result-badges").append(Object.assign(document.createElement("span"), { className: "badge warning-badge", textContent: `${data.omitted.length} omitted` }));

  for (const result of data.results) {
    const variants = result.status === "found" ? result.variants : [null];
    for (const variant of variants) {
      const row = document.createElement("tr");
      const codeCell = cell(row, "", "code");
      codeCell.textContent = "";
      if (result.url) {
        const link = document.createElement("a");
        link.href = result.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = result.code;
        codeCell.append(link);
      } else codeCell.textContent = result.code;
      const statusCell = cell(row, "");
      statusCell.replaceChildren(Object.assign(document.createElement("span"), { className: `badge status-${result.status}`, textContent: statusLabel(result.status) }));
      const details = variant?.messageDetails ? `${variant.message}\n${variant.messageDetails}` : variant?.message || result.message;
      cell(row, details);
      cell(row, variant?.cause || (result.status === "backtrace" ? "See the other errors for the underlying cause." : "Not specified."));
      const extra = variant?.additionalSections?.map(({ title, text }) => `${title}: ${text}`).join("\n\n");
      const actionCell = cell(row, [variant?.action, extra].filter(Boolean).join("\n\n"));
      if (result.fallbackSearchUrl) {
        const link = document.createElement("a");
        link.href = result.fallbackSearchUrl;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = "Search trusted Oracle sources";
        actionCell.append(document.createElement("br"), link);
      }
      rows.append(row);
    }
  }

  const notes = [];
  if (!data.detected.length) notes.push("No Oracle error codes were detected. Examples: ORA-00942, PLS-00201, TNS-12541.");
  if (data.omitted.length) notes.push(`${data.omitted.length} additional code(s) omitted after the first five: ${data.omitted.map(({ code }) => code).join(", ")}.`);
  if (data.ignored.length) notes.push(`Ignored unsupported prefix candidate(s): ${data.ignored.map(({ original }) => original).join(", ")}.`);
  $("#lookup-note").textContent = notes.join(" ");
  $("#lookup-note").hidden = !notes.length;
  $("#lookup-results").hidden = false;
}

$("#run-lookup").addEventListener("click", () => run(async () => {
  const text = [$("#lookup-text").value, fileText].filter(Boolean).join("\n");
  if (!text.trim()) throw new Error("Paste Oracle error text or choose a trace file first.");
  $("#run-lookup").disabled = true;
  $("#run-lookup").textContent = "Looking up…";
  message("Looking up Oracle errors…");
  try {
    renderResults(await api("/api/lookup", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text, version: $("#version").value }) }));
    message("Lookup complete.");
  } finally {
    $("#run-lookup").disabled = false;
    $("#run-lookup").textContent = "Run Lookup";
  }
}));

$("#clear-lookup").addEventListener("click", () => {
  $("#lookup-text").value = "";
  $("#lookup-file").value = "";
  fileText = "";
  $("#lookup-results").hidden = true;
  updatePayloadSize();
  message("Lookup cleared.");
});

const prefixList = () => prefixes.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
const updateCount = () => { $("#prefix-count").textContent = `${prefixList().length} prefixes`; };

function addSource(source = {}) {
  const row = $("#source-template").content.firstElementChild.cloneNode(true);
  for (const input of row.querySelectorAll("input")) input.value = source[input.dataset.field] || "";
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  sources.append(row);
}

const sourceList = () => [...sources.querySelectorAll(".source-row")].map((row) => Object.fromEntries(
  [...row.querySelectorAll("input")].map((input) => [input.dataset.field, input.value.trim()]),
));

prefixes.addEventListener("input", updateCount);
$("#add-source").addEventListener("click", () => addSource());
$("#refresh").addEventListener("click", () => run(async () => {
  message("Refreshing from Oracle…");
  const data = await api("/api/refresh", { method: "POST" });
  prefixes.value = data.prefixes.join("\n");
  updateCount();
  message("Refresh complete. Review the list, then save.");
}));
$("#save-prefixes").addEventListener("click", () => run(async () => {
  const data = await api("/api/prefixes", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ prefixes: prefixList() }) });
  prefixes.value = data.prefixes.join("\n");
  updateCount();
  message("Prefixes saved.");
}));
$("#save-sources").addEventListener("click", () => run(async () => {
  await api("/api/sources", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ sources: sourceList() }) });
  message("Trusted sources saved.");
}));

run(async () => {
  const data = await api("/api/config");
  prefixes.value = data.prefixes.join("\n");
  data.sources.forEach(addSource);
  updateCount();
  updatePayloadSize();
  message("Ready.");
});
