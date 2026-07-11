"""E8 Stage A (Sprint E): staging CSVs για δοκιμαστικό 3μηνο neo4j-admin import.

ΚΑΝΕΝΑ δικό του keying rebuild -- διαβάζει τα ΕΤΟΙΜΑ ``data/processed/vat_resolver.csv``
και ``data/processed/entities.csv`` (ίδιος κανόνας με το production pipeline). Γράφει
ΜΟΝΟ στο ``data/graph_staging/`` (gitignored, όπως όλο το ``data/``). Καμία αλλαγή στο
production pipeline/site.

Μοντέλο 5 labels (NEO4J_INTEGRATION_FINAL.md): Organization, Contractor, Award, CPV, Nuts.
Ποσά multi-member: ΜΟΝΟ στο member_index 0 (συνέπεια με HHI v1, kimdis pipeline).

Χρήση (το --months είναι ρητή λίστα, ΟΧΙ range -- #10, CHECK 2026-07-11):
    python scripts/graph/build_graph_staging.py --months 2024-01 2024-02 2024-03 --entities auction
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kimdis_data import RAW_DIR, normalize_vat, sanitize_value  # noqa: E402

STAGING_DIR = Path("data/graph_staging")
PROCESSED_DIR = Path("data/processed")
PARSE_FAILURE_THRESHOLD = 0.005  # 0.5% -- ίδιος κανόνας με το production pipeline

MEMBERS_COL = "contractingDataDetails.contractingMembersDataList"
CPV_COL = "objectDetailsList"

# #18 (CHECK 2026-07-11): submissionDate/totalCostWithVAT αφαιρέθηκαν --
# διαβάζονταν αλλά δεν χρησιμοποιούνταν σε κανένα output (άχρηστο I/O).
AUCTION_COLUMNS = [
    "referenceNumber", "organizationVatNumber", "organization.value",
    "nutsCode.key", "nutsCode.value", "totalCostWithoutVAT",
    "cancelled", "signedDate",
    MEMBERS_COL, CPV_COL,
]


def load_months(
    months: list[str], entities: list[str], raw_dir: Path
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Επιστρέφει (df, months_found, months_missing).

    #10 (CHECK 2026-07-11): αρχεία που λείπουν δεν παραλείπονται πια σιωπηλά --
    καταγράφονται σε warning και στο qa_report, ώστε ο αναγνώστης του report
    να ξέρει τι ΒΡΕΘΗΚΕ, όχι μόνο τι ζητήθηκε.
    """
    frames = []
    months_found: list[str] = []
    months_missing: list[str] = []
    for entity in entities:
        for month in months:
            year, mm = month.split("-")
            f = raw_dir / f"{entity}_{year}_{int(mm):02d}.parquet"
            if not f.exists():
                months_missing.append(f"{entity}:{month}")
                print(f"WARNING: λείπει το {f} -- ο μήνας {month} ({entity}) ΔΕΝ μπαίνει στον γράφο")
                continue
            months_found.append(f"{entity}:{month}")
            available = set(pq.ParquetFile(f).schema_arrow.names)
            present = [c for c in AUCTION_COLUMNS if c in available]
            df = pd.read_parquet(f, columns=present)
            for missing in AUCTION_COLUMNS:
                if missing not in df.columns:
                    df[missing] = None
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=AUCTION_COLUMNS), months_found, months_missing
    return pd.concat(frames, ignore_index=True), months_found, months_missing


def first_member(raw: str | None, failures: list | None = None) -> dict | None:
    # Κενό πεδίο (None/NaN από parquet) ΔΕΝ είναι parse failure -- μόνο
    # string που αποτυγχάνει στο json.loads μετράει ως σκουπίδι.
    if not isinstance(raw, str) or not raw:
        return None
    try:
        members = json.loads(raw)
    except json.JSONDecodeError:
        # #11 (CHECK 2026-07-11): οι JSON parse αποτυχίες μετρώνται πλέον
        # (πριν χάνονταν αόρατα ως κανονικά κενά).
        if failures is not None:
            failures.append(1)
        return None
    if not isinstance(members, list) or not members:
        return None
    m = members[0]
    return m if isinstance(m, dict) else None


def first_cpv(raw: str | None, failures: list | None = None) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        if failures is not None:
            failures.append(1)
        return None
    if not isinstance(items, list) or not items:
        return None
    cpvs = items[0].get("cpvs") if isinstance(items[0], dict) else None
    if not isinstance(cpvs, list) or not cpvs:
        return None
    first = cpvs[0]
    return first.get("key") if isinstance(first, dict) else None


def build(
    months: list[str],
    entities: list[str],
    raw_dir: Path = RAW_DIR,
    processed_dir: Path = PROCESSED_DIR,
    staging_dir: Path = STAGING_DIR,
) -> dict:
    staging_dir.mkdir(parents=True, exist_ok=True)
    resolver_path = processed_dir / "vat_resolver.csv"
    resolver = pd.read_csv(resolver_path, dtype=str).set_index("name")["vat"] \
        if resolver_path.exists() else pd.Series(dtype=object)

    df, months_found, months_missing = load_months(months, entities, raw_dir)
    n_total = len(df)
    if n_total == 0:
        raise SystemExit(f"Κανένα raw parquet βρέθηκε για μήνες {months}, entities {entities}")

    df["_org_vat"] = df["organizationVatNumber"].map(normalize_vat)
    missing_org_vat = df["_org_vat"].isna()
    if missing_org_vat.any():
        df.loc[missing_org_vat, "_org_vat"] = df.loc[missing_org_vat, "organization.value"].map(resolver)

    member_json_failures: list = []
    cpv_json_failures: list = []
    df["_member"] = df[MEMBERS_COL].map(lambda raw: first_member(raw, member_json_failures))
    df["_contractor_vat"] = df["_member"].map(lambda m: normalize_vat(m.get("vatNumber")) if m and isinstance(m.get("vatNumber"), str) else None)
    df["_contractor_name"] = df["_member"].map(lambda m: (m.get("name") or "").strip() if m else None)
    df["_cpv"] = df[CPV_COL].map(lambda raw: first_cpv(raw, cpv_json_failures))

    # #9 (CHECK 2026-07-11): ΙΔΙΟ sanitization με το production pipeline
    # (τιμές > 10 δισ. = γνωστά σκουπίδια πηγής, μηδενίζονται) + ρητό
    # to_numeric -- σε μήνες με ελλιπές σχήμα η στήλη γεμίζει με None (object
    # dtype) και ένα raw agg(sum) θα έσκαγε/παρήγαγε σκουπίδι.
    df["_amount"] = sanitize_value(pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce"))

    # signedDate: το KIMDIS raw περιέχει σποραδικά έτη με λάθος αριθμό ψηφίων
    # (π.χ. "0024-01-28" αντί "2024-01-28") -- pd.to_datetime τα δέχεται σιωπηλά
    # ως έτος 24 μ.Χ. και το neo4j-admin import σκάει σε strftime %Y χωρίς
    # zero-padding στα Windows. Implausible έτη (<1900) μηδενίζονται σε NaT και
    # μετράνε ως parse failure -- ίδιος κανόνας με τα υπόλοιπα σκουπίδια πηγής.
    df["_signed_date"] = pd.to_datetime(df["signedDate"], errors="coerce")
    n_date_failures = int((df["_signed_date"].notna() & (df["_signed_date"].dt.year < 1900)).sum())
    df.loc[df["_signed_date"].dt.year < 1900, "_signed_date"] = pd.NaT

    # #11: οι JSON parse αποτυχίες (members/CPV) μπαίνουν στο ίδιο kill-switch
    # κατώφλι με τις αποτυχίες κανονικοποίησης ΑΦΜ φορέα.
    n_org_vat_failures = int((df["_org_vat"].isna() & df["organizationVatNumber"].notna()).sum())
    n_member_json_failures = len(member_json_failures)
    n_cpv_json_failures = len(cpv_json_failures)
    n_parse_failures = n_org_vat_failures + n_member_json_failures + n_cpv_json_failures + n_date_failures
    parse_failure_pct = n_parse_failures / n_total if n_total else 0.0

    awards = df[df["_org_vat"].notna() & df["referenceNumber"].notna()].copy()
    awards = awards.drop_duplicates(subset=["referenceNumber"])

    # organizations.csv
    orgs = (
        df[df["_org_vat"].notna()][["_org_vat", "organization.value"]]
        .drop_duplicates(subset=["_org_vat"])
        .rename(columns={"_org_vat": "vat:ID(Organization)", "organization.value": "name"})
    )
    orgs.to_csv(staging_dir / "organizations.csv", index=False, encoding="utf-8-sig")

    # contractors.csv
    contractors = (
        df[df["_contractor_vat"].notna()][["_contractor_vat", "_contractor_name"]]
        .drop_duplicates(subset=["_contractor_vat"])
        .rename(columns={"_contractor_vat": "vat:ID(Contractor)", "_contractor_name": "name"})
    )
    contractors.to_csv(staging_dir / "contractors.csv", index=False, encoding="utf-8-sig")

    # cpv.csv / nuts.csv (μόνο κωδικός -- η αξία/όνομα δεν είναι πάντα διαθέσιμη στο first-cpv extract)
    cpv_codes = df["_cpv"].dropna().drop_duplicates()
    pd.DataFrame({"code:ID(CPV)": cpv_codes}).to_csv(staging_dir / "cpv.csv", index=False, encoding="utf-8-sig")

    nuts = df[df["nutsCode.key"].notna()][["nutsCode.key", "nutsCode.value"]].drop_duplicates(subset=["nutsCode.key"])
    nuts = nuts.rename(columns={"nutsCode.key": "code:ID(Nuts)", "nutsCode.value": "name"})
    nuts.to_csv(staging_dir / "nuts.csv", index=False, encoding="utf-8-sig")

    # awards.csv -- ποσό ΜΟΝΟ member_index 0 (συνέπεια HHI v1).
    # #20: amount_ex_vat (η τιμή είναι ΧΩΡΙΣ ΦΠΑ -- το παλιό όνομα amount_vat
    # θα παραπλανούσε τα Cypher queries της Φάσης 2.1).
    # #12: ρητό mapping του cancelled -- string εκδοχές ("false") από μήνες με
    # ανομοιογενές σχήμα (F1 pattern) θα γίνονταν True με σκέτο astype(bool).
    cancelled_bool = (
        awards["cancelled"]
        .map({True: True, False: False, "true": True, "false": False, "True": True, "False": False})
        .fillna(False)
        .astype(bool)
    )
    awards_out = pd.DataFrame({
        "adam:ID(Award)": awards["referenceNumber"],
        "amount_ex_vat:double": awards["_amount"],
        "date:date": awards["_signed_date"].dt.strftime("%Y-%m-%d"),
        "cancelled:boolean": cancelled_bool,
    })
    awards_out.to_csv(staging_dir / "awards.csv", index=False, encoding="utf-8-sig")

    # relationships (neo4j-admin bulk import format)
    rel_issued = pd.DataFrame({
        ":START_ID(Organization)": awards["_org_vat"],
        ":END_ID(Award)": awards["referenceNumber"],
    })
    rel_issued.to_csv(staging_dir / "rel_issued.csv", index=False, encoding="utf-8-sig")

    won_by = awards[awards["_contractor_vat"].notna()]
    rel_won_by = pd.DataFrame({
        ":START_ID(Award)": won_by["referenceNumber"],
        ":END_ID(Contractor)": won_by["_contractor_vat"],
    })
    rel_won_by.to_csv(staging_dir / "rel_won_by.csv", index=False, encoding="utf-8-sig")

    classified = awards[awards["_cpv"].notna()]
    rel_classified_as = pd.DataFrame({
        ":START_ID(Award)": classified["referenceNumber"],
        ":END_ID(CPV)": classified["_cpv"],
    })
    rel_classified_as.to_csv(staging_dir / "rel_classified_as.csv", index=False, encoding="utf-8-sig")

    located = awards[awards["nutsCode.key"].notna()]
    rel_located_in = pd.DataFrame({
        ":START_ID(Award)": located["referenceNumber"],
        ":END_ID(Nuts)": located["nutsCode.key"],
    })
    rel_located_in.to_csv(staging_dir / "rel_located_in.csv", index=False, encoding="utf-8-sig")

    # AWARDS_TO -- aggregate edge οργανισμού->αναδόχου, χρήσιμο για graph analytics.
    # #9: το άθροισμα γίνεται πάνω στο sanitized _amount, όχι στο raw column.
    agg = (
        won_by.groupby(["_org_vat", "_contractor_vat"])
        .agg(n_awards=("referenceNumber", "count"), total_amount_ex_vat=("_amount", "sum"))
        .reset_index()
    )
    rel_awards_to = pd.DataFrame({
        ":START_ID(Organization)": agg["_org_vat"],
        ":END_ID(Contractor)": agg["_contractor_vat"],
        "n_awards:long": agg["n_awards"],
        "total_amount_ex_vat:double": agg["total_amount_ex_vat"],
    })
    rel_awards_to.to_csv(staging_dir / "rel_awards_to.csv", index=False, encoding="utf-8-sig")

    qa_report = {
        "months": months,
        "months_found": months_found,
        "months_missing": months_missing,
        "entities": entities,
        "n_source_rows": n_total,
        "n_awards": len(awards_out),
        "n_organizations": len(orgs),
        "n_contractors": len(contractors),
        "n_cpv": len(cpv_codes),
        "n_nuts": len(nuts),
        "n_cancelled": int(awards_out["cancelled:boolean"].sum()),
        "n_org_vat_failures": n_org_vat_failures,
        "n_member_json_failures": n_member_json_failures,
        "n_cpv_json_failures": n_cpv_json_failures,
        "n_date_failures": n_date_failures,
        "parse_failure_pct": round(100 * parse_failure_pct, 4),
        "pct_org_vat_resolved": round(100 * df["_org_vat"].notna().mean(), 2) if n_total else 0.0,
        "pct_contractor_vat_resolved": round(100 * df["_contractor_vat"].notna().mean(), 2) if n_total else 0.0,
        "pct_cpv_present": round(100 * df["_cpv"].notna().mean(), 2) if n_total else 0.0,
    }
    with open(staging_dir / "qa_report.json", "w", encoding="utf-8") as fh:
        json.dump(qa_report, fh, ensure_ascii=False, indent=2)

    if parse_failure_pct > PARSE_FAILURE_THRESHOLD:
        raise SystemExit(
            f"ΑΠΟΤΥΧΙΑ: parse_failure_pct={parse_failure_pct:.4%} > {PARSE_FAILURE_THRESHOLD:.2%} -- βλ. qa_report.json"
        )
    return qa_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", nargs="+", required=True, help="YYYY-MM (π.χ. 2024-01 2024-02 2024-03)")
    parser.add_argument("--entities", nargs="+", default=["auction"])
    args = parser.parse_args()
    report = build(args.months, args.entities)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
