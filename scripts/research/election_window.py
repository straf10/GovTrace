"""P2-18: χρονική ανάλυση απευθείας αναθέσεων γύρω από τις αυτοδιοικητικές
εκλογές 2023 (PRE/LAME/POST vs baseline). Πλάνο: data/processed/docs-internal/
election/PLAN.md (§10 = αναλυτικές τεχνικές οδηγίες ανά βήμα).

Standalone, pandas μόνο (καμία εξάρτηση Neo4j/ΓΕΜΗ/nightly pipeline). Τρέχει
τοπικά, resumable μέσω --step (κάθε βήμα διαβάζει τα persisted ενδιάμεσα των
προηγούμενων βημάτων από OUT_DIR αντί να τα ξαναϋπολογίζει).

Χρήση:
    python scripts/research/election_window.py --step 1
    python scripts/research/election_window.py --step 2
    python scripts/research/election_window.py --step 3   # γράφει ota_mapping.csv, ΣΤΑΜΑΤΑ (human gate)
    python scripts/research/election_window.py --step 4   # χρειάζεται raw εκλογικά ΥΠΕΣ σε OUT_DIR/raw/
    ...
Windows (PowerShell): $env:PYTHONIOENCODING='utf-8'; python scripts/research/election_window.py --step 1
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/

from compute_indicators_v1 import DIRECT_AWARD_KEY  # noqa: E402
from er.splink_poc import normalize_name as _token_normalize  # noqa: E402
from kimdis_data import (  # noqa: E402
    NAME_COL,
    PERMANENT_AUCTION_GAPS,
    VAT_COL,
    load_entity,
    load_vat_resolver,
    resolve_vat,
    sanitize_value,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("election_window")

RAW_DIR = Path("data/raw")
ENTITIES_PATH = Path("data/processed/entities.csv")
OUT_DIR = Path("data/processed/docs-internal/election")
RAW_ELECTION_DIR = OUT_DIR / "raw"
MAPPING_PATH = OUT_DIR / "ota_mapping.csv"
ELECTIONS_PATH = OUT_DIR / "elections.csv"
LAG_AUDIT_PATH = OUT_DIR / "lag_audit.csv"
PANEL_PATH = OUT_DIR / "monthly_panel.parquet"

AWARD_COLUMNS = [
    NAME_COL,
    VAT_COL,
    "procedureType.key",
    "totalCostWithoutVAT",
    "totalCostWithVAT",
    "signedDate",
    "cancelled",
    "referenceNumber",
]

# §2 -- παράθυρα ανάλυσης, σε μηνιαία κοκκίδα (το panel είναι φορέας x μήνας).
# Δηλωμένα ως δεδομένα (§10.7) ώστε placebo/sensitivity να είναι απλώς άλλες
# γραμμές στο ίδιο code path.
WINDOWS = pd.DataFrame(
    [
        {"window": "PRE", "start": "2023-04", "end": "2023-09", "apex_year": 2023,
         "hypothesis": "H1", "statistical": True},
        {"window": "LAME", "start": "2023-10", "end": "2023-12", "apex_year": 2023,
         "hypothesis": "H2", "statistical": True},
        {"window": "POST", "start": "2024-01", "end": "2024-06", "apex_year": 2024,
         # R6: δομικό break (ν.5056/2023 απορρόφηση ΝΠΔΔ) -- περιγραφικό μόνο, όχι τεστ.
         "hypothesis": "descriptive", "statistical": False},
        {"window": "PLACEBO", "start": "2022-04", "end": "2022-09", "apex_year": 2022,
         "hypothesis": "placebo", "statistical": True},
    ]
)
BASELINE_YEARS_FULL = [2021, 2022, 2025]
BASELINE_YEARS_2225 = [2022, 2025]  # sensitivity: αγνοεί το 2021 (ν.4782/2021 όριο 30k)
MIN_BASELINE_N = 30  # §5.1 gate συμμετοχής ανά φορέα

# §10.7 -- 25 δήμοι Περιφέρειας Θεσσαλίας + η ίδια η Περιφέρεια (Daniel confounder).
THESSALY_MUNICIPALITIES = frozenset(
    {
        "ΔΗΜΟΣ ΒΟΛΟΥ", "ΔΗΜΟΣ ΖΑΓΟΡΑΣ ΜΟΥΡΕΣΙΟΥ", "ΔΗΜΟΣ ΝΟΤΙΟΥ ΠΗΛΙΟΥ", "ΔΗΜΟΣ ΡΗΓΑ ΦΕΡΑΙΟΥ",
        "ΔΗΜΟΣ ΑΛΜΥΡΟΥ", "ΔΗΜΟΣ ΛΑΡΙΣΑΙΩΝ", "ΔΗΜΟΣ ΑΓΙΑΣ", "ΔΗΜΟΣ ΕΛΑΣΣΟΝΑΣ",
        "ΔΗΜΟΣ ΚΙΛΕΛΕΡ", "ΔΗΜΟΣ ΦΑΡΣΑΛΩΝ", "ΔΗΜΟΣ ΤΕΜΠΩΝ", "ΔΗΜΟΣ ΤΥΡΝΑΒΟΥ",
        "ΔΗΜΟΣ ΚΑΡΔΙΤΣΑΣ", "ΔΗΜΟΣ ΜΟΥΖΑΚΙΟΥ", "ΔΗΜΟΣ ΠΑΛΑΜΑ", "ΔΗΜΟΣ ΛΙΜΝΗΣ ΠΛΑΣΤΗΡΑ",
        "ΔΗΜΟΣ ΣΟΦΑΔΩΝ", "ΔΗΜΟΣ ΑΡΓΙΘΕΑΣ",
        "ΔΗΜΟΣ ΤΡΙΚΚΑΙΩΝ", "ΔΗΜΟΣ ΚΑΛΑΜΠΑΚΑΣ", "ΔΗΜΟΣ ΦΑΡΚΑΔΟΝΑΣ", "ΔΗΜΟΣ ΠΥΛΗΣ",
        "ΔΗΜΟΣ ΜΕΤΕΩΡΩΝ",
        "ΠΕΡΙΦΕΡΕΙΑ ΘΕΣΣΑΛΙΑΣ",
    }
)

# §10.4 -- ονόματα που περνούν την αγκύρωση ^ΔΗΜΟΣ /^ΠΕΡΙΦΕΡΕΙΑ αλλά ΔΕΝ είναι
# ΟΤΑ v1 (ΔΕΥΑ/ΝΠΔΔ/αναπτυξιακές εταιρείες κ.λπ.). Χτίστηκε από επιθεώρηση
# entities.csv (2026-07-19) -- επεκτείνεται αν εμφανιστούν νέα patterns.
NON_OTA_KEYWORDS = (
    "ΝΠΔΔ", "ΝΟΜΙΚΟ ΠΡΟΣΩΠΟ", "ΑΝΩΝΥΜΗ", "ΕΤΑΙΡΕΙΑ", "ΕΠΙΧΕΙΡΗΣΗ", "ΣΧΟΛΙΚΗ",
    "ΔΕΥΑ", "ΛΙΜΕΝΙΚΟ", "ΟΡΓΑΝΙΣΜΟΣ", "ΤΑΜΕΙΟ", "ΚΟΙΝΩΦΕΛΗΣ", "ΙΔΡΥΜΑ",
    "ΚΕΝΤΡΟ", "ΜΟΝΑΔΑ",
)

_TOKEN_RE = re.compile(r"[Α-Ωα-ωA-Za-z0-9]+")


# --------------------------------------------------------------------------
# §10.4 -- κανονικοποίηση ονομάτων (accent-strip -> normalize_name), ενιαία
# για entities/δήμους/δημάρχους.
# --------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    """NFD αποσύνθεση + αφαίρεση combining marks (τόνοι/διαλυτικά)."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalize_greek(text: object) -> str:
    """accent-strip -> κανονικοποίηση tokens (βλ. splink_poc.normalize_name).

    Χρησιμοποιείται ΚΑΙ για ονόματα φορέων ΚΑΙ για εκλογικά ονόματα δήμων ΚΑΙ
    για ονοματεπώνυμα δημάρχων (§10.4) -- μία συνάρτηση, ένα unit test.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    return _token_normalize(strip_accents(str(text)))


def classify_ota(name_norm: str) -> tuple[str | None, str | None]:
    """(kind, excluded_reason). kind in {"dimos","perifereia",None}.

    Αγκυρωμένο regex ^ΔΗΜΟΣ /^ΠΕΡΙΦΕΡΕΙΑ  (κενό μετά) -- αποκλείει αυτόματα
    "ΔΗΜΟΤΙΚΗ …", "ΠΕΡΙΦΕΡΕΙΑΚΟ/Η …" (§10.4). NON_OTA_KEYWORDS αποκλείει τα
    ονόματα που περνούν την αγκύρωση αλλά είναι δημοτικά/περιφερειακά νομικά
    πρόσωπα (ΔΕΥΑ, ΝΠΔΔ, αναπτυξιακές εταιρείες...).
    """
    if name_norm.startswith("ΔΗΜΟΣ "):
        kind = "dimos"
    elif name_norm.startswith("ΠΕΡΙΦΕΡΕΙΑ "):
        kind = "perifereia"
    else:
        return None, None
    for kw in NON_OTA_KEYWORDS:
        if kw in name_norm:
            return None, f"non_ota_keyword:{kw}"
    return kind, None


# --------------------------------------------------------------------------
# Βήμα 1 -- load_awards
# --------------------------------------------------------------------------


def _clean_cancelled(series: pd.Series) -> pd.Series:
    """Ρητό mapping (§10.2) -- ΟΧΙ astype(bool), σκάει σε ανομοιογενή σχήματα."""
    mapping = {
        True: True, False: False,
        "true": True, "false": False,
        "True": True, "False": False,
        "TRUE": True, "FALSE": False,
    }
    return series.map(lambda v: mapping.get(v, False) if not (isinstance(v, float) and pd.isna(v)) else False)


def _clean_signed_date(series: pd.Series) -> pd.Series:
    """to_datetime coerce + μηδενισμός εκτός [2019, τρέχον έτος] (§10.2)."""
    dt = pd.to_datetime(series, errors="coerce")
    current_year = pd.Timestamp.now().year
    bad = dt.notna() & ((dt.dt.year < 2019) | (dt.dt.year > current_year))
    dt = dt.mask(bad)
    return dt


def load_awards(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Βήμα 1 (§10.2). Επιστρέφει το καθαρισμένο auction dataset (όλα τα
    procedure types -- χρειάζονται και για n_total)."""
    df = load_entity("auction", raw_dir=raw_dir, columns=AWARD_COLUMNS)
    if df.empty:
        return df

    df["cancelled"] = _clean_cancelled(df["cancelled"])
    df["signed_date"] = _clean_signed_date(df["signedDate"])

    resolver = load_vat_resolver()
    if resolver is None:
        logger.warning("load_awards: δεν βρέθηκε persisted vat_resolver.csv -- ΑΦΜ fallback μέσω ονόματος απενεργοποιημένο")
        resolver = pd.Series(dtype=object)
    df["vat_norm"] = resolve_vat(df, resolver)

    value_without_vat = pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce")
    value_with_vat = pd.to_numeric(df["totalCostWithVAT"], errors="coerce")
    # §10.2: αλληλουχία ταυτόσημη με direct_award_rate() για το reconciliation.
    df["value_reconcile"] = sanitize_value(value_without_vat.fillna(value_with_vat))
    # Κύριες μετρικές αξίας: ΜΟΝΟ WithoutVAT (μικτή βάση ΦΠΑ αλλιώς).
    df["value_without_vat"] = sanitize_value(value_without_vat)

    df["is_direct"] = df["procedureType.key"].astype(str) == DIRECT_AWARD_KEY

    n_missing_vat = df["vat_norm"].isna().mean()
    logger.info("load_awards: %d γραμμές, %.1f%% χωρίς τελικό ΑΦΜ", len(df), 100 * n_missing_vat)
    pct_bad_date = 100 * df["signedDate"].notna().mean() - 100 * df["signed_date"].notna().mean()
    if pct_bad_date > 0:
        logger.info("load_awards: %.2f%% signedDate εξαιρέθηκαν (NaT/εκτός εύρους)", pct_bad_date)

    return df


# --------------------------------------------------------------------------
# Βήμα 2 -- lag_audit
# --------------------------------------------------------------------------


def lag_audit(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """§10.3. Μόνο απευθείας αναθέσεις με έγκυρο signed_date.

    lag_months = (_source_year*12+_source_month) - (signed_year*12+signed_month)
    """
    sub = df[(df["is_direct"]) & df["signed_date"].notna()].copy()
    sub["lag_months"] = (
        sub["_source_year"] * 12 + sub["_source_month"]
        - (sub["signed_date"].dt.year * 12 + sub["signed_date"].dt.month)
    )

    def _summary(g: pd.DataFrame) -> pd.Series:
        lag = g["lag_months"]
        return pd.Series(
            {
                "n": len(lag),
                "median_lag": lag.median(),
                "p90_lag": lag.quantile(0.90),
                "p99_lag": lag.quantile(0.99),
                "pct_gt_3m": 100 * (lag > 3).mean(),
                "pct_negative": 100 * (lag < 0).mean(),
            }
        )

    by_year = sub.groupby("_source_year").apply(_summary, include_groups=False).reset_index()
    by_year["segment"] = "by_source_year"

    # Κρίσιμη έξοδος R2: lag profile γύρω από τη μετάβαση 2023-10..2024-03.
    transition_mask = sub["signed_date"].dt.to_period("M").between(
        pd.Period("2023-10"), pd.Period("2024-03")
    )
    rows = [_summary(sub).to_dict() | {"_source_year": "ALL", "segment": "overall"}]
    if transition_mask.any():
        rows.append(_summary(sub[transition_mask]).to_dict() | {"_source_year": "ALL", "segment": "transition_2023-10_2024-03"})
    overall = pd.DataFrame(rows)

    result = pd.concat([overall, by_year], ignore_index=True)

    overall_median = float(sub["lag_months"].median()) if len(sub) else float("nan")
    flag = {
        "overall_median_lag_months": overall_median,
        "quarterly_fallback_required": bool(overall_median > 1) if not np.isnan(overall_median) else None,
    }
    return result, flag


# --------------------------------------------------------------------------
# Βήμα 3 -- build_ota_mapping
# --------------------------------------------------------------------------


def build_ota_mapping(entities_path: Path = ENTITIES_PATH, official_names: list[str] | None = None) -> pd.DataFrame:
    """§10.4. Ταξινομεί τα entities.csv σε δήμους/περιφέρειες, γράφει mapping
    με human-review στήλες. Αν δοθεί ``official_names`` (λίστα κανονικοποιημένων
    επίσημων ονομάτων δήμων από το εκλογικό dataset ΥΠΕΣ 2023, §10.4), γίνεται
    exact + fuzzy join για το coverage report· χωρίς αυτό, η ταξινόμηση μένει
    μόνο σε επίπεδο entities.csv (χρήσιμη ήδη, αλλά coverage/332 pending).
    """
    entities = pd.read_csv(entities_path, dtype=str)
    entities["name_norm"] = entities["name"].fillna("").map(normalize_greek)

    rows = []
    for _, row in entities.iterrows():
        kind, excluded_reason = classify_ota(row["name_norm"])
        if kind is None and excluded_reason is None:
            continue  # δεν αγκυρώνεται καν -- δεν είναι καν candidate, δεν καταγράφεται
        rows.append(
            {
                "official_name": None,
                "kimdis_name": row["name"],
                "kimdis_name_norm": row["name_norm"],
                "vat": row["vat"],
                "kind": kind,
                "match_type": "unverified" if kind else "excluded",
                "similarity": None,
                "review_status": "pending" if kind else "auto",
                "excluded_reason": excluded_reason,
                "thessaly": row["name_norm"] in THESSALY_MUNICIPALITIES,
                "notes": None,
            }
        )
    mapping = pd.DataFrame(rows)

    if official_names:
        import difflib

        official_norm = {normalize_greek(n) for n in official_names}
        exact_hit = mapping["kimdis_name_norm"].isin(official_norm)
        mapping.loc[exact_hit & mapping["kind"].notna(), "match_type"] = "exact"
        mapping.loc[exact_hit & mapping["kind"].notna(), "review_status"] = "auto"
        mapping.loc[exact_hit & mapping["kind"].notna(), "official_name"] = mapping.loc[exact_hit, "kimdis_name_norm"]

        unmatched = mapping[mapping["kind"].notna() & ~exact_hit]
        for idx, row in unmatched.iterrows():
            close = difflib.get_close_matches(row["kimdis_name_norm"], list(official_norm), n=1, cutoff=0.75)
            if close:
                score = difflib.SequenceMatcher(None, row["kimdis_name_norm"], close[0]).ratio()
                mapping.loc[idx, ["official_name", "match_type", "similarity", "review_status"]] = [
                    close[0], "fuzzy", round(score, 3), "pending",
                ]
            else:
                mapping.loc[idx, "match_type"] = "missing"

    return mapping


def mapping_coverage_report(mapping: pd.DataFrame, official_names: list[str] | None) -> dict:
    """anti-join του επίσημου καταλόγου (332 δήμοι) με το mapping -- §3.3."""
    if not official_names:
        return {"coverage_pending": "χρειάζεται elections.csv (Βήμα 4) για επίσημη λίστα 332 δήμων"}
    official_norm = {normalize_greek(n) for n in official_names}
    matched = set(mapping.loc[mapping["kind"] == "dimos", "kimdis_name_norm"]) | set(
        mapping.loc[mapping["match_type"].isin(["exact", "fuzzy"]), "official_name"].dropna()
    )
    missing = sorted(official_norm - matched)
    return {"n_official": len(official_norm), "n_found": len(official_norm) - len(missing), "missing": missing}


# --------------------------------------------------------------------------
# Βήμα 4 -- load_elections (parser -- χρειάζεται raw export στο OUT_DIR/raw/)
# --------------------------------------------------------------------------


def load_elections(raw_dir: Path = RAW_ELECTION_DIR) -> pd.DataFrame:
    """§10.5. Parser του εκλογικού export ΥΠΕΣ 2023 (+ 2019 για το flag).

    R10: το ακριβές format δεν έχει επαληθευτεί ακόμα (χρειάζεται χειροκίνητο
    fetch από ekloges.ypes.gr ή data.gov.gr -- βλ. PLAN.md §10.5 βήμα 1). Αυτή
    η συνάρτηση περιμένει ήδη κατεβασμένα αρχεία σε ``raw_dir`` και σκάει ρητά
    αν δεν τα βρει, αντί να επινοήσει δεδομένα.
    """
    if not raw_dir.exists() or not any(raw_dir.iterdir()):
        raise FileNotFoundError(
            f"{raw_dir} άδειο -- Βήμα 4 απαιτεί χειροκίνητο fetch του εκλογικού export "
            "ΥΠΕΣ 2023 (και 2019 για το flag αλλαγής) πριν τρέξει ο parser. "
            "Βλ. PLAN.md §10.5 βήμα 1 (πρώτα επαλήθευση format, μετά parser)."
        )
    raise NotImplementedError(
        "Parser εκλογικού export: γράφεται αφού επαληθευτεί το πραγματικό "
        "format ΥΠΕΣ 2023 σε raw_dir (R10 -- ρητά μη δεσμευτικό σε schema εκ των προτέρων)."
    )


def compare_leadership(name_2019: object, name_2023: object) -> str:
    """§10.5 βήμα 3. exact ισότητα -> "false" (επανεκλογή)· διαφορά -> "true"·
    ίδιο επώνυμο διαφορετικό μικρό όνομα, ή λείπον 2019 -> "unknown" (R5)."""
    n19 = normalize_greek(name_2019)
    n23 = normalize_greek(name_2023)
    if not n19 or not n23:
        return "unknown"
    if n19 == n23:
        return "false"
    toks19, toks23 = n19.split(" "), n23.split(" ")
    surname19 = toks19[-1] if toks19 else ""
    surname23 = toks23[-1] if toks23 else ""
    if surname19 and surname19 == surname23:
        return "unknown"
    return "true"


# --------------------------------------------------------------------------
# Βήμα 5 -- monthly_panel
# --------------------------------------------------------------------------


def assign_month(df: pd.DataFrame, date_mode: str) -> pd.Series:
    """§10.6. Κοινή στήλη 'month' (Period[M]) ανεξάρτητα από date-mode."""
    if date_mode == "signed":
        return df["signed_date"].dt.to_period("M")
    if date_mode == "source":
        return pd.PeriodIndex.from_fields(year=df["_source_year"], month=df["_source_month"], freq="M")
    raise ValueError(f"άγνωστο date_mode: {date_mode!r}")


def monthly_panel(awards: pd.DataFrame, mapping: pd.DataFrame, date_mode: str = "signed") -> pd.DataFrame:
    """§10.6. Πλέγμα φορέας x μήνας (reindex 2020-01..2026-06), μηδενισμένο
    εκτός από τα PERMANENT_AUCTION_GAPS (NaN σε source mode -- §5-confounders)."""
    ota_vats = mapping.loc[mapping["kind"].notna(), "vat"].dropna().unique().tolist()
    df = awards[
        (~awards["cancelled"]) & awards["vat_norm"].isin(ota_vats)
    ].copy()
    df["month"] = assign_month(df, date_mode)
    df = df[df["month"].notna()]

    grouped = df.groupby(["vat_norm", "month"]).agg(
        n_total=("referenceNumber", "size"),
        n_direct=("is_direct", "sum"),
        value_total=("value_without_vat", "sum"),
        value_direct=("value_without_vat", lambda s: s[df.loc[s.index, "is_direct"]].sum()),
    ).reset_index()

    full_months = pd.period_range("2020-01", "2026-06", freq="M")
    all_vats = pd.Index(sorted(set(ota_vats) & set(df["vat_norm"].dropna().unique())), name="vat_norm")
    idx = pd.MultiIndex.from_product([all_vats, full_months], names=["vat_norm", "month"])
    panel = grouped.set_index(["vat_norm", "month"]).reindex(idx).reset_index()

    for col in ("n_total", "n_direct", "value_total", "value_direct"):
        panel[col] = panel[col].fillna(0.0)

    if date_mode == "source":
        gap_periods = {pd.Period(f"{y}-{m:02d}") for y, m in PERMANENT_AUCTION_GAPS}
        gap_mask = panel["month"].isin(gap_periods)
        for col in ("n_total", "n_direct", "value_total", "value_direct"):
            panel.loc[gap_mask, col] = np.nan

    panel["da_count_pct"] = np.where(panel["n_total"] > 0, 100 * panel["n_direct"] / panel["n_total"], np.nan)
    panel["da_value_pct"] = np.where(panel["value_total"] > 0, 100 * panel["value_direct"] / panel["value_total"], np.nan)

    # p99 winsorization ανά φορέα, επί όλης της περιόδου (§10.6).
    cap = df.groupby("vat_norm")["value_without_vat"].quantile(0.99).rename("p99_value")
    panel = panel.merge(cap, on="vat_norm", how="left")

    return panel


# --------------------------------------------------------------------------
# Βήμα 6 -- window_analysis
# --------------------------------------------------------------------------


def ratio_for_window(panel: pd.DataFrame, metric: str, window_row: pd.Series, baseline_years: list[int]) -> pd.Series:
    """§10.7. R_w = mean(apex μήνες) / mean(ίδιοι ημερολογιακοί μήνες, baseline
    έτη), skipna. baseline mean == 0 -> NaN (ο φορέας εξαιρείται, ΟΧΙ +ε)."""
    start, end = pd.Period(window_row["start"]), pd.Period(window_row["end"])
    apex_year = int(window_row["apex_year"])
    calendar_months = pd.period_range(start, end, freq="M")
    month_offsets = [m.month for m in calendar_months]

    apex_mask = panel["month"].isin(calendar_months)
    apex_mean = panel[apex_mask].groupby("vat_norm")[metric].mean()

    baseline_periods = [
        pd.Period(f"{y}-{mo:02d}") for y in baseline_years for mo in month_offsets
    ]
    baseline_mask = panel["month"].isin(baseline_periods)
    baseline_mean = panel[baseline_mask].groupby("vat_norm")[metric].mean()

    ratio = apex_mean / baseline_mean.replace(0, np.nan)
    return ratio


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    """§10.7. FDR 5% διόρθωση, χωρίς statsmodels dependency (~10 γραμμές)."""
    p = np.asarray(pvalues, dtype=float)
    n = len(p)
    if n == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    # μονοτονικό: κάθε τιμή <= την επόμενη (από το τέλος προς την αρχή)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    ranked = np.clip(ranked, 0, 1)
    adjusted = np.empty(n)
    adjusted[order] = ranked
    return adjusted.tolist()


def min_baseline_gate(panel: pd.DataFrame, baseline_years: list[int]) -> pd.Index:
    """§5.1 -- φορείς με >=MIN_BASELINE_N απευθείας αναθέσεις στο baseline."""
    baseline_mask = panel["month"].dt.year.isin(baseline_years)
    n_direct_baseline = panel[baseline_mask].groupby("vat_norm")["n_direct"].sum()
    return n_direct_baseline[n_direct_baseline >= MIN_BASELINE_N].index


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("γράφτηκε %s (%d γραμμές)", path, len(df))


def run_step1() -> pd.DataFrame:
    awards = load_awards()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    awards.to_parquet(OUT_DIR / "awards_clean.parquet", index=False)
    logger.info("Βήμα 1: %d γραμμές -> %s", len(awards), OUT_DIR / "awards_clean.parquet")
    return awards


def run_step2(awards: pd.DataFrame | None = None) -> None:
    if awards is None:
        awards = pd.read_parquet(OUT_DIR / "awards_clean.parquet")
    result, flag = lag_audit(awards)
    _write_csv(result, LAG_AUDIT_PATH)
    logger.info("lag audit: %s", flag)
    if flag["quarterly_fallback_required"]:
        logger.warning("QA gate §7.1: median lag > 1 μήνας -- η μηνιαία ανάλυση πρέπει να υποβαθμιστεί σε τριμηνιαία")


def run_step3() -> None:
    mapping = build_ota_mapping()
    _write_csv(mapping, MAPPING_PATH)
    n_pending = (mapping["review_status"] == "pending").sum()
    logger.info(
        "Βήμα 3: %d υποψήφιοι ΟΤΑ, %d εξαιρέθηκαν (non-OTA keyword). "
        "%d γραμμές review_status=pending -- HUMAN GATE: επιθεωρήστε το %s πριν προχωρήσετε στο Βήμα 4+.",
        (mapping["kind"].notna()).sum(),
        (mapping["kind"].isna()).sum(),
        n_pending,
        MAPPING_PATH,
    )


def _check_mapping_reviewed() -> pd.DataFrame:
    if not MAPPING_PATH.exists():
        raise FileNotFoundError(f"{MAPPING_PATH} δεν υπάρχει -- τρέξτε πρώτα --step 3")
    mapping = pd.read_csv(MAPPING_PATH, dtype=str)
    pending = mapping[mapping["review_status"] == "pending"]
    if len(pending):
        raise RuntimeError(
            f"{len(pending)} γραμμές του {MAPPING_PATH} έχουν review_status=pending -- "
            "human gate §6.3: επιθεωρήστε χειροκίνητα πριν συνεχίσετε (--step 4+)."
        )
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--step", type=int, choices=range(1, 8), default=None)
    parser.add_argument("--date-mode", choices=["signed", "source"], default="signed")
    parser.add_argument("--exclude-thessaly", action="store_true")
    parser.add_argument("--baseline", choices=["full", "2225"], default="full")
    args = parser.parse_args()

    if args.step in (None, 1):
        awards = run_step1()
    if args.step == 2:
        run_step2()
        return
    if args.step in (None, 2):
        run_step2()
    if args.step == 3:
        run_step3()
        return
    if args.step in (None, 3):
        run_step3()
        logger.info("Χωρίς --step: το script σταματά εδώ (human gate Βήματος 3, §6.3). "
                     "Ξανατρέξτε με --step 4 αφού επιθεωρήσετε το %s.", MAPPING_PATH)
        return
    if args.step == 4:
        load_elections()
        return
    if args.step in (5, 6, 7):
        _check_mapping_reviewed()
        raise NotImplementedError(
            f"Βήμα {args.step}: χρειάζεται elections.csv (Βήμα 4, χειροκίνητο fetch -- βλ. PLAN.md §10.5)."
        )


if __name__ == "__main__":
    main()
