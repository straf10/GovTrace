// Static dashboard v1 σκελετός -- διαβάζει data/indicators.json (γραμμένο από
// scripts/build_site_data.py), χωρίς build step ή framework.

const state = { records: [], sortKey: "n_total", sortDir: -1 };

const NUMERIC_KEYS = new Set([
  "year", "n_total", "da_count_pct", "da_value_pct", "hhi", "median_discount_pct",
]);

function fmt(value, key) {
  if (value === null || value === undefined) return "--";
  if (key === "hhi") return Number(value).toFixed(3);
  if (NUMERIC_KEYS.has(key) && key !== "year" && key !== "n_total") return `${Number(value).toFixed(1)}%`;
  return value;
}

function render() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = state.records;
  if (q) {
    rows = rows.filter((r) => (r.name || "").toLowerCase().includes(q));
  }

  rows = rows.slice().sort((a, b) => {
    const av = a[state.sortKey];
    const bv = b[state.sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (av < bv) return -1 * state.sortDir;
    if (av > bv) return 1 * state.sortDir;
    return 0;
  });

  const body = document.getElementById("table-body");
  const cols = ["name", "org_type", "nuts_city", "year", "n_total", "da_count_pct", "da_value_pct", "hhi", "median_discount_pct"];
  body.innerHTML = rows
    .slice(0, 500)
    .map((r) => `<tr>${cols.map((c) => `<td>${fmt(r[c], c)}</td>`).join("")}</tr>`)
    .join("");

  document.getElementById("status").textContent =
    `${rows.length} γραμμές φορέα/έτους (εμφανίζονται έως 500)`;
}

function attachSorting() {
  document.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) {
        state.sortDir *= -1;
      } else {
        state.sortKey = key;
        state.sortDir = 1;
      }
      render();
    });
  });
}

async function main() {
  attachSorting();
  document.getElementById("search").addEventListener("input", render);

  try {
    const res = await fetch("data/indicators.json");
    const payload = await res.json();
    state.records = payload.records;
    render();
  } catch (err) {
    document.getElementById("status").textContent =
      "Σφάλμα φόρτωσης δεδομένων -- τρέξε 'python scripts/build_site_data.py' και σέρβιρε το site/ μέσω HTTP server (όχι file://).";
  }
}

main();
