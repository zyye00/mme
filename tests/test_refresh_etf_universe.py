from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from refresh_etf_universe import (
    BASE_COLUMNS,
    INDUSTRIES,
    UNIVERSE_COLUMNS,
    build_universe,
    classify_industry_etfs,
    load_existing_universe,
    latest_candidate_codes,
)


def test_latest_candidate_codes_uses_each_market_latest_date(tmp_path: Path) -> None:
    sse_path = tmp_path / "sse.parquet"
    szse_path = tmp_path / "szse.parquet"
    pd.DataFrame({"统计日期": ["2026-07-15", "2026-07-16"], "基金代码": ["510050", "512480"]}).to_parquet(
        sse_path, index=False
    )
    pd.DataFrame({"日期": ["2026-07-15", "2026-07-16"], "基金代码": ["159915", "159819"]}).to_parquet(
        szse_path, index=False
    )

    assert latest_candidate_codes(sse_path, szse_path) == {"512480", "159819"}


def test_classify_industry_etfs_uses_tracking_target_only() -> None:
    overviews = pd.DataFrame(
        {
            "fund_code": ["512480", "512400", "512170", "588000"],
            "fund_name": ["半导体ETF", "有色ETF", "医疗ETF", "生物医药ETF"],
            "tracking_target": ["中证全指半导体产品与设备指数", "中证申万有色金属指数", "中证医疗指数", "中证生物医药指数"],
        }
    )

    industry = classify_industry_etfs(overviews, set())

    assert industry.loc[:, ["index_name", "fund_code"]].to_dict("records") == [
        {"index_name": "半导体", "fund_code": "512480"},
        {"index_name": "有色金属", "fund_code": "512400"},
    ]


def test_classify_industry_etfs_rejects_multiple_matches() -> None:
    overviews = pd.DataFrame(
        {
            "fund_code": ["512480"],
            "fund_name": ["行业ETF"],
            "tracking_target": ["半导体电力指数"],
        }
    )

    with pytest.raises(ValueError, match="多个行业"):
        classify_industry_etfs(overviews, set())


def test_build_universe_adds_tracking_targets_and_industry_groups() -> None:
    existing = pd.DataFrame(
        [["000016", "上证50", "1", "510050", "上证50ETF"]], columns=BASE_COLUMNS
    )
    overviews = pd.DataFrame(
        {
            "fund_code": ["510050", "512480"],
            "fund_name": ["上证50ETF", "半导体ETF"],
            "tracking_target": ["上证50指数", "中证全指半导体产品与设备指数"],
        }
    )

    universe = build_universe(existing, overviews)

    assert universe.columns.tolist() == UNIVERSE_COLUMNS
    assert universe["fund_code"].tolist() == ["510050", "512480"]
    assert universe.loc[1, "index_code"] == INDUSTRIES["半导体"][0]


def test_load_existing_universe_removes_previous_industry_groups(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    pd.DataFrame(
        [
            ["000016", "上证50", 1, "510050", "上证50ETF", "上证50指数"],
            ["industry_medicine", "医药", 11, "512170", "医疗ETF", "中证医疗指数"],
        ],
        columns=UNIVERSE_COLUMNS,
    ).to_csv(path, index=False)

    existing = load_existing_universe(path)

    assert existing["fund_code"].tolist() == ["510050"]
