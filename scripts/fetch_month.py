"""Checkpoint 0: κατεβάζει έναν πλήρη μήνα αναθέσεων + συμβάσεων σε Parquet.

Χρήση:
    python scripts/fetch_month.py --year 2025 --month 5
    python scripts/fetch_month.py --year 2025 --month 5 --entities auction contract payment

Γράφει data/raw/<entity>_<YYYY>_<MM>.parquet και τυπώνει report πληρότητας:
% εγγραφών με ΑΔΑΜ, ημερομηνία, ποσό.
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
logger = logging.getLogger("fetch_month")


def flatten(records: list[dict]) -> pd.DataFrame:
    """json_normalize στο πρώτο επίπεδο· τα εναπομείναντα nested list/dict σε JSON strings.

    Κρατάμε έτσι το 100% της πληροφορίας στο Parquet χωρίς εύθραυστα nested schemas.
    """
    df = pd.json_normalize(records)
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return df


def completeness_report(df: pd.DataFrame, entity: str) -> dict[str, float]:
    """% εγγραφών με ΑΔΑΜ, ημερομηνία, ποσό (τα κριτήρια του Checkpoint 0)."""
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
    logger.info("%s: %s", entity, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True, choices=range(1, 13))
    parser.add_argument(
        "--entities",
        nargs="+",
        default=["auction", "contract"],
        choices=[e.value for e in Endpoint],
    )
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    date_from = date(args.year, args.month, 1)
    date_to = date(args.year, args.month, calendar.monthrange(args.year, args.month)[1])
    args.out.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    with KimdisClient() as client:
        for entity in args.entities:
            endpoint = Endpoint(entity)
            logger.info("Άντληση %s για %s → %s", entity, date_from, date_to)
            records = list(client.iter_date_range(endpoint, date_from, date_to))
            df = flatten(records)
            out_path = args.out / f"{entity}_{args.year}_{args.month:02d}.parquet"
            df.to_parquet(out_path, index=False)
            logger.info("Γράφτηκε %s (%d εγγραφές)", out_path, len(df))
            reports[entity] = completeness_report(df, entity)

    print("\n=== Report πληρότητας (Checkpoint 0) ===")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
