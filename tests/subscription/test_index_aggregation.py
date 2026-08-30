from datetime import date

import pandas as pd
import pytest

from mme.subscription.profitability import _summarize_indexes


def test_index_profitability_and_bubble_return_use_amount_weights() -> None:
    batches = pd.DataFrame(
        {
            "estimated_subscription_amount": [100.0, 300.0],
            "return_to_date": [0.10, -0.05],
            "profitable_to_date": [True, False],
        }
    )

    profitable_amount = batches.loc[
        batches["profitable_to_date"], "estimated_subscription_amount"
    ].sum()
    total_amount = batches["estimated_subscription_amount"].sum()
    weighted_return = (
        batches["estimated_subscription_amount"] * batches["return_to_date"]
    ).sum() / total_amount

    assert profitable_amount / total_amount == pytest.approx(0.25)
    assert weighted_return == pytest.approx(-0.0125)


def test_redemption_does_not_offset_another_etf_subscription() -> None:
    share_changes = pd.Series([100.0, -80.0], index=["ETF A", "ETF B"])

    positive_subscriptions = share_changes.where(share_changes > 0).dropna()

    assert positive_subscriptions.to_dict() == {"ETF A": 100.0}


def test_first_share_record_is_not_counted_as_subscription() -> None:
    shares = pd.Series([1_000.0, 1_100.0])

    share_changes = shares - shares.shift()

    assert pd.isna(share_changes.iloc[0])
    assert share_changes.iloc[1] == 100.0


def test_split_adjustment_happens_before_subscription_detection() -> None:
    previous_shares = 1_000.0
    split_ratio = 2.0
    current_shares = 2_100.0

    adjusted_change = current_shares - previous_shares * split_ratio

    assert adjusted_change == 100.0


def test_60_trade_day_summary_uses_only_completed_batches() -> None:
    batches = pd.DataFrame(
        {
            "batch_id": [0, 1],
            "index_code": ["000300", "000300"],
            "index_name": ["沪深300", "沪深300"],
            "index_order": [1, 1],
            "subscription_date": [date(2026, 1, 5), date(2026, 1, 5)],
            "estimated_subscription_amount": [100.0, 300.0],
            "completed_60_trade_days": [True, False],
            "return_60_trade_days": [0.10, float("nan")],
            "profitable_60_trade_days": pd.Series([True, pd.NA], dtype="boolean"),
            "return_to_date": [0.10, -0.05],
            "profitable_to_date": [True, False],
        }
    )
    index_reference = batches[["index_code", "index_name", "index_order"]].drop_duplicates()

    daily, summary = _summarize_indexes(batches, index_reference)
    index_summary = summary.loc[summary["index_code"].eq("000300")].iloc[0]
    overall = summary.loc[summary["index_code"].eq("ALL")].iloc[0]

    assert daily.loc[0, "estimated_subscription_amount"] == 400.0
    assert daily.loc[0, "completed_60_trade_day_subscription_amount"] == 100.0
    assert daily.loc[0, "return_60_trade_days"] == pytest.approx(0.10)
    assert daily.loc[0, "return_to_date"] == pytest.approx(-0.0125)
    for row in [index_summary, overall]:
        assert row["subscription_amount"] == 400.0
        assert row["completed_60_trade_day_subscription_amount"] == 100.0
        assert row["profitable_amount_60_trade_days"] == 100.0
        assert row["profitable_capital_ratio_60_trade_days"] == pytest.approx(1.0)
        assert row["profitable_capital_ratio_to_date"] == pytest.approx(0.25)
        assert row["subscription_batches"] == 2
        assert row["completed_60_trade_day_batches"] == 1
        assert row["completed_60_trade_day_batch_ratio"] == pytest.approx(0.5)
