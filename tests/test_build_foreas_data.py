import json
import math

import numpy as np
import pandas as pd

from build_foreas_data import attach_indicators, attach_network, sanitize


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


def test_attach_network_wires_ego_neighbors_and_new_winner_rate(tmp_path):
    # P2-11/P2-17: το graph_staging/gds είναι offline/χειροκίνητο (βλ.
    # docs/PHASE_2.md R-06) -- ελέγχουμε ότι τα δύο exports του
    # scripts/graph/queries.py ενώνονται σωστά ανά ΑΦΜ φορέα.
    graph_dir = tmp_path / "gds"
    graph_dir.mkdir()
    ego = {
        "snapshot_date": "2026-07-12",
        "top_n": 30,
        "networks": {
            "090153025": [{"vat": "099755631", "name": "ΑΝΑΔΟΧΟΣ Α", "value": 1000.0, "n_awards": 2, "share": 50.0}],
        },
    }
    (graph_dir / "ego_networks.json").write_text(json.dumps(ego), encoding="utf-8")
    nwr = pd.DataFrame([
        {"org_vat": "090153025", "year": 2024, "n_contractors": 100, "n_new_contractors": 30, "new_winner_rate": 0.3},
        {"org_vat": "090153025", "year": 2025, "n_contractors": 90, "n_new_contractors": 20, "new_winner_rate": 0.2222},
    ])
    nwr.to_csv(graph_dir / "graph_features_org.csv", index=False)

    pages = {"090153025": {}, "999999999": {}}
    attach_network(pages, graph_dir=graph_dir)

    net = pages["090153025"]["network"]
    assert net["snapshot_date"] == "2026-07-12"
    assert net["top_neighbors"][0]["vat"] == "099755631"
    assert net["new_winner_rate"]["2025"] == {"value": 22.2, "n": 90}
    # φορέας χωρίς εγγραφή στο δίκτυο δεν παίρνει καθόλου το πεδίο (όχι κενό/None)
    assert "network" not in pages["999999999"]


def test_attach_network_no_op_when_graph_exports_missing(tmp_path):
    pages = {"090153025": {}}
    attach_network(pages, graph_dir=tmp_path / "does-not-exist")
    assert "network" not in pages["090153025"]
