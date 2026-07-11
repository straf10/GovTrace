from pathlib import Path

from build_site_data import latest_complete_month


def _touch(path: Path) -> None:
    path.write_bytes(b"")


def test_latest_complete_month_picks_max_across_entities(tmp_path):
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "contract_2024_05.parquet")
    _touch(tmp_path / "notice_2024_01.parquet")

    assert latest_complete_month(tmp_path) == "2024-05"


def test_latest_complete_month_empty_dir_returns_none(tmp_path):
    assert latest_complete_month(tmp_path) is None


def test_latest_complete_month_missing_dir_returns_none(tmp_path):
    assert latest_complete_month(tmp_path / "does-not-exist") is None


def test_latest_complete_month_ignores_non_matching_files(tmp_path):
    _touch(tmp_path / "auction_2024_03.parquet")
    _touch(tmp_path / "_backfill_failures.json")

    assert latest_complete_month(tmp_path) == "2024-03"
