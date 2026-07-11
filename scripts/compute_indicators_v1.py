"""Φάση 1: Δείκτες v1 ανά φορέα/έτος (METHODOLOGY.md §4).

Διαβάζει ό,τι raw δεδομένα υπάρχουν ήδη σε data/raw/ (auction_*, contract_*,
notice_*) και υπολογίζει:
  - §4.1 DA_count, DA_value: μερίδιο απευθείας αναθέσεων σε πλήθος/αξία
  - §4.2 HHI, top1: συγκέντρωση αναδόχων στις συμβάσεις (ελάχιστο N=10)
  - §4.5 bid-splitting: density ratio αναθέσεων γύρω από το νόμιμο όριο
    απευθείας ανάθεσης (ελάχιστο N=5 σε κάθε ζώνη)
  - §4.3 single-bid rate: ποσοστό ανταγωνιστικών συμβάσεων με μία μόνο προσφορά
  - §4.6 discount rate: ποσοστό διαδικασιών με ~0% έκπτωση προκήρυξη→ανάθεση,
    μέσω σύνδεσης notice.referenceNumber <-> auction.noticeRefNo (η σύνδεση
    notice<->contract είναι σχεδόν κενή στα δεδομένα, βλ. σημείωση #4.6 πιο
    κάτω· χρησιμοποιούμε την τιμή κατακύρωσης (auction) ως proxy της τιμής
    σύμβασης, τεκμηριωμένο εδώ και στο METHODOLOGY.md)

Ξαναρχόμενο script: τρέχει πάνω σε ό,τι δεδομένα υπάρχουν τη στιγμή εκτέλεσης
(ο backfill μπορεί να είναι ακόμα σε εξέλιξη).

Χρήση:
    python scripts/compute_indicators_v1.py
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from kimdis_data import (
    NAME_COL,
    PROCESSED_DIR,
    VAT_COL,
    build_vat_resolver,
    load_entity,
    load_vat_resolver,
    resolve_vat,
    sanitize_value,
)

RAW_DIR = Path("data/raw")
OUT_DIR = PROCESSED_DIR
DIRECT_AWARD_KEY = "6"  # procedureType.key == "6" -> Απευθείας ανάθεση (επιβεβαιωμένο, βλ. MEMORY.md §4)
MIN_N_HHI = 10  # METHODOLOGY §4.2
MIN_N_BID_SPLIT = 5  # METHODOLOGY §4.5 -- ελάχιστο πλήθος ανά ζώνη για δημοσίευση
MIN_N_DISCOUNT = 5  # METHODOLOGY §4.6
MIN_N_DEADLINE = 5  # METHODOLOGY §4.7 δεν ορίζει ρητά ελάχιστο N -- χρησιμοποιείται
                     # το ίδιο κατώφλι με §4.5/§4.6 (απόφαση session 18, καταγεγραμμένη
                     # ρητά εδώ όπως ζητήθηκε).
MIN_N_SINGLE_BID = 5  # METHODOLOGY §4.3 -- ίδιο κατώφλι δημοσίευσης με §4.5/§4.6/§4.7
MIN_N_BENFORD = 300  # METHODOLOGY §4.4 -- Nigrini ελάχιστο δείγμα για αξιόπιστο MAD
SINGLE_BID_CUTOVER = (2025, 4)  # Πρώτος πλήρης μήνας με bidsSubmitted· 2020..2025-03 είναι κενό/μερικό.
BIDS_SUBMITTED_MAX = 100  # Τιμές έως 335.315+ έχουν καταγραφεί ως garbage source values.

# P1 (audit): column pruning -- χωρίς αυτό φορτώνονται και οι ~53-65 στήλες
# (μαζί με ογκώδη nested JSON-string πεδία) όλης της ιστορίας σε pandas.
AUCTION_COLS = [
    NAME_COL, VAT_COL, "procedureType.key", "totalCostWithVAT", "totalCostWithoutVAT",
    "contractType.value", "submissionDate", "noticeRefNo",
]
CONTRACT_COLS = [
    NAME_COL, VAT_COL, "procedureType.key", "procedureType.value", "bidsSubmitted",
    "contract.bidsSubmitted", "totalCostWithVAT", "totalCostWithoutVAT", "submissionDate",
    "contractingDataDetails.contractingMembersDataList",
]
NOTICE_COLS = [
    NAME_COL, VAT_COL, "referenceNumber", "totalCostWithVAT", "totalCostWithoutVAT", "budget",
    "typeOfProcedure.key", "submissionDate", "finalSubmissionDate",
]
PAYMENT_COLS = [
    NAME_COL, VAT_COL, "totalCostWithoutVAT",
]

# --- §4.5 Νόμιμα όρια απευθείας ανάθεσης ανά περίοδο (ν. 4412/2016, άρθρο 118) ---
# Πριν 1/6/2021: ενιαίο όριο 20.000€ χωρίς ΦΠΑ (όλες οι κατηγορίες).
# Από 1/6/2021 (ν. 4782/2021 άρθ. 50, έναρξη ισχύος κατ' άρθ. 142): 30.000€
# προμήθειες/υπηρεσίες, 60.000€ έργα.
# ΣΗΜΕΙΩΣΗ (εκκρεμείς διορθώσεις πριν δημοσιευθεί ο δείκτης — βλ. docs/MEMORY.md
# session 17 «Εκκρεμείς έλεγχοι»):
#   (α) Μελέτες/Τεχνικές υπηρεσίες πιθανότατα 30.000€, όχι 60.000€ (διασταύρωση με ΦΕΚ)·
#   (β) τα όρια είναι ΠΡΟ ΦΠΑ — το fallback σε totalCostWithVAT παρακάτω δίνει
#       false positives στη ζώνη του ορίου (π.χ. 30.000×1,24=37.200)·
#   (γ) Βιβλίο ΙΙ (ΔΕΗ/ΕΥΔΑΠ/ΟΣΕ κ.λπ.) οριζόντιο 60.000€ + Παράρτημα XIV (CPV
#       ειδικών υπηρεσιών) 60.000€ και για Βιβλίο Ι (άρθ. 118 παρ. 6) — δεν χειρίζονται·
#   (δ) εξαιρέσεις άρθρου 32 (κατεπείγον/αποκλειστικότητα/άγονος) = νόμιμες άνω ορίου.
# Επίσης: κρίσιμη ημερομηνία κατά τον νόμο = έναρξη διαδικασίας· το submissionDate
# που χρησιμοποιείται είναι proxy.
THRESHOLD_CHANGE_DATE = date(2021, 6, 1)
WORKS_STUDIES_TYPES = {"Έργα", "Μελέτες", "Τεχνικές ή λοιπές συναφείς υπηρεσίες"}

# --- §4.7 Προθεσμίες υποβολής ---
# Session 18 απόφαση χρήστη: το "% διαδικασιών με σύντομη προθεσμία" ΔΕΝ
# υλοποιείται σε αυτό το πέρασμα -- θα χρειαστεί πίνακας ελάχιστων νόμιμων
# προθεσμιών ανά τύπο διαδικασίας (EU Directive 2014/24 άρθρα 27-28 / ν.4412/2016
# άρθρα 121-122), που δεν υπάρχει σήμερα τεκμηριωμένος στο METHODOLOGY.md και
# απαιτεί νομική επιβεβαίωση -- ίδιο status με το bid-splitting (§4.5).
# Δημοσιεύεται εδώ μόνο η αδιαμφισβήτητη διάμεση προθεσμία (ημέρες).
#
# Εξαιρέσεις μη-ανταγωνιστικών/ειδικών διαδικασιών για §4.7 και §4.3:
# - key=="6": Απευθείας ανάθεση (υφιστάμενη επιβεβαιωμένη εξαίρεση).
# - key=="12" / value "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση...":
#   άρθρο 32 ν.4412/2016, επιβεβαιωμένο από εγκύκλιο ΓΓ Εμπορίου 99864/2025.
# - key=="18" / value "Διαδικασία άρθρου 128 του ν.4412/16": πιθανό ότι δεν
#   έχει ενιαίο χρονοδιάγραμμα ανταγωνιστικής υποβολής, βλ.
#   docs/research/bid_splitting_and_deadlines_research.md.
# Αυτές δεν μετέχουν στη δημοσιευμένη median_deadline_days ώστε να μην
# αλλοιώνουν τη διάμεσο προς τα κάτω. Το pct_short_deadline παραμένει ανενεργό.
NOTICE_DIRECT_AWARD_KEY = "6"
NON_COMPETITIVE_PROCEDURE_KEYS = {NOTICE_DIRECT_AWARD_KEY, "12", "18"}
NON_COMPETITIVE_PROCEDURE_VALUES = {
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση",
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση (αρ.32/αρ.269)",
    "Διαδικασία άρθρου 128 του ν.4412/16",
}


# --- §4.4 Έλεγχος Benford (Nigrini) -----------------------------------------
# Δευτερεύον σήμα ΜΟΝΟ -- ΠΟΤΕ απόδειξη μόνο του, ΠΟΤΕ ένδειξη παραποίησης/απάτης
# στην ίδια τη γλώσσα της κάρτας/METHODOLOGY (νομική ουδετερότητα, MASTERPLAN Τμήμα 1).
# Θεσμικά κατώφλια (πολλά ποσά ακριβώς κάτω από όρια απευθείας ανάθεσης) παράγουν
# αναμενόμενες αποκλίσεις -- τεκμηριωμένο ρητά στη δημόσια σελίδα δείκτη.
BENFORD_D1_EXPECTED = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
BENFORD_D2_EXPECTED = {
    d2: sum(math.log10(1 + 1 / (10 * d1 + d2)) for d1 in range(1, 10))
    for d2 in range(0, 10)
}
# Nigrini (2012) MAD κατώφλια -- δημόσια, καθιερωμένα στατιστικά όρια (Digital
# Analysis Using Benford's Law), διαφορετικά για 1ο (9 κατηγορίες) και 2ο (10
# κατηγορίες) ψηφίο.
NIGRINI_MAD_BANDS_D1 = [
    (0.0060, "close"), (0.0120, "acceptable"), (0.0150, "marginal"), (float("inf"), "nonconformity"),
]
NIGRINI_MAD_BANDS_D2 = [
    (0.0080, "close"), (0.0100, "acceptable"), (0.0120, "marginal"), (float("inf"), "nonconformity"),
]


def _nigrini_band(mad: float, bands: list[tuple[float, str]]) -> str:
    for threshold, label in bands:
        if mad <= threshold:
            return label
    return "nonconformity"


def _digit_test(counts: pd.Series, expected: dict[int, float], n: int) -> tuple[float, float]:
    """Επιστρέφει (MAD, chi2) για ένα σύνολο counts ψηφίων έναντι του Benford."""
    k = len(expected)
    observed_pct = pd.Series({d: counts.get(d, 0) / n for d in expected})
    expected_pct = pd.Series(expected)
    mad = float((observed_pct - expected_pct).abs().mean())
    expected_counts = expected_pct * n
    chi2 = float((((counts.reindex(expected.keys(), fill_value=0) - expected_counts) ** 2) / expected_counts).sum())
    return round(mad, 6), round(chi2, 4)


def _first_digit(amount: float) -> int | None:
    if amount is None or amount != amount or amount <= 0:  # NaN-safe
        return None
    s = str(int(amount)) if amount == int(amount) else repr(float(amount))
    for ch in s:
        if ch.isdigit() and ch != "0":
            return int(ch)
    return None


def _first_two_digits(amount: float) -> tuple[int | None, int | None]:
    """(1ο, 2ο) ψηφίο· το 2ο μόνο για ποσά >= 10 (Nigrini απαίτηση -- αλλιώς
    ψηφία δεκαδικών θα μόλυναν το 2ο-ψηφίο τεστ)."""
    d1 = _first_digit(amount)
    if d1 is None:
        return None, None
    if amount < 10:
        return d1, None
    int_digits = str(int(amount))  # θετικός int -- ποτέ αρχικά μηδενικά
    d2 = int(int_digits[1]) if len(int_digits) >= 2 else None
    return d1, d2


def benford_indicator(payments: pd.DataFrame) -> pd.DataFrame:
    """§4.4 -- έλεγχος Benford (1ο/2ο ψηφίο) ανά φορέα, σε 2 επίπεδα period:
    ανά έτος ΚΑΙ 'all' (όλη η διαθέσιμη περίοδος) -- το N=300/έτος πιάνεται από
    λίγους φορείς, το 'all' δίνει νόημα σε μεσαίους φορείς επίσης (session 24
    [Διόρθωση πλάνου], SPRINT_E_PLAN.md §E6).

    Πεδίο ποσού: totalCostWithoutVAT (επιβεβαιωμένο 100% coverage στο payment
    schema, βλ. E3 gate check). Καθαρισμός: μόνο ποσά > 0 και πεπερασμένα· 1ο
    ψηφίο από string του ακέραιου μέρους (όχι log-tricks -- αποφυγή float
    artifacts)· 2ο ψηφίο μόνο για ποσά >= 10.
    """
    if payments.empty:
        return pd.DataFrame()

    df = payments.copy()
    df = df.dropna(subset=["vat_norm"])
    amounts = sanitize_value(pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce"))
    df["_amount"] = amounts
    valid_mask = amounts.notna() & (amounts > 0)
    df["_valid"] = valid_mask

    digits = df.loc[valid_mask, "_amount"].map(_first_two_digits)
    df.loc[valid_mask, "_d1"] = [d[0] for d in digits]
    df.loc[valid_mask, "_d2"] = [d[1] for d in digits]

    rows = []
    for vat, g in df.groupby("vat_norm"):
        name = mode_name(g[NAME_COL]) if NAME_COL in g.columns else None
        periods = [(str(int(y)), gy) for y, gy in g.groupby("_source_year")] if "_source_year" in g.columns else []
        periods.append(("all", g))
        for period, gp in periods:
            valid = gp[gp["_valid"]]
            n_valid = len(valid)
            coverage_pct = round(100.0 * n_valid / len(gp), 1) if len(gp) else None
            if n_valid < MIN_N_BENFORD:
                rows.append({
                    "vat": vat, "organization_name": name, "period": period,
                    "n_amounts": n_valid, "mad_d1": None, "mad_d2": None,
                    "chi2_d1": None, "chi2_d2": None,
                    "nigrini_band_d1": None, "nigrini_band_d2": None,
                    "coverage_pct": coverage_pct,
                    "note": f"ανεπαρκή δεδομένα (N<{MIN_N_BENFORD})",
                })
                continue
            d1_counts = valid["_d1"].value_counts()
            mad_d1, chi2_d1 = _digit_test(d1_counts, BENFORD_D1_EXPECTED, n_valid)
            d2_valid = valid[valid["_d2"].notna()]
            n_d2 = len(d2_valid)
            if n_d2 >= MIN_N_BENFORD:
                d2_counts = d2_valid["_d2"].value_counts()
                mad_d2, chi2_d2 = _digit_test(d2_counts, BENFORD_D2_EXPECTED, n_d2)
                band_d2 = _nigrini_band(mad_d2, NIGRINI_MAD_BANDS_D2)
            else:
                mad_d2, chi2_d2, band_d2 = None, None, None
            rows.append({
                "vat": vat, "organization_name": name, "period": period,
                "n_amounts": n_valid, "mad_d1": mad_d1, "mad_d2": mad_d2,
                "chi2_d1": chi2_d1, "chi2_d2": chi2_d2,
                "nigrini_band_d1": _nigrini_band(mad_d1, NIGRINI_MAD_BANDS_D1),
                "nigrini_band_d2": band_d2,
                "coverage_pct": coverage_pct,
                "note": None,
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["vat", "period"])


def is_competitive_procedure(df: pd.DataFrame) -> pd.Series:
    """Κοινό φίλτρο ανταγωνιστικών διαδικασιών για §4.3 και §4.7."""
    key = df.get("typeOfProcedure.key", pd.Series([None] * len(df), index=df.index)).astype(str)
    value = df.get("typeOfProcedure.value", pd.Series([None] * len(df), index=df.index)).astype(str).str.strip()
    return ~key.isin(NON_COMPETITIVE_PROCEDURE_KEYS) & ~value.isin(NON_COMPETITIVE_PROCEDURE_VALUES)


def load_entity_table() -> pd.DataFrame:
    path = OUT_DIR / "entities.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"vat": str})


def mode_name(names: pd.Series) -> str | None:
    """Πιο συχνό καταγεγραμμένο όνομα μέσα σε μια ομάδα ίδιου ΑΦΜ (μόνο για εμφάνιση)."""
    s = names.dropna()
    if s.empty:
        return None
    return s.mode(dropna=True).iloc[0]


def direct_award_rate(auctions: pd.DataFrame) -> pd.DataFrame:
    df = auctions.copy()
    df["value"] = sanitize_value(pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    ))
    df["is_direct"] = df["procedureType.key"].astype(str) == DIRECT_AWARD_KEY
    df = df.dropna(subset=["vat_norm"])

    rows = []
    for (vat, year), g in df.groupby(["vat_norm", "_source_year"]):
        n_total = len(g)
        n_direct = int(g["is_direct"].sum())
        val_total = g["value"].sum(skipna=True)
        val_direct = g.loc[g["is_direct"], "value"].sum(skipna=True)
        rows.append(
            {
                "organization_vat": vat,
                "organization_name": mode_name(g["organization.value"]),
                "year": year,
                "n_total": n_total,
                "n_direct": n_direct,
                "da_count_pct": round(100.0 * n_direct / n_total, 2) if n_total else None,
                "value_total": val_total,
                "value_direct": val_direct,
                "da_value_pct": round(100.0 * val_direct / val_total, 2) if val_total else None,
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def hhi_concentration(contracts: pd.DataFrame) -> pd.DataFrame:
    df = contracts.copy()
    df["value"] = sanitize_value(pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    ))

    def parse_members(raw: str | None) -> list[dict]:
        if not raw:
            return []
        try:
            members = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return members or []

    # B2 (tech_report v2 / απόφαση #6): το HHI πιστώνει όλη την αξία στον
    # ΠΡΩΤΟ ανάδοχο (members[0]) όταν μια σύμβαση έχει πολλαπλά μέλη
    # (κοινοπραξία) -- δεν υπάρχει στα δεδομένα ΚΗΜΔΗΣ επιμερισμός αξίας ανά
    # μέλος. Αυτή είναι ρητή, δηλωμένη παραδοχή (βλ. METHODOLOGY §4.2)· το
    # `pct_multi_member` παρακάτω μετρά πόσο συχνά ενεργοποιείται, ώστε να
    # φαίνεται το μέγεθος της πιθανής μεροληψίας.
    df["members"] = df["contractingDataDetails.contractingMembersDataList"].map(parse_members)
    df["contractor_vat"] = df["members"].map(lambda m: m[0].get("vatNumber") if m else None)
    df["is_multi_member"] = df["members"].map(len) > 1
    df = df.dropna(subset=["vat_norm"])

    rows = []
    for (vat, year), g in df.groupby(["vat_norm", "_source_year"]):
        name = mode_name(g["organization.value"])
        g = g.dropna(subset=["contractor_vat", "value"])
        n = len(g)
        pct_multi_member = round(100.0 * g["is_multi_member"].sum() / n, 2) if n else None
        if n < MIN_N_HHI:
            rows.append(
                {
                    "organization_vat": vat,
                    "organization_name": name,
                    "year": year,
                    "n_contracts": n,
                    "hhi": None,
                    "top1_share": None,
                    "pct_multi_member": pct_multi_member,
                    "note": f"ανεπαρκή δεδομένα (N<{MIN_N_HHI})",
                }
            )
            continue
        total_value = g["value"].sum()
        if total_value <= 0:
            continue
        shares = g.groupby("contractor_vat")["value"].sum() / total_value
        hhi = float((shares**2).sum())
        rows.append(
            {
                "organization_vat": vat,
                "organization_name": name,
                "year": year,
                "n_contracts": n,
                "hhi": round(hhi, 4),
                "top1_share": round(float(shares.max()), 4),
                "pct_multi_member": pct_multi_member,
                "note": None,
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def single_bid_rate(contracts: pd.DataFrame) -> pd.DataFrame:
    """§4.3 -- ποσοστό ανταγωνιστικών συμβάσεων με μία μόνο προσφορά.

    Το `bidsSubmitted` είναι άδειο έως 2025-03 και πλήρες από 2025-04 στα raw
    contract δεδομένα, άρα ο δείκτης ξεκινά ρητά από τον πρώτο πλήρη μήνα.
    Τιμές εκτός [1, BIDS_SUBMITTED_MAX] θεωρούνται data errors και εξαιρούνται
    από τον παρονομαστή, με δημοσιευμένο μετρητή outliers.
    """
    if contracts.empty:
        return pd.DataFrame()

    df = contracts.copy()
    after_cutover = (
        (df["_source_year"] > SINGLE_BID_CUTOVER[0])
        | ((df["_source_year"] == SINGLE_BID_CUTOVER[0]) & (df["_source_month"] >= SINGLE_BID_CUTOVER[1]))
    )
    df = df[after_cutover & is_competitive_procedure(df)].copy()
    df = df.dropna(subset=["vat_norm"])
    if df.empty:
        return pd.DataFrame()

    raw_bids = pd.to_numeric(df.get("bidsSubmitted"), errors="coerce")
    fallback = pd.to_numeric(df.get("contract.bidsSubmitted"), errors="coerce")
    df["bids_submitted"] = raw_bids.fillna(fallback)
    df["bids_outlier"] = (df["bids_submitted"] < 1) | (df["bids_submitted"] > BIDS_SUBMITTED_MAX)
    df["bids_valid"] = df["bids_submitted"].notna() & ~df["bids_outlier"]

    rows = []
    for (vat, year), g in df.groupby(["vat_norm", "_source_year"]):
        name = mode_name(g["organization.value"])
        valid = g.loc[g["bids_valid"]]
        n_competitive = len(g)
        n_valid = len(valid)
        n_single_bid = int((valid["bids_submitted"] == 1).sum())
        n_bids_outliers = int(g["bids_outlier"].fillna(False).sum())
        coverage_pct = round(100.0 * n_valid / n_competitive, 1) if n_competitive else None
        if n_valid < MIN_N_SINGLE_BID:
            rows.append(
                {
                    "organization_vat": vat,
                    "organization_name": name,
                    "year": year,
                    "n_competitive": n_competitive,
                    "n_with_bids": n_valid,
                    "n_single_bid": n_single_bid,
                    "single_bid_pct": None,
                    "coverage_pct": coverage_pct,
                    "n_bids_outliers": n_bids_outliers,
                    "note": f"ανεπαρκή δεδομένα (N<{MIN_N_SINGLE_BID})",
                }
            )
            continue
        rows.append(
            {
                "organization_vat": vat,
                "organization_name": name,
                "year": year,
                "n_competitive": n_competitive,
                "n_with_bids": n_valid,
                "n_single_bid": n_single_bid,
                "single_bid_pct": round(100.0 * n_single_bid / n_valid, 2),
                "coverage_pct": coverage_pct,
                "n_bids_outliers": n_bids_outliers,
                "note": None,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def _threshold_for(contract_type: str | None, when: pd.Timestamp) -> float | None:
    if pd.isna(when):
        return None
    period_from_2021 = when.date() >= THRESHOLD_CHANGE_DATE
    is_works = contract_type in WORKS_STUDIES_TYPES
    if period_from_2021:
        return 60_000.0 if is_works else 30_000.0
    return 20_000.0


def bid_splitting(auctions: pd.DataFrame) -> pd.DataFrame:
    """§4.5 -- density ratio αναθέσεων γύρω από το νόμιμο όριο απευθείας ανάθεσης.

    Ζώνη κάτω: [0.8×όριο, όριο). Ζώνη πάνω: [όριο, 1.2×όριο].
    ratio = πλήθος(κάτω) / πλήθος(πάνω) -- συστηματικά >1 σε έναν φορέα/περίοδο
    είναι το red flag (κατακερματισμός συμβάσεων για αποφυγή ανταγωνιστικής
    διαδικασίας), όχι μία μεμονωμένη τιμή.
    """
    df = auctions.copy()
    df["value"] = sanitize_value(pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    ))
    df["submission_date"] = pd.to_datetime(df.get("submissionDate"), errors="coerce")
    df = df.dropna(subset=["value", "submission_date"])

    df["threshold"] = [
        _threshold_for(ct, when) for ct, when in zip(df.get("contractType.value"), df["submission_date"])
    ]
    df = df.dropna(subset=["threshold"])
    df["period"] = df["submission_date"].dt.date.map(
        lambda d: "2021-06+" if d >= THRESHOLD_CHANGE_DATE else "pre-2021-06"
    )
    df = df.dropna(subset=["vat_norm"])

    df["band"] = None
    below = (df["value"] >= 0.8 * df["threshold"]) & (df["value"] < df["threshold"])
    above = (df["value"] >= df["threshold"]) & (df["value"] <= 1.2 * df["threshold"])
    df.loc[below, "band"] = "below"
    df.loc[above, "band"] = "above"
    df = df.dropna(subset=["band"])

    rows = []
    group_cols = ["vat_norm", "period"]
    for (vat, period), g in df.groupby(group_cols):
        name = mode_name(g["organization.value"])
        n_below = int((g["band"] == "below").sum())
        n_above = int((g["band"] == "above").sum())
        if n_below < MIN_N_BID_SPLIT or n_above < MIN_N_BID_SPLIT:
            rows.append(
                {
                    "organization_vat": vat,
                    "organization_name": name,
                    "period": period,
                    "n_below": n_below,
                    "n_above": n_above,
                    "density_ratio": None,
                    "note": f"ανεπαρκή δεδομένα (ζώνη<{MIN_N_BID_SPLIT})",
                }
            )
            continue
        rows.append(
            {
                "organization_vat": vat,
                "organization_name": name,
                "period": period,
                "n_below": n_below,
                "n_above": n_above,
                "density_ratio": round(n_below / n_above, 3),
                "note": None,
            }
        )
    return pd.DataFrame(rows).sort_values(["period", "organization_vat"])


def discount_rate(notices: pd.DataFrame, auctions: pd.DataFrame) -> pd.DataFrame:
    """§4.6 -- ποσοστό διαδικασιών με ~0% έκπτωση προκήρυξη -> ανάθεση.

    Σύνδεση notice.referenceNumber <-> auction.noticeRefNo. Η ζευγοποίηση
    notice<->contract μέσω contract.noticeReferenceNumber είναι σχεδόν κενή
    στα δεδομένα (<0.1% των εγγραφών, ελέγχθηκε επί 2020-01), ενώ η notice
    <-> auction είναι σαφώς πιο πλήρης (~70% των auctions με noticeRefNo
    ταιριάζουν με ήδη κατεβασμένο notice). Χρησιμοποιούμε την τιμή
    κατακύρωσης (auction) ως proxy της τελικής τιμής σύμβασης· η κάλυψη
    (coverage) δημοσιεύεται πάντα δίπλα στον δείκτη, όπως το §4.3.
    """
    if notices.empty or auctions.empty:
        return pd.DataFrame()

    notice_est = notices.copy()
    notice_est["est_value"] = sanitize_value(pd.to_numeric(notice_est.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(notice_est.get("totalCostWithVAT"), errors="coerce")
    ).fillna(pd.to_numeric(notice_est.get("budget"), errors="coerce")))
    notice_lookup = notice_est.set_index("referenceNumber")["est_value"]

    auc = auctions.copy()
    auc["final_value"] = sanitize_value(pd.to_numeric(auc.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(auc.get("totalCostWithVAT"), errors="coerce")
    ))
    auc = auc.dropna(subset=["noticeRefNo"])
    auc["est_value"] = auc["noticeRefNo"].map(notice_lookup)
    auc = auc.dropna(subset=["est_value", "final_value"])
    auc = auc[auc["est_value"] > 0]
    auc["discount_pct"] = 100.0 * (auc["est_value"] - auc["final_value"]) / auc["est_value"]
    auc["near_zero"] = auc["discount_pct"].abs() <= 1.0  # ±1% θεωρείται "μηδενική έκπτωση"
    auc = auc.dropna(subset=["vat_norm"])

    rows = []
    for (vat, year), g in auc.groupby(["vat_norm", "_source_year"]):
        name = mode_name(g["organization.value"])
        n = len(g)
        if n < MIN_N_DISCOUNT:
            rows.append(
                {
                    "organization_vat": vat,
                    "organization_name": name,
                    "year": year,
                    "n_linked": n,
                    "median_discount_pct": None,
                    "pct_near_zero_discount": None,
                    "note": f"ανεπαρκή δεδομένα (N<{MIN_N_DISCOUNT})",
                }
            )
            continue
        rows.append(
            {
                "organization_vat": vat,
                "organization_name": name,
                "year": year,
                "n_linked": n,
                "median_discount_pct": round(float(g["discount_pct"].median()), 2),
                "pct_near_zero_discount": round(100.0 * g["near_zero"].mean(), 2),
                "note": None,
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def deadline_indicator(notices: pd.DataFrame) -> pd.DataFrame:
    """§4.7 -- διάμεση προθεσμία υποβολής προσφορών ανά φορέα/έτος.

    `days = finalSubmissionDate − submissionDate` (πεδία notice), σε ημερήσια
    ανάλυση (και τα δύο πεδία κανονικοποιούνται στο ημερολογιακό τους date πριν
    την αφαίρεση -- το submissionDate έχει ώρα/λεπτό ενώ το finalSubmissionDate
    είναι σχεδόν πάντα μεσάνυχτα, οπότε η ωριαία διαφορά θα ήταν θόρυβος, όχι
    σήμα). Αποκλείονται οι απευθείας αναθέσεις (βλ. σχόλιο πιο πάνω) και οι
    εγγραφές με άκυρη/αρνητική διάρκεια (data error -- π.χ. finalSubmissionDate
    πριν το submissionDate). Το `pct_short_deadline` της METHODOLOGY §4.7 ΔΕΝ
    δημοσιεύεται εδώ (εκκρεμεί νομική επιβεβαίωση ορίων ανά τύπο διαδικασίας).
    """
    if notices.empty:
        return pd.DataFrame()

    df = notices.copy()
    df = df[is_competitive_procedure(df)]
    df = df.dropna(subset=["vat_norm"])

    n_competitive = df.groupby(["vat_norm", "_source_year"]).size()

    submission = pd.to_datetime(df["submissionDate"], errors="coerce").dt.normalize()
    final = pd.to_datetime(df["finalSubmissionDate"], errors="coerce").dt.normalize()
    df["deadline_days"] = (final - submission).dt.days
    df["valid"] = df["deadline_days"].notna() & (df["deadline_days"] >= 0)

    rows = []
    for (vat, year), g in df.groupby(["vat_norm", "_source_year"]):
        name = mode_name(g["organization.value"])
        n_total_competitive = int(n_competitive.get((vat, year), len(g)))
        valid = g.loc[g["valid"]]
        n_valid = len(valid)
        coverage_pct = round(100.0 * n_valid / n_total_competitive, 1) if n_total_competitive else None
        if n_valid < MIN_N_DEADLINE:
            rows.append(
                {
                    "vat": vat,
                    "organization_name": name,
                    "year": year,
                    "n_notices": n_valid,
                    "median_deadline_days": None,
                    "pct_short_deadline": None,
                    "coverage_pct": coverage_pct,
                    "note": f"ανεπαρκή δεδομένα (N<{MIN_N_DEADLINE})",
                }
            )
            continue
        rows.append(
            {
                "vat": vat,
                "organization_name": name,
                "year": year,
                "n_notices": n_valid,
                "median_deadline_days": round(float(valid["deadline_days"].median()), 1),
                "pct_short_deadline": None,  # εκκρεμεί νομική επιβεβαίωση, βλ. METHODOLOGY §4.7
                "coverage_pct": coverage_pct,
                "note": None,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["year", "vat"])


def _yearly_reverse_percentile(df: pd.DataFrame, value_col: str) -> pd.Series:
    """0..1 flag όπου χαμηλότερη τιμή σημαίνει υψηλότερο ρίσκο μέσα στο ίδιο έτος."""
    out = pd.Series(index=df.index, dtype=float)
    for _, g in df.groupby("year"):
        vals = g[value_col].dropna()
        if vals.empty:
            continue
        pct = vals.rank(pct=True, method="average")
        out.loc[pct.index] = 1.0 - pct
    return out


def composite_indicator(
    da: pd.DataFrame,
    hhi: pd.DataFrame,
    dr: pd.DataFrame,
    dl: pd.DataFrame,
    sb: pd.DataFrame,
) -> pd.DataFrame:
    """§4.8 -- μη σταθμισμένος μέσος των διαθέσιμων δημοσιευμένων flags.

    Ρητά δεν περιλαμβάνει bid-splitting (§4.5) ούτε pct_short_deadline (§4.7),
    επειδή δεν δημοσιεύονται πριν τη νομική επιβεβαίωση.
    """
    if da.empty:
        return pd.DataFrame()

    merged = da.rename(columns={"organization_vat": "vat", "organization_name": "name"})[
        ["vat", "name", "year", "da_count_pct", "da_value_pct", "n_total"]
    ].copy()
    if not hhi.empty:
        merged = merged.merge(
            hhi.rename(columns={"organization_vat": "vat"})[["vat", "year", "hhi", "n_contracts"]],
            on=["vat", "year"],
            how="left",
        )
    if not dr.empty:
        merged = merged.merge(
            dr.rename(columns={"organization_vat": "vat"})[["vat", "year", "pct_near_zero_discount", "n_linked"]],
            on=["vat", "year"],
            how="left",
        )
    if not dl.empty:
        deadline = dl[["vat", "year", "median_deadline_days", "n_notices"]].copy()
        deadline["deadline_flag"] = _yearly_reverse_percentile(deadline, "median_deadline_days")
        merged = merged.merge(deadline, on=["vat", "year"], how="left")
    if not sb.empty:
        merged = merged.merge(
            sb.rename(columns={"organization_vat": "vat"})[["vat", "year", "single_bid_pct", "n_with_bids"]],
            on=["vat", "year"],
            how="left",
        )

    merged["flag_da_count"] = pd.to_numeric(merged["da_count_pct"], errors="coerce") / 100.0
    merged["flag_da_value"] = pd.to_numeric(merged["da_value_pct"], errors="coerce") / 100.0
    merged["flag_hhi"] = pd.to_numeric(merged.get("hhi"), errors="coerce")
    merged["flag_discount"] = pd.to_numeric(merged.get("pct_near_zero_discount"), errors="coerce") / 100.0
    merged["flag_single_bid"] = pd.to_numeric(merged.get("single_bid_pct"), errors="coerce") / 100.0

    flag_cols = [
        "flag_da_count",
        "flag_da_value",
        "flag_hhi",
        "flag_discount",
        "deadline_flag",
        "flag_single_bid",
    ]
    merged["n_flags"] = merged[flag_cols].notna().sum(axis=1)
    merged["composite_score"] = merged[flag_cols].mean(axis=1, skipna=True).round(4)

    out_cols = [
        "vat",
        "name",
        "year",
        "composite_score",
        "n_flags",
        "flag_da_count",
        "flag_da_value",
        "flag_hhi",
        "flag_discount",
        "deadline_flag",
        "flag_single_bid",
        "n_total",
        "n_contracts",
        "n_linked",
        "n_notices",
        "n_with_bids",
    ]
    for col in out_cols:
        if col not in merged.columns:
            merged[col] = None
    return merged[out_cols].sort_values(["year", "vat"])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    auctions = load_entity("auction", columns=AUCTION_COLS)
    contracts = load_entity("contract", columns=CONTRACT_COLS)
    notices = load_entity("notice", columns=NOTICE_COLS)
    payments = load_entity("payment", columns=PAYMENT_COLS)

    if auctions.empty:
        print("Δεν βρέθηκαν δεδομένα auction σε data/raw/. Τρέξε πρώτα backfill/fetch.")
        return

    # A3: προτιμάται ο persisted resolver (build_entity_table.py) -- ίδιος
    # ανά όλα τα στάδια του pipeline. Fallback σε τοπικό χτίσιμο μόνο αν δεν
    # έχει τρέξει ακόμα το build_entity_table.py (πρώτο run/standalone use).
    resolver = load_vat_resolver()
    if resolver is None:
        print("(Δεν βρέθηκε data/processed/vat_resolver.csv -- τρέξε πρώτα build_entity_table.py. "
              "Χτίζεται προσωρινός resolver τοπικά.)")
        resolver = build_vat_resolver([df for df in (auctions, contracts, notices) if not df.empty])
    auctions["vat_norm"] = resolve_vat(auctions, resolver)
    if not contracts.empty:
        contracts["vat_norm"] = resolve_vat(contracts, resolver)
    if not notices.empty:
        notices["vat_norm"] = resolve_vat(notices, resolver)
    if not payments.empty:
        payments["vat_norm"] = resolve_vat(payments, resolver)

    entities = load_entity_table()
    if entities.empty:
        print("(Δεν βρέθηκε data/processed/entities.csv -- τρέξε πρώτα build_entity_table.py "
              "για τύπο/NUTS ανά φορέα. Οι δείκτες παρακάτω δεν επηρεάζονται, δουλεύουν απευθείας "
              "από τα raw δεδομένα.)")

    da = direct_award_rate(auctions)
    da_path = OUT_DIR / "indicator_direct_award.csv"
    da.to_csv(da_path, index=False, encoding="utf-8-sig")
    print(f"Direct-award indicator -> {da_path} ({len(da)} γραμμές φορέα/έτους)")

    print("\n=== Εθνικό σύνολο ανά έτος (sanity check έναντι EU Scoreboard ~58.2% single-bid, "
          "Vouliwatch 62.4% απευθείας σε συμβουλευτικές) ===")
    national = (
        auctions.assign(is_direct=lambda d: d["procedureType.key"].astype(str) == DIRECT_AWARD_KEY)
        .groupby("_source_year")
        .agg(n_total=("is_direct", "size"), n_direct=("is_direct", "sum"))
    )
    national["da_count_pct"] = round(100.0 * national["n_direct"] / national["n_total"], 2)
    print(national.to_string())

    hhi = pd.DataFrame()
    sb = pd.DataFrame()
    if not contracts.empty:
        hhi = hhi_concentration(contracts)
        hhi_path = OUT_DIR / "indicator_hhi.csv"
        hhi.to_csv(hhi_path, index=False, encoding="utf-8-sig")
        print(f"\nHHI indicator -> {hhi_path} ({len(hhi)} γραμμές φορέα/έτους)")

        sb = single_bid_rate(contracts)
        if not sb.empty:
            sb_path = OUT_DIR / "indicator_single_bid.csv"
            sb.to_csv(sb_path, index=False, encoding="utf-8-sig")
            print(f"\nSingle-bid indicator -> {sb_path} ({len(sb)} γραμμές φορέα/έτους, "
                  f"cutover {SINGLE_BID_CUTOVER[0]}-{SINGLE_BID_CUTOVER[1]:02d}, "
                  f"{int(sb['n_bids_outliers'].sum())} outlier bidsSubmitted εξαιρέθηκαν)")
        else:
            print("\n(Ανεπαρκή δεδομένα για single-bid -- παραλείπεται.)")
    else:
        print("\n(Δεν βρέθηκαν δεδομένα contract -- παραλείπονται HHI και single-bid.)")

    bs = bid_splitting(auctions)
    if not bs.empty:
        bs_path = OUT_DIR / "indicator_bid_splitting.csv"
        bs.to_csv(bs_path, index=False, encoding="utf-8-sig")
        print(f"\nBid-splitting indicator -> {bs_path} ({len(bs)} γραμμές φορέα/περιόδου)")
    else:
        print("\n(Ανεπαρκή δεδομένα για bid-splitting -- παραλείπεται.)")

    dr = pd.DataFrame()
    if not notices.empty:
        dr = discount_rate(notices, auctions)
        if not dr.empty:
            dr_path = OUT_DIR / "indicator_discount_rate.csv"
            dr.to_csv(dr_path, index=False, encoding="utf-8-sig")
            n_linked_total = int(dr["n_linked"].sum())
            n_auctions_with_notice = int(auctions["noticeRefNo"].notna().sum()) if "noticeRefNo" in auctions else 0
            print(f"\nDiscount-rate indicator -> {dr_path} ({len(dr)} γραμμές φορέα/έτους, "
                  f"{n_linked_total} συνδεδεμένες διαδικασίες από {n_auctions_with_notice} auctions "
                  f"με noticeRefNo)")
        else:
            print("\n(Ανεπαρκή δεδομένα για discount-rate -- παραλείπεται.)")
    else:
        print("\n(Δεν βρέθηκαν δεδομένα notice -- παραλείπεται το discount-rate.)")

    dl = pd.DataFrame()
    if not notices.empty:
        dl = deadline_indicator(notices)
        if not dl.empty:
            dl_path = OUT_DIR / "indicator_deadlines.csv"
            dl.to_csv(dl_path, index=False, encoding="utf-8-sig")
            competitive_mask = is_competitive_procedure(notices)
            n_excluded_non_competitive = int((~competitive_mask).sum())
            n_competitive_total = int(competitive_mask.sum())
            competitive = notices[competitive_mask]
            sub = pd.to_datetime(competitive["submissionDate"], errors="coerce").dt.normalize()
            fin = pd.to_datetime(competitive["finalSubmissionDate"], errors="coerce").dt.normalize()
            diff_days = (fin - sub).dt.days
            n_negative = int((diff_days < 0).sum())
            n_nat = int(diff_days.isna().sum())
            print(f"\nDeadline indicator -> {dl_path} ({len(dl)} γραμμές φορέα/έτους, "
                  f"{n_competitive_total} ανταγωνιστικές διαδικασίες από {len(notices)} notices "
                  f"[{n_excluded_non_competitive} μη-ανταγωνιστικές/ειδικές διαδικασίες αποκλείστηκαν], "
                  f"{n_negative} αρνητικές διάρκειες + {n_nat} άκυρες ημερομηνίες αποκλείστηκαν)")
        else:
            print("\n(Ανεπαρκή δεδομένα για deadline indicator -- παραλείπεται.)")
    else:
        print("\n(Δεν βρέθηκαν δεδομένα notice -- παραλείπεται το deadline indicator.)")

    if not payments.empty:
        # #16 (CHECK 2026-07-11): εθνικό-επίπεδο μέτρηση γραμμών payment ΧΩΡΙΣ
        # vat_norm -- το ανά-φορέα coverage_pct του Benford μετρά μόνο εγκυρότητα
        # ποσών (οι γραμμές χωρίς ΑΦΜ πέφτουν πριν από κάθε παρονομαστή, βλ.
        # METHODOLOGY §4.4). Αν η κάλυψη ΑΦΜ πέσει σε μελλοντικούς μήνες, εδώ
        # θα φανεί (E3 gate: μετρημένο 100% VAT coverage).
        pct_rows_without_vat = round(100.0 * payments["vat_norm"].isna().mean(), 2)
        print(f"\nPayment: {len(payments)} γραμμές, {pct_rows_without_vat}% χωρίς επιλύσιμο ΑΦΜ "
              "(εξαιρούνται από το Benford πριν από κάθε παρονομαστή)")
        bf = benford_indicator(payments)
        if not bf.empty:
            bf_path = OUT_DIR / "indicator_benford.csv"
            bf.to_csv(bf_path, index=False, encoding="utf-8-sig")
            n_scored = int(bf["mad_d1"].notna().sum())
            print(f"\nBenford indicator -> {bf_path} ({len(bf)} γραμμές φορέα/περιόδου, "
                  f"{n_scored} με επαρκές N>={MIN_N_BENFORD} για βαθμολόγηση)")
        else:
            print("\n(Ανεπαρκή δεδομένα για Benford indicator -- παραλείπεται.)")
    else:
        print("\n(Δεν βρέθηκαν δεδομένα payment -- παραλείπεται το Benford indicator -- εκκρεμεί E3 backfill.)")

    comp = composite_indicator(da, hhi, dr, dl, sb)
    if not comp.empty:
        comp_path = OUT_DIR / "indicator_composite.csv"
        comp.to_csv(comp_path, index=False, encoding="utf-8-sig")
        print(f"\nComposite indicator -> {comp_path} ({len(comp)} γραμμές φορέα/έτους, "
              "χωρίς bid-splitting/pct_short_deadline)")


if __name__ == "__main__":
    main()
