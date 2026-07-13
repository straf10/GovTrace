"""P2-02 (Φάση 2, session 34): whitelist ψευδωνύμων αναδόχων για display/graph.

ΔΕΝ αγγίζει το production keying (``data/processed/vat_resolver.csv``) --
διαβάζει μόνο τα ήδη υπάρχοντα ``data/processed/er/contractors_raw.csv``
(βλ. build_contractor_table.py) και ``entity_resolution_matches.csv`` (βλ.
splink_poc.py, full run E7: 557.784 ζεύγη vat/name -> 264.463 matched pairs).

Πολιτική συγχώνευσης -- ΜΟΝΟ high-confidence, ΧΩΡΙΣ κρίση/ΓΕΜΗ (ίδια
πολιτική με judge_audit_sample.py κανόνες 1-2, βλ. R-04 στο PHASE_2.md):
  1. Ίδιο κανονικοποιημένο ΑΦΜ: όλα τα ονόματα που έχουν καταγραφεί για το
     ίδιο ΑΦΜ γίνονται aliases του ίδιου κόμβου.
  2. Ζεύγος με ΕΝΑ ΑΦΜ null και jw_score>=0.999 (πανομοιότυπο όνομα): το
     όνομα-χωρίς-ΑΦΜ μπαίνει ως alias στο ΑΦΜ που έχει το άλλο μέλος.
  Διαφορετικό έγκυρο ΑΦΜ και στις δύο πλευρές (ίδιο όνομα) = "uncertain" ->
  ΔΕΝ συγχωνεύεται (θέλει ΓΕΜΗ, P2-B3).

Κανόνας εμφάνισης: "most recent name wins" -- το canonical_name είναι το
όνομα με το μεγαλύτερο last_year (ισοπαλία: μεγαλύτερο n). Οι υπόλοιπες
μορφές μπαίνουν στο πεδίο aliases (JSON list, ταξινομημένο first_year desc).

Χρήση:
    python scripts/er/build_alias_whitelist.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

CONTRACTORS_PATH = Path("data/processed/er/contractors_raw.csv")
MATCHES_PATH = Path("data/processed/er/entity_resolution_matches.csv")
OUT_PATH = Path("data/processed/er/contractor_aliases.csv")

IDENTICAL_NAME_THRESHOLD = 0.999


def _vat_valid(v: object) -> bool:
    return isinstance(v, str) and v.strip() not in ("", "nan")


def build_vat_groups(contractors: pd.DataFrame, matches: pd.DataFrame) -> dict[str, list[dict]]:
    """vat -> λίστα εμφανίσεων ονόματος {name, n, first_year, last_year}."""
    groups: dict[str, list[dict]] = {}

    for row in contractors.itertuples(index=False):
        if not _vat_valid(row.vat):
            continue
        groups.setdefault(row.vat, []).append(
            {"name": row.name, "n": int(row.n), "first_year": int(row.first_year), "last_year": int(row.last_year)}
        )

    # Κανόνας 2: missing-vat name <-> valid-vat name, πανομοιότυπο όνομα.
    # L2 (review.md): boolean mask αντί για `.loc[(None, missing_name)]` σε
    # MultiIndex -- το None==NaN matching σε object-dtype index είναι
    # implementation-dependent (σιωπηλό no-op αν δεν ταυτιστεί), το mask είναι
    # ρητό και ανεξάρτητο pandas version.
    missing_vat_mask = ~contractors["vat"].map(_vat_valid)
    same_name = matches[matches["jw_score"] >= IDENTICAL_NAME_THRESHOLD]
    for row in same_name.itertuples(index=False):
        vat_l_valid, vat_r_valid = _vat_valid(row.vat_l), _vat_valid(row.vat_r)
        if vat_l_valid == vat_r_valid:
            continue  # ίδιο ΑΦΜ ήδη καλυμμένο παραπάνω· διαφορετικό έγκυρο ΑΦΜ = uncertain, εξαιρείται
        target_vat = row.vat_l if vat_l_valid else row.vat_r
        missing_name = row.name_r if vat_l_valid else row.name_l
        src = contractors[missing_vat_mask & (contractors["name"] == missing_name)]
        if src.empty:
            continue
        entries = [r for _, r in src.iterrows()]
        for entry in entries:
            groups.setdefault(target_vat, []).append(
                {
                    "name": entry["name"],
                    "n": int(entry["n"]),
                    "first_year": int(entry["first_year"]),
                    "last_year": int(entry["last_year"]),
                }
            )
    return groups


def build_whitelist(contractors: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    groups = build_vat_groups(contractors, matches)
    rows = []
    for vat, entries in groups.items():
        # Dedupe ονόματα (ίδιο όνομα μπορεί να εμφανιστεί από 2 πηγές -- ίδιο vat group + rule 2 match).
        by_name: dict[str, dict] = {}
        for e in entries:
            cur = by_name.get(e["name"])
            if cur is None:
                by_name[e["name"]] = dict(e)
            else:
                cur["n"] += e["n"]
                cur["first_year"] = min(cur["first_year"], e["first_year"])
                cur["last_year"] = max(cur["last_year"], e["last_year"])
        ordered = sorted(by_name.values(), key=lambda e: (-e["last_year"], -e["n"]))
        canonical = ordered[0]
        aliases = [e["name"] for e in ordered[1:]]
        rows.append(
            {
                "vat": vat,
                "canonical_name": canonical["name"],
                "aliases": json.dumps(aliases, ensure_ascii=False),
                "n_names": len(ordered),
                "first_year": min(e["first_year"] for e in ordered),
                "last_year": max(e["last_year"] for e in ordered),
                "n_total": sum(e["n"] for e in ordered),
            }
        )
    return pd.DataFrame(rows).sort_values("vat").reset_index(drop=True)


def main() -> None:
    contractors = pd.read_csv(CONTRACTORS_PATH, dtype={"vat": str, "name": str})
    matches = pd.read_csv(MATCHES_PATH, dtype={"vat_l": str, "vat_r": str, "name_l": str, "name_r": str})
    if "jw_score" not in matches.columns:
        import duckdb

        con = duckdb.connect()
        con.register("df", matches)
        matches = con.execute("SELECT *, jaro_winkler_similarity(name_l, name_r) AS jw_score FROM df").df()

    whitelist = build_whitelist(contractors, matches)
    n_with_aliases = (whitelist["n_names"] > 1).sum()
    print(f"ΑΦΜ με >=1 καταγεγραμμένο όνομα: {len(whitelist)}")
    print(f"ΑΦΜ με >=2 ονόματα (πραγματικά aliases): {n_with_aliases}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    whitelist.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"Γράφτηκε {OUT_PATH}")


if __name__ == "__main__":
    main()
