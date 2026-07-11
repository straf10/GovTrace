"""E7 (Sprint E POC): δείγμα 200 ζευγών για χειροκίνητο audit.

ΕΥΡΗΜΑ POC (καταγράφεται στο docs/research/splink_poc_results.md): το
εκπαιδευμένο match_probability του splink_poc.py είναι **σταθερό** σε όλα τα
ζεύγη (0.9013595, ένα μοναδικό distinct value) -- ένα μοντέλο με ΕΝΑ μόνο
comparison column (name_norm) πάνω σε ζεύγη που έχουν ήδη περάσει από blocking
rule υψηλής ομοιότητας (ίδιο ΑΦΜ ή ίδια πρώτα 4 tokens) σχεδόν πάντα πέφτει
στο ίδιο (ανώτατο) gamma level, οπότε ο λόγος m/u -- άρα και η πιθανότητα --
δεν διαφοροποιείται. Το raw Jaro-Winkler score (υπολογισμένο εδώ απευθείας με
DuckDB, όχι μέσω του EM-trained μοντέλου) είναι το μόνο σήμα που όντως
διαφοροποιεί τα ζεύγη σε αυτό το POC, οπότε χρησιμοποιείται για τη
στρωματοποίηση του audit δείγματος (band = raw JW score) αντί για το
degenerate match_probability. Σύσταση για Φάση 2.1: πολλαπλά comparison
columns (π.χ. + token overlap/legal-form-stripped name, + numeric ΑΦΜ
gamma) ώστε η πιθανότητα να διαφοροποιεί πραγματικά.

Χρήση:
    python scripts/er/build_audit_sample.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

MATCHES_PATH = Path("data/processed/er/entity_resolution_matches.csv")
AUDIT_PATH = Path("data/processed/er/er_audit_sample.csv")
SAMPLE_SIZE = 200
SEED = 42

BANDS = [
    (0.5, 0.7, "0.5-0.7"),
    (0.7, 0.85, "0.7-0.85"),
    (0.85, 0.95, "0.85-0.95"),
    (0.95, 1.001, "0.95-1.0"),
]


def build_sample(scored: pd.DataFrame, sample_size: int = SAMPLE_SIZE, seed: int = SEED) -> pd.DataFrame:
    """Στρωματοποιημένο δείγμα ανά JW band, με top-up από τα υπόλοιπα ζεύγη.

    #6 (CHECK 2026-07-11): το top-up διατηρεί τα ORIGINAL indexes του
    ``scored`` μέχρι το τέλος -- το παλιό ``pd.concat(..., ignore_index=True)``
    πριν το ``scored.drop(sample.index)`` πετούσε τις πρώτες N γραμμές του
    scored ΚΑΤΑ ΘΕΣΗ (όχι τα δειγματισμένα ζεύγη), επιτρέποντας διπλοεγγραφές
    και συστηματικό αποκλεισμό λάθος γραμμών. Το reset_index γίνεται μόνο στο
    τελικό shuffle.
    """
    same_vat = (scored["vat_l"] == scored["vat_r"]) & scored["vat_l"].notna()
    scored["pair_type"] = same_vat.map({True: "same_vat", False: "diff_or_missing_vat"})

    scored["band"] = pd.cut(
        scored["jw_score"],
        bins=[b[0] for b in BANDS] + [BANDS[-1][1]],
        labels=[b[2] for b in BANDS],
        include_lowest=True,
    )

    # Στρωματοποίηση: μισό δείγμα ανά JW band (τα 4 bands ισοβαρή), μέσα σε
    # κάθε band προτεραιότητα στα diff/missing-VAT ζεύγη (το πραγματικά
    # δύσκολο υποσύνολο -- τα same-VAT ζεύγη είναι εξ ορισμού σωστά matches,
    # δεν χρειάζονται κρίση).
    per_band = sample_size // len(BANDS)
    parts = []
    for _, _, label in BANDS:
        sub = scored[scored["band"] == label]
        diff = sub[sub["pair_type"] == "diff_or_missing_vat"]
        same = sub[sub["pair_type"] == "same_vat"]
        n_diff = min(len(diff), int(per_band * 0.7))
        n_same = min(len(same), per_band - n_diff)
        picks = []
        if n_diff:
            picks.append(diff.sample(n=n_diff, random_state=seed))
        if n_same:
            picks.append(same.sample(n=n_same, random_state=seed))
        if picks:
            parts.append(pd.concat(picks))
    # ΧΩΡΙΣ ignore_index: τα original indexes είναι το κλειδί του top-up drop.
    sample = pd.concat(parts) if parts else scored.iloc[0:0]
    if len(sample) < sample_size:
        remaining = scored.drop(sample.index)
        extra = remaining.sample(n=min(sample_size - len(sample), len(remaining)), random_state=seed)
        sample = pd.concat([sample, extra])

    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)  # shuffle display order
    sample["label"] = ""  # γεμίζει χειροκίνητα: match / no_match / uncertain
    sample["justification"] = ""  # 1-γραμμη αιτιολόγηση ανά ετικέτα
    return sample


def main() -> None:
    df = pd.read_csv(MATCHES_PATH, dtype={"vat_l": str, "vat_r": str})
    con = duckdb.connect()
    con.register("df", df)
    scored = con.execute(
        "SELECT *, jaro_winkler_similarity(name_l, name_r) AS jw_score FROM df"
    ).df()

    sample = build_sample(scored)

    cols = [
        "id_l", "id_r", "vat_l", "vat_r", "name_l", "name_r",
        "jw_score", "band", "pair_type", "label", "justification",
    ]
    sample[cols].to_csv(AUDIT_PATH, index=False, encoding="utf-8-sig")
    print(f"Audit sample: {len(sample)} ζεύγη -> {AUDIT_PATH}")
    print(sample["band"].value_counts())
    print(sample["pair_type"].value_counts())


if __name__ == "__main__":
    main()
