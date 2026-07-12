from pathlib import Path

import build_site_data
from build_site_data import latest_complete_month, merge_indicators


def _touch(path: Path) -> None:
    path.write_bytes(b"")


def test_latest_complete_month_is_min_of_core_entity_maxima(tmp_path):
    # #13 (CHECK 2026-07-11): min πάνω στα per-entity max -- το footer δεν
    # υπερ-υπόσχεται κάλυψη όταν ένα core entity έχει μείνει πίσω.
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "contract_2024_05.parquet")
    _touch(tmp_path / "notice_2024_01.parquet")

    assert latest_complete_month(tmp_path) == "2024-01"


def test_latest_complete_month_uses_max_within_entity(tmp_path):
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "auction_2024_06.parquet")
    _touch(tmp_path / "contract_2024_06.parquet")
    _touch(tmp_path / "notice_2024_06.parquet")

    assert latest_complete_month(tmp_path) == "2024-06"


def test_latest_complete_month_ignores_non_core_entities(tmp_path):
    # Το payment (78 μήνες backfill) δεν πρέπει να «τραβάει» την κάλυψη.
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "contract_2024_03.parquet")
    _touch(tmp_path / "notice_2024_03.parquet")
    _touch(tmp_path / "payment_2026_06.parquet")

    assert latest_complete_month(tmp_path) == "2024-03"


def test_latest_complete_month_empty_dir_returns_none(tmp_path):
    assert latest_complete_month(tmp_path) is None


def test_latest_complete_month_missing_dir_returns_none(tmp_path):
    assert latest_complete_month(tmp_path / "does-not-exist") is None


def test_latest_complete_month_ignores_non_matching_files(tmp_path):
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "_backfill_failures.json")

    assert latest_complete_month(tmp_path) == "2024-03"


def test_merge_indicators_benford_all_period_does_not_leak_into_yearly_merge(tmp_path, monkeypatch):
    # E6: το indicator_benford.csv έχει δύο επίπεδα περιόδου (έτος + "all") --
    # η ένωση σε (vat, year) πρέπει να χρησιμοποιεί ΜΟΝΟ τις ετήσιες γραμμές,
    # αλλιώς η γραμμή "all" θα προσπαθούσε να γίνει int(year) και θα έσκαγε
    # ή θα μόλυνε το merge με λάθος έτος.
    monkeypatch.setattr(build_site_data, "PROCESSED_DIR", tmp_path)

    (tmp_path / "indicator_direct_award.csv").write_text(
        "organization_vat,organization_name,year,n_total,da_count_pct\n"
        "090153025,ΔΗΜΟΣ,2024,50,40.0\n",
        encoding="utf-8",
    )
    (tmp_path / "indicator_benford.csv").write_text(
        "vat,organization_name,period,n_amounts,mad_d1,mad_d2,chi2_d1,chi2_d2,"
        "nigrini_band_d1,nigrini_band_d2,coverage_pct,note\n"
        "090153025,ΔΗΜΟΣ,2024,500,0.006,0.008,1.2,1.5,close,close,99.0,\n"
        "090153025,ΔΗΜΟΣ,all,120,,,,,,,99.0,ανεπαρκή δεδομένα (N<300)\n",
        encoding="utf-8",
    )

    records = merge_indicators()

    assert len(records) == 1
    row = records[0]
    assert row["year"] == 2024
    assert row["mad_d1"] == 0.006
    assert row["nigrini_band_d1"] == "close"
    assert row["benford_coverage_pct"] == 99.0


def test_merge_indicators_benford_insufficient_n_gives_none_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site_data, "PROCESSED_DIR", tmp_path)

    (tmp_path / "indicator_direct_award.csv").write_text(
        "organization_vat,organization_name,year,n_total,da_count_pct\n"
        "090153025,ΔΗΜΟΣ,2024,50,40.0\n",
        encoding="utf-8",
    )
    (tmp_path / "indicator_benford.csv").write_text(
        "vat,organization_name,period,n_amounts,mad_d1,mad_d2,chi2_d1,chi2_d2,"
        "nigrini_band_d1,nigrini_band_d2,coverage_pct,note\n"
        "090153025,ΔΗΜΟΣ,2024,1,,,,,,,100.0,ανεπαρκή δεδομένα (N<300)\n",
        encoding="utf-8",
    )

    records = merge_indicators()

    assert len(records) == 1
    assert records[0]["mad_d1"] is None
    assert records[0]["nigrini_band_d1"] is None
    import json

    json.dumps(records, allow_nan=False)
