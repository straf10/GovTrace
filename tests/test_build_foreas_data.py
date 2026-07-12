import math

import numpy as np
import pandas as pd

from build_foreas_data import attach_indicators, sanitize


def _empty(cols):
    return pd.DataFrame(columns=cols)


def test_sanitize_converts_nan_to_none():
    assert sanitize({"a": float("nan")}) == {"a": None}


def test_sanitize_converts_nat_to_none():
    assert sanitize(pd.NaT) is None


def test_sanitize_converts_numpy_scalars_to_python_native():
    out = sanitize({"n": np.int64(5), "f": np.float64(1.5)})
    assert out == {"n": 5, "f": 1.5}
    assert isinstance(out["n"], int)
    assert isinstance(out["f"], float)


def test_sanitize_recurses_into_nested_structures():
    out = sanitize({"list": [1, float("nan"), {"x": np.int64(2)}]})
    assert out == {"list": [1, None, {"x": 2}]}


def test_sanitize_leaves_normal_values_unchanged():
    assert sanitize({"s": "text", "b": True, "i": 3}) == {"s": "text", "b": True, "i": 3}


def test_attach_indicators_benford_carries_both_period_levels():
    # E6: η κάρτα /foreas/<vat>/ χρειάζεται ΚΑΙ την ετήσια γραμμή ΚΑΙ τη
    # γραμμή "all" (fallback για φορείς που δεν πιάνουν N=300/έτος).
    pages = {"090153025": {}}
    benford = pd.DataFrame(
        [
            {
                "vat": "090153025", "period": "2024", "n_amounts": 500,
                "mad_d1": 0.006, "nigrini_band_d1": "close",
                "mad_d2": 0.008, "nigrini_band_d2": "close", "coverage_pct": 99.0,
            },
            {
                "vat": "090153025", "period": "all", "n_amounts": 120,
                "mad_d1": float("nan"), "nigrini_band_d1": None,
                "mad_d2": float("nan"), "nigrini_band_d2": None, "coverage_pct": 99.0,
            },
        ]
    )
    empty_org = _empty(["organization_vat", "year"])
    empty_vat = _empty(["vat", "year"])
    entities = _empty(["vat"])

    attach_indicators(
        pages, empty_org, empty_org, empty_org, empty_org, empty_vat, empty_vat,
        benford, {}, pd.Series(dtype=object), entities,
    )

    result = pages["090153025"]["indicators"]["benford"]
    assert set(result.keys()) == {"2024", "all"}
    assert result["2024"]["value"] == 0.006
    assert result["2024"]["insufficient_data"] is False
    assert result["all"]["insufficient_data"] is True
