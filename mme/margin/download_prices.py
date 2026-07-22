"""Download raw and post-adjusted daily ETF prices from BaoStock."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import baostock as bs
import pandas as pd

from mme.common.output import write_parquet_outputs

PRICE_COLUMNS = [
    "trade_date",
    "exchange",
    "security_code",
    "close",
    "close_unadjusted",
    "volume",
    "amount",
]


def download_prices(
    margin: pd.DataFrame,
    start: date,
    end: date,
    request_log: Path,
    max_requests_per_day: int,
) -> pd.DataFrame:
    if max_requests_per_day <= 0:
        raise ValueError("max_requests_per_day must be positive")
    universe = margin.loc[:, ["exchange", "security_code"]].drop_duplicates().copy()
    frames: list[pd.DataFrame] = []
    request_date = date.today().isoformat()
    if request_log.exists():
        log = pd.read_csv(request_log)
        if set(log.columns) != {"request_date", "code", "adjustflag"}:
            raise ValueError(f"invalid request log: {request_log}")
        requests_used = int((log["request_date"] == request_date).sum())
    else:
        requests_used = 0
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        for row in universe.itertuples(index=False):
            code = f"{'sh' if row.exchange == 'SSE' else 'sz'}.{row.security_code}"
            rows: list[list[str]] = []
            for adjustflag, fields in (("3", "date,close,volume,amount"), ("1", "date,close")):
                if requests_used >= max_requests_per_day:
                    raise RuntimeError(
                        f"BaoStock daily request limit reached: {requests_used}/{max_requests_per_day}"
                    )
                request_log.parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame([[request_date, code, adjustflag]], columns=["request_date", "code", "adjustflag"]).to_csv(
                    request_log, mode="a", header=not request_log.exists(), index=False
                )
                requests_used += 1
                result = bs.query_history_k_data_plus(
                    code, fields, start.isoformat(), end.isoformat(), "d", adjustflag
                )
                if result.error_code != "0":
                    raise RuntimeError(f"BaoStock query failed for {code}: {result.error_msg}")
                data: list[list[str]] = []
                while result.next():
                    data.append(result.get_row_data())
                rows.append(data)
            raw = pd.DataFrame(rows[0], columns=["trade_date", "close_unadjusted", "volume", "amount"])
            adjusted = pd.DataFrame(rows[1], columns=["trade_date", "close"])
            raw = raw.merge(adjusted, on="trade_date", how="inner", validate="one_to_one")
            raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="raise")
            raw["exchange"] = row.exchange
            raw["security_code"] = row.security_code
            for column in ["close", "close_unadjusted", "volume", "amount"]:
                raw[column] = pd.to_numeric(raw[column], errors="raise")
            frames.append(raw.loc[:, PRICE_COLUMNS])
    finally:
        bs.logout()
    prices = pd.concat(frames, ignore_index=True).sort_values(["trade_date", "exchange", "security_code"])
    if prices.duplicated(["trade_date", "exchange", "security_code"]).any() or (prices[["close", "volume", "amount"]] < 0).any().any():
        raise ValueError("invalid or duplicate price records")
    return prices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/derived/margin/etf_financing_buy.parquet"))
    parser.add_argument("--output", type=Path, default=Path("data/source/margin/etf_margin_prices.parquet"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--request-log", type=Path, default=Path("data/state/baostock/etf_price_requests.csv"))
    parser.add_argument("--max-requests-per-day", type=int, default=50_000)
    args = parser.parse_args()
    try:
        prices = download_prices(
            pd.read_parquet(args.input), args.start, args.end, args.request_log, args.max_requests_per_day
        )
        write_parquet_outputs({args.output: prices})
        print(f"Output: {args.output} ({len(prices)} rows)")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
