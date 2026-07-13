"""P2-03: ανεξάρτητο δείγμα ζευγών για χειροκίνητη κρίση (Checkpoint 2α).

Το υπάρχον er_audit_sample.csv (200 ζεύγη, βλ. build_audit_sample.py) είναι
labeled από την ΙΔΙΑ ντετερμινιστική πολιτική που παράγει τα matches ("ίδιο
ΑΦΜ -> match" εξ ορισμού) -- κυκλικό, δεν αποτελεί ανεξάρτητο έλεγχο (βλ.
docs/research/splink_poc_results.md, POC εκκρεμότητα #2).

Αυτό το script δειγματίζει ένα ΞΕΧΩΡΙΣΤΟ, μη επικαλυπτόμενο σύνολο ζευγών
(αποκλείει ό,τι ήδη υπάρχει στο er_audit_sample.csv) για ΑΝΕΞΑΡΤΗΤΗ ανθρώπινη
κρίση -- χωρίς μηχανική εφαρμογή του κανόνα "ίδιο ΑΦΜ = match". Στρωματοποίηση
50/50 same_vat vs diff_or_missing_vat (το δύσκολο υποσύνολο παίρνει ίση βαρύτητα,
όχι μόνο 70% όπως στο πρωτότυπο δείγμα, ακριβώς επειδή εδώ η κρίση δεν είναι
mechanical).

Χρήση:
    python scripts/er/build_independent_audit_sample.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MATCHES_PATH = Path("data/processed/er/entity_resolution_matches.csv")
EXISTING_AUDIT_PATH = Path("data/processed/er/er_audit_sample.csv")
OUTPUT_PATH = Path("docs/research/er_audit_independent_2026-07.csv")
SAMPLE_SIZE = 30
SEED = 20260713  # διαφορετικό seed από το build_audit_sample.py (42) -- σκόπιμα ανεξάρτητο δείγμα


def build_sample(matches: pd.DataFrame, already_audited: pd.DataFrame, sample_size: int = SAMPLE_SIZE, seed: int = SEED) -> pd.DataFrame:
    audited_keys = set(zip(already_audited["id_l"], already_audited["id_r"]))
    fresh = matches[~matches.apply(lambda r: (r["id_l"], r["id_r"]) in audited_keys, axis=1)].copy()

    same_vat = (fresh["vat_l"] == fresh["vat_r"]) & fresh["vat_l"].notna()
    fresh["pair_type"] = same_vat.map({True: "same_vat", False: "diff_or_missing_vat"})

    half = sample_size // 2
    same = fresh[fresh["pair_type"] == "same_vat"]
    diff = fresh[fresh["pair_type"] == "diff_or_missing_vat"]
    n_same = min(len(same), half)
    n_diff = min(len(diff), sample_size - n_same)
    # top-up αν κάποιο υποσύνολο έχει λιγότερα διαθέσιμα ζεύγη από το μισό
    if n_same < half:
        n_diff = min(len(diff), sample_size - n_same)
    if n_diff < sample_size - half:
        n_same = min(len(same), sample_size - n_diff)

    parts = []
    if n_same:
        parts.append(same.sample(n=n_same, random_state=seed))
    if n_diff:
        parts.append(diff.sample(n=n_diff, random_state=seed))
    sample = pd.concat(parts) if parts else fresh.iloc[0:0]
    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)  # shuffle display order

    sample["independent_judgment"] = ""  # γεμίζει χειροκίνητα: correct_match / incorrect_match / cannot_determine
    sample["rationale"] = ""  # 1-γραμμη αιτιολόγηση -- ΑΝΕΞΑΡΤΗΤΗ σκέψη, όχι αναπαραγωγή του κανόνα "ίδιο ΑΦΜ = match"
    return sample


def main() -> None:
    matches = pd.read_csv(MATCHES_PATH, dtype={"vat_l": str, "vat_r": str})
    already_audited = pd.read_csv(EXISTING_AUDIT_PATH, dtype={"id_l": "int64", "id_r": "int64"})

    sample = build_sample(matches, already_audited)

    cols = [
        "id_l", "id_r", "vat_l", "vat_r", "name_l", "name_r",
        "match_probability", "pair_type", "independent_judgment", "rationale",
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sample[cols].to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"Ανεξάρτητο audit δείγμα: {len(sample)} ζεύγη -> {OUTPUT_PATH}")
    print(sample["pair_type"].value_counts())


if __name__ == "__main__":
    main()
