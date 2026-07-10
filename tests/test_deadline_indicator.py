import pandas as pd

from compute_indicators_v1 import (
    MIN_N_DEADLINE,
    NOTICE_DIRECT_AWARD_KEY,
    NON_COMPETITIVE_PROCEDURE_KEYS,
    deadline_indicator,
)


def _notice_row(vat, year, proc_key, submission, final):
    return {
        "vat_norm": vat,
        "_source_year": year,
        "organization.value": f"ΦΟΡΕΑΣ {vat}",
        "typeOfProcedure.key": proc_key,
        "typeOfProcedure.value": None,
        "submissionDate": submission,
        "finalSubmissionDate": final,
    }


def test_empty_input_returns_empty_frame():
    assert deadline_indicator(pd.DataFrame()).empty


def test_direct_awards_excluded_from_competitive_denominator():
    rows = [_notice_row("1", 2022, NOTICE_DIRECT_AWARD_KEY, "2022-01-01", "2022-01-01")] * 10
    # καμία ανταγωνιστική εγγραφή -> καμία γραμμή φορέα/έτους στο αποτέλεσμα
    result = deadline_indicator(pd.DataFrame(rows))
    assert result.empty


def test_article_32_and_128_procedures_excluded_from_deadline_indicator():
    rows = (
        [_notice_row("1", 2025, "1", "2025-05-01", "2025-05-21")] * MIN_N_DEADLINE
        + [_notice_row("1", 2025, "12", "2025-05-01", "2025-05-02")] * 20
        + [_notice_row("1", 2025, "18", "2025-05-01", "2025-05-03")] * 20
    )
    result = deadline_indicator(pd.DataFrame(rows))
    row = result.iloc[0]

    assert {"6", "12", "18"}.issubset(NON_COMPETITIVE_PROCEDURE_KEYS)
    assert row["n_notices"] == MIN_N_DEADLINE
    assert row["median_deadline_days"] == 20.0
    assert row["coverage_pct"] == 100.0


def test_negative_and_missing_dates_excluded_as_invalid():
    rows = (
        [_notice_row("1", 2022, "1", "2022-06-10", "2022-06-20")] * 5  # 10 μέρες, έγκυρες
        + [_notice_row("1", 2022, "1", "2022-06-15", "2022-06-10")] * 3  # αρνητική διάρκεια -- εξαιρείται
        + [_notice_row("1", 2022, "1", None, "2022-06-20")] * 2  # κενή ημερομηνία -- εξαιρείται
    )
    result = deadline_indicator(pd.DataFrame(rows))
    row = result.iloc[0]
    assert row["n_notices"] == 5
    assert row["median_deadline_days"] == 10.0
    assert row["coverage_pct"] == 50.0  # 5/10 έγκυρες από τις ανταγωνιστικές
    assert row["note"] is None
    assert row["pct_short_deadline"] is None  # εκκρεμεί νομική επιβεβαίωση, βλ. METHODOLOGY §4.7


def test_below_min_n_flagged_insufficient():
    rows = [_notice_row("2", 2023, "1", "2023-01-01", f"2023-01-{10 + i:02d}") for i in range(MIN_N_DEADLINE - 1)]
    result = deadline_indicator(pd.DataFrame(rows))
    row = result.iloc[0]
    assert row["median_deadline_days"] is None or pd.isna(row["median_deadline_days"])
    assert "ανεπαρκή δεδομένα" in row["note"]


def test_rows_missing_vat_norm_are_dropped():
    rows = [_notice_row(None, 2022, "1", "2022-01-01", "2022-01-15")] * MIN_N_DEADLINE
    result = deadline_indicator(pd.DataFrame(rows))
    assert result.empty
