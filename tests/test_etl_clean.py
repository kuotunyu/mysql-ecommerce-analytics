"""DB-free unit tests for the pandas cleaning rules in etl/load.py.

The full load path needs MySQL and the Kaggle download; these tests pin the
pure cleaning contracts so they can run in CI without either.
"""

import pandas as pd
import pytest

from etl.load import (
    build_geolocation_zip,
    clean_customers,
    clean_geolocation,
    clean_order_reviews,
    clean_orders,
)

ORDER_DT_COLS = [
    "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
    "order_delivered_customer_date", "order_estimated_delivery_date",
]


def test_clean_customers_strips_and_zero_pads_zip():
    df = pd.DataFrame({"customer_id": [" a "], "customer_zip_code_prefix": ["1234"]})
    out, note = clean_customers(df)
    assert out.loc[0, "customer_id"] == "a"
    assert out.loc[0, "customer_zip_code_prefix"] == "01234"
    assert note is None


def test_clean_orders_accepts_mysql_datetime_and_empty_as_null():
    df = pd.DataFrame({c: ["2017-10-02 10:56:33"] for c in ORDER_DT_COLS})
    df["order_approved_at"] = [""]  # empty string is NULL semantics, must not raise
    out, _ = clean_orders(df)
    assert out.loc[0, "order_approved_at"] == ""


def test_clean_orders_raises_on_malformed_timestamp():
    df = pd.DataFrame({c: ["2017-10-02 10:56:33"] for c in ORDER_DT_COLS})
    df["order_purchase_timestamp"] = ["02/10/2017 10:56"]
    with pytest.raises(ValueError):
        clean_orders(df)


def test_clean_geolocation_drops_exact_duplicate_rows_only():
    row = {
        "geolocation_zip_code_prefix": "1037", "geolocation_lat": "-23.5",
        "geolocation_lng": "-46.6", "geolocation_city": "sao paulo", "geolocation_state": "SP",
    }
    other = dict(row, geolocation_lat="-23.6")
    out, _ = clean_geolocation(pd.DataFrame([row, row, other]))
    assert len(out) == 2
    assert set(out["geolocation_zip_code_prefix"]) == {"01037"}


def test_clean_order_reviews_rejects_duplicate_review_order_pairs():
    base = {
        "review_id": "r1", "order_id": "o1", "review_creation_date": "2018-01-01 00:00:00",
        "review_answer_timestamp": "2018-01-02 00:00:00",
    }
    ok, note = clean_order_reviews(pd.DataFrame([base, dict(base, order_id="o2")]))
    assert len(ok) == 2 and "review_id" in note
    with pytest.raises(AssertionError):
        clean_order_reviews(pd.DataFrame([base, base]))


def test_build_geolocation_zip_filters_bbox_and_aggregates():
    geo = pd.DataFrame({
        "geolocation_zip_code_prefix": ["01037", "01037", "01037", "99999"],
        "geolocation_lat": ["-23.50", "-23.60", "-23.70", "40.0"],  # last point is outside Brazil
        "geolocation_lng": ["-46.60", "-46.60", "-46.60", "-3.0"],
        "geolocation_city": ["sao paulo", "sao paulo", "são paulo", "madrid"],
        "geolocation_state": ["SP", "SP", "SP", "ES"],
    })
    out = build_geolocation_zip(geo)
    assert list(out.columns) == ["zip_code_prefix", "lat", "lng", "city", "state", "n_points"]
    assert out["zip_code_prefix"].tolist() == ["01037"]  # foreign-only zip is dropped
    assert out.loc[0, "lat"] == "-23.60000000"  # median of three points
    assert out.loc[0, "city"] == "sao paulo"  # mode
    assert out.loc[0, "n_points"] == "3"
