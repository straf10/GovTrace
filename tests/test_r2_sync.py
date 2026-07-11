from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from r2_sync import ObjectInfo, plan_pull, plan_push, pull, read_credential


class FakeStore:
    """Store που δεν κάνει καμία πραγματική κλήση δικτύου -- μόνο για tests."""

    def __init__(self, listing: dict[str, ObjectInfo]):
        self._listing = listing
        self.uploaded: list[tuple[str, Path]] = []
        self.downloaded: list[tuple[str, Path]] = []

    def list(self, prefix: str) -> dict[str, ObjectInfo]:
        return {k: v for k, v in self._listing.items() if k.startswith(prefix)}

    def upload(self, path: Path, key: str) -> None:
        self.uploaded.append((key, path))

    def download(self, key: str, path: Path) -> None:
        self.downloaded.append((key, path))


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    import r2_sync

    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    processed.mkdir()
    monkeypatch.setattr(r2_sync, "DIR_MAP", {"raw": raw, "processed": processed})
    return raw, processed


def _write(path: Path, content: str = "x") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_plan_push_uploads_missing_remote_file(raw_dir):
    raw, _ = raw_dir
    _write(raw / "auction_2024_01.parquet")

    store = FakeStore({})
    to_upload, skipped = plan_push(store, ["raw"])

    assert [key for key, _ in to_upload] == ["raw/auction_2024_01.parquet"]
    assert skipped == 0


def test_plan_push_skips_unchanged_file(raw_dir):
    raw, _ = raw_dir
    local_path = _write(raw / "auction_2024_01.parquet")
    stat = local_path.stat()
    far_future = datetime.now(timezone.utc) + timedelta(days=1)

    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=stat.st_size, last_modified=far_future)
    })
    to_upload, skipped = plan_push(store, ["raw"])

    assert to_upload == []
    assert skipped == 1


def test_plan_push_reuploads_when_size_differs(raw_dir):
    raw, _ = raw_dir
    _write(raw / "auction_2024_01.parquet", "longer-content")
    far_future = datetime.now(timezone.utc) + timedelta(days=1)

    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=1, last_modified=far_future)
    })
    to_upload, skipped = plan_push(store, ["raw"])

    assert [key for key, _ in to_upload] == ["raw/auction_2024_01.parquet"]
    assert skipped == 0


def test_plan_push_reuploads_when_locally_modified_after_last_upload(raw_dir):
    raw, _ = raw_dir
    local_path = _write(raw / "auction_2024_01.parquet")
    stat = local_path.stat()
    stale_remote = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc) - timedelta(days=1)

    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=stat.st_size, last_modified=stale_remote)
    })
    to_upload, skipped = plan_push(store, ["raw"])

    assert [key for key, _ in to_upload] == ["raw/auction_2024_01.parquet"]
    assert skipped == 0


def test_plan_push_second_run_is_idempotent(raw_dir):
    raw, _ = raw_dir
    local_path = _write(raw / "auction_2024_01.parquet")
    stat = local_path.stat()

    upload_time = datetime.now(timezone.utc) + timedelta(seconds=1)
    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=stat.st_size, last_modified=upload_time)
    })
    to_upload, skipped = plan_push(store, ["raw"])

    assert to_upload == []
    assert skipped == 1


def test_plan_push_never_lists_remote_deletions(raw_dir):
    raw, _ = raw_dir
    # τοπικά δεν υπάρχει τίποτα -- το remote-only αντικείμενο δεν πρέπει
    # να εμφανιστεί πουθενά στο plan_push (καμία λογική διαγραφής).
    store = FakeStore({
        "raw/gap_2021_02.parquet": ObjectInfo(size=10, last_modified=datetime.now(timezone.utc))
    })
    to_upload, skipped = plan_push(store, ["raw"])

    assert to_upload == []
    assert skipped == 0


def test_plan_pull_downloads_missing_local_file(raw_dir):
    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=10, last_modified=datetime.now(timezone.utc))
    })
    to_download, skipped = plan_pull(store, ["raw"])

    assert [key for key, _, _ in to_download] == ["raw/auction_2024_01.parquet"]
    assert skipped == 0


def test_plan_pull_skips_existing_local_file(raw_dir):
    raw, _ = raw_dir
    local_path = _write(raw / "auction_2024_01.parquet")

    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(
            size=local_path.stat().st_size, last_modified=datetime.now(timezone.utc)
        )
    })
    to_download, skipped = plan_pull(store, ["raw"])

    assert to_download == []
    assert skipped == 1


def test_plan_pull_redownloads_when_size_differs(raw_dir):
    # #5 (CHECK 2026-07-11): αρχείο που επιδιορθώθηκε remotely (διαφορετικό
    # μέγεθος) πρέπει να ξανακατέβει -- όχι να παραλειφθεί επειδή "υπάρχει".
    raw, _ = raw_dir
    _write(raw / "auction_2024_01.parquet", "old-version")

    store = FakeStore({
        "raw/auction_2024_01.parquet": ObjectInfo(size=99999, last_modified=datetime.now(timezone.utc))
    })
    to_download, skipped = plan_pull(store, ["raw"])

    assert [key for key, _, _ in to_download] == ["raw/auction_2024_01.parquet"]
    assert skipped == 0


def test_read_credential_strips_crlf_and_whitespace(monkeypatch):
    # #22 (CHECK 2026-07-11): secret με trailing CRLF (Windows paste) έριξε
    # το E2 run 1 με "Invalid endpoint" -- πρέπει να καθαρίζεται defensively.
    monkeypatch.setenv("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com\r\n")
    assert read_credential("R2_ENDPOINT") == "https://example.r2.cloudflarestorage.com"
    monkeypatch.setenv("R2_BUCKET", "  my-bucket  ")
    assert read_credential("R2_BUCKET") == "my-bucket"
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    assert read_credential("R2_ACCESS_KEY_ID") == ""
    assert read_credential("R2_ACCESS_KEY_ID", "default-bucket") == "default-bucket"


def test_plan_pull_empty_folder_downloads_full_copy(raw_dir):
    store = FakeStore({
        "processed/entities.csv": ObjectInfo(size=10, last_modified=datetime.now(timezone.utc)),
        "processed/vat_resolver.csv": ObjectInfo(size=20, last_modified=datetime.now(timezone.utc)),
    })
    to_download, skipped = plan_pull(store, ["processed"])

    assert {key for key, _, _ in to_download} == {"processed/entities.csv", "processed/vat_resolver.csv"}
    assert skipped == 0


def test_pull_preserves_remote_mtime_so_push_stays_incremental(raw_dir):
    # Regression: το boto3 download_file γράφει mtime = ώρα λήψης, οπότε χωρίς
    # utime στο pull, ένα pull -> push (E2 nightly) θα ξανα-ανέβαζε ΟΛΑ τα αρχεία.
    remote_time = datetime.now(timezone.utc) - timedelta(days=30)
    listing = {"raw/auction_2024_01.parquet": ObjectInfo(size=1, last_modified=remote_time)}

    class WritingFakeStore(FakeStore):
        def download(self, key: str, path: Path) -> None:
            _write(path)  # mtime = τώρα, όπως το πραγματικό download_file
            super().download(key, path)

    store = WritingFakeStore(listing)
    pull(store, ["raw"], dry_run=False)

    local_path = next(iter(store.downloaded))[1]
    local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime, tz=timezone.utc)
    assert abs((local_mtime - remote_time).total_seconds()) < 1

    to_upload, skipped = plan_push(store, ["raw"])
    assert to_upload == []
    assert skipped == 1
