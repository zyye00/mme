"""Download SSE and SZSE ETF daily shares from AkShare."""

from __future__ import annotations

import argparse
import calendar
import sys
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

from etf_universe import TARGET_FUND_CODES
from output_utils import write_parquet_outputs

REQUIRED_COLUMNS = {"基金代码", "基金简称", "统计日期", "基金份额"}
SHARE_CHANGE_WARNING_RATIO = 1.0
SZSE_MAX_QUERY_DAYS = 180


@dataclass(frozen=True)
class DownloadResult:
    requested_dates: int
    successful_dates: int
    empty_dates: tuple[date, ...]
    failed_dates: tuple[tuple[date, str], ...]
    source_errors: tuple[str, ...]
    warnings: tuple[str, ...]


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error


def six_months_ago(today: date) -> date:
    month = today.month - 6
    year = today.year
    if month < 1:
        year -= 1
        month += 12
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def trading_dates(start: date, end: date, trade_calendar: pd.DataFrame) -> list[date]:
    if start > end:
        raise ValueError("start date must not be after end date")
    if "trade_date" not in trade_calendar:
        raise ValueError("trade calendar is missing trade_date")
    dates = pd.to_datetime(trade_calendar["trade_date"], errors="raise").dt.date
    return [current_date for current_date in dates if start <= current_date <= end]


def date_chunks(start: date, end: date, maximum_days: int = SZSE_MAX_QUERY_DAYS) -> list[tuple[date, date]]:
    if start > end:
        raise ValueError("start date must not be after end date")
    if maximum_days <= 0:
        raise ValueError("maximum_days must be positive")

    chunks: list[tuple[date, date]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=maximum_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


def standardize_shares(
    frame: pd.DataFrame,
    date_column: str,
    target_fund_codes: Collection[str] = TARGET_FUND_CODES,
) -> pd.DataFrame:
    required_columns = (REQUIRED_COLUMNS - {"统计日期"}) | {date_column}
    missing_columns = required_columns - set(frame.columns)
    if missing_columns:
        names = ", ".join(sorted(missing_columns))
        raise ValueError(f"AkShare response is missing columns: {names}")

    shares = frame.loc[:, ["基金代码", "基金简称", date_column, "基金份额"]].copy()
    shares.columns = ["fund_code", "fund_name", "date", "total_shares"]
    shares["fund_code"] = shares["fund_code"].astype(str).str.zfill(6)
    shares["date"] = pd.to_datetime(shares["date"], errors="raise").dt.date
    shares["total_shares"] = pd.to_numeric(shares["total_shares"], errors="raise")
    shares = shares.loc[shares["total_shares"] != 0]
    return shares.loc[shares["fund_code"].isin(target_fund_codes)].reset_index(drop=True)


def validate_shares(shares: pd.DataFrame, dates: list[date]) -> list[str]:
    if shares.duplicated(["date", "fund_code"]).any():
        raise ValueError("duplicate date and fund_code records found")
    if (shares["total_shares"] <= 0).any():
        raise ValueError("total_shares must be positive")

    warnings: list[str] = []
    positions = {current_date: index for index, current_date in enumerate(dates)}
    for fund_code, group in shares.groupby("fund_code"):
        ordered = group.sort_values("date")
        fund_dates = ordered["date"].tolist()
        for previous, current in zip(fund_dates, fund_dates[1:]):
            if previous not in positions or current not in positions:
                warnings.append(f"{fund_code}: date is missing from the trading calendar")
            elif positions[current] - positions[previous] > 1:
                warnings.append(f"{fund_code}: missing trading dates between {previous} and {current}")

        changes = ordered["total_shares"].pct_change(fill_method=None).abs()
        if (changes > SHARE_CHANGE_WARNING_RATIO).any():
            warnings.append(f"{fund_code}: daily share change exceeds 100%")
    return warnings


def download_etf_shares(
    start: date,
    end: date,
    output_dir: Path,
    fetch_sse: Callable[[str], pd.DataFrame] = ak.fund_etf_scale_sse,
    fetch_szse: Callable[[str, str, str], pd.DataFrame] = ak.fund_scale_daily_szse,
    fetch_calendar: Callable[[], pd.DataFrame] = ak.tool_trade_date_hist_sina,
    progress: Callable[[str], None] = print,
    target_fund_codes: Collection[str] = TARGET_FUND_CODES,
) -> DownloadResult:
    sse_raw_frames: list[pd.DataFrame] = []
    share_frames: list[pd.DataFrame] = []
    empty_dates: list[date] = []
    failed_dates: list[tuple[date, str]] = []
    source_errors: list[str] = []
    successful_dates = 0
    dates = trading_dates(start, end, fetch_calendar())
    progress(f"SSE: downloading {len(dates)} trading days from {start} to {end}")

    for index, current_date in enumerate(dates, start=1):
        try:
            raw = fetch_sse(current_date.strftime("%Y%m%d"))
            shares = standardize_shares(raw, "统计日期", target_fund_codes)
        except Exception as error:
            failed_dates.append((current_date, str(error)))
        else:
            if raw.empty:
                empty_dates.append(current_date)
            else:
                successful_dates += 1
                sse_raw_frames.append(raw)
                share_frames.append(shares)
        if index % 10 == 0 or index == len(dates):
            progress(
                f"SSE: {index}/{len(dates)} complete; empty={len(empty_dates)} failed={len(failed_dates)}"
            )

    chunks = [
        (chunk_start, chunk_end)
        for chunk_start, chunk_end in date_chunks(start, end)
        if any(chunk_start <= current_date <= chunk_end for current_date in dates)
    ]
    progress(f"SZSE: downloading ETF shares in {len(chunks)} chunk(s) from {start} to {end}")
    szse_raw_frames: list[pd.DataFrame] = []
    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        try:
            raw = fetch_szse(chunk_start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), "ETF")
            if raw.empty:
                raise ValueError("empty response")
            share_frames.append(standardize_shares(raw, "日期", target_fund_codes))
            szse_raw_frames.append(raw)
        except Exception as error:
            source_errors.append(f"SZSE {chunk_start} to {chunk_end}: {error}")
            progress(f"SZSE: chunk {index}/{len(chunks)} failed")
        else:
            progress(f"SZSE: chunk {index}/{len(chunks)} complete; rows={len(raw)}")
    szse_raw = pd.concat(szse_raw_frames, ignore_index=True) if szse_raw_frames else pd.DataFrame()

    warnings: list[str] = []
    if empty_dates:
        source_errors.append(f"SSE: {len(empty_dates)} 个交易日返回空数据")
    if failed_dates:
        source_errors.append(f"SSE: {len(failed_dates)} 个交易日下载失败")
    if not sse_raw_frames:
        source_errors.append("SSE: 未获得原始份额数据")
    if not szse_raw_frames:
        source_errors.append("SZSE: 未获得原始份额数据")

    if share_frames:
        shares = pd.concat(share_frames, ignore_index=True)
        warnings = validate_shares(shares, dates)
        shares = shares.sort_values(["date", "fund_code"]).reset_index(drop=True)
        missing_codes = set(target_fund_codes) - set(shares["fund_code"])
        if missing_codes:
            source_errors.append(f"缺少映射 ETF：{', '.join(sorted(missing_codes))}")
    else:
        shares = pd.DataFrame()
        source_errors.append("未获得目标 ETF 份额数据")

    if not source_errors:
        outputs = {
            output_dir / "sse_etf_shares_raw.parquet": pd.concat(sse_raw_frames, ignore_index=True),
            output_dir / "szse_etf_shares_raw.parquet": szse_raw,
            output_dir / "etf_shares.parquet": shares,
        }
        write_parquet_outputs(outputs)
        progress(f"Complete: {len(target_fund_codes)}/{len(target_fund_codes)} target ETFs found")
        for path in outputs:
            progress(f"Output: {path}")
    else:
        progress("Incomplete download: existing Parquet outputs were not replaced")

    return DownloadResult(
        requested_dates=len(dates),
        successful_dates=successful_dates,
        empty_dates=tuple(empty_dates),
        failed_dates=tuple(failed_dates),
        source_errors=tuple(source_errors),
        warnings=tuple(warnings),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=six_months_ago(date.today()))
    parser.add_argument("--end", type=parse_date, default=date.today())
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = download_etf_shares(args.start, args.end, args.output_dir)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        "requested=%d successful=%d empty=%d failed=%d source_errors=%d"
        % (
            result.requested_dates,
            result.successful_dates,
            len(result.empty_dates),
            len(result.failed_dates),
            len(result.source_errors),
        )
    )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for failed_date, message in result.failed_dates:
        print(f"failed: {failed_date}: {message}", file=sys.stderr)
    for error in result.source_errors:
        print(f"failed: {error}", file=sys.stderr)
    if not result.successful_dates:
        print("error: no data was downloaded", file=sys.stderr)
        return 1
    return int(bool(result.empty_dates or result.failed_dates or result.source_errors))


if __name__ == "__main__":
    raise SystemExit(main())
