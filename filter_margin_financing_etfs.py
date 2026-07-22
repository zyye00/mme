"""Filter localized margin-financing details to ETFs using BaoStock basics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from output_utils import write_parquet_outputs


def filter_etfs(details: pd.DataFrame, basics: pd.DataFrame) -> pd.DataFrame:
    etfs = basics.loc[basics["security_type"] == "etf"].drop(columns="security_name")
    result = details.merge(etfs, on=["exchange", "security_code"], how="inner", validate="many_to_one")
    if result.empty:
        raise ValueError("no ETF records found after joining BaoStock security basics")
    return result.sort_values(["trade_date", "exchange", "security_code"]).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/margin_financing_buy.parquet"))
    parser.add_argument("--basics", type=Path, default=Path("data/baostock_security_basics.parquet"))
    parser.add_argument("--output", type=Path, default=Path("data/margin_financing_etf_buy.parquet"))
    args = parser.parse_args()
    try:
        etfs = filter_etfs(pd.read_parquet(args.input), pd.read_parquet(args.basics))
        write_parquet_outputs({args.output: etfs})
        print(f"Output: {args.output} ({len(etfs)} rows)")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
