"""Download and localize BaoStock security basic information."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import baostock as bs
import pandas as pd

from output_utils import write_parquet_outputs

SECURITY_TYPE_MAP = {
    "1": "stock",
    "2": "index",
    "3": "other",
    "4": "convertible_bond",
    "5": "etf",
}


def baostock_result_to_frame(result: bs.ResultData) -> pd.DataFrame:
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_msg}")
    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields)


def standardize_security_basics(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"code", "code_name", "ipoDate", "outDate", "type", "status"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"BaoStock response is missing columns: {', '.join(sorted(missing))}")
    basics = frame.loc[:, ["code", "code_name", "ipoDate", "outDate", "type", "status"]].copy()
    basics.columns = ["bs_code", "security_name", "ipo_date", "out_date", "type_code", "listing_status"]
    basics["exchange"] = basics["bs_code"].str.split(".").str[0].map({"sh": "SSE", "sz": "SZSE"})
    basics["security_code"] = basics["bs_code"].str.split(".").str[-1].str.zfill(6)
    basics["security_type"] = basics["type_code"].map(SECURITY_TYPE_MAP).fillna("unknown")
    basics["ipo_date"] = pd.to_datetime(basics["ipo_date"], errors="coerce").dt.date
    basics["out_date"] = pd.to_datetime(basics["out_date"], errors="coerce").dt.date
    return basics.loc[:, ["security_code", "exchange", "bs_code", "security_name", "security_type", "ipo_date", "out_date", "listing_status"]]


def download_security_basics(output: Path) -> pd.DataFrame:
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        basics = standardize_security_basics(baostock_result_to_frame(bs.query_stock_basic()))
    finally:
        bs.logout()
    if basics.duplicated(["exchange", "security_code"]).any():
        raise ValueError("duplicate exchange and security_code records found")
    write_parquet_outputs({output: basics})
    return basics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/baostock_security_basics.parquet"))
    args = parser.parse_args()
    try:
        basics = download_security_basics(args.output)
        print(f"Output: {args.output} ({len(basics)} rows; ETFs={(basics.security_type == 'etf').sum()})")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
