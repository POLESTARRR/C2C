/* -----------------------------------------------------------------------
   Clip2Cart · Frontend for Instamart grocery cart.
   ----------------------------------------------------------------------- */

let currentMode = "youtube_url";
let lastBasket   = [];

// ---- helpers -----------------------------------------------------------

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function showError(id, msg) { const el = document.getElementById(id); el.textContent = msg; el.classList.remove("hidden"); }
function hideError(id)      { document.getElementById(id).classList.add("hidden"); }

// ---- service tab switching ---------------------------------------------

const serviceTabs  = document.querySelectorAll(".service-tab");
const panels       = { instamart: document.getElementById("panel-instamart") };

serviceTabs.forEach(tab => {
  tab.addEventListener("click", () => {
    serviceTabs.forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    Object.values(panels).forEach(p => p.classList.remove("active"));
    panels[tab.dataset.service].classList.add("active");
  });
});

// ---- nav pill quick-links (same service switching) ---------------------

document.querySelectorAll(".nav-pill a").forEach(a => {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    const svc = a.textContent.trim().toLowerCase();
    if (panels[svc]) {
      serviceTabs.forEach(t => t.classList.remove("active"));
      document.querySelector(`.service-tab[data-service="${svc}"]`).classList.add("active");
      Object.values(panels).forEach(p => p.classList.remove("active"));
      panels[svc].classList.add("active");
      document.getElementById("service-tabs").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

// ---- health tag -------------------------------------------------------

(async () => {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const tag  = document.getElementById("health-tag");
    if (data.swiggy_token_present && data.servers?.instamart?.reachable) {
      tag.textContent = "SWIGGY · MCP · LIVE";
      tag.style.borderColor = "#1e8e3e";
    } else if (data.swiggy_token_present) {
      tag.textContent = "SWIGGY · MCP · TOKEN OK";
      tag.style.borderColor = "#fc8019";
    } else {
      tag.textContent = "SWIGGY · LOCAL MODE";
    }
  } catch (_) { /* ignore */ }
})();


// ======================================================================
//  INSTAMART — existing flow
// ======================================================================

document.querySelectorAll("#panel-instamart .tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll("#panel-instamart .tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    currentMode = tab.dataset.mode;
    document.getElementById("input-value").placeholder =
      currentMode === "youtube_url" ? "https://www.youtube.com/watch?v=..." : "Paste the transcript or caption text here...";
  });
});

const servingsInput = document.getElementById("servings");
function clampServings(n) { return Math.max(1, Math.min(20, Number.isFinite(n) ? n : 4)); }
document.getElementById("servings-down").addEventListener("click", () => { servingsInput.value = clampServings(parseInt(servingsInput.value,10)-1); });
document.getElementById("servings-up").addEventListener("click",   () => { servingsInput.value = clampServings(parseInt(servingsInput.value,10)+1); });
servingsInput.addEventListener("change", () => { servingsInput.value = clampServings(parseInt(servingsInput.value,10)); });

document.getElementById("submit-btn").addEventListener("click", async () => {
  const value     = document.getElementById("input-value").value.trim();
  const errorEl   = document.getElementById("error-msg");
  const loadingEl = document.getElementById("loading");
  const resultsEl = document.getElementById("results");

  errorEl.classList.add("hidden");
  resultsEl.classList.add("hidden");

  if (!value) { showError("error-msg", "Please paste a YouTube URL or transcript text."); return; }

  loadingEl.classList.remove("hidden");
  const sub = document.getElementById("loading-sub");
  const loadingText = document.getElementById("loading-text");
  sub.classList.add("hidden");
  loadingText.textContent = currentMode === "youtube_url" ? "FETCHING TRANSCRIPT…" : "PROCESSING TRANSCRIPT…";
  const slowTimer = currentMode === "youtube_url"
    ? setTimeout(() => { loadingText.textContent = "TRANSCRIBING AUDIO…"; sub.classList.remove("hidden"); }, 4000)
    : null;

  try {
    const params = new URLSearchParams({
      source_type: currentMode,
      value: value,
      servings: String(clampServings(parseInt(servingsInput.value,10))),
    });
    const res = await fetch(`/process?${params}`);
    let data;
    try { data = await res.json(); } catch { throw new Error(`Server returned an unreadable response (HTTP ${res.status}).`); }
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");
    renderInstamartResults(data);
  } catch (err) { showError("error-msg", err.message); }
  finally { if (slowTimer) clearTimeout(slowTimer); loadingEl.classList.add("hidden"); }
});

const VAGUE = new Set(["","unknown","some","to taste","as needed","as required","n/a","a little","a bit","thoda","thoda sa","as per taste"]);
function quantityLabel(value, isEstimate) {
  const text = String(value ?? "").trim();
  if (VAGUE.has(text.toLowerCase())) return `<span class="unstated">not stated</span>`;
  return isEstimate
    ? `${esc(text)} <span class="est-tag" title="Our estimate for the serving size.">est</span>`
    : esc(text);
}

function renderInstamartResults(data) {
  if (data.fallback_note) { document.getElementById("fallback-note").textContent = data.fallback_note; document.getElementById("fallback-note").classList.remove("hidden"); }
  else document.getElementById("fallback-note").classList.add("hidden");

  // Live mode + at least one real match → show the handoff into the Swiggy app.
  const live  = data.instamart_mode === "mcp";
  const handoff = document.getElementById("instamart-handoff");
  const addrEl = document.getElementById("handoff-address");
  if (live && data.basket.some(b => b.matched_catalog_item)) {
    handoff.classList.remove("hidden");
    // Show which delivery address the cart was written to.
    const addr = data.delivery_address;
    if (addr && addr.address_line) {
      const label = addr.label ? `(${addr.label})` : "";
      addrEl.textContent = `📍 CART DELIVERED TO: ${label} ${addr.address_line}`;
      addrEl.classList.remove("hidden");
    } else {
      addrEl.classList.add("hidden");
    }
  } else {
    handoff.classList.add("hidden");
  }

  document.getElementById("summary-total").textContent   = data.summary.total_items;
  document.getElementById("summary-matched").textContent = data.summary.matched_items;
  document.getElementById("summary-calls").textContent   = data.summary.mcp_call_count ?? 0;
  document.getElementById("summary-cost").textContent    = data.summary.estimated_total_inr.toFixed(2);
  servingsInput.value = data.summary.servings;

  const scaled = document.getElementById("scaled-note");
  if (data.summary.servings !== data.summary.recipe_serves) {
    scaled.textContent = `Recipe serves ${data.summary.recipe_serves}, scaled for ${data.summary.servings}`;
  } else {
    scaled.textContent = `Recipe serves ${data.summary.servings}`;
  }
  scaled.classList.remove("hidden");

  const isScaled = data.summary.servings !== data.summary.recipe_serves;
  document.getElementById("needs-header").textContent = `You need for ${data.summary.servings}`;

  const estimated = data.basket.filter(b => b.quantity_is_estimate).length;
  const note = document.getElementById("source-note");
  const labels = { captions: "Transcript from the video's own captions", audio: "Audio transcribed with Whisper", pasted: "Transcript pasted by you" };
  note.className = "source-note" + (data.transcript_source === "audio" ? " audio" : "");
  note.innerHTML = `<span class="dot"></span>${esc(labels[data.transcript_source] || "")}${estimated ? ` · ${estimated} quantity${estimated===1?"":"s"} estimated` : ""}`;
  note.classList.remove("hidden");

  const extractedList = document.getElementById("extracted-list");
  extractedList.innerHTML = "";
  data.extracted_products.forEach(item => {
    const chip = document.createElement("span");
    chip.className = `chip confidence-${esc(item.confidence).toLowerCase()}`;
    chip.innerHTML = `${esc(item.product_name)} <span class="chip-qty">${quantityLabel(item.estimated_quantity, item.quantity_is_estimate)}</span>`;
    extractedList.appendChild(chip);
  });

  const tbody = document.querySelector("#basket-table tbody");
  tbody.innerHTML = "";
  lastBasket = data.basket;

  data.basket.forEach(item => {
    const row   = document.createElement("tr");
    const score = item.match_score != null ? `<span class="score-tag">${item.match_score}%</span>` : "";
    const canonical = item.canonical_name && item.canonical_name.toLowerCase() !== item.product_name.toLowerCase()
      ? `<span class="canonical">${esc(item.canonical_name)}</span>` : "";
    const via = item.matched_by === "semantic" ? `<span class="via-semantic">AI</span>` : "";
    const product = item.matched_catalog_item
      ? `<span class="badge-matched">✓ ${esc(item.catalog_name)}</span>${via}${score}`
      : `<span class="badge-unmatched">✗ not stocked${item.suggested_substitute ? ` <span class="substitute-hint">(try: ${esc(item.suggested_substitute)})</span>` : ""}</span>${score}`;

    row.innerHTML = `
      <td><span class="confidence-dot confidence-${esc(item.confidence).toLowerCase()}"></span>${esc(item.product_name)}${canonical}</td>
      <td>${quantityLabel(item.estimated_quantity, item.quantity_is_estimate)}</td>
      <td class="needs-cell${isScaled ? " scaled" : ""}">${item.required_label ? esc(item.required_label) : `<span class="unstated">not stated</span>`}</td>
      <td>${product}</td>
      <td class="pack-cell">${esc(item.pack_label) || "·"}</td>
      <td class="units-cell">${item.matched_catalog_item ? "× "+item.units : "·"}</td>
      <td>${item.line_total_inr != null ? "₹"+item.line_total_inr : "·"}</td>`;
    tbody.appendChild(row);
  });

  renderWireLog(data);
  document.getElementById("results").classList.remove("hidden");
  document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderWireLog(data) {
  const body  = document.getElementById("wirelog-body");
  const badge = document.getElementById("mode-badge");
  const live  = data.instamart_mode === "mcp";
  badge.textContent = live ? "MODE: SWIGGY MCP (LIVE)" : "MODE: LOCAL SIMULATOR";
  badge.classList.toggle("live", live);
  body.innerHTML = "";
  (data.mcp_calls || []).forEach(call => {
    const entry = document.createElement("div");
    entry.className = "wirelog-entry";
    entry.innerHTML = `
      <div class="wirelog-meta">
        <span class="wirelog-seq">#${call.seq}</span><span>${esc(call.endpoint)}</span>
        <span class="wirelog-tool">${esc(call.tool)}</span><span>${call.latency_ms}ms</span>
      </div>
      <pre class="wirelog-pre">▶ ${esc(JSON.stringify(call.request, null, 2))}</pre>
      <pre class="wirelog-pre response">◀ ${esc(JSON.stringify(call.response, null, 2))}</pre>`;
    body.appendChild(entry);
  });
}

document.getElementById("wirelog-toggle").addEventListener("click", e => {
  const body = document.getElementById("wirelog-body");
  const hidden = body.classList.toggle("hidden");
  e.target.textContent = hidden ? "SHOW" : "HIDE";
  e.target.setAttribute("aria-expanded", String(!hidden));
});

document.getElementById("download-csv").addEventListener("click", () => {
  if (!lastBasket.length) return;
  const header = ["product_name","canonical_name","category","estimated_quantity","quantity_is_estimate","required_label","confidence","matched_catalog_item","product_id","catalog_name","pack_label","units","price_inr","line_total_inr","match_score","suggested_substitute"];
  const cell = v => `"${String(v??"").replace(/"/g,'""')}"`;
  const rows = lastBasket.map(item => header.map(k => cell(item[k])).join(","));
  const csv  = [header.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = "clip2cart-basket.csv"; a.click(); URL.revokeObjectURL(url);
});

