import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "er"))

from build_alias_whitelist import build_vat_groups  # noqa: E402


def test_build_vat_groups_rule2_merges_missing_vat_name_via_boolean_mask():
    # L2 (review.md): η παλιά υλοποίηση έκανε `.loc[(None, missing_name)]` σε
    # MultiIndex -- implementation-dependent None==NaN matching. Το boolean
    # mask πρέπει να βρίσκει τη γραμμή χωρίς ΑΦΜ και να τη μπερδεύει ως alias
    # στο ΑΦΜ του matched member.
    contractors = pd.DataFrame([
        {"vat": "090153025", "name": "ΕΤΑΙΡΕΙΑ Α", "n": 5, "first_year": 2021, "last_year": 2024},
        {"vat": float("nan"), "name": "ΕΤΑΙΡΕΙΑ Α ΟΕ", "n": 2, "first_year": 2020, "last_year": 2020},
    ])
    matches = pd.DataFrame([
        {"vat_l": "090153025", "name_l": "ΕΤΑΙΡΕΙΑ Α", "vat_r": None, "name_r": "ΕΤΑΙΡΕΙΑ Α ΟΕ", "jw_score": 1.0},
    ])

    groups = build_vat_groups(contractors, matches)

    names = {e["name"] for e in groups["090153025"]}
    assert names == {"ΕΤΑΙΡΕΙΑ Α", "ΕΤΑΙΡΕΙΑ Α ΟΕ"}


def test_build_vat_groups_rule2_ignores_low_confidence_matches():
    contractors = pd.DataFrame([
        {"vat": "090153025", "name": "ΕΤΑΙΡΕΙΑ Α", "n": 5, "first_year": 2021, "last_year": 2024},
        {"vat": float("nan"), "name": "ΕΤΑΙΡΕΙΑ Β", "n": 2, "first_year": 2020, "last_year": 2020},
    ])
    matches = pd.DataFrame([
        {"vat_l": "090153025", "name_l": "ΕΤΑΙΡΕΙΑ Α", "vat_r": None, "name_r": "ΕΤΑΙΡΕΙΑ Β", "jw_score": 0.9},
    ])

    groups = build_vat_groups(contractors, matches)

    names = {e["name"] for e in groups.get("090153025", [])}
    assert names == {"ΕΤΑΙΡΕΙΑ Α"}
