from datetime import date

import pytest

from kimdis.client import Endpoint, KimdisClient, PaginationIncompleteError, date_windows


def test_date_windows_single_window_when_within_limit():
    windows = list(date_windows(date(2024, 1, 1), date(2024, 1, 31), window_days=180))
    assert windows == [(date(2024, 1, 1), date(2024, 1, 31))]


def test_date_windows_splits_on_boundary():
    windows = list(date_windows(date(2024, 1, 1), date(2024, 1, 10), window_days=3))
    assert windows == [
        (date(2024, 1, 1), date(2024, 1, 3)),
        (date(2024, 1, 4), date(2024, 1, 6)),
        (date(2024, 1, 7), date(2024, 1, 9)),
        (date(2024, 1, 10), date(2024, 1, 10)),
    ]


def test_date_windows_single_day():
    windows = list(date_windows(date(2024, 1, 1), date(2024, 1, 1)))
    assert windows == [(date(2024, 1, 1), date(2024, 1, 1))]


def test_date_windows_rejects_inverted_range():
    with pytest.raises(ValueError):
        list(date_windows(date(2024, 2, 1), date(2024, 1, 1)))


def test_pagination_incomplete_error_is_a_runtime_error():
    assert issubclass(PaginationIncompleteError, RuntimeError)


def test_iter_records_completes_normally_when_totals_match():
    client = KimdisClient()
    pages = [
        {"content": [{"id": 1}, {"id": 2}], "totalElements": 3, "last": False},
        {"content": [{"id": 3}], "totalElements": 3, "last": True},
    ]
    client.search_page = lambda endpoint, criteria=None, page=0: pages[page]
    records = list(client.iter_records(Endpoint.AUCTION))
    assert [r["id"] for r in records] == [1, 2, 3]


def test_iter_records_raises_when_yielded_less_than_total(monkeypatch):
    """audit A1: ένα 404/κενή σελίδα στη μέση της σελιδοποίησης δεν πρέπει να
    τερματίσει σιωπηλά -- πρέπει να κάνει raise ώστε ο μήνας να μην γραφτεί
    ως πλήρης ενώ λείπουν εγγραφές."""
    client = KimdisClient()
    pages = [
        {"content": [{"id": 1}, {"id": 2}], "totalElements": 5, "last": False},
        {"content": [], "totalElements": 5, "last": True},  # transient 404 -> κενή σελίδα
    ]
    client.search_page = lambda endpoint, criteria=None, page=0: pages[page]
    with pytest.raises(PaginationIncompleteError):
        list(client.iter_records(Endpoint.AUCTION))


def test_search_page_404_on_page_zero_is_empty_result():
    client = KimdisClient()

    class FakeResponse:
        status_code = 404

    client._request = lambda method, path, **kw: FakeResponse()
    result = client.search_page(Endpoint.AUCTION, page=0)
    assert result == {"content": [], "totalPages": 0, "totalElements": 0, "last": True}


def test_search_page_404_on_later_page_raises():
    """audit A1: 404 σε page>0 δεν σημαίνει τέλος δεδομένων -- σφάλμα προς retry."""
    client = KimdisClient()

    class FakeResponse:
        status_code = 404

    client._request = lambda method, path, **kw: FakeResponse()
    with pytest.raises(PaginationIncompleteError):
        client.search_page(Endpoint.AUCTION, page=3)
