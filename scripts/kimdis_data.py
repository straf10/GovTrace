"""Κοινές βοηθητικές συναρτήσεις φόρτωσης raw δεδομένων ΚΗΜΔΗΣ.

Χρησιμοποιείται από compute_indicators_v1.py, build_entity_table.py και
build_site_data.py ώστε η λογική φόρτωσης (glob + concat + έτος/μήνας από
filename) να ζει σε ένα σημείο.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")


def load_entity(entity: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Φορτώνει και συνενώνει όλα τα διαθέσιμα <entity>_<YYYY>_<MM>.parquet.

    Προσθέτει _source_year/_source_month από το filename (πιο αξιόπιστο από
    τα raw πεδία ημερομηνίας, τα οποία λείπουν σε κάποιες εγγραφές).
    """
    files = sorted(raw_dir.glob(f"{entity}_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        _, year, month = f.stem.split("_")
        df["_source_year"] = int(year)
        df["_source_month"] = int(month)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)
