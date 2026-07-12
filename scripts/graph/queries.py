"""P2-08..11: Queries A-E πάνω στο πλήρες γράφημα (Neo4j, ήδη φορτωμένο -- βλ.
docs/PHASE_2.md §Α3). Cypher απευθείας πάνω στο βασικό γράφημα (Organization/
Award/Contractor/CPV + AWARDS_TO aggregate edge) -- ΔΕΝ χρειάζεται GDS
in-memory projection (αυτή είναι μόνο για run_gds.py's Louvain/PageRank/
betweenness).

Queries:
  A -- Αποκλειστικές σχέσεις (P2-08, ΕΣΩΤΕΡΙΚΟ μόνο):
       ζεύγη φορέας-ανάδοχος όπου η αμοιβαία αξία υπερβαίνει το 50% είτε
       του συνόλου του αναδόχου είτε του συνόλου του φορέα.
  B -- Market bridges (P2-09, μετά το P2-12 gate):
       ανάδοχοι που εμφανίζονται σε >=5 διαφορετικούς φορείς μέσα στην ίδια
       κατηγορία CPV (division = 2 πρώτα ψηφία του κωδικού).
  C -- Club effect / new-winner-rate (P2-10, μετά το P2-12 gate):
       ανά φορέα/έτος, ποσοστό αναδόχων που κερδίζουν ΓΙΑ ΠΡΩΤΗ ΦΟΡΑ από
       αυτόν τον φορέα (νέος ανάδοχος = δεν είχε ξανακερδίσει από αυτόν τον
       φορέα σε προηγούμενο source_year).
  D -- Ego-networks top-30 γείτονες ανά φορέα (P2-11, μετά το P2-12 gate):
       top-30 ανάδοχοι ανά φορέα βάσει total_amount_ex_vat στο AWARDS_TO.
       ΔΕΝ γράφονται ξεχωριστά αρχεία ανά φορέα (βλ. R-01) -- ένα JSON,
       ενσωματώνεται στο foreas_pages.json από το build_foreas_data.py.
  E -- Ύποπτη εναλλαγή (ΕΣΩΤΕΡΙΚΟ ΜΟΝΟ, ΔΕΝ δημοσιεύεται χωρίς METHODOLOGY
       thresholds + νομικό review): community/φορέας με ακριβώς 2 ανάδοχους
       που εναλλάσσονται έτος με το έτος.

Όλα τα exports χρησιμοποιούν το ΚΟΙΝΟ snapshot_date (scripts/graph/snapshot.py,
P2-07) -- όχι δικό τους "τώρα".

Χρήση:
    python scripts/graph/queries.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

from snapshot import read_snapshot_date

BOLT_URI = "bolt://127.0.0.1:7687"
OUT_DIR = Path("data/graph_staging/gds")

MIN_MUTUAL_VALUE = 10_000.0     # Query A: αγνοεί ζεύγη ασήμαντης αξίας
MIN_MUTUAL_SHARE = 0.5          # Query A: >50% αμοιβαίου όγκου
MIN_BRIDGE_ORGS = 5             # Query B: >=5 φορείς ίδιου CPV division
TOP_N_EGO = 30                  # Query D
MIN_ORG_TOTAL_FOR_NWR = 5       # Query C: ελάχιστο πλήθος αναδόχων/έτος για να υπολογιστεί


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


def query_a_exclusive(session) -> pd.DataFrame:
    rows = session.run(
        "MATCH (o:Organization)-[r:AWARDS_TO]->(c:Contractor) "
        "WHERE r.total_amount_ex_vat >= $min_value "
        "WITH o, c, r.total_amount_ex_vat AS mutual, r.n_awards AS n_awards "
        "MATCH (c)<-[rc:AWARDS_TO]-(:Organization) WITH o, c, mutual, n_awards, sum(rc.total_amount_ex_vat) AS c_total "
        "MATCH (o)-[ro:AWARDS_TO]->(:Contractor) WITH o, c, mutual, n_awards, c_total, sum(ro.total_amount_ex_vat) AS o_total "
        "WITH o, c, mutual, n_awards, c_total, o_total, "
        "  (CASE WHEN c_total > 0 THEN mutual / c_total ELSE 0.0 END) AS contractor_share, "
        "  (CASE WHEN o_total > 0 THEN mutual / o_total ELSE 0.0 END) AS org_share "
        "WHERE contractor_share >= $min_share OR org_share >= $min_share "
        "RETURN o.vat AS org_vat, o.name AS org_name, c.vat AS contractor_vat, c.name AS contractor_name, "
        "  mutual, n_awards, round(contractor_share, 4) AS contractor_share, round(org_share, 4) AS org_share "
        "ORDER BY mutual DESC",
        min_value=MIN_MUTUAL_VALUE, min_share=MIN_MUTUAL_SHARE,
    ).data()
    return pd.DataFrame(rows)


def query_b_market_bridges(session) -> pd.DataFrame:
    rows = session.run(
        "MATCH (c:Contractor)<-[:WON_BY]-(a:Award)<-[:ISSUED]-(o:Organization) "
        "MATCH (a)-[:CLASSIFIED_AS]->(cpv:CPV) "
        "WITH c, left(cpv.code, 2) AS cpv_division, o "
        "WITH c, cpv_division, count(DISTINCT o) AS n_orgs, collect(DISTINCT o.vat)[0..10] AS sample_org_vats "
        "WHERE n_orgs >= $min_orgs "
        "RETURN c.vat AS contractor_vat, c.name AS contractor_name, cpv_division, n_orgs, sample_org_vats "
        "ORDER BY n_orgs DESC",
        min_orgs=MIN_BRIDGE_ORGS,
    ).data()
    return pd.DataFrame(rows)


def query_c_new_winner_rate(session) -> pd.DataFrame:
    rows = session.run(
        "MATCH (o:Organization)-[:ISSUED]->(a:Award)-[:WON_BY]->(c:Contractor) "
        "WHERE a.source_year IS NOT NULL AND NOT a.cancelled "
        "WITH o, c, min(a.source_year) AS first_year "
        "MATCH (o)-[:ISSUED]->(a2:Award)-[:WON_BY]->(c) "
        "WHERE a2.source_year IS NOT NULL AND NOT a2.cancelled "
        "WITH o, a2.source_year AS year, c, first_year "
        "WITH o, year, count(DISTINCT c) AS n_contractors, "
        "  count(DISTINCT CASE WHEN first_year = year THEN c END) AS n_new_contractors "
        "WHERE n_contractors >= $min_n "
        "RETURN o.vat AS org_vat, year, n_contractors, n_new_contractors, "
        "  round(1.0 * n_new_contractors / n_contractors, 4) AS new_winner_rate "
        "ORDER BY o.vat, year",
        min_n=MIN_ORG_TOTAL_FOR_NWR,
    ).data()
    return pd.DataFrame(rows)


def query_d_ego_networks(session) -> dict:
    rows = session.run(
        "MATCH (o:Organization)-[r:AWARDS_TO]->(c:Contractor) "
        "WITH o, c, r.total_amount_ex_vat AS value, r.n_awards AS n_awards "
        "ORDER BY o.vat, value DESC "
        "WITH o, collect({vat: c.vat, name: c.name, value: value, n_awards: n_awards})[0..$top_n] AS neighbors, "
        "  sum(value) AS org_total "
        "RETURN o.vat AS org_vat, neighbors, org_total",
        top_n=TOP_N_EGO,
    ).data()
    ego: dict[str, list[dict]] = {}
    for row in rows:
        total = row["org_total"] or 0.0
        neighbors = [
            {
                "vat": n["vat"],
                "name": n["name"],
                "value": round(float(n["value"]), 2),
                "n_awards": int(n["n_awards"]),
                "share": round(100.0 * float(n["value"]) / total, 1) if total else None,
            }
            for n in row["neighbors"]
        ]
        ego[row["org_vat"]] = neighbors
    return ego


def query_e_alternation(session) -> pd.DataFrame:
    """ΕΣΩΤΕΡΙΚΟ ΜΟΝΟ (βλ. docs/PHASE_2.md §Α3 -- ΔΕΝ δημοσιεύεται χωρίς
    METHODOLOGY thresholds + νομικό review): φορείς με ΑΚΡΙΒΩΣ 2 ανάδοχους
    στο σύνολο του ιστορικού τους, όπου και οι δύο εμφανίζονται σε >=3
    διαφορετικά έτη (ένδειξη επαναλαμβανόμενης εναλλαγής, όχι απόδειξη)."""
    rows = session.run(
        "MATCH (o:Organization)-[:ISSUED]->(a:Award)-[:WON_BY]->(c:Contractor) "
        "WHERE a.source_year IS NOT NULL AND NOT a.cancelled "
        "WITH o, c, collect(DISTINCT a.source_year) AS years "
        "WITH o, collect({vat: c.vat, name: c.name, years: years}) AS contractors "
        "WHERE size(contractors) = 2 "
        "  AND size(contractors[0].years) >= 3 AND size(contractors[1].years) >= 3 "
        "RETURN o.vat AS org_vat, o.name AS org_name, "
        "  contractors[0].vat AS contractor_a_vat, contractors[0].name AS contractor_a_name, "
        "  contractors[1].vat AS contractor_b_vat, contractors[1].name AS contractor_b_name "
        "ORDER BY o.vat"
    ).data()
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_date = read_snapshot_date()

    driver = get_driver()
    try:
        with driver.session() as session:
            print("Query A -- αποκλειστικές σχέσεις...")
            exclusive = query_a_exclusive(session)
            exclusive.to_csv(OUT_DIR / "graph_findings_exclusive.csv", index=False, encoding="utf-8-sig")
            print(f"  {len(exclusive)} ζεύγη -> graph_findings_exclusive.csv (ΕΣΩΤΕΡΙΚΟ)")

            print("Query B -- market bridges...")
            bridges = query_b_market_bridges(session)
            bridges.to_csv(OUT_DIR / "graph_features_contractor.csv", index=False, encoding="utf-8-sig")
            print(f"  {len(bridges)} εγγραφές -> graph_features_contractor.csv")

            print("Query C -- new-winner-rate...")
            nwr = query_c_new_winner_rate(session)
            nwr.to_csv(OUT_DIR / "graph_features_org.csv", index=False, encoding="utf-8-sig")
            print(f"  {len(nwr)} εγγραφές (φορέας/έτος) -> graph_features_org.csv")

            print("Query D -- ego-networks top-30...")
            ego = query_d_ego_networks(session)
            ego_out = {"snapshot_date": snapshot_date, "top_n": TOP_N_EGO, "networks": ego}
            (OUT_DIR / "ego_networks.json").write_text(
                json.dumps(ego_out, ensure_ascii=False, indent=None), encoding="utf-8"
            )
            print(f"  {len(ego)} φορείς -> ego_networks.json")

            print("Query E -- ύποπτη εναλλαγή (ΕΣΩΤΕΡΙΚΟ)...")
            alternation = query_e_alternation(session)
            alternation.to_csv(OUT_DIR / "graph_findings_alternation.csv", index=False, encoding="utf-8-sig")
            print(f"  {len(alternation)} φορείς -> graph_findings_alternation.csv (ΕΣΩΤΕΡΙΚΟ, ΔΕΝ δημοσιεύεται)")

        summary = {
            "snapshot_date": snapshot_date,
            "n_exclusive_pairs": len(exclusive),
            "n_bridge_rows": len(bridges),
            "n_org_year_rows": len(nwr),
            "n_ego_orgs": len(ego),
            "n_alternation_orgs": len(alternation),
        }
        (OUT_DIR / "queries_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2).encode("ascii", "replace").decode("ascii"))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
