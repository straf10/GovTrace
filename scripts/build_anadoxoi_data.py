"""P2-15: slim index δεδομένων για `/anadoxoi/` (μόνο νομικά πρόσωπα).

ΔΕΝ pre-rendered σελίδα ανά ανάδοχο σε αυτή τη φάση (βλ. R-01 στο
docs/PHASE_2.md: 100-250k pre-rendered σελίδες σπάνε το όριο 20k αρχείων
του Cloudflare Pages) -- μόνο ΕΝΑ slim JSON, client-side search στο site.

Φίλτρο "μόνο νομικά πρόσωπα" (data minimization principle, βλ.
docs/PHASE_2.md "Τι ΔΕΝ αλλάζει"): χωρίς ΓΕΜΗ (έρχεται στο P2-B*) δεν
υπάρχει τρόπος να επιβεβαιωθεί η νομική μορφή -- γι' αυτό εφαρμόζεται
ΣΥΝΤΗΡΗΤΙΚΟ heuristic (βλ. LEGAL_FORM_MARKERS): αν το όνομα δεν περιέχει
αναγνωρίσιμο δείκτη νομικής μορφής, ο ανάδοχος ΑΠΟΚΛΕΙΕΤΑΙ (πιθανό φυσικό
πρόσωπο) αντί να δημοσιευτεί αβέβαια. Αποδεκτό false-negative (μια πραγματική
εταιρεία με ασυνήθιστο όνομα μένει εκτός) -- ΟΧΙ αποδεκτό false-positive
(δημοσίευση ονόματος φυσικού προσώπου). Θα ξαναδουλευτεί με ΓΕΜΗ (P2-B5).

Χρήση:
    python scripts/build_anadoxoi_data.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_foreas_data import load_auctions_slim  # noqa: E402
from kimdis_data import PROCESSED_DIR, normalize_vat, sanitize_value  # noqa: E402

SITE_DIR = PROCESSED_DIR.parent.parent / "site"
PUBLIC_DATA_DIR = SITE_DIR / "public" / "data"
ALIASES_PATH = PROCESSED_DIR / "er" / "contractor_aliases.csv"
OUT_PATH = PUBLIC_DATA_DIR / "anadoxoi-slim.json"

# Συντηρητικό heuristic -- δείκτες νομικής μορφής (Ελληνικά + λατινικά/διεθνή
# ισοδύναμα που εμφανίζονται σε ΚΗΜΔΗΣ ονόματα αλλοδαπών προμηθευτών).
LEGAL_FORM_MARKERS = [
    "Α.Ε", "ΑΕ ", "ΑΝΩΝΥΜ", "Ε.Π.Ε", "ΕΠΕ ", "Ι.Κ.Ε", "ΙΚΕ ", "Ο.Ε", "ΟΕ ",
    "Ε.Ε", "ΕΕ ", "ΟΜΟΡΡΥΘΜ", "ΕΤΕΡΟΡΡΥΘΜ", "ΚΟΙΝΟΠΡΑΞΙΑ", "ΣΥΝΕΤΑΙΡΙΣΜΟΣ",
    "ΕΤΑΙΡ", "ΝΠΔΔ", "Ν.Π.Δ.Δ", "ΝΠΙΔ", "Ν.Π.Ι.Δ", "ΔΗΜΟΣ", "ΠΕΡΙΦΕΡΕΙΑ",
    "ΥΠΟΥΡΓΕΙΟ", "ΟΡΓΑΝΙΣΜΟΣ", "ΙΔΡΥΜΑ", "ΣΥΛΛΟΓΟΣ", "ΕΝΩΣΗ", "ΙΝΣΤΙΤΟΥΤΟ",
    "ΝΟΣΟΚΟΜΕΙΟ", "ΠΑΝΕΠΙΣΤΗΜΙΟ", "ΕΠΙΧΕΙΡΗΣΗ", "LTD", "GMBH", "S.A.", "SA ",
    "PLC", "INC", "CORP", "LLC", "SRL", "S.R.L", "B.V", "N.V", "SPA", "S.P.A",
    "CO.", "COMPANY", "GROUP",
]
_MARKER_RE = re.compile("|".join(re.escape(m) for m in LEGAL_FORM_MARKERS))


def looks_like_legal_entity(name: str) -> bool:
    return bool(_MARKER_RE.search(f" {name.upper()} "))


def load_alias_names() -> dict[str, str]:
    if not ALIASES_PATH.exists():
        return {}
    df = pd.read_csv(ALIASES_PATH, dtype={"vat": str})
    return dict(zip(df["vat"], df["canonical_name"]))


def build_index() -> pd.DataFrame:
    auctions = load_auctions_slim()
    if auctions.empty:
        return pd.DataFrame()

    df = auctions.dropna(subset=["contractor_vat"]).copy()
    # #Bug (P2-15): _first_contractor() στο build_foreas_data.py επιστρέφει το
    # raw vatNumber ΧΩΡΙΣ normalize_vat -- η ΚΗΜΔΗΣ πηγή έχει σκουπίδια εκεί
    # (π.χ. "189,00 €", πολλαπλά ΑΦΜ μελών ενωμένα με κόμμα). Χωρίς αυτό το
    # groupby παρακάτω θα έφτιαχνε ψευδο-εγγραφές ανάδοχου από αυτά τα
    # σκουπίδια -- re-normalize εδώ πριν το keying (ίδιος κανόνας παντού).
    df["contractor_vat"] = df["contractor_vat"].map(normalize_vat)
    df = df.dropna(subset=["contractor_vat"])
    df["value"] = sanitize_value(pd.to_numeric(df["totalCostWithoutVAT"], errors="coerce").fillna(
        pd.to_numeric(df["totalCostWithVAT"], errors="coerce")
    ))

    agg = df.groupby("contractor_vat").agg(
        n_awards=("referenceNumber", "count"),
        value_total=("value", "sum"),
        first_year=("_source_year", "min"),
        last_year=("_source_year", "max"),
        _name_mode=("contractor_name", lambda s: s.mode(dropna=True).iloc[0] if not s.mode(dropna=True).empty else None),
    ).reset_index().rename(columns={"contractor_vat": "vat"})

    alias_names = load_alias_names()
    agg["name"] = agg["vat"].map(alias_names).fillna(agg["_name_mode"])
    agg = agg.drop(columns=["_name_mode"]).dropna(subset=["name"])

    agg = agg[agg["name"].map(looks_like_legal_entity)]
    agg["value_total"] = agg["value_total"].round(2)
    return agg.sort_values("value_total", ascending=False)


COLUMNS = ["vat", "name", "n_awards", "value_total", "first_year", "last_year"]


def main() -> None:
    PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
    table = build_index()
    if table.empty:
        raise RuntimeError("Δεν βρέθηκαν δεδομένα auction -- πιθανή αποτυχία backfill ή αρχεία R2 pull. "
                           "Σταμάτα τη χτίσιμο αντί να δημιουργηθούν ημιτελή δεδομένα.")
    # Columnar encoding (columns + row arrays, ΟΧΙ array of objects) -- σε
    # 55k+ γραμμές η επανάληψη των ίδιων 6 keys ανά εγγραφή προσέθετε ~3MB
    # καθαρή επανάληψη χωρίς πληροφορία (μετρημένο: 9,3MB -> ~6MB raw).
    rows = table[COLUMNS].values.tolist()
    OUT_PATH.write_text(json.dumps({"columns": COLUMNS, "rows": rows}, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"/anadoxoi/ index -> {OUT_PATH} ({len(rows)} ανάδοχοι, μετά το heuristic legal-entity φίλτρο)")


if __name__ == "__main__":
    main()
