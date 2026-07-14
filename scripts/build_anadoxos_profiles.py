"""P2-16: προφίλ αναδόχου `/anadoxos/<vat>/` -- ΜΟΝΟ top-N (βλ. docs/PHASE_2.md
γραμμή 62 / R-01): 100-250k ανάδοχοι σπάνε το όριο 20k αρχείων του Cloudflare
Pages, γι' αυτό pre-rendered σελίδα παίρνουν μόνο οι top-N (target <=5.000,
κριτήριο value_total -- το εναλλακτικό κριτήριο του πλάνου, "παρουσία σε graph
features", δεν είναι ακόμα διαθέσιμο: Queries A-D/P2-08..11 δεν έχουν γραφτεί).
Οι υπόλοιποι ανάδοχοι μένουν index-only entries στο ήδη-shipped /anadoxoi/
(P2-15), χωρίς δικό τους URL.

Ίδιο keying/heuristic με το build_anadoxoi_data.py (P2-15): normalize_vat πριν
το groupby, legal-entity name heuristic (LEGAL_FORM_MARKERS) -- ένας ανάδοχος
που αποκλείεται εκεί ΔΕΝ μπορεί να αποκτήσει προφίλ εδώ (ίδια αρχή
ελαχιστοποίησης δεδομένων).

Έξοδος:
    site/src/data/anadoxos_pages.json     (build-time only, ΔΕΝ πάει στο public/)
    site/public/data/anadoxos-index.json  (μόνο τα top-N ΑΦΜ, ώστε το
                                            /anadoxoi/index.astro να ξέρει
                                            ποια ονόματα να κάνει link)

Χρήση:
    python scripts/build_anadoxos_profiles.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_anadoxoi_data import build_index  # noqa: E402
from build_foreas_data import load_auctions_slim, sanitize  # noqa: E402
from kimdis_data import (  # noqa: E402
    VALUE_SANITY_CAP,
    load_vat_resolver,
    normalize_vat,
    resolve_vat,
    sanitize_value,
)

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
BUILD_DATA_DIR = SITE_DIR / "src" / "data"
PUBLIC_DATA_DIR = SITE_DIR / "public" / "data"

TOP_N = 5000
TOP_N_ORGS = 10
RECENT_N = 50


def build_profiles(auctions: pd.DataFrame, top_vats: set[str], resolver: pd.Series) -> dict:
    df = auctions.copy()
    df["contractor_vat"] = df["contractor_vat"].map(normalize_vat)
    df = df[df["contractor_vat"].isin(top_vats)]

    df["org_vat"] = resolve_vat(df, resolver)
    df["value"] = sanitize_value(pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce").fillna(
        pd.to_numeric(df["totalCostWithVAT"], errors="coerce")
    ))
    df["submission_date"] = pd.to_datetime(df["submissionDate"], errors="coerce")

    pages: dict[str, dict] = {}
    for vat, g in df.groupby("contractor_vat"):
        name = g["contractor_name"].mode(dropna=True)
        name = name.iloc[0] if not name.empty else None

        overview = {}
        for year, yg in g.groupby("_source_year"):
            overview[str(int(year))] = {
                "n_total": len(yg),
                "value_total": round(float(yg["value"].sum(skipna=True)), 2),
                "n_unique_orgs": int(yg["org_vat"].nunique(dropna=True)),
            }

        org_rows = g.dropna(subset=["org_vat"])
        top_orgs = []
        if not org_rows.empty:
            total_val = org_rows["value"].sum()
            org_agg = (
                org_rows.groupby(["org_vat", "organization.value"])["value"].sum()
                .sort_values(ascending=False)
                .head(TOP_N_ORGS)
            )
            top_orgs = [
                {
                    "vat": ovat,
                    "name": oname,
                    "value": round(float(v), 2),
                    "share": round(100.0 * float(v) / total_val, 1) if total_val else None,
                }
                for (ovat, oname), v in org_agg.items()
            ]

        recent = (
            g.dropna(subset=["submission_date"])
            .sort_values("submission_date", ascending=False)
            .head(RECENT_N)
        )
        # L3 (review.md): coerce μία φορά πριν το iterrows, βλ. ίδιο fix στο
        # build_foreas_data.py.
        recent_amount = pd.to_numeric(recent["totalCostWithVAT"], errors="coerce")
        recent_list = [
            {
                "adam": row["referenceNumber"],
                "date": row["submission_date"].date().isoformat(),
                "title": row["title"],
                "amount_with_vat": (
                    round(float(amount), 2) if pd.notna(amount) and amount <= VALUE_SANITY_CAP else None
                ),
                "procedure": row["procedureType.value"],
                "org_vat": row["org_vat"],
                "org_name": row["organization.value"],
            }
            for (_, row), amount in zip(recent.iterrows(), recent_amount)
        ]

        years = sorted(int(y) for y in overview.keys())
        pages[vat] = {
            "vat": vat,
            "name": name,
            "first_year": years[0] if years else None,
            "last_year": years[-1] if years else None,
            "years": years,
            "n_awards": len(g),
            "value_total": round(float(g["value"].sum(skipna=True)), 2),
            "overview": overview,
            "top_orgs": top_orgs,
            "recent": recent_list,
        }
    return pages


def main() -> None:
    BUILD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)

    index = build_index()
    if index.empty:
        raise RuntimeError("Δεν βρέθηκαν δεδομένα -- πιθανή αποτυχία backfill ή αρχεία R2 pull. "
                           "Σταμάτα τη χτίσιμο αντί να δημιουργηθούν ημιτελή δεδομένα.")
    top_vats = set(index["vat"].head(TOP_N))

    auctions = load_auctions_slim()
    if auctions.empty:
        print("Δεν βρέθηκαν δεδομένα auction σε data/raw/.")
        return

    resolver = load_vat_resolver()
    if resolver is None:
        print("(Δεν βρέθηκε data/processed/vat_resolver.csv -- τρέξε πρώτα build_entity_table.py. "
              "Τα ονόματα φορέων-αγοραστών στα top_orgs θα είναι λιγότερο πλήρη.)")
        resolver = pd.Series(dtype=object)

    pages = build_profiles(auctions, top_vats, resolver)
    pages = sanitize(pages)

    out_pages = BUILD_DATA_DIR / "anadoxos_pages.json"
    out_pages.write_text(json.dumps(pages, ensure_ascii=False, indent=None, allow_nan=False), encoding="utf-8")
    print(f"Προφίλ αναδόχου -> {out_pages} ({len(pages)} ΑΦΜ, στόχος top {TOP_N})")

    out_index = PUBLIC_DATA_DIR / "anadoxos-index.json"
    out_index.write_text(json.dumps(sorted(pages.keys()), ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"Profile index -> {out_index} ({len(pages)} ΑΦΜ)")


if __name__ == "__main__":
    main()
