import pandas as pd

from kimdis_data import build_vat_resolver, is_valid_vat_checksum, load_entity, normalize_vat, resolve_vat


def test_normalize_vat_valid_nine_digits():
    assert normalize_vat("090016590") == "090016590"


def test_normalize_vat_strips_whitespace_and_tabs():
    assert normalize_vat("\t090016590") == "090016590"
    assert normalize_vat(" 090153025") == "090153025"


def test_normalize_vat_pads_short_values():
    assert normalize_vat("1234567") == "001234567"  # 7 digits -> zfill(9)


def test_normalize_vat_strips_leading_zeros_when_too_long():
    assert normalize_vat("00901536025") == "901536025"  # 11 digits -> lstrip zeros then valid


def test_normalize_vat_rejects_all_zeros():
    assert normalize_vat("000000000") is None


def test_normalize_vat_rejects_non_string():
    assert normalize_vat(None) is None
    assert normalize_vat(90016590) is None


def test_normalize_vat_rejects_garbage():
    assert normalize_vat("abc") is None
    assert normalize_vat("12") is None


def test_is_valid_vat_checksum_accepts_known_valid_vat():
    assert is_valid_vat_checksum("090016590") is True
    assert is_valid_vat_checksum("090153025") is True


def test_is_valid_vat_checksum_rejects_bad_check_digit():
    assert is_valid_vat_checksum("111111112") is False


def test_is_valid_vat_checksum_rejects_malformed_input():
    assert is_valid_vat_checksum("abc") is False
    assert is_valid_vat_checksum("12345") is False
    assert is_valid_vat_checksum(None) is False


def test_build_vat_resolver_keeps_dominant_vat_above_threshold():
    df = pd.DataFrame(
        {
            "organization.value": ["Δήμος Α"] * 10 + ["Δήμος Α"],
            "organizationVatNumber": ["090016590"] * 10 + ["999999990"],
        }
    )
    resolver = build_vat_resolver([df], min_share=0.9)
    assert resolver["Δήμος Α"] == "090016590"


def test_build_vat_resolver_drops_ambiguous_names_below_threshold():
    df = pd.DataFrame(
        {
            "organization.value": ["Πανεπιστήμιο Χ"] * 5 + ["Πανεπιστήμιο Χ"] * 5,
            "organizationVatNumber": ["111111112"] * 5 + ["222222223"] * 5,
        }
    )
    resolver = build_vat_resolver([df], min_share=0.9)
    assert "Πανεπιστήμιο Χ" not in resolver.index


def test_resolve_vat_prefers_own_vat_then_falls_back_to_resolver():
    resolver = pd.Series({"Δήμος Β": "111111112"})
    df = pd.DataFrame(
        {
            "organization.value": ["Δήμος Α", "Δήμος Β"],
            "organizationVatNumber": ["090016590", None],
        }
    )
    out = resolve_vat(df, resolver)
    assert list(out) == ["090016590", "111111112"]


def test_load_entity_dedupes_reference_number(tmp_path):
    df1 = pd.DataFrame({"referenceNumber": ["24REQ001", "24REQ002"], "value": [1, 2]})
    df2 = pd.DataFrame({"referenceNumber": ["24REQ002"], "value": [999]})  # duplicate, newer
    df1.to_parquet(tmp_path / "auction_2024_01.parquet", index=False)
    df2.to_parquet(tmp_path / "auction_2024_02.parquet", index=False)

    result = load_entity("auction", raw_dir=tmp_path)
    assert sorted(result["referenceNumber"]) == ["24REQ001", "24REQ002"]
    kept = result[result["referenceNumber"] == "24REQ002"]
    assert kept["value"].iloc[0] == 999  # keep="last" -> το πιο πρόσφατο αρχείο κερδίζει


def test_load_entity_keeps_rows_with_null_reference_number(tmp_path):
    # F4: το drop_duplicates θεωρούσε τα NaN ίσα -- πολλαπλές γραμμές χωρίς ΑΔΑΜ
    # έπρεπε να επιζήσουν, όχι να συγχωνευτούν σε μία.
    df = pd.DataFrame({"referenceNumber": [None, None, "24REQ001"], "value": [1, 2, 3]})
    df.to_parquet(tmp_path / "auction_2024_01.parquet", index=False)

    result = load_entity("auction", raw_dir=tmp_path)
    assert len(result) == 3


def test_load_entity_fills_column_missing_from_one_file_schema(tmp_path):
    # F1: pd.read_parquet(columns=) σκάει με ArrowInvalid όταν λείπει στήλη από
    # ένα αρχείο (π.χ. auction_2025_05 χωρίς typeOfContractingAuthority).
    df1 = pd.DataFrame({"referenceNumber": ["24REQ001"], "typeOfContractingAuthority": ["A"]})
    df2 = pd.DataFrame({"referenceNumber": ["24REQ002"]})  # λείπει η στήλη
    df1.to_parquet(tmp_path / "auction_2024_01.parquet", index=False)
    df2.to_parquet(tmp_path / "auction_2024_02.parquet", index=False)

    result = load_entity("auction", raw_dir=tmp_path, columns=["referenceNumber", "typeOfContractingAuthority"])
    assert sorted(result["referenceNumber"]) == ["24REQ001", "24REQ002"]
    missing_row = result[result["referenceNumber"] == "24REQ002"]
    assert missing_row["typeOfContractingAuthority"].isna().all()
