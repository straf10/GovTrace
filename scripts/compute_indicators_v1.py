"""Φάση 1: Δείκτες v1 ανά φορέα/έτος (METHODOLOGY.md §4).

Διαβάζει ό,τι raw δεδομένα υπάρχουν ήδη σε data/raw/ (auction_*, contract_*,
notice_*) και υπολογίζει:
  - §4.1 DA_count, DA_value: μερίδιο απευθείας αναθέσεων σε πλήθος/αξία
  - §4.2 HHI, top1: συγκέντρωση αναδόχων στις συμβάσεις (ελάχιστο N=10)
  - §4.5 bid-splitting: density ratio αναθέσεων γύρω από το νόμιμο όριο
    απευθείας ανάθεσης (ελάχιστο N=5 σε κάθε ζώνη)
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
from datetime import date
from pathlib import Path

import pandas as pd

from kimdis_data import PROCESSED_DIR, load_entity

RAW_DIR = Path("data/raw")
OUT_DIR = PROCESSED_DIR
DIRECT_AWARD_KEY = "6"  # procedureType.key == "6" -> Απευθείας ανάθεση (επιβεβαιωμένο, βλ. MEMORY.md §4)
MIN_N_HHI = 10  # METHODOLOGY §4.2
MIN_N_BID_SPLIT = 5  # METHODOLOGY §4.5 -- ελάχιστο πλήθος ανά ζώνη για δημοσίευση
MIN_N_DISCOUNT = 5  # METHODOLOGY §4.6

# --- §4.5 Νόμιμα όρια απευθείας ανάθεσης ανά περίοδο (ν. 4412/2016, άρθρο 118) ---
# Πριν 1/6/2021: ενιαίο όριο 20.000€ χωρίς ΦΠΑ (όλες οι κατηγορίες).
# Από 1/6/2021 (ν. 4782/2021): 30.000€ προμήθειες/υπηρεσίες, 60.000€ έργα/μελέτες/
# τεχνικές υπηρεσίες. Πηγή: https://www.promitheies.gr/blog/apeutheias-anatheseis-dhmosiwn-simvasewn
# ΣΗΜΕΙΩΣΗ: πρώτο πέρασμα κατηγοριοποίησης βάσει contractType.value· χρειάζεται
# νομική επιβεβαίωση πριν δημοσιευθεί οριστικά (βλ. METHODOLOGY.md changelog).
THRESHOLD_CHANGE_DATE = date(2021, 6, 1)
WORKS_STUDIES_TYPES = {"Έργα", "Μελέτες", "Τεχνικές ή λοιπές συναφείς υπηρεσίες"}


def load_entity_table() -> pd.DataFrame:
    path = OUT_DIR / "entities.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"vat": str})


def primary_vat(vats: pd.Series) -> str | None:
    """Πιο συχνό ΑΦΜ μέσα σε μια ομάδα ίδιου ονόματος φορέα.

    Ο ίδιος φορέας εμφανίζεται στο ΚΗΜΔΗΣ με πολλαπλές παραλλαγές ΑΦΜ
    (typos κατά την καταχώρηση, π.χ. 090169846/09016846/90169846 για το
    ίδιο Υπουργείο) -- γι' αυτό ομαδοποιούμε δείκτες ανά *όνομα* φορέα, όχι
    ανά ΑΦΜ, και κρατάμε εδώ μόνο το πιο συχνό ΑΦΜ ως ενδεικτικό/για σύνδεση
    με entities.csv. ΠΡΟΣΟΧΗ: αν ποτέ υπάρξουν δύο πραγματικά διαφορετικοί
    φορείς με πανομοιότυπο κατεγραμμένο όνομα, θα συγχωνευτούν λανθασμένα
    εδώ -- βλ. σημείωση ελέγχου στο PLAN.md.
    """
    counts = vats.dropna().value_counts()
    return counts.index[0] if not counts.empty else None


def direct_award_rate(auctions: pd.DataFrame) -> pd.DataFrame:
    df = auctions.copy()
    df["value"] = pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    )
    df["is_direct"] = df["procedureType.key"].astype(str) == DIRECT_AWARD_KEY

    rows = []
    for (name, year), g in df.groupby(["organization.value", "_source_year"]):
        n_total = len(g)
        n_direct = int(g["is_direct"].sum())
        val_total = g["value"].sum(skipna=True)
        val_direct = g.loc[g["is_direct"], "value"].sum(skipna=True)
        rows.append(
            {
                "organization_vat": primary_vat(g["organizationVatNumber"]),
                "organization_name": name,
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
    df["value"] = pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    )

    def first_contractor_vat(raw: str | None) -> str | None:
        if not raw:
            return None
        try:
            members = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        if not members:
            return None
        return members[0].get("vatNumber")

    df["contractor_vat"] = df["contractingDataDetails.contractingMembersDataList"].map(first_contractor_vat)

    rows = []
    for (name, year), g in df.groupby(["organization.value", "_source_year"]):
        g = g.dropna(subset=["contractor_vat", "value"])
        n = len(g)
        if n < MIN_N_HHI:
            rows.append(
                {
                    "organization_vat": primary_vat(g["organizationVatNumber"]),
                    "organization_name": name,
                    "year": year,
                    "n_contracts": n,
                    "hhi": None,
                    "top1_share": None,
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
                "organization_vat": primary_vat(g["organizationVatNumber"]),
                "organization_name": name,
                "year": year,
                "n_contracts": n,
                "hhi": round(hhi, 4),
                "top1_share": round(float(shares.max()), 4),
                "note": None,
            }
        )
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
    df["value"] = pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    )
    df["submission_date"] = pd.to_datetime(df.get("submissionDate"), errors="coerce")
    df = df.dropna(subset=["value", "submission_date"])

    df["threshold"] = [
        _threshold_for(ct, when) for ct, when in zip(df.get("contractType.value"), df["submission_date"])
    ]
    df = df.dropna(subset=["threshold"])
    df["period"] = df["submission_date"].dt.date.map(
        lambda d: "2021-06+" if d >= THRESHOLD_CHANGE_DATE else "pre-2021-06"
    )

    df["band"] = None
    below = (df["value"] >= 0.8 * df["threshold"]) & (df["value"] < df["threshold"])
    above = (df["value"] >= df["threshold"]) & (df["value"] <= 1.2 * df["threshold"])
    df.loc[below, "band"] = "below"
    df.loc[above, "band"] = "above"
    df = df.dropna(subset=["band"])

    rows = []
    group_cols = ["organization.value", "period"]
    for (name, period), g in df.groupby(group_cols):
        n_below = int((g["band"] == "below").sum())
        n_above = int((g["band"] == "above").sum())
        if n_below < MIN_N_BID_SPLIT or n_above < MIN_N_BID_SPLIT:
            rows.append(
                {
                    "organization_vat": primary_vat(g["organizationVatNumber"]),
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
                "organization_vat": primary_vat(g["organizationVatNumber"]),
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
    notice_est["est_value"] = pd.to_numeric(notice_est.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(notice_est.get("totalCostWithVAT"), errors="coerce")
    ).fillna(pd.to_numeric(notice_est.get("budget"), errors="coerce"))
    notice_lookup = notice_est.set_index("referenceNumber")["est_value"]

    auc = auctions.copy()
    auc["final_value"] = pd.to_numeric(auc.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(auc.get("totalCostWithVAT"), errors="coerce")
    )
    auc = auc.dropna(subset=["noticeRefNo"])
    auc["est_value"] = auc["noticeRefNo"].map(notice_lookup)
    auc = auc.dropna(subset=["est_value", "final_value"])
    auc = auc[auc["est_value"] > 0]
    auc["discount_pct"] = 100.0 * (auc["est_value"] - auc["final_value"]) / auc["est_value"]
    auc["near_zero"] = auc["discount_pct"].abs() <= 1.0  # ±1% θεωρείται "μηδενική έκπτωση"

    rows = []
    for (name, year), g in auc.groupby(["organization.value", "_source_year"]):
        n = len(g)
        if n < MIN_N_DISCOUNT:
            rows.append(
                {
                    "organization_vat": primary_vat(g["organizationVatNumber"]),
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
                "organization_vat": primary_vat(g["organizationVatNumber"]),
                "organization_name": name,
                "year": year,
                "n_linked": n,
                "median_discount_pct": round(float(g["discount_pct"].median()), 2),
                "pct_near_zero_discount": round(100.0 * g["near_zero"].mean(), 2),
                "note": None,
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    auctions = load_entity("auction")
    contracts = load_entity("contract")
    notices = load_entity("notice")

    if auctions.empty:
        print("Δεν βρέθηκαν δεδομένα auction σε data/raw/. Τρέξε πρώτα backfill/fetch.")
        return

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

    if not contracts.empty:
        hhi = hhi_concentration(contracts)
        hhi_path = OUT_DIR / "indicator_hhi.csv"
        hhi.to_csv(hhi_path, index=False, encoding="utf-8-sig")
        print(f"\nHHI indicator -> {hhi_path} ({len(hhi)} γραμμές φορέα/έτους)")
    else:
        print("\n(Δεν βρέθηκαν δεδομένα contract -- παραλείπεται το HHI.)")

    bs = bid_splitting(auctions)
    if not bs.empty:
        bs_path = OUT_DIR / "indicator_bid_splitting.csv"
        bs.to_csv(bs_path, index=False, encoding="utf-8-sig")
        print(f"\nBid-splitting indicator -> {bs_path} ({len(bs)} γραμμές φορέα/περιόδου)")
    else:
        print("\n(Ανεπαρκή δεδομένα για bid-splitting -- παραλείπεται.)")

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


if __name__ == "__main__":
    main()
