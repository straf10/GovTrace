import json
import math

import numpy as np
import pandas as pd

import pytest

from build_foreas_data import (
    attach_indicators,
    attach_network,
    attach_replies,
    build_foreas_facts,
    percentiles_within_group,
    sanitize,
)


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
        {"org_vat": "090153025", "year": 2024, "n_contractors": 100, "n_new_contractors": 30, "new_winner_rate": 0.3, "left_censored": True},
        {"org_vat": "090153025", "year": 2025, "n_contractors": 90, "n_new_contractors": 20, "new_winner_rate": 0.2222, "left_censored": False},
    ])
    nwr.to_csv(graph_dir / "graph_features_org.csv", index=False)

    pages = {"090153025": {}, "999999999": {}}
    attach_network(pages, graph_dir=graph_dir)

    net = pages["090153025"]["network"]
    assert net["snapshot_date"] == "2026-07-12"
    assert net["top_neighbors"][0]["vat"] == "099755631"
    # M3 (review.md): left_censored περνάει από το CSV στο page JSON
    assert net["new_winner_rate"]["2024"] == {"value": 30.0, "n": 100, "left_censored": True}
    assert net["new_winner_rate"]["2025"] == {"value": 22.2, "n": 90, "left_censored": False}
    # φορέας χωρίς εγγραφή στο δίκτυο δεν παίρνει καθόλου το πεδίο (όχι κενό/None)
    assert "network" not in pages["999999999"]


def test_attach_network_no_op_when_graph_exports_missing(tmp_path):
    pages = {"090153025": {}}
    attach_network(pages, graph_dir=tmp_path / "does-not-exist")
    assert "network" not in pages["090153025"]


def test_attach_network_wires_nwr_when_ego_networks_missing(tmp_path):
    # L7 (review.md): αν λείπει το ego_networks.json αλλά υπάρχει το
    # graph_features_org.csv, το new-winner-rate ΔΕΝ πρέπει να χαθεί σιωπηλά.
    graph_dir = tmp_path / "gds"
    graph_dir.mkdir()
    nwr = pd.DataFrame([
        {"org_vat": "090153025", "year": 2025, "n_contractors": 90, "n_new_contractors": 20, "new_winner_rate": 0.2222, "left_censored": False},
    ])
    nwr.to_csv(graph_dir / "graph_features_org.csv", index=False)

    pages = {"090153025": {}}
    attach_network(pages, graph_dir=graph_dir)

    net = pages["090153025"]["network"]
    assert net["top_neighbors"] == []
    assert net["new_winner_rate"]["2025"]["n"] == 90


def test_attach_network_wires_ego_when_nwr_csv_missing(tmp_path):
    # L7 (review.md): συμμετρικά -- αν λείπει το graph_features_org.csv αλλά
    # υπάρχει το ego_networks.json, οι γείτονες ΔΕΝ πρέπει να χαθούν σιωπηλά.
    graph_dir = tmp_path / "gds"
    graph_dir.mkdir()
    ego = {
        "snapshot_date": "2026-07-12",
        "networks": {
            "090153025": [{"vat": "099755631", "name": "ΑΝΑΔΟΧΟΣ Α", "value": 1000.0, "n_awards": 2, "share": 50.0}],
        },
    }
    (graph_dir / "ego_networks.json").write_text(json.dumps(ego), encoding="utf-8")

    pages = {"090153025": {}}
    attach_network(pages, graph_dir=graph_dir)

    net = pages["090153025"]["network"]
    assert net["top_neighbors"][0]["vat"] == "099755631"
    assert net["new_winner_rate"] == {}


def test_attach_replies_wires_valid_reply(tmp_path):
    replies_dir = tmp_path / "replies"
    replies_dir.mkdir()
    (replies_dir / "090153025.json").write_text(
        json.dumps({"vat": "090153025", "replies": [{"date": "2026-01-01", "text": "Απάντηση φορέα."}]}),
        encoding="utf-8",
    )
    pages = {"090153025": {}}
    attach_replies(pages, replies_dir=replies_dir)
    assert pages["090153025"]["replies"] == [{"date": "2026-01-01", "text": "Απάντηση φορέα."}]


def test_attach_replies_raises_on_malformed_json(tmp_path):
    # M4 (review.md): κακοσχηματισμένο JSON δεν πρέπει να ρίχνει σιωπηλά ΟΛΟ
    # το nightly με άσχετο traceback -- ρητό SystemExit.
    replies_dir = tmp_path / "replies"
    replies_dir.mkdir()
    (replies_dir / "090153025.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SystemExit):
        attach_replies({"090153025": {}}, replies_dir=replies_dir)


def test_attach_replies_raises_on_bad_schema(tmp_path):
    # M4 (review.md): replies πρέπει να είναι λίστα από dicts με date/text strings.
    replies_dir = tmp_path / "replies"
    replies_dir.mkdir()
    (replies_dir / "090153025.json").write_text(
        json.dumps({"vat": "090153025", "replies": "not a list"}), encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        attach_replies({"090153025": {}}, replies_dir=replies_dir)


def test_percentiles_within_group_matches_naive_definition():
    # L9 (review.md): vectorized searchsorted πρέπει να δίνει το ίδιο
    # αποτέλεσμα με το naive (vals <= value).mean() ανά τιμή.
    values = pd.Series([float(i) for i in range(1, 25)])  # 24 >= MIN_GROUP_YEAR_FOR_PERCENTILE (20)
    result = percentiles_within_group(values)
    expected = values.map(lambda v: round(100.0 * float((values.to_numpy() <= v).mean()), 1))
    pd.testing.assert_series_equal(result, expected, check_names=False)


def test_percentiles_within_group_below_min_n_returns_nan():
    values = pd.Series([1.0, 2.0, 3.0])
    result = percentiles_within_group(values)
    assert result.isna().all()


def test_percentiles_within_group_ignores_nan_values():
    values = pd.Series([float(i) for i in range(1, 24)] + [float("nan")])
    result = percentiles_within_group(values)
    assert pd.isna(result.iloc[-1])
    assert not result.iloc[:-1].isna().any()


def test_build_foreas_facts_coerces_junk_amount_without_crashing():
    # L3 (review.md): float() γυμνό πάνω σε raw KIMDIS στήλη σκάει με
    # ValueError σε string junk· pd.to_numeric(errors="coerce") το κάνει None.
    auctions = pd.DataFrame([
        {
            "referenceNumber": "REF-1",
            "organization.value": "ΔΗΜΟΣ Α",
            "organizationVatNumber": "090153025",
            "title": "Τίτλος",
            "procedureType.key": "1",
            "procedureType.value": "Ανοιχτός",
            "totalCostWithVAT": "189,00 €",
            "totalCostWithoutVAT": None,
            "submissionDate": "2026-01-01",
            "cpv_code": None,
            "cpv_label": None,
            "contractor_vat": None,
            "contractor_name": None,
            "_source_year": 2026,
        }
    ])
    resolver = pd.Series(dtype=object)

    pages = build_foreas_facts(auctions, resolver)

    recent = pages["090153025"]["recent"]
    assert recent[0]["amount_with_vat"] is None
