"""P2-07: κοινό snapshot_date για όλα τα graph exports.

Ο γράφος είναι offline/αναλώσιμος, ΔΕΝ ανανεώνεται από το nightly (βλ.
docs/PHASE_2.md R-06). Το snapshot_date γράφεται ΜΙΑ φορά από το
build_graph_staging.py (ημερομηνία του staging build) και διαβάζεται από
εδώ -- ΚΑΝΕΝΑ downstream script (GDS, Queries A-D) δεν παράγει δικό του
"τώρα", ώστε exports της ίδιας φόρτωσης γράφου να δείχνουν πάντα το ίδιο
snapshot date στο site.
"""

from __future__ import annotations

import json
from pathlib import Path

QA_REPORT_PATH = Path("data/graph_staging/qa_report.json")


def read_snapshot_date(qa_report_path: Path = QA_REPORT_PATH) -> str:
    if not qa_report_path.exists():
        raise SystemExit(
            f"snapshot_date δεν βρέθηκε -- {qa_report_path} δεν υπάρχει. "
            "Τρέξε πρώτα scripts/graph/build_graph_staging.py."
        )
    qa_report = json.loads(qa_report_path.read_text(encoding="utf-8"))
    snapshot_date = qa_report.get("snapshot_date")
    if not snapshot_date:
        raise SystemExit(
            f"{qa_report_path} δεν έχει πεδίο snapshot_date -- staging build παλιότερο "
            "από το P2-07. Ξανατρέξε το build_graph_staging.py."
        )
    return snapshot_date
