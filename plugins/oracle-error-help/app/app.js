const $ = (selector) => document.querySelector(selector);
const prefixes = $("#prefixes");
const sources = $("#sources");
const status = $("#status");

function message(text, error = false) {
  status.textContent = text;
  status.className = error ? "error" : "success";
}

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

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed.");
  return body;
}

async function run(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

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
  message("References loaded.");
});
