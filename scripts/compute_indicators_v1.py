"""Φάση 1: Δείκτες v1 — % απευθείας αναθέσεων & συγκέντρωση αναδόχων (HHI).

Διαβάζει ό,τι raw δεδομένα υπάρχουν ήδη σε data/raw/ (auction_*, contract_*)
και υπολογίζει ανά φορέα/έτος:
  - DA_count, DA_value: μερίδιο απευθείας αναθέσεων σε πλήθος/αξία (METHODOLOGY §4.1)
  - HHI, top1: συγκέντρωση αναδόχων στις συμβάσεις (METHODOLOGY §4.2, ελάχιστο N=10)

Ξαναρχόμενο script: τρέχει πάνω σε ό,τι δεδομένα υπάρχουν τη στιγμή εκτέλεσης
(ο backfill μπορεί να είναι ακόμα σε εξέλιξη).

Χρήση:
    python scripts/compute_indicators_v1.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed")
DIRECT_AWARD_KEY = "6"  # procedureType.key == "6" -> Απευθείας ανάθεση (επιβεβαιωμένο, βλ. MEMORY.md §4)
MIN_N_HHI = 10  # METHODOLOGY §4.2


def load_entity(entity: str) -> pd.DataFrame:
    files = sorted(RAW_DIR.glob(f"{entity}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        # <entity>_<YYYY>_<MM>.parquet -> year απευθείας από το filename (πιο αξιόπιστο από τα raw πεδία)
        _, year, month = f.stem.split("_")
        df["_source_year"] = int(year)
        df["_source_month"] = int(month)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def direct_award_rate(auctions: pd.DataFrame) -> pd.DataFrame:
    df = auctions.copy()
    df["value"] = pd.to_numeric(df.get("totalCostWithoutVAT"), errors="coerce").fillna(
        pd.to_numeric(df.get("totalCostWithVAT"), errors="coerce")
    )
    df["is_direct"] = df["procedureType.key"].astype(str) == DIRECT_AWARD_KEY

    rows = []
    for (vat, name, year), g in df.groupby(["organizationVatNumber", "organization.value", "_source_year"]):
        n_total = len(g)
        n_direct = int(g["is_direct"].sum())
        val_total = g["value"].sum(skipna=True)
        val_direct = g.loc[g["is_direct"], "value"].sum(skipna=True)
        rows.append(
            {
                "organization_vat": vat,
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
    for (vat, name, year), g in df.groupby(["organizationVatNumber", "organization.value", "_source_year"]):
        g = g.dropna(subset=["contractor_vat", "value"])
        n = len(g)
        if n < MIN_N_HHI:
            rows.append(
                {
                    "organization_vat": vat,
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
                "organization_vat": vat,
                "organization_name": name,
                "year": year,
                "n_contracts": n,
                "hhi": round(hhi, 4),
                "top1_share": round(float(shares.max()), 4),
                "note": None,
            }
        )
    return pd.DataFrame(rows).sort_values(["year", "organization_vat"])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    auctions = load_entity("auction")
    contracts = load_entity("contract")

    if auctions.empty:
        print("Δεν βρέθηκαν δεδομένα auction σε data/raw/. Τρέξε πρώτα backfill/fetch.")
        return

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
        print("\n(Δεν βρέθηκαν δεδομένα contract — παραλείπεται το HHI.)")


if __name__ == "__main__":
    main()
