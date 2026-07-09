#!/usr/bin/env python3
"""
Research script - Δοκιμή για:
1. Βάθος ιστορικού ΚΗΜΔΗΣ API
2. Μέγιστο εύρος ημερών (dateFrom→dateTo)
3. Ανάλυση δείγματος για πεδία προσφορών

Τρέξε με: python scripts/research/api_limits.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kimdis.client import KimdisClient, Endpoint
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# 1. ΔΟΚΙΜΗ ΒΑΘΟΥΣ ΙΣΤΟΡΙΚΟΥ
# ============================================================================

def research_historical_depth():
    """Δοκιμάζει πόσο πίσω υπάρχουν δεδομένα."""
    print("\n" + "="*80)
    print("1. ΔΟΚΙΜΗ ΒΑΘΟΥΣ ΙΣΤΟΡΙΚΟΥ")
    print("="*80)

    with KimdisClient() as client:
        today = date.today()

        # Δοκιμές: 2, 4, 6, 10 χρόνια πίσω
        test_ranges = [
            ("2 χρόνια", 730),
            ("4 χρόνια", 1460),
            ("6 χρόνια", 2190),
            ("10 χρόνια", 3650),
        ]

        for label, days_back in test_ranges:
            from_date = today - timedelta(days=days_back)
            print(f"\n{label} πίσω ({from_date.isoformat()}):")

            for endpoint in [Endpoint.AUCTION, Endpoint.NOTICE, Endpoint.CONTRACT]:
                try:
                    criteria = {
                        "dateFrom": from_date.isoformat(),
                        "dateTo": today.isoformat(),
                    }

                    page = client.search_page(endpoint, criteria, page=0)
                    total = page.get("totalElements", 0)

                    status = "✓" if total > 0 else "✗"
                    print(f"  {status} {endpoint.value:10s}: {total:,} records")

                except Exception as e:
                    print(f"  ✗ {endpoint.value:10s}: {e}")

# ============================================================================
# 2. ΔΟΚΙΜΗ ΜΕΓΙΣΤΟΥ ΕΥΡΟΥΣ ΗΜΕΡΩΝ
# ============================================================================

def research_max_date_range():
    """Δοκιμάζει διάφορα window sizes για να βρει το πραγματικό όριο."""
    print("\n" + "="*80)
    print("2. ΔΟΚΙΜΗ ΜΕΓΙΣΤΟΥ ΕΥΡΟΥΣ ΗΜΕΡΩΝ")
    print("="*80)

    with KimdisClient() as client:
        today = date.today()
        endpoint = Endpoint.AUCTION

        # Δοκιμές: 180, 270, 365, 540, 730 ημέρες
        test_windows = [180, 270, 365, 540, 730]
        print(f"\nΔοκιμή {endpoint.value} endpoint με διάφορα εύρη:")

        for window_days in test_windows:
            from_date = today - timedelta(days=window_days)

            try:
                criteria = {
                    "dateFrom": from_date.isoformat(),
                    "dateTo": today.isoformat(),
                }

                page = client.search_page(endpoint, criteria, page=0)
                total = page.get("totalElements", 0)

                status = "✓" if total > 0 else "✗"
                print(f"  {status} {window_days:3d} ημέρες: {total:,} records")

            except Exception as e:
                print(f"  ✗ {window_days:3d} ημέρες: {e}")

# ============================================================================
# 3. ΕΛΕΓΧΟΣ ΔΕΙΓΜΑΤΟΣ ΓΙΑ ΠΕΔΙΑ ΠΡΟΣΦΟΡΩΝ
# ============================================================================

def research_bid_fields():
    """Ελέγχει δείγμα δεδομένων για πεδία προσφορών."""
    print("\n" + "="*80)
    print("3. ΑΝΑΛΥΣΗ ΔΕΙΓΜΑΤΟΣ ΓΙΑ ΠΕΔΙΑ ΠΡΟΣΦΟΡΩΝ")
    print("="*80)

    with KimdisClient() as client:
        # Λήψη δείγματος από Μάιο 2025 (δηλαδή πρόσφατο)
        from_date = date(2025, 5, 1)
        to_date = date(2025, 5, 31)

        print(f"\nΔείγμα: {from_date} → {to_date}\n")

        for endpoint in [Endpoint.AUCTION, Endpoint.NOTICE]:
            print(f"\n{endpoint.value.upper()}:")
            print("-" * 60)

            sample_records = []
            count = 0
            max_sample = 50

            criteria = {
                "dateFrom": from_date.isoformat(),
                "dateTo": to_date.isoformat(),
            }

            # Συλλογή δείγματος
            for record in client.iter_records(endpoint, criteria):
                sample_records.append(record)
                count += 1
                if count >= max_sample:
                    break

            if not sample_records:
                print(f"  ✗ Δεν βρέθηκαν δεδομένα\n")
                continue

            print(f"  Συλλέχθηκαν: {count} εγγραφές\n")

            # Συλλογή όλων των κλειδιών
            all_keys = set()
            for record in sample_records:
                all_keys.update(record.keys())

            # Ψάχνουμε για πεδία σχετικά με προσφορές
            bid_keywords = [
                "bid", "offer", "bids", "offers", "bidder", "tender",
                "number", "count", "quantity", "single", "proposal"
            ]

            relevant_keys = [
                k for k in all_keys
                if any(kw.lower() in k.lower() for kw in bid_keywords)
            ]

            print(f"  Σχετικά κλειδιά ({len(relevant_keys)}):")
            if relevant_keys:
                for key in sorted(relevant_keys):
                    print(f"    • {key}")
            else:
                print(f"    (κανένα)")

            # Εμφάνιση όλων των κλειδιών
            print(f"\n  Όλα τα κλειδιά ({len(all_keys)}):")
            for i, key in enumerate(sorted(all_keys), 1):
                print(f"    {i:2d}. {key}")

            # Δείγμα πρώτου record
            if sample_records:
                print(f"\n  Δείγμα πρώτου record (περικοπή):")
                first = sample_records[0]
                for i, key in enumerate(sorted(first.keys())[:8], 1):
                    value = first[key]
                    if isinstance(value, (dict, list)):
                        val_str = f"{type(value).__name__}(...)"
                    elif isinstance(value, str):
                        val_str = value[:60] + ("..." if len(value) > 60 else "")
                    else:
                        val_str = str(value)[:60]
                    print(f"      {key}: {val_str}")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        print("\n" + "╔" + "="*78 + "╗")
        print("║" + "  ΚΗΜΔΗΣ API Research - Βάθος & Range Limits & Bid Fields".center(78) + "║")
        print("╚" + "="*78 + "╝")

        research_historical_depth()
        research_max_date_range()
        research_bid_fields()

        print("\n" + "="*80)
        print("✓ ΟΛΟΚΛΗΡΩΘΗΚΕ")
        print("="*80 + "\n")

    except KeyboardInterrupt:
        print("\n\nΠρόγραμμα διακοπής από χρήστη.")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Σφάλμα: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
