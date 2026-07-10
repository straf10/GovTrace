import pandas as pd

from compute_indicators_v1 import (
    BIDS_SUBMITTED_MAX,
    MIN_N_SINGLE_BID,
    _threshold_for,
    composite_indicator,
    single_bid_rate,
)


def test_threshold_pre_2021_flat_20000():
    when = pd.Timestamp("2021-01-15")
    assert _threshold_for("Έργα", when) == 20_000.0
    assert _threshold_for("Προμήθειες", when) == 20_000.0


def test_threshold_post_2021_works_60000():
    when = pd.Timestamp("2021-06-01")
    assert _threshold_for("Έργα", when) == 60_000.0
    assert _threshold_for("Μελέτες", when) == 60_000.0


def test_threshold_post_2021_non_works_30000():
    when = pd.Timestamp("2022-01-01")
    assert _threshold_for("Προμήθειες", when) == 30_000.0


def test_threshold_returns_none_for_missing_date():
    assert _threshold_for("Έργα", pd.NaT) is None


def _contract_row(vat, year, month, bids, proc_key="1"):
    return {
        "vat_norm": vat,
        "_source_year": year,
        "_source_month": month,
        "organization.value": f"ΦΟΡΕΑΣ {vat}",
        "typeOfProcedure.key": proc_key,
        "typeOfProcedure.value": None,
        "bidsSubmitted": bids,
        "contract.bidsSubmitted": None,
    }


def test_single_bid_starts_at_first_full_month_and_excludes_non_competitive():
    rows = (
        [_contract_row("1", 2025, 3, 1)] * 20
        + [_contract_row("1", 2025, 4, 1)] * 3
        + [_contract_row("1", 2025, 4, 2)] * 2
        + [_contract_row("1", 2025, 4, 1, proc_key="12")] * 10
    )
    result = single_bid_rate(pd.DataFrame(rows))
    row = result.iloc[0]

    assert row["year"] == 2025
    assert row["n_competitive"] == 5
    assert row["n_with_bids"] == 5
    assert row["n_single_bid"] == 3
    assert row["single_bid_pct"] == 60.0


def test_single_bid_flags_and_excludes_garbage_bids_values():
    rows = (
        [_contract_row("1", 2025, 4, 1)] * MIN_N_SINGLE_BID
        + [_contract_row("1", 2025, 4, 0)]
        + [_contract_row("1", 2025, 4, BIDS_SUBMITTED_MAX + 1)]
    )
    result = single_bid_rate(pd.DataFrame(rows))
    row = result.iloc[0]

    assert row["n_competitive"] == MIN_N_SINGLE_BID + 2
    assert row["n_with_bids"] == MIN_N_SINGLE_BID
    assert row["n_bids_outliers"] == 2
    assert row["single_bid_pct"] == 100.0


def test_composite_indicator_uses_only_published_flags():
    da = pd.DataFrame(
        [
            {
                "organization_vat": "1",
                "organization_name": "ΦΟΡΕΑΣ 1",
                "year": 2025,
                "da_count_pct": 50.0,
                "da_value_pct": 20.0,
                "n_total": 10,
            },
            {
                "organization_vat": "2",
                "organization_name": "ΦΟΡΕΑΣ 2",
                "year": 2025,
                "da_count_pct": 10.0,
                "da_value_pct": 30.0,
                "n_total": 10,
            },
        ]
    )
    hhi = pd.DataFrame([{"organization_vat": "1", "year": 2025, "hhi": 0.4, "n_contracts": 10}])
    dr = pd.DataFrame([{"organization_vat": "1", "year": 2025, "pct_near_zero_discount": 25.0, "n_linked": 5}])
    dl = pd.DataFrame(
        [
            {"vat": "1", "year": 2025, "median_deadline_days": 10.0, "n_notices": 5},
            {"vat": "2", "year": 2025, "median_deadline_days": 30.0, "n_notices": 5},
        ]
    )
    sb = pd.DataFrame([{"organization_vat": "1", "year": 2025, "single_bid_pct": 75.0, "n_with_bids": 5}])

    result = composite_indicator(da, hhi, dr, dl, sb)
    row = result[result["vat"] == "1"].iloc[0]

    expected = round((0.5 + 0.2 + 0.4 + 0.25 + 0.5 + 0.75) / 6, 4)
    assert row["n_flags"] == 6
    assert row["composite_score"] == expected
    assert "flag_single_bid" in result.columns
    assert "pct_short_deadline" not in result.columns
