"""Client για το ΓΕΜΗ Open Data API (opendata-api.businessportal.gr).

Χαρακτηριστικά (κατά το πρότυπο του src/kimdis/client.py):
- Rate limiting με leaky-bucket (default 8 req/min -- το επιβεβαιωμένο όριο
  του API, βλ. email έγκρισης πρόσβασης 2026-07-14. ΠΟΛΥ πιο αυστηρό από το
  ΚΗΜΔΗΣ (300 req/min) -- P2-B2 πρέπει να λογαριάζει μέρες, όχι ώρες, για
  δεκάδες χιλιάδες ΑΦΜ).
- Retry με exponential backoff σε 429, 503 (documented ως "υπερφορτωμένο
  σύστημα") και γενικά 5xx.
- Αυθεντικοποίηση: header `api_key` (swagger securityDefinitions, όχι Bearer).

Swagger: https://opendata-api.businessportal.gr/api-docs
Τεκμηρίωση: https://opendata.businessportal.gr

Το API key ΠΟΤΕ δεν μπαίνει σε κώδικα/commit -- διαβάζεται από
GEMI_API_KEY (env var ή τοπικό .env, βλ. .env.example).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://opendata-api.businessportal.gr/api/opendata/v1"

# Επιβεβαιωμένο όριο (email ΓΕΜΗ 2026-07-14): 8 req/min. Αίτημα για υψηλότερο
# όριο μπορεί να σταλεί στο support@uhc.gr (λόγος χρήσης + αναμενόμενος όγκος
# + χρονικό διάστημα) -- βλ. R-02 στο docs/PHASE_2.md.
DEFAULT_REQUESTS_PER_MINUTE = 8

DEFAULT_RESULTS_SIZE = 50


class GemiApiError(RuntimeError):
    """Το API επέστρεψε 400/404/500+ μετά την εξάντληση των retries."""


@dataclass
class Company:
    """Υποσύνολο των δημόσιων πεδίων ΓΕΜΗ που χρειάζεται το project (P2-B5).

    Το raw JSON του API έχει πολύ περισσότερα πεδία (persons, capital, stocks,
    activities...) -- κρατάμε εδώ μόνο ό,τι έχει χρήση στο site προς το παρόν,
    το raw dict μένει προσβάσιμο μέσω ``raw``.
    """

    ar_gemi: int | None
    afm: str | None
    name_el: str | None
    legal_type: str | None
    status: str | None
    incorporation_date: str | None
    is_active: bool | None
    is_branch: bool | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Company":
        legal_type = data.get("legalType") or {}
        status = data.get("status") or {}
        return cls(
            ar_gemi=data.get("arGemi"),
            afm=data.get("afm"),
            name_el=data.get("coNameEl"),
            legal_type=legal_type.get("descr"),
            status=status.get("descr"),
            incorporation_date=data.get("incorporationDate"),
            is_active=data.get("autoRegistered"),
            is_branch=data.get("isBranch"),
            raw=data,
        )


class RateLimiter:
    """Ομοιόμορφος (leaky-bucket) rate limiter -- βλ. src/kimdis/client.py
    για το σκεπτικό της ομοιόμορφης έναντι sliding-window κατανομής."""

    def __init__(self, max_requests: int = DEFAULT_REQUESTS_PER_MINUTE, period: float = 60.0):
        self.min_interval = period / max_requests
        self._last_call: float | None = None

    def acquire(self) -> None:
        now = time.monotonic()
        if self._last_call is not None:
            wait = self._last_call + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        self._last_call = now


class GemiClient:
    """HTTP client για το ΓΕΜΗ Open Data API με rate limiting και retries."""

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        max_retries: int = 6,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise ValueError("api_key είναι υποχρεωτικό (GEMI_API_KEY)")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._limiter = RateLimiter(max_requests=requests_per_minute)
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json", "api_key": api_key},
        )

    def __enter__(self) -> "GemiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ #
    # Χαμηλό επίπεδο: request με rate limit + retry
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
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

            if response.status_code == 401:
                raise GemiApiError(
                    "401 Unauthorized -- ελέγξτε το GEMI_API_KEY (τιμή/κενά/newline)"
                )

            if response.status_code in (429, 503):
                delay = self._retry_after(response) or self._backoff(attempt)
                logger.warning(
                    "%d στο %s, retry %d/%d σε %.1fs",
                    response.status_code, path, attempt + 1, self.max_retries, delay,
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

        raise GemiApiError(
            f"Αποτυχία στο {path} μετά από {self.max_retries + 1} προσπάθειες"
        ) from last_error

    @staticmethod
    def _backoff(attempt: int) -> float:
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
    # Endpoints
    # ------------------------------------------------------------------ #

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/health")
        response.raise_for_status()
        return response.json() if response.content else {}

    def search_companies(
        self,
        afm: str | None = None,
        name: str | None = None,
        ar_gemi: str | None = None,
        results_offset: int = 0,
        results_size: int = DEFAULT_RESULTS_SIZE,
        **extra_criteria: Any,
    ) -> dict[str, Any]:
        """GET /companies -- επιστρέφει raw {searchMetadata, searchResults}."""
        params: dict[str, Any] = {"resultsOffset": results_offset, "resultsSize": results_size}
        if afm:
            params["afm"] = afm
        if name:
            params["name"] = name
        if ar_gemi:
            params["arGemi"] = ar_gemi
        params.update(extra_criteria)

        response = self._request("GET", "/companies", params=params)
        if response.status_code == 404:
            return {"searchMetadata": {"totalCount": 0}, "searchResults": []}
        response.raise_for_status()
        return response.json()

    def find_by_afm(self, afm: str) -> Company | None:
        """Αναζήτηση με ακριβές ΑΦΜ -- επιστρέφει τη μητρική εγγραφή ή None.

        Το ίδιο ΑΦΜ επιστρέφει συχνά >1 εγγραφή όταν η εταιρεία έχει
        υποκαταστήματα (κάθε ``arGemi`` καλύπτει και μητρική και branches) --
        προτιμάμε το πρώτο αποτέλεσμα με ``isBranch=False``· αν όλα είναι
        branches (απροσδόκητο), πέφτουμε στο πρώτο.
        """
        result = self.search_companies(afm=afm, results_size=10)
        results = result.get("searchResults") or []
        if not results:
            return None
        parent = next((r for r in results if r.get("isBranch") is False), None)
        return Company.from_api(parent if parent is not None else results[0])

    def get_company(self, ar_gemi: str) -> Company:
        """GET /companies/{arGemi} -- πλήρες προφίλ (persons, capital, κλπ)."""
        response = self._request("GET", f"/companies/{ar_gemi}")
        response.raise_for_status()
        return Company.from_api(response.json())
