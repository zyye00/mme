from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from mme.subscription.profitability import _calculate_batches, _cash_dividends, _eligible_dividend_events


def test_cash_dividends_require_earlier_subscription_and_paid_date() -> None:
    batches = pd.DataFrame(
        {
            "batch_id": [0, 1, 2],
            "fund_code": ["510300", "510300", "510300"],
            "subscription_date": [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 1)],
            "as_of_date": [date(2026, 1, 10), date(2026, 1, 10), date(2026, 1, 6)],
        }
    )
    dividends = pd.DataFrame(
        {
            "fund_code": ["510300"],
            "record_date": [date(2026, 1, 5)],
            "ex_date": [date(2026, 1, 6)],
            "cash_dividend_per_share": [0.1],
            "payment_date": [date(2026, 1, 8)],
        }
    )

    events = _eligible_dividend_events(batches, dividends, "as_of_date")

    assert events["batch_id"].tolist() == [0]
    assert _cash_dividends(batches, dividends, "as_of_date").tolist() == [0.1, 0.0, 0.0]


@pytest.mark.parametrize(("nav_count", "completed"), [(60, False), (61, True), (62, True)])
def test_60_trade_day_return_requires_a_complete_holding_period(nav_count: int, completed: bool) -> None:
    dates = pd.bdate_range("2026-01-02", periods=nav_count).date
    shares = pd.DataFrame(
        {"fund_code": ["510300"], "date": [dates[0]], "net_subscription_shares": [100.0]}
    )
    navs = pd.DataFrame(
        {
            "fund_code": ["510300"] * nav_count,
            "date": dates,
            "unit_nav": [1 + position / 100 for position in range(nav_count)],
        }
    )
    dividends = pd.DataFrame(
        columns=["fund_code", "record_date", "ex_date", "cash_dividend_per_share", "payment_date"]
    )

    batch = _calculate_batches(shares, navs, dividends, dates[-1]).iloc[0]

    assert bool(batch["completed_60_trade_days"]) is completed
    assert batch["return_60_nav_position"] == 60
    assert batch["return_to_date"] == pytest.approx((nav_count - 1) / 100)
    if completed:
        assert batch["return_60_date"] == dates[60]
        assert batch["return_60_trade_days"] == pytest.approx(0.6)
        assert bool(batch["profitable_60_trade_days"])
    else:
        assert pd.isna(batch["return_60_date"])
        assert pd.isna(batch["return_60_unit_nav"])
        assert pd.isna(batch["cash_dividend_per_share_60_trade_days"])
        assert pd.isna(batch["return_60_trade_days"])
        assert pd.isna(batch["profitable_60_trade_days"])
