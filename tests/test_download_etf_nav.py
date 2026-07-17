from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from download_etf_nav import (
    download_etf_navs,
    fetch_etf_nav,
    fetch_etf_splits,
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
    if indicator == "累计净值走势":
        return pd.DataFrame({"净值日期": ["2026-01-02", "2026-01-05"], "累计净值": [1.0, 1.1]})
    return pd.DataFrame(
        {
            "拆分折算日": ["2026-01-05"],
            "拆分类型": ["份额分拆"],
            "拆分折算比例": ["1:2.0000"],
        }
    )


def test_fetch_etf_nav_merges_standard_columns() -> None:
    navs = fetch_etf_nav("510100", fetch)

    assert navs.columns.tolist() == ["trade_date", "fund_code", "unit_nav", "cumulative_nav", "daily_return_pct"]
    assert navs["fund_code"].tolist() == ["510100", "510100"]
    assert navs["cumulative_nav"].tolist() == [1.0, 1.1]


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


def test_download_etf_navs_writes_data_and_failures(tmp_path: Path) -> None:
    output_path = tmp_path / "etf_nav.parquet"
    messages: list[str] = []

    result = download_etf_navs(
        output_path,
        fund_codes={"510100", "999999"},
        request_interval=0,
        fetch=fetch,
        sleeper=lambda _: None,
        progress=messages.append,
    )

    assert result.successful_funds == 1
    assert result.failures[0][0] == "999999"
    assert len(pd.read_parquet(output_path)) == 2
    assert len(pd.read_parquet(tmp_path / "etf_splits.parquet")) == 1
    assert output_path.with_name("etf_nav_failures.csv").exists()
    assert any("完成" in message for message in messages)


def test_validate_navs_rejects_duplicates() -> None:
    navs = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "fund_code": ["510100", "510100"],
            "unit_nav": [1.0, 1.0],
            "cumulative_nav": [1.0, 1.0],
            "daily_return_pct": [0.0, 0.0],
        }
    )

    with pytest.raises(ValueError, match="重复"):
        validate_navs(navs)
