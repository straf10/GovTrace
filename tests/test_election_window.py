import pandas as pd

from election_window import (
    WINDOWS,
    benjamini_hochberg,
    classify_ota,
    compare_leadership,
    normalize_greek,
    ratio_for_window,
    _clean_cancelled,
)


# (α) window assignment -- μήνες ακριβώς στα όρια PRE/LAME/POST και εκτός.


def test_windows_pre_boundaries():
    pre = WINDOWS[WINDOWS["window"] == "PRE"].iloc[0]
    months = pd.period_range(pre["start"], pre["end"], freq="M")
    assert pd.Period("2023-04") in months
    assert pd.Period("2023-09") in months
    assert pd.Period("2023-10") not in months
    assert pd.Period("2023-03") not in months


def test_windows_lame_boundaries():
    lame = WINDOWS[WINDOWS["window"] == "LAME"].iloc[0]
    months = pd.period_range(lame["start"], lame["end"], freq="M")
    assert pd.Period("2023-10") in months
    assert pd.Period("2023-12") in months
    assert pd.Period("2024-01") not in months


def test_windows_post_is_descriptive_only():
    post = WINDOWS[WINDOWS["window"] == "POST"].iloc[0]
    assert bool(post["statistical"]) is False


# (β) ratio με NaN gap months -- skipna συμπεριφορά και zero-baseline εξαίρεση.


def test_ratio_for_window_skipna_and_zero_baseline():
    window_row = pd.Series({"start": "2023-04", "end": "2023-05", "apex_year": 2023})
    panel = pd.DataFrame(
        [
            # φορέας A: κανονικό baseline
            {"vat_norm": "A", "month": pd.Period("2023-04"), "n_direct": 10.0},
            {"vat_norm": "A", "month": pd.Period("2023-05"), "n_direct": 10.0},
            {"vat_norm": "A", "month": pd.Period("2021-04"), "n_direct": 5.0},
            {"vat_norm": "A", "month": pd.Period("2021-05"), "n_direct": 5.0},
            # φορέας B: baseline μήνας NaN (gap) -- πρέπει να αγνοηθεί (skipna)
            {"vat_norm": "B", "month": pd.Period("2023-04"), "n_direct": 4.0},
            {"vat_norm": "B", "month": pd.Period("2023-05"), "n_direct": 4.0},
            {"vat_norm": "B", "month": pd.Period("2021-04"), "n_direct": float("nan")},
            {"vat_norm": "B", "month": pd.Period("2021-05"), "n_direct": 8.0},
            # φορέας C: baseline μηδέν -- R πρέπει να είναι NaN, όχι +ε
            {"vat_norm": "C", "month": pd.Period("2023-04"), "n_direct": 3.0},
            {"vat_norm": "C", "month": pd.Period("2023-05"), "n_direct": 3.0},
            {"vat_norm": "C", "month": pd.Period("2021-04"), "n_direct": 0.0},
            {"vat_norm": "C", "month": pd.Period("2021-05"), "n_direct": 0.0},
        ]
    )
    ratio = ratio_for_window(panel, "n_direct", window_row, baseline_years=[2021])
    assert ratio["A"] == 2.0
    assert ratio["B"] == 0.5  # apex mean=4, baseline mean=skipna(nan,8)=8 -> 0.5
    assert pd.isna(ratio["C"])


# (γ) ταξινόμηση ΟΤΑ -- "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ" in, μη-ΟΤΑ out, τόνοι/πεζά στην είσοδο.


def test_classify_ota_accepts_dimos_with_accents_and_lowercase():
    assert classify_ota(normalize_greek("Δήμος Αθηναίων")) == ("dimos", None)


def test_classify_ota_accepts_perifereia():
    assert classify_ota(normalize_greek("Περιφέρεια Θεσσαλίας")) == ("perifereia", None)


def test_classify_ota_rejects_dimotiki_epicheirisi():
    kind, reason = classify_ota(normalize_greek("Δημοτική Επιχείρηση Ύδρευσης Αποχέτευσης Βόλου"))
    assert kind is None


def test_classify_ota_rejects_perifereiako_tameio():
    kind, reason = classify_ota(normalize_greek("Περιφερειακό Ταμείο Ανάπτυξης Αττικής"))
    assert kind is None


def test_classify_ota_excludes_legal_person_of_municipality():
    kind, reason = classify_ota(normalize_greek("Δήμος Αθηναίων Ανώνυμη Αναπτυξιακή Εταιρεία"))
    assert kind is None
    assert reason is not None and "non_ota_keyword" in reason


def test_classify_ota_none_for_unrelated_name():
    assert classify_ota(normalize_greek("Υπουργείο Εθνικής Άμυνας")) == (None, None)


# (δ) normalization ονοματεπωνύμων + κανόνας ίδιο-επώνυμο->unknown.


def test_normalize_greek_strips_accents_and_case():
    assert normalize_greek("Βύρωνος") == normalize_greek("ΒΥΡΩΝΟΣ")
    assert normalize_greek("Βύρωνος") == "ΒΥΡΩΝΟΣ"


def test_compare_leadership_same_person_is_false():
    assert compare_leadership("Γιώργος Παπαδόπουλος", "Γιώργος Παπαδόπουλος") == "false"


def test_compare_leadership_different_person_is_true():
    assert compare_leadership("Γιώργος Παπαδόπουλος", "Μαρία Ιωάννου") == "true"


def test_compare_leadership_same_surname_is_unknown():
    assert compare_leadership("Γιώργος Παπαδόπουλος", "Νίκος Παπαδόπουλος") == "unknown"


def test_compare_leadership_missing_2019_is_unknown():
    assert compare_leadership(None, "Μαρία Ιωάννου") == "unknown"
    assert compare_leadership("", "Μαρία Ιωάννου") == "unknown"


# (ε) Benjamini-Hochberg έναντι γνωστού αριθμητικού παραδείγματος.


def test_benjamini_hochberg_known_example():
    # Κλασικό παράδειγμα: p = [0.01, 0.02, 0.03, 0.04, 0.20], n=5
    # raw*n/rank = [0.05, 0.05, 0.05, 0.05, 0.20], μονοτονικό (ήδη αύξον) -> ίδιο
    pvals = [0.01, 0.02, 0.03, 0.04, 0.20]
    adjusted = benjamini_hochberg(pvals)
    expected = [0.05, 0.05, 0.05, 0.05, 0.20]
    for a, e in zip(adjusted, expected):
        assert abs(a - e) < 1e-9


def test_benjamini_hochberg_empty():
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_monotone_step_down():
    # Παράδειγμα όπου χρειάζεται το cummin βήμα (μη μονοτονικό raw)
    pvals = [0.005, 0.03, 0.03, 0.05]
    adjusted = benjamini_hochberg(pvals)
    # μη-φθίνον όταν ταξινομημένο κατά p
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    sorted_adj = [adjusted[i] for i in order]
    assert all(sorted_adj[i] <= sorted_adj[i + 1] + 1e-12 for i in range(len(sorted_adj) - 1))


# (στ) cancelled string mapping ("false"->False).


def test_clean_cancelled_string_false_stays_false():
    s = pd.Series(["false", "true", "False", "True", True, False, None])
    result = _clean_cancelled(s)
    assert result.tolist() == [False, True, False, True, True, False, False]
