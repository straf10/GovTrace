"""Κοινές βοηθητικές συναρτήσεις φόρτωσης raw δεδομένων ΚΗΜΔΗΣ.

Χρησιμοποιείται από compute_indicators_v1.py, build_entity_table.py,
build_site_data.py και build_foreas_data.py ώστε η λογική φόρτωσης
(glob + concat + έτος/μήνας από filename) και η κανονικοποίηση ΑΦΜ να
ζουν σε ένα σημείο.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

_VAT_RE = re.compile(r"\D")
_VALID_VAT_RE = re.compile(r"\d{9}")

INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)


def flatten(records: list[dict]) -> pd.DataFrame:
    """json_normalize στο πρώτο επίπεδο· εναπομείναντα nested list/dict σε JSON strings.

    Ακραίες τιμές int εκτός int64 (παρατηρήθηκε π.χ. contractDuration=8e19 στο
    auction_2025_04 -- σαφώς κατεστραμμένη τιμή πηγής) γίνονται None αντί να
    σκάει το pyarrow στο to_parquet.

    B5 (tech_report v2): ενοποιημένο εδώ αντί να ζει διπλότυπο σε
    backfill_historical.py/fetch_month.py -- το session-12 overflow guard είχε
    μπει μόνο στο ένα αντίγραφο και το άλλο έσκαγε με OverflowError.
    """
    df = pd.json_normalize(records)
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
        elif df[col].map(lambda v: isinstance(v, int) and not isinstance(v, bool)).any():
            oversized = df[col].map(
                lambda v: isinstance(v, int) and not isinstance(v, bool) and not (INT64_MIN <= v <= INT64_MAX)
            )
            if oversized.any():
                logger.warning(
                    "flatten: %d ακραίες τιμές εκτός int64 στη στήλη %r -- μηδενίζονται",
                    oversized.sum(), col,
                )
                df.loc[oversized, col] = None
    return df


def completeness_report(df: pd.DataFrame, entity: str) -> dict[str, float]:
    """% εγγραφών με ΑΔΑΜ, ημερομηνία, ποσό."""
    n = len(df)
    if n == 0:
        return {"records": 0}

    def pct(mask: pd.Series) -> float:
        return round(100.0 * mask.sum() / n, 2)

    has_adam = df["referenceNumber"].notna() & (df["referenceNumber"].astype(str).str.len() > 0)
    has_date = df["submissionDate"].notna() if "submissionDate" in df.columns else pd.Series(False, index=df.index)
    amount_cols = [c for c in ("totalCostWithVAT", "totalCostWithoutVAT", "budget") if c in df.columns]
    has_amount = pd.Series(False, index=df.index)
    for col in amount_cols:
        has_amount |= pd.to_numeric(df[col], errors="coerce").notna()

    report = {
        "records": n,
        "pct_adam": pct(has_adam),
        "pct_date": pct(has_date),
        "pct_amount": pct(has_amount),
    }
    if "organizationVatNumber" in df.columns:
        vat = df["organizationVatNumber"]
        report["pct_org_vat"] = pct(vat.notna())
        # B3: format-valid (9ψήφιο) ΑΦΜ έναντι mod-11 checksum-valid -- η
        # απόσταση των δύο ποσοστών δείχνει πόσο "σκουπίδι" περνάει τη μορφή
        # αλλά όχι το checksum.
        report["pct_org_vat_checksum"] = pct(vat.map(is_valid_vat_checksum))
    return report


def load_entity(entity: str, raw_dir: Path = RAW_DIR, columns: list[str] | None = None) -> pd.DataFrame:
    """Φορτώνει και συνενώνει όλα τα διαθέσιμα <entity>_<YYYY>_<MM>.parquet.

    Προσθέτει _source_year/_source_month από το filename (πιο αξιόπιστο από
    τα raw πεδία ημερομηνίας, τα οποία λείπουν σε κάποιες εγγραφές).

    ``columns``: αν δοθεί, διαβάζονται μόνο αυτές οι στήλες (pyarrow column
    pruning) -- σημαντικό για το build_foreas_data.py που διαβάζει ολόκληρο
    το ιστορικό σε ένα πέρασμα. Στήλες που ζητούνται αλλά λείπουν από το
    σχήμα ενός συγκεκριμένου αρχείου (F1: διαφορετικά μηνιαία σχήματα, π.χ.
    auction_2025_05 χωρίς typeOfContractingAuthority) διαβάζονται παραλείποντας
    τις, και συμπληρώνονται με None ώστε το τελικό concat να έχει ενιαίο σχήμα
    αντί να σκάει με ArrowInvalid.
    """
    files = sorted(raw_dir.glob(f"{entity}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        if columns is None:
            df = pd.read_parquet(f)
        else:
            available = set(pq.ParquetFile(f).schema_arrow.names)
            present = [c for c in columns if c in available]
            df = pd.read_parquet(f, columns=present)
            for missing_col in columns:
                if missing_col not in df.columns:
                    df[missing_col] = None
        _, year, month = f.stem.split("_")
        df["_source_year"] = int(year)
        df["_source_month"] = int(month)
        frames.append(df)
    result = pd.concat(frames, ignore_index=True)
    # A4/P6: φθηνή προστασία από μελλοντικά διπλότυπα referenceNumber
    # (τροποποιητικές πράξεις, incremental updates, επικάλυψη backfill μηνών).
    # Το backfill δουλεύει σήμερα ανά μη-επικαλυπτόμενους μήνες (0 διπλότυπα
    # μετρημένα στο audit), αλλά downstream joins πάνω σε referenceNumber
    # (π.χ. discount_rate) σκάνε με InvalidIndexError αν εμφανιστεί ένα.
    if "referenceNumber" in result.columns:
        # F4: drop_duplicates θεωρεί τα NaN ίσα -- γραμμές χωρίς ΑΔΑΜ θα
        # κατέρρεαν σιωπηλά σε μία. Dedupe μόνο στις μη-κενές τιμές.
        ref = result["referenceNumber"]
        dup_mask = ref.notna() & ref.duplicated(keep="last")
        if dup_mask.any():
            result = result[~dup_mask]
    return result


def normalize_vat(value: object) -> str | None:
    """Κανονικοποιεί ένα ΑΦΜ σε 9ψήφιο string ή None αν είναι άκυρο/σκουπίδι.

    Βλ. docs/MEMORY.md session 6 audit: raw ΑΦΜ έχουν κενά/tabs/έξτρα
    μηδενικά (π.χ. "\\t090016590", " 090153025", "00901536025"). Κρατάμε
    μόνο τα ψηφία· αν είναι >9, κόβουμε αρχικά μηδενικά· αν καταλήγουν σε
    7-9 ψηφία, συμπληρώνουμε με μηδενικά αριστερά σε 9. Τιμές όπως "0",
    "09", "000000000" απορρίπτονται ως άκυρες.
    """
    if not isinstance(value, str):
        return None
    digits = _VAT_RE.sub("", value)
    if len(digits) > 9:
        digits = digits.lstrip("0")
    if 7 <= len(digits) <= 9:
        digits = digits.zfill(9)
    if _VALID_VAT_RE.fullmatch(digits) and digits != "000000000":
        return digits
    return None


def is_valid_vat_checksum(vat: str) -> bool:
    """Έλεγχος mod-11 του ελληνικού ΑΦΜ (απόφαση #7, B3 στο tech_report v2).

    Αλγόριθμος: για τα πρώτα 8 ψηφία d[0..7] (από αριστερά), βάρος 2^(8-i)·
    ψηφίο ελέγχου = (Σ d[i]·2^(8-i) mod 11) mod 10, συγκρίνεται με το ένατο
    ψηφίο. Δεν καλείται από ``normalize_vat`` -- το checksum μετράει
    **δημοσιεύσιμη εγκυρότητα** (βλ. ``completeness_report``), δεν φιλτράρει
    το keying/entity-resolution, όπου format-valid 9ψήφια (π.χ. ΑΦΜ φορέων
    με ιστορικά μη τυπικές τιμές) πρέπει να παραμένουν χρησιμοποιήσιμα.
    """
    if not isinstance(vat, str) or not _VALID_VAT_RE.fullmatch(vat):
        return False
    digits = [int(c) for c in vat]
    total = sum(d * (2 ** (8 - i)) for i, d in enumerate(digits[:8]))
    return (total % 11) % 10 == digits[8]


NAME_COL = "organization.value"
VAT_COL = "organizationVatNumber"

# 2026-07-10 session: το εθνικό sanity-check του compute_indicators_v1.py
# (da_value_pct) ταλαντευόταν χωρίς λογική έτος προς έτος (33%→97%→41%→98%...).
# Αιτία: 20 εγγραφές totalCostWithVAT/WithoutVAT >1 δισ. ευρώ, μερικές έως
# €600 δισ., με χαρακτηριστικό fingerprint φθαρμένης πηγής -- π.χ. ΑΦΜ
# "11111111111" με ποσό ακριβώς 111.111.111.111 (προφανές placeholder/test
# row), ή Δήμος Αγίας Παρασκευής με €600.030.600.000 σε μία ανάθεση όταν ο
# μέσος όρος των υπόλοιπων 802 αναθέσεών του είναι €13.600. Το όριο 10 δισ.
# χωρίζει καθαρά αυτές τις 9 εγγραφές-σκουπίδια από τις μεγαλύτερες αλλά
# εύλογες τιμές που μένουν (π.χ. ΑΔΜΗΕ €2,06δισ., Υπ. Εθνικής Άμυνας
# €4,26δισ.). Δεν κάνει clip στο cap -- τις μηδενίζει, ώστε να μην
# διαστρεβλώνουν sums/rankings (π.χ. terciles μεγέθους στο build_foreas_data.py).
VALUE_SANITY_CAP = 10_000_000_000.0  # 10 δισ. ευρώ


def sanitize_value(value: pd.Series) -> pd.Series:
    """Μηδενίζει τιμές πάνω από ``VALUE_SANITY_CAP`` (βλ. σημείωση παραπάνω)."""
    out = value.copy()
    bad = out > VALUE_SANITY_CAP
    if bad.any():
        logger.warning("sanitize_value: %d τιμές > %.0f -- μηδενίζονται ως σκουπίδια πηγής", bad.sum(), VALUE_SANITY_CAP)
        out.loc[bad] = None
    return out


def build_vat_resolver(frames: list[pd.DataFrame], min_share: float = 0.9) -> pd.Series:
    """Χτίζει lookup όνομα->ΑΦΜ από όλες τις γραμμές που έχουν ήδη έγκυρο ΑΦΜ.

    ΔΙΟΡΘΩΣΗ (session 6, 2ο πέρασμα): το ``greekOrganizationVatNumber`` είναι
    **boolean flag**, όχι εναλλακτικό ΑΦΜ -- λάθος υπόθεση του πρώτου audit
    (φαινόταν "100% συμπληρωμένο" επειδή μια bool στήλη δεν είναι ποτέ κενή).
    Το μόνο πεδίο με πραγματικό ΑΦΜ είναι το ``organizationVatNumber``, το
    οποίο είναι ~99% συμπληρωμένο στο **contract** (από 2021-03 και μετά) αλλά
    μόνο ~25% στο auction/notice (εκτός από το 2025-05 snapshot, 99.6%).

    Λύση: χτίζουμε lookup name -> ΑΦΜ από το σύνολο των raw δεδομένων (κυρίως
    contract) όπου το ΑΦΜ υπάρχει, κρατώντας μόνο ονόματα όπου ένα ΑΦΜ
    κυριαρχεί (μερίδιο >= ``min_share``, default 90% -- πιο αυστηρό από το
    hard-coded 80% του audit) και το χρησιμοποιούμε ως fallback για γραμμές
    (κυρίως auction/notice) που δεν έχουν δικό τους ΑΦΜ. Ονόματα κάτω από το
    κατώφλι (γνήσια αμφίσημα -- π.χ. πανεπιστήμιο + ΕΛΚΕ του) ΔΕΝ μπαίνουν
    στο lookup: οι γραμμές τους μένουν χωρίς ΑΦΜ και εξαιρούνται από τα
    ανά-φορέα προϊόντα αντί να συγχωνευτούν λανθασμένα.
    """
    pairs = []
    for df in frames:
        if NAME_COL not in df.columns or VAT_COL not in df.columns:
            continue
        vat = df[VAT_COL].map(normalize_vat)
        sub = pd.DataFrame({"name": df[NAME_COL], "vat": vat}).dropna()
        if not sub.empty:
            pairs.append(sub)
    if not pairs:
        return pd.Series(dtype=object)

    all_pairs = pd.concat(pairs, ignore_index=True)
    counts = all_pairs.groupby(["name", "vat"]).size().rename("n").reset_index()
    totals = counts.groupby("name")["n"].transform("sum")
    counts["share"] = counts["n"] / totals
    best = counts.sort_values("n", ascending=False).drop_duplicates("name")
    best = best[best["share"] >= min_share]
    return best.set_index("name")["vat"]


VAT_RESOLVER_PATH = PROCESSED_DIR / "vat_resolver.csv"


def save_vat_resolver(resolver: pd.Series, path: Path = VAT_RESOLVER_PATH) -> None:
    """Αποθηκεύει τον resolver (name -> ΑΦΜ) ως persisted artifact (audit A3).

    Γράφεται από build_entity_table.py, το οποίο τον χτίζει από
    auction+contract+notice (η σωστή, πληρέστερη πηγή ΑΦΜ). Τα υπόλοιπα
    downstream scripts (compute_indicators_v1.py, build_foreas_data.py) τον
    διαβάζουν αντί να τον ξαναχτίζουν με διαφορετικά/ελλιπή inputs -- πριν τη
    διόρθωση, το build_foreas_data.py έχτιζε δικό του resolver μόνο από
    auctions (~25% ΑΦΜ fill), με αποτέλεσμα ασύμφωνα κλειδιά ΑΦΜ ανάμεσα σε
    σελίδες προφίλ και δείκτες.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    resolver.rename("vat").rename_axis("name").reset_index().to_csv(path, index=False, encoding="utf-8-sig")


def load_vat_resolver(path: Path = VAT_RESOLVER_PATH) -> pd.Series | None:
    """Διαβάζει τον persisted resolver, ή None αν δεν υπάρχει ακόμα (πρώτο run)."""
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype=str)
    return df.set_index("name")["vat"]


def resolve_vat(df: pd.DataFrame, resolver: pd.Series) -> pd.Series:
    """Ανά-γραμμή ΑΦΜ: δικό της (κανονικοποιημένο) αν υπάρχει, αλλιώς fallback
    μέσω ``resolver`` (name -> ΑΦΜ, βλ. ``build_vat_resolver``)."""
    own = df[VAT_COL].map(normalize_vat) if VAT_COL in df.columns else pd.Series([None] * len(df), index=df.index)
    if NAME_COL not in df.columns:
        return own
    missing = own.isna()
    if missing.any():
        own = own.copy()
        own.loc[missing] = df.loc[missing, NAME_COL].map(resolver)
    return own
