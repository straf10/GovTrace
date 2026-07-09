import pandas as pd

from compute_indicators_v1 import _threshold_for


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
