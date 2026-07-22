import pandas as pd
import pytest


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
