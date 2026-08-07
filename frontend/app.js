let currentMode = "youtube_url";
let lastBasket = [];

document.querySelector('a[href="#basket-section"]').addEventListener("click", (e) => {
  const results = document.getElementById("results");
  if (results.classList.contains("hidden")) {
    e.preventDefault();
    const errorEl = document.getElementById("error-msg");
    errorEl.textContent = "Generate a basket first, paste a link or transcript below.";
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

  try {
    const res = await fetch("/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_type: currentMode, value }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Something went wrong.");
    }

    renderResults(data);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  } finally {
    loadingEl.classList.add("hidden");
  }
});

function renderResults(data) {
  document.getElementById("summary-total").textContent = data.summary.total_items;
  document.getElementById("summary-matched").textContent = data.summary.matched_items;
  document.getElementById("summary-cost").textContent = data.summary.estimated_total_inr.toFixed(2);

  const extractedList = document.getElementById("extracted-list");
  extractedList.innerHTML = "";
  data.extracted_products.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = `chip confidence-${item.confidence.toLowerCase()}`;
    chip.innerHTML = `${item.product_name} <span class="chip-qty">${item.estimated_quantity}</span>`;
    extractedList.appendChild(chip);
  });

  const tbody = document.querySelector("#basket-table tbody");
  tbody.innerHTML = "";
  lastBasket = data.basket;

  data.basket.forEach((item) => {
    const row = document.createElement("tr");
    const matchedLabel = item.matched_catalog_item
      ? `<span class="badge-matched">✓ ${item.catalog_name}</span>`
      : `<span class="badge-unmatched">✗ no match${item.suggested_substitute ? ` <span class="substitute-hint">(try: ${item.suggested_substitute})</span>` : ""}</span>`;

    row.innerHTML = `
      <td>${item.product_name}</td>
      <td><span class="category-pill">${item.category}</span></td>
      <td>${item.estimated_quantity}</td>
      <td><span class="confidence-dot confidence-${item.confidence.toLowerCase()}"></span>${item.confidence}</td>
      <td>${matchedLabel}</td>
      <td>${item.price_inr != null ? `₹${item.price_inr}` : "-"}</td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

document.getElementById("download-csv").addEventListener("click", () => {
  if (!lastBasket.length) return;

  const header = ["product_name", "category", "estimated_quantity", "confidence", "matched_catalog_item", "catalog_name", "price_inr", "suggested_substitute"];
  const rows = lastBasket.map((item) =>
    header.map((key) => JSON.stringify(item[key] ?? "")).join(",")
  );
  const csv = [header.join(","), ...rows].join("\n");

  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "basket.csv";
  a.click();
  URL.revokeObjectURL(url);
});
