from datetime import date

from backfill_historical import build_audit_targets, is_permanent_gap, merge_failure_manifest


def test_is_permanent_gap_only_matches_known_auction_months():
    assert is_permanent_gap("auction", 2021, 2) is True
    assert is_permanent_gap("auction", 2025, 8) is True
    assert is_permanent_gap("auction", 2021, 3) is False
    # M1 (review.md): το κενό είναι τεκμηριωμένο μόνο για auction, όχι για άλλα entities
    assert is_permanent_gap("contract", 2021, 2) is False


def test_build_audit_targets_excludes_permanent_gaps_from_recent_window():
    today = date(2025, 9, 15)
    targets = build_audit_targets(
        ["auction"], 2020, 2026, today,
        full_audit=False, audit_window=3, manifest=[],
    )
    assert ("auction", 2025, 8) not in targets


def test_build_audit_targets_excludes_permanent_gaps_from_full_audit():
    today = date(2026, 1, 15)
    targets = build_audit_targets(
        ["auction"], 2021, 2021, today,
        full_audit=True, audit_window=3, manifest=[],
    )
    assert ("auction", 2021, 2) not in targets
    assert ("auction", 2021, 1) in targets


def test_build_audit_targets_excludes_permanent_gaps_from_manifest_carryover():
    today = date(2026, 1, 15)
    manifest = [{"entity": "auction", "year": 2025, "month": 8, "error": "stale entry"}]
    targets = build_audit_targets(
        ["auction"], 2020, 2026, today,
        full_audit=False, audit_window=3, manifest=manifest,
    )
    assert ("auction", 2025, 8) not in targets


def test_merge_failure_manifest_keeps_other_entities_untouched():
    # L6 (review.md): ένα run με --entities payment δεν πρέπει να σβήσει
    # γνωστές αποτυχίες auction του παλιού manifest.
    prior = [{"entity": "auction", "year": 2022, "month": 3, "error": "old"}]
    merged = merge_failure_manifest(prior, [], entities=["payment"], start_year=2020, end_year=2026)
    assert merged == prior


def test_merge_failure_manifest_keeps_same_entity_out_of_range_entries():
    # L6 (review.md): --start-year 2024 --skip-audit δεν πρέπει να σβήσει
    # σιωπηλά γνωστές αποτυχίες auction του 2022 (εκτός του ελεγμένου εύρους).
    prior = [{"entity": "auction", "year": 2022, "month": 3, "error": "old"}]
    merged = merge_failure_manifest(prior, [], entities=["auction"], start_year=2024, end_year=2026)
    assert merged == prior


def test_merge_failure_manifest_drops_stale_in_range_entries_not_reported_again():
    # μηνας εντός εύρους που ΔΕΝ αποτυγχάνει πλέον -- πρέπει να φύγει από το manifest.
    prior = [{"entity": "auction", "year": 2024, "month": 3, "error": "old"}]
    merged = merge_failure_manifest(prior, [], entities=["auction"], start_year=2020, end_year=2026)
    assert merged == []


def test_merge_failure_manifest_dedupes_and_adds_new_failures():
    prior = [{"entity": "auction", "year": 2024, "month": 3, "error": "old"}]
    new_failures = [
        {"entity": "auction", "year": 2024, "month": 3, "error": "still failing"},
        {"entity": "auction", "year": 2025, "month": 1, "error": "new"},
    ]
    merged = merge_failure_manifest(prior, new_failures, entities=["auction"], start_year=2020, end_year=2026)
    assert merged == new_failures
