"""Static dashboard v1 (Checkpoint 1β -- βλ. docs/MASTERPLAN_ELLADA_3.0_2026-07.md
Παράρτημα Α): εξάγει τα ήδη υπολογισμένα δεδομένα από data/processed/*.csv
σε ένα ενιαίο JSON που καταναλώνει το site/ (static-first, €0).

Ιστορική σημείωση: γράφτηκε για τον προ-Astro σκελετό· το frontend είναι
πλέον Astro (απόφαση session 5), το JSON παραμένει το ίδιο interface.

Χρήση:
    python scripts/build_site_data.py

Γράφει site/data/indicators.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pandas as pd

from kimdis_data import RAW_DIR, PROCESSED_DIR, is_valid_vat_checksum, load_entity, normalize_vat, sanitize_value

SITE_DATA_DIR = PROCESSED_DIR.parent.parent / "site" / "public" / "data"

PUBLISHED_CSVS = [
    "entities.csv",
    "indicator_direct_award.csv",
    "indicator_hhi.csv",
    "indicator_single_bid.csv",
    "indicator_discount_rate.csv",
    "indicator_deadlines.csv",
    "indicator_composite.csv",
]

COMPLETENESS_ENTITIES = ["auction", "contract", "notice", "payment"]
COMPLETENESS_COLS = [
    "organizationVatNumber",
    "objectDetailsList",
    "totalCostWithVAT",
    "totalCostWithoutVAT",
    "budget",
]
CPV_RE = re.compile(r"^\d{8}(?:-\d)?$")
PERMANENT_AUCTION_GAPS = {(2021, 2), (2025, 8)}
RAW_FILENAME_RE = re.compile(r"^([a-z]+)_(\d{4})_(\d{2})$")

# #13 (CHECK 2026-07-11): τα entities που τροφοδοτούν τους κύριους δείκτες.
# Το footer δεν πρέπει να υπερ-υποσχεθεί κάλυψη επειδή π.χ. μόνο το notice
# έφερε νέο μήνα (ή επειδή το payment έχει 78 μήνες backfill).
CORE_COVERAGE_ENTITIES = ("auction", "contract", "notice")


def latest_complete_month(raw_dir=RAW_DIR) -> str | None:
    """«YYYY-MM» κάλυψης: το MIN των per-entity max μηνών πάνω στα core
    entities (auction, contract, notice).

    Ο τρέχων μήνας δεν κατεβαίνει ποτέ (A2 guard στο backfill_historical.py),
    άρα κάθε αρχείο που υπάρχει είναι εξ ορισμού πλήρης μήνας. #13 (CHECK
    2026-07-11): πριν, το max πάνω σε ΟΛΑ τα entities μπορούσε να δείξει
    «κάλυψη έως <νέος μήνας>» ενώ το auction (η βάση των κύριων δεικτών)
    είχε αποτύχει -- τώρα δημοσιεύεται ο μήνας που έχουν ΟΛΑ τα core
    entities. Entities εκτός λίστας (π.χ. payment) αγνοούνται.
    """
    if not raw_dir.exists():
        return None
    max_per_entity: dict[str, tuple[int, int]] = {}
    for path in raw_dir.glob("*.parquet"):
        m = RAW_FILENAME_RE.match(path.stem)
        if not m:
            continue
        entity, year, month = m.group(1), int(m.group(2)), int(m.group(3))
        if entity not in CORE_COVERAGE_ENTITIES:
            continue
        current = max_per_entity.get(entity)
        if current is None or (year, month) > current:
            max_per_entity[entity] = (year, month)
    if not max_per_entity:
        return None
    year, month = min(max_per_entity.values())
    return f"{year:04d}-{month:02d}"


def read_csv_or_empty(name: str) -> pd.DataFrame:
    path = PROCESSED_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"organization_vat": str, "vat": str})


def merge_indicators() -> list[dict]:
    entities = read_csv_or_empty("entities.csv")
    da = read_csv_or_empty("indicator_direct_award.csv")
    hhi = read_csv_or_empty("indicator_hhi.csv")
    sb = read_csv_or_empty("indicator_single_bid.csv")
    dr = read_csv_or_empty("indicator_discount_rate.csv")
    dl = read_csv_or_empty("indicator_deadlines.csv")
    comp = read_csv_or_empty("indicator_composite.csv")

    if da.empty:
        return []

    # Ένωση σε (vat, year) -- session 6: όλοι οι δείκτες πλέον υπολογίζονται
    # ανά κανονικοποιημένο ΑΦΜ (kimdis_data.canonical_vat), όχι ανά όνομα
    # φορέα (βλ. compute_indicators_v1.py). Το vat είναι πλέον αξιόπιστο και
    # ποτέ None στις γραμμές δεικτών, οπότε η ένωση πάνω σε vat δεν κινδυνεύει
    # να πολλαπλασιάσει γραμμές.
    merged = da.rename(columns={"organization_vat": "vat", "organization_name": "name"})
    if not hhi.empty:
        merged = merged.merge(
            hhi.rename(columns={"organization_vat": "vat"})[
                ["vat", "year", "n_contracts", "hhi", "top1_share"]
            ],
            on=["vat", "year"],
            how="left",
        )
    if not dr.empty:
        merged = merged.merge(
            dr.rename(columns={"organization_vat": "vat"})[
                ["vat", "year", "n_linked", "median_discount_pct", "pct_near_zero_discount"]
            ],
            on=["vat", "year"],
            how="left",
        )
    if not sb.empty:
        merged = merged.merge(
            sb.rename(columns={"organization_vat": "vat"})[
                ["vat", "year", "n_with_bids", "n_single_bid", "single_bid_pct", "coverage_pct", "n_bids_outliers"]
            ].rename(columns={"coverage_pct": "single_bid_coverage_pct"}),
            on=["vat", "year"],
            how="left",
        )
    if not dl.empty:
        merged = merged.merge(
            dl[["vat", "year", "n_notices", "median_deadline_days", "pct_short_deadline", "coverage_pct"]]
            .rename(columns={"coverage_pct": "deadline_coverage_pct"}),
            on=["vat", "year"],
            how="left",
        )
    if not comp.empty:
        merged = merged.merge(
            comp[["vat", "year", "composite_score", "n_flags"]],
            on=["vat", "year"],
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


def has_valid_cpv(raw: object) -> bool:
    if not raw:
        return False
    try:
        details = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(details, list):
        return False
    for item in details:
        if not isinstance(item, dict):
            continue
        for cpv in item.get("cpvs") or []:
            key = str(cpv.get("key") or "").strip()
            if CPV_RE.fullmatch(key):
                return True
    return False


def build_completeness_report() -> dict:
    frames = []
    by_entity = []
    for entity in COMPLETENESS_ENTITIES:
        df = load_entity(entity, columns=COMPLETENESS_COLS)
        if df.empty:
            continue
        df["entity"] = entity
        frames.append(df)
        by_entity.append(_completeness_rows(df, entity))

    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    by_year = _completeness_rows(rows, "all") if not rows.empty else []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "permanent_auction_gaps": [
            {"year": year, "month": month, "label": f"{year}-{month:02d}"}
            for year, month in sorted(PERMANENT_AUCTION_GAPS)
        ],
        "by_year": by_year,
        "by_entity_year": [row for rows_for_entity in by_entity for row in rows_for_entity],
    }


def _completeness_rows(df: pd.DataFrame, entity: str) -> list[dict]:
    if df.empty:
        return []

    vat_norm = df["organizationVatNumber"].map(normalize_vat)
    value = sanitize_value(
        pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce")
        .fillna(pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce"))
        .fillna(pd.to_numeric(df.get("budget"), errors="coerce"))
    )
    work = pd.DataFrame(
        {
            "year": df["_source_year"],
            "entity": entity,
            "vat_format_valid": vat_norm.notna(),
            "vat_checksum_valid": vat_norm.map(lambda v: is_valid_vat_checksum(v) if v else False),
            "cpv_valid": df["objectDetailsList"].map(has_valid_cpv),
            "amount_valid": value.notna() & (value > 0),
        }
    )

    rows = []
    for year, g in work.groupby("year"):
        n = len(g)
        gap_labels = [
            f"{gap_year}-{gap_month:02d}"
            for gap_year, gap_month in sorted(PERMANENT_AUCTION_GAPS)
            if gap_year == int(year) and entity in {"all", "auction"}
        ]
        rows.append(
            {
                "year": int(year),
                "entity": entity,
                "records": int(n),
                "pct_vat_format_valid": round(100.0 * float(g["vat_format_valid"].mean()), 2) if n else None,
                "pct_vat_checksum_valid": round(100.0 * float(g["vat_checksum_valid"].mean()), 2) if n else None,
                "pct_cpv_valid": round(100.0 * float(g["cpv_valid"].mean()), 2) if n else None,
                "pct_amount_valid": round(100.0 * float(g["amount_valid"].mean()), 2) if n else None,
                "auction_gaps": gap_labels,
            }
        )
    return rows


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
        "data_coverage_month": latest_complete_month(),
        "n_organizations_years": len(records),
        "records": records,
    }
    out_path = SITE_DATA_DIR / "indicators.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"Site data -> {out_path} ({len(records)} rows)".encode("ascii", "replace").decode("ascii"))

    completeness = build_completeness_report()
    completeness_path = SITE_DATA_DIR / "completeness_report.json"
    completeness_path.write_text(json.dumps(completeness, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"Completeness report -> {completeness_path}".encode("ascii", "replace").decode("ascii"))

    # Δημοσιεύσιμα CSV για τη σελίδα /dedomena/ (μόνο δείκτες που το site
    # ήδη δημοσιεύει -- ΟΧΙ bid_splitting §4.5 / vat_resolver, βλ. MEMORY).
    for name in PUBLISHED_CSVS:
        src = PROCESSED_DIR / name
        if src.exists():
            (SITE_DATA_DIR / name).write_bytes(src.read_bytes())
            print(f"CSV -> {SITE_DATA_DIR / name}".encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()
