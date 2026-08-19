let currentMode = "youtube_url";
let lastBasket = [];

// LLM output lands in the DOM, so everything we interpolate gets escaped.
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

document.querySelector('a[href="#basket-section"]').addEventListener("click", (e) => {
  const results = document.getElementById("results");
  if (results.classList.contains("hidden")) {
    e.preventDefault();
    const errorEl = document.getElementById("error-msg");
    errorEl.textContent = "Generate a basket first. Paste a link or transcript below.";
    errorEl.classList.remove("hidden");
    document.getElementById("tool").scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("input-value").focus();
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentMode = tab.dataset.mode;
    const textarea = document.getElementById("input-value");
    textarea.placeholder =
      currentMode === "youtube_url"
        ? "https://www.youtube.com/watch?v=..."
        : "Paste the transcript or caption text here...";
  });
});

// Serving size stepper.
const servingsInput = document.getElementById("servings");

function clampServings(n) {
  return Math.max(1, Math.min(20, Number.isFinite(n) ? n : 4));
}

document.getElementById("servings-down").addEventListener("click", () => {
  servingsInput.value = clampServings(parseInt(servingsInput.value, 10) - 1);
});
document.getElementById("servings-up").addEventListener("click", () => {
  servingsInput.value = clampServings(parseInt(servingsInput.value, 10) + 1);
});
servingsInput.addEventListener("change", () => {
  servingsInput.value = clampServings(parseInt(servingsInput.value, 10));
});

document.getElementById("submit-btn").addEventListener("click", async () => {
  const value = document.getElementById("input-value").value.trim();
  const errorEl = document.getElementById("error-msg");
  const loadingEl = document.getElementById("loading");
  const resultsEl = document.getElementById("results");

  errorEl.classList.add("hidden");
  resultsEl.classList.add("hidden");

  if (!value) {
    errorEl.textContent = "Please paste a YouTube URL or transcript text.";
    errorEl.classList.remove("hidden");
    return;
  }

  loadingEl.classList.remove("hidden");

  // Fetching a URL may need an audio download, which is slow enough that the
  // user deserves to know why they are waiting.
  const sub = document.getElementById("loading-sub");
  const loadingText = document.getElementById("loading-text");
  sub.classList.add("hidden");
  loadingText.textContent =
    currentMode === "youtube_url" ? "FETCHING TRANSCRIPT…" : "PROCESSING TRANSCRIPT…";
  const slowTimer =
    currentMode === "youtube_url"
      ? setTimeout(() => {
          loadingText.textContent = "TRANSCRIBING AUDIO…";
          sub.classList.remove("hidden");
        }, 4000)
      : null;

  try {
    const res = await fetch("/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_type: currentMode,
        value,
        servings: clampServings(parseInt(servingsInput.value, 10)),
      }),
    });

    // The server always answers JSON, but a proxy or a crash could still send
    // HTML. Do not let that surface as "Unexpected token '<'".
    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      throw new Error(`Server returned an unreadable response (HTTP ${res.status}).`);
    }

    if (!res.ok) {
      throw new Error(data.detail || "Something went wrong.");
    }

    renderResults(data);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  } finally {
    if (slowTimer) clearTimeout(slowTimer);
    loadingEl.classList.add("hidden");
  }
});

// Plenty of cooking videos never state amounts. "Not stated" is a real answer
// and should look like one, rather than an empty cell that reads as broken.
const VAGUE = new Set([
  "", "unknown", "some", "to taste", "as needed", "as required", "n/a",
  "a little", "a bit", "thoda", "thoda sa", "as per taste",
]);

// Every row gets a quantity. When the video never said one, we fill in a
// sensible amount and label it, so an estimate is never mistaken for something
// the chef actually said.
function quantityLabel(value, isEstimate) {
  const text = String(value ?? "").trim();
  if (VAGUE.has(text.toLowerCase())) {
    return `<span class="unstated">not stated</span>`;
  }
  return isEstimate
    ? `${esc(text)} <span class="est-tag" title="Not stated in the video. This is our estimate for 3 to 4 people.">est</span>`
    : esc(text);
}

function renderResults(data) {
  document.getElementById("summary-total").textContent = data.summary.total_items;
  document.getElementById("summary-matched").textContent = data.summary.matched_items;
  document.getElementById("summary-calls").textContent = data.summary.mcp_call_count ?? 0;
  document.getElementById("summary-cost").textContent =
    data.summary.estimated_total_inr.toFixed(2);

  // Keep the stepper showing what we actually shopped for, in case the recipe
  // set its own size on the first run.
  servingsInput.value = data.summary.servings;

  const scaled = document.getElementById("scaled-note");
  if (data.summary.servings !== data.summary.recipe_serves) {
    scaled.textContent =
      `Recipe serves ${data.summary.recipe_serves}, scaled for ${data.summary.servings}`;
    scaled.classList.remove("hidden");
  } else {
    scaled.textContent = `Recipe serves ${data.summary.servings}`;
    scaled.classList.remove("hidden");
  }

  // The Recipe says column deliberately never changes, so when we scale, the
  // You need column has to shout about it. Without this it looks like nothing
  // happened, especially when a larger pack absorbs the increase and Qty stays
  // at one.
  const isScaled = data.summary.servings !== data.summary.recipe_serves;
  document.getElementById("needs-header").textContent =
    `You need for ${data.summary.servings}`;

  const estimated = data.basket.filter((b) => b.quantity_is_estimate).length;
  const note = document.getElementById("source-note");
  const labels = {
    captions: "Transcript from the video's own captions",
    audio: "No captions on this video, so the audio was transcribed with Whisper",
    pasted: "Transcript pasted by you",
  };
  note.className = "source-note" + (data.transcript_source === "audio" ? " audio" : "");
  const estNote = estimated
    ? ` · ${estimated} quantity${estimated === 1 ? "" : "s"} estimated, marked est`
    : "";
  note.innerHTML = `<span class="dot"></span>${esc(labels[data.transcript_source] || "")}${esc(estNote)}`;
  note.classList.remove("hidden");

  const extractedList = document.getElementById("extracted-list");
  extractedList.innerHTML = "";
  data.extracted_products.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = `chip confidence-${esc(item.confidence).toLowerCase()}`;
    chip.innerHTML = `${esc(item.product_name)} <span class="chip-qty">${quantityLabel(item.estimated_quantity, item.quantity_is_estimate)}</span>`;
    extractedList.appendChild(chip);
  });

  const tbody = document.querySelector("#basket-table tbody");
  tbody.innerHTML = "";
  lastBasket = data.basket;

  data.basket.forEach((item) => {
    const row = document.createElement("tr");
    const score = item.match_score != null ? `<span class="score-tag">${item.match_score}%</span>` : "";

    // Show what the speaker said, and underneath it the English term we
    // matched on, whenever the two differ. On a Hindi video that is the whole
    // story of how the row got matched.
    const canonical =
      item.canonical_name &&
      item.canonical_name.toLowerCase() !== item.product_name.toLowerCase()
        ? `<span class="canonical">${esc(item.canonical_name)}</span>`
        : "";
    const via =
      item.matched_by === "semantic"
        ? `<span class="via-semantic" title="Matched by the semantic pass">AI</span>`
        : "";

    const product = item.matched_catalog_item
      ? `<span class="badge-matched">✓ ${esc(item.catalog_name)}</span>${via}${score}`
      : `<span class="badge-unmatched">✗ not stocked${
          item.suggested_substitute
            ? ` <span class="substitute-hint">(try: ${esc(item.suggested_substitute)})</span>`
            : ""
        }</span>${score}`;

    row.innerHTML = `
      <td><span class="confidence-dot confidence-${esc(item.confidence).toLowerCase()}"></span>${esc(item.product_name)}${canonical}</td>
      <td>${quantityLabel(item.estimated_quantity, item.quantity_is_estimate)}</td>
      <td class="needs-cell${isScaled ? " scaled" : ""}">${
        item.required_label
          ? esc(item.required_label)
          : `<span class="unstated">not stated</span>`
      }</td>
      <td>${product}</td>
      <td class="pack-cell">${esc(item.pack_label) || "·"}</td>
      <td class="units-cell">${item.matched_catalog_item ? "× " + item.units : "·"}</td>
      <td>${item.line_total_inr != null ? "₹" + item.line_total_inr : "·"}</td>
    `;
    tbody.appendChild(row);
  });

  renderWireLog(data);

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderWireLog(data) {
  const body = document.getElementById("wirelog-body");
  const badge = document.getElementById("mode-badge");
  const live = data.instamart_mode === "mcp";

  badge.textContent = live ? "MODE: SWIGGY MCP (LIVE)" : "MODE: LOCAL SIMULATOR";
  badge.classList.toggle("live", live);

  body.innerHTML = "";
  (data.mcp_calls || []).forEach((call) => {
    const entry = document.createElement("div");
    entry.className = "wirelog-entry";
    const unverified = call.arguments_verified
      ? ""
      : `<span class="wirelog-unverified">args inferred · reconciled via tools/list on connect</span>`;

    entry.innerHTML = `
      <div class="wirelog-meta">
        <span class="wirelog-seq">#${call.seq}</span>
        <span>${esc(call.endpoint)}</span>
        <span class="wirelog-tool">${esc(call.tool)}</span>
        <span>${call.latency_ms}ms</span>
        ${unverified}
      </div>
      <pre class="wirelog-pre">▶ ${esc(JSON.stringify(call.request, null, 2))}</pre>
      <pre class="wirelog-pre response">◀ ${esc(JSON.stringify(call.response, null, 2))}</pre>
    `;
    body.appendChild(entry);
  });
}

document.getElementById("wirelog-toggle").addEventListener("click", (e) => {
  const body = document.getElementById("wirelog-body");
  const hidden = body.classList.toggle("hidden");
  e.target.textContent = hidden ? "SHOW" : "HIDE";
  e.target.setAttribute("aria-expanded", String(!hidden));
});

document.getElementById("download-csv").addEventListener("click", () => {
  if (!lastBasket.length) return;

  const header = [
    "product_name", "canonical_name", "category", "estimated_quantity",
    "quantity_is_estimate", "required_label", "confidence",
    "matched_catalog_item", "product_id", "catalog_name", "pack_label", "units",
    "price_inr", "line_total_inr", "match_score", "suggested_substitute",
  ];

  // Proper CSV quoting. Double the quotes rather than JSON escaping them.
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = lastBasket.map((item) => header.map((key) => cell(item[key])).join(","));
  const csv = [header.join(","), ...rows].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "clip2cart-basket.csv";
  a.click();
  URL.revokeObjectURL(url);
});
