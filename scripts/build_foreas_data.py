"""Sprint B (πρώην UI_UX_PLAN.md §3.3 — βλ. πλέον docs/UI_UX_SPEC_FINAL.md):
δεδομένα για τις σελίδες προφίλ
φορέα `/foreas/<vat>/`.

Δύο ξεχωριστά κομμάτια δουλειάς:
  1. "Γεγονότα" ανά φορέα (επισκόπηση δαπανών, top CPV, top ανάδοχοι,
     πρόσφατες αναθέσεις) -- ένα πέρασμα πάνω στο **auction** entity μόνο.
     Το auction ΕΙΝΑΙ η ανάθεση: κάθε εγγραφή έχει ήδη το
     contractingDataDetails.contractingMembersDataList (τον ανάδοχο) --
     δεν χρειάζεται ξεχωριστό join με το contract entity (βλ. audit §8,
     docs/MEMORY.md session 6).
  2. Ομάδες σύγκρισης + κατανομές + percentile ανά δείκτη -- πάνω στα ήδη
     υπολογισμένα indicator_*.csv (γρήγορο, δεν ξαναδιαβάζει raw).

Έξοδος (ΕΝΑ αρχείο ανά προϊόν, όχι χιλιάδες μικρά -- απόφαση πρώην
UI_UX_PLAN.md §5, βλ. docs/UI_UX_SPEC_FINAL.md):
  site/src/data/foreas_pages.json   (καταναλώνεται ΜΟΝΟ στο Astro build,
                                      ΔΕΝ πάει στο public/ -- δεν είναι
                                      downloadable artifact)
  site/src/data/distributions.json  (ίδιο)
  site/public/data/entities-index.json  (μικρό, για το μελλοντικό global
                                          search island)

Χρήση:
    python scripts/build_foreas_data.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from kimdis_data import (
    PROCESSED_DIR,
    RAW_DIR,
    VALUE_SANITY_CAP,
    build_vat_resolver,
    load_vat_resolver,
    normalize_vat,
    resolve_vat,
    sanitize_value,
)

SITE_DIR = PROCESSED_DIR.parent.parent / "site"
BUILD_DATA_DIR = SITE_DIR / "src" / "data"   # build-time only, ΟΧΙ deployed
PUBLIC_DATA_DIR = SITE_DIR / "public" / "data"
REPLIES_DIR = PROCESSED_DIR.parent.parent / "replies"  # P2-13, tracked, βλ. replies/README.md
GRAPH_DIR = PROCESSED_DIR.parent / "graph_staging" / "gds"  # P2-11/P2-17, gitignored, offline/χειροκίνητο

TOP_N_CPV = 10
TOP_N_CONTRACTORS = 10
RECENT_N = 50
N_HIST_BINS = 12

# Ομάδες σύγκρισης (§7 audit): classification είναι πιο αξιόπιστο από
# org_type (44% κενό εκεί). 5 χοντρές ομάδες -- λεπτομερέστερο σχήμα
# άφηνε 11/29 υποομάδες με <20 μέλη.
GROUP_MAP = {
    "ΟΤΑ": "ΟΤΑ",
    "Κεντρική Κυβέρνηση": "Κεντρική Διοίκηση",
    "Εκτός Γενικής Κυβέρνησης": "Εκτός Γενικής Κυβέρνησης",
    "ΟΚΑ": "Λοιπή Γενική Κυβέρνηση",
}
MIN_GROUP_FOR_TERCILES = 60  # κάτω από αυτό, η ομάδα δεν σπάει σε terciles μεγέθους
MIN_GROUP_YEAR_FOR_PERCENTILE = 20  # κάτω από αυτό, δεν εμφανίζεται percentile (§7)


# --------------------------------------------------------------------------
# Μέρος 1: ομάδες σύγκρισης, κατανομές, percentiles (πάνω στα indicator CSV)
# --------------------------------------------------------------------------

def read_csv_or_empty(name: str, **kw) -> pd.DataFrame:
    path = PROCESSED_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"organization_vat": str, "vat": str}, **kw)


def assign_groups(entities: pd.DataFrame, da: pd.DataFrame) -> pd.Series:
    """Επιστρέφει vat -> group_label (classification-bucket [x size-tercile])."""
    base_group = entities.set_index("vat")["classification"].map(GROUP_MAP).fillna("Άγνωστο")
    size = da.groupby("organization_vat")["value_total"].sum()
    group_counts = base_group.value_counts()

    # terciles μόνο μέσα σε ομάδες αρκετά μεγάλες· αλλιώς η χοντρή ομάδα μένει ως έχει
    out = base_group.copy()
    for grp, count in group_counts.items():
        if count < MIN_GROUP_FOR_TERCILES:
            continue
        members = base_group[base_group == grp].index
        sizes = size.reindex(members).dropna()
        if len(sizes) < MIN_GROUP_FOR_TERCILES:
            continue
        terciles = pd.qcut(sizes.rank(method="first"), 3, labels=["S", "M", "L"])
        out.loc[terciles.index] = grp + "-" + terciles.astype(str)
    return out


def histogram(values: pd.Series, n_bins: int = N_HIST_BINS) -> dict:
    vals = values.dropna().to_numpy(dtype=float)
    if len(vals) == 0:
        return {"edges": [], "counts": [], "median": None, "n": 0}
    lo, hi = float(vals.min()), float(vals.max())
    if lo == hi:
        hi = lo + 1.0
    counts, edges = np.histogram(vals, bins=n_bins, range=(lo, hi))
    return {
        "edges": [round(float(e), 4) for e in edges],
        "counts": [int(c) for c in counts],
        "median": round(float(np.median(vals)), 4),
        "n": len(vals),
    }


def percentiles_within_group(values: pd.Series) -> pd.Series:
    """L9 (review.md): επιστρέφει, για κάθε τιμή της στήλης, το ποσοστό
    (0-100) των τιμών της ΙΔΙΑΣ ομάδας <= αυτής. Πριν, το `percentile_of`
    ξανάκανε `dropna().to_numpy()` για ΚΑΘΕ γραμμή μέσα σε iterrows -- O(n^2)
    ανά (year, group). Εδώ το sort γίνεται μία φορά ανά ομάδα και το ranking
    κάθε τιμής βγαίνει με `searchsorted` (O(n log n) συνολικά)."""
    vals = values.to_numpy(dtype=float)
    valid = ~np.isnan(vals)
    n = int(valid.sum())
    result = np.full(len(vals), np.nan)
    if n >= MIN_GROUP_YEAR_FOR_PERCENTILE:
        sorted_vals = np.sort(vals[valid])
        ranks = np.searchsorted(sorted_vals, vals[valid], side="right")
        result[valid] = np.round(100.0 * ranks / n, 1)
    return pd.Series(result, index=values.index)


def build_groups_distributions_percentiles(
    entities: pd.DataFrame,
    da: pd.DataFrame,
    hhi: pd.DataFrame,
    sb: pd.DataFrame,
    dr: pd.DataFrame,
    dl: pd.DataFrame,
    comp: pd.DataFrame,
):
    group_of = assign_groups(entities, da)

    distributions: dict[str, dict] = {}
    percentiles: dict[str, dict] = {}  # vat -> {indicator: {year: pct}}

    specs = [
        ("da", da, "organization_vat", "da_count_pct"),
        ("hhi", hhi, "organization_vat", "hhi"),
        ("single_bid", sb, "organization_vat", "single_bid_pct"),
        ("discount", dr, "organization_vat", "median_discount_pct"),
        ("deadline", dl, "vat", "median_deadline_days"),
        ("composite", comp, "vat", "composite_score"),
    ]
    for key, df, vat_col, value_col in specs:
        if df.empty or value_col not in df.columns:
            continue
        d = df.copy()
        d["group"] = d[vat_col].map(group_of)
        for (year, grp), g in d.groupby(["year", "group"]):
            dist_key = f"{key}|{grp}|{year}"
            distributions[dist_key] = histogram(g[value_col])
        for (year, grp), g in d.groupby(["year", "group"]):
            pcts = percentiles_within_group(g[value_col])
            for vat, pct in zip(g[vat_col], pcts):
                pct = None if pd.isna(pct) else float(pct)
                percentiles.setdefault(vat, {}).setdefault(key, {})[str(int(year))] = pct

    return group_of, distributions, percentiles


# --------------------------------------------------------------------------
# Μέρος 2: γεγονότα ανά φορέα από το auction (μία εγγραφή = μία ανάθεση)
# --------------------------------------------------------------------------

AUCTION_COLS = [
    "referenceNumber", "organization.value", "organizationVatNumber", "title",
    "procedureType.key", "procedureType.value", "totalCostWithVAT",
    "totalCostWithoutVAT", "submissionDate", "objectDetailsList",
    "contractingDataDetails.contractingMembersDataList",
]
DIRECT_AWARD_KEY = "6"


def _first_cpv(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    try:
        details = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not details:
        return None
    cpvs = details[0].get("cpvs") or []
    if not cpvs:
        return None
    c = cpvs[0]
    return c.get("key"), c.get("value")


def _first_contractor(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    try:
        members = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not members:
        return None
    m = members[0]
    return m.get("vatNumber"), m.get("name")


# Οι δύο ογκώδεις raw JSON στήλες που ΔΕΝ επιτρέπεται να συνυπάρξουν με
# ολόκληρο το ιστορικό στη μνήμη (#21, CHECK 2026-07-11): μαζί ~1,77 GB
# deep memory σε 1,76M γραμμές· μαζί με τα parsing intermediates ανέβαζαν
# το peak στα 9,78 GB (μετρημένο) -- πάνω από τα 7 GB του CI runner.
HEAVY_JSON_COLS = ["objectDetailsList", "contractingDataDetails.contractingMembersDataList"]


# L8 (review.md): build_foreas_data.py, build_anadoxoi_data.py και
# build_anadoxos_profiles.py καλούν όλα load_auctions_slim() στο ΙΔΙΟ nightly
# Pipeline step -- χωρίς cache, ξαναδιαβάζεται 3x το ίδιο ~1,7M-γραμμών
# ιστορικό (76 parquet, JSON parsing ανά αρχείο). Το cache ζει ΕΚΤΟΣ
# data/raw|processed|graph ώστε να ΜΗΝ synced στο R2 (ίδιος λόγος με L4/L5 --
# είναι καθαρά ενδο-run artifact, όχι δεδομένο προς διανομή/διατήρηση).
AUCTIONS_SLIM_CACHE = Path("data/_cache/auctions_slim.parquet")


def load_auctions_slim(raw_dir=RAW_DIR, cache_path: Path | None = AUCTIONS_SLIM_CACHE) -> pd.DataFrame:
    """Φορτώνει το auction ιστορικό ΑΝΑ αρχείο, εξάγει αμέσως τις slim στήλες
    (cpv_code/cpv_label/contractor_vat/contractor_name -- strings, όχι tuples)
    και πετάει τις raw JSON στήλες πριν το concat.

    Ίδιο schema-tolerant pattern με το kimdis_data.load_entity (footer-only
    schema read, συμπλήρωση απόντων στηλών με None, _source_year/_source_month
    από το filename, dedupe referenceNumber στο τέλος), αλλά το concat γίνεται
    πάνω στο slim σχήμα: peak μνήμης ~= 1 μήνας raw + slim ιστορικό, αντί για
    raw JSON x όλο το ιστορικό (#21).

    cache_path (L8): αν δίνεται και το parquet υπάρχει με mtime >= όλων των
    raw auction αρχείων, διαβάζεται απευθείας αντί να ξαναϋπολογιστεί -- έτσι
    οι επόμενες κλήσεις μέσα στο ίδιο nightly run (build_anadoxoi_data.py,
    build_anadoxos_profiles.py) δεν ξαναπερνούν όλο το raw ιστορικό. Ένα
    φρέσκο backfill (νεότερα raw αρχεία) ακυρώνει αυτόματα το cache.
    """
    files = sorted(raw_dir.glob("auction_*.parquet"))
    if not files:
        return pd.DataFrame()

    if cache_path is not None and cache_path.exists():
        cache_mtime = cache_path.stat().st_mtime
        if all(f.stat().st_mtime <= cache_mtime for f in files):
            return pd.read_parquet(cache_path)

    frames = []
    for f in files:
        available = set(pq.ParquetFile(f).schema_arrow.names)
        present = [c for c in AUCTION_COLS if c in available]
        df = pd.read_parquet(f, columns=present)
        for missing_col in AUCTION_COLS:
            if missing_col not in df.columns:
                df[missing_col] = None
        _, year, month = f.stem.split("_")
        df["_source_year"] = int(year)
        df["_source_month"] = int(month)

        cpv = df["objectDetailsList"].map(_first_cpv)
        df["cpv_code"] = cpv.map(lambda c: c[0] if c else None)
        df["cpv_label"] = cpv.map(lambda c: c[1] if c else None)
        contractor = df["contractingDataDetails.contractingMembersDataList"].map(_first_contractor)
        df["contractor_vat"] = contractor.map(lambda c: c[0] if c else None)
        df["contractor_name"] = contractor.map(lambda c: c[1] if c else None)
        df = df.drop(columns=HEAVY_JSON_COLS)
        frames.append(df)
    result = pd.concat(frames, ignore_index=True)
    # A4/P6 dedupe -- πανομοιότυπη λογική με το load_entity (F4: μόνο μη-κενά
    # referenceNumber, τα NaN δεν θεωρούνται ίσα μεταξύ τους).
    ref = result["referenceNumber"]
    dup_mask = ref.notna() & ref.duplicated(keep="last")
    if dup_mask.any():
        result = result[~dup_mask]
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(cache_path, index=False)
    return result


def build_foreas_facts(auctions: pd.DataFrame, resolver: pd.Series) -> dict:
    """Δέχεται το slim auction frame του load_auctions_slim (cpv_code/cpv_label/
    contractor_vat/contractor_name ήδη υπολογισμένα -- #21)."""
    df = auctions
    df["vat"] = resolve_vat(df, resolver)
    df = df.dropna(subset=["vat"])

    df["value"] = sanitize_value(pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce").fillna(
        pd.to_numeric(df["totalCostWithVAT"], errors="coerce")
    ))
    df["is_direct"] = df["procedureType.key"].astype(str) == DIRECT_AWARD_KEY
    df["submission_date"] = pd.to_datetime(df["submissionDate"], errors="coerce")

    pages: dict[str, dict] = {}
    for vat, g in df.groupby("vat"):
        name = g["organization.value"].mode(dropna=True)
        name = name.iloc[0] if not name.empty else None

        overview = {}
        for year, yg in g.groupby("_source_year"):
            n_total = len(yg)
            n_direct = int(yg["is_direct"].sum())
            value_total = float(yg["value"].sum(skipna=True))
            n_unique_contractors = int(yg["contractor_vat"].nunique(dropna=True))
            overview[str(int(year))] = {
                "n_total": n_total,
                "n_direct": n_direct,
                "da_pct": round(100.0 * n_direct / n_total, 1) if n_total else None,
                "value_total": round(value_total, 2),
                "n_unique_contractors": n_unique_contractors,
            }

        cpv_rows = g.dropna(subset=["cpv_code"])
        top_cpv = []
        if not cpv_rows.empty:
            cpv_agg = (
                cpv_rows.groupby(["cpv_code", "cpv_label"])["value"].sum()
                .sort_values(ascending=False)
                .head(TOP_N_CPV)
            )
            top_cpv = [
                {"code": code, "label": label, "value": round(float(v), 2)}
                for (code, label), v in cpv_agg.items()
            ]

        contractor_rows = g.dropna(subset=["contractor_vat"])
        top_contractors = []
        if not contractor_rows.empty:
            total_val = contractor_rows["value"].sum()
            contractor_agg = (
                contractor_rows.groupby(["contractor_vat", "contractor_name"])["value"].sum()
                .sort_values(ascending=False)
                .head(TOP_N_CONTRACTORS)
            )
            top_contractors = [
                {
                    "vat": cvat,
                    "name": cname,
                    "value": round(float(v), 2),
                    "share": round(100.0 * float(v) / total_val, 1) if total_val else None,
                }
                for (cvat, cname), v in contractor_agg.items()
            ]

        recent = (
            g.dropna(subset=["submission_date"])
            .sort_values("submission_date", ascending=False)
            .head(RECENT_N)
        )
        # L3 (review.md): coerce μία φορά πριν το iterrows -- raw KIMDIS στήλη
        # έχει αποδεδειγμένα string junk σε άλλα πεδία, ένα μελλοντικό μήνα
        # θα έριχνε το nightly με ValueError σε γυμνό float().
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
                "contractor_name": row["contractor_name"],
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
            "overview": overview,
            "top_cpv": top_cpv,
            "top_contractors": top_contractors,
            "recent": recent_list,
        }
    return pages


def attach_indicators(
    pages: dict,
    da: pd.DataFrame,
    hhi: pd.DataFrame,
    sb: pd.DataFrame,
    dr: pd.DataFrame,
    dl: pd.DataFrame,
    comp: pd.DataFrame,
    benford: pd.DataFrame,
    percentiles: dict,
    group_of: pd.Series,
    entities: pd.DataFrame,
) -> None:
    da_by_vat = {vat: g for vat, g in da.groupby("organization_vat")} if not da.empty else {}
    hhi_by_vat = {vat: g for vat, g in hhi.groupby("organization_vat")} if not hhi.empty else {}
    sb_by_vat = {vat: g for vat, g in sb.groupby("organization_vat")} if not sb.empty else {}
    dr_by_vat = {vat: g for vat, g in dr.groupby("organization_vat")} if not dr.empty else {}
    dl_by_vat = {vat: g for vat, g in dl.groupby("vat")} if not dl.empty else {}
    comp_by_vat = {vat: g for vat, g in comp.groupby("vat")} if not comp.empty else {}
    benford_by_vat = {vat: g for vat, g in benford.groupby("vat")} if not benford.empty else {}
    ent_by_vat = entities.set_index("vat") if not entities.empty else pd.DataFrame()

    for vat, page in pages.items():
        page["group_label"] = group_of.get(vat)
        if not ent_by_vat.empty and vat in ent_by_vat.index:
            row = ent_by_vat.loc[vat]
            page["org_type"] = row.get("org_type")
            page["classification"] = row.get("classification")
            page["nuts_city"] = row.get("nuts_city")

        indicators: dict = {}
        if vat in da_by_vat:
            indicators["da"] = {
                str(int(r["year"])): {"value": r["da_count_pct"], "n": int(r["n_total"])}
                for _, r in da_by_vat[vat].iterrows()
            }
        if vat in hhi_by_vat:
            indicators["hhi"] = {
                str(int(r["year"])): {"value": r["hhi"], "n": int(r["n_contracts"]), "top1_share": r["top1_share"]}
                for _, r in hhi_by_vat[vat].iterrows()
            }
        if vat in sb_by_vat:
            indicators["single_bid"] = {
                str(int(r["year"])): {
                    "value": r["single_bid_pct"],
                    "n": int(r["n_with_bids"]),
                    "coverage_pct": r["coverage_pct"],
                    "n_bids_outliers": int(r["n_bids_outliers"]),
                    "insufficient_data": pd.isna(r["single_bid_pct"]),
                }
                for _, r in sb_by_vat[vat].iterrows()
            }
        if vat in dr_by_vat:
            def dr_row(r):
                n_total_row = da_by_vat.get(vat)
                n_total = None
                if n_total_row is not None:
                    match = n_total_row[n_total_row["year"] == r["year"]]
                    if not match.empty:
                        n_total = int(match.iloc[0]["n_total"])
                coverage = round(100.0 * r["n_linked"] / n_total, 1) if n_total else None
                return {
                    "value": r["median_discount_pct"],
                    "n": int(r["n_linked"]),
                    "coverage_pct": coverage,
                    "insufficient_coverage": coverage is None or coverage < 20.0,
                }
            indicators["discount"] = {str(int(r["year"])): dr_row(r) for _, r in dr_by_vat[vat].iterrows()}
        if vat in dl_by_vat:
            indicators["deadline"] = {
                str(int(r["year"])): {
                    "value": r["median_deadline_days"],
                    "n": int(r["n_notices"]),
                    "coverage_pct": r["coverage_pct"],
                    "insufficient_data": pd.isna(r["median_deadline_days"]),
                }
                for _, r in dl_by_vat[vat].iterrows()
            }
        if vat in comp_by_vat:
            indicators["composite"] = {
                str(int(r["year"])): {"value": r["composite_score"], "n": int(r["n_flags"])}
                for _, r in comp_by_vat[vat].iterrows()
            }
        if vat in benford_by_vat:
            # period="all" καλύπτει φορείς που δεν πιάνουν N=300 σε κανένα
            # μεμονωμένο έτος -- fallback για την κάρτα, βλ. SPRINT_E_PLAN §E6.
            indicators["benford"] = {
                str(r["period"]): {
                    "value": r["mad_d1"],
                    "n": int(r["n_amounts"]),
                    "band": r["nigrini_band_d1"],
                    "mad_d2": r["mad_d2"],
                    "band_d2": r["nigrini_band_d2"],
                    "coverage_pct": r["coverage_pct"],
                    "insufficient_data": pd.isna(r["mad_d1"]),
                }
                for _, r in benford_by_vat[vat].iterrows()
            }

        page["indicators"] = indicators
        page["percentiles"] = percentiles.get(vat, {})


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def sanitize(obj):
    """Αναδρομικά μετατρέπει NaN/NaT/pd.NA -> None και numpy scalars -> Python
    native, ώστε το json.dumps(allow_nan=False) να παράγει πάντα έγκυρο JSON
    (το Python json module γράφει από default το μη-έγκυρο literal "NaN")."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, np.generic):
        obj = obj.item()
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


def _validate_reply_schema(path: Path, reply_data: object) -> list[dict]:
    """M4 (review.md): ελάχιστο schema check -- βλ. replies/README.md/_TEMPLATE.json.
    `vat` string, `replies` λίστα από dicts με `date`/`text` strings. Ρίχνει
    ValueError με ρητό μήνυμα αντί να αφήσει σκουπίδια να περάσουν σιωπηλά
    στο δημοσιευμένο JSON/Astro template."""
    if not isinstance(reply_data, dict):
        raise ValueError(f"{path.name}: το top-level JSON πρέπει να είναι object, όχι {type(reply_data).__name__}")
    if not isinstance(reply_data.get("vat"), str):
        raise ValueError(f"{path.name}: το πεδίο 'vat' λείπει ή δεν είναι string")
    replies = reply_data.get("replies", [])
    if not isinstance(replies, list):
        raise ValueError(f"{path.name}: το πεδίο 'replies' πρέπει να είναι λίστα, όχι {type(replies).__name__}")
    for i, entry in enumerate(replies):
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name}: replies[{i}] πρέπει να είναι object, όχι {type(entry).__name__}")
        if not isinstance(entry.get("date"), str):
            raise ValueError(f"{path.name}: replies[{i}].date λείπει ή δεν είναι string")
        if not isinstance(entry.get("text"), str):
            raise ValueError(f"{path.name}: replies[{i}].text λείπει ή δεν είναι string")
    return replies


def attach_replies(pages: dict, replies_dir: Path = REPLIES_DIR) -> None:
    """P2-13: ενσωματώνει replies/<ΑΦΜ>.json (χειροκίνητα επιμελημένο, tracked
    -- βλ. replies/README.md) στο αντίστοιχο page. ΔΕΝ σιωπά αν ένα reply
    αναφέρεται σε ΑΦΜ που δεν υπάρχει στα pages -- warning, ώστε να μην χαθεί
    αθόρυβα μια απάντηση φορέα.

    M4 (review.md): ένα κακοσχηματισμένο αρχείο (JSON parse error ή λάθος
    schema) ρίχνει ρητό SystemExit αντί να ρίξει άσχετο traceback (ή
    χειρότερα, να περάσει σιωπηλά σκουπίδια στο δημοσιευμένο JSON) -- το
    αρχείο είναι tracked, ο συντάκτης πρέπει να μάθει το λάθος στο commit."""
    if not replies_dir.exists():
        return
    for path in sorted(replies_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            reply_data = json.loads(path.read_text(encoding="utf-8"))
            replies = _validate_reply_schema(path, reply_data)
        except (json.JSONDecodeError, ValueError) as e:
            raise SystemExit(f"ΣΦΑΛΜΑ: κακοσχηματισμένο replies/{path.name} -- {e}") from e
        vat = normalize_vat(reply_data.get("vat") or path.stem)
        if vat not in pages:
            print(f"ΠΡΟΣΟΧΗ: replies/{path.name} αναφέρεται σε ΑΦΜ {vat} που δεν υπάρχει στα foreas_pages -- αγνοείται.")
            continue
        pages[vat]["replies"] = replies


def attach_network(pages: dict, graph_dir: Path = GRAPH_DIR) -> None:
    """P2-11/P2-17: κάρτα «Δίκτυο» -- top-30 ego-network γείτονες + new-winner-rate
    ανά φορέα, από τα offline exports του scripts/graph/queries.py (P2-08..11).

    Ο γράφος είναι χειροκίνητος/offline (ΔΕΝ ανανεώνεται από το nightly, βλ.
    docs/PHASE_2.md R-06) -- αν τα αρχεία λείπουν (π.χ. CI χωρίς Neo4j τοπικά),
    ΔΕΝ σκάει το build· απλά κανένα page δεν παίρνει πεδίο "network" και η
    κάρτα «Δίκτυο» δεν εμφανίζεται (βλ. P2-12 blocking gate -- η κάρτα ούτως ή
    άλλως δεν έπρεπε να υπάρχει αν δεν υπάρχουν δεδομένα)."""
    ego_path = graph_dir / "ego_networks.json"
    nwr_path = graph_dir / "graph_features_org.csv"
    # L7 (review.md): τα δύο exports χειρίζονται ανεξάρτητα -- αν λείπει το ένα
    # (π.χ. μερικό R2 pull) δεν πρέπει να χαθεί σιωπηλά και το άλλο.
    if not ego_path.exists() and not nwr_path.exists():
        return
    snapshot_date = None
    networks: dict = {}
    if ego_path.exists():
        ego = json.loads(ego_path.read_text(encoding="utf-8"))
        snapshot_date = ego.get("snapshot_date")
        networks = ego.get("networks", {})

    nwr_by_org: dict[str, dict] = {}
    if nwr_path.exists():
        nwr = pd.read_csv(nwr_path, dtype={"org_vat": str})
        for vat, g in nwr.groupby("org_vat"):
            nwr_by_org[vat] = {
                str(int(r["year"])): {
                    "value": round(100.0 * r["new_winner_rate"], 1),
                    "n": int(r["n_contractors"]),
                    # M3 (review.md): πρώτο ενεργό έτος του φορέα -- κάθε
                    # ανάδοχος είναι τετριμμένα «νέος» (100%), βλ.
                    # queries.py::query_c_new_winner_rate.
                    "left_censored": bool(r.get("left_censored", False)),
                }
                for _, r in g.iterrows()
            }

    for vat, page in pages.items():
        neighbors = networks.get(vat)
        if neighbors is None and vat not in nwr_by_org:
            continue
        page["network"] = {
            "snapshot_date": snapshot_date,
            "top_neighbors": neighbors or [],
            "new_winner_rate": nwr_by_org.get(vat, {}),
        }


def main() -> None:
    BUILD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)

    entities = read_csv_or_empty("entities.csv")
    da = read_csv_or_empty("indicator_direct_award.csv")
    hhi = read_csv_or_empty("indicator_hhi.csv")
    sb = read_csv_or_empty("indicator_single_bid.csv")
    dr = read_csv_or_empty("indicator_discount_rate.csv")
    dl = read_csv_or_empty("indicator_deadlines.csv")
    comp = read_csv_or_empty("indicator_composite.csv")
    benford = read_csv_or_empty("indicator_benford.csv")

    if da.empty or entities.empty:
        print("Λείπουν data/processed/entities.csv ή indicator_direct_award.csv -- τρέξε πρώτα "
              "build_entity_table.py και compute_indicators_v1.py.")
        return

    group_of, distributions, percentiles = build_groups_distributions_percentiles(entities, da, hhi, sb, dr, dl, comp)

    # #21 (CHECK 2026-07-11): per-file slim loading αντί για ενιαίο
    # load_entity -- οι raw JSON στήλες δεν συνυπάρχουν ποτέ με ολόκληρο
    # το ιστορικό στη μνήμη (μετρημένο peak 9,78 GB -> στόχος <4 GB, ώστε
    # να χωράει στα 7 GB του ubuntu-latest nightly runner).
    auctions = load_auctions_slim()
    if auctions.empty:
        print("Δεν βρέθηκαν δεδομένα auction σε data/raw/.")
        return

    # A3: προτιμάται ο persisted resolver (build_entity_table.py, χτισμένος από
    # auction+contract+notice) -- πριν τη διόρθωση αυτό το script έχτιζε δικό
    # του resolver μόνο από auctions (~25% ΑΦΜ fill), με αποτέλεσμα οι σελίδες
    # προφίλ να κλειδώνονται σε διαφορετικά ΑΦΜ από τα indicator CSV.
    resolver = load_vat_resolver()
    if resolver is None:
        print("(Δεν βρέθηκε data/processed/vat_resolver.csv -- τρέξε πρώτα build_entity_table.py. "
              "Χτίζεται προσωρινός resolver μόνο από auctions -- λιγότερο πλήρης.)")
        resolver = build_vat_resolver([auctions])
    pages = build_foreas_facts(auctions, resolver)
    attach_indicators(pages, da, hhi, sb, dr, dl, comp, benford, percentiles, group_of, entities)
    attach_replies(pages)
    attach_network(pages)

    pages = sanitize(pages)
    distributions = sanitize(distributions)

    out_pages = BUILD_DATA_DIR / "foreas_pages.json"
    out_pages.write_text(json.dumps(pages, ensure_ascii=False, indent=None, allow_nan=False), encoding="utf-8")
    print(f"Προφίλ φορέα -> {out_pages} ({len(pages)} ΑΦΜ)")

    out_dist = BUILD_DATA_DIR / "distributions.json"
    out_dist.write_text(json.dumps(distributions, ensure_ascii=False, indent=None, allow_nan=False), encoding="utf-8")
    print(f"Κατανομές -> {out_dist} ({len(distributions)} ομάδες/δείκτες/έτη)")

    index_rows = [
        {"vat": vat, "name": page.get("name"), "type": page.get("org_type") or page.get("classification")}
        for vat, page in pages.items()
        if page.get("name")
    ]
    out_index = PUBLIC_DATA_DIR / "entities-index.json"
    out_index.write_text(json.dumps(index_rows, ensure_ascii=False, indent=None, allow_nan=False), encoding="utf-8")
    print(f"Search index -> {out_index} ({len(index_rows)} εγγραφές)")

    meta = {"generated_at": datetime.now(timezone.utc).isoformat(), "n_pages": len(pages)}
    (BUILD_DATA_DIR / "foreas_meta.json").write_text(json.dumps(meta), encoding="utf-8")


if __name__ == "__main__":
    main()
