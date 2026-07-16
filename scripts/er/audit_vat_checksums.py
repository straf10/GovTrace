"""P2-03 follow-up: mod-11 checksum scan στα ΑΦΜ του entity_resolution_matches.csv.

Το build_independent_audit_sample.py εντόπισε 2 patterns προφανών placeholder
ΑΦΜ (999999999, 111111111) σε 1003 ζεύγη. Αυτό το script γενικεύει τον έλεγχο
με το ΥΠΑΡΧΟΝ is_valid_vat_checksum() (kimdis_data.py) πάνω σε ΟΛΑ τα ΑΦΜ
(same_vat και diff_or_missing_vat), για να δούμε αν υπάρχουν κι άλλα μη
έγκυρα ΑΦΜ πέρα από τα δύο γνωστά repeated-digit patterns.

Σημείωση: το checksum μετράει "δημοσιεύσιμη εγκυρότητα" -- format-valid
9ψήφια που αποτυγχάνουν το checksum ΔΕΝ αποκλείονται αυτόματα από το
keying (βλ. σχόλιο is_valid_vat_checksum), απλώς καταγράφονται εδώ.

Χρήση:
    python scripts/er/audit_vat_checksums.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kimdis_data import is_valid_vat_checksum  # noqa: E402

MATCHES_PATH = Path("data/processed/er/entity_resolution_matches.csv")
# Εκτός git (2026-07-16 audit): η λίστα περιέχει ονόματα+ΑΦΜ και φυσικών
# προσώπων -- μένει στο data/processed/ (gitignored, συγχρονίζεται στο R2).
OUT_PATH = Path("data/processed/er/vat_checksum_audit_2026-07.csv")


def checksum_ok(vat: str | float) -> bool | None:
    if pd.isna(vat):
        return None
    return is_valid_vat_checksum(str(vat).strip())


def main() -> None:
    df = pd.read_csv(MATCHES_PATH, dtype={"vat_l": str, "vat_r": str})

    vats = pd.concat(
        [
            df[["id_l", "vat_l", "name_l"]].rename(columns={"id_l": "id", "vat_l": "vat", "name_l": "name"}),
            df[["id_r", "vat_r", "name_r"]].rename(columns={"id_r": "id", "vat_r": "vat", "name_r": "name"}),
        ]
    ).drop_duplicates(subset=["id"])

    vats["checksum_ok"] = vats["vat"].map(checksum_ok)

    total = len(vats)
    present = vats["checksum_ok"].notna().sum()
    valid = (vats["checksum_ok"] == True).sum()  # noqa: E712
    invalid = (vats["checksum_ok"] == False).sum()  # noqa: E712
    missing = total - present

    print(f"μοναδικές entity εγγραφές: {total}")
    print(f"  με ΑΦΜ παρόν: {present} ({present/total:.1%})")
    print(f"    checksum valid: {valid} ({valid/present:.1%} του present)")
    print(f"    checksum INVALID: {invalid} ({invalid/present:.1%} του present)")
    print(f"  χωρίς ΑΦΜ: {missing}")

    invalid_rows = vats[vats["checksum_ok"] == False].copy()  # noqa: E712
    invalid_rows["vat_value_count"] = invalid_rows.groupby("vat")["vat"].transform("count")
    invalid_rows = invalid_rows.sort_values(["vat_value_count", "vat"], ascending=[False, True])

    print()
    print("Top-15 πιο συχνές invalid-checksum ΑΦΜ τιμές:")
    print(invalid_rows.drop_duplicates("vat")[["vat", "vat_value_count"]].head(15).to_string(index=False))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    invalid_rows[["id", "vat", "name", "vat_value_count"]].to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\nΠλήρης λίστα invalid-checksum εγγραφών -> {OUT_PATH}")


if __name__ == "__main__":
    main()
