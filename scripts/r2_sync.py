"""E1: sync δεδομένων data/raw + data/processed <-> Cloudflare R2 (S3-compatible API).

Επιτρέπει στο CI (E2 nightly) να κατεβάζει τα ήδη υπάρχοντα raw/processed πριν
τρέξει το pipeline (`pull`) και να ανεβάζει τα νέα/αλλαγμένα αρχεία μετά (`push`),
χωρίς να μπαίνουν ποτέ τα δεδομένα στο git (απόφαση #8 -- βλ. .gitignore).

Το `push` δεν σβήνει ΠΟΤΕ remote αντικείμενα -- τα μόνιμα κενά μήνες απλώς δεν
υπάρχουν ως αρχεία. Ένα αρχείο θεωρείται stale (χρειάζεται re-upload) αν λείπει
απομακρυσμένα, έχει διαφορετικό μέγεθος, ή το τοπικό mtime είναι μεταγενέστερο
από το remote LastModified (που ισούται με τον χρόνο του τελευταίου upload).

Χρήση:
    python scripts/r2_sync.py push [--prefix raw|processed] [--dry-run]
    python scripts/r2_sync.py pull [--prefix raw|processed] [--dry-run]

Credentials (env vars ή τοπικό .env, ΠΟΤΕ commit):
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT
    R2_BUCKET (προαιρετικό, default "ellada30-data")
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
DIR_MAP = {"raw": RAW_DIR, "processed": PROCESSED_DIR}
DEFAULT_BUCKET = "ellada30-data"


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    last_modified: datetime


class Store(Protocol):
    def list(self, prefix: str) -> dict[str, ObjectInfo]: ...
    def upload(self, path: Path, key: str) -> None: ...
    def download(self, key: str, path: Path) -> None: ...


class R2Store:
    """Store πάνω στο boto3 S3 client, configured για το R2 S3-compatible API."""

    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def list(self, prefix: str) -> dict[str, ObjectInfo]:
        result: dict[str, ObjectInfo] = {}
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                result[obj["Key"]] = ObjectInfo(size=obj["Size"], last_modified=obj["LastModified"])
        return result

    def upload(self, path: Path, key: str) -> None:
        self.client.upload_file(str(path), self.bucket, key)

    def download(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(path))


def load_dotenv(env_path: Path = Path(".env")) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_store() -> R2Store:
    load_dotenv()
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("R2_ENDPOINT")
    bucket = os.environ.get("R2_BUCKET", DEFAULT_BUCKET)
    if not access_key or not secret_key or not endpoint:
        raise SystemExit(
            "Λείπουν R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT (env vars ή .env)."
        )

    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return R2Store(client, bucket)


def iter_local_files(prefix: str) -> Iterator[tuple[str, Path]]:
    dir_path = DIR_MAP[prefix]
    if not dir_path.exists():
        return
    for path in sorted(dir_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(dir_path).as_posix()
            yield f"{prefix}/{rel}", path


def plan_push(store: Store, prefixes: list[str]) -> tuple[list[tuple[str, Path]], int]:
    """Επιστρέφει (προς-ανέβασμα [(key, local_path)], πλήθος παράλειψης)."""
    to_upload: list[tuple[str, Path]] = []
    skipped = 0
    for prefix in prefixes:
        remote = store.list(f"{prefix}/")
        for key, path in iter_local_files(prefix):
            stat = path.stat()
            local_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            info = remote.get(key)
            stale = info is None or info.size != stat.st_size or local_mtime > info.last_modified
            if stale:
                to_upload.append((key, path))
            else:
                skipped += 1
    return to_upload, skipped


def plan_pull(store: Store, prefixes: list[str]) -> tuple[list[tuple[str, Path]], int]:
    """Επιστρέφει (προς-κατέβασμα [(key, local_path)], πλήθος παράλειψης)."""
    to_download: list[tuple[str, Path]] = []
    skipped = 0
    for prefix in prefixes:
        remote = store.list(f"{prefix}/")
        for key in remote:
            rel = key[len(prefix) + 1 :]
            local_path = DIR_MAP[prefix] / rel
            if local_path.exists():
                skipped += 1
            else:
                to_download.append((key, local_path))
    return to_download, skipped


def push(store: Store, prefixes: list[str], dry_run: bool) -> None:
    to_upload, skipped = plan_push(store, prefixes)
    for key, path in to_upload:
        if dry_run:
            print(f"[dry-run] upload {key}")
        else:
            store.upload(path, key)
            print(f"upload {key}")
    print(f"push: {len(to_upload)} ανέβηκαν, {skipped} παραλείφθηκαν (ήδη ενημερωμένα)")


def pull(store: Store, prefixes: list[str], dry_run: bool) -> None:
    to_download, skipped = plan_pull(store, prefixes)
    for key, path in to_download:
        if dry_run:
            print(f"[dry-run] download {key}")
        else:
            store.download(key, path)
            print(f"download {key}")
    print(f"pull: {len(to_download)} κατέβηκαν, {skipped} παραλείφθηκαν (ήδη τοπικά)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["push", "pull"])
    parser.add_argument("--prefix", choices=list(DIR_MAP), action="append", dest="prefixes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    prefixes = args.prefixes or list(DIR_MAP)
    store = build_store()

    if args.action == "push":
        push(store, prefixes, args.dry_run)
    else:
        pull(store, prefixes, args.dry_run)


if __name__ == "__main__":
    main()
