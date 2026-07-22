from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from mme.subscription.download_nav import (
    dividend_years,
    download_etf_navs,
    fetch_etf_dividends,
    fetch_etf_nav,
    fetch_etf_splits,
    fetch_with_retries,
    parse_split_ratio,
    validate_navs,
)


def fetch(symbol: str, indicator: str) -> pd.DataFrame:
    if symbol == "999999":
        raise RuntimeError("upstream unavailable")
    if indicator == "单位净值走势":
        return pd.DataFrame(
            {
                "净值日期": ["2026-01-02", "2026-01-05"],
                "单位净值": [1.0, 1.1],
                "日增长率": [None, 10.0],
            }
        )
    return pd.DataFrame(
        {
            "拆分折算日": ["2026-01-05"],
            "拆分类型": ["份额分拆"],
            "拆分折算比例": ["1:2.0000"],
        }
    )


def fetch_dividends(year: str, typ: str) -> pd.DataFrame:
    assert typ == "指数型-股票"
    if year == "2025":
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "基金代码": [510100.0, 999999.0],
            "权益登记日": ["2026-01-03", "2026-01-03"],
            "除息日期": ["2026-01-06", "2026-01-06"],
            "分红": [0.01, 0.02],
            "分红发放日": ["2026-01-10", "2026-01-10"],
        }
    )


def test_fetch_etf_nav_uses_only_unit_nav() -> None:
    navs = fetch_etf_nav("510100", fetch)

    assert navs.columns.tolist() == ["trade_date", "fund_code", "unit_nav", "daily_return_pct"]
    assert navs["fund_code"].tolist() == ["510100", "510100"]
    assert navs["unit_nav"].tolist() == [1.0, 1.1]


def test_fetch_etf_nav_rejects_missing_unit_nav_columns() -> None:
    def fetch_missing_column(symbol: str, indicator: str) -> pd.DataFrame:
        return pd.DataFrame({"净值日期": ["2026-01-02"], "单位净值": [1.0]})

    with pytest.raises(ValueError, match="单位净值数据缺少字段"):
        fetch_etf_nav("510100", fetch_missing_column)


def test_fetch_etf_dividends_standardizes_and_filters() -> None:
    dividends = fetch_etf_dividends(2026, {"510100"}, fetch_dividends)

    assert dividends.columns.tolist() == [
        "fund_code",
        "record_date",
        "ex_date",
        "cash_dividend_per_share",
        "payment_date",
    ]
    assert dividends.to_dict("records") == [
        {
            "fund_code": "510100",
            "record_date": pd.Timestamp("2026-01-03"),
            "ex_date": pd.Timestamp("2026-01-06"),
            "cash_dividend_per_share": 0.01,
            "payment_date": pd.Timestamp("2026-01-10"),
        }
    ]


def test_fetch_etf_dividends_allows_empty_response() -> None:
    dividends = fetch_etf_dividends(2025, {"510100"}, fetch_dividends)

    assert dividends.empty
    assert len(dividends.columns) == 5


def test_dividend_years_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="开始日期"):
        dividend_years(date(2026, 2, 1), date(2026, 1, 1))


def test_dividend_years_includes_each_calendar_year() -> None:
    assert dividend_years(date(2025, 12, 31), date(2027, 1, 1)) == [2025, 2026, 2027]


def test_fetch_etf_splits_standardizes_ratio() -> None:
    splits = fetch_etf_splits("510100", fetch)

    assert splits.columns.tolist() == ["fund_code", "split_date", "split_type", "split_ratio"]
    assert splits.loc[0, "split_ratio"] == 2.0


def test_fetch_etf_splits_returns_standard_empty_table() -> None:
    def fetch_empty_split(symbol: str, indicator: str) -> pd.DataFrame:
        assert symbol == "510100"
        assert indicator == "拆分详情"
        return pd.DataFrame()

    splits = fetch_etf_splits("510100", fetch_empty_split)

    assert splits.empty
    assert splits.columns.tolist() == ["fund_code", "split_date", "split_type", "split_ratio"]


@pytest.mark.parametrize(("value", "expected"), [("1:10.0000", 10.0), ("1:0.4998", 0.4998)])
def test_parse_split_ratio(value: str, expected: float) -> None:
    assert parse_split_ratio(value) == expected


def test_parse_split_ratio_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="无法解析"):
        parse_split_ratio("2:1")


def test_download_etf_navs_preserves_outputs_on_failure(tmp_path: Path) -> None:
    output_path = tmp_path / "etf_nav.parquet"
    dividend_path = tmp_path / "etf_dividends.parquet"
    messages: list[str] = []
    previous = pd.DataFrame({"marker": ["complete"]})
    previous.to_parquet(output_path, index=False)
    previous.to_parquet(dividend_path, index=False)

    with pytest.raises(RuntimeError, match="现有 Parquet 输出未被替换"):
        download_etf_navs(
            output_path,
            fund_codes={"510100", "999999"},
            dividend_start=date(2026, 1, 1),
            dividend_end=date(2026, 7, 19),
            request_interval=0,
            fetch=fetch,
            dividend_fetch=fetch_dividends,
            sleeper=lambda _: None,
            progress=messages.append,
        )

    assert pd.read_parquet(output_path).to_dict("records") == [{"marker": "complete"}]
    assert pd.read_parquet(dividend_path).to_dict("records") == [{"marker": "complete"}]
    assert output_path.with_name("etf_nav_failures.csv").exists()
    assert not (tmp_path / "etf_splits.parquet").exists()


def test_download_etf_navs_writes_complete_outputs(tmp_path: Path) -> None:
    output_path = tmp_path / "etf_nav.parquet"

    result = download_etf_navs(
        output_path,
        fund_codes={"510100"},
        dividend_start=date(2026, 1, 1),
        dividend_end=date(2026, 7, 19),
        request_interval=0,
        fetch=fetch,
        dividend_fetch=fetch_dividends,
        sleeper=lambda _: None,
        progress=lambda message: None,
    )

    assert result.successful_funds == 1
    assert result.failures == ()
    assert len(pd.read_parquet(output_path)) == 2
    assert len(pd.read_parquet(tmp_path / "etf_splits.parquet")) == 1
    assert len(pd.read_parquet(tmp_path / "etf_dividends.parquet")) == 1


def test_validate_navs_rejects_duplicates() -> None:
    navs = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "fund_code": ["510100", "510100"],
            "unit_nav": [1.0, 1.0],
            "daily_return_pct": [0.0, 0.0],
        }
    )

    with pytest.raises(ValueError, match="重复"):
        validate_navs(navs)


def test_validate_navs_rejects_non_positive_unit_nav() -> None:
    navs = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02"]),
            "fund_code": ["510100"],
            "unit_nav": [0.0],
            "daily_return_pct": [None],
        }
    )

    with pytest.raises(ValueError, match="单位净值必须为正"):
        validate_navs(navs)


def test_fetch_with_retries_recovers_from_transient_failure() -> None:
    attempts = 0
    messages: list[str] = []

    def operation() -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary")
        return pd.DataFrame({"value": [1]})

    result = fetch_with_retries(operation, "test", sleeper=lambda _: None, progress=messages.append)

    assert result.to_dict("records") == [{"value": 1}]
    assert attempts == 2
    assert messages == ["test：第 1 次请求失败，准备重试"]
