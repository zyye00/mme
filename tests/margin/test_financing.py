from datetime import date

import pandas as pd
import pytest

from mme.common.baostock_requests import reserve_baostock_request
from mme.margin.download_details import download_margin_financing, standardize_details
from mme.margin.download_security_industries import standardize_security_industries
from mme.margin.profitability import (
    build_positions,
    calculate_cumulative_profitability,
    calculate_rolling_profitability,
    calculate_sample_coverage,
)
from mme.margin.summarize_first_day import annotate_security_types, summarize_security_types


def test_standardize_details_normalizes_sse_and_szse_rows() -> None:
    sse = standardize_details(
        pd.DataFrame({"标的证券代码": ["600000"], "标的证券简称": ["浦发银行"], "融资买入额": [10]}),
        "SSE",
        date(2026, 1, 5),
    )
    szse = standardize_details(
        pd.DataFrame({"证券代码": ["159001"], "证券简称": ["深100ETF"], "融资买入额": [20]}),
        "SZSE",
        date(2026, 1, 5),
    )

    assert sse.loc[0, "exchange"] == "SSE"
    assert szse.loc[0, "security_code"] == "159001"


def test_download_writes_complete_normalized_parquet(tmp_path) -> None:
    def calendar() -> pd.DataFrame:
        return pd.DataFrame({"trade_date": ["2026-01-05"]})

    def sse(_: str) -> pd.DataFrame:
        return pd.DataFrame({"标的证券代码": ["600000"], "标的证券简称": ["浦发银行"], "融资买入额": [10]})

    def szse(_: str) -> pd.DataFrame:
        return pd.DataFrame({"证券代码": ["159001"], "证券简称": ["深100ETF"], "融资买入额": [20]})

    output = tmp_path / "margin.parquet"
    result = download_margin_financing(date(2026, 1, 5), date(2026, 1, 5), output, sse, szse, calendar)

    assert len(result) == 2
    assert pd.read_parquet(output)["financing_buy_amount"].sum() == 30


def test_standardize_security_industries_normalizes_baostock_codes() -> None:
    industries = standardize_security_industries(
        pd.DataFrame(
            {
                "updateDate": ["2026-07-27", "2026-07-27"],
                "code": ["sh.600000", "sz.300001"],
                "code_name": ["浦发银行", "特锐德"],
                "industry": ["货币金融服务", "电气机械和器材制造业"],
                "industryClassification": ["证监会行业分类", "证监会行业分类"],
            }
        )
    )

    assert industries[["exchange", "security_code"]].to_dict("records") == [
        {"exchange": "SSE", "security_code": "600000"},
        {"exchange": "SZSE", "security_code": "300001"},
    ]
    assert industries.industry_update_date.astype(str).tolist() == ["2026-07-27", "2026-07-27"]


def test_annotate_security_types_clusters_detail_and_summarizes_amounts() -> None:
    selected = pd.DataFrame(
        {
            "security_code": ["510300", "600000", "300001"],
            "exchange": ["SSE", "SSE", "SZSE"],
            "financing_buy_amount": [20.0, 10.0, 30.0],
        }
    )
    basics = pd.DataFrame(
        {
            "security_code": ["510300", "600000", "300001"],
            "exchange": ["SSE", "SSE", "SZSE"],
            "security_type": ["etf", "stock", "stock"],
        }
    )

    annotated = annotate_security_types(selected, basics)
    summary = summarize_security_types(annotated)

    assert annotated["security_type"].tolist() == ["stock", "stock", "etf"]
    assert annotated["type_rank"].tolist() == [1, 2, 1]
    assert summary[["security_type", "security_count"]].to_dict("records") == [
        {"security_type": "stock", "security_count": 2},
        {"security_type": "etf", "security_count": 1},
    ]
    assert summary["sample_amount_ratio"].tolist() == pytest.approx([2 / 3, 1 / 3])


def test_profitability_interfaces_calculate_valid_cumulative_and_rolling_results() -> None:
    margin = pd.DataFrame(
        {
            'trade_date': pd.to_datetime(['2026-01-05', '2026-01-06']),
            'exchange': ['SSE', 'SSE'],
            'security_code': ['600000', '600000'],
            'security_name': ['浦发银行', '浦发银行'],
            'financing_buy_amount': [100.0, 200.0],
        }
    )
    prices = pd.DataFrame(
        {
            'trade_date': pd.to_datetime(['2026-01-05', '2026-01-06']),
            'exchange': ['SSE', 'SSE'],
            'security_code': ['600000', '600000'],
            'close': [10.0, 12.0],
            'close_unadjusted': [10.0, 12.0],
            'volume': [10.0, 10.0],
            'amount': [100.0, 120.0],
        }
    )
    sample = pd.DataFrame({'exchange': ['SSE'], 'security_code': ['600000']})

    positions = build_positions(margin, sample, prices)
    coverage = calculate_sample_coverage(margin, sample)
    cumulative = calculate_cumulative_profitability(positions, prices)
    rolling, overall = calculate_rolling_profitability(positions, prices, window_days=2)

    assert coverage.sample_coverage.tolist() == [1.0, 1.0]
    assert cumulative.profit_ratio.tolist() == pytest.approx([0.0, 1 / 3])
    assert rolling.loc[rolling.evaluation_date.eq(pd.Timestamp('2026-01-06')), 'rolling_profit_ratio'].item() == pytest.approx(1 / 3)
    assert overall.rolling_profit_ratio.tolist() == pytest.approx([0.0, 1 / 3])


def test_baostock_request_log_counts_rows_and_resets_on_a_new_day(tmp_path) -> None:
    request_log = tmp_path / 'requests.csv'

    assert reserve_baostock_request(
        'query_stock_basic', 2, request_log=request_log, request_date=date(2026, 7, 27)
    ) == 1
    assert reserve_baostock_request(
        'query_stock_industry', 2, request_log=request_log, request_date=date(2026, 7, 27)
    ) == 2
    with pytest.raises(RuntimeError, match='2/2'):
        reserve_baostock_request(
            'query_history_k_data_plus', 2, request_log=request_log, request_date=date(2026, 7, 27)
        )

    assert reserve_baostock_request(
        'query_stock_basic', 2, request_log=request_log, request_date=date(2026, 7, 28)
    ) == 1
    log = pd.read_csv(request_log, keep_default_na=False)
    assert len(log) == 1
    assert log.to_dict('records') == [
        {
            'request_date': '2026-07-28',
            'endpoint': 'query_stock_basic',
            'code': '',
            'adjustflag': '',
        }
    ]
