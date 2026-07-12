"""P2-05 (Φάση 2, session 35): DDL + verify για το πλήρες offline Neo4j graph.

Τρέχει ΜΕΤΑ το `neo4j-admin database import full` (disposable docker run,
βλ. docs/PHASE_2.md P2-05 + docs/MEMORY.md session 29/35). ΔΕΝ αγγίζει
production data -- μόνο διαβάζει το τοπικό Neo4j (127.0.0.1:7687) και τα
ήδη υπάρχοντα CSV στο data/graph_staging/ (η "pandas ground truth").

Χρήση:
    python scripts/graph/load_graph.py --ddl-and-verify
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kimdis_data import is_valid_vat_checksum  # noqa: E402

STAGING_DIR = Path("data/graph_staging")
DDL_PATH = Path("scripts/graph/ddl.cypher")
BOLT_URI = "bolt://127.0.0.1:7687"

# P2-05: γνωστό ego-network -- σύνολο αξίας αναθέσεων του Υπουργείου Εθνικής
# Άμυνας το 2020. Το PHASE_2.md ανέφερε ~2,27 δις€ ως εκτίμηση αναφοράς, αλλά
# η επαλήθευση στο πλήρες production indicators.json (session 35) έδειξε
# **3,30 δις€** (VAT 090153025 μόνο του: 3.301.045.117,26€, n=17.101) --
# η αρχική εκτίμηση του πλάνου ήταν stale/λάθος τάξης μεγέθους ~1,45×, ΟΧΙ ο
# γράφος. Ο έλεγχος χρησιμοποιεί την πραγματική επαληθευμένη τιμή ως
# reference. Το ΥΠΕΘΑ έχει πολλαπλά ΑΦΜ στο ΚΗΜΔΗΣ (γνωστό, μη ενοποιημένο
# νομικό πρόσωπο ανά υπηρεσία/κλάδο) -- ο έλεγχος αθροίζει σε ΟΛΑ τα ΑΦΜ με
# το ίδιο επίσημο όνομα (organization.value string, όχι το πιο αυστηρό
# canonical grouping του production entities.csv -- γι' αυτό η ανοχή
# παραμένει ευρεία, sanity check όχι exact reconciliation).
YPETHA_NAME = "ΥΠΟΥΡΓΕΙΟ ΕΘΝΙΚΗΣ ΑΜΥΝΑΣ"
YPETHA_2020_EXPECTED = 3.30e9
YPETHA_2020_TOLERANCE = 0.20  # ±20% -- proxy μέτρησης (γνωστό ego, όχι exact reconciliation)


def load_dotenv(env_path: Path = Path(".env")) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_driver():
    load_dotenv()
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise SystemExit("NEO4J_PASSWORD δεν βρέθηκε στο .env")
    return GraphDatabase.driver(BOLT_URI, auth=("neo4j", password))


def apply_ddl(driver) -> None:
    # Bug (P2-05, session 35): ένα naive split(";") + "skip αν το ΟΛΟ chunk
    # ξεκινά με //" πετούσε ΟΛΟΚΛΗΡΟ το statement όταν προηγούνταν σχόλιο
    # header στο ίδιο chunk (π.χ. organization_vat + και τα 2 Award indexes
    # χάνονταν σιωπηλά, μόνο 4/7 constraints/indexes εφαρμόζονταν). Διόρθωση:
    # αφαιρούνται οι γραμμές-σχόλια ΜΕΣΑ σε κάθε chunk, όχι όλο το chunk.
    statements = []
    for chunk in DDL_PATH.read_text(encoding="utf-8").split(";"):
        lines = [line for line in chunk.splitlines() if not line.strip().startswith("//")]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    with driver.session() as session:
        for stmt in statements:
            session.run(stmt)
    print(f"DDL εφαρμόστηκε: {len(statements)} statements")


class Check:
    def __init__(self, name: str, ok: bool, detail: str):
        self.name = name
        self.ok = ok
        self.detail = detail


def run_verify(driver) -> list[Check]:
    checks: list[Check] = []

    awards = pd.read_csv(STAGING_DIR / "awards.csv", dtype={"adam:ID(Award)": str})
    orgs = pd.read_csv(STAGING_DIR / "organizations.csv", dtype=str)
    contractors = pd.read_csv(STAGING_DIR / "contractors.csv", dtype=str)

    with driver.session() as session:
        # 1) Counts: Award/Organization/Contractor node counts == staging CSV row counts.
        n_award = session.run("MATCH (a:Award) RETURN count(a) AS n").single()["n"]
        n_org = session.run("MATCH (o:Organization) RETURN count(o) AS n").single()["n"]
        n_contr = session.run("MATCH (c:Contractor) RETURN count(c) AS n").single()["n"]
        checks.append(Check(
            "1. Counts (Award/Organization/Contractor)",
            n_award == len(awards) and n_org == len(orgs) and n_contr == len(contractors),
            f"Award: graph={n_award} csv={len(awards)} · Organization: graph={n_org} csv={len(orgs)} · "
            f"Contractor: graph={n_contr} csv={len(contractors)}",
        ))

        # 2) Sum(amount_ex_vat) ανά έτος: graph vs pandas ground truth.
        pandas_by_year = (
            pd.to_datetime(awards["date:date"], errors="coerce").dt.year
            .to_frame("year")
            .assign(amount=pd.to_numeric(awards["amount_ex_vat:double"], errors="coerce"))
            .dropna(subset=["year"])
            .groupby("year")["amount"].sum()
        )
        graph_by_year = session.run(
            "MATCH (a:Award) WHERE a.date IS NOT NULL "
            "RETURN a.date.year AS year, sum(a.amount_ex_vat) AS total ORDER BY year"
        ).data()
        graph_by_year_map = {row["year"]: row["total"] for row in graph_by_year}
        year_mismatches = [
            year for year, total in pandas_by_year.items()
            if abs((graph_by_year_map.get(int(year)) or 0) - total) > max(1.0, abs(total) * 1e-6)
        ]
        checks.append(Check(
            "2. Sum(amount_ex_vat) ανά έτος",
            not year_mismatches,
            f"{len(pandas_by_year)} έτη ελέγχθηκαν, αποκλίσεις: {year_mismatches}",
        ))

        # 3) Entities: 6 τυχαία Organization nodes -- name property ίδιο με CSV.
        sample_orgs = orgs.sample(n=min(20, len(orgs)), random_state=42)
        mismatches = []
        for row in sample_orgs.itertuples(index=False):
            rec = session.run("MATCH (o:Organization {vat: $vat}) RETURN o.name AS name", vat=row[0]).single()
            if rec is None or rec["name"] != row[1]:
                mismatches.append(row[0])
        checks.append(Check(
            "3. Entities (20 τυχαία Organization name)",
            not mismatches,
            f"{len(sample_orgs)} ελέγχθηκαν, αποκλίσεις: {mismatches}",
        ))

        # 4) 20 τυχαία ΑΔΑΜ (Award): amount/date/cancelled ίδια με CSV.
        # Bug (P2-05, session 35): positional row[0..3] υπέθετε τη ΠΑΛΙΑ σειρά
        # στηλών του awards.csv -- μετά την προσθήκη source_year/source_month
        # ΑΝΑΜΕΣΑ σε date και cancelled, το row[3] έδειχνε πλέον source_year
        # (πάντα truthy int) αντί για cancelled, κάνοντας ΟΛΑ τα δείγματα
        # "mismatch". Διόρθωση: πρόσβαση με column name, όχι θέση.
        sample_awards = awards.sample(n=min(20, len(awards)), random_state=42)
        i_adam = awards.columns.get_loc("adam:ID(Award)")
        i_amount = awards.columns.get_loc("amount_ex_vat:double")
        i_date = awards.columns.get_loc("date:date")
        i_cancelled = awards.columns.get_loc("cancelled:boolean")
        adam_mismatches = []
        for row in sample_awards.itertuples(index=False):
            adam, amount, date, cancelled = row[i_adam], row[i_amount], row[i_date], row[i_cancelled]
            rec = session.run(
                "MATCH (a:Award {adam: $adam}) RETURN a.amount_ex_vat AS amount, "
                "toString(a.date) AS date, a.cancelled AS cancelled",
                adam=adam,
            ).single()
            if rec is None:
                adam_mismatches.append(adam)
                continue
            amount_ok = (pd.isna(amount) and rec["amount"] is None) or (
                rec["amount"] is not None and abs(float(rec["amount"]) - float(amount)) < 1e-6
            )
            date_ok = (pd.isna(date) and rec["date"] is None) or (rec["date"] == date)
            cancelled_ok = bool(rec["cancelled"]) == bool(cancelled)
            if not (amount_ok and date_ok and cancelled_ok):
                adam_mismatches.append(adam)
        checks.append(Check(
            "4. 20 τυχαία ΑΔΑΜ (amount/date/cancelled)",
            not adam_mismatches,
            f"{len(sample_awards)} ελέγχθηκαν, αποκλίσεις: {adam_mismatches}",
        ))

        # 5) % έγκυρων ΑΦΜ (mod-11 checksum) -- Organization nodes, graph vs pandas.
        pandas_pct_valid = round(100 * orgs["vat:ID(Organization)"].map(is_valid_vat_checksum).mean(), 2)
        graph_valid = session.run(
            "MATCH (o:Organization) RETURN o.vat AS vat"
        ).data()
        graph_pct_valid = round(100 * pd.Series([is_valid_vat_checksum(r["vat"]) for r in graph_valid]).mean(), 2)
        checks.append(Check(
            "5. % έγκυρων ΑΦΜ (mod-11 checksum)",
            abs(pandas_pct_valid - graph_pct_valid) < 0.5,
            f"pandas={pandas_pct_valid}% graph={graph_pct_valid}%",
        ))

        # 6) Γνωστό ego-network: ΥΠΕΘΑ (όλα τα ΑΦΜ) σύνολο αξίας 2020.
        # Bug βρέθηκε στο P2-05 (session 35): a.date προέρχεται από
        # signedDate (καθυστερεί συχνά χρόνια μετά την ανάθεση) -- η
        # πρώτη έκδοση αυτού του check έδειχνε 0€ για 2020 επειδή
        # ΚΑΝΕΝΑ award αυτού του ΑΦΜ δεν έχει signedDate.year=2020 (όλα
        # 2021-2026). Η ΣΩΣΤΗ βάση "έτους" είναι το source_year (μήνας
        # fetch από filename -- ίδιο κριτήριο με το production
        # kimdis_data.py::flatten, βλ. σχόλιο στο build_graph_staging.py).
        ypetha_vats = orgs[orgs["name"] == YPETHA_NAME]["vat:ID(Organization)"].tolist()
        rec = session.run(
            "MATCH (o:Organization)-[:ISSUED]->(a:Award) "
            "WHERE o.vat IN $vats AND a.source_year = 2020 AND NOT a.cancelled "
            "RETURN sum(a.amount_ex_vat) AS total",
            vats=ypetha_vats,
        ).single()
        ypetha_total = rec["total"] or 0.0
        within_tolerance = abs(ypetha_total - YPETHA_2020_EXPECTED) <= YPETHA_2020_EXPECTED * YPETHA_2020_TOLERANCE
        checks.append(Check(
            "6. Ego-network ΥΠΕΘΑ 2020 (~2,27 δις€)",
            within_tolerance,
            f"graph={ypetha_total:,.2f}EUR ({len(ypetha_vats)} ΑΦΜ), αναμενόμενο ~{YPETHA_2020_EXPECTED:,.0f}EUR "
            f"(±{YPETHA_2020_TOLERANCE:.0%})",
        ))

    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ddl-and-verify", action="store_true", required=True)
    parser.parse_args()

    driver = get_driver()
    try:
        apply_ddl(driver)
        checks = run_verify(driver)
    finally:
        driver.close()

    all_ok = True
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}".encode("ascii", "replace").decode("ascii"))
        all_ok = all_ok and check.ok

    if not all_ok:
        raise SystemExit("ΑΠΟΤΥΧΙΑ verify -- βλ. FAIL checks παραπάνω")
    print("Verify: ΟΛΟΙ οι έλεγχοι πέτυχαν.")


if __name__ == "__main__":
    main()
