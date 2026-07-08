"""Δοκιμή ευρους ιστορικού: ποια χρόνια έχουν δεδομένα στο ΚΗΜΔΗΣ API.

Χρήση:
    python scripts/test_data_range.py
    python scripts/test_data_range.py --start-year 2010 --end-year 2025

Αποτέλεσμα: πίνακας με τα έτη που έχουν δεδομένα + αριθμό αναθέσεων.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kimdis import Endpoint, KimdisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("test_data_range")


def test_year(client: KimdisClient, year: int, entity: Endpoint = Endpoint.AUCTION) -> int:
    """Δοκιμάζει αν υπάρχουν δεδομένα για το έτος (πρώτο τρίμηνο).

    Επιστρέφει τον αριθμό αναθέσεων που βρέθηκαν.
    """
    date_from = date(year, 1, 1)
    date_to = date(year, 3, 31)  # Q1

    try:
        records = list(client.iter_date_range(entity, date_from, date_to))
        return len(records)
    except Exception as e:
        logger.warning("Σφάλμα για %d: %s", year, e)
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2002)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--entity", default="auction", choices=["auction", "contract", "payment"])
    args = parser.parse_args()

    entity = Endpoint(args.entity)
    results = []

    logger.info(
        "Δοκιμή ευρους %d→%d (%s) — πρώτο τρίμηνο κάθε έτους",
        args.start_year,
        args.end_year,
        args.entity,
    )

    with KimdisClient() as client:
        for year in range(args.start_year, args.end_year + 1):
            count = test_year(client, year, entity)
            results.append({"year": year, "records": count})
            status = "✓" if count > 0 else "✗"
            logger.info("  %4d: %s %8d records", year, status, count)

    df = pd.DataFrame(results)
    has_data = df[df["records"] > 0]

    print("\n=== Περίληψη ===")
    print(f"Περίοδος: {args.start_year}–{args.end_year}")
    print(f"Έτη με δεδομένα: {len(has_data)}")
    if len(has_data) > 0:
        print(f"  Από {has_data['year'].min():.0f} έως {has_data['year'].max():.0f}")
        print(f"  Σύνολο εγγραφών (Q1): {has_data['records'].sum():,.0f}")
    print("\n" + df.to_string(index=False))


if __name__ == "__main__":
    main()
