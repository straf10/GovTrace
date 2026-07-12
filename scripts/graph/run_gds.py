"""P2-06 (Φάση 2, session 35): GDS σε snapshot 2022 πρώτα -> μετά full graph.

Projection πάνω στο org-contractor award-value network (ισοδύναμο του
προ-υπολογισμένου AWARDS_TO, ΟΧΙ στο raw τριαδικό bipartite
Organization-Award-Contractor) -- Louvain (community detection),
PageRank, betweenness centrality. Community edition only (GDS 2.13,
επιβεβαιωμένο -- βλ. docs/MEMORY.md E8).

ΣΗΜΑΝΤΙΚΟ (εύρημα P2-05): το snapshot έτους χρησιμοποιεί `source_year`
(μήνας fetch, ίδιο κριτήριο με το production pipeline) -- ΟΧΙ `date.year`
(signedDate, καθυστερεί συχνά χρόνια μετά την ανάθεση). Το ήδη-φορτωμένο
:AWARDS_TO relationship είναι αθροισμένο σε ΟΛΑ τα έτη (2020-2026) -- για
το snapshot 2022 χρειάζεται on-the-fly Cypher aggregation projection
φιλτραρισμένη σε Award.source_year· για το "full" graph χρησιμοποιείται
το ήδη-υπολογισμένο :AWARDS_TO με native projection (γρηγορότερο).

Χρήση:
    python scripts/graph/run_gds.py --snapshot 2022
    python scripts/graph/run_gds.py --full
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timezone
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

from snapshot import read_snapshot_date

BOLT_URI = "bolt://127.0.0.1:7687"
OUT_DIR = Path("data/graph_staging/gds")


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


def project_snapshot_year(session, graph_name: str, year: int) -> dict:
    """Cypher aggregation projection: org-contractor edge βαρύτητας
    total_amount_ex_vat, ΜΟΝΟ awards με source_year=year, μη ακυρωμένα.

    Bug βρέθηκε στο P2-06 (session 35): μία μόνο κατεύθυνση org->contractor
    αφήνει τα Organization nodes ΧΩΡΙΣ εισερχόμενες ακμές -- το PageRank τα
    έδειχνε ΟΛΑ με το ίδιο floor score (0.15, το damping baseline), άχρηστο
    για σύγκριση. Διόρθωση: UNION ALL και των δύο κατευθύνσεων (ισοδύναμο
    undirected graph) -- ίδια λογική αναγκαία και για Louvain/betweenness
    ώστε φορέας/ανάδοχος να αντιμετωπίζονται συμμετρικά στο ίδιο δίκτυο."""
    session.run(f"CALL gds.graph.drop('{graph_name}', false)")
    result = session.run(
        "CALL gds.graph.project.cypher($name, "
        "'MATCH (n) WHERE n:Organization OR n:Contractor RETURN id(n) AS id, labels(n) AS labels', "
        "'MATCH (o:Organization)-[:ISSUED]->(a:Award)-[:WON_BY]->(c:Contractor) "
        "WHERE a.source_year = $year AND NOT a.cancelled "
        "WITH o, c, sum(a.amount_ex_vat) AS w "
        "RETURN id(o) AS source, id(c) AS target, w AS weight "
        "UNION ALL "
        "MATCH (o:Organization)-[:ISSUED]->(a:Award)-[:WON_BY]->(c:Contractor) "
        "WHERE a.source_year = $year AND NOT a.cancelled "
        "WITH o, c, sum(a.amount_ex_vat) AS w "
        "RETURN id(c) AS source, id(o) AS target, w AS weight', "
        "{parameters: {year: $year}})",
        name=graph_name, year=year,
    ).single()
    return dict(result)


def project_full_native(session, graph_name: str) -> dict:
    """Native projection πάνω στο ήδη-υπολογισμένο :AWARDS_TO (όλο το εύρος).
    UNDIRECTED (ίδιος λόγος με το snapshot -- βλ. σχόλιο project_snapshot_year)."""
    session.run(f"CALL gds.graph.drop('{graph_name}', false)")
    result = session.run(
        "CALL gds.graph.project($name, ['Organization', 'Contractor'], "
        "{AWARDS_TO: {orientation: 'UNDIRECTED', properties: 'total_amount_ex_vat'}})",
        name=graph_name,
    ).single()
    return dict(result)


def run_algorithms(session, graph_name: str, weight_property: str, label: str) -> dict:
    louvain = session.run(
        f"CALL gds.louvain.stream('{graph_name}', {{relationshipWeightProperty: $prop}}) "
        "YIELD nodeId, communityId "
        "RETURN gds.util.asNode(nodeId).vat AS vat, labels(gds.util.asNode(nodeId)) AS labels, communityId",
        prop=weight_property,
    ).data()

    pagerank = session.run(
        f"CALL gds.pageRank.stream('{graph_name}', {{relationshipWeightProperty: $prop}}) "
        "YIELD nodeId, score "
        "RETURN gds.util.asNode(nodeId).vat AS vat, gds.util.asNode(nodeId).name AS name, "
        "labels(gds.util.asNode(nodeId)) AS labels, score ORDER BY score DESC",
        prop=weight_property,
    ).data()

    betweenness = session.run(
        f"CALL gds.betweenness.stream('{graph_name}') "
        "YIELD nodeId, score "
        "RETURN gds.util.asNode(nodeId).vat AS vat, gds.util.asNode(nodeId).name AS name, "
        "labels(gds.util.asNode(nodeId)) AS labels, score ORDER BY score DESC",
    ).data()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(louvain).to_csv(OUT_DIR / f"louvain_{label}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pagerank).to_csv(OUT_DIR / f"pagerank_{label}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(betweenness).to_csv(OUT_DIR / f"betweenness_{label}.csv", index=False, encoding="utf-8-sig")

    n_communities = pd.Series([r["communityId"] for r in louvain]).nunique()
    return {
        "label": label,
        "n_nodes_louvain": len(louvain),
        "n_communities": int(n_communities),
        "top10_pagerank": pagerank[:10],
        "top10_betweenness": betweenness[:10],
    }


def sanity_check_pagerank(label: str, year: int | None) -> dict:
    """Top-PageRank Organization nodes πρέπει να επικαλύπτονται σε μεγάλο βαθμό
    με τα top-N οργανισμούς σε όγκο αναθέσεων (n_total) από τα ήδη δημοσιευμένα
    indicators -- markers μη-ασυνεπούς γράφου (π.χ. λάθος projection/keying)."""
    pagerank = pd.read_csv(OUT_DIR / f"pagerank_{label}.csv", dtype=str)
    top_org_vats_pagerank = (
        pagerank[pagerank["labels"].str.contains("Organization", na=False)]
        .head(10)["vat"].tolist()
    )
    indicators = json.loads(Path("site/public/data/indicators.json").read_text(encoding="utf-8"))
    records = indicators["records"]
    if year is not None:
        records = [r for r in records if r["year"] == year]
    # PageRank εδώ σταθμίζεται με amount_ex_vat (βλ. project_snapshot_year/
    # project_full_native) -- η σύγκριση πρέπει να γίνεται με value_total
    # (αξία), όχι n_total (πλήθος), ίδια μονάδα μέτρησης και στις δύο πλευρές.
    by_vat_total = {}
    for r in records:
        by_vat_total[r["vat"]] = by_vat_total.get(r["vat"], 0) + (r.get("value_total") or 0)
    top_org_vats_volume = [v for v, _ in sorted(by_vat_total.items(), key=lambda kv: -kv[1])[:10]]
    overlap = len(set(top_org_vats_pagerank) & set(top_org_vats_volume))
    return {
        "top10_pagerank_orgs": top_org_vats_pagerank,
        "top10_volume_orgs": top_org_vats_volume,
        "overlap": overlap,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=int, help="Έτος snapshot (π.χ. 2022) -- source_year, όχι date.year")
    group.add_argument("--full", action="store_true", help="Ολόκληρο το γράφημα (AWARDS_TO, όλα τα έτη)")
    args = parser.parse_args()

    driver = get_driver()
    try:
        with driver.session() as session:
            if args.snapshot:
                label = f"snapshot_{args.snapshot}"
                info = project_snapshot_year(session, "gds_graph", args.snapshot)
                weight_prop = "weight"
            else:
                label = "full"
                info = project_full_native(session, "gds_graph")
                weight_prop = "total_amount_ex_vat"

            print(f"Projection: {info.get('nodeCount')} nodes, {info.get('relationshipCount')} relationships")
            summary = run_algorithms(session, "gds_graph", weight_prop, label)
            session.run("CALL gds.graph.drop('gds_graph', false)")

        sanity = sanity_check_pagerank(label, args.snapshot)
        summary["sanity_check_vs_indicators"] = sanity
        # P2-07: snapshot_date = ημερομηνία staging build (μοναδική πηγή αλήθειας,
        # βλ. scripts/graph/snapshot.py) -- ΔΙΑΦΟΡΕΤΙΚΟ από generated_at (πότε
        # έτρεξε ΑΥΤΟ το GDS pass πάνω στο ίδιο, ήδη-φορτωμένο snapshot).
        summary["snapshot_date"] = read_snapshot_date()
        summary["generated_at"] = pd.Timestamp.now(tz=timezone.utc).isoformat()
        (OUT_DIR / f"summary_{label}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"GDS summary -> {OUT_DIR / f'summary_{label}.json'}".encode("ascii", "replace").decode("ascii"))
        print(f"Communities: {summary['n_communities']} · overlap top-10 PageRank/volume: {sanity['overlap']}/10")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
