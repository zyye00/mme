"""Analyze first-day margin-financing purchase concentration by security type."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {"trade_date", "exchange", "security_code", "security_name", "financing_buy_amount"}


def analyze_first_day(details: pd.DataFrame, threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    missing = REQUIRED_COLUMNS - set(details.columns)
    if missing:
        raise ValueError(f"input is missing columns: {', '.join(sorted(missing))}")

    details = details.copy()
    details["trade_date"] = pd.to_datetime(details["trade_date"], errors="raise").dt.date
    details["financing_buy_amount"] = pd.to_numeric(details["financing_buy_amount"], errors="raise")
    first_day = details["trade_date"].min()
    ranked = details.loc[
        (details["trade_date"] == first_day) & (details["financing_buy_amount"] > 0)
    ].sort_values("financing_buy_amount", ascending=False, kind="stable").reset_index(drop=True)
    total = ranked["financing_buy_amount"].sum()
    if not total:
        raise ValueError("first trading day has no positive financing purchase amount")
    ranked["rank"] = ranked.index + 1
    ranked["cumulative_amount"] = ranked["financing_buy_amount"].cumsum()
    ranked["cumulative_ratio"] = ranked["cumulative_amount"] / total
    cutoff = ranked["cumulative_ratio"].ge(threshold).idxmax()
    selected = ranked.loc[:cutoff].copy()
    summary = pd.DataFrame(
        [{"security_count": len(selected), "financing_buy_amount": selected["financing_buy_amount"].sum(), "amount_ratio": selected["financing_buy_amount"].sum() / total}]
    )
    return selected, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/margin_financing_etf_buy.parquet"))
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        selected, summary = analyze_first_day(pd.read_parquet(args.input), args.threshold)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        detail_path = args.output_dir / "margin_financing_first_day_top80.csv"
        summary_path = args.output_dir / "margin_financing_first_day_etf_summary.csv"
        selected.to_csv(detail_path, index=False)
        summary.to_csv(summary_path, index=False)
        first = selected.iloc[0]
        print(
            f"first_day={first['trade_date']} threshold={args.threshold:.1%} "
            f"security_count={len(selected)} cumulative_ratio={selected.iloc[-1]['cumulative_ratio']:.2%}"
        )
        print(summary.to_string(index=False))
        print(f"Output: {detail_path}\nOutput: {summary_path}")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
