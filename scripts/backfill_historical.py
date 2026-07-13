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

Εύρος audit (session 31 finding): ένας κλεισμένος μήνας του ΚΗΜΔΗΣ δεν
αλλάζει ποτέ ξανά (επιβεβαιωμένο εμπειρικά -- δύο πλήρη audit περάσματα σε
234 και 312 συνδυασμούς μήνας×entity, καμία μερική απόκλιση). Άρα το audit
δεν χρειάζεται να ελέγχει ΟΛΟ το ιστορικό κάθε βράδυ: default είναι μόνο οι
τελευταίοι --audit-window κλεισμένοι μήνες + όσοι είναι ήδη γνωστοί ως
αποτυχημένοι στο manifest. Ένα πλήρες πέρασμα (--full-audit) τρέχει
περιοδικά (π.χ. εβδομαδιαία) σαν δίχτυ ασφαλείας για καθυστερημένες
αναρτήσεις σε παλιότερους μήνες.

Μήνες που μόλις απέτυχαν στον κύριο βρόχο ΔΕΝ ξαναδοκιμάζονται στο ίδιο
audit πέρασμα (ένα transient σφάλμα του ΚΗΜΔΗΣ δεν λύνεται μέσα σε λίγα
λεπτά) -- περνάνε κατευθείαν στο manifest και ξαναδοκιμάζονται το επόμενο
βράδυ.
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
from kimdis_data import PERMANENT_AUCTION_GAPS, completeness_report, flatten

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_historical")

FAILURES_PATH = Path("data/raw/_backfill_failures.json")


def parquet_row_count(path: Path) -> int:
    """Πλήθος γραμμών από το footer metadata -- δεν διαβάζει τα δεδομένα (P2)."""
    return pq.ParquetFile(path).metadata.num_rows


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def is_permanent_gap(entity: str, year: int, month: int) -> bool:
    """M1 (review.md): γνωστό μόνιμο κενό (server-side σφάλμα ΚΗΜΔΗΣ, όχι
    transient) -- δεν χρειάζεται να ξαναδοκιμαστεί κάθε βράδυ."""
    return entity == "auction" and (year, month) in PERMANENT_AUCTION_GAPS


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


def recent_closed_months(today: date, window: int) -> list[tuple[int, int]]:
    """Οι --audit-window τελευταίοι ΚΛΕΙΣΜΕΝΟΙ μήνες πριν τον τρέχοντα.

    Υπολογισμός με ακέραιο μηνιαίο index (year*12 + month) ώστε να μην σπάει
    στο όριο έτους (π.χ. Ιανουάριος-Μάρτιος όπου μήνας - i θα έδινε <= 0).
    """
    current_index = today.year * 12 + (today.month - 1)  # 0-based μηνιαίος δείκτης
    months = []
    for i in range(1, window + 1):
        idx = current_index - i
        year, month0 = divmod(idx, 12)
        months.append((year, month0 + 1))
    return months


def build_audit_targets(
    entities: list[str],
    start_year: int,
    end_year: int,
    today: date,
    *,
    full_audit: bool,
    audit_window: int,
    manifest: list[dict],
) -> list[tuple[str, int, int]]:
    """Ρητή λίστα (entity, year, month) προς έλεγχο στο audit πέρασμα.

    full_audit=True: όλο το [start_year, end_year] (παλιά συμπεριφορά, δίχτυ
    ασφαλείας για καθυστερημένες αναρτήσεις -- τρέχει περιοδικά, όχι κάθε βράδυ).
    full_audit=False: μόνο το πρόσφατο παράθυρο + ό,τι είναι ήδη γνωστό ως
    αποτυχημένο στο manifest (και για τα δύο, μόνο για τα entities του run).
    """
    targets: set[tuple[str, int, int]] = set()

    if full_audit:
        for year in range(start_year, end_year + 1):
            if year > today.year:
                continue
            max_month = 12 if year < today.year else today.month - 1
            for month in range(1, max_month + 1):
                if year == today.year and month >= today.month:
                    continue
                for entity in entities:
                    if is_permanent_gap(entity, year, month):
                        continue
                    targets.add((entity, year, month))
        return sorted(targets)

    for year, month in recent_closed_months(today, audit_window):
        if year < start_year or year > end_year:
            continue
        for entity in entities:
            if is_permanent_gap(entity, year, month):
                continue
            targets.add((entity, year, month))

    entity_set = set(entities)
    for entry in manifest:
        if entry["entity"] in entity_set and not is_permanent_gap(entry["entity"], entry["year"], entry["month"]):
            targets.add((entry["entity"], entry["year"], entry["month"]))

    return sorted(targets)


def merge_failure_manifest(
    prior_manifest: list[dict],
    new_failures: list[dict],
    entities: list[str],
    start_year: int,
    end_year: int,
) -> list[dict]:
    """Ενώνει το παλιό manifest με τα νέα αποτελέσματα του run.

    Διατηρεί τις καταχωρίσεις του παλιού manifest για entities ΕΚΤΟΣ αυτού
    του run (ένα run με --entities payment δεν πρέπει να σβήνει γνωστές
    αποτυχίες auction/contract/notice) ΚΑΙ για έτη εκτός [start_year,
    end_year] του ίδιου entity (L6, review.md: ένα χειροκίνητο run με
    στενότερο εύρος -- π.χ. --start-year 2024 --skip-audit -- δεν πρέπει να
    σβήνει σιωπηλά γνωστές αποτυχίες παλιότερων ετών που δεν ελέγχθηκαν καν σε
    αυτό το run), dedup στο υπόλοιπο.
    """
    entity_set = set(entities)
    other_entries = [
        f for f in prior_manifest
        if f["entity"] not in entity_set or f["year"] < start_year or f["year"] > end_year
    ]
    seen = {(f["entity"], f["year"], f["month"]) for f in other_entries}
    merged = list(other_entries)
    for f in new_failures:
        key = (f["entity"], f["year"], f["month"])
        if key not in seen:
            seen.add(key)
            merged.append(f)
    return merged


def audit_and_repair(
    client: KimdisClient,
    out_dir: Path,
    targets: list[tuple[str, int, int]],
    skip: set[tuple[str, int, int]],
) -> list[dict]:
    """Ενιαίο πέρασμα (decision #3): για κάθε (entity, year, month) στο targets,
    συγκρίνει το πλήθος γραμμών του parquet με ένα φρέσκο totalElements (σελίδα 0
    μόνο, φθηνό)· αν αποκλίνει, ξανακατεβάζει τον μήνα.

    Στόχοι στο `skip` (μόλις απέτυχαν στον κύριο βρόχο αυτού του run) ΔΕΝ
    ελέγχονται ξανά -- ένα transient σφάλμα του ΚΗΜΔΗΣ δεν λύνεται μέσα σε λίγα
    λεπτά, οπότε περνάνε κατευθείαν στο manifest χωρίς επιπλέον HTTP κόστος.

    Επιστρέφει τη λίστα των μηνών που παραμένουν αποτυχημένοι μετά το πέρασμα.
    """
    remaining_failures: list[dict] = []
    repaired = 0
    checked = 0

    for entity, year, month in targets:
        if (entity, year, month) in skip:
            remaining_failures.append(
                {"entity": entity, "year": year, "month": month, "error": "απέτυχε στον κύριο βρόχο αυτού του run -- θα ξαναδοκιμαστεί το επόμενο run"}
            )
            continue

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

    logger.info(
        "Audit πέρασμα: %d στόχοι (%d ελέγχθηκαν, %d παραλείφθηκαν ως ήδη αποτυχημένοι αυτό το run), %d επανορθώθηκαν, %d παραμένουν αποτυχημένοι",
        len(targets), checked, len(targets) - checked, repaired, len(remaining_failures),
    )
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
    parser.add_argument(
        "--audit-window", type=int, default=3,
        help="Πλήθος πρόσφατων κλεισμένων μηνών προς έλεγχο πληρότητας (default 3). Αγνοείται με --full-audit.",
    )
    parser.add_argument(
        "--full-audit", action="store_true",
        help="Πλήρες audit περάσμα σε όλο το [--start-year, --end-year] αντί μόνο του πρόσφατου παραθύρου "
             "(δίχτυ ασφαλείας για καθυστερημένες αναρτήσεις σε παλιότερους μήνες -- τρέξε περιοδικά, όχι κάθε βράδυ)",
    )
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

                    if is_permanent_gap(entity, year, month):
                        logger.info("  (γνωστό μόνιμο κενό, παράλειψη) %s %04d-%02d", entity, year, month)
                        continue

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

        main_loop_failure_keys = {(f["entity"], f["year"], f["month"]) for f in failures}
        prior_manifest = load_failures()

        if not args.skip_audit:
            logger.info("\n=== Audit/repair πέρασμα (πληρότητα totalElements + γνωστά κενά) ===")
            targets = build_audit_targets(
                args.entities, args.start_year, args.end_year, today,
                full_audit=args.full_audit, audit_window=args.audit_window,
                manifest=prior_manifest,
            )
            failures = audit_and_repair(client, args.out, targets, main_loop_failure_keys)

        failures = merge_failure_manifest(prior_manifest, failures, args.entities, args.start_year, args.end_year)

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
