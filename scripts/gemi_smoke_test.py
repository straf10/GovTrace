"""P2-B1: smoke test του GEMI_API_KEY -- επιβεβαιώνει auth + βασικά endpoints.

Χρήση:
    python scripts/gemi_smoke_test.py --afm 094014201

Διαβάζει GEMI_API_KEY από env var ή τοπικό .env (ποτέ commit το key).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Windows console default (cp1252) σκάει σε ελληνικά χαρακτήρες -- αναγκάζουμε UTF-8
# στο stdout/stderr αντί να απαιτούμε PYTHONIOENCODING στο περιβάλλον του χρήστη.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemi import GemiClient  # noqa: E402


def load_dotenv(env_path: Path = Path(".env")) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--afm", help="ΑΦΜ δοκιμής (π.χ. ενός γνωστού αναδόχου)")
    args = parser.parse_args()

    load_dotenv()
    api_key = (os.environ.get("GEMI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("GEMI_API_KEY λείπει (env var ή .env). Βλ. .env.example.")

    with GemiClient(api_key=api_key) as client:
        print("== /health ==")
        try:
            print(json.dumps(client.health(), ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001 -- smoke test, θέλουμε να δούμε ό,τι σκάσει
            print(f"  (health endpoint: {exc})")

        print("\n== /companies?afm=... ==")
        afm = args.afm or "094014201"
        company = client.find_by_afm(afm)
        if company is None:
            print(f"  Δεν βρέθηκε εταιρεία με ΑΦΜ {afm}")
        else:
            print(json.dumps(
                {
                    "arGemi": company.ar_gemi,
                    "afm": company.afm,
                    "name_el": company.name_el,
                    "legal_type": company.legal_type,
                    "status": company.status,
                    "incorporation_date": company.incorporation_date,
                },
                ensure_ascii=False, indent=2,
            ))

            if company.ar_gemi:
                print("\n== /companies/{arGemi} ==")
                full = client.get_company(str(company.ar_gemi))
                n_persons = len(full.raw.get("persons") or [])
                print(f"  πλήρες προφίλ OK -- {n_persons} καταχωρημένα πρόσωπα")

    print("\nSmoke test OK.")


if __name__ == "__main__":
    main()
