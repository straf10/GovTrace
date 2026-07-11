"""E8 Stage A: tests πάνω σε synthetic auction parquet fixture (όχι πραγματικά δεδομένα).

Ελέγχει: header format (neo4j-admin ID/START_ID/END_ID conventions), ΑΦΜ resolution
μέσω persisted vat_resolver.csv, aggregate AWARDS_TO αθροίσματα σωστά, exit code σε
>0,5% parse failures. ΚΑΜΙΑ αλλαγή στο production pipeline/site.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "graph"))

from build_graph_staging import build  # noqa: E402


def _write_fixture(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "referenceNumber": "24AWRD000001",
            "organizationVatNumber": "090153025",
            "organization.value": "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ",
            "nutsCode.key": "EL305",
            "nutsCode.value": "Ανατολική Αττική",
            "totalCostWithoutVAT": 50000.0,
            "totalCostWithVAT": 62000.0,
            "cancelled": False,
            "submissionDate": "2024-01-05",
            "signedDate": "2024-01-10",
            "contractingDataDetails.contractingMembersDataList": json.dumps(
                [{"vatNumber": "800349931", "name": "ΑΝΑΔΟΧΟΣ Α ΑΕ"}], ensure_ascii=False
            ),
            "objectDetailsList": json.dumps(
                [{"cpvs": [{"key": "77320000-9", "value": "Συντήρηση αθλητικών γηπέδων"}]}], ensure_ascii=False
            ),
        },
        {
            # ΑΦΜ φορέα λείπει, όνομα υπάρχει -- πρέπει να λυθεί μέσω vat_resolver.csv
            "referenceNumber": "24AWRD000002",
            "organizationVatNumber": None,
            "organization.value": "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ",
            "nutsCode.key": "EL305",
            "nutsCode.value": "Ανατολική Αττική",
            "totalCostWithoutVAT": 20000.0,
            "totalCostWithVAT": 24800.0,
            "cancelled": True,
            "submissionDate": "2024-01-06",
            "signedDate": "2024-01-11",
            "contractingDataDetails.contractingMembersDataList": json.dumps(
                [{"vatNumber": "800349931", "name": "ΑΝΑΔΟΧΟΣ Α ΑΕ"}], ensure_ascii=False
            ),
            "objectDetailsList": json.dumps(
                [{"cpvs": [{"key": "77320000-9", "value": "Συντήρηση αθλητικών γηπέδων"}]}], ensure_ascii=False
            ),
        },
        {
            # ΑΦΜ φορέα λείπει (όχι κατεστραμμένο -- απλώς κενό) ΚΑΙ όνομα εκτός resolver
            # -- ΔΕΝ μετράει ως parse failure (κενό πεδίο, όχι σκουπίδι), απλώς εξαιρείται
            # από τα awards (χωρίς επιλύσιμο ΑΦΜ φορέα).
            "referenceNumber": "24AWRD000003",
            "organizationVatNumber": None,
            "organization.value": "ΑΓΝΩΣΤΟΣ ΦΟΡΕΑΣ ΧΩΡΙΣ RESOLVER",
            "nutsCode.key": None,
            "nutsCode.value": None,
            "totalCostWithoutVAT": None,
            "totalCostWithVAT": None,
            "cancelled": False,
            "submissionDate": "2024-01-07",
            "signedDate": None,
            "contractingDataDetails.contractingMembersDataList": None,
            "objectDetailsList": None,
        },
    ]
    df = pd.DataFrame(rows)
    df.to_parquet(raw_dir / "auction_2024_01.parquet", index=False)


def _write_resolver(processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"name": ["ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ"], "vat": ["090153025"]}
    ).to_csv(processed_dir / "vat_resolver.csv", index=False, encoding="utf-8-sig")


def test_header_format_and_relationships(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    _write_fixture(raw_dir)
    _write_resolver(processed_dir)

    report = build(["2024-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)

    orgs = pd.read_csv(staging_dir / "organizations.csv", dtype=str)
    assert "vat:ID(Organization)" in orgs.columns
    assert "090153025" in orgs["vat:ID(Organization)"].values

    awards = pd.read_csv(staging_dir / "awards.csv")
    assert "adam:ID(Award)" in awards.columns
    assert "amount_ex_vat:double" in awards.columns  # #20: ΧΩΡΙΣ ΦΠΑ, όχι amount_vat
    assert "cancelled:boolean" in awards.columns

    rel_issued = pd.read_csv(staging_dir / "rel_issued.csv")
    assert list(rel_issued.columns) == [":START_ID(Organization)", ":END_ID(Award)"]

    assert report["n_cancelled"] == 1


def test_vat_resolution_via_resolver(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    _write_fixture(raw_dir)
    _write_resolver(processed_dir)

    build(["2024-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)

    orgs = pd.read_csv(staging_dir / "organizations.csv", dtype=str)
    # Και οι δύο εγγραφές (μία με δικό της ΑΦΜ, μία μέσω resolver) καταλήγουν στο ίδιο Organization node.
    assert len(orgs) == 1
    assert orgs.iloc[0]["vat:ID(Organization)"] == "090153025"


def test_awards_to_aggregate_sums_correctly(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    _write_fixture(raw_dir)
    _write_resolver(processed_dir)

    build(["2024-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)

    rel_awards_to = pd.read_csv(staging_dir / "rel_awards_to.csv")
    assert len(rel_awards_to) == 1
    row = rel_awards_to.iloc[0]
    assert row["n_awards:long"] == 2
    assert row["total_amount_ex_vat:double"] == pytest.approx(70000.0)


def test_parse_failures_over_threshold_exit_nonzero(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)

    # 3 από 3 γραμμές έχουν άκυρο ΑΦΜ ΚΑΙ όνομα εκτός resolver -- 100% parse failure > 0,5%.
    rows = [
        {
            "referenceNumber": f"24AWRD00000{i}",
            "organizationVatNumber": "not-a-vat",
            "organization.value": "ΑΓΝΩΣΤΟΣ",
            "nutsCode.key": None, "nutsCode.value": None,
            "totalCostWithoutVAT": 1000.0, "totalCostWithVAT": 1240.0,
            "cancelled": False, "submissionDate": "2024-01-01", "signedDate": "2024-01-02",
            "contractingDataDetails.contractingMembersDataList": None,
            "objectDetailsList": None,
        }
        for i in range(3)
    ]
    pd.DataFrame(rows).to_parquet(raw_dir / "auction_2024_01.parquet", index=False)

    with pytest.raises(SystemExit):
        build(["2024-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)


def test_no_matching_files_raises(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)

    with pytest.raises(SystemExit):
        build(["2099-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)


def test_missing_month_recorded_in_qa_report(tmp_path: Path) -> None:
    # #10 (CHECK 2026-07-11): μήνας που λείπει καταγράφεται στο qa_report
    # (months_found/months_missing) αντί να παραλείπεται σιωπηλά.
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    _write_fixture(raw_dir)
    _write_resolver(processed_dir)

    report = build(
        ["2024-01", "2024-02"], ["auction"],
        raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir,
    )

    assert report["months_found"] == ["auction:2024-01"]
    assert report["months_missing"] == ["auction:2024-02"]


def test_garbage_amount_sanitized_and_object_dtype_survives(tmp_path: Path) -> None:
    # #9 (CHECK 2026-07-11): (α) τιμές > VALUE_SANITY_CAP μηδενίζονται όπως στο
    # production pipeline· (β) στήλη ποσού γεμάτη None (ελλιπές σχήμα μήνα,
    # object dtype μετά το concat) δεν ρίχνει το AWARDS_TO agg.
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    raw_dir.mkdir(parents=True)
    _write_resolver(processed_dir)

    members = json.dumps([{"vatNumber": "800349931", "name": "ΑΝΑΔΟΧΟΣ Α ΑΕ"}], ensure_ascii=False)
    rows = [
        {
            "referenceNumber": "24AWRD000001",
            "organizationVatNumber": "090153025",
            "organization.value": "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ",
            "nutsCode.key": None, "nutsCode.value": None,
            "totalCostWithoutVAT": 80_000_000_000_000.0,  # garbage > 10 δισ. cap
            "cancelled": False, "signedDate": "2024-01-10",
            "contractingDataDetails.contractingMembersDataList": members,
            "objectDetailsList": None,
        },
        {
            "referenceNumber": "24AWRD000002",
            "organizationVatNumber": "090153025",
            "organization.value": "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ",
            "nutsCode.key": None, "nutsCode.value": None,
            "totalCostWithoutVAT": 1000.0,
            "cancelled": False, "signedDate": "2024-01-11",
            "contractingDataDetails.contractingMembersDataList": members,
            "objectDetailsList": None,
        },
    ]
    pd.DataFrame(rows).to_parquet(raw_dir / "auction_2024_01.parquet", index=False)

    # Δεύτερος μήνας ΧΩΡΙΣ τη στήλη ποσού -- το load_months τη γεμίζει με None
    # (object dtype μετά το concat, το γνωστό F1 pattern).
    rows_no_amount = [{k: v for k, v in rows[1].items() if k != "totalCostWithoutVAT"} | {"referenceNumber": "24AWRD000003"}]
    pd.DataFrame(rows_no_amount).to_parquet(raw_dir / "auction_2024_02.parquet", index=False)

    build(["2024-01", "2024-02"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)

    awards = pd.read_csv(staging_dir / "awards.csv")
    garbage_row = awards[awards["adam:ID(Award)"] == "24AWRD000001"]
    assert garbage_row["amount_ex_vat:double"].isna().all()  # μηδενίστηκε από sanitize_value

    rel_awards_to = pd.read_csv(staging_dir / "rel_awards_to.csv")
    assert rel_awards_to.iloc[0]["total_amount_ex_vat:double"] == pytest.approx(1000.0)
    assert rel_awards_to.iloc[0]["n_awards:long"] == 3


def test_string_cancelled_values_map_correctly(tmp_path: Path) -> None:
    # #12 (CHECK 2026-07-11): string "false" ΔΕΝ πρέπει να γίνει True.
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    staging_dir = tmp_path / "staging"
    raw_dir.mkdir(parents=True)
    _write_resolver(processed_dir)

    rows = [
        {
            "referenceNumber": f"24AWRD00000{i}",
            "organizationVatNumber": "090153025",
            "organization.value": "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ",
            "nutsCode.key": None, "nutsCode.value": None,
            "totalCostWithoutVAT": 1000.0,
            "cancelled": cancelled, "signedDate": "2024-01-10",
            "contractingDataDetails.contractingMembersDataList": None,
            "objectDetailsList": None,
        }
        for i, cancelled in enumerate(["false", "true", None])
    ]
    pd.DataFrame(rows).to_parquet(raw_dir / "auction_2024_01.parquet", index=False)

    report = build(["2024-01"], ["auction"], raw_dir=raw_dir, processed_dir=processed_dir, staging_dir=staging_dir)

    assert report["n_cancelled"] == 1  # μόνο το "true"
