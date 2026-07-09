"""B4 (tech_report v2): φτιάχνει το release zip με ιχνηλασιμότητα κώδικα↔δεδομένων.

Πριν αυτό το script, το CI (`.github/workflows/deploy.yml`) κατέβαζε «ό,τι zip
βρει» από το mutable release tag `site-data-latest` -- τίποτα δεν έδενε το
asset με το commit κώδικα ή την ημερομηνία του pipeline run. Αυτό το script:

1. Γράφει ``site/dist/build-info.json`` με git SHA, ώρα build, και μετρήσιμα
   στοιχεία των δεδομένων (πλήθος ΑΦΜ/προφίλ, timestamp entities.csv).
2. Φτιάχνει το ``site-dist.zip`` με το ενσωματωμένο ``zipfile`` της Python
   (πάντα forward-slash paths -- το ``Compress-Archive`` του PowerShell γράφει
   backslash paths που σπάνε το ``unzip`` του GitHub Actions runner, βλ.
   tech_report B4 δευτερεύον σημείο).
3. Προαιρετικά ανεβάζει με ``gh release upload --clobber`` (``--upload``).

Χρήση:
    python scripts/build_release_zip.py
    python scripts/build_release_zip.py --upload
"""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DIST_DIR = Path("site/dist")
ZIP_PATH = Path("site-dist.zip")
ENTITIES_PATH = Path("data/processed/entities.csv")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def build_info() -> dict:
    n_entities = None
    entities_mtime = None
    if ENTITIES_PATH.exists():
        with ENTITIES_PATH.open(encoding="utf-8-sig") as f:
            n_entities = sum(1 for _ in f) - 1  # πλην header
        entities_mtime = datetime.fromtimestamp(
            ENTITIES_PATH.stat().st_mtime, tz=timezone.utc
        ).isoformat()

    n_dist_files = sum(1 for p in DIST_DIR.rglob("*") if p.is_file()) if DIST_DIR.exists() else None

    return {
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "entities_count": n_entities,
        "entities_csv_timestamp": entities_mtime,
        "dist_file_count": n_dist_files,
    }


def write_build_info(info: dict) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIST_DIR / "build-info.json"
    out_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def make_zip(dist_dir: Path = DIST_DIR, zip_path: Path = ZIP_PATH) -> Path:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                # POSIX (forward-slash) arcname -- ό,τι θα δει το unzip του
                # Actions runner ανεξάρτητα από το OS που έτρεξε το build.
                arcname = path.relative_to(dist_dir).as_posix()
                zf.write(path, arcname)
    return zip_path


def upload(zip_path: Path, info: dict) -> None:
    notes = (
        f"commit {info['git_sha'][:12]} ({info['git_branch']}) · "
        f"build {info['build_timestamp']} · "
        f"{info['entities_count']} ΑΦΜ φορέων"
    )
    subprocess.run(
        ["gh", "release", "upload", "site-data-latest", str(zip_path), "--clobber"],
        check=True,
    )
    subprocess.run(
        ["gh", "release", "edit", "site-data-latest", "--notes", notes],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true", help="gh release upload --clobber μετά το zip")
    args = parser.parse_args()

    if not DIST_DIR.exists():
        raise SystemExit(f"{DIST_DIR} δεν υπάρχει -- τρέξε πρώτα `npm run build` στο site/")

    info = build_info()
    info_path = write_build_info(info)
    print(f"Γράφτηκε {info_path}:")
    print(json.dumps(info, ensure_ascii=False, indent=2))

    zip_path = make_zip()
    print(f"Γράφτηκε {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")

    if args.upload:
        upload(zip_path, info)
        print("Ανέβηκε στο release site-data-latest (--clobber).")


if __name__ == "__main__":
    main()
