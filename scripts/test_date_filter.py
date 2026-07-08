"""
Test αν το API σέβεται το date filter.
Δοκιμή με πολύ συγκεκριμένα date ranges που θα έπρεπε να έχουν διαφορετικά αποτελέσματα.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kimdis.client import KimdisClient, Endpoint
import logging

logging.basicConfig(level=logging.WARNING)

def test_date_filter():
    print("\n" + "="*80)
    print("ΔΟΚΙΜΗ: Σέβασμα Date Filter")
    print("="*80)

    with KimdisClient() as client:
        today = date.today()

        # Δοκιμή 1: Last 30 days vs Last 6 months
        print("\n1️⃣  Recent Data (Last 30 days vs Last 180 days):")
        print("-" * 60)

        test_cases = [
            ("Last 30 days", today - timedelta(days=30)),
            ("Last 60 days", today - timedelta(days=60)),
            ("Last 90 days", today - timedelta(days=90)),
            ("Last 180 days", today - timedelta(days=180)),
            ("Last 365 days", today - timedelta(days=365)),
        ]

        for endpoint in [Endpoint.AUCTION, Endpoint.NOTICE]:
            print(f"\n{endpoint.value.upper()}:")
            for label, from_date in test_cases:
                criteria = {
                    "dateFrom": from_date.isoformat(),
                    "dateTo": today.isoformat(),
                }

                try:
                    page = client.search_page(endpoint, criteria, page=0)
                    total = page.get("totalElements", 0)
                    print(f"  {label:20s}: {total:,} records")
                except Exception as e:
                    print(f"  {label:20s}: ERROR - {e}")

        # Δοκιμή 2: Specific months (αν τα δεδομένα ξεκινάνε πρόσφατα θα δούμε διαφορές)
        print("\n\n2️⃣  Specific Months (Should show progression):")
        print("-" * 60)

        specific_months = [
            ("Jan 2025", date(2025, 1, 1), date(2025, 1, 31)),
            ("Feb 2025", date(2025, 2, 1), date(2025, 2, 28)),
            ("Mar 2025", date(2025, 3, 1), date(2025, 3, 31)),
            ("Apr 2025", date(2025, 4, 1), date(2025, 4, 30)),
            ("May 2025", date(2025, 5, 1), date(2025, 5, 31)),
            ("Jun 2025", date(2025, 6, 1), date(2025, 6, 30)),
            ("Jul 2025 (YTD)", date(2025, 7, 1), date(2025, 7, 8)),
        ]

        for endpoint in [Endpoint.AUCTION, Endpoint.NOTICE]:
            print(f"\n{endpoint.value.upper()}:")
            for label, from_date, to_date in specific_months:
                criteria = {
                    "dateFrom": from_date.isoformat(),
                    "dateTo": to_date.isoformat(),
                }

                try:
                    page = client.search_page(endpoint, criteria, page=0)
                    total = page.get("totalElements", 0)
                    print(f"  {label:20s}: {total:,} records")
                except Exception as e:
                    print(f"  {label:20s}: ERROR")

        # Δοκιμή 3: Ένα παλιό έτος (πχ 2020)
        print("\n\n3️⃣  Old Year (2020 vs 2025):")
        print("-" * 60)

        year_tests = [
            ("2020 (Jan-Dec)", date(2020, 1, 1), date(2020, 12, 31)),
            ("2021 (Jan-Dec)", date(2021, 1, 1), date(2021, 12, 31)),
            ("2022 (Jan-Dec)", date(2022, 1, 1), date(2022, 12, 31)),
            ("2023 (Jan-Dec)", date(2023, 1, 1), date(2023, 12, 31)),
            ("2024 (Jan-Dec)", date(2024, 1, 1), date(2024, 12, 31)),
            ("2025 (Jan-Jul)", date(2025, 1, 1), date(2025, 7, 8)),
        ]

        for endpoint in [Endpoint.AUCTION]:
            print(f"\n{endpoint.value.upper()}:")
            for label, from_date, to_date in year_tests:
                criteria = {
                    "dateFrom": from_date.isoformat(),
                    "dateTo": to_date.isoformat(),
                }

                try:
                    page = client.search_page(endpoint, criteria, page=0)
                    total = page.get("totalElements", 0)
                    print(f"  {label:20s}: {total:,} records")
                except Exception as e:
                    print(f"  {label:20s}: ERROR")

if __name__ == "__main__":
    try:
        test_date_filter()
        print("\n" + "="*80)
        print("✓ Test completed")
        print("="*80 + "\n")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
