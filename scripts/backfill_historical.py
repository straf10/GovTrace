"""Φάση 1: Backfill ιστορικού από 2020 έως σήμερα — όλες οι οντότητες.

Χρήση:
    python scripts/backfill_historical.py
    python scripts/backfill_historical.py --start-year 2020 --end-year 2026
    python scripts/backfill_historical.py --entities auction contract

Αποθήκευση: data/raw/<entity>_<YYYY>_<MM>.parquet
Report: Ολοκληρωμένα μήνια, παραλείψεις, σύνολο εγγραφών.

Πληρότητα (audit A1): ένας μήνας θεωρείται πλήρης ΜΟΝΟ αν το αρχείο υπάρχει.
Αν η άντληση αποτύχει (network, 5xx, ή σιωπηλή περικοπή σελιδοποίησης) ΔΕΝ
γράφεται κανένα parquet -- ο μήνας καταγράφεται στο failures manifest
(data/raw/_backfill_failures.json) και ξαναδοκιμάζεται σε ένα ενιαίο audit
πέρασμα στο τέλος του run, μαζί με έλεγχο πληρότητας (totalElements) όλων
των ήδη κατεβασμένων μηνών.
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
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kimdis import Endpoint, KimdisClient, PaginationIncompleteError
from kimdis_data import completeness_report, flatten

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_historical")

FAILURES_PATH = Path("data/raw/_backfill_failures.json")


def parquet_row_count(path: Path) -> int:
    """Πλήθος γραμμών από το footer metadata -- δεν διαβάζει τα δεδομένα (P2)."""
    return pq.ParquetFile(path).metadata.num_rows


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def fetch_month(client: KimdisClient, endpoint: Endpoint, year: int, month: int) -> pd.DataFrame | None:
    """Κατεβάζει έναν μήνα. Επιστρέφει None αν είναι κενός· raise αν αποτύχει."""
    date_from, date_to = month_bounds(year, month)
    records = list(client.iter_date_range(endpoint, date_from, date_to))
    if len(records) == 0:
        return None
    return flatten(records)


def load_failures() -> list[dict]:
    if FAILURES_PATH.exists():
        return json.loads(FAILURES_PATH.read_text(encoding="utf-8"))
    return []


def save_failures(failures: list[dict]) -> None:
    FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FAILURES_PATH.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")


def audit_and_repair(client: KimdisClient, out_dir: Path, start_year: int, end_year: int, entities: list[str]) -> list[dict]:
    """Ενιαίο πέρασμα (decision #3): για κάθε μήνα ήδη κατεβασμένο, συγκρίνει το
    πλήθος γραμμών του parquet με ένα φρέσκο totalElements (σελίδα 0 μόνο, φθηνό)·
    αν αποκλίνει, ξανακατεβάζει τον μήνα. Ξαναδοκιμάζει επίσης όσους μήνες είναι
    ήδη γνωστοί ως αποτυχημένοι (failures manifest).

    Επιστρέφει τη λίστα των μηνών που παραμένουν αποτυχημένοι μετά το πέρασμα.
    """
    today = date.today()
    remaining_failures: list[dict] = []
    repaired = 0
    checked = 0

    for year in range(start_year, end_year + 1):
        if year > today.year:
            continue
        max_month = 12 if year < today.year else today.month - 1
        for month in range(1, max_month + 1):
            if year == today.year and month >= today.month:
                continue
            for entity in entities:
                endpoint = Endpoint(entity)
                out_path = out_dir / f"{entity}_{year}_{month:02d}.parquet"

                date_from, date_to = month_bounds(year, month)
                try:
                    page0 = client.search_page(
                        endpoint,
                        {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
                        page=0,
                    )
                except Exception as e:
                    logger.error("Audit: αδύνατος έλεγχος totalElements %s %04d-%02d: %s", entity, year, month, e)
                    remaining_failures.append({"entity": entity, "year": year, "month": month, "error": str(e)})
                    continue

                total_elements = page0.get("totalElements") or 0
                checked += 1

                current_count = parquet_row_count(out_path) if out_path.exists() else 0
                if current_count >= total_elements:
                    continue  # πλήρης (ή σωστά κενός)

                logger.info(
                    "Audit: %s %04d-%02d ελλιπής (%d/%d) -- επαναλήψη λήψης",
                    entity, year, month, current_count, total_elements,
                )
                try:
                    df = fetch_month(client, endpoint, year, month)
                except Exception as e:
                    logger.error("Audit: αποτυχία επανάληψης %s %04d-%02d: %s", entity, year, month, e)
                    remaining_failures.append({"entity": entity, "year": year, "month": month, "error": str(e)})
                    continue

                if df is None:
                    if total_elements > 0:
                        remaining_failures.append(
                            {"entity": entity, "year": year, "month": month, "error": "totalElements>0 αλλά 0 εγγραφές επιστράφηκαν"}
                        )
                        continue
                    if out_path.exists():
                        out_path.unlink()
                    continue

                try:
                    df.to_parquet(out_path, index=False)
                except Exception as e:
                    logger.error("Audit: αποτυχία εγγραφής %s %04d-%02d: %s", entity, year, month, e)
                    remaining_failures.append({"entity": entity, "year": year, "month": month, "error": str(e)})
                    continue

                repaired += 1
                logger.info("  ✓ επανορθώθηκε %s (%d εγγραφές)", out_path.name, len(df))

    logger.info("Audit πέρασμα: %d μήνες ελέγχθηκαν, %d επανορθώθηκαν, %d παραμένουν αποτυχημένοι", checked, repaired, len(remaining_failures))
    return remaining_failures


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
    parser.add_argument("--skip-audit", action="store_true", help="Παράλειψη του τελικού audit/repair περάσματος")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    reports: dict[str, dict] = {}
    completed_months = 0
    total_records = 0
    failures: list[dict] = []

    today = date.today()
    logger.info("Backfill: %d–%d, οντότητες: %s", args.start_year, args.end_year, ", ".join(args.entities))

    # F3/decision #5: καθάρισμα τυχόν ημιτελούς αρχείου του τρέχοντος μήνα από
    # προηγούμενο run, ΠΡΙΝ τον κύριο βρόχο -- ο τρέχων μήνας εξαιρείται πάντα
    # από τον βρόχο (A2), άρα το cleanup πρέπει να γίνεται εδώ για να μην είναι
    # νεκρός κώδικας.
    for entity in args.entities:
        out_path = args.out / f"{entity}_{today.year}_{today.month:02d}.parquet"
        if out_path.exists():
            logger.info("Διαγραφή ημιτελούς αρχείου τρέχοντος μήνα: %s", out_path.name)
            out_path.unlink()

    with KimdisClient() as client:
        for year in range(args.start_year, args.end_year + 1):
            # A2/F3: ο τρέχων μήνας και οποιοσδήποτε μελλοντικός μήνας εξαιρούνται
            # πάντα, ανεξάρτητα από το --end-year -- υπολογισμός από το σημερινό
            # έτος/μήνα, όχι από το args.end_year (ένα --end-year μεγαλύτερο του
            # τρέχοντος έτους παρέκαμπτε πριν αυτή την προστασία).
            if year > today.year:
                continue
            max_month = 12 if year < today.year else today.month - 1
            for month in range(1, max_month + 1):
                for entity in args.entities:
                    endpoint = Endpoint(entity)
                    out_path = args.out / f"{entity}_{year}_{month:02d}.parquet"

                    # Παραλείπουμε αν υπάρχει ήδη (P2: μόνο footer metadata, όχι πλήρες read)
                    if out_path.exists():
                        existing_count = parquet_row_count(out_path)
                        logger.info("  ✓ %s (%d εγγραφές)", out_path.name, existing_count)
                        reports.setdefault(entity, {})[f"{year}-{month:02d}"] = existing_count
                        total_records += existing_count
                        continue

                    try:
                        logger.info("Άντληση %s %04d-%02d...", entity, year, month)
                        df = fetch_month(client, endpoint, year, month)

                        if df is None:
                            logger.info("  (κενό)")
                            continue

                        df.to_parquet(out_path, index=False)

                        report = completeness_report(df, entity)
                        logger.info("  ✓ %s (%d εγγραφές, ΑΔΑΜ: %.1f%%, ημερομηνία: %.1f%%)",
                                   out_path.name, len(df), report["pct_adam"], report["pct_date"])

                        reports.setdefault(entity, {})[f"{year}-{month:02d}"] = len(df)
                        total_records += len(df)
                        completed_months += 1

                    except PaginationIncompleteError as e:
                        # A1: ποτέ δεν γράφουμε ελλιπές parquet -- ο μήνας μένει
                        # απών και καταγράφεται ως αποτυχημένος (decision #4).
                        logger.error("Ελλιπής σελιδοποίηση: %s %04d-%02d: %s", entity, year, month, e)
                        failures.append({"entity": entity, "year": year, "month": month, "error": str(e)})
                    except Exception as e:
                        logger.error("Σφάλμα: %s %04d-%02d: %s", entity, year, month, e)
                        failures.append({"entity": entity, "year": year, "month": month, "error": str(e)})

        if not args.skip_audit:
            logger.info("\n=== Audit/repair πέρασμα (πληρότητα totalElements + γνωστά κενά) ===")
            remaining = audit_and_repair(client, args.out, args.start_year, args.end_year, args.entities)
            failures = remaining

    if failures:
        save_failures(failures)
        logger.warning("%d μήνες παραμένουν αποτυχημένοι -- βλ. %s", len(failures), FAILURES_PATH)
    elif FAILURES_PATH.exists():
        FAILURES_PATH.unlink()

    print("\n=== Ολοκλήρωση Backfill ===")
    print(f"Μήνες: {completed_months}")
    print(f"Σύνολο εγγραφών: {total_records:,.0f}")
    print(f"Αποτυχημένοι μήνες: {len(failures)}")
    print("\nΛεπτομέρειες ανά μήνα:")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    if failures:
        print("\nΑποτυχίες:")
        print(json.dumps(failures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
