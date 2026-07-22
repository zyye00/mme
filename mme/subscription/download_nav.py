"""Download historical ETF net asset values from AkShare."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd

from mme.common.output import write_parquet_outputs
from mme.subscription.universe import TARGET_FUND_CODES

NAV_COLUMNS = ["trade_date", "fund_code", "unit_nav", "daily_return_pct"]
SPLIT_COLUMNS = ["fund_code", "split_date", "split_type", "split_ratio"]
DIVIDEND_COLUMNS = ["fund_code", "record_date", "ex_date", "cash_dividend_per_share", "payment_date"]
UNIT_NAV_COLUMNS = {"净值日期", "单位净值", "日增长率"}
SPLIT_SOURCE_COLUMNS = {"拆分折算日", "拆分类型", "拆分折算比例"}
DIVIDEND_SOURCE_COLUMNS = {"基金代码", "权益登记日", "除息日期", "分红", "分红发放日"}
RETURN_WARNING_TOLERANCE = 0.1
SPLIT_RATIO_PATTERN = re.compile(r"^\s*1\s*:\s*(\d+(?:\.\d+)?)\s*$")
MAX_REQUEST_ATTEMPTS = 3
RETRY_INTERVAL = 1.0


@dataclass(frozen=True)
class NavDownloadResult:
    requested_funds: int
    successful_funds: int
    failures: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]


def normalize_fund_code(value: str) -> str:
    fund_code = str(value).strip().zfill(6)
    if len(fund_code) != 6 or not fund_code.isdigit():
        raise ValueError(f"无效的基金代码：{fund_code!r}")
    return fund_code


def parse_split_ratio(value: object) -> float:
    match = SPLIT_RATIO_PATTERN.fullmatch(str(value))
    if not match:
        raise ValueError(f"无法解析拆分折算比例：{value!r}")
    ratio = float(match.group(1))
    if ratio <= 0:
        raise ValueError(f"拆分折算比例必须为正：{value!r}")
    return ratio


def fetch_etf_nav(
    fund_code: str,
    fetch: Callable[..., pd.DataFrame] = ak.fund_open_fund_info_em,
) -> pd.DataFrame:
    """Fetch one ETF's unit NAV history."""
    fund_code = normalize_fund_code(fund_code)
    unit_raw = fetch(symbol=fund_code, indicator="单位净值走势")

    if unit_raw.empty:
        raise ValueError(f"未获取到基金 {fund_code} 的单位净值数据")
    missing_columns = UNIT_NAV_COLUMNS - set(unit_raw.columns)
    if missing_columns:
        raise ValueError(f"单位净值数据缺少字段：{', '.join(sorted(missing_columns))}")

    unit_nav = unit_raw.rename(
        columns={"净值日期": "trade_date", "单位净值": "unit_nav", "日增长率": "daily_return_pct"}
    ).loc[:, ["trade_date", "unit_nav", "daily_return_pct"]]
    unit_nav["trade_date"] = pd.to_datetime(unit_nav["trade_date"], errors="raise").dt.normalize()
    unit_nav["unit_nav"] = pd.to_numeric(unit_nav["unit_nav"], errors="coerce")
    unit_nav["daily_return_pct"] = pd.to_numeric(unit_nav["daily_return_pct"], errors="coerce")

    unit_nav.insert(1, "fund_code", fund_code)
    return (
        unit_nav.dropna(subset=["unit_nav"])
        .drop_duplicates(["fund_code", "trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
        .loc[:, NAV_COLUMNS]
    )


def fetch_etf_splits(
    fund_code: str,
    fetch: Callable[..., pd.DataFrame] = ak.fund_open_fund_info_em,
) -> pd.DataFrame:
    """Fetch and standardize one ETF's share split history."""
    fund_code = normalize_fund_code(fund_code)
    raw = fetch(symbol=fund_code, indicator="拆分详情")
    if raw.empty:
        return pd.DataFrame(columns=SPLIT_COLUMNS)

    missing_columns = SPLIT_SOURCE_COLUMNS - set(raw.columns)
    if missing_columns:
        raise ValueError(f"拆分数据缺少字段：{', '.join(sorted(missing_columns))}")
    splits = raw.rename(
        columns={"拆分折算日": "split_date", "拆分类型": "split_type", "拆分折算比例": "split_ratio"}
    ).loc[:, ["split_date", "split_type", "split_ratio"]]
    splits["split_date"] = pd.to_datetime(splits["split_date"], errors="raise").dt.normalize()
    splits["split_ratio"] = splits["split_ratio"].map(parse_split_ratio)
    splits.insert(0, "fund_code", fund_code)
    return splits.drop_duplicates(SPLIT_COLUMNS).sort_values("split_date").reset_index(drop=True)


def dividend_years(start: date, end: date) -> list[int]:
    if start > end:
        raise ValueError("分红下载开始日期不能晚于结束日期")
    return list(range(start.year, end.year + 1))


def fetch_etf_dividends(
    year: int,
    fund_codes: set[str] = TARGET_FUND_CODES,
    fetch: Callable[..., pd.DataFrame] = ak.fund_fh_em,
) -> pd.DataFrame:
    """Fetch and standardize ETF cash-dividend events for one calendar year."""
    raw = fetch(year=str(year), typ="指数型-股票")
    if raw.empty:
        return pd.DataFrame(columns=DIVIDEND_COLUMNS)

    missing_columns = DIVIDEND_SOURCE_COLUMNS - set(raw.columns)
    if missing_columns:
        raise ValueError(f"分红数据缺少字段：{', '.join(sorted(missing_columns))}")

    dividends = raw.loc[:, list(DIVIDEND_SOURCE_COLUMNS)].rename(
        columns={
            "基金代码": "fund_code",
            "权益登记日": "record_date",
            "除息日期": "ex_date",
            "分红": "cash_dividend_per_share",
            "分红发放日": "payment_date",
        }
    )
    dividends["fund_code"] = dividends["fund_code"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip().str.zfill(6)
    dividends = dividends.loc[dividends["fund_code"].isin(fund_codes)].copy()
    if dividends.empty:
        return pd.DataFrame(columns=DIVIDEND_COLUMNS)

    for column in ["record_date", "ex_date", "payment_date"]:
        dividends[column] = pd.to_datetime(dividends[column], errors="coerce").dt.normalize()
    dividends["cash_dividend_per_share"] = pd.to_numeric(dividends["cash_dividend_per_share"], errors="coerce")
    if dividends[["record_date", "ex_date", "cash_dividend_per_share"]].isna().any().any():
        raise ValueError(f"{year} 年分红数据包含无法解析的日期或金额")
    if (dividends["cash_dividend_per_share"] <= 0).any():
        raise ValueError(f"{year} 年分红金额必须为正")

    dividends = dividends.loc[:, DIVIDEND_COLUMNS].drop_duplicates().sort_values(
        ["fund_code", "record_date", "ex_date", "payment_date"], na_position="last"
    )
    event_columns = ["fund_code", "record_date", "ex_date", "payment_date"]
    if dividends.duplicated(event_columns).any():
        raise ValueError(f"{year} 年分红数据包含重复事件")
    return dividends.reset_index(drop=True)


def validate_navs(navs: pd.DataFrame) -> list[str]:
    if navs.duplicated(["fund_code", "trade_date"]).any():
        raise ValueError("净值数据包含重复的基金代码和日期")
    if (navs["unit_nav"] <= 0).any():
        raise ValueError("单位净值必须为正")
    warnings: list[str] = []
    for fund_code, group in navs.groupby("fund_code"):
        ordered = group.sort_values("trade_date")
        if not ordered["trade_date"].is_monotonic_increasing:
            raise ValueError(f"基金 {fund_code} 的净值日期未按升序排列")
        calculated_return = ordered["unit_nav"].pct_change(fill_method=None) * 100
        difference = (calculated_return - ordered["daily_return_pct"]).abs()
        if (difference > RETURN_WARNING_TOLERANCE).any():
            warnings.append(f"{fund_code}：单位净值变化与接口日增长率存在偏差")
    return warnings


def fetch_with_retries(
    operation: Callable[[], pd.DataFrame],
    label: str,
    sleeper: Callable[[float], None],
    progress: Callable[[str], None],
    max_attempts: int = MAX_REQUEST_ATTEMPTS,
) -> pd.DataFrame:
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception:
            if attempt == max_attempts:
                raise
            progress(f"{label}：第 {attempt} 次请求失败，准备重试")
            sleeper(RETRY_INTERVAL)
    raise RuntimeError("unreachable")


def download_etf_navs(
    output_path: Path,
    fund_codes: set[str] = TARGET_FUND_CODES,
    dividend_start: date | None = None,
    dividend_end: date | None = None,
    request_interval: float = 0.8,
    fetch: Callable[..., pd.DataFrame] = ak.fund_open_fund_info_em,
    dividend_fetch: Callable[..., pd.DataFrame] = ak.fund_fh_em,
    sleeper: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> NavDownloadResult:
    frames: list[pd.DataFrame] = []
    split_frames: list[pd.DataFrame] = []
    dividend_frames: list[pd.DataFrame] = []
    failures: list[tuple[str, str]] = []
    fund_codes = sorted(fund_codes)
    today = date.today()
    dividend_start = dividend_start or date(today.year, 1, 1)
    dividend_end = dividend_end or today
    years = dividend_years(dividend_start, dividend_end)
    progress(f"净值下载：共 {len(fund_codes)} 只 ETF")

    for index, fund_code in enumerate(fund_codes, start=1):
        try:
            frames.append(
                fetch_with_retries(
                    lambda: fetch_etf_nav(fund_code, fetch),
                    f"净值下载 {fund_code}",
                    sleeper,
                    progress,
                )
            )
        except Exception as error:
            failures.append((fund_code, str(error)))
            progress(f"净值下载：{index}/{len(fund_codes)} 失败（{fund_code}）")
        else:
            progress(f"净值下载：{index}/{len(fund_codes)} 完成（{fund_code}）")
        try:
            splits = fetch_with_retries(
                lambda: fetch_etf_splits(fund_code, fetch),
                f"拆分下载 {fund_code}",
                sleeper,
                progress,
            )
        except Exception as error:
            failures.append((fund_code, f"拆分详情：{error}"))
            progress(f"拆分下载：{index}/{len(fund_codes)} 失败（{fund_code}）")
        else:
            split_frames.append(splits)
            progress(f"拆分下载：{index}/{len(fund_codes)} 完成（{fund_code}，{len(splits)} 条）")
        if index < len(fund_codes):
            sleeper(request_interval)

    progress(f"分红下载：共 {len(years)} 个年度")
    for index, year in enumerate(years, start=1):
        try:
            dividends = fetch_with_retries(
                lambda: fetch_etf_dividends(year, set(fund_codes), dividend_fetch),
                f"分红下载 {year}",
                sleeper,
                progress,
            )
        except Exception as error:
            failures.append((f"dividends-{year}", str(error)))
            progress(f"分红下载：{index}/{len(years)} 失败（{year} 年）")
        else:
            dividend_frames.append(dividends)
            progress(f"分红下载：{index}/{len(years)} 完成（{year} 年，{len(dividends)} 条）")
        if index < len(years):
            sleeper(request_interval)

    failure_path = output_path.with_name(f"{output_path.stem}_failures.csv")
    if failures:
        pd.DataFrame(failures, columns=["fund_code", "error"]).to_csv(
            failure_path, index=False, encoding="utf-8-sig"
        )
        progress(f"失败记录：{failure_path}")
        raise RuntimeError("ETF 净值或拆分下载不完整，现有 Parquet 输出未被替换")
    failure_path.unlink(missing_ok=True)

    navs = pd.concat(frames, ignore_index=True).drop_duplicates(["fund_code", "trade_date"], keep="last")
    navs = navs.sort_values(["fund_code", "trade_date"]).reset_index(drop=True)
    warnings = validate_navs(navs)
    splits = pd.concat(split_frames, ignore_index=True) if split_frames else pd.DataFrame(columns=SPLIT_COLUMNS)
    splits = splits.loc[:, SPLIT_COLUMNS].sort_values(["fund_code", "split_date"]).reset_index(drop=True)
    dividends = (
        pd.concat(dividend_frames, ignore_index=True)
        if dividend_frames
        else pd.DataFrame(columns=DIVIDEND_COLUMNS)
    )
    dividends = dividends.loc[:, DIVIDEND_COLUMNS].drop_duplicates().sort_values(
        ["fund_code", "record_date", "ex_date", "payment_date"], na_position="last"
    ).reset_index(drop=True)
    split_path = output_path.parent / "etf_splits.parquet"
    dividend_path = output_path.parent / "etf_dividends.parquet"
    write_parquet_outputs({output_path: navs, split_path: splits, dividend_path: dividends})
    progress(f"净值下载完成：成功 {len(frames)}/{len(fund_codes)}，输出 {output_path}")
    progress(f"拆分下载完成：共 {len(splits)} 条，输出 {split_path}")
    progress(f"分红下载完成：共 {len(dividends)} 条，输出 {dividend_path}")
    return NavDownloadResult(len(fund_codes), len(frames), tuple(failures), tuple(warnings))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/source/subscription/etf_nav.parquet"))
    parser.add_argument("--request-interval", type=float, default=0.8)
    parser.add_argument("--dividend-start", type=date.fromisoformat, default=date(date.today().year, 1, 1))
    parser.add_argument("--dividend-end", type=date.fromisoformat, default=date.today())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = download_etf_navs(
            args.output,
            dividend_start=args.dividend_start,
            dividend_end=args.dividend_end,
            request_interval=args.request_interval,
        )
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"警告：{warning}", file=sys.stderr)
    return int(bool(result.failures))


if __name__ == "__main__":
    raise SystemExit(main())
