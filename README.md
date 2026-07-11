# Ellada 3.0 — Διαφάνεια Δημοσίων Συμβάσεων

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

The dashboard is an [Astro](https://astro.build/) static site in `site/`:

```bash
cd site
npm install
npm run dev      # local dev server
npm run build    # full static build (~3,200 pages)
```

### Rebuild the data

```bash
python scripts/backfill_historical.py    # Fetch raw ΚΗΜΔΗΣ history (long-running)
python scripts/build_entity_table.py     # Entity registry + VAT resolver
python scripts/compute_indicators_v1.py  # Risk indicators
python scripts/build_site_data.py        # indicators.json for the /foreis/ table
python scripts/build_foreas_data.py      # Per-entity profile data
```

## 📁 Structure

```
src/
  kimdis/                    # ΚΗΜΔΗΣ API client (rate limiting, pagination, retries)
scripts/
  backfill_historical.py     # Full-history backfill (2020+) with completeness audit
  build_entity_table.py      # Entity registry (normalized VAT) + persisted VAT resolver
  compute_indicators_v1.py   # Risk indicators (direct-award %, HHI, bid-splitting, discount)
  build_site_data.py         # Generate indicators.json for the /foreis/ table
  build_foreas_data.py       # Per-entity profile data for /foreas/<vat>/ pages
site/                        # Astro static site (Node build, zero-JS-by-default pages)
  src/pages/                 # /, /foreis/, /foreas/<vat>/, /methodologia/, ...
  public/data/               # Published JSON (build output, not tracked in git)
tests/                       # pytest suite (29 tests)
docs/
  METHODOLOGY.md             # Indicator definitions & methodology (+ changelog)
  DISCLAIMER.md              # Legal disclaimer & correction/right-of-reply process
  MASTERPLAN_ELLADA_3.0_2026-07.md  # Consolidated masterplan (planning entry point, incl. checkpoint definitions)
  SPRINTS_DETAILED.md        # Week-by-week sprint breakdown
  UI_UX_SPEC_FINAL.md        # Per-page UI/UX spec & design decisions
  NEO4J_INTEGRATION_FINAL.md # Graph analytics strategy (Φάση 2, offline)
  RESEARCH_RESULTS.md        # ΚΗΜΔΗΣ API research & findings
  tech_report.md             # Technical audit (2026-07-09) & fix plan
  MEMORY.md                  # Per-session project log & current state
```

## ⚙️ Technical

- **Python 3.x** — data pipeline
- **httpx** — async HTTP client for ΚΗΜΔΗΣ API
- **pandas/pyarrow** — data transformation
- **Vanilla JS** — dashboard (no framework)

## 📖 Documentation

See `docs/` folder for:
- **METHODOLOGY.md** — risk indicator definitions (versioned changelog)
- **DISCLAIMER.md** — legal notice, correction process & right of reply
- **MASTERPLAN_ELLADA_3.0_2026-07.md** — consolidated masterplan & checkpoint definitions (start here)
- **SPRINTS_DETAILED.md** — week-by-week sprint breakdown
- **UI_UX_SPEC_FINAL.md** — site structure & UI/UX decisions
- **tech_report.md** — technical audit & fix plan
- **MEMORY.md** — per-session project log (current state)

## 🔗 Data source

ΚΗΜΔΗΣ API: Central registry of Greek public contracts
- Rate limit: 350 req/min documented — we run conservatively at 300
- Historical data: 2020+
- Last sync: See backfill.log (local, not tracked)

---

**Status**: Phase 1 — Sprint C complete (2026-07-09). Full backfill re-run, pipeline re-run (12.827 unique VATs, 6.191 entity profiles), first live deploy, and CI wired up. **Live site**: https://ellada30.pages.dev. See [docs/MEMORY.md](docs/MEMORY.md) for the detailed session log.

### Deploying

Static site, hosted on Cloudflare Pages (free tier), project `ellada30`. Site data (`site/src/data/`, `site/public/data/`) is gitignored — CI cannot rebuild it from a bare checkout, so the deploy flow is:

```bash
cd site && npm run build                                    # 1. build site/dist locally
cd dist && zip -q -r -X ../../site-dist.zip .                # 2. zip (POSIX zip only — PowerShell's
                                                               #    Compress-Archive writes backslash paths
                                                               #    that break `unzip` on the Linux CI runner)
cd ../.. 
gh release upload site-data-latest site-dist.zip --clobber   # 3. publish as the CI's data source
gh workflow run deploy.yml                                   # 4. trigger the Cloudflare Pages deploy
```

`.github/workflows/deploy.yml` runs on `workflow_dispatch` (manual trigger, not every push — the site only changes when the data pipeline is re-run), checks out the repo (needed for `site/functions/`, which isn't part of `site-dist.zip`), and deploys via `cloudflare/wrangler-action`. Repo secrets `CLOUDFLARE_API_TOKEN` (Pages:Edit only) and `CLOUDFLARE_ACCOUNT_ID` are required.

**`/diorthoseis/` correction form (D11) — one-time Cloudflare Pages project setup, not automated by CI:**
1. Create a [Resend](https://resend.com) account **using `ellada30@proton.me` as the account email** (sandbox mode without a verified domain can only deliver to the account's own address — that's exactly our recipient, so no custom domain is needed yet). Generate an API key.
2. Create a Cloudflare KV namespace (e.g. `wrangler kv namespace create RATE_LIMIT_KV`) and bind it to the `ellada30` Pages project as `RATE_LIMIT_KV` (Pages dashboard → Settings → Functions → KV namespace bindings, both Production and Preview).
3. Add `RESEND_API_KEY` as a Pages **secret** on the `ellada30` project (dashboard → Settings → Environment variables, or `wrangler pages secret put RESEND_API_KEY --project-name=ellada30`). Do not put it in `.env` or any committed file.
4. Once a custom domain exists (S4), verify it in Resend and switch `from:` in `site/functions/api/submit-correction.js` off `onboarding@resend.dev` to remove the sandbox recipient restriction entirely.

**Latest changes**: `4759863` Cloudflare Pages deploy workflow + CI · `12e0088` audit-fix follow-up · `faf592d` security/bug fixes (XSS escaping, pagination completeness, unified VAT resolver, test suite) · `c9ae60b` Sprint B — entity profiles + VAT re-key.
