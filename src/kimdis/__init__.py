"""Client για το ΚΗΜΔΗΣ Open Data API."""

from .client import Endpoint, KimdisClient, PaginationIncompleteError, date_windows

__all__ = ["KimdisClient", "Endpoint", "date_windows", "PaginationIncompleteError"]
