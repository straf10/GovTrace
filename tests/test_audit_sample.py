"""#6 (CHECK 2026-07-11): το top-up του audit δείγματος πρέπει να δουλεύει με
τα ORIGINAL indexes του scored -- το παλιό ignore_index=True έκανε το
scored.drop(sample.index) να πετάει τις πρώτες N γραμμές κατά θέση, επιτρέποντας
διπλοεγγραφές ζευγών και συστηματικό αποκλεισμό λάθος γραμμών."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "er"))

from build_audit_sample import build_sample  # noqa: E402


def _scored_all_top_band(n: int) -> pd.DataFrame:
    # Όλα τα ζεύγη same-VAT με jw_score=1.0 -> όλα στο band 0.95-1.0, τα άλλα
    # 3 bands άδεια -- αναγκάζει το top-up branch να τρέξει.
    return pd.DataFrame({
        "id_l": range(n),
        "id_r": range(n, 2 * n),
        "vat_l": ["123456789"] * n,
        "vat_r": ["123456789"] * n,
        "name_l": ["ΦΟΡΕΑΣ Α"] * n,
        "name_r": ["ΦΟΡΕΑΣ Α"] * n,
        "jw_score": [1.0] * n,
    })


def test_topup_fills_sample_without_duplicate_pairs():
    scored = _scored_all_top_band(50)
    sample = build_sample(scored, sample_size=20, seed=42)

    assert len(sample) == 20
    # Κανένα ζεύγος δύο φορές -- το bug επέτρεπε το top-up να ξανα-δειγματίσει
    # ήδη επιλεγμένα ζεύγη.
    assert not sample[["id_l", "id_r"]].duplicated().any()


def test_topup_respects_available_rows():
    scored = _scored_all_top_band(10)
    sample = build_sample(scored, sample_size=20, seed=42)

    # Λιγότερα ζεύγη από το sample_size: επιστρέφονται όλα, χωρίς διπλότυπα.
    assert len(sample) == 10
    assert not sample[["id_l", "id_r"]].duplicated().any()


def test_sample_has_empty_label_columns():
    sample = build_sample(_scored_all_top_band(30), sample_size=8, seed=42)
    assert (sample["label"] == "").all()
    assert (sample["justification"] == "").all()
