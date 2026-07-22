from __future__ import annotations

from datetime import date

import pandas as pd

from mme.subscription.profitability import _cash_dividends, _eligible_dividend_events


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
