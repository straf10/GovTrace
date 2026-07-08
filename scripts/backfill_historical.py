"""Φάση 1: Backfill ιστορικού από 2020 έως σήμερα — όλες οι οντότητες.

Χρήση:
    python scripts/backfill_historical.py
    python scripts/backfill_historical.py --start-year 2020 --end-year 2026
    python scripts/backfill_historical.py --entities auction contract

Αποθήκευση: data/raw/<entity>_<YYYY>_<MM>.parquet
Report: Ολοκληρωμένα μήνια, παραλείψεις, σύνολο εγγραφών.
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kimdis import Endpoint, KimdisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_historical")


def flatten(records: list[dict]) -> pd.DataFrame:
    """json_normalize στο πρώτο επίπεδο· εναπομείναντα nested list/dict σε JSON strings."""
    df = pd.json_normalize(records)
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return df


def completeness_report(df: pd.DataFrame, entity: str) -> dict[str, float]:
    """% εγγραφών με ΑΔΑΜ, ημερομηνία, ποσό."""
    n = len(df)
    if n == 0:
        return {"records": 0}

    def pct(mask: pd.Series) -> float:
        return round(100.0 * mask.sum() / n, 2)

    has_adam = df["referenceNumber"].notna() & (df["referenceNumber"].astype(str).str.len() > 0)
    has_date = df["submissionDate"].notna() if "submissionDate" in df.columns else pd.Series(False, index=df.index)
    amount_cols = [c for c in ("totalCostWithVAT", "totalCostWithoutVAT", "budget") if c in df.columns]
    has_amount = pd.Series(False, index=df.index)
    for col in amount_cols:
        has_amount |= pd.to_numeric(df[col], errors="coerce").notna()

    report = {
        "records": n,
        "pct_adam": pct(has_adam),
        "pct_date": pct(has_date),
        "pct_amount": pct(has_amount),
    }
    if "organizationVatNumber" in df.columns:
        report["pct_org_vat"] = pct(df["organizationVatNumber"].notna())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument(
        "--entities",
        nargs="+",
        # payment αφαιρέθηκε προσωρινά από το default: είναι ο πιο ογκώδης όγκος
        # δεδομένων και δεν χρησιμοποιείται ακόμα σε κανέναν δείκτη v1· θα κατέβει
        # ξεχωριστά αργότερα. notice προστέθηκε γιατί χρειάζεται για §4.6/§4.7.
        default=["auction", "contract", "notice"],
        choices=[e.value for e in Endpoint],
    )
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    completed_months = 0
    total_records = 0

    logger.info("Backfill: %d–%d, οντότητες: %s", args.start_year, args.end_year, ", ".join(args.entities))

    with KimdisClient() as client:
        for year in range(args.start_year, args.end_year + 1):
            max_month = 12 if year < args.end_year else date.today().month
            for month in range(1, max_month + 1):
                date_from = date(year, month, 1)
                date_to = date(year, month, calendar.monthrange(year, month)[1])

                for entity in args.entities:
                    endpoint = Endpoint(entity)
                    out_path = args.out / f"{entity}_{year}_{month:02d}.parquet"

                    # Παραλείπουμε αν υπάρχει ήδη
                    if out_path.exists():
                        existing_df = pd.read_parquet(out_path)
                        existing_count = len(existing_df)
                        logger.info("  ✓ %s (%d εγγραφές)", out_path.name, existing_count)
                        if entity not in reports:
                            reports[entity] = {}
                        reports[entity][f"{year}-{month:02d}"] = existing_count
                        total_records += existing_count
                        continue

                    try:
                        logger.info("Άντληση %s %04d-%02d...", entity, year, month)
                        records = list(client.iter_date_range(endpoint, date_from, date_to))

                        if len(records) == 0:
                            logger.info("  (κενό)")
                            continue

                        df = flatten(records)
                        df.to_parquet(out_path, index=False)

                        report = completeness_report(df, entity)
                        logger.info("  ✓ %s (%d εγγραφές, ΑΔΑΜ: %.1f%%, ημερομηνία: %.1f%%)",
                                   out_path.name, len(df), report["pct_adam"], report["pct_date"])

                        if entity not in reports:
                            reports[entity] = {}
                        reports[entity][f"{year}-{month:02d}"] = len(df)
                        total_records += len(df)
                        completed_months += 1

                    except Exception as e:
                        logger.error("Σφάλμα: %s %04d-%02d: %s", entity, year, month, e)

    print("\n=== Ολοκλήρωση Backfill ===")
    print(f"Μήνες: {completed_months}")
    print(f"Σύνολο εγγραφών: {total_records:,.0f}")
    print("\nΛεπτομέρειες ανά μήνα:")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
