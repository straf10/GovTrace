"""Static dashboard v1 σκελετός (PLAN.md Checkpoint 1β): εξάγει τα ήδη
υπολογισμένα δεδομένα από data/processed/*.csv σε ένα ενιαίο JSON που
διαβάζει το site/ (καθαρό HTML/CSS/JS, χωρίς build step -- static-first,
€0, PLAN.md §1).

Δεν είναι η τελική επιλογή frontend framework (Observable Framework/Next.js
παραμένουν στο τραπέζι στο PLAN.md) -- είναι ένας ελάχιστος σκελετός για να
υπάρχει κάτι δημοσιεύσιμο ενώ τρέχει το backfill.

Χρήση:
    python scripts/build_site_data.py

Γράφει site/data/indicators.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from kimdis_data import PROCESSED_DIR

SITE_DATA_DIR = PROCESSED_DIR.parent.parent / "site" / "public" / "data"


def read_csv_or_empty(name: str) -> pd.DataFrame:
    path = PROCESSED_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"organization_vat": str, "vat": str})


def merge_indicators() -> list[dict]:
    entities = read_csv_or_empty("entities.csv")
    da = read_csv_or_empty("indicator_direct_award.csv")
    hhi = read_csv_or_empty("indicator_hhi.csv")
    dr = read_csv_or_empty("indicator_discount_rate.csv")

    if da.empty:
        return []

    # Ένωση σε (name, year) -- όχι (vat, year): κάθε δείκτης υπολογίζει το δικό
    # του "πιο συχνό ΑΦΜ" ανά όνομα φορέα (βλ. primary_vat στο
    # compute_indicators_v1.py), οπότε το vat μπορεί να διαφέρει ή να λείπει
    # (None) ανάμεσα σε da/hhi/dr για τον ίδιο φορέα -- ένωση πάνω σε vat θα
    # έχανε αντιστοιχίες ή, χειρότερα, θα πολλαπλασίαζε γραμμές όταν πολλές
    # None τιμές ταιριάζουν μεταξύ τους.
    merged = da.rename(columns={"organization_vat": "vat", "organization_name": "name"})
    if not hhi.empty:
        merged = merged.merge(
            hhi.rename(columns={"organization_name": "name"})[
                ["name", "year", "n_contracts", "hhi", "top1_share"]
            ],
            on=["name", "year"],
            how="left",
        )
    if not dr.empty:
        merged = merged.merge(
            dr.rename(columns={"organization_name": "name"})[
                ["name", "year", "n_linked", "median_discount_pct", "pct_near_zero_discount"]
            ],
            on=["name", "year"],
            how="left",
        )
    if not entities.empty:
        merged = merged.merge(
            entities[["vat", "org_type", "nuts_city"]],
            on="vat",
            how="left",
        )

    merged = merged.astype(object).where(pd.notna(merged), None)
    return merged.to_dict(orient="records")


# Ελάχιστο πλήθος αναθέσεων φορέα/έτους για να εμφανιστεί στο dashboard v1
# (καθαρά πρακτικό όριο μεγέθους JSON/UX -- δεν είναι το ίδιο με τα κατώφλια
# δημοσίευσης ανά δείκτη του METHODOLOGY.md §5, τα οποία εφαρμόζονται ήδη
# στα CSV του data/processed/).
MIN_N_TOTAL_FOR_SITE = 5


def main() -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    records = merge_indicators()
    records = [r for r in records if (r.get("n_total") or 0) >= MIN_N_TOTAL_FOR_SITE]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_organizations_years": len(records),
        "records": records,
    }
    out_path = SITE_DATA_DIR / "indicators.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"Site data -> {out_path} ({len(records)} rows)".encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()
