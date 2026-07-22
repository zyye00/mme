from datetime import date

import pandas as pd

from mme.margin.build_etf_details import filter_etfs
from mme.margin.download_details import download_margin_financing, standardize_details
from mme.margin.summarize_first_day import analyze_first_day


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


def test_analyze_uses_minimal_etf_prefix() -> None:
    details = pd.DataFrame(
        {
            "trade_date": [date(2026, 1, 5)] * 3,
            "exchange": ["SSE", "SZSE", "SSE"],
            "security_code": ["510300", "000001", "204001"],
            "security_name": ["沪深300ETF", "平安银行", "国债逆回购"],
            "financing_buy_amount": [60, 25, 15],
        }
    )
    basics = pd.DataFrame(
        {
            "exchange": ["SSE", "SZSE", "SSE"],
            "security_code": ["510300", "000001", "204001"],
            "security_name": ["沪深300ETF", "平安银行", "国债逆回购"],
            "security_type": ["etf", "stock", "other"],
        }
    )
    selected, summary = analyze_first_day(filter_etfs(details, basics), 0.8)

    assert selected["security_code"].tolist() == ["510300"]
    assert selected.iloc[-1]["cumulative_ratio"] == 1
    assert summary.loc[0, "security_count"] == 1
