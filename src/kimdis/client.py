"""Client για το ΚΗΜΔΗΣ Open Data API.

Χαρακτηριστικά:
- Rate limiting με leaky-bucket (default 300 req/min, κάτω από το όριο 350 του API).
- Retry με exponential backoff σε 429 (σεβασμός Retry-After) και 5xx.
- Αυτόματη σελιδοποίηση (50 εγγραφές/σελίδα, page query param).
- Σπάσιμο μεγάλων χρονικών διαστημάτων σε παράθυρα ≤180 ημερών.

Το API δεν απαιτεί authentication. Άδεια δεδομένων: CC BY 4.0.
Swagger: https://cerpp.eprocurement.gov.gr/khmdhs-opendata/swagger-ui/index.html
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from datetime import date, timedelta
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class PaginationIncompleteError(RuntimeError):
    """Το API επέστρεψε λιγότερες εγγραφές από όσες δήλωσε στο totalElements
    (ή ένα 404 διέκοψε τη σελιδοποίηση πριν την τελευταία σελίδα).

    Σκόπιμα δεν καταπίνεται σιωπηλά: ο καλών (backfill) πρέπει να μην γράψει
    το parquet του μήνα και να τον καταγράψει ως αποτυχημένο (βλ. audit A1).
    """


BASE_URL = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata"

# Το research (βλ. RESEARCH_RESULTS.md) έδειξε ότι το API δέχεται εύρη ≥730 ημερών·
# δουλεύουμε συντηρητικά με παράθυρα 180 ημερών ούτως ή άλλως, αφού το backfill
# προχωρά ανά μήνα.
MAX_WINDOW_DAYS = 180

# Όριο API: 350 req/min. Δοκιμάστηκε 340 με ομοιόμορφο pacing: δεν έδωσε πρακτικό
# κέρδος ταχύτητας (το bottleneck είναι server-side latency σε συγκεκριμένες σελίδες,
# ~25s spikes, ανεξάρτητο από τον δικό μας ρυθμό) και αύξησε τα 429. Μένουμε στα 300.
DEFAULT_REQUESTS_PER_MINUTE = 300


class Endpoint(str, Enum):
    """Τα search endpoints του API (όλα POST, με query param `page`)."""

    REQUEST = "request"    # Αιτήματα (ΑΔΑΜ: ..REQ.........)
    NOTICE = "notice"      # Προσκλήσεις/Προκηρύξεις/Διακηρύξεις (PROC)
    AUCTION = "auction"    # Αναθέσεις (AWRD)
    CONTRACT = "contract"  # Συμβάσεις (SYMV)
    PAYMENT = "payment"    # Εντολές Πληρωμών (PAY)


# Το /request απαιτεί υποχρεωτικά αυτά τα boolean πεδία στο body.
REQUEST_REQUIRED_DEFAULTS = {"isInitial": True, "isApproved": False, "isApproval": False}


def date_windows(
    date_from: date, date_to: date, window_days: int = MAX_WINDOW_DAYS
) -> Iterator[tuple[date, date]]:
    """Σπάει το [date_from, date_to] σε διαδοχικά, μη επικαλυπτόμενα παράθυρα ≤window_days.

    Τα όρια είναι inclusive και στις δύο πλευρές (όπως τα dateFrom/dateTo του API).
    """
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from}) > date_to ({date_to})")
    start = date_from
    while start <= date_to:
        end = min(start + timedelta(days=window_days - 1), date_to)
        yield start, end
        start = end + timedelta(days=1)


class RateLimiter:
    """Ομοιόμορφος (leaky-bucket) rate limiter: σταθερό ελάχιστο interval μεταξύ αιτημάτων.

    Σκόπιμα όχι sliding-window με μετρητή: ένα sliding window επιτρέπει bursts όποτε
    δεν έχουν γίνει πρόσφατα αιτήματα (π.χ. μετά από μήνες που παραλείφθηκαν γιατί
    υπήρχαν ήδη τα αρχεία) — και το ΚΗΜΔΗΣ API φαίνεται να απαντά με 429 σε τέτοια
    bursts ακόμα και όταν το σύνολο/λεπτό είναι εντός ορίου. Η ομοιόμορφη κατανομή
    πετυχαίνει τον ίδιο μέσο ρυθμό χωρίς ποτέ να στέλνει ριπές.
    """

    def __init__(self, max_requests: int = DEFAULT_REQUESTS_PER_MINUTE, period: float = 60.0):
        self.min_interval = period / max_requests
        self._last_call: float | None = None

    def acquire(self) -> None:
        """Μπλοκάρει μέχρι να περάσει το ελάχιστο interval από το προηγούμενο αίτημα."""
        now = time.monotonic()
        if self._last_call is not None:
            wait = self._last_call + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        self._last_call = now


class KimdisClient:
    """HTTP client για το ΚΗΜΔΗΣ Open Data API με rate limiting, retries και pagination."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        max_retries: int = 6,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._limiter = RateLimiter(max_requests=requests_per_minute)
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> "KimdisClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ #
    # Χαμηλό επίπεδο: request με rate limit + retry
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Εκτελεί ένα αίτημα με rate limiting και retry σε 429/5xx/δικτυακά σφάλματα."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                delay = self._backoff(attempt)
                logger.warning(
                    "Δικτυακό σφάλμα (%s) στο %s, retry %d/%d σε %.1fs",
                    exc, path, attempt + 1, self.max_retries, delay,
                )
                time.sleep(delay)
                continue

            if response.status_code == 429:
                delay = self._retry_after(response) or self._backoff(attempt)
                logger.warning(
                    "429 Too Many Requests στο %s, retry %d/%d σε %.1fs",
                    path, attempt + 1, self.max_retries, delay,
                )
                time.sleep(delay)
                continue

            if response.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"{response.status_code} στο {path}",
                    request=response.request,
                    response=response,
                )
                delay = self._backoff(attempt)
                logger.warning(
                    "%d στο %s, retry %d/%d σε %.1fs",
                    response.status_code, path, attempt + 1, self.max_retries, delay,
                )
                time.sleep(delay)
                continue

            return response

        raise RuntimeError(
            f"Αποτυχία στο {path} μετά από {self.max_retries + 1} προσπάθειες"
        ) from last_error

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff με jitter: ~1, 2, 4, 8, 16, 32s (cap 60s)."""
        return min(2**attempt, 60) + random.uniform(0, 1)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # Αναζήτηση: μία σελίδα / όλες οι σελίδες / παράθυρα ημερομηνιών
    # ------------------------------------------------------------------ #

    def search_page(
        self, endpoint: Endpoint, criteria: dict[str, Any] | None = None, page: int = 0
    ) -> dict[str, Any]:
        """Επιστρέφει μία σελίδα αποτελεσμάτων (Spring Page object).

        Το 404 στη σελίδα 0 σημαίνει «δεν βρέθηκαν δεδομένα» και επιστρέφεται ως κενή
        σελίδα. Ένα 404 σε page>0 ΔΕΝ σημαίνει τέλος δεδομένων -- σημαίνει transient
        σφάλμα στη μέση της σελιδοποίησης (βλ. audit A1) και γίνεται raise, ώστε ο
        καλών να μην το εκλάβει σιωπηλά ως ολοκλήρωση.
        """
        body = dict(criteria or {})
        if endpoint is Endpoint.REQUEST:
            body = {**REQUEST_REQUIRED_DEFAULTS, **body}

        response = self._request(
            "POST", f"/{endpoint.value}", params={"page": page}, json=body
        )
        if response.status_code == 404:
            if page == 0:
                return {"content": [], "totalPages": 0, "totalElements": 0, "last": True}
            raise PaginationIncompleteError(
                f"{endpoint.value}: 404 στη σελίδα {page} (page>0) -- πιθανό transient "
                f"σφάλμα, όχι τέλος δεδομένων"
            )
        response.raise_for_status()
        return response.json()

    def iter_records(
        self, endpoint: Endpoint, criteria: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Διατρέχει όλες τις σελίδες μιας αναζήτησης και παράγει τις εγγραφές μία-μία.

        Ελέγχει το totalElements της πρώτης σελίδας έναντι του πλήθους των
        εγγραφών που τελικά παράχθηκαν· αν υπολείπεται, raise PaginationIncompleteError
        αντί να τερματίσει σιωπηλά (βλ. audit A1 -- σιωπηλή περικοπή δεδομένων).
        """
        page = 0
        total_elements: int | None = None
        yielded = 0
        while True:
            result = self.search_page(endpoint, criteria, page=page)
            if page == 0:
                total_elements = result.get("totalElements")
            content = result.get("content") or []
            yielded += len(content)
            yield from content
            if result.get("last", True) or not content:
                if total_elements is not None and yielded < total_elements:
                    raise PaginationIncompleteError(
                        f"{endpoint.value}: ολοκληρώθηκε στη σελίδα {page} με "
                        f"{yielded}/{total_elements} εγγραφές -- πιθανή σιωπηλή "
                        f"περικοπή pagination"
                    )
                logger.info(
                    "%s: ολοκληρώθηκε στη σελίδα %d (totalElements=%s)",
                    endpoint.value, page, total_elements,
                )
                return
            page += 1

    def iter_date_range(
        self,
        endpoint: Endpoint,
        date_from: date,
        date_to: date,
        criteria: dict[str, Any] | None = None,
        window_days: int = MAX_WINDOW_DAYS,
    ) -> Iterator[dict[str, Any]]:
        """Αντλεί όλες τις εγγραφές του [date_from, date_to], σπάζοντάς το σε παράθυρα ≤180 ημερών."""
        for win_from, win_to in date_windows(date_from, date_to, window_days):
            logger.info(
                "%s: παράθυρο %s → %s", endpoint.value, win_from.isoformat(), win_to.isoformat()
            )
            window_criteria = {
                **(criteria or {}),
                "dateFrom": win_from.isoformat(),
                "dateTo": win_to.isoformat(),
            }
            yield from self.iter_records(endpoint, window_criteria)

    # ------------------------------------------------------------------ #
    # Λοιπά endpoints
    # ------------------------------------------------------------------ #

    def adam_chain(self, reference_number: str) -> dict[str, Any]:
        """Επιστρέφει την αλυσίδα συνδεδεμένων πράξεων (REQ→PROC→AWRD→SYMV→PAY) ενός ΑΔΑΜ."""
        response = self._request("GET", f"/adamChain/{reference_number}")
        response.raise_for_status()
        return response.json()

    def pde_reference_numbers(self, pde_number: str) -> dict[str, Any]:
        """Επιστρέφει τους ΑΔΑΜ που σχετίζονται με έναν Ενάριθμο ΠΔΕ."""
        response = self._request("GET", "/pde", params={"pdeNumber": pde_number})
        response.raise_for_status()
        return response.json()

    def attachment_pdf(self, endpoint: Endpoint, reference_number: str) -> bytes:
        """Κατεβάζει το PDF μιας πράξης με βάση τον ΑΔΑΜ της."""
        response = self._request(
            "GET", f"/{endpoint.value}/attachment/{quote(reference_number, safe='')}"
        )
        response.raise_for_status()
        return response.content
