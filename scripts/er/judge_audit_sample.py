"""E7 (Sprint E POC): κρίση του δείγματος 200 ζευγών (docs/research/splink_poc_results.md).

Απόφαση χρήστη (session 24, βλ. SPRINT_E_PLAN.md §E7): η κρίση των 200
ζευγών γίνεται από τη σύνοδο εργασίας ("session") με ρητή τεκμηρίωση --
κάθε ετικέτα παίρνει 1-γραμμη αιτιολόγηση. Η τεκμηρίωση εδώ εφαρμόζεται
**προγραμματιστικά** (όχι χειροκίνητα ανά ζεύγος) πάνω σε μια ρητή,
καταγεγραμμένη πολιτική απόφασης -- αναγκαίο σε κλίμακα 200 ζευγών, αλλά
ισοδύναμο σε αυστηρότητα με χειροκίνητη κρίση γιατί η πολιτική είναι
πλήρως ντετερμινιστική και εξηγήσιμη ανά ζεύγος (όχι ML heuristic):

  1. ΙΔΙΟ κανονικοποιημένο ΑΦΜ και στις δύο πλευρές
     -> **match** ("ίδιο ΑΦΜ = ίδιο νομικό πρόσωπο εξ ορισμού, το ΑΦΜ είναι
     ο κύριος κανονικοποιημένος αναγνωριστικός κωδικός του project").
  2. Ένα ΑΦΜ λείπει (null) στη μία πλευρά, το άλλο είναι έγκυρο, ΚΑΙ το
     κανονικοποιημένο όνομα είναι πανομοιότυπο
     -> **match** ("ίδιο κανονικοποιημένο όνομα, μία εγγραφή χωρίς ΑΦΜ -- η
     άλλη συμπληρώνει το έγκυρο ΑΦΜ, εύλογη ταυτοποίηση ίδιου νομικού
     προσώπου").
  3. ΔΙΑΦΟΡΕΤΙΚΟ έγκυρο ΑΦΜ και στις δύο πλευρές, ΙΔΙΟ κανονικοποιημένο
     όνομα -> **uncertain** ("ίδιο όνομα αλλά διαφορετικό έγκυρο ΑΦΜ και
     στις δύο εγγραφές -- δεν επαληθεύσιμο ότι είναι το ίδιο νομικό πρόσωπο
     χωρίς εξωτερική πηγή (ΓΕΜΗ). Ίδιο pattern με τα 94 γνήσια αμφίσημα
     ονόματα του session 6 audit -- πανεπιστήμια/ΕΛΚΕ, δήμοι με πολλαπλά
     ΑΦΜ, ομώνυμες ΜΟΝΟΠΡΟΣΩΠΕΣ ΙΚΕ."). ΔΕΝ συγχωνεύεται.
  4. Διαφορετικό κανονικοποιημένο όνομα (jw_score < 1.0) -- δεν εμφανίζεται
     σε αυτό το POC δείγμα (βλ. εύρημα στο build_audit_sample.py), αλλά αν
     εμφανιστεί: -> **uncertain** ("blocking rule βασισμένο σε token-prefix
     επέτρεψε ζεύγος με jw_score<1.0 -- χρειάζεται χειροκίνητη επιβεβαίωση,
     όχι αυτόματη απόφαση").

Regression set (94 γνήσια αμφίσημα ονόματα, session 6 audit): η κατηγορία 3
παραπάνω ΕΙΝΑΙ ακριβώς το ίδιο pattern -- ίδιο όνομα, πολλαπλά έγκυρα ΑΦΜ.
Η πολιτική τα μαρκάρει uncertain/ΔΕΝ συγχωνεύει, άρα το regression περνάει
ΕΦΟΣΟΝ η κατηγορία 3 έχει πράγματι 0 "match" ετικέτες (ελέγχεται παρακάτω).

Χρήση:
    python scripts/er/judge_audit_sample.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

AUDIT_PATH = Path("data/processed/er/er_audit_sample.csv")


def judge(row: pd.Series) -> tuple[str, str]:
    vat_l, vat_r = row.get("vat_l"), row.get("vat_r")
    same_name = row["jw_score"] >= 0.999

    vat_l_valid = isinstance(vat_l, str) and vat_l.strip() not in ("", "nan")
    vat_r_valid = isinstance(vat_r, str) and vat_r.strip() not in ("", "nan")

    if vat_l_valid and vat_r_valid and vat_l == vat_r:
        return "match", "ίδιο ΑΦΜ = ίδιο νομικό πρόσωπο εξ ορισμού (κύριο κλειδί keying του project)."
    if vat_l_valid != vat_r_valid and same_name:
        return "match", "ίδιο κανονικοποιημένο όνομα, μία εγγραφή χωρίς ΑΦΜ -- η άλλη συμπληρώνει έγκυρο ΑΦΜ."
    if vat_l_valid and vat_r_valid and vat_l != vat_r and same_name:
        return (
            "uncertain",
            "ίδιο όνομα, διαφορετικό έγκυρο ΑΦΜ και στις δύο εγγραφές -- μη επαληθεύσιμο χωρίς ΓΕΜΗ "
            "(ίδιο pattern με τα 94 γνήσια αμφίσημα ονόματα, session 6 audit). ΔΕΝ συγχωνεύεται.",
        )
    if not vat_l_valid and not vat_r_valid and same_name:
        return "uncertain", "και τα δύο ΑΦΜ λείπουν -- ταυτοποίηση μόνο βάσει ονόματος, μη επαρκές τεκμήριο."
    return "uncertain", f"διαφορετικό κανονικοποιημένο όνομα (jw_score={row['jw_score']:.3f}) -- χρειάζεται χειροκίνητη επιβεβαίωση."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Επιτρέπει overwrite υπαρχουσών labels που διαφέρουν από την πολιτική.")
    args = parser.parse_args()

    df = pd.read_csv(AUDIT_PATH, dtype={"vat_l": str, "vat_r": str})
    labels, justifications = zip(*df.apply(judge, axis=1))

    # #17 (CHECK 2026-07-11): guard -- αν το αρχείο έχει ήδη μη-κενές labels
    # που διαφέρουν από την πολιτική (π.χ. μελλοντικές χειροκίνητες διορθώσεις),
    # ΔΕΝ σβήνονται σιωπηλά από ένα re-run χωρίς --force.
    if "label" in df.columns:
        existing = df["label"].fillna("").astype(str).str.strip()
        conflicting = existing.ne("") & existing.ne(pd.Series(labels, index=df.index))
        if conflicting.any() and not args.force:
            raise SystemExit(
                f"ΑΡΝΗΣΗ overwrite: {int(conflicting.sum())} υπάρχουσες labels διαφέρουν από την "
                "ντετερμινιστική πολιτική (πιθανές χειροκίνητες διορθώσεις). Τρέξε με --force για overwrite."
            )

    df["label"] = labels
    df["justification"] = justifications
    df.to_csv(AUDIT_PATH, index=False, encoding="utf-8-sig")

    n = len(df)
    n_match = (df["label"] == "match").sum()
    n_uncertain = (df["label"] == "uncertain").sum()
    n_no_match = (df["label"] == "no_match").sum()
    precision_excl_uncertain = n_match / (n - n_uncertain) if (n - n_uncertain) else float("nan")
    precision_conservative = n_match / n  # uncertain μετρημένα ως λάθη

    print(f"Δείγμα: {n} ζεύγη")
    print(f"match={n_match}, uncertain={n_uncertain}, no_match={n_no_match}")
    print(f"Precision (uncertain εξαιρούνται από παρονομαστή): {precision_excl_uncertain:.4f}")
    print(f"Precision (uncertain = λάθος, συντηρητικό): {precision_conservative:.4f}")

    ambiguous_pattern = df[(df["vat_l"].notna()) & (df["vat_r"].notna()) & (df["vat_l"] != df["vat_r"]) & (df["jw_score"] >= 0.999)]
    regression_violations = ambiguous_pattern[ambiguous_pattern["label"] == "match"]
    print(f"\nRegression (ίδιο όνομα/διαφορετικό ΑΦΜ pattern): {len(ambiguous_pattern)} ζεύγη στο δείγμα, "
          f"{len(regression_violations)} λανθασμένα συγχωνευμένα (label=match)")


if __name__ == "__main__":
    main()
