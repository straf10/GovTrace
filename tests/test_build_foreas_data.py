import math

import numpy as np
import pandas as pd

from build_foreas_data import sanitize


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
