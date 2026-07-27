"""Download and localize BaoStock security industry classifications."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import baostock as bs
import pandas as pd

from mme.common.baostock_requests import reserve_baostock_request
from mme.common.output import write_parquet_outputs
from mme.margin.download_security_basics import baostock_result_to_frame


def standardize_security_industries(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"updateDate", "code", "code_name", "industry", "industryClassification"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"BaoStock response is missing columns: {', '.join(sorted(missing))}")
    industries = frame.loc[:, ["updateDate", "code", "code_name", "industry", "industryClassification"]].copy()
    industries.columns = ["industry_update_date", "bs_code", "security_name", "industry", "industry_classification"]
    industries["exchange"] = industries["bs_code"].str.split(".").str[0].map({"sh": "SSE", "sz": "SZSE"})
    industries["security_code"] = industries["bs_code"].str.split(".").str[-1].str.zfill(6)
    industries["industry_update_date"] = pd.to_datetime(industries["industry_update_date"], errors="coerce").dt.date
    return industries.loc[:, [
        "security_code",
        "exchange",
        "bs_code",
        "security_name",
        "industry",
        "industry_classification",
        "industry_update_date",
    ]]


def download_security_industries(output: Path, max_requests_per_day: int = 50_000) -> pd.DataFrame:
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        reserve_baostock_request('query_stock_industry', max_requests_per_day)
        industries = standardize_security_industries(baostock_result_to_frame(bs.query_stock_industry()))
    finally:
        bs.logout()
    if industries.duplicated(["exchange", "security_code"]).any():
        raise ValueError("duplicate exchange and security_code records found")
    write_parquet_outputs({output: industries})
    return industries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/source/security/baostock_security_industries.parquet"))
    parser.add_argument("--max-requests-per-day", type=int, default=50_000)
    args = parser.parse_args()
    try:
        industries = download_security_industries(args.output, args.max_requests_per_day)
        print(f"Output: {args.output} ({len(industries)} rows)")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
