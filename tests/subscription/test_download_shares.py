from datetime import date

import pandas as pd
import pytest

from mme.subscription.download_shares import date_chunks, download_etf_shares, standardize_shares, validate_shares


def response(day: str, shares: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "序号": [1, 2],
            "基金代码": ["510100", "999999"],
            "基金简称": ["上证50", "其他"],
            "统计日期": [day, day],
            "基金份额": [shares, 50.0],
        }
    )


def szse_response(day: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": [day],
            "基金代码": ["159633"],
            "基金简称": ["中证1000"],
            "基金份额": [200.0],
        }
    )


def test_standardize_shares_filters_and_normalizes() -> None:
    shares = standardize_shares(response("2026-01-05"), "统计日期")

    assert shares.to_dict("records") == [
        {
            "fund_code": "510100",
            "fund_name": "上证50",
            "date": date(2026, 1, 5),
            "total_shares": 100.0,
        }
    ]


def test_standardize_shares_ignores_prelisting_zero_share_rows() -> None:
    raw = response("2026-01-05", shares=0)

    shares = standardize_shares(raw, "统计日期")

    assert shares.empty


def test_date_chunks_respects_szse_query_limit() -> None:
    chunks = date_chunks(date(2026, 1, 1), date(2026, 7, 16), maximum_days=180)

    assert chunks == [
        (date(2026, 1, 1), date(2026, 6, 29)),
        (date(2026, 6, 30), date(2026, 7, 16)),
    ]


def test_validate_shares_rejects_invalid_data() -> None:
    shares = standardize_shares(response("2026-01-05", shares=-1), "统计日期")

    with pytest.raises(ValueError, match="positive"):
        validate_shares(shares, [date(2026, 1, 5)])


def test_download_uses_trade_calendar_and_reports_progress(tmp_path) -> None:
    sse_requests: list[str] = []
    progress: list[str] = []

    def fetch_sse(day: str) -> pd.DataFrame:
        sse_requests.append(day)
        if day == "20260106":
            raise RuntimeError("temporary outage")
        return response(day)

    def fetch_calendar() -> pd.DataFrame:
        return pd.DataFrame({"trade_date": ["2026-01-05", "2026-01-06", "2026-01-08"]})

    result = download_etf_shares(
        date(2026, 1, 5),
        date(2026, 1, 8),
        tmp_path,
        fetch_sse,
        lambda start, end, symbol: szse_response("2026-01-08"),
        fetch_calendar,
        progress.append,
        {"510100", "159633"},
    )

    assert sse_requests == ["20260105", "20260106", "20260108"]
    assert result.successful_dates == 2
    assert result.empty_dates == ()
    assert result.failed_dates == ((date(2026, 1, 6), "temporary outage"),)
    assert any("SSE: downloading 3 trading days" in message for message in progress)
    assert any("SZSE: chunk 1/1 complete; rows=1" in message for message in progress)
    assert result.source_errors == ("SSE: 1 个交易日下载失败",)
    assert not (tmp_path / "sse_etf_shares_raw.parquet").exists()
    assert not (tmp_path / "etf_shares.parquet").exists()


def test_complete_download_replaces_outputs(tmp_path) -> None:
    def fetch_calendar() -> pd.DataFrame:
        return pd.DataFrame({"trade_date": ["2026-01-05"]})

    result = download_etf_shares(
        date(2026, 1, 5),
        date(2026, 1, 5),
        tmp_path,
        lambda day: response(day),
        lambda start, end, symbol: szse_response("2026-01-05"),
        fetch_calendar,
        lambda message: None,
        {"510100", "159633"},
    )

    assert result.source_errors == ()
    shares = pd.read_parquet(tmp_path / "etf_shares.parquet")
    assert set(shares["fund_code"]) == {"510100", "159633"}
    assert (tmp_path / "sse_etf_shares_raw.parquet").exists()
    assert (tmp_path / "szse_etf_shares_raw.parquet").exists()
