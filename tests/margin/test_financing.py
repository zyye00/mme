from datetime import date

import pandas as pd
import pytest
import mme.margin.download_prices as price_download

from mme.common.baostock_requests import reserve_baostock_request
from mme.margin.download_details import download_margin_financing, standardize_details
from mme.margin.download_security_basics import standardize_security_basics
from mme.margin.download_security_industries import standardize_security_industries
from mme.margin.profitability import (
    build_positions,
    calculate_cumulative_profitability,
    calculate_rolling_profitability,
    calculate_sample_coverage,
    select_rolling_securities,
)
from mme.margin.summarize_first_day import (
    GROUP_OUTPUT_COLUMNS,
    annotate_security_types,
    select_first_day_financing_groups,
    summarize_security_types,
)


class FakeBaoStockResult:
    def __init__(self, rows: list[list[str]], error_code: str = "0", error_msg: str = "") -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self._rows = iter(rows)

    def next(self) -> bool:
        try:
            self._row = next(self._rows)
        except StopIteration:
            return False
        return True

    def get_row_data(self) -> list[str]:
        return self._row


class FakeBaoStockLogin:
    error_code = "0"
    error_msg = ""


def price_universe() -> pd.DataFrame:
    return pd.DataFrame(
        {"exchange": ["SSE", "SZSE"], "security_code": ["600000", "000001"]}
    )


def price_responses() -> dict[tuple[str, str], tuple[list[list[str]], str, str]]:
    rows = [["2026-01-05", "10", "100", "1000"], ["2026-01-06", "11", "110", "1210"]]
    adjusted = [["2026-01-05", "10"], ["2026-01-06", "11"]]
    return {
        (f"{exchange}.{code}", adjustflag): (raw, "0", "")
        for exchange, code in [("sh", "600000"), ("sz", "000001")]
        for adjustflag, raw in [("3", rows), ("1", adjusted)]
    }


def patch_price_client(monkeypatch: pytest.MonkeyPatch, responses: dict[tuple[str, str], tuple[list[list[str]], str, str]]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def query(code: str, _: str, __: str, ___: str, ____: str, adjustflag: str) -> FakeBaoStockResult:
        calls.append((code, adjustflag))
        rows, error_code, error_msg = responses[(code, adjustflag)]
        return FakeBaoStockResult(rows, error_code, error_msg)

    monkeypatch.setattr(price_download.bs, "login", lambda: FakeBaoStockLogin())
    monkeypatch.setattr(price_download.bs, "logout", lambda: None)
    monkeypatch.setattr(price_download.bs, "query_history_k_data_plus", query)
    monkeypatch.setattr(price_download, "reserve_baostock_request", lambda *args: None)
    return calls


def financing_details(count: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [date(2026, 1, 5)] * count,
            "exchange": ["SSE"] * count,
            "security_code": [f"{index:06d}" for index in range(1, count + 1)],
            "security_name": [f"证券{index}" for index in range(1, count + 1)],
            "financing_buy_amount": range(count, 0, -1),
        }
    )


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


def test_download_prices_writes_sorted_complete_data(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_price_client(monkeypatch, price_responses())

    prices = price_download.download_prices(
        price_universe(), date(2026, 1, 5), date(2026, 1, 6), max_requests_per_day=10
    )

    assert prices.columns.tolist() == price_download.PRICE_COLUMNS
    assert prices[["trade_date", "exchange", "security_code"]].to_dict("records") == [
        {"trade_date": pd.Timestamp("2026-01-05"), "exchange": "SSE", "security_code": "600000"},
        {"trade_date": pd.Timestamp("2026-01-05"), "exchange": "SZSE", "security_code": "000001"},
        {"trade_date": pd.Timestamp("2026-01-06"), "exchange": "SSE", "security_code": "600000"},
        {"trade_date": pd.Timestamp("2026-01-06"), "exchange": "SZSE", "security_code": "000001"},
    ]
    assert prices[["close", "close_unadjusted", "volume", "amount"]].to_dict("records")[0] == {
        "close": 10.0,
        "close_unadjusted": 10.0,
        "volume": 100,
        "amount": 1000.0,
    }


def test_download_prices_normalizes_suspended_day_trading_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = price_responses()
    responses[("sh.600000", "3")] = (
        [["2026-01-05", "10", "", ""], ["2026-01-06", "11", "110", "1210"]],
        "0",
        "",
    )
    patch_price_client(monkeypatch, responses)

    prices = price_download.download_prices(
        price_universe(), date(2026, 1, 5), date(2026, 1, 6), max_requests_per_day=10
    )

    suspended = prices.loc[
        prices.exchange.eq("SSE") & prices.trade_date.eq(pd.Timestamp("2026-01-05"))
    ].iloc[0]
    assert suspended["volume"] == 0
    assert suspended["amount"] == 0


def test_download_prices_rejects_partially_missing_trading_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = price_responses()
    responses[("sh.600000", "3")] = (
        [["2026-01-05", "10", "", "1000"], ["2026-01-06", "11", "110", "1210"]],
        "0",
        "",
    )
    patch_price_client(monkeypatch, responses)

    with pytest.raises(ValueError, match="incomplete volume and amount"):
        price_download.download_prices(
            price_universe(), date(2026, 1, 5), date(2026, 1, 6), max_requests_per_day=10
        )


def test_download_prices_main_preserves_old_output_and_writes_partial_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    responses = price_responses()
    responses[("sz.000001", "3")] = ([], "1", "temporary failure")
    calls = patch_price_client(monkeypatch, responses)
    input_path = tmp_path / "groups.parquet"
    output_path = tmp_path / "prices.parquet"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    price_universe().to_parquet(input_path, index=False)
    pd.DataFrame({"marker": ["complete"]}).to_parquet(output_path, index=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_prices",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
        ],
    )

    assert price_download.main() == 1
    assert calls == [("sh.600000", "3"), ("sh.600000", "1"), ("sz.000001", "3")]
    assert pd.read_parquet(output_path).to_dict("records") == [{"marker": "complete"}]
    partial = tmp_path / "prices_partial.parquet"
    partial_data = pd.read_parquet(partial)
    assert partial_data[["exchange", "security_code"]].drop_duplicates().to_dict("records") == [
        {"exchange": "SSE", "security_code": "600000"}
    ]


def test_download_prices_main_removes_partial_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    patch_price_client(monkeypatch, price_responses())
    input_path = tmp_path / "groups.parquet"
    output_path = tmp_path / "prices.parquet"
    price_universe().to_parquet(input_path, index=False)
    partial = tmp_path / "prices_partial.parquet"
    pd.DataFrame({"stale": [1]}).to_parquet(partial, index=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_prices",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-06",
        ],
    )

    assert price_download.main() == 0
    assert len(pd.read_parquet(output_path)) == 4
    assert not partial.exists()


@pytest.mark.parametrize(
    ("universe", "message"),
    [
        (pd.DataFrame({"exchange": ["SSE", "SSE"], "security_code": ["600000", "600000"]}), "duplicate"),
        (pd.DataFrame({"exchange": ["HKEX"], "security_code": ["000001"]}), "unsupported"),
    ],
)
def test_download_prices_rejects_invalid_universe(universe: pd.DataFrame, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        price_download.download_prices(universe, date(2026, 1, 5), date(2026, 1, 6), 10)


def test_download_prices_rejects_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = price_responses()
    responses[("sh.600000", "1")] = ([], "0", "")
    patch_price_client(monkeypatch, responses)

    with pytest.raises(ValueError, match="no price data"):
        price_download.download_prices(price_universe(), date(2026, 1, 5), date(2026, 1, 6), 10)


def test_download_prices_rejects_mismatched_adjusted_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = price_responses()
    responses[("sh.600000", "1")] = ([['2026-01-05', '10']], "0", "")
    patch_price_client(monkeypatch, responses)

    with pytest.raises(ValueError, match="dates differ"):
        price_download.download_prices(price_universe(), date(2026, 1, 5), date(2026, 1, 6), 10)


def test_download_prices_rejects_duplicate_trade_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = price_responses()
    responses[("sh.600000", "3")] = (
        [['2026-01-05', '10', '100', '1000'], ['2026-01-05', '10', '100', '1000']],
        "0",
        "",
    )
    patch_price_client(monkeypatch, responses)

    with pytest.raises(ValueError, match="duplicate trade dates"):
        price_download.download_prices(price_universe(), date(2026, 1, 5), date(2026, 1, 6), 10)


def test_validate_prices_rejects_incomplete_security_set() -> None:
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-05"]),
            "exchange": ["SSE"],
            "security_code": ["600000"],
            "close": [10.0],
            "close_unadjusted": [10.0],
            "volume": [100],
            "amount": [1000.0],
        }
    )

    with pytest.raises(ValueError, match="incomplete"):
        price_download._validate_prices(prices, price_universe())


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


def test_security_metadata_standardizers_reject_empty_responses() -> None:
    basics = pd.DataFrame(columns=["code", "code_name", "ipoDate", "outDate", "type", "status"])
    industries = pd.DataFrame(
        columns=["updateDate", "code", "code_name", "industry", "industryClassification"]
    )

    with pytest.raises(ValueError, match="no security basic information"):
        standardize_security_basics(basics)
    with pytest.raises(ValueError, match="no security industry information"):
        standardize_security_industries(industries)


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


@pytest.mark.parametrize(("security_count", "middle_ranks"), [(30, [15, 16]), (31, [15, 16])])
def test_select_first_day_financing_groups_selects_highest_and_middle_groups(
    security_count: int, middle_ranks: list[int]
) -> None:
    groups = select_first_day_financing_groups(financing_details(security_count), group_size=2)

    assert groups.columns.tolist() == GROUP_OUTPUT_COLUMNS
    assert groups["tier"].tolist() == ["highest", "highest", "median", "median"]
    assert groups.loc[groups["tier"].eq("highest"), "first_day_rank"].tolist() == [1, 2]
    assert groups.loc[groups["tier"].eq("median"), "first_day_rank"].tolist() == middle_ranks


def test_select_first_day_financing_groups_breaks_amount_ties_deterministically() -> None:
    details = financing_details(6).assign(financing_buy_amount=100)
    details.loc[:, "exchange"] = ["SZSE", "SSE", "SSE", "SZSE", "SSE", "SZSE"]
    details = details.sample(frac=1, random_state=7).reset_index(drop=True)

    groups = select_first_day_financing_groups(details, group_size=2)

    assert groups.loc[groups["tier"].eq("highest"), ["exchange", "security_code"]].to_dict("records") == [
        {"exchange": "SSE", "security_code": "000002"},
        {"exchange": "SSE", "security_code": "000003"},
    ]


@pytest.mark.parametrize(("group_size", "message"), [(0, "positive"), (2, "at least 6")])
def test_select_first_day_financing_groups_rejects_invalid_inputs(group_size: int, message: str) -> None:
    details = financing_details(5)

    with pytest.raises(ValueError, match=message):
        select_first_day_financing_groups(details, group_size)


def test_select_first_day_financing_groups_rejects_duplicate_security_keys() -> None:
    details = financing_details(6)
    details.loc[5, ["exchange", "security_code"]] = details.loc[0, ["exchange", "security_code"]]

    with pytest.raises(ValueError, match="duplicate"):
        select_first_day_financing_groups(details, group_size=2)


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


def test_rolling_selection_applies_declared_exclusions_symmetrically() -> None:
    codes = ['511360', '511520', '510300', '600001', '600002', '600003', '600004', '600005']
    rolling = pd.DataFrame(
        {
            'evaluation_date': pd.Timestamp('2026-04-01'),
            'exchange': 'SSE',
            'security_code': codes,
            'security_name': codes,
            'rolling_profit_ratio': [1.0, 0.0, 0.95, 0.8, 0.7, 0.3, 0.2, 0.1],
            'rolling_weighted_return': [0.01, -0.01, 0.2, 0.1, 0.05, -0.05, -0.1, -0.2],
            'is_full_window': True,
        }
    )
    metadata = pd.DataFrame(
        {
            'exchange': 'SSE',
            'security_code': codes,
            'security_type': ['etf', 'etf', 'etf'] + ['stock'] * 5,
            'industry': ['非股票证券'] * 3 + ['测试行业'] * 5,
            'industry_classification': [pd.NA] * 3 + ['测试分类'] * 5,
            'industry_update_date': pd.Timestamp('2026-04-01'),
        }
    )

    selection, ratio_high, ratio_low, return_high, return_low = select_rolling_securities(
        rolling, metadata, excluded_security_codes={'511360', '511520'}, size=2
    )

    assert set(selection.security_type) == {'stock', 'etf'}
    assert not selection.security_code.isin({'511360', '511520'}).any()
    assert ratio_high.security_code.tolist() == ['510300', '600001']
    assert ratio_low.security_code.tolist() == ['600005', '600004']
    assert return_high.security_code.tolist() == ['510300', '600001']
    assert return_low.security_code.tolist() == ['600005', '600004']


def test_rolling_selection_requires_disjoint_eligible_groups() -> None:
    rolling = pd.DataFrame(
        {
            'exchange': 'SSE',
            'security_code': ['600001', '600002', '600003'],
            'security_name': ['A', 'B', 'C'],
            'rolling_profit_ratio': [0.9, 0.5, 0.1],
            'rolling_weighted_return': [0.1, 0.0, -0.1],
            'is_full_window': True,
        }
    )
    metadata = pd.DataFrame(
        {
            'exchange': 'SSE',
            'security_code': ['600001', '600002', '600003'],
            'security_type': 'stock',
            'industry': '测试行业',
            'industry_classification': '测试分类',
            'industry_update_date': pd.Timestamp('2026-04-01'),
        }
    )

    with pytest.raises(RuntimeError, match='At least 4 eligible securities'):
        select_rolling_securities(rolling, metadata, excluded_security_codes=set(), size=2)


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
