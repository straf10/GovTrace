"""E7 (Sprint E POC): entity resolution αναδόχων με Splink (DuckDB backend).

ΚΑΜΙΑ αλλαγή στο production entity keying/δείκτες -- διαβάζει
``data/processed/er/contractors_raw.csv`` (βλ. build_contractor_table.py) και
γράφει ΜΟΝΟ σε ``data/processed/er/``.

Blocking rules (αποφεύγουν πλήρες pairwise σε 557k γραμμές):
  (i) ίδιο normalized ΑΦΜ (πιάνει ονόματα-παραλλαγές του ίδιου νομικού προσώπου)
  (ii) ίδια πρώτα 4 κανονικοποιημένα tokens ονόματος (πιάνει υποψήφια
       διαφορετικού/απόντος ΑΦΜ αλλά όμοιου ονόματος)
Comparison: Jaro-Winkler στο κανονικοποιημένο όνομα (χωρίς διεύθυνση -- το
πεδίο δεν υπάρχει στο contractingMembersDataList, επιβεβαιωμένο στο schema).

Χρήση:
    python scripts/er/splink_poc.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import splink.comparison_library as cl  # noqa: E402
from splink import DuckDBAPI, Linker, SettingsCreator, block_on  # noqa: E402

IN_PATH = Path("data/processed/er/contractors_raw.csv")
MATCHES_PATH = Path("data/processed/er/entity_resolution_matches.csv")
MATCH_THRESHOLD = 0.5  # γράφονται όλα τα ζεύγη πάνω από αυτό· το audit ανά probability band

_TOKEN_RE = re.compile(r"[Α-Ωα-ωA-Za-z0-9]+")


def normalize_name(name: str) -> str:
    """Uppercase + μόνο αλφαριθμητικά tokens, χωρίς νομική μορφή/σημεία στίξης."""
    tokens = _TOKEN_RE.findall(name.upper())
    return " ".join(tokens)


def name_prefix(name_norm: str, n_tokens: int = 4) -> str:
    tokens = name_norm.split(" ")
    return " ".join(tokens[:n_tokens])


def main() -> None:
    t0 = time.time()
    df = pd.read_csv(IN_PATH, dtype={"vat": str})
    df["unique_id"] = df.index
    df["name_norm"] = df["name"].fillna("").map(normalize_name)
    df["name_prefix"] = df["name_norm"].map(name_prefix)
    print(f"Input: {len(df)} γραμμές")

    db_api = DuckDBAPI()

    settings = SettingsCreator(
        link_type="dedupe_only",
        blocking_rules_to_generate_predictions=[
            block_on("vat"),
            block_on("name_prefix"),
        ],
        comparisons=[
            cl.JaroWinklerAtThresholds("name_norm", score_threshold_or_thresholds=[0.9, 0.7]),
        ],
        retain_matching_columns=True,
        retain_intermediate_calculation_columns=False,
    )

    linker = Linker(df, settings, db_api=db_api)
    linker.training.estimate_probability_two_random_records_match(
        [block_on("vat")], recall=0.7
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=2e6)
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("name_prefix"))

    predictions = linker.inference.predict(threshold_match_probability=MATCH_THRESHOLD)
    pred_df = predictions.as_pandas_dataframe()
    print(f"Ζεύγη πάνω από threshold {MATCH_THRESHOLD}: {len(pred_df)}")
    print("Στήλες predictions:", list(pred_df.columns))

    out = pred_df[
        [
            "unique_id_l", "unique_id_r",
            "vat_l", "vat_r",
            "name_norm_l", "name_norm_r",
            "match_probability",
        ]
    ].rename(columns={
        "unique_id_l": "id_l", "unique_id_r": "id_r",
        "name_norm_l": "name_l", "name_norm_r": "name_r",
    })
    MATCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(MATCHES_PATH, index=False, encoding="utf-8-sig")
    print(f"Γράφτηκε {MATCHES_PATH} σε {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
