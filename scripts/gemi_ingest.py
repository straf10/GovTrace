"""P2-B2: ΓΕΜΗ ingest -- ΜΟΝΟ ΑΦΜ που εμφανίζονται στο ΚΗΜΔΗΣ ως ανάδοχοι.

ΟΧΙ bulk όλου του ΓΕΜΗ: το σύνολο των ΑΦΜ προέρχεται από
data/processed/er/contractor_aliases.csv (χτισμένο από το P2-02 Splink run,
164.611 μοναδικά ΑΦΜ αναδόχων ΚΗΜΔΗΣ) αντί για τα ~1,5Μ εγγεγραμμένες
εταιρείες όλου του ΓΕΜΗ.

Στο επιβεβαιωμένο όριο 8 req/min, μία πλήρης διέλευση των 164.611 ΑΦΜ
χρειάζεται ~14 μέρες συνεχούς τρεξίματος (1 κλήση/ΑΦΜ: /companies?afm=
περιέχει ήδη όλα τα πεδία που χρειάζεται το P2-B5 -- δεν χρειάζεται δεύτερη
κλήση σε /companies/{arGemi} εκτός αν χρειαστεί το persons[] αργότερα, gated
πίσω από το P2-B4/νομική γνωμοδότηση).

Resume-able: το output parquet διαβάζεται στην αρχή, τα ήδη επεξεργασμένα ΑΦΜ
(είτε βρέθηκαν είτε όχι -- ένα φυσικό πρόσωπο/ελεύθερος επαγγελματίας ΔΕΝ
είναι ποτέ στο ΓΕΜΗ και δεν χρειάζεται να ξαναρωτηθεί) παραλείπονται.
Checkpoint κάθε --checkpoint-every κλήσεις (default 25, ~3 λεπτά στα 8/min).

Χρήση:
    python scripts/gemi_ingest.py                    # πλήρες run, resume-able
    python scripts/gemi_ingest.py --limit 50          # δοκιμαστικό run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemi import GemiClient  # noqa: E402
from gemi.client import GemiApiError  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gemi_ingest")

INPUT_PATH = Path("data/processed/er/contractor_aliases.csv")
OUTPUT_PATH = Path("data/processed/gemi/companies.parquet")

# Kill-switch (κατά το πρότυπο backfill_historical.py): πολλά συνεχόμενα
# σφάλματα μετά την εξάντληση retries του client σημαίνει συστημικό πρόβλημα
# (π.χ. ανακλήθηκε το key) -- σταματάμε αντί να τρέχουμε επί μέρες σκάζοντας.
MAX_CONSECUTIVE_ERRORS = 10


def load_dotenv(env_path: Path = Path(".env")) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_target_vats(input_path: Path) -> list[str]:
    df = pd.read_csv(input_path, dtype={"vat": str})
    return sorted(df["vat"].dropna().unique().tolist())


def load_done_vats(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    df = pd.read_parquet(output_path, columns=["vat"])
    return set(df["vat"])


def flush(rows: list[dict], output_path: Path) -> None:
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    if output_path.exists():
        existing = pd.read_parquet(output_path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
        output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.parquet")
    combined.to_parquet(tmp_path, index=False)
    tmp_path.replace(output_path)
    rows.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=None, help="Μόνο τα πρώτα N νέα ΑΦΜ (δοκιμές)")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    args = parser.parse_args()

    load_dotenv()
    api_key = (os.environ.get("GEMI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("GEMI_API_KEY λείπει (env var ή .env). Βλ. .env.example.")

    all_vats = load_target_vats(args.input)
    done = load_done_vats(args.output)
    todo = [v for v in all_vats if v not in done]
    if args.limit is not None:
        todo = todo[: args.limit]

    logger.info(
        "Σύνολο ΑΦΜ: %d, ήδη επεξεργασμένα: %d, προς άντληση: %d (~%.1f μέρες στα 8 req/min)",
        len(all_vats), len(done), len(todo), len(todo) / 8 / 60 / 24,
    )
    if not todo:
        logger.info("Τίποτα προς άντληση -- ολοκληρωμένο.")
        return

    buffer: list[dict] = []
    consecutive_errors = 0
    n_found = 0
    start = time.monotonic()

    with GemiClient(api_key=api_key) as client:
        for i, vat in enumerate(todo, start=1):
            try:
                company = client.find_by_afm(vat)
                consecutive_errors = 0
            except GemiApiError as exc:
                consecutive_errors += 1
                logger.error("ΑΦΜ %s: %s (συνεχόμενα σφάλματα: %d)", vat, exc, consecutive_errors)
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    flush(buffer, args.output)
                    raise SystemExit(
                        f"Kill-switch: {MAX_CONSECUTIVE_ERRORS} συνεχόμενα σφάλματα -- "
                        f"διακοπή στο ΑΦΜ {vat} ({i}/{len(todo)})."
                    ) from exc
                continue

            fetched_at = datetime.now(timezone.utc).isoformat()
            if company is None:
                buffer.append({"vat": vat, "found": False, "fetched_at": fetched_at})
            else:
                n_found += 1
                buffer.append({
                    "vat": vat,
                    "found": True,
                    "ar_gemi": company.ar_gemi,
                    "name_el": company.name_el,
                    "legal_type": company.legal_type,
                    "status": company.status,
                    "incorporation_date": company.incorporation_date,
                    "is_branch": company.is_branch,
                    "fetched_at": fetched_at,
                })

            if len(buffer) >= args.checkpoint_every:
                flush(buffer, args.output)
                elapsed = time.monotonic() - start
                rate = i / elapsed * 60 if elapsed > 0 else 0
                eta_min = (len(todo) - i) / rate if rate > 0 else float("inf")
                logger.info(
                    "checkpoint: %d/%d (βρέθηκαν %d, %.1f req/min, ETA %.0f λεπτά)",
                    i, len(todo), n_found, rate, eta_min,
                )

    flush(buffer, args.output)
    logger.info("Ολοκληρώθηκε: %d/%d ΑΦΜ, %d βρέθηκαν στο ΓΕΜΗ.", len(todo), len(todo), n_found)


if __name__ == "__main__":
    main()
