"""Download daily SSE and SZSE margin-financing purchase details."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd

from download_etf_shares import parse_date, trading_dates
from output_utils import write_parquet_outputs

OUTPUT_COLUMNS = [
    "trade_date",
    "exchange",
    "security_code",
    "security_name",
    "financing_buy_amount",
]


def standardize_details(frame: pd.DataFrame, exchange: str, current_date: date) -> pd.DataFrame:
    columns = (
        ["标的证券代码", "标的证券简称", "融资买入额"]
        if exchange == "SSE"
        else ["证券代码", "证券简称", "融资买入额"]
    )
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"AkShare response is missing columns: {', '.join(sorted(missing))}")

    details = frame.loc[:, columns].copy()
    details.columns = ["security_code", "security_name", "financing_buy_amount"]
    details.insert(0, "exchange", exchange)
    details.insert(0, "trade_date", current_date)
    details["security_code"] = details["security_code"].astype(str).str.zfill(6)
    details["security_name"] = details["security_name"].astype(str)
    details["financing_buy_amount"] = pd.to_numeric(details["financing_buy_amount"], errors="raise")
    return details.loc[:, OUTPUT_COLUMNS]


def validate_details(details: pd.DataFrame, dates: list[date]) -> None:
    if details.empty:
        raise ValueError("no margin-financing details were downloaded")
    if details.duplicated(["trade_date", "exchange", "security_code"]).any():
        raise ValueError("duplicate trade_date, exchange, and security_code records found")
    if (details["financing_buy_amount"] < 0).any():
        raise ValueError("financing_buy_amount must not be negative")
    if not set(details["trade_date"]).issubset(dates):
        raise ValueError("downloaded dates are outside the requested trading calendar")


def download_margin_financing(
    start: date,
    end: date,
    output: Path,
    fetch_sse: Callable[[str], pd.DataFrame] = ak.stock_margin_detail_sse,
    fetch_szse: Callable[[str], pd.DataFrame] = ak.stock_margin_detail_szse,
    fetch_calendar: Callable[[], pd.DataFrame] = ak.tool_trade_date_hist_sina,
    progress: Callable[[str], None] = print,
    workers: int = 8,
) -> pd.DataFrame:
    dates = trading_dates(start, end, fetch_calendar())
    if not dates:
        raise ValueError("no trading days in the requested date range")
    if workers <= 0:
        raise ValueError("workers must be positive")

    def fetch_day(current_date: date) -> list[pd.DataFrame]:
        day = current_date.strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []
        for exchange, fetch in (("SSE", fetch_sse), ("SZSE", fetch_szse)):
            try:
                raw = fetch(day)
                if raw.empty:
                    raise ValueError("empty response")
                frames.append(standardize_details(raw, exchange, current_date))
            except Exception as error:
                raise RuntimeError(f"{current_date} {exchange}: {error}") from error
        return frames

    frames: list[pd.DataFrame] = []
    progress(f"Downloading margin-financing details for {len(dates)} trading days")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, day_frames in enumerate(executor.map(fetch_day, dates), start=1):
            frames.extend(day_frames)
            if index % 10 == 0 or index == len(dates):
                progress(f"Downloaded {index}/{len(dates)} trading days")

    details = pd.concat(frames, ignore_index=True).sort_values(
        ["trade_date", "exchange", "security_code"]
    )
    validate_details(details, dates)
    write_parquet_outputs({output: details})
    progress(f"Output: {output} ({len(details)} rows)")
    return details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=date(2026, 1, 1))
    parser.add_argument("--end", type=parse_date, default=date.today())
    parser.add_argument("--output", type=Path, default=Path("data/margin_financing_buy.parquet"))
    parser.add_argument("--workers", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        download_margin_financing(args.start, args.end, args.output, workers=args.workers)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
