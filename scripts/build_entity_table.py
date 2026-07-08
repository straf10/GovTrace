"""Φάση 1 βήμα 2 (PLAN.md §2): Πίνακας κανονικοποίησης αναθετουσών αρχών.

Διαβάζει auction/contract/notice από data/raw/ και χτίζει ένα ενιαίο
μητρώο φορέων (ΑΦΜ -> όνομα, τύπος, NUTS), αντί το group-by ονόματος να
γίνεται ξανά και ξανά inline σε κάθε script δεικτών.

Επειδή το ίδιο ΑΦΜ εμφανίζεται σε πολλαπλές εγγραφές/entities με πιθανές
μικρο-αποκλίσεις (π.χ. σε ποιο entity υπάρχει typeOfContractingAuthority),
κρατάμε ανά ΑΦΜ την **πιο συχνή** (mode) τιμή κάθε περιγραφικού πεδίου.

Χρήση:
    python scripts/build_entity_table.py

Γράφει data/processed/entities.csv.
"""

from __future__ import annotations

import pandas as pd

from kimdis_data import PROCESSED_DIR, load_entity

# Ποια entities/στήλες συνεισφέρουν σε κάθε κανονικό πεδίο του πίνακα φορέων.
# Οι στήλες διαφέρουν ελαφρώς ανά entity (π.χ. .key/.value vs επίπεδη στήλη),
# οπότε δοκιμάζουμε μια λίστα υποψήφιων στηλών ανά entity και κρατάμε την
# πρώτη που υπάρχει.
FIELD_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "auction": {
        "vat": ["organizationVatNumber"],
        "name": ["organization.value"],
        "org_type": ["typeOfContractingAuthority"],
        "classification": ["classificationOfPublicLawOrganization.value"],
        "nuts_code": ["nutsCode.value"],
        "nuts_city": ["nutsCity"],
    },
    "contract": {
        "vat": ["organizationVatNumber"],
        "name": ["organization.value"],
        "org_type": ["typeOfContractingAuthority.value", "typeOfContractingAuthority"],
        "classification": ["classificationOfPublicLawOrganization"],
        "nuts_code": ["nutsCode.value"],
        "nuts_city": ["nutsCity"],
    },
    "notice": {
        "vat": ["organizationVatNumber"],
        "name": ["organization.value"],
        "org_type": [],
        "classification": ["classificationOfPublicLawOrganization"],
        "nuts_code": ["nutsCode.value"],
        "nuts_city": ["nutsCity"],
    },
}


def extract_entity_rows(entity: str) -> pd.DataFrame:
    df = load_entity(entity)
    if df.empty:
        return pd.DataFrame()

    fields = FIELD_CANDIDATES[entity]
    out = pd.DataFrame(index=df.index)
    for canonical, candidates in fields.items():
        col = next((c for c in candidates if c in df.columns), None)
        out[canonical] = df[col] if col is not None else None
    out["_source_year"] = df["_source_year"]
    return out


def build_entity_table() -> pd.DataFrame:
    frames = [extract_entity_rows(e) for e in ("auction", "contract", "notice")]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    all_rows = pd.concat(frames, ignore_index=True)
    all_rows = all_rows.dropna(subset=["vat"])
    all_rows = all_rows[all_rows["vat"].astype(str).str.len() > 0]

    def mode_or_none(s: pd.Series):
        s = s.dropna()
        if s.empty:
            return None
        return s.mode(dropna=True).iloc[0]

    rows = []
    for vat, g in all_rows.groupby("vat"):
        rows.append(
            {
                "vat": vat,
                "name": mode_or_none(g["name"]),
                "org_type": mode_or_none(g["org_type"]),
                "classification": mode_or_none(g["classification"]),
                "nuts_code": mode_or_none(g["nuts_code"]),
                "nuts_city": mode_or_none(g["nuts_city"]),
                "n_records": len(g),
                "first_year": int(g["_source_year"].min()),
                "last_year": int(g["_source_year"].max()),
            }
        )
    return pd.DataFrame(rows).sort_values("vat").reset_index(drop=True)


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    entities = build_entity_table()
    if entities.empty:
        print("Δεν βρέθηκαν δεδομένα σε data/raw/. Τρέξε πρώτα backfill/fetch.")
        return

    out_path = PROCESSED_DIR / "entities.csv"
    entities.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Πίνακας φορέων -> {out_path} ({len(entities)} μοναδικά ΑΦΜ)")


if __name__ == "__main__":
    main()
