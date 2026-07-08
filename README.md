# Rousfeti AI — Διαφάνεια Δημοσίων Συμβάσεων

Analysis and visualization of Greek public procurement data (ΚΗΜΔΗΣ) to identify procurement anomalies and risk indicators.

## 📊 What's included

- **Data pipeline**: Fetch historical procurement data from ΚΗΜΔΗΣ API (2020+)
- **Risk indicators**: Entity tables with bid-splitting, direct award %, and Herfindahl-Hirschman Index (HHI) anomalies
- **Dashboard UI**: Static HTML/JS dashboard for exploring risk indicators by organization, region, and time

## 🚀 Quick start

### Setup

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### Run the dashboard locally

Open `site/index.html` in a browser — it's a static site that loads data from `site/data/indicators.json`.

### Rebuild the data

```bash
python scripts/build_entity_table.py  # Fetch/process ΚΗΜΔΗΣ data
python scripts/build_site_data.py     # Generate dashboard JSON
```

## 📁 Structure

```
src/
  kimdis/                    # ΚΗΜΔΗΣ API client & data processing
scripts/
  build_entity_table.py      # Fetch & transform raw procurement data
  build_site_data.py         # Generate indicators.json for dashboard
site/
  index.html, app.js, style.css   # Static dashboard (no build step)
  data/
    indicators.json          # Dashboard data (build output)
docs/
  METHODOLOGY.md             # Indicator definitions & methodology
  DISCLAIMER.md              # Legal disclaimer & methodology caveats
  PLAN.md                    # Roadmap & phase plan
  RESEARCH_RESULTS.md        # Research & findings
```

## ⚙️ Technical

- **Python 3.x** — data pipeline
- **httpx** — async HTTP client for ΚΗΜΔΗΣ API
- **pandas/pyarrow** — data transformation
- **Vanilla JS** — dashboard (no framework)

## 📖 Documentation

See `docs/` folder for:
- **METHODOLOGY.md** — risk indicator definitions
- **DISCLAIMER.md** — legal notice & caveats
- **PLAN.md** — development roadmap

## 🔗 Data source

ΚΗΜΔΗΣ API: Central registry of Greek public contracts
- Rate limit: 300 req/min
- Historical data: 2020+
- Last sync: See backfill.log (local, not tracked)

---

**Status**: Phase 1 (entity table, core indicators, dashboard skeleton)
