"""Calculate ETF subscription profitability at the index level."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from etf_universe import load_etf_universe

SHARE_COLUMNS = {"date", "fund_code", "fund_name", "total_shares"}
NAV_COLUMNS = {"trade_date", "fund_code", "unit_nav", "daily_return_pct"}
SPLIT_COLUMNS = {"fund_code", "split_date", "split_type", "split_ratio"}
DIVIDEND_COLUMNS = {"fund_code", "record_date", "ex_date", "cash_dividend_per_share", "payment_date"}


@dataclass(frozen=True)
class EtfProfitabilityResult:
    """Calculated data consumed by the analysis notebook."""

    as_of_date: date
    universe: pd.DataFrame
    index_reference: pd.DataFrame
    fund_name_map: pd.Series
    batches: pd.DataFrame
    index_daily_batches: pd.DataFrame
    summary: pd.DataFrame
    split_adjustments: pd.DataFrame
    dividend_events: pd.DataFrame


def calculate_etf_profitability(
    shares_path: Path,
    nav_path: Path,
    splits_path: Path,
    dividends_path: Path,
    universe_path: Path,
    requested_as_of_date: date | None = None,
) -> EtfProfitabilityResult:
    """Load ETF datasets and calculate subscription profitability."""
    for path in [shares_path, nav_path, splits_path, dividends_path, universe_path]:
        if not path.exists():
            raise FileNotFoundError(f"Required input does not exist: {path}")

    universe = load_etf_universe(universe_path)
    expected_fund_codes = set(universe["fund_code"])
    fund_name_map = universe.set_index("fund_code")["fund_name"]
    index_reference = universe[["index_code", "index_name", "index_order"]].drop_duplicates().sort_values("index_order")

    shares = _load_shares(shares_path, expected_fund_codes, universe)
    navs = _load_navs(nav_path, expected_fund_codes, universe)
    splits = _load_splits(splits_path)
    dividends = _load_dividends(dividends_path, expected_fund_codes)
    as_of_date = _resolve_as_of_date(navs, len(expected_fund_codes), requested_as_of_date)
    shares, split_adjustments = _adjust_share_splits(shares, splits, as_of_date)
    batches = _calculate_batches(shares, navs, dividends, as_of_date)
    batches = batches.merge(
        universe[["fund_code", "fund_name", "index_code", "index_name", "index_order"]],
        on="fund_code",
        how="left",
        validate="many_to_one",
    )
    index_daily_batches, summary = _summarize_indexes(batches, index_reference)
    dividend_events = _eligible_dividend_events(batches, dividends, "as_of_date")
    return EtfProfitabilityResult(
        as_of_date=as_of_date,
        universe=universe,
        index_reference=index_reference,
        fund_name_map=fund_name_map,
        batches=batches,
        index_daily_batches=index_daily_batches,
        summary=summary,
        split_adjustments=split_adjustments,
        dividend_events=dividend_events,
    )


def _normalize_codes(values: pd.Series) -> pd.Series:
    return values.astype("string").str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _load_shares(path: Path, expected_fund_codes: set[str], universe: pd.DataFrame) -> pd.DataFrame:
    shares = pd.read_parquet(path)
    missing_columns = SHARE_COLUMNS - set(shares.columns)
    if missing_columns:
        raise ValueError(f"份额数据缺少字段：{', '.join(sorted(missing_columns))}")
    shares = shares.loc[:, sorted(SHARE_COLUMNS)].copy()
    shares["fund_code"] = _normalize_codes(shares["fund_code"])
    shares["date"] = pd.to_datetime(shares["date"], errors="raise").dt.date
    shares["total_shares"] = pd.to_numeric(shares["total_shares"], errors="raise")
    if shares.duplicated(["date", "fund_code"]).any() or (shares["total_shares"] <= 0).any():
        raise ValueError("份额数据包含重复记录或非正份额。")
    missing_funds = expected_fund_codes - set(shares["fund_code"])
    if missing_funds:
        names = universe.loc[universe["fund_code"].isin(missing_funds), "fund_name"]
        raise ValueError(f"份额数据缺少映射 ETF：{'、'.join(names)}")
    return shares.loc[shares["fund_code"].isin(expected_fund_codes)].copy()


def _load_navs(path: Path, expected_fund_codes: set[str], universe: pd.DataFrame) -> pd.DataFrame:
    navs = pd.read_parquet(path)
    missing_columns = NAV_COLUMNS - set(navs.columns)
    if missing_columns:
        raise ValueError(f"净值数据缺少字段：{', '.join(sorted(missing_columns))}")
    navs = navs.loc[:, list(NAV_COLUMNS)].rename(columns={"trade_date": "date"}).copy()
    navs["fund_code"] = _normalize_codes(navs["fund_code"])
    navs["date"] = pd.to_datetime(navs["date"], errors="raise").dt.date
    navs[["unit_nav", "daily_return_pct"]] = navs[["unit_nav", "daily_return_pct"]].apply(
        pd.to_numeric, errors="raise"
    )
    if navs.duplicated(["date", "fund_code"]).any() or (navs["unit_nav"] <= 0).any():
        raise ValueError("净值数据包含重复记录或非正净值。")
    navs = navs.loc[navs["fund_code"].isin(expected_fund_codes)].copy()
    missing_funds = expected_fund_codes - set(navs["fund_code"])
    if missing_funds:
        names = universe.loc[universe["fund_code"].isin(missing_funds), "fund_name"]
        raise ValueError(f"净值数据缺少映射 ETF：{'、'.join(names)}")
    return navs


def _load_splits(path: Path) -> pd.DataFrame:
    splits = pd.read_parquet(path)
    missing_columns = SPLIT_COLUMNS - set(splits.columns)
    if missing_columns:
        raise ValueError(f"拆分数据缺少字段：{', '.join(sorted(missing_columns))}")
    splits = splits.loc[:, list(SPLIT_COLUMNS)].copy()
    splits["fund_code"] = _normalize_codes(splits["fund_code"])
    splits["split_date"] = pd.to_datetime(splits["split_date"], errors="raise").dt.date
    splits["split_ratio"] = pd.to_numeric(splits["split_ratio"], errors="raise")
    if (splits["split_ratio"] <= 0).any():
        raise ValueError("拆分折算比例必须为正。")
    return splits


def _load_dividends(path: Path, expected_fund_codes: set[str]) -> pd.DataFrame:
    dividends = pd.read_parquet(path)
    missing_columns = DIVIDEND_COLUMNS - set(dividends.columns)
    if missing_columns:
        raise ValueError(f"分红数据缺少字段：{', '.join(sorted(missing_columns))}")
    dividends = dividends.loc[:, sorted(DIVIDEND_COLUMNS)].copy()
    dividends["fund_code"] = _normalize_codes(dividends["fund_code"])
    for column in ["record_date", "ex_date", "payment_date"]:
        dividends[column] = pd.to_datetime(dividends[column], errors="coerce").dt.date
    dividends["cash_dividend_per_share"] = pd.to_numeric(dividends["cash_dividend_per_share"], errors="coerce")
    dividends = dividends.loc[dividends["fund_code"].isin(expected_fund_codes)].copy()
    if dividends[["record_date", "ex_date", "cash_dividend_per_share"]].isna().any().any() or (
        dividends["cash_dividend_per_share"] <= 0
    ).any():
        raise ValueError("分红数据包含无法解析的日期、金额或非正分红。")
    if dividends.duplicated(["fund_code", "record_date", "ex_date", "payment_date"]).any():
        raise ValueError("分红数据包含重复事件。")
    return dividends


def _resolve_as_of_date(navs: pd.DataFrame, fund_count: int, requested_as_of_date: date | None) -> date:
    eligible_navs = navs
    if requested_as_of_date:
        eligible_navs = eligible_navs.loc[eligible_navs["date"] <= requested_as_of_date]
    common_dates = eligible_navs.groupby("date")["fund_code"].nunique()
    common_dates = common_dates.index[common_dates == fund_count]
    if common_dates.empty:
        raise ValueError("没有覆盖全部 ETF 的共同评价日。")
    return max(common_dates)


def _adjust_share_splits(shares: pd.DataFrame, splits: pd.DataFrame, as_of_date: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    shares = shares.loc[shares["date"] <= as_of_date].sort_values(["fund_code", "date"]).copy()
    share_bounds = shares.groupby("fund_code", as_index=False).agg(first_date=("date", "min"), last_date=("date", "max"))
    relevant_splits = splits.merge(share_bounds, on="fund_code", how="inner")
    relevant_splits = relevant_splits.loc[
        (relevant_splits["split_date"] >= relevant_splits["first_date"])
        & (relevant_splits["split_date"] <= relevant_splits["last_date"])
    ].copy()
    share_keys = pd.MultiIndex.from_frame(shares[["fund_code", "date"]])
    split_keys = pd.MultiIndex.from_frame(relevant_splits[["fund_code", "split_date"]])
    unmatched_splits = relevant_splits.loc[~split_keys.isin(share_keys)]
    if not unmatched_splits.empty:
        unmatched = unmatched_splits[["fund_code", "split_date"]].astype(str).agg("/".join, axis=1)
        raise ValueError(f"拆分日期无法匹配份额记录：{', '.join(unmatched)}")
    combined_splits = relevant_splits.groupby(["fund_code", "split_date"], as_index=False).agg(
        split_type=("split_type", lambda values: "、".join(sorted(set(values)))),
        split_ratio=("split_ratio", "prod"),
    )
    shares = shares.merge(
        combined_splits,
        left_on=["fund_code", "date"],
        right_on=["fund_code", "split_date"],
        how="left",
        validate="one_to_one",
    )
    shares["previous_total_shares"] = shares.groupby("fund_code")["total_shares"].shift()
    split_mask = shares["split_date"].notna()
    if shares.loc[split_mask, "previous_total_shares"].isna().any():
        raise ValueError("拆分日期缺少前一条份额记录，无法自动调整。")
    shares["unadjusted_share_change"] = shares["total_shares"] - shares["previous_total_shares"]
    shares["net_subscription_shares"] = shares["total_shares"] - shares["previous_total_shares"] * shares[
        "split_ratio"
    ].fillna(1.0)
    columns = [
        "fund_code",
        "split_date",
        "split_type",
        "split_ratio",
        "previous_total_shares",
        "total_shares",
        "unadjusted_share_change",
        "net_subscription_shares",
    ]
    return shares, shares.loc[split_mask, columns].copy()


def _calculate_batches(shares: pd.DataFrame, navs: pd.DataFrame, dividends: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    batches = shares.loc[shares["net_subscription_shares"] > 0].rename(columns={"date": "subscription_date"}).copy()
    batches = batches.reset_index(drop=True)
    batches["batch_id"] = batches.index
    nav_timeline = navs.loc[navs["date"] <= as_of_date].sort_values(["fund_code", "date"]).copy()
    nav_timeline["nav_position"] = nav_timeline.groupby("fund_code").cumcount()
    entry_navs = nav_timeline.rename(
        columns={"date": "subscription_date", "unit_nav": "entry_unit_nav", "nav_position": "entry_nav_position"}
    )[["subscription_date", "fund_code", "entry_unit_nav", "entry_nav_position"]]
    batches = batches.merge(entry_navs, on=["subscription_date", "fund_code"], how="left", validate="one_to_one")
    as_of_navs = nav_timeline.loc[nav_timeline["date"] == as_of_date, ["fund_code", "unit_nav", "nav_position"]].rename(
        columns={"unit_nav": "as_of_unit_nav", "nav_position": "as_of_nav_position"}
    )
    batches = batches.merge(as_of_navs, on="fund_code", how="left", validate="many_to_one")
    if batches[["entry_unit_nav", "as_of_unit_nav", "entry_nav_position"]].isna().any().any():
        raise ValueError("部分申购批次缺少净值数据。")
    batches["target_60_nav_position"] = batches["entry_nav_position"] + 60
    batches["completed_60_trade_days"] = batches["target_60_nav_position"] <= batches["as_of_nav_position"]
    batches["return_60_nav_position"] = batches["target_60_nav_position"].where(
        batches["completed_60_trade_days"], batches["as_of_nav_position"]
    )
    return_60_navs = nav_timeline.rename(
        columns={"date": "return_60_date", "unit_nav": "return_60_unit_nav", "nav_position": "return_60_nav_position"}
    )[["fund_code", "return_60_nav_position", "return_60_date", "return_60_unit_nav"]]
    batches = batches.merge(return_60_navs, on=["fund_code", "return_60_nav_position"], how="left", validate="many_to_one")
    if batches["return_60_unit_nav"].isna().any():
        raise ValueError("部分申购批次无法定位第 60 个交易日净值。")
    batches["as_of_date"] = as_of_date
    batches["cash_dividend_per_share_60_trade_days"] = _cash_dividends(batches, dividends, "return_60_date")
    batches["cash_dividend_per_share_to_date"] = _cash_dividends(batches, dividends, "as_of_date")
    batches["estimated_subscription_amount"] = batches["net_subscription_shares"] * batches["entry_unit_nav"]
    batches["return_60_trade_days"] = (
        batches["return_60_unit_nav"] + batches["cash_dividend_per_share_60_trade_days"] - batches["entry_unit_nav"]
    ) / batches["entry_unit_nav"]
    batches["return_to_date"] = (
        batches["as_of_unit_nav"] + batches["cash_dividend_per_share_to_date"] - batches["entry_unit_nav"]
    ) / batches["entry_unit_nav"]
    batches["profitable_60_trade_days"] = batches["return_60_trade_days"] > 0
    batches["profitable_to_date"] = batches["return_to_date"] > 0
    return batches


def _eligible_dividend_events(batches: pd.DataFrame, dividends: pd.DataFrame, exit_date_column: str) -> pd.DataFrame:
    events = batches[["batch_id", "fund_code", "subscription_date", exit_date_column]].merge(
        dividends, on="fund_code", how="left"
    )
    return events.loc[
        (events["subscription_date"] < events["record_date"]) & (events["payment_date"] <= events[exit_date_column])
    ].copy()


def _cash_dividends(batches: pd.DataFrame, dividends: pd.DataFrame, exit_date_column: str) -> pd.Series:
    events = _eligible_dividend_events(batches, dividends, exit_date_column)
    return events.groupby("batch_id")["cash_dividend_per_share"].sum().reindex(batches["batch_id"], fill_value=0.0)


def _summarize_indexes(batches: pd.DataFrame, index_reference: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    batches["profitable_amount_60_trade_days"] = batches["estimated_subscription_amount"].where(
        batches["profitable_60_trade_days"], 0.0
    )
    batches["profitable_amount_to_date"] = batches["estimated_subscription_amount"].where(
        batches["profitable_to_date"], 0.0
    )
    batches["weighted_return_60_trade_days"] = batches["estimated_subscription_amount"] * batches["return_60_trade_days"]
    batches["weighted_return_to_date"] = batches["estimated_subscription_amount"] * batches["return_to_date"]
    group_columns = ["index_code", "index_name", "index_order"]
    index_daily_batches = batches.groupby(group_columns + ["subscription_date"], as_index=False).agg(
        estimated_subscription_amount=("estimated_subscription_amount", "sum"),
        weighted_return_60_trade_days=("weighted_return_60_trade_days", "sum"),
        weighted_return_to_date=("weighted_return_to_date", "sum"),
        completed_60_trade_days=("completed_60_trade_days", "all"),
    )
    for period in ["60_trade_days", "to_date"]:
        index_daily_batches[f"return_{period}"] = (
            index_daily_batches[f"weighted_return_{period}"] / index_daily_batches["estimated_subscription_amount"]
        )
        index_daily_batches[f"profitable_{period}"] = index_daily_batches[f"return_{period}"] > 0
    index_amounts = batches.groupby(group_columns, as_index=False).agg(
        subscription_amount=("estimated_subscription_amount", "sum"),
        profitable_amount_60_trade_days=("profitable_amount_60_trade_days", "sum"),
        profitable_amount_to_date=("profitable_amount_to_date", "sum"),
    )
    index_batch_counts = index_daily_batches.groupby(group_columns, as_index=False).agg(
        subscription_batches=("subscription_date", "size"),
        completed_60_trade_day_batches=("completed_60_trade_days", "sum"),
    )
    summary = index_reference.merge(index_amounts, on=group_columns, how="left", validate="one_to_one").merge(
        index_batch_counts, on=group_columns, how="left", validate="one_to_one"
    )
    amount_columns = ["subscription_amount", "profitable_amount_60_trade_days", "profitable_amount_to_date"]
    count_columns = ["subscription_batches", "completed_60_trade_day_batches"]
    summary[amount_columns] = summary[amount_columns].fillna(0.0)
    summary[count_columns] = summary[count_columns].fillna(0).astype(int)
    for period in ["60_trade_days", "to_date"]:
        summary[f"profitable_capital_ratio_{period}"] = (
            summary[f"profitable_amount_{period}"] / summary["subscription_amount"]
        ).where(summary["subscription_amount"] > 0)
    total_subscription_amount = batches["estimated_subscription_amount"].sum()
    overall = pd.DataFrame(
        [
            {
                "index_code": "ALL",
                "index_name": "总体",
                "index_order": 99,
                "subscription_amount": total_subscription_amount,
                "profitable_amount_60_trade_days": batches.loc[batches["profitable_60_trade_days"], "estimated_subscription_amount"].sum(),
                "profitable_amount_to_date": batches.loc[batches["profitable_to_date"], "estimated_subscription_amount"].sum(),
                "subscription_batches": len(index_daily_batches),
                "completed_60_trade_day_batches": int(index_daily_batches["completed_60_trade_days"].sum()),
                "profitable_capital_ratio_60_trade_days": batches.loc[batches["profitable_60_trade_days"], "estimated_subscription_amount"].sum() / total_subscription_amount if total_subscription_amount else float("nan"),
                "profitable_capital_ratio_to_date": batches.loc[batches["profitable_to_date"], "estimated_subscription_amount"].sum() / total_subscription_amount if total_subscription_amount else float("nan"),
            }
        ]
    )
    return index_daily_batches, pd.concat([summary, overall], ignore_index=True)
