"""E6 (Sprint E): tests για compute_indicators_v1.benford_indicator (METHODOLOGY §4.4).

Synthetic δείγματα (όχι πραγματικά δεδομένα -- η ίδια η digit-extraction λογική
μπορεί να τεσταριστεί πριν ολοκληρωθεί το E3 payment backfill).
"""

import numpy as np
import pandas as pd

from compute_indicators_v1 import MIN_N_BENFORD, benford_indicator


def _make_payments(amounts: list[float], vat: str = "090153025", year: int = 2024) -> pd.DataFrame:
    return pd.DataFrame({
        "vat_norm": [vat] * len(amounts),
        "organization.value": ["ΔΟΚΙΜΑΣΤΙΚΟΣ ΦΟΡΕΑΣ"] * len(amounts),
        "_source_year": [year] * len(amounts),
        "totalCostWithoutVAT": amounts,
    })


def test_benford_conforming_sample_scores_close_band():
    rng = np.random.default_rng(42)
    # log-uniform ποσά 10..100.000 -- το κλασματικό μέρος του log10 είναι
    # ομοιόμορφο, άρα το 1ο ψηφίο ακολουθεί ακριβώς την κατανομή Benford.
    amounts = 10 ** rng.uniform(1, 5, size=2000)
    df = _make_payments(list(amounts))

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["n_amounts"] == 2000
    assert row["nigrini_band_d1"] in ("close", "acceptable")
    assert row["mad_d1"] < 0.012


def test_uniform_sample_scores_nonconformity():
    rng = np.random.default_rng(7)
    # Ομοιόμορφα 3ψήφια ποσά (100-999) -- το 1ο ψηφίο είναι σχεδόν ομοιόμορφο
    # 1-9, ξεκάθαρη απόκλιση από Benford.
    amounts = rng.integers(100, 999, size=1000).astype(float)
    df = _make_payments(list(amounts))

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["nigrini_band_d1"] == "nonconformity"
    assert row["mad_d1"] > 0.015


def test_below_min_n_returns_no_score_row():
    amounts = [100.0 * i for i in range(1, MIN_N_BENFORD)]  # N = MIN_N_BENFORD - 1
    df = _make_payments(amounts)

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["mad_d1"] is None
    assert row["nigrini_band_d1"] is None
    assert "ανεπαρκή δεδομένα" in row["note"]


def test_zero_negative_and_nan_amounts_ignored():
    valid = list(10 ** np.random.default_rng(1).uniform(1, 5, size=MIN_N_BENFORD))
    junk = [0.0, -500.0, float("nan"), None]
    df = _make_payments(valid + junk)

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["n_amounts"] == MIN_N_BENFORD
    assert row["coverage_pct"] == round(100.0 * MIN_N_BENFORD / len(valid + junk), 1)


def test_second_digit_only_for_amounts_over_10():
    # 260 ποσά >=10 (έχουν 2ο ψηφίο, αλλά κάτω από MIN_N_BENFORD=300) + ποσά <10
    # (δεν έχουν 2ο ψηφίο) -- σύνολο N>=300 για το 1ο ψηφίο, αλλά ΟΧΙ για το 2ο.
    big = list(10 ** np.random.default_rng(2).uniform(1, 5, size=260))
    small = [1.0, 2.0, 3.0, 5.0, 7.0, 9.0] * 10  # 60 ποσά < 10, δεν έχουν 2ο ψηφίο
    df = _make_payments(big + small)

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["n_amounts"] == 320
    # 2ο ψηφίο test κάτω από MIN_N_BENFORD (μόνο 260 ποσά >=10) -> καμία βαθμολόγηση 2ου ψηφίου
    assert row["mad_d2"] is None
    assert row["nigrini_band_d2"] is None
    # Το 1ο ψηφίο ΠΑΡΑΜΕΝΕΙ βαθμολογημένο (N=320 >= MIN_N_BENFORD)
    assert row["mad_d1"] is not None


def test_all_period_row_present_alongside_yearly():
    amounts_2023 = list(10 ** np.random.default_rng(3).uniform(1, 5, size=200))
    amounts_2024 = list(10 ** np.random.default_rng(4).uniform(1, 5, size=200))
    df = pd.concat([
        _make_payments(amounts_2023, year=2023),
        _make_payments(amounts_2024, year=2024),
    ], ignore_index=True)

    out = benford_indicator(df)
    periods = set(out[out["vat"] == "090153025"]["period"])

    assert {"2023", "2024", "all"} <= periods
    all_row = out[(out["vat"] == "090153025") & (out["period"] == "all")].iloc[0]
    assert all_row["n_amounts"] == 400  # 200+200 -- επαρκές N σε επίπεδο "all" ακόμα κι αν κάθε έτος μόνο του δεν έφτανε


def test_missing_vat_norm_rows_excluded():
    df = _make_payments(list(10 ** np.random.default_rng(5).uniform(1, 5, size=MIN_N_BENFORD)))
    df.loc[0, "vat_norm"] = None  # μία γραμμή χωρίς φορέα -- αποκλείεται πλήρως, όχι μόνο από το digit test

    out = benford_indicator(df)
    row = out[(out["vat"] == "090153025") & (out["period"] == "2024")].iloc[0]

    assert row["n_amounts"] == MIN_N_BENFORD - 1  # η γραμμή χωρίς vat_norm δεν μετράει καν στο N
    assert row["mad_d1"] is None  # πέφτει κάτω από MIN_N_BENFORD
    assert "090153025" not in out[out["vat"].isna()]["vat"].values  # καμία γραμμή με άγνωστο vat
