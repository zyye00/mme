"""Download raw and post-adjusted daily security prices from BaoStock."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import baostock as bs
import pandas as pd

from mme.common.baostock_requests import reserve_baostock_request
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
PRICE_NUMERIC_COLUMNS = ["close", "close_unadjusted", "volume", "amount"]
EXCHANGE_PREFIXES = {"SSE": "sh", "SZSE": "sz"}
PRICE_QUERY_SPECS = (("3", "date,close,volume,amount"), ("1", "date,close"))


def _validate_universe(margin: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    required = {"exchange", "security_code"}
    missing = required - set(margin.columns)
    if missing:
        raise ValueError(f"input is missing columns: {', '.join(sorted(missing))}")
    if start > end:
        raise ValueError("start date must not be later than end date")

    universe = margin.loc[:, ["exchange", "security_code"]].copy()
    if universe.isna().any().any():
        raise ValueError("input contains null security keys")
    universe["exchange"] = universe["exchange"].astype(str).str.strip()
    universe["security_code"] = universe["security_code"].astype(str).str.strip()
    if universe["security_code"].eq("").any():
        raise ValueError("input contains empty security codes")
    if universe.duplicated().any():
        raise ValueError("input contains duplicate security keys")
    invalid_exchanges = sorted(set(universe["exchange"]) - set(EXCHANGE_PREFIXES))
    if invalid_exchanges:
        raise ValueError(f"unsupported exchanges: {', '.join(invalid_exchanges)}")
    if universe.empty:
        raise ValueError("input contains no securities")
    return universe.sort_values(["exchange", "security_code"]).reset_index(drop=True)


def _security_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return set(frame[["exchange", "security_code"]].itertuples(index=False, name=None))


def _validate_prices(prices: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    missing = set(PRICE_COLUMNS) - set(prices.columns)
    if missing:
        raise ValueError(f"price data is missing columns: {', '.join(sorted(missing))}")
    prices = prices.loc[:, PRICE_COLUMNS].copy()
    key_columns = ["trade_date", "exchange", "security_code"]
    if prices.empty:
        raise ValueError("price data is empty")
    if prices.duplicated(key_columns).any():
        raise ValueError("price data contains duplicate trade-date security keys")
    if prices[PRICE_NUMERIC_COLUMNS].isna().any().any():
        raise ValueError("price data contains null numeric values")
    if (prices[PRICE_NUMERIC_COLUMNS] < 0).any().any():
        raise ValueError("price data contains negative numeric values")

    expected_keys = _security_keys(universe)
    actual_keys = _security_keys(prices)
    missing_keys = sorted(expected_keys - actual_keys)
    extra_keys = sorted(actual_keys - expected_keys)
    if missing_keys or extra_keys:
        message = []
        if missing_keys:
            message.append(f"missing securities: {missing_keys}")
        if extra_keys:
            message.append(f"unexpected securities: {extra_keys}")
        raise ValueError("price data security set is incomplete (" + "; ".join(message) + ")")
    return prices.sort_values(key_columns).reset_index(drop=True)


def _query_rows(code: str, fields: str, start: date, end: date, adjustflag: str, max_requests_per_day: int) -> list[list[str]]:
    reserve_baostock_request('query_history_k_data_plus', max_requests_per_day, code, adjustflag)
    result = bs.query_history_k_data_plus(
        code, fields, start.isoformat(), end.isoformat(), "d", adjustflag
    )
    if str(result.error_code) != "0":
        raise RuntimeError(f"BaoStock query failed for {code}: {result.error_msg}")
    data: list[list[str]] = []
    while result.next():
        data.append(result.get_row_data())
    if not data:
        raise ValueError(f"BaoStock returned no price data for {code} (adjustflag={adjustflag})")
    return data


def _download_security_prices(
    exchange: str, security_code: str, start: date, end: date, max_requests_per_day: int
) -> pd.DataFrame:
    code = f"{EXCHANGE_PREFIXES[exchange]}.{security_code}"
    raw_rows = _query_rows(code, PRICE_QUERY_SPECS[0][1], start, end, PRICE_QUERY_SPECS[0][0], max_requests_per_day)
    adjusted_rows = _query_rows(
        code, PRICE_QUERY_SPECS[1][1], start, end, PRICE_QUERY_SPECS[1][0], max_requests_per_day
    )
    raw = pd.DataFrame(raw_rows, columns=["trade_date", "close_unadjusted", "volume", "amount"])
    adjusted = pd.DataFrame(adjusted_rows, columns=["trade_date", "close"])
    for frame in [raw, adjusted]:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        if frame["trade_date"].duplicated().any():
            raise ValueError(f"BaoStock returned duplicate trade dates for {code}")
    raw_dates = set(raw["trade_date"])
    adjusted_dates = set(adjusted["trade_date"])
    if raw_dates != adjusted_dates:
        raise ValueError(f"raw and adjusted price dates differ for {code}")
    raw = raw.merge(adjusted, on="trade_date", how="inner", validate="one_to_one")
    raw["exchange"] = exchange
    raw["security_code"] = security_code
    for column in PRICE_NUMERIC_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    missing_trading_values = raw[["volume", "amount"]].isna()
    if missing_trading_values.any(axis=1).ne(missing_trading_values.all(axis=1)).any():
        raise ValueError(f"BaoStock returned incomplete volume and amount for {code}")
    raw.loc[missing_trading_values.all(axis=1), ["volume", "amount"]] = 0
    if raw[["close", "close_unadjusted"]].isna().any().any():
        raise ValueError(f"BaoStock returned missing close prices for {code}")
    return raw.loc[:, PRICE_COLUMNS]


def _write_partial_prices(frames: list[pd.DataFrame], output: Path) -> None:
    partial = pd.concat(frames, ignore_index=True)
    partial_universe = partial.loc[:, ["exchange", "security_code"]].drop_duplicates()
    write_parquet_outputs({output: _validate_prices(partial, partial_universe)})


def download_prices(
    margin: pd.DataFrame,
    start: date,
    end: date,
    max_requests_per_day: int,
    *,
    partial_output: Path | None = None,
) -> pd.DataFrame:
    if max_requests_per_day <= 0:
        raise ValueError("max_requests_per_day must be positive")
    universe = _validate_universe(margin, start, end)
    frames: list[pd.DataFrame] = []
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        for row in universe.itertuples(index=False):
            frames.append(
                _download_security_prices(
                    row.exchange, row.security_code, start, end, max_requests_per_day
                )
            )
        prices = _validate_prices(pd.concat(frames, ignore_index=True), universe)
    except Exception as error:
        if partial_output and frames:
            try:
                _write_partial_prices(frames, partial_output)
            except Exception as partial_error:
                raise RuntimeError(f"download failed and partial output could not be saved: {partial_error}") from error
        raise
    finally:
        bs.logout()
    return prices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("data/derived/margin/first_day_top80.parquet")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/source/margin/first_day_top80_prices.parquet")
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--max-requests-per-day", type=int, default=50_000)
    args = parser.parse_args()
    partial_output = args.output.with_name(f"{args.output.stem}_partial{args.output.suffix}")
    try:
        prices = download_prices(
            pd.read_parquet(args.input),
            args.start,
            args.end,
            args.max_requests_per_day,
            partial_output=partial_output,
        )
        write_parquet_outputs({args.output: prices})
        partial_output.unlink(missing_ok=True)
        print(f"Output: {args.output} ({len(prices)} rows)")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        if partial_output.exists():
            print(f"Partial output: {partial_output}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
