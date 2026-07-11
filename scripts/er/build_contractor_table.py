"""E7 (Sprint E POC): εξάγει τον πίνακα αναδόχων από τα auction parquet.

Διαβάζει ΜΟΝΟ τη στήλη ``contractingDataDetails.contractingMembersDataList``
(column pruning, per-month iteration μέσω glob -- όχι load_entity() γιατί
χρειαζόμαστε unpack λίστας πριν το concat) από όλα τα ``data/raw/auction_*.parquet``,
ξεδιπλώνει τα per-member JSON entries και γράφει μοναδικά ζεύγη vat/name με
συχνότητες σε ``data/processed/er/contractors_raw.csv``.

Κανονικοποίηση ΑΦΜ με το ΥΠΑΡΧΟΝ ``normalize_vat()`` του kimdis_data.py
(single source of truth στο keying -- καμία αντιγραφή λογικής).

Χρήση:
    python scripts/er/build_contractor_table.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kimdis_data import RAW_DIR, normalize_vat  # noqa: E402

OUT_PATH = Path("data/processed/er/contractors_raw.csv")
COL = "contractingDataDetails.contractingMembersDataList"


def extract_members(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    files = sorted(raw_dir.glob("auction_*.parquet"))
    counts: Counter[tuple[str | None, str]] = Counter()
    # #7β (CHECK 2026-07-11): min/max έτος εμφάνισης ανά ζεύγος (από το
    # filename) -- χωρίς αυτό η στρωματοποίηση «ανά περίοδο» του precision
    # re-measurement (Φάση 2.1) ήταν αδύνατη.
    years: dict[tuple[str | None, str], tuple[int, int]] = {}
    total_rows = 0
    total_members = 0
    for f in files:
        year = int(f.stem.split("_")[1])
        df = pd.read_parquet(f, columns=[COL])
        total_rows += len(df)
        for raw in df[COL].dropna():
            try:
                members = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(members, list):
                continue
            for m in members:
                if not isinstance(m, dict):
                    continue
                name = (m.get("name") or "").strip()
                if not name:
                    continue
                vat = normalize_vat(m.get("vatNumber")) if isinstance(m.get("vatNumber"), str) else None
                key = (vat, name)
                counts[key] += 1
                lo, hi = years.get(key, (year, year))
                years[key] = (min(lo, year), max(hi, year))
                total_members += 1
    rows = [
        {"vat": vat, "name": name, "n": n,
         "first_year": years[(vat, name)][0], "last_year": years[(vat, name)][1]}
        for (vat, name), n in counts.items()
    ]
    out = pd.DataFrame(rows).sort_values(["name", "n"], ascending=[True, False])
    print(f"auction αρχεία: {len(files)}, γραμμές: {total_rows}, μέλη (raw, non-unique): {total_members}")
    print(f"μοναδικά ζεύγη vat/name: {len(out)}")
    return out


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    table = extract_members()
    table.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"Γράφτηκε {OUT_PATH}")


if __name__ == "__main__":
    main()
